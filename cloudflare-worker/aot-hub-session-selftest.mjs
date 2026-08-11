import fs from "node:fs/promises";
import vm from "node:vm";

const workerUrl = new URL("./worker.js", import.meta.url);
const moduleCache = new Map();
const moduleContext = vm.createContext({
  URL, Request, Response, Headers, JSON, Map, Set, Object, Array,
  String, Number, Boolean, Math, Date, console,
});

const cloudflareModule = new vm.SyntheticModule(
  ["DurableObject"],
  function () {
    this.setExport("DurableObject", class DurableObject {
      constructor(ctx, env) {
        this.ctx = ctx;
        this.env = env;
      }
    });
  },
  { context: moduleContext }
);

async function loadModule(url) {
  if (moduleCache.has(url.href)) {
    return moduleCache.get(url.href);
  }
  const source = await fs.readFile(url, "utf8");
  const loaded = new vm.SourceTextModule(source, {
    context: moduleContext,
    identifier: url.href,
  });
  moduleCache.set(url.href, loaded);
  await loaded.link(async (specifier) => {
    if (specifier === "cloudflare:workers") {
      return cloudflareModule;
    }
    return loadModule(new URL(specifier, url));
  });
  return loaded;
}

const workerModule = await loadModule(workerUrl);
await workerModule.evaluate();
const response = await workerModule.namespace.default.fetch(
  new Request("https://test/aot/hub"),
  {}
);
const html = await response.text();
const scriptMatch = html.match(
  /<script>\s*([\s\S]*?)<\/script>\s*<\/body>/
);
const defaultMatch = html.match(
  /<input id="session" value="([^"]*)"/
);
if (!scriptMatch || !defaultMatch) {
  throw new Error("dashboard HTML structure unavailable");
}

class LocalStorage {
  constructor(values) {
    this.values = values || new Map();
  }
  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null;
  }
  setItem(key, value) {
    this.values.set(key, String(value));
  }
}

function loadDashboard(storage) {
  const elementIds = [
    "session", "error", "reference", "followers", "lastControl",
    "online", "synced", "out", "offline", "refresh", "apply",
    "pause", "resume", "back", "up", "down", "openSwift",
    "batchResults",
  ];
  const elements = new Map(
    elementIds.map((id) => [id, {
      id,
      value: id === "session" ? defaultMatch[1] : "",
      textContent: "",
      innerHTML: "",
      disabled: false,
      onclick: null,
    }])
  );
  const document = {
    getElementById(id) {
      return elements.get(id) || null;
    },
  };
  const window = {
    Telegram: null,
    localStorage: storage,
    setInterval() { return 1; },
    clearInterval() {},
    setTimeout() {},
  };
  vm.runInNewContext(scriptMatch[1], {
    window,
    document,
    console,
    encodeURIComponent,
  });
  return elements;
}

const storage = new LocalStorage();
let elements = loadDashboard(storage);
if (elements.get("session").value !== defaultMatch[1]) {
  throw new Error("missing storage did not preserve fallback session");
}

elements.get("session").value = "m74-m117-p3";
elements.get("apply").onclick();
if (storage.getItem("aot-hub-session-id") !== "m74-m117-p3") {
  throw new Error("Apply did not persist selected session");
}

elements = loadDashboard(storage);
if (elements.get("session").value !== "m74-m117-p3") {
  throw new Error("reload did not restore persisted session");
}

const invalidStorage = new LocalStorage(
  new Map([["aot-hub-session-id", "invalid session!"]])
);
elements = loadDashboard(invalidStorage);
if (elements.get("session").value !== defaultMatch[1]) {
  throw new Error("invalid stored session overrode fallback");
}

if (
  !html.includes("Batch → Mở Swift Backup") ||
  !scriptMatch[1].includes('kind: "open_swift_backup"') ||
  !scriptMatch[1].includes("renderBatch(data.last_batch)")
) {
  throw new Error("fixed Swift Backup batch UI is missing");
}

console.log("AOT_HUB_SESSION_SELFTEST=OK");
