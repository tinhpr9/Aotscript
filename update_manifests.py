import json
import hashlib
from pathlib import Path

ROOT = Path("aot-group-control")

def get_hash(filename):
    return hashlib.sha256((ROOT / filename).read_bytes()).hexdigest()

files_to_hash = ["relay.py", "runtime.py", "controller.py", "updater.py", "e2e.py", "worker_smoke_test.py", "worker-release-schema.json", "msetup_registration.py", "bootstrap.py", "legacy_relay_bridge.py"]

for manifest_name in ["worker-manifest-canary.json", "worker-manifest-stable.json"]:
    manifest_path = ROOT / manifest_name
    data = json.loads(manifest_path.read_text())
    data["version"] = "aot-worker-2026.08.14.04"
    data["url"] = data["url"].replace("2026.08.14.03", "2026.08.14.04")
    data["sha256"] = get_hash("legacy_relay_bridge.py")
    if "bootstrap" in data:
        data["bootstrap"]["url"] = data["bootstrap"]["url"].replace("2026.08.14.03", "2026.08.14.04")
        data["bootstrap"]["sha256"] = get_hash("bootstrap.py")

    for file_obj in data["files"]:
        filename = file_obj["path"]
        file_obj["url"] = file_obj["url"].replace("2026.08.14.03", "2026.08.14.04")
        if filename in files_to_hash:
            file_obj["sha256"] = get_hash(filename)
    manifest_path.write_text(json.dumps(data, indent=2) + "\n")
print("Manifests updated.")
