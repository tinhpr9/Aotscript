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
    if (String(tag).startsWith("aot-session:")) {
      const sessionId = String(tag).slice("aot-session:".length);
      return [...onlineTags]
        .filter((value) => value.startsWith("aot:") && value.includes(`:${sessionId}:`))
        .map((value) => ({
          tags: [value, tag],
          send(payload) { socketSends.push({ tag: value, payload, at: Date.now() }); },
          close() { onlineTags.delete(value); },
        }));
    }
    return onlineTags.has(tag)
      ? [{
          tags: [tag],
          send(payload) { socketSends.push({ tag, payload, at: Date.now() }); },
          close() { onlineTags.delete(tag); },
        }]
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

// msetup discovers exactly one active session and verifies actual Hub presence.
let response = await fleet.discoverAotRegistration(new Request("https://test/aot/registration/discover", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ device_id: "m200" }),
}));
let registration = await response.json();
if (!response.ok || registration.role !== "follower" || registration.session_id !== session || registration.reference_device_id !== reference) {
  throw new Error("fresh AOT registration discovery failed");
}
response = await fleet.discoverAotRegistration(new Request("https://test/aot/registration/discover", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ device_id: follower }),
}));
registration = await response.json();
if (!response.ok || registration.role !== "follower") {
  throw new Error("idempotent AOT registration discovery failed");
}
response = await fleet.verifyAotRegistration(new Request("https://test/aot/registration/verify", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ device_id: follower, role: "follower", session_id: session, reference_device_id: reference }),
}));
if (!response.ok) throw new Error("online registered device was not verified");
response = await fleet.verifyAotRegistration(new Request("https://test/aot/registration/verify", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ device_id: "m200", role: "follower", session_id: session, reference_device_id: reference }),
}));
if (response.status !== 409) throw new Error("server-unseen device was accepted");

const secondSession = "other-active";
await ctx.storage.put(`aot_session:${secondSession}`, {
  version: 1, session_id: secondSession, reference_device_id: "m50", followers: {},
});
onlineTags.add(`aot:reference:${secondSession}:m50`);
response = await fleet.discoverAotRegistration(new Request("https://test/aot/registration/discover", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ device_id: "m200" }),
}));
if (response.status !== 409) throw new Error("multiple active sessions were not rejected");
onlineTags.delete(`aot:reference:${secondSession}:m50`);
onlineTags.delete(`aot:reference:${session}:${reference}`);
response = await fleet.discoverAotRegistration(new Request("https://test/aot/registration/discover", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ device_id: "m200" }),
}));
if (response.status !== 404) throw new Error("missing active session was not rejected");
onlineTags.add(`aot:reference:${session}:${reference}`);

response = await fleet.resetAotIdentity(new Request("https://test/aot/registration/reset", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ old_device_id: follower, new_device_id: "m200", session_id: session, role: "follower" }),
}));
if (!response.ok) throw new Error("clone identity reset failed");
let resetRecord = await ctx.storage.get(`aot_session:${session}`);
if (resetRecord.followers[follower] || onlineTags.has(`aot:follower:${session}:${follower}`)) {
  throw new Error("old clone AOT state survived identity reset");
}
// Restore the fixture follower for the existing synchronization tests.
resetRecord.followers[follower] = { device_id: follower };
await ctx.storage.put(`aot_session:${session}`, resetRecord);
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
    worker_version: "aot-worker-fixture-new",
    capabilities: ["dynamic_update_channel"],
  }
);
if (
  !sanitized || sanitized.coordinate_ready !== true
  || sanitized.worker_version !== "aot-worker-fixture-new"
  || !sanitized.capabilities.includes("dynamic_update_channel")
) {
  throw new Error("sanitize layout status failed");
}

