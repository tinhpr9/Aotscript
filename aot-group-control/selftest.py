#!/data/data/com.termux/files/usr/bin/python3
from __future__ import annotations

import importlib.util
import pathlib
import sys
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent
CONTROLLER = ROOT / "controller.py"
spec = importlib.util.spec_from_file_location("aot_controller", CONTROLLER)
if spec is None or spec.loader is None:
    raise SystemExit("SELFTEST_IMPORT=FAILED")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module._root_shell_command("id") == (
    "PATH=/system/bin:/system/xbin:/vendor/bin; export PATH; id"
)

with mock.patch.object(module.os, "geteuid", return_value=0):
    assert module._root_command_argv("id") == [
        "/system/bin/sh",
        "-c",
        "PATH=/system/bin:/system/xbin:/vendor/bin; export PATH; id",
    ]

with mock.patch.object(module.os, "geteuid", return_value=2000):
    assert module._root_command_argv("id") == [
        "/data/data/com.termux/files/usr/bin/su",
        "-c",
        "PATH=/system/bin:/system/xbin:/vendor/bin; export PATH; id",
    ]

xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation='0'>
  <node index='0' class='android.widget.FrameLayout' resource-id='root'
        clickable='false' enabled='true' scrollable='false' password='false'
        bounds='[0,0][1000,2000]'>
    <node index='1' class='android.widget.Button' resource-id='pkg:id/account'
          clickable='true' enabled='true' scrollable='false' password='false'
          bounds='[100,200][500,500]' />
    <node index='2' class='android.widget.TextView' resource-id='pkg:id/title'
          clickable='false' enabled='true' scrollable='false' password='false'
          bounds='[600,200][900,400]' />
  </node>
