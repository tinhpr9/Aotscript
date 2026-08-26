import importlib.machinery
import importlib.util
import os
import re
import sys
import tempfile
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

    def test_source_contains_no_concrete_zeropoint_credentials(self):
        source = TOOLCHECK_PATH.read_text(encoding="utf-8")
        self.assertNotIn("ZP_FaceUnlock_0DLuB8BEqFpLM0bssjSPFqGwnRkgXm7p", source)
        self.assertNotIn("ZP_CookieChecker_gCvFnk1LOTPh2ysCcTcZhjCwuYVnwoYT", source)
        self.assertIsNone(
            re.search(r'ZP_(FaceUnlock|CookieChecker)_[A-Za-z0-9_]{10,}', source),
            "Toolcheck source must not embed concrete ZeroPoint API credentials",
        )

    def test_zeropoint_credentials_sourced_from_runtime_env(self):
        env = {
            "ZEROPOINT_FACE_API_KEY": "runtime-face-key-1",
            "ZEROPOINT_COOKIE_API_KEY": "runtime-cookie-key-2",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(self.tool.get_face_api_key(), "runtime-face-key-1")
            self.assertEqual(self.tool.get_cookie_api_key(), "runtime-cookie-key-2")

    def test_missing_face_credential_fails_safely_before_authenticated_request(self):
        env = {
            "ZEROPOINT_FACE_API_KEY": "",
            "FACE_API_KEY": "",
            "SHOUKO_ZEROPOINT_FACE_API_KEY": "",
        }
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(self.tool.requests, "get") as get_mock, \
             mock.patch.object(self.tool.requests, "post") as post_mock:
            self.tool.face_balance()
            result_clear = self.tool.force_clear_jobs()
            self.tool.face_unlock()
            self.tool.check_zeropoint_db()

        self.assertFalse(result_clear)
        get_mock.assert_not_called()
        post_mock.assert_not_called()

    def test_missing_cookie_credential_fails_safely_before_authenticated_request(self):
        env = {
            "ZEROPOINT_COOKIE_API_KEY": "",
            "COOKIE_API_KEY": "",
            "SHOUKO_ZEROPOINT_COOKIE_API_KEY": "",
        }
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(self.tool.requests, "get") as get_mock, \
             mock.patch.object(self.tool.requests, "post") as post_mock:
            self.tool.cookie_checker()
            self.tool.check_all_data_tong()

        get_mock.assert_not_called()
        post_mock.assert_not_called()

    def test_configured_credentials_pass_runtime_value_in_request_headers(self):
        env = {
            "ZEROPOINT_FACE_API_KEY": "runtime-face-token-abc",
            "ZEROPOINT_COOKIE_API_KEY": "runtime-cookie-token-xyz",
        }
        fake_balance_res = FakeResponse(200, {"effective": 10.5})
        fake_clear_res = FakeResponse(200, {"jobs": []})
        fake_cookie_res = FakeResponse(200, {"session_id": "sess-123"})
        fake_cookie_status = FakeResponse(200, {"status": "completed", "download_files": {}})

        with tempfile.TemporaryDirectory() as tmpdir:
            cookie_file = Path(tmpdir) / "Cookies.txt"
            cookie_file.write_text("user:pass:cookie123\n", encoding="utf-8")

            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(self.tool, "COOKIE_FILE", str(cookie_file)), \
                 mock.patch.object(self.tool, "BASE_DIR", tmpdir), \
                 mock.patch.object(self.tool.requests, "get", side_effect=[fake_balance_res, fake_clear_res, fake_cookie_status]) as get_mock, \
                 mock.patch.object(self.tool.requests, "post", side_effect=[fake_cookie_res]) as post_mock, \
                 mock.patch("time.sleep", return_value=None):
                self.tool.face_balance()
                self.tool.force_clear_jobs()
                self.tool.cookie_checker()

        # Check face_balance header
        self.assertEqual(
            get_mock.call_args_list[0].kwargs["headers"],
            {"X-API-Key": "runtime-face-token-abc"},
        )
        # Check force_clear_jobs header
        self.assertEqual(
            get_mock.call_args_list[1].kwargs["headers"],
            {"X-API-Key": "runtime-face-token-abc", "Content-Type": "application/json"},
        )
        # Check cookie_checker header
        self.assertEqual(
            post_mock.call_args_list[0].kwargs["headers"],
            {"X-API-Key": "runtime-cookie-token-xyz", "Content-Type": "application/json"},
        )

    def test_cookie_checker_mode3_dead_routes_direct_sheet_not_webhook(self):
        env = {
            "ZEROPOINT_COOKIE_API_KEY": "test-cookie-key",
            "SHOUKO_GOOGLE_SHEET_ID": "test-sheet-id",
            "SHOUKO_GOOGLE_ACCESS_TOKEN": "test-access-token",
        }
        fake_cookie_submit = FakeResponse(200, {"session_id": "sess-dead-1"})
        fake_cookie_status = FakeResponse(200, {
            "status": "completed",
            "download_files": {"invalid_cookies": 1},
        })
        fake_cookie_dl = FakeResponse(200)
        fake_cookie_dl.text = "deadcookie\n"
        fake_sheets_get = FakeResponse(200, {"values": []})
        fake_sheets_post = FakeResponse(200, {"updates": {"updatedRows": 1}})

        with tempfile.TemporaryDirectory() as tmpdir:
            cookie_file = Path(tmpdir) / "Cookies.txt"
            cookie_file.write_text("deaduser:deadpass:deadcookie\n", encoding="utf-8")
            found_file = Path(tmpdir) / "Ket_Qua_Tim_Duoc.txt"
            found_file.write_text("deaduser:deadpass:deadcookie\n", encoding="utf-8")

            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(self.tool, "COOKIE_FILE", str(cookie_file)), \
                 mock.patch.object(self.tool, "FOUND_RESULT_FILE", str(found_file)), \
                 mock.patch.object(self.tool, "BASE_DIR", tmpdir), \
                 mock.patch.object(self.tool, "SHEET_WEBHOOK", "https://script.google.com/macros/s/test/exec"), \
                 mock.patch.object(self.tool.requests, "get", side_effect=[fake_cookie_status, fake_cookie_dl, fake_sheets_get]) as get_mock, \
                 mock.patch.object(self.tool.requests, "post", side_effect=[fake_cookie_submit, fake_sheets_post]) as post_mock, \
                 mock.patch("time.sleep", return_value=None):
                self.tool.cookie_checker()

        # Verify sheets API was called and webhook was NOT called
        post_urls = [call.args[0] for call in post_mock.call_args_list]
        self.assertIn(f"{self.tool.COOKIE_URL}/submit", post_urls[0])
        self.assertIn("sheets.googleapis.com", post_urls[1])
        for url in post_urls:
            self.assertNotIn("script.google.com", url)

    def test_cookie_checker_live_and_face_webhook_routing(self):
        env = {
            "ZEROPOINT_COOKIE_API_KEY": "test-cookie-key",
        }
        fake_cookie_submit = FakeResponse(200, {"session_id": "sess-live-face"})
        fake_cookie_status = FakeResponse(200, {
            "status": "completed",
            "download_files": {"live_cookies": 1, "face": 1},
        })
        fake_cookie_dl_live = FakeResponse(200)
        fake_cookie_dl_live.text = "live_val\n"
        fake_cookie_dl_face = FakeResponse(200)
        fake_cookie_dl_face.text = "face_val\n"
        fake_webhook_res = FakeResponse(200, {"status": "ok"})

        webhook_url = "https://script.google.com/macros/s/test-webhook/exec"

        with tempfile.TemporaryDirectory() as tmpdir:
            cookie_file = Path(tmpdir) / "Cookies.txt"
            cookie_file.write_text("live_val\n", encoding="utf-8")
            found_file = Path(tmpdir) / "Ket_Qua_Tim_Duoc.txt"
            found_file.write_text("live_user:live_pw:live_val\nface_user:face_pw:face_val\n", encoding="utf-8")
            face_target = Path(tmpdir) / "Face_Target_File.txt"

            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(self.tool, "COOKIE_FILE", str(cookie_file)), \
                 mock.patch.object(self.tool, "FOUND_RESULT_FILE", str(found_file)), \
                 mock.patch.object(self.tool, "FACE_TARGET_CONFIG", str(face_target)), \
                 mock.patch.object(self.tool, "BASE_DIR", tmpdir), \
                 mock.patch.object(self.tool, "SHEET_WEBHOOK", webhook_url), \
                 mock.patch.object(self.tool.requests, "get", side_effect=[fake_cookie_status, fake_cookie_dl_live, fake_cookie_dl_face]) as get_mock, \
                 mock.patch.object(self.tool.requests, "post", side_effect=[fake_cookie_submit, fake_webhook_res, fake_webhook_res]) as post_mock, \
                 mock.patch("time.sleep", return_value=None):
                self.tool.cookie_checker()

        post_calls = post_mock.call_args_list
        # post 0: zeropoint submit
        self.assertEqual(post_calls[0].args[0], f"{self.tool.COOKIE_URL}/submit")
        # post 1: live accounts deleted via webhook
        self.assertEqual(post_calls[1].args[0], webhook_url)
        self.assertEqual(post_calls[1].kwargs["json"], {"delete_accounts": ["live_user:live_pw:live_val"]})
        # post 2: face accounts submitted via webhook
        self.assertEqual(post_calls[2].args[0], webhook_url)
        self.assertEqual(post_calls[2].kwargs["json"], {"accounts": ["face_user:face_pw:face_val"]})

    def test_error_logs_contain_no_secret_values(self):
        secret_face = "SUPER_SECRET_FACE_TOKEN_999"
        secret_cookie = "SUPER_SECRET_COOKIE_TOKEN_888"
        env = {
            "ZEROPOINT_FACE_API_KEY": secret_face,
            "ZEROPOINT_COOKIE_API_KEY": secret_cookie,
        }
        fake_err_res = FakeResponse(500, {"error": "internal error"})

        import io
        from contextlib import redirect_stdout, redirect_stderr

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        with tempfile.TemporaryDirectory() as tmpdir:
            cookie_file = Path(tmpdir) / "Cookies.txt"
            cookie_file.write_text("bad:data\n", encoding="utf-8")

            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(self.tool, "COOKIE_FILE", str(cookie_file)), \
                 mock.patch.object(self.tool, "BASE_DIR", tmpdir), \
                 mock.patch.object(self.tool.requests, "get", return_value=fake_err_res), \
                 mock.patch.object(self.tool.requests, "post", return_value=fake_err_res), \
                 redirect_stdout(stdout_buf), \
                 redirect_stderr(stderr_buf):
                self.tool.face_balance()
                self.tool.force_clear_jobs()
                self.tool.cookie_checker()

        output = stdout_buf.getvalue() + stderr_buf.getvalue()
        self.assertNotIn(secret_face, output)
        self.assertNotIn(secret_cookie, output)


if __name__ == "__main__":
    import unittest
    unittest.main()
