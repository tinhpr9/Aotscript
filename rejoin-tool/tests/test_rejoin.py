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
        if isinstance(self.launch_returns, dict):
            return self.launch_returns.get(package, True)
        return self.launch_returns

    def sleep(self, seconds: int):
        self.sleep_history.append(seconds)

    def log(self, level: int, msg: str):
        pass

class TestRejoin(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        rejoin_core.CONFIG_DIR = self.test_dir
        rejoin_core.CONFIG_FILE = os.path.join(self.test_dir, "config.json")
        rejoin_core.LOCK_FILE = os.path.join(self.test_dir, "monitor.lock")
        rejoin_core.LOG_FILE = os.path.join(self.test_dir, "rejoin.log")

    def tearDown(self):
        for f in [rejoin_core.CONFIG_FILE, rejoin_core.LOCK_FILE, rejoin_core.LOG_FILE, rejoin_core.CONFIG_FILE + ".tmp"]:
            if os.path.exists(f):
                os.remove(f)
        os.rmdir(self.test_dir)

    def test_config_load_save(self):
        conf = ConfigManager.load_config()
        self.assertIn("packages", conf)
        ConfigManager.add_package("pkg1", "url1")
        conf2 = ConfigManager.load_config()
        self.assertIn("pkg1", conf2["packages"])
        self.assertEqual(conf2["packages"]["pkg1"]["join_url"], "url1")
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
        ConfigManager.add_package("pkg1", "url1")
        ConfigManager.add_package("pkg1", "url2")
        conf = ConfigManager.load_config()
        self.assertEqual(len(conf["packages"]), 1)
        self.assertEqual(conf["packages"]["pkg1"]["join_url"], "url2")

    def test_process_running(self):
        env = MockEnv()
        env.running_processes.add("pkg1")
        ConfigManager.add_package("pkg1", "url1")
        monitor = Monitor(env)
        monitor.run_once()
        self.assertEqual(monitor.state["pkg1"]["status"], "RUNNING")
        self.assertEqual(len(env.launch_history), 0, f"History: {env.launch_history}")

    def test_launch_success(self):
        env = MockEnv()
        ConfigManager.add_package("pkg1", "url1")
        monitor = Monitor(env)
        monitor.run_once()
        self.assertEqual(monitor.state["pkg1"]["status"], "LAUNCHED")
        self.assertEqual(len(env.launch_history), 1)

    def test_launch_fail_and_max_retry(self):
        env = MockEnv()
        env.launch_returns = False
        ConfigManager.add_package("pkg1", "url1")
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
        env.launch_returns = {"fail_pkg": False, "ok_pkg": True}
        ConfigManager.add_package("fail_pkg", "url1")
        ConfigManager.add_package("ok_pkg", "url2")
        monitor = Monitor(env)
        
        monitor.run_once()
        self.assertEqual(monitor.state["fail_pkg"]["status"], "LAUNCH_ERROR")
        self.assertEqual(monitor.state["ok_pkg"]["status"], "LAUNCHED")

    @patch('os.kill')
    def test_duplicate_monitor_lock(self, mock_kill):
        # First acquire should succeed
        self.assertTrue(rejoin_core.acquire_lock())
        
        # Simulate another process holds lock (os.kill succeeds)
        with open(rejoin_core.LOCK_FILE, "w") as f:
            f.write("99999\n")
        
        mock_kill.return_value = None
        self.assertFalse(rejoin_core.acquire_lock())
        
        # Simulate dead process (os.kill raises OSError)
        mock_kill.side_effect = OSError
        self.assertTrue(rejoin_core.acquire_lock())
        rejoin_core.release_lock()

if __name__ == '__main__':
    unittest.main()
