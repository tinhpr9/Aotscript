#!/data/data/com.termux/files/usr/bin/python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

UIAUTOMATOR = "/system/bin/uiautomator"
SCREENCAP = "/system/bin/screencap"
INPUT = "/system/bin/input"
DUMPSYS = "/system/bin/dumpsys"
WM = "/system/bin/wm"
ANDROID_ROOT_PATH = "/system/bin:/system/xbin:/vendor/bin"
TERMUX_SU = "/data/data/com.termux/files/usr/bin/su"
DEVICE_ID_PATH = pathlib.Path(
    "/storage/emulated/0/Download/Shouko/device_id.txt"
)
DEVICE_GROUP_PATH = pathlib.Path(
    "/storage/emulated/0/Download/Shouko/device_group.txt"
)


class AotControllerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Bounds:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def center(self) -> tuple[int, int]:
        return (
            self.left + self.width // 2,
            self.top + self.height // 2,
        )

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def as_list(self) -> list[int]:
        return [self.left, self.top, self.right, self.bottom]


@dataclass
class UiNode:
    index: int
    parent: int | None
    class_name: str
    resource_id: str
    bounds: Bounds
    clickable: bool
    enabled: bool
    scrollable: bool
    password: bool
    selected: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "parent": self.parent,
            "class": self.class_name,
            "resource_id": self.resource_id,
            "bounds": self.bounds.as_list(),
            "clickable": self.clickable,
            "enabled": self.enabled,
            "scrollable": self.scrollable,
            "password": self.password,
            "selected": self.selected,
        }


def _bool_attr(value: str | None) -> bool:
    return str(value or "").strip().lower() == "true"


def parse_bounds(value: str) -> Bounds:
    match = re.fullmatch(
        r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]",
        value.strip(),
    )
    if not match:
        raise AotControllerError(f"invalid bounds: {value!r}")
    left, top, right, bottom = map(int, match.groups())
    if right < left or bottom < top:
        raise AotControllerError("negative bounds size")
    return Bounds(left, top, right, bottom)


def _root_shell_command(command: str) -> str:
    return (
        f"PATH={shlex.quote(ANDROID_ROOT_PATH)}; export PATH; {command}"
    )


def _root_command_argv(command: str) -> list[str]:
    return [
        TERMUX_SU,
        "-c",
        _root_shell_command(command),
    ]


