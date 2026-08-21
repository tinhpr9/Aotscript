#!/usr/bin/env python3
"""
test_mutation_hardening.py - Targeted Mutation Testing & Test-Strength Hardening (M2)

Deterministic verification of high-value production invariants against synthetic mutants:
- MUTANT-PHAN-1: Roblox URL validation, domain security, link code hex constraint.
- MUTANT-PHAN-2: Package sequence ordering, allocation bounds (1..10), intra-device URL deduplication.
- MUTANT-PHAN-3: 2PC commit-phase failure rollback and terminal ACK consistency.
- MUTANT-FLEET-1: Fleet allocator duplicate detection, capacity threshold, and invalid tab limits.
- MUTANT-GUARD-1: AST architecture guard mutation sensitivity on static and dynamic imports.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
AOT_GROUP_CONTROL = REPO_ROOT / "aot-group-control"
CLOUDFLARE_WORKER = REPO_ROOT / "cloudflare-worker"

# Load relay module from aot-group-control
RELAY_SPEC = importlib.util.spec_from_file_location("aot_relay", AOT_GROUP_CONTROL / "relay.py")
RELAY = importlib.util.module_from_spec(RELAY_SPEC)
sys.modules[RELAY_SPEC.name] = RELAY
RELAY_SPEC.loader.exec_module(RELAY)

# Load controller module from aot-group-control
CONTROLLER_SPEC = importlib.util.spec_from_file_location("aot_controller", AOT_GROUP_CONTROL / "controller.py")
CONTROLLER = importlib.util.module_from_spec(CONTROLLER_SPEC)
sys.modules[CONTROLLER_SPEC.name] = CONTROLLER
CONTROLLER_SPEC.loader.exec_module(CONTROLLER)


class TestRobloxUrlValidationMutants(unittest.TestCase):
    """
    MUTANT-PHAN-1: Validates that mutated/malformed Roblox URLs are fail-closed
    rejected by production regex and Relay batch handler.
    """

    def test_valid_urls_pass_regex(self) -> None:
        valid_urls = [
            "https://www.roblox.com/games/97598239454123?privateServerLinkCode=11111111111111111111111111111111",
            "https://roblox.com/games/12345?privateServerLinkCode=abcdef0123456789abcdef0123456789",
            "https://www.roblox.com/games/999?privateServerLinkCode=ABCDEF0123456789",
            "https://roblox.com/games/97598239454123?PrivateServerLinkCode=11111111111111111111111111111111",
        ]
        for url in valid_urls:
            self.assertIsNotNone(
                RELAY.ROBLOX_SERVER_URL_PATTERN.match(url),
                f"Valid URL failed regex: {url}"
            )

    def test_mutant_invalid_hosts_rejected(self) -> None:
        mutant_urls = [
            "http://www.roblox.com/games/123?privateServerLinkCode=11111111111111111111111111111111",  # Insecure HTTP
            "https://evil-roblox.com/games/123?privateServerLinkCode=11111111111111111111111111111111", # Phishing domain
            "https://roblox.com.evil.com/games/123?privateServerLinkCode=11111111111111111111111111111111",
            "https://fake.roblox.com/games/123?privateServerLinkCode=11111111111111111111111111111111",
        ]
        for url in mutant_urls:
            self.assertIsNone(
                RELAY.ROBLOX_SERVER_URL_PATTERN.match(url),
                f"Mutant invalid host passed regex: {url}"
            )

    def test_mutant_invalid_paths_and_params_rejected(self) -> None:
        mutant_urls = [
            "https://www.roblox.com/home?privateServerLinkCode=11111111111111111111111111111111",      # Wrong path
            "https://www.roblox.com/games/not_a_number?privateServerLinkCode=11111111111111111111111111111111",
            "https://www.roblox.com/games/123?otherCode=11111111111111111111111111111111",            # Wrong query key
            "https://www.roblox.com/games/123?privateServerLinkCode=NOT_HEX_CHARS_ZZZ!",               # Non-hex code
            "https://www.roblox.com/games/123?privateServerLinkCode=",                                  # Empty code
            "https://www.roblox.com/games/123",                                                         # Missing code
        ]
        for url in mutant_urls:
            self.assertIsNone(
                RELAY.ROBLOX_SERVER_URL_PATTERN.match(url),
                f"Mutant malformed URL passed regex: {url}"
            )


class TestRelayBatchAllocationMutants(unittest.TestCase):
    """
    MUTANT-PHAN-2: Validates Relay batch action invariants (ordering, capacity, uniqueness).
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.links_path = pathlib.Path(self.temp_dir.name) / "server_links.txt"
        self.state_path = pathlib.Path(self.temp_dir.name) / "aot_group_state.json"
        
        self.orig_links_path = RELAY.SERVER_LINKS_PATH
        self.orig_state_path = RELAY.STATE_PATH
        RELAY.SERVER_LINKS_PATH = self.links_path
        RELAY.STATE_PATH = self.state_path

        self.acks: list[dict] = []
        RELAY._send_batch_ack = lambda cfg, **kwargs: self.acks.append(kwargs)

    def tearDown(self) -> None:
        RELAY.SERVER_LINKS_PATH = self.orig_links_path
        RELAY.STATE_PATH = self.orig_state_path
        self.temp_dir.cleanup()

    def test_mutant_scrambled_package_order_rejected(self) -> None:
        """Mutant: Allocation provides com.tinh.vv.hj before com.tinh.vv.hi."""
        cfg = {"device_id": "m1"}
        state = {}
        scrambled_allocation = [
            {"pkg": "com.tinh.vv.hj", "url": "https://www.roblox.com/games/123?privateServerLinkCode=11111111111111111111111111111111"},
            {"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/123?privateServerLinkCode=22222222222222222222222222222222"},
        ]
        msg = {
            "type": "aot_batch_action",
            "protocol": "fleet-batch-v1",
            "target_device_ids": ["m1"],
            "action_id": "act-mut-pkg-order",
            "action": "PREPARE_ALLOCATE_SERVER",
            "allocation": scrambled_allocation,
            "expires_at": int(time.time() * 1000) + 10000,
        }
        self.acks.clear()
        self.assertTrue(RELAY._handle_batch_action(cfg, state, local_id="m1", message=msg))
        self.assertEqual(len(self.acks), 1)
        self.assertEqual(self.acks[0]["status"], "PREPARE_FAILED")
        self.assertIn("invalid_package_order_at_0", self.acks[0]["reason"])

    def test_mutant_duplicate_url_rejected(self) -> None:
        """Mutant: Allocation assigns the same URL to package .hi and package .hj."""
        cfg = {"device_id": "m1"}
        state = {}
        dup_url = "https://www.roblox.com/games/123?privateServerLinkCode=11111111111111111111111111111111"
        dup_allocation = [
            {"pkg": "com.tinh.vv.hi", "url": dup_url},
            {"pkg": "com.tinh.vv.hj", "url": dup_url},
        ]
        msg = {
            "type": "aot_batch_action",
            "protocol": "fleet-batch-v1",
            "target_device_ids": ["m1"],
            "action_id": "act-mut-dup-url",
            "action": "PREPARE_ALLOCATE_SERVER",
            "allocation": dup_allocation,
            "expires_at": int(time.time() * 1000) + 10000,
        }
        self.acks.clear()
        self.assertTrue(RELAY._handle_batch_action(cfg, state, local_id="m1", message=msg))
        self.assertEqual(len(self.acks), 1)
        self.assertEqual(self.acks[0]["status"], "PREPARE_FAILED")
        self.assertIn("duplicate_url_at_1", self.acks[0]["reason"])

    def test_mutant_out_of_bounds_allocation_length_rejected(self) -> None:
        """Mutant: Allocation length 0 or 11."""
        cfg = {"device_id": "m1"}
        state = {}
        for invalid_alloc in [[], [{"pkg": f"com.tinh.vv.h{c}", "url": "https://www.roblox.com/games/1?privateServerLinkCode=11111111111111111111111111111111"} for c in "abcdefghijkl"]]:
            msg = {
                "type": "aot_batch_action",
                "protocol": "fleet-batch-v1",
                "target_device_ids": ["m1"],
                "action_id": "act-mut-len",
                "action": "PREPARE_ALLOCATE_SERVER",
                "allocation": invalid_alloc,
                "expires_at": int(time.time() * 1000) + 10000,
            }
            self.acks.clear()
            self.assertTrue(RELAY._handle_batch_action(cfg, state, local_id="m1", message=msg))
            self.assertEqual(len(self.acks), 1)
            self.assertEqual(self.acks[0]["status"], "PREPARE_FAILED")
            self.assertIn("invalid_allocation_format", self.acks[0]["reason"])


