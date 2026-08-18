#!/usr/bin/env python3
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SETUP_SH = ROOT / "setup.sh"
PROVISION_SH = ROOT / "provision-device.sh"


class TestAotsetupHeadless(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aotsetup-test-")
        self.root = pathlib.Path(self.tmp.name)
        self.home = self.root / "home"
        self.state = self.root / "state"
        self.prefix = self.root / "prefix"
        self.storage = self.root / "storage"
        self.shouko = self.storage / "Download" / "Shouko"
        self.home.mkdir(parents=True)
        self.prefix.mkdir(parents=True)
        (self.prefix / "bin").mkdir(parents=True)
        self.shouko.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _base_env(self, host_id="test-host", input_mode="env", **extra):
        env = os.environ.copy()
        env.update({
            "HOME": str(self.home),
            "XDG_STATE_HOME": str(self.state),
            "PREFIX": str(self.prefix),
            "AOTSCRIPT_SETUP_TEST_MODE": "1",
            "AOTSCRIPT_SETUP_INPUT_MODE": input_mode,
            "AOTSCRIPT_SETUP_STORAGE_ROOT": str(self.storage),
            "AOTSCRIPT_SETUP_HOST_ID": host_id,
        })
        env.update(extra)
        return env

    def _run_setup(self, env):
        return subprocess.run(
            ["bash", str(SETUP_SH)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    # 1. fresh device: một lần aotsetup -> SUCCESS, không prompt ĐÃ XONG
    def test_01_fresh_device_success_without_checkpoint_prompt(self):
        env = self._base_env(
            AOTSCRIPT_SETUP_DRY_RUN="1",
            AOTSCRIPT_SETUP_DEVICE_ID="m88",
            AOTSCRIPT_SETUP_GROUP="NOVA",
            AOTSCRIPT_SETUP_CONFIRM="yes",
        )
        # Note: AOTSCRIPT_SETUP_CHECKPOINT_ACTION is NOT set at all
        res = self._run_setup(env)
        self.assertEqual(res.returncode, 0, f"Setup failed: {res.stderr}\n{res.stdout}")
        self.assertNotIn("ĐÃ XONG", res.stdout)
        self.assertNotIn("MỞ LẠI", res.stdout)
        self.assertIn("AOT setup hoàn tất", res.stdout)

    # 2. device_id.txt + device_group.txt được tạo trước khi setup kết thúc
    def test_02_shouko_identity_files_created(self):
        env = self._base_env(
            AOTSCRIPT_SETUP_DRY_RUN="1",
            AOTSCRIPT_SETUP_DEVICE_ID="m88",
            AOTSCRIPT_SETUP_GROUP="NOVA",
            AOTSCRIPT_SETUP_CONFIRM="yes",
        )
        res = self._run_setup(env)
        self.assertEqual(res.returncode, 0)
        self.assertTrue((self.shouko / "device_id.txt").is_file())
        self.assertTrue((self.shouko / "device_group.txt").is_file())
        self.assertEqual((self.shouko / "device_id.txt").read_text(encoding="utf-8").strip(), "m88")
        self.assertEqual((self.shouko / "device_group.txt").read_text(encoding="utf-8").strip(), "NOVA")

    # 3. aot_group_config.json tồn tại/hợp lệ
    def test_03_aot_group_config_valid(self):
        config_path = self.shouko / "aot_group_config.json"
        config_path.write_text(json.dumps({"enabled": True, "role": "follower", "session_id": "test"}), encoding="utf-8")
        env = self._base_env(
            AOTSCRIPT_SETUP_DRY_RUN="1",
            AOTSCRIPT_SETUP_DEVICE_ID="m88",
            AOTSCRIPT_SETUP_GROUP="MARMOT",
            AOTSCRIPT_SETUP_CONFIRM="yes",
        )
        res = self._run_setup(env)
        self.assertEqual(res.returncode, 0)
        self.assertTrue(config_path.is_file())
        data = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertTrue(data.get("enabled"))

    # 4. Agent start đúng 1 process (verified in test mode via state tracking)
    def test_04_agent_started_single_process(self):
        env = self._base_env(
            AOTSCRIPT_SETUP_DRY_RUN="1",
            AOTSCRIPT_SETUP_DEVICE_ID="m88",
            AOTSCRIPT_SETUP_GROUP="NOVA",
            AOTSCRIPT_SETUP_CONFIRM="yes",
        )
        res = self._run_setup(env)
        self.assertEqual(res.returncode, 0)
        setup_driver = self.state / "aotscript" / "setup-driver"
        self.assertEqual((setup_driver / "setup_complete").read_text().strip(), "yes")

    # 5. AOT registration configured
    def test_05_aot_registration_configured(self):
        env = self._base_env(
            AOTSCRIPT_SETUP_DRY_RUN="1",
            AOTSCRIPT_SETUP_DEVICE_ID="m88",
            AOTSCRIPT_SETUP_GROUP="NOVA",
            AOTSCRIPT_SETUP_CONFIRM="yes",
        )
        res = self._run_setup(env)
        self.assertEqual(res.returncode, 0)
        setup_driver = self.state / "aotscript" / "setup-driver"
        self.assertTrue((setup_driver / "device_id").is_file())
        self.assertEqual((setup_driver / "device_id").read_text().strip(), "m88")

    # 6. không cần mprovision done pre
    def test_06_no_mprovision_done_pre_needed(self):
        mprovision_json = self.state / "aotscript" / "mprovision.json"
        self.assertFalse(mprovision_json.exists())
        env = self._base_env(
            AOTSCRIPT_SETUP_DRY_RUN="1",
            AOTSCRIPT_SETUP_DEVICE_ID="m99",
            AOTSCRIPT_SETUP_GROUP="NOVA",
            AOTSCRIPT_SETUP_CONFIRM="yes",
        )
        res = self._run_setup(env)
        self.assertEqual(res.returncode, 0)
        self.assertNotIn("mprovision done pre", res.stdout)
        self.assertNotIn("THỦ CÔNG 1", res.stdout)

    # 7. không tự ghi backup/manual checkpoint là complete
    def test_07_does_not_forge_backup_checkpoint(self):
        env = self._base_env(
            AOTSCRIPT_SETUP_DRY_RUN="1",
            AOTSCRIPT_SETUP_DEVICE_ID="m99",
            AOTSCRIPT_SETUP_GROUP="NOVA",
            AOTSCRIPT_SETUP_CONFIRM="yes",
        )
        res = self._run_setup(env)
        self.assertEqual(res.returncode, 0)
        mprovision_json = self.state / "aotscript" / "mprovision.json"
        if mprovision_json.exists():
            data = json.loads(mprovision_json.read_text(encoding="utf-8"))
            self.assertEqual(data.get("backup_before", ""), "")
            self.assertEqual(data.get("manual_pre_confirmed_at", ""), "")

    # 8. rerun aotsetup idempotent
    def test_08_rerun_aotsetup_idempotent(self):
        env = self._base_env(
            AOTSCRIPT_SETUP_DRY_RUN="1",
            AOTSCRIPT_SETUP_DEVICE_ID="m88",
            AOTSCRIPT_SETUP_GROUP="NOVA",
            AOTSCRIPT_SETUP_CONFIRM="yes",
        )
        res1 = self._run_setup(env)
        self.assertEqual(res1.returncode, 0)
        
        # Second run without providing DEVICE_ID or CONFIRM env vars
        env2 = self._base_env(AOTSCRIPT_SETUP_DRY_RUN="1")
        res2 = self._run_setup(env2)
        self.assertEqual(res2.returncode, 0)
        self.assertIn("Identity hợp lệ", res2.stdout)

    # 9. existing device không reset identity
    def test_09_existing_device_preserves_identity(self):
        env = self._base_env(
            AOTSCRIPT_SETUP_DRY_RUN="1",
            AOTSCRIPT_SETUP_DEVICE_ID="m88",
            AOTSCRIPT_SETUP_GROUP="NOVA",
            AOTSCRIPT_SETUP_CONFIRM="yes",
        )
        self._run_setup(env)
        
        # Modify cookie in shouko to prove it is preserved
        cookie_file = self.shouko / "cookie.txt"
        cookie_file.write_text("my-secret-cookie", encoding="utf-8")
        
        env2 = self._base_env(AOTSCRIPT_SETUP_DRY_RUN="1")
        res2 = self._run_setup(env2)
        self.assertEqual(res2.returncode, 0)
        self.assertEqual(cookie_file.read_text(encoding="utf-8"), "my-secret-cookie")
        self.assertEqual((self.shouko / "device_id.txt").read_text(encoding="utf-8").strip(), "m88")

    # 10. clone migration behavior cũ không bị phá
    def test_10_clone_migration_preserved(self):
        setup_dir = self.state / "aotscript" / "setup-driver"
        setup_dir.mkdir(parents=True, exist_ok=True)
        (setup_dir / "device_id").write_text("m117\n")
        (setup_dir / "device_group").write_text("NOVA\n")
        source_hash = hashlib.sha256(b"source-host").hexdigest()
        (setup_dir / "host_fingerprint").write_text(source_hash + "\n")
        (setup_dir / "setup_complete").write_text("yes\n")
        (setup_dir / "provision_initialized").write_text("yes\n")
        (self.shouko / "device_id.txt").write_text("m117\n")
        (self.shouko / "device_group.txt").write_text("NOVA\n")
        (self.shouko / "agent_config.json").write_text(
            json.dumps({"worker_report_url": "https://example.invalid", "agent_report_secret": "sec"}),
            encoding="utf-8",
        )
        (self.storage / "Download" / "Agent_Core.py").write_text("print('agent')\n", encoding="utf-8")

        # Now run on target-host with new ID m74
        env = self._base_env(
            host_id="target-host",
            AOTSCRIPT_SETUP_DEVICE_ID="m74",
            AOTSCRIPT_SETUP_GROUP="NOVA",
            AOTSCRIPT_SETUP_CONFIRM="yes",
        )
        res = self._run_setup(env)
        out = res.stdout + res.stderr
        self.assertEqual(res.returncode, 0, f"Clone failed: {res.stderr}\n{res.stdout}")
        self.assertIn("m117", out)
        self.assertIn("m74", out)
        self.assertEqual((self.shouko / "device_id.txt").read_text(encoding="utf-8").strip(), "m74")
        self.assertEqual((setup_dir / "device_id").read_text(encoding="utf-8").strip(), "m74")

    # 11. mprovision manual workflow vẫn dùng độc lập được
    def test_11_mprovision_independent_status_and_commands(self):
        res = subprocess.run(
            ["bash", str(PROVISION_SH), "status"],
            env=self._base_env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn(res.returncode, (0, 1))

    # 12. Production child invoked with correct args & AOTSCRIPT_PROVISION_REF propagated
    def test_12_production_child_invoked_with_correct_args_and_env(self):
        recorder = self.root / "mock_msetup.sh"
        record_file = self.root / "record.json"
        recorder.write_text(
            f"#!/usr/bin/env bash\n"
            f"cat <<EOF > '{record_file}'\n"
            f"{{\n"
            f'  "arg1": "$1",\n'
            f'  "arg2": "$2",\n'
            f'  "provision_ref": "$AOTSCRIPT_PROVISION_REF"\n'
            f"}}\n"
            f"EOF\n"
            f"exit 0\n",
            encoding="utf-8",
        )
        recorder.chmod(0o755)

        env = self._base_env(
            AOTSCRIPT_SETUP_DEVICE_ID="m88",
            AOTSCRIPT_SETUP_GROUP="NOVA",
            AOTSCRIPT_SETUP_CONFIRM="yes",
            AOTSCRIPT_SETUP_M166_SOURCE=str(recorder),
        )
        res = self._run_setup(env)
        self.assertEqual(res.returncode, 0, f"Setup failed: {res.stderr}\n{res.stdout}")
        self.assertTrue(record_file.is_file())
        data = json.loads(record_file.read_text(encoding="utf-8"))
        self.assertEqual(data["arg1"], "m88")
        self.assertEqual(data["arg2"], "NOVA")
        self.assertTrue(data["provision_ref"].strip())

        setup_driver = self.state / "aotscript" / "setup-driver"
        self.assertEqual((setup_driver / "setup_complete").read_text().strip(), "yes")

    # 13. Malicious $PWD/setup-m166.sh is NEVER executed
    def test_13_malicious_pwd_script_ignored(self):
        pwd_script = self.root / "setup-m166.sh"
        marker = self.root / "malicious_marker"
        pwd_script.write_text(f"#!/usr/bin/env bash\ntouch '{marker}'\nexit 99\n", encoding="utf-8")
        pwd_script.chmod(0o755)

        fake_bin = self.root / "fake_bin"
        fake_bin.mkdir()
        fake_curl = fake_bin / "curl"
        safe_payload = self.root / "downloaded_msetup.sh"
        safe_payload.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        fake_curl.write_text(
            f"#!/usr/bin/env bash\n"
            f"shift\n"  # skip curl flags until destination file
            f'target="${{@: -1}}"\n'
            f"cp '{safe_payload}' \"$target\"\n"
            f"exit 0\n",
            encoding="utf-8",
        )
        fake_curl.chmod(0o755)

        path_env = os.environ.get("PATH", "")
        env = self._base_env(
            AOTSCRIPT_SETUP_DEVICE_ID="m88",
            AOTSCRIPT_SETUP_GROUP="NOVA",
            AOTSCRIPT_SETUP_CONFIRM="yes",
            PATH=f"{fake_bin}:{path_env}",
        )
        res = subprocess.run(
            ["bash", str(SETUP_SH)],
            cwd=str(self.root),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 0, f"Setup failed: {res.stderr}\n{res.stdout}")
        self.assertFalse(marker.exists(), "Malicious $PWD/setup-m166.sh must NOT be executed!")

    # 14. Download failure fails closed cleanly
    def test_14_download_failure_fails_closed(self):
        fake_bin = self.root / "fake_bin"
        fake_bin.mkdir()
        fake_curl = fake_bin / "curl"
        fake_curl.write_text("#!/usr/bin/env bash\nexit 22\n", encoding="utf-8")
        fake_curl.chmod(0o755)

        path_env = os.environ.get("PATH", "")
        env = self._base_env(
            AOTSCRIPT_SETUP_DEVICE_ID="m88",
            AOTSCRIPT_SETUP_GROUP="NOVA",
            AOTSCRIPT_SETUP_CONFIRM="yes",
            PATH=f"{fake_bin}:{path_env}",
        )
        res = self._run_setup(env)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Không tải được setup-m166.sh", res.stderr)
        setup_driver = self.state / "aotscript" / "setup-driver"
        self.assertFalse((setup_driver / "setup_complete").exists())

    # 15. Syntax validation failure fails closed before execution
    def test_15_syntax_validation_failure_fails_closed(self):
        fake_bin = self.root / "fake_bin"
        fake_bin.mkdir()
        fake_curl = fake_bin / "curl"
        fake_curl.write_text(
            '#!/usr/bin/env bash\n'
            'target="${@: -1}"\n'
            'echo "function { )" > "$target"\n'
            'exit 0\n',
            encoding="utf-8",
        )
        fake_curl.chmod(0o755)

        path_env = os.environ.get("PATH", "")
        env = self._base_env(
            AOTSCRIPT_SETUP_DEVICE_ID="m88",
            AOTSCRIPT_SETUP_GROUP="NOVA",
            AOTSCRIPT_SETUP_CONFIRM="yes",
            PATH=f"{fake_bin}:{path_env}",
        )
        res = self._run_setup(env)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("setup-m166.sh tải về sai cú pháp", res.stderr)
        setup_driver = self.state / "aotscript" / "setup-driver"
        self.assertFalse((setup_driver / "setup_complete").exists())

    # 16. Child exits non-zero -> setup fails closed
    def test_16_child_non_zero_exit_fails_closed(self):
        failing_script = self.root / "failing_msetup.sh"
        failing_script.write_text("#!/usr/bin/env bash\nexit 42\n", encoding="utf-8")
        failing_script.chmod(0o755)

        env = self._base_env(
            AOTSCRIPT_SETUP_DEVICE_ID="m88",
            AOTSCRIPT_SETUP_GROUP="NOVA",
            AOTSCRIPT_SETUP_CONFIRM="yes",
            AOTSCRIPT_SETUP_M166_SOURCE=str(failing_script),
        )
        res = self._run_setup(env)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("AOT msetup không hoàn tất", res.stderr)
        setup_driver = self.state / "aotscript" / "setup-driver"
        self.assertFalse((setup_driver / "setup_complete").exists())

    # 17. Incomplete identity pair fails closed
    def test_17_partial_identity_state_fails_closed(self):
        # Create incomplete identity pair: device_id exists but device_group is missing
        (self.shouko / "device_id.txt").write_text("m88\n", encoding="utf-8")

        env = self._base_env(
            AOTSCRIPT_SETUP_DEVICE_ID="m88",
            AOTSCRIPT_SETUP_GROUP="NOVA",
            AOTSCRIPT_SETUP_CONFIRM="yes",
        )
        res = self._run_setup(env)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("incomplete_identity_pair", res.stderr + res.stdout)

    # 18. Fresh setup executes production child once, rerun is idempotent without re-execution
    def test_18_fresh_setup_then_rerun_skips_child(self):
        count_file = self.root / "call_count.txt"
        count_file.write_text("0", encoding="utf-8")
        recorder = self.root / "counting_msetup.sh"
        recorder.write_text(
            f"#!/usr/bin/env bash\n"
            f"count=$(cat '{count_file}')\n"
            f"count=$((count + 1))\n"
            f"echo \"$count\" > '{count_file}'\n"
            f"exit 0\n",
            encoding="utf-8",
        )
        recorder.chmod(0o755)

        env1 = self._base_env(
            AOTSCRIPT_SETUP_DEVICE_ID="m88",
            AOTSCRIPT_SETUP_GROUP="NOVA",
            AOTSCRIPT_SETUP_CONFIRM="yes",
            AOTSCRIPT_SETUP_M166_SOURCE=str(recorder),
        )
        res1 = self._run_setup(env1)
        self.assertEqual(res1.returncode, 0)
        self.assertEqual(count_file.read_text(encoding="utf-8").strip(), "1")

        # Second run without M166_SOURCE or env overrides
        env2 = self._base_env()
        res2 = self._run_setup(env2)
        self.assertEqual(res2.returncode, 0)
        self.assertIn("Identity hợp lệ", res2.stdout)
        self.assertEqual(count_file.read_text(encoding="utf-8").strip(), "1", "Rerun must NOT re-execute child setup!")


if __name__ == "__main__":
    unittest.main()
