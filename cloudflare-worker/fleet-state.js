
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

export class FleetState
  extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.ctx = ctx;
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

  webSocketMessage(socket, message) {
    if (message === "ping") {
      try {
        socket.send("pong");
      } catch (error) {
        // Runtime cleans dead sockets.
      }
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
    const pair = new WebSocketPair();
    const [client, server] =
      Object.values(pair);
    this.ctx.acceptWebSocket(
      server,
      [
        this.aotSocketTag(
          role,
          sessionId,
          deviceId
        ),
      ]
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
    if (
      !body ||
      body.protocol !== "phase3-1" ||
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