</hierarchy>"""

bounds = module.parse_bounds("[10,20][110,220]")
assert bounds.center == (60, 120)
assert bounds.area == 20000

nodes = module.parse_ui_xml(xml)
assert len(nodes) == 3
assert nodes[1].resource_id == "pkg:id/account"
assert nodes[1].parent == 0

fingerprint_a = module.ui_fingerprint("pkg", nodes)
fingerprint_b = module.ui_fingerprint("pkg", list(reversed(nodes)))
assert fingerprint_a == fingerprint_b

# Device-specific dumpsys clickability metadata must not change canonical
# screen or interaction-layout identity when semantic structure is equal.
metadata_variant_nodes = module.parse_ui_xml(xml)
for node in metadata_variant_nodes:
    node.clickable = not node.clickable
assert (
    module.ui_fingerprint("pkg", metadata_variant_nodes)
    == fingerprint_a
)
assert (
    module.interaction_layout_signature(
        "pkg",
        metadata_variant_nodes,
        1000,
        2000,
        ime_visible=False,
    )
    == module.interaction_layout_signature(
        "pkg",
        nodes,
        1000,
        2000,
        ime_visible=False,
    )
)

# Canonical state must still distinguish navigation selection state.
state_nodes = module.parse_ui_xml(xml)
state_nodes[1].selected = True
fingerprint_state = module.ui_fingerprint("pkg", state_nodes)
assert fingerprint_state != fingerprint_a
assert (
    module.interaction_layout_signature(
        "pkg",
        state_nodes,
        1000,
        2000,
        ime_visible=False,
    )
    != module.interaction_layout_signature(
        "pkg",
        nodes,
        1000,
        2000,
        ime_visible=False,
    )
)

# Dynamic children inside a scrollable container must not make the screen
# fingerprint flap while the stable app chrome is unchanged.
dynamic_a = (
    "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n"
    "<hierarchy rotation='0'>\n"
    "  <node index='0' class='android.widget.FrameLayout' resource-id='root' "
    "clickable='false' enabled='true' scrollable='false' password='false' "
    "bounds='[0,0][1000,2000]'>\n"
    "    <node index='1' class='android.widget.Button' resource-id='pkg:id/nav' "
    "clickable='true' enabled='true' scrollable='false' password='false' "
    "bounds='[0,1700][1000,2000]' />\n"
    "    <node index='2' class='androidx.recyclerview.widget.RecyclerView' "
    "resource-id='pkg:id/list' clickable='false' enabled='true' "
    "scrollable='true' password='false' bounds='[0,0][1000,1700]'>\n"
    "      <node index='3' class='android.widget.TextView' "
    "resource-id='pkg:id/dynamic_a' clickable='true' enabled='true' "
    "scrollable='false' password='false' bounds='[0,0][500,200]' />\n"
    "    </node>\n"
    "  </node>\n"
    "</hierarchy>"
)

dynamic_b = dynamic_a.replace(
    "pkg:id/dynamic_a",
    "pkg:id/dynamic_b",
)

dynamic_nodes_a = module.parse_ui_xml(dynamic_a)
dynamic_nodes_b = module.parse_ui_xml(dynamic_b)

assert (
    module.ui_fingerprint("pkg", dynamic_nodes_a)
    == module.ui_fingerprint("pkg", dynamic_nodes_b)
)

# Fully clipped ViewPager pages must not affect the current-screen
# fingerprint merely because their un-clipped bounds have positive area.
clipped_page_xml = (
    "<hierarchy rotation='0'>"
    "<node class='Root' resource-id='pkg:id/root' clickable='false' "
    "enabled='true' scrollable='false' selected='false' password='false' "
    "bounds='[0,0][100,100]'>"
    "<node class='Pager' resource-id='pkg:id/pager' clickable='false' "
    "enabled='true' scrollable='false' selected='false' password='false' "
    "bounds='[0,0][100,100]'>"
    "<node class='Button' resource-id='pkg:id/current' clickable='true' "
    "enabled='true' scrollable='false' selected='true' password='false' "
    "bounds='[0,0][50,50]' />"
    "<node class='Button' resource-id='pkg:id/offscreen_a' clickable='true' "
    "enabled='true' scrollable='false' selected='false' password='false' "
    "bounds='[100,0][200,100]' />"
    "</node></node></hierarchy>"
)
clipped_page_nodes_a = module.parse_ui_xml(clipped_page_xml)
clipped_page_nodes_b = module.parse_ui_xml(
    clipped_page_xml.replace("offscreen_a", "offscreen_b")
)
assert [
    node.resource_id
    for node in module._stable_ui_candidates(clipped_page_nodes_a)
] == ["pkg:id/root", "pkg:id/pager", "pkg:id/current"]
assert (
    module.ui_fingerprint("pkg", clipped_page_nodes_a)
    == module.ui_fingerprint("pkg", clipped_page_nodes_b)
)

resolved = module.resolve_normalized_tap(nodes, 1000, 2000, 0.25, 0.15)
assert resolved["mode"] == "semantic"
assert resolved["resource_id"] == "pkg:id/account"

for invalid_x, invalid_y in (
    (-0.01, 0.5),
    (1.01, 0.5),
    (0.5, -0.01),
    (0.5, 1.01),
):
    try:
        module.resolve_normalized_tap(
            nodes,
            1000,
            2000,
            invalid_x,
            invalid_y,
        )
    except module.AotControllerError:
        pass
    else:
        raise AssertionError(
            "out-of-range normalized tap was accepted"
        )


# A tap on a repeated child resource-id must climb to the nearest unique
# actionable ancestor instead of degrading to unsafe coordinate mode.
nav_xml = (
    "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n"
    "<hierarchy rotation='0'>\n"
    "  <node index='0' class='android.widget.FrameLayout' resource-id='root' "
    "clickable='false' enabled='true' scrollable='false' password='false' "
    "bounds='[0,0][1000,2000]'>\n"
    "    <node index='1' class='android.widget.Button' "
    "resource-id='pkg:id/nav_home' clickable='true' enabled='true' "
    "scrollable='false' password='false' bounds='[0,1700][500,2000]'>\n"
    "      <node index='2' class='android.widget.TextView' "
    "resource-id='pkg:id/nav_label' clickable='false' enabled='true' "
    "scrollable='false' password='false' bounds='[50,1800][450,1960]' />\n"
    "    </node>\n"
    "    <node index='3' class='android.widget.Button' "
    "resource-id='pkg:id/nav_account' clickable='true' enabled='true' "
    "scrollable='false' password='false' bounds='[500,1700][1000,2000]'>\n"
    "      <node index='4' class='android.widget.TextView' "
    "resource-id='pkg:id/nav_label' clickable='false' enabled='true' "
    "scrollable='false' password='false' bounds='[550,1800][950,1960]' />\n"
    "    </node>\n"
    "  </node>\n"
    "</hierarchy>"
)

nav_nodes = module.parse_ui_xml(nav_xml)
nav_resolved = module.resolve_normalized_tap(
    nav_nodes,
    1000,
    2000,
    0.75,
    0.92,
)

assert nav_resolved["mode"] == "semantic"
assert nav_resolved["resource_id"] == "pkg:id/nav_account"

selected = module._unique_resource_id(nodes, "pkg:id/account")
assert selected.index == 1
assert module._clickable_target(nodes, selected).index == 1

try:
    module._verify_precondition("aaa", "bbb")
except module.AotControllerError:
    pass
else:
    raise AssertionError("precondition mismatch was accepted")

public = nodes[1].public()
assert "text" not in public
assert "content_desc" not in public


# UIAutomator bounds may use the rotated/logical input space while
# `wm size` reports the opposite orientation. The controller must keep
# normalized input in the same coordinate space as UI bounds.
landscape_xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation='1'>
  <node index='0' class='android.widget.FrameLayout' resource-id='root'
        clickable='false' enabled='true' scrollable='false' password='false'
        bounds='[0,0][1280,720]'>
    <node index='1' class='android.widget.Button'
          resource-id='pkg:id/nav_account'
          clickable='true' enabled='true'
          scrollable='false' password='false'
          bounds='[900,500][1200,700]' />
  </node>
</hierarchy>"""

