import fs from "node:fs/promises";
import vm from "node:vm";
import { webcrypto } from "node:crypto";

const sourceUrl = new URL("./fleet-state.js", import.meta.url);
const source = await fs.readFile(sourceUrl, "utf8");
const context = vm.createContext({
  URL, Request, Response, JSON, Map, Set, Object, Array,
  String, Number, Boolean, Math, Date, console,
  crypto: webcrypto,
});
const loaded = new vm.SourceTextModule(source, {
  context,
  identifier: sourceUrl.href,
});
await loaded.link(async (specifier) => {
  if (specifier !== "cloudflare:workers") {
    throw new Error(`unexpected import: ${specifier}`);
  }
  return new vm.SyntheticModule(
    ["DurableObject"],
    function () {
      this.setExport("DurableObject", class DurableObject {
        constructor(ctx, env) {
          this.ctx = ctx;
          this.env = env;
        }
      });
    },
    { context }
  );
});
await loaded.evaluate();
const { FleetState } = loaded.namespace;

class Storage {
  constructor() { this.values = new Map(); }
  async get(key) { return this.values.get(key); }
  async put(key, value) { this.values.set(key, value); }
  async delete(key) { this.values.delete(key); }
  async list(options = {}) {
    return new Map([...this.values].filter(([key]) => String(key).startsWith(options.prefix || "")));
  }
  async setAlarm(value) { this.alarm = value; }
}

const onlineTags = new Set();
const socketSends = [];
const ctx = {
  storage: new Storage(),
  getWebSockets(tag) {
    return onlineTags.has(tag)
      ? [{ send(payload) { socketSends.push({ tag, payload, at: Date.now() }); } }]
      : [];
  },
  getTags(socket) { return socket.tags || []; },
};
const fleet = new FleetState(ctx, {});
const session = "m37-m117-p3";
const reference = "m37";
const follower = "m117";
await ctx.storage.put(`aot_session:${session}`, {
  version: 1,
  session_id: session,
  reference_device_id: reference,
  followers: { [follower]: { device_id: follower } },
  paused: false,
  last_control: null,
});
onlineTags.add(`aot:reference:${session}:${reference}`);
onlineTags.add(`aot:follower:${session}:${follower}`);

function live(deviceId, role, overrides = {}) {
  return {
    device_id: deviceId,
    role,
    session_id: session,
    package: "org.swiftapps.swiftbackup",
    fingerprint: "a".repeat(24),
    layout_signature: "b".repeat(24),
    coordinate_ready: true,
    ime_visible: false,
    width: 1000,
    height: 2000,
    updated_at: Date.now(),
    ...overrides,
  };
}

const sanitized = fleet.sanitizeAotLiveStatus(
  { role: "follower", sessionId: session, deviceId: follower },
  {
    type: "aot_status",
    protocol: "phase4-1",
    role: "follower",
    session_id: session,
    device_id: follower,
    package: "org.swiftapps.swiftbackup",
    fingerprint: "a".repeat(24),
    layout_signature: "b".repeat(24),
    coordinate_ready: true,
    ime_visible: false,
    width: 1000,
    height: 2000,
  }
);
if (!sanitized || sanitized.coordinate_ready !== true) {
  throw new Error("sanitize layout status failed");
}

fleet.aotLive.set(`${session}:${reference}`, live(reference, "reference"));
fleet.aotLive.set(`${session}:${follower}`, live(follower, "follower"));
let response = await fleet.getAotHubState(
  new URL(`https://test/aot/hub/state?session=${session}`)
);
let state = await response.json();
if (state.followers[0].status !== "SYNCED") {
  throw new Error("matching layout was not SYNCED");
}

fleet.aotLive.set(`${session}:${follower}`, live(follower, "follower", {
  layout_signature: "c".repeat(24),
}));
response = await fleet.getAotHubState(
  new URL(`https://test/aot/hub/state?session=${session}`)
);
state = await response.json();
if (
  state.followers[0].status !== "OUT_OF_SYNC" ||
  state.followers[0].layout_compatible !== false
) {
  throw new Error("layout mismatch was not detected");
}

