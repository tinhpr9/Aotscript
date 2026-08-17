import fs from "node:fs/promises";
import vm from "node:vm";

const context = vm.createContext({ URL, Request, Response, Headers, JSON, Map, Set, Object, Array, String, Number, Boolean, Math, Date, console, crypto, structuredClone });
const telegramCalls = [];
const fetchCalls = [];
context.telegramMockStatus = 200;
context.fetch = async (url, options) => {
  fetchCalls.push({ url, options });
  if (context.customFetchHandler) {
    const res = await context.customFetchHandler(url, options);
    if (res !== undefined) return res;
  }
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
let scheduledAlarm = null;
const ctx = {
  storage: { get: async k => store.get(k), put: async (k, v) => store.set(k, structuredClone(v)), setAlarm: async (t) => { scheduledAlarm = t; }, list: async () => new Map() },
  getWebSockets: tag => sockets.get(tag) || [], getTags: (socket) => socket.tags || [], acceptWebSocket() {}, waitUntil() {},
};
const fleet = new mod.namespace.FleetState(ctx, { TELEGRAM_BOT_TOKEN: "tok" });
const ids = Array.from({ length: 40 }, (_, i) => `m${i + 201}`);
const record = await fleet.readFleet();
for (const id of ids) { record.devices[id] = { device_id: id, capabilities: ["allocate_server_2pc"] }; sockets.set(`aot-device:${id}`, [{ send() {} }]); }
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
if (!lastAllocPayload || lastAllocPayload.action !== "PREPARE_ALLOCATE_SERVER" || !lastAllocPayload.allocation || lastAllocPayload.allocation[0].pkg !== "com.tinh.vv.hi") throw new Error("Socket payload missing allocation map for device");
const allocActionId = body.batch.action_id;
const allocAck = async (status, executed = false) => fleet.dispatchFleetAck(new Request("https://test/aot/ack", { method: "POST", body: JSON.stringify({ protocol: "fleet-batch-v1", device_id: ids[0], action_id: allocActionId, batch_action: "ALLOCATE_SERVER", status, executed }) }));
await allocAck("PREPARE_READY");
const actualStatus = (await fleet.readFleet()).last_batch.devices[ids[0]].status;
if (actualStatus !== "COMMIT_SENT") throw new Error(`ALLOCATE_SERVER COMMIT_SENT failed, got ${actualStatus}`);
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
sRecord.canary_release = { status: "HEALTHY", version: "aot-worker-2026.08.16.04", device_ids: [ids[0], ids[1]] };
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
sRecord.canary_release = { status: "HEALTHY", version: "aot-worker-2026.08.16.04", device_ids: [ids[0], ids[1]] };
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
for (const id of tgIds) { tgRecord.devices[id] = { device_id: id, capabilities: ["allocate_server_2pc"] }; sockets.set(`aot-device:${id}`, [{ send() {} }]); }
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
// Reset last_batch
let resetRecord = await fleet.readFleet();
resetRecord.last_batch = null;
await fleet.writeFleet(resetRecord);

// Test 6: webSocketClose disconnect PREPARE logic
const dcIds = ["m101", "m102"];
let dcRecord = await fleet.readFleet();
for (const id of dcIds) { dcRecord.devices[id] = { device_id: id, capabilities: ["allocate_server_2pc"] }; fleet.aotLive.set(id, { capabilities: ["allocate_server_2pc"] }); sockets.set(`aot-device:${id}`, [{ send() {}, deserializeAttachment: () => ({ deviceId: id, capabilities: ["allocate_server_2pc"] }) }]); }
await fleet.writeFleet(dcRecord);

const dcRes = await fleet.dispatchFleetBatch(dcRecord, "ALLOCATE_SERVER", dcIds, { allocationMap: {} });
if (!dcRes.ok) {
  const data = await dcRes.json();
  throw new Error(`dispatch failed in Test 6: ${JSON.stringify(data)}`);
}
dcRecord = await fleet.readFleet();

// m101 disconnects before commit_decided
await fleet.webSocketClose({ tags: ["aot-device:m101"] });
dcRecord = await fleet.readFleet();
if (!dcRecord.last_batch || !dcRecord.last_batch.devices["m101"]) {
  console.error(JSON.stringify(dcRecord.last_batch));
}
if (dcRecord.last_batch.devices["m101"].status !== "ABORT_SENT") {
  console.log("m101 status:", dcRecord.last_batch.devices["m101"].status);
  throw new Error("Expected ABORT_SENT for m101");
}
if (dcRecord.last_batch.devices["m102"].status !== "ABORT_SENT") throw new Error("Expected ABORT_SENT for m102");
if (!dcRecord.last_batch.abort_sent) throw new Error("Expected abort_sent true");

// Test 7: webSocketClose disconnect after commit_decided
let resetRecord2 = await fleet.readFleet();
resetRecord2.last_batch = null;
await fleet.writeFleet(resetRecord2);

const cIds = ["m201", "m202"];
let cRecord = await fleet.readFleet();
for (const id of cIds) { cRecord.devices[id] = { device_id: id, capabilities: ["allocate_server_2pc"] }; fleet.aotLive.set(id, { capabilities: ["allocate_server_2pc"] }); sockets.set(`aot-device:${id}`, [{ send() {}, deserializeAttachment: () => ({ deviceId: id, capabilities: ["allocate_server_2pc"] }) }]); }
await fleet.writeFleet(cRecord);

await fleet.dispatchFleetBatch(cRecord, "ALLOCATE_SERVER", cIds, { allocationMap: {} });
cRecord = await fleet.readFleet();
for (const id of cIds) {
  await fleet.dispatchFleetAck(new Request("https://test/aot/ack", { method: "POST", body: JSON.stringify({ protocol: "fleet-batch-v1", device_id: id, action_id: cRecord.last_batch.action_id, batch_action: "ALLOCATE_SERVER", status: "PREPARE_READY", executed: false }) }));
}
cRecord = await fleet.readFleet();
if (!cRecord.last_batch.commit_decided) throw new Error("Expected commit_decided true");

// m201 disconnects
await fleet.webSocketClose({ tags: ["aot-device:m201"] });
cRecord = await fleet.readFleet();
if (cRecord.last_batch.devices["m201"].status === "FAILED") throw new Error("Expected m201 to NOT be FAILED");
if (cRecord.last_batch.devices["m201"].status === "ABORT_SENT") throw new Error("Expected m201 to NOT be ABORT_SENT");

// Test 8: Capability stale storage test
let resetRecord3 = await fleet.readFleet();
resetRecord3.last_batch = null;
await fleet.writeFleet(resetRecord3);

const staleId = "m301";
let staleRecord = await fleet.readFleet();
staleRecord.devices[staleId] = { device_id: staleId, capabilities: ["allocate_server_2pc"] };
await fleet.writeFleet(staleRecord);
sockets.set(`aot-device:${staleId}`, [{ send() {}, deserializeAttachment: () => ({ deviceId: staleId, capabilities: [] }) }]);
fleet.aotLive.set(staleId, { capabilities: [] });

const resStale = await fleet.dispatchFleetBatch(staleRecord, "ALLOCATE_SERVER", [staleId], { allocationMap: {} });
const dataStale = await resStale.json();
if (resStale.status !== 409 || dataStale.error !== "worker_missing_allocate_server_2pc_capability") throw new Error("Expected worker_missing_allocate_server_2pc_capability");

// Test 9: ALLOCATE_SERVER commit retry alarm scheduling without Telegram
let resetRecord4 = await fleet.readFleet();
resetRecord4.last_batch = null;
await fleet.writeFleet(resetRecord4);

const retryIds = ["m401", "m402"];
let retryRecord = await fleet.readFleet();
let retrySent = [];
for (const id of retryIds) {
  retryRecord.devices[id] = { device_id: id, capabilities: ["allocate_server_2pc"] };
  fleet.aotLive.set(id, { capabilities: ["allocate_server_2pc"] });
  sockets.set(`aot-device:${id}`, [{
    tags: [`aot-device:${id}`],
    send(payload) { retrySent.push({ id, data: JSON.parse(payload) }); }
  }]);
}
await fleet.writeFleet(retryRecord);

scheduledAlarm = null;
const retryRes = await fleet.dispatchFleetBatch(retryRecord, "ALLOCATE_SERVER", retryIds, { allocationMap: {} });
if (!retryRes.ok) throw new Error("dispatch failed in Test 9");
retryRecord = await fleet.readFleet();
const retryActionId = retryRecord.last_batch.action_id;

// Both devices ACK PREPARE_READY
for (const id of retryIds) {
  await fleet.dispatchFleetAck(new Request("https://test/aot/ack", {
    method: "POST",
    body: JSON.stringify({ protocol: "fleet-batch-v1", device_id: id, action_id: retryActionId, batch_action: "ALLOCATE_SERVER", status: "PREPARE_READY", executed: false })
  }));
}

retryRecord = await fleet.readFleet();
if (!retryRecord.last_batch.commit_decided) throw new Error("commit_decided should be true in Test 9");
if (retryRecord.last_batch.devices["m401"].status !== "COMMIT_SENT") throw new Error("m401 status should be COMMIT_SENT");
if (!retryRecord.last_batch.devices["m401"].commit_retry_time) throw new Error("commit_retry_time should be set");
if (!scheduledAlarm) throw new Error("scheduledAlarm should be scheduled on commit decision");

const initialRetryAt = retryRecord.last_batch.devices["m401"].commit_retry_time;
retrySent.length = 0;

// Advance time to commit_retry_time (still before expires_at)
const saveDateNow = Date.now;
try {
  Date.now = () => initialRetryAt + 100;
  await fleet.alarm();
  retryRecord = await fleet.readFleet();
} finally {
  Date.now = saveDateNow;
}

// Verify COMMIT was re-sent via alarm before expiry
if (retrySent.length !== 2) throw new Error(`Expected 2 COMMIT retries sent via alarm, got ${retrySent.length}`);
for (const item of retrySent) {
  if (item.data.action !== "COMMIT_ALLOCATE_SERVER") throw new Error("Action sent on alarm retry is not COMMIT_ALLOCATE_SERVER");
  if (item.data.action_id !== retryActionId) throw new Error("action_id mismatch on COMMIT retry");
  if (item.data.expires_at !== retryRecord.last_batch.expires_at) throw new Error("expires_at mismatch on COMMIT retry");
}
if (retryRecord.last_batch.devices["m401"].commit_retries !== 1) throw new Error("commit_retries should be incremented to 1");
if (retryRecord.last_batch.devices["m401"].commit_retry_time <= initialRetryAt) throw new Error("commit_retry_time should be updated with backoff");

// Test 10: Single-flight lock allows new dispatch when previous batch is ABORT_SENT
let resetRecord5 = await fleet.readFleet();
resetRecord5.last_batch = {
  action: "ALLOCATE_SERVER",
  action_id: "prev-aborted-batch",
  expires_at: Date.now() + 50000,
  devices: {
    m401: { device_id: "m401", status: "ABORT_SENT" },
    m402: { device_id: "m402", status: "PREPARE_FAILED" }
  }
};
await fleet.writeFleet(resetRecord5);

const dispatchAfterAbort = await fleet.dispatchFleetBatch(resetRecord5, "ALLOCATE_SERVER", retryIds, { allocationMap: {} });
if (!dispatchAfterAbort.ok) {
  const errData = await dispatchAfterAbort.json();
  throw new Error(`Expected dispatch to succeed after aborted batch, got error: ${JSON.stringify(errData)}`);
}

// Test 11: Monotonic rank — duplicate PREPARE_READY must NOT downgrade COMMIT_PENDING
let rec11 = await fleet.readFleet();
const actionId11 = rec11.last_batch.action_id;
rec11.last_batch.devices["m401"].status = "COMMIT_PENDING";
await fleet.writeFleet(rec11);

await fleet.dispatchFleetAck(new Request("https://test/aot/ack", {
  method: "POST",
  body: JSON.stringify({
    protocol: "fleet-batch-v1",
    device_id: "m401",
    action_id: actionId11,
    batch_action: "ALLOCATE_SERVER",
    status: "PREPARE_READY",
    executed: false
  })
}));

rec11 = await fleet.readFleet();
if (rec11.last_batch.devices["m401"].status !== "COMMIT_PENDING") {
  throw new Error(`Regression: COMMIT_PENDING was downgraded to ${rec11.last_batch.devices["m401"].status}`);
}

// Test 12: Telegram retry alarm scheduled even when batch has expired
let rec12 = await fleet.readFleet();
rec12.last_batch.expires_at = Date.now() - 5000;
rec12.last_batch.telegram_chat_id = "123456";
rec12.last_batch.telegram_notified = false;
rec12.last_batch.telegram_retry_time = Date.now() + 25000;
await fleet.writeFleet(rec12);

scheduledAlarm = null;
await fleet.scheduleNextAlarm();
if (scheduledAlarm !== rec12.last_batch.telegram_retry_time) {
  throw new Error(`Expected scheduledAlarm to be ${rec12.last_batch.telegram_retry_time}, got ${scheduledAlarm}`);
}

// Test 13: githubJson unauthenticated 403 error throws github_api_403 and does not send Authorization
const fleetNoAuth = new mod.namespace.FleetState(ctx, {});
context.customFetchHandler = async (url, options) => {
  if (url.includes("api.github.com")) {
    if (!options.headers.Authorization) {
      return { ok: false, status: 403, json: async () => ({ message: "API rate limit exceeded" }) };
    }
  }
};
let caughtNoAuth = null;
try {
  await fleetNoAuth.githubJson("/releases/tags/test-tag");
} catch (e) {
  caughtNoAuth = e;
}
if (!caughtNoAuth || caughtNoAuth.message !== "github_api_403") {
  throw new Error(`Expected github_api_403, got ${caughtNoAuth?.message}`);
}
const lastNoAuthCall = fetchCalls[fetchCalls.length - 1];
if (lastNoAuthCall.options.headers.Authorization) {
  throw new Error("Expected no Authorization header for unauthenticated fleet");
}

// Test 14: githubJson authenticated 200 passes Authorization header, version header, and returns JSON
const mockToken = "ghp_secretTestToken123456789";
const fleetWithAuth = new mod.namespace.FleetState(ctx, { GITHUB_TOKEN: mockToken });
context.customFetchHandler = async (url, options) => {
  if (url.includes("api.github.com")) {
    if (options.headers.Authorization === `Bearer ${mockToken}`) {
      return { ok: true, status: 200, json: async () => ({ tag_name: "test-tag", draft: false }) };
    }
    return { ok: false, status: 401, json: async () => ({ message: "Bad credentials" }) };
  }
};
const resWithAuth = await fleetWithAuth.githubJson("/releases/tags/test-tag");
if (resWithAuth.tag_name !== "test-tag") {
  throw new Error(`Expected authenticated JSON with tag_name test-tag, got ${JSON.stringify(resWithAuth)}`);
}
const lastAuthCall = fetchCalls[fetchCalls.length - 1];
if (lastAuthCall.options.headers.Authorization !== `Bearer ${mockToken}`) {
  throw new Error("Authorization header mismatch");
}
if (lastAuthCall.options.headers["X-GitHub-Api-Version"] !== "2022-11-28") {
  throw new Error("Missing or incorrect X-GitHub-Api-Version header");
}
if (lastAuthCall.options.headers["User-Agent"] !== "Aotscript-AOT-Hub") {
  throw new Error("User-Agent header was changed");
}

// Test 15: startWorkerUpdate error handling sanitizes token and never leaks it
fleetWithAuth.resolveWorkerRelease = async () => {
  throw new Error(`Failed to download asset with token Bearer ${mockToken} and ghp_secretTestToken123456789`);
};
const test15Res = await fleetWithAuth.startWorkerUpdate("fleet", await fleetWithAuth.readFleet(), "canary", ["m201"]);
const test15Body = await test15Res.json();
if (test15Body.ok || test15Body.error !== "release_resolution_failed") {
  throw new Error(`Expected release_resolution_failed, got ${JSON.stringify(test15Body)}`);
}
if (JSON.stringify(test15Body).includes(mockToken)) {
  throw new Error(`SECURITY REGRESSION: Token leaked in startWorkerUpdate error response: ${JSON.stringify(test15Body)}`);
}
// Test 16: allocate_server capability check falls back correctly when socket attachment has empty capabilities array
const test16Store = new Map();
const test16Ctx = {
  storage: { get: async k => test16Store.get(k), put: async (k, v) => test16Store.set(k, structuredClone(v)), setAlarm: async () => {}, list: async () => new Map() },
  getWebSockets: tag => sockets.get(tag) || [],
  getTags: (socket) => socket.tags || [],
  acceptWebSocket() {},
  waitUntil() {},
};
const test16Fleet = new mod.namespace.FleetState(test16Ctx, {});
const test16Record = {
  devices: {
    "m888": {
      device_id: "m888",
      capabilities: ["dynamic_update_channel", "allocate_server_2pc"],
      added_at: Date.now(),
    }
  },
  last_batch: null,
};
await test16Fleet.writeFleet(test16Record);

// Mock online socket with empty capabilities attachment (worker_version null)
const mockSocketWithEmptyCaps = {
  tags: ["aot-device:m888", "aot-fleet:device:m888"],
  deserializeAttachment: () => ({ device_id: "m888", worker_version: null, capabilities: [] }),
  serializeAttachment: () => {},
  send: () => {},
  close: () => {},
};
sockets.set("aot-device:m888", [mockSocketWithEmptyCaps]);
sockets.set("aot-fleet:device:m888", [mockSocketWithEmptyCaps]);

// Allocate server request should succeed because getLiveCaps falls back to record capabilities
test16Fleet.fetchManagedServerUrls = async () => ["https://s1.example.com"];
const allocRes = await test16Fleet.dispatchFleetBatch(test16Record, "ALLOCATE_SERVER", ["m888"], { allocationMap: { "m888": "https://s1.example.com" } });
const allocBody = await allocRes.json();
if (!allocBody.ok) {
  throw new Error(`Expected ALLOCATE_SERVER to succeed with capability fallback, got: ${JSON.stringify(allocBody)}`);
}

// Test 17: Fallback metadata-only status frame is sanitized and populates capabilities/version
const test17Identity = { role: "device", sessionId: "fleet", deviceId: "m999" };
const fallbackPayload = {
  type: "aot_status",
  protocol: "fleet-batch-v1",
  device_id: "m999",
  worker_version: "aot-worker-2026.08.16.04",
  capabilities: ["dynamic_update_channel", "allocate_server_2pc"],
  updated_at: Date.now(),
};
const sanitizedFallback = test16Fleet.sanitizeAotLiveStatus(test17Identity, fallbackPayload);
if (!sanitizedFallback) {
  throw new Error("Expected fallback metadata-only status frame to be accepted by sanitizeAotLiveStatus");
}
if (sanitizedFallback.worker_version !== "aot-worker-2026.08.16.04" || !sanitizedFallback.capabilities.includes("allocate_server_2pc")) {
  throw new Error(`Sanitized fallback payload missing version or capabilities: ${JSON.stringify(sanitizedFallback)}`);
}
if (sanitizedFallback.fingerprint !== null || sanitizedFallback.coordinate_ready !== false) {
  throw new Error(`Sanitized fallback payload should have null fingerprint and false coordinate_ready: ${JSON.stringify(sanitizedFallback)}`);
}

// Test 18: Normal status with invalid fingerprint is strictly rejected
const badFingerprintPayload = {
  type: "aot_status",
  protocol: "fleet-batch-v1",
  device_id: "m999",
  worker_version: "aot-worker-2026.08.16.04",
  capabilities: ["dynamic_update_channel"],
  fingerprint: "invalid_fingerprint_not_24_hex",
  width: 1080,
  height: 2400,
};
const badResult = test16Fleet.sanitizeAotLiveStatus(test17Identity, badFingerprintPayload);
if (badResult !== null) {
  throw new Error("Expected invalid fingerprint to be strictly rejected by sanitizeAotLiveStatus");
}

context.customFetchHandler = null;

console.log("AOT_AUTO_HEAL_TESTS=OK");
console.log("AOT_FLEET_STATE_SELFTEST=OK");
