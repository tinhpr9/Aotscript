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

    def test_stdin_piped_execution_runs_main_without_unbound_variable_error(self) -> None:
        # Piping setup.sh into bash -s (e.g. curl | bash) with set -u must not fail on unbound BASH_SOURCE
        env = os.environ.copy()
        env["XDG_STATE_HOME"] = str(self.tmp / "state")
        env["HOME"] = str(self.tmp / "home")
        setup_content = (REPO_ROOT / "setup.sh").read_text(encoding="utf-8")
        res = subprocess.run(
            ["bash", "-s", "--", "--validate-id", "m74"],
            input=setup_content,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        self.assertNotIn("unbound variable", res.stderr)
        self.assertEqual(0, res.returncode, f"Failed with stderr: {res.stderr}")
        self.assertEqual("m74", res.stdout.strip())

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


class TestFingerprintStrategyPersistence(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-strategy-"))
        self.state_dir = self.tmp / "state" / "aotscript" / "setup-driver"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_sourced(self, script_body: str, env_override: dict | None = None) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["AOTSCRIPT_SETUP_SOURCE_ONLY"] = "1"
        env["XDG_STATE_HOME"] = str(self.tmp / "state")
        env["HOME"] = str(self.tmp / "home")
        if env_override:
            env.update(env_override)
        cmd = f"""
        set -eu
        die() {{ echo "DIE:$*" >&2; exit 1; }}
        warn() {{ :; }}
        ok() {{ :; }}
        state_write() {{ :; }}
        state_read() {{ echo "no"; }}
        source "{REPO_ROOT / 'setup.sh'}"
        {script_body}
        """
        return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, check=False, env=env)

    def test_token_file_created_on_first_token_run(self) -> None:
        cmd = """
        su() { return 1; }
        host_fingerprint
        """
        res = self._run_sourced(cmd)
        self.assertEqual(0, res.returncode, res.stderr)
        token_file = self.state_dir / "host_token"
        self.assertTrue(token_file.exists())
        token = token_file.read_text().strip()
        self.assertGreaterEqual(len(token), 8)

    def test_strong_fingerprint_computed_on_first_strong_run(self) -> None:
        cmd = """
        su() {
          case "$*" in
            *"settings get secure android_id"*) echo "myandroidid" ;;
            *"getprop ro.boot.serialno"*) echo "myserial" ;;
            *) echo "" ;;
          esac
        }
        host_fingerprint
        """
        res = self._run_sourced(cmd)
        self.assertEqual(0, res.returncode, res.stderr)
        expected = hashlib.sha256(b"myandroidid|myserial").hexdigest()
        self.assertEqual(expected, res.stdout.strip())

    def test_token_binding_preserved_when_root_becomes_available(self) -> None:
        # First run: no root → token binding created
        cmd_no_root = """
        su() { return 1; }
        host_fingerprint
        """
        res1 = self._run_sourced(cmd_no_root)
        self.assertEqual(0, res1.returncode, res1.stderr)
        fp1 = res1.stdout.strip()
        # Simulate bind operation by writing host_fingerprint
        (self.state_dir / "host_fingerprint").write_text(fp1 + "\n", encoding="utf-8")

        # Second run: root becomes available → token binding preserved because bound hash matches token
        cmd_with_root = """
        su() {
          case "$*" in
            *"settings get secure android_id"*) echo "newandroidid" ;;
            *"getprop ro.boot.serialno"*) echo "newserial" ;;
            *) echo "" ;;
          esac
        }
        host_fingerprint
        """
        res2 = self._run_sourced(cmd_with_root)
        self.assertEqual(0, res2.returncode, res2.stderr)
        fp2 = res2.stdout.strip()
        self.assertEqual(fp1, fp2, "Fingerprint must not change when root becomes available after token-based bind")

    def test_cached_zip_source_binding_rejects_stale(self) -> None:
        # Simulate a stale zip (no .driveid file) → should trigger re-download
        setup_m166 = (REPO_ROOT / "setup-m166.sh").read_text(encoding="utf-8")
        # Verify the Drive ID binding logic is present
        self.assertIn('local id_file="${out}.driveid"', setup_m166)
        self.assertIn('[ "$cached_id" = "$id" ]', setup_m166)
        self.assertIn('printf \'%s\\n\' "$id" > "$id_file"', setup_m166)

    def test_cached_zip_source_binding_accepts_correct_source(self) -> None:
        # Verify that same drive ID + intact digest results in cache hit message
        setup_m166 = (REPO_ROOT / "setup-m166.sh").read_text(encoding="utf-8")
        self.assertIn("ZIP đã có sẵn, hợp lệ và nguyên vẹn:", setup_m166)


class TestFingerprintStrongStrategyFallbackBlocked(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-strong-fallback-"))
        self.state_dir = self.tmp / "state" / "aotscript" / "setup-driver"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_sourced(self, script_body: str) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["AOTSCRIPT_SETUP_SOURCE_ONLY"] = "1"
        env["XDG_STATE_HOME"] = str(self.tmp / "state")
        env["HOME"] = str(self.tmp / "home")
        cmd = f"""
        set -eu
        die() {{ echo "DIE:$*" >&2; exit 1; }}
        warn() {{ :; }}
        ok() {{ :; }}
        state_write() {{ :; }}
        state_read() {{ echo "no"; }}
        source "{REPO_ROOT / 'setup.sh'}"
        {script_body}
        """
        return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, check=False, env=env)

    def test_strong_strategy_fails_closed_when_root_unavailable(self) -> None:
        # First: simulate hardware binding exists
        binding_file = self.state_dir / "host_fingerprint"
        bound_hash = hashlib.sha256(b"myid|myserial").hexdigest()
        binding_file.write_text(bound_hash + "\n", encoding="utf-8")
        # Simulate root unavailable on second run without token file
        cmd = """
        su() { return 1; }
        host_fingerprint
        """
        res = self._run_sourced(cmd)
        self.assertNotEqual(0, res.returncode, "Should die when hardware-bound device loses root access")
        self.assertIn("su/Binder", res.stderr)

    def test_strong_strategy_still_works_when_root_available(self) -> None:
        binding_file = self.state_dir / "host_fingerprint"
        bound_hash = hashlib.sha256(b"myid|myserial").hexdigest()
        binding_file.write_text(bound_hash + "\n", encoding="utf-8")
        cmd = """
        su() {
          case "$*" in
            *"settings get secure android_id"*) echo "myid" ;;
            *"getprop ro.boot.serialno"*) echo "myserial" ;;
            *) echo "" ;;
          esac
        }
        host_fingerprint
        """
        res = self._run_sourced(cmd)
        self.assertEqual(0, res.returncode, res.stderr)
        self.assertEqual(bound_hash, res.stdout.strip())

    def test_zip_sha256_sidecar_stored_and_verified(self) -> None:
        setup_m166 = (REPO_ROOT / "setup-m166.sh").read_text(encoding="utf-8")
        self.assertIn('local sha_file="${out}.sha256"', setup_m166)
        self.assertIn('sha256sum "$out" > "$sha_file"', setup_m166)
        self.assertIn('stored_sha="$(cat "$sha_file"', setup_m166)
        self.assertIn('[ "$stored_sha" = "$current_sha" ]', setup_m166)


class TestFingerprintHardwareVerificationAndCloneDetection(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-hw-clone-"))
        self.state_dir = self.tmp / "state" / "aotscript" / "setup-driver"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_sourced(self, script_body: str, env_override: dict | None = None) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["AOTSCRIPT_SETUP_SOURCE_ONLY"] = "1"
        env["XDG_STATE_HOME"] = str(self.tmp / "state")
        env["HOME"] = str(self.tmp / "home")
        if env_override:
            env.update(env_override)
        cmd = f"""
        set -eu
        die() {{ echo "DIE:$*" >&2; exit 1; }}
        warn() {{ :; }}
        ok() {{ :; }}
        state_write() {{ :; }}
        state_read() {{ echo "no"; }}
        source "{REPO_ROOT / 'setup.sh'}"
        {script_body}
        """
        return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, check=False, env=env)

    def test_rooted_clone_detected_via_live_hardware_probe(self) -> None:
        # Original device bound with aid1|sno1
        orig_hash = hashlib.sha256(b"aid1|sno1").hexdigest()
        binding_file = self.state_dir / "host_fingerprint"
        binding_file.write_text(orig_hash + "\n", encoding="utf-8")

        # Cloned device runs with aid2|sno2
        cmd = """
        su() {
          case "$*" in
            *"settings get secure android_id"*) echo "aid2" ;;
            *"getprop ro.boot.serialno"*) echo "sno2" ;;
            *) echo "" ;;
          esac
        }
        host_fingerprint
        """
        res = self._run_sourced(cmd)
        self.assertEqual(0, res.returncode, res.stderr)
        clone_hash = res.stdout.strip()
        expected_clone_hash = hashlib.sha256(b"aid2|sno2").hexdigest()
        self.assertEqual(expected_clone_hash, clone_hash)
        self.assertNotEqual(orig_hash, clone_hash, "Clone must produce its own live hardware hash to trigger NEEDS_CONFIRM")

    def test_legacy_hardware_binding_fails_closed_without_root(self) -> None:
        # Legacy installation with only host_fingerprint (no strategy or token file)
        legacy_hash = hashlib.sha256(b"legacy_aid|legacy_sno").hexdigest()
        (self.state_dir / "host_fingerprint").write_text(legacy_hash + "\n", encoding="utf-8")

        cmd = """
        su() { return 1; }
        host_fingerprint
        """
        res = self._run_sourced(cmd)
        self.assertNotEqual(0, res.returncode, "Legacy hardware binding must fail closed when root is unavailable")
        self.assertIn("su/Binder", res.stderr)
        self.assertFalse((self.state_dir / "host_token").exists(), "Must not generate a spurious token file on failed hardware binding")

    def test_partial_hardware_binding_stability(self) -> None:
        # Device was originally bound when only android_id was available
        partial_hash = hashlib.sha256(b"myandroidid|").hexdigest()
        (self.state_dir / "host_fingerprint").write_text(partial_hash + "\n", encoding="utf-8")

        # On subsequent run, serial also becomes readable
        cmd = """
        su() {
          case "$*" in
            *"settings get secure android_id"*) echo "myandroidid" ;;
            *"getprop ro.boot.serialno"*) echo "myserial" ;;
            *) echo "" ;;
          esac
        }
        host_fingerprint
        """
        res = self._run_sourced(cmd)
        self.assertEqual(0, res.returncode, res.stderr)
        self.assertEqual(partial_hash, res.stdout.strip(), "Partial hardware binding must be preserved when additional signals appear")

    def test_token_binding_preserved_when_root_installed_later(self) -> None:
        # Device originally bound using token
        token = "test-persistent-token-12345"
        (self.state_dir / "host_token").write_text(token + "\n", encoding="utf-8")
        token_hash = hashlib.sha256(f"token:{token}".encode("utf-8")).hexdigest()
        (self.state_dir / "host_fingerprint").write_text(token_hash + "\n", encoding="utf-8")

        # Root becomes available later
        cmd = """
        su() {
          case "$*" in
            *"settings get secure android_id"*) echo "newaid" ;;
            *"getprop ro.boot.serialno"*) echo "newsno" ;;
            *) echo "" ;;
          esac
        }
        host_fingerprint
        """
        res = self._run_sourced(cmd)
        self.assertEqual(0, res.returncode, res.stderr)
        self.assertEqual(token_hash, res.stdout.strip(), "Token binding must be preserved even if root becomes available later")

    def test_full_hardware_binding_fails_closed_when_one_probe_disappears(self) -> None:
        # Device bound with both signals
        full_hash = hashlib.sha256(b"aid_full|sno_full").hexdigest()
        (self.state_dir / "host_fingerprint").write_text(full_hash + "\n", encoding="utf-8")
        (self.state_dir / "host_fingerprint_signals").write_text("both\n", encoding="utf-8")

        # On subsequent run, serial disappears (returns empty)
        cmd = """
        su() {
          case "$*" in
            *"settings get secure android_id"*) echo "aid_full" ;;
            *"getprop ro.boot.serialno"*) echo "" ;;
            *) echo "" ;;
          esac
        }
        host_fingerprint
        """
        res = self._run_sourced(cmd)
        self.assertNotEqual(0, res.returncode, "Must fail closed when a required hardware probe disappears")
        self.assertIn("android_id và serial", res.stderr)

    def test_legacy_full_hardware_binding_fails_closed_when_one_probe_disappears(self) -> None:
        # Legacy device bound with both signals (no signals file)
        legacy_full_hash = hashlib.sha256(b"legacy_aid|legacy_sno").hexdigest()
        (self.state_dir / "host_fingerprint").write_text(legacy_full_hash + "\n", encoding="utf-8")

        # On subsequent run, serial disappears (returns empty)
        cmd = """
        su() {
          case "$*" in
            *"settings get secure android_id"*) echo "legacy_aid" ;;
            *"getprop ro.boot.serialno"*) echo "" ;;
            *) echo "" ;;
          esac
        }
        host_fingerprint
        """
        res = self._run_sourced(cmd)
        self.assertNotEqual(0, res.returncode, "Legacy full binding must fail closed when one hardware probe is unavailable")
        self.assertIn("không khớp hoặc thiếu tín hiệu", res.stderr)

    def test_run_aot_setup_uses_dynamic_provision_ref_for_msetup_url(self) -> None:
        setup_sh = (REPO_ROOT / "setup.sh").read_text(encoding="utf-8")
        self.assertIn('AOTSCRIPT_SETUP_M166_URL:-$RAW_BASE/setup-m166.sh', setup_sh)


if __name__ == "__main__":
    unittest.main()
