const PREFIX = "rollout:";
const ACTIVE_KEY = "setting:active_rollout";
const TTL = 30 * 24 * 60 * 60;
const DEFAULT_BATCH = 5;
const MAX_BATCH = 20;
const EXTRA_WAIT_MS = 2 * 60 * 1000;
const MAX_ACTIVE_AGE_MS = 6 * 60 * 60 * 1000;
const TERMINAL = new Set(["completed", "cancelled"]);
const RUNNING = new Set(["canary_running", "batch_running", "retry_running"]);
const ALLOWED = new Set([
  "idle", "setup_vip", "install_track", "setup_boot",
  "setup_caylapbu", "run_caylapbu", "update_delta", "reboot",
]);

export function isRolloutMetadataKey(name) {
  return String(name || "").startsWith(PREFIX);
}

const keyFor = (id) => `${PREFIX}${id}`;
const validId = (value) => {
  const raw = String(value || "").trim();
  return /^[a-z0-9-]{8,40}$/i.test(raw) ? raw : null;
};

function commandKey(value) {
  const raw = String(value || "").trim().toLowerCase().replace(/[\s-]+/g, "_");
  const aliases = {
    idle: "idle", vip: "setup_vip", setup_vip: "setup_vip",
    track: "install_track", install_track: "install_track",
    boot: "setup_boot", setup_boot: "setup_boot",
    caylapbu: "setup_caylapbu", setup_caylapbu: "setup_caylapbu",
    run: "run_caylapbu", run_caylapbu: "run_caylapbu",
    delta: "update_delta", update_delta: "update_delta", reboot: "reboot",
  };
  const key = aliases[raw] || null;
  return key && ALLOWED.has(key) ? key : null;
}

function label(status) {
  return ({
    planned: "Chờ chạy máy thử",
    canary_running: "Đang chạy máy thử",
    batch_running: "Đang chạy một đợt",
    retry_running: "Đang thử lại máy lỗi",
    waiting_next_batch: "Chờ gửi đợt tiếp theo",
    paused_error: "Đã dừng vì có lỗi",
    completed: "Đã hoàn thành",
    cancelled: "Đã kết thúc",
  })[status] || status || "Không rõ";
}

function unique(values, compare) {
  return [...new Set(values || [])].sort(compare);
}

function mergeEntries(current, additions, compare) {
  const map = new Map();
  for (const item of [...(current || []), ...(additions || [])]) {
    const id = String(item?.id || "").trim();
    if (id) map.set(id, {
      id,
      reason: String(item?.reason || "-").slice(0, 180),
    });
  }
  return [...map.values()].sort((a, b) => compare(a.id, b.id));
}

async function load(id, env) {
  id = validId(id);
  if (!id) return null;
  try {
    const raw = await env.DEVICE_STATUS.get(keyFor(id));
    const value = raw ? JSON.parse(raw) : null;
    return value && typeof value === "object" ? value : null;
  } catch {
    return null;
  }
}

async function save(state, env) {
  state.updated_at = Date.now();
  await env.DEVICE_STATUS.put(
    keyFor(state.id),
    JSON.stringify(state),
    { expirationTtl: TTL }
  );
}

async function clearActive(id, env) {
  const current = await env.DEVICE_STATUS.get(ACTIVE_KEY);
  if (!id || current === id) {
    await env.DEVICE_STATUS.delete(ACTIVE_KEY);
  }
}


export async function getActiveRollout(env) {
  const id =
    await env.DEVICE_STATUS.get(
      ACTIVE_KEY
    );
  if (!id) return null;
  const state =
    await load(id, env);
  const updatedAt =
    Number(
      state?.updated_at ||
      state?.created_at ||
      0
    );
  const stale =
    updatedAt > 0 &&
    Date.now() - updatedAt >
      MAX_ACTIVE_AGE_MS;
  if (
    !state ||
    TERMINAL.has(
      state.status
    ) ||
    stale
  ) {
    await clearActive(
      id,
      env
    );
    return null;
  }
  return state;
}

