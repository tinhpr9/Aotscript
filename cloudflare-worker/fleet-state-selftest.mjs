import fs from "node:fs/promises";
import vm from "node:vm";

const context = vm.createContext({ URL, Request, Response, Headers, JSON, Map, Set, Object, Array, String, Number, Boolean, Math, Date, console, crypto, structuredClone });
const telegramCalls = [];
context.telegramMockStatus = 200;
context.fetch = async (url, options) => {
  if (url.includes("api.telegram.org")) {
    telegramCalls.push({ url, options });
    return { ok: context.telegramMockStatus >= 200 && context.telegramMockStatus < 300, status: context.telegramMockStatus, json: async () => ({}) };
  }
  return { ok: true, json: async () => ({}) };
};
const cf = new vm.SyntheticModule(["DurableObject"], function () {
  this.setExport("DurableObject", class { constructor(ctx, env) { this.ctx = ctx; this.env = env; } });
}, { context });
const source = await fs.readFile(new URL("./fleet-state.js", import.meta.url), "utf8");
const mod = new vm.SourceTextModule(source, { context });
await mod.link(async specifier => { if (specifier === "cloudflare:workers") return cf; throw new Error(specifier); });
await mod.evaluate();



const store = new Map();
const sockets = new Map();
const ctx = {
  storage: { get: async k => store.get(k), put: async (k, v) => store.set(k, structuredClone(v)), setAlarm: async () => {}, list: async () => new Map() },
  getWebSockets: tag => sockets.get(tag) || [], getTags: () => [], acceptWebSocket() {}, waitUntil() {},
};
const fleet = new mod.namespace.FleetState(ctx, { TELEGRAM_BOT_TOKEN: "tok" });
const ids = Array.from({ length: 40 }, (_, i) => `m${i + 201}`);
const record = await fleet.readFleet();
for (const id of ids) { record.devices[id] = { device_id: id }; sockets.set(`aot-device:${id}`, [{ send() {} }]); }
await fleet.writeFleet(record);

let response = await fleet.dispatchFleetBatch(record, "OPEN_SWIFT_BACKUP", ids.slice(0, 2));
let body = await response.json();
if (!body.ok || body.batch.devices.length !== 2 || body.batch.devices.some(d => d.status !== "SENT")) throw new Error("dynamic subset batch failed");
if (record.last_batch.devices[ids[2]]) throw new Error("unselected device received batch");
sockets.set(`aot-device:${ids[1]}`, []);
response = await fleet.dispatchFleetBatch(record, "OPEN_SWIFT_APPS", ids.slice(0, 2));
body = await response.json();
if (body.batch.devices.find(d => d.device_id === ids[1]).status !== "SKIPPED_OFFLINE") throw new Error("offline device not skipped");
if (body.batch.action !== "OPEN_SWIFT_APPS") throw new Error("Apps action lost");

for (const id of ids) sockets.set(`aot-device:${id}`, [{ send() {} }]);
response = await fleet.dispatchFleetBatch(record, "OPEN_SWIFT_BACKUP", ids);
body = await response.json();
if (!body.ok || body.batch.devices.length !== 40) throw new Error("40-device batch failed");
const actionId = record.last_batch.action_id;
for (const id of ids) {
  for (const status of ["ACCEPTED", "OPENED"]) {
    response = await fleet.dispatchFleetAck(new Request("https://test/aot/ack", { method: "POST", body: JSON.stringify({ protocol: "fleet-batch-v1", device_id: id, action_id: actionId, batch_action: "OPEN_SWIFT_BACKUP", status, executed: status === "OPENED" }) }));
    if (!response.ok) throw new Error(`ACK failed ${id}/${status}`);
  }
}
if (Object.values((await fleet.readFleet()).last_batch.devices).some(d => d.status !== "OPENED")) throw new Error("40 ACK state failed");

response = await fleet.dispatchFleetAck(new Request("https://test/aot/ack", { method: "POST", body: JSON.stringify({ protocol: "fleet-batch-v1", device_id: ids[0], action_id: actionId, batch_action: "OPEN_SWIFT_BACKUP", status: "DUPLICATE", executed: false }) }));
if (!response.ok || (await fleet.readFleet()).last_batch.devices[ids[0]].status !== "OPENED") throw new Error("dedupe terminal state changed");