landscape_nodes = module.parse_ui_xml(
    landscape_xml
)

ui_width, ui_height = module.ui_coordinate_size(
    landscape_nodes,
    720,
    1280,
)

assert (ui_width, ui_height) == (1280, 720)

account = landscape_nodes[1]
cx, cy = account.bounds.center

x_norm = cx / ui_width
y_norm = cy / ui_height

assert 0.0 <= x_norm <= 1.0
assert 0.0 <= y_norm <= 1.0

landscape_resolved = module.resolve_normalized_tap(
    landscape_nodes,
    ui_width,
    ui_height,
    x_norm,
    y_norm,
)

assert landscape_resolved["mode"] == "semantic"
assert (
    landscape_resolved["resource_id"]
    == "pkg:id/nav_account"
)

# Also support logical/physical scale mismatches where swapping the
# fallback dimensions is insufficient.
scaled_xml = landscape_xml.replace(
    "[0,0][1280,720]",
    "[0,0][1600,900]",
).replace(
    "[900,500][1200,700]",
    "[1200,650][1500,850]",
)

scaled_nodes = module.parse_ui_xml(
    scaled_xml
)

assert module.ui_coordinate_size(
    scaled_nodes,
    720,
    1280,
) == (1600, 900)

# Fallback parser for devices where `uiautomator dump` is killed.
activity_dump = """
TASK org.swiftapps.swiftbackup
  ACTIVITY org.swiftapps.swiftbackup/.MainActivity
    View Hierarchy:
      DecorView@abc[MainActivity]
        android.widget.FrameLayout{aaa V.E...... ........ 10,20-710,1260 #1020002 android:id/content}
          android.widget.Button{bbb V.E...C.. ..S..... 100,1000-300,1200 #7f010001 app:id/nav_account}
          android.widget.Button{ccc V.E...C.. ........ 300,1000-500,1200 #7f010002 app:id/nav_home}
    Looper (main, tid 1)
"""
activity_xml = module.activity_top_to_ui_xml(activity_dump)
activity_nodes = module.parse_ui_xml(activity_xml)
activity_account = module._unique_resource_id(
    activity_nodes,
    "org.swiftapps.swiftbackup:id/nav_account",
)
assert activity_account.bounds.as_list() == [110, 1020, 310, 1220]
assert activity_account.clickable is True
assert activity_account.enabled is True
assert activity_account.selected is True
activity_content = module._unique_resource_id(
    activity_nodes,
    "android:id/content",
)
assert activity_content.resource_id == "android:id/content"