fleet.aotLive.set(`${session}:${follower}`, live(follower, "follower", {
  coordinate_ready: false,
  ime_visible: true,
}));
response = await fleet.getAotHubState(
  new URL(`https://test/aot/hub/state?session=${session}`)
);
state = await response.json();
if (state.followers[0].status !== "OUT_OF_SYNC") {
  throw new Error("IME readiness failure was not OUT_OF_SYNC");
}

fleet.aotLive.set(`${session}:${follower}`, live(follower, "follower", {
  updated_at: Date.now() - 20000,
}));
response = await fleet.getAotHubState(
  new URL(`https://test/aot/hub/state?session=${session}`)
);
state = await response.json();
if (state.followers[0].status !== "WAITING") {
  throw new Error("stale live status was not WAITING");
}

// Fixed batch action snapshots connectivity and sends directly to all online sockets.
const batchSessionRecord = await ctx.storage.get(`aot_session:${session}`);
batchSessionRecord.followers.m88 = { device_id: "m88" };
batchSessionRecord.followers.m74 = { device_id: "m74" };
await ctx.storage.put(`aot_session:${session}`, batchSessionRecord);
onlineTags.add(`aot:follower:${session}:m74`);
socketSends.length = 0;
response = await fleet.controlAotHub(new Request("https://test/aot/hub/control", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    protocol: "phase4-1",
    session_id: session,
    kind: "open_swift_backup",
    target_device_ids: ["m999"],
  }),
}));
if (response.status !== 400 || socketSends.length !== 0) {
  throw new Error("non-member batch target was trusted");
}
response = await fleet.controlAotHub(new Request("https://test/aot/hub/control", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    protocol: "phase4-1",
    session_id: session,
    kind: "open_swift_backup",
    target_device_ids: [reference, follower, "m88"],
  }),
}));
let batchResponse = await response.json();
if (!batchResponse.ok || batchResponse.batch.action !== "OPEN_SWIFT_BACKUP") {
  throw new Error("batch dispatch failed");
}
const byId = Object.fromEntries(
  batchResponse.batch.devices.map((item) => [item.device_id, item])
);
if (byId[reference].status !== "SENT" || byId[follower].status !== "SENT") {
  throw new Error("online devices were not SENT");
}
if (byId.m88.status !== "SKIPPED_OFFLINE") {
  throw new Error("offline device was not skipped");
}
if (byId.m74) {
  throw new Error("unselected online device entered the batch");
}
if (socketSends.length !== 2) {
  throw new Error(`expected two near-simultaneous sends, got ${socketSends.length}`);
}
if (Math.max(...socketSends.map((item) => item.at)) - Math.min(...socketSends.map((item) => item.at)) > 100) {
  throw new Error("online batch sends were not near-simultaneous");
}
const sentPayloads = socketSends.map((item) => JSON.parse(item.payload));
if (new Set(sentPayloads.map((item) => item.action_id)).size !== 1) {
  throw new Error("batch action id was not shared");
}
if (sentPayloads.some((item) =>
  item.package !== "org.swiftapps.swiftbackup" ||
  item.action !== "OPEN_SWIFT_BACKUP"
)) {
  throw new Error("batch action was not fixed to Swift Backup");
}

const batchActionId = batchResponse.batch.action_id;
for (const status of ["ACCEPTED", "OPENED"]) {
  response = await fleet.dispatchAotAck(new Request("https://test/aot/ack", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      protocol: "phase4-1",
      session_id: session,
      reference_device_id: reference,
      follower_device_id: follower,
      action_id: batchActionId,
      batch_action: "OPEN_SWIFT_BACKUP",
      status,
      executed: status === "OPENED",
    }),
  }));
  if (!response.ok) throw new Error(`batch ACK ${status} rejected`);
}
response = await fleet.getAotHubState(
  new URL(`https://test/aot/hub/state?session=${session}`)
);
state = await response.json();
const opened = state.last_batch.devices.find((item) => item.device_id === follower);
if (!opened || opened.display_status !== "SENT → ACCEPTED → OPENED") {
  throw new Error("batch ACK transition was not retained");
}

