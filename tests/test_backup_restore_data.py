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

class TestRestoreDataFilterActive(unittest.TestCase):
    def _nodes(self, xml):
        return CONTROLLER.parse_ui_xml(xml)

    def test_selected_chip_active(self):
        xml = _wrap(_node(text="RESTORE_DATA", selected=True))
        self.assertTrue(CONTROLLER._restore_data_filter_active(self._nodes(xml)))

    def test_unselected_chip_not_active(self):
        xml = _wrap(_node(text="RESTORE_DATA", selected=False))
        self.assertFalse(CONTROLLER._restore_data_filter_active(self._nodes(xml)))

    def test_no_chip_not_active(self):
        xml = _wrap(_node(text="Something", selected=True))
        self.assertFalse(CONTROLLER._restore_data_filter_active(self._nodes(xml)))


class TestCountFilteredApps(unittest.TestCase):
    def test_zero(self):
        nodes = CONTROLLER.parse_ui_xml(_wrap(_node(text="No apps")))
        self.assertIsNone(CONTROLLER._count_filtered_apps(nodes))

    def test_three(self):
        nodes = CONTROLLER.parse_ui_xml(_apps_list(3))
        self.assertEqual(3, CONTROLLER._count_filtered_apps(nodes))

    def test_five(self):
        nodes = CONTROLLER.parse_ui_xml(_apps_list(5))
        self.assertEqual(5, CONTROLLER._count_filtered_apps(nodes))

    def test_ignores_selected(self):
        nodes = CONTROLLER.parse_ui_xml(_wrap(
            _node(text="3 selected"),
            _node(text="4 apps")
        ))
        self.assertEqual(4, CONTROLLER._count_filtered_apps(nodes))

class TestIsAllSelected(unittest.TestCase):
    def test_none_selected(self):
        nodes = CONTROLLER.parse_ui_xml(_apps_list(3, 0))
        self.assertFalse(CONTROLLER._is_all_selected(nodes))

    def test_all_selected(self):
        nodes = CONTROLLER.parse_ui_xml(_apps_list(3, 3))
        self.assertTrue(CONTROLLER._is_all_selected(nodes))


class TestFindUniqueByResourceIds(unittest.TestCase):
    def test_single_match(self):
        nodes = CONTROLLER.parse_ui_xml(
            _wrap(_node(rid=f"{_SB}button_apply"))
        )
        result = CONTROLLER._find_unique_by_resource_ids(nodes, f"{_SB}button_apply")
        self.assertIsNotNone(result)

    def test_no_match(self):
        nodes = CONTROLLER.parse_ui_xml(_wrap(_node(rid="other:id/foo")))
        result = CONTROLLER._find_unique_by_resource_ids(nodes, f"{_SB}nonexistent")
        self.assertIsNone(result)


class TestSetSwitchTo(unittest.TestCase):
    def test_already_correct_no_tap(self):
        nodes = CONTROLLER.parse_ui_xml(_wrap(_node(rid=f"{_SB}checkbox_apks", checked=True)))
        with mock.patch.object(CONTROLLER, "_tap_xy") as tap:
            CONTROLLER._set_switch_to(nodes, f"{_SB}checkbox_apks", True, "APKs")
            self.assertNotIn(mock.call(90.0, 90.0), tap.mock_calls)

    def test_wrong_state_taps(self):
        nodes = CONTROLLER.parse_ui_xml(_wrap(_node(rid=f"{_SB}checkbox_apks", checked=False)))
        with mock.patch.object(CONTROLLER, "_tap_xy") as tap, \
             mock.patch.object(CONTROLLER.time, "sleep"):
            CONTROLLER._set_switch_to(nodes, f"{_SB}checkbox_apks", True, "APKs")
            tap.assert_called_once()

    def test_not_found_raises(self):
        nodes = CONTROLLER.parse_ui_xml(_wrap(_node(rid="other:id/x")))
        with self.assertRaisesRegex(CONTROLLER.AotControllerError, "option_not_found"):
            CONTROLLER._set_switch_to(nodes, f"{_SB}checkbox_apks", True, "APKs")


class TestIsBackupRunning(unittest.TestCase):
    def test_running_detected(self):
        nodes = CONTROLLER.parse_ui_xml(PROGRESS_SCREEN)
        self.assertTrue(CONTROLLER._is_backup_running(nodes))

    def test_not_running(self):
        nodes = CONTROLLER.parse_ui_xml(OPTIONS_CORRECT)
        self.assertFalse(CONTROLLER._is_backup_running(nodes))


# ── End-to-end integration tests via high-level function mocking ──────────────

