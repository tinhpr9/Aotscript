import fs from "node:fs/promises";
import vm from "node:vm";
import crypto from "node:crypto";

const source = await fs.readFile(new URL("./worker.js", import.meta.url), "utf8");

let sentMessages = [];
let answeredCallbacks = [];
let clearedTokens = [];
let pendingAllocates = {};
let fleetControlCalls = [];
let getFleetHubStateCalls = 0;

const context = vm.createContext({
  console,
  setTimeout,
  clearTimeout,
  AbortController,
  crypto,
  env: { TELEGRAM_ADMIN_USER_ID: "123", TELEGRAM_BOT_TOKEN: "tok" },
  telegram: async (env, method, payload) => {
    if (method === "sendMessage" || method === "editMessageText") {
      sentMessages.push(payload);
    }
  },
  answerCallback: async (id, env, text, alert) => {
    answeredCallbacks.push({ id, text, alert });
  },
  resolveAndValidateTelegramTargets: async (targetStr, env) => {
    if (targetStr === "m1,m2") return ["m1", "m2"];
    if (targetStr === "offline") throw new Error("Thiết bị offline đang OFFLINE.");
    if (targetStr === "dup,dup") throw new Error("Thiết bị bị lặp.");
    return ["m1"];
  },
  fetch: async (url) => {
    return {
      ok: true,
      text: async () => `com.tinh.vv.hi,https://www.roblox.com/games/975?privateServerLinkCode=11111111111111111111111111111111
com.tinh.vv.hj,https://www.roblox.com/games/975?privateServerLinkCode=22222222222222222222222222222222
com.tinh.vv.hk,https://www.roblox.com/games/975?privateServerLinkCode=33333333333333333333333333333333
com.tinh.vv.hl,https://www.roblox.com/games/975?privateServerLinkCode=44444444444444444444444444444444
com.tinh.vv.hm,https://www.roblox.com/games/975?privateServerLinkCode=55555555555555555555555555555555
com.tinh.vv.hn,https://www.roblox.com/games/975?privateServerLinkCode=66666666666666666666666666666666
com.tinh.vv.ho,https://www.roblox.com/games/975?privateServerLinkCode=77777777777777777777777777777777
com.tinh.vv.hp,https://www.roblox.com/games/975?privateServerLinkCode=88888888888888888888888888888888
com.tinh.vv.hq,https://www.roblox.com/games/975?privateServerLinkCode=99999999999999999999999999999999
com.tinh.vv.hr,https://www.roblox.com/games/975?privateServerLinkCode=00000000000000000000000000000000
===`
    };
  },
  parseTongHopLink: (text, ids, tabs) => {
    const res = {};
    for (const id of ids) {
      res[id] = Array(tabs).fill(null).map((_, i) => ({ pkg: "com.tinh.vv.hi", url: "https://test?t=" + i }));
    }
    return res;
  },
  savePendingAllocate: async (token, spec) => {
    pendingAllocates[token] = spec;
  },
  loadPendingAllocate: async (token) => {
    return pendingAllocates[token];
  },
  clearPendingAllocate: async (token) => {
    clearedTokens.push(token);
    delete pendingAllocates[token];
  },
  fleetStateCall: async (env, path, init) => {
    if (path === "/aot/hub/control") {
      fleetControlCalls.push(init.body);
      return { response: { ok: true }, data: { batch: { action_id: "act-123", devices: [{ device_id: "m1", status: "SENT", history: ["SENT"] }] } } };
    }
    if (path === "/aot/hub/state") {
      getFleetHubStateCalls++;
      // Return terminal after 2 polls
      const status = getFleetHubStateCalls > 1 ? "OPENED" : "SENT";
      return { response: { ok: true }, data: { state: { last_batch: { action_id: "act-123", devices: [{ device_id: "m1", status, history: ["SENT", status] }] } } } };
    }
  },
  AOT_HUB_PROTOCOL_VERSION: "fleet-batch-v1"
});

