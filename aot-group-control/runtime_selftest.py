#!/data/data/com.termux/files/usr/bin/python3
from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
TARGET = ROOT / "runtime.py"

spec = importlib.util.spec_from_file_location(
    "aot_runtime",
    TARGET,
)
if spec is None or spec.loader is None:
    raise SystemExit("RUNTIME_SELFTEST_IMPORT=FAILED")

module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.normalize_device_id("M117") == "m117"
assert module.normalize_device_id("m0") is None
assert module.normalize_session_id("m37-m117-p3") == "m37-m117-p3"
assert module.normalize_session_id("bad session") is None
assert module.normalize_package("org.swiftapps.swiftbackup") == (
    "org.swiftapps.swiftbackup"
)
assert module.normalize_package("bad package") is None

reference = module.validate_config_data(
    {
        "enabled": True,
        "role": "reference",
        "session_id": "m37-m117-p3",
        "open_package": "org.swiftapps.swiftbackup",
    },
    local_device_id="m37",
)
assert reference["role"] == "reference"
assert reference["reference_device_id"] is None

follower = module.validate_config_data(
    {
        "enabled": True,
        "role": "follower",
        "session_id": "m37-m117-p3",
        "reference_device_id": "M37",
    },
    local_device_id="m117",
)
assert follower["reference_device_id"] == "m37"

try:
    module.validate_config_data(
        {
            "enabled": True,
            "role": "follower",
            "session_id": "m37-m117-p3",
            "reference_device_id": "m117",
        },
        local_device_id="m117",
    )
except module.AotRuntimeError:
    pass
else:
    raise AssertionError("self-reference follower config accepted")

print("AOT_RUNTIME_SELFTEST=OK")