# `dumpsys activity top` may contain several task hierarchies. The parser
# must select the foreground package instead of silently using the first one.
multi_activity_dump = """
  ACTIVITY com.google.android.keep/.activities.BrowseActivity
    View Hierarchy:
      DecorView@keep[BrowseActivity]
        android.widget.Button{aaa V.E...C.. ........ 0,0-100,100 #7f010001 app:id/menu_button}
    Looper (main, tid 1)
  ACTIVITY org.swiftapps.swiftbackup/.home.HomeActivity
    View Hierarchy:
      DecorView@swift[HomeActivity]
        android.widget.Button{bbb V.E...C.. ........ 0,100-100,200 #7f010002 app:id/nav_account}
    Looper (main, tid 1)
"""
multi_activity_xml = module.activity_top_to_ui_xml(
    multi_activity_dump,
    "org.swiftapps.swiftbackup",
)
multi_activity_nodes = module.parse_ui_xml(
    multi_activity_xml
)
assert module._unique_resource_id(
    multi_activity_nodes,
    "org.swiftapps.swiftbackup:id/nav_account",
).clickable is True
assert not any(
    node.resource_id == "com.google.android.keep:id/menu_button"
    for node in multi_activity_nodes
)
resumed_swift = """
  topResumedActivity=null
  mResumedActivity = org.swiftapps.swiftbackup/.home.HomeActivity
"""
stale_launcher_window = (
    "mCurrentFocus=Window{123 u0 "
    "ginlemon.flowerfree/ginlemon.flower.HomeScreen}\n"
    "mFocusedApp=ActivityRecord{456 u0 "
    "ginlemon.flowerfree/ginlemon.flower.HomeScreen}"
)

original_root_run = module._root_run


def foreground_probe_root_run(command, **_kwargs):
    if command == f"{module.DUMPSYS} activity activities":
        return resumed_swift
    if command == f"{module.DUMPSYS} window windows":
        return stale_launcher_window
    raise AssertionError(f"unexpected root command: {command}")


module._root_run = foreground_probe_root_run
try:
    assert (
        module.foreground_package()
        == "org.swiftapps.swiftbackup"
    )
finally:
    module._root_run = original_root_run


def no_focus_root_run(command, **_kwargs):
    if command == f"{module.DUMPSYS} activity activities":
        return resumed_swift
    if command == f"{module.DUMPSYS} window windows":
        return ""
    raise AssertionError(f"unexpected root command: {command}")


module._root_run = no_focus_root_run
try:
    assert (
        module.foreground_package()
        == "org.swiftapps.swiftbackup"
    )
finally:
    module._root_run = original_root_run


def fallback_root_run(command, **_kwargs):
    if command == f"{module.DUMPSYS} activity activities":
        return "mResumedActivity=null"
    if command == f"{module.DUMPSYS} window windows":
        return stale_launcher_window
    raise AssertionError(f"unexpected root command: {command}")


module._root_run = fallback_root_run
try:
    assert module.foreground_package() == "ginlemon.flowerfree"
finally:
    module._root_run = original_root_run


ambiguous_resumed = """
  mResumedActivity=org.example.first/.MainActivity
  mResumedActivity=org.example.second/.MainActivity
"""
try:
    module._activity_resumed_package(ambiguous_resumed)
except module.AotControllerError:
    pass
else:
    raise AssertionError("ambiguous resumed packages were accepted")


def hierarchy_root_run(command, **_kwargs):
    if command == f"{module.DUMPSYS} activity top":
        return multi_activity_dump
    if command == f"{module.DUMPSYS} activity activities":
        return resumed_swift
    if command == f"{module.DUMPSYS} window windows":
        return stale_launcher_window
    raise AssertionError(f"unexpected root command: {command}")


module._root_run = hierarchy_root_run
try:
    resolved_activity_xml = module.dump_ui_xml()
finally:
    module._root_run = original_root_run

resolved_activity_nodes = module.parse_ui_xml(
    resolved_activity_xml
)
assert module._unique_resource_id(
    resolved_activity_nodes,
    "org.swiftapps.swiftbackup:id/nav_account",
).clickable is True
assert not any(
    node.resource_id == "com.google.android.keep:id/menu_button"
    for node in resolved_activity_nodes
)

negative = module.parse_bounds("[-10,-20][110,220]")
assert negative.as_list() == [-10, -20, 110, 220]

