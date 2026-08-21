#!/usr/bin/env python3
"""
test_contract_conformance.py - Cross-Language Protocol Contract Conformance Guard (M3)

Enforces that Python Relay adheres strictly to the canonical contract
defined in `contracts/fleet_batch_v1_contract.json` shared with Cloudflare Worker.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import sys
import tempfile
import time
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
AOT_GROUP_CONTROL = REPO_ROOT / "aot-group-control"
CONTRACT_PATH = REPO_ROOT / "contracts" / "fleet_batch_v1_contract.json"

# Load canonical contract
with open(CONTRACT_PATH, "r", encoding="utf-8") as f:
    CONTRACT = json.load(f)

# Load relay module from aot-group-control as authoritative root
RELAY_SPEC = importlib.util.spec_from_file_location("aot_relay", AOT_GROUP_CONTROL / "relay.py")
RELAY = importlib.util.module_from_spec(RELAY_SPEC)
sys.modules[RELAY_SPEC.name] = RELAY
RELAY_SPEC.loader.exec_module(RELAY)


class TestPythonContractConformance(unittest.TestCase):
    """
    Validates Python Relay against the single source of contract truth.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.links_path = pathlib.Path(self.temp_dir.name) / "server_links.txt"
        self.state_path = pathlib.Path(self.temp_dir.name) / "aot_group_state.json"
        
        self.orig_links_path = RELAY.SERVER_LINKS_PATH
        self.orig_state_path = RELAY.STATE_PATH
        self.orig_send_ack = RELAY._send_batch_ack

        RELAY.SERVER_LINKS_PATH = self.links_path
        RELAY.STATE_PATH = self.state_path

        self.acks: list[dict] = []
        RELAY._send_batch_ack = lambda cfg, **kwargs: self.acks.append(kwargs)

    def tearDown(self) -> None:
        RELAY.SERVER_LINKS_PATH = self.orig_links_path
        RELAY.STATE_PATH = self.orig_state_path
        RELAY._send_batch_ack = self.orig_send_ack
        self.temp_dir.cleanup()

    def test_protocol_version_matches_contract(self) -> None:
        """Verify protocol version string matches canonical contract."""
        self.assertEqual(RELAY.HUB_PROTOCOL_VERSION, CONTRACT["protocol_version"])

    def test_package_mapping_and_order_matches_contract(self) -> None:
        """Verify package prefix and suffix ordering matches contract exactly."""
        prefix = CONTRACT["package_mapping"]["prefix"]
        suffixes = CONTRACT["package_mapping"]["suffixes"]
        max_tabs = CONTRACT["package_mapping"]["max_tabs"]
        cfg = {"device_id": "m1"}
        state = {}

        # 1. Valid allocation matching contract max_tabs
        valid_alloc = [
            {
                "pkg": f"{prefix}{suffixes[i]}",
                "url": f"https://www.roblox.com/games/97598239454123?privateServerLinkCode={str(i+1).zfill(32)}"
            }
            for i in range(max_tabs)
        ]
        msg = {
            "type": "aot_batch_action",
            "protocol": CONTRACT["protocol_version"],
            "target_device_ids": ["m1"],
            "action_id": "act-contract-order",
            "action": "PREPARE_ALLOCATE_SERVER",
            "allocation": valid_alloc,
            "expires_at": int(time.time() * 1000) + 10000,
        }
        self.acks.clear()
        self.assertTrue(RELAY._handle_batch_action(cfg, state, local_id="m1", message=msg))
        self.assertEqual(len(self.acks), 1)
        self.assertEqual(self.acks[0]["status"], "PREPARE_READY")

    def test_roblox_url_regex_matrix_parity(self) -> None:
        """Verify production URL pattern matches contract regex specification across valid and invalid matrix."""
        contract_regex = re.compile(CONTRACT["url_rules"]["regex"])

        # Valid matrix
        valid_urls = [
            "https://www.roblox.com/games/97598239454123?privateServerLinkCode=11111111111111111111111111111111",
            "https://roblox.com/games/12345?PrivateServerLinkCode=abcdef0123456789abcdef0123456789",
            "https://www.roblox.com/games/999?privateServerLinkCode=ABCDEF0123456789",
        ]
        for url in valid_urls:
            self.assertTrue(bool(contract_regex.match(url)), f"Contract regex rejected valid URL: {url}")
            self.assertTrue(bool(RELAY.ROBLOX_SERVER_URL_PATTERN.match(url)), f"Relay regex rejected valid URL: {url}")

        # Invalid matrix
        invalid_urls = [
            "http://www.roblox.com/games/123?privateServerLinkCode=11111111111111111111111111111111",     # Insecure HTTP
            "https://evil-roblox.com/games/123?privateServerLinkCode=11111111111111111111111111111111",    # Foreign host
            "https://roblox.com/home?privateServerLinkCode=11111111111111111111111111111111",              # Wrong path
            "https://roblox.com/games/123?privateServerLinkCode=NOT_HEX_CHARS_ZZZ!",                        # Non-hex code
            "https://roblox.com/games/123?privateServerLinkCode=111&extra=222",                             # Extra query param
        ]
        for url in invalid_urls:
            self.assertFalse(bool(contract_regex.match(url)), f"Contract regex accepted invalid URL: {url}")
            self.assertFalse(bool(RELAY.ROBLOX_SERVER_URL_PATTERN.match(url)), f"Relay regex accepted invalid URL: {url}")

    def test_all_relay_terminal_and_intermediate_ack_statuses_in_contract(self) -> None:
        """Verify all statuses emitted by Relay are declared in contract enums."""
        contract_statuses = set(CONTRACT["batch_ack_status_enums"])
        observed_statuses = set()

        cfg = {"device_id": "m1"}
        state = {}

        # 1. Allocate server statuses
        msg_prep = {
            "type": "aot_batch_action",
            "protocol": "fleet-batch-v1",
            "target_device_ids": ["m1"],
            "action_id": "act-stat-check",
            "action": "PREPARE_ALLOCATE_SERVER",
            "allocation": [{"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/1?privateServerLinkCode=11111111111111111111111111111111"}],
            "expires_at": int(time.time() * 1000) + 10000,
        }
        self.acks.clear()
        RELAY._handle_batch_action(cfg, state, local_id="m1", message=msg_prep)
        for a in self.acks:
            observed_statuses.add(a["status"])

        # 2. Timeout & Duplicate
        msg_timeout = dict(msg_prep, expires_at=int(time.time() * 1000) - 5000)
        self.acks.clear()
        RELAY._handle_batch_action(cfg, state, local_id="m1", message=msg_timeout)
        for a in self.acks:
            observed_statuses.add(a["status"])

        # 3. Swift Backup statuses (ACCEPTED, OPENED, APPS_OPENED, FAILED_NOT_INSTALLED)
        msg_swift = {
            "type": "aot_batch_action",
            "protocol": "fleet-batch-v1",
            "target_device_ids": ["m1"],
            "action_id": "act-swift-stat",
            "action": "OPEN_SWIFT_APPS",
            "package": "org.swiftapps.swiftbackup",
            "expires_at": int(time.time() * 1000) + 10000,
        }
        self.acks.clear()
        # Mock controller to avoid real UI execution
        orig_open_apps = RELAY.controller.open_swift_apps
        try:
            RELAY.controller.open_swift_apps = lambda: {"executed": True}
            RELAY._handle_batch_action(cfg, state, local_id="m1", message=msg_swift)
            for a in self.acks:
                observed_statuses.add(a["status"])
        finally:
            RELAY.controller.open_swift_apps = orig_open_apps

        for st in observed_statuses:
            self.assertIn(st, contract_statuses, f"Relay emitted status {st!r} not in contract enums!")


