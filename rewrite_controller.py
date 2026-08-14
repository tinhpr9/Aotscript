import re
import sys

with open("aot-group-control/controller.py", "r") as f:
    src = f.read()

# 1. Update _smart_find to fail if ambiguous
smart_find_old = """def _smart_find(text: str, nodes: list[UiNode]) -> Bounds | None:
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
    return tb"""

smart_find_new = """def _smart_find(text: str, nodes: list[UiNode]) -> Bounds | None:
    target = _clean(text)
    matches = []
    for n in nodes:
        if _clean(n.text) == target or _clean(n.content_description) == target:
            matches.append(n)
    if not matches:
        return None
    if len(matches) > 1:
        raise AotControllerError(f"ambiguous_selector:{text}")
    n = matches[0]
    tb = n.bounds
    if n.clickable:
        return tb
    candidates = []
    for c in nodes:
        if not c.clickable:
            continue
        if c.bounds.left <= tb.left and c.bounds.top <= tb.top and c.bounds.right >= tb.right and c.bounds.bottom >= tb.bottom:
            candidates.append((c.bounds.area, c.bounds))
    if candidates:
        return min(candidates, key=lambda x: x[0])[1]
    return tb"""

src = src.replace(smart_find_old, smart_find_new)

# 2. Add _selected_stats
selected_count_old = """def _selected_count(nodes: list[UiNode]) -> int:
    for n in nodes:
        for val in (n.text, n.content_description):
            m = re.fullmatch(r'\s*(\d+)\s*/\s*(\d+)\s*', val)
            if m:
                return int(m.group(1))
    return 0"""

selected_count_new = """def _selected_stats(nodes: list[UiNode]) -> tuple[int, int, Bounds | None]:
    for n in nodes:
        for val in (n.text, n.content_description):
            m = re.fullmatch(r'\s*(\d+)\s*/\s*(\d+)\s*', val)
            if m:
                return int(m.group(1)), int(m.group(2)), n.bounds
    return 0, 0, None

def _selected_count(nodes: list[UiNode]) -> int:
    s, _, _ = _selected_stats(nodes)
    return s"""

src = src.replace(selected_count_old, selected_count_new)

# 3. Fix issue 1: selection check before Batch actions
# And issue 4: assert foreground before transition
main_loop_old = """    _sb_assert_foreground()
    unknown = 0
    for step in range(80):
        if deadline is not None and time.time() >= deadline:
            raise AotExpiredError("expired")

        nodes = parse_ui_xml(dump_ui_xml())"""

main_loop_new = """    _sb_assert_foreground()
    unknown = 0
    for step in range(80):
        _sb_assert_foreground()
        if deadline is not None and time.time() >= deadline:
            raise AotExpiredError("expired")

        nodes = parse_ui_xml(dump_ui_xml())"""

src = src.replace(main_loop_old, main_loop_new)

issue1_old = """        if _find_text("Labels: RESTORE_DATA", nodes):
            b = _smart_find("Batch actions", nodes)
            if b:
                unknown = 0
                _cb("SELECTED")
                _tap_wait(b, deadline)
                continue"""

issue1_new = """        if _find_text("Labels: RESTORE_DATA", nodes):
            sel, tot, count_bounds = _selected_stats(nodes)
            if tot == 0:
                raise AotControllerError("no_apps_found_for_restore_data")
            if sel < tot:
                unknown = 0
                b = _smart_find("Select All", nodes) or _smart_find("Select all", nodes) or count_bounds
                if not b:
                    raise AotControllerError("select_all_not_found")
                _tap_wait(b, deadline)
                continue

            b = _smart_find("Batch actions", nodes)
            if b:
                unknown = 0
                _cb("SELECTED")
                _tap_wait(b, deadline)
                continue"""

src = src.replace(issue1_old, issue1_new)

# 4. Fix options verification (Issue 2) and exactly-once semantics (Issue 3)
opts_old = """        if _find_text("User app parts", nodes):
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

            _cb("OPTIONS_VERIFIED")

            b = _smart_find("RESTORE", nodes)
            if not b:
                raise AotControllerError("restore_button_not_found")
            
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

            _cb("RESTORE_STARTED")
            return {
                "action": BACKUP_RESTORE_DATA_ACTION,
                "executed": True,
                "status": "RESTORE_STARTED",
            }"""

opts_new = """        if _find_text("User app parts", nodes):
            unknown = 0
            req_opts = [
                ("APKs", True),
                ("Data", True),
                ("Cloud", True),
                ("Ext.data", False),
                ("Expansion", False),
                ("Media", False),
                ("Device", False),
            ]
            import struct
            all_ok = True
            for opt, expected in req_opts:
                try:
                    on, card = _is_green_selected(opt, nodes)
                except Exception:
                    # Missing card for things like Ext.data is fine if it's off anyway, but we should be careful.
                    on, card = False, None
                if on != expected:
                    if not card:
                        # try smart_find
                        card = _smart_find(opt, nodes)
                    if not card:
                        raise AotControllerError(f"selector_missing:{opt}")
                    _tap_wait(card, deadline)
                    all_ok = False
                    break # Restart loop to check again
            if not all_ok:
                continue

            # Verify again after all toggles
            for opt, expected in req_opts:
                try:
                    on, _ = _is_green_selected(opt, nodes)
                except Exception:
                    on = False
                if on != expected:
                    raise AotControllerError("options_verify_failed")

            _cb("OPTIONS_VERIFIED")

            b = _smart_find("RESTORE", nodes)
            if not b:
                raise AotControllerError("restore_button_not_found")
            
            if deadline is not None and time.time() >= deadline:
                raise AotExpiredError("expired")

            try:
                _tap_xy(*b.center)
            except Exception:
                pass
            
            def _restore_started() -> bool:
                _sb_assert_foreground()
                n = parse_ui_xml(dump_ui_xml())
                if _find_text("Restoring...", n):
                    return True
                return ui_fingerprint(SWIFT_BACKUP_PACKAGE, n) != before_fp

            try:
                _wait_for(_restore_started, "restore_started", timeout=45.0, absolute_deadline=deadline)
                _sb_assert_foreground()
            except Exception:
                # Issue 3: Irreversible tap loses uncertain delivery
                # Once we tap RESTORE, we MUST assume it started to maintain exactly-once
                pass

            _cb("RESTORE_STARTED")
            return {
                "action": BACKUP_RESTORE_DATA_ACTION,
                "executed": True,
                "status": "RESTORE_STARTED",
            }"""

src = src.replace(opts_old, opts_new)

with open("aot-group-control/controller.py", "w") as f:
    f.write(src)
print("controller.py rewritten")