# Interaction layout signatures normalize geometry across resolutions and
# fail coordinate readiness closed while an IME is visible or unknown.
def scaled_copy(source_nodes, factor):
    return [
        module.UiNode(
            index=node.index,
            parent=node.parent,
            class_name=node.class_name,
            resource_id=node.resource_id,
            bounds=module.Bounds(
                node.bounds.left * factor,
                node.bounds.top * factor,
                node.bounds.right * factor,
                node.bounds.bottom * factor,
            ),
            clickable=node.clickable,
            enabled=node.enabled,
            scrollable=node.scrollable,
            password=node.password,
            selected=node.selected,
        )
        for node in source_nodes
    ]

layout_a = module.interaction_layout_signature(
    "pkg",
    nav_nodes,
    1000,
    2000,
    ime_visible=False,
)
layout_scaled = module.interaction_layout_signature(
    "pkg",
    scaled_copy(nav_nodes, 2),
    2000,
    4000,
    ime_visible=False,
)
assert layout_a == layout_scaled

layout_changed_nodes = scaled_copy(nav_nodes, 1)
layout_changed_nodes[3].bounds = module.Bounds(
    400,
    1600,
    1000,
    2000,
)
layout_changed = module.interaction_layout_signature(
    "pkg",
    layout_changed_nodes,
    1000,
    2000,
    ime_visible=False,
)
assert layout_changed != layout_a


def ime_state_root_run(command, **_kwargs):
    if command == f"{module.DUMPSYS} input_method":
        return "mInputShown=false\nmIsInputViewShown=false\n"
    raise AssertionError(f"unexpected root command: {command}")


module._root_run = ime_state_root_run
try:
    hidden_layout = module.interaction_layout_state(
        "pkg",
        nav_nodes,
        1000,
        2000,
    )
finally:
    module._root_run = original_root_run
assert hidden_layout["coordinate_ready"] is True
assert hidden_layout["ime_visible"] is False


def visible_ime_root_run(command, **_kwargs):
    if command == f"{module.DUMPSYS} input_method":
        return "mInputShown=true\nmIsInputViewShown=true\n"
    raise AssertionError(f"unexpected root command: {command}")


module._root_run = visible_ime_root_run
try:
    visible_layout = module.interaction_layout_state(
        "pkg",
        nav_nodes,
        1000,
        2000,
    )
finally:
    module._root_run = original_root_run
assert visible_layout["coordinate_ready"] is False
assert visible_layout["ime_visible"] is True


def unknown_ime_root_run(command, **_kwargs):
    if command == f"{module.DUMPSYS} input_method":
        return "no visibility fields"
    raise AssertionError(f"unexpected root command: {command}")


module._root_run = unknown_ime_root_run
try:
    unknown_layout = module.interaction_layout_state(
        "pkg",
        nav_nodes,
        1000,
        2000,
    )
finally:
    module._root_run = original_root_run
assert unknown_layout["coordinate_ready"] is False
assert unknown_layout["ime_visible"] is None

# A selector absent from the primary hierarchy must retry the full-window
# hierarchy, while remaining semantic and precondition guarded.
primary_without_target = xml.replace(
    "pkg:id/account",
    "pkg:id/other",
)
snapshot_values = iter(
    (
        {"fingerprint": "before"},
        {"fingerprint": "after"},
    )
)
tapped = []
old_snapshot = module.snapshot
old_dump_ui_xml = module.dump_ui_xml
old_dump_full_ui_xml = module.dump_full_ui_xml
old_tap_xy = module._tap_xy
old_sleep = module.time.sleep
try:
    module.snapshot = lambda **_kwargs: next(snapshot_values)
    module.dump_ui_xml = lambda: primary_without_target
    module.dump_full_ui_xml = lambda: xml
    module._tap_xy = lambda x, y: tapped.append((x, y))
    module.time.sleep = lambda _seconds: None
    full_tap = module.tap_selector(
        "pkg:id/account",
        "before",
    )
finally:
    module.snapshot = old_snapshot
    module.dump_ui_xml = old_dump_ui_xml
    module.dump_full_ui_xml = old_dump_full_ui_xml
    module._tap_xy = old_tap_xy
    module.time.sleep = old_sleep
assert full_tap["hierarchy_source"] == "full_window"
assert full_tap["mode"] == "semantic"
assert tapped == [nodes[1].bounds.center]

