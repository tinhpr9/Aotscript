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
import relay
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

# Cleanup
for p in (links_path, bak_path):
    if os.path.exists(p):
        os.remove(p)

# 1. Invalid payload -> FAILED
relay._handle_allocate_server(cfg, state, local_id="m1", message={"type": "aot_batch_action", "protocol": "fleet-batch-v1", "action": "ALLOCATE_SERVER", "action_id": "a1", "expires_at": int(time.time()*1000) + 10000})
assert acks[-1]["status"] == "FAILED"
assert "invalid_allocation_format" in acks[-1]["reason"]

# 2. Valid payload -> ALLOCATED then OPENED
alloc = [
    {"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123?privateServerLinkCode=abc"},
    {"pkg": "com.tinh.vv.hj", "url": "https://www.roblox.com/games/123?privateServerLinkCode=def"}
]
msg = {
    "type": "aot_batch_action",
    "protocol": "fleet-batch-v1",
    "action": "ALLOCATE_SERVER",
    "action_id": "a2",
    "expires_at": int(time.time()*1000) + 10000,
    "allocation": alloc
}

relay._handle_allocate_server(cfg, state, local_id="m1", message=msg)
assert len(acks) >= 3
assert acks[-2]["status"] == "ALLOCATED"
print("ACKS:", acks); assert acks[-1]["status"] == "OPENED"
assert acks[-1]["executed"] == True
assert state["processed"] == True
assert "action_results" in state
assert state["action_results"]["a2"]["status"] == "OPENED"

with open(links_path, "r", encoding="utf-8") as f:
    lines = f.read().splitlines()
assert len(lines) == 2
assert lines[0] == "com.tinh.vv.hi,https://www.roblox.com/games/123?privateServerLinkCode=abc"

# Replay terminal result exactly once pattern
acks.clear()
relay._handle_allocate_server(cfg, state, local_id="m1", message=msg)
assert len(acks) == 1
assert acks[0]["status"] == "OPENED"

# 3. Open fails -> FAILED and rollback
state.clear()
acks.clear()
mock_ctrl.fail = True
msg["action_id"] = "a3"
with open(links_path, "w", encoding="utf-8") as f:
    f.write("OLD\n")
relay._handle_allocate_server(cfg, state, local_id="m1", message=msg)
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
relay._handle_allocate_server(cfg, state, local_id="m1", message=msg)
assert acks[-1]["status"] == "FAILED"
assert not os.path.exists(links_path)

print("AOT_ALLOCATE_SERVER_RELAY_TEST=OK")