// Monotonic tests
response = await fleet.dispatchFleetBatch(record, "BACKUP_RESTORE_DATA", [ids[0]]);
body = await response.json();
const bActionId = body.batch.action_id;
const ack = async (status, executed = false) => fleet.dispatchFleetAck(new Request("https://test/aot/ack", { method: "POST", body: JSON.stringify({ protocol: "fleet-batch-v1", device_id: ids[0], action_id: bActionId, batch_action: "BACKUP_RESTORE_DATA", status, executed }) }));
await ack("ACCEPTED");
await ack("FILTERED");
await ack("SWIFT_OPENED"); // regressive
if ((await fleet.readFleet()).last_batch.devices[ids[0]].status !== "FILTERED") throw new Error("monotonicity violated (regressive)");
await ack("BACKUP_STARTED"); // terminal for this action
await ack("SELECTED"); // late/post-terminal
if ((await fleet.readFleet()).last_batch.devices[ids[0]].status !== "BACKUP_STARTED") throw new Error("monotonicity violated (post-terminal)");

// Terminal executed=true retention test
response = await fleet.dispatchFleetBatch(record, "BACKUP_RESTORE_DATA", [ids[1]]);
body = await response.json();
const retentionActionId = body.batch.action_id;
const retentionAck = async (status, executed) => fleet.dispatchFleetAck(new Request("https://test/aot/ack", { method: "POST", body: JSON.stringify({ protocol: "fleet-batch-v1", device_id: ids[1], action_id: retentionActionId, batch_action: "BACKUP_RESTORE_DATA", status, executed }) }));
await retentionAck("ACCEPTED", false);
await retentionAck("TIMEOUT", true); // terminal transition
const batchViewAfterTimeout = fleet.aotBatchView((await fleet.readFleet()).last_batch);
if (!batchViewAfterTimeout.devices.find(d => d.device_id === ids[1]).executed) throw new Error("executed=true not propagated to dashboard");
await retentionAck("FAILED", false); // should be ignored
const batchViewAfterFailed = fleet.aotBatchView((await fleet.readFleet()).last_batch);
if (!batchViewAfterFailed.devices.find(d => d.device_id === ids[1]).executed || batchViewAfterFailed.devices.find(d => d.device_id === ids[1]).status !== "TIMEOUT") throw new Error("executed=true retention violated");

// Test reason, app_count, and selected_count propagation
response = await fleet.dispatchFleetBatch(record, "BACKUP_RESTORE_DATA", [ids[2]]);
body = await response.json();
const infoActionId = body.batch.action_id;
const infoAck = async (status, extra = {}) => fleet.dispatchFleetAck(new Request("https://test/aot/ack", { method: "POST", body: JSON.stringify({ protocol: "fleet-batch-v1", device_id: ids[2], action_id: infoActionId, batch_action: "BACKUP_RESTORE_DATA", status, executed: false, ...extra }) }));
await infoAck("BACKUP_STARTED", { app_count: 5, selected_count: 3, reason: "started_ok" });
const batchViewAfterStarted = fleet.aotBatchView((await fleet.readFleet()).last_batch);
const dStarted = batchViewAfterStarted.devices.find(d => d.device_id === ids[2]);
if (dStarted.app_count !== 5 || dStarted.selected_count !== 3 || dStarted.reason !== "started_ok") throw new Error("count/reason propagation failed");
await infoAck("FAILED", { reason: "test_reason" }); // ignored because BACKUP_STARTED is terminal
const batchViewAfterInfoFailed = fleet.aotBatchView((await fleet.readFleet()).last_batch);
const dFailedInfo = batchViewAfterInfoFailed.devices.find(d => d.device_id === ids[2]);
if (dFailedInfo.status !== "BACKUP_STARTED" || dFailedInfo.reason !== "started_ok") throw new Error("reason overwritten by late ack");

