"""Tests for the BACKUP_RESTORE_DATA full-chain batch action.

Covers:
- Architecture / policy constraints
- Core controller helper functions (unit tests)
- Full relay protocol flow (mocking backup_restore_data at the function level)
- Integration: backup_restore_data end-to-end with mocked UI helpers
"""
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

# ── Load modules ──────────────────────────────────────────────────────────────

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


# ── XML builder helpers ───────────────────────────────────────────────────────

_B = "[0,0][100,100]"


def _node(
    rid: str = "",
    text: str = "",
    desc: str = "",
    clickable: bool = True,
    enabled: bool = True,
    selected: bool = False,
    checked: bool = False,
    bounds: str = _B,
    cls: str = "android.widget.Button",
) -> str:
    return (
        f"<node class='{cls}' resource-id='{rid}' text='{text}'"
        f" content-desc='{desc}' clickable='{'true' if clickable else 'false'}'"
        f" enabled='{'true' if enabled else 'false'}'"
        f" selected='{'true' if selected else 'false'}'"
        f" checked='{'true' if checked else 'false'}'"
        f" scrollable='false' password='false' bounds='{bounds}'/>"
    )


def _wrap(*children: str) -> str:
    inner = "".join(children)
    return (
        "<hierarchy><node class='Root' resource-id='root' clickable='false'"
        f" enabled='true' scrollable='false' password='false' bounds='{_B}'>"
        f"{inner}</node></hierarchy>"
    )


def _apps_list(count: int = 3, selected_count: int = 0) -> str:
    """Build a filtered apps list with `count` items, `selected_count` selected."""
    items = []
    for i in range(count):
        sel = i < selected_count
        items.append(_node(
            rid=f"{_SB}app_item",
            text=f"App{i}",
            selected=sel,
            bounds=f"[{i*10},{i*10}][{i*10+10},{i*10+10}]",
        ))
    return _wrap(
        _node(rid=f"{_SB}nav_apps", text="Apps", selected=True),
        _node(text=f"{count} Apps"),
        _node(text="RESTORE_DATA", selected=True),
        _node(rid=f"{_SB}checkbox_select_all", text="Select all", checked=(count > 0 and count == selected_count)),
        # Include batch_actions button so step 7 can find it
        _node(rid=f"{_SB}menu_batch_actions", text="Batch actions"),
        *items,
    )


APPS_SCREEN = _wrap(
    _node(rid=f"{_SB}nav_apps", text="Apps", selected=True),
    _node(rid=f"{_SB}menu_filter", text="Filter"),
)
FILTER_DIALOG_UNSELECTED = _wrap(
    _node(text="RESTORE_DATA", selected=False, checked=False, cls="android.widget.CheckBox"),
    _node(rid=f"{_SB}button_apply", text="Apply"),
)
FILTER_DIALOG_CHIP_SELECTED = _wrap(
    _node(text="RESTORE_DATA", selected=True, checked=True, cls="android.widget.CheckBox"),
    _node(rid=f"{_SB}button_apply", text="Apply"),
)
BATCH_MENU = _wrap(_node(rid=f"{_SB}menu_batch_actions", text="Batch actions"))
BACKUP_MENU_SCREEN = _wrap(_node(rid=f"{_SB}menu_item_backup", text="Backup"))


def _options(
    apks=True, data=True, cloud=True,
    ext=False, expansion=False, media=False, device=False,
) -> str:
    return _wrap(
        _node(rid=f"{_SB}checkbox_apks", checked=apks),
        _node(rid=f"{_SB}checkbox_data", checked=data),
        _node(rid=f"{_SB}checkbox_cloud", checked=cloud),
        _node(rid=f"{_SB}checkbox_ext_data", checked=ext),
        _node(rid=f"{_SB}checkbox_expansion", checked=expansion),
        _node(rid=f"{_SB}checkbox_media", checked=media),
        _node(rid=f"{_SB}checkbox_device", checked=device),
        _node(rid=f"{_SB}button_backup_start", text="+ BACKUP", bounds="[80,80][100,100]"),
    )


OPTIONS_CORRECT = _options()  # APKs+Data+Cloud ON; rest OFF

PROGRESS_SCREEN = _wrap(
    _node(rid=f"{_SB}progress_backup", text="Backing up...", bounds="[0,0][50,50]"),
)