fleet.aotLive.set(`${session}:${reference}`, live(reference, "reference"));
fleet.aotLive.set(`${session}:${follower}`, live(follower, "follower"));
response = await fleet.getAotHubState(
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
if (batchResponse.batch.expires_at - batchResponse.batch.created_at !== 75000) {
  throw new Error("batch TTL does not allow cold-start verification");
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
if (timedOut.reason !== "worker_ack_timeout") {
  throw new Error("batch timeout reason was not exposed");
}

response = await fleet.controlAotHub(new Request("https://test/aot/hub/control", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    protocol: "phase4-1",
    session_id: session,
    kind: "open_swift_backup",
    target_device_ids: [follower],
  }),
}));
batchResponse = await response.json();
for (const [status, reason] of [
  ["ACCEPTED", ""],
  ["FAILED", "swift_backup_not_foreground"],
]) {
  response = await fleet.dispatchAotAck(new Request("https://test/aot/ack", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      protocol: "phase4-1",
      session_id: session,
      reference_device_id: reference,
      follower_device_id: follower,
      action_id: batchResponse.batch.action_id,
      batch_action: "OPEN_SWIFT_BACKUP",
      status,
      executed: false,
      reason,
    }),
  }));
  if (!response.ok) throw new Error(`batch failure ACK ${status} rejected`);
}
response = await fleet.getAotHubState(
  new URL(`https://test/aot/hub/state?session=${session}`)
);
state = await response.json();
const failed = state.last_batch.devices.find((item) => item.device_id === follower);
if (!failed || failed.reason !== "swift_backup_not_foreground") {
  throw new Error("batch failure reason was not retained");
}

// Dashboard sockets use the same hibernating Durable Object and receive ACK/disconnect deltas.
if (
  !source.includes("acceptWebSocket(server, [this.aotDashboardTag(sessionId)])") ||
  !source.includes("async webSocketClose(socket)") ||
  !source.includes("serializeAttachment") ||
  !source.includes("deserializeAttachment")
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

// Legacy .1-.4 workers advertise no dynamic-channel capability. The bridge
// tries only the selected sockets, then advances through the two historical
// fixed-channel protocols with distinct transport IDs after no authenticated
// ACK. A .5 worker succeeds on the primary canary attempt; a fixed-stable
// worker succeeds on the fallback.
const generatedDeviceId = (offset) => `m${700 + offset}`;
const legacySession = "legacy-bridge-session";
const legacyIds = [1, 2, 3].map(generatedDeviceId);
await ctx.storage.put(`aot_session:${legacySession}`, {
  version: 1, session_id: legacySession, reference_device_id: null,
  followers: Object.fromEntries(legacyIds.map((id) => [id, { device_id: id }])),
  paused: false,
});
for (const id of legacyIds) onlineTags.add(`aot:follower:${legacySession}:${id}`);
socketSends.length = 0;
response = await fleet.controlAotHub(new Request("https://test/aot/hub/control", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ protocol: "phase4-1", session_id: legacySession, kind: "update_canary" }),
}));
const legacyUpdate = await response.json();
const legacySelected = new Set(legacyUpdate.update.selected_device_ids);
const legacyMessages = socketSends.map((item) => ({
  tag: item.tag, payload: JSON.parse(item.payload),
}));
if (!response.ok || legacySelected.size !== 2 || legacyMessages.length !== 2) {
  throw new Error("legacy primary protocol dispatch failed");
}
for (const id of legacyIds) {
  const sent = legacyMessages.filter((item) => item.tag === `aot:follower:${legacySession}:${id}`);
  if (!legacySelected.has(id) && sent.length !== 0) {
    throw new Error("legacy bridge targeted a non-selected device");
  }
  if (legacySelected.has(id)) {
    if (
      sent.length !== 1 || sent[0].payload.channel !== "canary"
      || sent.some((item) => item.payload.target_device_ids.join(",") !== id)
      || !sent[0].payload.action_id.endsWith("-p1")
    ) {
      throw new Error("legacy primary protocol is not single-target");
    }
  }
}
let legacyRecord = await ctx.storage.get(`aot_session:${legacySession}`);
const legacyMembers = [...legacyRecord.last_update.active_group];
const acknowledgeLegacy = async (member, actionId) => {
  for (const status of ["DOWNLOADING", "VERIFIED", "INSTALLING", "RESTARTING", "HEALTHY"]) {
    response = await fleet.dispatchAotAck(new Request("https://test/aot/ack", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        protocol: "phase4-1", session_id: legacySession,
        reference_device_id: legacyIds[0], follower_device_id: member.device_id,
        action_id: actionId, batch_action: "UPDATE_WORKER",
        status, worker_version: "aot-worker-2026.08.11.7",
      }),
    }));
    if (!response.ok) throw new Error("legacy bridge upgrade ACK rejected");
  }
};
await acknowledgeLegacy(legacyMembers[0], `${legacyRecord.last_update.action_id}-p1`);
legacyRecord = await ctx.storage.get(`aot_session:${legacySession}`);
legacyRecord.last_update.group_deadline = 1;
await ctx.storage.put(`aot_session:${legacySession}`, legacyRecord);
socketSends.length = 0;
await fleet.alarm();
const fallbackMessages = socketSends.map((item) => ({
  tag: item.tag, payload: JSON.parse(item.payload),
}));
if (
  fallbackMessages.length !== 1
  || fallbackMessages[0].payload.channel !== "stable"
  || !fallbackMessages[0].payload.action_id.endsWith("-p2")
  || fallbackMessages[0].tag !== `aot:follower:${legacySession}:${legacyMembers[1].device_id}`
) {
  throw new Error("legacy fixed-channel fallback was not sequential and targeted");
}
legacyRecord = await ctx.storage.get(`aot_session:${legacySession}`);
await acknowledgeLegacy(legacyMembers[1], `${legacyRecord.last_update.action_id}-p2`);
legacyRecord = await ctx.storage.get(`aot_session:${legacySession}`);
if (legacyRecord.canary_release.status !== "HEALTHY") {
  throw new Error("legacy bridge did not unlock after two HEALTHY workers");
}

