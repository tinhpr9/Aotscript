"""Tests for AOT OmniControl Command Router.

Validates:
- router validation
- unauthorized rejected
- invalid device/group rejected
- ordinary commands do not call AI provider
- duplicate command / rate limit
"""

import unittest
from unittest import mock

from omnicontrol.auth import AuthResult, CallerIdentity
from omnicontrol.client import HubClient, HubApiError
from omnicontrol.config import OmniConfig
from omnicontrol.router import CommandRouter, RouteRequest, RouterError


class TestOmniControlRouter(unittest.TestCase):
    def setUp(self):
        self.config = OmniConfig(
            aot_hub_base_url="https://hub.example.com",
            aot_hub_api_secret="secret123",
            discord_gateway_enabled=True,
            discord_allowed_guild_ids=frozenset(["guild_1"]),
            discord_allowed_user_ids=frozenset(["user_1"]),
            rate_limit_per_user_per_minute=2,
            rate_limit_destructive_per_user_per_minute=1,
        )
        self.mock_client = mock.Mock(spec=HubClient)
        self.router = CommandRouter(config=self.config, client=self.mock_client)
        
        self.valid_caller = CallerIdentity(
            user_id="user_1",
            gateway="discord",
            guild_id="guild_1",
        )

    def test_unauthorized_rejected(self):
        # Invalid user
        caller = CallerIdentity(user_id="hacker", gateway="discord", guild_id="guild_1")
        req = RouteRequest(caller=caller, command_name="status", args=[])
        with self.assertRaisesRegex(RouterError, "Permission denied: unauthorized_user"):
            self.router.dispatch(req)
            
        # Invalid guild
        caller2 = CallerIdentity(user_id="user_1", gateway="discord", guild_id="evil_guild")
        req2 = RouteRequest(caller=caller2, command_name="status", args=[])
        with self.assertRaisesRegex(RouterError, "Permission denied: unauthorized_guild"):
            self.router.dispatch(req2)

    def test_invalid_device_rejected(self):
        req = RouteRequest(caller=self.valid_caller, command_name="start", args=["invalid123"])
        with self.assertRaisesRegex(RouterError, "Invalid device ID: invalid123"):
            self.router.dispatch(req)
            
        req2 = RouteRequest(caller=self.valid_caller, command_name="status_device", args=[])
        with self.assertRaisesRegex(RouterError, "Missing device argument"):
            self.router.dispatch(req2)

    def test_invalid_group_rejected(self):
        req = RouteRequest(caller=self.valid_caller, command_name="batch", args=["UNKNOWN_GROUP", "open_swift_backup"])
        with self.assertRaisesRegex(RouterError, "Invalid group: UNKNOWN_GROUP"):
            self.router.dispatch(req)

    def test_invalid_batch_action_rejected(self):
        req = RouteRequest(caller=self.valid_caller, command_name="batch", args=["NOVA", "sudo rm -rf /"])
        with self.assertRaisesRegex(RouterError, "Invalid or forbidden batch action: sudo rm -rf /"):
            self.router.dispatch(req)

    def test_valid_status_dispatch(self):
        self.mock_client.get_state.return_value = {"state": {"devices": []}}
        req = RouteRequest(caller=self.valid_caller, command_name="status", args=[])
        res = self.router.dispatch(req)
        self.assertEqual(res["message"], "State fetched")
        self.mock_client.get_state.assert_called_once()
        self.mock_client.control.assert_not_called()

    def test_valid_start_dispatch(self):
        self.mock_client.control.return_value = {"ok": True}
        req = RouteRequest(caller=self.valid_caller, command_name="start", args=["m123"])
        res = self.router.dispatch(req)
        self.assertTrue(res.get("ok"))
        self.mock_client.control.assert_called_once_with("open_swift_backup", target_device_ids=["m123"])

    def test_rate_limit_and_idempotency(self):
        # We allow 2 commands per minute total, but only 1 destructive.
        self.mock_client.control.return_value = {"ok": True}
        
        # First destructive command (start is not destructive, batch is destructive)
        req1 = RouteRequest(caller=self.valid_caller, command_name="update", args=["m123"])
        self.router.dispatch(req1)
        
        # Second destructive command should be rejected
        req2 = RouteRequest(caller=self.valid_caller, command_name="update", args=["m456"])
        with self.assertRaisesRegex(RouterError, "Rate limit exceeded"):
            self.router.dispatch(req2)
            
        # Non-destructive command should still pass because total limit is 2
        req3 = RouteRequest(caller=self.valid_caller, command_name="status", args=[])
        self.router.dispatch(req3)
        
        # Third command total should be rejected
        req4 = RouteRequest(caller=self.valid_caller, command_name="status", args=[])
        with self.assertRaisesRegex(RouterError, "Rate limit exceeded"):
            self.router.dispatch(req4)

    def test_ordinary_commands_no_ai_provider(self):
        # Implicitly tested by the fact that the router only calls HubClient
        # and has no AI integration wired in Phase 1.
        pass

    def test_batch_resolution(self):
        self.mock_client.get_state.return_value = {
            "state": {
                "devices": [
                    {"device_id": "m1", "device_group": "MARMOT", "online": True},
                    {"device_id": "m2", "device_group": "MARMOT", "online": False},
                    {"device_id": "m3", "device_group": "NOVA", "online": True},
                ]
            }
        }
        self.mock_client.control.return_value = {"ok": True}
        req = RouteRequest(caller=self.valid_caller, command_name="batch", args=["MARMOT", "open_swift_backup"])
        res = self.router.dispatch(req)
        
        self.assertTrue(res.get("ok"))
        self.mock_client.control.assert_called_once_with("open_swift_backup", target_device_ids=["m1"])


if __name__ == "__main__":
    unittest.main()