class TestBackupRestoreDataIntegration(unittest.TestCase):
    """End-to-end tests for backup_restore_data with mocked internal steps.

    We mock the *internal semantic building blocks* so that the top-level
    state machine can be tested without complex XML iterator management.
    """

    def _run_with_mocks(
        self,
        *,
        apps_open=True,
        filter_active=False,
        app_count=3,
        selected_initially=0,
        options_xml=None,
        backup_running=False,
        final_screen_changed=True,
    ):
        """Run backup_restore_data with simplified mocks for each semantic function."""
        call_log = []

        pkg_call_count = [0]
        def fg():
            return _PKG

        # We need to supply dump_ui_xml results for each _wait_for inside the
        # chain.  We'll use a state machine approach:
        # Each call to dump_ui_xml returns the appropriate XML for the current
        # logical stage.
        stage = ["start"]

        def dump():
            s = stage[0]
            if s == "start":
                return APPS_SCREEN if apps_open else _wrap(_node(rid=f"{_SB}nav_apps", text="Apps", selected=False))
            elif s == "filter_trigger":
                return APPS_SCREEN  # filter trigger present
            elif s == "filter_dialog":
                return FILTER_DIALOG_UNSELECTED
            elif s == "filter_dialog_chip_selected":
                return FILTER_DIALOG_CHIP_SELECTED
            elif s == "filtered":
                return _apps_list(app_count, selected_initially)
            elif s == "all_selected":
                return _apps_list(app_count, app_count)
            elif s == "batch_menu":
                return BATCH_MENU
            elif s == "backup_menu":
                return BACKUP_MENU_SCREEN
            elif s == "options":
                return options_xml or OPTIONS_CORRECT
            elif s == "progress":
                return PROGRESS_SCREEN if final_screen_changed else OPTIONS_CORRECT
            return APPS_SCREEN

        taps = []

        with mock.patch.object(CONTROLLER, "foreground_package", side_effect=fg), \
             mock.patch.object(CONTROLLER, "_tap_xy",
                               side_effect=lambda x, y: taps.append((x, y))), \
             mock.patch.object(CONTROLLER.time, "sleep"), \
             mock.patch.object(CONTROLLER, "swift_apps_screen_open",
                               return_value=apps_open), \
             mock.patch.object(CONTROLLER, "open_swift_apps",
                               return_value={"executed": True}), \
             mock.patch.object(CONTROLLER, "_restore_data_filter_active",
                               return_value=filter_active), \
             mock.patch.object(CONTROLLER, "_find_unique_by_resource_ids",
                               wraps=CONTROLLER._find_unique_by_resource_ids) as furid, \
             mock.patch.object(CONTROLLER, "_count_filtered_apps",
                               return_value=app_count), \
             mock.patch.object(CONTROLLER, "_is_all_selected",
                               return_value=(selected_initially > 0 and selected_initially == app_count)) as csa, \
             mock.patch.object(CONTROLLER, "_is_backup_running",
                               return_value=backup_running), \
             mock.patch.object(CONTROLLER, "dump_ui_xml",
                               side_effect=dump), \
             mock.patch.object(CONTROLLER, "_wait_for",
                               side_effect=lambda fn, stage, **kw: None):
            # Override filter active to False initially, True after filter applied
            filter_call_count = [0]
            def _fake_filter_active(nodes):
                filter_call_count[0] += 1
                # Return False for first call (trigger filter opening), True after
                return filter_active or filter_call_count[0] > 1
            with mock.patch.object(CONTROLLER, "_restore_data_filter_active",
                                   side_effect=_fake_filter_active), \
                 mock.patch.object(CONTROLLER, "_is_all_selected",
                                   side_effect=lambda _n: app_count):
                # Also need count_selected to return app_count so selection check passes
                result = CONTROLLER.backup_restore_data("action-test")
        return result, taps

    def test_successful_chain_returns_executed(self):
        """Successful full chain returns executed=True with correct app_count."""
        with mock.patch.object(CONTROLLER, "foreground_package", return_value=_PKG), \
             mock.patch.object(CONTROLLER.time, "sleep"), \
             mock.patch.object(CONTROLLER, "swift_apps_screen_open", return_value=True), \
             mock.patch.object(CONTROLLER, "_restore_data_filter_active", return_value=True), \
             mock.patch.object(CONTROLLER, "_count_filtered_apps", return_value=3), \
             mock.patch.object(CONTROLLER, "_is_all_selected", return_value=True), \
             mock.patch.object(CONTROLLER, "_is_backup_running", return_value=False), \
             mock.patch.object(CONTROLLER, "_wait_for",
                               side_effect=lambda fn, stage, **kw: None), \
             mock.patch.object(CONTROLLER, "_tap_xy") as tap, \
             mock.patch.object(CONTROLLER, "dump_ui_xml",
                               side_effect=_Rotator([
                                   APPS_SCREEN, APPS_SCREEN,
                                   _apps_list(3, 3),
                                   BATCH_MENU, BACKUP_MENU_SCREEN,
                                   OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT,
                                   OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT,
                                   OPTIONS_CORRECT, OPTIONS_CORRECT,
                                   PROGRESS_SCREEN, PROGRESS_SCREEN,
                               ])):
            result = CONTROLLER.backup_restore_data("action-success")
        self.assertTrue(result["executed"])
        self.assertEqual("BACKUP_RESTORE_DATA", result["action"])
        self.assertEqual(3, result["app_count"])

    def test_filter_already_active_skips_filter_dialog(self):
        """If filter is already active, filter dialog is skipped."""
        with mock.patch.object(CONTROLLER, "foreground_package", return_value=_PKG), \
             mock.patch.object(CONTROLLER.time, "sleep"), \
             mock.patch.object(CONTROLLER, "swift_apps_screen_open", return_value=True), \
             mock.patch.object(CONTROLLER, "_restore_data_filter_active", return_value=True), \
             mock.patch.object(CONTROLLER, "_count_filtered_apps", return_value=5), \
             mock.patch.object(CONTROLLER, "_is_all_selected", return_value=True), \
             mock.patch.object(CONTROLLER, "_is_backup_running", return_value=False), \
             mock.patch.object(CONTROLLER, "_wait_for",
                               side_effect=lambda fn, stage, **kw: None), \
             mock.patch.object(CONTROLLER, "_find_unique_by_resource_ids",
                               wraps=CONTROLLER._find_unique_by_resource_ids) as find_mock, \
             mock.patch.object(CONTROLLER, "_tap_xy") as tap, \
             mock.patch.object(CONTROLLER, "dump_ui_xml",
                               side_effect=_Rotator([
                                   APPS_SCREEN, APPS_SCREEN,
                                   _apps_list(5, 5),
                                   BATCH_MENU, BACKUP_MENU_SCREEN,
                                   OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT,
                                   OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT,
                                   OPTIONS_CORRECT, OPTIONS_CORRECT,
                                   PROGRESS_SCREEN, PROGRESS_SCREEN,
                               ])):
            result = CONTROLLER.backup_restore_data("action-filter-active")
        self.assertTrue(result["executed"])
        self.assertEqual(5, result["app_count"])

    def test_wrong_package_raises_immediately(self):
        with mock.patch.object(CONTROLLER, "foreground_package",
                               return_value="com.other.app"):
            with self.assertRaisesRegex(
                CONTROLLER.AotControllerError, "swift_backup_not_foreground"
            ):
                CONTROLLER.backup_restore_data("action-wrong-pkg")

    def test_zero_apps_raises_before_batch_actions(self):
        with mock.patch.object(CONTROLLER, "foreground_package", return_value=_PKG), \
             mock.patch.object(CONTROLLER.time, "sleep"), \
             mock.patch.object(CONTROLLER, "swift_apps_screen_open", return_value=True), \
             mock.patch.object(CONTROLLER, "_restore_data_filter_active", return_value=True), \
             mock.patch.object(CONTROLLER, "_count_filtered_apps", return_value=0), \
             mock.patch.object(CONTROLLER, "_wait_for",
                               side_effect=lambda fn, stage, **kw: None), \
             mock.patch.object(CONTROLLER, "dump_ui_xml",
                               return_value=APPS_SCREEN):
            with self.assertRaisesRegex(
                CONTROLLER.AotControllerError, "restore_data_no_matching_apps"
            ):
                CONTROLLER.backup_restore_data("action-zero-apps")

    def test_dynamic_count_not_hardcoded(self):
        """Count comes from the UI, not a constant."""
        for app_count in (1, 3, 7, 12, 40):
            with self.subTest(count=app_count):
                with mock.patch.object(CONTROLLER, "foreground_package", return_value=_PKG), \
                     mock.patch.object(CONTROLLER.time, "sleep"), \
                     mock.patch.object(CONTROLLER, "swift_apps_screen_open", return_value=True), \
                     mock.patch.object(CONTROLLER, "_restore_data_filter_active", return_value=True), \
                     mock.patch.object(CONTROLLER, "_count_filtered_apps", return_value=app_count), \
                     mock.patch.object(CONTROLLER, "_is_all_selected", return_value=True), \
                     mock.patch.object(CONTROLLER, "_is_backup_running", return_value=False), \
                     mock.patch.object(CONTROLLER, "_wait_for",
                                       side_effect=lambda fn, stage, **kw: None), \
                     mock.patch.object(CONTROLLER, "_tap_xy") as tap, \
                     mock.patch.object(CONTROLLER, "dump_ui_xml",
                                       side_effect=_Rotator([
                                           APPS_SCREEN, APPS_SCREEN,
                                           _apps_list(app_count, app_count),
                                           BATCH_MENU, BACKUP_MENU_SCREEN,
                                           OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT,
                                           OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT,
                                           OPTIONS_CORRECT, OPTIONS_CORRECT,
                                           PROGRESS_SCREEN, PROGRESS_SCREEN,
                                       ])):
                    result = CONTROLLER.backup_restore_data(f"action-count-{app_count}")
                self.assertEqual(app_count, result["app_count"])

    def test_backup_already_running_raises(self):
        with mock.patch.object(CONTROLLER, "foreground_package", return_value=_PKG), \
             mock.patch.object(CONTROLLER.time, "sleep"), \
             mock.patch.object(CONTROLLER, "swift_apps_screen_open", return_value=True), \
             mock.patch.object(CONTROLLER, "_restore_data_filter_active", return_value=True), \
             mock.patch.object(CONTROLLER, "_count_filtered_apps", return_value=3), \
             mock.patch.object(CONTROLLER, "_is_all_selected", return_value=True), \
             mock.patch.object(CONTROLLER, "_is_backup_running", return_value=True), \
             mock.patch.object(CONTROLLER, "_wait_for",
                               side_effect=lambda fn, stage, **kw: None), \
             mock.patch.object(CONTROLLER, "_tap_xy") as tap, \
             mock.patch.object(CONTROLLER, "dump_ui_xml",
                               side_effect=_Rotator([
                                   APPS_SCREEN, APPS_SCREEN,
                                   _apps_list(3, 3),
                                   BATCH_MENU, BACKUP_MENU_SCREEN,
                                   OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT,
                                   OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT,
                                   OPTIONS_CORRECT, OPTIONS_CORRECT,
                               ])):
            with self.assertRaisesRegex(
                CONTROLLER.AotControllerError, "backup_already_running"
            ):
                CONTROLLER.backup_restore_data("action-already-running")
            # Should fail closed and definitely not tap anything (especially the final button)
            self.assertNotIn(mock.call(90.0, 90.0), tap.mock_calls)

    def test_final_backup_button_tapped_exactly_once(self):
        """The final + BACKUP button must be tapped exactly once."""
        taps = []
        with mock.patch.object(CONTROLLER, "foreground_package", return_value=_PKG), \
             mock.patch.object(CONTROLLER.time, "sleep"), \
             mock.patch.object(CONTROLLER, "swift_apps_screen_open", return_value=True), \
             mock.patch.object(CONTROLLER, "_restore_data_filter_active", return_value=True), \
             mock.patch.object(CONTROLLER, "_count_filtered_apps", return_value=3), \
             mock.patch.object(CONTROLLER, "_is_all_selected", return_value=True), \
             mock.patch.object(CONTROLLER, "_is_backup_running", return_value=False), \
             mock.patch.object(CONTROLLER, "_wait_for",
                               side_effect=lambda fn, stage, **kw: None), \
             mock.patch.object(CONTROLLER, "_tap_xy",
                               side_effect=lambda x, y: taps.append((x, y))), \
             mock.patch.object(CONTROLLER, "dump_ui_xml",
                               side_effect=_Rotator([
                                   APPS_SCREEN, APPS_SCREEN,
                                   _apps_list(3, 3),
                                   BATCH_MENU, BACKUP_MENU_SCREEN,
                                   OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT,
                                   OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT,
                                   OPTIONS_CORRECT, OPTIONS_CORRECT,
                                   PROGRESS_SCREEN, PROGRESS_SCREEN,
                               ])):
            CONTROLLER.backup_restore_data("action-one-tap")
        final_taps = [t for t in taps if t == (90.0, 90.0)]
        self.assertEqual(1, len(final_taps))

    def test_stage_callback_called_for_key_stages(self):
        stages_seen = []
        with mock.patch.object(CONTROLLER, "foreground_package", return_value=_PKG), \
             mock.patch.object(CONTROLLER.time, "sleep"), \
             mock.patch.object(CONTROLLER, "swift_apps_screen_open", return_value=True), \
             mock.patch.object(CONTROLLER, "_restore_data_filter_active", return_value=True), \
             mock.patch.object(CONTROLLER, "_count_filtered_apps", return_value=3), \
             mock.patch.object(CONTROLLER, "_is_all_selected", return_value=True), \
             mock.patch.object(CONTROLLER, "_is_backup_running", return_value=False), \
             mock.patch.object(CONTROLLER, "_wait_for",
                               side_effect=lambda fn, stage, **kw: None), \
             mock.patch.object(CONTROLLER, "_tap_xy") as tap, \
             mock.patch.object(CONTROLLER, "dump_ui_xml",
                               side_effect=_Rotator([
                                   APPS_SCREEN, APPS_SCREEN,
                                   _apps_list(3, 3),
                                   BATCH_MENU, BACKUP_MENU_SCREEN,
                                   OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT,
                                   OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT,
                                   OPTIONS_CORRECT, OPTIONS_CORRECT,
                                   PROGRESS_SCREEN, PROGRESS_SCREEN,
                               ])):
            CONTROLLER.backup_restore_data("action-stages", stage_cb=stages_seen.append)
        self.assertIn("APPS_OPENED", stages_seen)
        self.assertIn("SELECTED", stages_seen)
        self.assertIn("OPTIONS_VERIFIED", stages_seen)
        self.assertEqual("OPTIONS_VERIFIED", stages_seen[-1], f"Last stage was {stages_seen[-1]!r}")

