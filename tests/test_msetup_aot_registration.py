from __future__ import annotations
import importlib.util, json, pathlib, tempfile, unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("registration", ROOT / "aot-group-control/msetup_registration.py")
MODULE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)

class MsetupFleetRegistrationTests(unittest.TestCase):
    def test_device_only_assignment_and_idempotent_atomic_config(self):
        config = MODULE.assignment_config("m301", {"ok": True, "device_id": "M301", "role": "follower", "session_id": "ignored"})
        self.assertEqual({"version": 3, "device_id": "m301", "enabled": True, "open_package": None}, config)
        self.assertNotIn("role", config); self.assertNotIn("session_id", config); self.assertNotIn("reference_device_id", config)
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "aot_group_config.json"
            self.assertTrue(MODULE.write_config_atomic(path, config))
            self.assertFalse(MODULE.write_config_atomic(path, config))
            self.assertEqual(config, json.loads(path.read_text()))

    def test_wrong_server_identity_fails_closed(self):
        with self.assertRaises(MODULE.RegistrationError):
            MODULE.assignment_config("m301", {"ok": True, "device_id": "m302"})

if __name__ == "__main__": unittest.main()
