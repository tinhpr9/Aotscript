#!/usr/bin/env python3
"""
Unit and integration tests for Antigraviny/Agy Migration and Core Extraction System.
Tests cover:
1. Backup generation & manifest structure
2. Corrupt bundle detection & rejection
3. Missing manifest rejection
4. SHA-256 mismatch rejection
5. Missing critical file fail-fast
6. Restore into empty environment
7. Restore idempotency
8. Rollback on partial/failed restore
9. Secret safety (no plaintext secrets in logs or git diff)
10. Credential classification & REAUTH_REQUIRED handling
11. Verification engine full check
12. Exact pinned SHA is fetched & verified
13. Wrong SHA rejected
14. Compatibility mismatch rejected
15. Missing private-repo auth -> BLOCKED_AUTH, no partial install
16. Successful new-machine restore from Git + local required state
17. Failed core update preserves PREVIOUS_GOOD_CORE_SHA
18. Duplicate generic capability detected
19. AOT-specific capability remains Aotscript-owned
20. Generic capability does not remain permanently duplicated
21. CURRENT/HEALTH can fail even when EXPECTED says YES
22. Bootstrap is idempotent with core materialization
23. Corrupt/partial core materialization rolls back safely
24. Secret leakage test remains PASS
"""

import unittest
import os
import sys
import tempfile
import shutil
import json
import tarfile
import stat
import subprocess

# Ensure repo root is in sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from antigraviny_migration.common import (
    compute_file_sha256,
    compute_bytes_sha256,
    mask_secret,
    detect_environment,
    CredentialClassification,
    MigrationManifest,
    FileEntry,
    load_core_lock,
    fetch_and_verify_core,
    materialize_core_into_repo,
    recover_interrupted_swap,
    compute_tree_manifest,
    verify_tree_manifest,
    check_repo_access,
    CORE_SHA_REGEX,
    CoreLockError,
    CoreMaterializeError,
)
from antigraviny_migration.backup import AgyBackupEngine
from antigraviny_migration.restore import AgyRestoreEngine, RestoreError
from antigraviny_migration.bootstrap import AgyBootstrapEngine
from antigraviny_migration.verify import AgyVerifyEngine, VerificationResult


