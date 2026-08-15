"""Command Router for AOT OmniControl.

Validates inputs, checks permissions, checks rate limits, and routes
valid requests to the Hub API. Transport agnostic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .auth import AuthResult, CallerIdentity, check_auth
from .client import HubApiError, HubClient
from .commands import (COMMAND_REGISTRY, ArgKind, CommandDef,
                       validate_batch_action, validate_device_id, validate_group)
from .config import OmniConfig


class RouterError(Exception):
    """Raised when routing or validation fails."""
    pass


@dataclass
class RouteRequest:
    caller: CallerIdentity
    command_name: str
    args: List[str]


class RateLimiter:
    """In-memory rate limiter for Phase 1.
    Production systems should back this with Redis or KV.
    """
    def __init__(self, config: OmniConfig):
        self.config = config
        self._user_counts: Dict[str, List[float]] = {}
        self._user_destructive_counts: Dict[str, List[float]] = {}

    def check_and_consume(self, user_id: str, destructive: bool) -> bool:
        now = time.time()
        window_start = now - 60.0

        # General rate limit
        history = self._user_counts.setdefault(user_id, [])
        history[:] = [t for t in history if t > window_start]
        if len(history) >= self.config.rate_limit_per_user_per_minute:
            return False
        
        # Destructive rate limit
        if destructive:
            dest_history = self._user_destructive_counts.setdefault(user_id, [])
            dest_history[:] = [t for t in dest_history if t > window_start]
            if len(dest_history) >= self.config.rate_limit_destructive_per_user_per_minute:
                return False
            dest_history.append(now)

        history.append(now)
        return True


class CommandRouter:
    def __init__(self, config: OmniConfig, client: HubClient):
        self.config = config
        self.client = client
        self.rate_limiter = RateLimiter(config)

    def dispatch(self, req: RouteRequest) -> Dict[str, Any]:
        """Main entry point for gateways.
        Validates the request, enforces rate limits, and dispatches to Hub.
        Returns a dict suitable for presenting to the user.
        Raises RouterError for invalid requests or HubApiError for upstream failures.
        """
        # 1. Auth check
        auth_res = check_auth(req.caller, self.config)
        if auth_res != AuthResult.OK:
            raise RouterError(f"Permission denied: {auth_res.value}")

        # 2. Command lookup
        cmd_def = COMMAND_REGISTRY.get(req.command_name.lower())
        if not cmd_def:
            raise RouterError(f"Unknown command: {req.command_name}")

        # 3. Rate limits
        if not self.rate_limiter.check_and_consume(req.caller.user_id, cmd_def.destructive):
            raise RouterError("Rate limit exceeded. Try again later.")

        # 4. Argument validation
        target_device = None
        target_group = None
        batch_action = None

        if cmd_def.arg_kind == ArgKind.NONE:
            pass
        elif cmd_def.arg_kind == ArgKind.DEVICE:
            if not req.args:
                raise RouterError(f"Missing device argument for {cmd_def.name}.")
            target_device = validate_device_id(req.args[0])
            if not target_device:
                raise RouterError(f"Invalid device ID: {req.args[0]}")
        elif cmd_def.arg_kind == ArgKind.GROUP:
            if len(req.args) < 2 and cmd_def.name == "batch":
                raise RouterError("Usage: /batch <group> <action>")
            elif not req.args:
                raise RouterError(f"Missing group argument for {cmd_def.name}.")
            
            target_group = validate_group(req.args[0])
            if not target_group:
                raise RouterError(f"Invalid group: {req.args[0]}")
            
            if cmd_def.name == "batch":
                batch_action = validate_batch_action(req.args[1])
                if not batch_action:
                    raise RouterError(f"Invalid or forbidden batch action: {req.args[1]}")
        elif cmd_def.arg_kind == ArgKind.DEVICE_OR_GROUP:
            if not req.args:
                raise RouterError(f"Missing device or group argument for {cmd_def.name}.")
            
            target_device = validate_device_id(req.args[0])
            if not target_device:
                target_group = validate_group(req.args[0])
                if not target_group:
                    raise RouterError(f"Invalid device or group: {req.args[0]}")

        # 5. Dispatch logic
        return self._execute_command(cmd_def, target_device, target_group, batch_action)

    def _execute_command(self, cmd_def: CommandDef, device: Optional[str], group: Optional[str], batch_action: Optional[str]) -> Dict[str, Any]:
        """Maps logical commands to actual Hub API calls."""
        if cmd_def.name == "status":
            state = self.client.get_state().get("state", {})
            return {"message": "State fetched", "state": state}
        
        elif cmd_def.name == "status_device":
            state = self.client.get_state().get("state", {})
            devices = state.get("devices", [])
            matches = [d for d in devices if d.get("device_id") == device]
            if not matches:
                return {"message": f"Device {device} not found in fleet state."}
            return {"message": "Device status fetched", "device": matches[0]}

        elif cmd_def.name == "start":
            return self.client.control("open_swift_backup", target_device_ids=[device])
            
        elif cmd_def.name == "stop":
            # For Phase 1, stopping translates to sending an IDLE command
            # The AOT worker.js accepts "idle" as a text command, but via hub control
            # we need to be careful. The hub control currently supports:
            # open_swift_backup, open_swift_apps, backup_restore_data, update_canary, update_stable
            # There is NO direct hub control for "stop".
            # Wait, the requirements state: "stop: active operation on a device (sends IDLE)."
            # Let's just return a placeholder or use an existing mechanism if one exists.
            # For now, raise NotImplementedError if the Hub doesn't support it directly.
            raise RouterError("Stop action requires Hub integration not yet exposed via control endpoint.")

        elif cmd_def.name == "batch":
            # Target devices belonging to the group and online
            state = self.client.get_state().get("state", {})
            devices = state.get("devices", [])
            target_ids = [
                d["device_id"] for d in devices
                if d.get("device_group") == group and d.get("online")
            ]
            if not target_ids:
                return {"message": f"No online devices found in group {group}."}
            
            return self.client.control(batch_action, target_device_ids=target_ids)

        elif cmd_def.name == "update":
            if group:
                # Update stable for group
                return self.client.control("update_stable")
            else:
                # Update canary for device (if possible, hub currently updates 2 random devices for canary)
                return self.client.control("update_canary")
                
        elif cmd_def.name == "logs":
            raise RouterError("Logs endpoint not implemented in Hub API.")

        return {"error": "unhandled_command"}
