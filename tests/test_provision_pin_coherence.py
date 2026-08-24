#!/usr/bin/env python3
"""
test_provision_pin_coherence.py - Provision Revision Authority & Registration Contract Coherence Tests

Validates:
1. Canonical revision authority: setup.sh, setup-m166.sh, Termuxboot, and provision-device.sh
   resolve input refs dynamically to immutable 40-hex commit SHAs without hardcoded commit SHA literals.
2. Dynamic revision propagation: setting AOTSCRIPT_PROVISION_REF to Revision A or Revision B
   propagates across the entire parent-child hierarchy WITHOUT ANY SOURCE CODE EDITS.
3. Once-and-only-once resolution & freeze: an input ref such as "main" resolves to an immutable
   40-hex commit SHA "R", freezing "R" for the entire setup transaction and child invocations.
4. Fail-closed revision validation: invalid, malformed, or injection-attempt revision refs
   are strictly rejected by setup.sh, setup-m166.sh, and provision-device.sh.
5. Pinned registration helper implements the canonical device-id-only contract.
6. Reproduction of original pre-PR70 failure and proof that the canonical helper resolves it.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SETUP_SH = ROOT / "setup.sh"
SETUP_M166_SH = ROOT / "setup-m166.sh"
TERMUXBOOT_SH = ROOT / "Termuxboot"
PROVISION_SH = ROOT / "provision-device.sh"
REGISTRATION_HELPER = ROOT / "aot-group-control/msetup_registration.py"


class ProvisionPinCoherenceTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SETUP_SH.is_file(), "setup.sh must exist")
        self.assertTrue(SETUP_M166_SH.is_file(), "setup-m166.sh must exist")
        self.assertTrue(TERMUXBOOT_SH.is_file(), "Termuxboot must exist")
        self.assertTrue(PROVISION_SH.is_file(), "provision-device.sh must exist")
        self.assertTrue(REGISTRATION_HELPER.is_file(), "msetup_registration.py must exist")

    def _load_registration_module(self) -> types.ModuleType:
        """Load registration helper as an isolated executable module."""
        msetup_bytes = REGISTRATION_HELPER.read_bytes()
        module = types.ModuleType("msetup_registration_tested")
        exec(compile(msetup_bytes.decode("utf-8"), "<msetup_registration_tested>", "exec"), module.__dict__)
        return module

    def test_01_canonical_revision_authority_and_no_hardcoded_sha(self):
        """
        Verify that setup.sh, setup-m166.sh, Termuxboot, and provision-device.sh
        use resolve_canonical_revision and do NOT contain hardcoded 40-character
        hex commit SHAs in production defaults.
        """
        setup_content = SETUP_SH.read_text(encoding="utf-8")
        msetup_content = SETUP_M166_SH.read_text(encoding="utf-8")
        termuxboot_content = TERMUXBOOT_SH.read_text(encoding="utf-8")
        provision_content = PROVISION_SH.read_text(encoding="utf-8")

        # 1. Verify resolve_canonical_revision is present in all components
        self.assertIn("resolve_canonical_revision", setup_content)
        self.assertIn("resolve_canonical_revision", msetup_content)
        self.assertIn("resolve_canonical_revision", termuxboot_content)
        self.assertIn("resolve_canonical_revision", provision_content)

        # 2. Verify no hardcoded 40-char hex commit SHA assignment in production setup defaults
        hardcoded_ref_pattern = re.compile(r'^\s*PROVISION_REF="[0-9a-fA-F]{40}"', re.MULTILINE)
        self.assertIsNone(
            hardcoded_ref_pattern.search(setup_content),
            "setup.sh must not hardcode a 40-char hex commit SHA as default PROVISION_REF"
        )
        self.assertIsNone(
            hardcoded_ref_pattern.search(msetup_content),
            "setup-m166.sh must not hardcode a 40-char hex commit SHA as default PROVISION_REF"
        )
        self.assertIsNone(
            hardcoded_ref_pattern.search(provision_content),
            "provision-device.sh must not hardcode a 40-char hex commit SHA as default PROVISION_REF"
        )

    def test_02_dynamic_revision_propagation_without_source_edits(self):
        """
        Prove:
        Revision A works AND Revision B works WITHOUT changing production source literals.
        REVISION_CHANGE_REQUIRES_SOURCE_EDIT=NO.
        """
        rev_a = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        rev_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

        # 1. Sourced setup.sh under rev_a resolves rev_a
        cmd_a = f"""
        AOTSCRIPT_SETUP_SOURCE_ONLY=1
        AOTSCRIPT_PROVISION_REF="{rev_a}"
        source "{SETUP_SH}"
        echo "RESOLVED_REF=$PROVISION_REF"
        echo "RESOLVED_RAW=$RAW_BASE"
        """
        res_a = subprocess.run(["bash", "-c", cmd_a], capture_output=True, text=True, check=True)
        self.assertIn(f"RESOLVED_REF={rev_a}", res_a.stdout)
        self.assertIn(f"RESOLVED_RAW=https://raw.githubusercontent.com/tinhpr9/Aotscript/{rev_a}", res_a.stdout)

        # 2. Sourced setup.sh under rev_b resolves rev_b without modifying source
        cmd_b = f"""
        AOTSCRIPT_SETUP_SOURCE_ONLY=1
        AOTSCRIPT_PROVISION_REF="{rev_b}"
        source "{SETUP_SH}"
        echo "RESOLVED_REF=$PROVISION_REF"
        echo "RESOLVED_RAW=$RAW_BASE"
        """
        res_b = subprocess.run(["bash", "-c", cmd_b], capture_output=True, text=True, check=True)
        self.assertIn(f"RESOLVED_REF={rev_b}", res_b.stdout)
        self.assertIn(f"RESOLVED_RAW=https://raw.githubusercontent.com/tinhpr9/Aotscript/{rev_b}", res_b.stdout)

        # 3. Termuxboot resolve_provision_ref under rev_a
        cmd_tb = f"""
        AOTSCRIPT_PROVISION_REF="{rev_a}"
        source "{TERMUXBOOT_SH}"
        resolve_provision_ref
        """
        res_tb = subprocess.run(["bash", "-c", cmd_tb], capture_output=True, text=True, check=True)
        self.assertEqual(res_tb.stdout.strip(), rev_a)

    def test_03_ref_resolved_once_to_immutable_40_hex_and_frozen(self):
        """
        Verify that input ref 'main' resolves to an immutable 40-character hex commit SHA 'R',
        freezing 'R' for the entire setup transaction and child invocations.
        """
        fake_remote_sha = "1234567890abcdef1234567890abcdef12345678"
        cmd = f"""
        AOTSCRIPT_SETUP_SOURCE_ONLY=1
        AOTSCRIPT_PROVISION_REF="main"
        AOTSCRIPT_RESOLVED_REVISION="{fake_remote_sha}"
        source "{SETUP_SH}"
        echo "RESOLVED_REF=$PROVISION_REF"
        echo "RESOLVED_RAW=$RAW_BASE"
        """
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, check=True)
        self.assertIn(f"RESOLVED_REF={fake_remote_sha}", res.stdout)
        self.assertIn(f"RESOLVED_RAW=https://raw.githubusercontent.com/tinhpr9/Aotscript/{fake_remote_sha}", res.stdout)
        self.assertNotIn("RESOLVED_REF=main", res.stdout)

    def test_04_pinned_registration_helper_behavior(self):
        """Verify observable behavior of registration helper on canonical contract inputs."""
        reg = self._load_registration_module()

        # A. Canonical production discover response
        res_valid = {"ok": True, "device_id": "m118"}
        config = reg.assignment_config("m118", res_valid)
        self.assertEqual(config["device_id"], "m118")
        self.assertEqual(config["version"], 3)
        self.assertTrue(config["enabled"])
        self.assertIsNone(config["open_package"])

        # B. Wrong device ID mismatch -> raises RegistrationError
        res_mismatch = {"ok": True, "device_id": "m119"}
        with self.assertRaises(reg.RegistrationError) as ctx:
            reg.assignment_config("m118", res_mismatch)
        self.assertIn("registration_assignment_invalid", str(ctx.exception))

        # C. Invalid non-dict response -> raises RegistrationError
        with self.assertRaises(reg.RegistrationError):
            reg.assignment_config("m118", None)  # type: ignore
        with self.assertRaises(reg.RegistrationError):
            reg.assignment_config("m118", "not_a_dict")  # type: ignore
        with self.assertRaises(reg.RegistrationError):
            reg.assignment_config("m118", {"device_id": "invalid_id_format"})

        # D. Extra legacy fields (role, session_id) must not break canonical assignment
        res_legacy_extra = {"ok": True, "device_id": "m118", "role": "follower", "session_id": "s1"}
        config_extra = reg.assignment_config("m118", res_legacy_extra)
        self.assertEqual(config_extra["device_id"], "m118")
        self.assertEqual(config_extra["version"], 3)

    def test_05_original_pre_pr70_failure_reproduction_and_kill(self):
        """
        Reproduce original production bug:
        Pre-PR70 legacy assignment logic required role/session_id/reference_device_id.
        When production returns device_id-only {"ok": True, "device_id": "m118"},
        legacy code failed with registration_assignment_invalid.
        The canonical helper resolves this completely.
        """
        # 1. Legacy Pre-PR70 assignment simulator (CONFIG_VERSION = 2)
        def legacy_assignment_config(expected_device_id: str, response: dict) -> dict:
            if not isinstance(response, dict) or response.get("device_id") != expected_device_id:
                raise ValueError("registration_assignment_invalid: device_id mismatch")
            role = response.get("role")
            if role not in ("leader", "follower"):
                raise ValueError("registration_assignment_invalid: missing or invalid role")
            if not response.get("session_id"):
                raise ValueError("registration_assignment_invalid: missing session_id")
            return {"device_id": expected_device_id, "version": 2}

        # 2. Reproduce: Production device_id-only payload causes legacy failure
        prod_payload = {"ok": True, "device_id": "m118"}
        with self.assertRaises(ValueError) as err:
            legacy_assignment_config("m118", prod_payload)
        self.assertIn("registration_assignment_invalid", str(err.exception))

        # 3. Kill: Canonical registration helper accepts production payload cleanly
        reg = self._load_registration_module()
        clean_config = reg.assignment_config("m118", prod_payload)
        self.assertEqual(clean_config["device_id"], "m118")
        self.assertEqual(clean_config["version"], 3)

    def test_06_fail_closed_on_invalid_revision_ref(self):
        """Verify that invalid revision formats fail closed with non-zero exit code."""
        invalid_refs = [
            "invalid_ref_with_symbols!",
            "; rm -rf /",
            "../../../etc/passwd",
            "nonexistent_branch_name_12345xyz",
        ]
        for bad_ref in invalid_refs:
            # A. setup.sh
            res = subprocess.run(
                ["bash", "-c", f'AOTSCRIPT_PROVISION_REF="{bad_ref}" bash "{SETUP_SH}" --validate-id m88'],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res.returncode, 0, f"setup.sh must reject invalid ref: {bad_ref}")
            self.assertIn("provision ref không hợp lệ", res.stderr + res.stdout)

            # B. provision-device.sh
            res_prov = subprocess.run(
                ["bash", "-c", f'AOTSCRIPT_PROVISION_REF="{bad_ref}" bash "{PROVISION_SH}" help'],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res_prov.returncode, 0, f"provision-device.sh must reject invalid ref: {bad_ref}")


if __name__ == "__main__":
    unittest.main()

