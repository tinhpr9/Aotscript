import fs from "node:fs/promises";
import vm from "node:vm";

const source = await fs.readFile(new URL("./worker.js", import.meta.url), "utf8");
if (source.includes("function aotHubHtml()")) throw new Error("dead legacy dashboard function aotHubHtml still exists");
const start = source.indexOf("function fleetHubHtml()");
const end = source.indexOf("async function handleAotHubPage", start);
if (start < 0 || end < 0) throw new Error("fleet dashboard unavailable");
const dashboard = source.slice(start, end);
for (const required of [
  "Chọn máy online", "Bỏ chọn hết", "Mở Swift Backup", "Mở Apps",
  "open_swift_backup", "open_swift_apps", "target_device_ids", "WebSocket",
]) {
  if (!dashboard.includes(required)) throw new Error(`dashboard missing ${required}`);
}
for (const forbidden of [
  "REFERENCE", "FOLLOWERS", "referencePreview", "preview_b64", "PAUSE",
  "RESUME", "SWIPE", "session_id", "setInterval(", "x_norm", "y_norm",
]) {
  if (dashboard.includes(forbidden)) throw new Error(`legacy dashboard exposed ${forbidden}`);
}
if (!dashboard.includes("Math.random()") || !dashboard.includes("Math.pow(2,retry++)")) {
  throw new Error("dashboard reconnect jitter/backoff missing");
}
if (!source.includes('const AOT_HUB_PROTOCOL_VERSION = "fleet-batch-v1"')) {
  throw new Error("fleet protocol version missing");
}
if (!source.includes('["OPEN_SWIFT_BACKUP", "OPEN_SWIFT_APPS", "UPDATE_WORKER"]')) {
  throw new Error("batch ACK actions incomplete");
}
console.log("AOT_HUB_FLEET_SELFTEST=OK");
