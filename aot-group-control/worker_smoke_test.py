#!/data/data/com.termux/files/usr/bin/python3
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
schema = json.loads((ROOT / "worker-release-schema.json").read_text(encoding="utf-8"))
if schema.get("schema_version") != 2:
    raise SystemExit("invalid_release_schema")
for name in schema.get("required_files", []):
    if not (ROOT / str(name)).is_file():
        raise SystemExit(f"missing_release_file:{name}")


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    if spec is None or spec.loader is None:
        raise SystemExit(f"import_spec_failed:{filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


updater = load("aot_release_smoke_updater", "updater.py")
if updater.UPDATER_API_VERSION < 2:
    raise SystemExit("updater_api_too_old")
if updater.normalize_channel("canary") != "canary":
    raise SystemExit("canary_channel_broken")
if updater.normalize_channel("stable") != "stable":
    raise SystemExit("stable_channel_broken")
if updater.normalize_channel("other") is not None:
    raise SystemExit("invalid_channel_accepted")

load("aot_release_smoke_controller", "controller.py")
load("aot_release_smoke_runtime", "runtime.py")
relay = load("aot_release_smoke_relay", "relay.py")
if relay.WORKER_VERSION != "aot-worker-2026.08.14.02":
    raise SystemExit("worker_version_mismatch")
if "dynamic_update_channel" not in relay.WORKER_CAPABILITIES:
    raise SystemExit("dynamic_channel_capability_missing")
if "backup_restore_data_semantic" not in relay.WORKER_CAPABILITIES:
    raise SystemExit("backup_restore_data_capability_missing")

relay_source = (ROOT / "relay.py").read_text(encoding="utf-8")
if 'WORKER_VERSION = "aot-worker-2026.08.14.02"' not in relay_source:
    raise SystemExit("worker_version_mismatch")
# Policy: standalone browser-controlled FILTER_RESTORE_DATA is banned.
# BACKUP_RESTORE_DATA is permitted as the fixed, allowlisted, fail-closed
# full-chain action (PR #34).  Verify the banned form is absent and the
# allowlisted full-chain constant is present.
if "FILTER_RESTORE_DATA" in relay_source:
    raise SystemExit("forbidden_restore_action")
if "BACKUP_RESTORE_DATA_ACTION" not in relay_source:
    raise SystemExit("backup_restore_data_action_missing")

print("AOT_WORKER_SMOKE_TEST=OK")
