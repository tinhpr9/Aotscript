#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestRuntimeRecoveryAndSetupGuards(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-runtime-recovery-"))
        self.state_dir = self.tmp / "state" / "aotscript" / "setup-driver"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_bash_sourced(self, script_body: str, env_override: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["AOTSCRIPT_SETUP_SOURCE_ONLY"] = "1"
        env["XDG_STATE_HOME"] = str(self.tmp / "state")
        env["HOME"] = str(self.tmp / "home")
        if env_override:
            env.update(env_override)
        full_cmd = f"""
        set -eu
        die() {{ echo "DIE:$*" >&2; exit 1; }}
        warn() {{ echo "WARN:$*" >&2; }}
        ok() {{ echo "OK:$*" >&2; }}
        emit() {{ echo "EMIT:$*" >&2; }}
        state_write() {{ :; }}
        state_read() {{ echo "no"; }}
        source "{REPO_ROOT / 'setup.sh'}"
        {script_body}
        """
        return subprocess.run(
            ["bash", "-c", full_cmd],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def test_source_only_guard_does_not_execute_main(self) -> None:
        # Sourcing setup.sh with AOTSCRIPT_SETUP_SOURCE_ONLY=1 must define functions without running main
        res = self._run_bash_sourced("type host_fingerprint")
        self.assertEqual(0, res.returncode, f"Failed to source: {res.stderr}")
        self.assertIn("host_fingerprint is a function", res.stdout)

    def test_strong_device_signal_produces_stable_fingerprint(self) -> None:
        # When strong signals are simulated
        cmd = """
        su() {
          case "$*" in
            *"settings get secure android_id"*) echo "android_12345" ;;
            *"getprop ro.boot.serialno"*) echo "serial_abc" ;;
            *) echo "" ;;
          esac
        }
        host_fingerprint
        """
        res = self._run_bash_sourced(cmd)
        self.assertEqual(0, res.returncode, f"Strong signal failed: {res.stderr}")
        fp = res.stdout.strip()
        expected = hashlib.sha256(b"android_12345|serial_abc").hexdigest()
        self.assertEqual(expected, fp)

    def test_empty_host_token_does_not_produce_constant_hash(self) -> None:
        # If host_token is empty, it must NOT produce sha256("token:")
        constant_hash = hashlib.sha256(b"token:").hexdigest()
        token_file = self.state_dir / "host_token"
        token_file.touch()
        cmd = """
        su() { return 1; }
        getprop() { return 1; }
        host_fingerprint
        """
        res = self._run_bash_sourced(cmd)
        self.assertEqual(0, res.returncode, f"Empty token failed: {res.stderr}")
        fp = res.stdout.strip()
        self.assertNotEqual(constant_hash, fp, "Empty host token produced constant hash 'token:'")
        self.assertTrue(bool(re.fullmatch(r"[0-9a-f]{64}", fp)))
        # Verify host_token was regenerated non-empty
        token_content = token_file.read_text(encoding="utf-8").strip()
        self.assertGreaterEqual(len(token_content), 8)

    def test_malformed_partial_host_token_recovers_safely(self) -> None:
        # If host_token is shorter than 8 chars (partial write)
        token_file = self.state_dir / "host_token"
        token_file.write_text("abc\n", encoding="utf-8")
        cmd = """
        su() { return 1; }
        getprop() { return 1; }
        host_fingerprint
        """
        res = self._run_bash_sourced(cmd)
        self.assertEqual(0, res.returncode, f"Partial token recovery failed: {res.stderr}")
        fp = res.stdout.strip()
        self.assertTrue(bool(re.fullmatch(r"[0-9a-f]{64}", fp)))
        token_content = token_file.read_text(encoding="utf-8").strip()
        self.assertGreaterEqual(len(token_content), 8)

    def test_two_cloud_instances_with_same_build_props_have_distinct_tokens(self) -> None:
        # Two fresh instances without strong device signals generate distinct tokens
        tmp2 = pathlib.Path(tempfile.mkdtemp(prefix="test-cloud-instance-2-"))
        try:
            cmd = """
            su() { return 1; }
            getprop() { echo "V2206"; }
            host_fingerprint
            """
            res1 = self._run_bash_sourced(cmd)
            env2 = {
                "AOTSCRIPT_SETUP_SOURCE_ONLY": "1",
                "XDG_STATE_HOME": str(tmp2 / "state"),
                "HOME": str(tmp2 / "home"),
            }
            full_cmd2 = f"""
            set -eu
            die() {{ echo "DIE:$*" >&2; exit 1; }}
            source "{REPO_ROOT / 'setup.sh'}"
            su() {{ return 1; }}
            getprop() {{ echo "V2206"; }}
            host_fingerprint
            """
            res2 = subprocess.run(["bash", "-c", full_cmd2], capture_output=True, text=True, check=False, env=env2)
            fp1 = res1.stdout.strip()
            fp2 = res2.stdout.strip()
            self.assertNotEqual(fp1, fp2, "Two distinct cloud instances must not share generic build property fingerprint")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

    def test_termux_boot_promotes_apk_part_to_apk_before_install(self) -> None:
        setup_m166 = (REPO_ROOT / "setup-m166.sh").read_text(encoding="utf-8")
        # Ensure mv -f "$apk_part" "$apk" occurs before pm install -r "$apk"
        mv_idx = setup_m166.find('mv -f "$apk_part" "$apk"')
        pm_idx = setup_m166.find("pm install -r '$apk'")
        self.assertNotEqual(-1, mv_idx, "mv -f $apk_part $apk not found in setup-m166.sh")
        self.assertNotEqual(-1, pm_idx, "pm install not found in setup-m166.sh")
        self.assertLess(mv_idx, pm_idx, "mv -f $apk_part $apk must execute before pm install")

    def test_readiness_polling_without_seq(self) -> None:
        setup_m166 = (REPO_ROOT / "setup-m166.sh").read_text(encoding="utf-8")
        # Ensure no 'seq' call in runtime polling loop
        self.assertNotIn("seq 1 15", setup_m166)
        self.assertIn("while [ \"$poll_i\" -le 15 ]; do", setup_m166)
        self.assertIn("poll_i=$((poll_i + 1))", setup_m166)

    def test_readiness_polling_succeeds_after_short_delay(self) -> None:
        # Simulate polling loop where runtime becomes ready on 3rd attempt
        poll_test = """
        fake_runtime() {
          count_file="/tmp/test_poll_count_$$"
          count=0
          [ -f "$count_file" ] && count="$(cat "$count_file")"
          count=$((count + 1))
          echo "$count" > "$count_file"
          if [ "$count" -ge 3 ]; then
            echo "AOT_CONFIG=OK"
            echo "PIDS=12345"
            return 0
          else
            echo "AOT_CONFIG=OK"
            echo "PIDS=NONE"
            return 0
          fi
        }
        RUNTIME_STATUS=""
        runtime_ready=false
        poll_i=1
        while [ "$poll_i" -le 15 ]; do
          RUNTIME_STATUS="$(fake_runtime)" || true
          if printf '%s\n' "$RUNTIME_STATUS" | grep -qx 'AOT_CONFIG=OK' &&
             printf '%s\n' "$RUNTIME_STATUS" | grep -Eq '^PIDS=[0-9]+(,[0-9]+)*$'; then
            runtime_ready=true
            break
          fi
          poll_i=$((poll_i + 1))
        done
        rm -f "/tmp/test_poll_count_$$"
        [ "$runtime_ready" = true ] || exit 1
        printf '%s\n' "$RUNTIME_STATUS"
        """
        res = subprocess.run(["bash", "-c", poll_test], capture_output=True, text=True, check=False)
        self.assertEqual(0, res.returncode)
        self.assertIn("AOT_CONFIG=OK", res.stdout)
        self.assertIn("PIDS=12345", res.stdout)

    def test_malformed_runtime_status_after_polling_fails_closed(self) -> None:
        # Simulate polling where runtime outputs malformed status (e.g. PIDS=NONE) across all iterations
        poll_test = """
        fake_runtime() {
          echo "AOT_CONFIG=OK"
          echo "PIDS=NONE"
          return 0
        }
        RUNTIME_STATUS=""
        runtime_ready=false
        poll_i=1
        while [ "$poll_i" -le 3 ]; do
          RUNTIME_STATUS="$(fake_runtime)" || true
          if printf '%s\n' "$RUNTIME_STATUS" | grep -qx 'AOT_CONFIG=OK' &&
             printf '%s\n' "$RUNTIME_STATUS" | grep -Eq '^PIDS=[0-9]+(,[0-9]+)*$'; then
            runtime_ready=true
            break
          fi
          poll_i=$((poll_i + 1))
        done
        if [ "$runtime_ready" = true ]; then
          echo "UNEXPECTED_PASS"
          exit 0
        else
          echo "PROPERLY_FAILED_CLOSED"
          exit 42
        fi
        """
        res = subprocess.run(["bash", "-c", poll_test], capture_output=True, text=True, check=False)
        self.assertEqual(42, res.returncode)
        self.assertIn("PROPERLY_FAILED_CLOSED", res.stdout)


if __name__ == "__main__":
    unittest.main()
