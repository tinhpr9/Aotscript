import fs from "node:fs/promises";
import vm from "node:vm";

// Load fleet-state-client.js
const clientSource = await fs.readFile(new URL("./fleet-state-client.js", import.meta.url), "utf8");
const context = vm.createContext({
  Request,
  Response,
  Headers,
  JSON,
  encodeURIComponent,
  Array,
  Object,
  String,
  Error,
  console
});
const clientMod = new vm.SourceTextModule(clientSource, { context });
await clientMod.link(() => {});
await clientMod.evaluate();

const {
  fleetStateStub,
  fleetStateCall,
  listFleetDeviceRecords,
  getFleetDeviceRecord,
  setFleetDeviceRevocation,
  enqueueFastCommand
} = clientMod.namespace;

console.log("AOT_FLEET_STATE_CLIENT_TESTS: Starting behavior preservation tests");

// 1. Missing FLEET_STATE binding
let missingThrown = false;
try {
  fleetStateStub({});
} catch (e) {
  missingThrown = true;
  if (!e.message.includes("Thiếu Durable Object binding FLEET_STATE")) {
    throw new Error(`Unexpected error message for missing binding: ${e.message}`);
  }
}
if (!missingThrown) throw new Error("Expected missing FLEET_STATE binding to throw");

// Mock Durable Object helper
function createMockEnv(fetchHandler) {
  return {
    FLEET_STATE: {
      idFromName(name) {
        if (name !== "aotscript-fleet") throw new Error(`Unexpected stub name: ${name}`);
        return { name };
      },
      get(id) {
        return {
          async fetch(req) {
            return fetchHandler(req);
          }
        };
      }
    }
  };
}

// 2. GET call and header correctness
let capturedReq = null;
const mockEnvGet = createMockEnv(async (req) => {
  capturedReq = req;
  return new Response(JSON.stringify({ ok: true, records: [{ device_id: "m1" }] }), {
    status: 200,
    headers: { "Content-Type": "application/json" }
  });
});

const getRes = await fleetStateCall(mockEnvGet, "/devices");
if (capturedReq.method !== "GET") throw new Error(`Expected GET, got ${capturedReq.method}`);
if (capturedReq.url !== "https://fleet-state.internal/devices") throw new Error(`Unexpected URL: ${capturedReq.url}`);
if (capturedReq.headers.get("Accept") !== "application/json") throw new Error("Missing Accept header");
if (capturedReq.headers.get("Content-Type")) throw new Error("GET should not set Content-Type header");
if (!getRes.data.ok || getRes.data.records.length !== 1) throw new Error("Failed to parse GET response data");

// 3. POST call with body and header correctness
const mockEnvPost = createMockEnv(async (req) => {
  capturedReq = req;
  const bodyText = await req.text();
  return new Response(JSON.stringify({ ok: true, echo: JSON.parse(bodyText) }), {
    status: 200,
    headers: { "Content-Type": "application/json" }
  });
});

const postRes = await fleetStateCall(mockEnvPost, "/test", {
  method: "POST",
  body: { foo: "bar" }
});
if (capturedReq.method !== "POST") throw new Error(`Expected POST, got ${capturedReq.method}`);
if (capturedReq.headers.get("Content-Type") !== "application/json") throw new Error("Missing Content-Type header on POST");
if (postRes.data.echo.foo !== "bar") throw new Error("POST body echo mismatch");

// 4. Empty response body handling
const mockEnvEmpty = createMockEnv(async () => {
  return new Response("", { status: 200 });
});
const emptyRes = await fleetStateCall(mockEnvEmpty, "/empty");
if (typeof emptyRes.data !== "object" || Object.keys(emptyRes.data).length !== 0) {
  throw new Error("Expected empty data object for empty body");
}

