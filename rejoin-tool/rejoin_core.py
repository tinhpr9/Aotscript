import json
import os
import subprocess
import time
import logging
import shlex
import re
import fcntl
from datetime import datetime

CONFIG_DIR = "/storage/emulated/0/Download/AotRejoin"
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
RUNTIME_DIR = os.path.expanduser("~/.aot-rejoin")
LOCK_FILE = os.path.join(RUNTIME_DIR, "monitor.lock")
LOG_FILE = os.path.join(CONFIG_DIR, "rejoin.log")

DEFAULT_CONFIG = {
    "packages": {},
    "settings": {
        "check_interval_sec": 30,
        "open_delay_sec": 10,
        "retry_delay_sec": 10,
        "max_retries": 3,
        "cooldown_after_success_sec": 30,
        "delay_between_packages_sec": 5,
        "package_prefix": "com.roblox"
    }
}

class SystemEnvironment:
    def is_process_running(self, package: str) -> bool:
        if not re.match(r'^[a-zA-Z0-9_.]+$', package):
            return False
        try:
            # use su -c pidof to check if running
            result = subprocess.run(["su", "-c", f"pidof {package}"], capture_output=True, text=True, timeout=5)
            return bool(result.stdout.strip())
        except subprocess.TimeoutExpired:
            self.log(logging.WARNING, f"[{package}] pidof timeout")
            return False
        except Exception:
            return False

    def launch_intent(self, package: str, url: str) -> bool:
        if not re.match(r'^[a-zA-Z0-9_.]+$', package):
            return False
        try:
            subprocess.run(["su", "-c", f"am force-stop {shlex.quote(package)}"], timeout=5)
        except subprocess.TimeoutExpired:
            self.log(logging.WARNING, f"[{package}] force-stop timeout")
        except Exception:
            pass

        self.sleep(2)

        fallback = False
        try:
            cmd_splash = f"am start -a android.intent.action.MAIN -n {shlex.quote(package)}/com.roblox.client.startup.ActivitySplash"
            res_splash = subprocess.run(["su", "-c", cmd_splash], capture_output=True, text=True, timeout=10)
            if "does not exist" in res_splash.stderr or "Error:" in res_splash.stderr or res_splash.returncode != 0:
                fallback = True
        except subprocess.TimeoutExpired:
            self.log(logging.WARNING, f"[{package}] splash timeout")
            fallback = True
        except Exception:
            fallback = True

        formatted_url = url
        if 'roblox.com' not in url and url.isdigit():
            formatted_url = f"roblox://placeID={url}"

        if fallback:
            try:
                cmd_fallback = f"am start -a android.intent.action.VIEW -d {shlex.quote(formatted_url)} -p {shlex.quote(package)}"
                res_join = subprocess.run(["su", "-c", cmd_fallback], capture_output=True, text=True, timeout=10)
                return "Starting: Intent" in res_join.stdout or res_join.returncode == 0
            except subprocess.TimeoutExpired:
                self.log(logging.WARNING, f"[{package}] fallback join timeout")
                return False
            except Exception:
                return False

        self.sleep(10)

        try:
            cmd_join = f"am start -a android.intent.action.VIEW -n {shlex.quote(package)}/com.roblox.client.ActivityProtocolLaunch -d {shlex.quote(formatted_url)}"
            result = subprocess.run(["su", "-c", cmd_join], capture_output=True, text=True, timeout=10)
            if "does not exist" in result.stderr or "Error:" in result.stderr or result.returncode != 0:
                cmd_fallback = f"am start -a android.intent.action.VIEW -d {shlex.quote(formatted_url)} -p {shlex.quote(package)}"
                result = subprocess.run(["su", "-c", cmd_fallback], capture_output=True, text=True, timeout=10)
            return "Starting: Intent" in result.stdout or result.returncode == 0
        except subprocess.TimeoutExpired:
            self.log(logging.WARNING, f"[{package}] join timeout")
            return False
        except Exception:
            return False

    def sleep(self, seconds: int):
        time.sleep(seconds)

    def log(self, level: int, msg: str):
        logging.log(level, msg)

