import os
import pathlib
import time
import shutil
import tempfile
import glob
import urllib.error
import http.client
from typing import Any

import relay
import controller

class MockController:
    def __init__(self):
        self.opened = []
        self.fail = False
    def open_roblox_servers(self, alloc):
        if self.fail:
            raise RuntimeError("Mock failure")
        self.opened.append(alloc)

mock_ctrl = MockController()
relay.controller.open_roblox_servers = mock_ctrl.open_roblox_servers

acks = []
def mock_send_batch_ack(cfg, **kwargs):
    acks.append(kwargs)

relay._send_batch_ack = mock_send_batch_ack
relay._save_state = lambda state: None

state: dict[str, Any] = {}
cfg: dict[str, str] = {}

temp_dir = tempfile.TemporaryDirectory()
temp_path = pathlib.Path(temp_dir.name)
links_path = str(temp_path / "server_links.txt")
bak_path = links_path + ".bak"
relay.SERVER_LINKS_PATH = temp_path / "server_links.txt"
relay.STATE_PATH = temp_path / "aot_group_state.json"

def cleanup():
    acks.clear()
    state.clear()
    mock_ctrl.opened.clear()
    mock_ctrl.fail = False
    for p in (links_path, bak_path):
        if os.path.exists(p): os.remove(p)
    for p in glob.glob(links_path + ".prep.*"): os.remove(p)

# 1. Invalid payload format
msg1 = {"type": "aot_batch_action", "protocol": "fleet-batch-v1", "action": "PREPARE_ALLOCATE_SERVER", "action_id": "a1", "expires_at": int(time.time()*1000) + 10000, "target_device_ids": ["m1"]}
relay._handle_batch_action(cfg, state, local_id="m1", message=msg1)
assert acks[-1]["status"] == "PREPARE_FAILED"
assert "invalid_allocation_format" in acks[-1]["reason"]

# 1b. Non-dict allocation entry rejected
cleanup()
msg1b = dict(msg1)
msg1b["action_id"] = "a1b"
msg1b["allocation"] = ["not-a-dict"]
relay._handle_batch_action(cfg, state, local_id="m1", message=msg1b)
assert acks[-1]["status"] == "PREPARE_FAILED"
assert "invalid_allocation_item_at_0" in acks[-1]["reason"]

# 2. Invalid order/package
cleanup()
msg2 = dict(msg1)
msg2["action_id"] = "a2"
msg2["allocation"] = [
    {"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123?privateServerLinkCode=abc"},
    {"pkg": "com.tinh.vv.hk", "url": "https://www.roblox.com/games/123?privateServerLinkCode=def"}
]
relay._handle_batch_action(cfg, state, local_id="m1", message=msg2)
assert acks[-1]["status"] == "PREPARE_FAILED"
assert "invalid_package_order_at_1" in acks[-1]["reason"]

# 3. Duplicate URL fail
cleanup()
msg3 = dict(msg1)
msg3["action_id"] = "a3"
msg3["allocation"] = [
    {"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123?privateServerLinkCode=abc"},
    {"pkg": "com.tinh.vv.hj", "url": "https://www.roblox.com/games/123?privateServerLinkCode=abc"}
]
relay._handle_batch_action(cfg, state, local_id="m1", message=msg3)
assert acks[-1]["status"] == "PREPARE_FAILED"
assert "duplicate_url_at_1" in acks[-1]["reason"]

# 4. PREPARE expired
cleanup()
msg_exp = dict(msg1)
msg_exp["action_id"] = "a4"
msg_exp["expires_at"] = int(time.time()*1000) - 10000
relay._handle_batch_action(cfg, state, local_id="m1", message=msg_exp)
assert acks[-1]["status"] == "TIMEOUT"

# 5. Regression test: lowercase privateserverlinkcode
cleanup()
msg_lower = dict(msg1)
msg_lower["action_id"] = "a_lower"
msg_lower["allocation"] = [
    {"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123?privateserverlinkcode=abc"}
]
relay._handle_batch_action(cfg, state, local_id="m1", message=msg_lower)
# After fix, this should PASS (PREPARE_READY -> PREPARE_ALLOCATE_SERVER)
assert acks[-1]["status"] == "PREPARE_READY", acks[-1]

# 6. Regression test: canonical privateServerLinkCode
cleanup()
msg_canon = dict(msg1)
msg_canon["action_id"] = "a_canon"
msg_canon["allocation"] = [
    {"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123?privateServerLinkCode=AbCdEf123"}
]
relay._handle_batch_action(cfg, state, local_id="m1", message=msg_canon)
assert acks[-1]["status"] == "PREPARE_READY", acks[-1]

