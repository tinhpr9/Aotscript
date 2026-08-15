"""Discord Gateway Adapter for AOT OmniControl.

Translates Discord slash commands into RouteRequests, handles user confirmations
via interactive buttons (Approve / Cancel), and renders responses.

This module acts as a contract/skeleton for Phase 1. Real Discord interaction
relies on a library like discord.py or interactions.py.
"""

from __future__ import annotations

import logging
from typing import Any

from omnicontrol.auth import CallerIdentity
from omnicontrol.commands import CommandDef
from omnicontrol.config import OmniConfig
from omnicontrol.router import CommandRouter, RouteRequest, RouterError

logger = logging.getLogger(__name__)


import time
import uuid

class DiscordGateway:
    def __init__(self, router: CommandRouter, config: OmniConfig):
        self.router = router
        self.config = config
        # Map of token -> {"caller": CallerIdentity, "command_name": str, "args": list[str], "expires": float}
        self.pending_requests: dict[str, dict] = {}

    def handle_slash_command(self, interaction: Any, command_name: str, args: list[str]) -> dict:
        """Handle an incoming slash command from Discord."""
        caller = self._extract_identity(interaction)
        
        req = RouteRequest(
            caller=caller,
            command_name=command_name,
            args=args
        )

        try:
            from omnicontrol.commands import COMMAND_REGISTRY
            cmd_def = COMMAND_REGISTRY.get(command_name)
            if not cmd_def:
                return self._format_error("Unknown command")
                
            if cmd_def.destructive:
                from omnicontrol.auth import is_authorized
                if not is_authorized(caller, self.config):
                    return self._format_error("Unauthorized")
                    
                now = time.time()
                # Cleanup expired
                expired_keys = [k for k, v in self.pending_requests.items() if now > v["expires"]]
                for k in expired_keys:
                    del self.pending_requests[k]
                    
                # Global capacity
                if len(self.pending_requests) >= 50:
                    return self._format_error("Global pending request limit reached. Please try again later.")
                    
                # Per-user capacity
                user_requests = sum(1 for v in self.pending_requests.values() if v["caller"].user_id == caller.user_id)
                if user_requests >= 5:
                    return self._format_error("Too many pending requests for your user. Please approve or wait for them to expire.")
                    
                token = uuid.uuid4().hex
                self.pending_requests[token] = {
                    "caller": caller,
                    "command_name": command_name,
                    "args": args,
                    "expires": now + 300  # 5 minutes expiry
                }
                return {
                    "content": f"⚠️ **Confirmation Required**\nAre you sure you want to execute `{command_name} {' '.join(args)}`?",
                    "components": [
                        {"type": 1, "components": [
                            {"type": 2, "style": 3, "label": "Approve", "custom_id": f"approve:{token}"},
                            {"type": 2, "style": 4, "label": "Cancel", "custom_id": f"cancel:{token}"}
                        ]}
                    ]
                }

            # Let the router do the validation and routing
            result = self.router.dispatch(req)
            return self._format_success(result)
        except RouterError as e:
            return self._format_error(str(e))
        except Exception as e:
            logger.exception("Unexpected error in Discord gateway")
            return self._format_error("Internal server error")

    def handle_button_click(self, interaction: Any, custom_id: str) -> dict:
        """Handle interactive buttons like Approve / Cancel.
        
        Args:
            interaction: The raw Discord interaction object.
            custom_id: The button identifier from Discord (e.g. "approve:<token>").
        """
        parts = custom_id.split(":", 1)
        action = parts[0]
        token = parts[1] if len(parts) > 1 else None

        if not token or token not in self.pending_requests:
            return self._format_error("Invalid, expired, or forged request token.")

        pending = self.pending_requests[token]
        
        # Prevent replay or double-click by deleting immediately
        del self.pending_requests[token]

        if time.time() > pending["expires"]:
            return self._format_error("Request has expired.")

        caller = self._extract_identity(interaction)
        if pending["caller"].user_id != caller.user_id:
            return self._format_error("You cannot confirm another user's request.")

        if action == "cancel":
            return {"content": "❌ Action cancelled."}
        elif action == "approve":
            try:
                # Re-dispatch with the stored authenticated state
                req = RouteRequest(
                    caller=pending["caller"],
                    command_name=pending["command_name"],
                    args=pending["args"]
                )
                result = self.router.dispatch(req)
                return self._format_success(result)
            except RouterError as e:
                return self._format_error(str(e))
            except Exception as e:
                logger.exception("Unexpected error in Discord gateway approve")
                return self._format_error("Internal server error")
        
        return self._format_error(f"Unknown button action: {action}")

    def _extract_identity(self, interaction: Any) -> CallerIdentity:
        """Map Discord interaction to transport-agnostic CallerIdentity."""
        # Stub implementation assuming typical discord.py-like structure
        user_id = str(getattr(interaction.user, "id", "unknown"))
        guild_id = str(getattr(interaction.guild, "id", "")) if getattr(interaction, "guild", None) else None
        channel_id = str(getattr(interaction.channel, "id", "")) if getattr(interaction, "channel", None) else None
        
        return CallerIdentity(
            user_id=user_id,
            gateway="discord",
            guild_id=guild_id,
            channel_id=channel_id,
        )

    def _format_success(self, result: dict) -> dict:
        """Format the router's JSON response into a Discord message."""
        content = result.get("message", "Success")
        # In a real app, we might build nice embeds here
        return {"content": f"✅ {content}\n```{result}```"}

    def _format_error(self, message: str) -> dict:
        """Format an error message for Discord."""
        return {"content": f"⚠️ **Error:** {message}"}
