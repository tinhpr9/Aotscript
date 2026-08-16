import os
import pathlib
import time
import shutil
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

import tempfile
import glob

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

# 5. PREPARE -> COMMIT -> OPENED
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
    {"pkg": "com.tinh.vv.hj", "url": "https://www.roblox.com/games/123?privateServerLinkCode=abcdef"}
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

print("AOT_ALLOCATE_SERVER_RELAY_TEST=OK")
