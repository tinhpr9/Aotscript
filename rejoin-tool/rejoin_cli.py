import sys
import argparse
import os
import subprocess
import signal

from rejoin_core import ConfigManager, init_logging, Monitor, SystemEnvironment, LOCK_FILE, CONFIG_DIR, is_rejoin_daemon

STATUS_FILE = os.path.join(CONFIG_DIR, "status.json")
BOOT_SCRIPT_DIR = os.path.expanduser("~/.termux/boot")
BOOT_SCRIPT_PATH = os.path.join(BOOT_SCRIPT_DIR, "start-rejoin.sh")

def get_pid():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                content = f.read().strip()
                if not content:
                    return None
                pid = int(content)
            os.kill(pid, 0)
            if is_rejoin_daemon(pid):
                return pid
            return None
        except (ValueError, OSError):
            return None
    return None

def start_daemon():
    ConfigManager.ensure_dir()

    try:
        ConfigManager.load_config(strict=True)
    except ValueError as e:
        print("FAILED to start monitor.")
        print(str(e))
        return

    pid = get_pid()
    if pid:
        print(f"Monitor is already running (PID: {pid}).")
        return
    print("Starting auto-rejoin monitor in background...")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_file = os.path.join(CONFIG_DIR, "rejoin.log")

    with open(log_file, "a") as f:
        subprocess.Popen(
            ["python3", os.path.join(script_dir, "rejoin_daemon.py")],
            stdout=f,
            stderr=f,
            start_new_session=True
        )

    import time
    for _ in range(10):
        time.sleep(0.5)
        new_pid = get_pid()
        if new_pid:
            print(f"Started successfully (PID: {new_pid}).")
            return
    print("FAILED to start monitor.")

def stop_daemon():
    pid = get_pid()
    if pid:
        print(f"Stopping monitor (PID: {pid})...")
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            print("Failed to stop or process already dead.")
            return

        import time
        for _ in range(20):
            try:
                os.kill(pid, 0)
                if not is_rejoin_daemon(pid):
                    print("Stopped.")
                    return
            except OSError:
                print("Stopped.")
                return
            time.sleep(0.5)
        print("Failed to stop: shutdown still pending.")
    else:
        print("Monitor is not running.")

def print_status():
    pid = get_pid()
    if pid:
        print(f"[RUNNING] Monitor is active (PID: {pid})")
    else:
        print("[STOPPED] Monitor is not active")

    try:
        config = ConfigManager.load_config(strict=True)
    except ValueError as e:
        print(str(e))
        return

    packages = config.get("packages", {})
    if not packages:
        print("No packages configured.")
        return

    import json
    state = {}
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r") as f:
                state = json.load(f)
        except Exception:
            pass

    print(f"\n{'Package':<30} | {'Enabled':<7} | {'Status':<15} | {'Retries':<7}")
    print("-" * 65)
    for pkg, data in packages.items():
        enabled = str(data.get("enabled", False))
        pkg_state = state.get(pkg, {})
        status = pkg_state.get("status", "UNKNOWN")
        retries = pkg_state.get("retries", 0)
        print(f"{pkg:<30} | {enabled:<7} | {status:<15} | {retries:<7}")

def install_boot():
    if not os.path.exists(BOOT_SCRIPT_DIR):
        os.makedirs(BOOT_SCRIPT_DIR, exist_ok=True)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    rejoin_bin = os.path.join(script_dir, "rejoin")
    import shlex
    safe_bin = shlex.quote(rejoin_bin)
    content = f"""#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
sleep 15
{safe_bin} start
"""
    with open(BOOT_SCRIPT_PATH, "w") as f:
        f.write(content)
    os.chmod(BOOT_SCRIPT_PATH, 0o755)
    print(f"Boot script installed at {BOOT_SCRIPT_PATH}")

def uninstall_boot():
    if os.path.exists(BOOT_SCRIPT_PATH):
        os.remove(BOOT_SCRIPT_PATH)
        print("Boot script removed.")
    else:
        print("Boot script not found.")

def print_logs():
    log_file = os.path.join(CONFIG_DIR, "rejoin.log")
    if os.path.exists(log_file):
        subprocess.run(["tail", "-n", "20", log_file])
    else:
        print("Log file not found.")

