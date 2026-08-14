import sys
sys.path.insert(0, "/root/Aotscript-batch-ack")
from tests.test_backup_restore_data import *

class DebugTest(TestRestoreData):
    def test_exact_safe_reason(self):
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
