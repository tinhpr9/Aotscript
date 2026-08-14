import sys
sys.path.insert(0, "/root/Aotscript-batch-ack")
from tests.test_backup_restore_data import *

class DebugTest(unittest.TestCase):
    def test_debug(self):
        self.mock_dump = mock.patch.object(CONTROLLER, "dump_ui_xml").start()
        self.mock_root = mock.patch.object(CONTROLLER, "_root_run").start()
        self.mock_root.return_value = "1080x2400"
        self.mock_sleep = mock.patch.object(time, "sleep").start()
        self.mock_screencap = mock.patch.object(CONTROLLER, "_raw_screencap").start()
        self.mock_tap_wait = mock.patch.object(CONTROLLER, "_tap_wait").start()
        self.mock_foreground = mock.patch.object(CONTROLLER, "_sb_assert_foreground").start()
        import struct
        w, h = 100, 200
        fmt = 1
        pixels = bytearray(w * h * 4)
        for i in range(0, len(pixels), 4):
            pixels[i] = 10
            pixels[i+1] = 200
            pixels[i+2] = 10
            pixels[i+3] = 255
        header = struct.pack("<III", w, h, fmt)
        self.mock_screencap.return_value = (w, h, header + pixels)

        self.mock_dump.side_effect = _Rotator([_wrap(_node(text="Random Screen"))])
        state = {}
        msg = {"action_id": "err_id", "type": "aot_batch_action", "action": "BACKUP_RESTORE_DATA", "expires_at": 9999999999999, "protocol": "phase4-1", "package": _PKG, "target_device_ids": ["123"]}
        cfg = {"hub_url": "mock", "auth_token": "mock"}
        
        with mock.patch.object(RELAY, "_send_ack") as m_ack:
            res = RELAY._handle_batch_action(cfg, state, local_id="123", message=msg)
            print("RESULT:", res)
            print("CALLS:", m_ack.call_args_list)

if __name__ == "__main__":
    unittest.main()
