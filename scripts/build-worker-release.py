#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import re
import shutil
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "aot-group-control"
FILES = [
    "relay.py", "runtime.py", "controller.py", "updater.py", "e2e.py",
    "worker_smoke_test.py", "worker-release-schema.json",
    "msetup_registration.py", "legacy_relay_bridge.py",
]
SUPERVISOR = ["bootstrap.py", "bootstrap_launcher.py"]


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reproduction_metadata(folder: pathlib.Path | None) -> tuple[str | None, dict[str, zipfile.ZipInfo]]:
    if folder is None:
        return None, {}
    manifest = json.loads((folder / "worker-manifest.json").read_text(encoding="utf-8"))
    built_at = manifest.get("built_at")
    if not isinstance(built_at, str) or not built_at:
        raise SystemExit("release_reproduction_built_at_invalid")
    with zipfile.ZipFile(folder / "worker-bundle.zip") as archive:
        infos = {info.filename: info for info in archive.infolist()}
    expected = set(FILES + SUPERVISOR)
    if set(infos) != expected:
        raise SystemExit("release_reproduction_zip_entries_invalid")
    return built_at, infos


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--source-root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--reproduce-metadata-from", type=pathlib.Path)
    args = parser.parse_args()
    if not re.fullmatch(r"2026\.08\.11\.8", args.version):
        raise SystemExit("release_version_mismatch")
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        raise SystemExit("release_commit_invalid")
    worker_version = "aot-worker-" + args.version
    source_dir = args.source_root / "aot-group-control"
    source = (source_dir / "relay.py").read_text(encoding="utf-8")
    if f'WORKER_VERSION = "{worker_version}"' not in source:
        raise SystemExit("code_worker_version_mismatch")
    output = args.output
    if output.exists() and any(output.iterdir()):
        raise SystemExit("release_output_not_empty")
    output.mkdir(parents=True, exist_ok=True)
    tag = "worker-v" + args.version
    base = f"https://github.com/tinhpr9/Aotscript/releases/download/{tag}"
    assets = []
    built_at, zip_infos = _reproduction_metadata(args.reproduce_metadata_from)
    for name in FILES + SUPERVISOR:
        source_path = source_dir / name
        target = output / name
        shutil.copyfile(source_path, target)
        sha = digest(target)
        assets.append({
            "path": name, "asset_name": name, "url": f"{base}/{name}",
            "size": target.stat().st_size, "sha256": sha,
            "github_digest": "sha256:" + sha,
        })
    bundle = output / "worker-bundle.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES + SUPERVISOR:
            if zip_infos:
                original = zip_infos[name]
                info = zipfile.ZipInfo(name, original.date_time)
                info.compress_type = original.compress_type
                info.comment = original.comment
                info.extra = original.extra
                info.create_system = original.create_system
                info.create_version = original.create_version
                info.extract_version = original.extract_version
                info.external_attr = original.external_attr
                info.internal_attr = original.internal_attr
                info.flag_bits = original.flag_bits
                archive.writestr(info, (output / name).read_bytes())
            else:
                archive.write(output / name, arcname=name)
    manifest = {
        "schema_version": 3,
        "worker_version": worker_version,
        "tag": tag,
        "commit_sha": args.commit,
        "asset_name": "worker-bundle.zip",
        "asset_size": bundle.stat().st_size,
        "asset_sha256": digest(bundle),
        "minimum_protocol": "github-release-v1",
        "minimum_bootstrap_version": 2,
        "built_at": built_at or datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "files": [item for item in assets if item["path"] in FILES],
        "bootstrap": next(item for item in assets if item["path"] == "bootstrap.py") | {"version": 5},
    }
    manifest_path = output / "worker-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksums = output / "worker-checksums.sha256"
    checksum_names = sorted(FILES + SUPERVISOR + ["worker-manifest.json", "worker-bundle.zip"])
    checksums.write_text("".join(f"{digest(output / name)}  {name}\n" for name in checksum_names), encoding="utf-8")
    print(json.dumps({"version": worker_version, "tag": tag, "assets": sorted(path.name for path in output.iterdir())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