# 7. Regression test: invalid host/path fails
cleanup()
msg_inv = dict(msg1)
msg_inv["action_id"] = "a_inv"
msg_inv["allocation"] = [
    {"pkg": "com.tinh.vv.hi", "url": "https://evil.roblox.com.proxy.com/games/123?privateServerLinkCode=abc"}
]
relay._handle_batch_action(cfg, state, local_id="m1", message=msg_inv)
assert acks[-1]["status"] == "PREPARE_FAILED"
assert "invalid_roblox_url_at_0" in acks[-1]["reason"]

# 8. PREPARE -> COMMIT -> OPENED
cleanup()
alloc = [
    {"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123?privateServerLinkCode=abc"},
    {"pkg": "com.tinh.vv.hj", "url": "https://www.roblox.com/games/123?privateServerLinkCode=def"}
]
msg5 = {
    "type": "aot_batch_action",
    "protocol": "fleet-batch-v1",
    "action": "PREPARE_ALLOCATE_SERVER",
    "action_id": "a5",
    "expires_at": int(time.time()*1000) + 10000,
    "allocation": alloc,
    "target_device_ids": ["m1"]
}
relay._handle_batch_action(cfg, state, local_id="m1", message=msg5)
assert acks[-1]["status"] == "PREPARE_READY"

msg_commit = dict(msg5)
msg_commit["action"] = "COMMIT_ALLOCATE_SERVER"
relay._handle_batch_action(cfg, state, local_id="m1", message=msg_commit)
assert acks[-2]["status"] == "ALLOCATED"
assert acks[-1]["status"] == "OPENED"
assert "a5" in state.get("processed_action_ids", [])
assert state.get("allocate_action_results", {}).get("a5", {}).get("status") == "OPENED"

# Replay -> OPENED (from allocate_action_results journal)
acks.clear()
relay._handle_batch_action(cfg, state, local_id="m1", message=msg5)
assert acks[-1]["status"] == "OPENED"

relay._handle_batch_action(cfg, state, local_id="m1", message=msg_commit)
assert acks[-1]["status"] == "OPENED"
assert not os.path.exists(links_path + ".prep.a5")

# 6. PREPARE -> ABORT cleanup đúng action
cleanup()
msg6 = dict(msg5)
msg6["action_id"] = "a6"
relay._handle_batch_action(cfg, state, local_id="m1", message=msg6)
assert os.path.exists(links_path + ".prep.a6")

msg_abort = dict(msg6)
msg_abort["action"] = "ABORT_ALLOCATE_SERVER"
relay._handle_batch_action(cfg, state, local_id="m1", message=msg_abort)
assert acks[-1]["status"] == "FAILED"
assert "aborted_by_hub" in acks[-1]["reason"]
assert not os.path.exists(links_path + ".prep.a6")

# 7. target_device_ids validation
cleanup()
msg7 = dict(msg1)
msg7["action_id"] = "a7"
msg7["target_device_ids"] = ["m2"]
relay._handle_batch_action(cfg, state, local_id="m1", message=msg7)
assert len(acks) == 0

# 8. URL validation with/without www
cleanup()
msg8 = dict(msg5)
msg8["action_id"] = "a8"
msg8["allocation"] = [
    {"pkg": "com.tinh.vv.hi", "url": "https://roblox.com/games/123?privateServerLinkCode=abcdef"},
    {"pkg": "com.tinh.vv.hj", "url": "https://www.roblox.com/games/123?privateServerLinkCode=deadbeef"}
]
msg8["target_device_ids"] = ["m1"]
relay._handle_batch_action(cfg, state, local_id="m1", message=msg8)
assert acks[-1]["status"] == "PREPARE_READY"

# 9. Irreversible ACK-loss resilience on COMMIT
cleanup()
msg9 = dict(msg5)
msg9["action_id"] = "a9"
relay._handle_batch_action(cfg, state, local_id="m1", message=msg9)
assert acks[-1]["status"] == "PREPARE_READY"

# Simulate network exception on sending OPENED ACK
def failing_ack(cfg, **kwargs):
    if kwargs.get("status") == "OPENED":
        raise RuntimeError("Simulated network drop on OPENED ACK")
    acks.append(kwargs)

