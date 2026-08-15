import time
import unittest
from unittest import mock

from omnicontrol.auth import CallerIdentity
from omnicontrol.config import OmniConfig
from omnicontrol.router import CommandRouter, RouteRequest
from omnicontrol.gateways.discord_adapter import DiscordGateway


class MockInteraction:
    def __init__(self, user_id, guild_id=None, channel_id=None):
        self.user = mock.Mock()
        self.user.id = user_id
        
        if guild_id:
            self.guild = mock.Mock()
            self.guild.id = guild_id
        else:
            self.guild = None
            
        if channel_id:
            self.channel = mock.Mock()
            self.channel.id = channel_id
        else:
            self.channel = None


class TestDiscordGateway(unittest.TestCase):
    def setUp(self):
        self.config = OmniConfig(
            aot_hub_base_url="http://test", 
            aot_hub_api_secret="secret",
            discord_gateway_enabled=True,
            discord_allowed_user_ids=["user_123"], discord_allowed_guild_ids=["guild_1"], discord_allowed_channel_ids=["channel_1"]
        )
        self.mock_router = mock.Mock(spec=CommandRouter)
        self.gateway = DiscordGateway(router=self.mock_router, config=self.config)
        self.interaction = MockInteraction(user_id="user_123", guild_id="guild_1", channel_id="channel_1")

    def test_no_dispatch_before_approve_for_destructive(self):
        # Action is destructive (like 'stop')
        res = self.gateway.handle_slash_command(self.interaction, "batch", ["marmot"])
        
        # Router should NOT be called
        self.mock_router.dispatch.assert_not_called()
        
        # Should return a confirmation UI
        self.assertIn("Confirmation Required", res["content"])
        self.assertEqual(len(self.gateway.pending_requests), 1)
        
        token = list(self.gateway.pending_requests.keys())[0]
        self.assertIn(token, res["components"][0]["components"][0]["custom_id"])

    def test_approve_dispatches_exactly_once(self):
        self.mock_router.dispatch.return_value = {"message": "Stopped"}
        
        # Initiate slash command
        res = self.gateway.handle_slash_command(self.interaction, "batch", ["marmot"])
        token = list(self.gateway.pending_requests.keys())[0]
        
        # Click Approve
        custom_id = f"approve:{token}"
        btn_res = self.gateway.handle_button_click(self.interaction, custom_id)
        
        # Should dispatch
        self.mock_router.dispatch.assert_called_once()
        self.assertIn("Stopped", btn_res["content"])
        
        # Token should be deleted
        self.assertNotIn(token, self.gateway.pending_requests)
        
        # Replay should be rejected
        btn_res2 = self.gateway.handle_button_click(self.interaction, custom_id)
        self.assertIn("Invalid, expired, or forged request token", btn_res2["content"])
        self.mock_router.dispatch.assert_called_once() # Still only 1 call

    def test_cancel_does_not_dispatch(self):
        self.gateway.handle_slash_command(self.interaction, "batch", ["marmot"])
        token = list(self.gateway.pending_requests.keys())[0]
        
        # Click Cancel
        custom_id = f"cancel:{token}"
        btn_res = self.gateway.handle_button_click(self.interaction, custom_id)
        
        # Should NOT dispatch
        self.mock_router.dispatch.assert_not_called()
        self.assertIn("cancelled", btn_res["content"])
        
        # Token should be deleted
        self.assertNotIn(token, self.gateway.pending_requests)

    def test_forged_token_rejected(self):
        self.gateway.handle_slash_command(self.interaction, "batch", ["marmot"])
        
        # Click Approve with forged token
        custom_id = "approve:forged_token"
        btn_res = self.gateway.handle_button_click(self.interaction, custom_id)
        
        self.mock_router.dispatch.assert_not_called()
        self.assertIn("Invalid, expired, or forged request token", btn_res["content"])

    def test_expired_token_rejected(self):
        self.gateway.handle_slash_command(self.interaction, "batch", ["marmot"])
        token = list(self.gateway.pending_requests.keys())[0]
        
        # Force expiration
        self.gateway.pending_requests[token]["expires"] = time.time() - 10
        
        custom_id = f"approve:{token}"
        btn_res = self.gateway.handle_button_click(self.interaction, custom_id)
        
        self.mock_router.dispatch.assert_not_called()
        self.assertIn("Request has expired", btn_res["content"])
        self.assertNotIn(token, self.gateway.pending_requests)

    def test_another_user_cannot_approve(self):
        self.gateway.handle_slash_command(self.interaction, "batch", ["marmot"])
        token = list(self.gateway.pending_requests.keys())[0]
        
        # Another user tries to approve
        evil_interaction = MockInteraction(user_id="evil_999", guild_id="guild_1", channel_id="channel_1")
        custom_id = f"approve:{token}"
        btn_res = self.gateway.handle_button_click(evil_interaction, custom_id)
        
        self.mock_router.dispatch.assert_not_called()
        self.assertIn("cannot confirm another user's request", btn_res["content"])
        
        # Token should still be deleted
        self.assertNotIn(token, self.gateway.pending_requests)

    def test_non_destructive_dispatches_immediately(self):
        self.mock_router.dispatch.return_value = {"message": "State fetched"}
        
        # Action is non-destructive (like 'status')
        res = self.gateway.handle_slash_command(self.interaction, "status", [])
        
        # Router should be called immediately
        self.mock_router.dispatch.assert_called_once()
        self.assertIn("State fetched", res["content"])
        self.assertEqual(len(self.gateway.pending_requests), 0)


    def test_cleanup_expired_requests(self):
        # Add an expired request
        expired_token = "expired_123"
        self.gateway.pending_requests[expired_token] = {
            "caller": self.interaction,
            "command_name": "batch",
            "args": ["m123"],
            "expires": time.time() - 100
        }
        
        # Dispatch a new destructive command
        self.gateway.handle_slash_command(self.interaction, "batch", ["marmot"])
        
        self.assertNotIn(expired_token, self.gateway.pending_requests)

    def test_user_limit_rejection(self):
        # Fill user quota
        for i in range(5):
            self.gateway.pending_requests[f"token_{i}"] = {
                "caller": CallerIdentity(user_id="user_123", gateway="discord", guild_id="guild_1", channel_id="channel_1"),
                "command_name": "batch",
                "args": ["m123"],
                "expires": time.time() + 300
            }
        
        res = self.gateway.handle_slash_command(self.interaction, "batch", ["marmot"])
            
        self.assertIn("Too many pending requests for your user", res["content"])

    def test_global_limit_rejection(self):
        # Fill global quota
        for i in range(50):
            self.gateway.pending_requests[f"global_{i}"] = {
                "caller": CallerIdentity(user_id=f"user_{i}", gateway="discord", guild_id="guild_1", channel_id="channel_1"),
                "command_name": "batch",
                "args": ["m123"],
                "expires": time.time() + 300
            }
            
        res = self.gateway.handle_slash_command(self.interaction, "batch", ["marmot"])
            
        self.assertIn("Global pending request limit reached", res["content"])

if __name__ == "__main__":
    unittest.main()