def interactive_menu():
    while True:
        print("\n--- AOT Rejoin Menu ---")
        print("1. Start auto rejoin")
        print("2. Stop auto rejoin")
        print("3. Add package + join URL")
        print("4. List packages / Status")
        print("5. Enable/disable package")
        print("6. Install Termux:Boot")
        print("7. Configuration details")
        print("8. Discover packages")
        print("9. Exit")
        choice = input("Select an option: ").strip()

        if choice == "1":
            start_daemon()
        elif choice == "2":
            stop_daemon()
        elif choice == "3":
            pkg = input("Package name (e.g., com.tinh.vv.hi): ").strip()
            url = input("Join URL (e.g., roblox://... or Game ID): ").strip()
            if pkg and url:
                if ConfigManager.add_package(pkg, url):
                    print(f"Added {pkg}")
                else:
                    print("Failed to add package.")
        elif choice == "4":
            print_status()
        elif choice == "5":
            pkg = input("Package name: ").strip()
            en = input("Enable? (y/n): ").strip().lower()
            if pkg:
                if ConfigManager.set_package_enabled(pkg, en == "y"):
                    print(f"Updated {pkg}")
                else:
                    print(f"Failed to update {pkg}")
        elif choice == "6":
            install_boot()
        elif choice == "7":
            try:
                config = ConfigManager.load_config(strict=True)
            except ValueError as e:
                print(str(e))
                continue
            import copy
            import json
            safe_config = copy.deepcopy(config)
            for pkg, data in safe_config.get("packages", {}).items():
                if "join_url" in data:
                    data["join_url"] = "***REDACTED***"
            print(json.dumps(safe_config, indent=2))
        elif choice == "8":
            from rejoin_core import discover_roblox_packages
            try:
                packages = discover_roblox_packages()
            except RuntimeError as e:
                print(str(e))
                continue
            if not packages:
                print("No Roblox packages found.")
            else:
                print(f"Found {len(packages)} packages.")
                add = input("Do you want to add them? (y/n): ").strip().lower()
                if add == 'y':
                    url = input("Join URL (e.g., roblox://... or Game ID): ").strip()
                    for p in packages:
                        if ConfigManager.add_package(p, url):
                            print(f"Added {p}")
                        else:
                            print(f"Failed to add {p}")
        elif choice == "9" or not choice:
            break
        else:
            print("Invalid choice")

def main():
    parser = argparse.ArgumentParser(description="Rejoin Tool")
    parser.add_argument("command", nargs="?", choices=[
        "setup", "packages", "add", "remove", "enable", "disable",
        "start", "stop", "status", "logs", "install-boot", "uninstall-boot", "discover"
    ], help="Command to run")

    parser.add_argument("args", nargs=argparse.REMAINDER)

    args = parser.parse_args()

    if not args.command:
        interactive_menu()
        return

    cmd = args.command
    if cmd == "start":
        start_daemon()
    elif cmd == "stop":
        stop_daemon()
    elif cmd == "status":
        print_status()
    elif cmd == "packages":
        print_status()
    elif cmd == "logs":
        print_logs()
    elif cmd == "install-boot":
        install_boot()
    elif cmd == "setup":
        install_boot()
    elif cmd == "uninstall-boot":
        uninstall_boot()
    elif cmd == "add":
        if len(args.args) >= 2:
            pkg = args.args[0]
            url = " ".join(args.args[1:])
            if ConfigManager.add_package(pkg, url):
                print(f"Added {pkg}")
            else:
                print("Failed to add package.")
        else:
            print("Usage: rejoin add <package> <url>")
    elif cmd == "remove":
        if args.args:
            if ConfigManager.remove_package(args.args[0]):
                print(f"Removed {args.args[0]}")
            else:
                print(f"Failed to remove {args.args[0]}")
        else:
            print("Usage: rejoin remove <package>")
    elif cmd == "enable":
        if args.args:
            if ConfigManager.set_package_enabled(args.args[0], True):
                print(f"Enabled {args.args[0]}")
            else:
                print(f"Failed to enable {args.args[0]}")
    elif cmd == "disable":
        if args.args:
            if ConfigManager.set_package_enabled(args.args[0], False):
                print(f"Disabled {args.args[0]}")
            else:
                print(f"Failed to disable {args.args[0]}")
    elif cmd == "discover":
        from rejoin_core import discover_roblox_packages
        try:
            packages = discover_roblox_packages()
        except RuntimeError as e:
            print(str(e))
            return
        if not packages:
            print("No Roblox packages found.")
        else:
            print(f"Found {len(packages)} packages:")
            for p in packages:
                print(f" - {p}")
                if args.args:
                    url = " ".join(args.args)
                    if ConfigManager.add_package(p, url):
                        print(f"   Added {p}")
                    else:
                        print(f"   Failed to add {p}")

if __name__ == "__main__":
    main()