relay._send_batch_ack = failing_ack
msg9_commit = dict(msg9)
msg9_commit["action"] = "COMMIT_ALLOCATE_SERVER"

# 1st COMMIT: open_roblox_servers succeeds, OPENED ACK throws, but no rollback occurs
relay._handle_batch_action(cfg, state, local_id="m1", message=msg9_commit)
assert len(mock_ctrl.opened) == 1
assert os.path.exists(links_path)
assert not os.path.exists(links_path + ".prep.a9")

# Restore normal ACK
relay._send_batch_ack = mock_send_batch_ack
acks.clear()

# 2nd COMMIT retry from Hub: replay OPENED from journal, do not call open_roblox_servers again
relay._handle_batch_action(cfg, state, local_id="m1", message=msg9_commit)
assert len(mock_ctrl.opened) == 1  # Still 1, NOT reopened!
assert acks[-1]["status"] == "OPENED"
assert acks[-1]["executed"] == True
assert os.path.exists(links_path)
cleanup()
temp_dir.cleanup()

# T10: ACK transport failure resilience - terminal_ack swallows AotRelayError, OSError, URLError, HTTPException
temp_dir2 = tempfile.TemporaryDirectory()
relay.SERVER_LINKS_PATH = pathlib.Path(temp_dir2.name) / "server_links.txt"
links_path2 = str(relay.SERVER_LINKS_PATH)
state2 = {}

for exc_to_raise in [
    relay.AotRelayError("Transport ACK delivery failed: 502"),
    OSError("Network unreachable / Connection reset"),
    urllib.error.URLError("Connection refused"),
    http.client.RemoteDisconnected("Remote disconnected"),
]:
    def broken_send_batch_ack(*args, **kwargs):
        raise exc_to_raise

    relay._send_batch_ack = broken_send_batch_ack

    action_id_10 = f"act-10-{type(exc_to_raise).__name__}"
    msg10_prep = {
        "type": "aot_batch_action",
        "protocol": "fleet-batch-v1",
        "target_device_ids": ["m1"],
        "action_id": action_id_10,
        "action": "PREPARE_ALLOCATE_SERVER",
        "allocation": [{"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123?privateServerLinkCode=abc10"}],
        "expires_at": int(time.time() * 1000) + 10000
    }
    # Should return True and NOT raise any exception
    res10 = relay._handle_batch_action(cfg, state2, local_id="m1", message=msg10_prep)
    assert res10 is True
    # Verify the prep file was still written
    prep_path10 = f"{links_path2}.prep.{action_id_10}"
    assert os.path.exists(prep_path10)

# T11: Stale prep files cleanup - new PREPARE cleans up older orphaned .prep.* files
stale_prep = f"{links_path2}.prep.stale-old-action"
with open(stale_prep, "w") as f:
    f.write("com.tinh.vv.hi,https://www.roblox.com/games/123?privateServerLinkCode=abcdef\n")
assert os.path.exists(stale_prep)

relay._send_batch_ack = mock_send_batch_ack
acks.clear()

msg11_prep = {
    "type": "aot_batch_action",
    "protocol": "fleet-batch-v1",
    "target_device_ids": ["m1"],
    "action_id": "act-11",
    "action": "PREPARE_ALLOCATE_SERVER",
    "allocation": [{"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123?privateServerLinkCode=abc11"}],
    "expires_at": int(time.time() * 1000) + 10000
}
res11 = relay._handle_batch_action(cfg, state2, local_id="m1", message=msg11_prep)
assert res11 is True
assert not os.path.exists(stale_prep), "Stale prep file was not cleaned up"
assert os.path.exists(f"{links_path2}.prep.act-11")

# T12: Minimum valid tabs (1 tab)
msg12_prep = {
    "type": "aot_batch_action",
    "protocol": "fleet-batch-v1",
    "target_device_ids": ["m1"],
    "action_id": "act-12",
    "action": "PREPARE_ALLOCATE_SERVER",
    "allocation": [{"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123?privateServerLinkCode=1001"}],
    "expires_at": int(time.time() * 1000) + 10000
}
acks.clear()
relay._handle_batch_action(cfg, state2, local_id="m1", message=msg12_prep)
assert acks[-1]["status"] == "PREPARE_READY"

