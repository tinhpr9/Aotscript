#!/data/data/com.termux/files/usr/bin/python3
"""Release-side compatibility helpers for the external bootstrap supervisor."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

UPDATER_API_VERSION = 2
VALID_CHANNELS = {"canary", "stable"}


def _supervisor_root() -> pathlib.Path:
    env_root = os.environ.get("AOT_RUNTIME_ROOT")
    if env_root:
        candidate = pathlib.Path(env_root).resolve()
        if (candidate / "bootstrap_launcher.py").is_file():
            return candidate
    here = pathlib.Path(__file__).resolve()
    for parent in (here.parent.parent, here.parent.parent.parent, pathlib.Path.home() / ".aot-group-control"):
        if (parent / "bootstrap_launcher.py").is_file():
            return parent
    return pathlib.Path.home() / ".aot-group-control"


def _bootstrap_launcher() -> pathlib.Path:
    return _supervisor_root() / "bootstrap_launcher.py"


def _pending_path() -> pathlib.Path:
    return _supervisor_root() / "update_pending.json"


BOOTSTRAP_LAUNCHER = _bootstrap_launcher()
PENDING_PATH = _pending_path()


def normalize_channel(value: object) -> str | None:
    channel = str(value or "").strip().lower()
    return channel if channel in VALID_CHANNELS else None


def notify_healthy(action_id: str, version: str) -> bool:
    launcher = getattr(sys.modules.get(__name__), "BOOTSTRAP_LAUNCHER", None) or _bootstrap_launcher()
    result = subprocess.run(
        [
            sys.executable, str(launcher), "health",
            "--action-id", action_id, "--version", version,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=20,
    )
    return result.returncode == 0


def notify_pending_healthy(running_version: str | None = None) -> bool:
    try:
        pending_path = getattr(sys.modules.get(__name__), "PENDING_PATH", None) or _pending_path()
        pending = json.loads(pathlib.Path(pending_path).read_text(encoding="utf-8"))
        action_id = str(pending["action_id"])
        version = str(pending["version"])
        if running_version is not None and running_version != version:
            return False
    except Exception:
        return False
    return notify_healthy(action_id, version)
