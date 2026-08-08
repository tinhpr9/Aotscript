#!/data/data/com.termux/files/usr/bin/python3
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent
RELAY_PATH = ROOT / "relay.py"
E2E_PATH = ROOT / "e2e.py"
CONFIG_PATH = pathlib.Path(
    "/storage/emulated/0/Download/Shouko/aot_group_config.json"
)
DEVICE_ID_PATH = pathlib.Path(
    "/storage/emulated/0/Download/Shouko/device_id.txt"
)
DEVICE_GROUP_PATH = pathlib.Path(
    "/storage/emulated/0/Download/Shouko/device_group.txt"
)
LOG_PATH = pathlib.Path(
    "/storage/emulated/0/Download/AOT_Group_Control.log"
)
CONFIG_VERSION = 1


class AotRuntimeError(RuntimeError):
    pass


def _read_small(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def normalize_device_id(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    match = re.fullmatch(r"m([1-9]\d{0,5})", raw)
    if not match:
        return None
    return f"m{match.group(1)}"


def normalize_session_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    return raw if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", raw) else None


def normalize_package(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return raw if re.fullmatch(r"[A-Za-z0-9._]{1,160}", raw) else None


def local_identity() -> tuple[str, str]:
    device_id = normalize_device_id(_read_small(DEVICE_ID_PATH))
    group = _read_small(DEVICE_GROUP_PATH).upper()
    if not device_id:
        raise AotRuntimeError("invalid_local_device_id")
    if group not in {"NOVA", "MARMOT"}:
        raise AotRuntimeError("invalid_local_device_group")
    return device_id, group


def validate_config_data(
    data: Any,
    *,
    local_device_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise AotRuntimeError("config_not_object")
    enabled = data.get("enabled") is True
    role = str(data.get("role") or "").strip().lower()
    if role not in {"reference", "follower"}:
        raise AotRuntimeError("invalid_role")
    session_id = normalize_session_id(data.get("session_id"))
    if not session_id:
        raise AotRuntimeError("invalid_session_id")
    reference_device_id = None
    if role == "follower":
        reference_device_id = normalize_device_id(
            data.get("reference_device_id")
        )
        if not reference_device_id:
            raise AotRuntimeError("invalid_reference_device_id")
        if (
            local_device_id
            and reference_device_id == local_device_id
        ):
            raise AotRuntimeError("reference_equals_local_device")
    open_package = None
    if data.get("open_package") not in (None, ""):
        open_package = normalize_package(data.get("open_package"))
        if not open_package:
            raise AotRuntimeError("invalid_open_package")
    return {
        "version": CONFIG_VERSION,
        "enabled": enabled,
        "role": role,
        "session_id": session_id,
        "reference_device_id": reference_device_id,
        "open_package": open_package,
    }


def load_config(*, required: bool = True) -> dict[str, Any] | None:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise AotRuntimeError("config_missing")
        return None
    except Exception as exc:
        raise AotRuntimeError("config_invalid_json") from exc
    device_id, _group = local_identity()
    return validate_config_data(
        data,
        local_device_id=device_id,
    )


def save_config(data: dict[str, Any]) -> None:
    device_id, _group = local_identity()
    clean = validate_config_data(
        data,
        local_device_id=device_id,
    )
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = CONFIG_PATH.with_name(
        CONFIG_PATH.name + f".tmp-{os.getpid()}"
    )
    try:
        temp.write_text(
            json.dumps(
                clean,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temp, CONFIG_PATH)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _compile_file(path: pathlib.Path) -> None:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AotRuntimeError(f"runtime_file_missing:{path.name}") from exc
    try:
        compile(source, str(path), "exec")
    except SyntaxError as exc:
        raise AotRuntimeError(f"runtime_file_syntax:{path.name}") from exc


def validate_runtime_files() -> None:
    _compile_file(RELAY_PATH)
    _compile_file(E2E_PATH)
    controller = ROOT / "controller.py"
    _compile_file(controller)


def _cmdline(pid: int) -> list[str]:
    try:
        raw = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [
        part.decode("utf-8", errors="replace")
        for part in raw.split(b"\0")
        if part
    ]


def matching_relay_pids(config: dict[str, Any]) -> list[int]:
    relay = str(RELAY_PATH.resolve())
    role = config["role"]
    session_id = config["session_id"]
    result = []
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        args = _cmdline(pid)
        if not args:
            continue
        if relay not in args:
            continue
        if role not in args:
            continue
        if session_id not in args:
            continue
        if role == "follower":
            reference = config.get("reference_device_id")
            if reference and reference not in args:
                continue
        result.append(pid)
    return sorted(set(result))


def stop_runtime(config: dict[str, Any]) -> list[int]:
    pids = matching_relay_pids(config)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        alive = [
            pid
            for pid in pids
            if pathlib.Path(f"/proc/{pid}").exists()
        ]
        if not alive:
            break
        time.sleep(0.2)
    return pids


def relay_command(config: dict[str, Any]) -> list[str]:
    command = [
        sys.executable,
        "-u",
        str(RELAY_PATH),
        config["role"],
        "--session",
        config["session_id"],
    ]
    if config["role"] == "follower":
        command.extend(
            [
                "--reference-device",
                str(config["reference_device_id"]),
            ]
        )
    if config.get("open_package"):
        command.extend(
            [
                "--open-package",
                str(config["open_package"]),
            ]
        )
    return command


def start_runtime(config: dict[str, Any]) -> int | None:
    if config.get("enabled") is not True:
        print("AOT_RUNTIME=DISABLED")
        return None
    validate_runtime_files()
    running = matching_relay_pids(config)
    if len(running) == 1:
        print(f"AOT_RUNTIME=ALREADY_RUNNING:{running[0]}")
        return running[0]
    if len(running) > 1:
        stopped = stop_runtime(config)
        print("AOT_RUNTIME_DUPLICATES_STOPPED=" + ",".join(map(str, stopped)))
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("ab") as log:
        process = subprocess.Popen(
            relay_command(config),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    time.sleep(1)
    if process.poll() is not None:
        raise AotRuntimeError("relay_exited_immediately")
    print(f"AOT_RUNTIME=STARTED:{process.pid}")
    return process.pid


def configure(args: argparse.Namespace) -> int:
    device_id, group = local_identity()
    data = {
        "version": CONFIG_VERSION,
        "enabled": not args.disabled,
        "role": args.role,
        "session_id": args.session,
        "reference_device_id": args.reference_device,
        "open_package": args.open_package,
    }
    save_config(data)
    print("AOT_CONFIG=SUCCESS")
    print(f"DEVICE_ID={device_id}")
    print(f"DEVICE_GROUP={group}")
    print(f"ROLE={args.role}")
    print(f"SESSION={args.session}")
    if args.role == "follower":
        print(f"REFERENCE_DEVICE={args.reference_device}")
    print("SECRET_OUTPUT=NO")
    return 0


def status() -> int:
    config = load_config(required=False)
    if config is None:
        print("AOT_CONFIG=MISSING")
        return 0
    pids = matching_relay_pids(config)
    print("AOT_CONFIG=OK")
    print(f"ENABLED={'YES' if config['enabled'] else 'NO'}")
    print(f"ROLE={config['role']}")
    print(f"SESSION={config['session_id']}")
    print("PIDS=" + (",".join(map(str, pids)) if pids else "NONE"))
    print("SECRET_OUTPUT=NO")
    return 0


def run_e2e(args: argparse.Namespace) -> int:
    config = load_config()
    if config["role"] != "reference":
        raise AotRuntimeError("e2e_requires_reference_role")
    followers = []
    seen = set()
    for raw in args.follower:
        device_id = normalize_device_id(raw)
        if not device_id:
            raise AotRuntimeError("invalid_e2e_follower")
        if device_id in seen:
            continue
        seen.add(device_id)
        followers.append(device_id)
    if not followers:
        raise AotRuntimeError("e2e_followers_empty")
    stop_runtime(config)
    command = [
        sys.executable,
        "-u",
        str(E2E_PATH),
        "--session",
        config["session_id"],
    ]
    for follower in followers:
        command.extend(["--follower", follower])
    package = args.package or config.get("open_package")
    if package:
        command.extend(["--package", package])
    try:
        result = subprocess.run(command, check=False)
        return int(result.returncode)
    finally:
        try:
            start_runtime(config)
        except AotRuntimeError as exc:
            print("AOT_RUNTIME_RESTART=FAILED")
            print("REASON=" + str(exc))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AOT Group Control runtime manager"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    config = sub.add_parser("configure")
    config.add_argument(
        "--role",
        choices=("reference", "follower"),
        required=True,
    )
    config.add_argument("--session", required=True)
    config.add_argument("--reference-device")
    config.add_argument("--open-package")
    config.add_argument("--disabled", action="store_true")

    sub.add_parser("start")
    sub.add_parser("stop")
    sub.add_parser("auto-start")
    sub.add_parser("status")

    e2e = sub.add_parser("e2e")
    e2e.add_argument(
        "--follower",
        action="append",
        required=True,
    )
    e2e.add_argument("--package")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "configure":
            return configure(args)
        if args.command == "status":
            return status()
        config = load_config(required=False)
        if config is None:
            if args.command == "auto-start":
                print("AOT_RUNTIME=NO_CONFIG")
                return 0
            raise AotRuntimeError("config_missing")
        if args.command in {"start", "auto-start"}:
            start_runtime(config)
            return 0
        if args.command == "stop":
            stopped = stop_runtime(config)
            print(
                "AOT_RUNTIME_STOPPED="
                + (",".join(map(str, stopped)) if stopped else "NONE")
            )
            return 0
        if args.command == "e2e":
            return run_e2e(args)
        raise AotRuntimeError("unknown_command")
    except AotRuntimeError as exc:
        print("AOT_RUNTIME=FAILED")
        print("REASON=" + str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