# T13: Maximum valid tabs (10 tabs)
pkgs = ['hi', 'hj', 'hk', 'hl', 'hm', 'hn', 'ho', 'hp', 'hq', 'hr']
alloc10 = [{"pkg": f"com.tinh.vv.{p}", "url": f"https://www.roblox.com/games/123?privateServerLinkCode=f00{i}"} for i, p in enumerate(pkgs)]
msg13_prep = {
    "type": "aot_batch_action",
    "protocol": "fleet-batch-v1",
    "target_device_ids": ["m1"],
    "action_id": "act-13",
    "action": "PREPARE_ALLOCATE_SERVER",
    "allocation": alloc10,
    "expires_at": int(time.time() * 1000) + 10000
}
acks.clear()
relay._handle_batch_action(cfg, state2, local_id="m1", message=msg13_prep)
assert acks[-1]["status"] == "PREPARE_READY"

# T14: 11 tabs rejected
alloc11 = alloc10 + [{"pkg": "com.tinh.vv.hs", "url": "https://www.roblox.com/games/123?privateServerLinkCode=f00b"}]
msg14_prep = {
    "type": "aot_batch_action",
    "protocol": "fleet-batch-v1",
    "target_device_ids": ["m1"],
    "action_id": "act-14",
    "action": "PREPARE_ALLOCATE_SERVER",
    "allocation": alloc11,
    "expires_at": int(time.time() * 1000) + 10000
}
acks.clear()
relay._handle_batch_action(cfg, state2, local_id="m1", message=msg14_prep)
assert acks[-1]["status"] == "PREPARE_FAILED"
assert acks[-1]["reason"] == "invalid_allocation_format"

# T15: 0 tabs rejected
msg15_prep = {
    "type": "aot_batch_action",
    "protocol": "fleet-batch-v1",
    "target_device_ids": ["m1"],
    "action_id": "act-15",
    "action": "PREPARE_ALLOCATE_SERVER",
    "allocation": [],
    "expires_at": int(time.time() * 1000) + 10000
}
acks.clear()
relay._handle_batch_action(cfg, state2, local_id="m1", message=msg15_prep)
assert acks[-1]["status"] == "PREPARE_FAILED"
assert acks[-1]["reason"] == "invalid_allocation_format"

# T16: Missing package in order rejected
alloc_bad_pkg = [{"pkg": "com.tinh.vv.hj", "url": "https://www.roblox.com/games/123?privateServerLinkCode=1001"}]
msg16_prep = {
    "type": "aot_batch_action",
    "protocol": "fleet-batch-v1",
    "target_device_ids": ["m1"],
    "action_id": "act-16",
    "action": "PREPARE_ALLOCATE_SERVER",
    "allocation": alloc_bad_pkg,
    "expires_at": int(time.time() * 1000) + 10000
}
acks.clear()
relay._handle_batch_action(cfg, state2, local_id="m1", message=msg16_prep)
assert acks[-1]["status"] == "PREPARE_FAILED"
assert "invalid_package_order" in acks[-1]["reason"]

# T17: Duplicate URL across tabs rejected
alloc_dup_url = [
    {"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123?privateServerLinkCode=deadbeef"},
    {"pkg": "com.tinh.vv.hj", "url": "https://www.roblox.com/games/123?privateServerLinkCode=deadbeef"}
]
msg17_prep = {
    "type": "aot_batch_action",
    "protocol": "fleet-batch-v1",
    "target_device_ids": ["m1"],
    "action_id": "act-17",
    "action": "PREPARE_ALLOCATE_SERVER",
    "allocation": alloc_dup_url,
    "expires_at": int(time.time() * 1000) + 10000
}
acks.clear()
relay._handle_batch_action(cfg, state2, local_id="m1", message=msg17_prep)
assert acks[-1]["status"] == "PREPARE_FAILED"
assert "duplicate_url" in acks[-1]["reason"]

# T18: ABORT after PREPARE cleans up prep file and COMMIT afterwards fails (self-contained, deterministic)
msg18_prep = {
    "type": "aot_batch_action",
    "protocol": "fleet-batch-v1",
    "target_device_ids": ["m1"],
    "action_id": "act-18",
    "action": "PREPARE_ALLOCATE_SERVER",
    "allocation": [{"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123?privateServerLinkCode=abc18"}],
    "expires_at": int(time.time() * 1000) + 10000
}
acks.clear()
res18_prep = relay._handle_batch_action(cfg, state2, local_id="m1", message=msg18_prep)
assert res18_prep is True
assert acks[-1]["status"] == "PREPARE_READY"
assert os.path.exists(f"{links_path2}.prep.act-18")