const legacyTimeoutSession = "legacy-bridge-timeout";
const timeoutIds = [4, 5].map(generatedDeviceId);
await ctx.storage.put(`aot_session:${legacyTimeoutSession}`, {
  version: 1, session_id: legacyTimeoutSession, reference_device_id: null,
  followers: Object.fromEntries(timeoutIds.map((id) => [id, { device_id: id }])),
  paused: false,
});
for (const id of timeoutIds) onlineTags.add(`aot:follower:${legacyTimeoutSession}:${id}`);
response = await fleet.controlAotHub(new Request("https://test/aot/hub/control", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ protocol: "phase4-1", session_id: legacyTimeoutSession, kind: "update_canary" }),
}));
if (!response.ok) throw new Error("legacy timeout fixture did not start");
let timeoutRecord = await ctx.storage.get(`aot_session:${legacyTimeoutSession}`);
timeoutRecord.last_update.group_deadline = 1;
await ctx.storage.put(`aot_session:${legacyTimeoutSession}`, timeoutRecord);
await fleet.alarm();
timeoutRecord = await ctx.storage.get(`aot_session:${legacyTimeoutSession}`);
timeoutRecord.last_update.group_deadline = 1;
timeoutRecord.last_update.final_deadline = 1;
await ctx.storage.put(`aot_session:${legacyTimeoutSession}`, timeoutRecord);
await fleet.alarm();
timeoutRecord = await ctx.storage.get(`aot_session:${legacyTimeoutSession}`);
if (
  !Object.values(timeoutRecord.last_update.devices).every((item) =>
    item.status === "FAILED"
    && item.reason.includes("phase4-1/UPDATE_WORKER/canary:no_authenticated_ack")
    && item.reason.includes("phase4-1/UPDATE_WORKER/stable:no_authenticated_ack")
  )
) {
  throw new Error("legacy protocol/action/channel rejection was not retained");
}

