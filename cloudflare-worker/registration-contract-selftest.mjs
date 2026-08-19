import fs from "node:fs/promises";
import vm from "node:vm";
import { webcrypto as crypto } from "node:crypto";
import assert from "node:assert/strict";

// Load canonical contract
const contractRaw = await fs.readFile(new URL("../aot-group-control/aot-registration-contract.json", import.meta.url), "utf8");
const contract = JSON.parse(contractRaw);

assert.equal(contract.identity_model, "device_id_only", "Contract must enforce device_id_only");

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
  storage: {
    get: async k => store.get(k),
    put: async (k, v) => store.set(k, structuredClone(v)),
    delete: async k => store.delete(k),
    list: async () => store,
    setAlarm: async () => {},
  },
  getWebSockets: tag => sockets.get(tag) || [],
  getTags: (socket) => socket.tags || [],
  acceptWebSocket() {},
  waitUntil() {},
};

const fleet = new mod.namespace.FleetState(ctx, {});

// Helper to make request to registration endpoint
async function postRegistration(op, body) {
  const req = new Request(`https://fleet-state.internal/aot/registration/${op}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const res = await fleet.registerFleetDevice(req, op);
  const data = await res.json();
  return { status: res.status, data };
}

console.log("RUNNING REGISTRATION CONTRACT SELFTEST...");

// 1. DISCOVER TEST
{
  const { status, data } = await postRegistration("discover", { device_id: "m118" });
  assert.equal(status, 200, "Discover status must be 200");
  assert.equal(data.ok, true, "Discover ok must be true");
  assert.equal(data.device_id, "m118", "Discover response must contain canonical device_id");
  for (const forbidden of contract.forbidden_fields) {
    assert.equal(data[forbidden], undefined, `Forbidden field ${forbidden} present in discover response`);
  }
}

// 2. VERIFY OFFLINE TEST (Fail-closed)
{
  const { status, data } = await postRegistration("verify", { device_id: "m118" });
  assert.equal(status, 409, "Offline verify must return HTTP 409");
  assert.equal(data.ok, false, "Offline verify ok must be false");
  assert.equal(data.error, "device_not_online_in_aot_hub");
}

// 3. VERIFY ONLINE TEST
{
  sockets.set("aot-device:m118", [{ send() {} }]);
  const { status, data } = await postRegistration("verify", { device_id: "m118" });
  assert.equal(status, 200, "Online verify must return HTTP 200");
  assert.equal(data.ok, true, "Online verify ok must be true");
  assert.equal(data.device_id, "m118", "Online verify device_id mismatch");
  assert.equal(data.online, true, "Online verify online must be true");
  assert.equal(data.visible_in_hub, true, "Online verify visible_in_hub must be true");
  for (const forbidden of contract.forbidden_fields) {
    assert.equal(data[forbidden], undefined, `Forbidden field ${forbidden} present in verify response`);
  }
}

// 4. RESET IDENTITY TEST
{
  // Setup old device m118 and unrelated device m119
  await postRegistration("discover", { device_id: "m119" });
  sockets.set("aot-device:m119", [{ send() {} }]);

  const { status, data } = await postRegistration("reset", { old_device_id: "m118", new_device_id: "m120" });
  assert.equal(status, 200, "Reset status must be 200");
  assert.equal(data.ok, true, "Reset ok must be true");
  assert.equal(data.old_device_id, "m118", "Reset response must return old_device_id");
  assert.equal(data.new_device_id, "m120", "Reset response must return new_device_id");

  // Verify unrelated device m119 is preserved
  const fleetRecord = await fleet.readFleet();
  assert.ok(fleetRecord.devices.m119, "Unrelated device m119 must be preserved");
  assert.ok(fleetRecord.devices.m120, "New device m120 must exist");
  assert.equal(fleetRecord.devices.m118, undefined, "Old device m118 must be purged");

  for (const forbidden of contract.forbidden_fields) {
    assert.equal(data[forbidden], undefined, `Forbidden field ${forbidden} present in reset response`);
  }
}

// 5. INVALID IDENTITY INPUTS
{
  const invalid1 = await postRegistration("discover", { device_id: "invalid-id" });
  assert.equal(invalid1.status, 400, "Invalid device_id in discover must return 400");

  const invalid2 = await postRegistration("reset", { old_device_id: "m120", new_device_id: "m120" });
  assert.equal(invalid2.status, 400, "Identical old and new device_id in reset must return 400");
}

console.log("AOT_REGISTRATION_CONTRACT_SELFTEST=OK");
