#!/data/data/com.termux/files/usr/bin/python3
from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
CONTROLLER = ROOT / "controller.py"
spec = importlib.util.spec_from_file_location("aot_controller", CONTROLLER)
if spec is None or spec.loader is None:
    raise SystemExit("SELFTEST_IMPORT=FAILED")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

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

# Fingerprint must distinguish navigation selection state.
state_nodes = module.parse_ui_xml(xml)
state_nodes[1].clickable = False
fingerprint_state = module.ui_fingerprint("pkg", state_nodes)
assert fingerprint_state != fingerprint_a

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

resolved = module.resolve_normalized_tap(nodes, 1000, 2000, 0.25, 0.15)
assert resolved["mode"] == "semantic"
assert resolved["resource_id"] == "pkg:id/account"


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
          android.widget.Button{bbb V.E...C.. ........ 100,1000-300,1200 #7f010001 app:id/nav_account}
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
activity_content = module._unique_resource_id(
    activity_nodes,
    "android:id/content",
)
assert activity_content.resource_id == "android:id/content"
negative = module.parse_bounds("[-10,-20][110,220]")
assert negative.as_list() == [-10, -20, 110, 220]

print("AOT_CONTROLLER_SELFTEST=OK")
