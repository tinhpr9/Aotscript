#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import pathlib
import py_compile
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("legacy_bridge_tested", HERE / "legacy_relay_bridge.py")
assert spec and spec.loader
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class Reply:
    def __init__(self, content: bytes):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return self.content


with tempfile.TemporaryDirectory(prefix="aot-legacy-protocol-") as folder:
    root = pathlib.Path(folder)
    state = root / "state"
    runtime = root / "runtime"
    state.mkdir()
    pending = state / "pending.json"
    pending.write_text(json.dumps({
        "action_id": "worker-canary-fixture-p2",
        "channel": "stable",
        "device_id": "m901",
        "reference_device_id": "m902",
    }), encoding="utf-8")
    bridge.LEGACY_PENDING = pending
    config = state / "aot.json"
    config.write_text(json.dumps({
        "enabled": True, "role": "follower", "session_id": "fixture-session",
        "reference_device_id": "m902",
    }), encoding="utf-8")
    bridge.AOT_CONFIG = config
    bridge.SUPERVISOR_ROOT = runtime

    launcher = b"print('launcher fixture')\n"
    bootstrap = b"import sys\nraise SystemExit(0)\n"
    content_by_url = {"https://fixture/launcher": launcher, "https://fixture/bootstrap": bootstrap}
    bridge.ASSETS = {
        "bootstrap_launcher.py": {
            "url": "https://fixture/launcher", "sha256": hashlib.sha256(launcher).hexdigest(),
        },
        "bootstrap.py": {
            "url": "https://fixture/bootstrap", "sha256": hashlib.sha256(bootstrap).hexdigest(),
        },
    }
    bridge.urllib.request.urlopen = lambda request, timeout=0: Reply(content_by_url[request.full_url])
    executed = {}
    bridge.os.execv = lambda executable, command: executed.update(executable=executable, command=command)
    assert bridge.main() == 0
    assert (runtime / "bootstrap.py").read_bytes() == bootstrap
    assert (runtime / "bootstrap_launcher.py").read_bytes() == launcher
    assert "--action-id" in executed["command"]
    assert "worker-canary-fixture-p2" in executed["command"]
    assert "--channel" in executed["command"] and "stable" in executed["command"]

    current = (runtime / "bootstrap.py").read_bytes()
    bridge.ASSETS["bootstrap.py"]["sha256"] = "0" * 64
    assert bridge.main() == 2
    assert (runtime / "bootstrap.py").read_bytes() == current
    py_compile.compile(str(runtime / "bootstrap.py"), doraise=True)

print("AOT_LEGACY_RELAY_BRIDGE_SELFTEST=OK")
