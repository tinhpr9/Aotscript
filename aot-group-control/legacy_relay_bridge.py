#!/data/data/com.termux/files/usr/bin/python3
"""One-shot bridge from the schema-1 relay updater to Bootstrap v2.

Worker 2026.08.11.1 replaces relay.py with the manifest's top-level asset.
This deliberately self-contained asset verifies and installs the external
supervisor, then resumes the already authenticated update action recorded by
the legacy updater. It never reads or rewrites device configuration.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import py_compile
import subprocess
import sys
import tempfile
import urllib.request

STATE_ROOT = pathlib.Path("/storage/emulated/0/Download/Shouko")
LEGACY_PENDING = STATE_ROOT / "aot_worker_update_pending.json"
AOT_CONFIG = STATE_ROOT / "aot_group_config.json"
SUPERVISOR_ROOT = pathlib.Path.home() / ".aot-group-control"
ASSETS = {
    "bootstrap_launcher.py": {
        "url": "https://raw.githubusercontent.com/tinhpr9/Aotscript/main/aot-group-control/bootstrap_launcher.py",
        "sha256": "3740036511adb056f26571958302445eed337abb31bdb73568fb831a681997d6",
    },
    "bootstrap.py": {
        "url": "https://raw.githubusercontent.com/tinhpr9/Aotscript/main/aot-group-control/bootstrap.py",
        "sha256": "2015a7b5895fa810ca268a1d9666f310ca9f4eb9c44dc377d0f30b54f68f761a",
    },
}


def fail(reason: str) -> int:
    print("AOT_LEGACY_BRIDGE=FAILED")
    print("REASON=" + reason)
    return 2


def read_pending() -> dict[str, object]:
    value = json.loads(LEGACY_PENDING.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("legacy_pending_invalid")
    required = ("action_id", "channel", "device_id")
    if any(not isinstance(value.get(key), str) or not value[key] for key in required):
        raise ValueError("legacy_pending_invalid")
    if value["channel"] not in {"canary", "stable"}:
        raise ValueError("legacy_channel_invalid")
    return value


def read_reference_device() -> str:
    value = json.loads(AOT_CONFIG.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("enabled") is not True:
        raise ValueError("legacy_aot_config_invalid")
    session_id = str(value.get("session_id") or "")
    reference = str(value.get("reference_device_id") or "")
    role = str(value.get("role") or "").lower()
    if not session_id or role not in {"reference", "follower"}:
        raise ValueError("legacy_aot_config_invalid")
    if role == "reference":
        reference = str(value.get("device_id") or reference)
    if not reference:
        raise ValueError("legacy_reference_missing")
    return reference


def download_verified(url: str, digest: str, target: pathlib.Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "AOT-Legacy-Bridge/1"})
    with urllib.request.urlopen(request, timeout=20) as response:
        content = response.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise ValueError("legacy_asset_too_large")
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("legacy_asset_sha256_mismatch")
    target.write_bytes(content)
    py_compile.compile(str(target), doraise=True)


def install_supervisor(stage: pathlib.Path) -> None:
    SUPERVISOR_ROOT.mkdir(parents=True, exist_ok=True)
    bootstrap = stage / "bootstrap.py"
    result = subprocess.run(
        [sys.executable, str(bootstrap), "self-test"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, check=False, timeout=20,
    )
    if result.returncode != 0:
        raise ValueError("legacy_bootstrap_smoke_failed")
    for name in ("bootstrap_launcher.py", "bootstrap.py"):
        source = stage / name
        target = SUPERVISOR_ROOT / name
        temporary = target.with_name("." + name + f".legacy-{os.getpid()}")
        temporary.write_bytes(source.read_bytes())
        py_compile.compile(str(temporary), doraise=True)
        os.chmod(temporary, 0o700)
        if name == "bootstrap.py" and target.is_file():
            backup = SUPERVISOR_ROOT / "bootstrap.py.last_good"
            backup.write_bytes(target.read_bytes())
            py_compile.compile(str(backup), doraise=True)
        os.replace(temporary, target)


def main() -> int:
    try:
        pending = read_pending()
        with tempfile.TemporaryDirectory(prefix="aot-legacy-bridge-") as folder:
            stage = pathlib.Path(folder)
            for name, asset in ASSETS.items():
                download_verified(asset["url"], asset["sha256"], stage / name)
            install_supervisor(stage)
        reference = read_reference_device()
        command = [
            sys.executable, str(SUPERVISOR_ROOT / "bootstrap_launcher.py"),
            "update-action", "--action-id", str(pending["action_id"]),
            "--channel", str(pending["channel"]),
            "--reference-device", reference,
        ]
        os.execv(sys.executable, command)
    except Exception as exc:
        return fail(str(exc)[:160] or type(exc).__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
