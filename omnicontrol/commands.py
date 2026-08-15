"""Command contract for AOT OmniControl.

Each entry maps a slash-command name to metadata used by the Router.
No business logic here – pure data / contract definitions.

Destructive = True means the Router must require explicit confirmation
before dispatching to AOT Hub.

ai_required = True means the command may only proceed after an AI
analysis step (Phase 2+). In Phase 1 every command has ai_required=False.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Device / group validation (mirrors normalizeDeviceId / normalizeDeviceGroup
# from cloudflare-worker/worker.js – kept in sync, no duplication of logic)
# ---------------------------------------------------------------------------

DEVICE_ID_RE = re.compile(r"^m([1-9]\d{0,5})$", re.IGNORECASE)
VALID_GROUPS = frozenset({"NOVA", "MARMOT"})


def validate_device_id(value: str) -> Optional[str]:
    """Return the normalised device ID, or None if invalid.

    Accepts m1–m999999 (case-insensitive).
    """
    m = DEVICE_ID_RE.match(str(value or "").strip())
    if not m:
        return None
    return f"m{m.group(1)}"


def validate_group(value: str) -> Optional[str]:
    """Return the normalised group name ('NOVA' or 'MARMOT'), or None."""
    normalised = str(value or "").strip().upper()
    return normalised if normalised in VALID_GROUPS else None


# ---------------------------------------------------------------------------
# Command schema
# ---------------------------------------------------------------------------

class ArgKind(str, Enum):
    NONE = "none"          # no argument required
    DEVICE = "device"      # single device ID
    GROUP = "group"        # group name
    DEVICE_OR_GROUP = "device_or_group"


@dataclass(frozen=True)
class CommandDef:
    name: str
    description: str
    arg_kind: ArgKind = ArgKind.NONE
    destructive: bool = False
    ai_required: bool = False     # Phase 2+; never True in Phase 1
    # Hub control kinds dispatched for this command (empty = read-only / local)
    hub_action: Optional[str] = None


# ---------------------------------------------------------------------------
# Allowlisted commands – Phase 1
# Router MUST reject any command not in this registry.
# ---------------------------------------------------------------------------

COMMAND_REGISTRY: dict[str, CommandDef] = {
    "status": CommandDef(
        name="status",
        description="Show fleet status or status of a specific device.",
        arg_kind=ArgKind.NONE,          # /status  or  /status <device>
    ),
    "status_device": CommandDef(
        name="status_device",
        description="Show status of a specific device.",
        arg_kind=ArgKind.DEVICE,
    ),
    "start": CommandDef(
        name="start",
        description="Start (open Swift Backup) on a device.",
        arg_kind=ArgKind.DEVICE,
        destructive=False,
        hub_action="open_swift_backup",
    ),
    "batch": CommandDef(
        name="batch",
        description="Run an action on all devices in a group.",
        arg_kind=ArgKind.GROUP,
        destructive=True,
        hub_action="batch_group",  # router resolves to Hub control kind
    ),
    "update": CommandDef(
        name="update",
        description="Trigger canary/stable worker update.",
        arg_kind=ArgKind.DEVICE_OR_GROUP,
        destructive=True,
        hub_action="update_canary",   # router maps device→canary, group→stable
    ),
}


# ---------------------------------------------------------------------------
# Batch action allow-list (for /batch <group> <action>)
# Mirrors the Hub control kind enum; raw shell or tap coords are NEVER allowed.
# ---------------------------------------------------------------------------

BATCH_ACTION_ALLOWLIST: frozenset[str] = frozenset({
    "open_swift_backup",
    "open_swift_apps",
    "backup_restore_data",
})


def validate_batch_action(value: str) -> Optional[str]:
    """Return the normalised batch action or None if not in allow-list."""
    normalised = str(value or "").strip().lower()
    return normalised if normalised in BATCH_ACTION_ALLOWLIST else None
