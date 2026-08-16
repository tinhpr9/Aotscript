import fs from "node:fs/promises";
import vm from "node:vm";

const source = await fs.readFile(new URL("./worker.js", import.meta.url), "utf8");

let fleetCalls = [];
const context = vm.createContext({
  AOT_HUB_PROTOCOL_VERSION: "fleet-batch-v1",
  AOT_PROTOCOL_VERSION: "aot-v1",
  normalizeDeviceId: (id) => id,
  normalizeAotActionId: (id) => id,
  isAuthorizedAgentRequest: () => true,
  noStoreJson: (body, status) => ({ ok: false, error: body.error, status }),
  readAotJson: async (req) => ({ value: req.body }),
  fleetStateCall: async (env, path, opts) => {
    fleetCalls.push({ path, body: opts.body });
    return { ok: true, data: {}, response: { status: 200 } };
  }
});

const handleAotControlAckSource = source.substring(
  source.indexOf("async function handleAotControlAck("),
  source.indexOf("async function handleAotControlHealth(")
);
vm.runInContext(`
  ${handleAotControlAckSource}
`, context);

const triggerAck = async (body) => {
  fleetCalls = [];
  return await vm.runInContext(`handleAotControlAck({ body: ${JSON.stringify(body)} }, {})`, context);
};

// ACCEPTED pass
await triggerAck({ protocol: "fleet-batch-v1", device_id: "m1", action_id: "act1", batch_action: "ALLOCATE_SERVER", status: "ACCEPTED" });
if (fleetCalls.length !== 1 || fleetCalls[0].body.status !== "ACCEPTED") throw new Error("ACCEPTED failed");

// ALLOCATED pass
await triggerAck({ protocol: "fleet-batch-v1", device_id: "m1", action_id: "act1", batch_action: "ALLOCATE_SERVER", status: "ALLOCATED" });
if (fleetCalls.length !== 1 || fleetCalls[0].body.status !== "ALLOCATED") throw new Error("ALLOCATED failed");

// OPENED pass
await triggerAck({ protocol: "fleet-batch-v1", device_id: "m1", action_id: "act1", batch_action: "ALLOCATE_SERVER", status: "OPENED" });
if (fleetCalls.length !== 1 || fleetCalls[0].body.status !== "OPENED") throw new Error("OPENED failed");

// FAILED + reason preserved
await triggerAck({ protocol: "fleet-batch-v1", device_id: "m1", action_id: "act1", batch_action: "ALLOCATE_SERVER", status: "FAILED", reason: "roblox_error" });
if (fleetCalls.length !== 1 || fleetCalls[0].body.reason !== "roblox_error") throw new Error("FAILED reason failed");

// invalid status fail
let res = await triggerAck({ protocol: "fleet-batch-v1", device_id: "m1", action_id: "act1", batch_action: "ALLOCATE_SERVER", status: "INVALID_STAT" });
if (res.status !== 400 || res.error !== "invalid_aot_ack") throw new Error("invalid status did not fail");

console.log("AOT_ACK_SELFTEST=OK");
