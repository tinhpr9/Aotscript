#!/data/data/com.termux/files/usr/bin/python3
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import socket
import sys
import time
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent
RELAY_PATH = ROOT / "relay.py"

spec = importlib.util.spec_from_file_location(
    "aot_relay_e2e",
    RELAY_PATH,
)
if spec is None or spec.loader is None:
    raise SystemExit("E2E_RELAY_IMPORT=FAILED")
relay = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = relay
spec.loader.exec_module(relay)
controller = relay.controller

SWIFT = "org.swiftapps.swiftbackup"
NAV_ACCOUNT = SWIFT + ":id/nav_account"
NAV_HOME = SWIFT + ":id/nav_home"
NAV_CLOUD = SWIFT + ":id/nav_cloud"
NAV_SCHEDULE = SWIFT + ":id/nav_schedule"


class E2EError(RuntimeError):
    pass


def recv_acks(
    sock: socket.socket,
    *,
    action_id: str,
    followers: list[str],
    timeout_seconds: int = 25,
) -> dict[str, dict[str, Any]]:
    expected = set(followers)
    received: dict[str, dict[str, Any]] = {}
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline and set(received) != expected:
        sock.settimeout(max(1, int(deadline - time.monotonic())))
        try:
            opcode, payload = relay._ws_recv_frame(sock)
        except socket.timeout:
            continue
        if opcode == 0x8:
            raise E2EError("reference_websocket_closed")
        if opcode == 0x9:
            relay._ws_send_frame(sock, 0xA, payload)
            continue
        if opcode == 0xA:
            continue
        if opcode != 0x1:
            continue
        try:
            message = json.loads(payload.decode("utf-8"))
        except Exception:
            continue
        if not isinstance(message, dict):
            continue
        if message.get("type") != "aot_ack":
            continue
        if message.get("action_id") != action_id:
            continue
        follower = relay.normalize_device_id(
            message.get("follower_device_id")
        )
        if follower in expected:
            received[follower] = message
    missing = sorted(expected - set(received))
    if missing:
        raise E2EError("ack_timeout:" + ",".join(missing))
    return received


def selector_center(resource_id: str) -> tuple[str, str, float, float]:
    snap = controller.snapshot(include_nodes=True)
    if snap.get("package") != SWIFT:
        raise E2EError("swift_not_foreground")
    matches = [
        node
        for node in snap.get("nodes", [])
        if node.get("resource_id") == resource_id
    ]
    if len(matches) != 1:
        raise E2EError(
            f"selector_match_count:{resource_id}:{len(matches)}"
        )
    bounds = matches[0].get("bounds")
    if not (
        isinstance(bounds, list)
        and len(bounds) == 4
        and all(isinstance(value, int) for value in bounds)
    ):
        raise E2EError("selector_bounds_invalid")
    left, top, right, bottom = bounds
    width = int(snap.get("width") or 0)
    height = int(snap.get("height") or 0)
    if width <= 0 or height <= 0 or right <= left or bottom <= top:
        raise E2EError("display_or_bounds_invalid")
    x_norm = ((left + right) / 2.0) / width
    y_norm = ((top + bottom) / 2.0) / height
    nodes = controller.parse_ui_xml(controller.dump_ui_xml())
    resolved = controller.resolve_normalized_tap(
        nodes,
        width,
        height,
        x_norm,
        y_norm,
    )
    if resolved.get("mode") != "semantic":
        raise E2EError("preview_semantic_resolution_failed")
    semantic_id = str(resolved.get("resource_id") or "").strip()
    if not semantic_id:
        raise E2EError("preview_semantic_id_missing")
    return str(snap.get("fingerprint") or ""), semantic_id, x_norm, y_norm


def validate_success_acks(
    acks: dict[str, dict[str, Any]],
    *,
    expected_after: str,
) -> None:
    for follower, ack in sorted(acks.items()):
        if ack.get("status") != "success":
            raise E2EError(
                f"{follower}_status:{ack.get('status')}"
            )
        if ack.get("executed") is not True:
            raise E2EError(f"{follower}_not_executed")
        if ack.get("after_fingerprint") != expected_after:
            raise E2EError(f"{follower}_fingerprint_mismatch")
        preview_bytes = int(ack.get("preview_bytes") or 0)
        preview_sha = str(ack.get("preview_sha256") or "")
        if preview_bytes <= 0 or len(preview_sha) != 64:
            raise E2EError(f"{follower}_preview_metadata_invalid")


