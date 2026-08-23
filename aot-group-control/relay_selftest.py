#!/usr/bin/env python3
import importlib.util, pathlib, sys, tempfile, time
root = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("relay", root / "relay.py")
module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
assert module.WORKER_VERSION == "aot-worker-2026.08.23.03"
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
        sent.clear()
        module._handle_batch_action({}, state, local_id="m301", message=message)
        assert sent[-1]["status"] == "DUPLICATE" and sent[-1]["executed"] is False
        apps = dict(message, action_id="action-2", action="OPEN_SWIFT_APPS")
        module.controller.open_swift_apps = lambda: {"executed": True}
        sent.clear()
        module._handle_batch_action({}, state, local_id="m301", message=apps)
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
        sent.clear()
        module._handle_batch_action({}, state, local_id="m301", message=brd)
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
        sent.clear()
        module._handle_batch_action({}, state, local_id="m301", message=brd)
        assert sent[-1]["status"] == "BACKUP_STARTED"
        assert sent[-1]["executed"] is True

        # BACKUP_RESTORE_DATA failure path
        fail_calls = [0]
        def _fail_backup(*_a, **_kw):
            fail_calls[0] += 1
            raise module.controller.AotControllerError("restore_data_no_matching_apps")
        module.controller.backup_restore_data = _fail_backup
        brd2 = dict(message, action_id="action-brd-2", action="BACKUP_RESTORE_DATA")

        sent.clear()
        module._handle_batch_action({}, state, local_id="m301", message=brd2)
        initial_ack = sent[-1].copy()
        assert initial_ack["status"] == "FAILED"
        assert initial_ack["executed"] is False
        assert "restore_data_no_matching_apps" in initial_ack.get("reason", "")

        module._save_state(state)
        state2 = module._load_state()
        sent.clear()

        # Test redelivery when expired
        brd2_expired = dict(brd2, expires_at=int(time.time()*1000)-1)
        module._handle_batch_action({}, state2, local_id="m301", message=brd2_expired)
        redelivered_ack = sent[-1]
        assert redelivered_ack["status"] == "FAILED", f"Expected FAILED on redelivery, got {redelivered_ack['status']}"
        assert redelivered_ack["executed"] is False
        for field in ("status", "executed", "reason", "app_count", "selected_count"):
            assert redelivered_ack.get(field) == initial_ack.get(field)
        assert fail_calls[0] == 1

        # BACKUP_RESTORE_DATA failure path (not installed)
        not_installed_calls = [0]
        def _fail_backup_not_installed(*_a, **_kw):
            not_installed_calls[0] += 1
            raise module.controller.AotControllerError("swift_backup_not_installed")
        module.controller.backup_restore_data = _fail_backup_not_installed
        brd3 = dict(message, action_id="action-brd-3", action="BACKUP_RESTORE_DATA")

        sent.clear()
        module._handle_batch_action({}, state, local_id="m301", message=brd3)
        initial_ack3 = sent[-1].copy()
        assert initial_ack3["status"] == "FAILED_NOT_INSTALLED"
        assert initial_ack3["executed"] is False

        module._save_state(state)
        state3 = module._load_state()
        sent.clear()

        brd3_expired = dict(brd3, expires_at=int(time.time()*1000)-1)
        module._handle_batch_action({}, state3, local_id="m301", message=brd3_expired)
        redelivered_ack3 = sent[-1]
        assert redelivered_ack3["status"] == "FAILED_NOT_INSTALLED", f"Expected FAILED_NOT_INSTALLED on redelivery, got {redelivered_ack3['status']}"
        assert redelivered_ack3["executed"] is False
        for field in ("status", "executed", "reason", "app_count", "selected_count"):
            assert redelivered_ack3.get(field) == initial_ack3.get(field)
        assert not_installed_calls[0] == 1

        # BACKUP_RESTORE_DATA exact root cause regression test
        def _fail_backup_selector(*_a, **_kw):
            raise module.controller.AotControllerError("swift_apps_selector_not_found")
        module.controller.backup_restore_data = _fail_backup_selector
        brd_selector = dict(message, action_id="action-brd-selector", action="BACKUP_RESTORE_DATA")
        
        sent.clear()
        module._handle_batch_action({}, state, local_id="m301", message=brd_selector)
        selector_ack = sent[-1]
        assert selector_ack["status"] == "FAILED"
        assert selector_ack["executed"] is False
        assert selector_ack.get("reason") == "swift_apps_selector_not_found", f"Expected exact safe reason, got {selector_ack.get('reason')}"

        # BACKUP_RESTORE_DATA expired TTL
        brd_exp = dict(message, action_id="action-brd-exp", action="BACKUP_RESTORE_DATA", expires_at=int(time.time()*1000)-1)
        sent.clear()
        module._handle_backup_restore_data({}, state, local_id="m301", message=brd_exp)
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

# Snapshot error resilience: verify snapshot failure returns None (gracefully isolates controller error)
# When previous_fingerprint is not None: no frame is sent
def error_snapshot(*_a, **_kw):
    raise module.controller.AotControllerError("dumpsys_locked")
module.controller.snapshot = error_snapshot
sent_status.clear()
res = module._send_live_status(None, device_id="d1", previous_fingerprint="fp1", force_preview=False)
assert res is None, "Expected None from failing snapshot"
assert len(sent_status) == 0, "No status frame should be sent when snapshot fails during periodic check"

