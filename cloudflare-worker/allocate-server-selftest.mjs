import assert from "assert";

// Mock environment and globals for normalizeAotHubControl testing
global.AOT_MAX_TARGETS = 128;
global.fetch = async (url) => {
  if (url.includes("tong_hop_link.txt")) {
    return {
      ok: true,
      text: async () => `com.tinh.vv.hi,https://www.roblox.com/games/97598239454123?privateServerLinkCode=11111111111111111111111111111111
com.tinh.vv.hj,https://www.roblox.com/games/97598239454123?privateServerLinkCode=22222222222222222222222222222222
com.tinh.vv.hk,https://www.roblox.com/games/97598239454123?privateServerLinkCode=33333333333333333333333333333333
com.tinh.vv.hl,https://www.roblox.com/games/97598239454123?privateServerLinkCode=44444444444444444444444444444444
com.tinh.vv.hm,https://www.roblox.com/games/97598239454123?privateServerLinkCode=55555555555555555555555555555555
com.tinh.vv.hn,https://www.roblox.com/games/97598239454123?privateServerLinkCode=66666666666666666666666666666666
com.tinh.vv.ho,https://www.roblox.com/games/97598239454123?privateServerLinkCode=77777777777777777777777777777777
===
com.tinh.vv.hi,https://www.roblox.com/games/97598239454123?privateServerLinkCode=88888888888888888888888888888888
com.tinh.vv.hj,https://www.roblox.com/games/97598239454123?privateServerLinkCode=99999999999999999999999999999999
com.tinh.vv.hk,https://www.roblox.com/games/97598239454123?privateServerLinkCode=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
com.tinh.vv.hl,https://www.roblox.com/games/97598239454123?privateServerLinkCode=BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB
com.tinh.vv.hm,https://www.roblox.com/games/97598239454123?privateServerLinkCode=CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC
com.tinh.vv.hn,https://www.roblox.com/games/97598239454123?privateServerLinkCode=DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD
com.tinh.vv.ho,https://www.roblox.com/games/97598239454123?privateServerLinkCode=EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE
com.tinh.vv.hp,https://www.roblox.com/games/97598239454123?privateServerLinkCode=FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF
com.tinh.vv.hq,https://www.roblox.com/games/97598239454123?privateServerLinkCode=00000000000000000000000000000000
com.tinh.vv.hr,https://www.roblox.com/games/97598239454123?privateServerLinkCode=12345678901234567890123456789012
# Malformed URL below
invalidurl
https://www.roblox.com/games/97598239454123?privateServerLinkCode=malformed-no-hex
# Duplicate URL
com.tinh.vv.hi,https://www.roblox.com/games/97598239454123?privateServerLinkCode=11111111111111111111111111111111`
    };
  }
  return { ok: false };
};

// Extracted handleAotHubControl logic for preview_allocate_server
async function simulatePreviewAllocate(target_device_ids, tabs) {
  const resp = await fetch("https://raw.githubusercontent.com/tinhpr9/Aotscript/main/tong_hop_link.txt");
  if (!resp.ok) throw new Error("Fetch tong_hop_link.txt failed");
  const text = await resp.text();
  const urls = [];
  const seenUrls = new Set();
  const lines = text.split('\n');
  for (let line of lines) {
    line = line.trim();
    if (!line || line === '===') continue;
    let url = line;
    if (line.includes(',')) {
      url = line.split(',').slice(1).join(',').trim();
    }
    if (!url.match(/^https:\/\/(www\.)?roblox\.com\/games\/\d+\?privateServerLinkCode=[0-9a-fA-F]+$/i)) continue;
    if (seenUrls.has(url)) continue;
    seenUrls.add(url);
    urls.push(url);
  }
  
  if (target_device_ids.length * tabs > urls.length) {
    throw new Error(`Không đủ URL! Cần ${target_device_ids.length * tabs}, nhưng file chỉ có ${urls.length} URL hợp lệ và duy nhất.`);
  }

  const allocationMap = {};
  const pkgs = ["com.tinh.vv.hi", "com.tinh.vv.hj", "com.tinh.vv.hk", "com.tinh.vv.hl", "com.tinh.vv.hm", "com.tinh.vv.hn", "com.tinh.vv.ho", "com.tinh.vv.hp", "com.tinh.vv.hq", "com.tinh.vv.hr"];
  let urlIdx = 0;
  for (const id of target_device_ids) {
    allocationMap[id] = [];
    for (let i = 0; i < tabs; i++) {
      allocationMap[id].push({ pkg: pkgs[i], url: urls[urlIdx++] });
    }
  }
  return { ok: true, allocationMap };
}

async function runTests() {
  console.log("Running allocate_server tests...");

  // Test 1: 1 device, 1 tab
  let res = await simulatePreviewAllocate(["m1"], 1);
  assert(res.ok);
  assert.strictEqual(res.allocationMap["m1"].length, 1);
  assert.strictEqual(res.allocationMap["m1"][0].pkg, "com.tinh.vv.hi");
  assert(res.allocationMap["m1"][0].url.includes("11111111111111111111111111111111"));

  // Test 2: 1 device, 5 tabs
  res = await simulatePreviewAllocate(["m1"], 5);
  assert(res.ok);
  assert.strictEqual(res.allocationMap["m1"].length, 5);
  assert.strictEqual(res.allocationMap["m1"][4].pkg, "com.tinh.vv.hm");
  assert(res.allocationMap["m1"][4].url.includes("55555555555555555555555555555555"));

  // Test 3: 2 devices, 5 tabs
  res = await simulatePreviewAllocate(["m1", "m2"], 5);
  assert(res.ok);
  assert.strictEqual(res.allocationMap["m1"].length, 5);
  assert.strictEqual(res.allocationMap["m2"].length, 5);
  assert.strictEqual(res.allocationMap["m2"][0].pkg, "com.tinh.vv.hi");
  // device 2 gets the 6th url
  assert(res.allocationMap["m2"][0].url.includes("66666666666666666666666666666666"));

  // Test 4: Not enough URLs
  try {
    await simulatePreviewAllocate(["m1", "m2"], 10);
    assert.fail("Should have thrown error");
  } catch (e) {
    assert(e.message.includes("Không đủ URL"));
  }

  console.log("AOT_ALLOCATE_SERVER_SELFTEST=OK");
}

runTests().catch(console.error);
