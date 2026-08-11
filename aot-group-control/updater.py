#!/data/data/com.termux/files/usr/bin/python3
"""Release-side compatibility helpers for the external bootstrap supervisor."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

UPDATER_API_VERSION = 2
CANARY_DEVICES = {"m37", "m117"}
BOOTSTRAP_LAUNCHER = pathlib.Path.home() / ".aot-group-control" / "bootstrap_launcher.py"
PENDING_PATH = pathlib.Path.home() / ".aot-group-control" / "update_pending.json"


def channel_for_device(device_id: str) -> str:
    return "canary" if str(device_id).lower() in CANARY_DEVICES else "stable"


def notify_healthy(action_id: str, version: str) -> bool:
    result = subprocess.run(
        [
            sys.executable, str(BOOTSTRAP_LAUNCHER), "health",
            "--action-id", action_id, "--version", version,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=20,
    )
    return result.returncode == 0


def notify_pending_healthy() -> bool:
    try:
        pending = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
        action_id = str(pending["action_id"])
        version = str(pending["version"])
    except Exception:
        return False
    return notify_healthy(action_id, version)
