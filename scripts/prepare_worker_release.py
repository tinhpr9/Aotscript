#!/usr/bin/env python3
"""Canonical Worker Release Preparation & Verification Tool.

Usage:
  python3 scripts/prepare_worker_release.py --check
  python3 scripts/prepare_worker_release.py --version 2026.08.23.04
  python3 scripts/prepare_worker_release.py --bump
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_version


def get_current_commit(repo_root: pathlib.Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        commit = proc.stdout.strip()
        if re.fullmatch(r"[0-9a-f]{40}", commit):
            return commit
    except Exception:
        pass
    return "0000000000000000000000000000000000000000"


def sync_release_mirrors(repo_root: pathlib.Path, version: str) -> None:
    worker_version = f"aot-worker-{version}"
    tag = f"worker-v{version}"

    # 1. worker-release.json
    version_file = repo_root / "aot-group-control" / "worker-release.json"
    release_version.save_canonical_version(version_file, version)

    # 2. relay.py
    relay_path = repo_root / "aot-group-control" / "relay.py"
    relay_text = relay_path.read_text(encoding="utf-8")
    relay_text = re.sub(
        r'WORKER_VERSION = "aot-worker-[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]{2}"',
        f'WORKER_VERSION = "{worker_version}"',
        relay_text,
    )
    relay_path.write_text(relay_text, encoding="utf-8")

    # 3. fleet-state.js
    fleet_path = repo_root / "cloudflare-worker" / "fleet-state.js"
    fleet_text = fleet_path.read_text(encoding="utf-8")
    fleet_text = re.sub(
        r'const AOT_WORKER_VERSION = "aot-worker-[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]{2}";',
        f'const AOT_WORKER_VERSION = "{worker_version}";',
        fleet_text,
    )
    fleet_text = re.sub(
        r'const AOT_WORKER_TAG = "worker-v[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]{2}";',
        f'const AOT_WORKER_TAG = "{tag}";',
        fleet_text,
    )
    fleet_path.write_text(fleet_text, encoding="utf-8")

    # 4. bootstrap.py (INITIAL_RELEASE_SPEC)
    boot_path = repo_root / "aot-group-control" / "bootstrap.py"
    boot_text = boot_path.read_text(encoding="utf-8")
    boot_text = re.sub(
        r'"version": "aot-worker-[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]{2}"',
        f'"version": "{worker_version}"',
        boot_text,
    )
    boot_text = re.sub(
        r'"tag": "worker-v[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]{2}"',
        f'"tag": "{tag}"',
        boot_text,
    )
    boot_text = re.sub(
        r'"url": "https://github\.com/tinhpr9/Aotscript/releases/download/worker-v[0-9]{4}\.[0-9]{2}\.[0-9]{2}\.[0-9]{2}/worker-manifest\.json"',
        f'"url": "https://github.com/tinhpr9/Aotscript/releases/download/{tag}/worker-manifest.json"',
        boot_text,
    )
    boot_path.write_text(boot_text, encoding="utf-8")


def generate_manifests(repo_root: pathlib.Path, version: str, commit_sha: str) -> None:
    worker_version = f"aot-worker-{version}"
    tag = f"worker-v{version}"
    with tempfile.TemporaryDirectory(prefix="aot-build-") as temp_dir:
        out_path = pathlib.Path(temp_dir) / "release"
        build_script = repo_root / "scripts" / "build-worker-release.py"
        subprocess.run(
            [
                sys.executable,
                str(build_script),
                "--version",
                version,
                "--commit",
                commit_sha,
                "--output",
                str(out_path),
                "--source-root",
                str(repo_root),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        schema3_manifest = json.loads((out_path / "worker-manifest.json").read_text(encoding="utf-8"))

        for channel in ("stable", "canary"):
            manifest_v2 = {
                "schema_version": 2,
                "version": worker_version,
                "channel": channel,
                "minimum_bootstrap_version": 2,
                "legacy_asset_url": f"https://github.com/tinhpr9/Aotscript/releases/download/{tag}/legacy_relay_bridge.py",
                "legacy_asset_sha256": next(f["sha256"] for f in schema3_manifest["files"] if f["path"] == "legacy_relay_bridge.py"),
                "bootstrap": {
                    "version": 7,
                    "url": f"https://github.com/tinhpr9/Aotscript/releases/download/{tag}/bootstrap.py",
                    "sha256": schema3_manifest["bootstrap"]["sha256"],
                },
                "files": [
                    {
                        "path": item["path"],
                        "url": item["url"],
                        "sha256": item["sha256"],
                    }
                    for item in schema3_manifest["files"]
                ],
                "release_version": worker_version,
            }
            target_manifest = repo_root / "aot-group-control" / f"worker-manifest-{channel}.json"
            target_manifest.write_text(json.dumps(manifest_v2, indent=2) + "\n", encoding="utf-8")


def check_release_coherence(repo_root: pathlib.Path) -> None:
    version_file = repo_root / "aot-group-control" / "worker-release.json"
    canonical = release_version.load_canonical_version(version_file)
    version = canonical["version"]
    worker_version = canonical["worker_version"]
    tag = canonical["tag"]

    errors = []

    # Check relay.py
    relay_path = repo_root / "aot-group-control" / "relay.py"
    relay_text = relay_path.read_text(encoding="utf-8")
    if f'WORKER_VERSION = "{worker_version}"' not in relay_text:
        errors.append(f"relay.py WORKER_VERSION does not match canonical {worker_version}")

    # Check fleet-state.js
    fleet_path = repo_root / "cloudflare-worker" / "fleet-state.js"
    fleet_text = fleet_path.read_text(encoding="utf-8")
    if f'const AOT_WORKER_VERSION = "{worker_version}";' not in fleet_text:
        errors.append(f"fleet-state.js AOT_WORKER_VERSION does not match canonical {worker_version}")
    if f'const AOT_WORKER_TAG = "{tag}";' not in fleet_text:
        errors.append(f"fleet-state.js AOT_WORKER_TAG does not match canonical {tag}")

    # Check bootstrap.py
    boot_path = repo_root / "aot-group-control" / "bootstrap.py"
    boot_text = boot_path.read_text(encoding="utf-8")
    if f'"version": "{worker_version}"' not in boot_text:
        errors.append(f"bootstrap.py INITIAL_RELEASE_SPEC version does not match canonical {worker_version}")
    if f'"tag": "{tag}"' not in boot_text:
        errors.append(f"bootstrap.py INITIAL_RELEASE_SPEC tag does not match canonical {tag}")

    # Check manifests
    for channel in ("stable", "canary"):
        man_path = repo_root / "aot-group-control" / f"worker-manifest-{channel}.json"
        if not man_path.is_file():
            errors.append(f"Missing manifest {man_path}")
            continue
        try:
            m = json.loads(man_path.read_text(encoding="utf-8"))
            if m.get("version") != worker_version:
                errors.append(f"{man_path.name} version {m.get('version')} != {worker_version}")
        except Exception as e:
            errors.append(f"Invalid JSON in {man_path}: {e}")

    if errors:
        print("RELEASE_COHERENCE_CHECK=FAIL")
        for err in errors:
            print(f" - {err}")
        sys.exit(1)

    print("RELEASE_COHERENCE_CHECK=PASS")
    print(f"CANONICAL_VERSION={version} ({worker_version})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="Verify release coherence without modifying files")
    parser.add_argument("--version", help="Set specific canonical version (YYYY.MM.DD.NN)")
    parser.add_argument("--bump", action="store_true", help="Bump to next sequential CalVer")
    parser.add_argument("--commit", help="Git commit SHA (defaults to HEAD)")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    version_file = repo_root / "aot-group-control" / "worker-release.json"

    if args.check:
        check_release_coherence(repo_root)
        return 0

    if args.bump and args.version:
        print("Error: Specify --bump OR --version, not both.", file=sys.stderr)
        return 1

    current = release_version.load_canonical_version(version_file)["version"]
    if args.bump:
        target_version = release_version.next_calver(current).serialize()
    elif args.version:
        target_version = release_version.parse_calver(args.version).serialize()
    else:
        target_version = current

    commit_sha = args.commit or get_current_commit(repo_root)
    print(f"Preparing worker release {target_version} (commit {commit_sha})...")

    sync_release_mirrors(repo_root, target_version)
    generate_manifests(repo_root, target_version, commit_sha)
    check_release_coherence(repo_root)
    print(f"Successfully prepared release {target_version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
