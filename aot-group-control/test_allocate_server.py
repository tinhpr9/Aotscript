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
prep_path = links_path + ".prep.a1"

# Cleanup
for p in (links_path, bak_path, prep_path):
    if os.path.exists(p):
        os.remove(p)

expires_future = int(time.time() * 1000) + 60_000

# 1. PREPARE with invalid allocation -> PREPARE_FAILED
relay._handle_allocate_server(cfg, state, local_id="m1", message={
    "type": "aot_batch_action", "protocol": "fleet-batch-v1",
    "action": "PREPARE_ALLOCATE_SERVER", "action_id": "a1",
    "expires_at": expires_future,
    # missing allocation field
})
assert acks[-1]["status"] == "PREPARE_FAILED", f"Expected PREPARE_FAILED got {acks[-1]['status']}"
assert "invalid_allocation_format" in acks[-1]["reason"]

# 2. PREPARE with expired expires_at -> TIMEOUT immediately
acks.clear()
relay._handle_allocate_server(cfg, state, local_id="m1", message={
    "type": "aot_batch_action", "protocol": "fleet-batch-v1",
    "action": "PREPARE_ALLOCATE_SERVER", "action_id": "a1_exp",
    "expires_at": int(time.time() * 1000) - 1000,  # already expired
    "allocation": [{"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123?privateServerLinkCode=abc"}],
})
assert acks[-1]["status"] == "TIMEOUT", f"Expected TIMEOUT for expired PREPARE, got {acks[-1]['status']}"

# 3. Full 2PC: PREPARE -> COMMIT -> OPENED
alloc = [
    {"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123?privateServerLinkCode=abc"},
    {"pkg": "com.tinh.vv.hj", "url": "https://www.roblox.com/games/123?privateServerLinkCode=def"}
]
acks.clear()
mock_ctrl.fail = False
# Step 1: PREPARE
relay._handle_allocate_server(cfg, state, local_id="m1", message={
    "type": "aot_batch_action", "protocol": "fleet-batch-v1",
    "action": "PREPARE_ALLOCATE_SERVER", "action_id": "a2",
    "expires_at": expires_future,
    "allocation": alloc,
})
assert acks[-1]["status"] == "PREPARE_READY", f"Expected PREPARE_READY, got {acks[-1]['status']}"
assert os.path.exists(links_path + ".prep.a2"), "prep file should exist"

# Step 2: COMMIT (must carry same expires_at)
relay._handle_allocate_server(cfg, state, local_id="m1", message={
    "type": "aot_batch_action", "protocol": "fleet-batch-v1",
    "action": "COMMIT_ALLOCATE_SERVER", "action_id": "a2",
    "expires_at": expires_future,  # Fix 1: same expires_at
})
assert acks[-1]["status"] == "OPENED", f"Expected OPENED, got {acks[-1]['status']}"
assert acks[-1]["executed"] == True
assert state["processed"] == True
assert not os.path.exists(links_path + ".prep.a2"), "prep file should be gone after COMMIT"

with open(links_path, "r", encoding="utf-8") as f:
    lines = f.read().splitlines()
assert len(lines) == 2
assert lines[0] == "com.tinh.vv.hi,https://www.roblox.com/games/123?privateServerLinkCode=abc"

# 4. ABORT: PREPARE then ABORT -> cleans up prep file
state.clear()
acks.clear()
relay._handle_allocate_server(cfg, state, local_id="m1", message={
    "type": "aot_batch_action", "protocol": "fleet-batch-v1",
    "action": "PREPARE_ALLOCATE_SERVER", "action_id": "a3",
    "expires_at": expires_future,
    "allocation": alloc,
})
assert acks[-1]["status"] == "PREPARE_READY"
prep_path_a3 = links_path + ".prep.a3"
assert os.path.exists(prep_path_a3), "prep file must exist before ABORT"

relay._handle_allocate_server(cfg, state, local_id="m1", message={
    "type": "aot_batch_action", "protocol": "fleet-batch-v1",
    "action": "ABORT_ALLOCATE_SERVER", "action_id": "a3",
    "expires_at": expires_future,  # Fix 1: same expires_at
})
assert not os.path.exists(prep_path_a3), "ABORT must clean up exact .prep.<action_id> file"

# 5. COMMIT with expired expires_at -> TIMEOUT (fail-closed)
state.clear()
acks.clear()
relay._handle_allocate_server(cfg, state, local_id="m1", message={
    "type": "aot_batch_action", "protocol": "fleet-batch-v1",
    "action": "COMMIT_ALLOCATE_SERVER", "action_id": "a4",
    "expires_at": int(time.time() * 1000) - 1000,  # expired
})
assert acks[-1]["status"] == "TIMEOUT", f"Expected TIMEOUT for expired COMMIT, got {acks[-1]['status']}"

print("AOT_ALLOCATE_SERVER_RELAY_TEST=OK")
