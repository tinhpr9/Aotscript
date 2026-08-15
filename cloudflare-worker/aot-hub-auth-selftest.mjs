import fs from "node:fs/promises";
import vm from "node:vm";

const rawSource = await fs.readFile(new URL("./worker.js", import.meta.url), "utf8");

let source = rawSource.replace(/import\s+\{[\s\S]*?\}\s+from\s+["'].*?["'];/g, "");
source = source.replace(/export\s+\{[\s\S]*?\};/g, "");
source = source.replace(/export\s+default\s+\{[\s\S]*?\};/g, "");

// Mock environment and request
const context = {
  Request: class Request {
    constructor(url, init) {
      this.url = url;
      this.method = init?.method || "GET";
      this.headers = new Map(Object.entries((init && init.headers) || {}));
      this.headers.get = (k) => Map.prototype.get.call(this.headers, k) || null;
    }
  },
  Response: class Response {
    constructor(body, init) {
      this.status = init?.status || 200;
    }
  },
  TextEncoder: class TextEncoder { encode() { return new Uint8Array(); } },
  TextDecoder: class TextDecoder {},
  console: console,
  addEventListener: () => {},
  crypto: { subtle: { importKey: () => {}, sign: () => {} } },
  Date: Date,
  Math: Math,
  Number: Number,
  String: String,
  JSON: JSON,
  Promise: Promise,
  Object: Object,
  URLSearchParams: class URLSearchParams { 
    constructor() { this.entries = () => []; }
    get() { return null; } 
    delete() {}
  },
  Map: Map,
  Set: Set,
  Array: Array,
};

vm.createContext(context);

try {
  vm.runInContext(source, context);
  
  // Expose internal functions we need to test
  vm.runInContext(`
    globalThis.testRequireAotHubAdmin = requireAotHubAdmin;
  `, context);
} catch (e) {
  console.error("Failed to compile worker.js in VM:", e);
  process.exit(1);
}

async function runTests() {
  const env = { AOT_HUB_API_SECRET: "test-secret" };
  
  // Test 1: Valid server-to-server auth
  const req1 = new context.Request("https://hub/aot/hub/api/state", {
    headers: { "Authorization": "Bearer test-secret" }
  });
  const res1 = await context.testRequireAotHubAdmin(req1, env);
  if (!res1.ok || res1.user.id !== "server") {
    throw new Error("Valid Bearer auth failed");
  }

  // Test 2: Invalid server-to-server auth
  const req2 = new context.Request("https://hub/aot/hub/api/state", {
    headers: { "Authorization": "Bearer wrong-secret" }
  });
  const res2 = await context.testRequireAotHubAdmin(req2, env);
  if (res2.ok || res2.response.status !== 401) {
    throw new Error("Invalid Bearer auth did not fail with 401");
  }
  
  // Test 3: Missing auth
  const req3 = new context.Request("https://hub/aot/hub/api/state", { headers: {} });
  const res3 = await context.testRequireAotHubAdmin(req3, env);
  if (res3.ok || res3.response.status !== 401) {
    throw new Error("Missing auth did not fail with 401");
  }

  console.log("AOT_HUB_AUTH_SELFTEST=OK");
}

runTests().catch(e => {
  console.error(e);
  process.exit(1);
});