// Allocate Server ACK test
let lastAllocPayload = null;
sockets.set(`aot-device:${ids[0]}`, [{ send(msg) { lastAllocPayload = JSON.parse(msg); } }]);
response = await fleet.dispatchFleetBatch(record, "ALLOCATE_SERVER", [ids[0]], { allocationMap: { [ids[0]]: [{ pkg: "com.tinh.vv.hi", url: "https://test" }] } });
body = await response.json();
if (!lastAllocPayload || lastAllocPayload.action !== "ALLOCATE_SERVER" || !lastAllocPayload.allocation || lastAllocPayload.allocation[0].pkg !== "com.tinh.vv.hi") throw new Error("Socket payload missing allocation map for device");
const allocActionId = body.batch.action_id;
const allocAck = async (status, executed = false) => fleet.dispatchFleetAck(new Request("https://test/aot/ack", { method: "POST", body: JSON.stringify({ protocol: "fleet-batch-v1", device_id: ids[0], action_id: allocActionId, batch_action: "ALLOCATE_SERVER", status, executed }) }));
await allocAck("ACCEPTED");
if ((await fleet.readFleet()).last_batch.devices[ids[0]].status !== "ACCEPTED") throw new Error("ALLOCATE_SERVER ACCEPTED failed");
await allocAck("ALLOCATED");
if ((await fleet.readFleet()).last_batch.devices[ids[0]].status !== "ALLOCATED") throw new Error("ALLOCATE_SERVER ALLOCATED failed");
await allocAck("OPENED", true);
if ((await fleet.readFleet()).last_batch.devices[ids[0]].status !== "OPENED") throw new Error("ALLOCATE_SERVER OPENED failed");
await allocAck("FAILED"); // ignored because OPENED is terminal
if ((await fleet.readFleet()).last_batch.devices[ids[0]].status !== "OPENED") throw new Error("ALLOCATE_SERVER terminal override failed");

// Test offline device fail-closed
sockets.delete(`aot-device:${ids[1]}`);
response = await fleet.dispatchFleetBatch(record, "ALLOCATE_SERVER", [ids[0], ids[1]], { allocationMap: {} });
body = await response.json();
if (body.ok || body.error !== "offline_devices_in_allocate_batch") throw new Error("ALLOCATE_SERVER offline fail-closed test failed: " + JSON.stringify(body));
sockets.set(`aot-device:${ids[1]}`, [{ send() {} }]);

// Telegram integration test for ALLOCATE_SERVER
telegramCalls.length = 0;
response = await fleet.dispatchFleetBatch(record, "ALLOCATE_SERVER", [ids[0]], { allocationMap: { [ids[0]]: [{ pkg: "com.tinh.vv.hi", url: "https://test" }] }, telegram_chat_id: 999 });
body = await response.json();
const allocActionId2 = body.batch.action_id;
const allocAck2 = async (status, executed = false) => fleet.dispatchFleetAck(new Request("https://test/aot/ack", { method: "POST", body: JSON.stringify({ protocol: "fleet-batch-v1", device_id: ids[0], action_id: allocActionId2, batch_action: "ALLOCATE_SERVER", status, executed }) }));
await allocAck2("ACCEPTED");
if (telegramCalls.length !== 0) throw new Error("Telegram called prematurely");
await allocAck2("ALLOCATED");
if (telegramCalls.length !== 0) throw new Error("Telegram called prematurely");
await allocAck2("OPENED", true);
if (telegramCalls.length !== 1) throw new Error("Telegram not called on terminal ACK");
const telegramBody = JSON.parse(telegramCalls[0].options.body);
if (telegramBody.chat_id !== 999) throw new Error("Telegram chat_id mismatch");
if (!telegramBody.text.includes("OPENED")) throw new Error("Telegram text mismatch");
await allocAck2("FAILED");
if (telegramCalls.length !== 1) throw new Error("Telegram called again on duplicate ACK");

