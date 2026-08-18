#!/data/data/com.termux/files/usr/bin/python3
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

CONFIG_VERSION = 3
LOCAL_IDENTITY_STATE = (
    "aot_group_state.json",
    "aot_worker_update_pending.json",
    "aot_worker_update_health.json",
    "aot_worker_version.json",
)


class RegistrationError(RuntimeError):
    pass


def normalize_device_id(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    return raw if re.fullmatch(r"m[1-9]\d{0,5}", raw) else None


def load_agent_auth(path: pathlib.Path, origin: str) -> tuple[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        report_url = str(data["worker_report_url"]).strip()
        secret = str(data["agent_report_secret"]).strip()
    except Exception as exc:
        raise RegistrationError("agent_config_invalid") from exc
    parsed = urllib.parse.urlparse(report_url)
    expected = urllib.parse.urlparse(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc or not secret
        or parsed.scheme != expected.scheme or parsed.netloc != expected.netloc
    ):
        raise RegistrationError("agent_config_origin_mismatch")
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")), secret


def post_json(origin: str, secret: str, operation: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        origin.rstrip("/") + f"/aot/control/registration/{operation}",
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Cache-Control": "no-store",
            "X-Agent-Secret": secret,
            "User-Agent": "Aotscript-msetup-aot/2",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = int(response.status)
            raw = response.read(128 * 1024)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read(128 * 1024)
    except Exception as exc:
        raise RegistrationError("registration_server_unreachable") from exc
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception as exc:
        raise RegistrationError("registration_response_invalid") from exc
    if status != 200 or not isinstance(data, dict) or data.get("ok") is not True:
        reason = str(data.get("error") or f"http_{status}") if isinstance(data, dict) else f"http_{status}"
        raise RegistrationError(reason)
    return data


def assignment_config(device_id: str, response: dict[str, Any]) -> dict[str, Any]:
    if normalize_device_id(response.get("device_id")) != device_id:
        raise RegistrationError("registration_assignment_invalid")
    return {
        "version": CONFIG_VERSION,
        "device_id": device_id,
        "enabled": True,
        "open_package": None,
    }


def write_config_atomic(path: pathlib.Path, config: dict[str, Any]) -> bool:
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        existing = None
    if isinstance(existing, dict):
        core_keys = ("device_id", "enabled")
        open_package = existing.get("open_package")
        open_package_valid = open_package in (None, "") or (
            isinstance(open_package, str)
            and re.fullmatch(r"[A-Za-z0-9._]{1,160}", open_package)
        )
        if all(existing.get(key) == config.get(key) for key in core_keys) and open_package_valid:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        temp.write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return True


def configure(args: argparse.Namespace) -> int:
    device_id = normalize_device_id(args.device_id)
    previous_id = normalize_device_id(args.previous_device_id) if args.previous_device_id else None
    if not device_id:
        raise RegistrationError("invalid_device_id")
    origin, secret = load_agent_auth(pathlib.Path(args.agent_config), args.origin)
    response = post_json(origin, secret, "discover", {
        "device_id": device_id,
        "previous_device_id": previous_id,
    })
    config = assignment_config(device_id, response)
    changed = write_config_atomic(pathlib.Path(args.aot_config), config)
    print("AOT_REGISTRATION=CONFIGURED")
    print("AOT_CONFIG_WRITE=" + ("UPDATED" if changed else "UNCHANGED"))
    print("MODE=FLEET")
    return 0


def reset_identity(args: argparse.Namespace) -> int:
    old_id = normalize_device_id(args.old_device_id)
    new_id = normalize_device_id(args.new_device_id)
    if not old_id or not new_id or old_id == new_id:
        raise RegistrationError("invalid_identity_reset")
    runtime_root = pathlib.Path(args.runtime_root).resolve()
    relay_pids = []
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = [part.decode(errors="replace") for part in (entry / "cmdline").read_bytes().split(b"\0") if part]
        except OSError:
            continue
        for part in command:
            candidate = pathlib.Path(part)
            if candidate.name != "relay.py":
                continue
            try:
                candidate.resolve().relative_to(runtime_root)
            except (OSError, ValueError):
                continue
            relay_pids.append(int(entry.name))
            break
    for pid in sorted(set(relay_pids)):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(pathlib.Path(f"/proc/{pid}").exists() for pid in relay_pids):
        time.sleep(0.1)
    if any(pathlib.Path(f"/proc/{pid}").exists() for pid in relay_pids):
        raise RegistrationError("old_relay_did_not_stop")
    config = json.loads(pathlib.Path(args.aot_config).read_text(encoding="utf-8"))
    origin, secret = load_agent_auth(pathlib.Path(args.agent_config), args.origin)
    post_json(origin, secret, "reset", {
        "old_device_id": old_id,
        "new_device_id": new_id,
    })
    state_root = pathlib.Path(args.state_root)
    for name in LOCAL_IDENTITY_STATE:
        (state_root / name).unlink(missing_ok=True)
    print("AOT_IDENTITY_RESET=OK")
    return 0


def verify(args: argparse.Namespace) -> int:
    device_id = normalize_device_id(args.device_id)
    config = json.loads(pathlib.Path(args.aot_config).read_text(encoding="utf-8"))
    if not device_id or config.get("device_id") != device_id:
        raise RegistrationError("aot_config_identity_mismatch")
    origin, secret = load_agent_auth(pathlib.Path(args.agent_config), args.origin)
    deadline = time.monotonic() + max(1, min(int(args.timeout), 60))
    last_error = "device_not_online_in_aot_hub"
    while time.monotonic() < deadline:
        try:
            data = post_json(origin, secret, "verify", {
                "device_id": device_id,
            })
            if data.get("online") is True and data.get("visible_in_hub") is True:
                print("AOT_SERVER_ONLINE=YES")
                print("AOT_HUB_VISIBLE=YES")
                return 0
        except RegistrationError as exc:
            last_error = str(exc)
        time.sleep(1)
    raise RegistrationError(last_error)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--origin", required=True)
    common.add_argument("--agent-config", required=True)
    common.add_argument("--aot-config", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    configure_parser = sub.add_parser("configure", parents=[common])
    configure_parser.add_argument("--device-id", required=True)
    configure_parser.add_argument("--previous-device-id", default="")
    reset_parser = sub.add_parser("reset-identity", parents=[common])
    reset_parser.add_argument("--old-device-id", required=True)
    reset_parser.add_argument("--new-device-id", required=True)
    reset_parser.add_argument("--state-root", required=True)
    reset_parser.add_argument("--runtime-root", required=True)
    verify_parser = sub.add_parser("verify", parents=[common])
    verify_parser.add_argument("--device-id", required=True)
    verify_parser.add_argument("--timeout", default="30")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "configure":
            return configure(args)
        if args.command == "reset-identity":
            return reset_identity(args)
        if args.command == "verify":
            return verify(args)
        return 2
    except (RegistrationError, OSError, ValueError, json.JSONDecodeError) as exc:
        print("AOT_REGISTRATION=FAILED")
        print("REASON=" + str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