const stored = await ctx.storage.get(`aot_session:${session}`);
stored.last_batch.devices[reference].status = "ACCEPTED";
stored.last_batch.devices[reference].history = ["SENT", "ACCEPTED"];
stored.last_batch.expires_at = Date.now() - 1;
await ctx.storage.put(`aot_session:${session}`, stored);
response = await fleet.getAotHubState(
  new URL(`https://test/aot/hub/state?session=${session}`)
);
state = await response.json();
const timedOut = state.last_batch.devices.find((item) => item.device_id === reference);
if (!timedOut || timedOut.status !== "TIMEOUT") {
  throw new Error("batch timeout did not terminate pending state");
}

// Dashboard sockets use the same hibernating Durable Object and receive ACK/disconnect deltas.
if (
  !source.includes("acceptWebSocket(server, [this.aotDashboardTag(sessionId)])") ||
  !source.includes("async webSocketClose(socket)")
) {
  throw new Error("dashboard WebSocket is not using hibernation APIs");
}
onlineTags.add(`aot-dashboard:${session}`);
socketSends.length = 0;
response = await fleet.controlAotHub(new Request("https://test/aot/hub/control", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    protocol: "phase4-1",
    session_id: session,
    kind: "open_swift_backup",
    target_device_ids: ["m74"],
  }),
}));
const dashboardBatch = await response.json();
socketSends.length = 0;
response = await fleet.dispatchAotAck(new Request("https://test/aot/ack", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    protocol: "phase4-1",
    session_id: session,
    reference_device_id: reference,
    follower_device_id: "m74",
    action_id: dashboardBatch.batch.action_id,
    batch_action: "OPEN_SWIFT_BACKUP",
    status: "ACCEPTED",
    executed: false,
  }),
}));
if (
  !response.ok ||
  socketSends.length !== 1 ||
  JSON.parse(socketSends[0].payload).type !== "aot_hub_state"
) {
  throw new Error("dashboard did not receive ACK state");
}
socketSends.length = 0;
const closingSocket = {
  tags: [`aot:follower:${session}:${follower}`],
};
onlineTags.delete(`aot:follower:${session}:${follower}`);
await fleet.webSocketClose(closingSocket);
if (
  socketSends.length !== 1 ||
  JSON.parse(socketSends[0].payload).type !== "aot_hub_state"
) {
  throw new Error("dashboard did not receive disconnect state");
}

// Scale POC: 40 connected devices, one direct batch, and 40 accepted/opened ACKs.
const scaleSession = "scale-40";
const scaleFollowers = {};
const scaleIds = [];
for (let index = 1; index <= 40; index += 1) {
  const deviceId = `m${index}`;
  scaleIds.push(deviceId);
  scaleFollowers[deviceId] = { device_id: deviceId };
  onlineTags.add(`aot:follower:${scaleSession}:${deviceId}`);
}
await ctx.storage.put(`aot_session:${scaleSession}`, {
  version: 1,
  session_id: scaleSession,
  reference_device_id: null,
  followers: scaleFollowers,
  paused: false,
  last_control: null,
});
socketSends.length = 0;
response = await fleet.controlAotHub(new Request("https://test/aot/hub/control", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    protocol: "phase4-1",
    session_id: scaleSession,
    kind: "open_swift_backup",
    target_device_ids: scaleIds,
  }),
}));
batchResponse = await response.json();
if (!response.ok || socketSends.length !== 40) {
  throw new Error("40-device batch dispatch failed");
}
const scaleActionId = batchResponse.batch.action_id;
let openedAcks = 0;
for (const deviceId of scaleIds) {
  for (const status of ["ACCEPTED", "OPENED"]) {
    response = await fleet.dispatchAotAck(new Request("https://test/aot/ack", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        protocol: "phase4-1",
        session_id: scaleSession,
        reference_device_id: scaleIds[0],
        follower_device_id: deviceId,
        action_id: scaleActionId,
        batch_action: "OPEN_SWIFT_BACKUP",
        status,
        executed: status === "OPENED",
      }),
    }));
    if (!response.ok) throw new Error(`40-device ACK failed: ${deviceId}/${status}`);
    if (status === "OPENED") openedAcks += 1;
  }
}
if (openedAcks !== 40) {
  throw new Error("did not accept 40 OPENED ACKs");
}

