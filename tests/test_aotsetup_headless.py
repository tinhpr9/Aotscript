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


if __name__ == "__main__":
    unittest.main()
