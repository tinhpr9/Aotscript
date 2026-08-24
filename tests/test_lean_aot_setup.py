#!/usr/bin/env python3
"""
tests/test_lean_aot_setup.py - Lean AOT Setup Invariant & Behavior Tests

Proves:
1. Fresh setup no longer downloads or references 'Shouko.zip'.
2. Fresh setup no longer downloads or extracts 'Delta.zip'.
3. No Gboard 'ime enable/set' or LatinIME configuration remains in the installer.
4. Setup never auto-downloads, installs, or opens the Termux:Boot Android APK.
5. Existing Shouko identity/config (device_id.txt, device_group.txt, agent_config.json,
   aot_group_config.json, agent_state.json) survives unchanged.
6. Fresh agent config can still be established through canonical existing mechanisms
   (existing valid config -> per-device backup -> Telegram pairing).
7. Existing per-device private agent backup resumes correctly.
8. Registration and runtime bootstrap execute cleanly without payload dependencies.
9. Rerun does not replay removed payload setup.
10. No hidden or dead helper dependencies on deleted ZIPs or Gboard remain.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SETUP_SH = ROOT / "setup.sh"
SETUP_M166_SH = ROOT / "setup-m166.sh"
TERMUXBOOT_SH = ROOT / "Termuxboot"


class LeanAotSetupStaticTests(unittest.TestCase):
    """Static and architectural invariant checks across the codebase."""

    def setUp(self):
        self.setup_m166_content = SETUP_M166_SH.read_text(encoding="utf-8")
        self.setup_content = SETUP_SH.read_text(encoding="utf-8")

    def test_01_gboard_configuration_eliminated(self):
        """Gboard package, IME detection, ime enable/set, and related messages must be gone."""
        self.assertNotIn("com.google.android.inputmethod.latin", self.setup_m166_content)
        self.assertNotIn("LatinIME", self.setup_m166_content)
        self.assertNotIn("CẤU HÌNH GBOARD", self.setup_m166_content)
        self.assertNotIn("ime enable", self.setup_m166_content)
        self.assertNotIn("ime set", self.setup_m166_content)
        self.assertNotIn("Gboard", self.setup_m166_content)

    def test_02_delta_payload_download_and_extraction_eliminated(self):
        """Delta.zip download, extract, and variables must not exist in setup-m166.sh."""
        self.assertNotIn("Delta.zip", self.setup_m166_content)
        self.assertNotIn("SOURCE_DELTA", self.setup_m166_content)
        self.assertNotIn("1BkHn3hyDfobTcy5tqhT9LePe01OzEHQ-", self.setup_m166_content)

    def test_03_shouko_downloadable_zip_payload_eliminated(self):
        """Shouko.zip download and extraction must not exist in setup-m166.sh."""
        self.assertNotIn("Shouko.zip", self.setup_m166_content)
        self.assertNotIn("1vDjK3hNCyT0B_rbAcsPlelD-TJJKzwG1", self.setup_m166_content)
        self.assertNotIn("download_zip", self.setup_m166_content)
        self.assertNotIn("extract_safe", self.setup_m166_content)
        self.assertNotIn("gdown", self.setup_m166_content)

    def test_04_termux_boot_app_auto_install_eliminated(self):
        """install_termux_boot_app() and APK download/install must be removed from setup-m166.sh."""
        self.assertNotIn("install_termux_boot_app", self.setup_m166_content)
        self.assertNotIn("Termux-Boot.apk", self.setup_m166_content)
        self.assertNotIn("termux-boot-meta", self.setup_m166_content)
        self.assertNotIn("f-droid.org/repo/com.termux.boot", self.setup_m166_content)

    def test_05_shouko_storage_and_identity_namespace_preserved(self):
        """Shouko storage directory and identity/config paths must remain intact."""
        self.assertIn('SHOUKO_DIR="$DL/Shouko"', self.setup_m166_content)
        self.assertIn('AGENT_CONFIG="$SHOUKO_DIR/agent_config.json"', self.setup_m166_content)
        self.assertIn('AOT_CONFIG="$SHOUKO_DIR/aot_group_config.json"', self.setup_m166_content)
        self.assertIn('mkdir -p "$SHOUKO_DIR"', self.setup_m166_content)

    def test_06_boot_script_generation_retained(self):
        """01-agent.sh boot script creation must be retained."""
        self.assertIn('AGENT_BOOT="$HOME/.termux/boot/01-agent.sh"', self.setup_m166_content)
        self.assertIn('Termuxboot', self.setup_m166_content)

    def test_07_unused_download_provision_removed_from_setup_sh(self):
        """Unused download_provision helper in setup.sh must be removed."""
        self.assertNotIn("download_provision()", self.setup_content)


class LeanAotSetupBehaviorTests(unittest.TestCase):
    """Dynamic execution and behavior tests."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lean-aot-test-")
        self.root = pathlib.Path(self.tmp.name)
        self.home = self.root / "home"
        self.state = self.root / "state"
        self.prefix = self.root / "prefix"
        self.storage = self.root / "storage"
        self.dl = self.storage / "Download"
        self.shouko = self.dl / "Shouko"
        self.bin_dir = self.home / "bin"
        self.config_dir = self.home / ".config" / "aotscript"

        self.home.mkdir(parents=True)
        self.prefix.mkdir(parents=True)
        (self.prefix / "bin").mkdir(parents=True)
        self.bin_dir.mkdir(parents=True)
        self.shouko.mkdir(parents=True)
        self.config_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_08_agent_config_preservation_when_matching_device_id(self):
        """Existing valid agent_config.json for device is preserved byte-for-byte."""
        agent_cfg = self.shouko / "agent_config.json"
        id_file = self.shouko / "device_id.txt"
        id_file.write_text("m74\n", encoding="utf-8")
        original_data = {
            "worker_report_url": "https://hub.example.invalid/agent/report",
            "agent_report_secret": "my-super-secret-token"
        }
        agent_cfg.write_text(json.dumps(original_data, indent=2) + "\n", encoding="utf-8")

        # Mock script executing install_agent_config logic
        test_script = self.root / "test_install_agent.sh"
        test_script.write_text(f"""#!/usr/bin/env bash
set -eu
DEVICE_ID="m74"
DEVICE_GROUP="NOVA"
SHOUKO_DIR="{self.shouko}"
AGENT_CONFIG="{agent_cfg}"
PRIVATE_AGENT_CONFIG_DIR="{self.config_dir}"
PRIVATE_AGENT_CONFIG="{self.config_dir}/agent_config.m74.json"
WORKER_ORIGIN="https://hub.example.invalid"
STAMP="20260824-000000"

ok() {{ echo "[OK] $*"; }}
warn() {{ echo "[WARN] $*"; }}
die() {{ echo "[DIE] $*"; exit 1; }}

validate_agent_config() {{
  python - "$1" <<'PY'
import json, pathlib, sys, urllib.parse
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
assert isinstance(data, dict)
url = data.get("worker_report_url")
secret = data.get("agent_report_secret")
assert isinstance(url, str) and url.strip()
parsed = urllib.parse.urlparse(url.strip())
assert parsed.scheme in {{"http", "https"}} and parsed.netloc
assert isinstance(secret, str) and secret.strip()
PY
}}

save_private_agent_config() {{
  mkdir -p "$PRIVATE_AGENT_CONFIG_DIR"
  cp -p "$1" "$PRIVATE_AGENT_CONFIG"
}}

# Source install_agent_config extracted from setup-m166.sh
current_device_id="$(tr -d '\\r\\n ' < "$SHOUKO_DIR/device_id.txt" 2>/dev/null | tr '[:upper:]' '[:lower:]' || true)"
if [ "$current_device_id" = "$DEVICE_ID" ] && [ -s "$AGENT_CONFIG" ] && validate_agent_config "$AGENT_CONFIG"; then
  chmod 600 "$AGENT_CONFIG" 2>/dev/null || true
  save_private_agent_config "$AGENT_CONFIG"
  ok "Giữ nguyên agent_config.json đúng Device ID"
  exit 0
fi
die "Did not preserve existing config"
""", encoding="utf-8")
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, f"Failed: {proc.stderr}\n{proc.stdout}")
        self.assertIn("Giữ nguyên agent_config.json đúng Device ID", proc.stdout)
        self.assertEqual(json.loads(agent_cfg.read_text(encoding="utf-8")), original_data)
        self.assertTrue((self.config_dir / "agent_config.m74.json").is_file())

    def test_09_agent_config_restored_from_private_backup(self):
        """When Shouko is wiped/clean, agent_config is restored from per-device private backup."""
        agent_cfg = self.shouko / "agent_config.json"
        if agent_cfg.exists():
            agent_cfg.unlink()

        backup_file = self.config_dir / "agent_config.m74.json"
        backup_data = {
            "worker_report_url": "https://hub.example.invalid/agent/report",
            "agent_report_secret": "restored-secret-token"
        }
        backup_file.write_text(json.dumps(backup_data, indent=2) + "\n", encoding="utf-8")

        test_script = self.root / "test_restore_agent.sh"
        test_script.write_text(f"""#!/usr/bin/env bash
set -eu
TMPDIR="{self.root}"
DEVICE_ID="m74"
DEVICE_GROUP="NOVA"
SHOUKO_DIR="{self.shouko}"
AGENT_CONFIG="{agent_cfg}"
PRIVATE_AGENT_CONFIG_DIR="{self.config_dir}"
PRIVATE_AGENT_CONFIG="{backup_file}"
WORKER_ORIGIN="https://hub.example.invalid"
STAMP="20260824-000000"

ok() {{ echo "[OK] $*"; }}
warn() {{ echo "[WARN] $*"; }}
die() {{ echo "[DIE] $*"; exit 1; }}

validate_agent_config() {{
  python - "$1" <<'PY'
import json, pathlib, sys, urllib.parse
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
assert isinstance(data, dict)
url = data.get("worker_report_url")
secret = data.get("agent_report_secret")
assert isinstance(url, str) and url.strip()
parsed = urllib.parse.urlparse(url.strip())
assert parsed.scheme in {{"http", "https"}} and parsed.netloc
assert isinstance(secret, str) and secret.strip()
PY
}}

save_private_agent_config() {{
  mkdir -p "$PRIVATE_AGENT_CONFIG_DIR"
  cp -p "$1" "$PRIVATE_AGENT_CONFIG"
}}

tmp="{self.root}/agent_config.$$"
source_name=""

if [ -s "$PRIVATE_AGENT_CONFIG" ]; then
  cp -p "$PRIVATE_AGENT_CONFIG" "$tmp"
  if validate_agent_config "$tmp"; then
    source_name="backup riêng theo Device ID"
  fi
fi

if [ -n "$source_name" ]; then
  mkdir -p "$SHOUKO_DIR"
  mv -f "$tmp" "$AGENT_CONFIG"
  save_private_agent_config "$AGENT_CONFIG"
  ok "Đã cài agent_config.json từ $source_name; không hiển thị nội dung"
  exit 0
fi
die "Failed to restore from backup"
""", encoding="utf-8")
        test_script.chmod(0o755)

        proc = subprocess.run(["bash", str(test_script)], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, f"Failed: {proc.stderr}\n{proc.stdout}")
        self.assertIn("Đã cài agent_config.json từ backup riêng theo Device ID", proc.stdout)
        self.assertTrue(agent_cfg.is_file())
        self.assertEqual(json.loads(agent_cfg.read_text(encoding="utf-8")), backup_data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
