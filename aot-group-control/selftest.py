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

resolved = module.resolve_normalized_tap(nodes, 1000, 2000, 0.25, 0.15)
assert resolved["mode"] == "semantic"
assert resolved["resource_id"] == "pkg:id/account"

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

print("AOT_CONTROLLER_SELFTEST=OK")
