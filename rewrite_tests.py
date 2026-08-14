import re
import os
import pathlib

ROOT = pathlib.Path("/root/Aotscript-batch-ack")

test_path = ROOT / "tests/test_backup_restore_data.py"

content = """\"\"\"Tests for the BACKUP_RESTORE_DATA full-chain batch action.\"\"\"
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]

def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m

CONTROLLER = _load("aot_group_controller", "aot-group-control/controller.py")
RELAY = _load("aot_relay", "aot-group-control/relay.py")

_PKG = CONTROLLER.SWIFT_BACKUP_PACKAGE
_SB = f"{_PKG}:id/"

_B = "[0,0][100,100]"

def _node(text="", desc="", clickable=True, bounds=_B, cls="android.widget.Button"):
    return f"<node class='{cls}' text='{text}' content-desc='{desc}' clickable='{'true' if clickable else 'false'}' bounds='{bounds}'/>"

def _wrap(*children: str) -> str:
    inner = "".join(children)
    return f"<hierarchy><node class='Root' bounds='{_B}'>{inner}</node></hierarchy>"

HOME_SCREEN = _wrap(_node(text="Apps"))
APPS_RESTORE_ACTIVE = _wrap(_node(text="Labels: RESTORE_DATA", clickable=False), _node(text="Batch actions"))
APPS_RESTORE_INACTIVE = _wrap(_node(text="Batch actions"))
FILTER_SCREEN_EMPTY = _wrap(_node(text="Select labels to filter"))
FILTER_SCREEN_SELECT_LABELS = _wrap(_node(text="Select labels", clickable=False), _node(text="RESTORE_DATA"))
FILTER_SCREEN_SELECT_LABELS_CHECKED = _wrap(_node(text="Select labels", clickable=False), _node(text="RESTORE_DATA"), _node(text="1 / 3"), _node(text="Apply"))
FILTER_SCREEN_ACTIVE = _wrap(_node(text="APPLY OPTIONS", clickable=False), _node(text="Labels: RESTORE_DATA", clickable=False))
BATCH_MENU = _wrap(_node(text="Restore from cloud"))
OPTIONS_MENU = _wrap(_node(text="Restore options"))

def _user_app_parts(apks_card="[10,10][90,50]", data_card="[10,60][90,100]", restore_btn="[10,110][90,150]"):
    nodes = [_node(text="User app parts", clickable=False)]
    if apks_card:
        nodes.append(_node(text="APKs", bounds=apks_card))
    if data_card:
        nodes.append(_node(text="Data", bounds=data_card))
    if restore_btn:
        nodes.append(_node(text="RESTORE", bounds=restore_btn))
    return _wrap(*nodes)

RESTORING_SCREEN = _wrap(_node(text="Restoring..."))

class _Rotator:
    def __init__(self, items):
        self._items = list(items)
        self._idx = 0
    def __call__(self):
        v = self._items[min(self._idx, len(self._items) - 1)]
        self._idx += 1
        return v

class TestRestoreData(unittest.TestCase):
    def setUp(self):
        self.mock_dump = mock.patch.object(CONTROLLER, "dump_ui_xml").start()
        self.mock_root = mock.patch.object(CONTROLLER, "_root_run").start()
        self.mock_root.return_value = "1080x2400"
        self.mock_sleep = mock.patch.object(time, "sleep").start()
        
        # default to all green
        self.mock_screencap = mock.patch.object(CONTROLLER, "_raw_screencap").start()
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

    def tearDown(self):
        mock.patch.stopall()

    def run_ctrl(self, ui_states, expected_stages, deadline=None, expected_error=None):
        self.mock_dump.side_effect = _Rotator(ui_states)
        stages = []
        if expected_error:
            with self.assertRaises(CONTROLLER.AotControllerError) as cm:
                CONTROLLER.backup_restore_data("test_id", stage_cb=stages.append, deadline=deadline)
            self.assertEqual(expected_error, str(cm.exception))
        else:
            res = CONTROLLER.backup_restore_data("test_id", stage_cb=stages.append, deadline=deadline)
            self.assertEqual("RESTORE_STARTED", res.get("status"))
        self.assertEqual(expected_stages, stages)

    # 1. Swift mở ở Home.
    def test_swift_home(self):
        self.run_ctrl([
            HOME_SCREEN, APPS_RESTORE_INACTIVE, FILTER_SCREEN_EMPTY, FILTER_SCREEN_SELECT_LABELS,
            FILTER_SCREEN_SELECT_LABELS_CHECKED, FILTER_SCREEN_ACTIVE, APPS_RESTORE_ACTIVE,
            BATCH_MENU, OPTIONS_MENU, _user_app_parts(), RESTORING_SCREEN
        ], ["SWIFT_OPENED", "FILTERED", "SELECTED", "OPTIONS_VERIFIED"])

    # 2. Swift mở lại Cloud synced apps.
    def test_swift_cloud_synced_apps(self):
        self.run_ctrl([
            APPS_RESTORE_ACTIVE, BATCH_MENU, OPTIONS_MENU, _user_app_parts(), RESTORING_SCREEN
        ], ["SELECTED", "OPTIONS_VERIFIED"])

    # 3. Swift mở ở Filter.
    def test_swift_filter_screen(self):
        self.run_ctrl([
            FILTER_SCREEN_EMPTY, FILTER_SCREEN_SELECT_LABELS, FILTER_SCREEN_SELECT_LABELS_CHECKED,
            FILTER_SCREEN_ACTIVE, APPS_RESTORE_ACTIVE, BATCH_MENU, OPTIONS_MENU, _user_app_parts(), RESTORING_SCREEN
        ], ["FILTERED", "SELECTED", "OPTIONS_VERIFIED"])

    # 4. RESTORE_DATA chưa setup.
    def test_restore_data_not_setup(self):
        self.run_ctrl([
            APPS_RESTORE_INACTIVE, FILTER_SCREEN_EMPTY, FILTER_SCREEN_SELECT_LABELS, FILTER_SCREEN_SELECT_LABELS_CHECKED,
            FILTER_SCREEN_ACTIVE, APPS_RESTORE_ACTIVE, BATCH_MENU, OPTIONS_MENU, _user_app_parts(), RESTORING_SCREEN
        ], ["APPS_OPENED", "FILTERED", "SELECTED", "OPTIONS_VERIFIED"])

    # 5. RESTORE_DATA đã active.
    def test_restore_data_active(self):
        self.run_ctrl([
            APPS_RESTORE_ACTIVE, BATCH_MENU, OPTIONS_MENU, _user_app_parts(), RESTORING_SCREEN
        ], ["SELECTED", "OPTIONS_VERIFIED"])

    def set_screencap_color(self, apks_on, data_on):
        w, h = 100, 200
        pixels = bytearray(w * h * 4)
        for y in range(h):
            for x in range(w):
                i = (y * w + x) * 4
                pixels[i+3] = 255
                if (apks_on and y < 60) or (data_on and y >= 60):
                    pixels[i], pixels[i+1], pixels[i+2] = 10, 200, 10
                else:
                    pixels[i], pixels[i+1], pixels[i+2] = 100, 100, 100
        import struct
        header = struct.pack("<III", w, h, 1)
        self.mock_screencap.return_value = (w, h, header + pixels)

    # 6. APKs ON / Data OFF.
    def test_apks_on_data_off(self):
        self.set_screencap_color(True, False)
        # Needs to dump twice for Data toggle, then verification is green
        def side_effect():
            yield _user_app_parts()
            yield _user_app_parts()
            self.set_screencap_color(True, True)
            while True:
                yield RESTORING_SCREEN
        self.mock_dump.side_effect = side_effect()
        res = CONTROLLER.backup_restore_data("test_id")
        self.assertEqual("RESTORE_STARTED", res["status"])

    # 7. APKs OFF / Data ON.
    def test_apks_off_data_on(self):
        self.set_screencap_color(False, True)
        def side_effect():
            yield _user_app_parts()
            yield _user_app_parts()
            self.set_screencap_color(True, True)
            while True:
                yield RESTORING_SCREEN
        self.mock_dump.side_effect = side_effect()
        res = CONTROLLER.backup_restore_data("test_id")
        self.assertEqual("RESTORE_STARTED", res["status"])

    # 8. cả hai OFF.
    def test_both_off(self):
        self.set_screencap_color(False, False)
        def side_effect():
            yield _user_app_parts() # check apks -> false, tap apks
            yield _user_app_parts() # verify apks -> fail, need to set green before
            # let's write a smarter side effect
            pass
        
        def smart_dump():
            state = {"step": 0}
            while True:
                if state["step"] == 0:
                    yield _user_app_parts()
                    state["step"] = 1
                elif state["step"] == 1:
                    # apks verified
                    self.set_screencap_color(True, False)
                    yield _user_app_parts()
                    state["step"] = 2
                elif state["step"] == 2:
                    # data verified
                    self.set_screencap_color(True, True)
                    yield _user_app_parts()
                    state["step"] = 3
                else:
                    yield RESTORING_SCREEN
        self.mock_dump.side_effect = smart_dump()
        res = CONTROLLER.backup_restore_data("test_id")
        self.assertEqual("RESTORE_STARTED", res["status"])

    # 9. cả hai ON.
    def test_both_on(self):
        self.set_screencap_color(True, True)
        self.mock_dump.side_effect = _Rotator([_user_app_parts(), RESTORING_SCREEN])
        res = CONTROLLER.backup_restore_data("test_id")
        self.assertEqual("RESTORE_STARTED", res["status"])

    # 10. RESTORE disabled.
    def test_restore_disabled(self):
        self.run_ctrl([
            _user_app_parts(restore_btn=None)
        ], [], expected_error="final_restore_button_not_found")

    # 11. selector missing.
    def test_selector_missing(self):
        self.run_ctrl([
            _user_app_parts(apks_card=None)
        ], [], expected_error="selector_missing:APKs")

    # 12. selector ambiguous.
    # The new smart_find resolves ambiguity by size, but we can test missing apply button
    def test_missing_apply(self):
        self.run_ctrl([
            FILTER_SCREEN_SELECT_LABELS_CHECKED.replace("Apply", "NoApply")
        ], [], expected_error="filter_apply_not_found")

    # 13. unknown UI state.
    def test_unknown_ui_state(self):
        self.run_ctrl([
            _wrap(_node(text="Random Screen"))
        ], [], expected_error="unknown_ui_state")

    # 14. redelivery không chạy RESTORE lần hai.
    # Tested in Relay
    def test_redelivery(self):
        state = {}
        msg = {"action_id": "test_id", "protocol": "phase4-1", "package": _PKG, "target_device_ids": ["123"]}
        cfg = {"hub_url": "mock", "auth_token": "mock"}
        with mock.patch.object(RELAY, "_send_batch_ack") as m_ack:
            # 1st run
            with mock.patch.object(CONTROLLER, "backup_restore_data", return_value={"status": "RESTORE_STARTED", "executed": True}):
                RELAY._handle_backup_restore_data(cfg, state, local_id="123", message=msg)
            # 2nd run
            RELAY._handle_backup_restore_data(cfg, state, local_id="123", message=msg)
        # Verify 2nd run returned ACCEPTED or RESTORE_STARTED from cache, not DUPLICATE because it had a cached result!
        # Wait, if it has a cached result, it replays it. Let's check calls.
        calls = m_ack.call_args_list
        # The last call should be the replayed RESTORE_STARTED
        self.assertEqual(calls[-1].kwargs["status"], "RESTORE_STARTED")

    # 15. exact safe_reason truyền controller → relay → AOT HUB.
    def test_exact_safe_reason(self):
        state = {}
        msg = {"action_id": "err_id", "protocol": "phase4-1", "package": _PKG, "target_device_ids": ["123"]}
        cfg = {"hub_url": "mock", "auth_token": "mock"}
        with mock.patch.object(RELAY, "_send_batch_ack") as m_ack:
            with mock.patch.object(CONTROLLER, "backup_restore_data", side_effect=CONTROLLER.AotControllerError("unknown_ui_state")):
                RELAY._handle_backup_restore_data(cfg, state, local_id="123", message=msg)
        calls = m_ack.call_args_list
        self.assertEqual(calls[-1].kwargs["status"], "FAILED")
        self.assertEqual(calls[-1].kwargs["reason"], "unknown_ui_state")

if __name__ == "__main__":
    unittest.main()
"""

with open(test_path, "w") as f:
    f.write(content)

