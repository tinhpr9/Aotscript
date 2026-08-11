#!/data/data/com.termux/files/usr/bin/python3
"""Versioned AOT worker supervisor, installed outside worker releases."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import pathlib
import py_compile
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from typing import Any, Iterator

BOOTSTRAP_VERSION = 2
ROOT = pathlib.Path(__file__).resolve().parent
RELEASES = ROOT / "releases"
CURRENT = ROOT / "current"
LAST_GOOD = ROOT / "last_good"
LOCK_PATH = ROOT / "supervisor.lock"
PENDING_PATH = ROOT / "update_pending.json"
HEALTH_PATH = ROOT / "update_health.json"
VERSION_PATH = ROOT / "installed_release.json"
STATE_ROOT = pathlib.Path("/storage/emulated/0/Download/Shouko")
CONFIG_PATH = STATE_ROOT / "aot_group_config.json"
DEVICE_ID_PATH = STATE_ROOT / "device_id.txt"
AGENT_CONFIG_PATH = STATE_ROOT / "agent_config.json"
LOG_PATH = pathlib.Path("/storage/emulated/0/Download/AOT_Group_Control.log")
MANIFEST_URLS = {
    "canary": "https://raw.githubusercontent.com/tinhpr9/Aotscript/main/aot-group-control/worker-manifest-canary.json",
    "stable": "https://raw.githubusercontent.com/tinhpr9/Aotscript/main/aot-group-control/worker-manifest-stable.json",
}
CANARY_DEVICES = {"m37", "m117"}
UPDATE_STATUSES = {
    "DOWNLOADING", "VERIFIED", "INSTALLING", "RESTARTING",
    "HEALTHY", "ROLLED_BACK", "FAILED",
}
REQUIRED_FILES = {
    "relay.py", "runtime.py", "controller.py", "updater.py", "e2e.py",
    "worker_smoke_test.py", "worker-release-schema.json",
    "msetup_registration.py",
}
HEALTH_TIMEOUT_SECONDS = 60
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_RELEASE_FILES = 32


class BootstrapError(RuntimeError):
    pass


def channel_for_device(device_id: str) -> str:
    return "canary" if device_id.lower() in CANARY_DEVICES else "stable"


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BootstrapError(f"invalid_json:{path.name}") from exc
    if not isinstance(value, dict):
        raise BootstrapError(f"invalid_json:{path.name}")
    return value


def _write_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        temp.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _download(url: str, destination: pathlib.Path) -> None:
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "raw.githubusercontent.com"
        or not parsed.path.startswith("/tinhpr9/Aotscript/")
    ):
        raise BootstrapError("untrusted_download_url")
    request = urllib.request.Request(url, headers={"User-Agent": "AOT-Worker-Bootstrap"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read(MAX_FILE_BYTES + 1)
    except Exception as exc:
        raise BootstrapError("download_failed") from exc
    if len(data) > MAX_FILE_BYTES:
        raise BootstrapError("download_too_large")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def _valid_version(value: Any) -> str | None:
    raw = str(value or "")
    return raw if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", raw) else None


def _valid_release_path(value: Any) -> str | None:
    raw = str(value or "")
    if not raw or raw.startswith("/") or ".." in pathlib.PurePosixPath(raw).parts:
        return None
    return raw if re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", raw) else None


def validate_manifest(raw: Any, expected_channel: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema_version") != 2:
        raise BootstrapError("invalid_manifest_schema")
    version = _valid_version(raw.get("version"))
    channel = str(raw.get("channel") or "")
    minimum = raw.get("minimum_bootstrap_version")
    files = raw.get("files")
    if not version or channel != expected_channel or not isinstance(minimum, int) or minimum < 1:
        raise BootstrapError("invalid_manifest_metadata")
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_RELEASE_FILES:
        raise BootstrapError("invalid_manifest_files")
    clean_files = []
    paths = set()
    for item in files:
        path = _valid_release_path(item.get("path") if isinstance(item, dict) else None)
        url = str(item.get("url") or "") if isinstance(item, dict) else ""
        digest = str(item.get("sha256") or "").lower() if isinstance(item, dict) else ""
        parsed = urllib.parse.urlparse(url)
        if (
            not path or path in paths or parsed.scheme != "https"
            or parsed.netloc != "raw.githubusercontent.com"
            or not parsed.path.startswith("/tinhpr9/Aotscript/")
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise BootstrapError("invalid_manifest_file")
        paths.add(path)
        clean_files.append({"path": path, "url": url, "sha256": digest})
    if not REQUIRED_FILES.issubset(paths):
        raise BootstrapError("manifest_required_files_missing")
    clean: dict[str, Any] = {
        "schema_version": 2, "version": version, "channel": channel,
        "minimum_bootstrap_version": minimum, "files": clean_files,
    }
    bootstrap = raw.get("bootstrap")
    if bootstrap is not None:
        if not isinstance(bootstrap, dict):
            raise BootstrapError("invalid_bootstrap_manifest")
        bootstrap_version = bootstrap.get("version")
        url = str(bootstrap.get("url") or "")
        digest = str(bootstrap.get("sha256") or "").lower()
        if (
            not isinstance(bootstrap_version, int) or bootstrap_version < 1
            or not url.startswith("https://raw.githubusercontent.com/tinhpr9/Aotscript/")
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise BootstrapError("invalid_bootstrap_manifest")
        clean["bootstrap"] = {"version": bootstrap_version, "url": url, "sha256": digest}
    return clean


def load_manifest(channel: str) -> dict[str, Any]:
    if channel not in MANIFEST_URLS:
        raise BootstrapError("invalid_channel")
    with tempfile.TemporaryDirectory(prefix="aot-worker-manifest-") as folder:
        target = pathlib.Path(folder) / "manifest.json"
        _download(MANIFEST_URLS[channel], target)
        return validate_manifest(_read_json(target), channel)


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _release_valid(path: pathlib.Path, manifest: dict[str, Any]) -> bool:
    try:
        return all(
            (path / item["path"]).is_file()
            and _sha256(path / item["path"]) == item["sha256"]
            for item in manifest["files"]
        )
    except OSError:
        return False


def stage_release(manifest: dict[str, Any], action_id: str) -> pathlib.Path:
    RELEASES.mkdir(parents=True, exist_ok=True)
    final = RELEASES / manifest["version"]
    if final.is_dir() and _release_valid(final, manifest):
        return final
    staging = RELEASES / f".staging-{manifest['version']}-{action_id}-{os.getpid()}"
    if staging.exists():
        raise BootstrapError("staging_path_exists")
    staging.mkdir(mode=0o700)
    try:
        for item in manifest["files"]:
            destination = staging / item["path"]
            _download(item["url"], destination)
            if _sha256(destination) != item["sha256"]:
                raise BootstrapError(f"sha256_mismatch:{item['path']}")
        for path in staging.rglob("*.py"):
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as exc:
                raise BootstrapError(f"py_compile_failed:{path.name}") from exc
        smoke = subprocess.run(
            [sys.executable, str(staging / "worker_smoke_test.py")],
            cwd=staging, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=30,
        )
        if smoke.returncode != 0:
            raise BootstrapError("release_smoke_test_failed")
        if final.exists():
            raise BootstrapError("release_version_conflict")
        os.replace(staging, final)
        return final
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _atomic_link(link: pathlib.Path, target: pathlib.Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    temp = link.with_name(link.name + f".tmp-{os.getpid()}")
    temp.unlink(missing_ok=True)
    os.symlink(str(target.resolve()), temp)
    os.replace(temp, link)


def _link_target(link: pathlib.Path) -> pathlib.Path | None:
    try:
        target = link.resolve(strict=True)
    except OSError:
        return None
    try:
        target.relative_to(RELEASES.resolve())
    except ValueError:
        return None
    return target if target.is_dir() else None


def activate_release(release: pathlib.Path, pending: dict[str, Any]) -> None:
    previous = _link_target(CURRENT)
    if previous and previous != release:
        _atomic_link(LAST_GOOD, previous)
        pending["previous_release"] = str(previous)
    elif previous:
        pending["previous_release"] = str(previous)
    _write_json(PENDING_PATH, pending)
    _atomic_link(CURRENT, release)


def rollback_release(pending: dict[str, Any]) -> pathlib.Path:
    previous = pathlib.Path(str(pending.get("previous_release") or ""))
    if not previous.is_dir():
        previous = _link_target(LAST_GOOD) or pathlib.Path()
    try:
        previous.resolve().relative_to(RELEASES.resolve())
    except (OSError, ValueError):
        raise BootstrapError("last_good_unavailable")
    _atomic_link(CURRENT, previous)
    PENDING_PATH.unlink(missing_ok=True)
    return previous


def ensure_legacy_release() -> pathlib.Path | None:
    current = _link_target(CURRENT)
    if current:
        return current
    legacy_names = {"relay.py", "runtime.py", "controller.py", "updater.py", "e2e.py"}
    if not all((ROOT / name).is_file() for name in legacy_names):
        return None
    legacy = RELEASES / "legacy-pr13"
    legacy.mkdir(parents=True, exist_ok=True)
    for name in legacy_names:
        destination = legacy / name
        if not destination.exists():
            destination.write_bytes((ROOT / name).read_bytes())
    _atomic_link(CURRENT, legacy)
    _atomic_link(LAST_GOOD, legacy)
    return legacy


def _config() -> tuple[dict[str, Any], str]:
    config = _read_json(CONFIG_PATH)
    device_id = DEVICE_ID_PATH.read_text(encoding="utf-8").strip().lower()
    if not re.fullmatch(r"m[1-9]\d{0,5}", device_id):
        raise BootstrapError("invalid_device_id")
    configured_device_id = str(config.get("device_id") or "").strip().lower()
    if configured_device_id != device_id:
        raise BootstrapError("worker_config_identity_mismatch")
    role = str(config.get("role") or "")
    session = str(config.get("session_id") or "")
    if config.get("enabled") is not True or role not in {"reference", "follower"}:
        raise BootstrapError("invalid_worker_config")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session):
        raise BootstrapError("invalid_worker_session")
    return config, device_id


def relay_command(config: dict[str, Any]) -> list[str]:
    command = [sys.executable, "-u", str(CURRENT / "relay.py"), str(config["role"]), "--session", str(config["session_id"])]
    if config["role"] == "follower":
        command.extend(["--reference-device", str(config.get("reference_device_id") or "")])
    if config.get("open_package"):
        command.extend(["--open-package", str(config["open_package"])])
    return command


def _relay_pids() -> list[int]:
    result = []
    root = str(RELEASES.resolve()) + os.sep
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            parts = [p.decode(errors="replace") for p in (entry / "cmdline").read_bytes().split(b"\0") if p]
        except OSError:
            continue
        if any(pathlib.Path(part).name == "relay.py" and str(pathlib.Path(part).resolve()).startswith(root) for part in parts):
            result.append(int(entry.name))
    return sorted(set(result))


def stop_workers(except_pid: int | None = None) -> None:
    pids = [pid for pid in _relay_pids() if pid != except_pid]
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(pathlib.Path(f"/proc/{pid}").exists() for pid in pids):
        time.sleep(0.1)


def start_worker(config: dict[str, Any]) -> subprocess.Popen[bytes]:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("ab") as log:
        return subprocess.Popen(
            relay_command(config), stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True, close_fds=True,
        )


def _agent_endpoint() -> tuple[str, str]:
    data = _read_json(AGENT_CONFIG_PATH)
    report = str(data.get("worker_report_url") or "")
    secret = str(data.get("agent_report_secret") or "")
    parsed = urllib.parse.urlparse(report)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not secret:
        raise BootstrapError("agent_config_invalid")
    origin = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    return origin + "/aot/control/ack", secret


def send_status(pending: dict[str, Any], status: str) -> None:
    if status not in UPDATE_STATUSES:
        raise BootstrapError("invalid_update_status")
    endpoint, secret = _agent_endpoint()
    payload = json.dumps({
        "protocol": "phase4-1", "session_id": pending["session_id"],
        "reference_device_id": pending["reference_device_id"],
        "follower_device_id": pending["device_id"], "action_id": pending["action_id"],
        "batch_action": "UPDATE_WORKER", "status": status,
        "worker_version": pending.get("version"), "channel": pending.get("channel"),
    }, separators=(",", ":")).encode()
    request = urllib.request.Request(endpoint, data=payload, method="POST", headers={
        "Content-Type": "application/json", "X-Agent-Secret": secret,
        "User-Agent": "AOT-Worker-Bootstrap",
    })
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read(128 * 1024).decode("utf-8"))
    except Exception as exc:
        raise BootstrapError("update_ack_failed") from exc
    if result.get("ok") is not True:
        raise BootstrapError("update_ack_rejected")


def wait_for_health(pending: dict[str, Any], timeout: float = HEALTH_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            health = _read_json(HEALTH_PATH)
        except BootstrapError:
            health = {}
        if health.get("action_id") == pending.get("action_id") and health.get("version") == pending.get("version"):
            PENDING_PATH.unlink(missing_ok=True)
            _write_json(VERSION_PATH, {"version": pending["version"], "channel": pending["channel"], "healthy_at": int(time.time())})
            return True
        time.sleep(0.25)
    return False


def notify_health(action_id: str, version: str) -> int:
    pending = _read_json(PENDING_PATH)
    if pending.get("action_id") != action_id or pending.get("version") != version:
        raise BootstrapError("health_identity_mismatch")
    if not action_id.startswith("startup-"):
        send_status(pending, "HEALTHY")
    _write_json(HEALTH_PATH, {"action_id": action_id, "version": version, "acknowledged_at": int(time.time())})
    return 0


@contextlib.contextmanager
def supervisor_lock() -> Iterator[None]:
    ROOT.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BootstrapError("update_already_running") from exc
        yield


def maybe_upgrade_bootstrap(manifest: dict[str, Any]) -> None:
    item = manifest.get("bootstrap")
    if not item or int(item["version"]) <= BOOTSTRAP_VERSION:
        if manifest["minimum_bootstrap_version"] > BOOTSTRAP_VERSION:
            raise BootstrapError("bootstrap_upgrade_required")
        return
    temp = ROOT / f"bootstrap.py.next-{os.getpid()}"
    backup = ROOT / "bootstrap.py.last_good"
    try:
        _download(item["url"], temp)
        if _sha256(temp) != item["sha256"]:
            raise BootstrapError("bootstrap_sha256_mismatch")
        py_compile.compile(str(temp), doraise=True)
        checked = subprocess.run([sys.executable, str(temp), "self-test"], stdin=subprocess.DEVNULL, check=False, timeout=20)
        if checked.returncode != 0:
            raise BootstrapError("bootstrap_self_test_failed")
        active = ROOT / "bootstrap.py"
        backup.write_bytes(active.read_bytes())
        os.chmod(backup, 0o700)
        os.chmod(temp, 0o700)
        os.replace(temp, active)
    except py_compile.PyCompileError as exc:
        raise BootstrapError("bootstrap_py_compile_failed") from exc
    finally:
        temp.unlink(missing_ok=True)


def install_manifest(manifest: dict[str, Any], pending: dict[str, Any], report: bool) -> pathlib.Path:
    maybe_upgrade_bootstrap(manifest)
    release = stage_release(manifest, pending["action_id"])
    if report:
        send_status(pending, "VERIFIED")
        send_status(pending, "INSTALLING")
    activate_release(release, pending)
    return release


def _run_update(config: dict[str, Any], device_id: str, pending: dict[str, Any], report: bool) -> int:
    manifest = load_manifest(pending["channel"])
    pending["version"] = manifest["version"]
    if report:
        send_status(pending, "DOWNLOADING")
    current = _link_target(CURRENT)
    if current and current.name == manifest["version"] and _release_valid(current, manifest):
        if not report:
            start_worker(config)
            return 0
        send_status(pending, "VERIFIED")
        send_status(pending, "INSTALLING")
        pending["previous_release"] = str(current)
        _write_json(PENDING_PATH, pending)
        stop_workers()
        process = start_worker(config)
        send_status(pending, "RESTARTING")
        if wait_for_health(pending):
            return 0
        try:
            os.kill(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        start_worker(config)
        send_status(pending, "ROLLED_BACK")
        return 3
    install_manifest(manifest, pending, report)
    stop_workers()
    process = start_worker(config)
    if report:
        send_status(pending, "RESTARTING")
    if wait_for_health(pending):
        return 0
    try:
        os.kill(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    rollback_release(pending)
    start_worker(config)
    if report:
        send_status(pending, "ROLLED_BACK")
    return 3


def action_update(args: argparse.Namespace) -> int:
    pending: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    try:
        with supervisor_lock():
            config, device_id = _config()
            channel = channel_for_device(device_id)
            if channel != args.channel:
                raise BootstrapError("channel_not_allowed_for_device")
            pending = {
                "action_id": args.action_id, "channel": channel,
                "device_id": device_id, "session_id": str(config["session_id"]),
                "reference_device_id": args.reference_device,
                "started_at": int(time.time()),
            }
            return _run_update(config, device_id, pending, True)
    except Exception:
        if pending:
            try:
                previous = pathlib.Path(str(pending.get("previous_release") or ""))
                current = _link_target(CURRENT)
                if config and previous.is_dir() and current and current != previous:
                    stop_workers()
                    rollback_release(pending)
                    start_worker(config)
                    send_status(pending, "ROLLED_BACK")
                else:
                    send_status(pending, "FAILED")
            except Exception:
                pass
        return 2


def startup() -> int:
    with supervisor_lock():
        config, device_id = _config()
        ensure_legacy_release()
        stop_workers()
        pending = {
            "action_id": f"startup-{int(time.time())}-{os.getpid()}",
            "channel": channel_for_device(device_id), "device_id": device_id,
            "session_id": str(config["session_id"]),
            "reference_device_id": str(config.get("reference_device_id") or device_id),
            "started_at": int(time.time()),
        }
        try:
            return _run_update(config, device_id, pending, False)
        except BootstrapError:
            previous = pathlib.Path(str(pending.get("previous_release") or ""))
            current = _link_target(CURRENT)
            if previous.is_dir() and current and current != previous:
                stop_workers()
                rollback_release(pending)
            if not _link_target(CURRENT):
                raise
            start_worker(config)
            return 0


def self_test() -> int:
    if BOOTSTRAP_VERSION < 2 or channel_for_device("m37") != "canary" or channel_for_device("m38") != "stable":
        return 1
    print(f"AOT_BOOTSTRAP_VERSION={BOOTSTRAP_VERSION}")
    return 0


def stop_supervised_worker() -> int:
    with supervisor_lock():
        stop_workers()
        PENDING_PATH.unlink(missing_ok=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AOT versioned worker supervisor")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start")
    sub.add_parser("stop")
    sub.add_parser("self-test")
    health = sub.add_parser("health")
    health.add_argument("--action-id", required=True)
    health.add_argument("--version", required=True)
    action = sub.add_parser("update-action")
    action.add_argument("--action-id", required=True)
    action.add_argument("--channel", choices=("canary", "stable"), required=True)
    action.add_argument("--reference-device", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "self-test":
            return self_test()
        if args.command == "health":
            return notify_health(args.action_id, args.version)
        if args.command == "update-action":
            return action_update(args)
        if args.command == "start":
            return startup()
        if args.command == "stop":
            return stop_supervised_worker()
        return 2
    except BootstrapError as exc:
        print("AOT_BOOTSTRAP=FAILED")
        print("REASON=" + str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
