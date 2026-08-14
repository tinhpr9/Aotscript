
import {
  DurableObject,
} from "cloudflare:workers";

const REVOCATION_RECHECK_MS =
  10 * 60 * 1000;
const COMMAND_QUEUE_PREFIX = "command_queue:";
const COMMAND_QUEUE_MAX = 64;
const COMMAND_BLOCK_MAX_BYTES = 64 * 1024;
const COMMAND_MAX_TARGETS = 1000;


const AOT_CONTROL_MAX_TARGETS = 128;
const AOT_BATCH_ACTION = "OPEN_SWIFT_BACKUP";
const AOT_APPS_ACTION = "OPEN_SWIFT_APPS";
// Fixed, allowlisted, fail-closed full-chain RESTORE_DATA backup action.
// Does not accept arbitrary labels, packages, or tap instructions from browser.
const AOT_BACKUP_RESTORE_DATA_ACTION = "BACKUP_RESTORE_DATA";
const AOT_BATCH_PACKAGE = "org.swiftapps.swiftbackup";
const AOT_BATCH_TTL_MS = 75 * 1000;
// Full chain bound: launch(45s) + multiple 30s UI transitions + final wait(45s) = ~300s total safe bound
const AOT_BACKUP_RESTORE_DATA_TTL_MS = 300 * 1000;
const AOT_UPDATE_ACTION = "UPDATE_WORKER";
const AOT_UPDATE_GROUP_SIZE = 5;
const AOT_UPDATE_TIMEOUT_MS = 75 * 1000;
const AOT_UPDATE_TERMINAL = new Set(["HEALTHY", "ROLLED_BACK", "FAILED", "SKIPPED_OFFLINE"]);
const AOT_WORKER_VERSION = "aot-worker-2026.08.14.03";
const AOT_WORKER_TAG = "worker-v2026.08.14.03";
const AOT_RELEASE_PROTOCOL = "github-release-v1";
const AOT_RELEASE_REPOSITORY = "tinhpr9/Aotscript";
const AOT_RELEASE_CACHE_MS = 5 * 60 * 1000;
const AOT_DYNAMIC_CHANNEL_CAPABILITY = "dynamic_update_channel";
const AOT_LEGACY_PROTOCOL_RETRY_MS = 12 * 1000;

function json(
  value,
  status = 200
) {
  return new Response(
    JSON.stringify(value),
    {
      status,
      headers: {
        "Content-Type":
          "application/json; charset=utf-8",
        "Cache-Control":
          "no-store, no-cache, must-revalidate",
      },
    }
  );
}

function validDeviceId(value) {
  const raw =
    String(value || "").trim();
  return (
    /^m[1-9]\d{0,5}$/i.test(raw) ||
    /^(MARMOT|NOVA)-(0[1-9]|10)$/i.test(raw)
  )
    ? raw
    : null;
}

const AOT_LIVE_FRESH_MS = 12 * 1000;

