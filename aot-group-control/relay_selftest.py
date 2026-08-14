#!/usr/bin/env python3
import importlib.util, pathlib, sys, tempfile, time
root = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("relay", root / "relay.py")
module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
assert module.WORKER_VERSION == "aot-worker-2026.08.11.12"
assert module.websocket_url("https://example.test/report", device_id="m301") == "wss://example.test/aot/control/ws?device_id=m301"
assert module.build_parser().parse_args(["fleet"]).command == "fleet"
assert "backup_restore_data_semantic" in module.WORKER_CAPABILITIES
with tempfile.TemporaryDirectory() as folder:
    module.STATE_PATH = pathlib.Path(folder) / "state.json"
    state = module._load_state(); sent=[]
    old_ack, old_open, old_apps = module._send_ack, module._open_swift_backup, module.controller.open_swift_apps
    module._send_ack = lambda _cfg, payload: sent.append(payload)
    try:
        message = {"type":"aot_batch_action","protocol":"fleet-batch-v1","target_device_ids":["m301"],"action_id":"action-1","action":"OPEN_SWIFT_BACKUP","package":module.SWIFT_BACKUP_PACKAGE,"expires_at":int(time.time()*1000)+5000}
        module._open_swift_backup = lambda: True
        assert module._handle_batch_action({}, state, local_id="m301", message=message)
        assert [item["status"] for item in sent] == ["ACCEPTED", "OPENED"]
        assert all(item["device_id"] == "m301" and "session_id" not in item for item in sent)
        sent.clear(); module._handle_batch_action({}, state, local_id="m301", message=message)
        assert sent[-1]["status"] == "DUPLICATE" and sent[-1]["executed"] is False
        apps = dict(message, action_id="action-2", action="OPEN_SWIFT_APPS")
        module.controller.open_swift_apps = lambda: {"executed": True}
        sent.clear(); module._handle_batch_action({}, state, local_id="m301", message=apps)
        assert [item["status"] for item in sent] == ["ACCEPTED", "APPS_OPENED"]

        # BACKUP_RESTORE_DATA: complete stage sequence
        def _fake_backup_restore_data(action_id, *, stage_cb=None, deadline=None):
            for s in ("APPS_OPENED", "FILTERED", "SELECTED", "OPTIONS_VERIFIED"):
                if stage_cb:
                    stage_cb(s)
            return {"action": "BACKUP_RESTORE_DATA", "executed": True, "status": "BACKUP_STARTED", "app_count": 3, "selected_count": 3}
        module.controller.backup_restore_data = _fake_backup_restore_data
        module._open_swift_backup = lambda: True
        brd = dict(message, action_id="action-brd-1", action="BACKUP_RESTORE_DATA")
        sent.clear(); module._handle_batch_action({}, state, local_id="m301", message=brd)
        statuses = [item["status"] for item in sent]
        assert statuses[0] == "ACCEPTED", statuses
        assert "SWIFT_OPENED" in statuses, statuses
        assert "APPS_OPENED" in statuses, statuses
        assert "FILTERED" in statuses, statuses
        assert "SELECTED" in statuses, statuses
        assert "OPTIONS_VERIFIED" in statuses, statuses
        assert statuses[-1] == "BACKUP_STARTED", statuses
        assert sent[-1]["executed"] is True

        # BACKUP_RESTORE_DATA duplicate protection
        sent.clear(); module._handle_batch_action({}, state, local_id="m301", message=brd)
        assert sent[-1]["status"] == "BACKUP_STARTED"
        assert sent[-1]["executed"] is True

        # BACKUP_RESTORE_DATA failure path
        def _fail_backup(*_a, **_kw):
            raise module.controller.AotControllerError("restore_data_no_matching_apps")
        module.controller.backup_restore_data = _fail_backup
        brd2 = dict(message, action_id="action-brd-2", action="BACKUP_RESTORE_DATA")
        sent.clear(); module._handle_batch_action({}, state, local_id="m301", message=brd2)
        assert sent[-1]["status"] == "FAILED"
        assert sent[-1]["executed"] is False
        assert "restore_data_no_matching_apps" in sent[-1].get("reason", "")

        sent.clear(); module._handle_batch_action({}, state, local_id="m301", message=brd2)
        assert sent[-1]["status"] == "FAILED", f"Expected FAILED on redelivery, got {sent[-1]['status']}"
        assert sent[-1]["executed"] is False

        # BACKUP_RESTORE_DATA failure path (not installed)
        def _fail_backup_not_installed(*_a, **_kw):
            raise module.controller.AotControllerError("swift_backup_not_installed")
        module.controller.backup_restore_data = _fail_backup_not_installed
        brd3 = dict(message, action_id="action-brd-3", action="BACKUP_RESTORE_DATA")
        sent.clear(); module._handle_batch_action({}, state, local_id="m301", message=brd3)
        assert sent[-1]["status"] == "FAILED_NOT_INSTALLED"
        assert sent[-1]["executed"] is False

        sent.clear(); module._handle_batch_action({}, state, local_id="m301", message=brd3)
        assert sent[-1]["status"] == "FAILED_NOT_INSTALLED", f"Expected FAILED_NOT_INSTALLED on redelivery, got {sent[-1]['status']}"
        assert sent[-1]["executed"] is False
        # BACKUP_RESTORE_DATA expired TTL
        brd_exp = dict(message, action_id="action-brd-exp", action="BACKUP_RESTORE_DATA", expires_at=int(time.time()*1000)-1)
        sent.clear(); module._handle_backup_restore_data({}, state, local_id="m301", message=brd_exp)
        assert sent[-1]["status"] == "TIMEOUT"
    finally:
        module._send_ack, module._open_swift_backup, module.controller.open_swift_apps = old_ack, old_open, old_apps

