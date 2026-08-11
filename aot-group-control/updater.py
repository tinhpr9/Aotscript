#!/data/data/com.termux/files/usr/bin/python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import py_compile
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent
RELAY_PATH = ROOT / "relay.py"
STATE_ROOT = pathlib.Path("/storage/emulated/0/Download/Shouko")
PENDING_PATH = STATE_ROOT / "aot_worker_update_pending.json"
HEALTH_PATH = STATE_ROOT / "aot_worker_update_health.json"
VERSION_PATH = STATE_ROOT / "aot_worker_version.json"
AGENT_CONFIG_PATH = STATE_ROOT / "agent_config.json"
MANIFEST_URLS = {
    "canary": "https://raw.githubusercontent.com/tinhpr9/Aotscript/main/aot-group-control/worker-manifest-canary.json",
    "stable": "https://raw.githubusercontent.com/tinhpr9/Aotscript/main/aot-group-control/worker-manifest-stable.json",
}
CANARY_DEVICES = {"m37", "m117"}
VALID_STATUSES = {"DOWNLOADING", "VERIFIED", "UPDATED", "ROLLED_BACK", "FAILED"}
HEALTH_TIMEOUT_SECONDS = 60
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024


class UpdateError(RuntimeError):
    pass


def channel_for_device(device_id: str) -> str:
    return "canary" if device_id.lower() in CANARY_DEVICES else "stable"


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise UpdateError(f"invalid_json:{path.name}") from exc
    if not isinstance(value, dict):
        raise UpdateError(f"invalid_json:{path.name}")
    return value