// extract handleUpdate and handleCallback
const getFuncText = (startStr, endStr) => {
  const start = source.indexOf(startStr);
  const end = source.indexOf(endStr, start);
  return source.substring(start, end);
};

const handleUpdateSource = getFuncText("async function handleUpdate", "async function handlePendingSessionMessage");
if (!handleUpdateSource) throw new Error("Could not find handleUpdate");
vm.runInContext(handleUpdateSource, context);

const handleCallbackSource = getFuncText("async function handleCallback", "\nfunction toggleCommand");
if (!handleCallbackSource) throw new Error("Could not find handleCallback");
vm.runInContext(`
async function handleRolloutCallback() { return false; }
async function handleRolloutCommand() { return false; }
async function getRolloutOps() { return false; }
${handleCallbackSource}
`, context);

async function runTests() {
  const triggerMessage = async (text) => {
    sentMessages = [];
    await vm.runInContext(`handleUpdate({ message: { from: { id: "123" }, chat: { id: 1 }, text: "${text}" } }, env)`, context);
  };
  const triggerCallback = async (data) => {
    answeredCallbacks = [];
    sentMessages = [];
    await vm.runInContext(`handleCallback({ id: "cb1", data: "${data}", message: { chat: { id: 1 } }, from: { id: "123" } }, 1, 12, env, "123")`, context);
  };

  // 1. malformed tabs
  await triggerMessage("/phanserver m1 5abc");
  if (!sentMessages[0].text.includes("Số tab phải từ 1 đến 10")) throw new Error("malformed tab test failed");
  await triggerMessage("/phanserver m1 11");
  if (!sentMessages[0].text.includes("Số tab phải từ 1 đến 10")) throw new Error("out of range tab test failed");
  await triggerMessage("/phanserver m1 5.5");
  if (!sentMessages[0].text.includes("Số tab phải từ 1 đến 10")) throw new Error("float tab test failed");

  // 2. preview flow
  await triggerMessage("/phanserver m1 5");
  if (!sentMessages[0] || !sentMessages[0].text.includes("PREVIEW PHÂN SERVER")) throw new Error("preview test failed");
  const inlineKb = sentMessages[0].reply_markup.inline_keyboard[0];
  const okCallbackData = inlineKb[0].callback_data;
  const cancelCallbackData = inlineKb[1].callback_data;

  // 3. confirm / cancel
  // Cancel
  await triggerCallback(cancelCallbackData);
  if (clearedTokens[0] !== cancelCallbackData.split(":")[1]) throw new Error("cancel test failed");
  if (answeredCallbacks[0].text !== "Đã hủy PHÂN SERVER.") throw new Error("cancel response failed");

  // Confirm
  // recreate pending
  await triggerMessage("/phanserver m1 5");
  const okCb2 = sentMessages[0].reply_markup.inline_keyboard[0][0].callback_data;
  
  await triggerCallback(okCb2);
  if (clearedTokens[1] !== okCb2.split(":")[1]) throw new Error("confirm clear test failed");
  if (answeredCallbacks[0].text !== "Đang chạy phân server...") throw new Error("confirm response failed");
  if (fleetControlCalls[0].kind !== "allocate_server") throw new Error("fleet control dispatch failed");
  if (!sentMessages[0] || !sentMessages[0].text.includes("đang chờ thiết bị phản hồi")) throw new Error("final intermediate result message failed");

  // Duplicate device
  await triggerMessage("/phanserver dup,dup 5");
  if (!sentMessages[0].text.includes("Lỗi: Thiết bị bị lặp")) throw new Error("dup test failed");

  // Offline device
  await triggerMessage("/phanserver offline 5");
  if (!sentMessages[0].text.includes("Lỗi: Thiết bị offline đang OFFLINE")) throw new Error("offline test failed");

  console.log("AOT_TELEGRAM_PHANSERVER_SELFTEST=OK");
}

runTests().catch(e => { console.error(e); process.exit(1); });