def dispatch(
    cfg: dict[str, str],
    sock: socket.socket,
    *,
    local_id: str,
    session_id: str,
    followers: list[str],
    action_id: str,
    before_fp: str,
    follower_action: dict[str, Any],
    expected_after: str,
) -> dict[str, dict[str, Any]]:
    relay._dispatch_action(
        cfg,
        {
            "protocol": relay.PROTOCOL_VERSION,
            "session_id": session_id,
            "reference_device_id": local_id,
            "target_device_ids": followers,
            "action_id": action_id,
            "expires_at": int(time.time() * 1000) + 20000,
            "precondition": before_fp,
            "action": follower_action,
        },
    )
    acks = recv_acks(
        sock,
        action_id=action_id,
        followers=followers,
    )
    validate_success_acks(acks, expected_after=expected_after)
    return acks


def semantic_step(
    cfg: dict[str, str],
    sock: socket.socket,
    *,
    local_id: str,
    session_id: str,
    followers: list[str],
    selector: str,
    label: str,
) -> tuple[str, dict[str, Any]]:
    before_fp, semantic_id, _x, _y = selector_center(selector)
    local = controller.tap_selector(semantic_id, before_fp)
    action_id = relay._make_action_id("e2e")
    dispatch(
        cfg,
        sock,
        local_id=local_id,
        session_id=session_id,
        followers=followers,
        action_id=action_id,
        before_fp=before_fp,
        follower_action={
            "kind": "tap_selector",
            "resource_id": semantic_id,
        },
        expected_after=str(local.get("after_fingerprint") or ""),
    )
    print(f"E2E_{label}=SUCCESS")
    return action_id, local


def back_step(
    cfg: dict[str, str],
    sock: socket.socket,
    *,
    local_id: str,
    session_id: str,
    followers: list[str],
) -> tuple[str, dict[str, Any]]:
    before = controller.snapshot(include_nodes=False)
    before_fp = str(before.get("fingerprint") or "")
    local = controller.press_back(before_fp)
    action_id = relay._make_action_id("e2e-back")
    dispatch(
        cfg,
        sock,
        local_id=local_id,
        session_id=session_id,
        followers=followers,
        action_id=action_id,
        before_fp=before_fp,
        follower_action={"kind": "back"},
        expected_after=str(local.get("after_fingerprint") or ""),
    )
    print("E2E_BACK=SUCCESS")
    return action_id, local


