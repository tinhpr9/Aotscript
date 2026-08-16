#!/usr/bin/env python3
"""Comprehensive test suite for Aotscript safe clone recovery and strict WebSocket validation.

Tests all required scenarios:
 1. missing setup-driver
 2. missing mprovision
 3. missing Shouko identity
 4. only one Shouko identity file missing
 5. contradictory IDs => fail-closed
 6. contradictory groups => fail-closed
 7. corrupt mprovision => fail-closed
 8. backup created before mutation
 9. unrelated Shouko files survive recovery
10. agent_config available/recreated before validation
11. recovery journal crash/resume
12. reboot after reset before provision
13. duplicate aotsetup
14. stale relay old identity
15. stale aot_group_config
16. Agent heartbeat online but AOT WS offline => setup NOT complete
17. successful recovery => AOT WS online + hub visible

Default Production Contract & Fault Injection Tests:
18. Case A: Default production without REQUIRE_AOT_WS; WS offline => fails closed
19. Case B: aot_group_config missing at verification time => fails closed
20. Case C: registration helper missing => fails closed, no fake success
21. Case D: worker_report_url invalid in agent_config => fails closed immediately without retry
22. Case E: Relay/Server offline with 503 => bounded retry fails closed after max attempts
23. Case F: Real HTTP Hub + real msetup_registration.py verify => setup_complete=yes
24. Crash before journal => zero mutation to state or identity files
25. Journal written -> crash before first mutation -> clean resume
26. Crash after identity invalidation -> clean resume
27. Crash after apply identity before provision -> clean resume
28. Retry: fail attempt 1 (503) -> success attempt 2 (200) => setup_complete=yes
29. New AOT group config contains exact target device_id
30. Multi-stage reboot cycle across planned, archived, identity_applied, agent_started
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import pathlib
import shutil
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SETUP_SCRIPT = REPO_ROOT / "setup.sh"
REAL_REGISTRATION_HELPER = REPO_ROOT / "aot-group-control/msetup_registration.py"


def sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def host_hash(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


class MockAotHubHandler(http.server.BaseHTTPRequestHandler):
    server: MockAotHubServer

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length > 0 else b"{}"
        payload = {}
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            pass

        auth = self.headers.get("X-Agent-Secret", self.headers.get("Authorization", ""))
        self.server.received_requests.append({
            "path": self.path,
            "auth": auth,
            "payload": payload,
        })

        if self.path in ("/aot/control/registration/verify", "/aot/verify"):
            if self.server.fail_first_n_requests > 0 and len(self.server.received_requests) <= self.server.fail_first_n_requests:
                status_code = 503
                response_body = {"error": "service_unavailable_retry_later"}
            elif self.server.response_sequence:
                idx = min(len(self.server.received_requests) - 1, len(self.server.response_sequence) - 1)
                status_code, response_body = self.server.response_sequence[idx]
            else:
                status_code = self.server.response_status
                response_body = self.server.response_payload
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response_body).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


class MockAotHubServer(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        super().__init__((host, port), MockAotHubHandler)
        self.received_requests: list[dict] = []
        self.response_status = 200
        self.response_payload = {"ok": True, "online": True, "visible_in_hub": True}
        self.response_sequence: list[tuple[int, dict]] = []
        self.fail_first_n_requests: int = 0
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"

    def shutdown_and_close(self):
        self.shutdown()
        self.server_close()


class BaseSetupFixture:
    def __init__(self, name: str = "test-case"):
        self.temp_dir = tempfile.TemporaryDirectory(prefix=f"aot-test-{name}-")
        self.root = pathlib.Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.state_dir = self.root / "state/aotscript"
        self.setup_driver = self.state_dir / "setup-driver"
        self.storage = self.root / "storage"
        self.shouko = self.storage / "Download/Shouko"
        self.prefix = self.root / "prefix"
        self.bin_dir = self.prefix / "bin"
        self.group_control = self.home / ".aot-group-control"

        self.home.mkdir(parents=True)
        self.setup_driver.mkdir(parents=True)
        self.shouko.mkdir(parents=True)
        self.bin_dir.mkdir(parents=True)
        self.group_control.mkdir(parents=True)

        if REAL_REGISTRATION_HELPER.exists():
            shutil.copy2(REAL_REGISTRATION_HELPER, self.state_dir / "msetup_registration.py")

        self.agent_path = self.storage / "Download/Agent_Core.py"
        self.agent_path.write_text('print("agent core running")\n', encoding="utf-8")
        self.agent_config_path = self.shouko / "agent_config.json"
        self.agent_config_content = {
            "worker_report_url": "https://hub.example.invalid/aot/report",
            "agent_report_secret": "test-secret-value-12345",
        }
        self.agent_config_path.write_text(
            json.dumps(self.agent_config_content, indent=2) + "\n",
            encoding="utf-8",
        )

        self.aot_group_config_path = self.shouko / "aot_group_config.json"
        self.aot_group_config_path.write_text(
            json.dumps({"version": 3, "device_id": "m117", "enabled": True}, indent=2) + "\n",
            encoding="utf-8",
        )

        self.cookie_path = self.shouko / "cookie.txt"
        self.cookie_path.write_text("user_session_token_xyz_keep_me\n", encoding="utf-8")
        self.server_links_path = self.shouko / "server_links.txt"
        self.server_links_path.write_text("https://server1.example.invalid\nhttps://server2.example.invalid\n", encoding="utf-8")
        self.cookies_tong_path = self.shouko / "Data_Tong_Cookies.txt"
        self.cookies_tong_path.write_text("cookie_data_tong_row_1\ncookie_data_tong_row_2\n", encoding="utf-8")
        self.acc_path = self.shouko / "acc.txt"
        self.acc_path.write_text("account_1:pass_1\naccount_2:pass_2\n", encoding="utf-8")

        self.delta_keep = self.storage / "Delta/keep.txt"
        self.delta_keep.parent.mkdir(parents=True, exist_ok=True)
        self.delta_keep.write_text("delta_keep_payload\n", encoding="utf-8")

    def cleanup(self):
        self.temp_dir.cleanup()

    def run_setup(
        self,
        host_id: str,
        *,
        device_id: str = "m74",
        group: str = "NOVA",
        confirm: str = "yes",
        checkpoint_action: str = "DA XONG",
        extra_env: dict[str, str] | None = None,
        input_mode: str = "env",
        mock_ws: str | None = "online",
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update({
            "HOME": str(self.home),
            "XDG_STATE_HOME": str(self.root / "state"),
            "PREFIX": str(self.prefix),
            "AOTSCRIPT_SETUP_TEST_MODE": "1",
            "AOTSCRIPT_SETUP_INPUT_MODE": input_mode,
            "AOTSCRIPT_SETUP_STORAGE_ROOT": str(self.storage),
            "AOTSCRIPT_SETUP_HOST_ID": host_id,
            "AOTSCRIPT_SETUP_DRY_RUN": "1",
            "AOTSCRIPT_SETUP_DEVICE_ID": device_id,
            "AOTSCRIPT_SETUP_GROUP": group,
            "AOTSCRIPT_SETUP_CONFIRM": confirm,
            "AOTSCRIPT_SETUP_CHECKPOINT_ACTION": checkpoint_action,
        })
        if mock_ws is not None:
            env["AOTSCRIPT_SETUP_MOCK_AOT_WS"] = mock_ws
        elif "AOTSCRIPT_SETUP_MOCK_AOT_WS" in env:
            del env["AOTSCRIPT_SETUP_MOCK_AOT_WS"]
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(SETUP_SCRIPT)],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )


class TestCloneRecoveryAndWebSocketValidation(unittest.TestCase):

    def setUp(self):
        self.fixtures: list[BaseSetupFixture] = []
        self.servers: list[MockAotHubServer] = []

    def tearDown(self):
        for s in self.servers:
            s.shutdown_and_close()
        for f in self.fixtures:
            f.cleanup()

    def create_fixture(self, name: str) -> BaseSetupFixture:
        fix = BaseSetupFixture(name)
        self.fixtures.append(fix)
        return fix

    def create_server(self) -> MockAotHubServer:
        srv = MockAotHubServer()
        self.servers.append(srv)
        return srv

    def test_01_missing_setup_driver_recovers(self):
        """1. Missing setup-driver recovers safely without dead-end."""
        fix = self.create_fixture("01-missing-setup-driver")
        shutil.rmtree(fix.setup_driver, ignore_errors=True)

        (fix.state_dir / "mprovision.json").write_text(
            json.dumps({"device_id": "m117", "device_group": "NOVA", "phase": "complete"}),
            encoding="utf-8",
        )
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        res = fix.run_setup("target-host-1", device_id="m74", group="NOVA")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("PHÁT HIỆN CLONE: m117 → m74", res.stderr)

        self.assertEqual((fix.setup_driver / "device_id").read_text().strip(), "m74")
        self.assertEqual((fix.setup_driver / "device_group").read_text().strip(), "NOVA")
        self.assertEqual((fix.shouko / "device_id.txt").read_text().strip(), "m74")
        self.assertEqual((fix.shouko / "device_group.txt").read_text().strip(), "NOVA")

        mprovision_data = json.loads((fix.state_dir / "mprovision.json").read_text())
        self.assertEqual(mprovision_data["device_id"], "m74")

    def test_02_missing_mprovision_recovers(self):
        """2. Missing mprovision recovers safely; no unsafe_mprovision_phase:missing dead-end."""
        fix = self.create_fixture("02-missing-mprovision")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.setup_driver / "host_fingerprint").write_text(host_hash("old-host") + "\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")
        mprovision_file = fix.state_dir / "mprovision.json"
        if mprovision_file.exists():
            mprovision_file.unlink()

        res = fix.run_setup("target-host-2", device_id="m74", group="NOVA")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertNotIn("unsafe_mprovision_phase", res.stdout + res.stderr)
        self.assertIn("PHÁT HIỆN CLONE: m117 → m74", res.stderr)

        self.assertTrue(mprovision_file.exists())
        mprovision_data = json.loads(mprovision_file.read_text())
        self.assertEqual(mprovision_data["device_id"], "m74")

    def test_03_missing_shouko_identity_recovers(self):
        """3. Missing Shouko identity files recovers safely without rmtree(shouko)."""
        fix = self.create_fixture("03-missing-shouko-identity")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.setup_driver / "host_fingerprint").write_text(host_hash("old-host") + "\n", encoding="utf-8")
        (fix.state_dir / "mprovision.json").write_text(
            json.dumps({"device_id": "m117", "device_group": "NOVA", "phase": "complete"}),
            encoding="utf-8",
        )
        (fix.shouko / "device_id.txt").unlink(missing_ok=True)
        (fix.shouko / "device_group.txt").unlink(missing_ok=True)

        res = fix.run_setup("target-host-3", device_id="m74", group="NOVA")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

        self.assertEqual((fix.shouko / "device_id.txt").read_text().strip(), "m74")
        self.assertEqual((fix.shouko / "device_group.txt").read_text().strip(), "NOVA")

    def test_04_only_one_shouko_identity_file_missing_recovers(self):
        """4. Only one Shouko identity file missing recovers without crash."""
        fix = self.create_fixture("04-one-shouko-file-missing")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.setup_driver / "host_fingerprint").write_text(host_hash("old-host") + "\n", encoding="utf-8")
        (fix.state_dir / "mprovision.json").write_text(
            json.dumps({"device_id": "m117", "device_group": "NOVA", "phase": "complete"}),
            encoding="utf-8",
        )
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").unlink(missing_ok=True)

        res = fix.run_setup("target-host-4", device_id="m74", group="NOVA")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertNotIn("incomplete_identity_pair", res.stdout + res.stderr)
        self.assertEqual((fix.shouko / "device_id.txt").read_text().strip(), "m74")
        self.assertEqual((fix.shouko / "device_group.txt").read_text().strip(), "NOVA")

    def test_05_contradictory_ids_fail_closed(self):
        """5. Contradictory IDs across sources fail-closed; no files modified."""
        fix = self.create_fixture("05-conflict-ids")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.state_dir / "mprovision.json").write_text(
            json.dumps({"device_id": "m88", "device_group": "NOVA", "phase": "complete"}),
            encoding="utf-8",
        )
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        before_setup_id = sha256_file(fix.setup_driver / "device_id")
        before_mprov = sha256_file(fix.state_dir / "mprovision.json")
        before_shouko_id = sha256_file(fix.shouko / "device_id.txt")

        res = fix.run_setup("target-host-5", device_id="m74", group="NOVA")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("IDENTITY_CONFLICT=", res.stdout + res.stderr)

        self.assertEqual(sha256_file(fix.setup_driver / "device_id"), before_setup_id)
        self.assertEqual(sha256_file(fix.state_dir / "mprovision.json"), before_mprov)
        self.assertEqual(sha256_file(fix.shouko / "device_id.txt"), before_shouko_id)

    def test_06_contradictory_groups_fail_closed(self):
        """6. Contradictory groups fail-closed; no state modified."""
        fix = self.create_fixture("06-conflict-groups")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.state_dir / "mprovision.json").write_text(
            json.dumps({"device_id": "m117", "device_group": "MARMOT", "phase": "complete"}),
            encoding="utf-8",
        )
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        res = fix.run_setup("target-host-6", device_id="m74", group="NOVA")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("IDENTITY_CONFLICT=", res.stdout + res.stderr)

    def test_07_corrupt_mprovision_fail_closed(self):
        """7. Corrupt mprovision JSON fails-closed."""
        fix = self.create_fixture("07-corrupt-mprovision")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.state_dir / "mprovision.json").write_text("{broken invalid json\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        before_raw = (fix.state_dir / "mprovision.json").read_bytes()
        res = fix.run_setup("target-host-7", device_id="m74", group="NOVA")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("invalid_json:", res.stdout + res.stderr)
        self.assertEqual((fix.state_dir / "mprovision.json").read_bytes(), before_raw)

    def test_08_backup_created_before_mutation(self):
        """8. Backup created before mutation in incomplete clone recovery with manifest."""
        fix = self.create_fixture("08-backup-before-mutation")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        res = fix.run_setup("target-host-8", device_id="m74", group="NOVA")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

        foreign_state = fix.state_dir / "foreign-state"
        self.assertTrue(foreign_state.exists())
        backups = list(foreign_state.glob("*-recovery*"))
        self.assertTrue(len(backups) > 0, f"No recovery backups in {foreign_state}")
        backup_dir = backups[0]
        manifest_file = backup_dir / "manifest.json"
        self.assertTrue(manifest_file.exists())
        manifest = json.loads(manifest_file.read_text())
        self.assertEqual(manifest["source_id"], "m117")
        self.assertEqual(manifest["target_id"], "m74")
        self.assertTrue(len(manifest["files"]) > 0)
        for item in manifest["files"]:
            archive_p = pathlib.Path(item["archive_path"])
            self.assertTrue(archive_p.is_file())
            self.assertEqual(sha256_file(archive_p), item["sha256"])

    def test_09_unrelated_shouko_files_survive_recovery(self):
        """9. Unrelated Shouko files survive recovery byte-for-byte; rmtree(shouko) eliminated."""
        fix = self.create_fixture("09-unrelated-shouko-survive")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        before_cookie = sha256_file(fix.cookie_path)
        before_server_links = sha256_file(fix.server_links_path)
        before_cookies_tong = sha256_file(fix.cookies_tong_path)
        before_acc = sha256_file(fix.acc_path)
        before_delta = sha256_file(fix.delta_keep)

        res = fix.run_setup("target-host-9", device_id="m74", group="NOVA")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

        self.assertEqual(sha256_file(fix.cookie_path), before_cookie)
        self.assertEqual(sha256_file(fix.server_links_path), before_server_links)
        self.assertEqual(sha256_file(fix.cookies_tong_path), before_cookies_tong)
        self.assertEqual(sha256_file(fix.acc_path), before_acc)
        self.assertEqual(sha256_file(fix.delta_keep), before_delta)

    def test_10_agent_config_available_and_validated(self):
        """10. agent_config.json is preserved and validated before agent restart."""
        fix = self.create_fixture("10-agent-config-preserved")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        before_config_hash = sha256_file(fix.agent_config_path)
        res = fix.run_setup("target-host-10", device_id="m74", group="NOVA")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue(fix.agent_config_path.exists())
        self.assertEqual(sha256_file(fix.agent_config_path), before_config_hash)

    def test_11_recovery_journal_crash_and_resume(self):
        """11. Recovery journal resumes safely after crash without re-prompt."""
        fix = self.create_fixture("11-crash-resume")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        res1 = fix.run_setup(
            "target-host-11",
            device_id="m74",
            group="NOVA",
            extra_env={"AOTSCRIPT_SETUP_INTERRUPT_AFTER": "archive"},
        )
        self.assertEqual(res1.returncode, 75, res1.stdout + res1.stderr)

        res2 = fix.run_setup("target-host-11", device_id="", group="")
        self.assertEqual(res2.returncode, 0, res2.stdout + res2.stderr)
        self.assertIn("Resume migration m117 → m74 tại stage=archived", res2.stdout + res2.stderr)
        self.assertEqual((fix.shouko / "device_id.txt").read_text().strip(), "m74")

    def test_12_reboot_after_reset_before_provision(self):
        """12. Reboot / re-run after identity reset completes cleanly."""
        fix = self.create_fixture("12-reboot-after-reset")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        res = fix.run_setup("target-host-12", device_id="m74", group="NOVA")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

        res_reboot = fix.run_setup("target-host-12", device_id="", group="")
        self.assertEqual(res_reboot.returncode, 0, res_reboot.stdout + res_reboot.stderr)
        self.assertIn("Identity hiện tại: m74 / NOVA", res_reboot.stdout + res_reboot.stderr)

    def test_13_duplicate_aotsetup(self):
        """13. Duplicate / concurrent aotsetup is blocked by setup.lock."""
        fix = self.create_fixture("13-duplicate-lock")
        (fix.setup_driver / "device_id").write_text("m74\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.setup_driver / "host_fingerprint").write_text(host_hash("target-host-13") + "\n", encoding="utf-8")
        (fix.state_dir / "mprovision.json").write_text(
            json.dumps({"device_id": "m74", "device_group": "NOVA", "phase": "complete"}),
            encoding="utf-8",
        )
        (fix.shouko / "device_id.txt").write_text("m74\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        env = os.environ.copy()
        env.update({
            "HOME": str(fix.home),
            "XDG_STATE_HOME": str(fix.root / "state"),
            "PREFIX": str(fix.prefix),
            "AOTSCRIPT_SETUP_TEST_MODE": "1",
            "AOTSCRIPT_SETUP_INPUT_MODE": "env",
            "AOTSCRIPT_SETUP_STORAGE_ROOT": str(fix.storage),
            "AOTSCRIPT_SETUP_HOST_ID": "target-host-13",
            "AOTSCRIPT_SETUP_DRY_RUN": "1",
            "AOTSCRIPT_SETUP_HOLD_LOCK_SECONDS": "3",
            "AOTSCRIPT_SETUP_MOCK_AOT_WS": "online",
        })
        proc1 = subprocess.Popen(["bash", str(SETUP_SCRIPT)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for _ in range(50):
                if (fix.setup_driver / "setup.lock").exists():
                    break
                time.sleep(0.05)
            res2 = fix.run_setup("target-host-13")
            self.assertNotEqual(res2.returncode, 0)
            self.assertIn("Một phiên aotsetup khác đang chạy", res2.stderr)
        finally:
            proc1.terminate()
            try:
                proc1.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc1.kill()
                proc1.wait()

    def test_14_stale_relay_old_identity(self):
        """14. Stale relay state from previous identity is purged during recovery."""
        fix = self.create_fixture("14-stale-relay")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        stale_group_state = fix.group_control / "aot_group_state.json"
        stale_group_state.write_text(json.dumps({"device_id": "m117", "stale": True}), encoding="utf-8")

        res = fix.run_setup("target-host-14", device_id="m74", group="NOVA")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertFalse(stale_group_state.exists(), "Stale group state was not purged!")

    def test_15_stale_aot_group_config(self):
        """15. Stale aot_group_config.json with old device ID is updated/cleared."""
        fix = self.create_fixture("15-stale-aot-group-config")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        aot_cfg = fix.shouko / "aot_group_config.json"
        aot_cfg.write_text(json.dumps({"version": 3, "device_id": "m117", "enabled": True}), encoding="utf-8")

        res = fix.run_setup("target-host-15", device_id="m74", group="NOVA")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        if aot_cfg.exists():
            cfg_data = json.loads(aot_cfg.read_text())
            self.assertEqual(cfg_data["device_id"], "m74", "aot_group_config still had old device ID!")

    def test_16_agent_heartbeat_online_but_aot_ws_offline_fails_complete(self):
        """16. Agent heartbeat online but AOT WS offline => setup NOT complete."""
        fix = self.create_fixture("16-ws-offline")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        res = fix.run_setup(
            "target-host-16",
            device_id="m74",
            group="NOVA",
            mock_ws="offline",
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("AOT WebSocket", res.stdout + res.stderr)
        self.assertFalse((fix.setup_driver / "setup_complete").exists())

    def test_17_successful_recovery_aot_ws_online_and_hub_visible(self):
        """17. Successful recovery => Agent online + AOT WS online + Hub visible."""
        fix = self.create_fixture("17-ws-online-success")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        res = fix.run_setup(
            "target-host-17",
            device_id="m74",
            group="NOVA",
            mock_ws="online",
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("AOT WebSocket ONLINE và Hub visible", res.stdout + res.stderr)
        self.assertEqual((fix.setup_driver / "device_id").read_text().strip(), "m74")
        self.assertEqual((fix.shouko / "device_id.txt").read_text().strip(), "m74")
        self.assertEqual((fix.setup_driver / "setup_complete").read_text().strip(), "yes")

    # =========================================================================
    # Default Production Contract Tests & Fault Injection Tests
    # =========================================================================

    def test_18_case_a_default_production_ws_offline_fails_closed(self):
        """18. Case A: Default production without REQUIRE_AOT_WS; WS offline => fails closed."""
        fix = self.create_fixture("18-prod-ws-offline")
        server = self.create_server()
        server.response_status = 200
        server.response_payload = {"ok": True, "online": False, "visible_in_hub": False}

        fix.agent_config_path.write_text(
            json.dumps({"worker_report_url": f"{server.url}/aot/report", "agent_report_secret": "sec-123"}),
            encoding="utf-8",
        )
        fix.aot_group_config_path.write_text(
            json.dumps({"version": 3, "device_id": "m74", "enabled": True}),
            encoding="utf-8",
        )

        res = fix.run_setup("target-host-18", device_id="m74", group="NOVA", mock_ws=None)
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse((fix.setup_driver / "setup_complete").exists())
        self.assertTrue(len(server.received_requests) > 0, "No verify request was sent to Hub!")

    def test_19_case_b_missing_aot_config_fails_closed(self):
        """19. Case B: aot_group_config.json missing at verification time => fails closed."""
        fix = self.create_fixture("19-missing-aot-config")
        fix.aot_group_config_path.unlink(missing_ok=True)

        res = fix.run_setup("target-host-19", device_id="m74", group="NOVA", mock_ws=None)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Thiếu aot_group_config.json", res.stdout + res.stderr)
        self.assertFalse((fix.setup_driver / "setup_complete").exists())

    def test_20_case_c_missing_registration_helper_fails_closed(self):
        """20. Case C: registration helper missing => fails closed, no fake success."""
        fix = self.create_fixture("20-missing-helper")
        (fix.state_dir / "msetup_registration.py").unlink(missing_ok=True)
        (fix.group_control / "msetup_registration.py").unlink(missing_ok=True)

        res = fix.run_setup("target-host-20", device_id="m74", group="NOVA", mock_ws=None)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Thiếu helper msetup_registration.py", res.stdout + res.stderr)
        self.assertFalse((fix.setup_driver / "setup_complete").exists())

    def test_21_case_d_invalid_worker_report_url_fails_closed(self):
        """21. Case D: worker_report_url invalid in agent_config => fails closed."""
        fix = self.create_fixture("21-invalid-origin")
        fix.agent_config_path.write_text(
            json.dumps({"worker_report_url": "invalid://", "agent_report_secret": "sec"}),
            encoding="utf-8",
        )

        res = fix.run_setup("target-host-21", device_id="m74", group="NOVA", mock_ws=None)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("worker_report_url", res.stdout + res.stderr)
        self.assertFalse((fix.setup_driver / "setup_complete").exists())

    def test_22_case_e_relay_offline_bounded_retry_fails_closed(self):
        """22. Case E: Relay / WS not connected => bounded retry triggers then fails closed."""
        fix = self.create_fixture("22-relay-offline-retry")
        server = self.create_server()
        server.response_status = 503
        server.response_payload = {"error": "service_unavailable"}

        fix.agent_config_path.write_text(
            json.dumps({"worker_report_url": f"{server.url}/aot/report", "agent_report_secret": "sec-123"}),
            encoding="utf-8",
        )
        fix.aot_group_config_path.write_text(
            json.dumps({"version": 3, "device_id": "m74", "enabled": True}),
            encoding="utf-8",
        )

        t0 = time.monotonic()
        res = fix.run_setup("target-host-22", device_id="m74", group="NOVA", mock_ws=None)
        elapsed = time.monotonic() - t0

        self.assertNotEqual(res.returncode, 0)
        self.assertIn("AOT WebSocket", res.stdout + res.stderr)
        self.assertFalse((fix.setup_driver / "setup_complete").exists())
        self.assertLess(elapsed, 45, "Retry exceeded finite timeout limit!")

    def test_23_case_f_real_http_ws_online_hub_visible_completes_setup(self):
        """23. Case F: Real HTTP verification succeeds => setup_complete=yes recorded."""
        fix = self.create_fixture("23-real-http-success")
        server = self.create_server()
        server.response_status = 200
        server.response_payload = {"ok": True, "online": True, "visible_in_hub": True}

        fix.agent_config_path.write_text(
            json.dumps({"worker_report_url": f"{server.url}/aot/report", "agent_report_secret": "sec-verified-99"}),
            encoding="utf-8",
        )
        fix.aot_group_config_path.write_text(
            json.dumps({"version": 3, "device_id": "m74", "enabled": True}),
            encoding="utf-8",
        )

        res = fix.run_setup("target-host-23", device_id="m74", group="NOVA", mock_ws=None)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("AOT WebSocket ONLINE và Hub visible", res.stdout + res.stderr)
        self.assertEqual((fix.setup_driver / "setup_complete").read_text().strip(), "yes")

        self.assertTrue(len(server.received_requests) > 0)
        req = server.received_requests[0]
        self.assertEqual(req["auth"], "sec-verified-99")
        self.assertEqual(req["payload"].get("device_id"), "m74")

    def test_24_crash_before_journal_zero_mutation(self):
        """24. Fault injection: crash before journal write leaves state 100% untouched."""
        fix = self.create_fixture("24-zero-mutation-before-journal")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        before_setup_id = sha256_file(fix.setup_driver / "device_id")
        before_shouko_id = sha256_file(fix.shouko / "device_id.txt")

        # Invalid confirmation or interrupted inspect/plan
        res = fix.run_setup("target-host-24", device_id="m74", group="NOVA", confirm="no")
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(sha256_file(fix.setup_driver / "device_id"), before_setup_id)
        self.assertEqual(sha256_file(fix.shouko / "device_id.txt"), before_shouko_id)

    def test_25_crash_after_journal_before_mutation_resumes(self):
        """25. Fault injection: journal written -> crash before mutation -> clean resume."""
        fix = self.create_fixture("25-crash-after-journal")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        res1 = fix.run_setup(
            "target-host-25",
            device_id="m74",
            group="NOVA",
            extra_env={"AOTSCRIPT_SETUP_INTERRUPT_AFTER": "plan"},
        )
        self.assertEqual(res1.returncode, 75, res1.stdout + res1.stderr)

        # Before mutation happened, original files were still intact
        self.assertEqual((fix.shouko / "device_id.txt").read_text().strip(), "m117")

        # Resume cleanly
        res2 = fix.run_setup("target-host-25", device_id="", group="")
        self.assertEqual(res2.returncode, 0, res2.stdout + res2.stderr)
        self.assertEqual((fix.shouko / "device_id.txt").read_text().strip(), "m74")
        self.assertEqual((fix.setup_driver / "setup_complete").read_text().strip(), "yes")

    def test_26_crash_after_apply_before_provision_resumes(self):
        """26. Fault injection: crash after apply before provision -> clean resume."""
        fix = self.create_fixture("26-crash-after-apply")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        res1 = fix.run_setup(
            "target-host-26",
            device_id="m74",
            group="NOVA",
            extra_env={"AOTSCRIPT_SETUP_INTERRUPT_AFTER": "apply"},
        )
        self.assertEqual(res1.returncode, 75, res1.stdout + res1.stderr)
        self.assertEqual((fix.shouko / "device_id.txt").read_text().strip(), "m74")

        res2 = fix.run_setup("target-host-26", device_id="", group="")
        self.assertEqual(res2.returncode, 0, res2.stdout + res2.stderr)
        self.assertEqual((fix.setup_driver / "setup_complete").read_text().strip(), "yes")

    def test_27_retry_fail_attempt1_success_attempt2(self):
        """27. Bounded retry: fail attempt 1 (503) -> success attempt 2 (200) => completes."""
        fix = self.create_fixture("27-retry-success-attempt-2")
        server = self.create_server()
        server.fail_first_n_requests = 6

        fix.agent_config_path.write_text(
            json.dumps({"worker_report_url": f"{server.url}/aot/report", "agent_report_secret": "sec-retry-99"}),
            encoding="utf-8",
        )
        fix.aot_group_config_path.write_text(
            json.dumps({"version": 3, "device_id": "m74", "enabled": True}),
            encoding="utf-8",
        )

        res = fix.run_setup("target-host-27", device_id="m74", group="NOVA", mock_ws=None)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("Lần 1/3: AOT WebSocket chưa ONLINE", res.stdout + res.stderr)
        self.assertIn("AOT WebSocket ONLINE và Hub visible", res.stdout + res.stderr)
        self.assertEqual((fix.setup_driver / "setup_complete").read_text().strip(), "yes")

    def test_28_new_aot_config_has_target_device_id(self):
        """28. Post-migration AOT config contains exact target device_id."""
        fix = self.create_fixture("28-target-device-id-in-config")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        res = fix.run_setup("target-host-28", device_id="m74", group="NOVA")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

        cfg_path = fix.shouko / "aot_group_config.json"
        self.assertTrue(cfg_path.exists())
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(cfg.get("device_id"), "m74")

    def test_29_permanent_invalid_config_no_useless_retry(self):
        """29. Permanent invalid config fails immediately without retry loop."""
        fix = self.create_fixture("29-no-useless-retry")
        fix.agent_config_path.unlink(missing_ok=True)

        res = fix.run_setup("target-host-29", device_id="m74", group="NOVA", mock_ws=None)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Thiếu agent_config.json", res.stdout + res.stderr)
        self.assertNotIn("Lần 1/3", res.stdout + res.stderr)
        self.assertNotIn("Lần 2/3", res.stdout + res.stderr)

    def test_30_multi_stage_reboot_cycle(self):
        """30. Interruption and resume across all intermediate stages."""
        for stage in ("plan", "archive", "apply", "start_agent"):
            fix = self.create_fixture(f"30-reboot-{stage}")
            (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
            (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
            (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
            (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

            res1 = fix.run_setup(
                f"target-host-30-{stage}",
                device_id="m74",
                group="NOVA",
                extra_env={"AOTSCRIPT_SETUP_INTERRUPT_AFTER": stage},
            )
            self.assertEqual(res1.returncode, 75, f"Stage {stage} did not exit with 75: {res1.stdout + res1.stderr}")

            res2 = fix.run_setup(f"target-host-30-{stage}", device_id="", group="")
            self.assertEqual(res2.returncode, 0, f"Resume from {stage} failed: {res2.stdout + res2.stderr}")
            self.assertEqual((fix.setup_driver / "setup_complete").read_text().strip(), "yes")

    def test_31_aot_verify_pass_journal_complete_fail_fails_closed(self):
        """31. AOT verify PASS + journal complete FAIL => setup_complete absent, exits error."""
        fix = self.create_fixture("31-journal-fail-closed")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        res = fix.run_setup(
            "target-host-31",
            device_id="m74",
            group="NOVA",
            extra_env={"AOTSCRIPT_SETUP_FAULT_JOURNAL_COMPLETE": "1"},
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse((fix.setup_driver / "setup_complete").exists())
        self.assertIn("Không hoàn tất được migration journal", res.stdout + res.stderr)

    def test_32_retry_after_journal_complete_fail_completes(self):
        """32. Rerun after journal complete fail retries terminal transition cleanly."""
        fix = self.create_fixture("32-retry-journal-complete")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        res1 = fix.run_setup(
            "target-host-32",
            device_id="m74",
            group="NOVA",
            extra_env={"AOTSCRIPT_SETUP_FAULT_JOURNAL_COMPLETE": "1"},
        )
        self.assertNotEqual(res1.returncode, 0)
        self.assertFalse((fix.setup_driver / "setup_complete").exists())

        res2 = fix.run_setup("target-host-32", device_id="", group="")
        self.assertEqual(res2.returncode, 0, res2.stdout + res2.stderr)
        self.assertEqual((fix.setup_driver / "setup_complete").read_text().strip(), "yes")

        # Verify journal is terminal complete
        journal_file = fix.setup_driver / "clone-migration.json"
        self.assertTrue(journal_file.exists())
        jdata = json.loads(journal_file.read_text(encoding="utf-8"))
        self.assertEqual(jdata["stage"], "complete")

    def test_33_crash_after_journal_complete_before_setup_complete_resumes(self):
        """33. Crash immediately after journal complete but before setup_complete resumes safely."""
        fix = self.create_fixture("33-crash-after-journal-complete")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        res1 = fix.run_setup(
            "target-host-33",
            device_id="m74",
            group="NOVA",
            extra_env={"AOTSCRIPT_SETUP_INTERRUPT_AFTER": "journal_complete"},
        )
        self.assertEqual(res1.returncode, 75, res1.stdout + res1.stderr)
        self.assertFalse((fix.setup_driver / "setup_complete").exists())

        journal_file = fix.setup_driver / "clone-migration.json"
        self.assertTrue(journal_file.exists())
        jdata = json.loads(journal_file.read_text(encoding="utf-8"))
        self.assertEqual(jdata["stage"], "complete")

        res2 = fix.run_setup("target-host-33", device_id="", group="")
        self.assertEqual(res2.returncode, 0, res2.stdout + res2.stderr)
        self.assertEqual((fix.setup_driver / "setup_complete").read_text().strip(), "yes")

    def test_34_crash_after_setup_complete_rerun_idempotent(self):
        """34. Rerun after complete is strictly idempotent."""
        fix = self.create_fixture("34-idempotent-complete")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        res1 = fix.run_setup("target-host-34", device_id="m74", group="NOVA")
        self.assertEqual(res1.returncode, 0, res1.stdout + res1.stderr)
        self.assertEqual((fix.setup_driver / "setup_complete").read_text().strip(), "yes")

        res2 = fix.run_setup("target-host-34", device_id="", group="")
        self.assertEqual(res2.returncode, 0, res2.stdout + res2.stderr)
        self.assertEqual((fix.setup_driver / "setup_complete").read_text().strip(), "yes")


    def test_35_production_e2e_recovery(self):
        """35. Production E2E test without DRY_RUN. Uses real wizard orchestration logic."""
        fix = self.create_fixture("35-e2e-recovery")
        (fix.setup_driver / "device_id").write_text("m117\n", encoding="utf-8")
        (fix.setup_driver / "device_group").write_text("NOVA\n", encoding="utf-8")
        (fix.shouko / "device_id.txt").write_text("m117\n", encoding="utf-8")
        (fix.shouko / "device_group.txt").write_text("NOVA\n", encoding="utf-8")

        # Create a mock wizard that actually runs `provision-device.sh reconcile`!
        wizard_path = fix.home / "bin/aotscript-wizard"
        wizard_path.parent.mkdir(parents=True, exist_ok=True)
        wizard_path.write_text(
            "#!/bin/bash\n"
            "if [ \"$1\" = start ]; then\n"
            f"  AOTSCRIPT_SETUP_TEST_MODE=1 STATE_DIR='{fix.state_dir}' \\\n"
            f"  bash {REPO_ROOT}/provision-device.sh reconcile\n"
            "fi\n",
            encoding="utf-8",
        )
        wizard_path.chmod(0o755)

        # Mock rclone and su so provision-device.sh automatic() doesn't fail
        rclone_path = fix.home / "bin/rclone"
        rclone_path.write_text(
            "#!/bin/bash\n"
            "echo \"rclone called with: $@\" >> /tmp/mock_rclone.log\n"
            "if [ \"$1\" = listremotes ]; then echo \"gdrive:\"; exit 0; fi\n"
            "if [ \"$1\" = lsf ]; then\n"
            "  echo \"Shouko.zip\"\n"
            "  echo \"Shouko.zip.sha256\"\n"
            "  echo \"Delta.zip\"\n"
            "  echo \"Delta.zip.sha256\"\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        rclone_path.chmod(0o755)
        
        su_path = fix.home / "bin/su"
        su_path.write_text(
            "#!/bin/bash\n"
            "if [ \"$2\" = id ]; then echo \"uid=0(root)\"; exit 0; fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        su_path.chmod(0o755)
        curl_path = fix.home / "bin/curl"
        curl_path.write_text(
            "#!/bin/bash\n"
            "for arg in \"$@\"; do\n"
            "  if [[ \"$arg\" == *\"setup-m166.sh\"* ]]; then\n"
            "    echo \"#!/bin/bash\" > \"${!#}\"\n"
            "    echo \"echo MOCKED SETUP-M166\" >> \"${!#}\"\n"
            "    exit 0\n"
            "  fi\n"
            "done\n"
            "exec /usr/bin/curl \"$@\"\n",
            encoding="utf-8",
        )
        curl_path.chmod(0o755)
        print(f"DEBUG: wizard_path is {wizard_path}, exists: {wizard_path.exists()}, exec: {os.access(wizard_path, os.X_OK)}")

        res = fix.run_setup(
            "target-host-35",
            device_id="m74",
            group="NOVA",
            extra_env={"AOTSCRIPT_SETUP_DRY_RUN": "0"},
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        
        self.assertEqual((fix.setup_driver / "setup_complete").read_text().strip(), "yes")
        
        mprov_path = fix.state_dir / "mprovision.json"
        self.assertTrue(mprov_path.exists())
        jdata = json.loads(mprov_path.read_text())
        
        # In provision-device.sh, automatic -> manual_post via pause_post
        self.assertEqual(jdata["phase"], "manual_post")


if __name__ == "__main__":
    unittest.main()
