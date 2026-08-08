# AOT Group Control

Current implementation includes:
- root Android controller with sanitized UI hierarchy;
- stable structural fingerprint and semantic selector resolution;
- preview-coordinate to semantic-node resolution through unique UI ancestors;
- realtime reference/follower WebSocket relay;
- follower ACK, dedupe and out-of-sync guard;
- AOT Hub live reference/follower status and previews;
- multi-follower targeting inside one session;
- persistent runtime configuration and exact-process start/stop management;
- Termux:Boot auto-start when `aot_group_config.json` is enabled;
- one multi-action E2E batch covering semantic taps, Back, swipe, preview metadata,
  dedupe and out-of-sync protection.

Runtime config path:
`/storage/emulated/0/Download/Shouko/aot_group_config.json`

The config contains no Worker secret. Worker credentials continue to be read only from
`/storage/emulated/0/Download/Shouko/agent_config.json` by the relay at runtime.

Example reference configuration:

```bash
python3 ~/.aot-group-control/runtime.py configure \
  --role reference \
  --session m37-m117-p3
```

Example follower configuration:

```bash
python3 ~/.aot-group-control/runtime.py configure \
  --role follower \
  --session m37-m117-p3 \
  --reference-device m37
```

Start/status:

```bash
python3 ~/.aot-group-control/runtime.py start
python3 ~/.aot-group-control/runtime.py status
```

Reference batch test:

```bash
python3 ~/.aot-group-control/runtime.py e2e \
  --follower m117 \
  --package org.swiftapps.swiftbackup
```

Security properties:
- no password, cookie, Shouko key, token or private config value is printed;
- stale UI actions are rejected by fingerprint preconditions;
- an unresolved preview tap is not converted to a blind coordinate tap;
- runtime process matching is exact to relay path, role and session;
- reboot auto-start is opt-in through the explicit runtime config.
