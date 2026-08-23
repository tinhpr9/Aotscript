import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const files = fs
  .readdirSync(__dirname)
  .filter((f) => f.endsWith("-selftest.mjs"))
  .sort();

if (files.length === 0) {
  console.error("No selftest files found in " + __dirname);
  process.exit(1);
}

console.log(`Discovered ${files.length} selftest files:`);
for (const f of files) {
  console.log(` - ${f}`);
}

for (const file of files) {
  const filePath = path.join(__dirname, file);
  console.log(`\n=== RUNNING ${file} ===`);
  const result = spawnSync(
    process.execPath,
    ["--experimental-vm-modules", filePath],
    {
      stdio: "inherit",
      cwd: __dirname,
    }
  );

  if (result.status !== 0) {
    console.error(`\n❌ Test failed: ${file} (exit code ${result.status})`);
    process.exit(result.status ?? 1);
  }
}

console.log(`\n✅ All ${files.length} Cloudflare Worker selftests PASSED.`);
