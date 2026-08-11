# AOT worker release rules

These rules apply to every change under `aot-group-control/` and to AOT Hub
worker actions in `cloudflare-worker/`.

## Versioned releases are mandatory

- Treat `~/.aot-group-control/bootstrap_launcher.py` and `bootstrap.py` as the
  external supervisor. Worker code belongs in immutable
  `releases/<worker-version>/` directories and is selected only through the
  atomic `current` symlink. Never restore in-place replacement of `relay.py`.
- Every worker behavior change must bump `WORKER_VERSION`, update both channel
  manifests, and publish the complete release file set. At minimum that set is
  `relay.py`, `runtime.py`, `controller.py`, `updater.py`, `e2e.py`,
  `worker_smoke_test.py`, and `worker-release-schema.json`.
  Registration changes must also publish `msetup_registration.py`.
- Each manifest must retain schema version 2, its explicit channel, a unique
  release version, `minimum_bootstrap_version`, and the URL plus SHA-256 of
  every release file. Canary targets are selected dynamically by AOT Hub from
  the current session's ONLINE devices; never bind a channel to Device IDs.
- Do not add machine identity, group, session, secrets, or other device state
  to a release. Those values remain outside releases under the Shouko storage
  directory and must survive updates byte-for-byte.

## Safety and compatibility

- Download and validate every file in a staging directory. All Python files
  must pass `py_compile`, and `worker_smoke_test.py` must pass before an atomic
  activation.
- Keep both `current` and `last_good`. The external supervisor must require an
  ONLINE health ACK within 60 seconds and atomically roll back on failure.
- Bootstrap upgrades are two-stage: verify hash, compile, run `self-test`, save
  `bootstrap.py.last_good`, then atomically replace. The stable launcher must
  remain able to restore the previous bootstrap.
- UPDATE_WORKER remains deduplicated and serialized by the supervisor lock.
  Stable rollout groups contain at most five devices, and the next group may
  start only after every device in the current group reports `HEALTHY`.
- Legacy workers that do not advertise dynamic channel capability may receive
  both valid channel variants with one action ID and a single-device target.
  This bridge is capability-based, never identity-based; dedupe guarantees one
  execution, and capable workers receive only the requested channel.
- Preserve Durable Object WebSocket Hibernation and event-driven dashboard
  updates. Do not add periodic polling or a new paid service.

## Required release checks

- Test full multi-file updates, protected state preservation, bad hashes,
  syntax failures, smoke-test failures, health timeout rollback, broken
  release-side updater rollback, duplicate update locking, channel isolation,
  and 40-device rollout groups of five.
- Run all existing repository self-tests. If any JavaScript file changes, the
  pull request must include one ZIP containing every changed file.
- `FILTER_RESTORE_DATA` is outside the worker-updater scope and must not be
  introduced by updater releases.
