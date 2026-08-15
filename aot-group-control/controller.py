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
ANDROID_SH = "/system/bin/sh"
TERMUX_SU = "/data/data/com.termux/files/usr/bin/su"
DEVICE_ID_PATH = pathlib.Path(
    "/storage/emulated/0/Download/Shouko/device_id.txt"
)
DEVICE_GROUP_PATH = pathlib.Path(
    "/storage/emulated/0/Download/Shouko/device_group.txt"
)


class AotControllerError(RuntimeError):
    pass

class AotTimeoutError(AotControllerError):
    pass

class AotExpiredError(AotControllerError):
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
    checked: bool = False
    text: str = ""
    content_description: str = ""

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
            "checked": self.checked,
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
    root_command = _root_shell_command(command)
    if os.geteuid() == 0:
        return [
            ANDROID_SH,
            "-c",
            root_command,
        ]
    return [
        TERMUX_SU,
        "-c",
        root_command,
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
                    checked=_bool_attr(child.attrib.get("checked")),
                    text=child.attrib.get("text", ""),
                    content_description=child.attrib.get("content-desc", ""),
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

    def has_positive_ancestor_clip(node: UiNode) -> bool:
        left = node.bounds.left
        top = node.bounds.top
        right = node.bounds.right
        bottom = node.bounds.bottom
        parent = node.parent
        visited: set[int] = set()
        while parent is not None and parent not in visited:
            visited.add(parent)
            ancestor = by_index.get(parent)
            if ancestor is None:
                return True
            left = max(left, ancestor.bounds.left)
            top = max(top, ancestor.bounds.top)
            right = min(right, ancestor.bounds.right)
            bottom = min(bottom, ancestor.bounds.bottom)
            if right <= left or bottom <= top:
                return False
            parent = ancestor.parent
        return True

    return [
        node
        for node in nodes
        if (
            node.resource_id
            and node.bounds.area > 0
            and has_positive_ancestor_clip(node)
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


SWIFT_BACKUP_PACKAGE = "org.swiftapps.swiftbackup"
SWIFT_APPS_RESOURCE_IDS = (
    "org.swiftapps.swiftbackup:id/apps",
    "org.swiftapps.swiftbackup:id/nav_apps",
    "org.swiftapps.swiftbackup:id/navigation_apps",
)


def _apps_semantic_matches(nodes: list[UiNode]) -> list[UiNode]:
    matches: dict[int, UiNode] = {}
    
    # 1. Primary: Explicit resource ID match
    for node in nodes:
        if node.resource_id in SWIFT_APPS_RESOURCE_IDS:
            target = _clickable_target(nodes, node)
            matches[target.index] = target
            
    # 2. Secondary: Structural fallback (Bottom Navigation)
    if not matches:
        bottom_navs = [
            n for n in nodes
            if "bottomnavigation" in n.class_name.lower() or n.resource_id.endswith(":id/bottom_navigation")
        ]
        if bottom_navs:
            bnav = bottom_navs[-1]
            # Find item views
            items = [
                n for n in nodes
                if "bottomnavigationitemview" in n.class_name.lower() and n.bounds.top >= bnav.bounds.top
            ]
            if not items:
                items = [n for n in nodes if n.parent == bnav.index]
                if len(items) == 1:
                    items = [n for n in nodes if n.parent == items[0].index]
            
            items.sort(key=lambda n: n.bounds.left)
            if len(items) >= 2:
                # Tab 1 is Home, Tab 2 is Apps
                try:
                    target = _clickable_target(nodes, items[1])
                    matches[target.index] = target
                except AotControllerError:
                    pass
                    
    return list(matches.values())


def swift_apps_screen_open(nodes: list[UiNode]) -> bool:
    selected = [
        node for node in nodes
        if node.selected and (
            node.resource_id in SWIFT_APPS_RESOURCE_IDS
        )
    ]
    markers = [
        node for node in nodes
        if node.resource_id.endswith(("apps_list", "apps_recycler", "apps_screen"))
    ]
    
    structural_selected = False
    if not selected and not markers:
        bottom_navs = [
            n for n in nodes
            if "bottomnavigation" in n.class_name.lower() or n.resource_id.endswith(":id/bottom_navigation")
        ]
        if bottom_navs:
            bnav = bottom_navs[-1]
            items = [
                n for n in nodes
                if "bottomnavigationitemview" in n.class_name.lower() and n.bounds.top >= bnav.bounds.top
            ]
            if not items:
                items = [n for n in nodes if n.parent == bnav.index]
                if len(items) == 1:
                    items = [n for n in nodes if n.parent == items[0].index]
            items.sort(key=lambda n: n.bounds.left)
            if len(items) >= 2 and items[1].selected:
                structural_selected = True

    return len(selected) == 1 or len(markers) == 1 or structural_selected


def open_swift_apps() -> dict[str, Any]:
    if foreground_package() != SWIFT_BACKUP_PACKAGE:
        raise AotControllerError("swift_backup_not_foreground")
    before_xml = dump_ui_xml()
    before_nodes = parse_ui_xml(before_xml)
    before_fingerprint = ui_fingerprint(SWIFT_BACKUP_PACKAGE, before_nodes)
    matches = _apps_semantic_matches(before_nodes)
    if len(matches) != 1:
        raise AotControllerError(
            "swift_apps_selector_not_found" if not matches
            else "swift_apps_selector_ambiguous"
        )
    target = matches[0]
    check_nodes = parse_ui_xml(dump_ui_xml())
    check_fingerprint = ui_fingerprint(SWIFT_BACKUP_PACKAGE, check_nodes)
    if check_fingerprint != before_fingerprint:
        raise AotControllerError("swift_apps_precondition_changed")
    check_matches = _apps_semantic_matches(check_nodes)
    if len(check_matches) != 1:
        raise AotControllerError("swift_apps_precondition_changed")
    _tap_xy(*check_matches[0].bounds.center)
    time.sleep(0.35)
    if foreground_package() != SWIFT_BACKUP_PACKAGE:
        raise AotControllerError("swift_apps_postcondition_failed")
    after_nodes = parse_ui_xml(dump_ui_xml())
    if not swift_apps_screen_open(after_nodes):
        raise AotControllerError("swift_apps_postcondition_failed")
    return {
        "action": "OPEN_SWIFT_APPS",
        "executed": True,
        "before_fingerprint": before_fingerprint,
        "after_fingerprint": ui_fingerprint(SWIFT_BACKUP_PACKAGE, after_nodes),
    }



# ── Swift Backup RESTORE_DATA full-chain backup ──────────────────────────────
# Timing constants (seconds).  Bounded, never polling.
RESTORE_DATA_STAGE_TIMEOUT = 30.0
RESTORE_DATA_POLL_INTERVAL = 0.4
RESTORE_DATA_LABEL = "RESTORE_DATA"
BACKUP_RESTORE_DATA_ACTION = "BACKUP_RESTORE_DATA"

# Exact resource IDs used by Swift Backup.  Every selector must resolve
# to exactly one node; any deviation fails closed before the next tap.
_SB = "org.swiftapps.swiftbackup:id/"

# Step 3 – filter panel trigger (three-dot or filter icon on Apps screen)
_RID_FILTER_TRIGGER = _SB + "menu_filter"           # overflow / filter button
_RID_FILTER_TRIGGER_ALT = _SB + "action_filter"     # alternative id seen on some builds

# Step 4 – chip / toggle for the RESTORE_DATA label inside the filter dialog
# These are resolved by text "RESTORE_DATA" since label IDs vary per-app.

# Step 5 – "Apply" or confirm button inside filter dialog
_RID_FILTER_APPLY = _SB + "button_apply"
_RID_FILTER_APPLY_ALT = _SB + "action_apply"

# Step 6 – "Select all" checkbox in the filtered apps list
_RID_SELECT_ALL = _SB + "checkbox_select_all"
_RID_SELECT_ALL_ALT = _SB + "action_select_all"

# Step 7 – "Batch actions" / overflow action on the apps list
_RID_BATCH_ACTIONS = _SB + "menu_batch_actions"
_RID_BATCH_ACTIONS_ALT = _SB + "action_batch"

# Step 8 – "Backup" item inside the batch-actions menu
_RID_BACKUP_MENU_ITEM = _SB + "menu_item_backup"

# Step 9 – individual option toggles in the backup-options dialog
_RID_OPT_APKS = _SB + "checkbox_apks"
_RID_OPT_DATA = _SB + "checkbox_data"
_RID_OPT_CLOUD = _SB + "checkbox_cloud"
_RID_OPT_EXT_DATA = _SB + "checkbox_ext_data"
_RID_OPT_EXPANSION = _SB + "checkbox_expansion"
_RID_OPT_MEDIA = _SB + "checkbox_media"
_RID_OPT_DEVICE = _SB + "checkbox_device"

# Step 10 – final "+ BACKUP" confirm button
_RID_FINAL_BACKUP = _SB + "button_backup_start"
_RID_FINAL_BACKUP_ALT = _SB + "button_start_backup"

# Step 11 – progress / running indicator in the backup screen
_RID_BACKUP_PROGRESS = _SB + "progress_backup"
_RID_BACKUP_RUNNING = _SB + "backup_running_indicator"


def _sb_assert_foreground() -> None:
    """Raise AotControllerError if Swift Backup is not foreground."""
    pkg = foreground_package()
    if pkg != SWIFT_BACKUP_PACKAGE:
        raise AotControllerError("swift_backup_not_foreground")


def _wait_for(
    condition_fn,
    stage: str,
    timeout: float = RESTORE_DATA_STAGE_TIMEOUT,
    absolute_deadline: float | None = None,
) -> None:
    """Poll condition_fn until True or timeout; raise on timeout."""
    deadline = time.monotonic() + timeout
    if absolute_deadline is not None:
        sys_deadline = absolute_deadline - time.time() + time.monotonic()
        deadline = min(deadline, sys_deadline)
    while time.monotonic() < deadline:
        try:
            if condition_fn():
                return
        except AotControllerError:
            pass
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(RESTORE_DATA_POLL_INTERVAL, remaining))
    if absolute_deadline is not None and time.time() >= absolute_deadline:
        raise AotExpiredError(f"stage_timeout:{stage}")
    raise AotTimeoutError(f"stage_timeout:{stage}")


def _find_unique_by_resource_ids(
    nodes: list[UiNode],
    *resource_ids: str,
) -> UiNode | None:
    """Return the single node matching any of the resource IDs, or None.
    Raises AotControllerError if more than one match exists."""
    matches: dict[int, UiNode] = {}
    for node in nodes:
        if node.resource_id in resource_ids:
            target = _clickable_target(nodes, node)
            matches[target.index] = target
    if len(matches) == 0:
        return None
    if len(matches) > 1:
        raise AotControllerError(
            f"ambiguous_selector count={len(matches)} ids={resource_ids!r}"
        )
    return list(matches.values())[0]


def _find_by_text_exact(
    nodes: list[UiNode],
    text: str,
    *,
    clickable_only: bool = True,
) -> list[UiNode]:
    """Return all nodes whose text or content-description exactly equals text."""
    result = []
    for node in nodes:
        if node.text == text or node.content_description == text:
            if clickable_only and not (node.clickable and node.enabled):
                # try parent
                try:
                    parent = _clickable_target(nodes, node)
                    result.append(parent)
                except AotControllerError:
                    pass
            else:
                result.append(node)
    # deduplicate by index
    seen: set[int] = set()
    unique = []
    for n in result:
        if n.index not in seen:
            seen.add(n.index)
            unique.append(n)
    return unique


def _tap_unique_resource_id(
    nodes: list[UiNode],
    stage: str,
    *resource_ids: str,
) -> None:
    """Find a unique node by resource ID and tap it; fail closed if not unique."""
    node = _find_unique_by_resource_ids(nodes, *resource_ids)
    if node is None:
        raise AotControllerError(f"selector_not_found:{stage}")
    _tap_xy(*node.bounds.center)


def _get_switch_state(nodes: list[UiNode], resource_id: str) -> bool | None:
    """Return the selected/checked state of a node, or None if not found."""
    matches = [n for n in nodes if n.resource_id == resource_id]
    if len(matches) == 1:
        return matches[0].checked
    return None


def _set_switch_to(
    nodes: list[UiNode],
    resource_id: str,
    desired_on: bool,
    option_name: str,
) -> None:
    """Set a toggle/checkbox to desired state (idempotent)."""
    matches = [n for n in nodes if n.resource_id == resource_id]
    if len(matches) == 0:
        raise AotControllerError(f"option_not_found:{option_name}")
    if len(matches) > 1:
        raise AotControllerError(f"option_ambiguous:{option_name}")
    node = matches[0]
    current = node.checked
    if current == desired_on:
        return  # already correct
    target = _clickable_target(nodes, node)
    _tap_xy(*target.bounds.center)
    time.sleep(0.25)


def _is_backup_running(nodes: list[UiNode]) -> bool:
    """Return True if a backup progress indicator is visible in the UI."""
    for node in nodes:
        if node.resource_id in (
            _RID_BACKUP_PROGRESS,
            _RID_BACKUP_RUNNING,
            _SB + "backup_progress",
            _SB + "progress_bar_backup",
        ):
            if node.bounds.area > 0 and node.enabled:
                return True
    return False


# ── AGENTS.md policy reconciliation ──────────────────────────────────────────
# AGENTS.md § "Required release checks" bans a standalone browser-controlled
# arbitrary filter/tap action as an updater release.  This function
# implements a fixed, allowlisted, fail-closed full-chain RESTORE_DATA backup
# action (BACKUP_RESTORE_DATA_ACTION).  No browser-supplied label, package,
# option payload, or tap instruction is accepted.  The maintainer-requested PR
# explicitly permits this specific action; see PR #34 description.


def backup_restore_data(
    action_id: str,
    *,
    stage_cb=None,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Run the complete Swift Backup RESTORE_DATA full chain using robust UI state-machine."""
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
    _verified_selection = False
    _final_app_count = 0
    _final_selected_count = 0

    for _step in range(80):
        _sb_assert_foreground()
        if deadline is not None and time.time() >= deadline:
            raise AotExpiredError("expired")

        nodes = parse_ui_xml(dump_ui_xml())

        if _find_text("User app parts", nodes):
            if not _verified_selection:
                _save_unknown_debug("stale_options_screen")
                raise AotControllerError("stale_options_screen")
            unknown = 0
            import struct
            
            frame = _raw_screencap()
            apk_on, apk_card = _is_green_selected("APKs", nodes, frame=frame)
            if not apk_on:
                if not apk_card:
                    raise AotControllerError("selector_missing:APKs")
                _tap_wait(apk_card, deadline)
                time.sleep(0.7)
                nodes = parse_ui_xml(dump_ui_xml())
                frame = _raw_screencap()
                apk_on2, _ = _is_green_selected("APKs", nodes, frame=frame)
                if not apk_on2:
                    _save_unknown_debug("apks_toggle_failed")
                    raise AotControllerError("apks_toggle_failed")

            data_on, data_card = _is_green_selected("Data", nodes, frame=frame)
            if not data_on:
                if not data_card:
                    raise AotControllerError("selector_missing:Data")
                _tap_wait(data_card, deadline)
                time.sleep(0.7)
                nodes = parse_ui_xml(dump_ui_xml())
                frame = _raw_screencap()
                data_on2, _ = _is_green_selected("Data", nodes, frame=frame)
                if not data_on2:
                    _save_unknown_debug("data_toggle_failed")
                    raise AotControllerError("data_toggle_failed")

            apk_on, _ = _is_green_selected("APKs", nodes, frame=frame)
            data_on, _ = _is_green_selected("Data", nodes, frame=frame)
            cloud_on, _ = _is_green_selected("Cloud", nodes, frame=frame)
            ext_on, _ = _is_green_selected("Ext.data", nodes, frame=frame)
            exp_on, _ = _is_green_selected("Expansion", nodes, frame=frame)
            med_on, _ = _is_green_selected("Media", nodes, frame=frame)
            dev_on, _ = _is_green_selected("Device", nodes, frame=frame)

            if not apk_on or not data_on or not cloud_on:
                raise AotControllerError("options_verify_failed")
            if ext_on or exp_on or med_on or dev_on:
                raise AotControllerError("options_verify_failed")

            _cb("OPTIONS_VERIFIED")

            n = _find_unique_by_resource_ids(nodes, _RID_FINAL_BACKUP, _RID_FINAL_BACKUP_ALT)
            b = n.bounds if n else _smart_find("BACKUP", nodes) or _smart_find("+ BACKUP", nodes)
            if not b:
                _save_unknown_debug("final_restore_button_not_found")
                raise AotControllerError("final_restore_button_not_found")
            
            if _find_unique_by_resource_ids(nodes, _RID_BACKUP_PROGRESS, _RID_BACKUP_RUNNING):
                raise AotControllerError("active_backup_found")
            if _find_text("Backing up...", nodes) or _find_text("Backup progress", nodes) or _find_text("Cancel", nodes):
                raise AotControllerError("active_backup_found")

            before_fp = ui_fingerprint(SWIFT_BACKUP_PACKAGE, nodes)
            
            def _restore_started() -> bool:
                _sb_assert_foreground()
                n = parse_ui_xml(dump_ui_xml())
                if _find_unique_by_resource_ids(n, _RID_BACKUP_PROGRESS, _RID_BACKUP_RUNNING):
                    return True
                if _find_text("Backing up...", n) or _find_text("Backup progress", n):
                    return True
                return ui_fingerprint(SWIFT_BACKUP_PACKAGE, n) != before_fp

            if deadline is not None and time.time() >= deadline:
                raise AotExpiredError("expired_before_tap")

            tap_delivered = False
            try:
                _tap_xy(*b.center)
                tap_delivered = True
            except AotControllerError as e:
                err_msg = str(e)
                if err_msg.startswith("root command failed: ") and "TimeoutExpired" not in err_msg:
                    raise AotControllerError("subprocess_spawn_failed")
                return {
                    "action": BACKUP_RESTORE_DATA_ACTION,
                    "executed": True,
                    "status": "FAILED",
                    "safe_reason": "final_tap_delivery_uncertain",
                }
            except Exception:
                return {
                    "action": BACKUP_RESTORE_DATA_ACTION,
                    "executed": True,
                    "status": "FAILED",
                    "safe_reason": "final_tap_delivery_uncertain",
                }

            try:
                _wait_for(_restore_started, "restore_started", timeout=45.0, absolute_deadline=deadline)
                _sb_assert_foreground()
            except (AotTimeoutError, AotExpiredError):
                return {
                    "action": BACKUP_RESTORE_DATA_ACTION,
                    "executed": True,
                    "status": "TIMEOUT",
                    "safe_reason": "post_tap_start_unconfirmed",
                }
            except Exception:
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
                "safe_reason": "",
                "app_count": _final_app_count,
                "selected_count": _final_selected_count,
            }


        b = _smart_find("Backup options", nodes)
        if b:
            unknown = 0
            _tap_wait(b, deadline)
            continue

        n = _find_unique_by_resource_ids(nodes, _RID_BACKUP_MENU_ITEM)
        if n:
            unknown = 0
            _tap_wait(n.bounds, deadline)
            continue
        b = _smart_find("Backup", nodes) or _smart_find("Backup to cloud", nodes)
        if b:
            unknown = 0
            _tap_wait(b, deadline)
            continue

        if _find_text("Select labels", nodes):
            unknown = 0
            # Verify that exactly RESTORE_DATA (and no other label) is checked.
            # If another label is already selected the apply would scope to the
            # wrong set; fail-closed to prevent operating outside RESTORE_DATA.
            checked_labels = [
                n for n in nodes
                if n.checked and (
                    _clean(n.text) == "restore_data"
                    or _clean(n.content_description) == "restore_data"
                )
            ]
            other_checked = []
            has_unnamed = False
            for n in nodes:
                if n.checked:
                    lbl = _clean(n.text or n.content_description)
                    if lbl == "":
                        has_unnamed = True
                    elif lbl != "restore_data":
                        other_checked.append(n)
            if has_unnamed:
                _save_unknown_debug("unnamed_checked_label")
                raise AotControllerError("unnamed_checked_label")
            if other_checked:
                # A stale/unrelated label is checked → fail-closed.
                _save_unknown_debug("stale_label_checked")
                raise AotControllerError("stale_label_checked")
            if checked_labels and not other_checked:
                # Exactly RESTORE_DATA is selected; apply the filter.
                b = _smart_find("Apply", nodes)
                if not b:
                    n_apply = _find_unique_by_resource_ids(nodes, _RID_FILTER_APPLY, _RID_FILTER_APPLY_ALT)
                    b = n_apply.bounds if n_apply else None
                if not b:
                    _save_unknown_debug("filter_apply_not_found")
                    raise AotControllerError("filter_apply_not_found")
                _sb_assert_foreground()
                _tap_wait(b, deadline)
                _cb("FILTERED")
                continue
            # RESTORE_DATA not yet checked; tap the chip to select it.
            b = _smart_find("RESTORE_DATA", nodes)
            if not b:
                _save_unknown_debug("restore_data_label_not_found")
                raise AotControllerError("restore_data_label_not_found")
            _sb_assert_foreground()
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
            sel, tot, count_bounds = _selected_stats(nodes)
            if tot == 0:
                raise AotControllerError("no_apps_found_for_restore_data")
            if sel < tot:
                unknown = 0
                b = _smart_find("Select All", nodes) or _smart_find("Select all", nodes)
                if not b:
                    raise AotControllerError("select_all_not_found")
                _tap_wait(b, deadline)
                continue

            b = _smart_find("Batch actions", nodes)
            if b:
                if sel != tot:
                    raise AotControllerError("selected_count_mismatch")
                unknown = 0
                _cb("SELECTED")
                _verified_selection = True
                _final_app_count = tot
                _final_selected_count = sel
                _tap_wait(b, deadline)
                continue

        if _find_text("Batch actions", nodes):
            unknown = 0
            _cb("APPS_OPENED")

            n = _find_unique_by_resource_ids(nodes, _RID_FILTER_TRIGGER, _RID_FILTER_TRIGGER_ALT)
            if not n:
                # No uniquely-identified filter trigger found; fail-closed.
                # Do NOT fall back to positional/heuristic guessing.
                _save_unknown_debug("filter_trigger_not_found")
                raise AotControllerError("filter_trigger_not_found")
            if not n.enabled:
                raise AotControllerError("filter_trigger_disabled")
            _sb_assert_foreground()
            _tap_wait(n.bounds, deadline)
            continue

        b = _smart_find("Apps", nodes)
        if b:
            unknown = 0
            _cb("SWIFT_OPENED")
            _tap_wait(b, deadline)
            continue

        print(f'UNKNOWN UI: {nodes}', file=sys.stderr)
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
    matches = []
    for n in nodes:
        if _clean(n.text) == target or _clean(n.content_description) == target:
            matches.append(n)
    if not matches:
        return None
    if len(matches) > 1:
        raise AotControllerError(f"ambiguous_selector:{text}")
    n = matches[0]
    if not n.enabled:
        raise AotControllerError(f"disabled_target:{text}")
    tb = n.bounds
    if n.clickable:
        return tb
    candidates = []
    for c in nodes:
        if not c.clickable:
            continue
        if c.bounds.left <= tb.left and c.bounds.top <= tb.top and c.bounds.right >= tb.right and c.bounds.bottom >= tb.bottom:
            candidates.append((c.bounds.area, c))
    if candidates:
        best_c = min(candidates, key=lambda x: x[0])[1]
        if not best_c.enabled:
            raise AotControllerError(f"disabled_target:{text}")
        return best_c.bounds
    return tb

def _tap_wait(bounds: Bounds, deadline: float | None):
    xml_before = dump_ui_xml()
    before_nodes = parse_ui_xml(xml_before)
    before_fp = ui_fingerprint(SWIFT_BACKUP_PACKAGE, before_nodes)
    _tap_xy(*bounds.center)
    end = time.time() + 4.0
    while time.time() < end:
        if deadline and time.time() >= deadline:
            break
        time.sleep(0.5)
        xml_after = dump_ui_xml()
        after_nodes = parse_ui_xml(xml_after)
        after_fp = ui_fingerprint(SWIFT_BACKUP_PACKAGE, after_nodes)
        if after_fp != before_fp:
            return

def _selected_stats(nodes: list[UiNode]) -> tuple[int, int, Bounds | None]:
    for n in nodes:
        for val in (n.text, n.content_description):
            m = re.fullmatch(r'\s*(\d+)\s*/\s*(\d+)\s*', val)
            if m:
                return int(m.group(1)), int(m.group(2)), n.bounds
    return 0, 0, None

def _selected_count(nodes: list[UiNode]) -> int:
    s, _, _ = _selected_stats(nodes)
    return s

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
        if fmt != 1:
            raise AotControllerError(f"screencap_unsupported_format:{fmt}")
        pixel_bytes = w * h * 4
        if len(raw) != 12 + pixel_bytes:
            # We don't support row stride/padding because we don't have the stride value.
            # Reject fail-closed to prevent reading misaligned pixels.
            raise AotControllerError(f"screencap_invalid_format:stride_or_header_mismatch")
        return w, h, raw[12:]
    except AotControllerError:
        raise
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

def _is_green_selected(name: str, nodes: list[UiNode], frame: tuple[int, int, bytes] | None = None) -> tuple[bool, Bounds | None]:
    card = _option_card(name, nodes)
    if not card:
        return False, None
    if frame is None:
        frame = _raw_screencap()
    w, h, pixels = frame
    dw, dh = display_size()
    if w != dw or h != dh:
        raise AotControllerError(f"screencap_resolution_mismatch:{w}x{h}_vs_{dw}x{dh}")
    if card.right > w or card.bottom > h:
        raise AotControllerError(f"ui_bounds_exceed_screencap:{card.right},{card.bottom}_vs_{w}x{h}")
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
