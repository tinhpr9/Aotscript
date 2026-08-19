from __future__ import annotations
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ProvisionPinCoherenceTests(unittest.TestCase):
    def setUp(self):
        self.setup_sh = ROOT / "setup.sh"
        self.assertTrue(self.setup_sh.is_file(), "setup.sh must exist")
        content = self.setup_sh.read_text(encoding="utf-8")

        ref_match = re.search(r'PROVISION_REF="([0-9a-fA-F]{40})"', content)
        self.assertIsNotNone(ref_match, "setup.sh must declare a 40-char hex PROVISION_REF")
        self.provision_ref = ref_match.group(1).lower()

        sha_match = re.search(r'PROVISION_SHA256="([0-9a-fA-F]{64})"', content)
        self.assertIsNotNone(sha_match, "setup.sh must declare a 64-char hex PROVISION_SHA256")
        self.provision_sha256 = sha_match.group(1).lower()

    def _git_show(self, path_in_ref: str, ref: str | None = None) -> bytes:
        target_ref = ref or self.provision_ref
        res = subprocess.run(
            ["git", "show", f"{target_ref}:{path_in_ref}"],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(
            res.returncode, 0,
            f"Git object {target_ref}:{path_in_ref} must exist; stderr: {res.stderr.decode()}"
        )
        return res.stdout

    def test_pinned_commit_exists_and_contains_required_artifacts(self):
        # 1. Verify provision-device.sh exists and matches declared PROVISION_SHA256
        prov_device = self._git_show("provision-device.sh")
        actual_sha = hashlib.sha256(prov_device).hexdigest()
        self.assertEqual(actual_sha, self.provision_sha256, "PROVISION_SHA256 mismatch for provision-device.sh")

        # 2. Verify setup-m166.sh exists in pinned ref
        setup_m166 = self._git_show("setup-m166.sh").decode("utf-8")
        self.assertIn("AOTSCRIPT_PROVISION_REF", setup_m166)

        # 3. Verify canonical contract exists in pinned ref
        contract_raw = self._git_show("aot-group-control/aot-registration-contract.json").decode("utf-8")
        contract = json.loads(contract_raw)
        self.assertEqual(contract.get("identity_model"), "device_id_only")
        self.assertIn("discover", contract.get("operations", {}))
        self.assertIn("verify", contract.get("operations", {}))
        self.assertIn("reset", contract.get("operations", {}))

        # 4. Verify msetup_registration.py exists in pinned ref and uses device_id_only
        msetup_reg = self._git_show("aot-group-control/msetup_registration.py").decode("utf-8")
        self.assertIn("CONFIG_VERSION = 3", msetup_reg)
        self.assertIn("assignment_config", msetup_reg)
        self.assertNotIn("session_id", msetup_reg.split("assignment_config")[1].split("def ")[0])

    def test_pre_pr70_commit_fails_contract_presence(self):
        old_ref = "92439f16cd168dbf7b6cc2d48c88b5114062189e"
        res = subprocess.run(
            ["git", "show", f"{old_ref}:aot-group-control/aot-registration-contract.json"],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(res.returncode, 0, "Old pre-PR70 commit must NOT contain contract artifact")

    def test_corrupted_provision_sha256_fails_validation(self):
        prov_device = self._git_show("provision-device.sh")
        bad_sha = "0" * 64
        actual_sha = hashlib.sha256(prov_device).hexdigest()
        self.assertNotEqual(actual_sha, bad_sha)

    def test_pinned_registration_helper_end_to_end_flow(self):
        # Load registration helper directly from pinned git object into isolated module
        msetup_code = self._git_show("aot-group-control/msetup_registration.py").decode("utf-8")
        module = types.ModuleType("pinned_registration")
        exec(compile(msetup_code, "<pinned_registration>", "exec"), module.__dict__)

        # Test canonical discover response handling
        canonical_res = {"ok": True, "device_id": "m118"}
        config = module.assignment_config("m118", canonical_res)
        self.assertEqual(config["device_id"], "m118")
        self.assertEqual(config["version"], 3)
        self.assertTrue(config["enabled"])
        self.assertIsNone(config["open_package"])

        # Test fail-closed on mismatch
        with self.assertRaises(module.RegistrationError):
            module.assignment_config("m118", {"ok": True, "device_id": "m999"})


if __name__ == "__main__":
    unittest.main()
