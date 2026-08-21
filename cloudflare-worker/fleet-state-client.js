// fleet-state-client.js - Low-level Durable Object client for FleetState

export function fleetStateStub(env) {
  if (!env.FLEET_STATE) {
    throw new Error(
      "Thiếu Durable Object binding FLEET_STATE"
    );
  }
  const objectId =
    env.FLEET_STATE.idFromName(
      "aotscript-fleet"
    );
  return env.FLEET_STATE.get(
    objectId
  );
}

export async function fleetStateCall(
  env,
  pathname,
  options = {}
) {
  const headers = {
    Accept: "application/json",
  };
  const init = {
    method:
      options.method || "GET",
    headers,
  };
  if (
    Object.prototype.hasOwnProperty.call(
      options,
      "body"
    )
  ) {
    headers["Content-Type"] =
      "application/json";
    init.body =
      JSON.stringify(options.body);
  }
  const response =
    await fleetStateStub(env).fetch(
      new Request(
        `https://fleet-state.internal${pathname}`,
        init
      )
    );
  const raw =
    await response.text();
  let data = {};
  if (raw) {
    try {
      data = JSON.parse(raw);
    } catch (error) {
      data = {
        ok: false,
        error:
          "invalid_fleet_state_response",
      };
    }
  }
  return {
    response,
    data,
  };
}

export async function listFleetDeviceRecords(
  env
) {
  const result =
    await fleetStateCall(
      env,
      "/devices"
    );
  if (!result.response.ok) {
    throw new Error(
      result.data?.error ||
      `Fleet state HTTP ${result.response.status}`
    );
  }
  return Array.isArray(
    result.data?.records
  )
    ? result.data.records
    : [];
}

export async function getFleetDeviceRecord(
  env,
  deviceId
) {
  const result =
    await fleetStateCall(
      env,
      `/device?id=${encodeURIComponent(deviceId)}`
    );
  if (result.response.status === 404) {
    return null;
  }
  if (!result.response.ok) {
    throw new Error(
      result.data?.error ||
      `Fleet state HTTP ${result.response.status}`
    );
  }
  return (
    result.data?.record &&
    typeof result.data.record ===
      "object"
  )
    ? result.data.record
    : null;
}

export async function setFleetDeviceRevocation(
  env,
  deviceId,
  revoked
) {
  const result =
    await fleetStateCall(
      env,
      revoked
        ? "/revoke"
        : "/restore",
      {
        method: "POST",
        body: {
          device_id: deviceId,
        },
      }
    );
  if (!result.response.ok) {
    throw new Error(
      result.data?.error ||
      `Fleet revocation HTTP ${result.response.status}`
    );
  }
}

export async function enqueueFastCommand(
  env,
  commandId,
  deviceIds,
  expiresAt,
  commandBlock
) {
  const result = await fleetStateCall(
    env,
    "/command/enqueue",
    {
      method: "POST",
      body: {
        command_id: commandId,
        device_ids: deviceIds,
        expires_at: expiresAt,
        command_block: commandBlock,
      },
    }
  );

  if (!result.response.ok) {
    throw new Error(
      result.data?.error ||
      `Fast command HTTP ${result.response.status}`
    );
  }

  return result.data;
}
