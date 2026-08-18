#!/data/data/com.termux/files/usr/bin/python3
"""Local fleet-worker smoke probe; no cross-device capture or replay."""
from __future__ import annotations
import argparse, json, pathlib, re

DEVICE_ID_PATH = pathlib.Path("/storage/emulated/0/Download/Shouko/device_id.txt")

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-device")
    args = parser.parse_args(argv)
    device_id = DEVICE_ID_PATH.read_text(encoding="utf-8").strip().lower()
    if not re.fullmatch(r"m[1-9]\d{0,5}", device_id):
        raise SystemExit("invalid_device_id")
    if args.expect_device and args.expect_device.lower() != device_id:
        raise SystemExit("device_id_mismatch")
    print(json.dumps({"ok": True, "protocol": "fleet-batch-v1", "device_id": device_id}))
    return 0

if __name__ == "__main__": raise SystemExit(main())
