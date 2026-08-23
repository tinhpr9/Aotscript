#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import re
import subprocess
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestRuntimeRecoveryAndSetupGuards(unittest.TestCase):
    def test_host_fingerprint_fallback_in_setup_sh(self) -> None:
        setup_sh = (REPO_ROOT / "setup.sh").read_text(encoding="utf-8")
        self.assertIn("prop:$fallback_id", setup_sh)
        self.assertIn("host_token", setup_sh)
        self.assertIn("ro.build.display.id", setup_sh)

    def test_host_fingerprint_executes_successfully_without_su(self) -> None:
        cmd = f"""
        SETUP_STATE_DIR="$(mktemp -d)"
        die() {{ echo "DIE:$*" >&2; exit 1; }}
        eval "$(awk '/^host_fingerprint\\(\\)/,/^}}/' "{REPO_ROOT / 'setup.sh'}")"
        host_fingerprint
        rm -rf "$SETUP_STATE_DIR"
        """
        res = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, res.returncode, f"host_fingerprint failed: {res.stderr}")
        fp = res.stdout.strip()
        self.assertTrue(bool(re.fullmatch(r"[0-9a-f]{64}", fp)), f"Invalid fingerprint: {fp}")

    def test_setup_m166_has_readiness_retry_loop(self) -> None:
        setup_m166 = (REPO_ROOT / "setup-m166.sh").read_text(encoding="utf-8")
        self.assertIn("for i in $(seq 1 15); do", setup_m166)
        self.assertIn("runtime.py\" status", setup_m166)
        self.assertIn("grep -qx 'AOT_CONFIG=OK'", setup_m166)


if __name__ == "__main__":
    unittest.main()
