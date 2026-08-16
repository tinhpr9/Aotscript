import sys
import os
import unittest
from unittest.mock import patch, MagicMock

import agent
import Updatedelta

class TestAutoUpdateSystem(unittest.TestCase):
    def setUp(self):
        self.patcher_print = patch('builtins.print')
        self.mock_print = self.patcher_print.start()
        
        self.patcher_os_path = patch('os.path.exists', return_value=True)
        self.mock_os_path = self.patcher_os_path.start()
        
        self.patcher_os_makedirs = patch('os.makedirs')
        self.mock_os_makedirs = self.patcher_os_makedirs.start()
        
        self.patcher_retrieve = patch('urllib.request.urlretrieve')
        self.mock_retrieve = self.patcher_retrieve.start()
        
        self.patcher_subprocess = patch('subprocess.run')
        self.mock_subprocess = self.patcher_subprocess.start()
        
        self.patcher_sleep = patch('time.sleep')
        self.mock_sleep = self.patcher_sleep.start()
        
        self.patcher_log = patch('agent.log_error')
        self.mock_log = self.patcher_log.start()

    def tearDown(self):
        patch.stopall()

    @patch('urllib.request.urlopen')
    def test_finds_delta_release_when_latest_is_worker(self, mock_urlopen):
        worker_release = {
            "tag_name": "worker-v2026.08.14.03",
            "assets": [{"name": f"file{i}.py"} for i in range(17)]
        }
        no_delta_release = {
            "tag_name": "v1.0",
            "assets": [{"name": "source.tar.gz"}]
        }
        delta_release = {
            "tag_name": "delta-v2",
            "assets": [{"name": "app.apk", "browser_download_url": "http://example.com/app.apk"}]
        }
        
        mock_response = MagicMock()
        import json
        mock_response.read.return_value = json.dumps([worker_release, no_delta_release, delta_release]).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        mock_result = MagicMock()
        mock_result.stdout = "Success"
        mock_result.returncode = 0
        self.mock_subprocess.return_value = mock_result

        agent.auto_update_system()

        self.mock_retrieve.assert_called_once_with("http://example.com/app.apk", unittest.mock.ANY)
        self.mock_subprocess.assert_called_once()
        
        success_printed = any("CẬP NHẬT DELTA HOÀN TẤT CHO THIẾT BỊ NÀY!" in call.args[0] for call in self.mock_print.call_args_list)
        self.assertTrue(success_printed)
        self.mock_log.assert_not_called()

        # Test Updatedelta semantics
        self.mock_retrieve.reset_mock()
        self.mock_print.reset_mock()
        Updatedelta.auto_update_system()
        self.mock_retrieve.assert_called_once()
        success_printed2 = any("TOÀN BỘ HỆ THỐNG ĐÃ ĐƯỢC CẬP NHẬT TỰ ĐỘNG 100%" in call.args[0] for call in self.mock_print.call_args_list)
        self.assertTrue(success_printed2)

    @patch('urllib.request.urlopen')
    def test_no_delta_release_fails(self, mock_urlopen):
        mock_response = MagicMock()
        import json
        mock_response.read.return_value = json.dumps([{"tag_name": "worker-v1", "assets": [{"name": "a.py"}]}]).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        agent.auto_update_system()

        self.assertTrue(self.mock_log.called)
        self.assertIn("Không tìm thấy Delta release", str(self.mock_log.call_args[0][1]))
        self.mock_retrieve.assert_not_called()

    @patch('urllib.request.urlopen')
    def test_download_fail_fails(self, mock_urlopen):
        mock_response = MagicMock()
        import json
        mock_response.read.return_value = json.dumps([{"tag_name": "delta-v1", "assets": [{"name": "app.apk", "browser_download_url": "url"}]}]).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        self.mock_retrieve.side_effect = Exception("Download timeout")

        agent.auto_update_system()

        self.assertTrue(self.mock_log.called)
        self.assertIn("Download timeout", str(self.mock_log.call_args[0][1]))
        self.mock_subprocess.assert_not_called()

    @patch('urllib.request.urlopen')
    def test_pm_install_fail_fails(self, mock_urlopen):
        mock_response = MagicMock()
        import json
        mock_response.read.return_value = json.dumps([{"tag_name": "delta-v1", "assets": [{"name": "app.apk", "browser_download_url": "url"}]}]).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        mock_result = MagicMock()
        mock_result.stdout = "Failure"
        mock_result.stderr = "INSTALL_FAILED_INVALID_APK"
        mock_result.returncode = 1
        self.mock_subprocess.return_value = mock_result

        agent.auto_update_system()

        self.assertTrue(self.mock_log.called)
        self.assertIn("không có APK nào được cài đặt thành công", str(self.mock_log.call_args[0][1]))

    @patch('urllib.request.urlopen')
    @patch('os.walk')
    @patch('zipfile.ZipFile')
    def test_zero_apk_installed_from_zip_fails(self, mock_zipfile, mock_walk, mock_urlopen):
        mock_response = MagicMock()
        import json
        mock_response.read.return_value = json.dumps([{"tag_name": "delta-v1", "assets": [{"name": "app.zip", "browser_download_url": "url"}]}]).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        mock_walk.return_value = [('/extracted', ('dir',), ('readme.txt', 'image.png'))]

        agent.auto_update_system()

        self.assertTrue(self.mock_log.called)
        self.assertIn("không có APK nào được cài đặt thành công", str(self.mock_log.call_args[0][1]))
        self.mock_subprocess.assert_not_called()

if __name__ == '__main__':
    unittest.main()
