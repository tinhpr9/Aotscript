#!/data/data/com.termux/files/usr/bin/python3
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
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

controller = load("aot_release_smoke_controller", "controller.py")
load("aot_release_smoke_runtime", "runtime.py")
relay = load("aot_release_smoke_relay", "relay.py")
if not re.fullmatch(r"^aot-worker-[12]\d{3}\.(0[1-9]|1[0-2])\.(0[1-9]|[12]\d|3[01])\.[0-9]{2}$", relay.WORKER_VERSION):
    raise SystemExit("worker_version_mismatch")
if "dynamic_update_channel" not in relay.WORKER_CAPABILITIES:
    raise SystemExit("dynamic_channel_capability_missing")
if "backup_restore_data_semantic" not in relay.WORKER_CAPABILITIES:
    raise SystemExit("backup_restore_data_capability_missing")
if "allocate_server_2pc" not in relay.WORKER_CAPABILITIES:
    raise SystemExit("allocate_server_capability_missing")

relay_source = (ROOT / "relay.py").read_text(encoding="utf-8")
if f'WORKER_VERSION = "{relay.WORKER_VERSION}"' not in relay_source:
    raise SystemExit("worker_version_mismatch")
# Policy: standalone browser-controlled FILTER_RESTORE_DATA is banned.
# BACKUP_RESTORE_DATA is permitted as the fixed, allowlisted, fail-closed
# full-chain action (PR #34).  Verify the banned form is absent and the
# allowlisted full-chain constant is present.
if "FILTER_RESTORE_DATA" in relay_source:
    raise SystemExit("forbidden_restore_action")
if "BACKUP_RESTORE_DATA_ACTION" not in relay_source:
    raise SystemExit("backup_restore_data_action_missing")
if "ALLOCATE_SERVER_ACTION" not in relay_source:
    raise SystemExit("allocate_server_action_missing")

# ALLOCATE_SERVER Smoke Guard:
# 1. Test non-root dispatch path exercises userspace am start
recorded_subproc_calls = []
orig_subprocess_run = controller.subprocess.run
orig_root_avail = controller.root_available
orig_root_run = controller._root_run

class MockCompletedProc:
    def __init__(self, rc=0, stderr=""):
        self.returncode = rc
        self.stderr = stderr

def mock_subproc_run(argv, *args, **kwargs):
    recorded_subproc_calls.append(list(argv))
    return MockCompletedProc(rc=0)

try:
    controller.root_available = lambda: False
    controller.subprocess.run = mock_subproc_run
    controller.open_roblox_servers([
        {"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/97598239454123?privateServerLinkCode=11111111111111111111111111111111"}
    ])
    if len(recorded_subproc_calls) != 1:
        raise SystemExit("allocate_server_nonroot_dispatch_missing")
    if recorded_subproc_calls[0][:4] != ["am", "start", "-a", "android.intent.action.VIEW"]:
        raise SystemExit("allocate_server_intent_format_invalid")

    # 2. Non-root fail-closed on non-zero exit code
    controller.subprocess.run = lambda argv, *args, **kwargs: MockCompletedProc(rc=1, stderr="ActivityNotFound")
    try:
        controller.open_roblox_servers([{"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123"}])
        raise SystemExit("allocate_server_nonroot_nonzero_not_fail_closed")
    except controller.AotControllerError:
        pass

    # 3. Root path verification
    recorded_root_calls = []
    controller.root_available = lambda: True
    controller._root_run = lambda cmd, *args, **kwargs: (
        "uid=0(root) gid=0(root)" if cmd == "id" else recorded_root_calls.append(cmd) or "OK"
    )
    controller.open_roblox_servers([
        {"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/97598239454123?privateServerLinkCode=11111111111111111111111111111111"}
    ])
    if len(recorded_root_calls) != 1 or not recorded_root_calls[0].startswith("am start -a android.intent.action.VIEW"):
        raise SystemExit("allocate_server_root_dispatch_failed")

finally:
    controller.subprocess.run = orig_subprocess_run
    controller.root_available = orig_root_avail
    controller._root_run = orig_root_run

print("AOT_WORKER_SMOKE_TEST=OK")
