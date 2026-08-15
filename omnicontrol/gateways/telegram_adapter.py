"""Telegram Gateway Adapter for AOT OmniControl.

Translates Telegram updates into RouteRequests and formats responses.
Phase 1: Contract only. The actual Telegram bot implementation currently
lives in the Cloudflare Worker, but may be migrated to use this router
in the future if desired.
"""

from __future__ import annotations

import logging
from typing import Any

from omnicontrol.auth import CallerIdentity
from omnicontrol.router import CommandRouter, RouteRequest, RouterError
from omnicontrol.config import OmniConfig

logger = logging.getLogger(__name__)


class TelegramGateway:
    def __init__(self, router: CommandRouter, config: OmniConfig):
        self.router = router
        self.config = config

    def handle_message(self, update: Any) -> dict:
        """Handle an incoming Telegram message (e.g. from a webhook).
        
        Args:
            update: The raw Telegram update object.
            
        Returns:
            A dictionary containing the response payload to send back.
        """
        # Contract only. No actual implementation for Phase 1.
        return self._format_error("Telegram gateway not fully implemented in Phase 1.")

    def _extract_identity(self, update: Any) -> CallerIdentity:
        """Map Telegram update to transport-agnostic CallerIdentity."""
        # Stub implementation
        user_id = str(update.get("message", {}).get("from", {}).get("id", "unknown"))
        chat_id = str(update.get("message", {}).get("chat", {}).get("id", ""))
        
        return CallerIdentity(
            user_id=user_id,
            gateway="telegram",
            guild_id=chat_id,  # We use guild_id field for chat_id
            channel_id=None,
        )

    def _format_success(self, result: dict) -> dict:
        content = result.get("message", "Success")
        return {"text": f"✅ {content}"}

    def _format_error(self, message: str) -> dict:
        return {"text": f"⚠️ {message}"}
