"""Safe command construction for Termux launcher fixtures in host-side tests."""

from __future__ import annotations

import os
import pathlib
import shlex
import shutil
from collections.abc import Mapping, Sequence


TERMUX_BASH = "/data/data/com.termux/files/usr/bin/bash"


class LauncherExecutionError(RuntimeError):
    """The host cannot safely execute a Termux launcher fixture."""


def launcher_command(
    launcher: pathlib.Path,
    arguments: Sequence[str] = (),
    *,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Return a fail-closed argv for a regular executable launcher fixture."""
    path = pathlib.Path(launcher)
    if not path.is_file() or path.is_symlink() or not os.access(path, os.X_OK):
        raise LauncherExecutionError(f"launcher is not a regular executable file: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            first_line = handle.readline().rstrip("\r\n")
        words = shlex.split(first_line[2:]) if first_line.startswith("#!") else []
    except (OSError, UnicodeError, ValueError) as exc:
        raise LauncherExecutionError(f"cannot read launcher shebang: {path}") from exc
    if len(words) != 1:
        raise LauncherExecutionError(f"unsupported launcher shebang: {first_line!r}")
    interpreter = pathlib.Path(words[0])
    if interpreter.is_file() and os.access(interpreter, os.X_OK):
        return [str(path), *arguments]

    env = os.environ if environ is None else environ
    is_ubuntu_ci = (
        words[0] == TERMUX_BASH
        and env.get("CI") == "true"
        and env.get("RUNNER_OS") == "Linux"
    )
    if not is_ubuntu_ci:
        raise LauncherExecutionError(
            f"launcher shebang interpreter is unavailable: {words[0]}"
        )
    bash = shutil.which("bash", path=env.get("PATH"))
    if not bash or not os.access(bash, os.X_OK):
        raise LauncherExecutionError("GitHub Actions Bash is unavailable")
    return [bash, str(path), *arguments]
