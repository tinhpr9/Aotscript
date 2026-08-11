from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from launcher_test_support import LauncherExecutionError, TERMUX_BASH, launcher_command


class LauncherCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="aot-launcher-command-")
        self.launcher = pathlib.Path(self.temporary.name) / "aotsetup"
        self.launcher.write_text(
            f"#!{TERMUX_BASH}\nprintf 'fixture:%s\\n' \"$1\"\n",
            encoding="utf-8",
        )
        self.launcher.chmod(0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_termux_interpreter_uses_direct_execution(self) -> None:
        original_access = os.access
        original_is_file = pathlib.Path.is_file

        def access(path: os.PathLike[str] | str, mode: int) -> bool:
            if pathlib.Path(path) == pathlib.Path(TERMUX_BASH):
                return True
            return original_access(path, mode)

        def is_file(path: pathlib.Path) -> bool:
            if path == pathlib.Path(TERMUX_BASH):
                return True
            return original_is_file(path)

        with mock.patch("launcher_test_support.os.access", side_effect=access), mock.patch(
            "launcher_test_support.pathlib.Path.is_file", autospec=True, side_effect=is_file
        ):
            command = launcher_command(self.launcher, ["update"])
        self.assertEqual(command, [str(self.launcher), "update"])

    def test_ubuntu_ci_runs_termux_shebang_through_bash(self) -> None:
        original_access = os.access

        def access(path: os.PathLike[str] | str, mode: int) -> bool:
            if pathlib.Path(path) == pathlib.Path(TERMUX_BASH):
                return False
            return original_access(path, mode)

        env = {"CI": "true", "RUNNER_OS": "Linux", "PATH": os.environ["PATH"]}
        with mock.patch("launcher_test_support.os.access", side_effect=access):
            command = launcher_command(self.launcher, ["update"], environ=env)
        self.assertEqual(command[:2], [shutil.which("bash"), str(self.launcher)])
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "fixture:update\n")

    def test_missing_interpreter_outside_ubuntu_ci_fails_closed(self) -> None:
        original_access = os.access

        def access(path: os.PathLike[str] | str, mode: int) -> bool:
            if pathlib.Path(path) == pathlib.Path(TERMUX_BASH):
                return False
            return original_access(path, mode)

        with mock.patch("launcher_test_support.os.access", side_effect=access):
            with self.assertRaisesRegex(LauncherExecutionError, "interpreter is unavailable"):
                launcher_command(self.launcher, environ={"PATH": os.environ["PATH"]})

    def test_python_tests_do_not_execute_launcher_path_directly(self) -> None:
        forbidden = re.compile(
            r"subprocess\.(?:run|Popen|check_call|check_output)\(\s*"
            r"\[\s*str\([^\n)]*launcher[^\n)]*\)"
        )
        tests = pathlib.Path(__file__).resolve().parent
        offenders = [
            str(path.relative_to(tests.parent))
            for path in tests.glob("*.py")
            if path != pathlib.Path(__file__) and forbidden.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
