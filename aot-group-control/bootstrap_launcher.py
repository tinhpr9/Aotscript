#!/data/data/com.termux/files/usr/bin/python3
"""Stable launcher for the versioned AOT worker bootstrap.

This file lives outside worker releases. It validates the active bootstrap before
every invocation and restores the last known bootstrap if an upgrade is broken.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

BOOTSTRAP_VERSION = 1
ROOT = pathlib.Path(__file__).resolve().parent
ACTIVE = ROOT / "bootstrap.py"
LAST_GOOD = ROOT / "bootstrap.py.last_good"


def _valid(path: pathlib.Path) -> bool:
    if not path.is_file():
        return False
    result = subprocess.run(
        [sys.executable, str(path), "self-test"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=20,
    )
    return result.returncode == 0


def _restore() -> bool:
    if not _valid(LAST_GOOD):
        return False
    temp = ACTIVE.with_name(ACTIVE.name + f".restore-{os.getpid()}")
    temp.write_bytes(LAST_GOOD.read_bytes())
    os.chmod(temp, 0o700)
    os.replace(temp, ACTIVE)
    return True


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not _valid(ACTIVE) and not _restore():
        print("AOT_BOOTSTRAP=FAILED")
        print("REASON=no_valid_bootstrap")
        return 86
    result = subprocess.run(
        [sys.executable, str(ACTIVE), *args],
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
