from __future__ import annotations
import importlib.util, pathlib, sys, tempfile, unittest
import json
import time
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fleet_controller", ROOT / "aot-group-control/controller.py")
CONTROLLER = importlib.util.module_from_spec(SPEC); sys.modules[SPEC.name] = CONTROLLER; SPEC.loader.exec_module(CONTROLLER)

APPS = "<hierarchy><node class='Root' resource-id='root' clickable='false' enabled='true' scrollable='false' password='false' bounds='[0,0][100,100]'><node class='Button' resource-id='org.swiftapps.swiftbackup:id/nav_apps' text='Apps' content-desc='Apps' clickable='true' enabled='true' scrollable='false' selected='false' password='false' bounds='[0,50][100,100]'/></node></hierarchy>"
OPEN = APPS.replace("selected='false'", "selected='true'")

class SwiftAppsSemanticTests(unittest.TestCase):
    def run_action(self, dumps, packages=None):
        packages = packages or [CONTROLLER.SWIFT_BACKUP_PACKAGE, CONTROLLER.SWIFT_BACKUP_PACKAGE]
        with mock.patch.object(CONTROLLER, "foreground_package", side_effect=packages), mock.patch.object(CONTROLLER, "dump_ui_xml", side_effect=dumps), mock.patch.object(CONTROLLER, "_tap_xy") as tap, mock.patch.object(CONTROLLER.time, "sleep"):
            result = CONTROLLER.open_swift_apps()
            return result, tap.call_count

    def test_semantic_success(self):
        result, taps = self.run_action([APPS, APPS, OPEN])
        self.assertTrue(result["executed"]); self.assertEqual(1, taps)

    def test_wrong_package_does_not_click(self):
        with mock.patch.object(CONTROLLER, "foreground_package", return_value="other.pkg"), mock.patch.object(CONTROLLER, "_tap_xy") as tap:
            with self.assertRaisesRegex(CONTROLLER.AotControllerError, "not_foreground"):
                CONTROLLER.open_swift_apps()
            tap.assert_not_called()

    def test_changed_fingerprint_does_not_click(self):
        changed = APPS.replace("nav_apps", "nav_apps_changed")
        with mock.patch.object(CONTROLLER, "foreground_package", return_value=CONTROLLER.SWIFT_BACKUP_PACKAGE), mock.patch.object(CONTROLLER, "dump_ui_xml", side_effect=[APPS, changed]), mock.patch.object(CONTROLLER, "_tap_xy") as tap:
            with self.assertRaisesRegex(CONTROLLER.AotControllerError, "precondition_changed"):
                CONTROLLER.open_swift_apps()
            tap.assert_not_called()

    def test_bad_postcondition_never_reports_success(self):
        with mock.patch.object(CONTROLLER, "foreground_package", return_value=CONTROLLER.SWIFT_BACKUP_PACKAGE), mock.patch.object(CONTROLLER, "dump_ui_xml", side_effect=[APPS, APPS, APPS]), mock.patch.object(CONTROLLER, "_tap_xy") as tap, mock.patch.object(CONTROLLER.time, "sleep"):
            with self.assertRaisesRegex(CONTROLLER.AotControllerError, "postcondition_failed"):
                CONTROLLER.open_swift_apps()
            tap.assert_called_once()

class FleetArchitectureTests(unittest.TestCase):
    def test_new_protocol_is_device_only_and_legacy_control_is_gone(self):
        worker = (ROOT / "cloudflare-worker/worker.js").read_text()
        self.assertNotIn("function fleetHubHtml()", worker)
        self.assertNotIn("async function handleAotHubPage", worker)
        self.assertNotIn("telegram-web-app.js", worker)
        self.assertNotIn("window.Telegram", worker)
        self.assertIn("cross_device_control_removed", worker)
        relay = (ROOT / "aot-group-control/relay.py").read_text()
        parser = relay[relay.index("def build_parser"):]
        self.assertNotIn('sub.add_parser("reference")', parser)
        self.assertNotIn('sub.add_parser("follower")', parser)

    def test_deployed_point_eight_has_one_way_update_adapter(self):
        worker = (ROOT / "cloudflare-worker/worker.js").read_text()
        self.assertIn('body.device_id || body.follower_device_id', worker)
        self.assertIn('[AOT_HUB_PROTOCOL_VERSION, "phase4-1"]', worker)
        bridge = (ROOT / "aot-group-control/legacy_relay_bridge.py").read_text()
        self.assertIn("update", bridge.lower())
        for forbidden in ("tap_selector", "tap_normalized", "swipe_normalized", "preview_b64"):
            self.assertNotIn(forbidden, bridge)