# Interval, jitter bounds, and initial phase
assert module._get_live_status_interval(None) == 900.0
i1 = module._get_live_status_interval("device1")
i2 = module._get_live_status_interval("device2")
assert 840.0 <= i1 <= 960.0
assert 840.0 <= i2 <= 960.0
assert module._get_live_status_interval("device1") == i1  # deterministic

d1 = module._get_live_status_initial_delay("device1")
d2 = module._get_live_status_initial_delay("device2")
assert 0.0 <= d1 < 900.0
assert 0.0 <= d2 < 900.0

# Immediate forced status semantics
def fake_snapshot(*_a, **_kw):
    return {"fingerprint": "fp1"}
module.controller.snapshot = fake_snapshot
module.controller.screenshot_bytes = lambda: b"fake_screenshot"
sent_status = []
module._ws_send_json = lambda sock, p: sent_status.append(p)
# Initial: fingerprint changes from None to fp1 -> includes preview bytes
res = module._send_live_status(None, device_id="d1", previous_fingerprint=None, force_preview=False)
assert res == "fp1"
assert "preview_sha256" in sent_status[-1]

# Next periodic: same fingerprint -> NO preview
sent_status.clear()
res = module._send_live_status(None, device_id="d1", previous_fingerprint="fp1", force_preview=False)
assert res == "fp1"
assert "preview_sha256" not in sent_status[-1]

# Forced: same fingerprint BUT force_preview -> includes preview
sent_status.clear()
res = module._send_live_status(None, device_id="d1", previous_fingerprint="fp1", force_preview=True)
assert res == "fp1"
assert "preview_sha256" in sent_status[-1]

# Meaningful change: new fingerprint -> includes preview
def fake_snapshot2(*_a, **_kw):
    return {"fingerprint": "fp2"}
module.controller.snapshot = fake_snapshot2
sent_status.clear()
res = module._send_live_status(None, device_id="d1", previous_fingerprint="fp1", force_preview=False)
assert res == "fp2"
assert "preview_sha256" in sent_status[-1]

# Preview decision and payload must reuse one snapshot. A second read could
# otherwise pair an fp2 payload with the preview decision made for fp1.
snapshot_calls = []
def sequential_snapshot(*_a, **_kw):
    snapshot_calls.append(True)
    return {"fingerprint": "fp1" if len(snapshot_calls) == 1 else "fp2"}
module.controller.snapshot = sequential_snapshot
sent_status.clear()
res = module._send_live_status(None, device_id="d1", previous_fingerprint="old", force_preview=False)
assert len(snapshot_calls) == 1
assert res == "fp1"
assert sent_status[-1]["fingerprint"] == "fp1"
assert "preview_sha256" in sent_status[-1]

print("AOT_RELAY_SELFTEST=OK")