// Worker rollout reuses the same hibernating DO and never fans out beyond five.
socketSends.length = 0;
response = await fleet.controlAotHub(new Request("https://test/aot/hub/control", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ protocol: "phase4-1", session_id: scaleSession, kind: "update_stable" }),
}));
let updateResponse = await response.json();
if (!response.ok || updateResponse.update.channel !== "stable") {
  throw new Error("stable update dispatch failed");
}
if (socketSends.filter((item) => item.tag.startsWith("aot:follower:")).length > 5) {
  throw new Error("worker rollout exceeded group size five");
}
let updateRecord = await ctx.storage.get(`aot_session:${scaleSession}`);
const updateActionId = updateRecord.last_update.action_id;
let updatedCount = 0;
while (updateRecord.last_update.active_group) {
  const active = [...updateRecord.last_update.active_group];
  if (active.length > 5) throw new Error("active update group exceeded five");
  for (const member of active) {
    for (const status of ["DOWNLOADING", "VERIFIED", "INSTALLING", "RESTARTING", "HEALTHY"]) {
      response = await fleet.dispatchAotAck(new Request("https://test/aot/ack", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          protocol: "phase4-1", session_id: scaleSession,
          reference_device_id: scaleIds[0], follower_device_id: member.device_id,
          action_id: updateActionId, batch_action: "UPDATE_WORKER", status,
        }),
      }));
      if (!response.ok) throw new Error(`update ACK rejected: ${member.device_id}/${status}`);
    }
    updatedCount += 1;
  }
  updateRecord = await ctx.storage.get(`aot_session:${scaleSession}`);
}
if (updatedCount !== 39) {
  throw new Error(`stable channel updated ${updatedCount}, expected 39`);
}
for (const id of ["m37", "m117"]) {
  if (updateRecord.last_update.devices[id]) throw new Error("canary device entered stable rollout");
}
if (!source.includes("async alarm()") || !source.includes("AOT_UPDATE_GROUP_SIZE = 5")) {
  throw new Error("durable rollout timeout/group guard missing");
}

// A failed group stops the release; a later group is never dispatched.
socketSends.length = 0;
response = await fleet.controlAotHub(new Request("https://test/aot/hub/control", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ protocol: "phase4-1", session_id: scaleSession, kind: "update_stable" }),
}));
updateResponse = await response.json();
const failedActionId = updateResponse.update.action_id;
updateRecord = await ctx.storage.get(`aot_session:${scaleSession}`);
const failedGroup = [...updateRecord.last_update.active_group];
for (let index = 0; index < failedGroup.length; index += 1) {
  const status = index === 0 ? "FAILED" : "HEALTHY";
  response = await fleet.dispatchAotAck(new Request("https://test/aot/ack", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      protocol: "phase4-1", session_id: scaleSession,
      reference_device_id: scaleIds[0], follower_device_id: failedGroup[index].device_id,
      action_id: failedActionId, batch_action: "UPDATE_WORKER", status,
    }),
  }));
  if (!response.ok) throw new Error("failed-group ACK rejected");
}
updateRecord = await ctx.storage.get(`aot_session:${scaleSession}`);
if (updateRecord.last_update.active_group || updateRecord.last_update.groups.length) {
  throw new Error("rollout continued after an unhealthy group");
}
if (socketSends.filter((item) => item.tag.startsWith("aot:follower:")).length !== 5) {
  throw new Error("a second rollout group was sent after failure");
}

onlineTags.add(`aot:follower:${session}:${follower}`);
socketSends.length = 0;
response = await fleet.controlAotHub(new Request("https://test/aot/hub/control", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ protocol: "phase4-1", session_id: session, kind: "update_canary" }),
}));
updateResponse = await response.json();
if (!response.ok || updateResponse.update.devices.length !== 2 || socketSends.filter((item) => item.tag.startsWith("aot:") && !item.tag.startsWith("aot-dashboard:")).length !== 2) {
  throw new Error("m37+m117 canary dispatch failed");
}

console.log("AOT_FLEET_STATE_SELFTEST=OK");