# When previous_fingerprint is None (initial connect): sends fallback frame with worker_version & capabilities
sent_status.clear()
module.controller.root_available = lambda: False
res = module._send_live_status(None, device_id="d1", previous_fingerprint=None, force_preview=False)
assert res is None
assert len(sent_status) == 1
assert sent_status[0]["fallback"] is True
assert sent_status[0]["worker_version"] == "aot-worker-2026.08.23.03"
assert "allocate_server_2pc" in sent_status[0]["capabilities"]
assert "dynamic_update_channel" in sent_status[0]["capabilities"]
assert "fleet_batch_v1" in sent_status[0]["capabilities"]
assert "swift_apps_semantic" not in sent_status[0]["capabilities"]
assert "backup_restore_data_semantic" not in sent_status[0]["capabilities"]

# With root available: capabilities include root-only semantic capabilities
module.controller.root_available = lambda: True
sent_status.clear()
res = module._send_live_status(None, device_id="d1", previous_fingerprint=None, force_preview=False)
assert "swift_apps_semantic" in sent_status[0]["capabilities"]
assert "backup_restore_data_semantic" in sent_status[0]["capabilities"]
module.controller.root_available = lambda: False

# Transport error propagation: any OSError from _ws_send_json must propagate unsuppressed
def failing_send(sock, payload):
    raise OSError(104, "Connection reset by peer")
module._ws_send_json = failing_send
module.controller.snapshot = fake_snapshot
try:
    module._send_live_status(None, device_id="d1", previous_fingerprint=None, force_preview=False)
    assert False, "Expected OSError from _ws_send_json to propagate"
except OSError as exc:
    assert exc.errno == 104

# Recovery: restore working send and snapshot -> status sent with preview
module._ws_send_json = lambda sock, p: sent_status.append(p)
sent_status.clear()
res = module._send_live_status(None, device_id="d1", previous_fingerprint=None, force_preview=False)
assert res == "fp1"
assert "preview_sha256" in sent_status[-1]

# Non-root fleet_loop startup contract:
# Prove fleet_loop connects and runs without root_available()
orig_root_avail = module.controller.root_available
orig_ws_connect = module.ws_connect
orig_recv_frame = module._ws_recv_frame
orig_read_small = module._read_small
orig_agent_cfg = module.load_agent_config

class DummySocket:
    def settimeout(self, _t): pass
    def close(self): pass

try:
    module.controller.root_available = lambda: False
    module._read_small = lambda path: "m74" if "device_id" in str(path) else "NOVA"
    module.load_agent_config = lambda: {"worker_report_url": "https://example.test/agent/report", "agent_report_secret": "test-secret"}
    connected_urls = []
    def fake_connect(url, secret):
        connected_urls.append(url)
        return DummySocket()
    module.ws_connect = fake_connect
    def fake_recv_stop(sock):
        raise KeyboardInterrupt()
    module._ws_recv_frame = fake_recv_stop
    try:
        module.fleet_loop()
        assert False, "Expected KeyboardInterrupt from loop"
    except KeyboardInterrupt:
        pass
    # open_package non-root launch and error resilience tests:
    # 1. Invalid package format rejected
    try:
        module._launch_package("com.invalid; rm -rf /")
        assert False, "invalid package name must be rejected"
    except module.AotRelayError as exc:
        assert str(exc) == "invalid_package"

    # 2. Userspace package launch executed without root
    launched_cmds = []
    def fake_subprocess_run(cmd, **_kw):
        launched_cmds.append(cmd)
        class Res:
            returncode = 0
            stdout = "com.test.pkg/.MainActivity\n"
            stderr = ""
        return Res()

    orig_sub_run = module.subprocess.run
    orig_sleep = module.time.sleep
    try:
        module.subprocess.run = fake_subprocess_run
        module.time.sleep = lambda _t: None
        module._launch_package("com.test.pkg")
        assert any("am" in cmd and "com.test.pkg" in str(cmd) for cmd in launched_cmds), "Userspace am start must be invoked"

        # 3. fleet_loop continues to connect WebSocket even if optional open_package fails/times out
        def fake_failing_run(cmd, **_kw):
            class Res:
                returncode = 1
                stdout = ""
                stderr = "Activity not found"
            return Res()

        module.subprocess.run = fake_failing_run
        connected_urls.clear()
        try:
            module.fleet_loop(open_package="com.test.failing")
            assert False, "Expected KeyboardInterrupt from loop"
        except KeyboardInterrupt:
            pass
        assert len(connected_urls) == 1, "fleet_loop must still connect WebSocket after optional open_package failure"

        # 4. Root-required actions must remain strictly fail-closed when root is unavailable
        try:
            module.reference_loop(session_id="s1", open_package=None)
            assert False, "reference_loop must fail closed without root"
        except module.AotRelayError as exc:
            assert str(exc) == "root_not_available"

        try:
            module.follower_loop(session_id="s1", reference_device="m1", open_package=None)
            assert False, "follower_loop must fail closed without root"
        except module.AotRelayError as exc:
            assert str(exc) == "root_not_available"

        try:
            module.reference_test(session_id="s1", follower_id="m99", selector="sel", open_package="com.pkg")
            assert False, "reference_test must fail closed without root"
        except module.AotRelayError as exc:
            assert str(exc) == "root_not_available"
    finally:
        module.subprocess.run = orig_sub_run
        module.time.sleep = orig_sleep

finally:
    module.controller.root_available = orig_root_avail
    module.ws_connect = orig_ws_connect
    module._ws_recv_frame = orig_recv_frame
    module._read_small = orig_read_small
    module.load_agent_config = orig_agent_cfg

print("AOT_RELAY_SELFTEST=OK")

