from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
TARGET = REPO / "aot-group-control" / "msetup_registration.py"
spec = importlib.util.spec_from_file_location("msetup_registration_test_target", TARGET)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class MsetupRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="msetup-aot-registration-")
        self.root = pathlib.Path(self.temp.name)
        self.agent = self.root / "agent_config.json"
        self.config = self.root / "aot_group_config.json"
        self.state = self.root / "state"
        self.runtime = self.root / "runtime"
        self.state.mkdir()
        self.runtime.mkdir()
        self.agent.write_text(json.dumps({
            "worker_report_url": "https://worker.example/agent/report",
            "agent_report_secret": "fixture-secret",
        }), encoding="utf-8")
        self.original_post = module.post_json

    def tearDown(self):
        module.post_json = self.original_post
        self.temp.cleanup()

    def configure_args(self, device="m200", previous=""):
        return argparse.Namespace(
            origin="https://worker.example", agent_config=str(self.agent),
            aot_config=str(self.config), device_id=device,
            previous_device_id=previous,
        )

    def test_fresh_device_writes_atomic_config_and_rerun_is_idempotent(self):
        module.post_json = lambda *_args, **_kwargs: {
            "ok": True, "role": "follower", "session_id": "active-session",
            "reference_device_id": "m37",
        }
        self.assertEqual(module.configure(self.configure_args()), 0)
        data = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(data["device_id"], "m200")
        self.assertEqual(data["reference_device_id"], "m37")
        before = self.config.stat()
        before_bytes = self.config.read_bytes()
        self.assertEqual(module.configure(self.configure_args()), 0)
        after = self.config.stat()
        self.assertEqual(before.st_ino, after.st_ino)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
        self.assertEqual(before_bytes, self.config.read_bytes())

    def test_missing_and_multiple_sessions_fail_closed(self):
        for reason in ("no_active_aot_session", "multiple_active_aot_sessions"):
            module.post_json = lambda *_args, _reason=reason, **_kwargs: (_ for _ in ()).throw(
                module.RegistrationError(_reason)
            )
            with self.assertRaisesRegex(module.RegistrationError, reason):
                module.configure(self.configure_args())
            self.assertFalse(self.config.exists())

    def test_clone_identity_removes_local_state_and_requests_server_reset(self):
        self.config.write_text(json.dumps({
            "version": 2, "device_id": "m200", "enabled": True,
            "role": "follower", "session_id": "active-session",
            "reference_device_id": "m37", "open_package": None,
        }), encoding="utf-8")
        for name in module.LOCAL_IDENTITY_STATE:
            (self.state / name).write_text("old", encoding="utf-8")
        calls = []
        module.post_json = lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True}
        args = argparse.Namespace(
            origin="https://worker.example", agent_config=str(self.agent),
            aot_config=str(self.config), old_device_id="m199", new_device_id="m200",
            state_root=str(self.state), runtime_root=str(self.runtime),
        )
        self.assertEqual(module.reset_identity(args), 0)
        self.assertTrue(all(not (self.state / name).exists() for name in module.LOCAL_IDENTITY_STATE))
        payload = calls[0][0][3]
        self.assertEqual(payload["old_device_id"], "m199")
        self.assertEqual(payload["new_device_id"], "m200")

    def test_server_unseen_device_never_verifies(self):
        self.config.write_text(json.dumps({
            "version": 2, "device_id": "m200", "enabled": True,
            "role": "follower", "session_id": "active-session",
            "reference_device_id": "m37", "open_package": None,
        }), encoding="utf-8")
        module.post_json = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            module.RegistrationError("device_not_online_in_aot_hub")
        )
        args = argparse.Namespace(
            origin="https://worker.example", agent_config=str(self.agent),
            aot_config=str(self.config), device_id="m200", timeout="1",
        )
        with self.assertRaisesRegex(module.RegistrationError, "device_not_online_in_aot_hub"):
            module.verify(args)

    def test_msetup_completion_is_guarded_by_runtime_and_server_checks(self):
        text = (REPO / "setup-m166.sh").read_text(encoding="utf-8")
        complete = text.index('echo "========== HOÀN TẤT =========="')
        self.assertLess(text.index("AOT_CONFIG=OK", text.index("Worker heartbeat HTTP 200")), complete)
        self.assertLess(text.index("AOT_HUB_VISIBLE=YES"), complete)
        self.assertIn("PIDS=[0-9]+", text)
        self.assertIn("msetup_registration.py", text)


if __name__ == "__main__":
    unittest.main()
