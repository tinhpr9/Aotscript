import os
import time
import shutil
from typing import Any

import relay
import controller

class MockController:
    def __init__(self):
        self.opened = []
    def open_roblox_servers(self, alloc):
        self.opened.append(alloc)

controller.open_roblox_servers = MockController().open_roblox_servers

acks = []
def mock_send_batch_ack(cfg, **kwargs):
    acks.append(kwargs)

relay._send_batch_ack = mock_send_batch_ack
relay.action_already_processed = lambda state, aid: state.get("processed", False)
relay.mark_action_processed = lambda state, aid: state.update({"processed": True})

state: dict[str, Any] = {}
cfg: dict[str, str] = {}

links_path = "/storage/emulated/0/Download/Shouko/server_links.txt"

# 1. Invalid payload
relay._handle_allocate_server(cfg, state, local_id="m1", message={"type": "aot_batch_action", "protocol": "fleet-batch-v1", "action": "ALLOCATE_SERVER", "action_id": "a1", "expires_at": int(time.time()*1000) + 10000})
assert acks[-1]["status"] == "FAILED"
assert "invalid_allocation_format" in acks[-1]["reason"]

# 2. Valid payload
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

try:
    os.remove(links_path)
except Exception:
    pass

relay._handle_allocate_server(cfg, state, local_id="m1", message=msg)
assert acks[-1]["status"] == "ALLOCATED"
assert acks[-1]["executed"] == True
assert state["processed"] == True

with open(links_path, "r", encoding="utf-8") as f:
    lines = f.read().splitlines()
assert len(lines) == 2
assert lines[0] == "com.tinh.vv.hi,https://www.roblox.com/games/123?privateServerLinkCode=abc"
assert lines[1] == "com.tinh.vv.hj,https://www.roblox.com/games/123?privateServerLinkCode=def"

print("AOT_ALLOCATE_SERVER_RELAY_TEST=OK")
