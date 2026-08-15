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


class DiscordGateway:
    def __init__(self, router: CommandRouter, config: OmniConfig):
        self.router = router
        self.config = config

    def handle_slash_command(self, interaction: Any, command_name: str, args: list[str]) -> dict:
        """Handle an incoming slash command from Discord.
        
        Args:
            interaction: The raw Discord interaction object.
            command_name: The name of the slash command (e.g. "status").
            args: Positional string arguments parsed from the slash command.
            
        Returns:
            A dictionary containing the response payload to send to Discord.
        """
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
                return {
                    "content": f"⚠️ **Confirmation Required**\nAre you sure you want to execute `{command_name} {' '.join(args)}`?",
                    "components": [
                        {"type": 1, "components": [
                            {"type": 2, "style": 3, "label": "Approve", "custom_id": f"approve"},
                            {"type": 2, "style": 4, "label": "Cancel", "custom_id": "cancel"}
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

    def handle_button_click(self, interaction: Any, action: str, state: dict) -> dict:
        """Handle interactive buttons like Approve / Cancel / Retry.
        
        Args:
            interaction: The raw Discord interaction object.
            action: The button identifier (e.g. "approve", "cancel").
            state: Serialised state attached to the message.
        """
        if action == "cancel":
            return {"content": "❌ Action cancelled."}
        elif action == "approve":
            try:
                caller = self._extract_identity(interaction)
                req = RouteRequest(
                    caller=caller,
                    command_name=state.get("command_name", ""),
                    args=state.get("args", [])
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
