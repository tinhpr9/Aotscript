#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("aot_bootstrap_selftest_target", HERE / "bootstrap.py")
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


assert module.normalize_channel("canary") == "canary"
assert module.normalize_channel("STABLE") == "stable"
assert module.normalize_channel("other") is None
assert module.DEFAULT_STARTUP_CHANNEL == "stable"

for channel in ("canary", "stable"):
    mock_files = []
    for p in ("relay.py", "runtime.py", "controller.py", "updater.py", "e2e.py", "worker_smoke_test.py", "worker-release-schema.json", "msetup_registration.py", "legacy_relay_bridge.py"):
        mock_files.append({"path": p, "sha256": digest(HERE / p), "url": "https://github.com/tinhpr9/Aotscript/releases/download/worker-v2026.08.14.06/" + p})
    mock_manifest = {
        "schema_version": 2,
        "version": "aot-worker-2026.08.14.06",
        "channel": channel,
        "minimum_bootstrap_version": 2,
        "files": mock_files,
        "bootstrap": {
            "version": module.BOOTSTRAP_RELEASE_VERSION,
            "sha256": digest(HERE / "bootstrap.py"),
            "url": "https://github.com/tinhpr9/Aotscript/releases/download/worker-v2026.08.14.06/bootstrap.py"
        }
    }
    actual = module.validate_manifest(mock_manifest, channel)
    assert actual["bootstrap"]["version"] == module.BOOTSTRAP_RELEASE_VERSION
    for item in actual["files"]:
        assert digest(HERE / item["path"]) == item["sha256"], item["path"]
    assert digest(HERE / "bootstrap.py") == actual["bootstrap"]["sha256"]