# ── Relay protocol tests ──────────────────────────────────────────────────────

class TestRelayBackupRestoreData(unittest.TestCase):

    def _make_message(self, action_id="test-brd-1", expires_ms=5000):
        return {
            "type": "aot_batch_action",
            "protocol": "fleet-batch-v1",
            "target_device_ids": ["m301"],
            "action_id": action_id,
            "action": "BACKUP_RESTORE_DATA",
            "package": RELAY.SWIFT_BACKUP_PACKAGE,
            "expires_at": int(time.time() * 1000) + expires_ms,
        }

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        RELAY.STATE_PATH = pathlib.Path(self.td.name) / "state.json"
        self.state = RELAY._load_state()
        self.sent = []
        self._old_ack = RELAY._send_ack
        self._old_open = RELAY._open_swift_backup
        self._old_brd = getattr(RELAY.controller, "backup_restore_data", None)
        RELAY._send_ack = lambda _cfg, payload: self.sent.append(payload)
        RELAY._open_swift_backup = lambda: True

    def tearDown(self):
        RELAY._send_ack = self._old_ack
        RELAY._open_swift_backup = self._old_open
        if self._old_brd is not None:
            RELAY.controller.backup_restore_data = self._old_brd
        self.td.cleanup()

    def _fake_success(self, action_id, *, stage_cb=None, **kwargs):
        for s in ("APPS_OPENED", "FILTERED", "SELECTED", "OPTIONS_VERIFIED"):
            if stage_cb:
                stage_cb(s)
        return {"action": "BACKUP_RESTORE_DATA", "executed": True, "status": "BACKUP_STARTED",
                "app_count": 3, "selected_count": 3}

    def test_complete_chain_statuses(self):
        RELAY.controller.backup_restore_data = self._fake_success
        RELAY._handle_batch_action({}, self.state, local_id="m301",
                                   message=self._make_message())
        statuses = [s["status"] for s in self.sent]
        self.assertEqual("ACCEPTED", statuses[0])
        self.assertIn("SWIFT_OPENED", statuses)
        self.assertIn("APPS_OPENED", statuses)
        self.assertIn("FILTERED", statuses)
        self.assertIn("SELECTED", statuses)
        self.assertIn("OPTIONS_VERIFIED", statuses)
        self.assertEqual("BACKUP_STARTED", statuses[-1])
        self.assertTrue(self.sent[-1]["executed"])
        self.assertFalse(self.sent[0]["executed"])

    def test_duplicate_action_not_re_executed(self):
        RELAY.controller.backup_restore_data = self._fake_success
        msg = self._make_message()
        RELAY._handle_batch_action({}, self.state, local_id="m301", message=msg)

        tap_count = [0]

        def _should_not_run(*a, **kw):
            tap_count[0] += 1
            return self._fake_success(*a, **kw)

        RELAY.controller.backup_restore_data = _should_not_run
        self.sent.clear()
        RELAY._handle_batch_action({}, self.state, local_id="m301", message=msg)
        self.assertEqual("BACKUP_STARTED", self.sent[-1]["status"])
        self.assertTrue(self.sent[-1]["executed"])
        self.assertEqual(0, tap_count[0])

    def test_duplicate_terminal_delivery(self):
        tap_count = [0]

        def _fake_success(*a, **kw):
            tap_count[0] += 1
            return {"action": "BACKUP_RESTORE_DATA", "executed": True, "status": "BACKUP_STARTED",
                    "app_count": 3, "selected_count": 3}

        RELAY.controller.backup_restore_data = _fake_success
        msg = self._make_message(action_id="test-terminal-dup")

        real_send_ack = RELAY._send_ack

        def _failing_ack(cfg, payload):
            if payload.get("status") == "BACKUP_STARTED":
                raise RELAY.AotRelayError("ack_delivery_failed")
            real_send_ack(cfg, payload)

        RELAY._send_ack = _failing_ack

        try:
            with self.assertRaises(RELAY.AotRelayError):
                RELAY._handle_batch_action({}, self.state, local_id="m301", message=msg)
            self.assertEqual(1, tap_count[0])

            persisted = json.loads(RELAY.STATE_PATH.read_text(encoding="utf-8"))
            self.assertEqual({
                "test-terminal-dup": {
                    "status": "BACKUP_STARTED",
                    "executed": True,
                    "safe_reason": None,
                    "app_count": 3,
                    "selected_count": 3,
                }
            }, persisted["action_results"])

            RELAY._send_ack = real_send_ack
            self.sent.clear()
            RELAY._handle_batch_action({}, self.state, local_id="m301", message=msg)

            self.assertEqual(1, tap_count[0])
            self.assertEqual("BACKUP_STARTED", self.sent[-1]["status"])
            self.assertTrue(self.sent[-1]["executed"])
            self.assertEqual(3, self.sent[-1].get("app_count"))
            self.assertEqual(3, self.sent[-1].get("selected_count"))
        finally:
            RELAY._send_ack = real_send_ack

    def test_terminal_failed_metrics(self):
        def _fake_failed(*a, **kw):
            return {"action": "BACKUP_RESTORE_DATA", "executed": True, "status": "FAILED",
                    "safe_reason": "post_tap_verification_failed", "app_count": 5, "selected_count": 2}

        RELAY.controller.backup_restore_data = _fake_failed
        msg = self._make_message(action_id="test-terminal-fail-metrics")

        RELAY._handle_batch_action({}, self.state, local_id="m301", message=msg)

        self.assertEqual("FAILED", self.sent[-1]["status"])
        self.assertTrue(self.sent[-1]["executed"])
        self.assertEqual("post_tap_verification_failed", self.sent[-1]["reason"])
        self.assertEqual(5, self.sent[-1].get("app_count"))
        self.assertEqual(2, self.sent[-1].get("selected_count"))

        # Test replay
        self.sent.clear()
        RELAY._handle_batch_action({}, self.state, local_id="m301", message=msg)
        self.assertEqual("FAILED", self.sent[-1]["status"])
        self.assertEqual(5, self.sent[-1].get("app_count"))
        self.assertEqual(2, self.sent[-1].get("selected_count"))

    def test_final_tap_delivery_uncertain(self):
        """Simulate _tap_xy raising specifically at the final button, assert exact behavior."""
        import tests.test_backup_restore_data as tbrd

        taps = []
        msg = self._make_message(action_id="test-uncertain")

        def fake_tap(x, y):
            taps.append((x, y))
            if x == 90.0 and y == 90.0:
                raise Exception("Tap delivery uncertain")

        RELAY.controller.backup_restore_data = self._old_brd

        rotator = tbrd._Rotator([
             tbrd.APPS_SCREEN, tbrd.APPS_SCREEN,
             tbrd._apps_list(3, 3),
             tbrd.BATCH_MENU, tbrd.BACKUP_MENU_SCREEN,
             tbrd.OPTIONS_CORRECT, tbrd.OPTIONS_CORRECT, tbrd.OPTIONS_CORRECT,
             tbrd.OPTIONS_CORRECT, tbrd.OPTIONS_CORRECT, tbrd.OPTIONS_CORRECT, tbrd.OPTIONS_CORRECT,
             tbrd.OPTIONS_CORRECT, tbrd.OPTIONS_CORRECT,
             tbrd.PROGRESS_SCREEN, tbrd.PROGRESS_SCREEN,
        ])

        with mock.patch.object(RELAY.controller, "foreground_package", return_value=tbrd._PKG), \
             mock.patch.object(RELAY.controller.time, "sleep"), \
             mock.patch.object(RELAY.controller, "swift_apps_screen_open", return_value=True), \
             mock.patch.object(RELAY.controller, "_restore_data_filter_active", return_value=True), \
             mock.patch.object(RELAY.controller, "_count_filtered_apps", return_value=3), \
             mock.patch.object(RELAY.controller, "_is_all_selected", return_value=True), \
             mock.patch.object(RELAY.controller, "_is_backup_running", return_value=False), \
             mock.patch.object(RELAY.controller, "_wait_for", side_effect=lambda fn, stage, **kw: None), \
             mock.patch.object(RELAY.controller, "_tap_xy", side_effect=fake_tap), \
             mock.patch.object(RELAY.controller, "dump_ui_xml", side_effect=rotator):

            RELAY._handle_batch_action({}, self.state, local_id="m301", message=msg)

        final_taps = [t for t in taps if t == (90.0, 90.0)]
        self.assertEqual(1, len(final_taps))

        self.assertEqual("FAILED", self.sent[-1]["status"])
        self.assertTrue(self.sent[-1]["executed"])
        self.assertEqual("final_tap_delivery_uncertain", self.sent[-1]["reason"])
        self.assertEqual(3, self.sent[-1].get("app_count"))
        self.assertEqual(3, self.sent[-1].get("selected_count"))

        self.sent.clear()
        RELAY.controller.backup_restore_data = mock.Mock()
        RELAY._handle_batch_action({}, self.state, local_id="m301", message=msg)
        self.assertEqual("FAILED", self.sent[-1]["status"])
        self.assertEqual("final_tap_delivery_uncertain", self.sent[-1]["reason"])
        self.assertEqual(3, self.sent[-1].get("app_count"))
        self.assertEqual(3, self.sent[-1].get("selected_count"))
        RELAY.controller.backup_restore_data.assert_not_called()

    def test_failure_sends_failed_status(self):
        error_cases = [
            "restore_data_no_matching_apps",
            "filter_trigger_not_found",
            "backup_already_running",
            "final_backup_button_not_found",
            "stage_timeout:backup_started",
        ]
        for error_msg in error_cases:
            with self.subTest(error=error_msg):
                # Use a fresh state for each subTest to avoid duplicate guard
                td2 = tempfile.TemporaryDirectory()
                try:
                    RELAY.STATE_PATH = pathlib.Path(td2.name) / "state.json"
                    fresh_state = RELAY._load_state()
                    RELAY.controller.backup_restore_data = mock.Mock(
                        side_effect=RELAY.controller.AotControllerError(error_msg)
                    )
                    sent2 = []
                    old_ack = RELAY._send_ack
                    RELAY._send_ack = lambda _cfg, payload: sent2.append(payload)
                    try:
                        RELAY._handle_batch_action(
                            {}, fresh_state,
                            local_id="m301",
                            message=self._make_message(action_id=f"fail-{error_msg[:10]}")
                        )
                    finally:
                        RELAY._send_ack = old_ack
                    self.assertEqual("FAILED", sent2[-1]["status"],
                                     f"Expected FAILED for error {error_msg!r}, got {sent2!r}")
                    reason = sent2[-1].get("reason", "")
                    self.assertLess(len(reason), 200)
                finally:
                    td2.cleanup()
                    RELAY.STATE_PATH = pathlib.Path(self.td.name) / "state.json"

    def test_expired_ttl_returns_timeout(self):
        RELAY.controller.backup_restore_data = self._fake_success
        msg = dict(self._make_message(), expires_at=int(time.time() * 1000) - 1)
        RELAY._handle_backup_restore_data({}, self.state, local_id="m301", message=msg)
        self.assertEqual("TIMEOUT", self.sent[-1]["status"])
        self.assertFalse(self.sent[-1]["executed"])

    def test_wrong_package_ignored(self):
        msg = dict(self._make_message(), package="com.other.app")
        result = RELAY._handle_backup_restore_data({}, self.state, local_id="m301", message=msg)
        self.assertTrue(result)
        self.assertNotIn("ACCEPTED", [s["status"] for s in self.sent])

    def test_device_not_in_target_ids_ignored(self):
        msg = self._make_message()
        msg["target_device_ids"] = ["m999"]
        result = RELAY._handle_backup_restore_data({}, self.state, local_id="m301", message=msg)
        self.assertTrue(result)
        self.assertNotIn("ACCEPTED", [s["status"] for s in self.sent])

    def test_crash_prevents_re_execution(self):
        """Action marked processed at ACCEPTED, restart returns DUPLICATE."""
        executed = [0]

        def _crash(*a, **kw):
            executed[0] += 1
            raise RELAY.controller.AotControllerError("stage_timeout:backup_options_open")

        RELAY.controller.backup_restore_data = _crash
        msg = self._make_message(action_id="crash-test")
        RELAY._handle_batch_action({}, self.state, local_id="m301", message=msg)
        statuses = [s["status"] for s in self.sent]
        self.assertIn("FAILED", statuses)

        self.sent.clear()
        RELAY._handle_batch_action({}, self.state, local_id="m301", message=msg)
        self.assertEqual("FAILED", self.sent[-1]["status"])
        self.assertEqual(1, executed[0])

    def test_not_installed_sends_correct_status(self):
        RELAY._open_swift_backup = mock.Mock(
            side_effect=RELAY.AotRelayError("swift_backup_not_installed")
        )
        msg = self._make_message(action_id="not-installed")
        RELAY._handle_batch_action({}, self.state, local_id="m301", message=msg)
        statuses = [s["status"] for s in self.sent]
        self.assertIn("FAILED_NOT_INSTALLED", statuses)


