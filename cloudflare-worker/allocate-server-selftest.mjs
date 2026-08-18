import fs from "node:fs/promises";
import vm from "node:vm";

const source = await fs.readFile(new URL("./worker.js", import.meta.url), "utf8");
const context = vm.createContext({ console });
const funcMatch = source.match(/function parseTongHopLink[\s\S]*?\n\}/);
if (!funcMatch) throw new Error("Could not find parseTongHopLink");
const parseTongHopLink = vm.runInContext(`(() => { ${funcMatch[0]}; return parseTongHopLink; })()`, context);

const parse = (text, ids, tabs) => parseTongHopLink(text, ids, tabs);

// Generate helper
const makeUrl = (id) => `https://www.roblox.com/games/97598239454123?privateServerLinkCode=${String(id).padStart(32, "0")}`;

// 1. 1 device, 1 tab
const text1 = makeUrl(1);
const res1 = parse(text1, ["m117"], 1);
if (res1["m117"].length !== 1 || res1["m117"][0].pkg !== "com.tinh.vv.hi" || res1["m117"][0].url !== makeUrl(1)) {
  throw new Error("Test 1 failed: 1 device 1 tab");
}

// 2. 1 device, 10 tabs
const text10 = Array.from({ length: 10 }, (_, i) => makeUrl(i + 1)).join("\n");
const res10 = parse(text10, ["m117"], 10);
if (res10["m117"].length !== 10) throw new Error("Test 2a failed: length != 10");
if (res10["m117"][0].pkg !== "com.tinh.vv.hi" || res10["m117"][9].pkg !== "com.tinh.vv.hr") throw new Error("Test 2b failed: wrong pkg mapping");

// 3. 2 devices, 10 tabs (needs 20 unique URLs)
const text20 = Array.from({ length: 25 }, (_, i) => makeUrl(i + 1)).join("\n");
const res20 = parse(text20, ["m117", "m116"], 10);
if (res20["m117"].length !== 10 || res20["m116"].length !== 10) throw new Error("Test 3a failed: wrong length");
if (res20["m117"][0].url !== makeUrl(1) || res20["m117"][9].url !== makeUrl(10)) throw new Error("Test 3b failed: m117 wrong range");
if (res20["m116"][0].url !== makeUrl(11) || res20["m116"][9].url !== makeUrl(20)) throw new Error("Test 3c failed: m116 wrong range");
const urls20 = [...res20["m117"], ...res20["m116"]].map(x => x.url);
if (new Set(urls20).size !== 20) throw new Error("Test 3d failed: overlapping URLs across devices");

// 4. 2 devices, 5 tabs (needs 10 unique URLs)
const res2x5 = parse(text20, ["m117", "m116"], 5);
if (res2x5["m117"].length !== 5 || res2x5["m116"].length !== 5) throw new Error("Test 4a failed");
if (res2x5["m117"][4].pkg !== "com.tinh.vv.hm" || res2x5["m116"][4].pkg !== "com.tinh.vv.hm") throw new Error("Test 4b failed: pkg mapping");
if (res2x5["m116"][0].url !== makeUrl(6)) throw new Error("Test 4c failed: m116 start url");

// 5. Insufficient URLs -> Fail Fast
try {
  // Only 15 URLs provided, but 2 devices * 10 tabs = 20 needed
  const text15 = Array.from({ length: 15 }, (_, i) => makeUrl(i + 1)).join("\n");
  parse(text15, ["m117", "m116"], 10);
  throw new Error("Test 5 failed: should have thrown on insufficient URLs");
} catch (e) {
  if (!e.message.includes("Không đủ URL hợp lệ! Cần 20 URL")) {
    throw new Error(`Test 5 failed with unexpected message: ${e.message}`);
  }
}

// 6. Duplicate URLs in file -> Deduplicated, then fails if unique count < required
try {
  const textDup = [makeUrl(1), makeUrl(1), makeUrl(2), makeUrl(2)].join("\n");
  // 4 lines, but only 2 unique URLs; needs 3 for 1 device * 3 tabs
  parse(textDup, ["m1"], 3);
  throw new Error("Test 6 failed: should have thrown due to duplicate deduction");
} catch (e) {
  if (!e.message.includes("Không đủ URL hợp lệ! Cần 3 URL (1 thiết bị × 3 tab), nhưng chỉ có 2 URL khả dụng.")) {
    throw new Error(`Test 6 failed with unexpected message: ${e.message}`);
  }
}

