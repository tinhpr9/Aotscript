import json
import os
import subprocess
import time
import logging
import shlex
import re
import fcntl
import math
import contextlib
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
    @contextlib.contextmanager
    def transaction_lock():
        ConfigManager.ensure_dir()
        if not os.path.exists(RUNTIME_DIR):
            os.makedirs(RUNTIME_DIR, exist_ok=True)
        tx_lock_file = os.path.join(RUNTIME_DIR, "config_tx.lock")
        fd = None
        try:
            fd = os.open(tx_lock_file, os.O_RDWR | os.O_CREAT, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    os.close(fd)
                except Exception:
                    pass

    @staticmethod
    def format_and_validate_url(url):
        import urllib.parse
        if not isinstance(url, str):
            return None
        url = url.strip()
        if not url:
            return None

        formatted_url = url
        if url.isdigit():
            formatted_url = f"roblox://placeID={url}"
            return formatted_url
        elif url.startswith('roblox://'):
            return formatted_url
        elif url.startswith('http://') or url.startswith('https://'):
            parsed = urllib.parse.urlparse(url)
            host = parsed.hostname
            if not host:
                return None
            if host != "roblox.com" and not host.endswith(".roblox.com"):
                return None
            return formatted_url

        return None

    @staticmethod
    def load_config(strict=False, _locked=False) -> dict:
        ConfigManager.ensure_dir()
        if not os.path.exists(CONFIG_FILE):
            if not _locked:
                with ConfigManager.transaction_lock():
                    if not os.path.exists(CONFIG_FILE):
                        import copy
                        default_copy = copy.deepcopy(DEFAULT_CONFIG)
                        ConfigManager.save_config(default_copy)
            import copy
            return copy.deepcopy(DEFAULT_CONFIG)
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)

            if strict:
                if not isinstance(config, dict):
                    raise ValueError("Error: config.json is malformed. Please fix or remove it.")
                if "settings" in config and not isinstance(config["settings"], dict):
                    raise ValueError("Error: config.json is malformed. Please fix or remove it.")
                if "packages" in config:
                    if not isinstance(config["packages"], dict):
                        raise ValueError("Error: config.json is malformed. Please fix or remove it.")
                    for k, v in config["packages"].items():
                        if not isinstance(k, str) or not re.match(r'^[a-zA-Z0-9_.]+$', k):
                            raise ValueError("Error: config.json is malformed. Please fix or remove it.")
                        if not isinstance(v, dict):
                            raise ValueError("Error: config.json is malformed. Please fix or remove it.")
                        if "enabled" in v and not isinstance(v["enabled"], bool):
                            raise ValueError("Error: config.json is malformed. Please fix or remove it.")

                        enabled = v.get("enabled", False)
                        if enabled:
                            if not ConfigManager.format_and_validate_url(v.get("join_url", "")):
                                raise ValueError("Error: config.json is malformed. Please fix or remove it.")
                        else:
                            if "join_url" in v and not isinstance(v["join_url"], str):
                                raise ValueError("Error: config.json is malformed. Please fix or remove it.")
            else:
                if not isinstance(config, dict):
                    config = {}
                if "packages" in config and not isinstance(config["packages"], dict):
                    config["packages"] = {}
                if "settings" in config and not isinstance(config["settings"], dict):
                    config["settings"] = {}

            return config
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
        if not isinstance(package, str) or not re.match(r'^[a-zA-Z0-9_.]+$', package):
            return False

        formatted_url = ConfigManager.format_and_validate_url(url)
        if not formatted_url:
            return False

        with ConfigManager.transaction_lock():
            try:
                config = ConfigManager.load_config(strict=True, _locked=True)
            except ValueError as e:
                print(str(e))
                return False

            if "packages" not in config:
                config["packages"] = {}
            config["packages"][package] = {"join_url": formatted_url, "enabled": True}
            ConfigManager.save_config(config)
            return True

    @staticmethod
    def remove_package(package: str) -> bool:
        with ConfigManager.transaction_lock():
            try:
                config = ConfigManager.load_config(strict=True, _locked=True)
            except ValueError as e:
                print(str(e))
                return False

            if "packages" in config and package in config["packages"]:
                del config["packages"][package]
                ConfigManager.save_config(config)
                return True
            return False

    @staticmethod
    def set_package_enabled(package: str, enabled: bool) -> bool:
        if not isinstance(enabled, bool):
            return False

        with ConfigManager.transaction_lock():
            try:
                config = ConfigManager.load_config(strict=True, _locked=True)
            except ValueError as e:
                print(str(e))
                return False

            if "packages" in config and package in config["packages"]:
                if enabled:
                    join_url = config["packages"][package].get("join_url", "")
                    if not ConfigManager.format_and_validate_url(join_url):
                        return False
                config["packages"][package]["enabled"] = enabled
                ConfigManager.save_config(config)
                return True
            return False

