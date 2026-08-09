import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = Path(os.environ.get("AOT_LAUNCHER_UNDER_TEST", REPO_ROOT / "aot")).resolve()
EXPECTED_ORIGIN = "https://github.com/tinhpr9/Aotscript.git"
SOURCES = (
    "AOTSCRIPT_RULES_CORE.txt",
    "AOTSCRIPT_HANDOFF_CURRENT.txt",
    "AOTSCRIPT_WORKFLOW_MASTER.txt",
)


def run(command, *, cwd=None, env=None, check=True):
    return subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        env=env,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def git(cwd, *args, check=True):
    return run(("git", *args), cwd=cwd, check=check)


class LauncherFixture:
    def __init__(self, *, context=True, bad_manifest=False):
        self.temp = tempfile.TemporaryDirectory(prefix="aot-launcher-test-")
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        self.product = self.root / "product"
        self.home = self.root / "home"
        self.outside = self.root / "outside"
        self.stub_bin = self.root / "stub-bin"
        self.codex_log = self.root / "codex-log.json"
        self.secret = "AOT_TEST_SECRET_MUST_NOT_APPEAR"
        self.rewrite_key = f"url.file://{self.remote}.insteadOf"
        self.context_commit = None

        self.home.mkdir()
        self.outside.mkdir()
        self.stub_bin.mkdir()
        git(self.root, "init", "--bare", self.remote)
        self._create_product()
        if context:
            self._create_context(bad_manifest=bad_manifest)
        self._write_codex_stub(valid_help=True)

    def cleanup(self):
        self.temp.cleanup()

    def _identity(self, repo):
        git(repo, "config", "user.name", "Aot Launcher Test")
        git(repo, "config", "user.email", "aot-launcher-test@example.invalid")

    def _create_product(self):
        git(self.root, "init", "-b", "main", self.product)
        self._identity(self.product)
        shutil.copy2(LAUNCHER, self.product / "aot")
        (self.product / "aot").chmod(0o755)
        (self.product / "provision-device.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        group = self.product / "aot-group-control"
        group.mkdir()
        (group / "README.md").write_text("fixture\n", encoding="utf-8")
        git(self.product, "add", "aot", "provision-device.sh", "aot-group-control/README.md")
        git(self.product, "commit", "-m", "fixture product")
        git(self.product, "remote", "add", "origin", EXPECTED_ORIGIN)
        git(self.product, "config", self.rewrite_key, EXPECTED_ORIGIN)
        git(self.product, "push", "origin", "main")
        (self.product / "agent_config.json").write_text(
            json.dumps({"secret": self.secret}) + "\n",
            encoding="utf-8",
        )

    def _create_context(self, *, bad_manifest):
        context_repo = self.root / "context"
        git(self.root, "init", "-b", "aotscript-context", context_repo)
        self._identity(context_repo)
        for name in SOURCES:
            (context_repo / name).write_text(f"{name}\nfixture context\n", encoding="utf-8")
        manifest_lines = []
        for index, name in enumerate(SOURCES):
            digest = hashlib.sha256((context_repo / name).read_bytes()).hexdigest()
            if bad_manifest and index == 0:
                digest = "0" * 64
            manifest_lines.append(f"{digest}  {name}\n")
        (context_repo / "MANIFEST.sha256").write_text("".join(manifest_lines), encoding="utf-8")
        git(context_repo, "add", *SOURCES, "MANIFEST.sha256")
        git(context_repo, "commit", "-m", "fixture context")
        self.context_commit = git(context_repo, "rev-parse", "HEAD").stdout.strip()
        git(context_repo, "remote", "add", "fixture", f"file://{self.remote}")
        git(context_repo, "push", "fixture", "aotscript-context")

    def _write_codex_stub(self, *, valid_help):
        help_text = (
            "Usage: codex [OPTIONS] [PROMPT]\n"
            "  -C, --cd <DIR>\n"
            "      --add-dir <DIR>\n"
            if valid_help
            else "Usage: codex [OPTIONS]\n"
        )
        script = f"""#!{sys.executable}
import json
import os
import sys
if sys.argv[1:] == [\"--help\"]:
    sys.stdout.write({help_text!r})
    raise SystemExit(0)
with open(os.environ[\"CODEX_STUB_LOG\"], \"w\", encoding=\"utf-8\") as handle:
    json.dump({{\"cwd\": os.getcwd(), \"argv\": sys.argv[1:]}}, handle)
"""
        path = self.stub_bin / "codex"
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)

    def make_codex_interface_invalid(self):
        self._write_codex_stub(valid_help=False)

    def make_checksum_checker_crash(self):
        path = self.stub_bin / "sha256sum"
        path.write_text("#!/bin/sh\nexit 70\n", encoding="utf-8")
        path.chmod(0o755)

    def make_fetch_fail(self):
        git(self.product, "config", "--remove-section", f"url.file://{self.remote}")
        missing = self.root / "missing-remote.git"
        self.rewrite_key = f"url.file://{missing}.insteadOf"
        git(self.product, "config", self.rewrite_key, EXPECTED_ORIGIN)

    def set_wrong_origin(self):
        git(self.product, "remote", "set-url", "origin", "https://example.invalid/wrong.git")

    def worktree_state(self):
        return {
            "head": git(self.product, "rev-parse", "HEAD").stdout,
            "status": git(
                self.product,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ).stdout,
            "staged": git(self.product, "diff", "--cached", "--name-status").stdout,
        }

    def invoke(self, *task, extra_env=None):
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.stub_bin}:{env.get('PATH', '')}",
                "CODEX_STUB_LOG": str(self.codex_log),
                "GIT_ALLOW_PROTOCOL": "file:https:ssh",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        if extra_env:
            env.update(extra_env)
        result = run((self.product / "aot", *task), cwd=self.outside, env=env, check=False)
        invocation = None
        if self.codex_log.exists():
            invocation = json.loads(self.codex_log.read_text(encoding="utf-8"))
        return result, invocation

    def cache_dir(self):
        if self.context_commit is None:
            return None
        return self.home / ".cache" / "aotscript-context" / self.context_commit


