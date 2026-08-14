import fs from "node:fs/promises";
import vm from "node:vm";

const context = vm.createContext({ URL, Request, Response, Headers, JSON, Map, Set, Object, Array, String, Number, Boolean, Math, Date, console, crypto, structuredClone });
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
  storage: { get: async k => store.get(k), put: async (k, v) => store.set(k, structuredClone(v)), setAlarm: async () => {} },
  getWebSockets: tag => sockets.get(tag) || [], getTags: () => [], acceptWebSocket() {}, waitUntil() {},
};
const fleet = new mod.namespace.FleetState(ctx, {});
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
await ack("RESTORE_STARTED"); // terminal for this action
await ack("SELECTED"); // late/post-terminal
if ((await fleet.readFleet()).last_batch.devices[ids[0]].status !== "RESTORE_STARTED") throw new Error("monotonicity violated (post-terminal)");

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
await infoAck("RESTORE_STARTED", { app_count: 5, selected_count: 3, reason: "started_ok" });
const batchViewAfterStarted = fleet.aotBatchView((await fleet.readFleet()).last_batch);
const dStarted = batchViewAfterStarted.devices.find(d => d.device_id === ids[2]);
if (dStarted.app_count !== 5 || dStarted.selected_count !== 3 || dStarted.reason !== "started_ok") throw new Error("count/reason propagation failed");
await infoAck("FAILED", { reason: "test_reason" }); // ignored because BACKUP_STARTED is terminal
const batchViewAfterInfoFailed = fleet.aotBatchView((await fleet.readFleet()).last_batch);
const dFailedInfo = batchViewAfterInfoFailed.devices.find(d => d.device_id === ids[2]);
if (dFailedInfo.status !== "RESTORE_STARTED" || dFailedInfo.reason !== "started_ok") throw new Error("reason overwritten by late ack");

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

console.log("AOT_AUTO_HEAL_TESTS=OK");
console.log("AOT_FLEET_STATE_SELFTEST=OK");
