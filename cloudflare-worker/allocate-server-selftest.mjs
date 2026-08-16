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

// Test 8: Block < requested tabs
try {
  parse(mockText, ["m1"], 9);
  throw new Error("should throw");
} catch(e) {
  if(!e.message.includes("cần ít nhất 9 link")) throw e;
}

console.log("AOT_ALLOCATE_SERVER_SELFTEST=OK");
