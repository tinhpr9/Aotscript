
import {
  DurableObject,
} from "cloudflare:workers";

const REVOCATION_RECHECK_MS =
  10 * 60 * 1000;

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
