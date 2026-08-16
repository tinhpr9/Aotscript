import fs from "node:fs/promises";
import vm from "node:vm";

const source = await fs.readFile(new URL("./worker.js", import.meta.url), "utf8");
const context = vm.createContext({ console });
const funcMatch = source.match(/function parseTongHopLink[\s\S]*?\n\}/);
if (!funcMatch) throw new Error("Could not find parseTongHopLink");
const parseTongHopLink = vm.runInContext(`(() => { ${funcMatch[0]}; return parseTongHopLink; })()`, context);

const parse = (text, ids, tabs) => parseTongHopLink(text, ids, tabs);

const mockText = `
com.tinh.vv.hi,https://www.roblox.com/games/97598239454123?privateServerLinkCode=11111111111111111111111111111111
com.tinh.vv.hj,https://www.roblox.com/games/97598239454123?privateServerLinkCode=22222222222222222222222222222222
com.tinh.vv.hk,https://www.roblox.com/games/97598239454123?privateServerLinkCode=33333333333333333333333333333333
com.tinh.vv.hl,https://www.roblox.com/games/97598239454123?privateServerLinkCode=44444444444444444444444444444444
com.tinh.vv.hm,https://www.roblox.com/games/97598239454123?privateServerLinkCode=55555555555555555555555555555555
com.tinh.vv.hn,https://www.roblox.com/games/97598239454123?privateServerLinkCode=66666666666666666666666666666666
com.tinh.vv.ho,https://www.roblox.com/games/97598239454123?privateServerLinkCode=77777777777777777777777777777777
com.tinh.vv.hp,https://www.roblox.com/games/97598239454123?privateServerLinkCode=88888888888888888888888888888888
===
com.tinh.vv.hi,https://www.roblox.com/games/97598239454123?privateServerLinkCode=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
com.tinh.vv.hj,https://www.roblox.com/games/97598239454123?privateServerLinkCode=BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB
com.tinh.vv.hk,https://www.roblox.com/games/97598239454123?privateServerLinkCode=CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC
com.tinh.vv.hl,https://www.roblox.com/games/97598239454123?privateServerLinkCode=DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD
com.tinh.vv.hm,https://www.roblox.com/games/97598239454123?privateServerLinkCode=EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE
com.tinh.vv.hn,https://www.roblox.com/games/97598239454123?privateServerLinkCode=FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF
com.tinh.vv.ho,https://www.roblox.com/games/97598239454123?privateServerLinkCode=11111111111111111111111111111112
com.tinh.vv.hp,https://www.roblox.com/games/97598239454123?privateServerLinkCode=11111111111111111111111111111113
`;

// Test 1: 1 device, 1 tab
let res = parse(mockText, ["m1"], 1);
if (res["m1"].length !== 1) throw new Error("fail 1");

// Test 2: 1 device, 8 tabs
res = parse(mockText, ["m1"], 8);
if (res["m1"].length !== 8) throw new Error("fail 2");

// Test 3: 2 devices, 5 tabs
res = parse(mockText, ["m1", "m2"], 5);
if (res["m1"].length !== 5 || res["m2"].length !== 5) throw new Error("fail 3");
if (!res["m2"][0].url.includes("AAAA")) throw new Error("fail 3 url");

// Test 4: Not enough URLs
try {
  parse(mockText, ["m1", "m2", "m3"], 5);
  throw new Error("should throw");
} catch(e) {
  if(!e.message.includes("Không đủ block URL")) throw e;
}

// Test 5: Malformed URL
try {
  parse("com.tinh.vv.hi,https://www.roblox.com/games/97598239454123?privateServerLinkCode=ZZZZ", ["m1"], 1);
  throw new Error("should throw");
} catch(e) {
  if(!e.message.includes("URL không hợp lệ")) throw e;
}

// Test 6: Wrong package order
try {
  parse("com.tinh.vv.hj,https://www.roblox.com/games/97598239454123?privateServerLinkCode=11111111111111111111111111111111", ["m1"], 1);
  throw new Error("should throw");
} catch(e) {
  if(!e.message.includes("Sai package hoặc sai thứ tự")) throw e;
}

// Test 7: Duplicate URL
try {
  parse(`com.tinh.vv.hi,https://www.roblox.com/games/97598239454123?privateServerLinkCode=11111111111111111111111111111111
com.tinh.vv.hj,https://www.roblox.com/games/97598239454123?privateServerLinkCode=11111111111111111111111111111111`, ["m1"], 2);
  throw new Error("should throw");
} catch(e) {
  if(!e.message.includes("URL bị lặp")) throw e;
}

// Test 8: Block < requested tabs (no valid block with >= 9 links exists)
try {
  parse(mockText, ["m1"], 9);
  throw new Error("should throw");
} catch(e) {
  if(!e.message.includes("block đủ link")) throw e;
}

// Test 9: 8,8,10,10 block file — tabs=10 must skip first 2 blocks (only 8 links) and pick 3rd+4th
const generate10Lines = (offset) => {
  const pkgs = ["com.tinh.vv.hi","com.tinh.vv.hj","com.tinh.vv.hk","com.tinh.vv.hl","com.tinh.vv.hm","com.tinh.vv.hn","com.tinh.vv.ho","com.tinh.vv.hp","com.tinh.vv.hq","com.tinh.vv.hr"];
  return pkgs.map((p,i) => `${p},https://www.roblox.com/games/97598239454123?privateServerLinkCode=${String(offset * 10 + i + 1).padStart(32,"0")}`).join("\n");
};
const generate8Lines = (offset) => {
  const pkgs = ["com.tinh.vv.hi","com.tinh.vv.hj","com.tinh.vv.hk","com.tinh.vv.hl","com.tinh.vv.hm","com.tinh.vv.hn","com.tinh.vv.ho","com.tinh.vv.hp"];
  return pkgs.map((p,i) => `${p},https://www.roblox.com/games/97598239454123?privateServerLinkCode=${String(offset * 10 + i + 101).padStart(32,"0")}`).join("\n");
};
const mixedText = [generate8Lines(0), "===", generate8Lines(1), "===", generate10Lines(2), "===", generate10Lines(3)].join("\n");
const result9 = parse(mixedText, ["m1", "m2"], 10);
if (result9["m1"].length !== 10) throw new Error(`Test 9a: m1 got ${result9["m1"].length} links, expected 10`);
if (result9["m2"].length !== 10) throw new Error(`Test 9b: m2 got ${result9["m2"].length} links, expected 10`);
// Verify m1 comes from block 3 (offset=2) and m2 from block 4 (offset=3) — not blocks 1 or 2
if (result9["m1"][0].pkg !== "com.tinh.vv.hi") throw new Error("Test 9c: m1 wrong pkg");
if (result9["m2"][9].pkg !== "com.tinh.vv.hr") throw new Error("Test 9d: m2 wrong last pkg");
// Verify all URLs are unique across both devices
const allUrls = [...result9["m1"], ...result9["m2"]].map(x => x.url);
if (new Set(allUrls).size !== allUrls.length) throw new Error("Test 9e: duplicate URLs across batch");

// Test 9f: tabs=8 should pick first 2 blocks (with 8 links each), not the later ones
const result9f = parse(mixedText, ["m1", "m2"], 8);
if (result9f["m1"].length !== 8) throw new Error(`Test 9f: m1 got ${result9f["m1"].length} links, expected 8`);
if (result9f["m2"].length !== 8) throw new Error(`Test 9g: m2 got ${result9f["m2"].length} links, expected 8`);

console.log("AOT_ALLOCATE_SERVER_SELFTEST=OK");
