
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
const AOT_BATCH_PACKAGE = "org.swiftapps.swiftbackup";
const AOT_BATCH_TTL_MS = 12 * 1000;

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
      const key =
        this.aotLiveKey(
          identity.sessionId,
          identity.deviceId
        );
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


  webSocketClose() {}

  webSocketError() {}



  aotSocketTag(
    role,
    sessionId,
    deviceId
  ) {
    return `aot:${role}:${sessionId}:${deviceId}`;
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

  parseAotSocketIdentity(socket) {
    let tags;
    try {
      tags = this.ctx.getTags(socket);
    } catch (error) {
      return null;
    }
    const tag = tags.find(
      (value) =>
        String(value).startsWith("aot:")
    );
    if (!tag) {
      return null;
    }
    const parts = String(tag).split(":");
    if (parts.length !== 4) {
      return null;
    }
    const role = parts[1];
    const sessionId = parts[2];
    const deviceId =
      validDeviceId(parts[3]);
    if (
      !["reference", "follower"].includes(role) ||
      !/^[A-Za-z0-9_-]{1,64}$/.test(
        sessionId
      ) ||
      !deviceId
    ) {
      return null;
    }
    return {
      role,
      sessionId,
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
      body.protocol !== "phase4-1" ||
      String(body.role || "") !==
        identity.role ||
      String(body.session_id || "") !==
        identity.sessionId ||
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
        return {
          device_id: item.device_id,
          status,
          history,
          display_status: history.join(" → ") || status,
          updated_at: Number(item.updated_at || batch.created_at || 0),
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
      action: AOT_BATCH_ACTION,
      package: AOT_BATCH_PACKAGE,
      created_at: Number(batch.created_at || 0),
      expires_at: expiresAt,
      devices,
    };
  }

  async dispatchOpenSwiftBackup(sessionId, record) {
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
    const actionId =
      `swift-${Date.now()}-${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`;
    const createdAt = Date.now();
    const expiresAt = createdAt + AOT_BATCH_TTL_MS;
    const online = [];
    const devices = {};
    for (const member of members) {
      const connected = this.ctx.getWebSockets(
        this.aotSocketTag(member.role, sessionId, member.device_id)
      ).length > 0;
      devices[member.device_id] = {
        device_id: member.device_id,
        role: member.role,
        status: connected ? "SENT" : "SKIPPED_OFFLINE",
        history: connected ? ["SENT"] : ["SKIPPED_OFFLINE"],
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
      return this.dispatchOpenSwiftBackup(sessionId, record);
    }

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
    await this.rememberAotMember(
      sessionId,
      role,
      deviceId
    );
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
    const isBatch =
      body?.protocol === "phase4-1" &&
      body?.batch_action === AOT_BATCH_ACTION;
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
      const allowed = new Set([
        "ACCEPTED",
        "OPENED",
        "FAILED_NOT_INSTALLED",
        "FAILED",
        "TIMEOUT",
        "DUPLICATE",
      ]);
      const status = String(body.status || "");
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
          device.updated_at = Date.now();
        }
      }
      await this.writeAotSession(sessionId, record);
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

  async fetch(request) {
    const url =
      new URL(request.url);

    if (
      request.method === "GET" &&
      url.pathname === "/aot/hub/state"
    ) {
      return this.getAotHubState(
        url
      );
    }

    if (
      request.method === "POST" &&
      url.pathname === "/aot/hub/control"
    ) {
      return this.controlAotHub(
        request
      );
    }


    if (
      request.method === "GET" &&
      url.pathname === "/aot/ws"
    ) {
      return this.connectAotWebSocket(
        url,
        request
      );
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
      return this.dispatchAotAck(
        request
      );
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
