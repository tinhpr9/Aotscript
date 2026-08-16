import os
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
relay.action_already_processed = lambda state, aid: state.get("processed", False)
relay.mark_action_processed = lambda state, aid: state.update({"processed": True})

state: dict[str, Any] = {}
cfg: dict[str, str] = {}

links_path = "/storage/emulated/0/Download/Shouko/server_links.txt"
bak_path = links_path + ".bak"

for p in (links_path, bak_path):
    if os.path.exists(p):
        os.remove(p)

# 1. Invalid payload -> FAILED (using PREPARE and COMMIT)
msg1 = {"type": "aot_batch_action", "protocol": "fleet-batch-v1", "action": "PREPARE_ALLOCATE_SERVER", "action_id": "a1", "expires_at": int(time.time()*1000) + 10000}
relay._handle_batch_action(cfg, state, local_id="m1", message=msg1)
msg1_c = dict(msg1)
msg1_c["action"] = "COMMIT_ALLOCATE_SERVER"
relay._handle_batch_action(cfg, state, local_id="m1", message=msg1_c)
assert acks[-2]["status"] == "PREPARE_FAILED"
assert "invalid_allocation_format" in acks[-2]["reason"]
assert acks[-1]["status"] == "FAILED"
assert "missing_prep_file" in acks[-1]["reason"]

# 2. Valid payload -> ALLOCATED then OPENED
alloc = [
    {"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123?privateServerLinkCode=abc"},
    {"pkg": "com.tinh.vv.hj", "url": "https://www.roblox.com/games/123?privateServerLinkCode=def"}
]
msg = {
    "type": "aot_batch_action",
    "protocol": "fleet-batch-v1",
    "action": "PREPARE_ALLOCATE_SERVER",
    "action_id": "a2",
    "expires_at": int(time.time()*1000) + 10000,
    "allocation": alloc
}
msg_commit = dict(msg)
msg_commit["action"] = "COMMIT_ALLOCATE_SERVER"

relay._handle_batch_action(cfg, state, local_id="m1", message=msg)
relay._handle_batch_action(cfg, state, local_id="m1", message=msg_commit)
assert len(acks) >= 3
assert acks[-2]["status"] == "ALLOCATED"
assert acks[-1]["status"] == "OPENED"
assert acks[-1]["executed"] == True
assert state["processed"] == True

with open(links_path, "r", encoding="utf-8") as f:
    lines = f.read().splitlines()
assert len(lines) == 2
assert lines[0] == "com.tinh.vv.hi,https://www.roblox.com/games/123?privateServerLinkCode=abc"

# Replay terminal result exactly once pattern
acks.clear()
relay._handle_batch_action(cfg, state, local_id="m1", message=msg)
relay._handle_batch_action(cfg, state, local_id="m1", message=msg_commit)
assert len(acks) == 2
assert acks[0]["status"] == "PREPARE_READY"
assert acks[1]["status"] == "DUPLICATE"

# 3. Open fails -> FAILED and rollback
state.clear()
acks.clear()
mock_ctrl.fail = True
msg["action_id"] = "a3"
msg_commit["action_id"] = "a3"
with open(links_path, "w", encoding="utf-8") as f:
    f.write("OLD\n")
relay._handle_batch_action(cfg, state, local_id="m1", message=msg)
relay._handle_batch_action(cfg, state, local_id="m1", message=msg_commit)
assert acks[-1]["status"] == "FAILED"
assert "open_servers_failed" in acks[-1]["reason"]
assert state.get("processed") is not True
# Verify rollback
with open(links_path, "r", encoding="utf-8") as f:
    assert f.read() == "OLD\n"

# 4. Open fails when file didn't exist -> file should be deleted
state.clear()
acks.clear()
mock_ctrl.fail = True
os.remove(links_path)
msg["action_id"] = "a4"
msg_commit["action_id"] = "a4"
relay._handle_batch_action(cfg, state, local_id="m1", message=msg)
relay._handle_batch_action(cfg, state, local_id="m1", message=msg_commit)
assert acks[-1]["status"] == "FAILED"
assert not os.path.exists(links_path)

print("AOT_ALLOCATE_SERVER_RELAY_TEST=OK")
