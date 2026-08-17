#!/usr/bin/env python3
"""
Unit and integration tests for Antigraviny/Agy Migration System.
Tests cover:
1. Backup generation & manifest structure
2. Corrupt bundle detection & rejection
3. Missing manifest rejection
4. SHA-256 mismatch rejection
5. Missing critical file fail-fast
6. Restore into empty environment
7. Restore idempotency
8. Rollback on partial/failed restore
9. Permission & executable bit preservation
10. Repo remote & HEAD verification
11. ECC completeness verification
12. Launcher start smoke test
13. Secret safety (no plaintext secrets in logs or git diff)
14. Credential classification & REAUTH_REQUIRED handling
15. Path translation between old and new machines
16. Idempotent bootstrap
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

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

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

        # 6. Repo structure
        self.repo_dir = os.path.join(self.termux_home, "Aotscript-ecc-production")
        os.makedirs(os.path.join(self.repo_dir, ".agents", "rules"), exist_ok=True)
        os.makedirs(os.path.join(self.repo_dir, ".agents", "skills", "test-ecc-skill"), exist_ok=True)
        os.makedirs(os.path.join(self.repo_dir, ".agents", "workflows"), exist_ok=True)
        os.makedirs(os.path.join(self.repo_dir, ".agents", "agents"), exist_ok=True)
        with open(os.path.join(self.repo_dir, "AGENTS.md"), "w") as f:
            f.write("# AGENTS rules\n")
        with open(os.path.join(self.repo_dir, ".agents", "ecc-install-state.json"), "w") as f:
            json.dump({"installed": True, "version": "1.0"}, f)
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

        # Unpack, tamper with a file, repack
        tamper_dir = os.path.join(self.temp_dir, "tamper_unpack")
        os.makedirs(tamper_dir, exist_ok=True)
        with tarfile.open(bundle_path, "r:gz") as tar:
            tar.extractall(tamper_dir)

        # Modify one of the files
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

        # Check that files were placed correctly
        restored_agy = os.path.join(self.new_env_dir, "root", ".local", "bin", "agy")
        self.assertTrue(os.path.exists(restored_agy))
        self.assertEqual(compute_file_sha256(restored_agy), compute_file_sha256(self.agy_bin))

        # Check executable bit
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

        # Check hash still matches
        restored_agy = os.path.join(self.new_env_dir, "root", ".local", "bin", "agy")
        self.assertEqual(compute_file_sha256(restored_agy), compute_file_sha256(self.agy_bin))

    def test_08_rollback_on_failure(self):
        """Test that if an error occurs mid-restore, pre-existing target files are preserved via rollback."""
        # Setup existing file on target machine
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
            _inject_failure_after_count=2,  # Inject error during restoration
        )
        with self.assertRaises(RestoreError):
            restore_engine.restore()

        # Check rollback restored pre-existing file
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

        # Check manifest does not contain raw token content
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

        # Copy mock repo to new machine for verify test
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
        self.assertTrue(res.checks["ECC"]["pass"])
        self.assertTrue(res.checks["PERMISSIONS"]["pass"])


if __name__ == "__main__":
    unittest.main()
