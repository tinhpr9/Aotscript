import json
import unittest
from unittest import mock
import urllib.error

from omnicontrol.client import HubClient, HubApiError
from omnicontrol.config import OmniConfig


class TestHubClient(unittest.TestCase):
    def setUp(self):
        self.config = OmniConfig(
            aot_hub_base_url="https://hub.example.com/",
            aot_hub_api_secret="my-super-secret"
        )
        self.client = HubClient(config=self.config)

    @mock.patch("urllib.request.urlopen")
    def test_get_state_public_route_and_auth(self, mock_urlopen):
        mock_response = mock.Mock()
        mock_response.read.return_value = b'{"ok": true, "state": {"devices": []}}'
        mock_urlopen.return_value.__enter__.return_value = mock_response

        res = self.client.get_state()
        
        self.assertEqual(res, {"ok": True, "state": {"devices": []}})
        
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "https://hub.example.com/aot/hub/api/state")
        self.assertEqual(req.get_header("Authorization"), "Bearer my-super-secret")
        self.assertEqual(req.method, "GET")

    @mock.patch("urllib.request.urlopen")
    def test_control_public_route_and_auth(self, mock_urlopen):
        mock_response = mock.Mock()
        mock_response.read.return_value = b'{"ok": true, "batch": {}}'
        mock_urlopen.return_value.__enter__.return_value = mock_response

        res = self.client.control("update_stable", ["marmot"])
        
        self.assertEqual(res, {"ok": True, "batch": {}})
        
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "https://hub.example.com/aot/hub/api/control")
        self.assertEqual(req.get_header("Authorization"), "Bearer my-super-secret")
        self.assertEqual(req.method, "POST")
        
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["kind"], "update_stable")
        self.assertEqual(body["target_device_ids"], ["marmot"])

    @mock.patch("urllib.request.urlopen")
    def test_hub_error_fail_closed(self, mock_urlopen):
        # Simulate unauthorized HTTP 401
        error = urllib.error.HTTPError(
            "https://hub.example.com/aot/hub/api/state", 401, "Unauthorized", {}, None
        )
        error.read = mock.Mock(return_value=b'{"ok": false, "error": "hub_unauthorized"}')
        mock_urlopen.side_effect = error

        with self.assertRaises(HubApiError) as ctx:
            self.client.get_state()
        
        self.assertIn("hub_unauthorized", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