def swipe_step(
    cfg: dict[str, str],
    sock: socket.socket,
    *,
    local_id: str,
    session_id: str,
    followers: list[str],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    before = controller.snapshot(include_nodes=False)
    before_fp = str(before.get("fingerprint") or "")
    action = {
        "kind": "swipe",
        "x1": 0.50,
        "y1": 0.72,
        "x2": 0.50,
        "y2": 0.32,
        "duration_ms": 300,
    }
    local = controller.swipe_normalized(
        action["x1"],
        action["y1"],
        action["x2"],
        action["y2"],
        duration_ms=action["duration_ms"],
        expected_fingerprint=before_fp,
    )
    action_id = relay._make_action_id("e2e-swipe")
    dispatch(
        cfg,
        sock,
        local_id=local_id,
        session_id=session_id,
        followers=followers,
        action_id=action_id,
        before_fp=before_fp,
        follower_action=action,
        expected_after=str(local.get("after_fingerprint") or ""),
    )
    print("E2E_SWIPE=SUCCESS")
    return action_id, local, {
        "protocol": relay.PROTOCOL_VERSION,
        "session_id": session_id,
        "reference_device_id": local_id,
        "target_device_ids": followers,
        "action_id": action_id,
        "expires_at": int(time.time() * 1000) + 20000,
        "precondition": before_fp,
        "action": action,
    }


def run(session_id: str, followers: list[str], package: str) -> int:
    local_id = relay.normalize_device_id(
        relay._read_small(relay.DEVICE_ID_PATH)
    )
    if not local_id:
        raise E2EError("invalid_local_device_id")
    if local_id in followers:
        raise E2EError("reference_in_follower_set")
    if not controller.root_available():
        raise E2EError("root_not_available")
    cfg = relay.load_agent_config()
    health_url = relay.worker_origin(
        cfg["worker_report_url"]
    ) + "/aot/control/health"
    status, health = relay._http_json(
        health_url,
        cfg["agent_report_secret"],
        method="GET",
    )
    if (
        status != 200
        or health.get("ok") is not True
        or health.get("protocol") != relay.PROTOCOL_VERSION
    ):
        raise E2EError("worker_health_failed")
    relay._launch_package(package)
    sock = relay.ws_connect(
        relay.websocket_url(
            cfg["worker_report_url"],
            device_id=local_id,
            role="reference",
            session_id=session_id,
        ),
        cfg["agent_report_secret"],
    )
    try:
        semantic_step(
            cfg,
            sock,
            local_id=local_id,
            session_id=session_id,
            followers=followers,
            selector=NAV_ACCOUNT,
            label="ACCOUNT_TAP",
        )
        back_step(
            cfg,
            sock,
            local_id=local_id,
            session_id=session_id,
            followers=followers,
        )
        semantic_step(
            cfg,
            sock,
            local_id=local_id,
            session_id=session_id,
            followers=followers,
            selector=NAV_CLOUD,
            label="CLOUD_TAP",
        )
        semantic_step(
            cfg,
            sock,
            local_id=local_id,
            session_id=session_id,
            followers=followers,
            selector=NAV_HOME,
            label="HOME_AFTER_CLOUD",
        )
        semantic_step(
            cfg,
            sock,
            local_id=local_id,
            session_id=session_id,
            followers=followers,
            selector=NAV_SCHEDULE,
            label="SCHEDULE_TAP",
        )
        semantic_step(
            cfg,
            sock,
            local_id=local_id,
            session_id=session_id,
            followers=followers,
            selector=NAV_HOME,
            label="HOME_AFTER_SCHEDULE",
        )
        swipe_action_id, _local_swipe, swipe_dispatch = swipe_step(
            cfg,
            sock,
            local_id=local_id,
            session_id=session_id,
            followers=followers,
        )

        relay._dispatch_action(cfg, swipe_dispatch)
        duplicates = recv_acks(
            sock,
            action_id=swipe_action_id,
            followers=followers,
        )
        for follower, ack in duplicates.items():
            if ack.get("status") != "duplicate":
                raise E2EError(f"{follower}_dedupe_failed")
        print("E2E_DEDUPE=SUCCESS")

        guard_id = relay._make_action_id("e2e-guard")
        relay._dispatch_action(
            cfg,
            {
                "protocol": relay.PROTOCOL_VERSION,
                "session_id": session_id,
                "reference_device_id": local_id,
                "target_device_ids": followers,
                "action_id": guard_id,
                "expires_at": int(time.time() * 1000) + 20000,
                "precondition": "0" * 24,
                "action": {
                    "kind": "tap_selector",
                    "resource_id": NAV_ACCOUNT,
                },
            },
        )
        guards = recv_acks(
            sock,
            action_id=guard_id,
            followers=followers,
        )
        for follower, ack in guards.items():
            if (
                ack.get("status") != "out_of_sync"
                or ack.get("executed") is not False
            ):
                raise E2EError(f"{follower}_out_of_sync_guard_failed")
        print("E2E_OUT_OF_SYNC_GUARD=SUCCESS")
        print("E2E_GUARD_TAP_EXECUTED=NO")
        print("AOT_E2E_BATCH=SUCCESS")
        print("REFERENCE_DEVICE=" + local_id)
        print("FOLLOWERS=" + ",".join(followers))
        return 0
    finally:
        try:
            sock.close()
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AOT Group Control multi-action E2E batch"
    )
    parser.add_argument("--session", required=True)
    parser.add_argument(
        "--follower",
        action="append",
        required=True,
    )
    parser.add_argument(
        "--package",
        default=SWIFT,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        session_id = relay.normalize_session_id(args.session)
        if not session_id:
            raise E2EError("invalid_session_id")
        followers = []
        seen = set()
        for raw in args.follower:
            follower = relay.normalize_device_id(raw)
            if not follower:
                raise E2EError("invalid_follower")
            if follower in seen:
                continue
            seen.add(follower)
            followers.append(follower)
        return run(session_id, followers, args.package)
    except (E2EError, relay.AotRelayError, controller.AotControllerError) as exc:
        print("AOT_E2E_BATCH=FAILED")
        print("REASON=" + str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
