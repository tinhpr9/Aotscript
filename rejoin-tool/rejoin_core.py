import json
import os
import subprocess
import time
import logging
import shlex
import re
from datetime import datetime

CONFIG_DIR = "/storage/emulated/0/Download/AotRejoin"
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
LOCK_FILE = os.path.join(CONFIG_DIR, "monitor.lock")
LOG_FILE = os.path.join(CONFIG_DIR, "rejoin.log")

DEFAULT_CONFIG = {
    "packages": {},
    "settings": {
        "check_interval_sec": 30,
        "open_delay_sec": 10,
        "retry_delay_sec": 10,
        "max_retries": 3,
        "cooldown_after_success_sec": 30
    }
}

class SystemEnvironment:
    def is_process_running(self, package: str) -> bool:
        if not re.match(r'^[a-zA-Z0-9_.]+$', package):
            return False
        try:
            # use su -c pidof to check if running
            result = subprocess.run(["su", "-c", f"pidof {package}"], capture_output=True, text=True)
            return bool(result.stdout.strip())
        except Exception:
            return False

    def launch_intent(self, package: str, url: str) -> bool:
        if not re.match(r'^[a-zA-Z0-9_.]+$', package):
            return False
        try:
            # We use monkey or am to launch intent
            cmd_str = f"am start -a android.intent.action.VIEW -d {shlex.quote(url)} {shlex.quote(package)}"
            result = subprocess.run(["su", "-c", cmd_str], capture_output=True, text=True)
            return "Starting: Intent" in result.stdout or result.returncode == 0
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
    def load_config() -> dict:
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
    def add_package(package: str, url: str):
        config = ConfigManager.load_config()
        config["packages"][package] = {"join_url": url, "enabled": True}
        ConfigManager.save_config(config)

    @staticmethod
    def remove_package(package: str):
        config = ConfigManager.load_config()
        if package in config["packages"]:
            del config["packages"][package]
            ConfigManager.save_config(config)

    @staticmethod
    def set_package_enabled(package: str, enabled: bool):
        config = ConfigManager.load_config()
        if package in config["packages"]:
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
                # Hide secret from URL in log
                url = data.get("join_url", "")
                safe_url = url.split("?")[0] + "?***" if "?" in url else url
                self.env.log(logging.DEBUG, f"[{pkg}] Intent URL: {safe_url}")

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
            interval = config.get("settings", {}).get("check_interval_sec", 30)
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

def acquire_lock() -> bool:
    ConfigManager.ensure_dir()
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                pid = int(f.read().strip())
            # check if pid is still running
            os.kill(pid, 0)
            if is_rejoin_daemon(pid):
                return False # Lock is held by running process
        except (ValueError, OSError):
            pass # Process dead or invalid pid, we can take lock
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True

def release_lock():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                pid = int(f.read().strip())
            if pid == os.getpid():
                os.remove(LOCK_FILE)
        except Exception:
            pass

def init_logging():
    ConfigManager.ensure_dir()
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
