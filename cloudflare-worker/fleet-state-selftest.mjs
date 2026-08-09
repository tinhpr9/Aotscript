import fs from "node:fs/promises";
import vm from "node:vm";

const sourceUrl = new URL("./fleet-state.js", import.meta.url);
const source = await fs.readFile(sourceUrl, "utf8");
const context = vm.createContext({
  URL, Request, Response, JSON, Map, Set, Object, Array,
  String, Number, Boolean, Math, Date, console,
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
}

const onlineTags = new Set();
const ctx = {
  storage: new Storage(),
  getWebSockets(tag) { return onlineTags.has(tag) ? [{}] : []; },
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

console.log("AOT_FLEET_STATE_SELFTEST=OK");
