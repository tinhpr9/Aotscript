import unittest
import os
import tempfile
import json
import logging
from unittest.mock import patch, MagicMock

import rejoin_core
from rejoin_core import ConfigManager, Monitor, SystemEnvironment

class MockEnv(SystemEnvironment):
    def __init__(self):
        self.running_processes = set()
        self.launch_history = []
        self.sleep_history = []
        self.launch_returns = True

    def is_process_running(self, package: str) -> bool:
        return package in self.running_processes

    def launch_intent(self, package: str, url: str) -> bool:
        self.launch_history.append((package, url))
        success = True
        if isinstance(self.launch_returns, dict):
            success = self.launch_returns.get(package, True)
        else:
            success = self.launch_returns

        if success and getattr(self, "simulate_process_start", False):
            self.running_processes.add(package)

        return success

    def sleep(self, seconds: int):
        self.sleep_history.append(seconds)

    def log(self, level: int, msg: str):
        pass

class TestRejoin(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.old_config_dir = rejoin_core.CONFIG_DIR
        self.old_config_file = rejoin_core.CONFIG_FILE
        self.old_lock_file = rejoin_core.LOCK_FILE
        self.old_log_file = rejoin_core.LOG_FILE

        rejoin_core.CONFIG_DIR = self.test_dir
        rejoin_core.CONFIG_FILE = os.path.join(self.test_dir, "config.json")
        rejoin_core.LOCK_FILE = os.path.join(self.test_dir, "monitor.lock")
        rejoin_core.LOG_FILE = os.path.join(self.test_dir, "rejoin.log")

        if rejoin_core._lock_fd is not None:
            rejoin_core.release_lock()

    def tearDown(self):
        rejoin_core.release_lock()
        rejoin_core.CONFIG_DIR = self.old_config_dir
        rejoin_core.CONFIG_FILE = self.old_config_file
        rejoin_core.LOCK_FILE = self.old_lock_file
        rejoin_core.LOG_FILE = self.old_log_file

        for f in os.listdir(self.test_dir):
            os.remove(os.path.join(self.test_dir, f))
        os.rmdir(self.test_dir)

    def test_config_load_save(self):
        conf = ConfigManager.load_config()
        self.assertIn("packages", conf)
        ConfigManager.add_package("pkg1", "roblox://url1")
        conf2 = ConfigManager.load_config()
        self.assertIn("pkg1", conf2["packages"])
        self.assertEqual(conf2["packages"]["pkg1"]["join_url"], "roblox://url1")
        self.assertTrue(conf2["packages"]["pkg1"]["enabled"])

        ConfigManager.set_package_enabled("pkg1", False)
        self.assertFalse(ConfigManager.load_config()["packages"]["pkg1"]["enabled"])

        ConfigManager.remove_package("pkg1")
        self.assertNotIn("pkg1", ConfigManager.load_config()["packages"])

    def test_malformed_config(self):
        with open(rejoin_core.CONFIG_FILE, "w") as f:
            f.write("{ invalid json ")
        conf = ConfigManager.load_config()
        self.assertIn("packages", conf) # Falls back to default

    def test_duplicate_package(self):
        ConfigManager.add_package("pkg1", "roblox://url1")
        ConfigManager.add_package("pkg1", "roblox://url2")
        conf = ConfigManager.load_config()
        self.assertEqual(len(conf["packages"]), 1)
        self.assertEqual(conf["packages"]["pkg1"]["join_url"], "roblox://url2")

    def test_process_running(self):
        env = MockEnv()
        env.running_processes.add("pkg1")
        ConfigManager.add_package("pkg1", "roblox://url1")
        monitor = Monitor(env)
        monitor.run_once()
        self.assertEqual(monitor.state["pkg1"]["status"], "RUNNING")
        self.assertEqual(len(env.launch_history), 0, f"History: {env.launch_history}")

    def test_launch_success(self):
        env = MockEnv()
        env.simulate_process_start = True
        ConfigManager.add_package("pkg1", "roblox://url1")
        monitor = Monitor(env)
        monitor.run_once()
        self.assertEqual(monitor.state["pkg1"]["status"], "RUNNING")
        self.assertEqual(len(env.launch_history), 1)

    def test_launch_false_success(self):
        env = MockEnv()
        ConfigManager.add_package("pkg1", "roblox://url1")
        monitor = Monitor(env)
        monitor.run_once()
        self.assertEqual(monitor.state["pkg1"]["status"], "LAUNCH_ERROR")
        self.assertEqual(len(env.launch_history), 1)

    def test_invalid_package_rejected(self):
        env = SystemEnvironment()
        self.assertFalse(env.is_process_running("invalid package"))
        self.assertFalse(env.launch_intent("invalid package", "url"))

    def test_launch_fail_and_max_retry(self):
        env = MockEnv()
        env.launch_returns = False
        ConfigManager.add_package("pkg1", "roblox://url1")
        monitor = Monitor(env)

        # We need to simulate time passing for retry delay
        mock_time = 100
        def fake_time():
            nonlocal mock_time
            mock_time += 15
            return mock_time

        with patch('time.time', side_effect=fake_time):
            monitor.run_once() # Retry 1
            self.assertEqual(monitor.state["pkg1"]["status"], "LAUNCH_ERROR")
            monitor.run_once() # Retry 2
            monitor.run_once() # Retry 3
            monitor.run_once() # Max retries hit
            self.assertEqual(monitor.state["pkg1"]["status"], "FAILED")

    def test_one_failure_does_not_stop_others(self):
        env = MockEnv()
        env.simulate_process_start = True
        env.launch_returns = {"fail_pkg": False, "ok_pkg": True}
        ConfigManager.add_package("fail_pkg", "roblox://url1")
        ConfigManager.add_package("ok_pkg", "roblox://url2")
        monitor = Monitor(env)

        monitor.run_once()
        self.assertEqual(monitor.state["fail_pkg"]["status"], "LAUNCH_ERROR")
        self.assertEqual(monitor.state["ok_pkg"]["status"], "RUNNING")

    def test_duplicate_monitor_lock_atomic(self):
        # First acquire should succeed
        self.assertTrue(rejoin_core.acquire_lock())

        # Second acquire in same process might succeed depending on OS flock behavior,
        # but let's test isolation by spawning a new process
        import subprocess
        import sys

        script = f"""
import sys
sys.path.append('{os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}')
import rejoin_core
rejoin_core.CONFIG_DIR = '{self.test_dir}'
rejoin_core.LOCK_FILE = '{rejoin_core.LOCK_FILE}'
success = rejoin_core.acquire_lock()
sys.exit(0 if success else 1)
"""
        # Spawning another process to acquire the lock should fail (exit code 1)
        res = subprocess.run([sys.executable, "-c", script])
        self.assertEqual(res.returncode, 1)

        rejoin_core.release_lock()

        # After release, it should succeed
        res = subprocess.run([sys.executable, "-c", script])
        self.assertEqual(res.returncode, 0)

    def test_cooldown_after_success(self):
        env = MockEnv()
        env.simulate_process_start = True
        ConfigManager.add_package("pkg1", "roblox://url1")
        monitor = Monitor(env)

        mock_time = 100
        def fake_time():
            nonlocal mock_time
            mock_time += 1
            return mock_time

        with patch('time.time', side_effect=fake_time):
            monitor.run_once() # sets last_success
            self.assertEqual(monitor.state["pkg1"]["status"], "RUNNING")

            env.running_processes.remove("pkg1")

            monitor.run_once() # should be in cooldown, won't launch
            self.assertEqual(len(env.launch_history), 1)

            mock_time += 35 # pass cooldown
            monitor.run_once() # should launch again
            self.assertEqual(len(env.launch_history), 2)

    @patch('subprocess.run')
    def test_timeouts_and_fallback(self, mock_run):
        import subprocess
        env = SystemEnvironment()
        env.sleep = MagicMock()

        # 1. pidof timeout
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="pidof", timeout=5)
        self.assertFalse(env.is_process_running("com.roblox"))

        # 2. explicit success does not use fallback
        mock_run.reset_mock()
        def fake_run_success(*args, **kwargs):
            res = MagicMock()
            res.returncode = 0
            res.stdout = "Starting: Intent"
            res.stderr = ""
            return res

        mock_run.side_effect = fake_run_success
        self.assertTrue(env.launch_intent("com.roblox", "roblox://123"))
        fallback_called = False
        for call in mock_run.call_args_list:
            cmd = call[0][0][2]
            if "-p 'com.roblox'" in cmd or "-p com.roblox" in cmd:
                fallback_called = True
        self.assertFalse(fallback_called, "Fallback should not be called if explicit succeeds")

        # 3. splash timeout fails safely and uses fallback
        mock_run.reset_mock()
        def fake_run_splash_timeout(*args, **kwargs):
            cmd = args[0][2]
            if "ActivitySplash" in cmd:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)
            res = MagicMock()
            res.returncode = 0
            res.stdout = "Starting: Intent"
            res.stderr = ""
            return res

        mock_run.side_effect = fake_run_splash_timeout
        self.assertTrue(env.launch_intent("com.roblox", "roblox://123"))
        fallback_called = False
        for call in mock_run.call_args_list:
            cmd = call[0][0][2]
            if "-p com.roblox" in cmd:
                fallback_called = True
        self.assertTrue(fallback_called, "Fallback should be called if splash times out")

        # 4. protocol timeout fails safely
        mock_run.reset_mock()
        def fake_run_protocol_timeout(*args, **kwargs):
            cmd = args[0][2]
            if "ActivityProtocolLaunch" in cmd:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)
            res = MagicMock()
            res.returncode = 0
            res.stdout = "Starting: Intent"
            res.stderr = ""
            return res

        mock_run.side_effect = fake_run_protocol_timeout
        self.assertFalse(env.launch_intent("com.roblox", "roblox://123"))

        # 5. component failure invokes fallback
        mock_run.reset_mock()
        def fake_run_component_failure(*args, **kwargs):
            cmd = args[0][2]
            res = MagicMock()
            res.stdout = ""
            res.stderr = ""
            res.returncode = 0

            if "ActivityProtocolLaunch" in cmd:
                res.returncode = 1
                res.stderr = "Error: does not exist"
            elif "-p com.roblox" in cmd:
                res.stdout = "Starting: Intent"
                res.returncode = 0
            return res

        mock_run.side_effect = fake_run_component_failure
        self.assertTrue(env.launch_intent("com.roblox", "123"))
        fallback_called = False
        for call in mock_run.call_args_list:
            cmd = call[0][0][2]
            if "-p com.roblox" in cmd and "roblox://placeID=123" in cmd:
                fallback_called = True
        self.assertTrue(fallback_called, "Fallback should be called with correct URL if explicit component fails")

        # 6. force-stop timeout fails safely
        mock_run.reset_mock()
        def fake_run_force_stop_timeout(*args, **kwargs):
            cmd = args[0][2]
            if "force-stop" in cmd:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)
            res = MagicMock()
            res.returncode = 0
            res.stdout = "Starting: Intent"
            res.stderr = ""
            return res

        mock_run.side_effect = fake_run_force_stop_timeout
        self.assertTrue(env.launch_intent("com.roblox", "roblox://123"))

        # 7. shell injection regression
        self.assertFalse(env.launch_intent("com.roblox; rm -rf /", "123"))

if __name__ == '__main__':
    unittest.main()