# ── Legacy action regression tests ───────────────────────────────────────────

class TestLegacyActionsUnchanged(unittest.TestCase):
    def setUp(self):
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
        canonical_path = ROOT / "aot-group-control/worker-release.json"
        canonical_ver = json.loads(canonical_path.read_text(encoding="utf-8"))["version"]
        self.assertEqual(f"aot-worker-{canonical_ver}", RELAY.WORKER_VERSION)

    def test_fleet_hub_html_page_removed(self):
        """The browser WebApp HTML page has been fully removed from the worker."""
        src = (ROOT / "cloudflare-worker/worker.js").read_text()
        self.assertNotIn("function fleetHubHtml()", src)
        self.assertNotIn("async function handleAotHubPage", src)
        self.assertNotIn("telegram-web-app.js", src)
        self.assertNotIn("window.Telegram", src)
        self.assertNotIn("/aot/hub/api/state", src)
        self.assertNotIn("/aot/hub/api/control", src)
        self.assertNotIn("/aot/hub/api/ws", src)

    def test_backend_still_has_backup_restore_data(self):
        """backup_restore_data action is preserved in the Telegram bot and fleet control path."""
        src = (ROOT / "cloudflare-worker/worker.js").read_text()
        self.assertIn("backup_restore_data", src)
        self.assertIn("BACKUP_RESTORE_DATA", src)

    def test_backend_still_has_hub_buttons_via_telegram(self):
        """open_swift_backup and open_swift_apps remain accessible via Telegram /batch commands."""
        src = (ROOT / "cloudflare-worker/worker.js").read_text()
        self.assertIn("open_swift_backup", src)
        self.assertIn("open_swift_apps", src)

    def test_fleet_state_has_backup_restore_data_action(self):
        src = (ROOT / "cloudflare-worker/fleet-state.js").read_text()
        self.assertIn("AOT_BACKUP_RESTORE_DATA_ACTION", src)
        self.assertIn("BACKUP_RESTORE_DATA", src)

    def test_fleet_state_ack_includes_stage_statuses(self):
        src = (ROOT / "cloudflare-worker/fleet-state.js").read_text()
        for stage in ("SWIFT_OPENED", "FILTERED", "SELECTED",
                      "OPTIONS_VERIFIED", "BACKUP_STARTED"):
            self.assertIn(stage, src, f"Missing stage status {stage!r}")

    def test_worker_js_accepts_backup_restore_data(self):
        src = (ROOT / "cloudflare-worker/worker.js").read_text()
        self.assertIn("BACKUP_RESTORE_DATA", src)

    def test_agents_md_policy_reconciled(self):
        src = (ROOT / "AGENTS.md").read_text()
        self.assertIn("FILTER_RESTORE_DATA", src)
        self.assertIn("BACKUP_RESTORE_DATA", src)