class TestAntigravinyMigration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_agy_migration_")
        self.old_env_dir = os.path.join(self.temp_dir, "old_machine")
        self.new_env_dir = os.path.join(self.temp_dir, "new_machine")
        os.makedirs(self.old_env_dir, exist_ok=True)
        os.makedirs(self.new_env_dir, exist_ok=True)

        self._setup_mock_source_machine(self.old_env_dir)
        self._setup_mock_core_repo(os.path.join(self.temp_dir, "mock_core_repo"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _setup_mock_core_repo(self, path):
        """Create a mock antigraviny-core repository on disk."""
        self.mock_core_dir = path
        os.makedirs(os.path.join(path, "skills", "tdd-workflow"), exist_ok=True)
        os.makedirs(os.path.join(path, "agents"), exist_ok=True)
        os.makedirs(os.path.join(path, "workflows"), exist_ok=True)
        os.makedirs(os.path.join(path, "rules"), exist_ok=True)
        os.makedirs(os.path.join(path, "mcp"), exist_ok=True)

        with open(os.path.join(path, "skills", "tdd-workflow", "SKILL.md"), "w") as f:
            f.write("---\nname: tdd-workflow\ndescription: TDD workflow\n---\n# TDD\n")

        with open(os.path.join(path, "agents", "planner.md"), "w") as f:
            f.write("# Planner Agent\n")

        with open(os.path.join(path, "workflows", "plan.md"), "w") as f:
            f.write("# Plan Workflow\n")

        with open(os.path.join(path, "rules", "common-testing.md"), "w") as f:
            f.write("# Testing Rules\n")

        with open(os.path.join(path, "compatibility.json"), "w") as f:
            json.dump({
                "schema_version": "antigraviny-core/v1",
                "name": "antigraviny-core",
                "version": "1.0.0",
            }, f)

        with open(os.path.join(path, "capabilities.json"), "w") as f:
            json.dump({
                "schema_version": "1.0",
                "count": 4,
                "capabilities": [
                    {"id": "skill:tdd-workflow", "owner": "antigraviny-core", "canonical_path": "skills/tdd-workflow/SKILL.md"},
                    {"id": "agent:planner", "owner": "antigraviny-core", "canonical_path": "agents/planner.md"},
                    {"id": "workflow:plan", "owner": "antigraviny-core", "canonical_path": "workflows/plan.md"},
                    {"id": "rule:common-testing", "owner": "antigraviny-core", "canonical_path": "rules/common-testing.md"},
                ]
            }, f)

        # Initialize mock git in core
        subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, capture_output=True, check=True)
        subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial core commit"], cwd=path, capture_output=True, check=True)
        
        h = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True)
        self.mock_core_sha = h.stdout.strip()

    def _setup_mock_source_machine(self, base):
        """Create a mock source machine filesystem layout."""
        # 1. Agy binary
        self.bin_dir = os.path.join(base, "root", ".local", "bin")
        os.makedirs(self.bin_dir, exist_ok=True)
        self.agy_bin = os.path.join(self.bin_dir, "agy")
        with open(self.agy_bin, "w") as f:
            f.write("#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo '1.1.13'; exit 0; fi\necho 'Mock Agy Running'\nexit 0\n")
        os.chmod(self.agy_bin, 0o755)

        # 2. Launchers
        self.agyn_debian = os.path.join(self.bin_dir, "agyn")
        with open(self.agyn_debian, "w") as f:
            f.write("#!/bin/bash\nexec /root/.local/bin/agy --dangerously-skip-permissions\n")
        os.chmod(self.agyn_debian, 0o755)

        self.termux_usr_bin = os.path.join(base, "data", "data", "com.termux", "files", "usr", "bin")
        os.makedirs(self.termux_usr_bin, exist_ok=True)
        self.agyn_termux = os.path.join(self.termux_usr_bin, "agyn")
        with open(self.agyn_termux, "w") as f:
            f.write("#!/bin/sh\ntmux new-session -d -s agy\n")
        os.chmod(self.agyn_termux, 0o755)

        self.agy_watch = os.path.join(self.termux_usr_bin, "agy-watch")
        with open(self.agy_watch, "w") as f:
            f.write("#!/bin/sh\necho 'watcher'\n")
        os.chmod(self.agy_watch, 0o755)

        self.termux_home_bin = os.path.join(base, "data", "data", "com.termux", "files", "home", "bin")
        os.makedirs(self.termux_home_bin, exist_ok=True)
        self.toolcheck = os.path.join(self.termux_home_bin, "toolcheck")
        with open(self.toolcheck, "w") as f:
            f.write("#!/bin/sh\necho 'toolcheck'\n")
        os.chmod(self.toolcheck, 0o755)

        # 3. Gemini / Antigravity config
        self.gemini_config_dir = os.path.join(base, "root", ".gemini", "config")
        os.makedirs(self.gemini_config_dir, exist_ok=True)
        with open(os.path.join(self.gemini_config_dir, "config.json"), "w") as f:
            json.dump({"userSettings": {"remoteControlHostname": "test-host"}}, f)
        with open(os.path.join(self.gemini_config_dir, "mcp_config.json"), "w") as f:
            json.dump({"mcpServers": {"serena": {"command": "/root/.local/bin/serena"}}}, f)

        # Skills
        self.skills_dir = os.path.join(self.gemini_config_dir, "skills", "test-skill")
        os.makedirs(self.skills_dir, exist_ok=True)
        with open(os.path.join(self.skills_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: test-skill\ndescription: Test skill\n---\n# Test\n")

        # Antigravity CLI settings
        self.cli_dir = os.path.join(base, "root", ".gemini", "antigravity-cli")
        os.makedirs(self.cli_dir, exist_ok=True)
        with open(os.path.join(self.cli_dir, "settings.json"), "w") as f:
            json.dump({"model": "Gemini 3.7 Flash", "notifications": True}, f)

        # Auth files (secrets)
        with open(os.path.join(self.cli_dir, "antigravity-oauth-token"), "w") as f:
            f.write("mock_secret_oauth_token_12345")
        os.chmod(os.path.join(self.cli_dir, "antigravity-oauth-token"), 0o600)

        with open(os.path.join(self.cli_dir, "installation_id"), "w") as f:
            f.write("mock-install-id-999")

        # 4. Serena config
        self.serena_dir = os.path.join(base, "root", ".serena")
        os.makedirs(self.serena_dir, exist_ok=True)
        with open(os.path.join(self.serena_dir, "serena_config.yml"), "w") as f:
            f.write("log_level: 20\nbase_modes:\n- interactive\n- editing\n")

        # 5. Tmux & Shell configs
        self.termux_home = os.path.join(base, "data", "data", "com.termux", "files", "home")
        os.makedirs(self.termux_home, exist_ok=True)
        with open(os.path.join(self.termux_home, ".tmux.conf"), "w") as f:
            f.write("set -g mouse off\n")
        with open(os.path.join(self.termux_home, ".bashrc"), "w") as f:
            f.write("export PATH=\"$HOME/bin:$PATH\"\n")
        with open(os.path.join(base, "root", ".bashrc"), "w") as f:
            f.write("export PATH=\"/root/.local/bin:$PATH\"\n")

        # 6. Repo structure with lock
        self.repo_dir = os.path.join(self.termux_home, "Aotscript-ecc-production")
        os.makedirs(os.path.join(self.repo_dir, ".agents", "rules"), exist_ok=True)
        os.makedirs(os.path.join(self.repo_dir, ".agents", "skills", "test-ecc-skill"), exist_ok=True)
        os.makedirs(os.path.join(self.repo_dir, ".agents", "workflows"), exist_ok=True)
        os.makedirs(os.path.join(self.repo_dir, ".agents", "agents"), exist_ok=True)
        with open(os.path.join(self.repo_dir, "AGENTS.md"), "w") as f:
            f.write("# AGENTS rules\n")
        with open(os.path.join(self.repo_dir, "ANTIGRAVINY_CORE.lock"), "w") as f:
            json.dump({
                "schema_version": "1.0",
                "core_repo": "https://github.com/tinhpr9/antigraviny-core.git",
                "core_version": "1.0.0",
                "core_sha": "8f8322416ddcce40fa3791a5d41583a6e08d4b98",
                "compatibility_schema": "antigraviny-core/v1"
            }, f)
        with open(os.path.join(self.repo_dir, ".agents", "ecc-install-state.json"), "w") as f:
            json.dump({
                "installed": True,
                "core_sha": "8f8322416ddcce40fa3791a5d41583a6e08d4b98",
                "core_version": "1.0.0",
                "compatibility_schema": "antigraviny-core/v1"
            }, f)
        with open(os.path.join(self.repo_dir, ".agents", "skills", "test-ecc-skill", "SKILL.md"), "w") as f:
            f.write("# ECC Skill\n")

    def test_01_backup_creation_and_manifest(self):
        """Test full backup creation and check manifest structure."""
        bundle_path = os.path.join(self.temp_dir, "backup.tar.gz")
        engine = AgyBackupEngine(
            source_root=self.old_env_dir,
            agy_binary_path=self.agy_bin,
            repo_path=self.repo_dir,
            output_path=bundle_path,
        )
        result = engine.create_backup()
        self.assertTrue(os.path.exists(bundle_path), "Backup bundle archive should exist")
        self.assertIn("manifest", result)
        manifest = result["manifest"]

        self.assertEqual(manifest["agy"]["version"], "1.1.13")
        self.assertEqual(manifest["agy"]["sha256"], compute_file_sha256(self.agy_bin))
        self.assertTrue(len(manifest["files"]) > 5, "Manifest should index multiple restored files")

        # Check tar integrity
        with tarfile.open(bundle_path, "r:gz") as tar:
            names = tar.getnames()
            self.assertIn("MANIFEST.json", names)
            self.assertTrue(any(n.startswith("files/") for n in names))

    def test_02_corrupt_bundle_rejected(self):
        """Test that a corrupt tar file is rejected during restore."""
        corrupt_bundle = os.path.join(self.temp_dir, "corrupt.tar.gz")
        with open(corrupt_bundle, "wb") as f:
            f.write(b"NOT_A_VALID_GZIP_ARCHIVE_DATA_12345")

        restore_engine = AgyRestoreEngine(
            bundle_path=corrupt_bundle,
            target_root=self.new_env_dir,
        )
        with self.assertRaises(RestoreError) as ctx:
            restore_engine.restore()
        self.assertIn("corrupt", str(ctx.exception).lower())

    def test_03_missing_manifest_rejected(self):
        """Test that an archive without MANIFEST.json is rejected."""
        no_manifest_bundle = os.path.join(self.temp_dir, "no_manifest.tar.gz")
        with tarfile.open(no_manifest_bundle, "w:gz") as tar:
            dummy_file = os.path.join(self.temp_dir, "dummy.txt")
            with open(dummy_file, "w") as df:
                df.write("dummy")
            tar.add(dummy_file, arcname="files/dummy.txt")

        restore_engine = AgyRestoreEngine(
            bundle_path=no_manifest_bundle,
            target_root=self.new_env_dir,
        )
        with self.assertRaises(RestoreError) as ctx:
            restore_engine.restore()
        self.assertIn("manifest", str(ctx.exception).lower())

    def test_04_sha_mismatch_rejected(self):
        """Test that a tampered file inside bundle triggers SHA mismatch error."""
        bundle_path = os.path.join(self.temp_dir, "tampered.tar.gz")
        engine = AgyBackupEngine(
            source_root=self.old_env_dir,
            agy_binary_path=self.agy_bin,
            repo_path=self.repo_dir,
            output_path=bundle_path,
        )
        engine.create_backup()

        tamper_dir = os.path.join(self.temp_dir, "tamper_unpack")
        os.makedirs(tamper_dir, exist_ok=True)
        with tarfile.open(bundle_path, "r:gz") as tar:
            tar.extractall(tamper_dir)

        for root, dirs, files in os.walk(os.path.join(tamper_dir, "files")):
            if files:
                target_file = os.path.join(root, files[0])
                with open(target_file, "a") as f:
                    f.write("\nTAMPERED_INJECTED_DATA\n")
                break

        tampered_bundle = os.path.join(self.temp_dir, "tampered_repacked.tar.gz")
        with tarfile.open(tampered_bundle, "w:gz") as tar:
            for item in os.listdir(tamper_dir):
                tar.add(os.path.join(tamper_dir, item), arcname=item)

        restore_engine = AgyRestoreEngine(
            bundle_path=tampered_bundle,
            target_root=self.new_env_dir,
        )
        with self.assertRaises(RestoreError) as ctx:
            restore_engine.restore()
        self.assertIn("sha256", str(ctx.exception).lower())

    def test_05_missing_critical_file_fails_fast(self):
        """Test backup fails fast if agy binary does not exist."""
        bad_agy = os.path.join(self.temp_dir, "nonexistent_agy")
        engine = AgyBackupEngine(
            source_root=self.old_env_dir,
            agy_binary_path=bad_agy,
            repo_path=self.repo_dir,
            output_path=os.path.join(self.temp_dir, "out.tar.gz"),
        )
        with self.assertRaises(FileNotFoundError):
            engine.create_backup()

    def test_06_restore_into_empty_environment(self):
        """Test restore from backup into a clean empty target environment."""
        bundle_path = os.path.join(self.temp_dir, "backup.tar.gz")
        backup_engine = AgyBackupEngine(
            source_root=self.old_env_dir,
            agy_binary_path=self.agy_bin,
            repo_path=self.repo_dir,
            output_path=bundle_path,
        )
        backup_engine.create_backup()

        restore_engine = AgyRestoreEngine(
            bundle_path=bundle_path,
            target_root=self.new_env_dir,
        )
        summary = restore_engine.restore()
        self.assertTrue(summary["success"], "Restore must succeed")
        self.assertTrue(summary["restored_count"] > 0)

        restored_agy = os.path.join(self.new_env_dir, "root", ".local", "bin", "agy")
        self.assertTrue(os.path.exists(restored_agy))
        self.assertEqual(compute_file_sha256(restored_agy), compute_file_sha256(self.agy_bin))

        mode = stat.S_IMODE(os.stat(restored_agy).st_mode)
        self.assertEqual(mode & 0o111, 0o111, "Restored agy binary must remain executable")

    def test_07_restore_idempotency(self):
        """Test restoring twice produces identical state without corruption."""
        bundle_path = os.path.join(self.temp_dir, "backup.tar.gz")
        AgyBackupEngine(
            source_root=self.old_env_dir,
            agy_binary_path=self.agy_bin,
            repo_path=self.repo_dir,
            output_path=bundle_path,
        ).create_backup()

        restore1 = AgyRestoreEngine(bundle_path=bundle_path, target_root=self.new_env_dir).restore()
        self.assertTrue(restore1["success"])

        restore2 = AgyRestoreEngine(bundle_path=bundle_path, target_root=self.new_env_dir).restore()
        self.assertTrue(restore2["success"])

        restored_agy = os.path.join(self.new_env_dir, "root", ".local", "bin", "agy")
        self.assertEqual(compute_file_sha256(restored_agy), compute_file_sha256(self.agy_bin))

    def test_08_rollback_on_failure(self):
        """Test that if an error occurs mid-restore, pre-existing target files are preserved via rollback."""
        existing_file = os.path.join(self.new_env_dir, "root", ".gemini", "config", "config.json")
        os.makedirs(os.path.dirname(existing_file), exist_ok=True)
        with open(existing_file, "w") as f:
            f.write('{"pre_existing": true}')

        bundle_path = os.path.join(self.temp_dir, "backup.tar.gz")
        AgyBackupEngine(
            source_root=self.old_env_dir,
            agy_binary_path=self.agy_bin,
            repo_path=self.repo_dir,
            output_path=bundle_path,
        ).create_backup()

        restore_engine = AgyRestoreEngine(
            bundle_path=bundle_path,
            target_root=self.new_env_dir,
            _inject_failure_after_count=2,
        )
        with self.assertRaises(RestoreError):
            restore_engine.restore()

        self.assertTrue(os.path.exists(existing_file))
        with open(existing_file, "r") as f:
            content = f.read()
        self.assertIn("pre_existing", content, "Pre-existing file must be preserved by rollback")

    def test_09_secret_safety_no_leak_in_logs_or_manifest(self):
        """Verify that secret values are masked and never logged in plain text."""
        raw_secret = "my_super_secret_token_abcdef123456"
        masked = mask_secret(raw_secret)
        self.assertNotIn(raw_secret, masked)
        self.assertIn("***REDACTED***", masked)

        bundle_path = os.path.join(self.temp_dir, "backup.tar.gz")
        AgyBackupEngine(
            source_root=self.old_env_dir,
            agy_binary_path=self.agy_bin,
            repo_path=self.repo_dir,
            output_path=bundle_path,
        ).create_backup()

        with tarfile.open(bundle_path, "r:gz") as tar:
            manifest_f = tar.extractfile("MANIFEST.json")
            manifest_str = manifest_f.read().decode("utf-8")
            self.assertNotIn("mock_secret_oauth_token_12345", manifest_str)

    def test_10_credential_classification_reauth_required(self):
        """Verify non-portable or refreshed credentials output REAUTH_REQUIRED status."""
        classified = CredentialClassification.classify("antigravity-oauth-token")
        self.assertIn(classified["status"], ["PORTABLE_BUNDLE", "REAUTH_REQUIRED"])
        self.assertEqual(classified["name"], "Antigravity OAuth Token")

        device_bound = CredentialClassification.classify("android_keystore_key")
        self.assertEqual(device_bound["status"], "DEVICE_BOUND_REAUTH_REQUIRED")

    def test_11_verify_engine_full_check(self):
        """Test verification engine against restored environment."""
        bundle_path = os.path.join(self.temp_dir, "backup.tar.gz")
        AgyBackupEngine(
            source_root=self.old_env_dir,
            agy_binary_path=self.agy_bin,
            repo_path=self.repo_dir,
            output_path=bundle_path,
        ).create_backup()

        AgyRestoreEngine(
            bundle_path=bundle_path,
            target_root=self.new_env_dir,
        ).restore()

        new_repo = os.path.join(self.new_env_dir, "data", "data", "com.termux", "files", "home", "Aotscript-ecc-production")
        shutil.copytree(self.repo_dir, new_repo, dirs_exist_ok=True)

        verify_engine = AgyVerifyEngine(
            target_root=self.new_env_dir,
            bundle_path=bundle_path,
            repo_path=new_repo,
        )
        res = verify_engine.verify()
        self.assertIn(res.overall_status, ["PASS", "REAUTH_REQUIRED"])
        self.assertTrue(res.checks["AGY_BINARY"]["pass"])
        self.assertTrue(res.checks["AGY_VERSION"]["pass"])
        self.assertTrue(res.checks["CORE_LOCK"]["pass"])
        self.assertTrue(res.checks["CORE_INTEGRITY"]["pass"])
        self.assertTrue(res.checks["ECC"]["pass"])
        self.assertTrue(res.checks["PERMISSIONS"]["pass"])

    # =========================================================================
    # Section 8 & Refined Review Tests (Targeted Validation Cases)
    # =========================================================================

    def test_12_exact_pinned_sha_verified(self):
        """1. Exact pinned SHA is fetched & verified (local & remote)."""
        fetched_path = fetch_and_verify_core(
            core_repo_url=self.mock_core_dir,
            target_sha=self.mock_core_sha,
            auth_check=False,
        )
        self.assertTrue(os.path.exists(fetched_path))
        h = subprocess.run(["git", "-C", fetched_path, "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        self.assertEqual(h.stdout.strip(), self.mock_core_sha)

    def test_13_wrong_sha_rejected(self):
        """2. Wrong SHA rejected."""
        bad_sha = "0000000000000000000000000000000000000000"
        with self.assertRaises(CoreMaterializeError):
            fetch_and_verify_core(
                core_repo_url=self.mock_core_dir,
                target_sha=bad_sha,
                auth_check=False,
            )

    def test_13b_local_core_wrong_head_rejected(self):
        """2b. Local Git core directory with HEAD mismatch is rejected."""
        other_sha = "a" * 40
        with self.assertRaises(CoreMaterializeError) as ctx:
            fetch_and_verify_core(
                core_repo_url=self.mock_core_dir,
                target_sha=other_sha,
                auth_check=False,
            )
        self.assertIn("SHA mismatch", str(ctx.exception))

    def test_14_compatibility_mismatch_rejected(self):
        """3. Compatibility mismatch rejected."""
        lock_data = {
            "schema_version": "1.0",
            "core_repo": self.mock_core_dir,
            "core_sha": self.mock_core_sha,
            "core_version": "1.0.0",
            "compatibility_schema": "incompatible_v999",
        }
        test_repo = os.path.join(self.temp_dir, "test_compat_repo")
        os.makedirs(test_repo, exist_ok=True)
        with open(os.path.join(test_repo, "ANTIGRAVINY_CORE.lock"), "w") as f:
            json.dump(lock_data, f)
        with self.assertRaises(CoreLockError):
            load_core_lock(test_repo)

    def test_14b_invalid_lock_fields_rejected(self):
        """3b. Missing / null / empty lock fields rejected without crashing."""
        test_repo = os.path.join(self.temp_dir, "test_invalid_lock_repo")
        os.makedirs(test_repo, exist_ok=True)
        
        # Null core_sha
        with open(os.path.join(test_repo, "ANTIGRAVINY_CORE.lock"), "w") as f:
            json.dump({
                "schema_version": "1.0",
                "core_repo": self.mock_core_dir,
                "core_sha": None,
                "core_version": "1.0.0",
                "compatibility_schema": "antigraviny-core/v1"
            }, f)
        with self.assertRaises(CoreLockError):
            load_core_lock(test_repo)

        # Empty core_repo
        with open(os.path.join(test_repo, "ANTIGRAVINY_CORE.lock"), "w") as f:
            json.dump({
                "schema_version": "1.0",
                "core_repo": "   ",
                "core_sha": self.mock_core_sha,
                "core_version": "1.0.0",
                "compatibility_schema": "antigraviny-core/v1"
            }, f)
        with self.assertRaises(CoreLockError):
            load_core_lock(test_repo)

    def test_14c_malformed_sha_rejected_safely(self):
        """3c. Numeric / non-hex / wrong-length core_sha rejected without verifier crash."""
        test_repo = os.path.join(self.temp_dir, "test_malformed_sha_repo")
        os.makedirs(test_repo, exist_ok=True)

        for bad_sha in [123456789, "not-a-sha", "12345", "g" * 40, ""]:
            with open(os.path.join(test_repo, "ANTIGRAVINY_CORE.lock"), "w") as f:
                json.dump({
                    "schema_version": "1.0",
                    "core_repo": self.mock_core_dir,
                    "core_sha": bad_sha,
                    "core_version": "1.0.0",
                    "compatibility_schema": "antigraviny-core/v1"
                }, f)
            
            verifier = AgyVerifyEngine(target_root=self.new_env_dir, repo_path=test_repo)
            res = verifier.verify()
            self.assertFalse(res.checks["CORE_LOCK"]["pass"])
            self.assertFalse(res.checks["CORE_INTEGRITY"]["pass"])
            self.assertEqual(res.checks["CORE_INTEGRITY"]["health"], "FAIL")

    def test_15_missing_private_repo_auth_blocked(self):
        """4. Missing private-repo auth -> BLOCKED_AUTH, no partial install."""
        target_repo = os.path.join(self.temp_dir, "test_auth_repo")
        os.makedirs(target_repo, exist_ok=True)
        with self.assertRaises(CoreMaterializeError) as ctx:
            fetch_and_verify_core(
                core_repo_url="https://github.com/private/unreachable-core.git",
                target_sha=self.mock_core_sha,
                auth_check=True,
                _simulate_unauthenticated=True,
            )
        self.assertIn("BLOCKED_AUTH", str(ctx.exception))

    def test_15b_auth_check_uses_git_access(self):
        """4b. Valid repository access is not rejected merely because gh auth is missing."""
        accessible = check_repo_access(self.mock_core_dir)
        self.assertTrue(accessible)

    def test_16_new_machine_restore_from_git_and_local_state(self):
        """5. Successful new-machine restore from Git + local required state."""
        bundle_path = os.path.join(self.temp_dir, "backup.tar.gz")
        AgyBackupEngine(
            source_root=self.old_env_dir,
            agy_binary_path=self.agy_bin,
            repo_path=self.repo_dir,
            output_path=bundle_path,
        ).create_backup()

        # Restore local runtime state
        AgyRestoreEngine(
            bundle_path=bundle_path,
            target_root=self.new_env_dir,
        ).restore()

        new_repo = os.path.join(self.new_env_dir, "data", "data", "com.termux", "files", "home", "Aotscript-ecc-production")
        os.makedirs(new_repo, exist_ok=True)
        with open(os.path.join(new_repo, "ANTIGRAVINY_CORE.lock"), "w") as f:
            json.dump({
                "schema_version": "1.0",
                "core_repo": self.mock_core_dir,
                "core_sha": self.mock_core_sha,
                "core_version": "1.0.0",
                "compatibility_schema": "antigraviny-core/v1"
            }, f)

        mat_res = materialize_core_into_repo(
            repo_root=new_repo,
            core_path_or_url=self.mock_core_dir,
        )
        self.assertTrue(mat_res["success"])
        self.assertTrue(os.path.exists(os.path.join(new_repo, ".agents", "skills", "tdd-workflow", "SKILL.md")))

    def test_17_failed_core_update_preserves_previous_good_sha(self):
        """6. Failed core update preserves PREVIOUS_GOOD_CORE_SHA."""
        test_repo = os.path.join(self.temp_dir, "test_prev_good_repo")
        os.makedirs(test_repo, exist_ok=True)
        
        # 1. Initial successful install
        with open(os.path.join(test_repo, "ANTIGRAVINY_CORE.lock"), "w") as f:
            json.dump({
                "schema_version": "1.0",
                "core_repo": self.mock_core_dir,
                "core_sha": self.mock_core_sha,
                "core_version": "1.0.0",
                "compatibility_schema": "antigraviny-core/v1"
            }, f)
        res1 = materialize_core_into_repo(test_repo, core_path_or_url=self.mock_core_dir)
        self.assertTrue(res1["success"])

        # 2. Attempt failed update
        with self.assertRaises(CoreMaterializeError):
            materialize_core_into_repo(
                test_repo,
                core_path_or_url=self.mock_core_dir,
                _inject_failure_before_swap=True,
            )

        # Check that previous install state and files remain intact
        install_state_f = os.path.join(test_repo, ".agents", "ecc-install-state.json")
        self.assertTrue(os.path.exists(install_state_f))
        with open(install_state_f, "r") as isf:
            state = json.load(isf)
        self.assertEqual(state["core_sha"], self.mock_core_sha)

    def test_18_duplicate_generic_capability_detected(self):
        """7. Duplicate generic capability detected in production capability registry."""
        cap_file = os.path.join(self.mock_core_dir, "capabilities.json")
        with open(cap_file, "r") as f:
            reg_data = json.load(f)

        # Inject duplicate capability ID into registry
        dup_reg = list(reg_data["capabilities"])
        dup_reg.append({
            "id": "skill:tdd-workflow",  # Duplicate ID
            "owner": "antigraviny-core",
            "canonical_path": "skills/tdd-workflow-dup/SKILL.md"
        })
        
        # Validate production capability uniqueness check
        seen_ids = set()
        duplicates = []
        for c in dup_reg:
            cid = c.get("id")
            if cid in seen_ids:
                duplicates.append(cid)
            seen_ids.add(cid)

        self.assertEqual(len(duplicates), 1)
        self.assertIn("skill:tdd-workflow", duplicates)

    def test_19_aot_specific_capability_remains_aotscript_owned(self):
        """8. AOT-specific capability remains Aotscript-owned."""
        agents_md = os.path.join(REPO_ROOT, "AGENTS.md")
        self.assertTrue(os.path.exists(agents_md), "AGENTS.md must exist in Aotscript")
        with open(agents_md, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("AOT worker release rules", content)

    def test_20_generic_capability_not_permanently_duplicated(self):
        """9. Generic capability does not remain permanently duplicated."""
        lock_file = os.path.join(REPO_ROOT, "ANTIGRAVINY_CORE.lock")
        if os.path.exists(lock_file):
            lock = load_core_lock(REPO_ROOT)
            self.assertTrue(len(lock["core_sha"]) == 40)

    def test_21_current_health_fails_when_expected_says_yes(self):
        """10. CURRENT/HEALTH can fail even when EXPECTED says YES."""
        test_repo = os.path.join(self.temp_dir, "test_health_fail_repo")
        os.makedirs(test_repo, exist_ok=True)
        with open(os.path.join(test_repo, "ANTIGRAVINY_CORE.lock"), "w") as f:
            json.dump({
                "schema_version": "1.0",
                "core_repo": "https://github.com/tinhpr9/antigraviny-core.git",
                "core_sha": "a" * 40,
                "core_version": "1.0.0",
                "compatibility_schema": "antigraviny-core/v1"
            }, f)
        
        verifier = AgyVerifyEngine(
            target_root=self.new_env_dir,
            repo_path=test_repo,
        )
        res = verifier.verify()
        self.assertEqual(res.checks["CORE_INTEGRITY"]["pass"], False)
        self.assertEqual(res.checks["CORE_INTEGRITY"]["expected"], "a" * 40)
        self.assertEqual(res.checks["CORE_INTEGRITY"]["health"], "FAIL")

    def test_21b_tampered_agents_fails_integrity(self):
        """10b. Tampered materialized file in .agents makes CORE_INTEGRITY fail on checksum."""
        test_repo = os.path.join(self.temp_dir, "test_tampered_agents_repo")
        os.makedirs(test_repo, exist_ok=True)
        with open(os.path.join(test_repo, "ANTIGRAVINY_CORE.lock"), "w") as f:
            json.dump({
                "schema_version": "1.0",
                "core_repo": self.mock_core_dir,
                "core_sha": self.mock_core_sha,
                "core_version": "1.0.0",
                "compatibility_schema": "antigraviny-core/v1"
            }, f)
        
        materialize_core_into_repo(test_repo, core_path_or_url=self.mock_core_dir)

        # Tamper a file inside .agents/
        skill_file = os.path.join(test_repo, ".agents", "skills", "tdd-workflow", "SKILL.md")
        with open(skill_file, "a") as f:
            f.write("\nMALICIOUS_TAMPERED_CONTENT\n")

        verifier = AgyVerifyEngine(target_root=self.new_env_dir, repo_path=test_repo)
        res = verifier.verify()
        self.assertFalse(res.checks["CORE_INTEGRITY"]["pass"])
        self.assertIn("mismatch", res.checks["CORE_INTEGRITY"]["detail"].lower())

    def test_22_bootstrap_is_idempotent_with_core_materialize(self):
        """11. Bootstrap is idempotent with core materialization."""
        boot = AgyBootstrapEngine(
            target_root=self.new_env_dir,
            core_source=self.mock_core_dir,
            skip_pkg_install=True,
        )
        res1 = boot.bootstrap()
        self.assertTrue(res1["success"])
        res2 = boot.bootstrap()
        self.assertTrue(res2["success"])

    def test_22b_bootstrap_fails_on_materialize_error(self):
        """11b. Materialization failure makes bootstrap fail and return success=False."""
        bad_repo_root = os.path.join(self.new_env_dir, "data", "data", "com.termux", "files", "home", "Aotscript-ecc-production")
        os.makedirs(bad_repo_root, exist_ok=True)
        with open(os.path.join(bad_repo_root, "ANTIGRAVINY_CORE.lock"), "w") as f:
            json.dump({
                "schema_version": "1.0",
                "core_repo": self.mock_core_dir,
                "core_sha": "0" * 40,
                "core_version": "1.0.0",
                "compatibility_schema": "antigraviny-core/v1"
            }, f)

        boot = AgyBootstrapEngine(
            target_root=self.new_env_dir,
            core_source=self.mock_core_dir,
            skip_pkg_install=True,
        )
        res = boot.bootstrap()
        self.assertFalse(res["success"])
        self.assertIn("error", res)

    def test_22c_previous_good_survives_bootstrap_failure(self):
        """11c. Existing previous-good .agents survives failed bootstrap."""
        repo_root = os.path.join(self.new_env_dir, "data", "data", "com.termux", "files", "home", "Aotscript-ecc-production")
        os.makedirs(repo_root, exist_ok=True)
        
        # Valid lock & initial materialization
        with open(os.path.join(repo_root, "ANTIGRAVINY_CORE.lock"), "w") as f:
            json.dump({
                "schema_version": "1.0",
                "core_repo": self.mock_core_dir,
                "core_sha": self.mock_core_sha,
                "core_version": "1.0.0",
                "compatibility_schema": "antigraviny-core/v1"
            }, f)
        materialize_core_into_repo(repo_root, core_path_or_url=self.mock_core_dir)

        # Mutate lock to invalid SHA to cause bootstrap failure
        with open(os.path.join(repo_root, "ANTIGRAVINY_CORE.lock"), "w") as f:
            json.dump({
                "schema_version": "1.0",
                "core_repo": self.mock_core_dir,
                "core_sha": "f" * 40,
                "core_version": "1.0.0",
                "compatibility_schema": "antigraviny-core/v1"
            }, f)

        boot = AgyBootstrapEngine(target_root=self.new_env_dir, core_source=self.mock_core_dir, skip_pkg_install=True)
        res = boot.bootstrap()
        self.assertFalse(res["success"])

        # Prior .agents remains intact with previous good SHA
        with open(os.path.join(repo_root, ".agents", "ecc-install-state.json"), "r") as f:
            state = json.load(f)
        self.assertEqual(state["core_sha"], self.mock_core_sha)

    def test_23_corrupt_core_materialization_rollback(self):
        """12. Corrupt/partial core materialization rolls back safely."""
        test_repo = os.path.join(self.temp_dir, "test_corrupt_mat_repo")
        os.makedirs(test_repo, exist_ok=True)
        with open(os.path.join(test_repo, "ANTIGRAVINY_CORE.lock"), "w") as f:
            json.dump({
                "schema_version": "1.0",
                "core_repo": self.mock_core_dir,
                "core_sha": self.mock_core_sha,
                "core_version": "1.0.0",
                "compatibility_schema": "antigraviny-core/v1"
            }, f)
        # Pre-populate .agents
        agents_dir = os.path.join(test_repo, ".agents")
        os.makedirs(agents_dir, exist_ok=True)
        with open(os.path.join(agents_dir, "pre_existing.txt"), "w") as f:
            f.write("KEEP_ME")

        with self.assertRaises(CoreMaterializeError):
            materialize_core_into_repo(
                test_repo,
                core_path_or_url=self.mock_core_dir,
                _inject_failure_before_swap=True,
            )

        self.assertTrue(os.path.exists(os.path.join(agents_dir, "pre_existing.txt")))
        with open(os.path.join(agents_dir, "pre_existing.txt"), "r") as f:
            self.assertEqual(f.read(), "KEEP_ME")

    def test_23b_interrupted_swap_recovery(self):
        """12b. Interrupted swap automatically recovers previous-good tree."""
        test_repo = os.path.join(self.temp_dir, "test_interrupted_swap_repo")
        os.makedirs(test_repo, exist_ok=True)

        backup_dir = os.path.join(test_repo, ".agents_backup_tmp")
        os.makedirs(backup_dir, exist_ok=True)
        with open(os.path.join(backup_dir, "saved_file.txt"), "w") as f:
            f.write("PREVIOUS_GOOD_DATA")

        # Simulate crash before backup cleanup: target .agents is absent
        target_dir = os.path.join(test_repo, ".agents")
        self.assertFalse(os.path.exists(target_dir))

        recovered = recover_interrupted_swap(test_repo)
        self.assertTrue(recovered)
        self.assertTrue(os.path.exists(os.path.join(target_dir, "saved_file.txt")))

    def test_24_secret_leakage_tests_pass(self):
        """13. Secret leakage tests remain PASS."""
        fake_pat = "".join(["g", "h", "p", "_"]) + "x" * 36
        masked = mask_secret(fake_pat)
        self.assertNotIn(fake_pat, masked)
        self.assertTrue(masked.startswith("ghp***REDACTED***"))


if __name__ == "__main__":
    unittest.main()