apps_xml = """<hierarchy><node class='Root' resource-id='root' clickable='false' enabled='true' scrollable='false' password='false' bounds='[0,0][100,100]'><node class='Button' resource-id='org.swiftapps.swiftbackup:id/nav_apps' text='Apps' content-desc='Apps' clickable='true' enabled='true' scrollable='false' selected='false' password='false' bounds='[0,50][100,100]'/></node></hierarchy>"""
apps_selected_xml = apps_xml.replace("selected='false'", "selected='true'")
apps_taps = []
old_foreground = module.foreground_package
old_dump = module.dump_ui_xml
old_tap = module._tap_xy
old_sleep = module.time.sleep
try:
    module.foreground_package = lambda: module.SWIFT_BACKUP_PACKAGE
    dumps = iter((apps_xml, apps_xml, apps_selected_xml))
    module.dump_ui_xml = lambda: next(dumps)
    module._tap_xy = lambda x, y: apps_taps.append((x, y))
    module.time.sleep = lambda _seconds: None
    result = module.open_swift_apps()
finally:
    module.foreground_package = old_foreground
    module.dump_ui_xml = old_dump
    module._tap_xy = old_tap
    module.time.sleep = old_sleep
assert result["executed"] is True and apps_taps == [(50, 75)]

apps_button = apps_xml[apps_xml.index("<node class='Button'"):apps_xml.index("</node></hierarchy>")]
for bad_xml, reason in (
    ("<hierarchy/>", "swift_apps_selector_not_found"),
    (apps_xml.replace(apps_button, apps_button + apps_button), "swift_apps_selector_ambiguous"),
):
    try:
        module.foreground_package = lambda: module.SWIFT_BACKUP_PACKAGE
        module.dump_ui_xml = lambda: bad_xml
        module.open_swift_apps()
    except module.AotControllerError as exc:
        assert str(exc) == reason
    else:
        raise AssertionError(reason + " accepted")

# Structural semantic fallback test for open_swift_apps (regression for m74 without literal 'Apps')
apps_structural_xml = """<hierarchy><node class='Root' resource-id='root' bounds='[0,0][100,200]'>
    <node class='com.google.android.material.bottomnavigation.BottomNavigationView' resource-id='org.swiftapps.swiftbackup:id/bottom_navigation' bounds='[0,150][100,200]'>
        <node class='com.google.android.material.bottomnavigation.BottomNavigationItemView' resource-id='unknown_id_1' text='Trang chủ' clickable='true' enabled='true' bounds='[0,150][25,200]'/>
        <node class='com.google.android.material.bottomnavigation.BottomNavigationItemView' resource-id='unknown_id_2' text='Ứng dụng' clickable='true' enabled='true' bounds='[25,150][50,200]'/>
        <node class='com.google.android.material.bottomnavigation.BottomNavigationItemView' resource-id='unknown_id_3' text='Lịch trình' clickable='true' enabled='true' bounds='[50,150][75,200]'/>
        <node class='com.google.android.material.bottomnavigation.BottomNavigationItemView' resource-id='unknown_id_4' text='Tài khoản' clickable='true' enabled='true' bounds='[75,150][100,200]'/>
    </node>
</node></hierarchy>"""
apps_structural_selected_xml = apps_structural_xml.replace("text='Ứng dụng' clickable='true' enabled='true'", "text='Ứng dụng' clickable='true' enabled='true' selected='true'")
apps_taps.clear()
try:
    module.foreground_package = lambda: module.SWIFT_BACKUP_PACKAGE
    dumps = iter((apps_structural_xml, apps_structural_xml, apps_structural_selected_xml))
    module.dump_ui_xml = lambda: next(dumps)
    module._tap_xy = lambda x, y: apps_taps.append((x, y))
    module.time.sleep = lambda _seconds: None
    result = module.open_swift_apps()
finally:
    module.foreground_package = old_foreground
    module.dump_ui_xml = old_dump
    module._tap_xy = old_tap
    module.time.sleep = old_sleep
assert result["executed"] is True and apps_taps == [(37, 175)]

module.foreground_package = old_foreground
module.dump_ui_xml = old_dump

print("AOT_CONTROLLER_SELFTEST=OK")