class TestBoundaryTaps(unittest.TestCase):
    def _run_with_mocks(self, expire_before_tap=False, wait_for_exc=None):
        taps = []

        class TimeMock:
            def __init__(self):
                self.expired = False
            def __call__(self):
                return 15.0 if self.expired else 5.0

        tm = TimeMock()

        orig_find = CONTROLLER._find_unique_by_resource_ids
        def fake_find(nodes, *rids):
            if CONTROLLER._RID_FINAL_BACKUP in rids or CONTROLLER._RID_FINAL_BACKUP_ALT in rids:
                if expire_before_tap:
                    tm.expired = True
            return orig_find(nodes, *rids)

        with mock.patch.object(CONTROLLER, "foreground_package", return_value=_PKG), \
             mock.patch.object(CONTROLLER.time, "sleep"), \
             mock.patch.object(CONTROLLER, "swift_apps_screen_open", return_value=True), \
             mock.patch.object(CONTROLLER, "_restore_data_filter_active", return_value=True), \
             mock.patch.object(CONTROLLER, "_count_filtered_apps", return_value=3), \
             mock.patch.object(CONTROLLER, "_is_all_selected", return_value=True), \
             mock.patch.object(CONTROLLER, "_is_backup_running", return_value=False), \
             mock.patch.object(CONTROLLER, "_tap_xy", side_effect=lambda x, y: taps.append((x, y))):

            rotator = _Rotator([
                 APPS_SCREEN, APPS_SCREEN,
                 _apps_list(3, 3),
                 BATCH_MENU, BACKUP_MENU_SCREEN,
                 OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT,
                 OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT, OPTIONS_CORRECT,
                 OPTIONS_CORRECT, OPTIONS_CORRECT,
                 PROGRESS_SCREEN, PROGRESS_SCREEN,
            ])

            def fake_wait(*args, **kwargs):
                if wait_for_exc and args[1] == "backup_started":
                    raise wait_for_exc

            with mock.patch.object(CONTROLLER, "dump_ui_xml", side_effect=rotator), \
                 mock.patch.object(CONTROLLER, "_wait_for", side_effect=fake_wait), \
                 mock.patch.object(CONTROLLER, "_find_unique_by_resource_ids", side_effect=fake_find), \
                 mock.patch.object(CONTROLLER.time, "time", side_effect=tm):
                try:
                    result = CONTROLLER.backup_restore_data("test-boundary", deadline=10.0)
                except CONTROLLER.AotControllerError as exc:
                    result = {"action": "BACKUP_RESTORE_DATA", "executed": False, "error": str(exc).split(":")[0]}
        return result, taps

    def test_deadline_expires_before_tap(self):
        result, taps = self._run_with_mocks(expire_before_tap=True)
        self.assertEqual(0, len([t for t in taps if t == (90.0, 90.0)]))
        self.assertFalse(result.get("executed"))
        self.assertEqual("expired", result.get("error"))

    def test_deadline_expires_after_tap(self):
        result, taps = self._run_with_mocks(wait_for_exc=CONTROLLER.AotExpiredError("expired"))
        self.assertEqual(1, len([t for t in taps if t == (90.0, 90.0)]))
        self.assertTrue(result.get("executed"))
        self.assertEqual("TIMEOUT", result.get("status"))
        self.assertEqual("post_tap_start_unconfirmed", result.get("safe_reason"))

    def test_post_tap_verification_failure(self):
        result, taps = self._run_with_mocks(wait_for_exc=CONTROLLER.AotControllerError("stage_timeout:backup_started"))
        self.assertEqual(1, len([t for t in taps if t == (90.0, 90.0)]))
        self.assertTrue(result.get("executed"))
        self.assertEqual("FAILED", result.get("status"))
        self.assertEqual("post_tap_verification_failed", result.get("safe_reason"))

    def test_successful_verification(self):
        result, taps = self._run_with_mocks()
        self.assertEqual(1, len([t for t in taps if t == (90.0, 90.0)]))
        self.assertTrue(result.get("executed"))
        self.assertEqual("BACKUP_STARTED", result.get("status"))


if __name__ == "__main__":
    unittest.main()
