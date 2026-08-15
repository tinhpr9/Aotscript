import {
  checkActiveRollout,
  getActiveRollout,
  handleRolloutCallback,
  handleRolloutCommand,
  isRolloutMetadataKey,
} from "./rollout.js";
import { FleetState } from "./fleet-state.js";
export { FleetState };

const OWNER = "tinhpr9";
const REPO = "Aotscript";
const BRANCH = "main";

const TARGET_FILES = {
  all: "lenh_all.txt",
  marmot: "lenh_marmot.txt",
  nova: "lenh_nova.txt",
};

const DEFAULT_GROUP_LABELS = {
  MARMOT: "NHÓM 1",
  NOVA: "NHÓM 2",
};

const DEVICE_ALIAS_PREFIX = "device_alias:";
const GROUP_LABEL_PREFIX = "group_label:";
const PAIR_PREFIX = "pair:";
const PAIR_DEVICE_PREFIX = "pair_device:";
const PAIR_RATE_PREFIX = "pair_rate:";
const PAIR_TTL_SECONDS = 10 * 60;
const PAIR_RATE_SECONDS = 60;
const MAINTENANCE_PREFIX = "maintenance:";
const REVOKED_PREFIX = "revoked:";
const HEALTH_STATE_PREFIX = "health_state:";
const SETTING_PREFIX = "setting:";
const GROUP_NUMBER_MIGRATION_KEY =
  `${SETTING_PREFIX}numbered_groups_v1`;
const ALERTS_SETTING_KEY =
  `${SETTING_PREFIX}alerts_enabled`;
const HEALTH_BOOTSTRAP_KEY =
  `${SETTING_PREFIX}health_bootstrapped`;
const OFFLINE_ALERT_AFTER_MS =
  3 * 60 * 1000;
const RECENT_ERROR_WINDOW_MS =
  24 * 60 * 60 * 1000;
const LOW_BATTERY_PERCENT = 15;
const BATTERY_RECOVERY_PERCENT = 20;
const LOW_STORAGE_BYTES =
  2 * 1024 * 1024 * 1024;
const STORAGE_RECOVERY_BYTES =
  3 * 1024 * 1024 * 1024;
const QUIET_HOUR_START = 23;
const QUIET_HOUR_END = 7;
const COMMAND_TTL_MS = 5 * 60 * 1000;
const DEFERRED_COMMAND_TTL_MS =
  24 * 60 * 60 * 1000;
const MAX_PENDING_COMMAND_BLOCKS = 256;
const MAX_PENDING_COMMAND_BYTES =
  256 * 1024;
const COMMAND_METADATA_TTL_SECONDS = 30 * 24 * 60 * 60;
const ONLINE_WINDOW_MS = 90 * 1000;
const TARGETING_AGENT_VERSION = "fleet-ops-1";
const DANGEROUS_COMMAND_KEYS = new Set([
  "setup_boot",
  "setup_caylapbu",
  "run_caylapbu",
  "update_delta",
  "repair",
  "reboot",
]);

const COMMANDS = {
  idle: { value: "IDLE", bit: 1, label: "💤 IDLE" },
  setup_vip: { value: "SETUP_VIP", bit: 2, label: "⚙️ VIP" },
  install_track: { value: "INSTALL_TRACK", bit: 4, label: "📡 TRACK" },
  setup_boot: { value: "SETUP_BOOT", bit: 8, label: "🔧 BOOT" },
  setup_caylapbu: { value: "SETUP_CAYLAPBU", bit: 16, label: "🌱 CÀI CAYLAPBU" },
  run_caylapbu: { value: "RUN_CAYLAPBU", bit: 32, label: "▶️ CHẠY CAYLAPBU" },
  update_delta: { value: "UPDATE_DELTA", bit: 64, label: "🔁 DELTA" },
  repair: { value: "RECONCILE_SYSTEM", bit: 2048, label: "🛠 TỰ SỬA" },
  update_solver: { value: "UPDATE_SOLVER", bit: 256, label: "🧠 SOLVER" },
  update_script: { value: "UPDATESCRIPT", bit: 512, label: "📝 SCRIPT" },

  reload_novagag2: {
    value: "RELOAD_NOVAGAG2",
    bit: 1024,
    label: "🔄 NOVAGAG2",
  },

  reboot: { value: "REBOOT", bit: 128, label: "🔌 REBOOT" },
};

const COMMAND_ORDER = [
  "idle",
  "setup_vip",
  "install_track",
  "setup_boot",
  "setup_caylapbu",
  "run_caylapbu",
  "update_delta",
  "repair",
  "update_solver",
  "update_script",
  "reboot",
];

const ALLOWED_REPORT_STATUSES = [
  "heartbeat",
  "received",
  "running",
  "success",
  "error",
  "expired",
];



function normalizeDeviceId(value) {
  const raw = String(value || "").trim();

  const dynamicMatch =
    raw.match(/^m([1-9]\d{0,5})$/i);

  if (dynamicMatch) {
    return `m${dynamicMatch[1]}`;
  }

  const legacyMatch =
    raw.match(/^(MARMOT|NOVA)-(\d{2})$/i);

  if (!legacyMatch) return null;

  const group = legacyMatch[1].toUpperCase();
  const index = Number(legacyMatch[2]);

  if (index < 1 || index > 10) {
    return null;
  }

  return `${group}-${String(index).padStart(2, "0")}`;
}

function normalizeDeviceGroup(value) {
  const group =
    String(value || "").trim().toUpperCase();

  return ["MARMOT", "NOVA"].includes(group)
    ? group
    : null;
}

function normalizeGroupInput(value) {
  const raw = String(value || "")
    .trim()
    .toUpperCase()
    .replace(/[\s_-]+/g, "");

  if (
    ["1", "NHOM1", "GROUP1", "MARMOT"].includes(raw)
  ) {
    return "MARMOT";
  }

  if (
    ["2", "NHOM2", "GROUP2", "NOVA"].includes(raw)
  ) {
    return "NOVA";
  }

  return null;
}

function normalizeTargetInput(value) {
  const raw = String(value || "")
    .trim()
    .toLowerCase();

  if (raw === "all") {
    return "all";
  }

  const group =
    normalizeGroupInput(raw);

  if (group === "MARMOT") {
    return "marmot";
  }

  if (group === "NOVA") {
    return "nova";
  }

  return null;
}

async function ensureNumberedGroupLabels(env) {
  const migrated =
    await env.DEVICE_STATUS.get(
      GROUP_NUMBER_MIGRATION_KEY
    );

  if (migrated === "1") {
    return;
  }

  await Promise.all([
    env.DEVICE_STATUS.put(
      `${GROUP_LABEL_PREFIX}MARMOT`,
      DEFAULT_GROUP_LABELS.MARMOT
    ),
    env.DEVICE_STATUS.put(
      `${GROUP_LABEL_PREFIX}NOVA`,
      DEFAULT_GROUP_LABELS.NOVA
    ),
    env.DEVICE_STATUS.put(
      GROUP_NUMBER_MIGRATION_KEY,
      "1"
    ),
  ]);
}


function sanitizeLabel(value, maxLength = 48) {
  const label = String(value || "")
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  if (!label || label.length > maxLength) {
    return null;
  }

  return label;
}

function isDeviceStatusMetadataKey(name) {
  return (
    name === "latest_command" ||
    name.startsWith("cmd:") ||
    name.startsWith(DEVICE_ALIAS_PREFIX) ||
    name.startsWith(GROUP_LABEL_PREFIX) ||
    name.startsWith(PAIR_PREFIX) ||
    name.startsWith(PAIR_DEVICE_PREFIX) ||
    name.startsWith(PAIR_RATE_PREFIX) ||
    name.startsWith(MAINTENANCE_PREFIX) ||
    name.startsWith(REVOKED_PREFIX) ||
    name.startsWith(HEALTH_STATE_PREFIX) ||
    isRolloutMetadataKey(name) ||
    name.startsWith(SETTING_PREFIX)
  );
}

async function getDeviceAlias(deviceId, env) {
  try {
    const value = await env.DEVICE_STATUS.get(
      `${DEVICE_ALIAS_PREFIX}${deviceId}`
    );
    return sanitizeLabel(value, 48);
  } catch (error) {
    return null;
  }
}

async function getGroupLabel(group, env) {
  const normalized =
    normalizeDeviceGroup(group);

  if (!normalized) {
    return String(group || "-").toUpperCase();
  }

  try {
    const value =
      await env.DEVICE_STATUS.get(
        `${GROUP_LABEL_PREFIX}${normalized}`
      );

    return (
      sanitizeLabel(value, 32) ||
      DEFAULT_GROUP_LABELS[normalized]
    );
  } catch (error) {
    return (
      DEFAULT_GROUP_LABELS[normalized] ||
      normalized
    );
  }
}

async function getTargetLabel(target, env) {
  if (target === "all") {
    return "TẤT CẢ";
  }
  return getGroupLabel(target, env);
}

function compareDeviceIds(left, right) {
  return left.localeCompare(
    right,
    undefined,
    {
      numeric: true,
      sensitivity: "base",
    }
  );
}


