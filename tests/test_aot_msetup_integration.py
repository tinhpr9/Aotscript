import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SETUP = Path(
    os.environ.get("AOT_SETUP_UNDER_TEST", REPO_ROOT / "setup-m166.sh")
).resolve()
LAUNCHER = (REPO_ROOT / "aot").resolve()
PINNED_REF = "0123456789abcdef0123456789abcdef01234567"
SECRET = "AOT_MSETUP_TEST_SECRET_MUST_NOT_APPEAR"


def run(command, *, cwd=None, env=None):
    return subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class MsetupAotFixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aot-msetup-integration-")
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.bin_dir = self.home / "bin"
        self.stub_bin = self.root / "stub-bin"
        self.outside = self.root / "outside"
        self.source = self.root / "launcher-source"
        self.curl_log = self.root / "curl-url.log"
        self.secret_file = self.outside / "agent_config.json"
        self.home.mkdir()
        self.bin_dir.mkdir()
        self.stub_bin.mkdir()
        self.outside.mkdir()
        shutil.copy2(LAUNCHER, self.source)
        self.secret_file.write_text(SECRET + "\n", encoding="utf-8")
        self._write_curl_stub()

    def cleanup(self):
        self.temp.cleanup()

    @property
    def target(self):
        return self.bin_dir / "aot"

    def _write_curl_stub(self):
        path = self.stub_bin / "curl"
        path.write_text(
            f"""#!{sys.executable}
import os
import pathlib
import shutil
import sys
args = sys.argv[1:]
try:
    output = pathlib.Path(args[args.index('-o') + 1])
except (ValueError, IndexError):
    raise SystemExit(64)
url = next((item for item in args if item.startswith('https://')), '')
pathlib.Path(os.environ['AOT_TEST_CURL_LOG']).write_text(url + '\\n', encoding='utf-8')
mode = os.environ.get('AOT_TEST_CURL_MODE', 'success')
if mode == 'network-fail':
    raise SystemExit(22)
if mode == 'empty':
    output.write_bytes(b'')
    raise SystemExit(0)
if mode == 'syntax-invalid':
    output.write_text('#!/usr/bin/env bash\\nif then\\n', encoding='utf-8')
    raise SystemExit(0)
if mode == 'structure-invalid':
    output.write_text('#!/usr/bin/env bash\\necho fixture\\n', encoding='utf-8')
    raise SystemExit(0)
shutil.copyfile(os.environ['AOT_TEST_LAUNCHER_SOURCE'], output)
""",
            encoding="utf-8",
        )
        path.chmod(0o755)

    def enable_replace_failure(self):
        real_mv = shutil.which("mv")
        path = self.stub_bin / "mv"
        path.write_text(
            f"""#!{sys.executable}
import os
import pathlib
import sys
args = sys.argv[1:]
target = pathlib.Path(args[-1]) if args else pathlib.Path('')
if os.environ.get('AOT_TEST_MV_FAIL') == '1' and target.name == 'aot':
    raise SystemExit(1)
os.execv({real_mv!r}, [{real_mv!r}, *args])
""",
            encoding="utf-8",
        )
        path.chmod(0o755)

    def enable_checker_crash(self):
        path = self.stub_bin / "python"
        path.write_text("#!/bin/sh\nexit 70\n", encoding="utf-8")
        path.chmod(0o755)

    def invoke(self, *, mode="success", replace_fail=False):
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.stub_bin}:{env.get('PATH', '')}",
                "AOTSCRIPT_INSTALL_AOT_ONLY": "1",
                "AOTSCRIPT_PROVISION_REF": PINNED_REF,
                "AOT_TEST_CURL_MODE": mode,
                "AOT_TEST_CURL_LOG": str(self.curl_log),
                "AOT_TEST_LAUNCHER_SOURCE": str(self.source),
                "AOT_TEST_MV_FAIL": "1" if replace_fail else "0",
            }
        )
        return run(("bash", SETUP), cwd=self.outside, env=env)

    def backups(self):
        return sorted(self.bin_dir.glob("aot.bak-*"))


