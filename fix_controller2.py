import pathlib

ROOT = pathlib.Path("/root/Aotscript-batch-ack")
controller_path = ROOT / "aot-group-control/controller.py"

with open(controller_path, "r") as f:
    src = f.read()

# Replace unconditional nodes = parse_ui_xml(dump_ui_xml())
old_snippet = """
            nodes = parse_ui_xml(dump_ui_xml())
            data_on, data_card = _is_green_selected("Data", nodes)
            if not data_on:
                if not data_card:
                    raise AotControllerError("selector_missing:Data")
                _tap_wait(data_card, deadline)
                time.sleep(0.7)
                nodes2 = parse_ui_xml(dump_ui_xml())
                data_on2, _ = _is_green_selected("Data", nodes2)
                if not data_on2:
                    _save_unknown_debug("data_toggle_failed")
                    raise AotControllerError("data_toggle_failed")

            nodes = parse_ui_xml(dump_ui_xml())
            apk_on, _ = _is_green_selected("APKs", nodes)
"""

new_snippet = """
            data_on, data_card = _is_green_selected("Data", nodes)
            if not data_on:
                if not data_card:
                    raise AotControllerError("selector_missing:Data")
                _tap_wait(data_card, deadline)
                time.sleep(0.7)
                nodes = parse_ui_xml(dump_ui_xml())
                data_on2, _ = _is_green_selected("Data", nodes)
                if not data_on2:
                    _save_unknown_debug("data_toggle_failed")
                    raise AotControllerError("data_toggle_failed")

            apk_on, _ = _is_green_selected("APKs", nodes)
"""

# Wait, in apk_on block it does nodes2 = parse_ui_xml(dump_ui_xml()) but doesn't update nodes.
# Let's fix the whole "User app parts" block.
import re

block_start = src.find('if _find_text("User app parts", nodes):')
block_end = src.find('_cb("OPTIONS_VERIFIED")')

new_block = """if _find_text("User app parts", nodes):
            unknown = 0
            import struct
            
            apk_on, apk_card = _is_green_selected("APKs", nodes)
            if not apk_on:
                if not apk_card:
                    raise AotControllerError("selector_missing:APKs")
                _tap_wait(apk_card, deadline)
                time.sleep(0.7)
                nodes = parse_ui_xml(dump_ui_xml())
                apk_on2, _ = _is_green_selected("APKs", nodes)
                if not apk_on2:
                    _save_unknown_debug("apks_toggle_failed")
                    raise AotControllerError("apks_toggle_failed")

            data_on, data_card = _is_green_selected("Data", nodes)
            if not data_on:
                if not data_card:
                    raise AotControllerError("selector_missing:Data")
                _tap_wait(data_card, deadline)
                time.sleep(0.7)
                nodes = parse_ui_xml(dump_ui_xml())
                data_on2, _ = _is_green_selected("Data", nodes)
                if not data_on2:
                    _save_unknown_debug("data_toggle_failed")
                    raise AotControllerError("data_toggle_failed")

            apk_on, _ = _is_green_selected("APKs", nodes)
            data_on, _ = _is_green_selected("Data", nodes)
            if not apk_on or not data_on:
                raise AotControllerError("options_verify_failed")

            """

src = src[:block_start] + new_block + src[block_end:]

with open(controller_path, "w") as f:
    f.write(src)