with tempfile.TemporaryDirectory(prefix="aot-bundle-updater-") as folder:
    base = pathlib.Path(folder)
    source = base / "source"
    source.mkdir()
    root = base / "bootstrap-root"
    state = base / "protected-state"
    state.mkdir()
    protected = {
        "device_id.txt": "m901\n",
        "device_group.txt": "NOVA\n",
        "aot_group_config.json": json.dumps({"enabled": True, "role": "reference", "session_id": "fixture"}),
        "agent_config.json": json.dumps({"worker_report_url": "https://example.invalid/report", "agent_report_secret": "fixture-secret"}),
    }
    for name, value in protected.items():
        (state / name).write_text(value, encoding="utf-8")

    files = {
        "relay.py": "WORKER_VERSION = 'fixture-v2'\n",
        "runtime.py": "RUNTIME = 2\n",
        "controller.py": "CONTROLLER = 2\n",
        "updater.py": "UPDATER_API_VERSION = 2\n",
        "e2e.py": "E2E = 2\n",
        "worker_smoke_test.py": "raise SystemExit(0)\n",
        "msetup_registration.py": "REGISTRATION = 2\n",
        "legacy_relay_bridge.py": "BRIDGE = 1\n",
        "worker-release-schema.json": json.dumps({"schema_version": 2}),
    }
    for name, value in files.items():
        (source / name).write_text(value, encoding="utf-8")

    module.ROOT = root
    module.RELEASES = root / "releases"
    module.CURRENT = root / "current"
    module.LAST_GOOD = root / "last_good"
    module.LOCK_PATH = root / "supervisor.lock"
    module.PENDING_PATH = root / "update_pending.json"
    module.HEALTH_PATH = root / "update_health.json"
    module.VERSION_PATH = root / "installed_release.json"
    module.STATE_ROOT = state
    module.CONFIG_PATH = state / "aot_group_config.json"
    module.DEVICE_ID_PATH = state / "device_id.txt"
    module.AGENT_CONFIG_PATH = state / "agent_config.json"

    def manifest(version: str = "fixture-v2"):
        return module.validate_manifest({
            "schema_version": 2,
            "version": version,
            "channel": "canary",
            "minimum_bootstrap_version": 2,
            "files": [
                {"path": name, "url": f"https://raw.githubusercontent.com/tinhpr9/Aotscript/main/aot-group-control/{name}", "sha256": digest(source / name)}
                for name in files
            ],
        }, "canary")

    module._download = lambda url, destination, expected_size=None: destination.write_bytes(
        (source / pathlib.PurePosixPath(url).name).read_bytes()
    )

    before = {name: (state / name).read_bytes() for name in protected}
    release = module.stage_release(manifest(), "action-1")
    assert {item.name for item in release.iterdir() if item.name != "__pycache__"} == set(files)
    assert all((state / name).read_bytes() == value for name, value in before.items())

    legacy = module.RELEASES / "fixture-v1"
    legacy.mkdir()
    (legacy / "relay.py").write_text("OLD = True\n", encoding="utf-8")
    module._atomic_link(module.CURRENT, legacy)
    module._atomic_link(module.LAST_GOOD, legacy)
    pending = {"action_id": "action-1", "version": "fixture-v2", "channel": "canary"}
    module.activate_release(release, pending)
    assert module._link_target(module.CURRENT) == release
    assert module.wait_for_health(pending, timeout=0) is False
    restored = module.rollback_release(pending)
    assert restored == legacy and module._link_target(module.CURRENT) == legacy

    # A broken release-side updater cannot damage the external supervisor rollback.
    module.activate_release(release, pending)
    (release / "updater.py").write_text("broken updater: [", encoding="utf-8")
    assert module.wait_for_health(pending, timeout=0) is False
    assert module.rollback_release(pending) == legacy
    (source / "updater.py").write_text(files["updater.py"], encoding="utf-8")

    bad_hash = manifest("fixture-bad-hash")
    bad_hash["files"][0]["sha256"] = "0" * 64
    try:
        module.stage_release(bad_hash, "action-hash")
    except module.BootstrapError as exc:
        assert "sha256_mismatch" in str(exc)
    else:
        raise AssertionError("bad hash accepted")
    assert module._link_target(module.CURRENT) == legacy

    (source / "relay.py").write_text("not python: [", encoding="utf-8")
    try:
        module.stage_release(manifest("fixture-bad-syntax"), "action-syntax")
    except module.BootstrapError as exc:
        assert "py_compile_failed" in str(exc)
    else:
        raise AssertionError("bad syntax accepted")
    assert module._link_target(module.CURRENT) == legacy

    with module.supervisor_lock():
        try:
            with module.supervisor_lock():
                pass
        except module.BootstrapError as exc:
            assert str(exc) == "update_already_running"
        else:
            raise AssertionError("duplicate supervisor acquired lock")

    launcher_spec = importlib.util.spec_from_file_location(
        "aot_bootstrap_launcher_selftest", HERE / "bootstrap_launcher.py"
    )
    assert launcher_spec and launcher_spec.loader
    launcher = importlib.util.module_from_spec(launcher_spec)
    launcher_spec.loader.exec_module(launcher)
    launcher.ROOT = root
    launcher.ACTIVE = root / "bootstrap-under-test.py"
    launcher.LAST_GOOD = root / "bootstrap-under-test.py.last_good"
    launcher.ACTIVE.write_text("broken bootstrap: [", encoding="utf-8")
    launcher.LAST_GOOD.write_text("raise SystemExit(0)\n", encoding="utf-8")
    assert launcher.main(["self-test"]) == 0
    assert launcher.ACTIVE.read_bytes() == launcher.LAST_GOOD.read_bytes()

    # Bootstrap upgrades are staged and cannot replace the active copy when bad.
    active_bootstrap = root / "bootstrap.py"
    active_bootstrap.write_text("OLD_BOOTSTRAP = True\n", encoding="utf-8")
    (source / "bootstrap.py").write_text("broken bootstrap: [", encoding="utf-8")
    broken_bootstrap = {
        "minimum_bootstrap_version": 2,
        "bootstrap": {
            "version": module.BOOTSTRAP_RELEASE_VERSION + 1,
            "url": "https://raw.githubusercontent.com/tinhpr9/Aotscript/main/aot-group-control/bootstrap.py",
            "sha256": digest(source / "bootstrap.py"),
        },
    }
    try:
        module.maybe_upgrade_bootstrap(broken_bootstrap)
    except module.BootstrapError as exc:
        assert str(exc) == "bootstrap_py_compile_failed"
    else:
        raise AssertionError("broken bootstrap upgrade accepted")
    assert active_bootstrap.read_text(encoding="utf-8") == "OLD_BOOTSTRAP = True\n"

print("AOT_BUNDLE_UPDATER_SELFTEST=OK")