def _root_run(command: str, *, binary: bool = False, timeout: int = 12):
    try:
        proc = subprocess.run(
            _root_command_argv(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            text=not binary,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AotControllerError(f"root command failed: {type(exc).__name__}") from exc
    if proc.returncode != 0:
        if binary:
            detail = proc.stderr.decode("utf-8", errors="replace").strip()
        else:
            detail = proc.stderr.strip()
        detail = detail[:240]
        raise AotControllerError(
            f"root command rc={proc.returncode}: {detail or 'no stderr'}"
        )
    return proc.stdout


def root_available() -> bool:
    try:
        output = _root_run("id", timeout=5)
    except AotControllerError:
        return False
    return "uid=0(root)" in output


def _read_small(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def device_identity() -> dict[str, str]:
    return {
        "device_id": _read_small(DEVICE_ID_PATH),
        "device_group": _read_small(DEVICE_GROUP_PATH),
    }


def _activity_resumed_package(text: str) -> str | None:
    field_re = re.compile(
        r"^\s*"
        r"(topResumedActivity|mResumedActivity)"
        r"\s*[:=]\s*(.*?)\s*$"
    )
    component_re = re.compile(
        r"(?<![A-Za-z0-9._])"
        r"([A-Za-z][A-Za-z0-9._]*)/"
        r"[A-Za-z0-9._$]+"
    )
    candidates: dict[str, list[str]] = {
        "topResumedActivity": [],
        "mResumedActivity": [],
    }

    for raw in text.splitlines():
        field_match = field_re.fullmatch(raw)
        if field_match is None:
            continue
        field, value = field_match.groups()
        if not value or value.lower() in {"null", "none"}:
            continue
        packages = component_re.findall(value)
        if len(packages) != 1:
            raise AotControllerError(
                "resumed activity component is ambiguous"
            )
        candidates[field].append(packages[0])

    for field in ("topResumedActivity", "mResumedActivity"):
        packages = set(candidates[field])
        if len(packages) == 1:
            return next(iter(packages))
        if len(packages) > 1:
            raise AotControllerError(
                "resumed activity package is ambiguous"
            )
    return None

def foreground_package() -> str:
    try:
        activity_output = _root_run(
            f"{shlex.quote(DUMPSYS)} activity activities"
        )
    except AotControllerError:
        activity_output = ""
    if activity_output:
        resumed_package = _activity_resumed_package(
            activity_output
        )
        if resumed_package:
            return resumed_package

    output = _root_run(f"{shlex.quote(DUMPSYS)} window windows")
    patterns = (
        r"mCurrentFocus=.*?\s([A-Za-z0-9._]+)/(?:[A-Za-z0-9._$]+)",
        r"mFocusedApp=.*?\s([A-Za-z0-9._]+)/(?:[A-Za-z0-9._$]+)",
        r"\bu\d+\s+([A-Za-z0-9._]+)/(?:[A-Za-z0-9._$]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, output)
        if match:
            return match.group(1)
    return "UNKNOWN"


def display_size() -> tuple[int, int]:
    output = _root_run(f"{shlex.quote(WM)} size")
    matches = re.findall(r"(?:Override|Physical) size:\s*(\d+)x(\d+)", output)
    if not matches:
        raise AotControllerError("wm size unavailable")
    width, height = matches[-1]
    return int(width), int(height)


def ui_coordinate_size(
    nodes: list[UiNode],
    fallback_width: int,
    fallback_height: int,
) -> tuple[int, int]:
    if fallback_width <= 0 or fallback_height <= 0:
        raise AotControllerError(
            "invalid fallback display size"
        )

    visible = [
        node
        for node in nodes
        if node.bounds.area > 0
    ]

    if not visible:
        return fallback_width, fallback_height

    max_right = max(
        node.bounds.right
        for node in visible
    )
    max_bottom = max(
        node.bounds.bottom
        for node in visible
    )

    candidates = (
        (fallback_width, fallback_height),
        (fallback_height, fallback_width),
    )

    for width, height in candidates:
        if (
            max_right <= width
            and max_bottom <= height
        ):
            return width, height

    return max_right, max_bottom


def activity_top_to_ui_xml(
    text: str,
    foreground_package_name: str = "",
) -> str:
    lines = text.splitlines()
    activity_re = re.compile(
        r"^\s*ACTIVITY\s+([A-Za-z0-9._]+)/"
    )
    activity_index = None
    activity_package = ""
    for index, raw in enumerate(lines):
        activity_match = activity_re.match(raw)
        if activity_match is None:
            continue
        candidate_package = activity_match.group(1)
        if (
            foreground_package_name
            and candidate_package != foreground_package_name
        ):
            continue
        activity_index = index
        activity_package = candidate_package
        break
    if activity_index is None:
        raise AotControllerError(
            "dumpsys activity top has no matching activity"
        )

    marker_index = None
    marker_indent = 0
    for index in range(activity_index + 1, len(lines)):
        raw = lines[index]
        if activity_re.match(raw):
            break
        if raw.strip() == "View Hierarchy:":
            marker_index = index
            marker_indent = len(raw) - len(raw.lstrip(" "))
            break
    if marker_index is None:
        raise AotControllerError(
            "dumpsys activity top has no View Hierarchy"
        )

    view_re = re.compile(
        r"^(?P<class>[A-Za-z0-9_.$]+)\{(?P<body>.*)\}$"
    )
    coord_re = re.compile(
        r"(?<!\S)(-?\d+),(-?\d+)-(-?\d+),(-?\d+)(?=\s|$)"
    )
    resource_re = re.compile(
        r"(?:^|\s)([A-Za-z0-9_.]+:id/[A-Za-z0-9_.$]+)(?=\s|$)"
    )

    root = ET.Element(
        "hierarchy",
        {
            "rotation": "0",
            "source": "dumpsys_activity_top",
        },
    )
    stack: list[tuple[int, ET.Element, int, int]] = []
    parsed = 0

    for raw in lines[marker_index + 1 :]:
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if parsed and indent <= marker_indent:
            break
        stripped = raw.strip()
        match = view_re.fullmatch(stripped)
        if not match:
            continue
        body = match.group("body")
        coord = coord_re.search(body)
        if coord is None:
            continue

        local_left, local_top, local_right, local_bottom = map(
            int,
            coord.groups(),
        )
        if local_right < local_left or local_bottom < local_top:
            continue

        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stack:
            parent_element = stack[-1][1]
            parent_left = stack[-1][2]
            parent_top = stack[-1][3]
        else:
            parent_element = root
            parent_left = 0
            parent_top = 0

        abs_left = parent_left + local_left
        abs_top = parent_top + local_top
        abs_right = abs_left + (local_right - local_left)
        abs_bottom = abs_top + (local_bottom - local_top)

        parts = body.split()
        flags = parts[1] if len(parts) > 1 else ""
        private_flags = parts[2] if len(parts) > 2 else ""
        visible = len(flags) >= 1 and flags[0] == "V"
        enabled = visible and len(flags) >= 3 and flags[2] == "E"
        clickable = len(flags) >= 7 and flags[6] == "C"
        selected = (
            len(private_flags) >= 3
            and private_flags[2] == "S"
        )
        class_name = match.group("class")
        scrollable = (
            (len(flags) >= 6 and flags[4] == "H")
            or (len(flags) >= 6 and flags[5] == "V")
            or class_name.endswith(
                (
                    "ScrollView",
                    "RecyclerView",
                    "ListView",
                    "ViewPager",
                    "ViewPager2",
                )
            )
        )
        resource_match = resource_re.search(body)
        resource_id = (
            resource_match.group(1)
            if resource_match is not None
            else ""
        )
        if resource_id.startswith("app:id/"):
            if not activity_package:
                raise AotControllerError(
                    "dumpsys activity top app resource without package"
                )
            resource_id = (
                activity_package
                + resource_id[len("app") :]
            )
        if not visible:
            abs_left = abs_top = abs_right = abs_bottom = 0

        element = ET.SubElement(
            parent_element,
            "node",
            {
                "class": class_name,
                "resource-id": resource_id,
                "clickable": "true" if clickable else "false",
                "enabled": "true" if enabled else "false",
                "scrollable": "true" if scrollable else "false",
                "password": "false",
                "selected": "true" if selected else "false",
                "bounds": (
                    f"[{abs_left},{abs_top}]"
                    f"[{abs_right},{abs_bottom}]"
                ),
            },
        )
        stack.append((indent, element, abs_left, abs_top))
        parsed += 1

    if parsed == 0:
        raise AotControllerError(
            "dumpsys activity top produced no parseable views"
        )
    return ET.tostring(root, encoding="unicode")


def dump_full_ui_xml() -> str:
    remote = f"/data/local/tmp/aot-group-ui-{os.getpid()}.xml"
    command = (
        f"{shlex.quote(UIAUTOMATOR)} dump --compressed {shlex.quote(remote)} "
        f">/dev/null 2>&1 && cat {shlex.quote(remote)}; "
        f"rc=$?; rm -f {shlex.quote(remote)}; exit $rc"
    )
    output = _root_run(command, timeout=15)
    if "<hierarchy" not in output:
        raise AotControllerError("uiautomator produced no hierarchy")
    return output

def dump_ui_xml() -> str:
    try:
        activity_dump = _root_run(
            f"{shlex.quote(DUMPSYS)} activity top",
            timeout=15,
        )
        return activity_top_to_ui_xml(
            activity_dump,
            foreground_package(),
        )
    except AotControllerError:
        return dump_full_ui_xml()


def parse_ui_xml(xml_text: str) -> list[UiNode]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise AotControllerError("invalid UI XML") from exc

    nodes: list[UiNode] = []

    def walk(element: ET.Element, parent: int | None) -> None:
        for child in list(element):
            if child.tag != "node":
                walk(child, parent)
                continue
            index = len(nodes)
            try:
                bounds = parse_bounds(child.attrib.get("bounds", ""))
            except AotControllerError:
                bounds = Bounds(0, 0, 0, 0)
            nodes.append(
                UiNode(
                    index=index,
                    parent=parent,
                    class_name=child.attrib.get("class", ""),
                    resource_id=child.attrib.get("resource-id", ""),
                    bounds=bounds,
                    clickable=_bool_attr(child.attrib.get("clickable")),
                    enabled=_bool_attr(child.attrib.get("enabled")),
                    scrollable=_bool_attr(child.attrib.get("scrollable")),
                    password=_bool_attr(child.attrib.get("password")),
                    selected=_bool_attr(child.attrib.get("selected")),
                )
            )
            walk(child, index)

    walk(root, None)
    return nodes


def _stable_ui_candidates(nodes: list[UiNode]) -> list[UiNode]:
    by_index = {node.index: node for node in nodes}

    def has_scrollable_ancestor(node: UiNode) -> bool:
        parent = node.parent
        visited: set[int] = set()
        while parent is not None and parent not in visited:
            visited.add(parent)
            ancestor = by_index.get(parent)
            if ancestor is None:
                return False
            if ancestor.scrollable:
                return True
            parent = ancestor.parent
        return False

    return [
        node
        for node in nodes
        if (
            node.resource_id
            and node.bounds.area > 0
            and not has_scrollable_ancestor(node)
        )
    ]


def ui_fingerprint(package: str, nodes: list[UiNode]) -> str:
    pool = _stable_ui_candidates(nodes)

    counts: dict[str, int] = {}
    for node in pool:
        counts[node.resource_id] = counts.get(node.resource_id, 0) + 1

    stable = []
    for node in pool:
        if counts.get(node.resource_id) != 1:
            continue
        stable.append(
            "|".join(
                (
                    node.resource_id,
                    node.class_name,
                    "1" if node.selected else "0",
                    "1" if node.scrollable else "0",
                )
            )
        )

    if not stable:
        stable = [
            "|".join(
                (
                    node.class_name,
                    "1" if node.selected else "0",
                    "1" if node.scrollable else "0",
                )
            )
            for node in nodes
        ]

    payload = package + "\n" + "\n".join(sorted(stable))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def interaction_layout_signature(
    package: str,
    nodes: list[UiNode],
    width: int,
    height: int,
    *,
    ime_visible: bool | None,
) -> str:
    if width <= 0 or height <= 0:
        raise AotControllerError("invalid interaction layout size")

    candidates = _stable_ui_candidates(nodes)
    resource_counts: dict[str, int] = {}
    for node in candidates:
        resource_counts[node.resource_id] = (
            resource_counts.get(node.resource_id, 0) + 1
        )
    candidate_indexes = {node.index for node in candidates}

    def normalized(value: int, extent: int) -> int:
        return min(
            1000,
            max(0, round(value * 1000 / extent)),
        )

    stable: list[str] = []
    fallback: list[str] = []
    for node in nodes:
        if (
            not node.enabled
            or node.bounds.area <= 0
        ):
            continue
        geometry = ",".join(
            str(value)
            for value in (
                normalized(node.bounds.left, width),
                normalized(node.bounds.top, height),
                normalized(node.bounds.right, width),
                normalized(node.bounds.bottom, height),
            )
        )
        entry = "|".join(
            (
                node.class_name,
                "1" if node.selected else "0",
                "1" if node.scrollable else "0",
                geometry,
            )
        )
        fallback.append(entry)
        if (
            node.index in candidate_indexes
            and resource_counts.get(node.resource_id) == 1
        ):
            stable.append(node.resource_id + "|" + entry)

    pool = stable or fallback or ["EMPTY"]
    orientation = "landscape" if width >= height else "portrait"
    if ime_visible is True:
        ime_state = "visible"
    elif ime_visible is False:
        ime_state = "hidden"
    else:
        ime_state = "unknown"
    payload = "\n".join(
        (
            package,
            orientation,
            ime_state,
            *sorted(pool),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _ime_layout_state() -> tuple[bool, bool | None]:
    try:
        output = _root_run(
            f"{shlex.quote(DUMPSYS)} input_method",
            timeout=12,
        )
    except AotControllerError:
        return False, None

    values = re.findall(
        r"(?:mInputShown|mIsInputViewShown|inputShown)"
        r"\s*=\s*(true|false)",
        output,
        flags=re.IGNORECASE,
    )
    if not values:
        return False, None
    visible = any(value.lower() == "true" for value in values)
    return True, visible


def interaction_layout_state(
    package: str,
    nodes: list[UiNode],
    width: int,
    height: int,
) -> dict[str, Any]:
    visibility_ready, ime_visible = _ime_layout_state()
    return {
        "layout_signature": interaction_layout_signature(
            package,
            nodes,
            width,
            height,
            ime_visible=ime_visible,
        ),
        "coordinate_ready": (
            visibility_ready
            and ime_visible is False
        ),
        "ime_visible": ime_visible,
    }

def snapshot(
    *,
    include_nodes: bool = True,
) -> dict[str, Any]:
    fallback_width, fallback_height = display_size()
    nodes = parse_ui_xml(dump_ui_xml())
    width, height = ui_coordinate_size(
        nodes,
        fallback_width,
        fallback_height,
    )
    package = foreground_package()
    layout = interaction_layout_state(
        package,
        nodes,
        width,
        height,
    )
    data: dict[str, Any] = {
        "package": package,
        "fingerprint": ui_fingerprint(
            package,
            nodes,
        ),
        "layout_signature": layout["layout_signature"],
        "coordinate_ready": layout["coordinate_ready"],
        "ime_visible": layout["ime_visible"],
        "width": width,
        "height": height,
        "node_count": len(nodes),
        "resource_id_count": sum(
            bool(node.resource_id)
            for node in nodes
        ),
        "clickable_count": sum(
            node.clickable
            for node in nodes
        ),
    }
    if include_nodes:
        data["nodes"] = [
            node.public()
            for node in nodes
        ]
    return data


def _unique_resource_id(nodes: list[UiNode], resource_id: str) -> UiNode:
    matches = [node for node in nodes if node.resource_id == resource_id]
    if len(matches) != 1:
        raise AotControllerError(
            f"selector match count={len(matches)} for {resource_id!r}"
        )
    return matches[0]


def _clickable_target(nodes: list[UiNode], node: UiNode) -> UiNode:
    current = node
    visited: set[int] = set()
    while True:
        if current.enabled and current.clickable and current.bounds.area > 0:
            return current
        if current.parent is None or current.parent in visited:
            break
        visited.add(current.index)
        current = nodes[current.parent]
    if node.enabled and node.bounds.area > 0:
        return node
    raise AotControllerError("selector has no actionable bounds")


def resolve_normalized_tap(
    nodes: list[UiNode],
    width: int,
    height: int,
    x_norm: float,
    y_norm: float,
) -> dict[str, Any]:
    if not (0.0 <= x_norm <= 1.0 and 0.0 <= y_norm <= 1.0):
        raise AotControllerError("normalized tap must be in [0,1]")

    x = min(width - 1, max(0, round(x_norm * width)))
    y = min(height - 1, max(0, round(y_norm * height)))

    resource_counts: dict[str, int] = {}
    for node in nodes:
        if node.resource_id:
            resource_counts[node.resource_id] = (
                resource_counts.get(node.resource_id, 0) + 1
            )

    def semantic_selector_from(node: UiNode) -> tuple[str, int] | None:
        current = node
        visited: set[int] = set()

        while True:
            if (
                current.enabled
                and current.bounds.area > 0
                and current.resource_id
                and resource_counts.get(current.resource_id) == 1
            ):
                probe = current
                probe_visited: set[int] = set()

                while True:
                    if (
                        probe.enabled
                        and probe.clickable
                        and probe.bounds.area > 0
                    ):
                        return current.resource_id, current.index

                    if (
                        probe.parent is None
                        or probe.parent in probe_visited
                    ):
                        break

                    probe_visited.add(probe.index)
                    probe = nodes[probe.parent]

            if (
                current.parent is None
                or current.parent in visited
            ):
                break

            visited.add(current.index)
            current = nodes[current.parent]

        return None

    candidates = [
        node
        for node in nodes
        if (
            node.enabled
            and node.bounds.area > 0
            and node.bounds.contains(x, y)
        )
    ]

    candidates.sort(
        key=lambda node: (
            node.bounds.area,
            -node.index,
        )
    )

    for node in candidates:
        resolved = semantic_selector_from(node)
        if resolved is None:
            continue

        resource_id, node_index = resolved
        return {
            "mode": "semantic",
            "resource_id": resource_id,
            "x": x,
            "y": y,
            "node_index": node_index,
        }

    return {
        "mode": "coordinate",
        "x": x,
        "y": y,
        "x_norm": x_norm,
        "y_norm": y_norm,
    }


def _verify_precondition(current: str, expected: str | None) -> None:
    if expected and current != expected:
        raise AotControllerError(
            f"PRECONDITION_FAILED current={current} expected={expected}"
        )


def _tap_xy(x: int, y: int) -> None:
    _root_run(f"{shlex.quote(INPUT)} tap {int(x)} {int(y)}")


def tap_selector(
    resource_id: str,
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    before = snapshot(include_nodes=False)
    _verify_precondition(before["fingerprint"], expected_fingerprint)
    nodes = parse_ui_xml(dump_ui_xml())
    matches = [
        node
        for node in nodes
        if node.resource_id == resource_id
    ]
    hierarchy_source = "primary"
    if len(matches) > 1:
        _unique_resource_id(nodes, resource_id)
    try:
        node = _clickable_target(
            nodes,
            _unique_resource_id(nodes, resource_id),
        )
    except AotControllerError:
        nodes = parse_ui_xml(dump_full_ui_xml())
        node = _clickable_target(
            nodes,
            _unique_resource_id(nodes, resource_id),
        )
        hierarchy_source = "full_window"
    x, y = node.bounds.center
    _tap_xy(x, y)
    time.sleep(0.25)
    after = snapshot(include_nodes=False)
    return {
        "action": "tap",
        "mode": "semantic",
        "hierarchy_source": hierarchy_source,
        "resource_id": resource_id,
        "before_fingerprint": before["fingerprint"],
        "after_fingerprint": after["fingerprint"],
        "screen_changed": before["fingerprint"] != after["fingerprint"],
    }


def swipe_normalized(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    duration_ms: int = 300,
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    before = snapshot(include_nodes=False)
    _verify_precondition(before["fingerprint"], expected_fingerprint)
    width = int(before["width"])
    height = int(before["height"])
    values = (x1, y1, x2, y2)
    if any(value < 0.0 or value > 1.0 for value in values):
        raise AotControllerError("normalized swipe must be in [0,1]")
    px = [
        round(x1 * width),
        round(y1 * height),
        round(x2 * width),
        round(y2 * height),
    ]
    duration_ms = min(5000, max(50, int(duration_ms)))
    _root_run(
        f"{shlex.quote(INPUT)} swipe {px[0]} {px[1]} {px[2]} {px[3]} {duration_ms}"
    )
    time.sleep(0.25)
    after = snapshot(include_nodes=False)
    return {
        "action": "swipe",
        "before_fingerprint": before["fingerprint"],
        "after_fingerprint": after["fingerprint"],
        "screen_changed": before["fingerprint"] != after["fingerprint"],
    }


def press_back(expected_fingerprint: str | None = None) -> dict[str, Any]:
    before = snapshot(include_nodes=False)
    _verify_precondition(before["fingerprint"], expected_fingerprint)
    _root_run(f"{shlex.quote(INPUT)} keyevent 4")
    time.sleep(0.25)
    after = snapshot(include_nodes=False)
    return {
        "action": "back",
        "before_fingerprint": before["fingerprint"],
        "after_fingerprint": after["fingerprint"],
        "screen_changed": before["fingerprint"] != after["fingerprint"],
    }


def screenshot_bytes() -> bytes:
    data = _root_run(shlex.quote(SCREENCAP) + " -p", binary=True, timeout=15)
    if not isinstance(data, bytes) or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AotControllerError("screencap did not return PNG")
    return data


def save_screenshot(path: pathlib.Path) -> dict[str, Any]:
    data = screenshot_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        temp.write_bytes(data)
        if not temp.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise AotControllerError("temporary screenshot validation failed")
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
    return {"path": str(path), "bytes": len(data)}


def probe() -> dict[str, Any]:
    identity = device_identity()
    data: dict[str, Any] = {
        "root": root_available(),
        **identity,
    }
    if not data["root"]:
        return data
    snap = snapshot(include_nodes=False)
    frame = screenshot_bytes()
    data.update(snap)
    data["screenshot_bytes"] = len(frame)
    return data


def _json_print(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AOT Group Control local controller MVP")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health")
    sub.add_parser("probe")
    sub.add_parser("snapshot")

    resolve = sub.add_parser("resolve-tap")
    resolve.add_argument("x_norm", type=float)
    resolve.add_argument("y_norm", type=float)

    tap = sub.add_parser("tap-selector")
    tap.add_argument("resource_id")
    tap.add_argument("--expect-fingerprint")

    swipe = sub.add_parser("swipe")
    swipe.add_argument("x1", type=float)
    swipe.add_argument("y1", type=float)
    swipe.add_argument("x2", type=float)
    swipe.add_argument("y2", type=float)
    swipe.add_argument("--duration-ms", type=int, default=300)
    swipe.add_argument("--expect-fingerprint")

    back = sub.add_parser("back")
    back.add_argument("--expect-fingerprint")

    shot = sub.add_parser("screencap")
    shot.add_argument("path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "health":
            data = {"root": root_available(), **device_identity()}
        elif args.command == "probe":
            data = probe()
        elif args.command == "snapshot":
            data = snapshot(include_nodes=True)
        elif args.command == "resolve-tap":
            snap = snapshot(include_nodes=True)
            nodes = [
                UiNode(
                    index=item["index"],
                    parent=item["parent"],
                    class_name=item["class"],
                    resource_id=item["resource_id"],
                    bounds=Bounds(*item["bounds"]),
                    clickable=item["clickable"],
                    enabled=item["enabled"],
                    scrollable=item["scrollable"],
                    password=item["password"],
                    selected=item.get("selected", False),
                )
                for item in snap["nodes"]
            ]
            data = {
                "fingerprint": snap["fingerprint"],
                "resolution": resolve_normalized_tap(
                    nodes,
                    int(snap["width"]),
                    int(snap["height"]),
                    args.x_norm,
                    args.y_norm,
                ),
            }
        elif args.command == "tap-selector":
            data = tap_selector(args.resource_id, args.expect_fingerprint)
        elif args.command == "swipe":
            data = swipe_normalized(
                args.x1,
                args.y1,
                args.x2,
                args.y2,
                duration_ms=args.duration_ms,
                expected_fingerprint=args.expect_fingerprint,
            )
        elif args.command == "back":
            data = press_back(args.expect_fingerprint)
        elif args.command == "screencap":
            data = save_screenshot(pathlib.Path(args.path))
        else:
            raise AotControllerError("unknown command")
    except AotControllerError as exc:
        _json_print({"ok": False, "error": str(exc)})
        return 2
    _json_print({"ok": True, "result": data})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