export class FleetState
  extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.ctx = ctx;
    this.aotLive = new Map();
  }

  async sha256Hex(bytes) {
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
  }

  async githubJson(path) {
    const response = await fetch(`https://api.github.com/repos/${AOT_RELEASE_REPOSITORY}${path}`, {
      headers: { Accept: "application/vnd.github+json", "User-Agent": "Aotscript-AOT-Hub" },
    });
    if (!response.ok) throw new Error(`github_api_${response.status}`);
    return response.json();
  }

  async resolveWorkerRelease() {
    const cacheKey = `worker_release:${AOT_WORKER_TAG}`;
    const cached = await this.ctx.storage.get(cacheKey);
    if (cached?.resolved_at > Date.now() - AOT_RELEASE_CACHE_MS) return cached.value;
    const release = await this.githubJson(`/releases/tags/${encodeURIComponent(AOT_WORKER_TAG)}`);
    if (release.draft || release.prerelease || release.tag_name !== AOT_WORKER_TAG) throw new Error("release_not_published");
    const tagRef = await this.githubJson(`/git/ref/tags/${encodeURIComponent(AOT_WORKER_TAG)}`);
    let commitSha = String(tagRef?.object?.sha || "").toLowerCase();
    if (tagRef?.object?.type === "tag") {
      const tagObject = await this.githubJson(`/git/tags/${commitSha}`);
      if (tagObject?.object?.type !== "commit") throw new Error("release_tag_not_commit");
      commitSha = String(tagObject.object.sha || "").toLowerCase();
    }
    if (!/^[0-9a-f]{40}$/.test(commitSha)) throw new Error("release_commit_invalid");
    const byName = new Map();
    for (const asset of Array.isArray(release.assets) ? release.assets : []) {
      if (byName.has(asset.name)) throw new Error("release_asset_duplicate");
      byName.set(asset.name, asset);
    }
    for (const name of ["worker-bundle.zip", "worker-manifest.json", "worker-checksums.sha256"]) {
      if (!byName.has(name)) throw new Error(`release_asset_missing:${name}`);
    }
    const manifestAsset = byName.get("worker-manifest.json");
    const manifestResponse = await fetch(manifestAsset.browser_download_url, { redirect: "follow" });
    if (!manifestResponse.ok) throw new Error(`release_manifest_http_${manifestResponse.status}`);
    const manifestBytes = await manifestResponse.arrayBuffer();
    if (manifestBytes.byteLength !== Number(manifestAsset.size)) throw new Error("release_manifest_size_mismatch");
    const manifestSha = await this.sha256Hex(manifestBytes);
    const apiDigest = String(manifestAsset.digest || "").toLowerCase();
    if (apiDigest && apiDigest !== `sha256:${manifestSha}`) throw new Error("release_manifest_digest_mismatch");
    const manifest = JSON.parse(new TextDecoder().decode(manifestBytes));
    if (
      manifest.schema_version !== 3 || manifest.worker_version !== AOT_WORKER_VERSION
      || manifest.tag !== AOT_WORKER_TAG || manifest.commit_sha !== commitSha
      || manifest.minimum_protocol !== AOT_RELEASE_PROTOCOL
    ) throw new Error("release_manifest_identity_mismatch");
    const bundleAsset = byName.get("worker-bundle.zip");
    if (
      manifest.asset_name !== bundleAsset.name || manifest.asset_size !== bundleAsset.size
      || `sha256:${manifest.asset_sha256}` !== String(bundleAsset.digest || "").toLowerCase()
    ) throw new Error("release_bundle_mismatch");
    const manifestFiles = Array.isArray(manifest.files) ? manifest.files : [];
    const manifestNames = new Set();
    for (const item of manifestFiles) {
      if (manifestNames.has(item.asset_name)) throw new Error("release_manifest_asset_duplicate");
      manifestNames.add(item.asset_name);
      const asset = byName.get(item.asset_name);
      if (
        !asset || item.url !== asset.browser_download_url || item.size !== asset.size
        || String(item.github_digest || "").toLowerCase() !== String(asset.digest || "").toLowerCase()
        || item.github_digest !== `sha256:${item.sha256}`
      ) throw new Error(`release_asset_mismatch:${item.asset_name || "unknown"}`);
    }
    for (const name of ["relay.py", "runtime.py", "controller.py", "updater.py", "e2e.py", "worker_smoke_test.py", "worker-release-schema.json", "msetup_registration.py", "legacy_relay_bridge.py"]) {
      if (!manifestNames.has(name)) throw new Error(`release_manifest_asset_missing:${name}`);
    }
    const value = {
      protocol: AOT_RELEASE_PROTOCOL, version: AOT_WORKER_VERSION,
      tag: AOT_WORKER_TAG, commit_sha: commitSha,
      manifest: {
        name: manifestAsset.name, url: manifestAsset.browser_download_url,
        size: manifestAsset.size, sha256: manifestSha,
        github_digest: apiDigest,
      },
    };
    await this.ctx.storage.put(cacheKey, { resolved_at: Date.now(), value });
    return value;
  }

  deviceKey(deviceId) {
    return `device:${deviceId}`;
  }

  revocationKey(deviceId) {
    return `revocation:${deviceId}`;
  }

  async readJson(request) {
    try {
      const body =
        await request.json();
      return (
        body &&
        typeof body === "object"
      )
        ? body
        : null;
    } catch (error) {
      return null;
    }
  }

  async report(request) {
    const body =
      await this.readJson(
        request
      );
    const deviceId =
      validDeviceId(
        body?.device_id
      );
    if (!body || !deviceId) {
      return json(
        {
          ok: false,
          error:
            "invalid_report",
        },
        400
      );
    }
    const now = Date.now();
    const revocationKey =
      this.revocationKey(
        deviceId
      );
    let revocation =
      await this.ctx.storage.get(
        revocationKey
      );
    const revocationFresh =
      revocation &&
      typeof revocation ===
        "object" &&
      Number(
        revocation.checked_at ||
        0
      ) > 0 &&
      now -
        Number(
          revocation.checked_at
        ) <=
        REVOCATION_RECHECK_MS;
    if (
      !revocationFresh &&
      body.revocation_checked !==
        true
    ) {
      return json(
        {
          ok: false,
          error:
            "revocation_unknown",
        },
        409
      );
    }
    if (
      body.revocation_checked ===
        true
    ) {
      revocation = {
        revoked:
          body.revoked === true,
        checked_at: now,
      };
      await this.ctx.storage.put(
        revocationKey,
        revocation
      );
    }
    if (revocation?.revoked) {
      await this.ctx.storage.delete(
        this.deviceKey(
          deviceId
        )
      );
      return json(
        {
          ok: false,
          error:
            "device_revoked",
        },
        410
      );
    }
    const key =
      this.deviceKey(
        deviceId
      );
    let record =
      await this.ctx.storage.get(
        key
      );
    if (
      !record ||
      typeof record !== "object"
    ) {
      record = {};
    }
    record.device_id =
      deviceId;
    record.device_group =
      String(
        body.device_group ||
        ""
      ).toUpperCase();
    record.last_seen = now;
    record.last_report_status =
      body.status;
    if (
      body.status ===
        "heartbeat"
    ) {
      if (!record.last_status) {
        record.last_status =
          "heartbeat";
      }
    } else {
      record.last_status =
        body.status;
    }
    if (body.command_id) {
      record.last_command_id =
        body.command_id;
      record.last_command =
        body.command ??
        record.last_command ??
        null;
      record.last_command_index =
        body.command_index ??
        record.last_command_index ??
        null;
      record.last_command_total =
        body.command_total ??
        record.last_command_total ??
        null;
      record.last_command_ts =
        Date.parse(
          body.timestamp
        ) || now;
    }
    if (body.last_result) {
      record.last_result =
        String(
          body.last_result
        ).slice(0, 500);
    }
    const battery =
      Number(
        body.battery_level
      );
    if (
      Number.isFinite(
        battery
      ) &&
      battery >= 0 &&
      battery <= 100
    ) {
      record.battery_level =
        Math.round(battery);
    }
    if (
      typeof body.charging ===
        "boolean"
    ) {
      record.charging =
        body.charging;
    }
    if (
      typeof body.android_version ===
        "string" &&
      body.android_version
    ) {
      record.android_version =
        body.android_version
          .slice(0, 32);
    }
    if (
      typeof body.agent_version ===
        "string" &&
      body.agent_version
    ) {
      record.agent_version =
        body.agent_version
          .slice(0, 64);
    }

    if (
      typeof body
        .reload_novagag2_capable ===
        "boolean"
    ) {
      record
        .reload_novagag2_capable =
        body
          .reload_novagag2_capable;
    }

    if (typeof body.secure_solver_capable === "boolean") {
      record.secure_solver_capable = body.secure_solver_capable;
    }

    if (
      typeof body.self_heal_capable ===
        "boolean"
    ) {
      record.self_heal_capable =
        body.self_heal_capable;
    }

    if (
      typeof body.deferred_command_queue_capable ===
        "boolean"
    ) {
      record.deferred_command_queue_capable =
        body.deferred_command_queue_capable;
    }

    const uptime =
      Number(
        body.uptime_seconds
      );
    if (
      Number.isFinite(
        uptime
      ) &&
      uptime >= 0
    ) {
      record.uptime_seconds =
        Math.floor(uptime);
    }
    const storage =
      Number(
        body.storage_free_bytes
      );
    if (
      Number.isFinite(
        storage
      ) &&
      storage >= 0
    ) {
      record.storage_free_bytes =
        Math.floor(storage);
    }
    await this.ctx.storage.put(
      key,
      record
    );
    return json({
      ok: true,
      device_id: deviceId,
    });
  }

  async getDevice(url) {
    const deviceId =
      validDeviceId(
        url.searchParams.get(
          "id"
        )
      );
    if (!deviceId) {
      return json(
        {
          ok: false,
          error:
            "invalid_device_id",
        },
        400
      );
    }
    const record =
      await this.ctx.storage.get(
        this.deviceKey(
          deviceId
        )
      );
    if (!record) {
      return json(
        {
          ok: false,
          error:
            "not_found",
        },
        404
      );
    }
    return json({
      ok: true,
      record,
    });
  }

  async listDevices() {
    const entries =
      await this.ctx.storage.list({
        prefix: "device:",
      });
    const records = [];
    for (
      const record
      of entries.values()
    ) {
      if (
        record &&
        typeof record ===
          "object"
      ) {
        records.push(record);
      }
    }
    return json({
      ok: true,
      records,
    });
  }
  commandQueueKey(deviceId) {
    return `${COMMAND_QUEUE_PREFIX}${deviceId}`;
  }

  commandSocketTag(deviceId) {
    return `command:${deviceId}`;
  }

  cleanCommandQueue(value, now = Date.now()) {
    if (!Array.isArray(value)) return [];
    const result = [];
    const seen = new Set();
    for (const item of value) {
      const commandId = String(item?.command_id || "").trim();
      const expiresAt = Number(item?.expires_at);
      const block = typeof item?.command_block === "string"
        ? item.command_block
        : "";
      if (
        !/^[\w-]{1,128}$/.test(commandId) ||
        !Number.isFinite(expiresAt) ||
        expiresAt <= now ||
        !block ||
        new TextEncoder().encode(block).length > COMMAND_BLOCK_MAX_BYTES ||
        seen.has(commandId)
      ) {
        continue;
      }
      seen.add(commandId);
      result.push({
        command_id: commandId,
        expires_at: expiresAt,
        command_block: block,
      });
    }
    return result.slice(-COMMAND_QUEUE_MAX);
  }

  async readCommandQueue(deviceId) {
    return this.cleanCommandQueue(
      await this.ctx.storage.get(this.commandQueueKey(deviceId))
    );
  }

  async writeCommandQueue(deviceId, value) {
    const key = this.commandQueueKey(deviceId);
    const queue = this.cleanCommandQueue(value);
    if (queue.length === 0) {
      await this.ctx.storage.delete(key);
      return;
    }
    await this.ctx.storage.put(key, queue);
  }

  sendCommandToSockets(deviceId, item) {
    const sockets = this.ctx.getWebSockets(
      this.commandSocketTag(deviceId)
    );
    if (sockets.length === 0) return 0;
    const payload = JSON.stringify({
      type: "command",
      command_id: item.command_id,
      expires_at: item.expires_at,
      command_block: item.command_block,
    });
    let sent = 0;
    for (const socket of sockets) {
      try {
        socket.send(payload);
        sent += 1;
      } catch (error) {
        // GitHub fallback remains authoritative.
      }
    }
    return sent;
  }

  async flushCommandQueue(deviceId) {
    const queue = await this.readCommandQueue(deviceId);
    if (queue.length === 0) return;
    const remaining = [];
    for (const item of queue) {
      if (this.sendCommandToSockets(deviceId, item) === 0) {
        remaining.push(item);
      }
    }
    await this.writeCommandQueue(deviceId, remaining);
  }

  async enqueueCommand(request) {
    const body = await this.readJson(request);
    const commandId = String(body?.command_id || "").trim();
    const expiresAt = Number(body?.expires_at);
    const block = typeof body?.command_block === "string"
      ? body.command_block
      : "";
    const rawIds = Array.isArray(body?.device_ids) ? body.device_ids : [];
    if (
      !body ||
      !/^[\w-]{1,128}$/.test(commandId) ||
      !Number.isFinite(expiresAt) ||
      expiresAt <= Date.now() ||
      !block ||
      new TextEncoder().encode(block).length > COMMAND_BLOCK_MAX_BYTES ||
      rawIds.length < 1 ||
      rawIds.length > COMMAND_MAX_TARGETS
    ) {
      return json({ ok: false, error: "invalid_command" }, 400);
    }
    const expected = `# telegram_command_id=${commandId}`;
    if (!block.split(/\r?\n/).map((line) => line.trim()).includes(expected)) {
      return json({ ok: false, error: "command_id_mismatch" }, 400);
    }
    const ids = [];
    const seen = new Set();
    for (const raw of rawIds) {
      const deviceId = validDeviceId(raw);
      if (!deviceId || seen.has(deviceId)) continue;
      seen.add(deviceId);
      ids.push(deviceId);
    }
    if (ids.length === 0) {
      return json({ ok: false, error: "no_valid_devices" }, 400);
    }
    const item = {
      command_id: commandId,
      expires_at: expiresAt,
      command_block: block,
    };
    let pushed = 0;
    let queued = 0;
    for (const deviceId of ids) {
      if (this.sendCommandToSockets(deviceId, item) > 0) {
        pushed += 1;
        continue;
      }
      let queue = await this.readCommandQueue(deviceId);
      queue = queue.filter((old) => old.command_id !== commandId);
      queue.push(item);
      await this.writeCommandQueue(deviceId, queue);
      queued += 1;
    }
    return json({
      ok: true,
      command_id: commandId,
      device_count: ids.length,
      pushed,
      queued,
    });
  }

  async connectCommandWebSocket(url, request) {
    const deviceId = validDeviceId(url.searchParams.get("id"));
    if (!deviceId) {
      return json({ ok: false, error: "invalid_device_id" }, 400);
    }
    if (
      String(request.headers.get("Upgrade") || "").toLowerCase()
      !== "websocket"
    ) {
      return json({ ok: false, error: "upgrade_required" }, 426);
    }
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    this.ctx.acceptWebSocket(
      server,
      [this.commandSocketTag(deviceId)]
    );
    this.ctx.waitUntil(this.flushCommandQueue(deviceId));
    return new Response(null, {
      status: 101,
      webSocket: client,
    });
  }

  async webSocketMessage(
    socket,
    message
  ) {
    if (message === "ping") {
      try {
        socket.send("pong");
      } catch (error) {
        // Runtime cleans dead sockets.
      }
      return;
    }

    if (typeof message !== "string") {
      return;
    }

    let body;
    try {
      body = JSON.parse(message);
    } catch (error) {
      return;
    }

    if (
      !body ||
      typeof body !== "object"
    ) {
      return;
    }

    const identity =
      this.parseAotSocketIdentity(
        socket
      );
    if (!identity) {
      return;
    }

    if (
      body.type === "aot_status"
    ) {
      const live =
        this.sanitizeAotLiveStatus(
          identity,
          body
        );
      if (!live) {
        return;
      }
      const key = identity.deviceId;
      const previous =
        this.aotLive.get(key);
      if (
        !live.preview_b64 &&
        previous?.preview_b64 &&
        previous.preview_sha256 ===
          live.preview_sha256
      ) {
        live.preview_b64 =
          previous.preview_b64;
      }
      this.aotLive.set(
        key,
        live
      );
      const fleet = await this.readFleet();
      fleet.devices[identity.deviceId] = {
        ...(fleet.devices[identity.deviceId] || {}), device_id: identity.deviceId,
        worker_version: live.worker_version, capabilities: live.capabilities,
      };
      await this.writeFleet(fleet);
      await this.broadcastFleetState();
      try {
        if (typeof socket.serializeAttachment === "function") {
          socket.serializeAttachment({
            device_id: identity.deviceId,
            worker_version: live.worker_version,
            capabilities: live.capabilities,
          });
        }
      } catch (error) {
        // Capability falls back to live status for non-hibernating runtimes.
      }
      return;
    }

    if (
      body.type ===
        "aot_control_result"
    ) {
      await this.recordAotControlResult(
        identity,
        body
      );
    }
  }


  async webSocketClose(socket) {
    const identity = this.parseAotSocketIdentity(socket);
    if (identity) {
      this.aotLive.delete(
        identity.deviceId
      );
      await this.broadcastFleetState();
    }
  }

  async webSocketError(socket) {
    await this.webSocketClose(socket);
  }



  aotSocketTag(
    role,
    sessionId,
    deviceId
  ) {
    return `aot-device:${deviceId}`;
  }

  sendAotPayload(tag, payload) {
    const sockets =
      this.ctx.getWebSockets(tag);
    if (sockets.length === 0) {
      return 0;
    }
    const text = JSON.stringify(payload);
    let sent = 0;
    for (const socket of sockets) {
      try {
        socket.send(text);
        sent += 1;
      } catch (error) {
        // No durable queue: stale UI actions must never replay.
      }
    }
    return sent;
  }

  aotDashboardTag(sessionId) {
    return "aot-dashboard:fleet";
  }

  async broadcastAotHubState(sessionId) {
    const response = await this.getAotHubState(
      new URL(
        `https://fleet-state.internal/aot/hub/state?session=${encodeURIComponent(sessionId)}`
      )
    );
    if (!response.ok) return 0;
    const state = await response.json();
    return this.sendAotPayload(
      this.aotDashboardTag(sessionId),
      { type: "aot_hub_state", ...state }
    );
  }


  aotSessionKey(sessionId) {
    return `aot_session:${sessionId}`;
  }

  aotLiveKey(
    sessionId,
    deviceId
  ) {
    return `${sessionId}:${deviceId}`;
  }

  async readAotSession(sessionId) {
    let record =
      await this.ctx.storage.get(
        this.aotSessionKey(sessionId)
      );
    if (
      !record ||
      typeof record !== "object"
    ) {
      record = {
        version: 1,
        session_id: sessionId,
        reference_device_id: null,
        followers: {},
        paused: false,
        created_at: Date.now(),
        updated_at: Date.now(),
        last_control: null,
      };
    }
    if (
      !record.followers ||
      typeof record.followers !== "object" ||
      Array.isArray(record.followers)
    ) {
      record.followers = {};
    }
    return record;
  }

  async writeAotSession(
    sessionId,
    record
  ) {
    record.session_id = sessionId;
    record.updated_at = Date.now();
    await this.ctx.storage.put(
      this.aotSessionKey(sessionId),
      record
    );
  }

  async rememberAotMember(
    sessionId,
    role,
    deviceId
  ) {
    const record =
      await this.readAotSession(
        sessionId
      );
    if (role === "reference") {
      record.reference_device_id =
        deviceId;
    } else {
      const previous =
        record.followers[deviceId];
      record.followers[deviceId] = {
        ...(previous &&
        typeof previous === "object"
          ? previous
          : {}),
        device_id: deviceId,
        joined_at:
          Number(
            previous?.joined_at
          ) || Date.now(),
      };
    }
    await this.writeAotSession(
      sessionId,
      record
    );
  }

  async discoverAotRegistration(request) {
    const body = await this.readJson(request);
    const deviceId = validDeviceId(body?.device_id);
    const previousDeviceId = body?.previous_device_id
      ? validDeviceId(body.previous_device_id)
      : null;
    if (!deviceId || (body?.previous_device_id && !previousDeviceId)) {
      return json({ ok: false, error: "invalid_registration_identity" }, 400);
    }
    const entries = await this.ctx.storage.list({ prefix: "aot_session:" });
    const candidates = [];
    for (const [key] of entries) {
      const sessionId = String(key).slice("aot_session:".length);
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(sessionId)) continue;
      const identities = this.ctx.getWebSockets(`aot-session:${sessionId}`)
        .map((socket) => this.parseAotSocketIdentity(socket))
        .filter(Boolean);
      const references = [...new Set(
        identities.filter((item) => item.role === "reference").map((item) => item.deviceId)
      )];
      if (references.length !== 1) continue;
      const referenceDeviceId = references[0];
      candidates.push({
        session_id: sessionId,
        reference_device_id: referenceDeviceId,
        role: [deviceId, previousDeviceId].includes(referenceDeviceId)
          ? "reference"
          : "follower",
      });
    }
    if (candidates.length === 0) {
      return json({ ok: false, error: "no_active_aot_session" }, 404);
    }
    if (candidates.length !== 1) {
      return json({ ok: false, error: "multiple_active_aot_sessions", count: candidates.length }, 409);
    }
    return json({ ok: true, ...candidates[0] });
  }

  async resetAotIdentity(request) {
    const body = await this.readJson(request);
    const oldDeviceId = validDeviceId(body?.old_device_id);
    const newDeviceId = validDeviceId(body?.new_device_id);
    const sessionId = String(body?.session_id || "").trim();
    const role = String(body?.role || "");
    if (
      !oldDeviceId || !newDeviceId || oldDeviceId === newDeviceId
      || !/^[A-Za-z0-9_-]{1,64}$/.test(sessionId)
      || !["reference", "follower"].includes(role)
    ) {
      return json({ ok: false, error: "invalid_identity_reset" }, 400);
    }
    const entries = await this.ctx.storage.list({ prefix: "aot_session:" });
    for (const [key, record] of entries) {
      const candidateSession = String(key).slice("aot_session:".length);
      let changed = false;
      if (record?.reference_device_id === oldDeviceId) {
        record.reference_device_id = candidateSession === sessionId && role === "reference"
          ? newDeviceId
          : null;
        changed = true;
      }
      if (record?.followers && Object.prototype.hasOwnProperty.call(record.followers, oldDeviceId)) {
        delete record.followers[oldDeviceId];
        changed = true;
      }
      this.aotLive.delete(this.aotLiveKey(candidateSession, oldDeviceId));
      for (const oldRole of ["reference", "follower"]) {
        for (const socket of this.ctx.getWebSockets(this.aotSocketTag(oldRole, candidateSession, oldDeviceId))) {
          try { socket.close(4002, "identity_changed"); } catch (error) {}
        }
      }
      if (changed) {
        await this.writeAotSession(candidateSession, record);
        await this.broadcastAotHubState(candidateSession);
      }
    }
    return json({ ok: true, old_device_id: oldDeviceId, new_device_id: newDeviceId });
  }

  async verifyAotRegistration(request) {
    const body = await this.readJson(request);
    const deviceId = validDeviceId(body?.device_id);
    const referenceDeviceId = validDeviceId(body?.reference_device_id);
    const sessionId = String(body?.session_id || "").trim();
    const role = String(body?.role || "");
    if (
      !deviceId || !referenceDeviceId
      || !/^[A-Za-z0-9_-]{1,64}$/.test(sessionId)
      || !["reference", "follower"].includes(role)
    ) {
      return json({ ok: false, error: "invalid_registration_verification" }, 400);
    }
    const record = await this.readAotSession(sessionId);
    const isMember = role === "reference"
      ? record.reference_device_id === deviceId && referenceDeviceId === deviceId
      : Boolean(record.followers?.[deviceId]) && record.reference_device_id === referenceDeviceId;
    const online = this.ctx.getWebSockets(this.aotSocketTag(role, sessionId, deviceId)).length > 0;
    if (!isMember || !online) {
      return json({
        ok: false,
        error: !isMember ? "device_not_in_aot_session" : "device_not_online_in_aot_hub",
        member: isMember,
        online,
      }, 409);
    }
    return json({
      ok: true, device_id: deviceId, role, session_id: sessionId,
      reference_device_id: referenceDeviceId, online: true, visible_in_hub: true,
    });
  }

  parseAotSocketIdentity(socket) {
    let tags;
    try {
      tags = this.ctx.getTags(socket);
    } catch (error) {
      return null;
    }
    const tag = tags.find((value) => String(value).startsWith("aot-device:"));
    if (!tag) {
      return null;
    }
    const parts = String(tag).split(":");
    if (parts.length !== 2) {
      return null;
    }
    const deviceId = validDeviceId(parts[1]);
    if (!deviceId) {
      return null;
    }
    return {
      role: "device",
      sessionId: "fleet",
      deviceId,
    };
  }

  sanitizeAotLiveStatus(
    identity,
    body
  ) {
    if (
      !body ||
      body.type !== "aot_status" ||
      !["fleet-batch-v1", "phase4-1"].includes(body.protocol) ||
      validDeviceId(body.device_id) !==
        identity.deviceId
    ) {
      return null;
    }
    const fingerprint = String(
      body.fingerprint || ""
    ).toLowerCase();
    if (
      !/^[a-f0-9]{24}$/.test(
        fingerprint
      )
    ) {
      return null;
    }
    const rawLayoutSignature = String(
      body.layout_signature || ""
    ).toLowerCase();
    const layoutSignature =
      /^[a-f0-9]{24}$/.test(
        rawLayoutSignature
      )
        ? rawLayoutSignature
        : null;
    const coordinateReady =
      body.coordinate_ready === true &&
      layoutSignature !== null;
    const imeVisible =
      body.ime_visible === true
        ? true
        : body.ime_visible === false
          ? false
          : null;
    const packageName = String(
      body.package || ""
    ).trim();
    if (
      packageName.length > 160 ||
      /[\u0000-\u001f\u007f]/.test(
        packageName
      )
    ) {
      return null;
    }
    const width = Number(body.width);
    const height = Number(body.height);
    if (
      !Number.isFinite(width) ||
      !Number.isFinite(height) ||
      width <= 0 ||
      height <= 0 ||
      width > 10000 ||
      height > 10000
    ) {
      return null;
    }
    const workerVersion = String(body.worker_version || "").trim();
    if (workerVersion && !/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(workerVersion)) {
      return null;
    }
    const capabilities = Array.isArray(body.capabilities)
      ? [...new Set(body.capabilities.map(String).filter((value) =>
        /^[a-z][a-z0-9_]{0,63}$/.test(value)
      ))].slice(0, 16)
      : [];
    let preview = null;
    if (
      typeof body.preview_b64 ===
        "string" &&
      body.preview_b64
    ) {
      if (
        body.preview_b64.length <=
          256 * 1024 &&
        /^[A-Za-z0-9+/=]+$/.test(
          body.preview_b64
        )
      ) {
        preview = body.preview_b64;
      }
    }
    return {
      device_id: identity.deviceId,
      role: identity.role,
      session_id:
        identity.sessionId,
      worker_version: workerVersion || null,
      capabilities,
      package: packageName,
      fingerprint,
      layout_signature:
        layoutSignature,
      coordinate_ready:
        coordinateReady,
      ime_visible:
        imeVisible,
      width: Math.round(width),
      height: Math.round(height),
      preview_b64: preview,
      preview_sha256:
        typeof body.preview_sha256 ===
          "string" &&
        /^[a-f0-9]{64}$/.test(
          body.preview_sha256
        )
          ? body.preview_sha256
          : null,
      preview_bytes:
        Number.isFinite(
          Number(body.preview_bytes)
        )
          ? Math.max(
              0,
              Math.floor(
                Number(
                  body.preview_bytes
                )
              )
            )
          : 0,
      updated_at: Date.now(),
    };
  }

  async recordAotControlResult(
    identity,
    body
  ) {
    if (
      identity.role !== "reference" ||
      !body ||
      body.type !==
        "aot_control_result" ||
      body.protocol !== "phase4-1" ||
      String(body.session_id || "") !==
        identity.sessionId ||
      validDeviceId(body.device_id) !==
        identity.deviceId
    ) {
      return false;
    }
    const controlId = String(
      body.control_id || ""
    ).trim();
    const status = String(
      body.status || ""
    ).trim();
    if (
      !/^[A-Za-z0-9_-]{1,128}$/.test(
        controlId
      ) ||
      ![
        "success",
        "partial",
        "error",
      ].includes(status)
    ) {
      return false;
    }
    const record =
      await this.readAotSession(
        identity.sessionId
      );
    record.last_control = {
      control_id: controlId,
      status,
      reason:
        typeof body.reason === "string"
          ? body.reason.slice(0, 160)
          : null,
      action_id:
        typeof body.action_id === "string"
          ? body.action_id.slice(0, 128)
          : null,
      updated_at: Date.now(),
    };
    await this.writeAotSession(
      identity.sessionId,
      record
    );
    return true;
  }

  async getAotHubState(url) {
    const sessionId = String(
      url.searchParams.get("session") ||
      ""
    ).trim();
    if (
      !/^[A-Za-z0-9_-]{1,64}$/.test(
        sessionId
      )
    ) {
      return json(
        {
          ok: false,
          error: "invalid_session_id",
        },
        400
      );
    }
    const record =
      await this.readAotSession(
        sessionId
      );
    const referenceId =
      validDeviceId(
        record.reference_device_id
      );
    const referenceLive =
      referenceId
        ? this.aotLive.get(
            this.aotLiveKey(
              sessionId,
              referenceId
            )
          ) || null
        : null;

    const now = Date.now();
    const isFresh = (live) =>
      Boolean(
        live &&
        Number(live.updated_at) > 0 &&
        now - Number(live.updated_at) <=
          AOT_LIVE_FRESH_MS
      );
    const referenceFresh =
      isFresh(referenceLive);

    const buildDevice = (
      role,
      deviceId,
      followerRecord = null
    ) => {
      if (!deviceId) {
        return null;
      }
      const live =
        this.aotLive.get(
          this.aotLiveKey(
            sessionId,
            deviceId
          )
        ) || null;
      const online =
        this.ctx.getWebSockets(
          this.aotSocketTag(
            role,
            sessionId,
            deviceId
          )
        ).length > 0;
      const fresh =
        isFresh(live);
      const fingerprintMatch =
        Boolean(
          referenceLive &&
          live &&
          referenceLive.fingerprint ===
            live.fingerprint
        );
      const layoutCompatible =
        Boolean(
          referenceFresh &&
          fresh &&
          referenceLive
            ?.coordinate_ready === true &&
          live?.coordinate_ready === true &&
          referenceLive
            ?.layout_signature &&
          referenceLive
            .layout_signature ===
              live?.layout_signature
        );
      let status;
      if (!online) {
        status = "OFFLINE";
      } else if (role === "reference") {
        status = "REFERENCE";
      } else if (
        referenceFresh &&
        fresh &&
        fingerprintMatch &&
        layoutCompatible
      ) {
        status = "SYNCED";
      } else if (
        referenceFresh &&
        fresh &&
        referenceLive &&
        live
      ) {
        status = "OUT_OF_SYNC";
      } else if (
        followerRecord?.last_ack_status ===
          "out_of_sync"
      ) {
        status = "OUT_OF_SYNC";
      } else {
        status = "WAITING";
      }
      return {
        device_id: deviceId,
        role,
        online,
        fresh,
        status,
        package:
          live?.package || null,
        worker_version:
          live?.worker_version || null,
        capabilities:
          Array.isArray(live?.capabilities) ? live.capabilities : [],
        fingerprint:
          live?.fingerprint || null,
        layout_signature:
          live?.layout_signature || null,
        coordinate_ready:
          live?.coordinate_ready === true,
        ime_visible:
          typeof live?.ime_visible ===
          "boolean"
            ? live.ime_visible
            : null,
        layout_compatible:
          role === "reference"
            ? null
            : layoutCompatible,
        width:
          live?.width || null,
        height:
          live?.height || null,
        preview_b64:
          live?.preview_b64 || null,
        preview_sha256:
          live?.preview_sha256 || null,
        updated_at:
          live?.updated_at || null,
        last_ack_status:
          followerRecord
            ?.last_ack_status ||
          null,
      };
    };

    const reference =
      buildDevice(
        "reference",
        referenceId
      );
    const followers = [];
    for (
      const [deviceId, value]
      of Object.entries(
        record.followers || {}
      )
    ) {
      const normalized =
        validDeviceId(deviceId);
      if (!normalized) continue;
      followers.push(
        buildDevice(
          "follower",
          normalized,
          value
        )
      );
    }
    followers.sort(
      (left, right) =>
        String(left.device_id)
          .localeCompare(
            String(
              right.device_id
            ),
            undefined,
            {
              numeric: true,
              sensitivity: "base",
            }
          )
    );
    const devices = [
      ...(reference
        ? [reference]
        : []),
      ...followers,
    ];
    const summary = {
      online:
        devices.filter(
          (item) => item.online
        ).length,
      synced:
        followers.filter(
          (item) =>
            item.status === "SYNCED"
        ).length,
      out_of_sync:
        followers.filter(
          (item) =>
            item.status ===
              "OUT_OF_SYNC"
        ).length,
      offline:
        devices.filter(
          (item) =>
            item.status === "OFFLINE"
        ).length,
    };
    return json({
      ok: true,
      protocol: "phase4-1",
      session_id: sessionId,
      paused:
        record.paused === true,
      reference,
      followers,
      summary,
      last_control:
        record.last_control || null,
      last_batch:
        this.aotBatchView(
          record.last_batch,
          now
        ),
      last_update: this.aotUpdateView(record.last_update),
      updated_at: Date.now(),
    });
  }

  aotBatchView(batch, now = Date.now()) {
    if (!batch || typeof batch !== "object") {
      return null;
    }
    const expiresAt = Number(batch.expires_at || 0);
    const devices = Object.values(batch.devices || {})
      .filter((item) => item && validDeviceId(item.device_id))
      .map((item) => {
        let status = String(item.status || "FAILED");
        let history = Array.isArray(item.history)
          ? item.history.map(String).slice(-4)
          : [];
        if (
          ["SENT", "ACCEPTED"].includes(status) &&
          expiresAt > 0 &&
          now >= expiresAt
        ) {
          status = "TIMEOUT";
          if (!history.includes("TIMEOUT")) {
            history = [...history, "TIMEOUT"];
          }
        }
        const reason = (status === "TIMEOUT" && !item.reason)
          ? "worker_ack_timeout"
          : String(item.reason || "").trim().slice(0, 160);
        return {
          device_id: item.device_id,
          status,
          history,
          display_status: history.join(" → ") || status,
          reason: reason || null,
          executed: item.executed === true,
          updated_at: Number(item.updated_at || batch.created_at || 0),
          app_count: Number.isSafeInteger(item.app_count) ? item.app_count : null,
          selected_count: Number.isSafeInteger(item.selected_count) ? item.selected_count : null,
        };
      });
    devices.sort((left, right) =>
      String(left.device_id).localeCompare(
        String(right.device_id),
        undefined,
        { numeric: true, sensitivity: "base" }
      )
    );
    return {
      action_id: String(batch.action_id || ""),
      action: String(batch.action || ""),
      package: AOT_BATCH_PACKAGE,
      created_at: Number(batch.created_at || 0),
      expires_at: expiresAt,
      devices,
    };
  }

  aotUpdateView(update) {
    if (!update || typeof update !== "object") return null;
    const devices = Object.values(update.devices || {}).map((item) => ({
      device_id: String(item.device_id || ""),
      status: String(item.status || "FAILED"),
      history: Array.isArray(item.history) ? item.history.map(String).slice(-6) : [],
      display_status: (Array.isArray(item.history) ? item.history : []).join(" → ") || String(item.status || "FAILED"),
      worker_version: String(item.worker_version || ""),
      protocol_mode: String(item.protocol_mode || ""),
      reason: String(item.reason || "").trim().slice(0, 160) || null,
      updated_at: Number(item.updated_at || update.created_at || 0),
    })).sort((a, b) => a.device_id.localeCompare(b.device_id, undefined, { numeric: true }));
    return {
      action_id: String(update.action_id || ""),
      action: AOT_UPDATE_ACTION,
      channel: String(update.channel || ""),
      version: String(update.version || ""),
      selected_device_ids: Array.isArray(update.selected_device_ids)
        ? update.selected_device_ids.map(String)
        : [],
      created_at: Number(update.created_at || 0),
      devices,
    };
  }

  refreshCanaryReleaseGate(record) {
    const update = record.last_update;
    const gate = record.canary_release;
    if (
      !update || update.channel !== "canary" || !gate
      || gate.action_id !== update.action_id
    ) return;
    const ids = Array.isArray(gate.device_ids) ? gate.device_ids : [];
    const devices = ids.map((id) => update.devices?.[id]).filter(Boolean);
    if (devices.length !== 2) {
      gate.status = "FAILED";
    } else if (devices.some((item) => ["FAILED", "ROLLED_BACK"].includes(item.status))) {
      gate.status = "FAILED";
    } else if (devices.every((item) =>
      item.status === "HEALTHY" && item.worker_version === gate.version
    )) {
      gate.status = "HEALTHY";
    } else {
      gate.status = "PENDING";
    }
    gate.updated_at = Date.now();
  }

  rememberUpdateStatus(record, device) {
    if (!device || !validDeviceId(device.device_id)) return;
    if (!record.update_device_status || typeof record.update_device_status !== "object") {
      record.update_device_status = {};
    }
    record.update_device_status[device.device_id] = {
      status: String(device.status || "FAILED"),
      worker_version: String(device.worker_version || ""),
      reason: String(device.reason || "").slice(0, 160),
      updated_at: Number(device.updated_at || Date.now()),
    };
  }

  updateDispatchProtocols(sessionId, member, requestedChannel) {
    const live = this.aotLive.get(this.aotLiveKey(sessionId, member.device_id));
    let capabilities = Array.isArray(live?.capabilities) ? live.capabilities : [];
    if (!capabilities.includes(AOT_DYNAMIC_CHANNEL_CAPABILITY)) {
      for (const socket of this.ctx.getWebSockets(
        this.aotSocketTag(member.role, sessionId, member.device_id)
      )) {
        try {
          const attachment = typeof socket.deserializeAttachment === "function"
            ? socket.deserializeAttachment()
            : null;
          if (Array.isArray(attachment?.capabilities)) {
            capabilities = attachment.capabilities;
          }
        } catch (error) {}
      }
    }
    if (capabilities.includes(AOT_DYNAMIC_CHANNEL_CAPABILITY)) {
      return [{ name: "phase4-dynamic", action: AOT_UPDATE_ACTION, channel: requestedChannel }];
    }
    // Historical workers 2026.08.11.1-.4 bind UPDATE_WORKER to a local
    // channel. Try the requested channel first, then the other historical
    // variant with a distinct transport action ID. This avoids the old relay's
    // dedupe journal swallowing the fallback after a pre-ACK launch failure.
    const alternate = requestedChannel === "canary" ? "stable" : "canary";
    return [requestedChannel, alternate].map((channel, index) => ({
      name: index === 0 ? "phase4-fixed-primary" : "phase4-fixed-fallback",
      action: AOT_UPDATE_ACTION,
      channel,
    }));
  }

  updateAttemptActionId(update, device, index) {
    return `${update.action_id}-p${index + 1}`;
  }

  sendUpdateAttempt(sessionId, update, member, device) {
    const index = Number(device.protocol_attempt_index || 0);
    const attempt = device.protocol_attempts?.[index];
    if (!attempt) return 0;
    const transportActionId = this.updateAttemptActionId(update, device, index);
    device.transport_action_id = transportActionId;
    device.protocol_mode = attempt.name;
    device.rejected_protocols = Array.isArray(device.rejected_protocols)
      ? device.rejected_protocols
      : [];
    return this.sendAotPayload(
      this.aotSocketTag(member.role, sessionId, member.device_id),
      {
        type: "aot_batch_action", protocol: "phase4-1", session_id: sessionId,
        reference_device_id: update.reference_device_id,
        target_device_ids: [member.device_id], action_id: transportActionId,
        action: attempt.action, channel: attempt.channel,
        release: update.release,
        expires_at: update.final_deadline,
      }
    );
  }

  aotMembers(record) {
    const members = [];
    const referenceId = validDeviceId(record.reference_device_id);
    if (referenceId) members.push({ role: "reference", device_id: referenceId });
    for (const rawId of Object.keys(record.followers || {})) {
      const deviceId = validDeviceId(rawId);
      if (deviceId && !members.some((item) => item.device_id === deviceId)) {
        members.push({ role: "follower", device_id: deviceId });
      }
    }
    return members;
  }

  async dispatchNextUpdateGroup(sessionId, record) {
    const update = record.last_update;
    if (!update || update.active_group) return;
    const next = (update.groups || []).shift();
    if (!next) return;
    update.active_group = next;
    update.final_deadline = Date.now() + AOT_UPDATE_TIMEOUT_MS;
    update.group_deadline = Date.now() + AOT_LEGACY_PROTOCOL_RETRY_MS;
    for (const member of next) {
      const device = update.devices[member.device_id];
      const sent = this.sendUpdateAttempt(sessionId, update, member, device);
      if (sent === 0) {
        device.status = "FAILED";
        device.history = ["FAILED"];
        device.reason = "websocket_send_failed";
        device.updated_at = Date.now();
        this.rememberUpdateStatus(record, device);
      }
    }
    await this.writeAotSession(sessionId, record);
    if (next.some((member) => update.devices[member.device_id].status === "FAILED")) {
      await this.abortUpdateRollout(sessionId, record);
      return;
    }
    await this.ctx.storage.setAlarm(update.group_deadline);
    await this.broadcastAotHubState(sessionId);
  }

  async abortUpdateRollout(sessionId, record) {
    const update = record.last_update;
    for (const group of update?.groups || []) {
      for (const member of group) {
        const device = update.devices[member.device_id];
        if (device && device.status === "QUEUED") {
          device.status = "FAILED";
          device.history = ["FAILED"];
          device.reason = "rollout_stopped_after_failure";
          device.updated_at = Date.now();
          this.rememberUpdateStatus(record, device);
        }
      }
    }
    if (update) {
      update.groups = [];
      update.active_group = null;
      update.group_deadline = null;
      update.failed = true;
    }
    this.refreshCanaryReleaseGate(record);
    await this.writeAotSession(sessionId, record);
    await this.broadcastAotHubState(sessionId);
  }

  async startWorkerUpdate(sessionId, record, channel) {
    if (!new Set(["canary", "stable"]).has(channel)) {
      return json({ ok: false, error: "invalid_update_channel" }, 400);
    }
    const prev = record.last_update;
    if (prev && (prev.active_group || (prev.groups || []).length)) {
      const activeIds = (prev.active_group || []).map((item) => item.device_id);
      const isTerminal = activeIds.length > 0 && activeIds.every((id) => AOT_UPDATE_TERMINAL.has(prev.devices?.[id]?.status));
      const hasFailed = activeIds.some((id) => ["FAILED", "ROLLED_BACK", "SKIPPED_OFFLINE"].includes(prev.devices?.[id]?.status));
      const groupFinishedFailed = isTerminal && hasFailed;

      const created = Number(prev.created_at || 0);
      const isOrphaned = !prev.active_group && (prev.groups || []).length > 0 && Number(prev.final_deadline || 0) < Date.now() && (created === 0 || Date.now() > created + 15000);
      const isTimeout = Number(prev.final_deadline || 0) > 0 && Date.now() > Number(prev.final_deadline) + 5000;

      if (groupFinishedFailed || isOrphaned || isTimeout) {
        if (prev.active_group) {
          for (const member of prev.active_group) {
            const device = prev.devices?.[member.device_id];
            if (device && !AOT_UPDATE_TERMINAL.has(device.status)) {
              device.status = "FAILED";
              if (!Array.isArray(device.history)) device.history = [];
              if (!device.history.includes("FAILED")) device.history.push("FAILED");
              const attempts = (device.rejected_protocols || []).concat(
                (device.protocol_attempts || []).slice(device.protocol_attempt_index || 0, (device.protocol_attempt_index || 0) + 1)
                  .map((attempt) => ({
                    protocol: "phase4-1", action: attempt.action,
                    channel: attempt.channel, reason: "no_authenticated_ack",
                  }))
              );
              device.reason = attempts.length
                ? `protocol_rejected:${attempts.map((item) =>
                    `${item.protocol}/${item.action}/${item.channel}:${item.reason}`
                  ).join(",")}`.slice(0, 160)
                : "worker_ack_timeout";
              device.updated_at = Date.now();
              this.rememberUpdateStatus(record, device);
            }
          }
        }
        await this.abortUpdateRollout(sessionId, record);
      } else {
        return json({ ok: false, error: "worker_update_in_progress" }, 409);
      }
    }
    let release;
    try {
      release = await this.resolveWorkerRelease();
    } catch (error) {
      return json({ ok: false, error: "release_resolution_failed", message: String(error.message || error).slice(0, 160) }, 503);
    }
    const all = this.aotMembers(record);
    const connected = (member) => this.ctx.getWebSockets(
      this.aotSocketTag(member.role, sessionId, member.device_id)
    ).length > 0;
    const onlineMembers = all.filter(connected);
    let selected;
    let alreadyHealthy = new Set();
    if (channel === "canary") {
      if (onlineMembers.length < 2) {
        return json({
          ok: false,
          error: "canary_requires_two_online",
          message: "Cần ít nhất 2 máy ONLINE để cập nhật 2 máy thử.",
          online_count: onlineMembers.length,
        }, 409);
      }
      const previousDevices = {
        ...(record.update_device_status || {}),
        ...(record.last_update?.devices || {}),
      };
      const previousTrialIds = new Set(
        record.canary_release?.version === AOT_WORKER_VERSION
          && Array.isArray(record.canary_release?.device_ids)
          ? record.canary_release.device_ids.map(String)
          : []
      );
      const ranked = onlineMembers.map((member) => ({
        ...member,
        failed: previousDevices[member.device_id]?.status === "FAILED",
        healthy: previousDevices[member.device_id]?.status === "HEALTHY"
          && previousDevices[member.device_id]?.worker_version === AOT_WORKER_VERSION,
        previous_trial: previousTrialIds.has(member.device_id),
        previous_updated_at: Number(previousDevices[member.device_id]?.updated_at || 0),
        tie_breaker: crypto.randomUUID(),
      }));
      const retainedHealthy = ranked.filter((item) => item.healthy && item.previous_trial)
        .sort((left, right) => right.previous_updated_at - left.previous_updated_at)
        .slice(0, 2);
      const retainedIds = new Set(retainedHealthy.map((item) => item.device_id));
      const candidates = ranked.filter((item) => !retainedIds.has(item.device_id) && !item.healthy)
        .sort((left, right) =>
        Number(right.failed) - Number(left.failed)
        || right.previous_updated_at - left.previous_updated_at
        || left.tie_breaker.localeCompare(right.tie_breaker)
      );
      selected = [...retainedHealthy, ...candidates].slice(0, 2)
        .map(({ failed, healthy, previous_trial, previous_updated_at, tie_breaker, ...member }) => member);
      if (selected.length < 2) {
        return json({
          ok: false, error: "canary_requires_two_eligible",
          message: "Cần đủ 2 máy ONLINE chưa lỗi điều kiện cập nhật.",
        }, 409);
      }
    } else {
      const gate = record.canary_release;
      if (gate?.status === "FAILED" && gate.version === AOT_WORKER_VERSION) {
        return json({
          ok: false,
          error: "canary_release_failed",
          message: "Máy thử đã FAILED; chưa thể phát hành Stable.",
        }, 409);
      }
      if (
        gate?.status !== "HEALTHY" || gate.version !== AOT_WORKER_VERSION
        || !Array.isArray(gate.device_ids) || gate.device_ids.length !== 2
      ) {
        return json({
          ok: false,
          error: "canary_release_not_healthy",
          message: "Hai máy thử phải HEALTHY đúng phiên bản trước khi phát hành Stable.",
        }, 409);
      }
      alreadyHealthy = new Set(gate.device_ids.map(String));
      selected = all;
    }
    const online = [];
    const devices = {};
    for (const member of selected) {
      const previous = record.update_device_status?.[member.device_id];
      const skipHealthy = (channel === "stable" && alreadyHealthy.has(member.device_id))
        || (channel === "canary" && previous?.status === "HEALTHY"
          && previous?.worker_version === AOT_WORKER_VERSION);
      const isConnected = connected(member);
      const protocolAttempts = skipHealthy
        ? []
        : this.updateDispatchProtocols(sessionId, member, channel);
      devices[member.device_id] = {
        device_id: member.device_id,
        status: skipHealthy ? "HEALTHY" : (isConnected ? "QUEUED" : "SKIPPED_OFFLINE"),
        history: skipHealthy ? ["HEALTHY"] : (isConnected ? ["QUEUED"] : ["SKIPPED_OFFLINE"]),
        worker_version: skipHealthy ? AOT_WORKER_VERSION : "",
        protocol_mode: protocolAttempts[0]?.name || "already_healthy",
        protocol_attempts: protocolAttempts,
        protocol_attempt_index: 0,
        rejected_protocols: [],
        reason: null,
        updated_at: Date.now(),
      };
      if (isConnected && !skipHealthy) online.push(member);
    }
    const actionId = `worker-${channel}-${Date.now()}-${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`;
    const groups = [];
    for (let i = 0; i < online.length; i += AOT_UPDATE_GROUP_SIZE) groups.push(online.slice(i, i + AOT_UPDATE_GROUP_SIZE));
    record.last_update = {
      action_id: actionId, action: AOT_UPDATE_ACTION, channel,
      version: AOT_WORKER_VERSION,
      release,
      selected_device_ids: channel === "canary"
        ? selected.map((item) => item.device_id)
        : [...alreadyHealthy],
      reference_device_id: validDeviceId(record.reference_device_id) || selected[0]?.device_id,
      created_at: Date.now(), devices, groups, active_group: null,
      final_deadline: null,
    };
    if (channel === "canary") {
      record.canary_release = {
        action_id: actionId,
        version: AOT_WORKER_VERSION,
        device_ids: selected.map((item) => item.device_id),
        status: "PENDING",
        updated_at: Date.now(),
      };
    }
    await this.writeAotSession(sessionId, record);
    this.refreshCanaryReleaseGate(record);
    await this.writeAotSession(sessionId, record);
    await this.dispatchNextUpdateGroup(sessionId, record);
    return json({ ok: true, update: this.aotUpdateView(record.last_update) });
  }

  async dispatchOpenSwiftBackup(sessionId, record, requestedTargetIds) {
    const referenceId = validDeviceId(record.reference_device_id);
    const members = [];
    if (referenceId) {
      members.push({ role: "reference", device_id: referenceId });
    }
    for (const rawId of Object.keys(record.followers || {})) {
      const deviceId = validDeviceId(rawId);
      if (
        deviceId &&
        !members.some((item) => item.device_id === deviceId)
      ) {
        members.push({ role: "follower", device_id: deviceId });
      }
    }
    const memberById = new Map(
      members.map((item) => [item.device_id, item])
    );
    const targets = [];
    const seen = new Set();
    for (const rawId of requestedTargetIds) {
      const deviceId = validDeviceId(rawId);
      if (!deviceId || seen.has(deviceId) || !memberById.has(deviceId)) {
        return json(
          { ok: false, error: "invalid_batch_target", device_id: String(rawId || "") },
          400
        );
      }
      seen.add(deviceId);
      targets.push(memberById.get(deviceId));
    }
    if (targets.length < 1 || targets.length > AOT_CONTROL_MAX_TARGETS) {
      return json({ ok: false, error: "invalid_batch_targets" }, 400);
    }
    const actionId =
      `swift-${Date.now()}-${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`;
    const createdAt = Date.now();
    const expiresAt = createdAt + AOT_BATCH_TTL_MS;
    const online = [];
    const devices = {};
    for (const member of targets) {
      const connected = this.ctx.getWebSockets(
        this.aotSocketTag(member.role, sessionId, member.device_id)
      ).length > 0;
      devices[member.device_id] = {
        device_id: member.device_id,
        role: member.role,
        status: connected ? "SENT" : "SKIPPED_OFFLINE",
        history: connected ? ["SENT"] : ["SKIPPED_OFFLINE"],
        reason: connected ? null : "device_offline",
        updated_at: createdAt,
      };
      if (connected) online.push(member);
    }
    record.last_batch = {
      action_id: actionId,
      action: AOT_BATCH_ACTION,
      package: AOT_BATCH_PACKAGE,
      created_at: createdAt,
      expires_at: expiresAt,
      devices,
    };
    await this.writeAotSession(sessionId, record);

    const targetIds = online.map((item) => item.device_id);
    const ackReferenceId = referenceId || targetIds[0] || null;
    let sendFailed = false;
    if (ackReferenceId) {
      const payload = {
        type: "aot_batch_action",
        protocol: "phase4-1",
        session_id: sessionId,
        reference_device_id: ackReferenceId,
        target_device_ids: targetIds,
        action_id: actionId,
        action: AOT_BATCH_ACTION,
        package: AOT_BATCH_PACKAGE,
        expires_at: expiresAt,
      };
      for (const member of online) {
        const sent = this.sendAotPayload(
          this.aotSocketTag(member.role, sessionId, member.device_id),
          payload
        );
        if (sent === 0) {
          sendFailed = true;
          devices[member.device_id].status = "FAILED";
          devices[member.device_id].history.push("FAILED");
          devices[member.device_id].reason = "websocket_send_failed";
          devices[member.device_id].updated_at = Date.now();
        }
      }
    }
    if (sendFailed) {
      await this.writeAotSession(sessionId, record);
    }
    return json({
      ok: true,
      batch: this.aotBatchView(record.last_batch, createdAt),
    });
  }

  async controlAotHub(request) {
    const body =
      await this.readJson(request);
    const sessionId = String(
      body?.session_id || ""
    ).trim();
    const kind = String(
      body?.kind || ""
    ).trim();
    if (
      !body ||
      body.protocol !== "phase4-1" ||
      !/^[A-Za-z0-9_-]{1,64}$/.test(
        sessionId
      )
    ) {
      return json(
        {
          ok: false,
          error:
            "invalid_hub_control",
        },
        400
      );
    }
    const record =
      await this.readAotSession(
        sessionId
      );

    if (kind === "open_swift_backup") {
      const requested = Array.isArray(body.target_device_ids)
        ? body.target_device_ids
        : [];
      return this.dispatchOpenSwiftBackup(sessionId, record, requested);
    }
    if (kind === "update_canary") return this.startWorkerUpdate(sessionId, record, "canary");
    if (kind === "update_stable") return this.startWorkerUpdate(sessionId, record, "stable");

    if (
      kind === "pause" ||
      kind === "resume"
    ) {
      record.paused =
        kind === "pause";
      record.last_control = {
        control_id:
          `state-${Date.now()}`,
        status:
          record.paused
            ? "paused"
            : "resumed",
        reason: null,
        updated_at: Date.now(),
      };
      await this.writeAotSession(
        sessionId,
        record
      );
      return json({
        ok: true,
        paused: record.paused,
      });
    }

    if (record.paused === true) {
      return json(
        {
          ok: false,
          error: "hub_paused",
        },
        409
      );
    }

    const referenceId =
      validDeviceId(
        record.reference_device_id
      );
    if (!referenceId) {
      return json(
        {
          ok: false,
          error:
            "reference_not_registered",
        },
        409
      );
    }
    const referenceTag =
      this.aotSocketTag(
        "reference",
        sessionId,
        referenceId
      );
    if (
      this.ctx.getWebSockets(
        referenceTag
      ).length === 0
    ) {
      return json(
        {
          ok: false,
          error:
            "reference_offline",
        },
        409
      );
    }

    let action;
    if (kind === "back") {
      action = { kind: "back" };
    } else if (kind === "tap") {
      const x = Number(body.x_norm);
      const y = Number(body.y_norm);
      if (
        !Number.isFinite(x) ||
        !Number.isFinite(y) ||
        x < 0 ||
        x > 1 ||
        y < 0 ||
        y > 1
      ) {
        return json(
          {
            ok: false,
            error:
              "invalid_tap_coordinates",
          },
          400
        );
      }
      action = {
        kind: "tap",
        x_norm: x,
        y_norm: y,
      };
    } else if (kind === "swipe") {
      const values = [
        Number(body.x1),
        Number(body.y1),
        Number(body.x2),
        Number(body.y2),
      ];
      if (
        values.some(
          (value) =>
            !Number.isFinite(value) ||
            value < 0 ||
            value > 1
        )
      ) {
        return json(
          {
            ok: false,
            error:
              "invalid_swipe_coordinates",
          },
          400
        );
      }
      action = {
        kind: "swipe",
        x1: values[0],
        y1: values[1],
        x2: values[2],
        y2: values[3],
        duration_ms: Math.min(
          5000,
          Math.max(
            50,
            Math.round(
              Number(
                body.duration_ms
              ) || 300
            )
          )
        ),
      };
    } else {
      return json(
        {
          ok: false,
          error:
            "unsupported_hub_control",
        },
        400
      );
    }

    const targets = [];
    for (
      const deviceId
      of Object.keys(
        record.followers || {}
      )
    ) {
      const normalized =
        validDeviceId(deviceId);
      if (
        normalized &&
        this.ctx.getWebSockets(
          this.aotSocketTag(
            "follower",
            sessionId,
            normalized
          )
        ).length > 0
      ) {
        targets.push(normalized);
      }
    }

    const controlId =
      `hub-${Date.now()}-${crypto
        .randomUUID()
        .replace(/-/g, "")
        .slice(0, 8)}`;
    const payload = {
      type: "aot_hub_action",
      protocol: "phase4-1",
      session_id: sessionId,
      reference_device_id:
        referenceId,
      target_device_ids: targets,
      control_id: controlId,
      action,
    };
    const sent =
      this.sendAotPayload(
        referenceTag,
        payload
      );
    if (sent === 0) {
      return json(
        {
          ok: false,
          error:
            "reference_offline",
        },
        409
      );
    }
    record.last_control = {
      control_id: controlId,
      status: "queued",
      reason: null,
      updated_at: Date.now(),
    };
    await this.writeAotSession(
      sessionId,
      record
    );
    return json({
      ok: true,
      control_id: controlId,
      target_count:
        targets.length,
    });
  }

  async connectAotDashboard(url, request) {
    const sessionId = String(url.searchParams.get("session") || "").trim();
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(sessionId)) {
      return json({ ok: false, error: "invalid_session_id" }, 400);
    }
    if (
      String(request.headers.get("Upgrade") || "").toLowerCase() !==
      "websocket"
    ) {
      return json({ ok: false, error: "upgrade_required" }, 426);
    }
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    this.ctx.acceptWebSocket(server, [this.aotDashboardTag(sessionId)]);
    return new Response(null, { status: 101, webSocket: client });
  }

  async connectAotWebSocket(
    url,
    request
  ) {
    const deviceId = validDeviceId(
      url.searchParams.get("id")
    );
    const role = String(
      url.searchParams.get("role") || ""
    ).trim();
    const sessionId = String(
      url.searchParams.get("session") || ""
    ).trim();
    if (
      !deviceId ||
      !["reference", "follower"].includes(role) ||
      !/^[A-Za-z0-9_-]{1,64}$/.test(sessionId)
    ) {
      return json(
        {
          ok: false,
          error: "invalid_aot_socket",
        },
        400
      );
    }
    if (
      String(
        request.headers.get("Upgrade") || ""
      ).toLowerCase() !== "websocket"
    ) {
      return json(
        {
          ok: false,
          error: "upgrade_required",
        },
        426
      );
    }

    const specificTag =
      this.aotSocketTag(
        role,
        sessionId,
        deviceId
      );
    for (
      const oldSocket
      of this.ctx.getWebSockets(
        specificTag
      )
    ) {
      try {
        oldSocket.close(
          4001,
          "replaced"
        );
      } catch (error) {
        // Runtime will clean stale sockets.
      }
    }

    const pair = new WebSocketPair();
    const [client, server] =
      Object.values(pair);
    this.ctx.acceptWebSocket(
      server,
      [
        specificTag,
        `aot-session:${sessionId}`,
      ]
    );
    try {
      if (typeof server.serializeAttachment === "function") {
        server.serializeAttachment({
          role, session_id: sessionId, device_id: deviceId,
          worker_version: null, capabilities: [],
        });
      }
    } catch (error) {}
    await this.rememberAotMember(
      sessionId,
      role,
      deviceId
    );
    await this.broadcastAotHubState(sessionId);
    return new Response(null, {
      status: 101,
      webSocket: client,
    });
  }


  async dispatchAotAction(request) {
    const body = await this.readJson(
      request
    );
    const sessionId = String(
      body?.session_id || ""
    ).trim();
    const referenceId = validDeviceId(
      body?.reference_device_id
    );
    const actionId = String(
      body?.action_id || ""
    ).trim();
    const expiresAt = Number(
      body?.expires_at
    );
    const precondition = String(
      body?.precondition || ""
    ).trim();
    const action =
      body?.action &&
      typeof body.action === "object"
        ? body.action
        : null;
    const rawTargets = Array.isArray(
      body?.target_device_ids
    )
      ? body.target_device_ids
      : [];
    if (
      !body ||
      body.protocol !== "phase3-1" ||
      !/^[A-Za-z0-9_-]{1,64}$/.test(
        sessionId
      ) ||
      !referenceId ||
      !/^[A-Za-z0-9_-]{1,128}$/.test(
        actionId
      ) ||
      !Number.isFinite(expiresAt) ||
      expiresAt <= Date.now() ||
      !/^[a-f0-9]{24}$/.test(
        precondition
      ) ||
      !action ||
      rawTargets.length < 1 ||
      rawTargets.length > AOT_CONTROL_MAX_TARGETS
    ) {
      return json(
        {
          ok: false,
          error: "invalid_aot_action",
        },
        400
      );
    }
    const targets = [];
    const seen = new Set();
    for (const raw of rawTargets) {
      const deviceId = validDeviceId(raw);
      if (
        deviceId &&
        !seen.has(deviceId)
      ) {
        seen.add(deviceId);
        targets.push(deviceId);
      }
    }
    if (targets.length === 0) {
      return json(
        {
          ok: false,
          error: "no_valid_targets",
        },
        400
      );
    }
    const payload = {
      type: "aot_action",
      protocol: "phase3-1",
      session_id: sessionId,
      reference_device_id: referenceId,
      action_id: actionId,
      expires_at: expiresAt,
      precondition,
      action,
    };
    const offline = [];
    let pushed = 0;
    for (const deviceId of targets) {
      const sent = this.sendAotPayload(
        this.aotSocketTag(
          "follower",
          sessionId,
          deviceId
        ),
        payload
      );
      if (sent > 0) {
        pushed += 1;
      } else {
        offline.push(deviceId);
      }
    }
    if (pushed === 0) {
      return json(
        {
          ok: false,
          error: "followers_offline",
          offline,
        },
        409
      );
    }
    return json({
      ok: true,
      pushed,
      offline,
    });
  }

  async dispatchAotAck(request) {
    const body = await this.readJson(
      request
    );
    const sessionId = String(
      body?.session_id || ""
    ).trim();
    const referenceId = validDeviceId(
      body?.reference_device_id
    );
    const followerId = validDeviceId(
      body?.follower_device_id
    );
    const actionId = String(
      body?.action_id || ""
    ).trim();
    const batchAction = String(body?.batch_action || "");
    const isBatch = body?.protocol === "phase4-1" &&
      [AOT_BATCH_ACTION, AOT_UPDATE_ACTION].includes(batchAction);
    if (
      !body ||
      (!isBatch && body.protocol !== "phase3-1") ||
      !/^[A-Za-z0-9_-]{1,64}$/.test(
        sessionId
      ) ||
      !referenceId ||
      !followerId ||
      !/^[A-Za-z0-9_-]{1,128}$/.test(
        actionId
      )
    ) {
      return json(
        {
          ok: false,
          error: "invalid_aot_ack",
        },
        400
      );
    }

    const record =
      await this.readAotSession(
        sessionId
      );
    if (isBatch) {
      if (batchAction === AOT_UPDATE_ACTION) {
        const allowed = new Set(["DOWNLOADING", "VERIFIED", "INSTALLING", "RESTARTING", "HEALTHY", "ROLLED_BACK", "FAILED"]);
        const update = record.last_update;
        const device = update?.devices?.[followerId];
        const status = String(body.status || "");
        const workerVersion = String(body.worker_version || "").trim().slice(0, 80);
        const reportedReason = String(body.reason || "").trim().slice(0, 160);
        const acceptedActionIds = !update || !device
          ? []
          : [update.action_id, ...(device.protocol_attempts || []).map((_, index) =>
              this.updateAttemptActionId(update, device, index)
            )];
        if (!allowed.has(status) || !update || !acceptedActionIds.includes(actionId) || !device) {
          return json({ ok: false, error: "invalid_update_ack" }, 400);
        }
        if (!AOT_UPDATE_TERMINAL.has(device.status)) {
          const attemptIndex = (device.protocol_attempts || []).findIndex((_, index) =>
            this.updateAttemptActionId(update, device, index) === actionId
          );
          if (attemptIndex >= 0) {
            device.protocol_attempt_index = attemptIndex;
            const attempt = device.protocol_attempts[attemptIndex];
            device.protocol_mode = attempt.name;
            device.accepted_protocol = {
              protocol: "phase4-1", action: attempt.action, channel: attempt.channel,
            };
          }
          const nextStatus = status === "HEALTHY" && workerVersion !== update.version
            ? "FAILED"
            : status;
          device.status = nextStatus;
          if (!Array.isArray(device.history)) device.history = [];
          if (!device.history.includes(nextStatus)) device.history.push(nextStatus);
          if (workerVersion) device.worker_version = workerVersion;
          if (nextStatus === "HEALTHY") {
            device.reason = null;
          } else if (nextStatus === "FAILED") {
            device.reason = status === "HEALTHY"
              ? "worker_version_mismatch"
              : (reportedReason || "worker_reported_failure");
          } else if (nextStatus === "ROLLED_BACK") {
            device.reason = reportedReason || "health_ack_timeout_rolled_back";
          }
          device.updated_at = Date.now();
          this.rememberUpdateStatus(record, device);
        }
        this.refreshCanaryReleaseGate(record);
        const activeIds = (update.active_group || []).map((item) => item.device_id);
        const groupFinished = activeIds.length && activeIds.every((id) => AOT_UPDATE_TERMINAL.has(update.devices[id]?.status));
        const groupHealthy = activeIds.length && activeIds.every((id) => update.devices[id]?.status === "HEALTHY");
        if (groupFinished) {
          update.active_group = null;
          update.group_deadline = null;
        }
        await this.writeAotSession(sessionId, record);
        if (groupFinished && !groupHealthy) {
          await this.abortUpdateRollout(sessionId, record);
        } else if (groupHealthy) {
          await this.dispatchNextUpdateGroup(sessionId, record);
        }
        await this.broadcastAotHubState(sessionId);
        return json({ ok: true, action_id: update.action_id, device_id: followerId, status: device.status });
      }
      const allowed = new Set([
        "ACCEPTED",
        "OPENED",
        "FAILED_NOT_INSTALLED",
        "FAILED",
        "TIMEOUT",
        "DUPLICATE",
      ]);
      const status = String(body.status || "");
      const reason = String(body.reason || "").trim().slice(0, 160);
      const batch = record.last_batch;
      const device = batch?.devices?.[followerId];
      if (
        !allowed.has(status) ||
        !batch ||
        batch.action_id !== actionId ||
        batch.action !== AOT_BATCH_ACTION ||
        !device ||
        device.device_id !== followerId
      ) {
        return json(
          { ok: false, error: "invalid_batch_ack" },
          400
        );
      }
      if (status !== "DUPLICATE") {
        const terminal = new Set([
          "OPENED",
          "FAILED_NOT_INSTALLED",
          "FAILED",
          "TIMEOUT",
          "SKIPPED_OFFLINE",
        ]);
        const expired =
          Number(batch.expires_at || 0) <= Date.now();
        const nextStatus =
          expired && !terminal.has(device.status)
            ? "TIMEOUT"
            : status;
        if (!terminal.has(device.status)) {
          if (!Array.isArray(device.history)) {
            device.history = [String(device.status || "SENT")];
          }
          device.status = nextStatus;
          if (!device.history.includes(nextStatus)) {
            device.history.push(nextStatus);
          }
          if (["FAILED_NOT_INSTALLED", "FAILED", "TIMEOUT"].includes(nextStatus)) {
            device.reason = reason || (
              nextStatus === "TIMEOUT"
                ? "worker_ack_timeout"
                : "worker_reported_failure"
            );
          } else if (nextStatus === "OPENED") {
            device.reason = null;
          }
          device.updated_at = Date.now();
        }
      }
      await this.writeAotSession(sessionId, record);
      await this.broadcastAotHubState(sessionId);
      return json({
        ok: true,
        action_id: actionId,
        device_id: followerId,
        status: device.status,
      });
    }
    const previous =
      record.followers[followerId];
    record.followers[followerId] = {
      ...(previous &&
      typeof previous === "object"
        ? previous
        : {}),
      device_id: followerId,
      joined_at:
        Number(
          previous?.joined_at
        ) || Date.now(),
      last_ack_status:
        String(body.status || "")
          .slice(0, 40),
      last_ack_action_id:
        actionId,
      last_ack_at: Date.now(),
    };
    await this.writeAotSession(
      sessionId,
      record
    );
    await this.broadcastAotHubState(sessionId);

    if (
      typeof body.preview_b64 ===
        "string" &&
      body.preview_b64 &&
      body.preview_b64.length <=
        256 * 1024 &&
      /^[A-Za-z0-9+/=]+$/.test(
        body.preview_b64
      )
    ) {
      const key =
        this.aotLiveKey(
          sessionId,
          followerId
        );
      const oldLive =
        this.aotLive.get(key) || {};
      this.aotLive.set(
        key,
        {
          ...oldLive,
          device_id: followerId,
          role: "follower",
          session_id: sessionId,
          preview_b64:
            body.preview_b64,
          preview_sha256:
            typeof body
              .preview_sha256 ===
                "string"
              ? body.preview_sha256
              : oldLive
                  .preview_sha256 ||
                null,
          preview_bytes:
            Number.isFinite(
              Number(
                body.preview_bytes
              )
            )
              ? Math.max(
                  0,
                  Math.floor(
                    Number(
                      body.preview_bytes
                    )
                  )
                )
              : oldLive
                  .preview_bytes ||
                0,
          updated_at: Date.now(),
        }
      );
    }

    const payload = {
      type: "aot_ack",
      ...body,
    };
    const sent = this.sendAotPayload(
      this.aotSocketTag(
        "reference",
        sessionId,
        referenceId
      ),
      payload
    );
    if (sent === 0) {
      return json(
        {
          ok: false,
          error: "reference_offline",
        },
        409
      );
    }
    return json({
      ok: true,
      delivered: sent,
    });
  }

  async alarm() {
    const entries = await this.ctx.storage.list({ prefix: "aot_session:" });
    for (const [key, record] of entries) {
      const update = record?.last_update;
      if ((!update?.active_group && (!update?.groups || update.groups.length === 0)) || Number(update.group_deadline || 0) > Date.now()) continue;
      let retried = false;
      if (Number(update.final_deadline || 0) > Date.now()) {
        if (update.active_group) {
          for (const member of update.active_group) {
            const device = update.devices?.[member.device_id];
            if (!device || device.status !== "QUEUED") continue;
            const current = device.protocol_attempts?.[device.protocol_attempt_index || 0];
            const nextIndex = Number(device.protocol_attempt_index || 0) + 1;
            if (!device.protocol_attempts?.[nextIndex]) continue;
            device.rejected_protocols = Array.isArray(device.rejected_protocols)
              ? device.rejected_protocols
              : [];
            device.rejected_protocols.push({
              protocol: "phase4-1", action: current?.action || AOT_UPDATE_ACTION,
              channel: current?.channel || "", reason: "no_authenticated_ack",
            });
            device.protocol_attempt_index = nextIndex;
            if (this.sendUpdateAttempt(String(key).slice("aot_session:".length), update, member, device) > 0) {
              retried = true;
            }
            device.updated_at = Date.now();
          }
        }
      }
      if (Number(update.final_deadline || 0) > Date.now()) {
        update.group_deadline = update.final_deadline;
        const sessionId = String(key).slice("aot_session:".length);
        await this.writeAotSession(sessionId, record);
        await this.ctx.storage.setAlarm(update.group_deadline);
        if (retried) await this.broadcastAotHubState(sessionId);
        continue;
      }
      if (update.active_group) {
        for (const member of update.active_group) {
          const device = update.devices?.[member.device_id];
          if (device && !AOT_UPDATE_TERMINAL.has(device.status)) {
            device.status = "FAILED";
            if (!Array.isArray(device.history)) device.history = [];
            if (!device.history.includes("FAILED")) device.history.push("FAILED");
            const attempts = (device.rejected_protocols || []).concat(
              (device.protocol_attempts || []).slice(device.protocol_attempt_index || 0, (device.protocol_attempt_index || 0) + 1)
                .map((attempt) => ({
                  protocol: "phase4-1", action: attempt.action,
                  channel: attempt.channel, reason: "no_authenticated_ack",
                }))
            );
            device.reason = attempts.length
              ? `protocol_rejected:${attempts.map((item) =>
                  `${item.protocol}/${item.action}/${item.channel}:${item.reason}`
                ).join(",")}`.slice(0, 160)
              : "worker_ack_timeout";
            device.updated_at = Date.now();
            this.rememberUpdateStatus(record, device);
          }
        }
      }
      update.active_group = null;
      update.group_deadline = null;
      const sessionId = String(key).slice("aot_session:".length);
      await this.writeAotSession(sessionId, record);
      await this.abortUpdateRollout(sessionId, record);
    }
  }

  async setRevocation(
    request,
    revoked
  ) {
    const body =
      await this.readJson(
        request
      );
    const deviceId =
      validDeviceId(
        body?.device_id
      );
    if (!deviceId) {
      return json(
        {
          ok: false,
          error:
            "invalid_device_id",
        },
        400
      );
    }
    await this.ctx.storage.put(
      this.revocationKey(
        deviceId
      ),
      {
        revoked,
        checked_at:
          Date.now(),
      }
    );
    await this.ctx.storage.delete(
      this.deviceKey(
        deviceId
      )
    );
    await this.ctx.storage.delete(
      this.commandQueueKey(deviceId)
    );
    return json({
      ok: true,
      device_id: deviceId,
      revoked,
    });
  }

  async readFleet() {
    const stored = await this.ctx.storage.get("aot_fleet");
    const record = stored && typeof stored === "object" ? stored : {
      version: 2, devices: {}, last_batch: null, last_update: null,
      canary_release: null, update_device_status: {}, updated_at: Date.now(),
    };
    if (!record.devices || typeof record.devices !== "object") record.devices = {};
    return record;
  }

  async writeFleet(record) {
    record.updated_at = Date.now();
    await this.ctx.storage.put("aot_fleet", record);
  }

  fleetMembers(record) {
    return Object.keys(record.devices || {}).map((device_id) => ({
      device_id, role: "device",
      worker_version: record.devices[device_id]?.worker_version || "",
      capabilities: record.devices[device_id]?.capabilities || [],
    }));
  }

  async getFleetHubState() {
    const record = await this.readFleet();
    const now = Date.now();
    const devices = this.fleetMembers(record).map((item) => {
      const live = this.aotLive.get(item.device_id) || {};
      const online = this.ctx.getWebSockets(this.aotSocketTag("device", "fleet", item.device_id)).length > 0;
      return {
        device_id: item.device_id, online, status: online ? "ONLINE" : "OFFLINE",
        worker_version: live.worker_version || item.worker_version || "",
        capabilities: live.capabilities || item.capabilities || [], updated_at: live.updated_at || 0,
      };
    }).sort((a, b) => a.device_id.localeCompare(b.device_id));
    return json({ ok: true, state: {
      protocol: "fleet-batch-v1", devices,
      last_batch: this.aotBatchView(record.last_batch, now),
      last_update: this.aotUpdateView(record.last_update), canary_release: record.canary_release || null,
    }});
  }

  async broadcastFleetState() {
    const response = await this.getFleetHubState();
    if (!response.ok) return 0;
    const payload = await response.json();
    return this.sendAotPayload(this.aotDashboardTag("fleet"), { type: "aot_hub_state", ...payload.state });
  }

  async connectFleetDashboard(request) {
    if (String(request.headers.get("Upgrade") || "").toLowerCase() !== "websocket") {
      return json({ ok: false, error: "upgrade_required" }, 426);
    }
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    this.ctx.acceptWebSocket(server, [this.aotDashboardTag("fleet")]);
    return new Response(null, { status: 101, webSocket: client });
  }

  async connectFleetWebSocket(url, request) {
    const deviceId = validDeviceId(url.searchParams.get("id"));
    if (!deviceId) return json({ ok: false, error: "invalid_device_id" }, 400);
    if (String(request.headers.get("Upgrade") || "").toLowerCase() !== "websocket") {
      return json({ ok: false, error: "upgrade_required" }, 426);
    }
    const tag = this.aotSocketTag("device", "fleet", deviceId);
    for (const socket of this.ctx.getWebSockets(tag)) {
      try { socket.close(4001, "replaced"); } catch (error) {}
    }
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    this.ctx.acceptWebSocket(server, [tag]);
    if (typeof server.serializeAttachment === "function") {
      server.serializeAttachment({ device_id: deviceId, fleet: true, worker_version: null, capabilities: [] });
    }
    const record = await this.readFleet();
    record.devices[deviceId] = { ...(record.devices[deviceId] || {}), device_id: deviceId, joined_at: record.devices[deviceId]?.joined_at || Date.now() };
    await this.writeFleet(record);
    await this.broadcastFleetState();
    return new Response(null, { status: 101, webSocket: client });
  }

  async dispatchFleetBatch(record, action, requestedTargetIds) {
    const allowed = new Set([AOT_BATCH_ACTION, AOT_APPS_ACTION, AOT_BACKUP_RESTORE_DATA_ACTION]);
    if (!allowed.has(action)) return json({ ok: false, error: "invalid_batch_action" }, 400);
    const seen = new Set();
    const targets = [];
    for (const raw of requestedTargetIds) {
      const id = validDeviceId(raw);
      if (!id || seen.has(id) || !record.devices[id]) return json({ ok: false, error: "invalid_batch_target" }, 400);
      seen.add(id); targets.push(id);
    }
    if (!targets.length || targets.length > AOT_CONTROL_MAX_TARGETS) return json({ ok: false, error: "invalid_batch_targets" }, 400);
    const ttl = action === AOT_BACKUP_RESTORE_DATA_ACTION ? AOT_BACKUP_RESTORE_DATA_TTL_MS : AOT_BATCH_TTL_MS;
    const createdAt = Date.now(), expiresAt = createdAt + ttl;
    const actionId = `fleet-${createdAt}-${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`;
    const devices = {}, online = [];
    for (const id of targets) {
      const connected = this.ctx.getWebSockets(this.aotSocketTag("device", "fleet", id)).length > 0;
      devices[id] = { device_id: id, status: connected ? "SENT" : "SKIPPED_OFFLINE", history: [connected ? "SENT" : "SKIPPED_OFFLINE"], reason: connected ? null : "device_offline", updated_at: createdAt };
      if (connected) online.push(id);
    }
    record.last_batch = { action_id: actionId, action, package: AOT_BATCH_PACKAGE, created_at: createdAt, expires_at: expiresAt, devices };
    await this.writeFleet(record);
    const payload = { type: "aot_batch_action", protocol: "fleet-batch-v1", target_device_ids: online, action_id: actionId, action, package: AOT_BATCH_PACKAGE, expires_at: expiresAt };
    for (const id of online) {
      if (!this.sendAotPayload(this.aotSocketTag("device", "fleet", id), payload)) {
        devices[id].status = "FAILED"; devices[id].history.push("FAILED"); devices[id].reason = "websocket_send_failed";
      }
    }
    await this.writeFleet(record);
    return json({ ok: true, batch: this.aotBatchView(record.last_batch, createdAt) });
  }

  async controlFleetHub(request) {
    const body = await this.readJson(request);
    if (!body || body.protocol !== "fleet-batch-v1") return json({ ok: false, error: "invalid_hub_control" }, 400);
    const record = await this.readFleet();
    if (body.kind === "open_swift_backup" || body.kind === "open_swift_apps") {
      return this.dispatchFleetBatch(record, body.kind === "open_swift_apps" ? AOT_APPS_ACTION : AOT_BATCH_ACTION, Array.isArray(body.target_device_ids) ? body.target_device_ids : []);
    }
    if (body.kind === "backup_restore_data") {
      return this.dispatchFleetBatch(record, AOT_BACKUP_RESTORE_DATA_ACTION, Array.isArray(body.target_device_ids) ? body.target_device_ids : []);
    }
    if (body.kind === "update_canary" || body.kind === "update_stable") {
      record.followers = Object.fromEntries(this.fleetMembers(record).map((m) => [m.device_id, m]));
      record.reference_device_id = this.fleetMembers(record)[0]?.device_id || null;
      const response = await this.startWorkerUpdate("fleet", record, body.kind === "update_canary" ? "canary" : "stable");
      await this.writeFleet(record);
      return response;
    }
    return json({ ok: false, error: "unsupported_fleet_control" }, 400);
  }

  async dispatchFleetAck(request) {
    const body = await this.readJson(request);
    const id = validDeviceId(body?.device_id), actionId = String(body?.action_id || ""), action = String(body?.batch_action || "");
    if (!id || !/^[A-Za-z0-9_-]{1,128}$/.test(actionId) || ![AOT_BATCH_ACTION, AOT_APPS_ACTION, AOT_BACKUP_RESTORE_DATA_ACTION, AOT_UPDATE_ACTION].includes(action)) return json({ ok: false, error: "invalid_aot_ack" }, 400);
    const record = await this.readFleet();
    if (action === AOT_UPDATE_ACTION) {
      record.followers = Object.fromEntries(this.fleetMembers(record).map((m) => [m.device_id, m]));
      record.reference_device_id = this.fleetMembers(record)[0]?.device_id || id;
      body.session_id = "fleet"; body.reference_device_id = record.reference_device_id; body.follower_device_id = id; body.protocol = "phase4-1";
      const response = await this.dispatchAotAck(new Request(request.url, { method: "POST", body: JSON.stringify(body) }));
      await this.writeFleet(await this.readAotSession("fleet"));
      return response;
    }
    const batch = record.last_batch, device = batch?.devices?.[id];
    const allowed = new Set(["ACCEPTED", "OPENED", "APPS_OPENED", "SWIFT_OPENED", "FILTERED", "SELECTED", "OPTIONS_VERIFIED", "RESTORE_STARTED", "FAILED_NOT_INSTALLED", "FAILED", "TIMEOUT", "DUPLICATE"]);
    if (!batch || batch.action_id !== actionId || batch.action !== action || !device || !allowed.has(body.status)) return json({ ok: false, error: "invalid_batch_ack" }, 400);
    const terminal = new Set(["FAILED_NOT_INSTALLED", "FAILED", "TIMEOUT", "SKIPPED_OFFLINE"]);
    if (action === AOT_BATCH_ACTION) terminal.add("OPENED");
    if (action === AOT_APPS_ACTION) terminal.add("APPS_OPENED");
    if (action === AOT_BACKUP_RESTORE_DATA_ACTION) terminal.add("RESTORE_STARTED");
    const ranks = { "SENT": 0, "ACCEPTED": 1, "OPENED": 2, "SWIFT_OPENED": 2, "APPS_OPENED": 3, "FILTERED": 4, "SELECTED": 5, "OPTIONS_VERIFIED": 6, "RESTORE_STARTED": 7, "FAILED_NOT_INSTALLED": 8, "FAILED": 8, "TIMEOUT": 8, "SKIPPED_OFFLINE": 8 };
    if (!terminal.has(device.status) && body.status !== "DUPLICATE") {
      const next = Date.now() >= batch.expires_at ? "TIMEOUT" : body.status;
      if ((ranks[next] || 0) > (ranks[device.status] || 0)) {
        device.status = next; if (!device.history.includes(next)) device.history.push(next);
        if (action === AOT_BACKUP_RESTORE_DATA_ACTION) {
          device.reason = typeof body.reason === "string" ? body.reason : (["FAILED", "FAILED_NOT_INSTALLED", "TIMEOUT"].includes(next) ? String(body.reason || "worker_reported_failure").slice(0, 160) : null);
          if (Number.isSafeInteger(body.app_count) && body.app_count >= 0) device.app_count = body.app_count;
          if (Number.isSafeInteger(body.selected_count) && body.selected_count >= 0) device.selected_count = body.selected_count;
        } else {
          device.reason = ["FAILED", "FAILED_NOT_INSTALLED", "TIMEOUT"].includes(next) ? String(body.reason || "worker_reported_failure").slice(0, 160) : null;
        }
        device.executed = body.executed === true; device.updated_at = Date.now();
        await this.writeFleet(record); await this.broadcastFleetState();
      }
    }
    return json({ ok: true, action_id: actionId, device_id: id, status: device.status });
  }

  async registerFleetDevice(request, operation) {
    const body = await this.readJson(request);
    const deviceId = validDeviceId(body?.device_id || body?.new_device_id);
    if (!deviceId) return json({ ok: false, error: "invalid_device_id" }, 400);
    const record = await this.readFleet();
    if (operation === "reset") {
      const oldId = validDeviceId(body?.old_device_id);
      if (!oldId || oldId === deviceId) return json({ ok: false, error: "invalid_identity_reset" }, 400);
      delete record.devices[oldId]; this.aotLive.delete(oldId);
      for (const socket of this.ctx.getWebSockets(this.aotSocketTag("device", "fleet", oldId))) {
        try { socket.close(4002, "identity_changed"); } catch (error) {}
      }
    }
    record.devices[deviceId] = { ...(record.devices[deviceId] || {}), device_id: deviceId, joined_at: record.devices[deviceId]?.joined_at || Date.now() };
    await this.writeFleet(record);
    if (operation === "verify") {
      const online = this.ctx.getWebSockets(this.aotSocketTag("device", "fleet", deviceId)).length > 0;
      if (!online) return json({ ok: false, error: "device_not_online_in_aot_hub", online: false }, 409);
      return json({ ok: true, device_id: deviceId, online: true, visible_in_hub: true });
    }
    return json({ ok: true, device_id: deviceId });
  }

  async fetch(request) {
    const url =
      new URL(request.url);

    if (
      request.method === "GET" &&
      url.pathname === "/aot/hub/state"
    ) {
      return this.getFleetHubState();
    }

    if (
      request.method === "POST" &&
      url.pathname === "/aot/hub/control"
    ) {
      return this.controlFleetHub(request);
    }

    if (request.method === "POST" && url.pathname === "/aot/registration/discover") {
      return this.registerFleetDevice(request, "discover");
    }
    if (request.method === "POST" && url.pathname === "/aot/registration/reset") {
      return this.registerFleetDevice(request, "reset");
    }
    if (request.method === "POST" && url.pathname === "/aot/registration/verify") {
      return this.registerFleetDevice(request, "verify");
    }

    if (
      request.method === "GET" &&
      url.pathname === "/aot/hub/dashboard-ws"
    ) {
      return this.connectFleetDashboard(request);
    }


    if (
      request.method === "GET" &&
      url.pathname === "/aot/ws"
    ) {
      return this.connectFleetWebSocket(url, request);
    }

    if (
      request.method === "POST" &&
      url.pathname === "/aot/action"
    ) {
      return this.dispatchAotAction(
        request
      );
    }

    if (
      request.method === "POST" &&
      url.pathname === "/aot/ack"
    ) {
      return this.dispatchFleetAck(request);
    }

    if (
      request.method === "POST" &&
      url.pathname === "/command/enqueue"
    ) {
      return this.enqueueCommand(request);
    }

    if (
      request.method === "GET" &&
      url.pathname === "/command/ws"
    ) {
      return this.connectCommandWebSocket(url, request);
    }

    if (
      request.method ===
        "POST" &&
      url.pathname ===
        "/report"
    ) {
      return this.report(
        request
      );
    }
    if (
      request.method ===
        "GET" &&
      url.pathname ===
        "/device"
    ) {
      return this.getDevice(
        url
      );
    }
    if (
      request.method ===
        "GET" &&
      url.pathname ===
        "/devices"
    ) {
      return this.listDevices();
    }
    if (
      request.method ===
        "POST" &&
      url.pathname ===
        "/revoke"
    ) {
      return this.setRevocation(
        request,
        true
      );
    }
    if (
      request.method ===
        "POST" &&
      url.pathname ===
        "/restore"
    ) {
      return this.setRevocation(
        request,
        false
      );
    }
    return json(
      {
        ok: false,
        error: "not_found",
      },
      404
    );
  }
}
