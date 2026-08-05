const OWNER = "tinhpr9";
const REPO = "Aotscript";
const BRANCH = "main";

const TARGET_FILES = {
  all: "lenh_all.txt",
  marmot: "lenh_marmot.txt",
  nova: "lenh_nova.txt",
};

const DEVICE_ALIAS_PREFIX = "device_alias:";
const GROUP_LABEL_PREFIX = "group_label:";
const PAIR_PREFIX = "pair:";
const PAIR_DEVICE_PREFIX = "pair_device:";
const PAIR_RATE_PREFIX = "pair_rate:";
const PAIR_TTL_SECONDS = 10 * 60;
const PAIR_RATE_SECONDS = 60;
const MAINTENANCE_PREFIX = "maintenance:";
const COMMAND_TTL_MS = 5 * 60 * 1000;
const COMMAND_METADATA_TTL_SECONDS = 30 * 24 * 60 * 60;
const ONLINE_WINDOW_MS = 90 * 1000;
const TARGETING_AGENT_VERSION = "fleet-ops-1";
const DANGEROUS_COMMAND_KEYS = new Set([
  "setup_boot",
  "setup_caylapbu",
  "run_caylapbu",
  "update_delta",
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
  update_solver: { value: "UPDATE_SOLVER", bit: 256, label: "🧠 SOLVER" },
  update_script: { value: "UPDATESCRIPT", bit: 512, label: "📝 SCRIPT" },
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
    name.startsWith(MAINTENANCE_PREFIX)
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
  const normalized = normalizeDeviceGroup(group);
  if (!normalized) {
    return String(group || "-").toUpperCase();
  }

  try {
    const value = await env.DEVICE_STATUS.get(
      `${GROUP_LABEL_PREFIX}${normalized}`
    );
    return sanitizeLabel(value, 32) || normalized;
  } catch (error) {
    return normalized;
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

  // Chỉ lấy thiết bị đã thực sự gửi heartbeat/report vào KV.
  const ids = new Set();

  let cursor;

  do {
    const page = await env.DEVICE_STATUS.list({
      limit: 1000,
      ...(cursor ? { cursor } : {}),
    });

    for (const item of page.keys || []) {
      if (isDeviceStatusMetadataKey(item.name)) {
        continue;
      }

      const record =
        await getDeviceRecord(item.name, env);

      if (!record) continue;

      const deviceId =
        normalizeDeviceId(
          record.device_id || item.name
        );

      const deviceGroup =
        normalizeDeviceGroup(
          record.device_group
        );

      if (!deviceId || !deviceGroup) {
        continue;
      }

      if (
        wantedGroup &&
        deviceGroup !== wantedGroup
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

  return [...ids].sort(compareDeviceIds);
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

function buildCommandLinesFromKeys(
  commandKeys
) {
  return orderedCommandKeys(
    commandKeys
  ).map((key) => {
    if (
      key === "update_solver" ||
      key === "update_script"
    ) {
      throw new Error(
        "Lệnh Solver hoặc Script cần URL."
      );
    }

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
    const deviceIds =
      await validateDirectDeviceIds(
        spec.deviceIds,
        env
      );

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

  const deviceIds =
    await activeDeviceIdsForTarget(
      target,
      env
    );

  if (deviceIds.length === 0) {
    throw new Error(
      "Không có thiết bị đủ điều kiện nhận lệnh."
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
        "Lệnh hết hạn sau 5 phút.",
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
  const expiresAt =
    created + COMMAND_TTL_MS;

  const content = commandEnvelope(
    commandId,
    deviceIds,
    expiresAt,
    spec.commandLines
  );

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
    env
  );

  await storeCommandMetadata(
    env,
    commandId,
    spec.target,
    spec.commandLines,
    {
      targetLabel:
        spec.targetLabel,
      fileTarget:
        spec.fileTarget,
      deviceIds,
      commandKeys:
        spec.commandKeys,
      created,
      expiresAt,
    }
  );

  await sendMessage(
    chatId,
    env,
    `✅ Đã gửi tới ${deviceIds.length} máy\n` +
      `Đích: ${spec.targetLabel}\n` +
      `Lệnh:\n- ${spec.commandLines.join("\n- ")}\n` +
      `Hết hạn: 5 phút\n` +
      `Commit: ${commit.slice(0, 7)}`,
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

  const clientIp =
    request.headers.get("CF-Connecting-IP") ||
    "unknown";

  const ipHash = (
    await sha256Hex(clientIp)
  ).slice(0, 20);

  const rateKey =
    `${PAIR_RATE_PREFIX}${deviceId}:${ipHash}`;

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

  const record = {
    pair_id: pairId,
    device_id: deviceId,
    device_group: deviceGroup,
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

  const expiresText =
    new Date(expiresAt)
      .toISOString()
      .replace("T", " ")
      .replace("Z", " UTC");

  try {
    await sendMessage(
      String(env.TELEGRAM_ADMIN_USER_ID),
      env,
      `🔐 Yêu cầu ghép nối Agent\n` +
        `Thiết bị: ${deviceId}\n` +
        `Profile: ${deviceGroup}\n` +
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
          `Profile: ${record.device_group}\n` +
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

  await Promise.all([
    env.DEVICE_STATUS.delete(deviceId),
    env.DEVICE_STATUS.delete(
      `${DEVICE_ALIAS_PREFIX}${deviceId}`
    ),
    env.DEVICE_STATUS.delete(
      maintenanceKey(deviceId)
    ),
  ]);

  await sendMessage(
    chatId,
    env,
    `Đã xóa ${deviceId} khỏi danh sách. ` +
      "Nếu Agent còn chạy, thiết bị sẽ tự xuất hiện lại."
  );
}

async function setGroupLabel(
  chatId,
  rawGroup,
  rawLabel,
  env
) {
  const group = normalizeDeviceGroup(rawGroup);

  if (!group) {
    await sendMessage(
      chatId,
      env,
      "Nhóm phải là MARMOT hoặc NOVA."
    );
    return;
  }

  if (String(rawLabel || "").trim() === "-") {
    await env.DEVICE_STATUS.delete(
      `${GROUP_LABEL_PREFIX}${group}`
    );
    await sendMessage(
      chatId,
      env,
      `Đã khôi phục tên nhóm ${group}.`
    );
    return;
  }

  const label = sanitizeLabel(rawLabel, 32);

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
    `Đã đặt tên nhóm ${group}: ${label}`
  );
}

export default {
  async fetch(request, env) {
    try {
      const url = new URL(request.url);

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

        await telegram(env, "setMyCommands", {
          commands: [
            { command: "start", description: "Mở bảng điều khiển" },
            { command: "status", description: "Xem lệnh hiện tại" },
            { command: "devices", description: "Danh sách thiết bị" },
            { command: "device", description: "Chi tiết một thiết bị" },
            { command: "to", description: "Gửi lệnh tới máy cụ thể" },
            { command: "maintenance", description: "Bật hoặc tắt bảo trì" },
            { command: "rename", description: "Đổi tên thiết bị" },
            { command: "delete", description: "Xóa thiết bị khỏi danh sách" },
            { command: "groupname", description: "Đổi tên hiển thị nhóm" },
            { command: "progress", description: "Xem tiến độ lệnh gần nhất" },
            { command: "solver", description: "Cập nhật URL Solver" },
            { command: "script", description: "Cập nhật URL script" },
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
      /^\/to\s+([A-Za-z0-9_,-]+)\s+solver\s+(https?:\/\/\S+)$/i
    );

  if (directSolverMatch) {
    const url = directSolverMatch[2];

    if (!isValidUrl(url)) {
      await sendMessage(
        chatId,
        env,
        "URL Solver không hợp lệ."
      );
      return;
    }

    await requestCommandDispatch(
      chatId,
      {
        deviceIds:
          directSolverMatch[1].split(","),
        commandKeys:
          ["update_solver"],
        commandLines:
          [`UPDATE_SOLVER ${url}`],
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
          "Solver: /to m166 solver https://...\n" +
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

  const groupNameMatch =
    input.match(/^\/groupname\s+(marmot|nova)\s+(.+)$/i);

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

  const pendingState = await loadSessionState(from.id, chatId);
  if (pendingState && pendingState.step && !input.startsWith("/")) {
    await handlePendingSessionMessage(chatId, from.id, pendingState, input, env);
    return;
  }

  // Support URL-based commands: /solver and /script
  const solverMatch = input.match(/^\/solver\s+(all|marmot|nova)\s+(https?:\/\/\S+)$/i);
  if (solverMatch) {
    const target = solverMatch[1].toLowerCase();
    const url = solverMatch[2];
    if (!TARGET_FILES[target]) {
      await sendMessage(chatId, env, "Target không hợp lệ cho /solver.");
      return;
    }
    if (!isValidUrl(url)) {
      await sendMessage(chatId, env, "URL không hợp lệ. Phải là http:// hoặc https://");
      return;
    }
    await executeSolverCommand(chatId, target, url, env);
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

  const solverShortMatch = input.match(/^\/solver\s+(https?:\/\/\S+)$/i);
  if (solverShortMatch) {
    const url = solverShortMatch[1];
    if (!isValidUrl(url)) {
      await sendMessage(chatId, env, "URL không hợp lệ. Phải là http:// hoặc https://");
      return;
    }
    await promptTargetSelection(chatId, "solver", url, env);
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
  const match = input.match(/^\/(all|marmot|nova)\s+(.+)$/i);
  if (!match) return null;

  const target = match[1].toLowerCase();
  const commandKeys = match[2]
    .toLowerCase()
    .split(/[\s,]+/)
    .filter(Boolean);

  if (commandKeys.length === 0) return null;

  // Allow commands in the multi-select flow, including URL-based selectors.
  const allowed = new Set(Object.keys(COMMANDS));
  const filtered = commandKeys.filter((k) => allowed.has(k));
  if (filtered.length === 0 || filtered.some((key) => !COMMANDS[key])) return null;

  const uniqueKeys = [...new Set(filtered)];
  if (uniqueKeys.includes("idle") && uniqueKeys.length > 1) {
    return null;
  }

  return { target, commandKeys: uniqueKeys };
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
          text: `🦫 ${marmotLabel}`,
          callback_data: `shortcmd:${command}:${token}:marmot`,
        },
      ],
      [
        {
          text: `✨ ${novaLabel}`,
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
    step: null,
    solverUrl: null,
    scriptUrl: null,
  };

  if (commandKeys.includes("update_solver")) {
    state.step = "awaiting_solver_url";
    await saveSessionState(userId, chatId, state, env);
    await sendMessage(chatId, env, "Vui lòng gửi URL Solver (http:// hoặc https://) hoặc bấm HỦY.", {
      inline_keyboard: [[{ text: "❌ HỦY", callback_data: "cancel-session" }]],
    });
    return;
  }

  state.step = "awaiting_script_url";
  await saveSessionState(userId, chatId, state, env);
  await sendMessage(chatId, env, "Vui lòng gửi URL Script (http:// hoặc https://) hoặc bấm HỦY.", {
    inline_keyboard: [[{ text: "❌ HỦY", callback_data: "cancel-session" }]],
  });
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
          if (
            !isValidUrl(
              state.solverUrl
            )
          ) {
            throw new Error(
              "URL Solver không hợp lệ."
            );
          }

          return (
            `/solver ${state.target} ` +
            `${state.solverUrl}`
          );
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

          return (
            `/script ${state.target} ` +
            `${state.scriptUrl}`
          );
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

    if (
      commandKeys.includes(
        "update_solver"
      ) ||
      commandKeys.includes(
        "update_script"
      )
    ) {
      await answerCallback(
        callback.id,
        env,
        "Dùng lệnh /to để nhập URL.",
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

    if (commandKeys.some((k) => ["update_solver", "update_script"].includes(k))) {
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
    "Chọn nhóm hoặc chọn từng máy:";

  const replyMarkup = {
    inline_keyboard: [
      [
        {
          text: "🌍 TẤT CẢ",
          callback_data: "target:all",
        },
        {
          text: `🦫 ${marmotLabel}`,
          callback_data:
            "target:marmot",
        },
      ],
      [
        {
          text: `✨ ${novaLabel}`,
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
          text: "📋 TRẠNG THÁI",
          callback_data: "status",
        },
        {
          text: "📊 TIẾN ĐỘ",
          callback_data:
            "show_progress",
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
  const lines = ["📋 Lệnh hiện tại trên GitHub:"];

  for (const [target, file] of Object.entries(TARGET_FILES)) {
    try {
      const data = await getGitHubFile(file, env);
      const content = decodeBase64(data.content || "").trim() || "(trống)";
      lines.push(`\n${target.toUpperCase()} — ${file}\n${content}`);
    } catch (error) {
      lines.push(`\n${target.toUpperCase()} — lỗi: ${error.message}`);
    }
  }

  await sendMessage(chatId, env, lines.join("\n"));
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

async function updateGitHubFile(path, content, message, env) {
  const current = await getGitHubFile(path, env);
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


async function executeSolverCommand(
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
        ["update_solver"],
      commandLines:
        [`/solver ${target} ${url}`],
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
        [`/script ${target} ${url}`],
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

async function handleAgentReport(request, env) {
  const secret =
    request.headers.get("X-Agent-Secret");

  if (
    !secret ||
    secret !== env.AGENT_REPORT_SECRET
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
    device_id: reportedDeviceId,
    device_group: reportedDeviceGroup,
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
    !ALLOWED_REPORT_STATUSES.includes(status)
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
    normalizeDeviceId(reportedDeviceId);

  const deviceGroup =
    normalizeDeviceGroup(
      reportedDeviceGroup
    );

  if (!deviceId) {
    return json(
      {
        ok: false,
        error: "invalid_device_id",
      },
      400
    );
  }

  if (!deviceGroup) {
    return json(
      {
        ok: false,
        error: "invalid_device_group",
      },
      400
    );
  }

  // ID cũ phải khớp nhóm trong tên.
  // ID m<number> dùng device_group.txt làm profile.
  const legacyPrefix =
    deviceId.match(/^(MARMOT|NOVA)-/);

  if (
    legacyPrefix &&
    legacyPrefix[1] !== deviceGroup
  ) {
    return json(
      {
        ok: false,
        error: "group_mismatch",
      },
      400
    );
  }

  try {
    const key = deviceId;
    let record = {};

    try {
      const existing =
        await env.DEVICE_STATUS.get(key);

      if (existing) {
        record = JSON.parse(existing);
      }
    } catch (error) {
      record = {};
    }

    const now = Date.now();

    record.device_id = deviceId;
    record.device_group = deviceGroup;
    record.last_seen = now;
    record.last_report_status = status;

    if (status === "heartbeat") {
      if (!record.last_status) {
        record.last_status = "heartbeat";
      }
    } else {
      record.last_status = status;
    }

    if (command_id) {
      record.last_command_id = command_id;

      record.last_command =
        command ??
        record.last_command ??
        null;

      record.last_command_index =
        command_index ??
        record.last_command_index ??
        null;

      record.last_command_total =
        command_total ??
        record.last_command_total ??
        null;

      record.last_command_ts =
        Date.parse(timestamp) || now;
    }

    if (last_result) {
      record.last_result = last_result;
    }

    const batteryNumber =
      Number(body.battery_level);

    if (
      Number.isFinite(batteryNumber) &&
      batteryNumber >= 0 &&
      batteryNumber <= 100
    ) {
      record.battery_level =
        Math.round(batteryNumber);
    }

    if (
      typeof body.charging ===
      "boolean"
    ) {
      record.charging =
        body.charging;
    }

    const androidLabel =
      sanitizeLabel(
        body.android_version,
        32
      );

    if (androidLabel) {
      record.android_version =
        androidLabel;
    }

    const agentLabel =
      sanitizeLabel(
        body.agent_version,
        64
      );

    if (agentLabel) {
      record.agent_version =
        agentLabel;
    }

    const uptimeNumber =
      Number(body.uptime_seconds);

    if (
      Number.isFinite(uptimeNumber) &&
      uptimeNumber >= 0
    ) {
      record.uptime_seconds =
        Math.floor(uptimeNumber);
    }

    const storageNumber =
      Number(body.storage_free_bytes);

    if (
      Number.isFinite(storageNumber) &&
      storageNumber >= 0
    ) {
      record.storage_free_bytes =
        Math.floor(storageNumber);
    }

    await env.DEVICE_STATUS.put(
      key,
      JSON.stringify(record)
    );

    return json({
      ok: true,
      device_id: deviceId,
      device_group: deviceGroup,
    });

  } catch (error) {
    console.error(error);

    return json(
      {
        ok: false,
        error: String(
          error?.message || error
        ),
      },
      500
    );
  }
}

async function getDeviceRecord(deviceId, env) {
  try {
    const raw = await env.DEVICE_STATUS.get(deviceId);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (e) {
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

  const replyMarkup = {
    inline_keyboard: [
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
        {
          text:
            `✅ GỬI ${selected.length} LỆNH`,
          callback_data:
            `dsend:${deviceId}:${mask}`,
        },
      ],
      [
        {
          text: "⬅️ CHI TIẾT MÁY",
          callback_data:
            `device:${deviceId}`,
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