function fleetStateStub(env) {
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
async function fleetStateCall(
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
async function reportFleetDevice(
  env,
  payload
) {
  let result =
    await fleetStateCall(
      env,
      "/report",
      {
        method: "POST",
        body: payload,
      }
    );
  if (
    result.response.status === 409 &&
    result.data?.error ===
      "revocation_unknown"
  ) {
    const revoked =
      await isDeviceRevoked(
        payload.device_id,
        env
      );
    result =
      await fleetStateCall(
        env,
        "/report",
        {
          method: "POST",
          body: {
            ...payload,
            revocation_checked:
              true,
            revoked,
          },
        }
      );
  }
  return result;
}
async function listFleetDeviceRecords(
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
async function getFleetDeviceRecord(
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
async function setFleetDeviceRevocation(
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

async function enqueueFastCommand(
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

async function handleAgentCommandWebSocket(
  request,
  env,
  url
) {
  if (!isAuthorizedAgentRequest(request, env)) {
    return noStoreJson(
      { ok: false, error: "unauthorized" },
      401
    );
  }

  if (
    String(request.headers.get("Upgrade") || "").toLowerCase()
    !== "websocket"
  ) {
    return noStoreJson(
      { ok: false, error: "upgrade_required" },
      426
    );
  }

  const deviceId = normalizeDeviceId(
    url.searchParams.get("device_id")
  );

  if (!deviceId) {
    return noStoreJson(
      { ok: false, error: "invalid_device_id" },
      400
    );
  }

  if (await isDeviceRevoked(deviceId, env)) {
    return noStoreJson(
      { ok: false, error: "device_revoked" },
      410
    );
  }

  return fleetStateStub(env).fetch(
    new Request(
      `https://fleet-state.internal/command/ws?id=${encodeURIComponent(deviceId)}`,
      {
        method: "GET",
        headers: { Upgrade: "websocket" },
      }
    )
  );
}


const AOT_PROTOCOL_VERSION = "phase3-1";
const AOT_MAX_JSON_BYTES = 384 * 1024;
const AOT_MAX_TARGETS = 128;
const AOT_ACTION_TTL_MAX_MS = 30 * 1000;

function normalizeAotSessionId(value) {
  const raw = String(value || "").trim();
  return /^[A-Za-z0-9_-]{1,64}$/.test(raw)
    ? raw
    : null;
}

function normalizeAotActionId(value) {
  const raw = String(value || "").trim();
  return /^[A-Za-z0-9_-]{1,128}$/.test(raw)
    ? raw
    : null;
}

function normalizeAotFingerprint(value) {
  const raw = String(value || "").trim().toLowerCase();
  return /^[a-f0-9]{24}$/.test(raw)
    ? raw
    : null;
}

async function readAotJson(request) {
  const length = Number(
    request.headers.get("Content-Length") || 0
  );
  if (
    Number.isFinite(length) &&
    length > AOT_MAX_JSON_BYTES
  ) {
    return { error: "payload_too_large" };
  }
  const raw = await request.text();
  if (
    new TextEncoder().encode(raw).length >
    AOT_MAX_JSON_BYTES
  ) {
    return { error: "payload_too_large" };
  }
  try {
    const value = JSON.parse(raw);
    return (
      value &&
      typeof value === "object" &&
      !Array.isArray(value)
    )
      ? { value }
      : { error: "invalid_json_body" };
  } catch (error) {
    return { error: "invalid_json_body" };
  }
}

function normalizeAotAction(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const kind = String(value.kind || "");
  if (kind === "tap_selector") {
    const resourceId = String(
      value.resource_id || ""
    ).trim();
    if (
      !resourceId ||
      resourceId.length > 200 ||
      /[\u0000-\u001f\u007f]/.test(resourceId)
    ) {
      return null;
    }
    return {
      kind,
      resource_id: resourceId,
    };
  }
  if (kind === "back") {
    return { kind };
  }
  if (kind === "swipe") {
    const values = [
      Number(value.x1),
      Number(value.y1),
      Number(value.x2),
      Number(value.y2),
    ];
    if (
      values.some(
        (item) =>
          !Number.isFinite(item) ||
          item < 0 ||
          item > 1
      )
    ) {
      return null;
    }
    const duration = Math.min(
      5000,
      Math.max(
        50,
        Number(value.duration_ms) || 300
      )
    );
    return {
      kind,
      x1: values[0],
      y1: values[1],
      x2: values[2],
      y2: values[3],
      duration_ms: Math.round(duration),
    };
  }
  return null;
}

async function handleAotControlWebSocket(
  request,
  env,
  url
) {
  if (!isAuthorizedAgentRequest(request, env)) {
    return noStoreJson(
      { ok: false, error: "unauthorized" },
      401
    );
  }
  if (
    String(
      request.headers.get("Upgrade") || ""
    ).toLowerCase() !== "websocket"
  ) {
    return noStoreJson(
      { ok: false, error: "upgrade_required" },
      426
    );
  }
  const deviceId = normalizeDeviceId(
    url.searchParams.get("device_id")
  );
  if (
    !deviceId
  ) {
    return noStoreJson(
      { ok: false, error: "invalid_aot_socket" },
      400
    );
  }
  if (await isDeviceRevoked(deviceId, env)) {
    return noStoreJson(
      { ok: false, error: "device_revoked" },
      410
    );
  }
  const query = new URLSearchParams({ id: deviceId });
  return fleetStateStub(env).fetch(
    new Request(
      `https://fleet-state.internal/aot/ws?${query.toString()}`,
      {
        method: "GET",
        headers: { Upgrade: "websocket" },
      }
    )
  );
}

async function handleAotControlAction(
  request,
  env
) {
  if (!isAuthorizedAgentRequest(request, env)) {
    return noStoreJson(
      { ok: false, error: "unauthorized" },
      401
    );
  }
  const parsed = await readAotJson(request);
  if (parsed.error) {
    return noStoreJson(
      { ok: false, error: parsed.error },
      400
    );
  }
  const body = parsed.value;
  const sessionId = normalizeAotSessionId(
    body.session_id
  );
  const referenceId = normalizeDeviceId(
    body.reference_device_id
  );
  const actionId = normalizeAotActionId(
    body.action_id
  );
  const precondition = normalizeAotFingerprint(
    body.precondition
  );
  const action = normalizeAotAction(body.action);
  const expiresAt = Number(body.expires_at);
  const rawTargets = Array.isArray(
    body.target_device_ids
  )
    ? body.target_device_ids
    : [];
  const targets = [];
  const seen = new Set();
  for (const raw of rawTargets) {
    const deviceId = normalizeDeviceId(raw);
    if (
      deviceId &&
      !seen.has(deviceId)
    ) {
      seen.add(deviceId);
      targets.push(deviceId);
    }
  }
  const now = Date.now();
  if (
    body.protocol !== AOT_PROTOCOL_VERSION ||
    !sessionId ||
    !referenceId ||
    !actionId ||
    !precondition ||
    !action ||
    !Number.isFinite(expiresAt) ||
    expiresAt <= now ||
    expiresAt - now > AOT_ACTION_TTL_MAX_MS ||
    targets.length < 1 ||
    targets.length > AOT_MAX_TARGETS
  ) {
    return noStoreJson(
      { ok: false, error: "invalid_aot_action" },
      400
    );
  }
  if (await isDeviceRevoked(referenceId, env)) {
    return noStoreJson(
      { ok: false, error: "device_revoked" },
      410
    );
  }
  for (const target of targets) {
    if (await isDeviceRevoked(target, env)) {
      return noStoreJson(
        {
          ok: false,
          error: "target_device_revoked",
          device_id: target,
        },
        410
      );
    }
  }
  const result = await fleetStateCall(
    env,
    "/aot/action",
    {
      method: "POST",
      body: {
        protocol: AOT_PROTOCOL_VERSION,
        session_id: sessionId,
        reference_device_id: referenceId,
        target_device_ids: targets,
        action_id: actionId,
        expires_at: expiresAt,
        precondition,
        action,
      },
    }
  );
  return noStoreJson(
    result.data,
    result.response.status
  );
}

async function handleAotControlAck(
  request,
  env
) {
  if (!isAuthorizedAgentRequest(request, env)) {
    return noStoreJson(
      { ok: false, error: "unauthorized" },
      401
    );
  }
  const parsed = await readAotJson(request);
  if (parsed.error) {
    return noStoreJson(
      { ok: false, error: parsed.error },
      400
    );
  }
  const body = parsed.value;
  const deviceId = normalizeDeviceId(body.device_id || body.follower_device_id);
  const actionId = normalizeAotActionId(
    body.action_id
  );
  const status = String(body.status || "");
  const batchAction = String(body.batch_action || "");
  const isBatch = [AOT_HUB_PROTOCOL_VERSION, "phase4-1"].includes(body.protocol) &&
    ["OPEN_SWIFT_BACKUP", "OPEN_SWIFT_APPS", "BACKUP_RESTORE_DATA", "UPDATE_WORKER"].includes(batchAction);
  const allowedStatus = new Set(
    batchAction === "UPDATE_WORKER"
      ? ["DOWNLOADING", "VERIFIED", "INSTALLING", "RESTARTING", "HEALTHY", "ROLLED_BACK", "FAILED"]
      : isBatch
      ? (
          batchAction === "BACKUP_RESTORE_DATA"
          ? [
              "ACCEPTED",
              "SWIFT_OPENED",
              "APPS_OPENED",
              "FILTERED",
              "SELECTED",
              "OPTIONS_VERIFIED",
              "BACKUP_STARTED",
              "FAILED_NOT_INSTALLED",
              "FAILED",
              "TIMEOUT",
              "DUPLICATE",
            ]
          : [
              "ACCEPTED",
              "OPENED",
              "APPS_OPENED",
              "FAILED_NOT_INSTALLED",
              "FAILED",
              "TIMEOUT",
              "DUPLICATE",
            ]
        )
      : [
          "success",
          "duplicate",
          "out_of_sync",
          "expired",
          "error",
        ]
  );
  let preview = null;
  if (
    typeof body.preview_b64 === "string" &&
    body.preview_b64
  ) {
    if (
      body.preview_b64.length > 256 * 1024 ||
      !/^[A-Za-z0-9+/=]+$/.test(
        body.preview_b64
      )
    ) {
      return noStoreJson(
        {
          ok: false,
          error: "invalid_preview",
        },
        400
      );
    }
    preview = body.preview_b64;
  }
  if (
    !isBatch || !deviceId ||
    !actionId ||
    !allowedStatus.has(status)
  ) {
    return noStoreJson(
      { ok: false, error: "invalid_aot_ack" },
      400
    );
  }
  const clean = {
    protocol: isBatch
      ? AOT_HUB_PROTOCOL_VERSION
      : AOT_PROTOCOL_VERSION,
    device_id: deviceId,
    action_id: actionId,
    status,
    executed: body.executed === true,
    screen_changed: body.screen_changed === true,
  };
  if (isBatch) {
    clean.batch_action = batchAction;
    if (batchAction === "UPDATE_WORKER") {
      clean.worker_version = String(body.worker_version || "").slice(0, 80);
      clean.channel = ["canary", "stable"].includes(body.channel) ? body.channel : "";
    } else if (batchAction === "BACKUP_RESTORE_DATA") {
      if (typeof body.reason === "string") {
        clean.reason = body.reason.trim().slice(0, 160);
      }
      if (Number.isSafeInteger(body.app_count) && body.app_count >= 0 && body.app_count <= 100000) {
        clean.app_count = body.app_count;
      }
      if (Number.isSafeInteger(body.selected_count) && body.selected_count >= 0 && body.selected_count <= 100000) {
        clean.selected_count = body.selected_count;
      }
    }
  }
  for (const key of [
    "before_fingerprint",
    "after_fingerprint",
    "preview_sha256",
  ]) {
    if (
      typeof body[key] === "string" &&
      body[key].length <= 80
    ) {
      clean[key] = body[key];
    }
  }
  if (
    Number.isFinite(Number(body.preview_bytes)) &&
    Number(body.preview_bytes) >= 0
  ) {
    clean.preview_bytes = Math.floor(
      Number(body.preview_bytes)
    );
  }
  const result = await fleetStateCall(
    env,
    "/aot/ack",
    {
      method: "POST",
      body: clean,
    }
  );
  return noStoreJson(
    result.data,
    result.response.status
  );
}

async function handleAotControlHealth(
  request,
  env
) {
  if (!isAuthorizedAgentRequest(request, env)) {
    return noStoreJson(
      { ok: false, error: "unauthorized" },
      401
    );
  }
  return noStoreJson({
    ok: true,
    protocol: AOT_PROTOCOL_VERSION,
  });
}

async function handleAotRegistration(request, env, operation) {
  if (!isAuthorizedAgentRequest(request, env)) {
    return noStoreJson({ ok: false, error: "unauthorized" }, 401);
  }
  const parsed = await readAotJson(request);
  if (parsed.error) {
    return noStoreJson({ ok: false, error: parsed.error }, 400);
  }
  const deviceId = normalizeDeviceId(parsed.value.device_id || parsed.value.new_device_id);
  if (!deviceId) {
    return noStoreJson({ ok: false, error: "invalid_device_id" }, 400);
  }
  if (await isDeviceRevoked(deviceId, env)) {
    return noStoreJson({ ok: false, error: "device_revoked" }, 410);
  }
  const allowed = new Set(["discover", "reset", "verify"]);
  if (!allowed.has(operation)) {
    return noStoreJson({ ok: false, error: "invalid_registration_operation" }, 400);
  }
  const result = await fleetStateCall(env, `/aot/registration/${operation}`, {
    method: "POST",
    body: parsed.value,
  });
  return noStoreJson(result.data, result.response.status);
}


const AOT_HUB_PROTOCOL_VERSION = "fleet-batch-v1";
const AOT_HUB_AUTH_MAX_AGE_SECONDS = 60 * 60;
const AOT_HUB_INITDATA_MAX_BYTES = 16 * 1024;
const AOT_HUB_FALLBACK_URL =
  "https://billowing-haze-0cafaotscript-control.tinh1020pr.workers.dev/aot/hub";

function aotHubPublicUrl(env) {
  const configured = String(
    env.AOT_HUB_URL || ""
  ).trim();
  if (isValidUrl(configured)) {
    return configured;
  }
  return AOT_HUB_FALLBACK_URL;
}

function bytesToHex(bytes) {
  return Array.from(bytes)
    .map(
      (byte) =>
        byte.toString(16).padStart(2, "0")
    )
    .join("");
}

async function hmacSha256Bytes(
  keyBytes,
  dataBytes
) {
  const key = await crypto.subtle.importKey(
    "raw",
    keyBytes,
    {
      name: "HMAC",
      hash: "SHA-256",
    },
    false,
    ["sign"]
  );
  const signature =
    await crypto.subtle.sign(
      "HMAC",
      key,
      dataBytes
    );
  return new Uint8Array(signature);
}

function constantTimeTextEqual(left, right) {
  const a = String(left || "");
  const b = String(right || "");
  if (a.length !== b.length) {
    return false;
  }
  let diff = 0;
  for (let index = 0; index < a.length; index += 1) {
    diff |=
      a.charCodeAt(index) ^
      b.charCodeAt(index);
  }
  return diff === 0;
}

async function validateAotTelegramInitData(
  rawValue,
  env
) {
  const raw = String(rawValue || "");
  if (
    !raw ||
    new TextEncoder().encode(raw).length >
      AOT_HUB_INITDATA_MAX_BYTES
  ) {
    return null;
  }
  const botToken = String(
    env.TELEGRAM_BOT_TOKEN || ""
  );
  const adminId = String(
    env.TELEGRAM_ADMIN_USER_ID || ""
  ).trim();
  if (!botToken || !adminId) {
    return null;
  }
  const params = new URLSearchParams(raw);
  const hash = String(
    params.get("hash") || ""
  ).toLowerCase();
  if (!/^[a-f0-9]{64}$/.test(hash)) {
    return null;
  }
  params.delete("hash");
  const entries = [
    ...params.entries(),
  ].sort((left, right) =>
    left[0].localeCompare(right[0])
  );
  const dataCheckString = entries
    .map(
      ([key, value]) =>
        `${key}=${value}`
    )
    .join("\n");
  const encoder = new TextEncoder();
  const secretKey =
    await hmacSha256Bytes(
      encoder.encode("WebAppData"),
      encoder.encode(botToken)
    );
  const calculated = bytesToHex(
    await hmacSha256Bytes(
      secretKey,
      encoder.encode(dataCheckString)
    )
  );
  if (
    !constantTimeTextEqual(
      calculated,
      hash
    )
  ) {
    return null;
  }
  const authDate = Number(
    params.get("auth_date")
  );
  const nowSeconds = Math.floor(
    Date.now() / 1000
  );
  if (
    !Number.isFinite(authDate) ||
    authDate <= 0 ||
    authDate > nowSeconds + 60 ||
    nowSeconds - authDate >
      AOT_HUB_AUTH_MAX_AGE_SECONDS
  ) {
    return null;
  }
  let user;
  try {
    user = JSON.parse(
      params.get("user") || "{}"
    );
  } catch (error) {
    return null;
  }
  if (
    !user ||
    typeof user !== "object" ||
    String(user.id || "") !== adminId
  ) {
    return null;
  }
  return {
    id: String(user.id),
    first_name:
      String(user.first_name || "")
        .slice(0, 80),
  };
}

async function requireAotHubAdmin(
  request,
  env
) {
  const authHeader = request.headers.get("Authorization");
  if (authHeader && authHeader.startsWith("Bearer ")) {
    const token = authHeader.substring(7);
    const secret = env.AOT_HUB_API_SECRET;
    if (secret && token === secret) {
      return { ok: true, user: { id: "server", first_name: "Server" } };
    }
    // Fail-closed for server-to-server auth
    return {
      ok: false,
      response: noStoreJson(
        { ok: false, error: "hub_unauthorized" },
        401
      ),
    };
  }

  const user =
    await validateAotTelegramInitData(
      request.headers.get(
        "X-Telegram-Init-Data"
      ),
      env
    );
  if (!user) {
    return {
      ok: false,
      response: noStoreJson(
        {
          ok: false,
          error: "hub_unauthorized",
        },
        401
      ),
    };
  }
  return {
    ok: true,
    user,
  };
}

function aotHubHtml() {
  return `<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="color-scheme" content="dark light">
  <title>AOT Group Control</title>
  <script src="https://telegram.org/js/telegram-web-app.js?63"></script>
  <style>
    :root {
      color-scheme: dark;
      font-family: system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      background: #101114;
      color: #f5f7fb;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: #101114;
      color: #f5f7fb;
    }
    button,input {
      font: inherit;
    }
    .app {
      max-width: 980px;
      margin: 0 auto;
      padding: 14px;
    }
    .top {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }
    h1 {
      font-size: 20px;
      margin: 0;
      flex: 1;
    }
    .session {
      display: flex;
      gap: 6px;
      width: 100%;
    }
    .session input {
      flex: 1;
      min-width: 0;
      border: 1px solid #343843;
      border-radius: 10px;
      padding: 10px;
      background: #181a20;
      color: inherit;
    }
    button {
      border: 1px solid #3a3f4c;
      border-radius: 10px;
      background: #20232b;
      color: inherit;
      padding: 10px 12px;
      font-weight: 650;
    }
    button:disabled {
      opacity: .45;
    }
    .summary {
      display: grid;
      grid-template-columns: repeat(4,minmax(0,1fr));
      gap: 8px;
      margin: 10px 0 14px;
    }
    .metric,.card {
      background: #181a20;
      border: 1px solid #2b2f39;
      border-radius: 14px;
    }
    .metric {
      padding: 10px;
      text-align: center;
    }
    .metric strong {
      display: block;
      font-size: 20px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit,minmax(240px,1fr));
      gap: 10px;
    }
    .card {
      overflow: hidden;
    }
    .card header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      padding: 10px 12px;
    }
    .device {
      font-weight: 750;
    }
    .status {
      font-size: 12px;
      font-weight: 800;
      padding: 4px 7px;
      border-radius: 999px;
      background: #30343e;
    }
    .status.SYNCED,.status.REFERENCE {
      background: #173d2a;
      color: #8df1b8;
    }
    .status.OUT_OF_SYNC {
      background: #4b3214;
      color: #ffd28a;
    }
    .status.OFFLINE {
      background: #3f2024;
      color: #ff9ca6;
    }
    .status.WAITING {
      background: #273044;
      color: #aac3ff;
    }
    .preview {
      width: 100%;
      display: block;
      background: #090a0c;
      min-height: 160px;
      object-fit: contain;
      touch-action: manipulation;
    }
    .placeholder {
      min-height: 160px;
      display: grid;
      place-items: center;
      color: #8f96a3;
      background: #090a0c;
    }
    .meta {
      padding: 9px 12px 12px;
      color: #aeb4c0;
      font-size: 12px;
      word-break: break-word;
    }
    .controls {
      position: sticky;
      bottom: 0;
      display: grid;
      grid-template-columns: repeat(5,minmax(0,1fr));
      gap: 7px;
      padding: 10px 0 calc(10px + env(safe-area-inset-bottom));
      background: linear-gradient(180deg,transparent,#101114 24%);
    }
    .error {
      color: #ff9ca6;
      min-height: 22px;
      margin: 8px 0;
      font-size: 13px;
    }
    .hint {
      color: #9ba3b0;
      font-size: 12px;
      margin: 8px 0;
    }
    .batch {
      margin-top: 14px;
      padding: 12px;
    }
    .batch-results {
      display: grid;
      gap: 6px;
      margin-top: 10px;
      font: 12px ui-monospace,SFMono-Regular,Consolas,monospace;
    }
    .batch-row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      border-top: 1px solid #2b2f39;
      padding-top: 6px;
    }
    .batch-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
    }
    .batch-targets {
      display: grid;
      grid-template-columns: repeat(auto-fit,minmax(150px,1fr));
      gap: 6px;
      margin: 10px 0;
    }
    .batch-target {
      display: flex;
      align-items: center;
      gap: 7px;
      padding: 7px;
      border: 1px solid #2b2f39;
      border-radius: 9px;
    }
    @media (max-width: 560px) {
      .summary {
        grid-template-columns: repeat(2,minmax(0,1fr));
      }
      .controls {
        grid-template-columns: repeat(3,minmax(0,1fr));
      }
    }
  </style>
</head>
<body>
  <main class="app">
    <div class="top">
      <h1>AOT GROUP CONTROL</h1>
      <button id="refresh">Làm mới</button>
    </div>
    <div class="session">
      <input id="session" value="" placeholder="Session ID" autocomplete="off" spellcheck="false">
      <button id="apply">Mở</button>
    </div>
    <div class="hint">
      Chạm trực tiếp lên ảnh REFERENCE để gửi semantic tap. Nếu điểm chạm không ánh xạ được node an toàn, hệ thống sẽ từ chối.
    </div>
    <div id="error" class="error"></div>
    <section class="summary">
      <div class="metric"><strong id="online">0</strong>ONLINE</div>
      <div class="metric"><strong id="synced">0</strong>SYNCED</div>
      <div class="metric"><strong id="out">0</strong>OUT OF SYNC</div>
      <div class="metric"><strong id="offline">0</strong>OFFLINE</div>
    </section>
    <section id="reference"></section>
    <h2 style="font-size:16px;margin:16px 0 8px">FOLLOWERS</h2>
    <section id="followers" class="grid"></section>
    <div id="lastControl" class="hint"></div>
    <section class="card batch">
      <div class="batch-actions">
        <button id="selectOnline">Chọn máy online</button>
        <button id="clearSelection">Bỏ chọn hết</button>
        <button id="openSwift">Mở Swift Backup</button>
      </div>
      <div class="hint">Chỉ gửi OPEN_SWIFT_BACKUP tới các máy đang kết nối.</div>
      <div id="batchTargets" class="batch-targets"></div>
      <div id="batchResults" class="batch-results"></div>
      <div class="batch-actions" style="margin-top:14px">
        <button id="updateCanary">Cập nhật 2 máy thử</button>
        <button id="updateStable">Phát hành cho tất cả</button>
      </div>
      <div id="updateError" class="error"></div>
      <div id="updateHint" class="hint">Hai máy thử được chọn động từ các máy ONLINE. Stable theo nhóm tối đa 5 máy.</div>
      <div id="updateResults" class="batch-results"></div>
    </section>
    <nav class="controls">
      <button id="pause">PAUSE</button>
      <button id="resume">RESUME</button>
      <button id="back">BACK</button>
      <button id="up">SWIPE ↑</button>
      <button id="down">SWIPE ↓</button>
    </nav>
  </main>
  <script>
  (function () {
    "use strict";
    var tg = window.Telegram && window.Telegram.WebApp;
    var auth = tg ? String(tg.initData || "") : "";
    var currentState = null;
    var selectedDeviceIds = new Set();
    var dashboardSocket = null;
    var reconnectTimer = null;
    var reconnectAttempt = 0;
    var socketGeneration = 0;
    var sessionInput = document.getElementById("session");
    var sessionStorageKey = "aot-hub-session-id";
    var errorEl = document.getElementById("error");
    var referenceEl = document.getElementById("reference");
    var followersEl = document.getElementById("followers");
    var lastControlEl = document.getElementById("lastControl");
    var batchTargetsEl = document.getElementById("batchTargets");
    var batchResultsEl = document.getElementById("batchResults");
    var updateResultsEl = document.getElementById("updateResults");
    var updateErrorEl = document.getElementById("updateError");
    var updateHintEl = document.getElementById("updateHint");

    if (tg) {
      tg.ready();
      tg.expand();
      try { tg.disableVerticalSwipes(); } catch (e) {}
    }

    function esc(value) {
      return String(value == null ? "" : value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function sessionId() {
      return String(sessionInput.value || "").trim();
    }

    function isValidSessionId(value) {
      return /^[A-Za-z0-9_-]{1,64}$/.test(
        String(value || "").trim()
      );
    }

    function restoreSession() {
      try {
        var stored = window.localStorage.getItem(
          sessionStorageKey
        );
        if (isValidSessionId(stored)) {
          sessionInput.value = String(stored).trim();
        }
      } catch (error) {}
    }

    function applySession() {
      var session = sessionId();
      if (isValidSessionId(session)) {
        try {
          window.localStorage.setItem(
            sessionStorageKey,
            session
          );
        } catch (error) {}
      }
      loadState();
      connectDashboard();
    }

    async function api(path, options) {
      if (!auth) {
        throw new Error("Hãy mở AOT HUB từ nút trong Telegram.");
      }
      var init = options || {};
      init.headers = Object.assign(
        {},
        init.headers || {},
        {
          "X-Telegram-Init-Data": auth,
          "Accept": "application/json"
        }
      );
      if (init.body && typeof init.body !== "string") {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(init.body);
      }
      var response = await fetch(path, init);
      var data = await response.json().catch(function () {
        return { ok: false, error: "invalid_response" };
      });
      if (!response.ok || data.ok !== true) {
        throw new Error(String(data.message || data.error || ("HTTP " + response.status)));
      }
      return data;
    }

    function preview(device, isReference) {
      if (!device || !device.preview_b64) {
        return '<div class="placeholder">Chưa có preview</div>';
      }
      var id = isReference ? ' id="referencePreview"' : "";
      return '<img' + id + ' class="preview" alt="preview" src="data:image/png;base64,' +
        device.preview_b64 + '">';
    }

    function card(device, isReference) {
      if (!device) {
        return '<div class="card"><div class="placeholder">REFERENCE chưa kết nối</div></div>';
      }
      var state = esc(device.status || "WAITING");
      var role = isReference ? "REFERENCE" : "FOLLOWER";
      return '<article class="card">' +
        '<header><div><div class="device">' + esc(device.device_id) +
        '</div><div class="hint" style="margin:2px 0 0">' + role +
        '</div></div><span class="status ' + state + '">' + state +
        '</span></header>' +
        preview(device, isReference) +
        '<div class="meta">' +
        esc(device.package || "-") + '<br>' +
        'FP ' + esc(device.fingerprint || "-") +
        '</div></article>';
    }

    function renderBatch(batch) {
      if (!batch || !Array.isArray(batch.devices)) {
        batchResultsEl.innerHTML = '<div class="hint">Chưa có batch.</div>';
        return;
      }
      batchResultsEl.innerHTML = batch.devices.map(function (item) {
        var reasons = {
          swift_backup_not_installed: "Swift Backup chưa được cài",
          swift_backup_launcher_unavailable: "Không tìm thấy màn hình mở ứng dụng",
          swift_backup_launch_failed: "Android từ chối lệnh mở ứng dụng",
          swift_backup_not_foreground: "Android không đưa Swift Backup lên trước",
          websocket_send_failed: "Không gửi được lệnh tới máy",
          worker_ack_timeout: "Worker không phản hồi kịp",
          device_offline: "Máy đã offline"
        };
        var rawReason = String(item.reason || "");
        var visibleReason = rawReason.indexOf("protocol_rejected:") === 0
          ? ("Protocol bị từ chối — " + rawReason.slice("protocol_rejected:".length))
          : (reasons[rawReason] || rawReason);
        var detail = rawReason ? " — " + visibleReason : "";
        return '<div class="batch-row"><strong>' + esc(item.device_id) +
          '</strong><span>' + esc(item.display_status || item.status) +
          esc(detail) + '</span></div>';
      }).join("");
    }

    function renderUpdate(update) {
      if (!update || !Array.isArray(update.devices)) {
        updateResultsEl.innerHTML = '<div class="hint">Chưa có lần cập nhật worker.</div>';
        return;
      }
      var selected = Array.isArray(update.selected_device_ids)
        ? update.selected_device_ids.map(String)
        : [];
      updateHintEl.textContent = selected.length === 2
        ? ("Hai máy thử: " + selected.join(" + ") + ". Stable theo nhóm tối đa 5 máy.")
        : "Hai máy thử được chọn động từ các máy ONLINE. Stable theo nhóm tối đa 5 máy.";
      updateResultsEl.innerHTML = update.devices.map(function (item) {
        var reasons = {
          worker_ack_timeout: "Worker không gửi trạng thái cập nhật kịp thời",
          websocket_send_failed: "WebSocket ngắt trước khi gửi lệnh",
          rollout_stopped_after_failure: "Đã dừng vì một máy trong nhóm trước FAILED",
          worker_version_mismatch: "Worker HEALTHY nhưng sai phiên bản phát hành",
          worker_reported_failure: "Worker báo cập nhật thất bại",
          health_ack_timeout: "Worker mới không gửi health ACK trong 60 giây",
          health_ack_timeout_rolled_back: "Không có health ACK; đã rollback"
        };
        var rawReason = String(item.reason || "");
        var visibleReason = rawReason.indexOf("protocol_rejected:") === 0
          ? ("Protocol bị từ chối — " + rawReason.slice("protocol_rejected:".length))
          : (reasons[rawReason] || rawReason);
        var detail = rawReason ? " — " + visibleReason : "";
        return '<div class="batch-row"><strong>' + esc(item.device_id) +
          '</strong><span>' + esc(item.display_status || item.status) +
          esc(detail) + '</span></div>';
      }).join("");
    }

    function sessionDevices(data) {
      var devices = [];
      if (data && data.reference) devices.push(data.reference);
      return devices.concat((data && data.followers) || []);
    }

    function renderBatchTargets(data) {
      var devices = sessionDevices(data);
      var members = new Set(devices.map(function (item) {
        return String(item.device_id);
      }));
      Array.from(selectedDeviceIds).forEach(function (deviceId) {
        var device = devices.find(function (item) {
          return String(item.device_id) === deviceId;
        });
        if (!members.has(deviceId) || !device || device.online !== true) {
          selectedDeviceIds.delete(deviceId);
        }
      });
      batchTargetsEl.innerHTML = devices.map(function (item) {
        var deviceId = String(item.device_id || "");
        var online = item.online === true;
        return '<label class="batch-target"><input type="checkbox" data-device-id="' +
          esc(deviceId) + '"' +
          (selectedDeviceIds.has(deviceId) ? ' checked' : '') +
          (online ? '' : ' disabled') + '><span>' + esc(deviceId) +
          ' — ' + (online ? 'ONLINE' : 'OFFLINE') + '</span></label>';
      }).join("");
      Array.from(batchTargetsEl.querySelectorAll("input[data-device-id]")).forEach(
        function (input) {
          input.onchange = function () {
            var deviceId = String(input.getAttribute("data-device-id") || "");
            if (input.checked && !input.disabled) selectedDeviceIds.add(deviceId);
            else selectedDeviceIds.delete(deviceId);
          };
        }
      );
    }

    function render(data) {
      currentState = data;
      var summary = data.summary || {};
      document.getElementById("online").textContent = summary.online || 0;
      document.getElementById("synced").textContent = summary.synced || 0;
      document.getElementById("out").textContent = summary.out_of_sync || 0;
      document.getElementById("offline").textContent = summary.offline || 0;
      referenceEl.innerHTML = card(data.reference, true);
      followersEl.innerHTML = (data.followers || [])
        .map(function (item) { return card(item, false); })
        .join("");
      document.getElementById("pause").disabled = !!data.paused;
      document.getElementById("resume").disabled = !data.paused;
      var last = data.last_control;
      lastControlEl.textContent = last
        ? ("Control: " + String(last.status || "-") +
           (last.reason ? " — " + String(last.reason) : ""))
        : "";
      renderBatchTargets(data);
      renderBatch(data.last_batch);
      renderUpdate(data.last_update);
      var image = document.getElementById("referencePreview");
      if (image) {
        image.addEventListener("click", function (event) {
          if (!currentState || currentState.paused) {
            return;
          }
          var rect = image.getBoundingClientRect();
          if (!rect.width || !rect.height) {
            return;
          }
          var x = (event.clientX - rect.left) / rect.width;
          var y = (event.clientY - rect.top) / rect.height;
          sendControl("tap", {
            x_norm: Math.max(0, Math.min(1, x)),
            y_norm: Math.max(0, Math.min(1, y))
          });
        });
      }
    }

    async function loadState() {
      var session = sessionId();
      if (!session) {
        return;
      }
      try {
        var data = await api(
          "/aot/hub/api/state?session_id=" +
          encodeURIComponent(session)
        );
        errorEl.textContent = "";
        render(data);
      } catch (error) {
        errorEl.textContent = String(error.message || error);
      }
    }

    async function sendControl(kind, extra) {
      try {
        errorEl.textContent = "";
        await api("/aot/hub/api/control", {
          method: "POST",
          body: Object.assign(
            {
              session_id: sessionId(),
              kind: kind
            },
            extra || {}
          )
        });
        window.setTimeout(loadState, 250);
      } catch (error) {
        errorEl.textContent = String(error.message || error);
      }
    }

    async function openSwiftBackupBatch() {
      var button = document.getElementById("openSwift");
      button.disabled = true;
      try {
        errorEl.textContent = "";
        var onlineSelected = sessionDevices(currentState).filter(function (item) {
          return item.online === true && selectedDeviceIds.has(String(item.device_id));
        }).map(function (item) { return String(item.device_id); });
        if (!onlineSelected.length) {
          throw new Error("Hãy chọn ít nhất một máy ONLINE.");
        }
        var data = await api("/aot/hub/api/control", {
          method: "POST",
          body: {
            session_id: sessionId(),
            kind: "open_swift_backup",
            target_device_ids: onlineSelected
          }
        });
        renderBatch(data.batch);
      } catch (error) {
        errorEl.textContent = String(error.message || error);
      } finally {
        button.disabled = false;
      }
    }

    async function updateWorkers(kind, buttonId) {
      var button = document.getElementById(buttonId);
      button.disabled = true;
      try {
        updateErrorEl.textContent = "";
        var data = await api("/aot/hub/api/control", {
          method: "POST",
          body: { session_id: sessionId(), kind: kind }
        });
        renderUpdate(data.update);
      } catch (error) {
        updateErrorEl.textContent = String(error.message || error);
      } finally {
        button.disabled = false;
      }
    }

    function scheduleDashboardReconnect(generation) {
      if (generation !== socketGeneration || !auth) return;
      var base = Math.min(30000, 1000 * Math.pow(2, reconnectAttempt));
      var delay = Math.round(base * (0.75 + Math.random() * 0.5));
      reconnectAttempt = Math.min(reconnectAttempt + 1, 5);
      reconnectTimer = window.setTimeout(function () {
        if (generation === socketGeneration) connectDashboard(generation);
      }, delay);
    }

    function connectDashboard(existingGeneration) {
      if (!auth || !isValidSessionId(sessionId()) || !window.WebSocket) return;
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      var generation = existingGeneration || (socketGeneration + 1);
      socketGeneration = generation;
      if (dashboardSocket) {
        dashboardSocket.onclose = null;
        dashboardSocket.close();
      }
      var scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
      var socket = new window.WebSocket(
        scheme + "//" + window.location.host + "/aot/hub/api/ws?session_id=" +
        encodeURIComponent(sessionId()) + "&init_data=" + encodeURIComponent(auth)
      );
      dashboardSocket = socket;
      socket.onopen = function () { reconnectAttempt = 0; };
      socket.onmessage = function (event) {
        try {
          var data = JSON.parse(String(event.data || ""));
          if (
            data.type === "aot_hub_state" &&
            data.session_id === sessionId()
          ) {
            errorEl.textContent = "";
            render(data);
          }
        } catch (error) {}
      };
      socket.onclose = function () {
        if (dashboardSocket === socket) dashboardSocket = null;
        scheduleDashboardReconnect(generation);
      };
      socket.onerror = function () { socket.close(); };
    }

    restoreSession();
    document.getElementById("refresh").onclick = loadState;
    document.getElementById("apply").onclick = applySession;
    document.getElementById("pause").onclick = function () {
      sendControl("pause");
    };
    document.getElementById("resume").onclick = function () {
      sendControl("resume");
    };
    document.getElementById("back").onclick = function () {
      sendControl("back");
    };
    document.getElementById("selectOnline").onclick = function () {
      sessionDevices(currentState).forEach(function (item) {
        if (item.online === true) selectedDeviceIds.add(String(item.device_id));
      });
      renderBatchTargets(currentState);
    };
    document.getElementById("clearSelection").onclick = function () {
      selectedDeviceIds.clear();
      renderBatchTargets(currentState);
    };
    document.getElementById("openSwift").onclick = openSwiftBackupBatch;
    document.getElementById("updateCanary").onclick = function () {
      updateWorkers("update_canary", "updateCanary");
    };
    document.getElementById("updateStable").onclick = function () {
      updateWorkers("update_stable", "updateStable");
    };
    document.getElementById("up").onclick = function () {
      sendControl("swipe", {
        x1: 0.5, y1: 0.75, x2: 0.5, y2: 0.28, duration_ms: 300
      });
    };
    document.getElementById("down").onclick = function () {
      sendControl("swipe", {
        x1: 0.5, y1: 0.28, x2: 0.5, y2: 0.75, duration_ms: 300
      });
    };

    loadState();
    connectDashboard();
  }());
  </script>
</body>
</html>`;
}

function fleetHubHtml() {
  return `<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AOT Hub</title><script src="https://telegram.org/js/telegram-web-app.js?63"></script><style>body{font:14px system-ui;background:#101114;color:#f5f7fb;margin:0}.app{max-width:900px;margin:auto;padding:16px}button{padding:10px;margin:4px;border:1px solid #555;border-radius:9px;background:#20232b;color:inherit}.device{display:flex;gap:10px;padding:10px;border-bottom:1px solid #333}.OFFLINE,.error{color:#ff9ca6}.ONLINE{color:#8df1b8}.error{min-height:22px}.result{font-family:monospace;padding:4px}</style></head><body><main class="app"><h1>AOT HUB</h1><button id="refresh">Làm mới</button><div><button id="selectOnline">Chọn máy online</button><button id="clear">Bỏ chọn hết</button><button id="backup">Mở Swift Backup</button><button id="apps">Mở Apps</button><button id="backupRestoreData">Backup RESTORE_DATA</button></div><div id="error" class="error"></div><section id="devices"></section><section id="results"></section><hr><button id="canary">Cập nhật 2 máy thử</button><button id="stable">Phát hành cho tất cả</button><div id="updateError" class="error"></div></main><script>(()=>{'use strict';const tg=window.Telegram&&window.Telegram.WebApp,auth=tg?String(tg.initData||''):'',selected=new Set();let state=null,ws=null,retry=0,timer=null;const el=id=>document.getElementById(id);if(tg){tg.ready();tg.expand()}const api=async(path,init={})=>{init.headers=Object.assign({},init.headers||{}, {'X-Telegram-Init-Data':auth,'Accept':'application/json'});if(init.body&&typeof init.body!=='string'){init.headers['Content-Type']='application/json';init.body=JSON.stringify(init.body)}const r=await fetch(path,init),d=await r.json();if(!r.ok||d.ok!==true)throw Error(d.message||d.error||('HTTP '+r.status));return d},devices=()=>state&&Array.isArray(state.devices)?state.devices:[],render=()=>{el('devices').innerHTML=devices().map(d=>'<label class="device"><input type="checkbox" data-id="'+d.device_id+'" '+(selected.has(d.device_id)?'checked':'')+' '+(d.online?'':'disabled')+'><b>'+d.device_id+'</b><span class="'+(d.online?'ONLINE':'OFFLINE')+'">'+(d.online?'ONLINE':'OFFLINE')+'</span></label>').join('');el('devices').querySelectorAll('input').forEach(n=>n.onchange=()=>n.checked?selected.add(n.dataset.id):selected.delete(n.dataset.id));const b=state&&state.last_batch;el('results').innerHTML=b&&b.devices?b.devices.map(d=>'<div class="result">'+d.device_id+': '+d.history.join(' → ')+(d.reason?' — '+d.reason:'')+(Number.isFinite(d.app_count)?' [Apps: '+d.app_count+']':'')+(Number.isFinite(d.selected_count)?' [Selected: '+d.selected_count+']':'')+'</div>').join(''):''},load=async()=>{try{state=(await api('/aot/hub/api/state')).state;render();el('error').textContent=''}catch(e){el('error').textContent=e.message}},control=async(kind,ids)=>api('/aot/hub/api/control',{method:'POST',body:{kind,target_device_ids:ids}}),run=async kind=>{try{const ids=[...selected].filter(id=>devices().some(d=>d.device_id===id&&d.online));if(!ids.length)throw Error('Hãy chọn ít nhất một máy ONLINE.');const d=await control(kind,ids);state.last_batch=d.batch;render();el('error').textContent=''}catch(e){el('error').textContent=e.message}},connect=()=>{if(!auth||!window.WebSocket)return;clearTimeout(timer);ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/aot/hub/api/ws?init_data='+encodeURIComponent(auth));ws.onopen=()=>{retry=0};ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.type==='aot_hub_state'){state=d;render()}};ws.onclose=()=>{const delay=Math.min(30000,1000*Math.pow(2,retry++))*(.75+Math.random()*.5);timer=setTimeout(connect,delay)}};el('refresh').onclick=load;el('selectOnline').onclick=()=>{devices().filter(d=>d.online).forEach(d=>selected.add(d.device_id));render()};el('clear').onclick=()=>{selected.clear();render()};el('backup').onclick=()=>run('open_swift_backup');el('apps').onclick=()=>run('open_swift_apps');el('backupRestoreData').onclick=()=>run('backup_restore_data');el('canary').onclick=()=>control('update_canary').catch(e=>el('updateError').textContent=e.message);el('stable').onclick=()=>control('update_stable').catch(e=>el('updateError').textContent=e.message);load();connect()})();</script></body></html>`;
}

async function handleAotHubPage() {
  return new Response(
    fleetHubHtml(),
    {
      status: 200,
      headers: {
        "Content-Type":
          "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "Content-Security-Policy":
          "default-src 'self'; " +
          "script-src 'self' https://telegram.org 'unsafe-inline'; " +
          "img-src 'self' data:; " +
          "style-src 'self' 'unsafe-inline'; " +
          "connect-src 'self'; " +
          "frame-ancestors https://web.telegram.org https://*.telegram.org",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
      },
    }
  );
}

async function handleAotHubState(
  request,
  env,
  url
) {
  const auth = await requireAotHubAdmin(
    request,
    env
  );
  if (!auth.ok) {
    return auth.response;
  }
  const result = await fleetStateCall(
    env,
    "/aot/hub/state"
  );
  return noStoreJson(
    result.data,
    result.response.status
  );
}

async function handleAotHubDashboardWebSocket(request, env, url) {
  const user = await validateAotTelegramInitData(
    url.searchParams.get("init_data"),
    env
  );
  if (!user) {
    return noStoreJson({ ok: false, error: "hub_unauthorized" }, 401);
  }
  if (
    String(request.headers.get("Upgrade") || "").toLowerCase() !==
    "websocket"
  ) {
    return noStoreJson({ ok: false, error: "upgrade_required" }, 426);
  }
  return fleetStateStub(env).fetch(
    new Request(
      "https://fleet-state.internal/aot/hub/dashboard-ws",
      { method: "GET", headers: { Upgrade: "websocket" } }
    )
  );
}

function normalizeAotHubControl(body) {
  if (
    !body ||
    typeof body !== "object"
  ) {
    return null;
  }
  const kind = String(
    body.kind || ""
  ).trim();
  if (
    [
      "open_swift_backup",
      "open_swift_apps",
      "backup_restore_data",
      "update_canary",
      "update_stable",
    ].includes(kind)
  ) {
    const control = {
      kind,
    };
    if (["open_swift_backup", "open_swift_apps", "backup_restore_data"].includes(kind)) {
      if (
        !Array.isArray(body.target_device_ids) ||
        body.target_device_ids.length < 1 ||
        body.target_device_ids.length > AOT_MAX_TARGETS
      ) {
        return null;
      }
      control.target_device_ids = body.target_device_ids.map(String);
    } else if (["update_canary", "update_stable"].includes(kind)) {
      if (Array.isArray(body.target_device_ids)) {
        if (body.target_device_ids.length > AOT_MAX_TARGETS) {
          return null;
        }
        if (body.target_device_ids.length > 0) {
          control.target_device_ids = body.target_device_ids.map(String);
        }
      }
    }
    return control;
  }
  return null;
}

async function handleAotHubControl(
  request,
  env
) {
  const auth = await requireAotHubAdmin(
    request,
    env
  );
  if (!auth.ok) {
    return auth.response;
  }
  const parsed = await readAotJson(request);
  if (parsed.error) {
    return noStoreJson(
      {
        ok: false,
        error: parsed.error,
      },
      400
    );
  }
  const control = normalizeAotHubControl(
    parsed.value
  );
  if (!control) {
    return noStoreJson(
      {
        ok: false,
        error: "invalid_hub_control",
      },
      400
    );
  }
  const result = await fleetStateCall(
    env,
    "/aot/hub/control",
    {
      method: "POST",
      body: {
        protocol:
          AOT_HUB_PROTOCOL_VERSION,
        ...control,
      },
    }
  );
  return noStoreJson(
    result.data,
    result.response.status
  );
}




async function deviceIdsForTarget(target, env) {
  const wantedGroup =
    target === "all"
      ? null
      : normalizeDeviceGroup(target);
  if (target !== "all" && !wantedGroup) {
    throw new Error(
      `Invalid device target: ${target}`
    );
  }
  const ids = new Set();
  try {
    const durableRecords =
      await listFleetDeviceRecords(
        env
      );
    for (
      const record
      of durableRecords
    ) {
      const deviceId =
        normalizeDeviceId(
          record?.device_id
        );
      const deviceGroup =
        normalizeDeviceGroup(
          record?.device_group
        );
      if (
        !deviceId ||
        !deviceGroup ||
        (
          wantedGroup &&
          deviceGroup !== wantedGroup
        )
      ) {
        continue;
      }
      if (
        await isDeviceRevoked(
          deviceId,
          env
        )
      ) {
        continue;
      }
      ids.add(deviceId);
    }
  } catch (error) {
    console.error(
      "fleet_state_list_failed",
      error?.message || error
    );
  }
  let cursor;
  do {
    const page =
      await env.DEVICE_STATUS.list({
        limit: 1000,
        ...(cursor
          ? { cursor }
          : {}),
      });
    for (
      const item
      of page.keys || []
    ) {
      if (
        isDeviceStatusMetadataKey(
          item.name
        )
      ) {
        continue;
      }
      const record =
        await getDeviceRecord(
          item.name,
          env
        );
      if (!record) continue;
      const deviceId =
        normalizeDeviceId(
          record.device_id ||
          item.name
        );
      const deviceGroup =
        normalizeDeviceGroup(
          record.device_group
        );
      if (
        !deviceId ||
        !deviceGroup ||
        (
          wantedGroup &&
          deviceGroup !== wantedGroup
        )
      ) {
        continue;
      }
      ids.add(deviceId);
    }
    cursor =
      page.list_complete
        ? undefined
        : page.cursor;
  } while (cursor);
  return [...ids].sort(
    compareDeviceIds
  );
}



function maintenanceKey(deviceId) {
  return `${MAINTENANCE_PREFIX}${deviceId}`;
}

async function isDeviceMaintenance(deviceId, env) {
  return (
    await env.DEVICE_STATUS.get(
      maintenanceKey(deviceId)
    )
  ) === "1";
}

async function setDeviceMaintenance(
  deviceId,
  enabled,
  env
) {
  if (enabled) {
    await env.DEVICE_STATUS.put(
      maintenanceKey(deviceId),
      "1"
    );
  } else {
    await env.DEVICE_STATUS.delete(
      maintenanceKey(deviceId)
    );
  }
}

function revokedKey(deviceId) {
  return `${REVOKED_PREFIX}${deviceId}`;
}

async function isDeviceRevoked(
  deviceId,
  env
) {
  return (
    await env.DEVICE_STATUS.get(
      revokedKey(deviceId)
    )
  ) !== null;
}

async function deleteKvPrefix(
  prefix,
  env
) {
  let cursor;

  do {
    const page =
      await env.DEVICE_STATUS.list({
        prefix,
        limit: 1000,
        ...(cursor ? { cursor } : {}),
      });

    await Promise.all(
      (page.keys || []).map(
        (item) =>
          env.DEVICE_STATUS.delete(
            item.name
          )
      )
    );

    cursor =
      page.list_complete
        ? undefined
        : page.cursor;
  } while (cursor);
}

function pendingRevocationCacheKey(
  token
) {
  return (
    "https://aotscript.local/" +
    `pending-revocation/${token}`
  );
}

async function savePendingRevocation(
  token,
  deviceId
) {
  await caches.default.put(
    new Request(
      pendingRevocationCacheKey(
        token
      )
    ),
    new Response(
      JSON.stringify({
        device_id: deviceId,
        created: Date.now(),
      }),
      {
        headers: {
          "Content-Type":
            "application/json",
        },
      }
    )
  );
}

async function loadPendingRevocation(
  token
) {
  const response =
    await caches.default.match(
      new Request(
        pendingRevocationCacheKey(
          token
        )
      )
    );

  if (!response) return null;

  try {
    const value = JSON.parse(
      await response.text()
    );

    if (
      !value ||
      typeof value.created !==
        "number" ||
      Date.now() - value.created >
        COMMAND_TTL_MS
    ) {
      await clearPendingRevocation(
        token
      );
      return null;
    }

    const deviceId =
      normalizeDeviceId(
        value.device_id
      );

    if (!deviceId) {
      await clearPendingRevocation(
        token
      );
      return null;
    }

    return {
      device_id: deviceId,
      created: value.created,
    };
  } catch (error) {
    await clearPendingRevocation(
      token
    );
    return null;
  }
}

async function clearPendingRevocation(
  token
) {
  await caches.default.delete(
    new Request(
      pendingRevocationCacheKey(
        token
      )
    )
  );
}

async function requestPermanentDelete(
  chatId,
  rawDeviceId,
  env
) {
  const deviceId =
    normalizeDeviceId(
      rawDeviceId
    );

  if (!deviceId) {
    await sendMessage(
      chatId,
      env,
      "Device ID không hợp lệ."
    );
    return;
  }

  if (
    await isDeviceRevoked(
      deviceId,
      env
    )
  ) {
    await sendMessage(
      chatId,
      env,
      `🛑 ${deviceId} đã bị thu hồi vĩnh viễn.\n` +
        `Dùng /restore ${deviceId} nếu thật sự muốn cho phép ID này hoạt động lại.`
    );
    return;
  }

  const record =
    await getDeviceRecord(
      deviceId,
      env
    );

  const alias =
    await getDeviceAlias(
      deviceId,
      env
    );

  const name =
    alias ||
    `Máy ${deviceId}`;

  const group =
    normalizeDeviceGroup(
      record?.device_group
    );

  const token =
    crypto.randomUUID()
      .replace(/-/g, "")
      .slice(0, 12);

  await savePendingRevocation(
    token,
    deviceId
  );

  const details = record
    ? (
        `Tên: ${name}\n` +
        `Nhóm: ${group || "-"}\n` +
        `Heartbeat cuối: ${formatRelativeDeviceTime(record.last_seen)}`
      )
    : (
        "Hiện không có bản ghi hoạt động, " +
        "nhưng ID vẫn sẽ được đưa vào danh sách chặn."
      );

  await sendMessage(
    chatId,
    env,
    `⚠️ XÓA VĨNH VIỄN\n\n` +
      `ID: ${deviceId}\n` +
      `${details}\n\n` +
      "Sau khi xác nhận:\n" +
      "• Xóa thiết bị khỏi danh sách\n" +
      "• Chặn mọi heartbeat/report\n" +
      "• Chặn ghép nối lại cùng ID\n\n" +
      "Có thể mở lại sau bằng /restore.",
    {
      inline_keyboard: [
        [
          {
            text:
              "🗑 XÁC NHẬN XÓA",
            callback_data:
              `revoke_ok:${token}`,
          },
          {
            text: "❌ HỦY",
            callback_data:
              `revoke_cancel:${token}`,
          },
        ],
      ],
    }
  );
}

async function permanentlyRevokeDevice(
  chatId,
  rawDeviceId,
  env
) {
  const deviceId =
    normalizeDeviceId(
      rawDeviceId
    );

  if (!deviceId) {
    await sendMessage(
      chatId,
      env,
      "Device ID không hợp lệ."
    );
    return;
  }

  const existingRevocation =
    await env.DEVICE_STATUS.get(
      revokedKey(deviceId)
    );

  if (existingRevocation) {
    await sendMessage(
      chatId,
      env,
      `🛑 ${deviceId} đã bị thu hồi trước đó.`
    );
    return;
  }

  const record =
    await getDeviceRecord(
      deviceId,
      env
    );

  const alias =
    await getDeviceAlias(
      deviceId,
      env
    );

  const pairId =
    await env.DEVICE_STATUS.get(
      pairDeviceKey(deviceId)
    );

  const tombstone = {
    device_id: deviceId,
    revoked_at: Date.now(),
    last_device_group:
      normalizeDeviceGroup(
        record?.device_group
      ),
    last_seen:
      Number(record?.last_seen) ||
      null,
    last_alias:
      alias || null,
  };

  await env.DEVICE_STATUS.put(
    revokedKey(deviceId),
    JSON.stringify(tombstone)
  );

  const deletes = [
    env.DEVICE_STATUS.delete(
      deviceId
    ),
    env.DEVICE_STATUS.delete(
      `${DEVICE_ALIAS_PREFIX}${deviceId}`
    ),
    env.DEVICE_STATUS.delete(
      maintenanceKey(deviceId)
    ),
    env.DEVICE_STATUS.delete(
      healthStateKey(deviceId)
    ),
    env.DEVICE_STATUS.delete(
      pairDeviceKey(deviceId)
    ),
  ];

  if (pairId) {
    deletes.push(
      env.DEVICE_STATUS.delete(
        pairRecordKey(pairId)
      )
    );
  }

  await Promise.all(deletes);

  await setFleetDeviceRevocation(
    env,
    deviceId,
    true
  );

  await deleteKvPrefix(
    `${PAIR_RATE_PREFIX}${deviceId}:`,
    env
  );

  await env.DEVICE_STATUS.delete(
    deviceId
  );

  await sendMessage(
    chatId,
    env,
    `🗑 Đã xóa vĩnh viễn ${deviceId}.\n` +
      "Heartbeat/report và ghép nối cùng ID đã bị chặn.\n" +
      `Khôi phục khi cần: /restore ${deviceId}`
  );
}

async function restoreDevice(
  chatId,
  rawDeviceId,
  env
) {
  const deviceId =
    normalizeDeviceId(
      rawDeviceId
    );

  if (!deviceId) {
    await sendMessage(
      chatId,
      env,
      "Device ID không hợp lệ."
    );
    return;
  }

  if (
    !(await isDeviceRevoked(
      deviceId,
      env
    ))
  ) {
    await sendMessage(
      chatId,
      env,
      `${deviceId} hiện không nằm trong danh sách thu hồi.`
    );
    return;
  }

  const pairId =
    await env.DEVICE_STATUS.get(
      pairDeviceKey(deviceId)
    );

  const deletes = [
    env.DEVICE_STATUS.delete(
      deviceId
    ),
    env.DEVICE_STATUS.delete(
      `${DEVICE_ALIAS_PREFIX}${deviceId}`
    ),
    env.DEVICE_STATUS.delete(
      maintenanceKey(deviceId)
    ),
    env.DEVICE_STATUS.delete(
      healthStateKey(deviceId)
    ),
    env.DEVICE_STATUS.delete(
      pairDeviceKey(deviceId)
    ),
  ];

  if (pairId) {
    deletes.push(
      env.DEVICE_STATUS.delete(
        pairRecordKey(pairId)
      )
    );
  }

  await Promise.all(deletes);

  await setFleetDeviceRevocation(
    env,
    deviceId,
    false
  );

  await deleteKvPrefix(
    `${PAIR_RATE_PREFIX}${deviceId}:`,
    env
  );

  await env.DEVICE_STATUS.delete(
    revokedKey(deviceId)
  );

  await sendMessage(
    chatId,
    env,
    `✅ Đã cho phép lại ID ${deviceId}.\n` +
      "ID này chưa tự xuất hiện cho tới khi một Agent gửi heartbeat hoặc ghép nối lại.\n" +
      "Nếu Agent cũ vẫn còn chạy, nó cũng có thể xuất hiện lại."
  );
}

function normalizeDeviceIdList(values) {
  const rawValues = Array.isArray(values)
    ? values
    : String(values || "").split(",");

  const result = [];
  const seen = new Set();

  for (const rawValue of rawValues) {
    const deviceId = normalizeDeviceId(
      rawValue
    );

    if (!deviceId) {
      throw new Error(
        `Device ID không hợp lệ: ${rawValue}`
      );
    }

    if (seen.has(deviceId)) {
      throw new Error(
        `Device ID bị lặp: ${deviceId}`
      );
    }

    seen.add(deviceId);
    result.push(deviceId);
  }

  return result.sort(compareDeviceIds);
}

function deviceSupportsSecureSolver(record) {
  return record?.secure_solver_capable === true;
}

async function filterSecureSolverDeviceIds(deviceIds, env) {
  const result = [];
  for (const deviceId of deviceIds) {
    const record = await getDeviceRecord(deviceId, env);
    if (deviceSupportsSecureSolver(record)) result.push(deviceId);
  }
  return result;
}

function deviceSupportsSelfHeal(record) {
  return record?.self_heal_capable === true;
}

async function filterSelfHealDeviceIds(
  deviceIds,
  env
) {
  const result = [];

  for (const deviceId of deviceIds) {
    const record =
      await getDeviceRecord(
        deviceId,
        env
      );

    if (
      deviceSupportsSelfHeal(
        record
      )
    ) {
      result.push(deviceId);
    }
  }

  return result;
}

function deviceSupportsDeferredCommandQueue(
  record
) {
  return (
    record?.deferred_command_queue_capable ===
    true
  );
}

async function deferredCommandQueueReadiness(
  deviceIds,
  env
) {
  const unsupported = [];

  for (const deviceId of deviceIds) {
    const record =
      await getDeviceRecord(
        deviceId,
        env
      );

    if (
      !deviceSupportsDeferredCommandQueue(
        record
      )
    ) {
      unsupported.push(deviceId);
    }
  }

  return {
    ready: unsupported.length === 0,
    unsupported,
  };
}



async function activeDeviceIdsForTarget(
  target,
  env
) {
  const ids = await deviceIdsForTarget(
    target,
    env
  );

  const result = [];

  for (const deviceId of ids) {
    if (
      !(await isDeviceMaintenance(
        deviceId,
        env
      ))
    ) {
      result.push(deviceId);
    }
  }

  return result;
}

async function validateDirectDeviceIds(
  values,
  env
) {
  const ids = normalizeDeviceIdList(
    values
  );

  for (const deviceId of ids) {
    const record = await getDeviceRecord(
      deviceId,
      env
    );

    if (!record) {
      throw new Error(
        `${deviceId} chưa từng kết nối`
      );
    }

    if (
      await isDeviceMaintenance(
        deviceId,
        env
      )
    ) {
      throw new Error(
        `${deviceId} đang ở chế độ bảo trì`
      );
    }

    if (
      record.agent_version !==
      TARGETING_AGENT_VERSION
    ) {
      throw new Error(
        `${deviceId} chưa cập nhật Agent hỗ trợ gửi riêng. ` +
        "Hãy chạy lại msetup trên máy và chờ heartbeat."
      );
    }
  }

  return ids;
}

async function revalidateGroupDeviceIds(
  values,
  env
) {
  const ids = normalizeDeviceIdList(
    values
  );

  const result = [];

  for (const deviceId of ids) {
    const record = await getDeviceRecord(
      deviceId,
      env
    );

    if (
      record &&
      !(await isDeviceMaintenance(
        deviceId,
        env
      ))
    ) {
      result.push(deviceId);
    }
  }

  return result;
}

async function deviceDisplayName(
  deviceId,
  env
) {
  const alias = await getDeviceAlias(
    deviceId,
    env
  );

  if (
    alias &&
    alias.trim().toLowerCase() !==
      deviceId.toLowerCase()
  ) {
    return alias.trim();
  }

  return `Máy ${deviceId}`;
}

function commandEnvelope(
  commandId,
  deviceIds,
  expiresAt,
  commandLines
) {
  return [
    `# telegram_target_ids=${deviceIds.join(",")}`,
    `# telegram_expires_at=${expiresAt}`,
    `# telegram_command_id=${commandId}`,
    ...commandLines,
    "",
  ].join("\n");
}

function splitCommandEnvelopeBlocks(content) {
  const lines =
    String(content || "")
      .replace(/\r\n?/g, "\n")
      .split("\n");

  const blocks = [];
  let current = [];

  const flush = () => {
    const block =
      current.join("\n").trim();

    if (
      block &&
      /^# telegram_target_ids=/m.test(
        block
      ) &&
      /^# telegram_command_id=/m.test(
        block
      )
    ) {
      blocks.push(block);
    }

    current = [];
  };

  for (const line of lines) {
    if (
      line.startsWith(
        "# telegram_target_ids="
      )
    ) {
      flush();
      current.push(line);
      continue;
    }

    if (current.length > 0) {
      current.push(line);
    }
  }

  flush();
  return blocks;
}

function commandEnvelopeExpiresAt(block) {
  const match =
    String(block || "").match(
      /^# telegram_expires_at=(\d+)$/m
    );

  if (!match) {
    return 0;
  }

  const value = Number(match[1]);

  return Number.isFinite(value)
    ? value
    : 0;
}

function commandEnvelopeId(block) {
  const match =
    String(block || "").match(
      /^# telegram_command_id=([\w-]+)$/m
    );

  return match
    ? match[1]
    : "";
}

function mergeCommandFileContent(
  currentContent,
  newEnvelope,
  now
) {
  const incoming =
    String(newEnvelope || "").trim();

  if (!incoming) {
    throw new Error(
      "Nội dung lệnh mới trống."
    );
  }

  const incomingId =
    commandEnvelopeId(incoming);

  if (!incomingId) {
    throw new Error(
      "Lệnh mới thiếu command ID."
    );
  }

  let blocks =
    splitCommandEnvelopeBlocks(
      currentContent
    ).filter(
      (block) =>
        commandEnvelopeExpiresAt(block)
          > now &&
        commandEnvelopeId(block)
          !== incomingId
    );

  blocks.push(incoming);

  blocks = blocks.slice(
    -MAX_PENDING_COMMAND_BLOCKS
  );

  const encoder = new TextEncoder();

  let merged =
    blocks.join("\n\n").trim()
    + "\n";

  while (
    blocks.length > 1 &&
    encoder.encode(merged).length
      > MAX_PENDING_COMMAND_BYTES
  ) {
    blocks.shift();

    merged =
      blocks.join("\n\n").trim()
      + "\n";
  }

  if (
    encoder.encode(merged).length
      > MAX_PENDING_COMMAND_BYTES
  ) {
    throw new Error(
      "Hàng đợi lệnh vượt giới hạn."
    );
  }

  return merged;
}


function pendingDispatchCacheKey(token) {
  return (
    `https://aotscript.local/` +
    `pending-dispatch/${token}`
  );
}

async function savePendingDispatch(
  token,
  spec
) {
  await caches.default.put(
    new Request(
      pendingDispatchCacheKey(token)
    ),
    new Response(
      JSON.stringify({
        ...spec,
        created: Date.now(),
      }),
      {
        headers: {
          "Content-Type":
            "application/json",
        },
      }
    )
  );
}

async function loadPendingDispatch(token) {
  const response = await caches.default.match(
    new Request(
      pendingDispatchCacheKey(token)
    )
  );

  if (!response) return null;

  try {
    const value = JSON.parse(
      await response.text()
    );

    if (
      !value ||
      typeof value.created !== "number" ||
      Date.now() - value.created >
        COMMAND_TTL_MS
    ) {
      await clearPendingDispatch(token);
      return null;
    }

    return value;
  } catch (error) {
    await clearPendingDispatch(token);
    return null;
  }
}

async function clearPendingDispatch(token) {
  await caches.default.delete(
    new Request(
      pendingDispatchCacheKey(token)
    )
  );
}

function parseCommandKeysText(value) {
  const keys = String(value || "")
    .toLowerCase()
    .split(/[\s,]+/)
    .filter(Boolean);

  if (keys.length === 0) {
    return null;
  }

  const unique = [...new Set(keys)];

  if (
    unique.some(
      (key) => !COMMANDS[key]
    )
  ) {
    return null;
  }

  if (
    unique.includes("idle") &&
    unique.length > 1
  ) {
    return null;
  }

  return unique;
}

function orderedCommandKeys(commandKeys) {
  const selected = [
    ...new Set(commandKeys || []),
  ];

  let ordered = COMMAND_ORDER.filter(
    (key) => selected.includes(key)
  );

  if (ordered.includes("reboot")) {
    ordered = ordered.filter(
      (key) => key !== "reboot"
    );
    ordered.push("reboot");
  }

  return ordered;
}

function buildCommandLinesFromKeys(commandKeys) {
  return orderedCommandKeys(commandKeys).map((key) => {
    if (key === "update_solver") return "UPDATE_SOLVER_SECURE";
    if (key === "update_script") throw new Error("Lệnh Script cần URL.");
    return COMMANDS[key].value;
  });
}

async function resolveCommandSpec(
  spec,
  env
) {
  const commandLines = (
    Array.isArray(spec.commandLines)
      ? spec.commandLines
      : []
  )
    .map((line) => String(line).trim())
    .filter(Boolean);

  if (
    commandLines.length === 0 ||
    commandLines.length > 20 ||
    commandLines.some(
      (line) =>
        line.includes("\n") ||
        line.includes("\r") ||
        line.length > 2000
    )
  ) {
    throw new Error(
      "Nội dung lệnh không hợp lệ."
    );
  }

  const commandKeys = [
    ...new Set(
      Array.isArray(spec.commandKeys)
        ? spec.commandKeys
        : []
    ),
  ];

  if (
    commandKeys.length === 0 ||
    commandKeys.some(
      (key) => !COMMANDS[key]
    )
  ) {
    throw new Error(
      "Loại lệnh không hợp lệ."
    );
  }

  if (
    Array.isArray(spec.deviceIds) &&
    spec.deviceIds.length > 0
  ) {
    let deviceIds =
      await validateDirectDeviceIds(
        spec.deviceIds,
        env
      );

    if (commandKeys.includes("update_solver")) {
      deviceIds = await filterSecureSolverDeviceIds(deviceIds, env);
      if (deviceIds.length === 0) {
        throw new Error("Máy chưa cập nhật Agent hỗ trợ Solver bảo mật.");
      }
    }

    if (commandKeys.includes("repair")) {
      deviceIds =
        await filterSelfHealDeviceIds(
          deviceIds,
          env
        );

      if (deviceIds.length === 0) {
        throw new Error(
          "Máy chưa cập nhật Agent hỗ trợ tự sửa."
        );
      }
    }

    return {
      target: "devices",
      fileTarget: "all",
      targetLabel:
        deviceIds.length === 1
          ? await deviceDisplayName(
              deviceIds[0],
              env
            )
          : `${deviceIds.length} máy đã chọn`,
      deviceIds,
      commandKeys,
      commandLines,
    };
  }

  const target = String(
    spec.target || ""
  ).toLowerCase();

  if (!TARGET_FILES[target]) {
    throw new Error(
      "Nhóm máy không hợp lệ."
    );
  }

  let deviceIds =
    await activeDeviceIdsForTarget(
      target,
      env
    );

  if (commandKeys.includes("update_solver")) {
    deviceIds = await filterSecureSolverDeviceIds(deviceIds, env);
  }

  if (commandKeys.includes("repair")) {
    deviceIds =
      await filterSelfHealDeviceIds(
        deviceIds,
        env
      );
  }

  if (deviceIds.length === 0) {
    throw new Error(
      commandKeys.includes("update_solver")
        ? "Chưa có máy nào cập nhật Agent hỗ trợ Solver bảo mật."
        : "Không có thiết bị đủ điều kiện nhận lệnh."
    );
  }

  return {
    target,
    fileTarget: target,
    targetLabel:
      await getTargetLabel(target, env),
    deviceIds,
    commandKeys,
    commandLines,
  };
}

async function requestCommandDispatch(
  chatId,
  spec,
  env,
  options = {}
) {
  const activeRollout =
    await getActiveRollout(env);

  if (activeRollout) {
    await sendMessage(
      chatId,
      env,
      `Đang có rollout ${activeRollout.id}. ` +
        "Không gửi lệnh thường để tránh ghi đè file lệnh. " +
        "Dùng /rollout status hoặc /rollout stop."
    );
    return null;
  }

  let resolved;

  try {
    resolved = await resolveCommandSpec(
      spec,
      env
    );
  } catch (error) {
    await sendMessage(
      chatId,
      env,
      `❌ Không thể chuẩn bị lệnh: ${error.message}`
    );
    return null;
  }

  const dangerous =
    resolved.commandKeys.some(
      (key) =>
        DANGEROUS_COMMAND_KEYS.has(key)
    );

  if (
    dangerous &&
    !options.confirmed
  ) {
    const token =
      crypto.randomUUID()
        .replace(/-/g, "")
        .slice(0, 12);

    await savePendingDispatch(
      token,
      resolved
    );

    const summary =
      resolved.commandLines
        .map((line) => {
          const shortLine =
            line.length > 240
              ? line.slice(0, 237) + "..."
              : line;

          return `- ${shortLine}`;
        })
        .join("\n");

    await sendMessage(
      chatId,
      env,
      `⚠️ XÁC NHẬN LỆNH\n\n` +
        `Đích: ${resolved.targetLabel}\n` +
        `Số máy: ${resolved.deviceIds.length}\n` +
        `Lệnh:\n${summary}\n\n` +
        (
          resolved.target === "devices"
            ? "Lệnh hết hạn sau 5 phút."
            : "Tự bù 24 giờ sẽ bật khi Agent đã hỗ trợ hàng đợi."
        ),
      {
        inline_keyboard: [
          [
            {
              text: "✅ XÁC NHẬN",
              callback_data:
                `dispatch_ok:${token}`,
            },
            {
              text: "❌ HỦY",
              callback_data:
                `dispatch_cancel:${token}`,
            },
          ],
        ],
      }
    );

    return null;
  }

  try {
    return await dispatchCommandSpec(
      chatId,
      resolved,
      env
    );
  } catch (error) {
    await sendMessage(
      chatId,
      env,
      `❌ Không gửi được lệnh: ${error.message}`
    );
    return null;
  }
}

async function dispatchCommandSpec(
  chatId,
  spec,
  env
) {
  const deviceIds =
    spec.target === "devices"
      ? await validateDirectDeviceIds(
          spec.deviceIds,
          env
        )
      : await revalidateGroupDeviceIds(
          spec.deviceIds,
          env
        );

  if (deviceIds.length === 0) {
    throw new Error(
      "Không còn thiết bị đủ điều kiện nhận lệnh."
    );
  }

  const file =
    TARGET_FILES[spec.fileTarget];

  if (!file) {
    throw new Error(
      "Không xác định được file lệnh."
    );
  }

  const commandId =
    `${Date.now()}-` +
    crypto.randomUUID().slice(0, 8);

  const created = Date.now();
  const groupDispatch =
    spec.target !== "devices";

  const queueReadiness =
    await deferredCommandQueueReadiness(
      deviceIds,
      env
    );

  const queueCompatible =
    queueReadiness.ready;

  const deferred =
    groupDispatch &&
    queueCompatible;

  const expiresAt =
    created +
    (
      deferred
        ? DEFERRED_COMMAND_TTL_MS
        : COMMAND_TTL_MS
    );

  const envelope = commandEnvelope(
    commandId,
    deviceIds,
    expiresAt,
    spec.commandLines
  );

  const currentFile =
    await getGitHubFile(
      file,
      env
    );

  const currentContent =
    decodeBase64(
      currentFile.content || ""
    );

  const activeCurrentBlocks =
    splitCommandEnvelopeBlocks(
      currentContent
    ).filter(
      (block) =>
        commandEnvelopeExpiresAt(block)
          > created
    );

  const clearsGroupQueue =
    groupDispatch &&
    spec.commandKeys.includes("idle");

  if (
    !queueCompatible &&
    activeCurrentBlocks.length > 0 &&
    !clearsGroupQueue
  ) {
    throw new Error(
      "Hàng đợi đang hoạt động nhưng có máy chưa cập nhật Agent: " +
      queueReadiness.unsupported.join(", ")
    );
  }

  const content =
    clearsGroupQueue
      ? envelope
      : queueCompatible
        ? mergeCommandFileContent(
            currentContent,
            envelope,
            created
          )
        : envelope;

  const commandName =
    spec.commandKeys
      .map(
        (key) => COMMANDS[key].value
      )
      .join(" + ");

  const commit = await updateGitHubFile(
    file,
    content,
    `bot: ${spec.targetLabel} ${commandName}`,
    env,
    currentFile
  );
  try {
    await enqueueFastCommand(
      env,
      commandId,
      deviceIds,
      expiresAt,
      envelope
    );
  } catch (error) {
    console.error(
      "fast_command_enqueue_failed",
      error?.message || error
    );
  }


  let metadataStored = true;
  try {
    await storeCommandMetadata(
      env,
      commandId,
      spec.target,
      spec.commandLines,
      {
        targetLabel: spec.targetLabel,
        fileTarget: spec.fileTarget,
        deviceIds,
        commandKeys: spec.commandKeys,
        created,
        expiresAt,
      }
    );
  } catch (error) {
    metadataStored = false;
    console.error("command_metadata_store_failed", error?.message || error);
  }

  await sendMessage(
    chatId,
    env,
    `✅ Đã gửi tới ${deviceIds.length} máy\n` +
      `Đích: ${spec.targetLabel}\n` +
      `Lệnh:\n- ${spec.commandLines.join("\n- ")}\n` +
      `Hết hạn: ${deferred ? "24 giờ" : "5 phút"}\n` +
      (
        deferred
          ? "Tự bù: máy offline sẽ nhận khi online lại.\n"
          : groupDispatch
            ? `Tự bù: chưa bật vì ${queueReadiness.unsupported.length} máy chưa cập nhật Agent.\n`
            : ""
      ) +
      `Commit: ${commit.slice(0, 7)}` +
      (metadataStored ? "" : "\nTiến độ: tạm không lưu do KV hết quota."),
    {
      inline_keyboard: [
        [
          {
            text: "CẬP NHẬT TIẾN ĐỘ",
            callback_data:
              "show_progress",
          },
          {
            text: "DANH SÁCH MÁY",
            callback_data:
              "show_devices",
          },
        ],
      ],
    }
  );

  return {
    commandId,
    deviceIds,
    commit,
  };
}


async function storeCommandMetadata(
  env,
  commandId,
  target,
  commands,
  options = {}
) {
  if (
    target !== "devices" &&
    !TARGET_FILES[target]
  ) {
    throw new Error(
      `Invalid command target: ${target}`
    );
  }

  const commandList =
    Array.isArray(commands)
      ? commands.map(String)
      : [String(commands)];

  const deviceIds =
    normalizeDeviceIdList(
      options.deviceIds || []
    );

  const created =
    Number(options.created) ||
    Date.now();

  const expiresAt =
    Number(options.expiresAt) ||
    created + COMMAND_TTL_MS;

  const metadata = {
    command_id: commandId,
    target,
    target_label:
      options.targetLabel ||
      target.toUpperCase(),
    file_target:
      options.fileTarget ||
      target,
    commands: commandList,
    command_keys:
      Array.isArray(options.commandKeys)
        ? options.commandKeys
        : [],
    command_count:
      commandList.length,
    device_count:
      deviceIds.length,
    device_ids:
      deviceIds,
    created,
    expires_at:
      expiresAt,
  };

  await env.DEVICE_STATUS.put(
    `cmd:${commandId}`,
    JSON.stringify(metadata),
    {
      expirationTtl:
        COMMAND_METADATA_TTL_SECONDS,
    }
  );

  await env.DEVICE_STATUS.put(
    "latest_command",
    JSON.stringify({
      command_id: commandId,
      created,
    })
  );

  return metadata;
}



function randomBase64Url(byteLength = 32) {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);

  let binary = "";

  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }

  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

async function sha256Hex(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest(
    "SHA-256",
    bytes
  );

  return Array.from(
    new Uint8Array(digest),
    (byte) => byte.toString(16).padStart(2, "0")
  ).join("");
}

async function readSmallJson(request) {
  let raw;

  try {
    raw = await request.text();
  } catch (error) {
    return {
      error: "invalid_body",
    };
  }

  if (raw.length > 4096) {
    return {
      error: "body_too_large",
    };
  }

  try {
    const value = JSON.parse(raw);

    if (!value || typeof value !== "object") {
      return {
        error: "invalid_json",
      };
    }

    return {
      value,
    };
  } catch (error) {
    return {
      error: "invalid_json",
    };
  }
}

function normalizePairId(value) {
  const raw = String(value || "").trim();

  return /^[A-Za-z0-9_-]{20,40}$/.test(raw)
    ? raw
    : null;
}

function normalizePairToken(value) {
  const raw = String(value || "").trim();

  return /^[A-Za-z0-9_-]{40,100}$/.test(raw)
    ? raw
    : null;
}

function pairRecordKey(pairId) {
  return `${PAIR_PREFIX}${pairId}`;
}

function pairDeviceKey(deviceId) {
  return `${PAIR_DEVICE_PREFIX}${deviceId}`;
}

async function clearPairDeviceIndex(
  record,
  pairId,
  env
) {
  if (!record?.device_id) return;

  const key = pairDeviceKey(record.device_id);
  const current = await env.DEVICE_STATUS.get(key);

  if (current === pairId) {
    await env.DEVICE_STATUS.delete(key);
  }
}

async function handlePairRequest(
  request,
  env,
  workerOrigin
) {
  if (
    !String(env.AGENT_REPORT_SECRET || "").trim() ||
    !String(env.TELEGRAM_BOT_TOKEN || "").trim() ||
    !String(env.TELEGRAM_ADMIN_USER_ID || "").trim()
  ) {
    return noStoreJson(
      {
        ok: false,
        error: "pairing_not_configured",
      },
      503
    );
  }

  const parsed = await readSmallJson(request);

  if (parsed.error) {
    return noStoreJson(
      {
        ok: false,
        error: parsed.error,
      },
      400
    );
  }

  const purposeRaw = String(
    parsed.value.purpose || "agent"
  ).trim();
  const purpose =
    purposeRaw === "google_login"
      ? "google_login"
      : purposeRaw === "agent"
        ? "agent"
        : null;

  if (!purpose) {
    return noStoreJson(
      {
        ok: false,
        error: "invalid_pair_purpose",
      },
      400
    );
  }

  if (
    purpose === "google_login" &&
    (
      !String(env.GOOGLE_LOGIN_EMAIL || "").trim() ||
      !String(env.GOOGLE_LOGIN_PASSWORD || "")
    )
  ) {
    return noStoreJson(
      {
        ok: false,
        error: "google_login_not_configured",
      },
      503
    );
  }

  const deviceId = normalizeDeviceId(
    parsed.value.device_id
  );
  const deviceGroup = normalizeDeviceGroup(
    parsed.value.device_group
  );

  if (!deviceId) {
    return noStoreJson(
      {
        ok: false,
        error: "invalid_device_id",
      },
      400
    );
  }

  if (!deviceGroup) {
    return noStoreJson(
      {
        ok: false,
        error: "invalid_device_group",
      },
      400
    );
  }

  const legacyPrefix =
    deviceId.match(/^(MARMOT|NOVA)-/);

  if (
    legacyPrefix &&
    legacyPrefix[1] !== deviceGroup
  ) {
    return noStoreJson(
      {
        ok: false,
        error: "group_mismatch",
      },
      400
    );
  }

  if (
    await isDeviceRevoked(
      deviceId,
      env
    )
  ) {
    return noStoreJson(
      {
        ok: false,
        error: "device_revoked",
        device_id: deviceId,
      },
      410
    );
  }

  const clientIp =
    request.headers.get("CF-Connecting-IP") ||
    "unknown";

  const ipHash = (
    await sha256Hex(clientIp)
  ).slice(0, 20);

  const rateKey =
    `${PAIR_RATE_PREFIX}${purpose}:${deviceId}:${ipHash}`;

  if (await env.DEVICE_STATUS.get(rateKey)) {
    return noStoreJson(
      {
        ok: false,
        error: "pair_rate_limited",
        retry_after: PAIR_RATE_SECONDS,
      },
      429
    );
  }

  await env.DEVICE_STATUS.put(
    rateKey,
    "1",
    {
      expirationTtl: PAIR_RATE_SECONDS,
    }
  );

  const previousPairId =
    await env.DEVICE_STATUS.get(
      pairDeviceKey(deviceId)
    );

  if (previousPairId) {
    await env.DEVICE_STATUS.delete(
      pairRecordKey(previousPairId)
    );
  }

  const pairId = randomBase64Url(18);
  const pairToken = randomBase64Url(32);
  const tokenHash = await sha256Hex(pairToken);

  const randomNumber = new Uint32Array(1);
  crypto.getRandomValues(randomNumber);

  const verificationCode = String(
    randomNumber[0] % 1000000
  ).padStart(6, "0");

  const createdAt = Date.now();
  const expiresAt =
    createdAt + PAIR_TTL_SECONDS * 1000;

  if (
    await isDeviceRevoked(
      deviceId,
      env
    )
  ) {
    return noStoreJson(
      {
        ok: false,
        error: "device_revoked",
        device_id: deviceId,
      },
      410
    );
  }

  const record = {
    pair_id: pairId,
    device_id: deviceId,
    device_group: deviceGroup,
    purpose,
    verification_code: verificationCode,
    token_hash: tokenHash,
    status: "pending",
    created_at: createdAt,
    expires_at: expiresAt,
  };

  await env.DEVICE_STATUS.put(
    pairRecordKey(pairId),
    JSON.stringify(record),
    {
      expirationTtl: PAIR_TTL_SECONDS,
    }
  );

  await env.DEVICE_STATUS.put(
    pairDeviceKey(deviceId),
    pairId,
    {
      expirationTtl: PAIR_TTL_SECONDS,
    }
  );

  if (
    await isDeviceRevoked(
      deviceId,
      env
    )
  ) {
    await env.DEVICE_STATUS.delete(
      pairRecordKey(pairId)
    );
    await env.DEVICE_STATUS.delete(
      pairDeviceKey(deviceId)
    );

    return noStoreJson(
      {
        ok: false,
        error: "device_revoked",
        device_id: deviceId,
      },
      410
    );
  }

  const expiresText =
    new Date(expiresAt)
      .toISOString()
      .replace("T", " ")
      .replace("Z", " UTC");

  const requestLabel =
    purpose === "google_login"
      ? "đăng nhập Google"
      : "ghép nối Agent";

  try {
    await sendMessage(
      String(env.TELEGRAM_ADMIN_USER_ID),
      env,
      `🔐 Yêu cầu ${requestLabel}\n` +
        `Thiết bị: ${deviceId}\n` +
        `Nhóm: ${await getGroupLabel(deviceGroup, env)}\n` +
        `Mã xác minh: ${verificationCode}\n` +
        `Hết hạn: ${expiresText}\n\n` +
        "Chỉ chấp nhận nếu mã trên Termux trùng khớp.",
      {
        inline_keyboard: [
          [
            {
              text: "✅ CHẤP NHẬN",
              callback_data:
                `pair_approve:${pairId}`,
            },
            {
              text: "❌ TỪ CHỐI",
              callback_data:
                `pair_deny:${pairId}`,
            },
          ],
        ],
      }
    );
  } catch (error) {
    await env.DEVICE_STATUS.delete(
      pairRecordKey(pairId)
    );
    await env.DEVICE_STATUS.delete(
      pairDeviceKey(deviceId)
    );

    console.error(
      "pair_notification_failed",
      error?.message || error
    );

    return noStoreJson(
      {
        ok: false,
        error: "pair_notification_failed",
      },
      502
    );
  }

  return noStoreJson(
    {
      ok: true,
      status: "pending",
      purpose,
      pair_id: pairId,
      pair_token: pairToken,
      verification_code: verificationCode,
      expires_in: PAIR_TTL_SECONDS,
      poll_after: 3,
    },
    201
  );
}

async function handlePairStatus(
  request,
  env,
  workerOrigin
) {
  if (!String(env.AGENT_REPORT_SECRET || "").trim()) {
    return noStoreJson(
      {
        ok: false,
        error: "pairing_not_configured",
      },
      503
    );
  }

  const parsed = await readSmallJson(request);

  if (parsed.error) {
    return noStoreJson(
      {
        ok: false,
        error: parsed.error,
      },
      400
    );
  }

  const pairId = normalizePairId(
    parsed.value.pair_id
  );
  const pairToken = normalizePairToken(
    parsed.value.pair_token
  );

  if (!pairId || !pairToken) {
    return noStoreJson(
      {
        ok: false,
        error: "invalid_pair_credentials",
      },
      400
    );
  }

  const raw = await env.DEVICE_STATUS.get(
    pairRecordKey(pairId)
  );

  if (!raw) {
    return noStoreJson(
      {
        ok: false,
        error: "pair_not_found",
      },
      404
    );
  }

  let record;

  try {
    record = JSON.parse(raw);
  } catch (error) {
    return noStoreJson(
      {
        ok: false,
        error: "invalid_pair_record",
      },
      500
    );
  }

  const purpose =
    record.purpose === "google_login"
      ? "google_login"
      : "agent";

  const pairedDeviceId =
    normalizeDeviceId(
      record.device_id
    );

  if (
    pairedDeviceId &&
    await isDeviceRevoked(
      pairedDeviceId,
      env
    )
  ) {
    await env.DEVICE_STATUS.delete(
      pairRecordKey(pairId)
    );
    await clearPairDeviceIndex(
      record,
      pairId,
      env
    );

    return noStoreJson(
      {
        ok: false,
        error: "device_revoked",
        device_id:
          pairedDeviceId,
      },
      410
    );
  }

  const now = Date.now();

  if (
    !Number(record.expires_at) ||
    now > Number(record.expires_at)
  ) {
    await env.DEVICE_STATUS.delete(
      pairRecordKey(pairId)
    );
    await clearPairDeviceIndex(
      record,
      pairId,
      env
    );

    return noStoreJson(
      {
        ok: false,
        error: "pair_expired",
      },
      410
    );
  }

  const tokenHash = await sha256Hex(pairToken);

  if (tokenHash !== record.token_hash) {
    return noStoreJson(
      {
        ok: false,
        error: "invalid_pair_credentials",
      },
      401
    );
  }

  if (record.status === "pending") {
    return noStoreJson(
      {
        ok: true,
        status: "pending",
        purpose,
      },
      202
    );
  }

  if (record.status === "denied") {
    return noStoreJson(
      {
        ok: false,
        status: "denied",
        error: "pair_denied",
      },
      403
    );
  }

  if (record.status === "consumed") {
    return noStoreJson(
      {
        ok: false,
        status: "consumed",
        error: "pair_already_consumed",
      },
      409
    );
  }

  if (record.status !== "approved") {
    return noStoreJson(
      {
        ok: false,
        error: "invalid_pair_status",
      },
      409
    );
  }

  if (purpose === "google_login") {
    const email =
      String(env.GOOGLE_LOGIN_EMAIL || "").trim();
    const password =
      String(env.GOOGLE_LOGIN_PASSWORD || "");

    if (
      !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email) ||
      !password ||
      password.length > 256
    ) {
      return noStoreJson(
        {
          ok: false,
          error: "google_login_not_configured",
        },
        503
      );
    }

    const consumedRecord = {
      ...record,
      status: "consumed",
      consumed_at: now,
    };

    delete consumedRecord.token_hash;

    await env.DEVICE_STATUS.put(
      pairRecordKey(pairId),
      JSON.stringify(consumedRecord),
      {
        expirationTtl: 60,
      }
    );

    await clearPairDeviceIndex(
      record,
      pairId,
      env
    );

    return noStoreJson({
      ok: true,
      status: "approved",
      purpose,
      google_login: {
        enabled: true,
        email,
        password,
        delete_after_success: true,
      },
    });
  }

  const consumedRecord = {
    ...record,
    status: "consumed",
    consumed_at: now,
  };

  delete consumedRecord.token_hash;

  await env.DEVICE_STATUS.put(
    pairRecordKey(pairId),
    JSON.stringify(consumedRecord),
    {
      expirationTtl: 60,
    }
  );

  await clearPairDeviceIndex(
    record,
    pairId,
    env
  );

  return noStoreJson({
    ok: true,
    status: "approved",
    purpose,
    worker_report_url:
      `${workerOrigin}/agent/report`,
    agent_report_secret:
      String(env.AGENT_REPORT_SECRET),
  });
}

async function handlePairDecision(
  pairIdValue,
  approve,
  callback,
  chatId,
  messageId,
  env,
  fromId
) {
  const pairId = normalizePairId(pairIdValue);

  if (!pairId) {
    await answerCallback(
      callback.id,
      env,
      "Yêu cầu ghép nối không hợp lệ.",
      true
    );
    return;
  }

  const key = pairRecordKey(pairId);
  const raw = await env.DEVICE_STATUS.get(key);

  if (!raw) {
    await answerCallback(
      callback.id,
      env,
      "Yêu cầu không tồn tại hoặc đã hết hạn.",
      true
    );
    return;
  }

  let record;

  try {
    record = JSON.parse(raw);
  } catch (error) {
    await answerCallback(
      callback.id,
      env,
      "Dữ liệu yêu cầu không hợp lệ.",
      true
    );
    return;
  }

  const pairedDeviceId =
    normalizeDeviceId(
      record.device_id
    );

  if (
    pairedDeviceId &&
    await isDeviceRevoked(
      pairedDeviceId,
      env
    )
  ) {
    await env.DEVICE_STATUS.delete(
      key
    );
    await clearPairDeviceIndex(
      record,
      pairId,
      env
    );

    await answerCallback(
      callback.id,
      env,
      "ID này đã bị thu hồi vĩnh viễn.",
      true
    );
    return;
  }

  const now = Date.now();

  if (
    !Number(record.expires_at) ||
    now > Number(record.expires_at)
  ) {
    await env.DEVICE_STATUS.delete(key);
    await clearPairDeviceIndex(
      record,
      pairId,
      env
    );

    await answerCallback(
      callback.id,
      env,
      "Yêu cầu đã hết hạn.",
      true
    );
    return;
  }

  if (record.status !== "pending") {
    await answerCallback(
      callback.id,
      env,
      `Yêu cầu đã ở trạng thái: ${record.status}`,
      true
    );
    return;
  }

  record.status =
    approve ? "approved" : "denied";
  record.decided_at = now;
  record.decided_by = String(fromId);

  const remainingTtl = Math.max(
    60,
    Math.ceil(
      (Number(record.expires_at) - now) / 1000
    )
  );

  await env.DEVICE_STATUS.put(
    key,
    JSON.stringify(record),
    {
      expirationTtl:
        approve ? remainingTtl : 60,
    }
  );

  if (!approve) {
    await clearPairDeviceIndex(
      record,
      pairId,
      env
    );
  }

  await answerCallback(
    callback.id,
    env,
    approve
      ? "Đã chấp nhận ghép nối."
      : "Đã từ chối ghép nối."
  );

  const statusText =
    approve
      ? "✅ ĐÃ CHẤP NHẬN"
      : "❌ ĐÃ TỪ CHỐI";

  if (messageId) {
    try {
      await editMessage(
        chatId,
        messageId,
        env,
        `${statusText} GHÉP NỐI\n` +
          `Thiết bị: ${record.device_id}\n` +
          `Nhóm: ${await getGroupLabel(record.device_group, env)}\n` +
          `Mã xác minh: ${record.verification_code}`,
        {
          inline_keyboard: [],
        }
      );
    } catch (error) {
      console.error(
        "pair_message_edit_failed",
        error?.message || error
      );
    }
  }
}

async function renameDevice(
  chatId,
  rawDeviceId,
  rawLabel,
  env
) {
  const deviceId = normalizeDeviceId(rawDeviceId);

  if (!deviceId) {
    await sendMessage(
      chatId,
      env,
      "Device ID không hợp lệ."
    );
    return;
  }

  const record = await getDeviceRecord(deviceId, env);

  if (!record) {
    await sendMessage(
      chatId,
      env,
      `Không tìm thấy thiết bị ${deviceId}.`
    );
    return;
  }

  if (String(rawLabel || "").trim() === "-") {
    await env.DEVICE_STATUS.delete(
      `${DEVICE_ALIAS_PREFIX}${deviceId}`
    );
    await sendMessage(
      chatId,
      env,
      `Đã xóa tên riêng của ${deviceId}.`
    );
    return;
  }

  const label = sanitizeLabel(rawLabel, 48);

  if (!label) {
    await sendMessage(
      chatId,
      env,
      "Tên phải từ 1 đến 48 ký tự."
    );
    return;
  }

  await env.DEVICE_STATUS.put(
    `${DEVICE_ALIAS_PREFIX}${deviceId}`,
    label
  );

  await sendMessage(
    chatId,
    env,
    `Đã đổi tên ${deviceId} thành: ${label}`
  );
}


async function deleteDevice(
  chatId,
  rawDeviceId,
  env
) {
  await requestPermanentDelete(
    chatId,
    rawDeviceId,
    env
  );
}

async function setGroupLabel(
  chatId,
  rawGroup,
  rawLabel,
  env
) {
  const group =
    normalizeGroupInput(
      rawGroup
    );

  if (!group) {
    await sendMessage(
      chatId,
      env,
      "Nhóm phải là 1 hoặc 2."
    );
    return;
  }

  const defaultLabel =
    DEFAULT_GROUP_LABELS[group];

  if (
    String(rawLabel || "")
      .trim() === "-"
  ) {
    await env.DEVICE_STATUS.delete(
      `${GROUP_LABEL_PREFIX}${group}`
    );

    await sendMessage(
      chatId,
      env,
      `Đã khôi phục tên mặc định: ${defaultLabel}.`
    );
    return;
  }

  const label =
    sanitizeLabel(
      rawLabel,
      32
    );

  if (!label) {
    await sendMessage(
      chatId,
      env,
      "Tên nhóm phải từ 1 đến 32 ký tự."
    );
    return;
  }

  await env.DEVICE_STATUS.put(
    `${GROUP_LABEL_PREFIX}${group}`,
    label
  );

  await sendMessage(
    chatId,
    env,
    `Đã đổi tên ${defaultLabel} thành: ${label}`
  );
}

function getRolloutOps() {
  return {
    targetFiles: TARGET_FILES,
    commands: COMMANDS,
    targetAgentVersion:
      TARGETING_AGENT_VERSION,
    onlineWindowMs:
      ONLINE_WINDOW_MS,
    commandTtlMs:
      COMMAND_TTL_MS,
    normalizeTargetInput,
    normalizeDeviceId,
    compareDeviceIds,
    deviceIdsForTarget,
    getDeviceRecord,
    isDeviceMaintenance,
    getTargetLabel,
    commandEnvelope,
    updateGitHubFile,
    storeCommandMetadata,
    sendMessage,
    answerCallback,
  };
}

export default {
  async fetch(request, env) {
    try {
      const url = new URL(request.url);

      if (
        request.method === "GET" &&
        url.pathname === "/aot/hub"
      ) {
        return await handleAotHubPage();
      }

      if (
        request.method === "GET" &&
        url.pathname === "/aot/hub/api/state"
      ) {
        return await handleAotHubState(
          request,
          env,
          url
        );
      }

      if (
        request.method === "GET" &&
        url.pathname === "/aot/hub/api/ws"
      ) {
        return await handleAotHubDashboardWebSocket(request, env, url);
      }

      if (
        request.method === "POST" &&
        url.pathname === "/aot/hub/api/control"
      ) {
        return await handleAotHubControl(
          request,
          env
        );
      }


      if (
        request.method === "GET" &&
        url.pathname === "/aot/control/health"
      ) {
        return await handleAotControlHealth(
          request,
          env
        );
      }

      if (request.method === "POST" && url.pathname === "/aot/control/registration/discover") {
        return await handleAotRegistration(request, env, "discover");
      }
      if (request.method === "POST" && url.pathname === "/aot/control/registration/reset") {
        return await handleAotRegistration(request, env, "reset");
      }
      if (request.method === "POST" && url.pathname === "/aot/control/registration/verify") {
        return await handleAotRegistration(request, env, "verify");
      }

      if (
        request.method === "GET" &&
        url.pathname === "/aot/control/ws"
      ) {
        return await handleAotControlWebSocket(
          request,
          env,
          url
        );
      }

      if (
        request.method === "POST" &&
        url.pathname === "/aot/control/action"
      ) {
        return noStoreJson({ ok: false, error: "cross_device_control_removed" }, 410);
      }

      if (
        request.method === "POST" &&
        url.pathname === "/aot/control/ack"
      ) {
        return await handleAotControlAck(
          request,
          env
        );
      }

      if (
        request.method === "GET" &&
        url.pathname === "/agent/commands/ws"
      ) {
        return await handleAgentCommandWebSocket(
          request,
          env,
          url
        );
      }


      if (request.method === "GET" && url.pathname === "/") {
        return text("Aotscript Control Bot is online");
      }

      if (request.method === "GET" && url.pathname === "/setup") {
        const webhookUrl = `${url.origin}/telegram`;
        const result = await telegram(env, "setWebhook", {
          url: webhookUrl,
          secret_token: env.TELEGRAM_WEBHOOK_SECRET,
          allowed_updates: ["message", "callback_query"],
          drop_pending_updates: true,
        });

        await ensureNumberedGroupLabels(env);

        await telegram(env, "setMyCommands", {
          commands: [
            { command: "start", description: "Mở bảng điều khiển" },
            { command: "status", description: "Xem lệnh hiện tại" },
            { command: "devices", description: "Danh sách thiết bị" },
            { command: "health", description: "Sức khỏe toàn hệ thống" },
            { command: "alerts", description: "Cấu hình cảnh báo tự động" },
            { command: "device", description: "Chi tiết một thiết bị" },
            { command: "to", description: "Gửi lệnh tới máy cụ thể" },
            { command: "update", description: "Cập nhật canary hoặc stable" },
            { command: "batch", description: "Chạy lệnh theo lô (backup/apps)" },
            { command: "maintenance", description: "Bật hoặc tắt bảo trì" },
            { command: "rename", description: "Đổi tên thiết bị" },
            { command: "delete", description: "Thu hồi thiết bị vĩnh viễn" },
            { command: "restore", description: "Cho phép lại ID đã thu hồi" },
            { command: "groupname", description: "Đổi tên hiển thị nhóm" },
            { command: "progress", description: "Xem tiến độ lệnh gần nhất" },
            { command: "rollout", description: "Triển khai an toàn theo đợt" },
          ],
        });

        return json({ ok: true, webhook: webhookUrl, telegram: result });
      }

      if (request.method === "POST" && url.pathname === "/telegram") {
        const webhookSecret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
        if (webhookSecret !== env.TELEGRAM_WEBHOOK_SECRET) {
          return new Response("Unauthorized", { status: 401 });
        }

        const update = await request.json();
        await handleUpdate(update, env);
        return text("ok");
      }

      if (
        request.method === "POST" &&
        url.pathname === "/agent/pair/request"
      ) {
        return await handlePairRequest(
          request,
          env,
          url.origin
        );
      }

      if (
        request.method === "POST" &&
        url.pathname === "/agent/pair/status"
      ) {
        return await handlePairStatus(
          request,
          env,
          url.origin
        );
      }

      if (request.method === "GET" && url.pathname === "/agent/solver-url") {
        return await handleSecureSolverUrl(request, env);
      }

      // Agent report endpoint
      if (request.method === "POST" && url.pathname === "/agent/report") {
        return await handleAgentReport(request, env);
      }

      return new Response("Not found", { status: 404 });
    } catch (error) {
      console.error(error);
      return json({ ok: false, error: String(error?.message || error) }, 500);
    }
  },

  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(
      Promise.all([
        ensureNumberedGroupLabels(env),
        checkFleetHealth(env),
        checkActiveRollout(
          env,
          getRolloutOps()
        ),
      ])
    );
  },
};

async function handleUpdate(update, env) {
  const message = update.message;
  const callback = update.callback_query;
  const from = message?.from || callback?.from;
  const chatId = message?.chat?.id || callback?.message?.chat?.id;
  const messageId = callback?.message?.message_id;

  if (!from || !chatId) return;

  if (String(from.id) !== String(env.TELEGRAM_ADMIN_USER_ID)) {
    if (callback) {
      await telegram(env, "answerCallbackQuery", {
        callback_query_id: callback.id,
        text: "Không có quyền sử dụng bot.",
        show_alert: true,
      });
    }
    return;
  }

  if (callback) {
    await handleCallback(callback, chatId, messageId, env, from.id);
    return;
  }

  const input = (message.text || message.caption || "").trim();

  if (input === "/start" || input === "/menu") {
    await showTargets(chatId, env);
    return;
  }

  if (input === "/status") {
    await sendStatus(chatId, env);
    return;
  }

  if (input === "/devices") {
    await showDevices(chatId, env);
    return;
  }

  if (input === "/health") {
    await showHealth(
      chatId,
      env
    );
    return;
  }

  const alertsMatch =
    input.match(
      /^\/alerts(?:\s+(on|off|status))?$/i
    );

  if (alertsMatch) {
    const requested =
      String(
        alertsMatch[1] || "status"
      ).toLowerCase();

    await configureAlerts(
      chatId,
      requested === "on"
        ? true
        : requested === "off"
          ? false
          : null,
      env
    );
    return;
  }

  if (
    await handleRolloutCommand({
      input,
      chatId,
      env,
      ops: getRolloutOps(),
    })
  ) {
    return;
  }

  const deviceMatch =
    input.match(
      /^\/device\s+(\S+)$/i
    );

  if (deviceMatch) {
    await showDeviceDetail(
      chatId,
      deviceMatch[1],
      env
    );
    return;
  }

  const maintenanceMatch =
    input.match(
      /^\/maintenance\s+(\S+)\s+(on|off)$/i
    );

  if (maintenanceMatch) {
    await changeDeviceMaintenance(
      chatId,
      maintenanceMatch[1],
      maintenanceMatch[2]
        .toLowerCase() === "on",
      env
    );
    return;
  }

  const directSolverMatch =
    input.match(
      /^\/to\s+([A-Za-z0-9_,-]+)\s+solver(?:\s+(https?:\/\/\S+))?$/i
    );

  if (directSolverMatch) {
    if (directSolverMatch[2]) {
      await sendMessage(
        chatId,
        env,
        "Không gửi URL Solver qua Telegram. Dùng: /to m116 solver"
      );
      return;
    }
    await requestCommandDispatch(
      chatId,
      {
        deviceIds: directSolverMatch[1].split(","),
        commandKeys: ["update_solver"],
        commandLines: ["UPDATE_SOLVER_SECURE"],
      },
      env
    );
    return;
  }

  const directScriptMatch =
    input.match(
      /^\/to\s+([A-Za-z0-9_,-]+)\s+script\s+(https?:\/\/\S+)$/i
    );

  if (directScriptMatch) {
    const url = directScriptMatch[2];

    if (!isValidUrl(url)) {
      await sendMessage(
        chatId,
        env,
        "URL Script không hợp lệ."
      );
      return;
    }

    await requestCommandDispatch(
      chatId,
      {
        deviceIds:
          directScriptMatch[1].split(","),
        commandKeys:
          ["update_script"],
        commandLines:
          [`Updatescript ${url}`],
      },
      env
    );
    return;
  }

  const directMatch =
    input.match(
      /^\/to\s+([A-Za-z0-9_,-]+)\s+(.+)$/i
    );

  if (directMatch) {
    const commandKeys =
      parseCommandKeysText(
        directMatch[2]
      );

    if (
      !commandKeys ||
      commandKeys.includes(
        "update_solver"
      ) ||
      commandKeys.includes(
        "update_script"
      )
    ) {
      await sendMessage(
        chatId,
        env,
        "Cú pháp không hợp lệ.\n" +
          "Ví dụ: /to m166 idle\n" +
          "Nhiều máy: /to m166,m167 reboot\n" +
          "Solver: /to m166 solver\n" +
          "Script: /to m166 script https://..."
      );
      return;
    }

    let commandLines;

    try {
      commandLines =
        buildCommandLinesFromKeys(
          commandKeys
        );
    } catch (error) {
      await sendMessage(
        chatId,
        env,
        `❌ ${error.message}`
      );
      return;
    }

    await requestCommandDispatch(
      chatId,
      {
        deviceIds:
          directMatch[1].split(","),
        commandKeys,
        commandLines,
      },
      env
    );
    return;
  }

  const renameMatch =
    input.match(/^\/rename\s+(\S+)\s+(.+)$/i);

  if (renameMatch) {
    await renameDevice(
      chatId,
      renameMatch[1],
      renameMatch[2],
      env
    );
    return;
  }

  const deleteMatch =
    input.match(/^\/delete\s+(\S+)$/i);

  if (deleteMatch) {
    await deleteDevice(
      chatId,
      deleteMatch[1],
      env
    );
    return;
  }

  const restoreMatch =
    input.match(
      /^\/restore\s+(\S+)$/i
    );

  if (restoreMatch) {
    await restoreDevice(
      chatId,
      restoreMatch[1],
      env
    );
    return;
  }

  const groupNameMatch =
    input.match(/^\/groupname\s+(\S+)\s+(.+)$/i);

  if (groupNameMatch) {
    await setGroupLabel(
      chatId,
      groupNameMatch[1],
      groupNameMatch[2],
      env
    );
    return;
  }

  if (input === "/progress") {
    await sendProgress(chatId, env);
    return;
  }

  const updateMatch = input.match(/^\/update\s+(canary|stable)(?:\s+([A-Za-z0-9_,-]+))?$/i);
  if (updateMatch) {
    const channel = updateMatch[1].toLowerCase();
    const target = updateMatch[2] ? updateMatch[2].split(",") : undefined;
    const body = {
      protocol: AOT_HUB_PROTOCOL_VERSION,
      kind: `update_${channel}`,
    };
    if (target) body.target_device_ids = target;
    const result = await fleetStateCall(env, "/aot/hub/control", { method: "POST", body });
    if (result.response.ok) {
      await sendMessage(chatId, env, `Lệnh update_${channel} đã được gửi thành công.`);
    } else {
      await sendMessage(chatId, env, `Lỗi: ${result.data?.error || result.response.status}`);
    }
    return;
  }

  const batchMatch = input.match(/^\/batch\s+(backup|apps|restore_data)(?:\s+([A-Za-z0-9_,-]+))?$/i);
  if (batchMatch) {
    const actionMap = {
      "backup": "open_swift_backup",
      "apps": "open_swift_apps",
      "restore_data": "backup_restore_data"
    };
    const kind = actionMap[batchMatch[1].toLowerCase()];
    const target = batchMatch[2] ? batchMatch[2].split(",") : undefined;
    
    const body = {
      protocol: AOT_HUB_PROTOCOL_VERSION,
      kind: kind,
    };
    if (target) body.target_device_ids = target;

    const result = await fleetStateCall(env, "/aot/hub/control", { method: "POST", body });
    if (result.response.ok) {
      await sendMessage(chatId, env, `Lệnh ${kind} đã được gửi thành công.`);
    } else {
      await sendMessage(chatId, env, `Lỗi: ${result.data?.error || result.response.status}`);
    }
    return;
  }

  const pendingState = await loadSessionState(from.id, chatId);
  if (pendingState && pendingState.step && !input.startsWith("/")) {
    await handlePendingSessionMessage(chatId, from.id, pendingState, input, env);
    return;
  }

  // Solver dùng Cloudflare secret; không nhận URL qua Telegram.
  if (/^\/solver\b.*https?:\/\//i.test(input)) {
    await sendMessage(
      chatId,
      env,
      "Không gửi URL Solver qua Telegram. Dùng /solver all, /solver marmot hoặc /solver nova."
    );
    return;
  }

  const solverMatch = input.match(/^\/solver\s+(all|marmot|nova)$/i);
  if (solverMatch) {
    await executeSolverCommand(chatId, solverMatch[1].toLowerCase(), null, env);
    return;
  }

  const scriptMatch = input.match(/^\/script\s+(all|marmot|nova)\s+(https?:\/\/\S+)$/i);
  if (scriptMatch) {
    const target = scriptMatch[1].toLowerCase();
    const url = scriptMatch[2];
    if (!TARGET_FILES[target]) {
      await sendMessage(chatId, env, "Target không hợp lệ cho /script.");
      return;
    }
    if (!isValidUrl(url)) {
      await sendMessage(chatId, env, "URL không hợp lệ. Phải là http:// hoặc https://");
      return;
    }
    await executeScriptCommand(chatId, target, url, env);
    return;
  }

  if (input === "/solver") {
    await promptTargetSelection(chatId, "solver", "", env);
    return;
  }

  const scriptShortMatch = input.match(/^\/script\s+(https?:\/\/\S+)$/i);
  if (scriptShortMatch) {
    const url = scriptShortMatch[1];
    if (!isValidUrl(url)) {
      await sendMessage(chatId, env, "URL không hợp lệ. Phải là http:// hoặc https://");
      return;
    }
    await promptTargetSelection(chatId, "script", url, env);
    return;
  }

  const parsed = parseTextCommand(input);
  if (!parsed) {
    await sendMessage(
      chatId,
      env,
      "Lệnh không hợp lệ. Dùng /start để mở bảng điều khiển hoặc /status để xem trạng thái."
    );
    return;
  }

  await executeCommands(chatId, parsed.target, parsed.commandKeys, env);
}

function parseTextCommand(input) {
  const match =
    String(input || "").match(
      /^\/(\S+)\s+(.+)$/i
    );

  if (!match) {
    return null;
  }

  const target =
    normalizeTargetInput(
      match[1]
    );

  if (
    !target ||
    !TARGET_FILES[target]
  ) {
    return null;
  }

  const commandKeys =
    match[2]
      .toLowerCase()
      .split(/[\s,]+/)
      .filter(Boolean);

  if (commandKeys.length === 0) {
    return null;
  }

  const allowed =
    new Set(
      Object.keys(COMMANDS)
    );

  const filtered =
    commandKeys.filter(
      (key) =>
        allowed.has(key)
    );

  if (
    filtered.length === 0 ||
    filtered.some(
      (key) => !COMMANDS[key]
    )
  ) {
    return null;
  }

  const uniqueKeys =
    [...new Set(filtered)];

  if (
    uniqueKeys.includes("idle") &&
    uniqueKeys.length > 1
  ) {
    return null;
  }

  return {
    target,
    commandKeys: uniqueKeys,
  };
}

async function promptTargetSelection(chatId, command, url, env) {
  const token = crypto.randomUUID().slice(0, 8);
  await saveShortCommand(token, command, url);

  const title = command === "solver" ? "/solver" : "/script";
  const marmotLabel = await getGroupLabel("MARMOT", env);
  const novaLabel = await getGroupLabel("NOVA", env);

  const replyMarkup = {
    inline_keyboard: [
      [
        {
          text: "🌍 TẤT CẢ",
          callback_data: `shortcmd:${command}:${token}:all`,
        },
        {
          text: `1️⃣ ${marmotLabel}`,
          callback_data: `shortcmd:${command}:${token}:marmot`,
        },
      ],
      [
        {
          text: `2️⃣ ${novaLabel}`,
          callback_data: `shortcmd:${command}:${token}:nova`,
        },
        {
          text: "❌ HỦY",
          callback_data: `cancel:${token}`,
        },
      ],
    ],
  };

  await sendMessage(
    chatId,
    env,
    `Chọn nhóm để gửi ${title} ${url}`,
    replyMarkup
  );
}

async function saveShortCommand(token, command, url) {
  const key = shortCommandCacheKey(token);
  const value = JSON.stringify({ command, url, created: Date.now() });
  await caches.default.put(
    new Request(key),
    new Response(value, { headers: { "Content-Type": "application/json" } })
  );
}

async function loadShortCommand(token) {
  const key = shortCommandCacheKey(token);
  const response = await caches.default.match(new Request(key));
  if (!response) return null;
  try {
    const stored = JSON.parse(await response.text());
    if (!stored || typeof stored.created !== "number") {
      await clearShortCommand(token);
      return null;
    }
    const age = Date.now() - stored.created;
    if (age > 5 * 60 * 1000) {
      await clearShortCommand(token);
      return null;
    }
    return stored;
  } catch (error) {
    await clearShortCommand(token);
    return null;
  }
}

async function clearShortCommand(token) {
  const key = shortCommandCacheKey(token);
  await caches.default.delete(new Request(key));
}

function shortCommandCacheKey(token) {
  return `https://aotscript.local/shortcmd/${token}`;
}

function sessionStateKey(userId, chatId) {
  return `https://aotscript.local/session/${userId}:${chatId}`;
}

async function saveSessionState(userId, chatId, state, env) {
  const key = sessionStateKey(userId, chatId);
  const body = JSON.stringify({ ...state, created: Date.now() });
  await caches.default.put(
    new Request(key),
    new Response(body, { headers: { "Content-Type": "application/json" } })
  );
}

async function loadSessionState(userId, chatId) {
  const key = sessionStateKey(userId, chatId);
  const response = await caches.default.match(new Request(key));
  if (!response) return null;
  try {
    const stored = JSON.parse(await response.text());
    if (!stored || typeof stored.created !== "number") {
      await clearSessionState(userId, chatId);
      return null;
    }
    if (Date.now() - stored.created > 10 * 60 * 1000) {
      await clearSessionState(userId, chatId);
      return null;
    }
    return stored;
  } catch (error) {
    await clearSessionState(userId, chatId);
    return null;
  }
}

async function clearSessionState(userId, chatId) {
  const key = sessionStateKey(userId, chatId);
  await caches.default.delete(new Request(key));
}

async function startUrlSession(chatId, userId, target, commandKeys, env) {
  const state = {
    userId,
    chatId,
    target,
    commandKeys,
    step: "awaiting_script_url",
    solverUrl: commandKeys.includes("update_solver") ? "Cloudflare secret" : null,
    scriptUrl: null,
  };
  if (!commandKeys.includes("update_script")) {
    await executeCommands(chatId, target, commandKeys, env);
    return;
  }
  await saveSessionState(userId, chatId, state, env);
  await sendMessage(
    chatId,
    env,
    "Vui lòng gửi URL Script (http:// hoặc https://) hoặc bấm HỦY.",
    { inline_keyboard: [[{ text: "❌ HỦY", callback_data: "cancel-session" }]] }
  );
}

async function handlePendingSessionMessage(chatId, userId, state, input, env) {
  const lower = input.trim();
  if (state.step === "awaiting_solver_url") {
    if (!isValidUrl(lower)) {
      await sendMessage(chatId, env, "URL Solver không hợp lệ. Phải bắt đầu bằng http:// hoặc https://. Vui lòng gửi lại hoặc bấm HỦY.");
      return;
    }
    state.solverUrl = lower;
    if (state.commandKeys.includes("update_script")) {
      state.step = "awaiting_script_url";
      await saveSessionState(userId, chatId, state, env);
      await sendMessage(chatId, env, "Vui lòng gửi URL Script (http:// hoặc https://) hoặc bấm HỦY.", {
        inline_keyboard: [[{ text: "❌ HỦY", callback_data: "cancel-session" }]],
      });
      return;
    }
  } else if (state.step === "awaiting_script_url") {
    if (!isValidUrl(lower)) {
      await sendMessage(chatId, env, "URL Script không hợp lệ. Phải bắt đầu bằng http:// hoặc https://. Vui lòng gửi lại hoặc bấm HỦY.");
      return;
    }
    state.scriptUrl = lower;
  } else {
    await sendMessage(chatId, env, "Phiên hiện tại không hợp lệ. Vui lòng bấm /start để bắt đầu lại.");
    return;
  }

  await saveSessionState(userId, chatId, state, env);

  if (state.commandKeys.includes("update_solver") && !state.solverUrl) {
    return;
  }
  if (state.commandKeys.includes("update_script") && !state.scriptUrl) {
    return;
  }

  await sendSessionSummary(chatId, state, env);
}

async function sendSessionSummary(chatId, state, env) {
  const lines = [`Nhóm: ${state.target.toUpperCase()}`, "Lệnh:"];
  for (const key of state.commandKeys) {
    if (key === "update_solver") {
      lines.push(`- ${COMMANDS[key].value}: ${state.solverUrl}`);
    } else if (key === "update_script") {
      lines.push(`- ${COMMANDS[key].value}: ${state.scriptUrl}`);
    } else {
      lines.push(`- ${COMMANDS[key].value}`);
    }
  }
  lines.push("\nNhấn XÁC NHẬN GỬI để tiếp tục hoặc HỦY để dừng.");
  const replyMarkup = {
    inline_keyboard: [
      [{ text: "✅ XÁC NHẬN GỬI", callback_data: "confirm-session" }],
      [{ text: "❌ HỦY", callback_data: "cancel-session" }],
    ],
  };
  await sendMessage(chatId, env, lines.join("\n"), replyMarkup);
}


async function executeSessionCommands(
  chatId,
  state,
  env
) {
  try {
    const commandLines =
      orderedCommandKeys(
        state.commandKeys
      ).map((key) => {
        if (key === "update_solver") {
          return "UPDATE_SOLVER_SECURE";
        }

        if (key === "update_script") {
          if (
            !isValidUrl(
              state.scriptUrl
            )
          ) {
            throw new Error(
              "URL Script không hợp lệ."
            );
          }

          return `Updatescript ${state.scriptUrl}`;
        }

        return COMMANDS[key].value;
      });

    await requestCommandDispatch(
      chatId,
      {
        target: state.target,
        commandKeys:
          state.commandKeys,
        commandLines,
      },
      env,
      {
        confirmed: true,
      }
    );
  } catch (error) {
    await sendMessage(
      chatId,
      env,
      `❌ Không gửi được lệnh: ${error.message}`
    );
  } finally {
    await clearSessionState(
      state.userId,
      state.chatId
    );
  }
}

async function handleCallback(callback, chatId, messageId, env, fromId) {
  const data = callback.data || "";

  if (
    await handleRolloutCallback({
      data,
      callbackId: callback.id,
      chatId,
      env,
      ops: getRolloutOps(),
    })
  ) {
    return;
  }

  if (data === "show_health") {
    await answerCallback(
      callback.id,
      env
    );

    await showHealth(
      chatId,
      env,
      messageId
    );
    return;
  }

  if (data === "health_offline") {
    await answerCallback(
      callback.id,
      env
    );

    await showHealthList(
      chatId,
      "offline",
      env
    );
    return;
  }

  if (data === "health_issues") {
    await answerCallback(
      callback.id,
      env
    );

    await showHealthList(
      chatId,
      "issues",
      env
    );
    return;
  }

  if (data === "health_agent") {
    await answerCallback(
      callback.id,
      env
    );

    await showHealthList(
      chatId,
      "agent",
      env
    );
    return;
  }

  if (data === "alerts_status") {
    await answerCallback(
      callback.id,
      env
    );

    await configureAlerts(
      chatId,
      null,
      env
    );
    return;
  }

  if (
    data.startsWith(
      "revoke_ok:"
    )
  ) {
    const token = data.slice(
      "revoke_ok:".length
    );

    const pending =
      await loadPendingRevocation(
        token
      );

    if (!pending) {
      await answerCallback(
        callback.id,
        env,
        "Xác nhận đã hết hạn.",
        true
      );
      return;
    }

    await clearPendingRevocation(
      token
    );

    await answerCallback(
      callback.id,
      env,
      "Đang thu hồi thiết bị..."
    );

    await permanentlyRevokeDevice(
      chatId,
      pending.device_id,
      env
    );
    return;
  }

  if (
    data.startsWith(
      "revoke_cancel:"
    )
  ) {
    await clearPendingRevocation(
      data.slice(
        "revoke_cancel:".length
      )
    );

    await answerCallback(
      callback.id,
      env,
      "Đã hủy xóa thiết bị."
    );
    return;
  }

  if (
    data.startsWith(
      "dispatch_ok:"
    )
  ) {
    const token = data.slice(
      "dispatch_ok:".length
    );

    const pending =
      await loadPendingDispatch(
        token
      );

    if (!pending) {
      await answerCallback(
        callback.id,
        env,
        "Xác nhận đã hết hạn.",
        true
      );
      return;
    }

    await clearPendingDispatch(token);

    await answerCallback(
      callback.id,
      env,
      "Đang gửi lệnh..."
    );

    await requestCommandDispatch(
      chatId,
      pending,
      env,
      {
        confirmed: true,
      }
    );
    return;
  }

  if (
    data.startsWith(
      "dispatch_cancel:"
    )
  ) {
    await clearPendingDispatch(
      data.slice(
        "dispatch_cancel:".length
      )
    );

    await answerCallback(
      callback.id,
      env,
      "Đã hủy."
    );
    return;
  }

  if (
    data.startsWith(
      "devicecmd:"
    )
  ) {
    await answerCallback(
      callback.id,
      env
    );

    await showDeviceCommands(
      chatId,
      data.slice(
        "devicecmd:".length
      ),
      0,
      env,
      messageId
    );
    return;
  }


  if (
    data.startsWith(
      "reload_novagag2:"
    )
  ) {
    const deviceId =
      normalizeDeviceId(
        data.slice(
          "reload_novagag2:"
            .length
        )
      );

    if (!deviceId) {
      await answerCallback(
        callback.id,
        env,
        "Device ID không hợp lệ.",
        true
      );
      return;
    }

    const record =
      await getDeviceRecord(
        deviceId,
        env
      );

    if (!record) {
      await answerCallback(
        callback.id,
        env,
        "Không tìm thấy thiết bị.",
        true
      );
      return;
    }

    if (
      normalizeDeviceGroup(
        record.device_group
      ) !== "NOVA"
    ) {
      await answerCallback(
        callback.id,
        env,
        "Novagag2 chỉ dành cho NHÓM 2.",
        true
      );
      return;
    }

    if (
      record
        .reload_novagag2_capable !==
        true
    ) {
      await answerCallback(
        callback.id,
        env,
        "Máy chưa cập nhật Agent hỗ trợ lệnh này.",
        true
      );
      return;
    }

    if (
      Date.now() -
        Number(
          record.last_seen || 0
        ) >
        ONLINE_WINDOW_MS
    ) {
      await answerCallback(
        callback.id,
        env,
        "Máy đang offline.",
        true
      );
      return;
    }

    if (
      await isDeviceMaintenance(
        deviceId,
        env
      )
    ) {
      await answerCallback(
        callback.id,
        env,
        "Máy đang bảo trì.",
        true
      );
      return;
    }

    await answerCallback(
      callback.id,
      env,
      "Đang gửi lệnh nạp lại..."
    );

    const dispatched =
      await requestCommandDispatch(
        chatId,
        {
          deviceIds: [
            deviceId,
          ],
          commandKeys: [
            "reload_novagag2",
          ],
          commandLines: [
            "RELOAD_NOVAGAG2",
          ],
        },
        env
      );

    if (dispatched) {
      await sendMessage(
        chatId,
        env,
        "Sau khi tiến độ báo thành công, hãy đóng hẳn Roblox/Delta rồi mở lại."
      );
    }

    return;
  }

  if (data.startsWith("dpick:")) {
    const parts = data.split(":");
    const deviceId =
      normalizeDeviceId(parts[1]);
    const mask = Number(parts[2]);
    const commandKey = parts[3];

    if (
      !deviceId ||
      !Number.isInteger(mask) ||
      !COMMANDS[commandKey]
    ) {
      await answerCallback(
        callback.id,
        env,
        "Lựa chọn không hợp lệ.",
        true
      );
      return;
    }

    await answerCallback(
      callback.id,
      env
    );

    await showDeviceCommands(
      chatId,
      deviceId,
      toggleCommand(
        mask,
        commandKey
      ),
      env,
      messageId
    );
    return;
  }

  if (data.startsWith("dsend:")) {
    const parts = data.split(":");
    const deviceId =
      normalizeDeviceId(parts[1]);
    const mask = Number(parts[2]);
    const commandKeys =
      commandKeysFromMask(mask);

    if (
      !deviceId ||
      commandKeys.length === 0
    ) {
      await answerCallback(
        callback.id,
        env,
        "Bạn chưa chọn lệnh.",
        true
      );
      return;
    }

    if (commandKeys.includes("update_script")) {
      await answerCallback(
        callback.id,
        env,
        "Dùng lệnh /to để nhập URL Script.",
        true
      );
      return;
    }

    let commandLines;

    try {
      commandLines =
        buildCommandLinesFromKeys(
          commandKeys
        );
    } catch (error) {
      await answerCallback(
        callback.id,
        env,
        error.message,
        true
      );
      return;
    }

    await answerCallback(
      callback.id,
      env,
      "Đang chuẩn bị lệnh..."
    );

    await requestCommandDispatch(
      chatId,
      {
        deviceIds: [deviceId],
        commandKeys,
        commandLines,
      },
      env
    );
    return;
  }

  if (
    data.startsWith(
      "maintenance_toggle:"
    )
  ) {
    const deviceId =
      normalizeDeviceId(
        data.slice(
          "maintenance_toggle:".length
        )
      );

    if (!deviceId) {
      await answerCallback(
        callback.id,
        env,
        "Device ID không hợp lệ.",
        true
      );
      return;
    }

    const enabled =
      !(await isDeviceMaintenance(
        deviceId,
        env
      ));

    await answerCallback(
      callback.id,
      env
    );

    await changeDeviceMaintenance(
      chatId,
      deviceId,
      enabled,
      env,
      {
        silent: true,
      }
    );

    await showDeviceDetail(
      chatId,
      deviceId,
      env,
      messageId
    );
    return;
  }

  if (data.startsWith("device:")) {
    await answerCallback(
      callback.id,
      env
    );

    await showDeviceDetail(
      chatId,
      data.slice(
        "device:".length
      ),
      env,
      messageId
    );
    return;
  }

  if (data.startsWith("pair_approve:")) {
    await handlePairDecision(
      data.slice("pair_approve:".length),
      true,
      callback,
      chatId,
      messageId,
      env,
      fromId
    );
    return;
  }

  if (data.startsWith("pair_deny:")) {
    await handlePairDecision(
      data.slice("pair_deny:".length),
      false,
      callback,
      chatId,
      messageId,
      env,
      fromId
    );
    return;
  }

  if (data.startsWith("target:")) {
    const target = data.slice(7);
    if (!TARGET_FILES[target]) {
      await answerCallback(callback.id, env, "Nhóm không hợp lệ.", true);
      return;
    }

    await answerCallback(callback.id, env);
    await showCommands(chatId, target, 0, env, messageId);
    return;
  }

  if (data.startsWith("pick:")) {
    const [, target, maskText, commandKey] = data.split(":");
    const mask = Number(maskText);

    if (!TARGET_FILES[target] || !Number.isInteger(mask) || !COMMANDS[commandKey]) {
      await answerCallback(callback.id, env, "Lựa chọn không hợp lệ.", true);
      return;
    }

    const nextMask = toggleCommand(mask, commandKey);
    await answerCallback(callback.id, env);
    await showCommands(chatId, target, nextMask, env, messageId);
    return;
  }

  if (data.startsWith("send:")) {
    const [, target, maskText] = data.split(":");
    const mask = Number(maskText);
    const commandKeys = commandKeysFromMask(mask);

    if (!TARGET_FILES[target] || commandKeys.length === 0) {
      await answerCallback(callback.id, env, "Bạn chưa chọn lệnh nào.", true);
      return;
    }

    if (commandKeys.includes("update_script")) {
      await answerCallback(callback.id, env);
      await startUrlSession(chatId, fromId, target, commandKeys, env);
      return;
    }


    await answerCallback(callback.id, env, "Đang gửi lệnh...");
    await executeCommands(chatId, target, commandKeys, env);
    return;
  }

  if (data.startsWith("confirm:")) {
    const [, target, maskText] = data.split(":");
    const mask = Number(maskText);
    const commandKeys = commandKeysFromMask(mask);
    if (!TARGET_FILES[target] || commandKeys.length === 0) {
      await answerCallback(callback.id, env, "Không tìm thấy lệnh để gửi.", true);
      return;
    }
    await answerCallback(callback.id, env, "Đang gửi lệnh...");
    await executeCommands(chatId, target, commandKeys, env);
    return;
  }

  if (data === "confirm-session") {
    await answerCallback(callback.id, env, "Đang gửi lệnh...");
    const state = await loadSessionState(fromId, chatId);
    if (!state || !state.commandKeys || !state.target) {
      await answerCallback(callback.id, env, "Phiên đã hết hạn hoặc không hợp lệ.", true);
      return;
    }
    await executeSessionCommands(chatId, state, env);
    return;
  }

  if (data === "cancel-session") {
    await clearSessionState(fromId, chatId);
    await answerCallback(callback.id, env, "Đã hủy.");
    return;
  }

  if (data.startsWith("cancel:")) {
    const parts = data.split(":");
    if (parts.length === 2) {
      const token = parts[1];
      await clearShortCommand(token);
      await answerCallback(callback.id, env, "Đã hủy.");
      return;
    }
    await answerCallback(callback.id, env, "Đã hủy.");
    return;
  }

  if (data.startsWith("clear:")) {
    const target = data.slice(6);
    if (!TARGET_FILES[target]) {
      await answerCallback(callback.id, env, "Nhóm không hợp lệ.", true);
      return;
    }

    await answerCallback(callback.id, env, "Đã bỏ chọn.");
    await showCommands(chatId, target, 0, env, messageId);
    return;
  }

  if (data.startsWith("shortcmd:")) {
    const parts = data.split(":");
    if (parts.length !== 4) {
      await answerCallback(callback.id, env, "Dữ liệu không hợp lệ.", true);
      return;
    }
    const [, command, token, target] = parts;
    if (!["solver", "script"].includes(command) || !TARGET_FILES[target]) {
      await answerCallback(callback.id, env, "Lựa chọn không hợp lệ.", true);
      return;
    }

    const stored = await loadShortCommand(token);
    if (!stored || stored.command !== command) {
      await answerCallback(callback.id, env, "Lệnh đã hết hạn. Vui lòng thử lại.", true);
      return;
    }

    await answerCallback(callback.id, env);
    await clearShortCommand(token);
    if (command === "solver") {
      await executeSolverCommand(chatId, target, stored.url, env);
      return;
    }
    await executeScriptCommand(chatId, target, stored.url, env);
    return;
  }

  if (data === "status") {
    await answerCallback(callback.id, env);
    await sendStatus(chatId, env);
    return;
  }

  if (data === "menu") {
    await answerCallback(callback.id, env);
    await showTargets(chatId, env, messageId);
    return;
  }

  if (data === "show_devices") {
    await answerCallback(callback.id, env);
    await showDevices(chatId, env);
    return;
  }

  if (data === "show_progress") {
    await answerCallback(callback.id, env);
    await sendProgress(chatId, env);
    return;
  }

  await answerCallback(callback.id, env, "Nút không hợp lệ.", true);
}

function toggleCommand(mask, commandKey) {
  const command = COMMANDS[commandKey];

  if (commandKey === "idle") {
    return mask === command.bit ? 0 : command.bit;
  }

  const maskWithoutIdle = mask & ~COMMANDS.idle.bit;
  return maskWithoutIdle ^ command.bit;
}

function commandKeysFromMask(mask) {
  if (!Number.isInteger(mask) || mask <= 0) return [];

  if (mask & COMMANDS.idle.bit) {
    return ["idle"];
  }

  return COMMAND_ORDER.filter(
    (key) => key !== "idle" && (mask & COMMANDS[key].bit) !== 0
  );
}



async function showTargets(
  chatId,
  env,
  messageId
) {
  const marmotLabel =
    await getGroupLabel(
      "MARMOT",
      env
    );

  const novaLabel =
    await getGroupLabel(
      "NOVA",
      env
    );

  const textValue =
    "Chọn nhóm, thiết bị hoặc chức năng quản lý:";

  const replyMarkup = {
    inline_keyboard: [
      [
        {
          text: "🌍 TẤT CẢ",
          callback_data:
            "target:all",
        },
        {
          text: `1️⃣ ${marmotLabel}`,
          callback_data:
            "target:marmot",
        },
      ],
      [
        {
          text: `2️⃣ ${novaLabel}`,
          callback_data:
            "target:nova",
        },
        {
          text: "🎯 TỪNG MÁY",
          callback_data:
            "show_devices",
        },
      ],
      [
        {
          text: "🚀 TRIỂN KHAI",
          callback_data:
            "show_rollout",
        },
      ],
      [
        {
          text: "🏥 SỨC KHỎE",
          callback_data:
            "show_health",
        },
        {
          text: "📊 TIẾN ĐỘ",
          callback_data:
            "show_progress",
        },
      ],
      [
        {
          text: "📋 TRẠNG THÁI",
          callback_data:
            "status",
        },
        {
          text: "🔔 CẢNH BÁO",
          callback_data:
            "alerts_status",
        },
      ],
      [
        {
          text: "🎛 AOT HUB",
          web_app: {
            url: aotHubPublicUrl(env),
          },
        },
      ],
    ],
  };

  if (messageId) {
    await editMessage(
      chatId,
      messageId,
      env,
      textValue,
      replyMarkup
    );
  } else {
    await sendMessage(
      chatId,
      env,
      textValue,
      replyMarkup
    );
  }
}

async function showCommands(chatId, target, mask, env, messageId) {
  const label = await getTargetLabel(target, env);
  const selected = commandKeysFromMask(mask);
  const selectedText = selected.length
    ? selected.map((key) => COMMANDS[key].value).join(", ")
    : "Chưa chọn";

  const button = (key) => ({
    text: `${mask & COMMANDS[key].bit ? "✅" : "⬜"} ${COMMANDS[key].label}`,
    callback_data: `pick:${target}:${mask}:${key}`,
  });

  const textValue =
    `Nhóm đã chọn: ${label}\n` +
    `Đã chọn: ${selectedText}\n\n` +
    "Chạm để chọn/bỏ chọn, sau đó bấm GỬI LỆNH.";

  const replyMarkup = {
    inline_keyboard: [
      [button("idle"), button("setup_vip")],
      [button("install_track"), button("setup_boot")],
      [button("setup_caylapbu"), button("run_caylapbu")],
      [button("update_delta"), button("update_solver")],
      [button("update_script"), button("reboot")],
      [
        {
          text: `✅ GỬI ${selected.length} LỆNH`,
          callback_data: `send:${target}:${mask}`,
        },
        { text: "🗑 BỎ CHỌN", callback_data: `clear:${target}` },
      ],
      [
        { text: "⬅️ Đổi nhóm", callback_data: "menu" },
        { text: "📋 Trạng thái", callback_data: "status" },
      ],
    ],
  };

  if (messageId) {
    await editMessage(chatId, messageId, env, textValue, replyMarkup);
  } else {
    await sendMessage(chatId, env, textValue, replyMarkup);
  }
}


async function executeCommands(
  chatId,
  target,
  commandKeys,
  env,
  options = {}
) {
  if (
    !TARGET_FILES[target] ||
    !Array.isArray(commandKeys) ||
    commandKeys.length === 0
  ) {
    await sendMessage(
      chatId,
      env,
      "Target hoặc lệnh không hợp lệ."
    );
    return;
  }

  let commandLines;

  try {
    commandLines =
      buildCommandLinesFromKeys(
        commandKeys
      );
  } catch (error) {
    await sendMessage(
      chatId,
      env,
      `❌ ${error.message}`
    );
    return;
  }

  await requestCommandDispatch(
    chatId,
    {
      target,
      commandKeys,
      commandLines,
    },
    env,
    options
  );
}

async function sendStatus(chatId, env) {
  const lines = [
    "📋 Lệnh hiện tại trên GitHub:",
  ];

  for (
    const [target, file]
    of Object.entries(TARGET_FILES)
  ) {
    const label =
      await getTargetLabel(
        target,
        env
      );

    try {
      const data =
        await getGitHubFile(
          file,
          env
        );

      const content =
        decodeBase64(
          data.content || ""
        ).trim() || "(trống)";

      lines.push(
        `\n${label} — ${file}\n${content}`
      );
    } catch (error) {
      lines.push(
        `\n${label} — lỗi: ${error.message}`
      );
    }
  }

  await sendMessage(
    chatId,
    env,
    lines.join("\n")
  );
}

async function getGitHubFile(path, env) {
  const response = await fetch(
    `https://api.github.com/repos/${OWNER}/${REPO}/contents/${encodeURIComponent(path)}?ref=${BRANCH}`,
    {
      headers: githubHeaders(env),
    }
  );

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.message || `GitHub GET ${response.status}`);
  }
  return data;
}

