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

function attachMockSocket(tag) {
  const socket = {
    tags: [tag],
    send() {},
    close() {
      const arr = sockets.get(tag) || [];
      const idx = arr.indexOf(socket);
      if (idx !== -1) arr.splice(idx, 1);
    }
  };
  if (!sockets.has(tag)) sockets.set(tag, []);
  sockets.get(tag).push(socket);
  return socket;
}

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
  attachMockSocket("aot-device:m118");
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

// 4. RESET IDENTITY TEST (Public Endpoint Observable Behavior)
{
  // Setup old device m118 and unrelated device m119
  await postRegistration("discover", { device_id: "m119" });
  attachMockSocket("aot-device:m119");

  const resetReq = { old_device_id: "m118", new_device_id: "m120" };
  validateAgainstSchema(resetReq, "ResetRequest");
  const { status, data } = await postRegistration("reset", resetReq);
  assert.equal(status, 200, "Reset status must be 200");
  validateAgainstSchema(data, "ResetResponseSuccess");
  assert.equal(data.ok, true, "Reset ok must be true");
  assert.equal(data.old_device_id, "m118", "Reset response must return old_device_id");
  assert.equal(data.new_device_id, "m120", "Reset response must return new_device_id");

  // Verify new device m120 is observable via public verify endpoint
  attachMockSocket("aot-device:m120");
  const verifyNew = await postRegistration("verify", { device_id: "m120" });
  assert.equal(verifyNew.status, 200, "New device m120 must be verifiable");

  // Verify unrelated device m119 is preserved
  const verifyUnrelated = await postRegistration("verify", { device_id: "m119" });
  assert.equal(verifyUnrelated.status, 200, "Unrelated device m119 must be preserved");

  // Verify old device m118 is purged and offline
  const verifyOld = await postRegistration("verify", { device_id: "m118" });
  assert.equal(verifyOld.status, 409, "Old device m118 must be offline/purged");
}

// 5. NEGATIVE TESTS: INVALID, NULL, EMPTY, & FORBIDDEN FIELDS
{
  // Null, empty, and omitted ID edge cases
  const nullDiscover = await postRegistration("discover", { device_id: null });
  assert.equal(nullDiscover.status, 400, "Null device_id in discover must return 400");

  const emptyDiscover = await postRegistration("discover", { device_id: "" });
  assert.equal(emptyDiscover.status, 400, "Empty device_id in discover must return 400");

  const omittedDiscover = await postRegistration("discover", {});
  assert.equal(omittedDiscover.status, 400, "Omitted device_id in discover must return 400");

  const nullVerify = await postRegistration("verify", { device_id: null });
  assert.equal(nullVerify.status, 400, "Null device_id in verify must return 400");

  const emptyVerify = await postRegistration("verify", { device_id: "" });
  assert.equal(emptyVerify.status, 400, "Empty device_id in verify must return 400");

  const omittedVerify = await postRegistration("verify", {});
  assert.equal(omittedVerify.status, 400, "Omitted device_id in verify must return 400");

  const nullReset1 = await postRegistration("reset", { old_device_id: null, new_device_id: "m120" });
  assert.equal(nullReset1.status, 400, "Null old_device_id in reset must return 400");

  const nullReset2 = await postRegistration("reset", { old_device_id: "m118", new_device_id: null });
  assert.equal(nullReset2.status, 400, "Null new_device_id in reset must return 400");

  const emptyReset = await postRegistration("reset", { old_device_id: "", new_device_id: "m120" });
  assert.equal(emptyReset.status, 400, "Empty old_device_id in reset must return 400");

  const omittedReset = await postRegistration("reset", {});
  assert.equal(omittedReset.status, 400, "Omitted fields in reset must return 400");

  // Invalid device ID pattern
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
