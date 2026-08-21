import fs from "node:fs/promises";
import vm from "node:vm";

// Load canonical contract
const contractPath = new URL("../contracts/fleet_batch_v1_contract.json", import.meta.url);
const contract = JSON.parse(await fs.readFile(contractPath, "utf8"));

// Load production worker.js parseTongHopLink function
const workerSource = await fs.readFile(new URL("./worker.js", import.meta.url), "utf8");
const context = vm.createContext({ console });
const funcMatch = workerSource.match(/function parseTongHopLink[\s\S]*?\n\}/);
if (!funcMatch) throw new Error("Could not find parseTongHopLink in worker.js");
const parseTongHopLink = vm.runInContext(`(() => { ${funcMatch[0]}; return parseTongHopLink; })()`, context);

console.log("AOT_CONTRACT_CONFORMANCE_JS: Starting verification against", contract.protocol_version);

// 1. Conformance of package mapping
const dummyUrl = (i) => `https://www.roblox.com/games/97598239454123?privateServerLinkCode=${String(i).padStart(32, "0")}`;
const sampleText = Array.from({ length: 10 }, (_, i) => dummyUrl(i + 1)).join("\n");
const parsed = parseTongHopLink(sampleText, ["m117"], 10);

const expectedPkgs = contract.package_mapping.suffixes.map(s => `${contract.package_mapping.prefix}${s}`);
if (parsed["m117"].length !== contract.package_mapping.max_tabs) {
  throw new Error(`JS produced ${parsed["m117"].length} tabs, expected max ${contract.package_mapping.max_tabs}`);
}
for (let i = 0; i < 10; i++) {
  if (parsed["m117"][i].pkg !== expectedPkgs[i]) {
    throw new Error(`Package mismatch at index ${i}: got ${parsed["m117"][i].pkg}, expected ${expectedPkgs[i]}`);
  }
}
console.log("AOT_CONTRACT_CONFORMANCE_JS: Package mapping aligned with contract");

// 2. Conformance of URL regex parsing
const contractRegex = new RegExp(contract.url_rules.regex);
const validUrls = [
  "https://www.roblox.com/games/97598239454123?privateServerLinkCode=11111111111111111111111111111111",
  "https://roblox.com/games/12345?PrivateServerLinkCode=abcdef0123456789abcdef0123456789",
];
for (const url of validUrls) {
  if (!contractRegex.test(url)) {
    throw new Error(`Contract regex failed to match valid URL: ${url}`);
  }
}

// 3. Batch Action envelope structure verification
function validateBatchActionEnvelope(msg) {
  const schema = contract.schemas.BatchActionEnvelope;
  for (const field of schema.required) {
    if (msg[field] === undefined || msg[field] === null) {
      throw new Error(`Missing required field in BatchActionEnvelope: ${field}`);
    }
  }
  if (msg.protocol !== contract.protocol_version) {
    throw new Error(`Invalid protocol version: got ${msg.protocol}, expected ${contract.protocol_version}`);
  }
  if (!contract.batch_action_enums.includes(msg.action)) {
    throw new Error(`Unknown batch action: ${msg.action}`);
  }
  if (!Array.isArray(msg.target_device_ids) || msg.target_device_ids.length < 1) {
    throw new Error("target_device_ids must be a non-empty array");
  }
  if (msg.allocation) {
    if (!Array.isArray(msg.allocation) || msg.allocation.length < 1 || msg.allocation.length > 10) {
      throw new Error("allocation length must be between 1 and 10");
    }
  }
  return true;
}

// 4. Batch Ack envelope structure verification
function validateBatchAckEnvelope(ack) {
  const schema = contract.schemas.BatchAckEnvelope;
  for (const field of schema.required) {
    if (ack[field] === undefined || ack[field] === null) {
      throw new Error(`Missing required field in BatchAckEnvelope: ${field}`);
    }
  }
  if (ack.protocol !== contract.protocol_version) {
    throw new Error(`Invalid protocol in ACK: got ${ack.protocol}, expected ${contract.protocol_version}`);
  }
  if (!contract.batch_ack_status_enums.includes(ack.status)) {
    throw new Error(`Invalid status enum in ACK: ${ack.status}`);
  }
  return true;
}

// Test production message conformance
const sampleBatchAction = {
  type: "aot_batch_action",
  protocol: "fleet-batch-v1",
  action: "PREPARE_ALLOCATE_SERVER",
  action_id: "act-test-01",
  target_device_ids: ["m117"],
  expires_at: Date.now() + 10000,
  allocation: [
    { pkg: "com.tinh.vv.hi", url: dummyUrl(1) },
    { pkg: "com.tinh.vv.hj", url: dummyUrl(2) },
  ],
};
validateBatchActionEnvelope(sampleBatchAction);

const sampleBatchAck = {
  protocol: "fleet-batch-v1",
  device_id: "m117",
  action_id: "act-test-01",
  batch_action: "ALLOCATE_SERVER",
  status: "PREPARE_READY",
  executed: false,
};
validateBatchAckEnvelope(sampleBatchAck);

console.log("AOT_CONTRACT_CONFORMANCE_JS=OK");