async function eligible(target, requested, env, ops) {
  const source = requested === null
    ? await ops.deviceIdsForTarget(target, env)
    : requested;
  const ids = unique(
    (source || []).map(ops.normalizeDeviceId).filter(Boolean),
    ops.compareDeviceIds
  );
  const ok = [];
  const excluded = [];
  const now = Date.now();
  for (const id of ids) {
    const record = await ops.getDeviceRecord(id, env);
    if (!record) {
      excluded.push({ id, reason: "không tồn tại hoặc đã thu hồi" });
    } else if (await ops.isDeviceMaintenance(id, env)) {
      excluded.push({ id, reason: "đang bảo trì" });
    } else if (now - Number(record.last_seen || 0) > ops.onlineWindowMs) {
      excluded.push({ id, reason: "đang offline" });
    } else if (record.agent_version !== ops.targetAgentVersion) {
      excluded.push({ id, reason: "Agent chưa cập nhật" });
    } else {
      ok.push(id);
    }
  }
  return {
    eligible: ok.sort(ops.compareDeviceIds),
    excluded,
  };
}

async function counts(state, env, ops) {
  const result = {
    received: 0,
    running: 0,
    success: 0,
    error: 0,
    no_response: 0,
  };
  for (const id of state.current_batch_ids || []) {
    const record = await ops.getDeviceRecord(id, env);
    if (!record || record.last_command_id !== state.current_command_id) {
      result.no_response += 1;
    } else if (record.last_status === "received") {
      result.received += 1;
    } else if (record.last_status === "running") {
      result.running += 1;
    } else if (record.last_status === "success") {
      result.success += 1;
    } else if (["error", "expired"].includes(record.last_status)) {
      result.error += 1;
    } else {
      result.no_response += 1;
    }
  }
  return result;
}

function markup(state) {
  const rows = [];
  if (state.status === "planned") {
    rows.push([
      { text: "🧪 CHẠY MÁY THỬ", callback_data: `rollout_start:${state.id}` },
      { text: "🛑 HỦY", callback_data: `rollout_stop:${state.id}` },
    ]);
  }
  if (RUNNING.has(state.status)) {
    rows.push([
      { text: "🔄 CẬP NHẬT", callback_data: `rollout_refresh:${state.id}` },
      { text: "🛑 DỪNG", callback_data: `rollout_stop:${state.id}` },
    ]);
  }
  if (state.status === "waiting_next_batch") {
    rows.push([
      { text: `▶️ GỬI ${state.batch_size} MÁY`, callback_data: `rollout_next:${state.id}` },
      { text: "🚀 GỬI HẾT", callback_data: `rollout_all:${state.id}` },
    ]);
    rows.push([
      { text: "🛑 KẾT THÚC", callback_data: `rollout_stop:${state.id}` },
    ]);
  }
  if (state.status === "paused_error") {
    rows.push([
      { text: "🔁 THỬ LẠI", callback_data: `rollout_retry:${state.id}` },
      { text: "⏭ BỎ QUA", callback_data: `rollout_skip:${state.id}` },
    ]);
    rows.push([
      { text: "🛑 KẾT THÚC", callback_data: `rollout_stop:${state.id}` },
    ]);
  }
  rows.push([{ text: "⬅️ MENU", callback_data: "menu" }]);
  return { inline_keyboard: rows };
}

async function textFor(state, env, ops) {
  const lines = [
    "🚀 TRIỂN KHAI AN TOÀN",
    "",
    `ID: ${state.id}`,
    `Nhóm: ${state.target_label}`,
    `Lệnh: ${state.command_value}`,
    `Trạng thái: ${label(state.status)}`,
    `Máy thử: ${state.canary_id || "-"}`,
    `Mỗi đợt: ${state.batch_size} máy`,
    "",
    `Tổng máy hợp lệ: ${state.total_devices}`,
    `✅ Thành công: ${(state.success_ids || []).length}`,
    `❌ Lỗi: ${(state.failed_ids || []).length}`,
    `⏭ Bỏ qua: ${(state.skipped_ids || []).length}`,
    `⏳ Còn lại: ${(state.pending_ids || []).length}`,
  ];
  if ((state.current_batch_ids || []).length) {
    const current = await counts(state, env, ops);
    lines.push(
      "",
      `Đợt đang chạy: ${state.current_batch_ids.join(", ")}`,
      `Received: ${current.received}`,
      `Running: ${current.running}`,
      `Success: ${current.success}`,
      `Error: ${current.error}`,
      `No response: ${current.no_response}`
    );
  }
  if ((state.failed_ids || []).length) {
    lines.push("", "Máy lỗi:");
    for (const item of state.failed_ids) {
      lines.push(`- ${item.id}: ${item.reason}`);
    }
  }
  return lines.join("\n");
}

