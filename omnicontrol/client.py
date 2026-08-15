"""AOT Hub HTTP Client.

Abstracts calls to the existing /aot/hub/api/state and /aot/hub/api/control.
No transport/gateway logic here. Only depends on config for URL and secret.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List

import urllib.request
import urllib.error

from .config import OmniConfig


class HubApiError(Exception):
    """Raised when the AOT Hub returns an error or non-200 status."""
    pass


@dataclass
class HubClient:
    config: OmniConfig

    def _request(self, path: str, method: str = "GET", payload: dict | None = None) -> Any:
        url = f"{self.config.aot_hub_base_url.rstrip('/')}{path}"
        headers = {
            "Accept": "application/json",
            "X-Telegram-Init-Data": self.config.aot_hub_api_secret,
        }
        
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        
        try:
            with urllib.request.urlopen(req, timeout=self.config.hub_request_timeout_seconds) as response:
                resp_data = response.read().decode("utf-8")
                parsed = json.loads(resp_data)
                if not parsed.get("ok"):
                    raise HubApiError(parsed.get("error", "Unknown error from Hub"))
                return parsed
        except urllib.error.HTTPError as e:
            try:
                resp_data = e.read().decode("utf-8")
                parsed = json.loads(resp_data)
                raise HubApiError(parsed.get("error", f"HTTP {e.code}"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise HubApiError(f"HTTP {e.code}")
        except urllib.error.URLError as e:
            raise HubApiError(f"Connection failed: {e.reason}")

    def get_state(self) -> Dict[str, Any]:
        """Fetch the current fleet state (/aot/hub/state)."""
        return self._request("/aot/hub/state")

    def control(self, kind: str, target_device_ids: List[str] | None = None) -> Dict[str, Any]:
        """Send a control command to the hub (/aot/hub/control)."""
        payload = {"kind": kind, "protocol": "fleet-batch-v1"}
        if target_device_ids is not None:
            payload["target_device_ids"] = target_device_ids
        return self._request("/aot/hub/control", method="POST", payload=payload)
