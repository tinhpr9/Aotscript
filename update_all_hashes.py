import json
import hashlib
import glob
import os

def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

manifests = ["/root/Aotscript/aot-group-control/worker-manifest-canary.json", "/root/Aotscript/aot-group-control/worker-manifest-stable.json"]

for m_path in manifests:
    with open(m_path, "r") as f:
        data = json.load(f)
    data["version"] = "aot-worker-2026.08.16.01"
    data["url"] = data["url"].replace("2026.08.15.01", "2026.08.16.01")
    data["bootstrap"]["url"] = data["bootstrap"]["url"].replace("2026.08.15.01", "2026.08.16.01")
    for item in data.get("files", []):
        file_path = os.path.join("/root/Aotscript/aot-group-control", item["path"])
        if os.path.exists(file_path):
            item["sha256"] = sha256_file(file_path)
            item["url"] = item["url"].replace("2026.08.15.01", "2026.08.16.01")
    with open(m_path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

print("Hashes updated")
