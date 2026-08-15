"""Auth/permission layer for AOT OmniControl.

Centralised checks that apply regardless of transport.  The gateway adapters
call `AuthContext.check(...)` before passing any request to the Router.

Rules (all must pass):
  1. Transport context (guild/channel) is in the allow-list.
  2. User is in the operator allow-list.
  3. (Phase 2+) MFA / secondary confirmation for destructive commands.

No secrets, tokens, cookies, or session state are stored here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .config import OmniConfig


class AuthResult(str, Enum):
    OK = "ok"
    UNAUTHORIZED_USER = "unauthorized_user"
    UNAUTHORIZED_GUILD = "unauthorized_guild"
    UNAUTHORIZED_CHANNEL = "unauthorized_channel"
    GATEWAY_DISABLED = "gateway_disabled"


@dataclass(frozen=True)
class CallerIdentity:
    """Transport-agnostic caller descriptor.

    The gateway adapter fills this from Discord interaction / Telegram update.
    No raw tokens or init-data strings are stored here.
    """
    user_id: str          # opaque string from transport (Discord user ID, TG user ID)
    gateway: str          # "discord" | "telegram"
    guild_id: Optional[str] = None     # Discord guild / Telegram chat ID
    channel_id: Optional[str] = None   # Discord channel / Telegram thread


def check_auth(caller: CallerIdentity, config: OmniConfig) -> AuthResult:
    """Return AuthResult.OK if the caller is allowed to use OmniControl.

    Checks are performed in order; the first failure is returned.
    """
    # Gateway enabled?
    if caller.gateway == "discord" and not config.discord_gateway_enabled:
        return AuthResult.GATEWAY_DISABLED
    if caller.gateway == "telegram" and not config.telegram_gateway_enabled:
        return AuthResult.GATEWAY_DISABLED

    # Guild allow-list (Discord only)
    if caller.gateway == "discord":
        if (
            config.discord_allowed_guild_ids
            and caller.guild_id not in config.discord_allowed_guild_ids
        ):
            return AuthResult.UNAUTHORIZED_GUILD

        if (
            config.discord_allowed_channel_ids
            and caller.channel_id not in config.discord_allowed_channel_ids
        ):
            return AuthResult.UNAUTHORIZED_CHANNEL

    # Telegram: check admin user ID
    if caller.gateway == "telegram":
        if (
            config.telegram_admin_user_id
            and str(caller.user_id) != str(config.telegram_admin_user_id)
        ):
            return AuthResult.UNAUTHORIZED_USER

    # User allow-list (Discord)
    if caller.gateway == "discord":
        if (
            config.discord_allowed_user_ids
            and caller.user_id not in config.discord_allowed_user_ids
        ):
            return AuthResult.UNAUTHORIZED_USER

    return AuthResult.OK


def is_authorized(caller: CallerIdentity, config: OmniConfig) -> bool:
    """Convenience wrapper – returns True only when check_auth returns OK."""
    return check_auth(caller, config) == AuthResult.OK
