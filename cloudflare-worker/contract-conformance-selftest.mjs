import fs from "node:fs/promises";
import vm from "node:vm";
import { webcrypto as crypto } from "node:crypto";

// Load canonical contract
const contractPath = new URL("../contracts/fleet_batch_v1_contract.json", import.meta.url);
const contract = JSON.parse(await fs.readFile(contractPath, "utf8"));

// 1. Production worker.js parseTongHopLink verification
const workerSource = await fs.readFile(new URL("./worker.js", import.meta.url), "utf8");
const workerContext = vm.createContext({ console });
const funcMatch = workerSource.match(/function parseTongHopLink[\s\S]*?\n\}/);
if (!funcMatch) throw new Error("Could not find parseTongHopLink in worker.js");
const parseTongHopLink = vm.runInContext(`(() => { ${funcMatch[0]}; return parseTongHopLink; })()`, workerContext);

console.log("AOT_CONTRACT_CONFORMANCE_JS: Verifying against", contract.protocol_version);

// Derive tab count and package sequence dynamically from canonical contract
const maxTabs = contract.package_mapping.max_tabs;
const makeUrl = (i) => `https://www.roblox.com/games/97598239454123?privateServerLinkCode=${String(i).padStart(32, "0")}`;
const sampleText = Array.from({ length: maxTabs }, (_, i) => makeUrl(i + 1)).join("\n");
const parsed = parseTongHopLink(sampleText, ["m117"], maxTabs);

const expectedPkgs = contract.package_mapping.suffixes.slice(0, maxTabs).map(s => `${contract.package_mapping.prefix}${s}`);
if (parsed["m117"].length !== maxTabs) {
  throw new Error(`JS produced ${parsed["m117"].length} tabs, expected max ${maxTabs}`);
}
for (let i = 0; i < maxTabs; i++) {
  if (parsed["m117"][i].pkg !== expectedPkgs[i]) {
    throw new Error(`Package mismatch at index ${i}: got ${parsed["m117"][i].pkg}, expected ${expectedPkgs[i]}`);
  }
}
console.log("AOT_CONTRACT_CONFORMANCE_JS: Package mapping aligned with contract derived bounds");

// 2. Strict Schema validator functions
function validateBatchActionEnvelope(msg) {
  const schema = contract.schemas.BatchActionEnvelope;
  if (!msg || typeof msg !== "object") throw new Error("BatchActionEnvelope must be an object");
  for (const field of schema.required) {
    if (msg[field] === undefined || msg[field] === null) {
      throw new Error(`Missing required field in BatchActionEnvelope: ${field}`);
    }
  }
  if (msg.type !== "aot_batch_action") {
    throw new Error(`Invalid type const: got ${msg.type}, expected "aot_batch_action"`);
  }
  if (msg.protocol !== contract.protocol_version) {
    throw new Error(`Invalid protocol version: got ${msg.protocol}, expected ${contract.protocol_version}`);
  }
  if (typeof msg.action_id !== "string" || msg.action_id.length === 0) {
    throw new Error("action_id must be a non-empty string");
  }
  if (!contract.batch_action_enums.includes(msg.action)) {
    throw new Error(`Unknown batch action: ${msg.action}`);
  }
  if (!Array.isArray(msg.target_device_ids) || msg.target_device_ids.length < 1) {
    throw new Error("target_device_ids must be a non-empty array");
  }
  if (!Number.isInteger(msg.expires_at)) {
    throw new Error("expires_at must be an integer");
  }
  if (msg.allocation !== undefined) {
    if (!Array.isArray(msg.allocation) || msg.allocation.length < contract.package_mapping.min_tabs || msg.allocation.length > contract.package_mapping.max_tabs) {
      throw new Error(`allocation length must be between ${contract.package_mapping.min_tabs} and ${contract.package_mapping.max_tabs}`);
    }
    const urlRegex = new RegExp(contract.url_rules.regex);
    for (const item of msg.allocation) {
      if (!item.pkg || !/^com\.tinh\.vv\.h[i-r]$/.test(item.pkg)) {
        throw new Error(`Invalid allocation package format: ${item.pkg}`);
      }
      if (!item.url || !urlRegex.test(item.url)) {
        throw new Error(`Allocation URL does not conform to contract regex: ${item.url}`);
      }
    }
  }
  return true;
}

function validateBatchAckEnvelope(ack) {
  const schema = contract.schemas.BatchAckEnvelope;
  if (!ack || typeof ack !== "object") throw new Error("BatchAckEnvelope must be an object");
  for (const field of schema.required) {
    if (ack[field] === undefined || ack[field] === null) {
      throw new Error(`Missing required field in BatchAckEnvelope: ${field}`);
    }
  }
  if (ack.protocol !== contract.protocol_version) {
    throw new Error(`Invalid protocol in ACK: got ${ack.protocol}, expected ${contract.protocol_version}`);
  }
  if (typeof ack.device_id !== "string" || ack.device_id.length === 0) {
    throw new Error("device_id must be a non-empty string");
  }
  if (typeof ack.action_id !== "string" || ack.action_id.length === 0) {
    throw new Error("action_id must be a non-empty string");
  }
  if (!contract.batch_ack_action_enums.includes(ack.batch_action)) {
    throw new Error(`Invalid batch_action enum in ACK: ${ack.batch_action}`);
  }
  if (!contract.batch_ack_status_enums.includes(ack.status)) {
    throw new Error(`Invalid status enum in ACK: ${ack.status}`);
  }
  if (ack.executed !== undefined && typeof ack.executed !== "boolean") {
    throw new Error("executed field must be a boolean");
  }
  if (ack.reason !== undefined && typeof ack.reason !== "string") {
    throw new Error("reason field must be a string");
  }
  return true;
}