class Monitor:
    def __init__(self, env: SystemEnvironment, stop_event=None):
        self.env = env
        self.stop_event = stop_event
        self.state = {} # package -> {"retries": 0, "status": "UNKNOWN", "last_launch": 0}

    def run_once(self):
        try:
            config = ConfigManager.load_config(strict=True)
        except ValueError as e:
            self.env.log(logging.ERROR, str(e))
            raise

        settings = config.get("settings", {})
        if not isinstance(settings, dict):
            settings = {}
        packages = config.get("packages", {})
        if not isinstance(packages, dict):
            packages = {}

        def safe_get_num(key, default_val, min_val=0):
            val = settings.get(key, default_val)
            if isinstance(val, bool):
                return default_val
            try:
                val = float(val)
                if not math.isfinite(val):
                    return default_val
                return max(val, min_val)
            except (TypeError, ValueError):
                return default_val

        for pkg, data in packages.items():
            if self.stop_event and self.stop_event():
                break

            if not isinstance(data, dict) or not data.get("enabled", False):
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
                max_retries = int(safe_get_num("max_retries", 3, 0))
                if retries >= max_retries:
                    if self.state[pkg]["status"] != "FAILED":
                        self.env.log(logging.ERROR, f"[{pkg}] FAILED after {retries} retries.")
                    self.state[pkg]["status"] = "FAILED"
                    continue

                # Check cooldown / delay
                last_launch = self.state[pkg].get("last_launch", 0)
                last_success = self.state[pkg].get("last_success", 0)
                retry_delay = safe_get_num("retry_delay_sec", 10, 0)
                cooldown = safe_get_num("cooldown_after_success_sec", 30, 0)

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
                    self.env.sleep(safe_get_num("open_delay_sec", 10, 0))
                    if self.env.is_process_running(pkg):
                        self.state[pkg]["status"] = "RUNNING"
                        self.state[pkg]["retries"] = 0
                        self.state[pkg]["last_success"] = time.time()
                        self.env.log(logging.INFO, f"[{pkg}] Verified RUNNING after launch.")
                        delay_seq = safe_get_num("delay_between_packages_sec", 5, 0)
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

            try:
                self.run_once()
                self.save_state()
                config = ConfigManager.load_config(strict=True)
            except ValueError:
                self.env.log(logging.ERROR, "Monitor stopping due to configuration error.")
                break

            settings = config.get("settings", {})
            if not isinstance(settings, dict):
                settings = {}

            interval_raw = settings.get("check_interval_sec", 30)
            if isinstance(interval_raw, bool):
                interval = 30
            else:
                try:
                    interval = float(interval_raw)
                    if not math.isfinite(interval):
                        interval = 30
                    else:
                        interval = int(interval)
                        interval = max(interval, 1)
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
    try:
        config = ConfigManager.load_config(strict=True)
    except ValueError as e:
        print(str(e))
        return []

    settings = config.get("settings", {})
    if not isinstance(settings, dict):
        settings = {}

    prefix = settings.get("package_prefix", "com.roblox")
    if not isinstance(prefix, str) or not re.match(r'^[a-zA-Z0-9_.]+$', prefix):
        prefix = "com.roblox"

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