// Scale POC: 40 connected devices, one direct batch, and 40 accepted/opened ACKs.
const scaleSession = "scale-40";
const scaleFollowers = {};
const scaleIds = [];
for (let index = 1; index <= 40; index += 1) {
  const deviceId = `m${index + 100}`;
  scaleIds.push(deviceId);
  scaleFollowers[deviceId] = { device_id: deviceId };
  onlineTags.add(`aot:follower:${scaleSession}:${deviceId}`);
  fleet.aotLive.set(`${scaleSession}:${deviceId}`, {
    capabilities: ["dynamic_update_channel"],
    worker_version: "aot-worker-2026.08.11.7",
  });
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

// Dynamic canary selection is identity/role/order independent and prefers the
// two most recently failed ONLINE devices. An offline failed device is ignored.
let updateRecord = await ctx.storage.get(`aot_session:${scaleSession}`);
updateRecord.last_update = {
  action_id: "previous-update", channel: "stable", groups: [], active_group: null,
  devices: {
    [scaleIds[4]]: { device_id: scaleIds[4], status: "FAILED", updated_at: 200 },
    [scaleIds[19]]: { device_id: scaleIds[19], status: "FAILED", updated_at: 300 },
    [scaleIds[29]]: { device_id: scaleIds[29], status: "FAILED", updated_at: 400 },
  },
};
onlineTags.delete(`aot:follower:${scaleSession}:${scaleIds[29]}`);
await ctx.storage.put(`aot_session:${scaleSession}`, updateRecord);
socketSends.length = 0;
response = await fleet.controlAotHub(new Request("https://test/aot/hub/control", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ protocol: "phase4-1", session_id: scaleSession, kind: "update_canary" }),
}));
let updateResponse = await response.json();
const firstCanaryIds = new Set(updateResponse.update.selected_device_ids);
if (
  !response.ok || updateResponse.update.channel !== "canary"
  || firstCanaryIds.size !== 2
  || !firstCanaryIds.has(scaleIds[4])
  || !firstCanaryIds.has(scaleIds[19])
  || firstCanaryIds.has(scaleIds[29])
) {
  throw new Error("dynamic canary did not prefer two failed ONLINE devices");
}
if (!updateResponse.update.devices.every((item) => item.display_status === "QUEUED")) {
  throw new Error("canary did not expose QUEUED state");
}
const capableMessages = socketSends.map((item) => JSON.parse(item.payload));
if (
  capableMessages.length !== 2
  || capableMessages.some((item) =>
    item.channel !== "canary" || item.target_device_ids.length !== 1
  )
) {
  throw new Error("capable workers did not receive one standard Canary command");
}

// Fewer than two ONLINE members fails closed with a user-visible Vietnamese error.
const smallSession = "dynamic-small";
await ctx.storage.put(`aot_session:${smallSession}`, {
  version: 1, session_id: smallSession, reference_device_id: "m901",
  followers: { m902: { device_id: "m902" } }, paused: false,
});
onlineTags.add(`aot:reference:${smallSession}:m901`);
response = await fleet.controlAotHub(new Request("https://test/aot/hub/control", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ protocol: "phase4-1", session_id: smallSession, kind: "update_canary" }),
}));
const smallError = await response.json();
if (response.status !== 409 || smallError.error !== "canary_requires_two_online" || !smallError.message.includes("2 máy ONLINE")) {
  throw new Error("under-two-online canary error is not explicit");
}

// One failed trial device locks Stable release.
updateRecord = await ctx.storage.get(`aot_session:${scaleSession}`);
const failedCanaryAction = updateRecord.last_update.action_id;
const failedCanaryGroup = [...updateRecord.last_update.active_group];
for (let index = 0; index < failedCanaryGroup.length; index += 1) {
  response = await fleet.dispatchAotAck(new Request("https://test/aot/ack", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      protocol: "phase4-1", session_id: scaleSession,
      reference_device_id: scaleIds[0], follower_device_id: failedCanaryGroup[index].device_id,
      action_id: failedCanaryAction, batch_action: "UPDATE_WORKER",
      status: index === 0 ? "FAILED" : "HEALTHY",
      worker_version: "aot-worker-2026.08.11.7",
    }),
  }));
  if (!response.ok) throw new Error("canary failure ACK rejected");
}
response = await fleet.controlAotHub(new Request("https://test/aot/hub/control", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ protocol: "phase4-1", session_id: scaleSession, kind: "update_stable" }),
}));
const lockedStable = await response.json();
if (response.status !== 409 || lockedStable.error !== "canary_release_failed") {
  throw new Error("failed canary did not lock Stable release");
}

