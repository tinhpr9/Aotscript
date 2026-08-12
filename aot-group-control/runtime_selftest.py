#!/usr/bin/env python3
import importlib.util, pathlib, sys
root = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("runtime", root / "runtime.py")
module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
config = module.validate_config_data({"version": 2, "enabled": True, "device_id": "M301", "role": "follower", "session_id": "legacy", "reference_device_id": "m1"}, local_device_id="m301")
assert config == {"version": 3, "device_id": "m301", "enabled": True, "open_package": None}
assert "role" not in config and "session_id" not in config and "reference_device_id" not in config
parser = module.build_parser(); args = parser.parse_args(["configure", "--open-package", "org.swiftapps.swiftbackup"])
assert args.command == "configure"
print("AOT_RUNTIME_SELFTEST=OK")
