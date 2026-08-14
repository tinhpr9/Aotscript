import json
import hashlib
import os

def hash_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

old_versions = ["2026.08.11.12", "2026.08.11.13", "2026.08.14.01", "2026.08.14.02"]
new_v = "2026.08.14.03"

def sed(path):
    with open(path) as f:
        content = f.read()
    for ov in old_versions:
        content = content.replace(ov, new_v)
    with open(path, "w") as f:
        f.write(content)

for p in ["relay.py", "worker_smoke_test.py", "release_update_selftest.py", "relay_selftest.py"]:
    sed(f"aot-group-control/{p}")
for p in [".github/workflows/release-worker.yml", "tests/test_backup_restore_data.py", "tests/test_worker_release_workflow.py", "cloudflare-worker/fleet-state.js", "scripts/build-worker-release.py"]:
    if os.path.exists(p):
        sed(p)

for c in ["stable", "canary"]:
    path = f"aot-group-control/worker-manifest-{c}.json"
    with open(path) as f:
        manifest = json.loads(f.read())
    
    manifest["version"] = f"aot-worker-{new_v}"
    
    if manifest.get("bootstrap"):
        for ov in old_versions:
            manifest["bootstrap"]["url"] = manifest["bootstrap"]["url"].replace(ov, new_v)
    
    for ov in old_versions:
        manifest["url"] = manifest["url"].replace(ov, new_v)
        
    if os.path.exists("aot-group-control/legacy_relay_bridge.py"):
        manifest["sha256"] = hash_file("aot-group-control/legacy_relay_bridge.py")
    else:
        manifest["sha256"] = hash_file("aot-group-control/relay.py")

    for f in manifest["files"]:
        for ov in old_versions:
            f["url"] = f["url"].replace(ov, new_v)
        if os.path.exists(f"aot-group-control/{f['path']}"):
            f["sha256"] = hash_file(f"aot-group-control/{f['path']}")
        elif f['path'] == "legacy_relay_bridge.py":
            f["sha256"] = hash_file("aot-group-control/relay.py")
            
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

print("Bumped version to " + new_v)
