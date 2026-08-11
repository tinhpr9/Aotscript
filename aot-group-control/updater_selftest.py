#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("aot_updater_selftest_target", ROOT / "updater.py")
assert spec and spec.loader
updater = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = updater
spec.loader.exec_module(updater)

assert updater.channel_for_device("m37") == "canary"
assert updater.channel_for_device("M117") == "canary"
assert updater.channel_for_device("m38") == "stable"

with tempfile.TemporaryDirectory(prefix="aot-updater-test-") as folder:
    base = pathlib.Path(folder)
    state = base / "state"
    relay = base / "relay.py"
    source = base / "new-relay.py"
    relay.write_text("OLD = True\n", encoding="utf-8")
    source.write_text("WORKER_VERSION = 'test-v2'\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    updater.ROOT = base
    updater.RELAY_PATH = relay
    updater.STATE_ROOT = state
    updater.PENDING_PATH = state / "pending.json"
    updater.HEALTH_PATH = state / "health.json"
    updater.VERSION_PATH = state / "version.json"
    updater.load_manifest = lambda channel: {
        "version": "test-v2", "url": "https://example.invalid/relay.py",
        "sha256": digest, "channel": channel,
    }
    updater._download = lambda url, destination: destination.write_bytes(source.read_bytes())
    pending = updater.prepare_update("m37", "update-test-1", "canary")
    assert pending and relay.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert pathlib.Path(pending["backup"]).read_text(encoding="utf-8") == "OLD = True\n"
    updater.rollback(pending)
    assert relay.read_text(encoding="utf-8") == "OLD = True\n"

    bad = "not python: ["
    source.write_text(bad, encoding="utf-8")
    updater.load_manifest = lambda channel: {
        "version": "test-v3", "url": "https://example.invalid/relay.py",
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "channel": channel,
    }
    try:
        updater.prepare_update("m38", "update-test-2", "stable")
    except Exception:
        pass
    else:
        raise AssertionError("py_compile accepted invalid worker")
    assert relay.read_text(encoding="utf-8") == "OLD = True\n"

    try:
        updater.prepare_update("m37", "update-test-3", "stable")
    except updater.UpdateError as exc:
        assert str(exc) == "channel_not_allowed_for_device"
    else:
        raise AssertionError("cross-channel update accepted")

print("AOT_UPDATER_SELFTEST=OK")