class AotMsetupIntegrationTests(unittest.TestCase):
    def fixture(self):
        fixture = MsetupAotFixture()
        self.addCleanup(fixture.cleanup)
        return fixture

    def assert_failed_closed(self, result, error_type):
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("AOT_INSTALL=FAIL", output)
        self.assertIn(f"ERROR_TYPE={error_type}", output)
        self.assertNotIn(SECRET, output)

    def test_setup_and_launcher_sources_have_valid_syntax(self):
        self.assertEqual(run(("bash", "-n", SETUP)).returncode, 0)
        self.assertEqual(run(("bash", "-n", LAUNCHER)).returncode, 0)

    def test_normal_setup_control_flow_installs_after_path_setup(self):
        text = SETUP.read_text(encoding="utf-8")
        path_setup = text.index('case ":$PATH:" in')
        normal_call = text.index(
            'install_aot_launcher ||\n  die "Không cài được launcher aot"',
            path_setup,
        )
        msetup_generation = text.index('MSETUP_CMD="$HOME/bin/msetup"')
        self.assertLess(path_setup, normal_call)
        self.assertLess(normal_call, msetup_generation)
        self.assertEqual(text.count('install_aot_launcher() {'), 1)

    def test_first_install_preserves_pinned_ref_and_resolves_command(self):
        fixture = self.fixture()
        before_secret = fixture.secret_file.read_bytes()
        result = fixture.invoke()
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("AOT_INSTALL=INSTALLED", output)
        self.assertEqual(fixture.target.read_bytes(), LAUNCHER.read_bytes())
        self.assertEqual(stat.S_IMODE(fixture.target.stat().st_mode), 0o700)
        self.assertIn(f"/{PINNED_REF}/aot?", fixture.curl_log.read_text(encoding="utf-8"))
        env = os.environ.copy()
        env["PATH"] = f"{fixture.bin_dir}:{env.get('PATH', '')}"
        resolved = run(("bash", "-c", "command -v aot"), env=env)
        self.assertEqual(resolved.returncode, 0, resolved.stdout + resolved.stderr)
        self.assertEqual(Path(resolved.stdout.strip()), fixture.target)
        self.assertEqual(fixture.secret_file.read_bytes(), before_secret)
        self.assertNotIn(SECRET, output)

    def test_reinstall_is_idempotent_and_does_not_create_backup(self):
        fixture = self.fixture()
        first = fixture.invoke()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        before = fixture.target.stat()
        second = fixture.invoke()
        output = second.stdout + second.stderr
        after = fixture.target.stat()
        self.assertEqual(second.returncode, 0, output)
        self.assertIn("AOT_INSTALL=UNCHANGED", output)
        self.assertEqual(before.st_ino, after.st_ino)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
        self.assertEqual(fixture.backups(), [])

    def test_existing_correct_launcher_is_not_replaced(self):
        fixture = self.fixture()
        shutil.copy2(LAUNCHER, fixture.target)
        fixture.target.chmod(0o700)
        before = fixture.target.stat()
        result = fixture.invoke()
        after = fixture.target.stat()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AOT_INSTALL=UNCHANGED", result.stdout + result.stderr)
        self.assertEqual(before.st_ino, after.st_ino)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
        self.assertEqual(fixture.backups(), [])

    def test_empty_download_fails_closed_without_damaging_good_launcher(self):
        fixture = self.fixture()
        shutil.copy2(LAUNCHER, fixture.target)
        before = fixture.target.read_bytes()
        result = fixture.invoke(mode="empty")
        self.assert_failed_closed(result, "EMPTY_DOWNLOAD")
        self.assertEqual(fixture.target.read_bytes(), before)
        self.assertEqual(fixture.backups(), [])

    def test_syntax_invalid_download_fails_closed(self):
        fixture = self.fixture()
        result = fixture.invoke(mode="syntax-invalid")
        self.assert_failed_closed(result, "SYNTAX_INVALID")
        self.assertFalse(fixture.target.exists())

    def test_structure_invalid_download_fails_closed(self):
        fixture = self.fixture()
        result = fixture.invoke(mode="structure-invalid")
        self.assert_failed_closed(result, "STRUCTURE_INVALID")
        self.assertFalse(fixture.target.exists())

    def test_network_failure_preserves_existing_launcher(self):
        fixture = self.fixture()
        shutil.copy2(LAUNCHER, fixture.target)
        before = fixture.target.read_bytes()
        result = fixture.invoke(mode="network-fail")
        self.assert_failed_closed(result, "DOWNLOAD_FAILED")
        self.assertEqual(fixture.target.read_bytes(), before)
        self.assertEqual(fixture.backups(), [])

    def test_atomic_replace_failure_preserves_target_and_backup(self):
        fixture = self.fixture()
        old = LAUNCHER.read_text(encoding="utf-8") + "\n# previous launcher\n"
        fixture.target.write_text(old, encoding="utf-8")
        fixture.target.chmod(0o700)
        fixture.enable_replace_failure()
        result = fixture.invoke(replace_fail=True)
        self.assert_failed_closed(result, "ATOMIC_REPLACE_FAILED")
        self.assertEqual(fixture.target.read_text(encoding="utf-8"), old)
        backups = fixture.backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), old)

    def test_checker_crash_is_classified_as_checker_error(self):
        fixture = self.fixture()
        fixture.enable_checker_crash()
        result = fixture.invoke()
        self.assert_failed_closed(result, "CHECKER_ERROR")
        self.assertFalse(fixture.target.exists())

    def test_symlink_conflict_fails_closed(self):
        fixture = self.fixture()
        conflict = fixture.root / "other-aot"
        shutil.copy2(LAUNCHER, conflict)
        fixture.target.symlink_to(conflict)
        result = fixture.invoke()
        self.assert_failed_closed(result, "TARGET_CONFLICT")
        self.assertTrue(fixture.target.is_symlink())
        self.assertEqual(fixture.target.resolve(), conflict)

    def test_setup_m166_nonroot_graceful_continuation(self):
        # When su does not exist or fails, setup-m166.sh must NOT die with "ROOT không hoạt động"
        stub_dir = tempfile.mkdtemp(prefix="nonroot-test-")
        self.addCleanup(shutil.rmtree, stub_dir, ignore_errors=True)
        # Create a failing su stub
        su_stub = Path(stub_dir) / "su"
        su_stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        su_stub.chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = f"{stub_dir}:{env.get('PATH', '')}"
        # We invoke setup-m166.sh with m74 and 2 (NOVA), but stop early or check output
        # Using bash -c with a timeout or dry-run checks
        script_text = SETUP.read_text(encoding="utf-8")
        self.assertIn("HAVE_ROOT=0", script_text)
        self.assertIn("warn \"ROOT không có hoặc không hoạt động trên máy này\"", script_text)
        self.assertNotIn('die "ROOT không hoạt động"', script_text)

    def test_setup_m166_root_detected_when_available(self):
        script_text = SETUP.read_text(encoding="utf-8")
        self.assertIn("HAVE_ROOT=1", script_text)
        self.assertIn('ok "ROOT hoạt động — tất cả bước sẽ chạy đầy đủ"', script_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