class ConfigManager:
    @staticmethod
    def ensure_dir():
        if not os.path.exists(CONFIG_DIR):
            os.makedirs(CONFIG_DIR, exist_ok=True)

    @staticmethod
    def load_config(strict=False) -> dict:
        ConfigManager.ensure_dir()
        if not os.path.exists(CONFIG_FILE):
            import copy
            default_copy = copy.deepcopy(DEFAULT_CONFIG)
            ConfigManager.save_config(default_copy)
            return default_copy
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            if strict:
                raise ValueError("Error: config.json is malformed. Please fix or remove it.")
            import copy
            return copy.deepcopy(DEFAULT_CONFIG)

    @staticmethod
    def save_config(config: dict):
        ConfigManager.ensure_dir()
        tmp_file = CONFIG_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp_file, CONFIG_FILE)

    @staticmethod
    def add_package(package: str, url: str) -> bool:
        if not re.match(r'^[a-zA-Z0-9_.]+$', package):
            return False

        import urllib.parse
        formatted_url = url
        if url.isdigit():
            formatted_url = f"roblox://placeID={url}"
        elif url.startswith('roblox://'):
            pass
        elif url.startswith('http://') or url.startswith('https://'):
            parsed = urllib.parse.urlparse(url)
            host = parsed.hostname
            if not host:
                return False
            if host != "roblox.com" and not host.endswith(".roblox.com"):
                return False
        else:
            return False

        try:
            config = ConfigManager.load_config(strict=True)
        except ValueError as e:
            print(str(e))
            return False

        if "packages" not in config:
            config["packages"] = {}
        config["packages"][package] = {"join_url": formatted_url, "enabled": True}
        ConfigManager.save_config(config)
        return True

    @staticmethod
    def remove_package(package: str):
        try:
            config = ConfigManager.load_config(strict=True)
        except ValueError as e:
            print(str(e))
            return

        if "packages" in config and package in config["packages"]:
            del config["packages"][package]
            ConfigManager.save_config(config)

    @staticmethod
    def set_package_enabled(package: str, enabled: bool):
        try:
            config = ConfigManager.load_config(strict=True)
        except ValueError as e:
            print(str(e))
            return

        if "packages" in config and package in config["packages"]:
            config["packages"][package]["enabled"] = enabled
            ConfigManager.save_config(config)

