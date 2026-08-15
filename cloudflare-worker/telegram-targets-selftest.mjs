import fs from "node:fs/promises";
import vm from "node:vm";

const source = await fs.readFile(new URL("./worker.js", import.meta.url), "utf8");

// We need to extract resolveAndValidateTelegramTargets and some mock functions
const context = vm.createContext({
  console,
  ONLINE_WINDOW_MS: 90000,
  listFleetDeviceRecords: async () => {
    const now = Date.now();
    return [
      { device_id: "m1", device_group: "NOVA", last_seen: now - 1000 },
      { device_id: "m2", device_group: "NOVA", last_seen: now - 50000 },
      { device_id: "m3", device_group: "MARMOT", last_seen: now - 100000 }, // stale (offline)
      { device_id: "m4", device_group: "MARMOT", last_seen: now - 2000 }
    ];
  },
  normalizeDeviceGroup: (value) => {
    const group = String(value || "").trim().toUpperCase();
    return ["MARMOT", "NOVA"].includes(group) ? group : null;
  },
  normalizeDeviceId: (id) => String(id || "").toLowerCase(),
  normalizeDeviceIdList: (str) => {
    const rawValues = Array.isArray(str) ? str : String(str || "").split(",");
    const result = [];
    const seen = new Set();
    for (const rawValue of rawValues) {
      if (!rawValue) continue;
      const deviceId = String(rawValue).toLowerCase();
      if (seen.has(deviceId)) throw new Error(`Device ID bị lặp: ${deviceId}`);
      seen.add(deviceId);
      result.push(deviceId);
    }
    return result.sort();
  },
  compareDeviceIds: (a, b) => a.localeCompare(b)
});

// extract the function
const funcMatch = source.match(/async function resolveAndValidateTelegramTargets.*?return ids;\n}/s);
if (!funcMatch) throw new Error("Could not find resolveAndValidateTelegramTargets");

vm.runInContext(funcMatch[0], context);

async function runTests() {
  const resolve = (targetStr) => vm.runInContext(`resolveAndValidateTelegramTargets("${targetStr}")`, context);

  // 1. target device
  const t1 = await resolve("m1");
  if (t1.join(",") !== "m1") throw new Error("test 1 failed");

  // 2. multi-device
  const t2 = await resolve("m1,m2");
  if (t2.join(",") !== "m1,m2") throw new Error("test 2 failed");

  // 3. NOVA (group) -> m1,m2
  const t3 = await resolve("NOVA");
  if (t3.join(",") !== "m1,m2") throw new Error("test 3 failed");

  // 4. empty target
  await resolve("   ").then(() => { throw new Error("test 4 fail - should throw on empty") }).catch(e => {
    if (!e.message.includes("Target không được để trống")) throw e;
  });

  // 5. MARMOT group with one offline member -> should throw
  await resolve("MARMOT").then(() => { throw new Error("test 5 fail - should throw on offline group member") }).catch(e => {
    if (!e.message.includes("đang OFFLINE")) throw e;
  });

  // 6. multi-device with valid + offline -> should throw
  await resolve("m1,m3").then(() => { throw new Error("test 6 fail - should throw on offline device") }).catch(e => {
    if (!e.message.includes("đang OFFLINE")) throw e;
  });

  // 7. duplicate device -> should throw
  await resolve("m1,m1").then(() => { throw new Error("test 7 fail - should throw on duplicate") }).catch(e => {
    if (!e.message.includes("bị lặp")) throw e;
  });

  // 8. unknown device
  await resolve("m99").then(() => { throw new Error("test 8 fail - should throw on unknown") }).catch(e => {
    if (!e.message.includes("không tồn tại")) throw e;
  });

  console.log("AOT_TELEGRAM_TARGETS_SELFTEST=OK");
}

runTests().catch(e => { console.error(e); process.exit(1); });