msg18_abort = {
    "type": "aot_batch_action",
    "protocol": "fleet-batch-v1",
    "target_device_ids": ["m1"],
    "action_id": "act-18",
    "action": "ABORT_ALLOCATE_SERVER",
    "expires_at": int(time.time() * 1000) + 10000
}
acks.clear()
res18_abort = relay._handle_batch_action(cfg, state2, local_id="m1", message=msg18_abort)
assert res18_abort is True
assert acks[-1]["status"] == "FAILED"
assert acks[-1]["reason"] == "aborted_by_hub"
assert not os.path.exists(f"{links_path2}.prep.act-18")

msg18_commit = {
    "type": "aot_batch_action",
    "protocol": "fleet-batch-v1",
    "target_device_ids": ["m1"],
    "action_id": "act-18",
    "action": "COMMIT_ALLOCATE_SERVER",
    "expires_at": int(time.time() * 1000) + 10000
}
acks.clear()
res18_commit = relay._handle_batch_action(cfg, state2, local_id="m1", message=msg18_commit)
assert res18_commit is True
assert acks[-1]["status"] == "FAILED"
assert acks[-1]["reason"] == "missing_prep_file"

# T19: Interleaved Abort & New Prepare - new transaction PREPARE cleans up older aborted transaction's prep file without breaking delayed ABORT or current transaction's COMMIT
msg19_act1_prep = {
    "type": "aot_batch_action",
    "protocol": "fleet-batch-v1",
    "target_device_ids": ["m1"],
    "action_id": "act-19-a",
    "action": "PREPARE_ALLOCATE_SERVER",
    "allocation": [{"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123?privateServerLinkCode=abc19a"}],
    "expires_at": int(time.time() * 1000) + 10000
}
acks.clear()
assert relay._handle_batch_action(cfg, state2, local_id="m1", message=msg19_act1_prep) is True
assert acks[-1]["status"] == "PREPARE_READY"
assert os.path.exists(f"{links_path2}.prep.act-19-a")

# New transaction PREPARE arrives before delayed ABORT for act-19-a
msg19_act2_prep = {
    "type": "aot_batch_action",
    "protocol": "fleet-batch-v1",
    "target_device_ids": ["m1"],
    "action_id": "act-19-b",
    "action": "PREPARE_ALLOCATE_SERVER",
    "allocation": [{"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123?privateServerLinkCode=abc19b"}],
    "expires_at": int(time.time() * 1000) + 10000
}
acks.clear()
assert relay._handle_batch_action(cfg, state2, local_id="m1", message=msg19_act2_prep) is True
assert acks[-1]["status"] == "PREPARE_READY"
assert not os.path.exists(f"{links_path2}.prep.act-19-a"), "Older prep file cleaned up"
assert os.path.exists(f"{links_path2}.prep.act-19-b"), "New prep file exists"

# Delayed ABORT for act-19-a arrives -> cleanly returns FAILED (aborted_by_hub), does NOT damage act-19-b
msg19_act1_abort = {
    "type": "aot_batch_action",
    "protocol": "fleet-batch-v1",
    "target_device_ids": ["m1"],
    "action_id": "act-19-a",
    "action": "ABORT_ALLOCATE_SERVER",
    "expires_at": int(time.time() * 1000) + 10000
}
acks.clear()
assert relay._handle_batch_action(cfg, state2, local_id="m1", message=msg19_act1_abort) is True
assert acks[-1]["status"] == "FAILED"
assert acks[-1]["reason"] == "aborted_by_hub"
assert os.path.exists(f"{links_path2}.prep.act-19-b"), "act-19-b prep file intact"

# COMMIT for act-19-b succeeds
msg19_act2_commit = {
    "type": "aot_batch_action",
    "protocol": "fleet-batch-v1",
    "target_device_ids": ["m1"],
    "action_id": "act-19-b",
    "action": "COMMIT_ALLOCATE_SERVER",
    "expires_at": int(time.time() * 1000) + 10000
}
acks.clear()
assert relay._handle_batch_action(cfg, state2, local_id="m1", message=msg19_act2_commit) is True
assert acks[-1]["status"] == "OPENED"
assert not os.path.exists(f"{links_path2}.prep.act-19-b")
assert os.path.exists(links_path2)

temp_dir2.cleanup()
relay.SERVER_LINKS_PATH = temp_path / "server_links.txt"
relay.STATE_PATH = temp_path / "aot_group_state.json"

print("AOT_ALLOCATE_SERVER_RELAY_TEST=OK")
