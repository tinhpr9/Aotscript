const OWNER = "tinhpr9";
const REPO = "Aotscript";
const BRANCH = "main";

const TARGET_FILES = {
  all: "lenh_all.txt",
  marmot: "lenh_marmot.txt",
  nova: "lenh_nova.txt",
};

const COMMANDS = {
  idle: { value: "IDLE", bit: 1, label: "💤 IDLE" },
  setup_vip: { value: "SETUP_VIP", bit: 2, label: "⚙️ SETUP_VIP" },
  install_track: { value: "INSTALL_TRACK", bit: 4, label: "📡 INSTALL_TRACK" },
  setup_boot: { value: "SETUP_BOOT", bit: 8, label: "🔧 SETUP_BOOT" },
  setup_caylapbu: { value: "SETUP_CAYLAPBU", bit: 16, label: "🌱 SETUP_CAYLAPBU" },
  run_caylapbu: { value: "RUN_CAYLAPBU", bit: 32, label: "▶️ RUN_CAYLAPBU" },
  update_delta: { value: "UPDATE_DELTA", bit: 64, label: "🔁 UPDATE_DELTA" },
  update_solver: { value: "UPDATE_SOLVER", bit: 256, label: "🧠 UPDATE_SOLVER" },
  update_script: { value: "UPDATESCRIPT", bit: 512, label: "📝 UPDATESCRIPT" },
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

const ALLOWED_REPORT_STATUSES = ["heartbeat", "received", "running", "success", "error"];

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
  const replyMarkup = {
    inline_keyboard: [
      [
        { text: "🌍 TẤT CẢ", callback_data: `shortcmd:${command}:${token}:all` },
        { text: "🦫 MARMOT", callback_data: `shortcmd:${command}:${token}:marmot` },
      ],
      [
        { text: "✨ NOVA", callback_data: `shortcmd:${command}:${token}:nova` },
        { text: "❌ HỦY", callback_data: `cancel:${token}` },
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

async function executeSessionCommands(chatId, state, env) {
  const file = TARGET_FILES[state.target];
  const normalizedKeys = [...new Set(state.commandKeys)];

  if (!file || normalizedKeys.length === 0 || normalizedKeys.some((key) => !COMMANDS[key])) {
    await sendMessage(chatId, env, "Target hoặc lệnh không hợp lệ.");
    await clearSessionState(state.userId, state.chatId);
    return;
  }

  if (normalizedKeys.includes("idle") && normalizedKeys.length > 1) {
    await sendMessage(chatId, env, "IDLE phải đứng một mình, không thể gửi kèm lệnh khác.");
    await clearSessionState(state.userId, state.chatId);
    return;
  }

  let orderedKeys = COMMAND_ORDER.filter((key) => normalizedKeys.includes(key));
  if (orderedKeys.includes("reboot")) {
    orderedKeys = orderedKeys.filter((k) => k !== "reboot");
    orderedKeys.push("reboot");
  }

  const commandLines = orderedKeys.map((key) => {
    if (key === "update_solver") {
      return `/solver ${state.target} ${state.solverUrl}`;
    }
    if (key === "update_script") {
      return `/script ${state.target} ${state.scriptUrl}`;
    }
    return COMMANDS[key].value;
  });

  const commandId = `${Date.now()}-${crypto.randomUUID().slice(0, 8)}`;
  const content = `# telegram_command_id=${commandId}\n${commandLines.join("\n")}\n`;

  try {
    const commit = await updateGitHubFile(
      file,
      content,
      `bot: ${state.target.toUpperCase()} ${commandLines.join(" + ")}`,
      env
    );
      const replyMarkup = {
        inline_keyboard: [[
          { text: "CẬP NHẬT TIẾN ĐỘ", callback_data: `show_progress` },
          { text: "DANH SÁCH MÁY", callback_data: `show_devices` },
        ]],
      };

      try {
        const pseudoCommandId = content.match(/telegram_command_id=([\w\-]+)/)?.[1] || `${Date.now()}-${crypto.randomUUID().slice(0,8)}`;
        await env.DEVICE_STATUS.put(`cmd:${pseudoCommandId}`, JSON.stringify({ target: state.target, total_expected: commandLines.length, created: Date.now() }));
      } catch (e) {
        console.error('KV put cmd meta failed', e);
      }

      await sendMessage(
        chatId,
        env,
        `✅ Đã gửi ${commandLines.length} lệnh\n` +
          `Nhóm: ${state.target.toUpperCase()}\n` +
          `Lệnh:\n- ${commandLines.join("\n- ")}\n` +
          `File: ${file}\n` +
          `Commit: ${commit.slice(0, 7)}`,
        replyMarkup
      );
  } catch (error) {
    await sendMessage(chatId, env, `❌ Không gửi được lệnh: ${error.message}`);
  } finally {
    await clearSessionState(state.userId, state.chatId);
  }
}

async function handleCallback(callback, chatId, messageId, env, fromId) {
  const data = callback.data || "";

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

    const needsConfirm = commandKeys.some((k) => ["run_caylapbu", "update_delta", "reboot"].includes(k));
    if (needsConfirm) {
      await answerCallback(callback.id, env);
      const confirmMarkup = {
        inline_keyboard: [
          [
            { text: "❗ Xác nhận gửi", callback_data: `confirm:${target}:${mask}` },
            { text: "❌ Hủy", callback_data: `cancel:${target}:${mask}` },
          ],
        ],
      };
      await sendMessage(chatId, env, `Xác nhận gửi ${commandKeys.length} lệnh?`, confirmMarkup);
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

async function showTargets(chatId, env, messageId) {
  const textValue = "Chọn nhóm máy:";
  const replyMarkup = {
    inline_keyboard: [
      [
        { text: "🌍 TẤT CẢ", callback_data: "target:all" },
        { text: "🦫 MARMOT", callback_data: "target:marmot" },
      ],
      [
        { text: "✨ NOVA", callback_data: "target:nova" },
        { text: "📋 TRẠNG THÁI", callback_data: "status" },
      ],
    ],
  };

  if (messageId) {
    await editMessage(chatId, messageId, env, textValue, replyMarkup);
  } else {
    await sendMessage(chatId, env, textValue, replyMarkup);
  }
}

async function showCommands(chatId, target, mask, env, messageId) {
  const label = target.toUpperCase();
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
      [button("idle"), button("setup_vip"), button("install_track"), button("setup_boot")],
      [button("setup_caylapbu"), button("run_caylapbu"), button("update_delta"), button("update_solver")],
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

async function executeCommands(chatId, target, commandKeys, env) {
  const file = TARGET_FILES[target];
  const normalizedKeys = [...new Set(commandKeys)];

  if (!file || normalizedKeys.length === 0 || normalizedKeys.some((key) => !COMMANDS[key])) {
    await sendMessage(chatId, env, "Target hoặc lệnh không hợp lệ.");
    return;
  }

  if (normalizedKeys.includes("idle") && normalizedKeys.length > 1) {
    await sendMessage(chatId, env, "IDLE phải đứng một mình, không thể gửi kèm lệnh khác.");
    return;
  }

  let orderedKeys = COMMAND_ORDER.filter((key) => normalizedKeys.includes(key));
  // Ensure REBOOT if present is always last
  if (orderedKeys.includes("reboot")) {
    orderedKeys = orderedKeys.filter((k) => k !== "reboot");
    orderedKeys.push("reboot");
  }
  const commandValues = orderedKeys.map((key) => COMMANDS[key].value);
  const commandId = `${Date.now()}-${crypto.randomUUID().slice(0, 8)}`;
  const content = `# telegram_command_id=${commandId}\n${commandValues.join("\n")}\n`;

  try {
    const commit = await updateGitHubFile(
      file,
      content,
      `bot: ${target.toUpperCase()} ${commandValues.join(" + ")}`,
      env
    );
      const replyMarkup = {
        inline_keyboard: [[
          { text: "CẬP NHẬT TIẾN ĐỘ", callback_data: `show_progress` },
          { text: "DANH SÁCH MÁY", callback_data: `show_devices` },
        ]],
      };

      // store command metadata for progress aggregation
      try {
        await env.DEVICE_STATUS.put(`cmd:${commandId}`, JSON.stringify({ target, total_expected: commandValues.length, created: Date.now() }));
      } catch (e) {
        console.error('KV put cmd meta failed', e);
      }

      await sendMessage(
        chatId,
        env,
        `✅ Đã gửi ${commandValues.length} lệnh\n` +
          `Nhóm: ${target.toUpperCase()}\n` +
          `Lệnh:\n- ${commandValues.join("\n- ")}\n` +
          `File: ${file}\n` +
          `Commit: ${commit.slice(0, 7)}`,
        replyMarkup
      );
  } catch (error) {
    await sendMessage(chatId, env, `❌ Không gửi được lệnh: ${error.message}`);
  }
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

async function executeSolverCommand(chatId, target, url, env) {
  const file = TARGET_FILES[target];
  const commandId = `${Date.now()}-${crypto.randomUUID().slice(0, 8)}`;
  const line = `/solver ${target} ${url}`;
  const content = `# telegram_command_id=${commandId}\n${line}\n`;
  try {
    const commit = await updateGitHubFile(file, content, `bot: ${target.toUpperCase()} /solver`, env);
    await sendMessage(chatId, env, `✅ Đã gửi lệnh /solver\nNhóm: ${target}\nURL: ${url}\nCommit: ${commit.slice(0,7)}`);
  } catch (err) {
    await sendMessage(chatId, env, `❌ Không gửi được /solver: ${err.message}`);
  }
}

async function executeScriptCommand(chatId, target, url, env) {
  const file = TARGET_FILES[target];
  const commandId = `${Date.now()}-${crypto.randomUUID().slice(0, 8)}`;
  const line = `/script ${target} ${url}`;
  const content = `# telegram_command_id=${commandId}\n${line}\n`;
  try {
    const commit = await updateGitHubFile(file, content, `bot: ${target.toUpperCase()} /script`, env);
    await sendMessage(chatId, env, `✅ Đã gửi lệnh /script\nNhóm: ${target}\nURL: ${url}\nCommit: ${commit.slice(0,7)}`);
  } catch (err) {
    await sendMessage(chatId, env, `❌ Không gửi được /script: ${err.message}`);
  }
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
  const secret = request.headers.get("X-Agent-Secret");
  if (!secret || secret !== env.AGENT_REPORT_SECRET) {
    return new Response("Unauthorized", { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch (e) {
    return json({ ok: false, error: "invalid_json" }, 400);
  }

  const { device_id, device_group, timestamp, status, command_id, command, command_index, command_total, last_result } = body;

  if (!device_id || !device_group || !timestamp || !status) {
    return json({ ok: false, error: "missing_fields" }, 400);
  }

  // Validate status
  if (!ALLOWED_REPORT_STATUSES.includes(status)) {
    return json({ ok: false, error: "invalid_status" }, 400);
  }

  // Validate device_id and group
  const didMatch = device_id.match(/^(MARMOT|NOVA)-(\d{2})$/);
  if (!didMatch) return json({ ok: false, error: "invalid_device_id" }, 400);
  const prefix = didMatch[1];
  const idx = parseInt(didMatch[2], 10);
  if (idx < 1 || idx > 10) return json({ ok: false, error: "device_index_out_of_range" }, 400);
  if (device_group !== prefix) return json({ ok: false, error: "group_mismatch" }, 400);

  // store/update device record in KV
  try {
    const key = device_id;
    let rec = {};
    try {
      const existing = await env.DEVICE_STATUS.get(key);
      if (existing) rec = JSON.parse(existing);
    } catch (e) {
      rec = {};
    }

    const now = Date.now();
    rec.device_id = device_id;
    rec.device_group = device_group;
    rec.last_seen = now;
    rec.last_status = status;
    if (command_id) {
      rec.last_command_id = command_id;
      rec.last_command = command || rec.last_command || null;
      rec.last_command_index = command_index || rec.last_command_index || null;
      rec.last_command_total = command_total || rec.last_command_total || null;
      rec.last_command_ts = Date.parse(timestamp) || now;
    }
    if (last_result) rec.last_result = last_result;

    await env.DEVICE_STATUS.put(key, JSON.stringify(rec));
    return json({ ok: true });
  } catch (e) {
    console.error(e);
    return json({ ok: false, error: String(e?.message || e) }, 500);
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

async function showDevices(chatId, env) {
  const now = Date.now();
  const lines = ["📋 Danh sách 20 thiết bị:"];
  const ids = [];
  for (let i = 1; i <= 10; i++) ids.push(`MARMOT-${String(i).padStart(2,'0')}`);
  for (let i = 1; i <= 10; i++) ids.push(`NOVA-${String(i).padStart(2,'0')}`);

  for (const id of ids) {
    const rec = await getDeviceRecord(id, env);
    if (!rec) {
      lines.push(`${id} — offline — group: ${id.split('-')[0]} — last_seen: -`);
      continue;
    }
    const online = now - (rec.last_seen || 0) <= 90 * 1000;
    lines.push(`${id} — ${online ? 'online' : 'offline'} — group: ${rec.device_group} — last_seen: ${formatTimestamp(rec.last_seen)} — status: ${rec.last_status || '-'}${rec.last_command_id ? ' — cmd:' + rec.last_command_id : ''}`);
  }

  await sendMessage(chatId, env, lines.join('\n'));
}

async function sendProgress(chatId, env) {
  // find latest command meta stored in KV
  try {
    // scan known cmd keys by listing (KV list), fallback: scan devices to find newest last_command_ts
    let latest = { cmd: null, ts: 0 };
    // check device records
    const ids = [];
    for (let i = 1; i <= 10; i++) ids.push(`MARMOT-${String(i).padStart(2,'0')}`);
    for (let i = 1; i <= 10; i++) ids.push(`NOVA-${String(i).padStart(2,'0')}`);
    const recs = [];
    for (const id of ids) {
      const r = await getDeviceRecord(id, env);
      if (r) recs.push(r);
      if (r && r.last_command_ts && r.last_command_ts > latest.ts) {
        latest = { cmd: r.last_command_id, ts: r.last_command_ts };
      }
    }

    if (!latest.cmd) {
      return sendMessage(chatId, env, 'Không tìm thấy command gần đây để báo tiến độ.');
    }

    // try load command meta
    let meta = null;
    try {
      const mraw = await env.DEVICE_STATUS.get(`cmd:${latest.cmd}`);
      if (mraw) meta = JSON.parse(mraw);
    } catch (e) {}

    const target = meta?.target || 'unknown';
    const total_expected = meta?.total_expected || (target === 'marmot' || target === 'nova' ? 10 : 20);

    const counts = { received: 0, running: 0, success: 0, error: 0, offline: 0, no_response: 0 };
    const now = Date.now();
    for (const id of ids) {
      const r = await getDeviceRecord(id, env);
      if (!r) {
        counts.offline += 1;
        continue;
      }
      // if device is offline
      if (now - (r.last_seen || 0) > 90 * 1000) {
        counts.offline += 1;
        continue;
      }
      if (r.last_command_id !== latest.cmd) {
        counts.no_response += 1;
        continue;
      }
      const s = r.last_status;
      if (s === 'received') counts.received += 1;
      else if (s === 'running') counts.running += 1;
      else if (s === 'success') counts.success += 1;
      else if (s === 'error') counts.error += 1;
      else counts.no_response += 1;
    }

    const lines = [
      `📊 Tiến độ cho command: ${latest.cmd}`,
      `Nhóm: ${target.toUpperCase()}`,
      `Tổng máy dự kiến: ${total_expected}`,
      `Received: ${counts.received}`,
      `Running: ${counts.running}`,
      `Success: ${counts.success}`,
      `Error: ${counts.error}`,
      `Offline: ${counts.offline}`,
      `No response / other: ${counts.no_response}`,
    ];

    await sendMessage(chatId, env, lines.join('\n'));
  } catch (e) {
    await sendMessage(chatId, env, `Lỗi khi tổng hợp tiến độ: ${e.message}`);
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