class TestFleetLoopResilience(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.SPEC_RELAY = importlib.util.spec_from_file_location("fleet_relay", ROOT / "aot-group-control/relay.py")
        self.RELAY = importlib.util.module_from_spec(self.SPEC_RELAY)
        sys.modules[self.SPEC_RELAY.name] = self.RELAY
        self.SPEC_RELAY.loader.exec_module(self.RELAY)
        self.RELAY.STATE_PATH = pathlib.Path(self.td.name) / "state.json"

    def tearDown(self):
        self.td.cleanup()

    @mock.patch("fleet_relay.load_agent_config")
    @mock.patch("fleet_relay._read_small")
    @mock.patch("fleet_relay.controller.root_available", return_value=True)
    @mock.patch("fleet_relay.ws_connect")
    @mock.patch("fleet_relay.time.sleep")
    def test_initial_snapshot_error_does_not_kill_socket_and_processes_update_worker(
        self, mock_sleep, mock_ws_connect, mock_root, mock_read, mock_cfg
    ):
        mock_read.side_effect = lambda p: "m116" if "device_id" in str(p) else "NOVA"
        mock_cfg.return_value = {
            "worker_report_url": "https://hub.example.com/report",
            "agent_report_secret": "sec",
        }
        mock_sock = mock.MagicMock()
        mock_ws_connect.return_value = mock_sock

        update_msg = {
            "type": "aot_batch_action",
            "protocol": "fleet-batch-v1",
            "action": "UPDATE_WORKER",
            "action_id": "act-update-m116",
            "target_device_ids": ["m116"],
            "channel": "canary",
            "expires_at": 9999999999000,
            "release": {
                "protocol": "github-release-v1",
                "version": self.RELAY.WORKER_VERSION,
                "tag": "worker-v" + self.RELAY.WORKER_VERSION.removeprefix("aot-worker-"),
                "commit_sha": "a" * 40,
                "manifest": {
                    "name": "worker-manifest.json",
                    "size": 1234,
                    "sha256": "b" * 64,
                },
            }
        }
        import json
        frames = [
            (0x1, json.dumps(update_msg).encode("utf-8")),
            KeyboardInterrupt(),
        ]
        def fake_recv(s):
            res = frames.pop(0)
            if isinstance(res, BaseException):
                raise res
            return res

        with mock.patch.object(self.RELAY.controller, "snapshot", side_effect=self.RELAY.controller.AotControllerError("dumpsys_locked")), \
             mock.patch.object(self.RELAY, "_ws_recv_frame", side_effect=fake_recv), \
             mock.patch.object(self.RELAY.subprocess, "Popen") as mock_popen, \
             mock.patch.object(self.RELAY.updater, "normalize_channel", return_value="canary"), \
             mock.patch.object(self.RELAY, "action_already_processed", return_value=False):
            with self.assertRaises(KeyboardInterrupt):
                self.RELAY.fleet_loop()

        self.assertEqual(mock_ws_connect.call_count, 1)
        self.assertNotIn(mock.call(2), mock_sleep.call_args_list)
        mock_popen.assert_called_once()

    @mock.patch("fleet_relay.load_agent_config")
    @mock.patch("fleet_relay._read_small")
    @mock.patch("fleet_relay.controller.root_available", return_value=True)
    @mock.patch("fleet_relay.ws_connect")
    @mock.patch("fleet_relay.time.sleep")
    def test_real_transport_error_triggers_reconnect(
        self, mock_sleep, mock_ws_connect, mock_root, mock_read, mock_cfg
    ):
        mock_read.side_effect = lambda p: "m116" if "device_id" in str(p) else "NOVA"
        mock_cfg.return_value = {
            "worker_report_url": "https://hub.example.com/report",
            "agent_report_secret": "sec",
        }
        mock_sock = mock.MagicMock()
        mock_ws_connect.return_value = mock_sock

        calls = [0]
        def fake_recv(s):
            calls[0] += 1
            if calls[0] == 1:
                raise ConnectionError("connection_reset")
            raise KeyboardInterrupt()

        with mock.patch.object(self.RELAY.controller, "snapshot", return_value={"fingerprint": "fp1"}), \
             mock.patch.object(self.RELAY, "_ws_recv_frame", side_effect=fake_recv):
            with self.assertRaises(KeyboardInterrupt):
                self.RELAY.fleet_loop()

        self.assertEqual(mock_ws_connect.call_count, 2)
        self.assertIn(mock.call(2), mock_sleep.call_args_list)

    @mock.patch("fleet_relay.load_agent_config")
    @mock.patch("fleet_relay._read_small")
    @mock.patch("fleet_relay.controller.root_available", return_value=True)
    @mock.patch("fleet_relay.ws_connect")
    @mock.patch("fleet_relay.time.sleep")
    def test_send_transport_error_triggers_reconnect(
        self, mock_sleep, mock_ws_connect, mock_root, mock_read, mock_cfg
    ):
        mock_read.side_effect = lambda p: "m116" if "device_id" in str(p) else "NOVA"
        mock_cfg.return_value = {
            "worker_report_url": "https://hub.example.com/report",
            "agent_report_secret": "sec",
        }
        mock_sock = mock.MagicMock()
        mock_ws_connect.return_value = mock_sock

        calls = [0]
        def fake_send_json(s, payload):
            calls[0] += 1
            if calls[0] == 1:
                raise BrokenPipeError("broken_pipe")
            raise KeyboardInterrupt()

        with mock.patch.object(self.RELAY.controller, "snapshot", return_value={"fingerprint": "fp1"}), \
             mock.patch.object(self.RELAY, "_ws_send_json", side_effect=fake_send_json):
            with self.assertRaises(KeyboardInterrupt):
                self.RELAY.fleet_loop()

        self.assertEqual(mock_ws_connect.call_count, 2)
        self.assertIn(mock.call(2), mock_sleep.call_args_list)

    def test_handle_worker_update_popen_failure_does_not_mark_processed(self):
        state = {"processed_action_ids": []}
        message = {
            "protocol": self.RELAY.HUB_PROTOCOL_VERSION,
            "type": "aot_batch_action",
            "action": "UPDATE_WORKER",
            "action_id": "act-test-upd-1",
            "target_device_ids": ["m116"],
            "channel": "canary",
            "expires_at": int(time.time() * 1000) + 60000,
            "release": {
                "protocol": "github-release-v1",
                "version": self.RELAY.WORKER_VERSION,
                "tag": "worker-v" + self.RELAY.WORKER_VERSION.removeprefix("aot-worker-"),
                "commit_sha": "a" * 40,
                "manifest": {
                    "name": "worker-manifest.json",
                    "size": 1234,
                    "sha256": "b" * 64,
                },
            },
        }
        with mock.patch.object(self.RELAY.subprocess, "Popen", side_effect=OSError("process limit reached")), \
             mock.patch.object(self.RELAY.updater, "normalize_channel", return_value="canary"):
            res = self.RELAY._handle_worker_update(state, local_id="m116", message=message)
            self.assertTrue(res)
            self.assertNotIn("act-test-upd-1", state["processed_action_ids"])

    def test_handle_worker_update_cross_version_spawns_bootstrap(self):
        # Worker running older version .15.01 receiving target .16.04
        self.RELAY.WORKER_VERSION = "aot-worker-2026.08.15.01"
        state = {"processed_action_ids": []}
        message = {
            "protocol": self.RELAY.HUB_PROTOCOL_VERSION,
            "type": "aot_batch_action",
            "action": "UPDATE_WORKER",
            "action_id": "act-cross-ver-1",
            "target_device_ids": ["m116"],
            "channel": "canary",
            "expires_at": int(time.time() * 1000) + 60000,
            "release": {
                "protocol": "github-release-v1",
                "version": "aot-worker-2026.08.17.01",
                "tag": "worker-v2026.08.17.01",
                "commit_sha": "a" * 40,
                "manifest": {
                    "name": "worker-manifest.json",
                    "size": 1234,
                    "sha256": "b" * 64,
                },
            },
        }
        with mock.patch.object(self.RELAY.subprocess, "Popen") as mock_popen, \
             mock.patch.object(self.RELAY.updater, "normalize_channel", return_value="canary"):
            res = self.RELAY._handle_worker_update(state, local_id="m116", message=message)
            self.assertTrue(res)
            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            self.assertIn("update-action", cmd)
            self.assertIn("--action-id", cmd)
            self.assertIn("act-cross-ver-1", cmd)
            self.assertIn("--channel", cmd)
            self.assertIn("canary", cmd)
            self.assertIn("--release-metadata", cmd)
            self.assertIn("act-cross-ver-1", state["processed_action_ids"])

    def test_handle_worker_update_same_version_spawns_bootstrap(self):
        state = {"processed_action_ids": []}
        message = {
            "protocol": self.RELAY.HUB_PROTOCOL_VERSION,
            "type": "aot_batch_action",
            "action": "UPDATE_WORKER",
            "action_id": "act-same-ver-1",
            "target_device_ids": ["m116"],
            "channel": "stable",
            "expires_at": int(time.time() * 1000) + 60000,
            "release": {
                "protocol": "github-release-v1",
                "version": self.RELAY.WORKER_VERSION,
                "tag": "worker-v" + self.RELAY.WORKER_VERSION.removeprefix("aot-worker-"),
                "commit_sha": "a" * 40,
                "manifest": {
                    "name": "worker-manifest.json",
                    "size": 1234,
                    "sha256": "b" * 64,
                },
            },
        }
        with mock.patch.object(self.RELAY.subprocess, "Popen") as mock_popen, \
             mock.patch.object(self.RELAY.updater, "normalize_channel", return_value="stable"):
            res = self.RELAY._handle_worker_update(state, local_id="m116", message=message)
            self.assertTrue(res)
            mock_popen.assert_called_once()
            self.assertIn("act-same-ver-1", state["processed_action_ids"])

    def test_handle_worker_update_malformed_version_rejected(self):
        state = {"processed_action_ids": []}
        message = {
            "protocol": self.RELAY.HUB_PROTOCOL_VERSION,
            "type": "aot_batch_action",
            "action": "UPDATE_WORKER",
            "action_id": "act-bad-ver-1",
            "target_device_ids": ["m116"],
            "channel": "canary",
            "expires_at": int(time.time() * 1000) + 60000,
            "release": {
                "protocol": "github-release-v1",
                "version": "bad_version_format",
                "tag": "worker-vbad",
                "commit_sha": "a" * 40,
                "manifest": {
                    "name": "worker-manifest.json",
                    "size": 1234,
                    "sha256": "b" * 64,
                },
            },
        }
        with mock.patch.object(self.RELAY.subprocess, "Popen") as mock_popen, \
             mock.patch.object(self.RELAY.updater, "normalize_channel", return_value="canary"):
            res = self.RELAY._handle_worker_update(state, local_id="m116", message=message)
            self.assertTrue(res)
            mock_popen.assert_not_called()
            self.assertNotIn("act-bad-ver-1", state["processed_action_ids"])

    def test_handle_worker_update_tag_mismatch_rejected(self):
        state = {"processed_action_ids": []}
        message = {
            "protocol": self.RELAY.HUB_PROTOCOL_VERSION,
            "type": "aot_batch_action",
            "action": "UPDATE_WORKER",
            "action_id": "act-tag-mismatch-1",
            "target_device_ids": ["m116"],
            "channel": "canary",
            "expires_at": int(time.time() * 1000) + 60000,
            "release": {
                "protocol": "github-release-v1",
                "version": "aot-worker-2026.08.17.01",
                "tag": "worker-v2026.08.15.01",  # mismatch tag
                "commit_sha": "a" * 40,
                "manifest": {
                    "name": "worker-manifest.json",
                    "size": 1234,
                    "sha256": "b" * 64,
                },
            },
        }
        with mock.patch.object(self.RELAY.subprocess, "Popen") as mock_popen, \
             mock.patch.object(self.RELAY.updater, "normalize_channel", return_value="canary"):
            res = self.RELAY._handle_worker_update(state, local_id="m116", message=message)
            self.assertTrue(res)
            mock_popen.assert_not_called()
            self.assertNotIn("act-tag-mismatch-1", state["processed_action_ids"])

    def test_handle_worker_update_missing_manifest_rejected(self):
        state = {"processed_action_ids": []}
        message = {
            "protocol": self.RELAY.HUB_PROTOCOL_VERSION,
            "type": "aot_batch_action",
            "action": "UPDATE_WORKER",
            "action_id": "act-no-manifest-1",
            "target_device_ids": ["m116"],
            "channel": "canary",
            "expires_at": int(time.time() * 1000) + 60000,
            "release": {
                "protocol": "github-release-v1",
                "version": "aot-worker-2026.08.17.01",
                "tag": "worker-v2026.08.17.01",
                "commit_sha": "a" * 40,
            },
        }
        with mock.patch.object(self.RELAY.subprocess, "Popen") as mock_popen, \
             mock.patch.object(self.RELAY.updater, "normalize_channel", return_value="canary"):
            res = self.RELAY._handle_worker_update(state, local_id="m116", message=message)
            self.assertTrue(res)
            mock_popen.assert_not_called()
            self.assertNotIn("act-no-manifest-1", state["processed_action_ids"])

    def test_handle_worker_update_unauthorized_payload_rejected(self):
        state = {"processed_action_ids": []}
        for bad_key in ("token", "authorization", "secret", "password"):
            message = {
                "protocol": self.RELAY.HUB_PROTOCOL_VERSION,
                "type": "aot_batch_action",
                "action": "UPDATE_WORKER",
                "action_id": f"act-bad-{bad_key}",
                "target_device_ids": ["m116"],
                "channel": "canary",
                "expires_at": int(time.time() * 1000) + 60000,
                "release": {
                    "protocol": "github-release-v1",
                    "version": "aot-worker-2026.08.17.01",
                    "tag": "worker-v2026.08.17.01",
                    "commit_sha": "a" * 40,
                    "manifest": {
                        "name": "worker-manifest.json",
                        "size": 1234,
                        "sha256": "b" * 64,
                    },
                    bad_key: "leaked_val",
                },
            }
            with mock.patch.object(self.RELAY.subprocess, "Popen") as mock_popen, \
                 mock.patch.object(self.RELAY.updater, "normalize_channel", return_value="canary"):
                res = self.RELAY._handle_worker_update(state, local_id="m116", message=message)
                self.assertTrue(res)
                mock_popen.assert_not_called()
                self.assertNotIn(f"act-bad-{bad_key}", state["processed_action_ids"])

    def test_handle_worker_update_nested_secret_rejected(self):
        state = {"processed_action_ids": []}
        message = {
            "protocol": self.RELAY.HUB_PROTOCOL_VERSION,
            "type": "aot_batch_action",
            "action": "UPDATE_WORKER",
            "action_id": "act-nested-secret",
            "target_device_ids": ["m116"],
            "channel": "canary",
            "expires_at": int(time.time() * 1000) + 60000,
            "release": {
                "protocol": "github-release-v1",
                "version": "aot-worker-2026.08.17.01",
                "tag": "worker-v2026.08.17.01",
                "commit_sha": "a" * 40,
                "manifest": {
                    "name": "worker-manifest.json",
                    "size": 1234,
                    "sha256": "b" * 64,
                },
                "extra": {
                    "nested_creds": {
                        "token": "secret_token_val",
                    },
                },
            },
        }
        with mock.patch.object(self.RELAY.subprocess, "Popen") as mock_popen, \
             mock.patch.object(self.RELAY.updater, "normalize_channel", return_value="canary"):
            res = self.RELAY._handle_worker_update(state, local_id="m116", message=message)
            self.assertTrue(res)
            mock_popen.assert_not_called()
            self.assertNotIn("act-nested-secret", state["processed_action_ids"])

    def test_handle_worker_update_idempotency_duplicate_skipped(self):
        state = {"processed_action_ids": ["act-dup-1"]}
        message = {
            "protocol": self.RELAY.HUB_PROTOCOL_VERSION,
            "type": "aot_batch_action",
            "action": "UPDATE_WORKER",
            "action_id": "act-dup-1",
            "target_device_ids": ["m116"],
            "channel": "canary",
            "expires_at": int(time.time() * 1000) + 60000,
            "release": {
                "protocol": "github-release-v1",
                "version": "aot-worker-2026.08.17.01",
                "tag": "worker-v2026.08.17.01",
                "commit_sha": "a" * 40,
                "manifest": {
                    "name": "worker-manifest.json",
                    "size": 1234,
                    "sha256": "b" * 64,
                },
            },
        }
        with mock.patch.object(self.RELAY.subprocess, "Popen") as mock_popen, \
             mock.patch.object(self.RELAY.updater, "normalize_channel", return_value="canary"):
            res = self.RELAY._handle_worker_update(state, local_id="m116", message=message)
            self.assertTrue(res)
            mock_popen.assert_not_called()

    def test_initial_snapshot_error_sends_fallback_status_with_capabilities_and_version(self):
        sent_payloads = []
        mock_sock = mock.MagicMock()

        def fake_send(sock, payload):
            sent_payloads.append(payload)

        with mock.patch.object(self.RELAY.controller, "snapshot", side_effect=self.RELAY.controller.AotControllerError("dump_failed")), \
             mock.patch.object(self.RELAY, "_ws_send_json", side_effect=fake_send):
            fp = self.RELAY._send_live_status(mock_sock, device_id="m116", previous_fingerprint=None)
            self.assertIsNone(fp)
            self.assertEqual(len(sent_payloads), 1)
            payload = sent_payloads[0]
            self.assertEqual(payload.get("type"), "aot_status")
            self.assertEqual(payload.get("device_id"), "m116")
            self.assertEqual(payload.get("worker_version"), self.RELAY.WORKER_VERSION)
            self.assertTrue(payload.get("fallback"))
            self.assertIn("allocate_server_2pc", payload.get("capabilities", []))

    def test_subsequent_snapshot_error_does_not_send_status(self):
        sent_payloads = []
        mock_sock = mock.MagicMock()

        def fake_send(sock, payload):
            sent_payloads.append(payload)

        with mock.patch.object(self.RELAY.controller, "snapshot", side_effect=self.RELAY.controller.AotControllerError("dump_failed")), \
             mock.patch.object(self.RELAY, "_ws_send_json", side_effect=fake_send):
            fp = self.RELAY._send_live_status(mock_sock, device_id="m116", previous_fingerprint="existing_fp")
            self.assertIsNone(fp)
            self.assertEqual(len(sent_payloads), 0)

    def test_live_status_transport_errors_propagate_unsuppressed(self):
        mock_sock = mock.MagicMock()
        for err in [
            ConnectionError("connection_reset"),
            BrokenPipeError("broken_pipe"),
            OSError(104, "Connection reset by peer"),
            OSError(32, "Broken pipe"),
        ]:
            with mock.patch.object(self.RELAY.controller, "snapshot", return_value={"fingerprint": "fp1"}), \
                 mock.patch.object(self.RELAY, "_ws_send_json", side_effect=err):
                with self.assertRaises(type(err)):
                    self.RELAY._send_live_status(mock_sock, device_id="m116", previous_fingerprint=None)

    def test_runtime_requires_canonical_current_symlink(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("runtime_test", ROOT / "aot-group-control" / "runtime.py")
        rt_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rt_module)
        self.assertEqual(rt_module.RELAY_PATH, rt_module.ROOT / "current" / "relay.py")

    def test_notify_pending_healthy_rejects_version_mismatch(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            pending_file = pathlib.Path(tmp) / "update_pending.json"
            pending_file.write_text(json.dumps({"action_id": "act-1", "version": "aot-worker-2026.08.17.01"}))
            with mock.patch.object(self.RELAY.updater, "PENDING_PATH", pending_file), \
                 mock.patch.object(self.RELAY.updater, "notify_healthy") as mock_nh:
                # Mismatching running version should not notify healthy
                res = self.RELAY.updater.notify_pending_healthy("aot-worker-2026.08.11.5")
                self.assertFalse(res)
                mock_nh.assert_not_called()

                # Matching running version should notify healthy
                mock_nh.return_value = True
                res2 = self.RELAY.updater.notify_pending_healthy("aot-worker-2026.08.17.01")
                self.assertTrue(res2)
                mock_nh.assert_called_once_with("act-1", "aot-worker-2026.08.17.01")

    @mock.patch("fleet_relay.load_agent_config")
    @mock.patch("fleet_relay._read_small")
    @mock.patch("fleet_relay.controller.root_available", return_value=True)
    @mock.patch("fleet_relay.ws_connect")
    @mock.patch("fleet_relay.time.sleep")
    def test_send_transport_plain_oserror_triggers_reconnect(
        self, mock_sleep, mock_ws_connect, mock_root, mock_read, mock_cfg
    ):
        mock_read.side_effect = lambda p: "m116" if "device_id" in str(p) else "NOVA"
        mock_cfg.return_value = {
            "worker_report_url": "https://hub.example.com/report",
            "agent_report_secret": "sec",
        }
        mock_sock = mock.MagicMock()
        mock_ws_connect.return_value = mock_sock

        calls = [0]
        def fake_send_json(s, payload):
            calls[0] += 1
            if calls[0] == 1:
                raise OSError(104, "Connection reset by peer")
            raise KeyboardInterrupt()

        with mock.patch.object(self.RELAY.controller, "snapshot", return_value={"fingerprint": "fp1"}), \
             mock.patch.object(self.RELAY, "_ws_send_json", side_effect=fake_send_json):
            with self.assertRaises(KeyboardInterrupt):
                self.RELAY.fleet_loop()

        self.assertEqual(mock_ws_connect.call_count, 2)
        self.assertIn(mock.call(2), mock_sleep.call_args_list)

    def test_runtime_resolve_relay_path_from_release_dir(self):
        import importlib.util
        runtime_path = ROOT / "aot-group-control" / "runtime.py"
        spec = importlib.util.spec_from_file_location("runtime_test_mod", runtime_path)
        runtime_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime_mod)

        with tempfile.TemporaryDirectory() as tmp:
            supervisor_root = pathlib.Path(tmp)
            releases_dir = supervisor_root / "releases" / "aot-worker-2026.08.16.04"
            releases_dir.mkdir(parents=True)
            (releases_dir / "relay.py").write_text("print('release')")
            (releases_dir / "runtime.py").write_text("print('runtime')")

            current_symlink = supervisor_root / "current"
            current_symlink.symlink_to(releases_dir, target_is_directory=True)

            # Test resolver from release directory
            with mock.patch.object(runtime_mod, "ROOT", releases_dir):
                resolved = runtime_mod._resolve_relay_path()
                self.assertEqual(resolved, supervisor_root / "current" / "relay.py")
                self.assertEqual(resolved.resolve(), (releases_dir / "relay.py").resolve())


if __name__ == "__main__": unittest.main()
