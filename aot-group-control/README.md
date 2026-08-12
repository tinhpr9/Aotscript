# AOT fleet worker

Each registered Device ID maintains one authenticated hibernating WebSocket.
AOT Hub selects any ONLINE subset and sends immutable, expiring batch actions.
ACKs are keyed by `device_id` and `action_id`; dedupe prevents re-execution.

Supported fleet actions:

- `OPEN_SWIFT_BACKUP`: open the launcher activity for `org.swiftapps.swiftbackup`.
- `OPEN_SWIFT_APPS`: require Swift Backup in foreground, resolve the unique Apps
  control semantically, verify the UI fingerprint immediately before clicking,
  and verify the Apps screen semantic postcondition afterward.
- `UPDATE_WORKER`: dynamic Canary followed by Stable groups of at most five,
  retaining staged activation, health ACK, `current`, `last_good`, and rollback.

There is no cross-device screen preview, coordinate command, capture, or replay.
Configuration contains only device-local identity and optional startup package.
The isolated legacy release bridge exists only so deployed pre-fleet workers can
receive one authenticated `UPDATE_WORKER` and migrate forward.