// 5. Invalid JSON response body handling
const mockEnvInvalidJson = createMockEnv(async () => {
  return new Response("<!DOCTYPE html><html>error</html>", { status: 502 });
});
const invalidRes = await fleetStateCall(mockEnvInvalidJson, "/bad");
if (invalidRes.data.ok !== false || invalidRes.data.error !== "invalid_fleet_state_response") {
  throw new Error(`Unexpected invalid JSON handling: ${JSON.stringify(invalidRes.data)}`);
}

// 6. listFleetDeviceRecords wrapper behavior
const mockEnvListOk = createMockEnv(async () => {
  return new Response(JSON.stringify({ ok: true, records: [{ device_id: "m101" }] }), { status: 200 });
});
const records = await listFleetDeviceRecords(mockEnvListOk);
if (!Array.isArray(records) || records.length !== 1 || records[0].device_id !== "m101") {
  throw new Error("listFleetDeviceRecords failed on 200 response");
}

const mockEnvListErr = createMockEnv(async () => {
  return new Response(JSON.stringify({ ok: false, error: "unauthorized" }), { status: 401 });
});
let listErrThrown = false;
try {
  await listFleetDeviceRecords(mockEnvListErr);
} catch (e) {
  listErrThrown = true;
  if (!e.message.includes("unauthorized")) throw new Error(`Unexpected list error message: ${e.message}`);
}
if (!listErrThrown) throw new Error("Expected listFleetDeviceRecords to throw on non-2xx");

// 7. getFleetDeviceRecord wrapper behavior
const mockEnvGetRecord = createMockEnv(async (req) => {
  if (req.url.endsWith("/device?id=m%201%23")) {
    return new Response(JSON.stringify({ ok: true, record: { device_id: "m 1#" } }), { status: 200 });
  }
  return new Response(JSON.stringify({ ok: false, error: "not_found" }), { status: 404 });
});

const foundRecord = await getFleetDeviceRecord(mockEnvGetRecord, "m 1#");
if (!foundRecord || foundRecord.device_id !== "m 1#") throw new Error("Failed to find record with encoded ID");

const notFoundRecord = await getFleetDeviceRecord(mockEnvGetRecord, "m2");
if (notFoundRecord !== null) throw new Error("Expected 404 to return null");

// 8. setFleetDeviceRevocation wrapper behavior
let revokeActionUrl = null;
let revokeActionBody = null;
const mockEnvRevoke = createMockEnv(async (req) => {
  revokeActionUrl = req.url;
  revokeActionBody = await req.json();
  return new Response(JSON.stringify({ ok: true }), { status: 200 });
});

await setFleetDeviceRevocation(mockEnvRevoke, "m50", true);
if (revokeActionUrl !== "https://fleet-state.internal/revoke") throw new Error(`Expected /revoke, got ${revokeActionUrl}`);
if (revokeActionBody.device_id !== "m50") throw new Error("Revoke body device_id mismatch");

await setFleetDeviceRevocation(mockEnvRevoke, "m50", false);
if (revokeActionUrl !== "https://fleet-state.internal/restore") throw new Error(`Expected /restore, got ${revokeActionUrl}`);
if (revokeActionBody.device_id !== "m50") throw new Error("Restore body device_id mismatch");

// 9. enqueueFastCommand wrapper behavior
let enqueueBody = null;
const mockEnvEnqueue = createMockEnv(async (req) => {
  enqueueBody = await req.json();
  return new Response(JSON.stringify({ ok: true, command_id: enqueueBody.command_id }), { status: 200 });
});

const enqRes = await enqueueFastCommand(mockEnvEnqueue, "cmd-99", ["m1", "m2"], 1700000000, "base64payload");
if (enqueueBody.command_id !== "cmd-99" || enqueueBody.device_ids.length !== 2 || enqueueBody.command_block !== "base64payload") {
  throw new Error("enqueueFastCommand payload mismatch");
}
if (!enqRes.ok || enqRes.command_id !== "cmd-99") throw new Error("enqueueFastCommand response mismatch");

console.log("AOT_FLEET_STATE_CLIENT_TESTS=OK");