def _write_json_atomic(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        temp.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _download(url: str, destination: pathlib.Path) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise UpdateError("update_url_must_be_https")
    request = urllib.request.Request(url, headers={"User-Agent": "AOT-Worker-Updater"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read(MAX_DOWNLOAD_BYTES + 1)
    except Exception as exc:
        raise UpdateError("update_download_failed") from exc
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise UpdateError("update_download_too_large")
    destination.write_bytes(data)


def load_manifest(channel: str) -> dict[str, str]:
    if channel not in MANIFEST_URLS:
        raise UpdateError("invalid_update_channel")
    with tempfile.TemporaryDirectory(prefix="aot-manifest-") as folder:
        path = pathlib.Path(folder) / "manifest.json"
        _download(MANIFEST_URLS[channel], path)
        raw = _read_json(path)
    version = str(raw.get("version") or "")
    url = str(raw.get("url") or "")
    digest = str(raw.get("sha256") or "").lower()
    if not version or len(version) > 80 or not all(c.isalnum() or c in ".-_" for c in version):
        raise UpdateError("invalid_manifest_version")
    if not url.startswith("https://raw.githubusercontent.com/tinhpr9/Aotscript/"):
        raise UpdateError("invalid_manifest_url")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise UpdateError("invalid_manifest_sha256")
    return {"version": version, "url": url, "sha256": digest, "channel": channel}


def current_version() -> str:
    try:
        return str(_read_json(VERSION_PATH).get("version") or "")
    except UpdateError:
        return ""


def prepare_update(device_id: str, action_id: str, requested_channel: str | None = None) -> dict[str, Any] | None:
    channel = channel_for_device(device_id)
    if requested_channel and requested_channel != channel:
        raise UpdateError("channel_not_allowed_for_device")
    manifest = load_manifest(channel)
    if current_version() == manifest["version"]:
        if hashlib.sha256(RELAY_PATH.read_bytes()).hexdigest() != manifest["sha256"]:
            raise UpdateError("installed_sha256_mismatch")
        try:
            py_compile.compile(str(RELAY_PATH), doraise=True)
        except py_compile.PyCompileError as exc:
            raise UpdateError("installed_py_compile_failed") from exc
        return None
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    temp = RELAY_PATH.with_name(RELAY_PATH.name + f".download-{os.getpid()}")
    backup = RELAY_PATH.with_name(RELAY_PATH.name + f".rollback-{action_id}")
    try:
        _download(manifest["url"], temp)
        actual = hashlib.sha256(temp.read_bytes()).hexdigest()
        if actual != manifest["sha256"]:
            raise UpdateError("sha256_mismatch")
        try:
            py_compile.compile(str(temp), doraise=True)
        except py_compile.PyCompileError as exc:
            raise UpdateError("downloaded_py_compile_failed") from exc
        backup.write_bytes(RELAY_PATH.read_bytes())
        os.chmod(backup, RELAY_PATH.stat().st_mode)
        os.chmod(temp, RELAY_PATH.stat().st_mode)
        pending = {
            **manifest,
            "action_id": action_id,
            "device_id": device_id,
            "backup": str(backup),
            "started_at": int(time.time()),
        }
        _write_json_atomic(PENDING_PATH, pending)
        os.replace(temp, RELAY_PATH)
        return pending
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _agent_auth() -> tuple[str, str]:
    cfg = _read_json(AGENT_CONFIG_PATH)
    report = str(cfg.get("worker_report_url") or "")
    secret = str(cfg.get("agent_report_secret") or "")
    parsed = urllib.parse.urlparse(report)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not secret:
        raise UpdateError("agent_config_invalid")
    origin = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    return origin + "/aot/control/ack", secret


def send_status(pending: dict[str, Any], session_id: str, reference_id: str, status: str) -> None:
    if status not in VALID_STATUSES:
        raise UpdateError("invalid_update_status")
    endpoint, secret = _agent_auth()
    payload = json.dumps({
        "protocol": "phase4-1",
        "session_id": session_id,
        "reference_device_id": reference_id,
        "follower_device_id": pending["device_id"],
        "action_id": pending["action_id"],
        "batch_action": "UPDATE_WORKER",
        "status": status,
        "worker_version": pending.get("version"),
        "channel": pending.get("channel"),
    }, separators=(",", ":")).encode()
    request = urllib.request.Request(endpoint, data=payload, method="POST", headers={
        "Content-Type": "application/json", "X-Agent-Secret": secret,
        "User-Agent": "AOT-Worker-Updater",
    })
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.loads(response.read(128 * 1024).decode("utf-8"))
    if result.get("ok") is not True:
        raise UpdateError("update_ack_rejected")


def acknowledge_online(session_id: str, reference_id: str, device_id: str) -> bool:
    try:
        pending = _read_json(PENDING_PATH)
    except UpdateError:
        return False
    if pending.get("device_id") != device_id:
        return False
    if not str(pending.get("action_id") or "").startswith("startup-"):
        send_status(pending, session_id, reference_id, "UPDATED")
    _write_json_atomic(HEALTH_PATH, {
        "action_id": pending["action_id"], "version": pending["version"],
        "acknowledged_at": int(time.time()),
    })
    _write_json_atomic(VERSION_PATH, {
        "version": pending["version"], "channel": pending["channel"],
        "updated_at": int(time.time()),
    })
    return True


def wait_for_health(pending: dict[str, Any], timeout: int = HEALTH_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            health = _read_json(HEALTH_PATH)
        except UpdateError:
            health = {}
        if health.get("action_id") == pending.get("action_id") and health.get("version") == pending.get("version"):
            pathlib.Path(str(pending["backup"])).unlink(missing_ok=True)
            PENDING_PATH.unlink(missing_ok=True)
            return True
        time.sleep(0.25)
    return False


def rollback(pending: dict[str, Any]) -> None:
    backup = pathlib.Path(str(pending.get("backup") or ""))
    if not backup.is_file() or backup.parent != ROOT:
        raise UpdateError("rollback_backup_missing")
    os.replace(backup, RELAY_PATH)
    PENDING_PATH.unlink(missing_ok=True)


def action_update(args: argparse.Namespace) -> int:
    pending: dict[str, Any] | None = None
    try:
        placeholder = {"device_id": args.device_id, "action_id": args.action_id, "channel": args.channel}
        send_status(placeholder, args.session, args.reference_device, "DOWNLOADING")
        pending = prepare_update(args.device_id, args.action_id, args.channel)
        if pending is None:
            placeholder["version"] = current_version()
            send_status(placeholder, args.session, args.reference_device, "VERIFIED")
            send_status(placeholder, args.session, args.reference_device, "UPDATED")
            return 0
        send_status(pending, args.session, args.reference_device, "VERIFIED")
        try:
            os.kill(args.parent_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        time.sleep(0.5)
        with open(os.devnull, "rb") as null_in, open(os.devnull, "ab") as null_out:
            process = subprocess.Popen(args.relay_command, stdin=null_in, stdout=null_out, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
        if wait_for_health(pending):
            return 0
        try:
            os.kill(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        rollback(pending)
        subprocess.Popen(args.relay_command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
        send_status(pending, args.session, args.reference_device, "ROLLED_BACK")
        return 3
    except Exception:
        if pending is not None:
            try:
                rollback(pending)
            except Exception:
                pass
        try:
            send_status(pending or {"device_id": args.device_id, "action_id": args.action_id, "channel": args.channel}, args.session, args.reference_device, "FAILED")
        except Exception:
            pass
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    action = sub.add_parser("action")
    action.add_argument("--device-id", required=True)
    action.add_argument("--action-id", required=True)
    action.add_argument("--channel", choices=("canary", "stable"), required=True)
    action.add_argument("--session", required=True)
    action.add_argument("--reference-device", required=True)
    action.add_argument("--parent-pid", type=int, required=True)
    action.add_argument("relay_command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "action":
        if not args.relay_command:
            raise SystemExit("relay_command_required")
        return action_update(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
