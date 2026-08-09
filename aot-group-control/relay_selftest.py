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

launch_commands = []
old_root_run = module.controller._root_run
old_sleep = module.time.sleep


def fake_root_run(command: str, **_kwargs):
    launch_commands.append(command)
    if command.startswith(
        "/system/bin/cmd package resolve-activity"
    ):
        return "org.swiftapps.swiftbackup/.MainActivity\n"
    return ""


try:
    module.controller._root_run = fake_root_run
    module.time.sleep = lambda _seconds: None
    module._launch_package(
        "org.swiftapps.swiftbackup"
    )
finally:
    module.controller._root_run = old_root_run
    module.time.sleep = old_sleep

assert launch_commands == [
    (
        "/system/bin/cmd package resolve-activity --brief --user 0 "
        "-a android.intent.action.MAIN "
        "-c android.intent.category.LAUNCHER "
        "org.swiftapps.swiftbackup"
    ),
    (
        "/system/bin/am start -W --user 0 --display 0 "
        "-a android.intent.action.MAIN "
        "-c android.intent.category.LAUNCHER "
        "-n org.swiftapps.swiftbackup/.MainActivity >/dev/null"
    ),
]

parser = module.build_parser()
parsed = parser.parse_args(
    [
        "reference",
        "--session",
        "m37-m117-p3",
    ]
)
assert parsed.command == "reference"

primary_xml = """<hierarchy rotation='0'>
  <node class='android.widget.FrameLayout' resource-id=''
        clickable='false' enabled='true' scrollable='false' password='false'
        bounds='[0,0][1000,2000]' />
</hierarchy>"""
full_xml = """<hierarchy rotation='0'>
  <node class='android.widget.FrameLayout' resource-id=''
        clickable='false' enabled='true' scrollable='false' password='false'
        bounds='[0,0][1000,2000]'>
    <node class='android.widget.Button' resource-id='pkg:id/full_target'
          clickable='true' enabled='true' scrollable='false' password='false'
          bounds='[400,800][600,1200]' />
  </node>
</hierarchy>"""
old_foreground_package = module.controller.foreground_package
old_display_size = module.controller.display_size
old_dump_ui_xml = module.controller.dump_ui_xml
old_dump_full_ui_xml = module.controller.dump_full_ui_xml
try:
    module.controller.foreground_package = lambda: "pkg"
    module.controller.display_size = lambda: (1000, 2000)
    module.controller.dump_ui_xml = lambda: primary_xml
    module.controller.dump_full_ui_xml = lambda: full_xml
    resolved_fp, resolved_id = module._resolve_reference_tap(
        0.5,
        0.5,
    )
    assert resolved_id == "pkg:id/full_target"
    assert resolved_fp == module.controller.ui_fingerprint(
        "pkg",
        module.controller.parse_ui_xml(primary_xml),
    )

    module.controller.dump_full_ui_xml = lambda: primary_xml
    try:
        module._resolve_reference_tap(0.5, 0.5)
    except module.AotRelayError as exc:
        assert str(exc) == "semantic_target_not_found"
    else:
        raise AssertionError("unguarded coordinate tap was accepted")
finally:
    module.controller.foreground_package = old_foreground_package
    module.controller.display_size = old_display_size
    module.controller.dump_ui_xml = old_dump_ui_xml
    module.controller.dump_full_ui_xml = old_dump_full_ui_xml

try:
    module._execute_action(
        {"kind": "tap_normalized", "x_norm": 0.5, "y_norm": 0.5},
        "0" * 24,
    )
except module.AotRelayError as exc:
    assert str(exc) == "unsupported_action_kind"
else:
    raise AssertionError("coordinate follower action was enabled")


# Existing primary semantic resolution must not invoke the full-window fallback.
old_foreground_package = module.controller.foreground_package
old_display_size = module.controller.display_size
old_dump_ui_xml = module.controller.dump_ui_xml
old_dump_full_ui_xml = module.controller.dump_full_ui_xml
try:
    module.controller.foreground_package = lambda: "pkg"
    module.controller.display_size = lambda: (1000, 2000)
    module.controller.dump_ui_xml = lambda: full_xml

    def unexpected_full_dump():
        raise AssertionError("full-window fallback ran for a primary semantic hit")

    module.controller.dump_full_ui_xml = unexpected_full_dump
    primary_fp, primary_id = module._resolve_reference_tap(0.5, 0.5)
    assert primary_id == "pkg:id/full_target"
    assert primary_fp == module.controller.ui_fingerprint(
        "pkg",
        module.controller.parse_ui_xml(full_xml),
    )
finally:
    module.controller.foreground_package = old_foreground_package
    module.controller.display_size = old_display_size
    module.controller.dump_ui_xml = old_dump_ui_xml
    module.controller.dump_full_ui_xml = old_dump_full_ui_xml

# Live status must expose only the non-sensitive compatibility metadata.
old_snapshot = module.controller.snapshot
try:
    module.controller.snapshot = lambda **_kwargs: {
        "package": "org.swiftapps.swiftbackup",
        "fingerprint": "a" * 24,
        "layout_signature": "b" * 24,
        "coordinate_ready": False,
        "ime_visible": True,
        "width": 1000,
        "height": 2000,
    }
    live_payload = module._live_status_payload(
        role="follower",
        session_id="m37-m117-p3",
        device_id="m117",
        include_preview=False,
    )
finally:
    module.controller.snapshot = old_snapshot
assert live_payload["layout_signature"] == "b" * 24
assert live_payload["coordinate_ready"] is False
assert live_payload["ime_visible"] is True
assert "preview_b64" not in live_payload

# ACK delivery and reference control results must retain status/executed truth.
http_calls = []
old_http_json = module._http_json
try:
    def fake_http_json(endpoint, auth, *, body=None, **_kwargs):
        http_calls.append((endpoint, auth, body))
        return 200, {"ok": True}

    module._http_json = fake_http_json
    ack_payload = {
        "status": "out_of_sync",
        "executed": False,
        "action_id": "fixture-action",
    }
    module._send_ack(
        {
            "worker_report_url": "https://fixture.invalid/agent/report",
            "agent_report_secret": "fixture-auth",
        },
        ack_payload,
    )
finally:
    module._http_json = old_http_json
assert len(http_calls) == 1
assert http_calls[0][2] == ack_payload
assert http_calls[0][2]["executed"] is False

control_payloads = []
old_ws_send_json = module._ws_send_json
try:
    module._ws_send_json = lambda _sock, payload: control_payloads.append(payload)
    module._send_control_result(
        object(),
        session_id="m37-m117-p3",
        device_id="m37",
        control_id="fixture-control",
        status="error",
        reason="semantic_target_not_found",
    )
finally:
    module._ws_send_json = old_ws_send_json
assert len(control_payloads) == 1
assert control_payloads[0]["status"] == "error"
assert control_payloads[0]["reason"] == "semantic_target_not_found"

print("AOT_RELAY_SELFTEST=OK")
