import unittest
import os
import sys
import types
from unittest.mock import patch
from io import StringIO
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from unittest.mock import patch, MagicMock

with patch.dict('sys.modules', {'requests': MagicMock()}):
    with open(os.path.join(ROOT, "Toolcheck"), "r", encoding="utf-8") as f:
        code = f.read()

    Toolcheck = types.ModuleType("Toolcheck")
    exec(code, Toolcheck.__dict__)

class TestMultiM(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.join(ROOT, "test_shouko_temp")
        os.makedirs(self.test_dir, exist_ok=True)
        Toolcheck.BASE_DIR = self.test_dir
        Toolcheck.ACC_FILE_PATH = os.path.join(self.test_dir, "acc.txt")
        Toolcheck.DB_COOKIES_FILE = os.path.join(self.test_dir, "Data_Tong_Cookies.txt")
        Toolcheck.FOUND_RESULT_FILE = os.path.join(self.test_dir, "Ket_Qua_Tim_Duoc.txt")
        Toolcheck.COOKIE_FILE = os.path.join(self.test_dir, "Cookies.txt")

        with open(Toolcheck.DB_COOKIES_FILE, "w", encoding="utf-8") as f:
            f.write("user1:pass1:cookie1\n")
            f.write("user2:pass2:cookie2\n")
            f.write("user3:pass3:cookie3\n")
            f.write("user4:pass4:cookie4\n")
            f.write("m123:password:cookie123\n")
            f.write("user5:pass5:cookie5\n")
            f.write("m999:pass999:cookie999\n")
            f.write("user7:pass7:cookie7\n")
            f.write("user6:pass6:cookie6\n")
            f.write("m124:password_with_spaces:cookie124\n")

        with open(Toolcheck.ACC_FILE_PATH, "w", encoding="utf-8") as f:
            f.write("m88\n")
            f.write("m123:password\n") # Fake header in M88
            f.write("user1:pass1\n")
            f.write("m124:  password_with_spaces\n") # whitespace-after-colon false-header
            f.write("user6:pass6\n") # another account after false-header

            f.write("M153\n") # valid header without brackets
            f.write("user2:pass2\n")

            f.write("M153:\n") # adjacent section (replaces [M153])
            f.write("user3:pass3\n")

            f.write("m153 - 12/08\n") # adjacent section again
            f.write("user4:pass4\n")

            f.write("m105\n") # empty section

            f.write("m106\n")
            f.write("user5:pass5\n")

            f.write("m91\n") # Valid header instead of decorated
            f.write("m999:pass999\n") # Another false header
            f.write("=== m92 ===\n") # Malformed/decorated (ignored)
            f.write("user7:pass7\n") # Stays in m91

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
        self.assertIn("TARGETS=4", out)
        self.assertIn("FOUND=4", out)

    @patch('builtins.input')
    @patch('sys.stdout', new_callable=StringIO)
    def test_production_m153(self, mock_stdout, mock_input):
        # 153 has 3 sections: [M153], M153:, m153 - 12/08
        mock_input.side_effect = ["n", "153"]
        Toolcheck.find_cookies()
        out = mock_stdout.getvalue()
        self.assertIn("M_SELECTED=M153", out)
        self.assertIn("M_VALID=M153", out)
        self.assertIn("TARGETS=3", out)
        self.assertIn("FOUND=3", out)

    @patch('builtins.input')
    @patch('sys.stdout', new_callable=StringIO)
    def test_multi_m_comma_space_numeric(self, mock_stdout, mock_input):
        mock_input.side_effect = ["n", "88, 153 M106"]
        Toolcheck.find_cookies()
        out = mock_stdout.getvalue()
        self.assertIn("M_SELECTED=M88,M153,M106", out)
        self.assertIn("M_VALID=M88,M153,M106", out)
        self.assertIn("TARGETS=8", out)
        self.assertIn("FOUND=8", out)

    @patch('builtins.input')
    @patch('sys.stdout', new_callable=StringIO)
    def test_multi_m_duplicate_input(self, mock_stdout, mock_input):
        mock_input.side_effect = ["n", "153 153 M153"]
        Toolcheck.find_cookies()
        out = mock_stdout.getvalue()
        self.assertIn("M_SELECTED=M153", out)
        self.assertIn("TARGETS=3", out)

    @patch('builtins.input')
    @patch('sys.stdout', new_callable=StringIO)
    def test_empty_section(self, mock_stdout, mock_input):
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
        Toolcheck.find_cookies(download_choice='n', preset_m_code='88 91')
        out = mock_stdout.getvalue()
        self.assertIn("M_SELECTED=M88,M91", out)
        self.assertIn("TARGETS=6", out)

    @patch('builtins.input')
    @patch('sys.stdout', new_callable=StringIO)
    def test_false_header(self, mock_stdout, mock_input):
        mock_input.side_effect = ["n", "123 124 999"]
        Toolcheck.find_cookies()
        out = mock_stdout.getvalue()
        self.assertIn("[-] M123: Không tồn tại", out)
        self.assertIn("[-] M124: Không tồn tại", out)
        self.assertIn("[-] M999: Không tồn tại", out)

if __name__ == '__main__':
    unittest.main()