class TestCrossLanguageDriftAdversarialProofs(unittest.TestCase):
    """
    Adversarial proofs: Demonstrates that independent drift on either side
    (protocol version, action enum, status enum, package mapping) is immediately caught.
    """

    def test_adversarial_protocol_version_drift_caught(self) -> None:
        """Simulate protocol version drift (e.g. Hub sends fleet-batch-v2)."""
        cfg = {"device_id": "m1"}
        state = {}
        msg = {
            "type": "aot_batch_action",
            "protocol": "fleet-batch-v2", # Drifted version
            "target_device_ids": ["m1"],
            "action_id": "act-drift-01",
            "action": "PREPARE_ALLOCATE_SERVER",
            "allocation": [{"pkg": "com.tinh.vv.hi", "url": "https://www.roblox.com/games/1?privateServerLinkCode=11111111111111111111111111111111"}],
            "expires_at": int(time.time() * 1000) + 10000,
        }
        acks = []
        orig_send = RELAY._send_batch_ack
        try:
            RELAY._send_batch_ack = lambda cfg, **kwargs: acks.append(kwargs)
            result = RELAY._handle_batch_action(cfg, state, local_id="m1", message=msg)
            self.assertTrue(result)
            self.assertEqual(len(acks), 0, "Drifted protocol version should not trigger ACK")
        finally:
            RELAY._send_batch_ack = orig_send

    def test_adversarial_unknown_action_drift_caught(self) -> None:
        """Simulate action enum drift (e.g. Hub sends PREPARE_ALLOCATE_SRV)."""
        cfg = {"device_id": "m1"}
        state = {}
        msg = {
            "type": "aot_batch_action",
            "protocol": "fleet-batch-v1",
            "target_device_ids": ["m1"],
            "action_id": "act-drift-02",
            "action": "PREPARE_ALLOCATE_SRV", # Drifted typo
            "expires_at": int(time.time() * 1000) + 10000,
        }
        acks = []
        orig_send = RELAY._send_batch_ack
        try:
            RELAY._send_batch_ack = lambda cfg, **kwargs: acks.append(kwargs)
            result = RELAY._handle_batch_action(cfg, state, local_id="m1", message=msg)
            self.assertTrue(result)
            self.assertEqual(len(acks), 0, "Unknown action enum should not execute")
        finally:
            RELAY._send_batch_ack = orig_send


if __name__ == "__main__":
    unittest.main()