// Telegram timeout test
telegramCalls.length = 0;
response = await fleet.dispatchFleetBatch(record, "ALLOCATE_SERVER", [ids[0]], { allocationMap: { [ids[0]]: [{ pkg: "com.tinh.vv.hi", url: "https://test" }] }, telegram_chat_id: 888 });
const allocActionId3 = (await response.json()).batch.action_id;
let fleetRecord = await fleet.readFleet();
fleetRecord.last_batch.expires_at = Date.now() - 1000;
await fleet.writeFleet(fleetRecord);
await fleet.alarm();
if (telegramCalls.length !== 1) throw new Error("Telegram not called on timeout");
const timeoutBody = JSON.parse(telegramCalls[0].options.body);
if (timeoutBody.chat_id !== 888) throw new Error("Telegram timeout chat_id mismatch");
if (!timeoutBody.text.includes("TIMEOUT")) throw new Error("Telegram timeout text mismatch");

// Pending allocate test
response = await fleet.controlFleetHub(new Request("https://test", { method: "POST", body: JSON.stringify({ protocol: "fleet-batch-v1", kind: "pending_allocate_save", token: "tok1", spec: { test: 1 } }) }));
if (!(await response.json()).ok) throw new Error("pending_allocate_save failed");
response = await fleet.controlFleetHub(new Request("https://test", { method: "POST", body: JSON.stringify({ protocol: "fleet-batch-v1", kind: "pending_allocate_consume", token: "tok1" }) }));
body = await response.json();
if (!body.ok || body.spec.test !== 1) throw new Error("pending_allocate_consume failed");
response = await fleet.controlFleetHub(new Request("https://test", { method: "POST", body: JSON.stringify({ protocol: "fleet-batch-v1", kind: "pending_allocate_consume", token: "tok1" }) }));
if ((await response.json()).ok) throw new Error("pending_allocate_consume should be atomic one-time");

const file = source;
if (!file.includes("AOT_UPDATE_GROUP_SIZE = 5") || !file.includes("ROLLED_BACK")) throw new Error("update rollout/rollback lost");
const fleetProtocol = file.slice(file.indexOf("async readFleet()"));
for (const bad of [/\bm[1-9][0-9]{0,5}\b/i, /x_norm/, /y_norm/]) if (bad.test(fleetProtocol)) throw new Error(`hard-coded identity/coordinate: ${bad}`);

// Auto-heal rollout tests
const sRecord = {
  reference_device_id: ids[0],
  followers: { [ids[1]]: true },
  last_update: {
    active_group: null,
    groups: [ [{ device_id: ids[1] }] ],
    final_deadline: Date.now() - 10000,
    created_at: Date.now() - 80000,
    devices: { [ids[1]]: { status: "QUEUED" } }
  }
};
await store.set("aot_session:test_session", sRecord);

let updateResp = await fleet.startWorkerUpdate("test_session", sRecord, "stable");
let updateBody = await updateResp.json();
if (updateBody.error === "worker_update_in_progress") throw new Error("Stale rollout blocked new update");
let state = await store.get("aot_session:test_session");
if (state.last_update.active_group !== null || state.last_update.groups.length > 0 || state.last_update.failed !== true) throw new Error("Stale rollout not properly cleaned up");

sRecord.last_update = {
  active_group: [{ device_id: ids[1] }],
  groups: [],
  final_deadline: Date.now() + 10000,
  created_at: Date.now() - 10000,
  devices: { [ids[1]]: { status: "QUEUED" } }
};
await store.set("aot_session:test_session_active", sRecord);
updateResp = await fleet.startWorkerUpdate("test_session_active", sRecord, "stable");
updateBody = await updateResp.json();
if (updateBody.error !== "worker_update_in_progress") throw new Error("Active rollout did not block new update");

