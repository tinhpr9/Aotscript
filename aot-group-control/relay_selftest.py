#!/usr/bin/env python3
import importlib.util, pathlib, sys, tempfile, time
root = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("relay", root / "relay.py")
module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
assert module.WORKER_VERSION == "aot-worker-2026.08.11.10"
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
        def _fake_backup_restore_data(action_id, *, stage_cb=None):
            for s in ("APPS_OPENED", "FILTERED", "SELECTED", "OPTIONS_VERIFIED"):
                if stage_cb: stage_cb(s)
            return {"action": "BACKUP_RESTORE_DATA", "executed": True, "app_count": 3, "selected_count": 3}
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
        assert sent[-1]["status"] == "DUPLICATE"
        assert sent[-1]["executed"] is False

        # BACKUP_RESTORE_DATA failure path
        def _fail_backup(*_a, **_kw):
            raise module.controller.AotControllerError("restore_data_no_matching_apps")
        module.controller.backup_restore_data = _fail_backup
        brd2 = dict(message, action_id="action-brd-2", action="BACKUP_RESTORE_DATA")
        sent.clear(); module._handle_batch_action({}, state, local_id="m301", message=brd2)
        assert sent[-1]["status"] == "FAILED"
        assert "restore_data_no_matching_apps" in sent[-1].get("reason", "")

        # BACKUP_RESTORE_DATA expired TTL
        brd_exp = dict(message, action_id="action-brd-exp", action="BACKUP_RESTORE_DATA", expires_at=int(time.time()*1000)-1)
        sent.clear(); module._handle_backup_restore_data({}, state, local_id="m301", message=brd_exp)
        assert sent[-1]["status"] == "TIMEOUT"
    finally:
        module._send_ack, module._open_swift_backup, module.controller.open_swift_apps = old_ack, old_open, old_apps
print("AOT_RELAY_SELFTEST=OK")
