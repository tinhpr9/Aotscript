import json
import hashlib
import os

files = [
    "relay.py",
    "runtime.py",
    "controller.py",
    "updater.py",
    "e2e.py",
    "worker_smoke_test.py",
    "worker-release-schema.json",
    "msetup_registration.py",
    "legacy_relay_bridge.py"
]

def hash_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

old_v = "2026.08.11.12"
new_v = "2026.08.11.13"

# Bump version in Python files
def sed(path):
    with open(path) as f:
        content = f.read()
    content = content.replace(old_v, new_v)
    with open(path, "w") as f:
        f.write(content)

for p in ["relay.py", "worker_smoke_test.py", "release_update_selftest.py", "relay_selftest.py"]:
    sed(f"aot-group-control/{p}")
for p in [".github/workflows/release-worker.yml", "tests/test_backup_restore_data.py", "tests/test_worker_release_workflow.py", "cloudflare-worker/fleet-state.js"]:
    if os.path.exists(p):
        sed(p)

for c in ["stable", "canary"]:
    path = f"aot-group-control/worker-manifest-{c}.json"
    with open(path) as f:
        manifest = json.loads(f.read())
    
    manifest["version"] = f"aot-worker-{new_v}"
    
    if manifest.get("bootstrap"):
        manifest["bootstrap"]["url"] = manifest["bootstrap"]["url"].replace(old_v, new_v)
        # Note: bootstrap.py hash probably hasn't changed if it wasn't modified, but if we wanted to hash it we would.
    
    # Update legacy_relay_bridge URL and sha
    manifest["url"] = manifest["url"].replace(old_v, new_v)
    if os.path.exists("aot-group-control/legacy_relay_bridge.py"):
        manifest["sha256"] = hash_file("aot-group-control/legacy_relay_bridge.py")
    else:
        # It's actually relay.py
        manifest["sha256"] = hash_file("aot-group-control/relay.py")

    for f in manifest["files"]:
        f["url"] = f["url"].replace(old_v, new_v)
        if os.path.exists(f"aot-group-control/{f['path']}"):
            f["sha256"] = hash_file(f"aot-group-control/{f['path']}")
        elif f['path'] == "legacy_relay_bridge.py":
            f["sha256"] = hash_file("aot-group-control/relay.py")
            
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

print("Bumped version to " + new_v)
