import fs from "node:fs/promises";
import vm from "node:vm";
import { webcrypto as crypto } from "node:crypto";
import assert from "node:assert/strict";

// Load canonical contract
const contractRaw = await fs.readFile(new URL("../aot-group-control/aot-registration-contract.json", import.meta.url), "utf8");
const contract = JSON.parse(contractRaw);

assert.equal(contract.identity_model, "device_id_only", "Contract must enforce device_id_only");
assert.ok(contract.$defs, "Contract must define JSON Schema $defs");

function validateAgainstSchema(instance, defName) {
  const schema = contract.$defs[defName];
  assert.ok(schema, `Schema definition ${defName} must exist`);
  assert.equal(typeof instance, schema.type, `Instance type must be ${schema.type}`);
  if (schema.required) {
    for (const reqKey of schema.required) {
      assert.ok(reqKey in instance, `Missing required field ${reqKey} for ${defName}`);
    }
  }
  if (schema.additionalProperties === false && schema.properties) {
    const allowed = new Set(Object.keys(schema.properties));
    for (const key of Object.keys(instance)) {
      assert.ok(allowed.has(key), `Disallowed additional property ${key} for ${defName}`);
    }
  }
}

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
  const reqBody = { device_id: "m118" };
  validateAgainstSchema(reqBody, "DiscoverRequest");
  const { status, data } = await postRegistration("discover", reqBody);
  assert.equal(status, 200, "Discover status must be 200");
  validateAgainstSchema(data, "DiscoverResponseSuccess");
  assert.equal(data.ok, true, "Discover ok must be true");
  assert.equal(data.device_id, "m118", "Discover response must contain canonical device_id");
}

// 2. VERIFY OFFLINE TEST (Fail-closed)
{
  const reqBody = { device_id: "m118" };
  validateAgainstSchema(reqBody, "VerifyRequest");
  const { status, data } = await postRegistration("verify", reqBody);
  assert.equal(status, 409, "Offline verify must return HTTP 409");
  assert.equal(data.ok, false, "Offline verify ok must be false");
  assert.equal(data.error, "device_not_online_in_aot_hub");
}

// 3. VERIFY ONLINE TEST
{
  sockets.set("aot-device:m118", [{ send() {} }]);
  const reqBody = { device_id: "m118" };
  validateAgainstSchema(reqBody, "VerifyRequest");
  const { status, data } = await postRegistration("verify", reqBody);
  assert.equal(status, 200, "Online verify must return HTTP 200");
  validateAgainstSchema(data, "VerifyResponseSuccess");
  assert.equal(data.ok, true, "Online verify ok must be true");
  assert.equal(data.device_id, "m118", "Online verify device_id mismatch");
  assert.equal(data.online, true, "Online verify online must be true");
  assert.equal(data.visible_in_hub, true, "Online verify visible_in_hub must be true");
}

// 4. RESET IDENTITY TEST
{
  // Setup old device m118 and unrelated device m119
  await postRegistration("discover", { device_id: "m119" });
  sockets.set("aot-device:m119", [{ send() {} }]);

  const resetReq = { old_device_id: "m118", new_device_id: "m120" };
  validateAgainstSchema(resetReq, "ResetRequest");
  const { status, data } = await postRegistration("reset", resetReq);
  assert.equal(status, 200, "Reset status must be 200");
  validateAgainstSchema(data, "ResetResponseSuccess");
  assert.equal(data.ok, true, "Reset ok must be true");
  assert.equal(data.old_device_id, "m118", "Reset response must return old_device_id");
  assert.equal(data.new_device_id, "m120", "Reset response must return new_device_id");

  // Verify unrelated device m119 is preserved
  const fleetRecord = await fleet.readFleet();
  assert.ok(fleetRecord.devices.m119, "Unrelated device m119 must be preserved");
  assert.ok(fleetRecord.devices.m120, "New device m120 must exist");
  assert.equal(fleetRecord.devices.m118, undefined, "Old device m118 must be purged");
}

// 5. NEGATIVE TESTS: INVALID & FORBIDDEN FIELDS
{
  // Invalid device ID
  const invalid1 = await postRegistration("discover", { device_id: "invalid-id" });
  assert.equal(invalid1.status, 400, "Invalid device_id in discover must return 400");

  // Forbidden fields rejected fail-closed
  const forbidden1 = await postRegistration("discover", { device_id: "m118", role: "reference" });
  assert.equal(forbidden1.status, 400, "Forbidden role in discover must return 400");

  const forbidden2 = await postRegistration("verify", { device_id: "m118", session_id: "s1" });
  assert.equal(forbidden2.status, 400, "Forbidden session_id in verify must return 400");

  const forbidden3 = await postRegistration("reset", { old_device_id: "m118", new_device_id: "m120", reference_device_id: "m100" });
  assert.equal(forbidden3.status, 400, "Forbidden reference_device_id in reset must return 400");

  // Field substitution rejected
  const sub1 = await postRegistration("discover", { new_device_id: "m118" });
  assert.equal(sub1.status, 400, "new_device_id substituted in discover must return 400");

  const sub2 = await postRegistration("reset", { old_device_id: "m118", device_id: "m120" });
  assert.equal(sub2.status, 400, "device_id substituted in reset must return 400");

  const invalid2 = await postRegistration("reset", { old_device_id: "m120", new_device_id: "m120" });
  assert.equal(invalid2.status, 400, "Identical old and new device_id in reset must return 400");
}

console.log("AOT_REGISTRATION_CONTRACT_SELFTEST=OK");