// 3. Real FleetState DO message production and ACK consumption testing
const fsContext = vm.createContext({ URL, Request, Response, Headers, JSON, Map, Set, Object, Array, String, Number, Boolean, Math, Date, console, crypto, structuredClone });
fsContext.fetch = async () => ({ ok: true, json: async () => ({}) });
const cf = new vm.SyntheticModule(["DurableObject"], function () {
  this.setExport("DurableObject", class { constructor(ctx, env) { this.ctx = ctx; this.env = env; } });
}, { context: fsContext });
const fsSource = await fs.readFile(new URL("./fleet-state.js", import.meta.url), "utf8");
const fsMod = new vm.SourceTextModule(fsSource, { context: fsContext });
await fsMod.link(async specifier => { if (specifier === "cloudflare:workers") return cf; throw new Error(specifier); });
await fsMod.evaluate();

const store = new Map();
const sockets = new Map();
const sentFrames = [];
const ctx = {
  storage: { get: async k => store.get(k), put: async (k, v) => store.set(k, structuredClone(v)), setAlarm: async () => {}, list: async () => new Map() },
  getWebSockets: tag => sockets.get(tag) || [],
  getTags: () => [],
  acceptWebSocket() {},
  waitUntil() {},
};
const fleet = new fsMod.namespace.FleetState(ctx, { TELEGRAM_BOT_TOKEN: "tok" });

const devId = "m117";
const record = await fleet.readFleet();
record.devices[devId] = { device_id: devId, capabilities: ["allocate_server_2pc"] };
sockets.set(`aot-device:${devId}`, [{
  send(frameStr) {
    try {
      sentFrames.push(JSON.parse(frameStr));
    } catch {
      sentFrames.push(frameStr);
    }
  }
}]);
await fleet.writeFleet(record);

// A. Test Real BatchActionEnvelope emitted by FleetState DO for ALLOCATE_SERVER (PREPARE)
const allocUrls = [
  { pkg: "com.tinh.vv.hi", url: makeUrl(1) },
  { pkg: "com.tinh.vv.hj", url: makeUrl(2) },
];
sentFrames.length = 0;
const allocBatchRes = await fleet.dispatchFleetBatch(record, "ALLOCATE_SERVER", [devId], {
  allocationMap: { [devId]: allocUrls }
});
if (!allocBatchRes.ok) throw new Error("dispatchFleetBatch failed");
if (sentFrames.length !== 1) throw new Error(`Expected 1 sent frame, got ${sentFrames.length}`);

const realPrepareFrame = sentFrames[0];
validateBatchActionEnvelope(realPrepareFrame);
if (realPrepareFrame.action !== "PREPARE_ALLOCATE_SERVER") {
  throw new Error(`Expected action PREPARE_ALLOCATE_SERVER, got ${realPrepareFrame.action}`);
}
console.log("AOT_CONTRACT_CONFORMANCE_JS: Real FleetState PREPARE_ALLOCATE_SERVER envelope validated");

// B. Test Real BatchAck consumption in FleetState DO for all canonical statuses
for (const status of contract.batch_ack_status_enums) {
  const req = new Request("https://test/aot/ack", {
    method: "POST",
    body: JSON.stringify({
      protocol: contract.protocol_version,
      device_id: devId,
      action_id: realPrepareFrame.action_id,
      batch_action: "ALLOCATE_SERVER",
      status: status,
      executed: status === "OPENED"
    })
  });
  const ackRes = await fleet.dispatchFleetAck(req);
  if (!ackRes || ackRes.status >= 500) {
    throw new Error(`FleetState failed to process canonical status: ${status}`);
  }
}
console.log("AOT_CONTRACT_CONFORMANCE_JS: Real FleetState ACK handler validated across canonical statuses");

// 4. URL Regex Parity Verification Matrix
const contractRegex = new RegExp(contract.url_rules.regex);

// Valid Matrix
const matrixValid = [
  "https://www.roblox.com/games/97598239454123?privateServerLinkCode=11111111111111111111111111111111",
  "https://roblox.com/games/12345?PrivateServerLinkCode=abcdef0123456789abcdef0123456789",
  "https://www.roblox.com/games/999?privateServerLinkCode=ABCDEF0123456789",
];
for (const url of matrixValid) {
  if (!contractRegex.test(url)) throw new Error(`Contract regex rejected valid URL: ${url}`);
}

// Invalid Matrix
const matrixInvalid = [
  "http://www.roblox.com/games/123?privateServerLinkCode=11111111111111111111111111111111",     // http
  "https://evil-roblox.com/games/123?privateServerLinkCode=11111111111111111111111111111111",    // foreign host
  "https://roblox.com/home?privateServerLinkCode=11111111111111111111111111111111",              // wrong path
  "https://roblox.com/games/123?privateServerLinkCode=NOT_HEX_ZZZ!",                              // non-hex code
  "https://roblox.com/games/123?privateServerLinkCode=111&extraParam=222",                        // extra param
];
for (const url of matrixInvalid) {
  if (contractRegex.test(url)) throw new Error(`Contract regex accepted invalid URL: ${url}`);
}

console.log("AOT_CONTRACT_CONFORMANCE_JS=OK");
