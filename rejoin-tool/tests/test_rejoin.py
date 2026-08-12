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
        self.log_calls = []

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
        self.log_calls.append(msg)

class TestRejoin(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_runtime_dir = tempfile.mkdtemp()
        self.old_config_dir = rejoin_core.CONFIG_DIR
        self.old_config_file = rejoin_core.CONFIG_FILE
        self.old_runtime_dir = getattr(rejoin_core, 'RUNTIME_DIR', None)
        self.old_lock_file = rejoin_core.LOCK_FILE
        self.old_log_file = rejoin_core.LOG_FILE

        rejoin_core.CONFIG_DIR = self.test_dir
        rejoin_core.CONFIG_FILE = os.path.join(self.test_dir, "config.json")
        rejoin_core.RUNTIME_DIR = self.test_runtime_dir
        rejoin_core.LOCK_FILE = os.path.join(self.test_runtime_dir, "monitor.lock")
        rejoin_core.LOG_FILE = os.path.join(self.test_dir, "rejoin.log")

        if rejoin_core._lock_fd is not None:
            rejoin_core.release_lock()

    def tearDown(self):
        rejoin_core.release_lock()
        rejoin_core.CONFIG_DIR = self.old_config_dir
        rejoin_core.CONFIG_FILE = self.old_config_file
        if self.old_runtime_dir is not None:
            rejoin_core.RUNTIME_DIR = self.old_runtime_dir
        rejoin_core.LOCK_FILE = self.old_lock_file
        rejoin_core.LOG_FILE = self.old_log_file

        for d in [self.test_dir, self.test_runtime_dir]:
            if os.path.exists(d):
                for f in os.listdir(d):
                    os.remove(os.path.join(d, f))
                os.rmdir(d)

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
sys.path.append({repr(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))})
import rejoin_core
rejoin_core.CONFIG_DIR = {repr(self.test_dir)}
rejoin_core.RUNTIME_DIR = {repr(self.test_runtime_dir)}
rejoin_core.LOCK_FILE = {repr(rejoin_core.LOCK_FILE)}
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

    def test_lock_handoff_and_stable_path(self):
        # Ensure lock doesn't exist initially to simulate clean start
        if os.path.exists(rejoin_core.LOCK_FILE):
            os.remove(rejoin_core.LOCK_FILE)

        # Initial acquire
        self.assertTrue(rejoin_core.acquire_lock())

        # Verify file exists and grab its inode
        self.assertTrue(os.path.exists(rejoin_core.LOCK_FILE))
        inode1 = os.stat(rejoin_core.LOCK_FILE).st_ino

        # Release lock (should not remove file, but clear contents)
        rejoin_core.release_lock()

        # File should still exist
        self.assertTrue(os.path.exists(rejoin_core.LOCK_FILE))
        # Contents should be empty
        with open(rejoin_core.LOCK_FILE, "r") as f:
            self.assertEqual(f.read().strip(), "")

        # Second acquire (simulating another daemon taking over)
        self.assertTrue(rejoin_core.acquire_lock())

        # File should still exist and inode must remain exactly the same
        self.assertTrue(os.path.exists(rejoin_core.LOCK_FILE))
        inode2 = os.stat(rejoin_core.LOCK_FILE).st_ino
        self.assertEqual(inode1, inode2)

        rejoin_core.release_lock()

    def test_get_pid_empty_file(self):
        if getattr(rejoin_core, 'RUNTIME_DIR', None) and not os.path.exists(rejoin_core.RUNTIME_DIR):
            os.makedirs(rejoin_core.RUNTIME_DIR, exist_ok=True)
        # Create an empty lock file
        with open(rejoin_core.LOCK_FILE, "w") as f:
            f.write("")
        import rejoin_cli
        old_cli_lock = getattr(rejoin_cli, 'LOCK_FILE', None)
        rejoin_cli.LOCK_FILE = rejoin_core.LOCK_FILE
        try:
            self.assertIsNone(rejoin_cli.get_pid())
        finally:
            if old_cli_lock:
                rejoin_cli.LOCK_FILE = old_cli_lock

    def test_url_validation(self):
        self.assertTrue(ConfigManager.add_package("pkg.val", "roblox://placeID=123"))
        self.assertTrue(ConfigManager.add_package("pkg.val", "123456"))
        self.assertTrue(ConfigManager.add_package("pkg.val", "https://roblox.com/games/123"))
        self.assertTrue(ConfigManager.add_package("pkg.val", "http://www.roblox.com/test"))

        self.assertFalse(ConfigManager.add_package("pkg.val", "https://evilroblox.com"))
        self.assertFalse(ConfigManager.add_package("pkg.val", "http://roblox.com.evil.example"))
        self.assertFalse(ConfigManager.add_package("pkg.val", "not_a_url"))

    def test_log_redaction(self):
        env = MockEnv()
        ConfigManager.add_package("pkg1", "roblox://placeID=123&secret=abc")
        monitor = Monitor(env)
        monitor.run_once()
        for call in env.log_calls:
            self.assertNotIn("secret=abc", call)

    def test_release_lock_exception_safety(self):
        self.assertTrue(rejoin_core.acquire_lock())

        with patch('os.ftruncate', side_effect=OSError("Disk full")):
            rejoin_core.release_lock()

        self.assertIsNone(rejoin_core._lock_fd)
        self.assertTrue(rejoin_core.acquire_lock())
        rejoin_core.release_lock()

    def test_stop_event_honored(self):
        env = MockEnv()
        ConfigManager.add_package("pkg1", "123")
        ConfigManager.add_package("pkg2", "456")

        stop_flag = True
        monitor = Monitor(env, lambda: stop_flag)
        monitor.run_once()

        self.assertEqual(len(monitor.state), 0)

    @patch('os.kill')
    @patch('rejoin_cli.is_rejoin_daemon')
    @patch('rejoin_cli.get_pid')
    @patch('builtins.print')
    def test_stop_daemon_wait(self, mock_print, mock_get_pid, mock_is_rejoin, mock_kill):
        import rejoin_cli
        mock_get_pid.return_value = 123

        def fake_kill(pid, sig):
            if sig == 0:
                raise OSError()
            return
        mock_kill.side_effect = fake_kill

        with patch('time.sleep', return_value=None):
            rejoin_cli.stop_daemon()

        mock_kill.assert_any_call(123, 15)
        mock_print.assert_any_call("Stopped.")

        mock_print.reset_mock()
        mock_kill.reset_mock()
        mock_kill.side_effect = None
        mock_is_rejoin.return_value = False

        with patch('time.sleep', return_value=None):
            rejoin_cli.stop_daemon()

        mock_print.assert_any_call("Stopped.")

        mock_print.reset_mock()
        mock_kill.reset_mock()
        mock_is_rejoin.return_value = True
        with patch('time.sleep', return_value=None):
            rejoin_cli.stop_daemon()

        mock_print.assert_any_call("Failed to stop: shutdown still pending.")

    def test_missing_packages_safe(self):
        with open(rejoin_core.CONFIG_FILE, "w") as f:
            f.write("{}")

        ConfigManager.remove_package("pkg1")
        ConfigManager.set_package_enabled("pkg1", True)

    def test_check_interval_validation(self):
        env = MockEnv()
        monitor = Monitor(env, lambda: False)

        def run_with_interval(val):
            env.sleep_history.clear()
            conf = {"settings": {"check_interval_sec": val}}

            def fake_load_config(*args, **kwargs):
                fake_load_config.calls += 1
                if fake_load_config.calls > 2:
                    raise StopIteration("STOP")
                return conf
            fake_load_config.calls = 0

            with patch.object(ConfigManager, 'load_config', side_effect=fake_load_config):
                try:
                    monitor.run_loop()
                except StopIteration:
                    pass
            return len(env.sleep_history)

        self.assertEqual(run_with_interval("5"), 5)
        self.assertEqual(run_with_interval(5.8), 5)
        self.assertEqual(run_with_interval("abc"), 30)
        self.assertEqual(run_with_interval(0), 1)
        self.assertEqual(run_with_interval(-10), 1)
        self.assertEqual(run_with_interval(None), 30)

    @patch('builtins.print')
    def test_malformed_config_mutation_failsafe(self, mock_print):
        with open(rejoin_core.CONFIG_FILE, "w") as f:
            f.write("{ invalid json ")

        self.assertFalse(ConfigManager.add_package("pkg1", "123"))
        mock_print.assert_any_call("Error: config.json is malformed. Please fix or remove it.")

        self.assertFalse(ConfigManager.remove_package("pkg1"))
        self.assertFalse(ConfigManager.set_package_enabled("pkg1", True))

        with open(rejoin_core.CONFIG_FILE, "r") as f:
            self.assertEqual(f.read(), "{ invalid json ")

    def test_mutation_returns_accurate_status(self):
        ConfigManager.add_package("pkg1", "123")

        # valid package -> success
        self.assertTrue(ConfigManager.set_package_enabled("pkg1", False))
        self.assertTrue(ConfigManager.remove_package("pkg1"))

        # missing package -> failure
        self.assertFalse(ConfigManager.set_package_enabled("pkg_missing", True))
        self.assertFalse(ConfigManager.remove_package("pkg_missing"))

    def test_malformed_config_monitor_fails_closed(self):
        with open(rejoin_core.CONFIG_FILE, "w") as f:
            f.write("{ invalid json ")

        env = MockEnv()
        monitor = Monitor(env)

        with self.assertRaises(ValueError):
            monitor.run_once()

        monitor.run_loop()

        with open(rejoin_core.CONFIG_FILE, "r") as f:
            self.assertEqual(f.read(), "{ invalid json ")

        self.assertEqual(len(monitor.state), 0)

        os.remove(rejoin_core.CONFIG_FILE)
        monitor.run_once()
        self.assertTrue(os.path.exists(rejoin_core.CONFIG_FILE))

    def test_schema_validation(self):
        # wrong container types
        with open(rejoin_core.CONFIG_FILE, "w") as f:
            f.write("[]")
        with self.assertRaises(ValueError):
            ConfigManager.load_config(strict=True)

        with open(rejoin_core.CONFIG_FILE, "w") as f:
            f.write('{"packages": []}')
        with self.assertRaises(ValueError):
            ConfigManager.load_config(strict=True)

        with open(rejoin_core.CONFIG_FILE, "w") as f:
            f.write('{"packages": {"pkg1": []}}')
        with self.assertRaises(ValueError):
            ConfigManager.load_config(strict=True)

        with open(rejoin_core.CONFIG_FILE, "w") as f:
            f.write('{"settings": []}')
        with self.assertRaises(ValueError):
            ConfigManager.load_config(strict=True)

        with open(rejoin_core.CONFIG_FILE, "w") as f:
            f.write('{"packages": {"pkg1": {"enabled": "yes"}}}')
        with self.assertRaises(ValueError):
            ConfigManager.load_config(strict=True)

        with open(rejoin_core.CONFIG_FILE, "w") as f:
            f.write('{"packages": {"pkg1": {"join_url": 123}}}')
        with self.assertRaises(ValueError):
            ConfigManager.load_config(strict=True)

    def test_package_schema_validation(self):
        def check_malformed(json_str):
            with open(rejoin_core.CONFIG_FILE, "w") as f:
                f.write(json_str)
            with self.assertRaises(ValueError):
                ConfigManager.load_config(strict=True)
            with open(rejoin_core.CONFIG_FILE, "r") as f:
                self.assertEqual(f.read(), json_str)

        def check_valid(json_str):
            with open(rejoin_core.CONFIG_FILE, "w") as f:
                f.write(json_str)
            ConfigManager.load_config(strict=True)

        # enabled + missing join_url
        check_malformed('{"packages": {"pkg1": {"enabled": true}}}')

        # enabled + empty join_url
        check_malformed('{"packages": {"pkg1": {"enabled": true, "join_url": ""}}}')

        # enabled + whitespace join_url
        check_malformed('{"packages": {"pkg1": {"enabled": true, "join_url": "   "}}}')

        # enabled + invalid join target
        check_malformed('{"packages": {"pkg1": {"enabled": true, "join_url": "http://evil.com"}}}')

        # invalid package key
        check_malformed('{"packages": {"pkg1!@#": {"enabled": true, "join_url": "123"}}}')

        # valid numeric ID
        check_valid('{"packages": {"pkg1": {"enabled": true, "join_url": "123"}}}')

        # valid roblox:// target
        check_valid('{"packages": {"pkg1": {"enabled": true, "join_url": "roblox://test"}}}')

        # valid roblox.com URL
        check_valid('{"packages": {"pkg1": {"enabled": true, "join_url": "https://www.roblox.com/games/123"}}}')

        # disabled package behavior (allowed missing/invalid/empty join_url)
        check_valid('{"packages": {"pkg1": {"enabled": false}}}')
        check_valid('{"packages": {"pkg1": {"enabled": false, "join_url": ""}}}')
        check_valid('{"packages": {"pkg1": {"enabled": false, "join_url": "invalid string"}}}')
        check_malformed('{"packages": {"pkg1": {"enabled": false, "join_url": 123}}}') # Still fails on non-string

    @patch('rejoin_cli.get_pid')
    @patch('subprocess.Popen')
    @patch('builtins.print')
    def test_start_daemon_refuses_malformed(self, mock_print, mock_popen, mock_get_pid):
        mock_get_pid.return_value = None
        with open(rejoin_core.CONFIG_FILE, "w") as f:
            f.write("{ invalid json ")

        import rejoin_cli
        rejoin_cli.start_daemon()

        mock_popen.assert_not_called()
        mock_print.assert_any_call("FAILED to start monitor.")

    @patch('builtins.print')
    def test_operational_cli_reads_fail_closed(self, mock_print):
        with open(rejoin_core.CONFIG_FILE, "w") as f:
            f.write("{ invalid json ")

        import rejoin_cli
        rejoin_cli.print_status()
        mock_print.assert_any_call("Error: config.json is malformed. Please fix or remove it.")

        from rejoin_core import discover_roblox_packages
        self.assertEqual(discover_roblox_packages(), [])

    def test_all_numeric_settings_safe(self):
        env = MockEnv()
        monitor = Monitor(env, lambda: False)

        conf = {
            "settings": {
                "check_interval_sec": "invalid",
                "max_retries": -5,
                "retry_delay_sec": "-2.5",
                "cooldown_after_success_sec": None,
                "open_delay_sec": {},
                "delay_between_packages_sec": "5.5"
            },
            "packages": {
                "pkg1": {"enabled": True, "join_url": "123"}
            }
        }

        def fake_load_config(*args, **kwargs):
            fake_load_config.calls += 1
            if fake_load_config.calls > 2:
                raise StopIteration("STOP")
            return conf
        fake_load_config.calls = 0

        with patch.object(ConfigManager, 'load_config', side_effect=fake_load_config):
            try:
                monitor.run_loop()
            except StopIteration:
                pass

    def test_finite_numeric_settings(self):
        env = MockEnv()
        monitor = Monitor(env, lambda: False)
        import math

        # Test nan/inf/booleans
        conf = {
            "settings": {
                "check_interval_sec": float('inf'),
                "max_retries": float('nan'),
                "retry_delay_sec": float('-inf'),
                "cooldown_after_success_sec": True,
                "open_delay_sec": False,
                "delay_between_packages_sec": "inf"
            },
            "packages": {
                "pkg1": {"enabled": True, "join_url": "123"}
            }
        }
        def fake_load_config(*args, **kwargs):
            fake_load_config.calls += 1
            if fake_load_config.calls > 2:
                raise StopIteration("STOP")
            return conf
        fake_load_config.calls = 0

        with patch.object(ConfigManager, 'load_config', side_effect=fake_load_config):
            try:
                monitor.run_loop()
            except StopIteration:
                pass

    def test_disabled_package_mutation(self):
        # 1. Invalid URL in disabled package is fine
        ConfigManager.ensure_dir()
        with open(rejoin_core.CONFIG_FILE, "w") as f:
            f.write('{"packages": {"pkg1": {"enabled": false, "join_url": ""}}}')

        # 2. Attempt to enable it should fail
        self.assertFalse(ConfigManager.set_package_enabled("pkg1", True))

        # 3. Check JSON preservation
        with open(rejoin_core.CONFIG_FILE, "r") as f:
            self.assertEqual(f.read(), '{"packages": {"pkg1": {"enabled": false, "join_url": ""}}}')

    def test_non_bool_enabled(self):
        ConfigManager.ensure_dir()
        with open(rejoin_core.CONFIG_FILE, "w") as f:
            f.write('{"packages": {"pkg1": {"enabled": false, "join_url": "123"}}}')

        self.assertFalse(ConfigManager.set_package_enabled("pkg1", "yes"))
        with open(rejoin_core.CONFIG_FILE, "r") as f:
            self.assertEqual(f.read(), '{"packages": {"pkg1": {"enabled": false, "join_url": "123"}}}')

    def test_concurrent_mutations(self):
        ConfigManager.ensure_dir()
        with open(rejoin_core.CONFIG_FILE, "w") as f:
            f.write('{"packages": {}}')

        import multiprocessing
        def worker_add(pkg):
            import rejoin_core
            rejoin_core.ConfigManager.add_package(pkg, "123")

        p1 = multiprocessing.Process(target=worker_add, args=("pkg1",))
        p2 = multiprocessing.Process(target=worker_add, args=("pkg2",))

        p1.start()
        p2.start()
        p1.join()
        p2.join()

        config = ConfigManager.load_config(strict=True)
        self.assertIn("pkg1", config["packages"])
        self.assertIn("pkg2", config["packages"])

        def worker_conflict():
            import rejoin_core
            rejoin_core.ConfigManager.add_package("pkg3", "123")

        def worker_remove():
            import rejoin_core
            rejoin_core.ConfigManager.remove_package("pkg1")

        p3 = multiprocessing.Process(target=worker_conflict)
        p4 = multiprocessing.Process(target=worker_remove)
        p3.start()
        p4.start()
        p3.join()
        p4.join()

        config = ConfigManager.load_config(strict=True)
        self.assertIn("pkg3", config["packages"])
        self.assertNotIn("pkg1", config["packages"])

if __name__ == '__main__':
    unittest.main()