// 7. Invalid lines, empty lines, separator lines === skipped cleanly
const textWithNoise = `
===
not-a-url
${makeUrl(1)}

===
https://invalid.domain.com/games/123?privateServerLinkCode=1234
${makeUrl(2)}
   
===
${makeUrl(3)}
`;
const resNoise = parse(textWithNoise, ["m1"], 3);
if (resNoise["m1"].length !== 3) throw new Error("Test 7 failed: noise not skipped");
if (resNoise["m1"][0].url !== makeUrl(1) || resNoise["m1"][1].url !== makeUrl(2) || resNoise["m1"][2].url !== makeUrl(3)) {
  throw new Error("Test 7b failed: wrong URLs extracted from noise");
}

// 8. Backward compatibility with legacy comma prefixes (e.g. com.tinh.vv.hi,https://...)
const textLegacy = `
com.tinh.vv.hi,${makeUrl(1)}
com.tinh.vv.hj,${makeUrl(2)}
`;
const resLegacy = parse(textLegacy, ["m1"], 2);
if (resLegacy["m1"].length !== 2 || resLegacy["m1"][0].url !== makeUrl(1) || resLegacy["m1"][1].url !== makeUrl(2)) {
  throw new Error("Test 8 failed: legacy comma prefixes not parsed");
}

// 9. Invalid parameters (tab < 1, tab > 10, empty target devices)
try {
  parse(text20, [], 5);
  throw new Error("Test 9a failed");
} catch (e) {
  if (!e.message.includes("Danh sách thiết bị không hợp lệ")) throw e;
}
try {
  parse(text20, ["m1"], 0);
  throw new Error("Test 9b failed");
} catch (e) {
  if (!e.message.includes("Số tab phải từ 1 đến 10")) throw e;
}
try {
  parse(text20, ["m1"], 11);
  throw new Error("Test 9c failed");
} catch (e) {
  if (!e.message.includes("Số tab phải từ 1 đến 10")) throw e;
}

// 10. Duplicate target device IDs -> throws error before allocation
try {
  parse(text20, ["m117", "m117"], 1);
  throw new Error("Test 10 failed: duplicate device IDs should throw");
} catch (e) {
  if (!e.message.includes("Device ID bị lặp: m117")) throw e;
}

// 11. Case-insensitive duplicate target device IDs -> throws error before allocation
try {
  parse(text20, ["M117", "m117"], 1);
  throw new Error("Test 11 failed: case-insensitive duplicate device IDs should throw");
} catch (e) {
  if (!e.message.includes("Device ID bị lặp: m117")) throw e;
}

// 13. Logical duplicate URL (different case in query key)
try {
  parse(`com.tinh.vv.hi,https://www.roblox.com/games/97598239454123?privateServerLinkCode=11111111111111111111111111111111
com.tinh.vv.hj,https://www.roblox.com/games/97598239454123?pRiVaTeSeRvErLiNkCoDe=11111111111111111111111111111111`, ["m1"], 2);
  throw new Error("Test 13 failed: case-insensitive duplicate URLs should throw");
} catch(e) {
  if(!e.message.includes("URL khả dụng")) throw e; // Due to new parsing, dedupe drops it, so validUrls.length is 1, which throws "Không đủ URL hợp lệ!"
}

// 14. Mixed-case query key passes valid validation
const mixedCaseRes = parse(`com.tinh.vv.hi,https://www.roblox.com/games/97598239454123?pRiVaTeSeRvErLiNkCoDe=11111111111111111111111111111111`, ["m1"], 1);
if (mixedCaseRes["m1"].length !== 1) throw new Error("Test 14: Mixed case query key failed to parse");
// 12. Non-string or empty device ID in target list -> throws invalid device list
try {
  parse(text20, [null], 1);
  throw new Error("Test 12a failed: null device ID should throw");
} catch (e) {
  if (!e.message.includes("Danh sách thiết bị không hợp lệ")) throw e;
}
try {
  parse(text20, ["   "], 1);
  throw new Error("Test 12b failed: empty whitespace device ID should throw");
} catch (e) {
  if (!e.message.includes("Danh sách thiết bị không hợp lệ")) throw e;
}

console.log("AOT_ALLOCATE_SERVER_SELFTEST=OK");