async function show(chatId, id, env, ops) {
  const state = id
    ? await load(id, env)
    : await getActiveRollout(env);
  if (!state) {
    await ops.sendMessage(
      chatId,
      env,
      "🚀 TRIỂN KHAI AN TOÀN\n\n" +
        "Chưa có rollout đang hoạt động.\n\n" +
        "Cách dùng:\n" +
        "/rollout 1 idle\n" +
        "/rollout 2 update_delta\n" +
        "/rollout all setup_boot 10\n\n" +
        "Số cuối là kích thước mỗi đợt, mặc định 5."
    );
    return;
  }
  await ops.sendMessage(
    chatId,
    env,
    await textFor(state, env, ops),
    markup(state)
  );
}

async function create(chatId, rawTarget, rawCommand, rawBatch, env, ops) {
  const active = await getActiveRollout(env);
  if (active) {
    await ops.sendMessage(
      chatId,
      env,
      `Đang có rollout ${active.id}. Hãy hoàn tất hoặc kết thúc trước.`
    );
    await show(chatId, active.id, env, ops);
    return;
  }
  const target = ops.normalizeTargetInput(rawTarget);
  const cmd = commandKey(rawCommand);
  const batch = rawBatch == null ? DEFAULT_BATCH : Number(rawBatch);
  if (!target || !ops.targetFiles[target]) {
    await ops.sendMessage(chatId, env, "Nhóm rollout phải là all, 1 hoặc 2.");
    return;
  }
  if (!cmd || !ops.commands[cmd]) {
    await ops.sendMessage(
      chatId,
      env,
      "Lệnh hợp lệ: idle, setup_vip, install_track, setup_boot, " +
        "setup_caylapbu, run_caylapbu, update_delta, reboot."
    );
    return;
  }
  if (!Number.isInteger(batch) || batch < 1 || batch > MAX_BATCH) {
    await ops.sendMessage(
      chatId,
      env,
      `Kích thước đợt phải từ 1 đến ${MAX_BATCH}.`
    );
    return;
  }
  const snapshot = await eligible(target, null, env, ops);
  if (!snapshot.eligible.length) {
    await ops.sendMessage(
      chatId,
      env,
      "Không có máy online, không bảo trì và dùng Agent mới để rollout."
    );
    return;
  }
  const id =
    `${Date.now().toString(36)}-` +
    crypto.randomUUID().replace(/-/g, "").slice(0, 8);
  const state = {
    id,
    chat_id: String(chatId),
    target,
    target_label: await ops.getTargetLabel(target, env),
    command_key: cmd,
    command_value: ops.commands[cmd].value,
    batch_size: batch,
    status: "planned",
    created_at: Date.now(),
    updated_at: Date.now(),
    total_devices: snapshot.eligible.length,
    canary_id: snapshot.eligible[0],
    pending_ids: snapshot.eligible,
    success_ids: [],
    failed_ids: [],
    skipped_ids: snapshot.excluded,
    current_batch_ids: [],
    current_command_id: null,
    current_started_at: null,
    current_deadline: null,
    current_phase: null,
    current_commit: null,
    batch_number: 0,
  };
  await save(state, env);
  await env.DEVICE_STATUS.put(ACTIVE_KEY, id);
  await show(chatId, id, env, ops);
}