async function updateGitHubFile(
  path,
  content,
  message,
  env,
  currentFile = null
) {
  const current =
    currentFile ||
    await getGitHubFile(
      path,
      env
    );
  const response = await fetch(
    `https://api.github.com/repos/${OWNER}/${REPO}/contents/${encodeURIComponent(path)}`,
    {
      method: "PUT",
      headers: githubHeaders(env),
      body: JSON.stringify({
        message,
        content: encodeBase64(content),
        sha: current.sha,
        branch: BRANCH,
      }),
    }
  );

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.message || `GitHub PUT ${response.status}`);
  }
  return data.commit.sha;
}

function githubHeaders(env) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    "X-GitHub-Api-Version": "2026-03-10",
    "User-Agent": "aotscript-cloudflare-worker",
    "Content-Type": "application/json",
  };
}

function isValidUrl(u) {
  try {
    const parsed = new URL(u);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch (e) {
    return false;
  }
}


async function executeSolverCommand(chatId, target, _url, env) {
  await requestCommandDispatch(
    chatId,
    {
      target,
      commandKeys: ["update_solver"],
      commandLines: ["UPDATE_SOLVER_SECURE"],
    },
    env
  );
}

async function executeScriptCommand(
  chatId,
  target,
  url,
  env
) {
  await requestCommandDispatch(
    chatId,
    {
      target,
      commandKeys:
        ["update_script"],
      commandLines:
        [`Updatescript ${url}`],
    },
    env
  );
}

async function sendMessage(chatId, env, textValue, replyMarkup) {
  return telegram(env, "sendMessage", {
    chat_id: chatId,
    text: textValue,
    reply_markup: replyMarkup,
  });
}

async function editMessage(chatId, messageId, env, textValue, replyMarkup) {
  return telegram(env, "editMessageText", {
    chat_id: chatId,
    message_id: messageId,
    text: textValue,
    reply_markup: replyMarkup,
  });
}

async function answerCallback(callbackQueryId, env, textValue, showAlert = false) {
  return telegram(env, "answerCallbackQuery", {
    callback_query_id: callbackQueryId,
    ...(textValue ? { text: textValue, show_alert: showAlert } : {}),
  });
}

async function telegram(env, method, payload) {
  const response = await fetch(
    `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );

  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.description || `Telegram ${response.status}`);
  }
  return data.result;
}

function encodeBase64(value) {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function decodeBase64(value) {
  const clean = value.replace(/\n/g, "");
  const binary = atob(clean);
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

// -------------------- Agent reporting and device helpers --------------------



function isAuthorizedAgentRequest(request, env) {
  const expected = String(env.AGENT_REPORT_SECRET || "");
  const provided = String(request.headers.get("X-Agent-Secret") || "");
  return expected.length > 0 && provided === expected;
}

async function handleSecureSolverUrl(request, env) {
  if (!isAuthorizedAgentRequest(request, env)) {
    return new Response("Unauthorized", { status: 401 });
  }
  const solverUrl = String(env.SOLVER_UPDATE_URL || "").trim();
  if (!isValidUrl(solverUrl)) {
    return noStoreJson({ ok: false, error: "solver_not_configured" }, 503);
  }
  return noStoreJson({ ok: true, solver_url: solverUrl });
}

async function handleAgentReport(request, env) {
  const secret =
    request.headers.get(
      "X-Agent-Secret"
    );
  if (
    !secret ||
    secret !==
      env.AGENT_REPORT_SECRET
  ) {
    return new Response(
      "Unauthorized",
      { status: 401 }
    );
  }
  let body;
  try {
    body = await request.json();
  } catch (error) {
    return json(
      {
        ok: false,
        error: "invalid_json",
      },
      400
    );
  }
  const {
    device_id:
      reportedDeviceId,
    device_group:
      reportedDeviceGroup,
    timestamp,
    status,
    command_id,
    command,
    command_index,
    command_total,
    last_result,
  } = body;
  if (
    !reportedDeviceId ||
    !reportedDeviceGroup ||
    !timestamp ||
    !status
  ) {
    return json(
      {
        ok: false,
        error: "missing_fields",
      },
      400
    );
  }
  if (
    !ALLOWED_REPORT_STATUSES
      .includes(status)
  ) {
    return json(
      {
        ok: false,
        error: "invalid_status",
      },
      400
    );
  }
  const deviceId =
    normalizeDeviceId(
      reportedDeviceId
    );
  const deviceGroup =
    normalizeDeviceGroup(
      reportedDeviceGroup
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
  if (!deviceGroup) {
    return json(
      {
        ok: false,
        error:
          "invalid_device_group",
      },
      400
    );
  }
  const legacyPrefix =
    deviceId.match(
      /^(MARMOT|NOVA)-/
    );
  if (
    legacyPrefix &&
    legacyPrefix[1] !==
      deviceGroup
  ) {
    return json(
      {
        ok: false,
        error:
          "group_mismatch",
      },
      400
    );
  }
  const payload = {
    device_id: deviceId,
    device_group: deviceGroup,
    timestamp,
    status,
  };
  if (command_id) {
    payload.command_id =
      command_id;
    payload.command =
      command ?? null;
    payload.command_index =
      command_index ?? null;
    payload.command_total =
      command_total ?? null;
  }
  if (last_result) {
    payload.last_result =
      String(last_result)
        .slice(0, 500);
  }
  const batteryNumber =
    Number(body.battery_level);
  if (
    Number.isFinite(
      batteryNumber
    ) &&
    batteryNumber >= 0 &&
    batteryNumber <= 100
  ) {
    payload.battery_level =
      Math.round(
        batteryNumber
      );
  }
  if (
    typeof body.charging ===
      "boolean"
  ) {
    payload.charging =
      body.charging;
  }
  const androidLabel =
    sanitizeLabel(
      body.android_version,
      32
    );
  if (androidLabel) {
    payload.android_version =
      androidLabel;
  }
  const agentLabel =
    sanitizeLabel(
      body.agent_version,
      64
    );
  if (agentLabel) {
    payload.agent_version =
      agentLabel;
  }

  if (
    typeof body
      .reload_novagag2_capable ===
      "boolean"
  ) {
    payload
      .reload_novagag2_capable =
      body
        .reload_novagag2_capable;
  }

  if (typeof body.secure_solver_capable === "boolean") {
    payload.secure_solver_capable = body.secure_solver_capable;
  } else if (status === "heartbeat") {
    payload.secure_solver_capable = false;
  }

  if (
    typeof body.self_heal_capable ===
      "boolean"
  ) {
    payload.self_heal_capable =
      body.self_heal_capable;
  } else if (
    status === "heartbeat"
  ) {
    payload.self_heal_capable = false;
  }

  if (
    typeof body.deferred_command_queue_capable ===
      "boolean"
  ) {
    payload.deferred_command_queue_capable =
      body.deferred_command_queue_capable;
  } else if (
    status === "heartbeat"
  ) {
    payload.deferred_command_queue_capable = false;
  }

  const uptimeNumber =
    Number(body.uptime_seconds);
  if (
    Number.isFinite(
      uptimeNumber
    ) &&
    uptimeNumber >= 0
  ) {
    payload.uptime_seconds =
      Math.floor(
        uptimeNumber
      );
  }
  const storageNumber =
    Number(
      body.storage_free_bytes
    );
  if (
    Number.isFinite(
      storageNumber
    ) &&
    storageNumber >= 0
  ) {
    payload.storage_free_bytes =
      Math.floor(
        storageNumber
      );
  }
  try {
    const result =
      await reportFleetDevice(
        env,
        payload
      );
    if (
      result.response.status ===
        410
    ) {
      return noStoreJson(
        {
          ok: false,
          error:
            "device_revoked",
          device_id: deviceId,
        },
        410
      );
    }
    if (!result.response.ok) {
      return noStoreJson(
        {
          ok: false,
          error:
            result.data?.error ||
            "fleet_state_failed",
        },
        result.response.status
      );
    }
    return noStoreJson({
      ok: true,
      device_id: deviceId,
      device_group:
        deviceGroup,
    });
  } catch (error) {
    console.error(
      "agent_report_failed",
      error?.message || error
    );
    return noStoreJson(
      {
        ok: false,
        error:
          String(
            error?.message ||
            error
          ),
      },
      500
    );
  }
}



async function getDeviceRecord(
  deviceId,
  env
) {
  try {
    const normalized =
      normalizeDeviceId(
        deviceId
      );
    if (!normalized) {
      return null;
    }
    if (
      await isDeviceRevoked(
        normalized,
        env
      )
    ) {
      return null;
    }
    try {
      const durableRecord =
        await getFleetDeviceRecord(
          env,
          normalized
        );
      if (durableRecord) {
        return durableRecord;
      }
    } catch (error) {
      console.error(
        "fleet_state_read_failed",
        error?.message || error
      );
    }
    const raw =
      await env.DEVICE_STATUS.get(
        normalized
      );
    if (!raw) return null;
    const record =
      JSON.parse(raw);
    return (
      record &&
      typeof record ===
        "object"
    )
      ? record
      : null;
  } catch (error) {
    return null;
  }
}

function formatTimestamp(ms) {
  try {
    return new Date(ms).toISOString().replace('T',' ').replace('Z',' UTC');
  } catch (e) {
    return '-';
  }
}

function formatRelativeDeviceTime(ms, now = Date.now()) {
  const timestamp = Number(ms);

  if (!Number.isFinite(timestamp) || timestamp <= 0) {
    return "chưa rõ";
  }

  const elapsed = Math.max(0, now - timestamp);

  if (elapsed < 15 * 1000) {
    return "vừa xong";
  }

  if (elapsed < 60 * 1000) {
    return `${Math.floor(elapsed / 1000)} giây trước`;
  }

  if (elapsed < 60 * 60 * 1000) {
    return `${Math.floor(elapsed / (60 * 1000))} phút trước`;
  }

  if (elapsed < 24 * 60 * 60 * 1000) {
    return `${Math.floor(elapsed / (60 * 60 * 1000))} giờ trước`;
  }

  if (elapsed < 7 * 24 * 60 * 60 * 1000) {
    return `${Math.floor(elapsed / (24 * 60 * 60 * 1000))} ngày trước`;
  }

  try {
    return new Intl.DateTimeFormat(
      "vi-VN",
      {
        timeZone: "Asia/Ho_Chi_Minh",
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }
    ).format(new Date(timestamp));
  } catch (error) {
    return "chưa rõ";
  }
}

function deviceStatusLabel(record, online) {
  if (!online) {
    return "Mất kết nối";
  }

  const status =
    record?.last_status ||
    record?.last_report_status ||
    "heartbeat";

  switch (status) {
    case "received":
      return "Đã nhận lệnh";

    case "running":
      return "Đang chạy lệnh";

    case "success":
      return "Đã hoàn tất lệnh";

    case "error":
      return "Có lỗi";

    case "expired":
      return "Lệnh đã hết hạn";

    case "heartbeat":
    default:
      return "Đang kết nối";
  }
}



function formatByteCount(value) {
  const bytes = Number(value);

  if (
    !Number.isFinite(bytes) ||
    bytes < 0
  ) {
    return "chưa rõ";
  }

  if (bytes >= 1024 ** 3) {
    return (
      `${(bytes / 1024 ** 3).toFixed(1)} GB`
    );
  }

  if (bytes >= 1024 ** 2) {
    return (
      `${(bytes / 1024 ** 2).toFixed(0)} MB`
    );
  }

  return `${Math.round(bytes / 1024)} KB`;
}

function formatUptimeSeconds(value) {
  const seconds = Number(value);

  if (
    !Number.isFinite(seconds) ||
    seconds < 0
  ) {
    return "chưa rõ";
  }

  const days =
    Math.floor(seconds / 86400);

  const hours =
    Math.floor(
      (seconds % 86400) / 3600
    );

  const minutes =
    Math.floor(
      (seconds % 3600) / 60
    );

  const parts = [];

  if (days > 0) {
    parts.push(`${days} ngày`);
  }

  if (hours > 0) {
    parts.push(`${hours} giờ`);
  }

  parts.push(`${minutes} phút`);

  return parts.join(" ");
}

async function changeDeviceMaintenance(
  chatId,
  rawDeviceId,
  enabled,
  env,
  options = {}
) {
  const deviceId =
    normalizeDeviceId(rawDeviceId);

  if (!deviceId) {
    await sendMessage(
      chatId,
      env,
      "Device ID không hợp lệ."
    );
    return false;
  }

  const record =
    await getDeviceRecord(
      deviceId,
      env
    );

  if (!record) {
    await sendMessage(
      chatId,
      env,
      `Không tìm thấy ${deviceId}.`
    );
    return false;
  }

  if (
    record.agent_version !==
    TARGETING_AGENT_VERSION
  ) {
    await sendMessage(
      chatId,
      env,
      `${deviceId} chưa cập nhật Agent hỗ trợ bảo trì. ` +
      "Hãy chạy lại msetup và chờ heartbeat."
    );
    return false;
  }

  await setDeviceMaintenance(
    deviceId,
    enabled,
    env
  );

  if (!options.silent) {
    await sendMessage(
      chatId,
      env,
      enabled
        ? `🛠 Đã bật bảo trì cho ${deviceId}.`
        : `✅ Đã tắt bảo trì cho ${deviceId}.`
    );
  }

  return true;
}

async function showDeviceDetail(
  chatId,
  rawDeviceId,
  env,
  messageId
) {
  const deviceId =
    normalizeDeviceId(rawDeviceId);

  if (!deviceId) {
    await sendMessage(
      chatId,
      env,
      "Device ID không hợp lệ."
    );
    return;
  }

  const record =
    await getDeviceRecord(
      deviceId,
      env
    );

  if (!record) {
    await sendMessage(
      chatId,
      env,
      `Không tìm thấy ${deviceId}.`
    );
    return;
  }

  const now = Date.now();

  const online =
    now - Number(
      record.last_seen || 0
    ) <= ONLINE_WINDOW_MS;

  const maintenance =
    await isDeviceMaintenance(
      deviceId,
      env
    );

  const name =
    await deviceDisplayName(
      deviceId,
      env
    );

  const groupLabel =
    await getGroupLabel(
      record.device_group,
      env
    );

  const batteryNumber =
    Number(record.battery_level);

  const battery =
    Number.isFinite(batteryNumber)
      ? `${batteryNumber}%`
      : "chưa rõ";

  const charging =
    record.charging === true
      ? " — đang sạc"
      : record.charging === false
        ? " — không sạc"
        : "";

  const compatible =
    record.agent_version ===
    TARGETING_AGENT_VERSION;

  const resultText =
    String(
      record.last_result ||
      "Không có"
    ).slice(0, 180);

  const textValue =
    `${online ? "🟢" : "⚪"} ${name}\n` +
    `ID: ${deviceId}\n` +
    `Nhóm: ${groupLabel}\n` +
    `Chế độ: ${maintenance ? "🛠 Bảo trì" : "Hoạt động"}\n` +
    `Kết nối: ${online ? "Đang online" : "Mất kết nối"}\n` +
    `Hoạt động: ${formatRelativeDeviceTime(record.last_seen, now)}\n\n` +
    `Pin: ${battery}${charging}\n` +
    `Android: ${record.android_version || "chưa rõ"}\n` +
    `Agent: ${record.agent_version || "bản cũ"}\n` +
    `Gửi riêng: ${compatible ? "Sẵn sàng" : "Chưa cập nhật"}\n` +
    `Uptime: ${formatUptimeSeconds(record.uptime_seconds)}\n` +
    `Bộ nhớ trống: ${formatByteCount(record.storage_free_bytes)}\n\n` +
    `Lệnh cuối: ${record.last_command || "-"}\n` +
    `Trạng thái: ${deviceStatusLabel(record, online)}\n` +
    `Kết quả: ${resultText}`;

  const replyMarkup = {
    inline_keyboard: [
      [
        {
          text: "🎯 GỬI LỆNH",
          callback_data:
            `devicecmd:${deviceId}`,
        },
        {
          text: maintenance
            ? "✅ TẮT BẢO TRÌ"
            : "🛠 BẬT BẢO TRÌ",
          callback_data:
            `maintenance_toggle:${deviceId}`,
        },
      ],
      [
        {
          text: "⬅️ DANH SÁCH",
          callback_data:
            "show_devices",
        },
      ],
    ],
  };

  if (messageId) {
    await editMessage(
      chatId,
      messageId,
      env,
      textValue,
      replyMarkup
    );
  } else {
    await sendMessage(
      chatId,
      env,
      textValue,
      replyMarkup
    );
  }
}


async function showDeviceCommands(
  chatId,
  rawDeviceId,
  mask,
  env,
  messageId
) {
  const deviceId =
    normalizeDeviceId(rawDeviceId);

  const record =
    deviceId
      ? await getDeviceRecord(
          deviceId,
          env
        )
      : null;

  if (!deviceId || !record) {
    await sendMessage(
      chatId,
      env,
      "Không tìm thấy thiết bị."
    );
    return;
  }

  if (
    record.agent_version !==
    TARGETING_AGENT_VERSION
  ) {
    await sendMessage(
      chatId,
      env,
      `${deviceId} chưa cập nhật Agent hỗ trợ gửi riêng.`
    );
    return;
  }

  if (
    await isDeviceMaintenance(
      deviceId,
      env
    )
  ) {
    await sendMessage(
      chatId,
      env,
      `${deviceId} đang bảo trì nên không nhận lệnh mới.`
    );
    return;
  }

  const name =
    await deviceDisplayName(
      deviceId,
      env
    );

  const selected =
    commandKeysFromMask(mask);

  const button = (key) => ({
    text:
      `${mask & COMMANDS[key].bit ? "✅" : "⬜"} ` +
      `${COMMANDS[key].label}`,
    callback_data:
      `dpick:${deviceId}:${mask}:${key}`,
  });

  const canReloadNovagag2 =
    normalizeDeviceGroup(
      record.device_group
    ) === "NOVA" &&
    record
      .reload_novagag2_capable ===
      true;

  const textValue =
    `Máy đã chọn: ${name}\n` +
    `ID: ${deviceId}\n` +
    `Đã chọn: ${
      selected.length
        ? selected
            .map(
              (key) =>
                COMMANDS[key].value
            )
            .join(", ")
        : "Chưa chọn"
    }`;

  const rows = [
    [
      button("idle"),
      button("setup_vip"),
    ],
    [
      button("install_track"),
      button("setup_boot"),
    ],
    [
      button("setup_caylapbu"),
      button("run_caylapbu"),
    ],
    [
      button("update_delta"),
      button("reboot"),
    ],
    [
      button("update_solver"),
      button("update_script"),
    ],
  ];

  if (canReloadNovagag2) {
    rows.push([
      {
        text:
          "🔄 NẠP LẠI NOVAGAG2",
        callback_data:
          `reload_novagag2:${deviceId}`,
      },
    ]);
  }

  rows.push(
    [
      {
        text:
          `✅ GỬI ${selected.length} LỆNH`,
        callback_data:
          `dsend:${deviceId}:${mask}`,
      },
    ],
    [
      {
        text:
          "⬅️ CHI TIẾT MÁY",
        callback_data:
          `device:${deviceId}`,
      },
    ]
  );

  const replyMarkup = {
    inline_keyboard: rows,
  };

  if (messageId) {
    await editMessage(
      chatId,
      messageId,
      env,
      textValue,
      replyMarkup
    );
  } else {
    await sendMessage(
      chatId,
      env,
      textValue,
      replyMarkup
    );
  }
}

function healthStateKey(deviceId) {
  return `${HEALTH_STATE_PREFIX}${deviceId}`;
}

async function alertsEnabled(env) {
  const value =
    await env.DEVICE_STATUS.get(
      ALERTS_SETTING_KEY
    );

  return value !== "0";
}

async function configureAlerts(
  chatId,
  enabled,
  env
) {
  if (enabled === null) {
    const current =
      await alertsEnabled(env);

    await sendMessage(
      chatId,
      env,
      `🔔 CẢNH BÁO TỰ ĐỘNG\n\n` +
        `Trạng thái: ${current ? "ĐANG BẬT" : "ĐANG TẮT"}\n` +
        "Máy offline: báo sau 3 phút\n" +
        "Pin yếu: từ 15% trở xuống\n" +
        "Bộ nhớ thấp: dưới 2 GB\n" +
        `Agent chuẩn: ${TARGETING_AGENT_VERSION}\n` +
        "Giờ yên lặng: 23:00–07:00\n\n" +
        "Bật: /alerts on\n" +
        "Tắt: /alerts off"
    );
    return;
  }

  await env.DEVICE_STATUS.put(
    ALERTS_SETTING_KEY,
    enabled ? "1" : "0"
  );

  if (enabled) {
    await deleteKvPrefix(
      HEALTH_STATE_PREFIX,
      env
    );

    await env.DEVICE_STATUS.delete(
      HEALTH_BOOTSTRAP_KEY
    );
  }

  await sendMessage(
    chatId,
    env,
    enabled
      ? "🔔 Đã bật cảnh báo tự động. Lần quét đầu sẽ tạo mốc ban đầu để tránh gửi hàng loạt cảnh báo cũ."
      : "🔕 Đã tắt cảnh báo tự động."
  );
}

async function countKvPrefix(
  prefix,
  env
) {
  let count = 0;
  let cursor;

  do {
    const page =
      await env.DEVICE_STATUS.list({
        prefix,
        limit: 1000,
        ...(cursor ? { cursor } : {}),
      });

    count +=
      (page.keys || []).length;

    cursor =
      page.list_complete
        ? undefined
        : page.cursor;
  } while (cursor);

  return count;
}

function vietnamHourNow() {
  try {
    const parts =
      new Intl.DateTimeFormat(
        "en-GB",
        {
          timeZone:
            "Asia/Ho_Chi_Minh",
          hour: "2-digit",
          hour12: false,
        }
      ).formatToParts(
        new Date()
      );

    const hourPart =
      parts.find(
        (part) =>
          part.type === "hour"
      );

    return Number(
      hourPart?.value
    );
  } catch (error) {
    return -1;
  }
}

function isQuietHour() {
  const hour = vietnamHourNow();

  if (
    !Number.isInteger(hour) ||
    hour < 0
  ) {
    return false;
  }

  return (
    hour >= QUIET_HOUR_START ||
    hour < QUIET_HOUR_END
  );
}

function formatHealthDuration(value) {
  const milliseconds =
    Math.max(
      0,
      Number(value) || 0
    );

  const minutes =
    Math.floor(
      milliseconds / 60000
    );

  if (minutes < 1) {
    return "dưới 1 phút";
  }

  if (minutes < 60) {
    return `${minutes} phút`;
  }

  const hours =
    Math.floor(
      minutes / 60
    );

  const remainingMinutes =
    minutes % 60;

  if (hours < 24) {
    return remainingMinutes
      ? `${hours} giờ ${remainingMinutes} phút`
      : `${hours} giờ`;
  }

  const days =
    Math.floor(hours / 24);

  const remainingHours =
    hours % 24;

  return remainingHours
    ? `${days} ngày ${remainingHours} giờ`
    : `${days} ngày`;
}

function initialHealthState(
  device,
  now,
  suppressCurrentIssues
) {
  return {
    version: 1,
    observed_online:
      device.online,
    offline_since:
      device.online
        ? null
        : now,
    offline_alert_sent:
      false,
    low_battery_alerted:
      suppressCurrentIssues
        ? device.lowBattery
        : false,
    low_storage_alerted:
      suppressCurrentIssues
        ? device.lowStorage
        : false,
    agent_old_alerted:
      suppressCurrentIssues
        ? device.oldAgent
        : false,
    last_error_alerted_key:
      suppressCurrentIssues
        ? device.errorKey
        : null,
    updated_at: now,
  };
}

async function loadHealthState(
  deviceId,
  env
) {
  try {
    const raw =
      await env.DEVICE_STATUS.get(
        healthStateKey(deviceId)
      );

    if (!raw) return null;

    const value =
      JSON.parse(raw);

    return (
      value &&
      typeof value === "object"
    )
      ? value
      : null;
  } catch (error) {
    return null;
  }
}


function healthStateChanged(
  current,
  next
) {
  const left = {
    ...(current || {}),
  };
  const right = {
    ...(next || {}),
  };
  delete left.updated_at;
  delete right.updated_at;
  return (
    JSON.stringify(left) !==
    JSON.stringify(right)
  );
}

async function saveHealthState(
  deviceId,
  state,
  env
) {
  await env.DEVICE_STATUS.put(
    healthStateKey(deviceId),
    JSON.stringify(state)
  );
}

async function collectFleetHealth(
  env,
  includeRevoked = true
) {
  const now = Date.now();

  const ids =
    await deviceIdsForTarget(
      "all",
      env
    );

  const devices = [];
  const groupLabels = new Map();

  for (const id of ids) {
    const record =
      await getDeviceRecord(
        id,
        env
      );

    if (!record) continue;

    const maintenance =
      await isDeviceMaintenance(
        id,
        env
      );

    const name =
      await deviceDisplayName(
        id,
        env
      );

    const group =
      normalizeDeviceGroup(
        record.device_group
      );

    if (
      group &&
      !groupLabels.has(group)
    ) {
      groupLabels.set(
        group,
        await getGroupLabel(
          group,
          env
        )
      );
    }

    const lastSeen =
      Number(
        record.last_seen || 0
      );

    const online =
      lastSeen > 0 &&
      now - lastSeen <=
        ONLINE_WINDOW_MS;

    const batteryNumber =
      Number(
        record.battery_level
      );

    const batteryLevel =
      Number.isFinite(
        batteryNumber
      )
        ? batteryNumber
        : null;

    const storageNumber =
      Number(
        record.storage_free_bytes
      );

    const storageFreeBytes =
      Number.isFinite(
        storageNumber
      )
        ? storageNumber
        : null;

    const lowBattery =
      batteryLevel !== null &&
      batteryLevel <=
        LOW_BATTERY_PERCENT;

    const lowStorage =
      storageFreeBytes !== null &&
      storageFreeBytes <
        LOW_STORAGE_BYTES;

    const oldAgent =
      record.agent_version !==
        TARGETING_AGENT_VERSION;

    const commandTimestamp =
      Number(
        record.last_command_ts || 0
      );

    const recentError =
      record.last_status === "error" &&
      commandTimestamp > 0 &&
      now - commandTimestamp <=
        RECENT_ERROR_WINDOW_MS;

    const errorKey =
      recentError
        ? (
            `${
              record.last_command_id ||
              "unknown"
            }:${commandTimestamp}`
          )
        : null;

    devices.push({
      id,
      name,
      group:
        groupLabels.get(group) ||
        group ||
        "-",
      record,
      maintenance,
      lastSeen,
      online,
      batteryLevel,
      storageFreeBytes,
      lowBattery,
      lowStorage,
      oldAgent,
      recentError,
      errorKey,
    });
  }

  const revoked =
    includeRevoked
      ? await countKvPrefix(
          REVOKED_PREFIX,
          env
        )
      : 0;

  const issues =
    devices.filter(
      (device) =>
        device.lowBattery ||
        device.lowStorage ||
        device.oldAgent ||
        device.recentError
    ).length;

  return {
    now,
    revoked,
    devices,
    counts: {
      total:
        devices.length,
      online:
        devices.filter(
          (device) =>
            device.online
        ).length,
      offline:
        devices.filter(
          (device) =>
            !device.online
        ).length,
      maintenance:
        devices.filter(
          (device) =>
            device.maintenance
        ).length,
      lowBattery:
        devices.filter(
          (device) =>
            device.lowBattery
        ).length,
      lowStorage:
        devices.filter(
          (device) =>
            device.lowStorage
        ).length,
      oldAgent:
        devices.filter(
          (device) =>
            device.oldAgent
        ).length,
      recentError:
        devices.filter(
          (device) =>
            device.recentError
        ).length,
      issues,
    },
  };
}

function splitTextChunks(
  lines,
  maxLength = 3500
) {
  const chunks = [];
  let current = "";

  for (const rawLine of lines) {
    const line =
      String(rawLine ?? "");

    const next =
      current
        ? `${current}\n${line}`
        : line;

    if (
      next.length > maxLength &&
      current
    ) {
      chunks.push(current);
      current = line;
    } else {
      current = next;
    }
  }

  if (current) {
    chunks.push(current);
  }

  return chunks;
}

async function sendTextChunks(
  chatId,
  env,
  lines,
  replyMarkup
) {
  const chunks =
    splitTextChunks(lines);

  for (
    let index = 0;
    index < chunks.length;
    index += 1
  ) {
    await sendMessage(
      chatId,
      env,
      chunks[index],
      index === chunks.length - 1
        ? replyMarkup
        : undefined
    );
  }
}

async function showHealth(
  chatId,
  env,
  messageId
) {
  const snapshot =
    await collectFleetHealth(
      env,
      true
    );

  const alertsOn =
    await alertsEnabled(env);

  const counts =
    snapshot.counts;

  const textValue =
    "🏥 SỨC KHỎE HỆ THỐNG\n\n" +
    `Tổng thiết bị: ${counts.total}\n` +
    `🟢 Online: ${counts.online}\n` +
    `🔴 Offline: ${counts.offline}\n` +
    `🛠 Bảo trì: ${counts.maintenance}\n` +
    `🛑 Đã thu hồi: ${snapshot.revoked}\n\n` +
    `⚠️ Pin từ ${LOW_BATTERY_PERCENT}% trở xuống: ${counts.lowBattery}\n` +
    `💾 Bộ nhớ dưới 2 GB: ${counts.lowStorage}\n` +
    `⬆️ Agent cũ: ${counts.oldAgent}\n` +
    `❌ Lệnh lỗi gần đây: ${counts.recentError}\n\n` +
    `Cảnh báo: ${alertsOn ? "ĐANG BẬT" : "ĐANG TẮT"}\n` +
    "Giờ yên lặng: 23:00–07:00";

  const replyMarkup = {
    inline_keyboard: [
      [
        {
          text:
            `🔴 OFFLINE (${counts.offline})`,
          callback_data:
            "health_offline",
        },
        {
          text:
            `⚠️ CÓ VẤN ĐỀ (${counts.issues})`,
          callback_data:
            "health_issues",
        },
      ],
      [
        {
          text:
            `⬆️ AGENT CŨ (${counts.oldAgent})`,
          callback_data:
            "health_agent",
        },
        {
          text: "📋 TẤT CẢ MÁY",
          callback_data:
            "show_devices",
        },
      ],
      [
        {
          text:
            alertsOn
              ? "🔔 CẢNH BÁO: BẬT"
              : "🔕 CẢNH BÁO: TẮT",
          callback_data:
            "alerts_status",
        },
        {
          text: "⬅️ MENU",
          callback_data:
            "menu",
        },
      ],
    ],
  };

  if (messageId) {
    await editMessage(
      chatId,
      messageId,
      env,
      textValue,
      replyMarkup
    );
  } else {
    await sendMessage(
      chatId,
      env,
      textValue,
      replyMarkup
    );
  }
}

async function showHealthList(
  chatId,
  filter,
  env
) {
  const snapshot =
    await collectFleetHealth(
      env,
      false
    );

  let selected;
  let title;

  if (filter === "offline") {
    selected =
      snapshot.devices.filter(
        (device) =>
          !device.online
      );

    title =
      "🔴 THIẾT BỊ OFFLINE";
  } else if (filter === "agent") {
    selected =
      snapshot.devices.filter(
        (device) =>
          device.oldAgent
      );

    title =
      "⬆️ THIẾT BỊ DÙNG AGENT CŨ";
  } else {
    selected =
      snapshot.devices.filter(
        (device) =>
          device.lowBattery ||
          device.lowStorage ||
          device.oldAgent ||
          device.recentError
      );

    title =
      "⚠️ THIẾT BỊ CÓ VẤN ĐỀ";
  }

  if (selected.length === 0) {
    await sendMessage(
      chatId,
      env,
      `${title}\n\nKhông có thiết bị phù hợp.`,
      {
        inline_keyboard: [
          [
            {
              text: "⬅️ SỨC KHỎE",
              callback_data:
                "show_health",
            },
          ],
        ],
      }
    );
    return;
  }

  selected.sort(
    (left, right) =>
      left.name.localeCompare(
        right.name,
        "vi",
        {
          numeric: true,
          sensitivity: "base",
        }
      )
  );

  const lines = [
    `${title}: ${selected.length}`,
    "",
  ];

  for (const device of selected) {
    const flags = [];

    if (!device.online) {
      flags.push(
        `Offline ${formatRelativeDeviceTime(device.lastSeen, snapshot.now)}`
      );
    }

    if (device.lowBattery) {
      flags.push(
        `Pin ${device.batteryLevel}%`
      );
    }

    if (device.lowStorage) {
      flags.push(
        `Trống ${formatByteCount(device.storageFreeBytes)}`
      );
    }

    if (device.oldAgent) {
      flags.push(
        `Agent ${
          device.record.agent_version ||
          "bản cũ"
        }`
      );
    }

    if (device.recentError) {
      flags.push(
        "Có lệnh lỗi gần đây"
      );
    }

    if (device.maintenance) {
      flags.push(
        "Đang bảo trì"
      );
    }

    lines.push(
      `${device.online ? "🟢" : "⚪"} ${device.name}`,
      `ID: ${device.id}`,
      `Nhóm: ${device.group}`,
      `Vấn đề: ${flags.join(", ") || "-"}`,
      ""
    );
  }

  await sendTextChunks(
    chatId,
    env,
    lines,
    {
      inline_keyboard: [
        [
          {
            text: "⬅️ SỨC KHỎE",
            callback_data:
              "show_health",
          },
          {
            text: "📋 DANH SÁCH",
            callback_data:
              "show_devices",
          },
        ],
      ],
    }
  );
}

async function checkFleetHealth(env) {
  try {
    if (
      !(await alertsEnabled(env))
    ) {
      return;
    }

    const snapshot =
      await collectFleetHealth(
        env,
        false
      );

    const now =
      snapshot.now;

    const quiet =
      isQuietHour();

    const bootstrapped =
      (
        await env.DEVICE_STATUS.get(
          HEALTH_BOOTSTRAP_KEY
        )
      ) === "1";

    const alertEntries = [];
    const finalStates = new Map();

    for (
      const device of snapshot.devices
    ) {
      let state =
        await loadHealthState(
          device.id,
          env
        );

      if (
        !state ||
        state.version !== 1
      ) {
        state =
          initialHealthState(
            device,
            now,
            !bootstrapped
          );

        await saveHealthState(
          device.id,
          state,
          env
        );

        if (!bootstrapped) {
          continue;
        }
      } else {
        state = {
          ...state,
        };
      }

      if (device.maintenance) {
        const maintenanceState =
          initialHealthState(
            device,
            now,
            true
          );
        if (
          healthStateChanged(
            state,
            maintenanceState
          )
        ) {
          await saveHealthState(
            device.id,
            maintenanceState,
            env
          );
        }
        continue;
      }

      if (
        typeof state.observed_online !==
        "boolean"
      ) {
        state.observed_online =
          device.online;
      }

      if (
        state.observed_online !==
        device.online
      ) {
        state.observed_online =
          device.online;

        if (!device.online) {
          state.offline_since =
            now;
          state.offline_alert_sent =
            false;
        } else if (
          !state.offline_alert_sent
        ) {
          state.offline_since =
            null;
        }
      }

      if (
        device.batteryLevel !== null &&
        device.batteryLevel >=
          BATTERY_RECOVERY_PERCENT
      ) {
        state.low_battery_alerted =
          false;
      }

      if (
        device.storageFreeBytes !==
          null &&
        device.storageFreeBytes >=
          STORAGE_RECOVERY_BYTES
      ) {
        state.low_storage_alerted =
          false;
      }

      if (!device.oldAgent) {
        state.agent_old_alerted =
          false;
      }

      state.updated_at = now;

      const finalState = {
        ...state,
      };

      if (!device.online) {
        if (
          !state.offline_since
        ) {
          state.offline_since =
            now;
          finalState.offline_since =
            now;
        }

        if (
          !quiet &&
          !state.offline_alert_sent &&
          now -
            Number(
              state.offline_since
            ) >=
            OFFLINE_ALERT_AFTER_MS
        ) {
          alertEntries.push(
            `🔴 ${device.name} đã mất kết nối\n` +
              `ID: ${device.id}\n` +
              `Heartbeat cuối: ${formatRelativeDeviceTime(device.lastSeen, now)}`
          );

          finalState.offline_alert_sent =
            true;
        }
      } else if (
        state.offline_alert_sent
      ) {
        if (!quiet) {
          alertEntries.push(
            `🟢 ${device.name} đã online trở lại\n` +
              `ID: ${device.id}\n` +
              `Gián đoạn: ${formatHealthDuration(now - Number(state.offline_since || now))}`
          );

          finalState.offline_alert_sent =
            false;
          finalState.offline_since =
            null;
        }
      } else {
        state.offline_since =
          null;
        finalState.offline_since =
          null;
      }

      if (
        device.lowBattery &&
        !state.low_battery_alerted &&
        !quiet
      ) {
        alertEntries.push(
          `🔋 ${device.name} sắp hết pin\n` +
            `ID: ${device.id}\n` +
            `Pin còn: ${device.batteryLevel}%`
        );

        finalState.low_battery_alerted =
          true;
      }

      if (
        device.lowStorage &&
        !state.low_storage_alerted &&
        !quiet
      ) {
        alertEntries.push(
          `💾 ${device.name} sắp hết bộ nhớ\n` +
            `ID: ${device.id}\n` +
            `Dung lượng trống: ${formatByteCount(device.storageFreeBytes)}`
        );

        finalState.low_storage_alerted =
          true;
      }

      if (
        device.oldAgent &&
        !state.agent_old_alerted &&
        !quiet
      ) {
        alertEntries.push(
          `⬆️ ${device.name} đang dùng Agent cũ\n` +
            `ID: ${device.id}\n` +
            `Hiện tại: ${device.record.agent_version || "không rõ"}\n` +
            `Yêu cầu: ${TARGETING_AGENT_VERSION}`
        );

        finalState.agent_old_alerted =
          true;
      }

      if (
        device.recentError &&
        device.errorKey !==
          state.last_error_alerted_key &&
        !quiet
      ) {
        alertEntries.push(
          `❌ ${device.name} báo lỗi lệnh\n` +
            `ID: ${device.id}\n` +
            `Lệnh: ${device.record.last_command || "-"}\n` +
            `Kết quả: ${String(device.record.last_result || "-").slice(0, 200)}`
        );

        finalState.last_error_alerted_key =
          device.errorKey;
      }

      finalState.updated_at =
        now;

      if (
        healthStateChanged(
          state,
          finalState
        )
      ) {
        finalStates.set(
          device.id,
          finalState
        );
      }
    }

    if (alertEntries.length > 0) {
      const lines = [
        "🚨 CẢNH BÁO HỆ THỐNG",
        "",
      ];

      for (
        const entry of alertEntries
      ) {
        lines.push(
          entry,
          ""
        );
      }

      await sendTextChunks(
        String(
          env.TELEGRAM_ADMIN_USER_ID
        ),
        env,
        lines
      );

    }

    for (
      const [deviceId, nextState]
      of finalStates
    ) {
      await saveHealthState(
        deviceId,
        nextState,
        env
      );
    }

    if (!bootstrapped) {
      await env.DEVICE_STATUS.put(
        HEALTH_BOOTSTRAP_KEY,
        "1"
      );
    }
  } catch (error) {
    console.error(
      "fleet_health_check_failed",
      error?.message || error
    );
  }
}

async function showDevices(chatId, env) {
  const now = Date.now();

  const ids =
    await deviceIdsForTarget(
      "all",
      env
    );

  const devices = [];

  for (const id of ids) {
    const record =
      await getDeviceRecord(id, env);

    if (!record) continue;

    const online =
      now - Number(
        record.last_seen || 0
      ) <= ONLINE_WINDOW_MS;

    const maintenance =
      await isDeviceMaintenance(
        id,
        env
      );

    const name =
      await deviceDisplayName(
        id,
        env
      );

    const groupLabel =
      await getGroupLabel(
        record.device_group,
        env
      );

    devices.push({
      id,
      name,
      online,
      maintenance,
      groupLabel,
      activity:
        formatRelativeDeviceTime(
          record.last_seen,
          now
        ),
      status:
        maintenance
          ? "Đang bảo trì"
          : deviceStatusLabel(
              record,
              online
            ),
    });
  }

  if (devices.length === 0) {
    await sendMessage(
      chatId,
      env,
      "📋 THIẾT BỊ: 0\n\nChưa có thiết bị nào kết nối."
    );
    return;
  }

  devices.sort((left, right) => {
    if (
      left.maintenance !==
      right.maintenance
    ) {
      return left.maintenance
        ? 1
        : -1;
    }

    if (
      left.online !== right.online
    ) {
      return left.online ? -1 : 1;
    }

    return left.name.localeCompare(
      right.name,
      "vi",
      {
        numeric: true,
        sensitivity: "base",
      }
    );
  });

  const lines = [
    `📋 THIẾT BỊ: ${devices.length}`,
    "",
  ];

  for (const device of devices) {
    const icon =
      device.maintenance
        ? "🛠"
        : device.online
          ? "🟢"
          : "⚪";

    lines.push(
      `${icon} ${device.name}`,
      `ID: ${device.id}`,
      `Nhóm: ${device.groupLabel}`,
      `Hoạt động: ${device.activity}`,
      `Trạng thái: ${device.status}`,
      ""
    );
  }

  const buttons = [];

  for (
    let index = 0;
    index < devices.length;
    index += 2
  ) {
    buttons.push(
      devices
        .slice(index, index + 2)
        .map((device) => ({
          text:
            `${
              device.maintenance
                ? "🛠"
                : device.online
                  ? "🟢"
                  : "⚪"
            } ${device.name.slice(0, 22)}`,
          callback_data:
            `device:${device.id}`,
        }))
    );
  }

  let chunk = "";

  for (const line of lines) {
    const next =
      chunk
        ? `${chunk}\n${line}`
        : line;

    if (next.length > 3500) {
      await sendMessage(
        chatId,
        env,
        chunk.trim()
      );
      chunk = line;
    } else {
      chunk = next;
    }
  }

  if (chunk) {
    await sendMessage(
      chatId,
      env,
      chunk.trim(),
      {
        inline_keyboard: buttons,
      }
    );
  }
}


async function sendProgress(chatId, env) {
  try {
    let commandId = null;
    let metadata = null;

    const latestRaw =
      await env.DEVICE_STATUS.get(
        "latest_command"
      );

    if (latestRaw) {
      try {
        const latest =
          JSON.parse(latestRaw);

        commandId =
          latest.command_id || null;
      } catch (error) {
        commandId =
          latestRaw.trim() || null;
      }
    }

    if (commandId) {
      const metadataRaw =
        await env.DEVICE_STATUS.get(
          `cmd:${commandId}`
        );

      if (metadataRaw) {
        try {
          metadata =
            JSON.parse(metadataRaw);
        } catch (error) {
          metadata = null;
        }
      }
    }

    // Fallback cho command cũ chưa có latest_command.
    if (!commandId) {
      let newestTimestamp = 0;

      const allDeviceIds =
        await deviceIdsForTarget(
          "all",
          env
        );

      for (const deviceId of allDeviceIds) {
        const record =
          await getDeviceRecord(
            deviceId,
            env
          );

        if (
          record?.last_command_id &&
          Number(
            record.last_command_ts || 0
          ) > newestTimestamp
        ) {
          commandId =
            record.last_command_id;

          newestTimestamp =
            Number(
              record.last_command_ts || 0
            );
        }
      }

      if (commandId) {
        const metadataRaw =
          await env.DEVICE_STATUS.get(
            `cmd:${commandId}`
          );

        if (metadataRaw) {
          try {
            metadata =
              JSON.parse(metadataRaw);
          } catch (error) {
            metadata = null;
          }
        }

        if (!metadata) {
          metadata = {
            command_id: commandId,
            target: "all",
            commands: [],
            command_count: 0,
            device_count:
              allDeviceIds.length,
            device_ids:
              allDeviceIds,
            created:
              newestTimestamp,
          };
        }
      }
    }

    if (!commandId) {
      await sendMessage(
        chatId,
        env,
        "Không tìm thấy command gần đây để báo tiến độ."
      );
      return;
    }

    const target =
      ["all", "marmot", "nova", "devices"].includes(
        metadata?.target
      )
        ? metadata.target
        : "all";

    const deviceIds =
      Array.isArray(metadata?.device_ids) &&
      metadata.device_ids.length > 0
        ? [
            ...new Set(
              metadata.device_ids
                .map(normalizeDeviceId)
                .filter(Boolean)
            ),
          ].sort(compareDeviceIds)
        : await deviceIdsForTarget(
            target,
            env
          );

    const commands =
      Array.isArray(metadata?.commands)
        ? metadata.commands
        : [];

    const counts = {
      received: 0,
      running: 0,
      success: 0,
      error: 0,
      offline: 0,
      no_response: 0,
    };

    const now = Date.now();

    for (const deviceId of deviceIds) {
      const record =
        await getDeviceRecord(
          deviceId,
          env
        );

      if (
        !record ||
        now - Number(
          record.last_seen || 0
        ) > 90 * 1000
      ) {
        counts.offline += 1;
        continue;
      }

      if (
        record.last_command_id !==
        commandId
      ) {
        counts.no_response += 1;
        continue;
      }

      switch (record.last_status) {
        case "received":
          counts.received += 1;
          break;

        case "running":
          counts.running += 1;
          break;

        case "success":
          counts.success += 1;
          break;

        case "error":
          counts.error += 1;
          break;

        default:
          counts.no_response += 1;
          break;
      }
    }

    const lines = [
      `📊 Tiến độ command: ${commandId}`,
      `Đích: ${metadata?.target_label || target.toUpperCase()}`,
      `Thời gian tạo: ${
        formatTimestamp(metadata?.created)
      }`,
    ];

    if (commands.length > 0) {
      lines.push(
        `Lệnh (${commands.length}):`,
        ...commands.map(
          (command) => `- ${command}`
        )
      );
    } else {
      lines.push(
        "Lệnh: chưa có metadata của command cũ"
      );
    }

    lines.push(
      `Tổng máy dự kiến: ${deviceIds.length}`,
      `Received: ${counts.received}`,
      `Running: ${counts.running}`,
      `Success: ${counts.success}`,
      `Error: ${counts.error}`,
      `Offline: ${counts.offline}`,
      `No response: ${counts.no_response}`
    );

    await sendMessage(
      chatId,
      env,
      lines.join("\n")
    );

  } catch (error) {
    await sendMessage(
      chatId,
      env,
      `Lỗi khi tổng hợp tiến độ: ${error.message}`
    );
  }
}

function text(value, status = 200) {
  return new Response(value, {
    status,
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}

function json(value, status = 200) {
  return new Response(JSON.stringify(value, null, 2), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

function noStoreJson(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: {
      "Content-Type":
        "application/json; charset=utf-8",
      "Cache-Control":
        "no-store, no-cache, must-revalidate",
      Pragma: "no-cache",
    },
  });
}
