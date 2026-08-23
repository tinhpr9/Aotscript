import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TERMUXBOOT = REPO_ROOT / "Termuxboot"


class TermuxbootTestFixture:
    def __init__(self, curl_mode="ok"):
        self.temp = tempfile.TemporaryDirectory(prefix="termuxboot-test-")
        self.root = pathlib.Path(self.temp.name)
        self.sdcard = self.root / "sdcard" / "Download"
        self.shouko = self.sdcard / "Shouko"
        self.home = self.root / "home"
        self.bin_dir = self.root / "bin"
        self.aot_runtime = self.home / ".aot-group-control"
        
        self.sdcard.mkdir(parents=True)
        self.shouko.mkdir(parents=True)
        self.home.mkdir(parents=True)
        self.bin_dir.mkdir(parents=True)
        self.aot_runtime.mkdir(parents=True)

        self.id_file = self.shouko / "device_id.txt"
        self.group_file = self.shouko / "device_group.txt"
        self.config_file = self.shouko / "agent_config.json"
        self.aot_config_file = self.shouko / "aot_group_config.json"
        self.agent_file = self.sdcard / "Agent_Core.py"
        self.agent_tmp = self.sdcard / "Agent_Core.py.tmp"
        self.agent_log = self.sdcard / "Agent_Log.txt"
        self.aot_log = self.sdcard / "AOT_Group_Control.log"
        self.state_file = self.home / ".local" / "state" / "aotscript" / "mprovision.json"
        self.state_file.parent.mkdir(parents=True)
        self.state_file.write_text(json.dumps({"phase": "complete"}), encoding="utf-8")

        self.id_file.write_text("m116\n", encoding="utf-8")
        self.group_file.write_text("NOVA\n", encoding="utf-8")
        self.config_file.write_text(json.dumps({
            "worker_report_url": "https://worker.example.invalid/report",
            "agent_report_secret": "secret123"
        }), encoding="utf-8")

        self._setup_stub_curl(curl_mode)

    def _setup_stub_curl(self, mode):
        self.curl_stub = self.bin_dir / "curl"
        script = f"""#!/usr/bin/env bash
MODE="{mode}"
OUT=""
for ((i=1; i<=$#; i++)); do
  if [ "${{!i}}" = "-o" ]; then
    j=$((i+1))
    OUT="${{!j}}"
  fi
done

if [ "$MODE" = "429" ] || [ "$MODE" = "503" ] || [ "$MODE" = "fail" ]; then
  exit 22
fi

if [ "$MODE" = "bad_syntax" ]; then
  [ -n "$OUT" ] && printf "def invalid_syntax_here( :\\n" > "$OUT"
  exit 0
fi

if [ "$MODE" = "new_agent" ]; then
  if [ -n "$OUT" ]; then
    printf 'import time\\nprint("NEW_AGENT_RUNNING", flush=True)\\ntime.sleep(30)\\n' > "$OUT"
  fi
  exit 0
fi

# default ok
if [ -n "$OUT" ]; then
  if [[ "$OUT" == *"bootstrap_launcher.py"* ]] || [[ "$OUT" == *"bootstrap.py"* ]]; then
    printf 'import sys\\nif len(sys.argv) > 1 and sys.argv[1] == "self-test":\\n    sys.exit(0)\\n' > "$OUT"
  else
    printf 'import time\\nprint("STUB_AGENT_RUNNING", flush=True)\\ntime.sleep(30)\\n' > "$OUT"
  fi
fi
exit 0
"""
        self.curl_stub.write_text(script, encoding="utf-8")
        self.curl_stub.chmod(0o755)

    def run_boot(self, extra_env=None):
        env = os.environ.copy()
        env.update({
            "AOTSCRIPT_PYTHON": sys.executable,
            "AOTSCRIPT_CURL": str(self.curl_stub),
            "AOTSCRIPT_AGENT": str(self.agent_file),
            "AOTSCRIPT_AGENT_TEMP": str(self.agent_tmp),
            "AOTSCRIPT_LOG": str(self.agent_log),
            "AOTSCRIPT_ID_FILE": str(self.id_file),
            "AOTSCRIPT_GROUP_FILE": str(self.group_file),
            "AOTSCRIPT_CONFIG_FILE": str(self.config_file),
            "AOTSCRIPT_STATE_FILE": str(self.state_file),
            "AOTSCRIPT_AOT_CONFIG_FILE": str(self.aot_config_file),
            "AOTSCRIPT_AOT_RUNTIME_DIR": str(self.aot_runtime),
            "AOTSCRIPT_AOT_LOG": str(self.aot_log),
            "AOTSCRIPT_LOCK_DIR": str(self.home / ".cache" / "aotscript-agent-start.lock"),
            "AOTSCRIPT_BOOT_TEST_MODE": "1",
            "HOME": str(self.home),
        })
        if extra_env:
            env.update(extra_env)

        proc = subprocess.run(
            ["bash", str(TERMUXBOOT)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        return proc

    def cleanup(self):
        # Kill any agent spawned in background
        if self.agent_log.exists():
            log_content = self.agent_log.read_text(encoding="utf-8", errors="ignore")
            for line in log_content.splitlines():
                if "PID=" in line:
                    try:
                        pid = int(line.split("PID=")[1].split()[0])
                        os.kill(pid, signal.SIGKILL)
                    except Exception:
                        pass
                elif "[PID]" in line:
                    try:
                        pid = int(line.split("[PID]")[1].split()[0])
                        os.kill(pid, signal.SIGKILL)
                    except Exception:
                        pass
        for entry in pathlib.Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                cmdline = (entry / "cmdline").read_bytes().decode("utf-8", errors="replace")
                if str(self.root) in cmdline and int(entry.name) != os.getpid():
                    os.kill(int(entry.name), signal.SIGKILL)
            except Exception:
                pass
        self.temp.cleanup()


class TermuxbootFallbackTests(unittest.TestCase):
    def test_raw_429_with_valid_local_agent_starts(self):
        fixture = TermuxbootTestFixture(curl_mode="429")
        try:
            fixture.agent_file.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            res = fixture.run_boot()
            self.assertEqual(res.returncode, 0, res.stderr)
            log = fixture.agent_log.read_text(encoding="utf-8")
            self.assertIn("Không tải được Agent", log)
            self.assertIn("Dùng Agent_Core.py cục bộ hiện tại", log)
            self.assertIn("Đã khởi động Agent", log)
        finally:
            fixture.cleanup()

    def test_raw_503_with_valid_local_agent_starts(self):
        fixture = TermuxbootTestFixture(curl_mode="503")
        try:
            fixture.agent_file.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            res = fixture.run_boot()
            self.assertEqual(res.returncode, 0, res.stderr)
            log = fixture.agent_log.read_text(encoding="utf-8")
            self.assertIn("Không tải được Agent", log)
            self.assertIn("Dùng Agent_Core.py cục bộ hiện tại", log)
            self.assertIn("Đã khởi động Agent", log)
        finally:
            fixture.cleanup()

    def test_raw_fail_with_valid_bak_agent_restores_and_starts(self):
        fixture = TermuxbootTestFixture(curl_mode="fail")
        try:
            bak_file = fixture.agent_file.with_name("Agent_Core.py.bak")
            bak_file.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            res = fixture.run_boot()
            self.assertEqual(res.returncode, 0, res.stderr)
            log = fixture.agent_log.read_text(encoding="utf-8")
            self.assertIn("Khôi phục và dùng Agent_Core.py.bak", log)
            self.assertIn("Đã khởi động Agent", log)
            self.assertTrue(fixture.agent_file.exists())
        finally:
            fixture.cleanup()

    def test_download_fail_and_missing_local_fails_closed(self):
        fixture = TermuxbootTestFixture(curl_mode="429")
        try:
            res = fixture.run_boot()
            self.assertEqual(res.returncode, 1)
            log = fixture.agent_log.read_text(encoding="utf-8")
            self.assertIn("không có bản Agent cục bộ hợp lệ", log)
        finally:
            fixture.cleanup()

    def test_download_fail_and_corrupt_local_fails_closed(self):
        fixture = TermuxbootTestFixture(curl_mode="503")
        try:
            fixture.agent_file.write_text("def broken( :\n", encoding="utf-8")
            res = fixture.run_boot()
            self.assertEqual(res.returncode, 1)
            log = fixture.agent_log.read_text(encoding="utf-8")
            self.assertIn("không có bản Agent cục bộ hợp lệ", log)
        finally:
            fixture.cleanup()

    def test_successful_download_updates_and_starts(self):
        fixture = TermuxbootTestFixture(curl_mode="new_agent")
        try:
            fixture.agent_file.write_text("import time\nprint('OLD_AGENT')\ntime.sleep(30)\n", encoding="utf-8")
            res = fixture.run_boot()
            self.assertEqual(res.returncode, 0, res.stderr)
            log = fixture.agent_log.read_text(encoding="utf-8")
            self.assertIn("Đã thay Agent bằng bản hợp lệ mới nhất", log)
            self.assertIn("Đã khởi động Agent", log)
            bak_file = fixture.agent_file.with_name("Agent_Core.py.bak")
            self.assertTrue(bak_file.exists())
            self.assertIn("OLD_AGENT", bak_file.read_text(encoding="utf-8"))
            self.assertIn("NEW_AGENT_RUNNING", fixture.agent_file.read_text(encoding="utf-8"))
        finally:
            fixture.cleanup()

    def test_aot_group_control_raw_fail_with_valid_local_starts(self):
        fixture = TermuxbootTestFixture(curl_mode="429")
        try:
            fixture.aot_config_file.write_text(json.dumps({
                "enabled": True,
                "device_id": "m116"
            }), encoding="utf-8")
            (fixture.aot_runtime / "bootstrap_launcher.py").write_text("import sys\nprint('LAUNCHER_START')\n", encoding="utf-8")
            (fixture.aot_runtime / "bootstrap.py").write_text("import sys\nif len(sys.argv) > 1 and sys.argv[1] == 'self-test': sys.exit(0)\n", encoding="utf-8")
            
            fixture.agent_file.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            res = fixture.run_boot()
            self.assertEqual(res.returncode, 0, res.stderr)
            log = fixture.agent_log.read_text(encoding="utf-8")
            self.assertIn("Dùng AOT runtime cục bộ hiện tại", log)
            self.assertIn("AOT Group Control auto-start đã xử lý", log)
        finally:
            fixture.cleanup()

    def test_aot_group_control_raw_fail_with_corrupt_local_fails_closed(self):
        fixture = TermuxbootTestFixture(curl_mode="429")
        try:
            fixture.aot_config_file.write_text(json.dumps({
                "enabled": True,
                "device_id": "m116"
            }), encoding="utf-8")
            (fixture.aot_runtime / "bootstrap_launcher.py").write_text("def broken( :\n", encoding="utf-8")
            (fixture.aot_runtime / "bootstrap.py").write_text("def broken( :\n", encoding="utf-8")
            
            fixture.agent_file.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            res = fixture.run_boot()
            log = fixture.agent_log.read_text(encoding="utf-8")
            self.assertIn("Không có AOT runtime cục bộ hợp lệ", log)
        finally:
            fixture.cleanup()


if __name__ == "__main__":
    unittest.main()