async function dispatch(chatId, state, requested, phase, env, ops) {
  const snapshot = await eligible(
    state.target,
    requested,
    env,
    ops
  );
  state.skipped_ids = mergeEntries(
    state.skipped_ids,
    snapshot.excluded,
    ops.compareDeviceIds
  );
  const excluded = snapshot.excluded.map((item) => item.id);
  state.pending_ids = (state.pending_ids || []).filter(
    (id) => !excluded.includes(id)
  );
  if (!snapshot.eligible.length) {
    state.current_batch_ids = [];
    state.current_command_id = null;
    state.current_started_at = null;
    state.current_deadline = null;
    state.current_phase = null;
    state.status = state.pending_ids.length
      ? "waiting_next_batch"
      : "completed";
    await save(state, env);
    if (state.status === "completed") {
      await clearActive(state.id, env);
    }
    await show(chatId, state.id, env, ops);
    return;
  }
  const commandId =
    `${Date.now()}-` +
    crypto.randomUUID().slice(0, 8);
  const created = Date.now();
  const expiresAt = created + ops.commandTtlMs;
  const value = ops.commands[state.command_key].value;
  const batchNumber = Number(state.batch_number || 0) + 1;
  const commit = await ops.updateGitHubFile(
    ops.targetFiles[state.target],
    ops.commandEnvelope(
      commandId,
      snapshot.eligible,
      expiresAt,
      [value]
    ),
    `bot: rollout ${state.id} batch ${batchNumber} ${value}`,
    env
  );
  await ops.storeCommandMetadata(
    env,
    commandId,
    "devices",
    [value],
    {
      targetLabel: `ROLLOUT ${state.target_label}`,
      fileTarget: state.target,
      deviceIds: snapshot.eligible,
      commandKeys: [state.command_key],
      created,
      expiresAt,
    }
  );
  state.batch_number = batchNumber;
  state.current_batch_ids = snapshot.eligible;
  state.current_command_id = commandId;
  state.current_started_at = created;
  state.current_deadline = expiresAt + EXTRA_WAIT_MS;
  state.current_phase = phase;
  state.current_commit = commit;
  state.failed_ids = [];
  state.status = phase === "canary"
    ? "canary_running"
    : phase === "retry"
      ? "retry_running"
      : "batch_running";
  await save(state, env);
  const title = phase === "canary"
    ? "🧪 Đã gửi máy thử"
    : phase === "retry"
      ? "🔁 Đã gửi lại máy lỗi"
      : "▶️ Đã gửi một đợt";
  await ops.sendMessage(
    chatId,
    env,
    `${title}\n` +
      `Máy: ${snapshot.eligible.join(", ")}\n` +
      `Lệnh: ${value}\n` +
      `Commit: ${commit.slice(0, 7)}\n\n` +
      "Worker tự kiểm tra mỗi phút.",
    markup(state)
  );
}

async function start(chatId, id, env, ops) {
  const state = await load(id, env);
  if (!state || state.status !== "planned") {
    await ops.sendMessage(chatId, env, "Rollout không còn chờ máy thử.");
    return;
  }
  const snapshot = await eligible(
    state.target,
    state.pending_ids,
    env,
    ops
  );
  state.skipped_ids = mergeEntries(
    state.skipped_ids,
    snapshot.excluded,
    ops.compareDeviceIds
  );
  const excluded = snapshot.excluded.map((item) => item.id);
  state.pending_ids = state.pending_ids.filter(
    (item) => !excluded.includes(item)
  );
  if (!snapshot.eligible.length) {
    state.status = "completed";
    await save(state, env);
    await clearActive(state.id, env);
    await show(chatId, state.id, env, ops);
    return;
  }
  state.canary_id = snapshot.eligible.includes(state.canary_id)
    ? state.canary_id
    : snapshot.eligible[0];
  await dispatch(
    chatId,
    state,
    [state.canary_id],
    "canary",
    env,
    ops
  );
}

