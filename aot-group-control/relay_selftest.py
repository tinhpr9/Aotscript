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

enable_commands = []
launch_attempts = []
old_root_run = module.controller._root_run
old_launch_package = module._launch_package
try:
    module.controller._root_run = lambda command, **_kwargs: enable_commands.append(command) or ""

    def launch_after_enable(package):
        launch_attempts.append(package)
        if len(launch_attempts) == 1:
            raise module.AotRelayError("package_activity_not_resolved")

    module._launch_package = launch_after_enable
    module._launch_swift_backup_once()
finally:
    module.controller._root_run = old_root_run
    module._launch_package = old_launch_package
assert launch_attempts == [module.SWIFT_BACKUP_PACKAGE] * 2
assert enable_commands == [
    "/system/bin/pm enable --user 0 org.swiftapps.swiftbackup >/dev/null"
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
assert live_payload["worker_version"] == module.WORKER_VERSION
assert "dynamic_update_channel" in live_payload["capabilities"]
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

# The fixed batch action reuses ACK/dedupe and never enters capture/replay.
batch_acks = []
old_send_ack = module._send_ack
old_foreground_package = module.controller.foreground_package
old_batch_root_run = module.controller._root_run
old_launch_package = module._launch_package
old_state_path = module.STATE_PATH
old_monotonic = module.time.monotonic
old_sleep = module.time.sleep
old_open_timeout = module.SWIFT_OPEN_TIMEOUT_SECONDS
old_open_retry = module.SWIFT_OPEN_RETRY_SECONDS
old_open_poll = module.SWIFT_OPEN_POLL_SECONDS
try:
    with tempfile.TemporaryDirectory() as tmp:
        module.STATE_PATH = pathlib.Path(tmp) / "batch-state.json"
        state = module._load_state()
        module._send_ack = lambda _cfg, ack: batch_acks.append(dict(ack))
        module.controller.foreground_package = (
            lambda: module.SWIFT_BACKUP_PACKAGE
        )

        def unexpected_launch(_package):
            raise AssertionError("already-open Swift Backup was relaunched")

        module._launch_package = unexpected_launch
        message = {
            "type": "aot_batch_action",
            "protocol": module.HUB_PROTOCOL_VERSION,
            "session_id": "m37-m117-p3",
            "reference_device_id": "m37",
            "target_device_ids": ["m37", "m117"],
            "action_id": "swift-fixture-1",
            "action": module.OPEN_SWIFT_BACKUP_ACTION,
            "package": module.SWIFT_BACKUP_PACKAGE,
            "expires_at": 9999999999999,
        }
        assert module._handle_batch_action(
            {}, state, local_id="m117", session_id="m37-m117-p3",
            message=message,
        )
        assert [ack["status"] for ack in batch_acks] == [
            "ACCEPTED", "OPENED"
        ]
        assert all(ack["follower_device_id"] == "m117" for ack in batch_acks)
        assert all(ack["action_id"] == "swift-fixture-1" for ack in batch_acks)
        assert batch_acks[-1]["executed"] is False

        batch_acks.clear()
        assert module._handle_batch_action(
            {}, state, local_id="m117", session_id="m37-m117-p3",
            message=message,
        )
        assert [ack["status"] for ack in batch_acks] == ["DUPLICATE"]

        batch_acks.clear()
        closed = dict(message, action_id="swift-fixture-2")
        foreground_values = iter([
            "com.android.settings",
            module.SWIFT_BACKUP_PACKAGE,
        ])
        module.controller.foreground_package = lambda: next(foreground_values)
        module.controller._root_run = lambda _command: (
            "package:/data/app/org.swiftapps.swiftbackup/base.apk\n"
        )
        launched = []
        module._launch_package = lambda package: launched.append(package)
        assert module._handle_batch_action(
            {}, state, local_id="m117", session_id="m37-m117-p3",
            message=closed,
        )
        assert launched == [module.SWIFT_BACKUP_PACKAGE]
        assert [ack["status"] for ack in batch_acks] == [
            "ACCEPTED", "OPENED"
        ]
        assert batch_acks[-1]["executed"] is True

        batch_acks.clear()
        cold = dict(message, action_id="swift-fixture-cold")
        clock = {"now": 0.0}
        module.time.monotonic = lambda: clock["now"]
        module.time.sleep = lambda seconds: clock.__setitem__(
            "now", clock["now"] + seconds
        )
        module.SWIFT_OPEN_TIMEOUT_SECONDS = 45.0
        module.SWIFT_OPEN_RETRY_SECONDS = 15.0
        module.SWIFT_OPEN_POLL_SECONDS = 0.5
        module.controller.foreground_package = lambda: (
            module.SWIFT_BACKUP_PACKAGE
            if clock["now"] >= 20.0
            else "com.android.settings"
        )
        module.controller._root_run = lambda _command: (
            "package:/data/app/org.swiftapps.swiftbackup/base.apk\n"
        )
        launched = []
        module._launch_package = lambda package: launched.append(package)
        assert module._handle_batch_action(
            {}, state, local_id="m117", session_id="m37-m117-p3",
            message=cold,
        )
        assert launched == [module.SWIFT_BACKUP_PACKAGE] * 2
        assert [ack["status"] for ack in batch_acks] == [
            "ACCEPTED", "OPENED"
        ]

        batch_acks.clear()
        failed_cold = dict(message, action_id="swift-fixture-timeout")
        clock["now"] = 0.0
        module.SWIFT_OPEN_TIMEOUT_SECONDS = 1.0
        module.SWIFT_OPEN_RETRY_SECONDS = 0.25
        module.controller.foreground_package = lambda: "com.android.settings"
        assert module._handle_batch_action(
            {}, state, local_id="m117", session_id="m37-m117-p3",
            message=failed_cold,
        )
        assert [ack["status"] for ack in batch_acks] == [
            "ACCEPTED", "FAILED"
        ]
        assert batch_acks[-1]["reason"] == "swift_backup_not_foreground"

        batch_acks.clear()
        missing = dict(message, action_id="swift-fixture-3")
        module.controller.foreground_package = lambda: "com.android.settings"
        module.controller._root_run = lambda _command: ""
        module._launch_package = lambda _package: (_ for _ in ()).throw(
            module.AotRelayError("package_activity_not_resolved")
        )
        assert module._handle_batch_action(
            {}, state, local_id="m117", session_id="m37-m117-p3",
            message=missing,
        )
        assert [ack["status"] for ack in batch_acks] == [
            "ACCEPTED", "FAILED_NOT_INSTALLED"
        ]
finally:
    module._send_ack = old_send_ack
    module.controller.foreground_package = old_foreground_package
    module.controller._root_run = old_batch_root_run
    module._launch_package = old_launch_package
    module.STATE_PATH = old_state_path
    module.time.monotonic = old_monotonic
    module.time.sleep = old_sleep
    module.SWIFT_OPEN_TIMEOUT_SECONDS = old_open_timeout
    module.SWIFT_OPEN_RETRY_SECONDS = old_open_retry
    module.SWIFT_OPEN_POLL_SECONDS = old_open_poll

# UPDATE_WORKER is channel-bound, expires, and is deduped before spawning updater.
old_popen = module.subprocess.Popen
old_state_path = module.STATE_PATH
spawned = []
try:
    with tempfile.TemporaryDirectory() as tmp:
        module.STATE_PATH = pathlib.Path(tmp) / "update-state.json"
        state = module._load_state()
        module.subprocess.Popen = lambda command, **kwargs: spawned.append((command, kwargs))
        update_reference = "m" + str(210 + 1)
        update_target = "m" + str(210 + 2)
        update_message = {
            "type": "aot_batch_action", "protocol": module.HUB_PROTOCOL_VERSION,
            "session_id": "dynamic-canary", "reference_device_id": update_reference,
            "target_device_ids": [update_reference, update_target], "action_id": "worker-canary-1",
            "action": module.UPDATE_WORKER_ACTION, "channel": "canary",
            "release": {
                "protocol": "github-release-v1", "version": module.WORKER_VERSION,
                "tag": "worker-v2026.08.11.8", "commit_sha": "a" * 40,
                "manifest": {
                    "name": "worker-manifest.json",
                    "url": "https://github.com/tinhpr9/Aotscript/releases/download/worker-v2026.08.11.8/worker-manifest.json",
                    "size": 100, "sha256": "b" * 64,
                    "github_digest": "sha256:" + "b" * 64,
                },
            },
            "expires_at": 9999999999999,
        }
        assert module._handle_worker_update(
            state, local_id=update_target, session_id="dynamic-canary",
            reference_device_id=update_reference, message=update_message,
        )
        assert len(spawned) == 1
        assert "bootstrap_launcher.py" in " ".join(spawned[0][0])
        assert "--release-metadata" in spawned[0][0]
        assert module._handle_worker_update(
            state, local_id=update_target, session_id="dynamic-canary",
            reference_device_id=update_reference, message=update_message,
        )
        assert len(spawned) == 1
        stable_channel = dict(update_message, action_id="worker-bridge-1", channel="stable")
        assert module._handle_worker_update(
            state, local_id=update_target, session_id="dynamic-canary",
            reference_device_id=update_reference, message=stable_channel,
        )
        assert len(spawned) == 2
        assert spawned[-1][0][spawned[-1][0].index("--channel") + 1] == "stable"
        bridge_duplicate = dict(stable_channel, channel="canary")
        assert module._handle_worker_update(
            state, local_id=update_target, session_id="dynamic-canary",
            reference_device_id=update_reference, message=bridge_duplicate,
        )
        assert len(spawned) == 2
        invalid_channel = dict(update_message, action_id="worker-invalid-1", channel="preview")
        assert module._handle_worker_update(
            state, local_id=update_target, session_id="dynamic-canary",
            reference_device_id=update_reference, message=invalid_channel,
        )
        assert len(spawned) == 2
        assert not module.action_already_processed(state, "worker-invalid-1")
        missing_release = dict(update_message, action_id="worker-no-release-1")
        missing_release.pop("release")
        assert module._handle_worker_update(
            state, local_id=update_target, session_id="dynamic-canary",
            reference_device_id=update_reference, message=missing_release,
        )
        assert len(spawned) == 2
        assert not module.action_already_processed(state, "worker-no-release-1")
finally:
    module.subprocess.Popen = old_popen
    module.STATE_PATH = old_state_path

print("AOT_RELAY_SELFTEST=OK")
