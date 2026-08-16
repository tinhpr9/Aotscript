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
relay.action_already_processed = lambda state, aid: state.get(f"processed_{aid}", False)
relay.mark_action_processed = lambda state, aid: state.update({f"processed_{aid}": True})

state: dict[str, Any] = {}
cfg: dict[str, str] = {}

links_path = "/storage/emulated/0/Download/Shouko/server_links.txt"
bak_path = links_path + ".bak"

for p in (links_path, bak_path):
    if os.path.exists(p):
        os.remove(p)

def cleanup():
    acks.clear()
    state.clear()
    for p in (links_path, bak_path):
        if os.path.exists(p): os.remove(p)
    import glob
    for p in glob.glob(links_path + ".prep.*"): os.remove(p)

# 1. Invalid payload format
msg1 = {"type": "aot_batch_action", "protocol": "fleet-batch-v1", "action": "PREPARE_ALLOCATE_SERVER", "action_id": "a1", "expires_at": int(time.time()*1000) + 10000}
relay._handle_batch_action(cfg, state, local_id="m1", message=msg1)
assert acks[-1]["status"] == "PREPARE_FAILED"
assert "invalid_allocation_format" in acks[-1]["reason"]

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
    "allocation": alloc
}
relay._handle_batch_action(cfg, state, local_id="m1", message=msg5)
assert acks[-1]["status"] == "PREPARE_READY"

msg_commit = dict(msg5)
msg_commit["action"] = "COMMIT_ALLOCATE_SERVER"
relay._handle_batch_action(cfg, state, local_id="m1", message=msg_commit)
assert acks[-2]["status"] == "ALLOCATED"
assert acks[-1]["status"] == "OPENED"
assert state["processed_a5"] == True

# Replay -> DUPLICATE
acks.clear()
relay._handle_batch_action(cfg, state, local_id="m1", message=msg5)
assert acks[-1]["status"] == "DUPLICATE"

relay._handle_batch_action(cfg, state, local_id="m1", message=msg_commit)
assert acks[-1]["status"] == "DUPLICATE"
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

print("AOT_ALLOCATE_SERVER_RELAY_TEST=OK")