sRecord.last_update = {
  active_group: [{ device_id: ids[1] }],
  groups: [],
  final_deadline: Date.now() - 10000, // Timeout
  created_at: Date.now() - 100000,
  devices: { [ids[1]]: { status: "QUEUED" } }
};
await store.set("aot_session:test_session_timeout", sRecord);
updateResp = await fleet.startWorkerUpdate("test_session_timeout", sRecord, "stable");
updateBody = await updateResp.json();
if (updateBody.error === "worker_update_in_progress") throw new Error("Timeout rollout blocked new update");
state = await store.get("aot_session:test_session_timeout");
if (state.last_update.devices[ids[1]].status !== "FAILED" || state.last_update.devices[ids[1]].reason !== "worker_ack_timeout") throw new Error("Timeout active group not transitioned to FAILED");
if (state.last_update.active_group !== null || state.last_update.groups.length > 0 || state.last_update.failed !== true) throw new Error("Timeout rollout not properly cleaned up");

sRecord.last_update = {
  active_group: [{ device_id: ids[1] }],
  groups: [ [{ device_id: ids[2] }] ],
  final_deadline: Date.now() + 10000,
  created_at: Date.now() - 10000,
  devices: { [ids[1]]: { status: "HEALTHY" }, [ids[2]]: { status: "QUEUED" } }
};
await store.set("aot_session:test_session_healthy_pending", sRecord);
updateResp = await fleet.startWorkerUpdate("test_session_healthy_pending", sRecord, "stable");
updateBody = await updateResp.json();
if (updateBody.error !== "worker_update_in_progress") throw new Error("Active HEALTHY rollout with pending groups incorrectly aborted");

// Test device_group propagation to getFleetHubState
const groupRecord = await fleet.readFleet();
groupRecord.devices["MARMOT-01"] = { device_id: "MARMOT-01", device_group: "MARMOT" };
await fleet.writeFleet(groupRecord);
const stateResp = await fleet.getFleetHubState();
const stateBody = await stateResp.json();
const marmotDevice = stateBody.state.devices.find(d => d.device_id === "MARMOT-01");
if (!marmotDevice || marmotDevice.device_group !== "MARMOT") throw new Error("device_group not exposed in getFleetHubState");

// Test device_group preservation on heartbeat without device_group
const heartbeatReq = new Request("https://fleet.test/api/report?id=MARMOT-01", {
  method: "POST",
  body: JSON.stringify({ status: "heartbeat" })
});
await fleet.report(heartbeatReq);
const afterHeartbeatRecord = await fleet.readFleet();
if (afterHeartbeatRecord.devices["MARMOT-01"].device_group !== "MARMOT") {
  throw new Error("device_group was overwritten by a heartbeat without device_group");
}

// Test explicit targets
sRecord.last_update = null;
fleet.resolveWorkerRelease = async () => ({ version: "2026.08.13.1", hash: "mock" });
sRecord.canary_release = { status: "HEALTHY", version: "aot-worker-2026.08.15.01", device_ids: [ids[0], ids[1]] };
sRecord.devices = sRecord.devices || {};
sRecord.devices[ids[2]] = { device_id: ids[2], role: "follower", added_at: Date.now() };
sRecord.followers = sRecord.followers || {};
sRecord.followers[ids[2]] = true;
await store.set("aot_session:test_explicit_targets", sRecord);

// Canary with explicit targets (keeps exactly the target)
let explicitCanaryResp = await fleet.startWorkerUpdate("test_explicit_targets", sRecord, "canary", [ids[0]]);
let explicitCanaryBody = await explicitCanaryResp.json();
if (explicitCanaryBody.ok !== true || explicitCanaryBody.update.selected_device_ids.length !== 1 || explicitCanaryBody.update.selected_device_ids[0] !== ids[0]) {
  throw new Error("Explicit canary update did not select exactly the targeted device");
}
await fleet.abortUpdateRollout("test_explicit_targets", sRecord);
sRecord.canary_release = { status: "HEALTHY", version: "aot-worker-2026.08.15.01", device_ids: [ids[0], ids[1]] };
await store.set("aot_session:test_explicit_targets", sRecord);

