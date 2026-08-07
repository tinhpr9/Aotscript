#!/data/data/com.termux/files/usr/bin/python3
from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
RELAY = ROOT / "relay.py"

spec = importlib.util.spec_from_file_location(
    "aot_relay",
    RELAY,
)
if spec is None or spec.loader is None:
    raise SystemExit("RELAY_SELFTEST_IMPORT=FAILED")

module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.normalize_device_id("M117") == "m117"
assert module.normalize_device_id("m0") is None
assert module.normalize_device_id("m1000000") is None

assert module.normalize_session_id("m37-m117") == "m37-m117"
assert module.normalize_session_id("bad session") is None

assert module.normalize_action_id("tap-123_ab") == "tap-123_ab"
assert module.normalize_action_id("bad action") is None

url = module.websocket_url(
    "https://example.test/agent/report",
    device_id="m117",
    role="follower",
    session_id="m37-m117",
)
assert url.startswith("wss://example.test/aot/control/ws?")
assert "device_id=m117" in url
assert "role=follower" in url
assert "session_id=m37-m117" in url

old_state_path = module.STATE_PATH
try:
    with tempfile.TemporaryDirectory() as tmp:
        module.STATE_PATH = pathlib.Path(tmp) / "state.json"
        state = module._load_state()
        assert not module.action_already_processed(
            state,
            "action-1",
        )
        module.mark_action_processed(
            state,
            "action-1",
        )
        state = module._load_state()
        assert module.action_already_processed(
            state,
            "action-1",
        )
        module.mark_action_processed(
            state,
            "action-1",
        )
        state = module._load_state()
        assert state["processed_action_ids"].count(
            "action-1"
        ) == 1
finally:
    module.STATE_PATH = old_state_path

assert module.normalize_target_ids(
    ["M117", "m117", "m38", "bad"]
) == ["m117", "m38"]

parser = module.build_parser()
parsed = parser.parse_args(
    [
        "reference",
        "--session",
        "m37-m117-p3",
    ]
)
assert parsed.command == "reference"

print("AOT_RELAY_SELFTEST=OK")