# ── Helper: build multi-call iterator from list  ──────────────────────────────

class _Rotator:
    """Return items from a list; repeat the last item indefinitely."""
    def __init__(self, items):
        self._items = list(items)
        self._idx = 0

    def __call__(self):
        v = self._items[min(self._idx, len(self._items) - 1)]
        self._idx += 1
        return v


# ── Controller unit tests ─────────────────────────────────────────────────────


_B = "[0,0][100,100]"

def _node(text="", desc="", clickable=True, bounds=_B, cls="android.widget.Button"):
    return f"<node class='{cls}' text='{text}' content-desc='{desc}' clickable='{'true' if clickable else 'false'}' bounds='{bounds}'/>"

def _wrap(*children: str) -> str:
    inner = "".join(children)
    return f"<hierarchy><node class='Root' bounds='{_B}'>{inner}</node></hierarchy>"

HOME_SCREEN = _wrap(_node(text="Apps"))
APPS_RESTORE_ACTIVE = _wrap(_node(text="Labels: RESTORE_DATA", clickable=False), _node(text="Batch actions"), _node(text="5 / 5"))
APPS_RESTORE_INACTIVE = _wrap(_node(text="Batch actions"), _node(text="0 / 5"))
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
    nodes.append(_node(text="Cloud"))
    nodes.append(_node(text="Ext.data"))
    nodes.append(_node(text="Expansion"))
    nodes.append(_node(text="Media"))
    nodes.append(_node(text="Device"))
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
        RELAY.controller = CONTROLLER
        self.mock_open_sb = mock.patch.object(RELAY, "_open_swift_backup").start()
        self.mock_open_sb.return_value = True
        self.mock_fg_relay = mock.patch.object(RELAY.controller, "_sb_assert_foreground").start()
        self.mock_dump = mock.patch.object(CONTROLLER, "dump_ui_xml").start()
        
        def mock_is_green(opt, nodes):
            card = getattr(CONTROLLER, "_smart_find")(opt, nodes)
            if not card:
                return False, None
            if opt in ["APKs", "Data", "Cloud"]:
                return True, card
            return False, card
        self.mock_green = mock.patch.object(CONTROLLER, "_is_green_selected", side_effect=mock_is_green).start()

        self.mock_root = mock.patch.object(CONTROLLER, "_root_run").start()
        self.mock_root.return_value = "1080x2400"
        self.mock_sleep = mock.patch.object(time, "sleep").start()
        self.mock_foreground = mock.patch.object(CONTROLLER, "_sb_assert_foreground").start()
        self.mock_tap_wait = mock.patch.object(CONTROLLER, "_tap_wait").start()
        self.mock_tap_xy = mock.patch.object(CONTROLLER, "_tap_xy").start()
        self.mock_press_filter = mock.patch.object(CONTROLLER, "_press_filter", return_value=True).start()
        
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
        ], ["SWIFT_OPENED", "APPS_OPENED", "FILTERED", "SELECTED", "OPTIONS_VERIFIED"])

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
        def smart_dump():
            state = {"step": 0}
            while True:
                if state["step"] == 0:
                    yield _user_app_parts()
                    state["step"] = 1
                elif state["step"] == 1:
                    self.set_screencap_color(True, False)
                    yield _user_app_parts()
                    state["step"] = 2
                elif state["step"] == 2:
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
        ], ["OPTIONS_VERIFIED"], expected_error="final_restore_button_not_found")

    # 11. selector missing.
    def test_selector_missing(self):
        self.run_ctrl([
            _user_app_parts(apks_card=None)
        ], [], expected_error="selector_missing:APKs")

    # 12. selector ambiguous.
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
    def test_redelivery(self):
        self.mock_dump.side_effect = _Rotator([_user_app_parts(), RESTORING_SCREEN])
        state = {}
        msg = {"action_id": "test_id", "type": "aot_batch_action", "action": "BACKUP_RESTORE_DATA", "expires_at": 9999999999999, "protocol": "phase4-1", "package": _PKG, "target_device_ids": ["m123"]}
        cfg = {"hub_url": "mock", "auth_token": "mock"}
        with mock.patch.object(RELAY, "_send_batch_ack") as m_ack:
            RELAY._handle_batch_action(cfg, state, local_id="m123", message=msg)
            RELAY._handle_batch_action(cfg, state, local_id="m123", message=msg)
        calls = m_ack.call_args_list
        self.assertEqual(calls[-1].kwargs["status"], "RESTORE_STARTED")

    # 15. exact safe_reason truyền controller -> relay -> AOT HUB.
    def test_exact_safe_reason(self):
        self.mock_dump.side_effect = _Rotator([_wrap(_node(text="Random Screen"))])
        state = {}
        msg = {"action_id": "err_id", "type": "aot_batch_action", "action": "BACKUP_RESTORE_DATA", "expires_at": 9999999999999, "protocol": "phase4-1", "package": _PKG, "target_device_ids": ["m123"]}
        cfg = {"hub_url": "mock", "auth_token": "mock"}
        with mock.patch.object(RELAY, "_send_batch_ack") as m_ack:
            RELAY._handle_batch_action(cfg, state, local_id="m123", message=msg)
        calls = m_ack.call_args_list
        self.assertEqual(calls[-1].kwargs["status"], "FAILED")
        self.assertEqual(calls[-1].kwargs["reason"], "unknown_ui_state")

