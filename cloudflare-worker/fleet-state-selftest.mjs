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

const file = source;
if (!file.includes("AOT_UPDATE_GROUP_SIZE = 5") || !file.includes("ROLLED_BACK")) throw new Error("update rollout/rollback lost");
const fleetProtocol = file.slice(file.indexOf("async readFleet()"));
for (const bad of [/\bm[1-9][0-9]{0,5}\b/i, /x_norm/, /y_norm/]) if (bad.test(fleetProtocol)) throw new Error(`hard-coded identity/coordinate: ${bad}`);
console.log("AOT_FLEET_STATE_SELFTEST=OK");
