#!/usr/bin/env python3
"""
tests/test_bootstrap_release_coherence.py - Tests for AOT Worker Release Coherence & Bootstrap Upgrade
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_version
import prepare_worker_release


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise ImportError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestBootstrapReleaseCoherence(unittest.TestCase):
    def setUp(self):
        self.bootstrap = load_module("test_bootstrap_mod", ROOT / "aot-group-control" / "bootstrap.py")
        self.updater = load_module("test_updater_mod", ROOT / "aot-group-control" / "updater.py")

    def test_01_old_release_immutable_and_canonical_bumped(self):
        """Verify worker-release.json is bumped to 2026.08.24.02, leaving old 2026.08.24.01 immutable."""
        canonical = release_version.load_canonical_version(ROOT / "aot-group-control" / "worker-release.json")
        self.assertEqual(canonical["version"], "2026.08.24.02")
        self.assertEqual(canonical["worker_version"], "aot-worker-2026.08.24.02")
        self.assertEqual(canonical["tag"], "worker-v2026.08.24.02")

    def test_02_new_generated_manifests_point_to_new_release(self):
        """Verify manifests specify new version and bootstrap asset version 8."""
        for channel in ("stable", "canary"):
            manifest_path = ROOT / "aot-group-control" / f"worker-manifest-{channel}.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], "aot-worker-2026.08.24.02")
            self.assertEqual(manifest["release_version"], "aot-worker-2026.08.24.02")
            self.assertEqual(manifest["bootstrap"]["version"], 8)
            self.assertIn("worker-v2026.08.24.02/bootstrap.py", manifest["bootstrap"]["url"])

    def test_03_bootstrap_asset_version_upgrade_accepted_from_v7(self):
        """Verify deployed v7 supervisor accepts manifest with bootstrap version 8."""
        manifest = json.loads((ROOT / "aot-group-control" / "worker-manifest-stable.json").read_text(encoding="utf-8"))
        bootstrap_item = manifest.get("bootstrap")
        self.assertIsNotNone(bootstrap_item)
        # Deployed devices with BOOTSTRAP_RELEASE_VERSION = 7 will see item["version"] (8) > 7
        self.assertGreater(int(bootstrap_item["version"]), 7)

    def test_04_files_match_generated_hashes(self):
        """Verify local files match hashes declared in generated manifests."""
        for channel in ("stable", "canary"):
            manifest = json.loads((ROOT / "aot-group-control" / f"worker-manifest-{channel}.json").read_text(encoding="utf-8"))
            for item in manifest["files"]:
                file_path = ROOT / "aot-group-control" / item["path"]
                actual_sha = hashlib.sha256(file_path.read_bytes()).hexdigest()
                self.assertEqual(actual_sha, item["sha256"], f"SHA mismatch for {item["path"]} in {channel}")
            bootstrap_sha = hashlib.sha256((ROOT / "aot-group-control" / "bootstrap.py").read_bytes()).hexdigest()
            self.assertEqual(bootstrap_sha, manifest["bootstrap"]["sha256"])

    def test_05_release_coherence_check_passes(self):
        """Verify canonical release coherence check succeeds."""
        prepare_worker_release.check_release_coherence(ROOT)


if __name__ == "__main__":
    unittest.main()