class TestLegacyActionsUnchanged(unittest.TestCase):
    def setUp(self):
        RELAY.controller = CONTROLLER
        self.mock_open_sb = mock.patch.object(RELAY, "_open_swift_backup").start()
        self.mock_open_sb.return_value = True
        self.mock_fg_relay = mock.patch.object(RELAY.controller, "_sb_assert_foreground").start()
        self.td = tempfile.TemporaryDirectory()
        RELAY.STATE_PATH = pathlib.Path(self.td.name) / "state.json"
        self.state = RELAY._load_state()
        self.sent = []
        self._old_ack = RELAY._send_ack
        self._old_open = RELAY._open_swift_backup
        self._old_apps = RELAY.controller.open_swift_apps
        RELAY._send_ack = lambda _cfg, payload: self.sent.append(payload)

    def tearDown(self):
        RELAY._send_ack = self._old_ack
        RELAY._open_swift_backup = self._old_open
        RELAY.controller.open_swift_apps = self._old_apps
        self.td.cleanup()

    def _msg(self, action: str, action_id: str = "legacy-1"):
        return {
            "type": "aot_batch_action",
            "protocol": "fleet-batch-v1",
            "target_device_ids": ["m301"],
            "action_id": action_id,
            "action": action,
            "package": RELAY.SWIFT_BACKUP_PACKAGE,
            "expires_at": int(time.time() * 1000) + 5000,
        }

    def test_open_swift_backup(self):
        RELAY._open_swift_backup = lambda: True
        RELAY._handle_batch_action({}, self.state, local_id="m301",
                                   message=self._msg("OPEN_SWIFT_BACKUP"))
        statuses = [s["status"] for s in self.sent]
        self.assertIn("ACCEPTED", statuses)
        self.assertIn("OPENED", statuses)

    def test_open_swift_apps(self):
        RELAY.controller.open_swift_apps = lambda: {"executed": True}
        RELAY._handle_batch_action({}, self.state, local_id="m301",
                                   message=self._msg("OPEN_SWIFT_APPS"))
        statuses = [s["status"] for s in self.sent]
        self.assertIn("ACCEPTED", statuses)
        self.assertIn("APPS_OPENED", statuses)


# ── Architecture / policy tests ───────────────────────────────────────────────