async function next(chatId, id, all, env, ops) {
  const state = await load(id, env);
  if (!state || state.status !== "waiting_next_batch") {
    await ops.sendMessage(
      chatId,
      env,
      "Rollout chưa sẵn sàng gửi đợt tiếp theo."
    );
    return;
  }
  if (!(state.pending_ids || []).length) {
    state.status = "completed";
    await save(state, env);
    await clearActive(state.id, env);
    await show(chatId, state.id, env, ops);
    return;
  }
  const selected = all
    ? state.pending_ids
    : state.pending_ids.slice(0, state.batch_size);
  await dispatch(
    chatId,
    state,
    selected,
    "batch",
    env,
    ops
  );
}

async function evaluate(state, env, ops) {
  if (!RUNNING.has(state.status)) {
    return { state, changed: false };
  }
  const now = Date.now();
  const deadline = Number(state.current_deadline || 0);
  const success = [];
  const failed = [];
  const waiting = [];
  for (const id of state.current_batch_ids || []) {
    const record = await ops.getDeviceRecord(id, env);
    if (
      record &&
      record.last_command_id === state.current_command_id
    ) {
      if (record.last_status === "success") {
        success.push(id);
        continue;
      }
      if (["error", "expired"].includes(record.last_status)) {
        failed.push({
          id,
          reason: String(
            record.last_result ||
            record.last_status
          ).slice(0, 180),
        });
        continue;
      }
    }
    waiting.push(id);
  }
  if (waiting.length && now < deadline) {
    return { state, changed: false };
  }
  for (const id of waiting) {
    const record = await ops.getDeviceRecord(id, env);
    const online =
      record &&
      now - Number(record.last_seen || 0) <=
        ops.onlineWindowMs;
    failed.push({
      id,
      reason: online
        ? "hết thời gian chờ phản hồi"
        : "mất kết nối khi đang chạy",
    });
  }
  const finished = [
    ...success,
    ...failed.map((item) => item.id),
  ];
  state.success_ids = unique(
    [...(state.success_ids || []), ...success],
    ops.compareDeviceIds
  );
  state.pending_ids = (state.pending_ids || []).filter(
    (id) => !finished.includes(id)
  );
  state.failed_ids = mergeEntries(
    [],
    failed,
    ops.compareDeviceIds
  );
  state.current_batch_ids = [];
  state.current_command_id = null;
  state.current_started_at = null;
  state.current_deadline = null;
  state.current_phase = null;
  if (failed.length) {
    state.status = "paused_error";
  } else if (!state.pending_ids.length) {
    state.status = "completed";
    await clearActive(state.id, env);
  } else {
    state.status = "waiting_next_batch";
  }
  await save(state, env);
  return { state, changed: true };
}

async function refresh(chatId, id, env, ops) {
  let state = await load(id, env);
  if (!state) {
    await ops.sendMessage(chatId, env, "Không tìm thấy rollout.");
    return;
  }
  state = (await evaluate(state, env, ops)).state;
  await show(chatId, state.id, env, ops);
}

async function retry(chatId, id, env, ops) {
  const state = await load(id, env);
  if (!state || state.status !== "paused_error") {
    await ops.sendMessage(
      chatId,
      env,
      "Không có máy lỗi cần thử lại."
    );
    return;
  }
  const ids = (state.failed_ids || []).map((item) => item.id);
  if (!ids.length) {
    await ops.sendMessage(chatId, env, "Không có máy lỗi để thử lại.");
    return;
  }
  state.failed_ids = [];
  await save(state, env);
  await dispatch(
    chatId,
    state,
    ids,
    "retry",
    env,
    ops
  );
}

async function skip(chatId, id, env, ops) {
  const state = await load(id, env);
  if (!state || state.status !== "paused_error") {
    await ops.sendMessage(chatId, env, "Không có lỗi để bỏ qua.");
    return;
  }
  state.skipped_ids = mergeEntries(
    state.skipped_ids,
    state.failed_ids,
    ops.compareDeviceIds
  );
  state.failed_ids = [];
  if (!(state.pending_ids || []).length) {
    state.status = "completed";
    await clearActive(state.id, env);
  } else {
    state.status = "waiting_next_batch";
  }
  await save(state, env);
  await show(chatId, state.id, env, ops);
}


