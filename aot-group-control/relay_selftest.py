#!/usr/bin/env python3
import importlib.util, pathlib, sys, tempfile, time
root = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("relay", root / "relay.py")
module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
assert module.WORKER_VERSION == "aot-worker-2026.08.11.9"
assert module.websocket_url("https://example.test/report", device_id="m301") == "wss://example.test/aot/control/ws?device_id=m301"
assert module.build_parser().parse_args(["fleet"]).command == "fleet"
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
    finally:
        module._send_ack, module._open_swift_backup, module.controller.open_swift_apps = old_ack, old_open, old_apps
print("AOT_RELAY_SELFTEST=OK")