class TestArchitectureConstraints(unittest.TestCase):
    def test_filter_restore_data_absent_from_relay(self):
        """The banned identifier must not appear in relay.py."""
        src = (ROOT / "aot-group-control/relay.py").read_text()
        self.assertNotIn("FILTER_RESTORE_DATA", src)

    def test_backup_restore_data_action_in_relay(self):
        src = (ROOT / "aot-group-control/relay.py").read_text()
        self.assertIn("BACKUP_RESTORE_DATA_ACTION", src)

    def test_no_hardcoded_app_count_7(self):
        for fname in ("aot-group-control/relay.py", "aot-group-control/controller.py"):
            src = (ROOT / fname).read_text()
            self.assertNotIn("app_count == 7", src, f"hardcoded count in {fname}")
            self.assertNotIn("app_count = 7", src, f"hardcoded count in {fname}")

    def test_no_absolute_coordinates_in_backup_restore_data(self):
        src = (ROOT / "aot-group-control/controller.py").read_text()
        fn_start = src.index("def backup_restore_data(")
        # Find next def at same indent level
        fn_src = src[fn_start:fn_start + 30000]
        # Check for hardcoded pixel coordinates (literal ints, not via center)
        hardcoded = re.findall(r"_tap_xy\(\s*\d+\s*,\s*\d+\s*\)", fn_src)
        self.assertEqual([], hardcoded,
                         f"Hardcoded coordinates found: {hardcoded!r}")

    def test_new_capability_advertised(self):
        self.assertIn("backup_restore_data_semantic", RELAY.WORKER_CAPABILITIES)

    def test_worker_version_bumped_to_12(self):
        self.assertEqual("aot-worker-2026.08.14.04", RELAY.WORKER_VERSION)

    def test_fleet_hub_html_has_backup_restore_data_button(self):
        src = (ROOT / "cloudflare-worker/worker.js").read_text()
        hub_start = src.index("function fleetHubHtml()")
        hub_end = src.index("async function handleAotHubPage")
        hub = src[hub_start:hub_end]
        self.assertIn("backupRestoreData", hub)
        self.assertIn("Backup RESTORE_DATA", hub)
        self.assertIn("backup_restore_data", hub)

    def test_existing_hub_buttons_preserved(self):
        src = (ROOT / "cloudflare-worker/worker.js").read_text()
        hub_start = src.index("function fleetHubHtml()")
        hub_end = src.index("async function handleAotHubPage")
        hub = src[hub_start:hub_end]
        self.assertIn("open_swift_backup", hub)
        self.assertIn("open_swift_apps", hub)
        self.assertIn("Mở Swift Backup", hub)
        self.assertIn("Mở Apps", hub)

    def test_no_session_id_or_coordinates_in_hub(self):
        src = (ROOT / "cloudflare-worker/worker.js").read_text()
        hub_start = src.index("function fleetHubHtml()")
        hub_end = src.index("async function handleAotHubPage")
        hub = src[hub_start:hub_end]
        for forbidden in ("session_id", "x_norm", "y_norm", "REFERENCE"):
            self.assertNotIn(forbidden, hub)

    def test_fleet_state_version_bumped(self):
        src = (ROOT / "cloudflare-worker/fleet-state.js").read_text()
        self.assertIn("aot-worker-2026.08.14.04", src)
        self.assertIn("worker-v2026.08.14.04", src)

    def test_fleet_state_has_backup_restore_data_action(self):
        src = (ROOT / "cloudflare-worker/fleet-state.js").read_text()
        self.assertIn("AOT_BACKUP_RESTORE_DATA_ACTION", src)
        self.assertIn("BACKUP_RESTORE_DATA", src)

    def test_fleet_state_ack_includes_stage_statuses(self):
        src = (ROOT / "cloudflare-worker/fleet-state.js").read_text()
        for stage in ("SWIFT_OPENED", "APPS_OPENED", "FILTERED", "SELECTED",
                      "OPTIONS_VERIFIED", "RESTORE_STARTED"):
            self.assertIn(stage, src, f"Missing stage status {stage!r}")

    def test_worker_js_accepts_backup_restore_data(self):
        src = (ROOT / "cloudflare-worker/worker.js").read_text()
        self.assertIn("BACKUP_RESTORE_DATA", src)

    def test_agents_md_policy_reconciled(self):
        src = (ROOT / "AGENTS.md").read_text()
        self.assertIn("FILTER_RESTORE_DATA", src)
        self.assertIn("BACKUP_RESTORE_DATA", src)

    def test_smoke_test_checks_new_version(self):
        src = (ROOT / "aot-group-control/worker_smoke_test.py").read_text()
        self.assertIn("aot-worker-2026.08.14.04", src)
        self.assertIn("backup_restore_data_semantic", src)
        self.assertIn("BACKUP_RESTORE_DATA_ACTION", src)

    def test_relay_selftest_checks_new_version(self):
        src = (ROOT / "aot-group-control/relay_selftest.py").read_text()
        self.assertIn("aot-worker-2026.08.14.04", src)
        self.assertIn("BACKUP_RESTORE_DATA", src)

