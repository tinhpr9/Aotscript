import re

with open("tests/test_backup_restore_data.py", "r") as f:
    src = f.read()

src = src.replace('self.mock_sleep = mock.patch.object(time, "sleep").start()',
                  'self.mock_sleep = mock.patch.object(time, "sleep").start()\n        self.mock_foreground = mock.patch.object(CONTROLLER, "_sb_assert_foreground").start()')

with open("tests/test_backup_restore_data.py", "w") as f:
    f.write(src)
