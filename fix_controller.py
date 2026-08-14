import re
import os
import pathlib

ROOT = pathlib.Path("/root/Aotscript-batch-ack")

controller_path = ROOT / "aot-group-control/controller.py"
with open(controller_path, "r") as f:
    src = f.read()

src = src.replace('BACKUP_STARTED', 'RESTORE_STARTED')

start_str = "def backup_restore_data("
end_str = "def swipe_normalized("

start_idx = src.find(start_str)
end_idx = src.find(end_str)

if start_idx == -1 or end_idx == -1:
    print("Could not find boundaries")
    exit(1)

new_logic = """def backup_restore_data(
    action_id: str,
    *,
    stage_cb=None,
    deadline: float | None = None,
) -> dict[str, Any]:
    \"\"\"Run the complete Swift Backup RESTORE_DATA full chain using robust UI state-machine.\"\"\"
    def _cb(stage: str) -> None:
        if stage_cb is not None:
            if deadline is not None and time.time() >= deadline:
                raise AotExpiredError("expired")
            try:
                stage_cb(stage)
            except Exception:
                pass

    _sb_assert_foreground()
    unknown = 0
    for step in range(80):
        if deadline is not None and time.time() >= deadline:
            raise AotExpiredError("expired")

        nodes = parse_ui_xml(dump_ui_xml())

        b = _smart_find("Restore options", nodes)
        if b:
            unknown = 0
            _tap_wait(b, deadline)
            continue

        b = _smart_find("Restore from cloud", nodes)
        if b:
            unknown = 0
            _tap_wait(b, deadline)
            continue

        if _find_text("Select labels", nodes):
            unknown = 0
            if _selected_count(nodes) > 0:
                b = _smart_find("Apply", nodes)
                if not b:
                    _save_unknown_debug("filter_apply_not_found")
                    raise AotControllerError("filter_apply_not_found")
                _tap_wait(b, deadline)
                _cb("FILTERED")
                continue
            b = _smart_find("RESTORE_DATA", nodes)
            if not b:
                _save_unknown_debug("restore_data_label_not_found")
                raise AotControllerError("restore_data_label_not_found")
            _tap_wait(b, deadline)
            continue

        if _find_text("APPLY OPTIONS", nodes) and _find_text("Labels: RESTORE_DATA", nodes):
            unknown = 0
            import shlex
            _root_run(f"{shlex.quote(INPUT)} keyevent KEYCODE_BACK")
            time.sleep(1)
            continue

        b = _smart_find("Select labels to filter", nodes)
        if b:
            unknown = 0
            _tap_wait(b, deadline)
            continue

        if _find_text("Labels: RESTORE_DATA", nodes):
            b = _smart_find("Batch actions", nodes)
            if b:
                unknown = 0
                _cb("SELECTED")
                _tap_wait(b, deadline)
                continue

        if _find_text("Batch actions", nodes):
            unknown = 0
            _cb("APPS_OPENED")
            if not _press_filter(nodes, deadline):
                _save_unknown_debug("filter_trigger_not_found")
                raise AotControllerError("filter_trigger_not_found")
            continue

        b = _smart_find("Apps", nodes)
        if b:
            unknown = 0
            _cb("SWIFT_OPENED")
            _tap_wait(b, deadline)
            continue

        if _find_text("User app parts", nodes):
            unknown = 0
            import struct
            
            apk_on, apk_card = _is_green_selected("APKs", nodes)
            if not apk_on:
                if not apk_card:
                    raise AotControllerError("selector_missing:APKs")
                _tap_wait(apk_card, deadline)
                time.sleep(0.7)
                nodes2 = parse_ui_xml(dump_ui_xml())
                apk_on2, _ = _is_green_selected("APKs", nodes2)
                if not apk_on2:
                    _save_unknown_debug("apks_toggle_failed")
                    raise AotControllerError("apks_toggle_failed")

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
            data_on, _ = _is_green_selected("Data", nodes)
            if not apk_on or not data_on:
                raise AotControllerError("options_verify_failed")

            _cb("OPTIONS_VERIFIED")

            b = _smart_find("RESTORE", nodes)
            if not b:
                _save_unknown_debug("final_restore_button_not_found")
                raise AotControllerError("final_restore_button_not_found")
            
            before_fp = ui_fingerprint(SWIFT_BACKUP_PACKAGE, nodes)
            _tap_xy(*b.center)
            
            def _restore_started() -> bool:
                _sb_assert_foreground()
                n = parse_ui_xml(dump_ui_xml())
                if _find_text("Restoring...", n):
                    return True
                return ui_fingerprint(SWIFT_BACKUP_PACKAGE, n) != before_fp

            try:
                _wait_for(_restore_started, "restore_started", timeout=45.0, absolute_deadline=deadline)
                _sb_assert_foreground()
            except AotExpiredError:
                return {
                    "action": BACKUP_RESTORE_DATA_ACTION,
                    "executed": True,
                    "status": "TIMEOUT",
                    "safe_reason": "post_tap_start_unconfirmed",
                }
            except (AotTimeoutError, AotControllerError):
                return {
                    "action": BACKUP_RESTORE_DATA_ACTION,
                    "executed": True,
                    "status": "FAILED",
                    "safe_reason": "post_tap_verification_failed",
                }

            return {
                "action": BACKUP_RESTORE_DATA_ACTION,
                "executed": True,
                "status": "RESTORE_STARTED",
            }

        unknown += 1
        if unknown < 4:
            time.sleep(1)
            continue

        _save_unknown_debug("unknown_ui_state")
        raise AotControllerError("unknown_ui_state")

    raise AotControllerError("state_machine_timeout")


def _save_unknown_debug(reason: str):
    debug_dir = "/sdcard/Download/SwiftDebug"
    import shlex
    _root_run(f"mkdir -p {debug_dir}")
    _root_run(f"cp /sdcard/window.xml {debug_dir}/window.xml")
    try:
        _root_run(f"screencap -p {debug_dir}/screen.png")
    except Exception:
        pass
    try:
        _root_run(f"echo {shlex.quote(reason)} > {debug_dir}/reason.txt")
    except Exception:
        pass

def _clean(val: str) -> str:
    return (val or "").strip().lower().lstrip("+").strip()

def _find_text(text: str, nodes: list[UiNode]) -> Bounds | None:
    target = _clean(text)
    for n in nodes:
        if _clean(n.text) == target or _clean(n.content_description) == target:
            return n.bounds
    return None

def _smart_find(text: str, nodes: list[UiNode]) -> Bounds | None:
    target = _clean(text)
    tb = None
    for n in nodes:
        if _clean(n.text) == target or _clean(n.content_description) == target:
            if n.clickable:
                return n.bounds
            tb = n.bounds
            break
    if not tb:
        return None
    candidates = []
    for n in nodes:
        if not n.clickable:
            continue
        if n.bounds.left <= tb.left and n.bounds.top <= tb.top and n.bounds.right >= tb.right and n.bounds.bottom >= tb.bottom:
            candidates.append((n.bounds.area, n.bounds))
    if candidates:
        return min(candidates, key=lambda x: x[0])[1]
    return tb

def _tap_wait(bounds: Bounds, deadline: float | None):
    p = pathlib.Path("/sdcard/window.xml")
    before_fp = hashlib.md5(p.read_bytes()).hexdigest() if p.exists() else ""
    _tap_xy(*bounds.center)
    end = time.time() + 4.0
    while time.time() < end:
        if deadline and time.time() >= deadline:
            break
        time.sleep(0.5)
        dump_ui_xml()
        after_fp = hashlib.md5(p.read_bytes()).hexdigest() if p.exists() else ""
        if after_fp != before_fp:
            return

def _selected_count(nodes: list[UiNode]) -> int:
    for n in nodes:
        for val in (n.text, n.content_description):
            m = re.fullmatch(r'\s*(\d+)\s*/\s*(\d+)\s*', val)
            if m:
                return int(m.group(1))
    return 0

def _press_filter(nodes: list[UiNode], deadline: float | None) -> bool:
    try:
        size_out = _root_run(f"{WM} size")
        m = re.search(r'(\d+)x(\d+)', size_out)
        if not m:
            return False
        W, H = map(int, m.groups())
    except Exception:
        return False
        
    buttons = []
    for n in nodes:
        if not n.clickable:
            continue
        cx, cy = n.bounds.center
        if cx > W * 0.60:
            buttons.append((cy, cx, n.bounds))
            
    if not buttons:
        return False
    top_y = min(x[0] for x in buttons)
    toolbar = [(cx, b) for cy, cx, b in buttons if abs(cy - top_y) <= H * 0.03]
    toolbar.sort(key=lambda x: x[0])
    if len(toolbar) < 2:
        return False
    _tap_wait(toolbar[-2][1], deadline)
    return True

def _raw_screencap():
    import struct
    try:
        proc = subprocess.run([TERMUX_SU, "-c", SCREENCAP], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        raw = proc.stdout
        if len(raw) < 12:
            raise AotControllerError("screencap_failed")
        w, h, fmt = struct.unpack("<III", raw[:12])
        pixel_bytes = w * h * 4
        header = len(raw) - pixel_bytes
        if header < 12 or header > 64:
            raise AotControllerError("screencap_invalid_format")
        return w, h, raw[header:]
    except Exception as e:
        raise AotControllerError("screencap_failed")

def _option_card(name: str, nodes: list[UiNode]) -> Bounds | None:
    target = _clean(name)
    tb = None
    for n in nodes:
        if _clean(n.text) == target or _clean(n.content_description) == target:
            tb = n.bounds
            break
    if not tb:
        return None
    tw = tb.width
    th = tb.height
    cands = []
    for n in nodes:
        b = n.bounds
        if b.left <= tb.left and b.top <= tb.top and b.right >= tb.right and b.bottom >= tb.bottom:
            if b.width >= tw + 20 and b.height >= th + 12 and b.height <= 180:
                cands.append((b.area, b))
    if cands:
        return min(cands, key=lambda x: x[0])[1]
    return tb

def _is_green_selected(name: str, nodes: list[UiNode]) -> tuple[bool, Bounds | None]:
    card = _option_card(name, nodes)
    if not card:
        return False, None
    w, h, pixels = _raw_screencap()
    x1 = max(0, min(w - 1, card.left))
    x2 = max(0, min(w, card.right))
    y1 = max(0, min(h - 1, card.top))
    y2 = max(0, min(h, card.bottom))
    green = 0
    total = 0
    for y in range(y1 + 2, max(y1 + 2, y2 - 2), 2):
        for x in range(x1 + 2, max(x1 + 2, x2 - 2), 2):
            i = (y * w + x) * 4
            r, g, b_ = pixels[i], pixels[i+1], pixels[i+2]
            total += 1
            if g >= 70 and g - r >= 10 and g - b_ >= 6:
                green += 1
    score = green / max(total, 1)
    return score > 0.20, card

"""

src = src[:start_idx] + new_logic + src[end_idx:]

with open(controller_path, "w") as f:
    f.write(src)