class Monitor:
    def __init__(self, env: SystemEnvironment, stop_event=None):
        self.env = env
        self.stop_event = stop_event
        self.state = {} # package -> {"retries": 0, "status": "UNKNOWN", "last_launch": 0}

    def run_once(self):
        config = ConfigManager.load_config()
        settings = config.get("settings", DEFAULT_CONFIG["settings"])
        packages = config.get("packages", {})

        for pkg, data in packages.items():
            if self.stop_event and self.stop_event():
                break

            if not data.get("enabled", False):
                self.state[pkg] = {"status": "DISABLED", "retries": 0, "last_launch": 0}
                continue

            if pkg not in self.state:
                self.state[pkg] = {"retries": 0, "status": "CHECKING", "last_launch": 0}

            is_running = self.env.is_process_running(pkg)
            now = time.time()

            if is_running:
                if self.state[pkg]["status"] != "RUNNING":
                    self.env.log(logging.INFO, f"[{pkg}] is RUNNING")
                self.state[pkg]["status"] = "RUNNING"
                self.state[pkg]["retries"] = 0
            else:
                # Need to launch
                retries = self.state[pkg]["retries"]
                max_retries = settings.get("max_retries", 3)
                if retries >= max_retries:
                    if self.state[pkg]["status"] != "FAILED":
                        self.env.log(logging.ERROR, f"[{pkg}] FAILED after {retries} retries.")
                    self.state[pkg]["status"] = "FAILED"
                    continue

                # Check cooldown / delay
                last_launch = self.state[pkg].get("last_launch", 0)
                last_success = self.state[pkg].get("last_success", 0)
                retry_delay = settings.get("retry_delay_sec", 10)
                cooldown = settings.get("cooldown_after_success_sec", 30)

                if now - last_success < cooldown:
                    continue
                if now - last_launch < retry_delay:
                    continue

                self.env.log(logging.INFO, f"[{pkg}] NOT RUNNING. Launching (Attempt {retries + 1}/{max_retries})...")
                url = data.get("join_url", "")
                self.env.log(logging.DEBUG, f"[{pkg}] Intent URL: ***REDACTED***")

                success = self.env.launch_intent(pkg, url)
                self.state[pkg]["last_launch"] = time.time()
                self.state[pkg]["retries"] += 1

                if success:
                    self.env.sleep(settings.get("open_delay_sec", 10))
                    if self.env.is_process_running(pkg):
                        self.state[pkg]["status"] = "RUNNING"
                        self.state[pkg]["retries"] = 0
                        self.state[pkg]["last_success"] = time.time()
                        self.env.log(logging.INFO, f"[{pkg}] Verified RUNNING after launch.")
                        delay_seq = settings.get("delay_between_packages_sec", 5)
                        if delay_seq > 0:
                            self.env.sleep(delay_seq)
                    else:
                        self.env.log(logging.WARNING, f"[{pkg}] Launched but process not found.")
                        self.state[pkg]["status"] = "LAUNCH_ERROR"
                else:
                    self.env.log(logging.WARNING, f"[{pkg}] Launch command failed.")
                    self.state[pkg]["status"] = "LAUNCH_ERROR"

    def save_state(self):
        try:
            status_file = os.path.join(CONFIG_DIR, "status.json")
            tmp_file = status_file + ".tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp_file, status_file)
        except Exception as e:
            self.env.log(logging.ERROR, f"Failed to save state: {e}")

    def run_loop(self):
        self.env.log(logging.INFO, "Monitor started.")
        while True:
            if self.stop_event and self.stop_event():
                break
            self.run_once()
            self.save_state()
            config = ConfigManager.load_config()

            interval_raw = config.get("settings", {}).get("check_interval_sec", 30)
            try:
                interval = int(interval_raw)
                if interval < 1:
                    interval = 1
            except (TypeError, ValueError):
                interval = 30

            # Sleep in small increments to allow responsive stop
            for _ in range(interval):
                if self.stop_event and self.stop_event():
                    break
                self.env.sleep(1)

def is_rejoin_daemon(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/cmdline", "r") as f:
            cmdline = f.read().replace('\x00', ' ')
        return "rejoin_daemon.py" in cmdline
    except OSError:
        return False

def discover_roblox_packages() -> list:
    config = ConfigManager.load_config()
    prefix = config.get("settings", {}).get("package_prefix", "com.roblox")
    if not re.match(r'^[a-zA-Z0-9_.]+$', prefix):
        return []
    try:
        result = subprocess.run(["su", "-c", f"pm list packages {shlex.quote(prefix)}"], capture_output=True, text=True, timeout=10)
        packages = []
        for line in result.stdout.strip().splitlines():
            if line.startswith("package:"):
                packages.append(line.replace("package:", "").strip())
        return packages
    except Exception:
        return []

_lock_fd = None

def acquire_lock() -> bool:
    global _lock_fd
    ConfigManager.ensure_dir()
    if not os.path.exists(RUNTIME_DIR):
        os.makedirs(RUNTIME_DIR, exist_ok=True)
    try:
        fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        _lock_fd = fd
        return True
    except (BlockingIOError, OSError):
        if 'fd' in locals():
            os.close(fd)
        return False

def release_lock():
    global _lock_fd
    if _lock_fd is not None:
        try:
            os.ftruncate(_lock_fd, 0)
            os.fsync(_lock_fd)
        except Exception:
            pass
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            os.close(_lock_fd)
        except Exception:
            pass
        _lock_fd = None

def init_logging():
    ConfigManager.ensure_dir()
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