// A fresh dynamic canary succeeds regardless of Device ID, then Stable skips
// those same HEALTHY devices at the same worker version.
socketSends.length = 0;
response = await fleet.controlAotHub(new Request("https://test/aot/hub/control", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ protocol: "phase4-1", session_id: scaleSession, kind: "update_canary" }),
}));
updateResponse = await response.json();
if (!response.ok || updateResponse.update.selected_device_ids.length !== 2) {
  throw new Error("second dynamic canary dispatch failed");
}
const priorHealthyId = failedCanaryGroup[1].device_id;
const priorFailedId = failedCanaryGroup[0].device_id;
if (
  !updateResponse.update.selected_device_ids.includes(priorHealthyId)
  || !updateResponse.update.selected_device_ids.includes(priorFailedId)
  || socketSends.length !== 1
  || JSON.parse(socketSends[0].payload).target_device_ids[0] !== priorFailedId
) {
  throw new Error("retry did not retain HEALTHY worker and target only FAILED worker");
}
const healthyCanaryIds = [...updateResponse.update.selected_device_ids];
updateRecord = await ctx.storage.get(`aot_session:${scaleSession}`);
const healthyCanaryAction = updateRecord.last_update.action_id;
for (const member of [...updateRecord.last_update.active_group]) {
  for (const status of ["DOWNLOADING", "VERIFIED", "INSTALLING", "RESTARTING", "HEALTHY"]) {
    response = await fleet.dispatchAotAck(new Request("https://test/aot/ack", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        protocol: "phase4-1", session_id: scaleSession,
        reference_device_id: scaleIds[0], follower_device_id: member.device_id,
        action_id: healthyCanaryAction, batch_action: "UPDATE_WORKER", status,
        worker_version: "aot-worker-2026.08.11.7",
      }),
    }));
    if (!response.ok) throw new Error(`healthy canary ACK rejected: ${member.device_id}/${status}`);
  }
}
updateRecord = await ctx.storage.get(`aot_session:${scaleSession}`);
if (updateRecord.canary_release.status !== "HEALTHY") {
  throw new Error("two healthy canary devices did not unlock Stable");
}

socketSends.length = 0;
response = await fleet.controlAotHub(new Request("https://test/aot/hub/control", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ protocol: "phase4-1", session_id: scaleSession, kind: "update_stable" }),
}));
updateResponse = await response.json();
if (!response.ok || updateResponse.update.channel !== "stable") {
  throw new Error("Stable update dispatch after healthy canary failed");
}
updateRecord = await ctx.storage.get(`aot_session:${scaleSession}`);
for (const id of healthyCanaryIds) {
  if (updateRecord.last_update.devices[id]?.history.join(",") !== "HEALTHY") {
    throw new Error("Stable repeated a healthy canary device");
  }
  if ((updateRecord.last_update.active_group || []).some((item) => item.device_id === id)) {
    throw new Error("healthy canary device entered Stable active group");
  }
}
let updatedCount = 0;
const stableActionId = updateRecord.last_update.action_id;
while (updateRecord.last_update.active_group) {
  const active = [...updateRecord.last_update.active_group];
  if (active.length > 5) throw new Error("active Stable group exceeded five");
  for (const member of active) {
    for (const status of ["DOWNLOADING", "VERIFIED", "INSTALLING", "RESTARTING", "HEALTHY"]) {
      response = await fleet.dispatchAotAck(new Request("https://test/aot/ack", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          protocol: "phase4-1", session_id: scaleSession,
          reference_device_id: scaleIds[0], follower_device_id: member.device_id,
          action_id: stableActionId, batch_action: "UPDATE_WORKER", status,
          worker_version: "aot-worker-2026.08.11.7",
        }),
      }));
      if (!response.ok) throw new Error(`Stable ACK rejected: ${member.device_id}/${status}`);
    }
    updatedCount += 1;
  }
  updateRecord = await ctx.storage.get(`aot_session:${scaleSession}`);
}
if (updatedCount !== 37) {
  throw new Error(`Stable updated ${updatedCount}, expected 37 online non-canary devices`);
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
      worker_version: "aot-worker-2026.08.11.7",
    }),
  }));
  if (!response.ok) throw new Error("failed-group ACK rejected");
}
updateRecord = await ctx.storage.get(`aot_session:${scaleSession}`);
if (updateRecord.last_update.active_group || updateRecord.last_update.groups.length) {
  throw new Error("rollout continued after an unhealthy group");
}
if (socketSends.filter((item) => item.tag.startsWith(`aot:follower:${scaleSession}:`)).length !== 5) {
  throw new Error("a second rollout group was sent after failure");
}

console.log("AOT_FLEET_STATE_SELFTEST=OK");
