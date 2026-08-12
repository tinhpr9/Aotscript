#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import pathlib
import tempfile
import urllib.error

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("release_bootstrap_tested", HERE / "bootstrap.py")
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Reply:
    def __init__(self, data: bytes, status: int = 200):
        self.data, self.status = data, status
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, limit: int): return self.data[:limit]


with tempfile.TemporaryDirectory(prefix="aot-release-download-") as folder:
    root = pathlib.Path(folder)
    target = root / "asset"
    content = b"complete release asset"
    module.urllib.request.urlopen = lambda request, timeout=0: Reply(content)
    module._download("https://github.com/tinhpr9/Aotscript/releases/download/worker-v2026.08.11.9/asset", target, len(content))
    assert target.read_bytes() == content  # urllib follows the browser_download_url 302.
    for error in (
        urllib.error.HTTPError("https://github.com", 404, "missing", {}, None),
        TimeoutError("timeout"),
    ):
        module.urllib.request.urlopen = lambda request, timeout=0, error=error: (_ for _ in ()).throw(error)
        try:
            module._download("https://github.com/tinhpr9/Aotscript/releases/download/worker-v2026.08.11.9/asset", target)
        except module.BootstrapError as exc:
            assert str(exc) == "download_failed"
        else:
            raise AssertionError("failed HTTP request accepted")
    module.urllib.request.urlopen = lambda request, timeout=0: Reply(content[:-1])
    try:
        module._download("https://github.com/tinhpr9/Aotscript/releases/download/worker-v2026.08.11.9/asset", target, len(content))
    except module.BootstrapError as exc:
        assert str(exc) == "download_size_mismatch"
    else:
        raise AssertionError("partial release asset accepted")

    release_files = []
    for name in sorted(module.REQUIRED_FILES):
        body = (name + " fixture").encode()
        release_files.append({
            "path": name,
            "url": f"https://github.com/tinhpr9/Aotscript/releases/download/worker-v2026.08.11.9/{name}",
            "size": len(body), "sha256": hashlib.sha256(body).hexdigest(),
            "github_digest": "sha256:" + hashlib.sha256(body).hexdigest(),
        })
    manifest = {
        "schema_version": 3, "worker_version": "aot-worker-2026.08.11.9",
        "tag": "worker-v2026.08.11.9", "commit_sha": "a" * 40,
        "minimum_protocol": "github-release-v1", "minimum_bootstrap_version": 2,
        "files": release_files,
    }
    manifest_bytes = (json.dumps(manifest) + "\n").encode()
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    module.urllib.request.urlopen = lambda request, timeout=0: Reply(manifest_bytes)
    metadata = {
        "version": "aot-worker-2026.08.11.9", "tag": "worker-v2026.08.11.9",
        "commit_sha": "a" * 40,
        "manifest": {
            "name": "worker-manifest.json",
            "url": "https://github.com/tinhpr9/Aotscript/releases/download/worker-v2026.08.11.9/worker-manifest.json",
            "size": len(manifest_bytes), "sha256": manifest_sha,
            "github_digest": "sha256:" + manifest_sha,
        },
    }
    encoded = base64.urlsafe_b64encode(json.dumps(metadata).encode()).decode().rstrip("=")
    loaded = module.load_pinned_release(encoded, "canary")
    assert loaded["version"] == "aot-worker-2026.08.11.9" and loaded["channel"] == "canary"
    broken = json.loads(json.dumps(metadata))
    broken["manifest"]["github_digest"] = "sha256:" + "0" * 64
    encoded = base64.urlsafe_b64encode(json.dumps(broken).encode()).decode().rstrip("=")
    try:
        module.load_pinned_release(encoded, "stable")
    except module.BootstrapError as exc:
        assert str(exc) == "invalid_release_manifest_asset"
    else:
        raise AssertionError("GitHub digest mismatch accepted")

print("AOT_GITHUB_RELEASE_UPDATE_SELFTEST=OK")
