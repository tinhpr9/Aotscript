import importlib.machinery
import importlib.util
import os
import sys
import types
from pathlib import Path
from unittest import TestCase, mock
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
TOOLCHECK_PATH = ROOT / "Toolcheck"


def load_toolcheck():
    fake_requests = types.SimpleNamespace(
        get=mock.Mock(),
        post=mock.Mock(),
        Session=mock.Mock(),
        utils=types.SimpleNamespace(quote=quote),
    )
    loader = importlib.machinery.SourceFileLoader("toolcheck_direct_sheet_test", str(TOOLCHECK_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"requests": fake_requests}):
        loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data


class ToolcheckDirectSheetTests(TestCase):
    def setUp(self):
        self.tool = load_toolcheck()

    def test_invalid_is_dead_not_live(self):
        self.assertEqual(self.tool.classify_cookie_result_type("invalid"), "dead")
        self.assertEqual(self.tool.classify_cookie_result_type("invalid_cookies"), "dead")
        self.assertEqual(self.tool.classify_cookie_result_type("valid"), "live")
        self.assertEqual(self.tool.classify_cookie_result_type("live_cookies"), "live")

    def test_direct_sheet_sync_dedupes_username_and_never_uses_webhook(self):
        env = {
            "SHOUKO_GOOGLE_SHEET_ID": "sheet-test-id",
            "SHOUKO_GOOGLE_SHEET_TAB": "Trang tính1",
            "SHOUKO_GOOGLE_ACCESS_TOKEN": "test-access-token",
        }
        existing = FakeResponse(200, {"values": [["ExistingUser:pw:cookie-old"]]})
        appended = FakeResponse(200, {"updates": {"updatedRows": 1}})

        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(self.tool.requests, "get", return_value=existing) as get_mock, \
             mock.patch.object(self.tool.requests, "post", return_value=appended) as post_mock:
            ok = self.tool.sync_dead_accounts_direct([
                "ExistingUser:newpw:newcookie",
                "NewUser:pw:newcookie",
                "newuser:pw:duplicate-case-variant",
            ])

        self.assertTrue(ok)
        self.assertEqual(get_mock.call_count, 1)
        self.assertEqual(post_mock.call_count, 1)

        post_url = post_mock.call_args.args[0]
        self.assertIn("sheets.googleapis.com", post_url)
        self.assertNotIn("script.google.com", post_url)
        self.assertEqual(
            post_mock.call_args.kwargs["json"]["values"],
            [["NewUser:pw:newcookie"]],
        )

    def test_direct_sheet_sync_fails_closed_without_auth(self):
        env = {
            "SHOUKO_GOOGLE_SHEET_ID": "sheet-test-id",
            "SHOUKO_GOOGLE_SHEET_TAB": "Trang tính1",
            "SHOUKO_GOOGLE_ACCESS_TOKEN": "",
            "SHOUKO_GOOGLE_CLIENT_ID": "",
            "SHOUKO_GOOGLE_CLIENT_SECRET": "",
            "SHOUKO_GOOGLE_REFRESH_TOKEN": "",
        }
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(self.tool.requests, "get") as get_mock, \
             mock.patch.object(self.tool.requests, "post") as post_mock:
            ok = self.tool.sync_dead_accounts_direct(["DeadUser:pw:cookie"])

        self.assertFalse(ok)
        get_mock.assert_not_called()
        post_mock.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
