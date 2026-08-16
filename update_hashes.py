import json
import hashlib

def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

relay_sha = sha256_file("/root/Aotscript/aot-group-control/relay.py")
manifests = ["/root/Aotscript/aot-group-control/worker-manifest-canary.json", "/root/Aotscript/aot-group-control/worker-manifest-stable.json"]

for m_path in manifests:
    with open(m_path, "r") as f:
        data = json.load(f)
    data["release_version"] = "aot-worker-2026.08.16.01"
    for item in data.get("assets", []):
        if item["path"] == "relay.py":
            item["sha256"] = relay_sha
    with open(m_path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

print("Hashes updated")