async function stop(chatId, id, env, ops) {
  const state =
    await load(id, env);
  if (!state) {
    await clearActive(
      id,
      env
    );
    await ops.sendMessage(
      chatId,
      env,
      "Không tìm thấy rollout; đã thử xóa khóa hoạt động."
    );
    return;
  }
  state.status =
    "cancelled";
  state.stopped_at =
    Date.now();
  await clearActive(
    state.id,
    env
  );
  let historySaved = true;
  try {
    await save(
      state,
      env
    );
  } catch (error) {
    historySaved = false;
    console.error(
      "rollout_stop_history_failed",
      error?.message || error
    );
  }
  await ops.sendMessage(
    chatId,
    env,
    `🛑 Đã kết thúc rollout ${state.id}.\n` +
      "Khóa gửi lệnh thường đã được gỡ trước khi lưu lịch sử.\n" +
      "Lệnh đã gửi vẫn có thể hoàn thành, nhưng không gửi thêm đợt mới." +
      (
        historySaved
          ? ""
          : "\n⚠️ KV đang chặn ghi nên chưa lưu được trạng thái cancelled."
      )
  );
}

export async function handleRolloutCommand({
  input,
  chatId,
  env,
  ops,
}) {
  if (
    input === "/rollout" ||
    /^\/rollout\s+status$/i.test(input)
  ) {
    await show(chatId, null, env, ops);
    return true;
  }
  if (/^\/rollout\s+stop$/i.test(input)) {
    const active = await getActiveRollout(env);
    if (active) {
      await stop(chatId, active.id, env, ops);
    } else {
      await ops.sendMessage(
        chatId,
        env,
        "Không có rollout đang hoạt động."
      );
    }
    return true;
  }
  const match = String(input || "").match(
    /^\/rollout\s+(\S+)\s+([A-Za-z0-9_-]+)(?:\s+(\d+))?$/i
  );
  if (!match) return false;
  await create(
    chatId,
    match[1],
    match[2],
    match[3],
    env,
    ops
  );
  return true;
}

export async function handleRolloutCallback({
  data,
  callbackId,
  chatId,
  env,
  ops,
}) {
  if (data === "show_rollout") {
    await ops.answerCallback(callbackId, env);
    await show(chatId, null, env, ops);
    return true;
  }
  const actions = [
    ["rollout_start:", start, "Đang gửi máy thử..."],
    ["rollout_next:", (c, id, e, o) => next(c, id, false, e, o), "Đang gửi đợt tiếp theo..."],
    ["rollout_all:", (c, id, e, o) => next(c, id, true, e, o), "Đang gửi các máy còn lại..."],
    ["rollout_refresh:", refresh, null],
    ["rollout_retry:", retry, "Đang thử lại máy lỗi..."],
    ["rollout_skip:", skip, "Đã bỏ qua máy lỗi."],
    ["rollout_stop:", stop, "Đang kết thúc rollout..."],
  ];
  for (const [prefix, action, notice] of actions) {
    if (!data.startsWith(prefix)) continue;
    const id = data.slice(prefix.length);
    if (!validId(id)) {
      await ops.answerCallback(
        callbackId,
        env,
        "Rollout không hợp lệ.",
        true
      );
      return true;
    }
    await ops.answerCallback(
      callbackId,
      env,
      notice || undefined
    );
    try {
      await action(chatId, id, env, ops);
    } catch (error) {
      await ops.sendMessage(
        chatId,
        env,
        `❌ Rollout thất bại: ${error.message}`
      );
    }
    return true;
  }
  return false;
}

export async function checkActiveRollout(env, ops) {
  try {
    const state = await getActiveRollout(env);
    if (!state || !RUNNING.has(state.status)) return;
    const result = await evaluate(state, env, ops);
    if (result.changed) {
      await show(
        result.state.chat_id,
        result.state.id,
        env,
        ops
      );
    }
  } catch (error) {
    console.error(
      "rollout_check_failed",
      error?.message || error
    );
  }
}