class AotLauncherTests(unittest.TestCase):
    def fixture(self, **kwargs):
        fixture = LauncherFixture(**kwargs)
        self.addCleanup(fixture.cleanup)
        return fixture

    def assert_failed_closed(self, result, error_type):
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("AOT_LAUNCHER=FAIL", output)
        self.assertIn("CODEX_START=NO", output)
        self.assertIn(f"ERROR_TYPE={error_type}", output)
        self.assertNotIn("CODEX_START=YES", output)

    def test_new_cache_no_task_launches_in_verified_repo(self):
        fixture = self.fixture()
        before = fixture.worktree_state()
        result, invocation = fixture.invoke()
        after = fixture.worktree_state()
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertEqual(before, after)
        self.assertNotIn(fixture.secret, output)
        self.assertIsNotNone(invocation)
        self.assertEqual(invocation["cwd"], str(fixture.product))
        self.assertEqual(invocation["argv"][0:2], ["-C", str(fixture.product)])
        self.assertEqual(invocation["argv"][2], "--add-dir")
        self.assertEqual(invocation["argv"][3], str(fixture.cache_dir()))
        prompt = invocation["argv"][4]
        self.assertIn(f"CONTEXT_DIR={fixture.cache_dir()}", prompt)
        self.assertIn(f"CONTEXT_COMMIT={fixture.context_commit}", prompt)
        self.assertIn("CURRENT_REQUEST=RESUME checkpoint", prompt)
        self.assertIn("Đọc ĐẦY ĐỦ $CONTEXT_DIR/AOTSCRIPT_RULES_CORE.txt", prompt)
        self.assertIn("CONTEXT_CACHE=CREATED", output)
        self.assertIn("PRODUCT_WORKTREE_UNCHANGED_BEFORE_CODEX=YES", output)
        self.assertTrue(fixture.cache_dir().is_dir())

    def test_existing_cache_with_task(self):
        fixture = self.fixture()
        first, _ = fixture.invoke()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        task = "Tiếp tục blocker launcher hiện tại"
        second, invocation = fixture.invoke(task)
        output = second.stdout + second.stderr

        self.assertEqual(second.returncode, 0, output)
        self.assertIn("CONTEXT_CACHE=EXISTING", output)
        self.assertIn(f"CURRENT_REQUEST={task}", invocation["argv"][4])

    def test_wrong_repo_fails_closed(self):
        fixture = self.fixture()
        fixture.set_wrong_origin()
        result, invocation = fixture.invoke()
        self.assertIsNone(invocation)
        self.assert_failed_closed(result, "REPO_NOT_FOUND")

    def test_missing_context_branch_fails_closed(self):
        fixture = self.fixture(context=False)
        result, invocation = fixture.invoke()
        self.assertIsNone(invocation)
        self.assert_failed_closed(result, "CONTEXT_FETCH_FAILED")

    def test_bad_remote_manifest_fails_closed(self):
        fixture = self.fixture(bad_manifest=True)
        result, invocation = fixture.invoke()
        self.assertIsNone(invocation)
        self.assert_failed_closed(result, "MANIFEST_INVALID")
        self.assertFalse(fixture.cache_dir().exists())

    def test_corrupt_existing_cache_fails_closed(self):
        fixture = self.fixture()
        first, _ = fixture.invoke()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        (fixture.cache_dir() / SOURCES[0]).write_text("corrupt\n", encoding="utf-8")
        fixture.codex_log.unlink(missing_ok=True)
        second, invocation = fixture.invoke()
        self.assertIsNone(invocation)
        self.assert_failed_closed(second, "MANIFEST_INVALID")

    def test_network_fetch_failure_does_not_use_cache(self):
        fixture = self.fixture()
        first, _ = fixture.invoke()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        fixture.make_fetch_fail()
        fixture.codex_log.unlink(missing_ok=True)
        second, invocation = fixture.invoke()
        self.assertIsNone(invocation)
        self.assert_failed_closed(second, "CONTEXT_FETCH_FAILED")

    def test_checker_error_is_not_reported_as_repo_error(self):
        fixture = self.fixture()
        fixture.make_checksum_checker_crash()
        result, invocation = fixture.invoke()
        self.assertIsNone(invocation)
        self.assert_failed_closed(result, "CHECKER_ERROR")

    def test_invalid_codex_interface_fails_closed(self):
        fixture = self.fixture()
        fixture.make_codex_interface_invalid()
        result, invocation = fixture.invoke()
        self.assertIsNone(invocation)
        self.assert_failed_closed(result, "CODEX_INTERFACE_INVALID")


if __name__ == "__main__":
    unittest.main(verbosity=2)