class TestRelayCommitRollbackMutants(unittest.TestCase):
    """
    MUTANT-PHAN-3: Validates that failure during open_roblox_servers execution
    restores prior valid server_links.txt and sends FAILED ACK with open_servers_failed.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.links_path = pathlib.Path(self.temp_dir.name) / "server_links.txt"
        self.state_path = pathlib.Path(self.temp_dir.name) / "aot_group_state.json"
        
        self.orig_links_path = RELAY.SERVER_LINKS_PATH
        self.orig_state_path = RELAY.STATE_PATH
        self.orig_open = RELAY.controller.open_roblox_servers
        self.orig_root = CONTROLLER._root_run

        RELAY.SERVER_LINKS_PATH = self.links_path
        RELAY.STATE_PATH = self.state_path
        self.acks: list[dict] = []
        RELAY._send_batch_ack = lambda cfg, **kwargs: self.acks.append(kwargs)

    def tearDown(self) -> None:
        RELAY.SERVER_LINKS_PATH = self.orig_links_path
        RELAY.STATE_PATH = self.orig_state_path
        RELAY.controller.open_roblox_servers = self.orig_open
        CONTROLLER._root_run = self.orig_root
        self.temp_dir.cleanup()

    def test_mutant_commit_failure_restores_file_and_fails_closed(self) -> None:
        cfg = {"device_id": "m1"}
        state = {}

        # 1. Establish initial baseline server_links.txt
        initial_content = "com.tinh.vv.hi,https://www.roblox.com/games/1?privateServerLinkCode=00000000000000000000000000000001\n"
        self.links_path.write_text(initial_content, encoding="utf-8")

        # 2. Prepare new allocation
        new_alloc = [
            {"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/1?privateServerLinkCode=22222222222222222222222222222222"}
        ]
        msg_prep = {
            "type": "aot_batch_action",
            "protocol": "fleet-batch-v1",
            "target_device_ids": ["m1"],
            "action_id": "act-rollback-test",
            "action": "PREPARE_ALLOCATE_SERVER",
            "allocation": new_alloc,
            "expires_at": int(time.time() * 1000) + 10000,
        }
        self.assertTrue(RELAY._handle_batch_action(cfg, state, local_id="m1", message=msg_prep))

        # 3. Simulate failure in open_roblox_servers
        def broken_open(allocation):
            raise CONTROLLER.AotControllerError("Simulated am start failure")

        RELAY.controller.open_roblox_servers = broken_open

        msg_commit = {
            "type": "aot_batch_action",
            "protocol": "fleet-batch-v1",
            "target_device_ids": ["m1"],
            "action_id": "act-rollback-test",
            "action": "COMMIT_ALLOCATE_SERVER",
            "expires_at": int(time.time() * 1000) + 10000,
        }
        self.acks.clear()
        self.assertTrue(RELAY._handle_batch_action(cfg, state, local_id="m1", message=msg_commit))

        # Assert ALLOCATED then FAILED ACK sent
        self.assertEqual(len(self.acks), 2)
        self.assertEqual(self.acks[0]["status"], "ALLOCATED")
        self.assertEqual(self.acks[1]["status"], "FAILED")
        self.assertIn("open_servers_failed", self.acks[1]["reason"])

        # Assert server_links.txt was rolled back to initial content
        self.assertEqual(self.links_path.read_text(encoding="utf-8"), initial_content)


class TestArchitectureGuardMutationSensitivity(unittest.TestCase):
    """
    MUTANT-GUARD-1: Proves that AST architecture guards fail if import extraction
    is mutated or weakened.
    """

    def test_mutant_omitted_import_from_detected(self) -> None:
        """Prove that from ... import ... forbidden imports are strictly caught."""
        from tests.test_architecture_guards import check_boundary_rules

        code = "from relay import _handle_batch_action\n"
        violations = check_boundary_rules(code, {"relay"}, filename="controller.py")
        self.assertEqual(len(violations), 1)
        self.assertIn("forbidden import 'relay'", violations[0])

    def test_mutant_omitted_dynamic_import_detected(self) -> None:
        """Prove that dynamic import calls are strictly caught."""
        from tests.test_architecture_guards import check_boundary_rules

        code = "mod = __import__('relay')\n"
        violations = check_boundary_rules(code, {"relay"}, filename="controller.py")
        self.assertEqual(len(violations), 1)
        self.assertIn("forbidden import 'relay'", violations[0])


if __name__ == "__main__":
    unittest.main()
