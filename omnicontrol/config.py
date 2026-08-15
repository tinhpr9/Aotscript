"""Config for AOT OmniControl – loaded entirely from environment variables.

No secrets, tokens, or device state are hard-coded here.
Call `load_config()` once at startup; the returned `OmniConfig` object is
passed through to every subsystem.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class OmniConfig:
    # ── AOT Hub ────────────────────────────────────────────────────────────
    # Base URL of the Cloudflare Worker, e.g. "https://your-worker.workers.dev"
    aot_hub_base_url: str

    # Shared secret used in X-Telegram-Init-Data / equivalent hub auth header.
    # In Discord mode the adapter presents a different credential;
    # the Hub client picks the right header based on gateway type.
    aot_hub_api_secret: str          # never logged, never sent to Discord/TG

    # ── Auth allow-lists ───────────────────────────────────────────────────
    # Comma-separated Discord guild IDs that are allowed to issue commands.
    discord_allowed_guild_ids: frozenset[str] = field(default_factory=frozenset)

    # Comma-separated Discord channel IDs (optional – empty = any channel in
    # an allowed guild).
    discord_allowed_channel_ids: frozenset[str] = field(default_factory=frozenset)

    # Comma-separated Discord user IDs that have operator permission.
    discord_allowed_user_ids: frozenset[str] = field(default_factory=frozenset)

    # Telegram admin user ID (if Telegram gateway is enabled in Phase 2+).
    telegram_admin_user_id: Optional[str] = None

    # ── Rate limiting ──────────────────────────────────────────────────────
    # Max commands per user per minute (applied per-user across transports).
    rate_limit_per_user_per_minute: int = 20

    # Max destructive commands per user per minute.
    rate_limit_destructive_per_user_per_minute: int = 3

    # ── Feature flags ─────────────────────────────────────────────────────
    # Whether the Telegram gateway is active (Phase 1 = False).
    telegram_gateway_enabled: bool = False

    # Whether the Discord gateway is active.
    discord_gateway_enabled: bool = True

    # ── Timeouts / limits ─────────────────────────────────────────────────
    hub_request_timeout_seconds: float = 30.0


def _csv_frozenset(raw: str) -> frozenset[str]:
    return frozenset(v.strip() for v in raw.split(",") if v.strip())


def load_config(environ: Optional[dict] = None) -> OmniConfig:
    """Load OmniConfig from environment variables (or the provided dict).

    Required variables:
        OMNI_HUB_BASE_URL       – AOT Hub base URL
        OMNI_HUB_API_SECRET     – Hub API credential

    Optional variables (with defaults):
        OMNI_DISCORD_GUILD_IDS         – comma-separated guild IDs
        OMNI_DISCORD_CHANNEL_IDS       – comma-separated channel IDs
        OMNI_DISCORD_USER_IDS          – comma-separated user IDs
        OMNI_TELEGRAM_ADMIN_USER_ID    – single Telegram user ID
        OMNI_RATE_LIMIT_PER_MINUTE     – integer (default 20)
        OMNI_RATE_LIMIT_DESTRUCTIVE    – integer (default 3)
        OMNI_TELEGRAM_ENABLED          – "1" to enable Telegram gateway
        OMNI_DISCORD_ENABLED           – "0" to disable Discord gateway
        OMNI_HUB_TIMEOUT               – float seconds (default 30)
    """
    env = environ if environ is not None else os.environ

    hub_url = env.get("OMNI_HUB_BASE_URL", "").strip()
    if not hub_url:
        raise EnvironmentError("OMNI_HUB_BASE_URL is required but not set.")

    hub_secret = env.get("OMNI_HUB_API_SECRET", "").strip()
    if not hub_secret:
        raise EnvironmentError("OMNI_HUB_API_SECRET is required but not set.")

    return OmniConfig(
        aot_hub_base_url=hub_url.rstrip("/"),
        aot_hub_api_secret=hub_secret,
        discord_allowed_guild_ids=_csv_frozenset(
            env.get("OMNI_DISCORD_GUILD_IDS", "")
        ),
        discord_allowed_channel_ids=_csv_frozenset(
            env.get("OMNI_DISCORD_CHANNEL_IDS", "")
        ),
        discord_allowed_user_ids=_csv_frozenset(
            env.get("OMNI_DISCORD_USER_IDS", "")
        ),
        telegram_admin_user_id=env.get("OMNI_TELEGRAM_ADMIN_USER_ID", "").strip() or None,
        rate_limit_per_user_per_minute=int(
            env.get("OMNI_RATE_LIMIT_PER_MINUTE", "20")
        ),
        rate_limit_destructive_per_user_per_minute=int(
            env.get("OMNI_RATE_LIMIT_DESTRUCTIVE", "3")
        ),
        telegram_gateway_enabled=env.get("OMNI_TELEGRAM_ENABLED", "0").strip() == "1",
        discord_gateway_enabled=env.get("OMNI_DISCORD_ENABLED", "1").strip() != "0",
        hub_request_timeout_seconds=float(
            env.get("OMNI_HUB_TIMEOUT", "30")
        ),
    )