// Stable with explicit targets (keeps exactly the targets)
let explicitStableResp = await fleet.startWorkerUpdate("test_explicit_targets", sRecord, "stable", [ids[1], ids[2]]);
let explicitStableBody = await explicitStableResp.json();
if (explicitStableBody.ok !== true || explicitStableBody.update.devices.length !== 2) {
  console.log("STABLE BODY:", JSON.stringify(explicitStableBody, null, 2));
  throw new Error("Explicit stable update did not select exactly the targeted devices");
}
const selectedIds = explicitStableBody.update.devices.map(d => d.device_id).sort();
const expectedIds = [ids[1], ids[2]].sort();
if (selectedIds.join(",") !== expectedIds.join(",")) {
  throw new Error("Explicit stable update selected wrong devices: " + selectedIds.join(","));
}
await fleet.abortUpdateRollout("test_explicit_targets", sRecord);

// Invalid/missing targets fail closed
let invalidTargetResp = await fleet.startWorkerUpdate("test_explicit_targets", sRecord, "canary", ["INVALID-DEVICE"]);
let invalidTargetBody = await invalidTargetResp.json();
if (invalidTargetBody.ok === true || invalidTargetBody.error !== "invalid_explicit_targets") {
  throw new Error("Invalid explicit target did not fail closed");
}

// Telegram Retry Tests
const tgIds = ["m901", "m902"];
let tgRecord = await fleet.readFleet();
for (const id of tgIds) { tgRecord.devices[id] = { device_id: id }; sockets.set(`aot-device:${id}`, [{ send() {} }]); }
await fleet.writeFleet(tgRecord);

// 1. Simulate a batch dispatch for allocate_server
await fleet.dispatchFleetBatch(tgRecord, "ALLOCATE_SERVER", tgIds);
tgRecord = await fleet.readFleet();
tgRecord.last_batch.telegram_chat_id = 12345;
await fleet.writeFleet(tgRecord);

// 2. Fail first notification with 500
context.telegramMockStatus = 500;
const tgActionId = tgRecord.last_batch.action_id;
const initialCalls = telegramCalls.length;
for (const id of tgIds) {
  await fleet.dispatchFleetAck(new Request("https://test/aot/ack", { method: "POST", body: JSON.stringify({ protocol: "fleet-batch-v1", device_id: id, action_id: tgActionId, batch_action: "ALLOCATE_SERVER", status: "OPENED", executed: true }) }));
}
tgRecord = await fleet.readFleet();
if (tgRecord.last_batch.telegram_notified) throw new Error("telegram_notified should be false after 500 error");
if (telegramCalls.length !== initialCalls + 1) throw new Error("Should have attempted notification once");
if (!tgRecord.last_batch.telegram_retry_time || tgRecord.last_batch.telegram_retries !== 1) throw new Error("Retry state not set correctly");

// 3. Retry via alarm but fail with 429
context.telegramMockStatus = 429;
// simulate time passing
const oldNow = Date.now;
Date.now = () => oldNow() + 35000; // fast forward past retry time
await fleet.alarm();
tgRecord = await fleet.readFleet();
if (tgRecord.last_batch.telegram_notified) throw new Error("telegram_notified should be false after 429 error");
if (telegramCalls.length !== initialCalls + 2) throw new Error("Should have attempted notification twice");
if (tgRecord.last_batch.telegram_retries !== 2) throw new Error("Retry count not incremented");

// 4. Retry via alarm and succeed with 200
context.telegramMockStatus = 200;
Date.now = () => oldNow() + 70000;
await fleet.alarm();
tgRecord = await fleet.readFleet();
if (!tgRecord.last_batch.telegram_notified) throw new Error("telegram_notified should be true after 200 success");
if (telegramCalls.length !== initialCalls + 3) throw new Error("Should have attempted notification thrice");

// 5. Duplicate ACK should not trigger again
await fleet.dispatchFleetAck(new Request("https://test/aot/ack", { method: "POST", body: JSON.stringify({ protocol: "fleet-batch-v1", device_id: tgIds[0], action_id: tgActionId, batch_action: "ALLOCATE_SERVER", status: "OPENED", executed: true }) }));
if (telegramCalls.length !== initialCalls + 3) throw new Error("Duplicate ACK triggered extra notification");

Date.now = oldNow;

console.log("AOT_AUTO_HEAL_TESTS=OK");
console.log("AOT_FLEET_STATE_SELFTEST=OK");
