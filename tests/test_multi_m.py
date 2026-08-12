import unittest
import os
import sys
import types
from unittest.mock import patch
from io import StringIO
import re

# Nạp Toolcheck dưới dạng module
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from unittest.mock import MagicMock

with open(os.path.join(ROOT, "Toolcheck"), "r", encoding="utf-8") as f:
    code = f.read()

Toolcheck = types.ModuleType("Toolcheck")
with patch.dict('sys.modules', {'requests': MagicMock()}):
    exec(code, Toolcheck.__dict__)

class TestMultiM(unittest.TestCase):
    def setUp(self):
        # Thiết lập các thư mục và file giả định
        self.test_dir = os.path.join(ROOT, "test_shouko_temp")
        os.makedirs(self.test_dir, exist_ok=True)
        Toolcheck.BASE_DIR = self.test_dir
        Toolcheck.ACC_FILE_PATH = os.path.join(self.test_dir, "acc.txt")
        Toolcheck.DB_COOKIES_FILE = os.path.join(self.test_dir, "Data_Tong_Cookies.txt")
        Toolcheck.FOUND_RESULT_FILE = os.path.join(self.test_dir, "Ket_Qua_Tim_Duoc.txt")
        Toolcheck.COOKIE_FILE = os.path.join(self.test_dir, "Cookies.txt")

        # Fake db cookies
        with open(Toolcheck.DB_COOKIES_FILE, "w", encoding="utf-8") as f:
            f.write("user1:pass1:cookie1\n")
            f.write("user2:pass2:cookie2\n")
            f.write("user3:pass3:cookie3\n")
            f.write("user4:pass4:cookie4\n")
            f.write("m123:password:cookie123\n")
        
        # Fake acc.txt
        with open(Toolcheck.ACC_FILE_PATH, "w", encoding="utf-8") as f:
            f.write("m88\n")
            f.write("m123:password\n") # Fake header
            f.write("user1:pass1\n")
            f.write("user2:pass2\n")
            f.write("M91 \n")
            f.write("user2:pass2\n") # duplicated user in m91
            f.write("user3:pass3\n")
            f.write("m105\n")
            # m105 empty
            f.write("m106\n")
            f.write("user4:pass4\n")

    def tearDown(self):
        for f in ["acc.txt", "Data_Tong_Cookies.txt", "Ket_Qua_Tim_Duoc.txt", "Cookies.txt"]:
            path = os.path.join(self.test_dir, f)
            if os.path.exists(path):
                os.remove(path)
        os.rmdir(self.test_dir)

    @patch('builtins.input')
    @patch('sys.stdout', new_callable=StringIO)
    def test_single_m(self, mock_stdout, mock_input):
        mock_input.side_effect = ["n", "m88"]
        Toolcheck.find_cookies()
        out = mock_stdout.getvalue()
        self.assertIn("M_SELECTED=M88", out)
        self.assertIn("M_VALID=M88", out)
        self.assertIn("TARGETS=3", out)
        self.assertIn("FOUND=3", out)

    @patch('builtins.input')
    @patch('sys.stdout', new_callable=StringIO)
    def test_multi_m_comma(self, mock_stdout, mock_input):
        mock_input.side_effect = ["n", "m88,m91"]
        Toolcheck.find_cookies()
        out = mock_stdout.getvalue()
        self.assertIn("M_SELECTED=M88,M91", out)
        self.assertIn("M_VALID=M88,M91", out)
        self.assertIn("TARGETS=4", out) # user2 is deduplicated
        self.assertIn("FOUND=4", out)

    @patch('builtins.input')
    @patch('sys.stdout', new_callable=StringIO)
    def test_multi_m_space(self, mock_stdout, mock_input):
        mock_input.side_effect = ["n", "m88 m91"]
        Toolcheck.find_cookies()
        out = mock_stdout.getvalue()
        self.assertIn("M_SELECTED=M88,M91", out)

    @patch('builtins.input')
    @patch('sys.stdout', new_callable=StringIO)
    def test_multi_m_mixed_case(self, mock_stdout, mock_input):
        mock_input.side_effect = ["n", "M88 m91 M106"]
        Toolcheck.find_cookies()
        out = mock_stdout.getvalue()
        self.assertIn("M_SELECTED=M88,M91,M106", out)
        self.assertIn("M_VALID=M88,M91,M106", out)
        self.assertIn("TARGETS=5", out)

    @patch('builtins.input')
    @patch('sys.stdout', new_callable=StringIO)
    def test_multi_m_duplicate_input(self, mock_stdout, mock_input):
        mock_input.side_effect = ["n", "m88 m88 m88"]
        Toolcheck.find_cookies()
        out = mock_stdout.getvalue()
        self.assertIn("M_SELECTED=M88", out)
        self.assertIn("TARGETS=3", out)

    @patch('builtins.input')
    @patch('sys.stdout', new_callable=StringIO)
    def test_multi_m_one_exist_one_not(self, mock_stdout, mock_input):
        mock_input.side_effect = ["n", "m88 m999"]
        Toolcheck.find_cookies()
        out = mock_stdout.getvalue()
        self.assertIn("M_SELECTED=M88,M999", out)
        self.assertIn("M_VALID=M88", out)
        self.assertIn("[-] M999: Không tồn tại", out)
        self.assertIn("TARGETS=3", out)

    @patch('builtins.input')
    @patch('sys.stdout', new_callable=StringIO)
    def test_multi_m_empty_section(self, mock_stdout, mock_input):
        mock_input.side_effect = ["n", "m105"]
        Toolcheck.find_cookies()
        out = mock_stdout.getvalue()
        self.assertIn("[-] M105: Tồn tại nhưng trống", out)
        self.assertIn("Không có account nào được tìm thấy", out)

    @patch('builtins.input')
    @patch('sys.stdout', new_callable=StringIO)
    def test_manual_mode(self, mock_stdout, mock_input):
        mock_input.side_effect = ["n", "", "user1:pass1", "END"]
        Toolcheck.find_cookies()
        out = mock_stdout.getvalue()
        self.assertNotIn("M_SELECTED", out)
        self.assertIn("TÌM THẤY 1/1 TÀI KHOẢN", out)

    @patch('builtins.input')
    @patch('sys.stdout', new_callable=StringIO)
    def test_mode9_preset(self, mock_stdout, mock_input):
        # mode9 preset test
        Toolcheck.find_cookies(download_choice='n', preset_m_code='m88 m91')
        out = mock_stdout.getvalue()
        self.assertIn("M_SELECTED=M88,M91", out)
        self.assertIn("TARGETS=4", out)

    @patch('builtins.input')
    @patch('sys.stdout', new_callable=StringIO)
    def test_multi_m_numeric_input(self, mock_stdout, mock_input):
        mock_input.side_effect = ["n", "88 91"]
        Toolcheck.find_cookies()
        out = mock_stdout.getvalue()
        self.assertIn("M_SELECTED=M88,M91", out)
        self.assertIn("M_VALID=M88,M91", out)

    @patch('builtins.input')
    @patch('sys.stdout', new_callable=StringIO)
    def test_false_header(self, mock_stdout, mock_input):
        # Truy vấn m123, sẽ báo không tồn tại vì m123:password không phải là header
        mock_input.side_effect = ["n", "m123"]
        Toolcheck.find_cookies()
        out = mock_stdout.getvalue()
        self.assertIn("[-] M123: Không tồn tại", out)

if __name__ == '__main__':
    unittest.main()
