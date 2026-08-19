from __future__ import annotations
import argparse
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("registration", ROOT / "aot-group-control/msetup_registration.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MsetupFleetRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.contract_path = ROOT / "aot-group-control/aot-registration-contract.json"
        self.assertTrue(self.contract_path.is_file(), "Canonical registration contract artifact must exist")
        self.contract = json.loads(self.contract_path.read_text(encoding="utf-8"))

    def _validate_instance(self, instance: dict, def_name: str):
        defs = self.contract.get("$defs", {})
        self.assertIn(def_name, defs, f"Schema $defs must include {def_name}")
        schema = defs[def_name]
        self.assertEqual(schema.get("type"), "object")
        for req in schema.get("required", []):
            self.assertIn(req, instance, f"Missing required property {req} for {def_name}")
        if schema.get("additionalProperties") is False:
            allowed = set(schema.get("properties", {}).keys())
            for key in instance:
                self.assertIn(key, allowed, f"Disallowed property {key} for {def_name}")

    def test_canonical_contract_structure_and_definitions(self):
        self.assertEqual(self.contract.get("identity_model"), "device_id_only")
        self.assertIn("discover", self.contract["operations"])
        self.assertIn("verify", self.contract["operations"])
        self.assertIn("reset", self.contract["operations"])
        self.assertEqual(self.contract.get("forbidden_fields"), ["role", "session_id", "reference_device_id"])

        # Test request and response schemas against valid instances
        self._validate_instance({"device_id": "m118"}, "DiscoverRequest")
        self._validate_instance({"ok": True, "device_id": "m118"}, "DiscoverResponseSuccess")
        self._validate_instance({"device_id": "m118"}, "VerifyRequest")
        self._validate_instance({"ok": True, "device_id": "m118", "online": True, "visible_in_hub": True}, "VerifyResponseSuccess")
        self._validate_instance({"old_device_id": "m118", "new_device_id": "m120"}, "ResetRequest")
        self._validate_instance({"ok": True, "old_device_id": "m118", "new_device_id": "m120"}, "ResetResponseSuccess")

    def test_device_only_assignment_and_idempotent_atomic_config(self):
        # Discover response satisfying canonical contract
        discover_res = {"ok": True, "device_id": "M301"}
        config = MODULE.assignment_config("m301", discover_res)
        self.assertEqual({"version": 3, "device_id": "m301", "enabled": True, "open_package": None}, config)
        for forbidden in self.contract["forbidden_fields"]:
            self.assertNotIn(forbidden, config)

        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "aot_group_config.json"
            self.assertTrue(MODULE.write_config_atomic(path, config))
            self.assertFalse(MODULE.write_config_atomic(path, config))
            self.assertEqual(config, json.loads(path.read_text(encoding="utf-8")))

    def test_wrong_server_identity_fails_closed(self):
        with self.assertRaises(MODULE.RegistrationError) as ctx:
            MODULE.assignment_config("m301", {"ok": True, "device_id": "m302"})
        self.assertEqual(str(ctx.exception), "registration_assignment_invalid")

    def test_missing_server_device_id_fails_closed(self):
        with self.assertRaises(MODULE.RegistrationError) as ctx:
            MODULE.assignment_config("m301", {"ok": True, "session_id": "s1", "role": "follower"})
        self.assertEqual(str(ctx.exception), "registration_assignment_invalid")

    def test_configure_flow_success_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            agent_cfg = pathlib.Path(folder) / "agent_config.json"
            agent_cfg.write_text(json.dumps({
                "worker_report_url": "https://hub.internal/report",
                "agent_report_secret": "test-secret"
            }), encoding="utf-8")
            aot_cfg = pathlib.Path(folder) / "aot_group_config.json"

            args = argparse.Namespace(
                origin="https://hub.internal",
                agent_config=str(agent_cfg),
                aot_config=str(aot_cfg),
                device_id="m118",
                previous_device_id=None,
            )

            # 1. Success matching contract
            with mock.patch.object(MODULE, "post_json", return_value={"ok": True, "device_id": "m118"}) as mock_post:
                res = MODULE.configure(args)
                self.assertEqual(res, 0)
                mock_post.assert_called_once_with(
                    "https://hub.internal", "test-secret", "discover",
                    {"device_id": "m118", "previous_device_id": None}
                )
                self.assertTrue(aot_cfg.is_file())
                saved = json.loads(aot_cfg.read_text(encoding="utf-8"))
                self.assertEqual(saved["device_id"], "m118")

            # 2. Server returns mismatch
            with mock.patch.object(MODULE, "post_json", return_value={"ok": True, "device_id": "m999"}):
                with self.assertRaises(MODULE.RegistrationError):
                    MODULE.configure(args)

    def test_verify_flow_online_passes_and_offline_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            agent_cfg = pathlib.Path(folder) / "agent_config.json"
            agent_cfg.write_text(json.dumps({
                "worker_report_url": "https://hub.internal/report",
                "agent_report_secret": "test-secret"
            }), encoding="utf-8")
            aot_cfg = pathlib.Path(folder) / "aot_group_config.json"
            aot_cfg.write_text(json.dumps({"device_id": "m118"}), encoding="utf-8")

            args = argparse.Namespace(
                origin="https://hub.internal",
                agent_config=str(agent_cfg),
                aot_config=str(aot_cfg),
                device_id="m118",
                timeout="2",
            )

            # 1. Online verify matches contract
            with mock.patch.object(MODULE, "post_json", return_value={
                "ok": True, "device_id": "m118", "online": True, "visible_in_hub": True
            }) as mock_post:
                res = MODULE.verify(args)
                self.assertEqual(res, 0)
                mock_post.assert_called_once_with(
                    "https://hub.internal", "test-secret", "verify",
                    {"device_id": "m118"}
                )

            # 2. Offline verify fails closed
            with mock.patch.object(MODULE, "post_json", side_effect=MODULE.RegistrationError("device_not_online_in_aot_hub")):
                with self.assertRaises(MODULE.RegistrationError) as ctx:
                    MODULE.verify(args)
                self.assertIn("device_not_online_in_aot_hub", str(ctx.exception))

    def test_reset_identity_flow(self):
        with tempfile.TemporaryDirectory() as folder:
            agent_cfg = pathlib.Path(folder) / "agent_config.json"
            agent_cfg.write_text(json.dumps({
                "worker_report_url": "https://hub.internal/report",
                "agent_report_secret": "test-secret"
            }), encoding="utf-8")
            aot_cfg = pathlib.Path(folder) / "aot_group_config.json"
            aot_cfg.write_text(json.dumps({"device_id": "m118"}), encoding="utf-8")
            state_dir = pathlib.Path(folder) / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            for fname in MODULE.LOCAL_IDENTITY_STATE:
                (state_dir / fname).write_text("dummy", encoding="utf-8")

            runtime_dir = pathlib.Path(folder) / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)

            args = argparse.Namespace(
                origin="https://hub.internal",
                agent_config=str(agent_cfg),
                aot_config=str(aot_cfg),
                old_device_id="m118",
                new_device_id="m120",
                state_root=str(state_dir),
                runtime_root=str(runtime_dir),
            )

            with mock.patch.object(MODULE, "post_json", return_value={
                "ok": True, "old_device_id": "m118", "new_device_id": "m120"
            }) as mock_post:
                res = MODULE.reset_identity(args)
                self.assertEqual(res, 0)
                mock_post.assert_called_once_with(
                    "https://hub.internal", "test-secret", "reset",
                    {"old_device_id": "m118", "new_device_id": "m120"}
                )
                # Ensure local identity state files unlinked
                for fname in MODULE.LOCAL_IDENTITY_STATE:
                    self.assertFalse((state_dir / fname).exists())


if __name__ == "__main__":
    unittest.main()
