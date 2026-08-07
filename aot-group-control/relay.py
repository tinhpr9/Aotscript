#!/data/data/com.termux/files/usr/bin/python3
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import socket
import ssl
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent
CONTROLLER_PATH = ROOT / "controller.py"
AGENT_CONFIG_PATH = pathlib.Path(
    "/storage/emulated/0/Download/Shouko/agent_config.json"
)
DEVICE_ID_PATH = pathlib.Path(
    "/storage/emulated/0/Download/Shouko/device_id.txt"
)
DEVICE_GROUP_PATH = pathlib.Path(
    "/storage/emulated/0/Download/Shouko/device_group.txt"
)
STATE_PATH = pathlib.Path(
    "/storage/emulated/0/Download/Shouko/aot_group_state.json"
)
PROTOCOL_VERSION = "phase3-1"
MAX_WS_FRAME_BYTES = 512 * 1024
MAX_HTTP_JSON_BYTES = 384 * 1024
MAX_PREVIEW_BYTES = 180 * 1024
PROCESSED_ACTIONS_MAX = 256


class AotRelayError(RuntimeError):
    pass


def _load_controller():
    spec = importlib.util.spec_from_file_location(
        "aot_group_controller",
        CONTROLLER_PATH,
    )
    if spec is None or spec.loader is None:
        raise AotRelayError("controller_import_failed")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


controller = _load_controller()


def _read_small(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def normalize_device_id(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    match = re.fullmatch(r"m([1-9]\d{0,5})", raw)
    if not match:
        return None
    return f"m{match.group(1)}"


def normalize_session_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    return raw if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", raw) else None


def normalize_action_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    return raw if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", raw) else None


def load_agent_config() -> dict[str, str]:
    try:
        data = json.loads(
            AGENT_CONFIG_PATH.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise AotRelayError("agent_config_unavailable") from exc
    if not isinstance(data, dict):
        raise AotRelayError("agent_config_invalid")
    report_url = data.get("worker_report_url")
    secret = data.get("agent_report_secret")
    if not isinstance(report_url, str) or not report_url.strip():
        raise AotRelayError("worker_report_url_missing")
    if not isinstance(secret, str) or not secret.strip():
        raise AotRelayError("agent_report_secret_missing")
    parsed = urllib.parse.urlparse(report_url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise AotRelayError("worker_report_url_invalid")
    return {
        "worker_report_url": report_url.strip(),
        "agent_report_secret": secret.strip(),
    }


def worker_origin(report_url: str) -> str:
    parsed = urllib.parse.urlparse(report_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise AotRelayError("worker_report_url_invalid")
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, "", "", "", "")
    )


def websocket_url(
    report_url: str,
    *,
    device_id: str,
    role: str,
    session_id: str,
) -> str:
    parsed = urllib.parse.urlparse(report_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    query = urllib.parse.urlencode(
        {
            "device_id": device_id,
            "role": role,
            "session_id": session_id,
        }
    )
    return urllib.parse.urlunparse(
        (
            scheme,
            parsed.netloc,
            "/aot/control/ws",
            "",
            query,
            "",
        )
    )


def _http_json(
    url: str,
    secret: str,
    *,
    method: str = "POST",
    body: dict[str, Any] | None = None,
    timeout: int = 15,
) -> tuple[int, dict[str, Any]]:
    payload = None
    headers = {
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "User-Agent": "AOT-Group-Control",
        "X-Agent-Secret": secret,
    }
    if body is not None:
        payload = json.dumps(
            body,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MAX_HTTP_JSON_BYTES:
            raise AotRelayError("http_payload_too_large")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(MAX_HTTP_JSON_BYTES + 1)
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read(MAX_HTTP_JSON_BYTES + 1)
        status = int(exc.code)
    if len(raw) > MAX_HTTP_JSON_BYTES:
        raise AotRelayError("worker_response_too_large")
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception as exc:
        raise AotRelayError("worker_response_invalid_json") from exc
    if not isinstance(data, dict):
        raise AotRelayError("worker_response_invalid")
    return status, data


def _ws_read_exact(sock: socket.socket, size: int) -> bytes:
    chunks = []
    remain = size
    while remain > 0:
        chunk = sock.recv(remain)
        if not chunk:
            raise ConnectionError("websocket_closed")
        chunks.append(chunk)
        remain -= len(chunk)
    return b"".join(chunks)


def _ws_read_headers(sock: socket.socket) -> bytes:
    data = bytearray()
    while not data.endswith(b"\r\n\r\n"):
        if len(data) >= 16384:
            raise AotRelayError("websocket_headers_too_large")
        chunk = sock.recv(1)
        if not chunk:
            raise ConnectionError("websocket_handshake_closed")
        data.extend(chunk)
    return bytes(data)


def _ws_send_frame(
    sock: socket.socket,
    opcode: int,
    payload: bytes = b"",
) -> None:
    payload = bytes(payload)
    first = 0x80 | (opcode & 0x0F)
    length = len(payload)
    mask = os.urandom(4)
    if length < 126:
        header = bytes([first, 0x80 | length])
    elif length <= 0xFFFF:
        header = (
            bytes([first, 0x80 | 126])
            + struct.pack("!H", length)
        )
    else:
        header = (
            bytes([first, 0x80 | 127])
            + struct.pack("!Q", length)
        )
    body = bytes(
        value ^ mask[index % 4]
        for index, value in enumerate(payload)
    )
    sock.sendall(header + mask + body)


def _ws_recv_frame(sock: socket.socket) -> tuple[int, bytes]:
    header = _ws_read_exact(sock, 2)
    first, second = header
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack(
            "!H",
            _ws_read_exact(sock, 2),
        )[0]
    elif length == 127:
        length = struct.unpack(
            "!Q",
            _ws_read_exact(sock, 8),
        )[0]
    if length > MAX_WS_FRAME_BYTES:
        raise AotRelayError("websocket_frame_too_large")
    mask = _ws_read_exact(sock, 4) if masked else b""
    payload = _ws_read_exact(sock, length)
    if masked:
        payload = bytes(
            value ^ mask[index % 4]
            for index, value in enumerate(payload)
        )
    if not fin:
        raise AotRelayError("websocket_fragmented_frame")
    return opcode, payload


def ws_connect(ws_url: str, secret: str) -> socket.socket:
    parsed = urllib.parse.urlparse(ws_url)
    if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
        raise AotRelayError("invalid_websocket_url")
    secure = parsed.scheme == "wss"
    port = parsed.port or (443 if secure else 80)
    raw_sock = socket.create_connection(
        (parsed.hostname, port),
        timeout=10,
    )
    try:
        if secure:
            context = ssl.create_default_context()
            sock = context.wrap_socket(
                raw_sock,
                server_hostname=parsed.hostname,
            )
        else:
            sock = raw_sock
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        host = parsed.hostname
        if parsed.port and parsed.port not in (80, 443):
            host = f"{host}:{parsed.port}"
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = "\r\n".join(
            [
                f"GET {path} HTTP/1.1",
                f"Host: {host}",
                "Upgrade: websocket",
                "Connection: Upgrade",
                f"Sec-WebSocket-Key: {key}",
                "Sec-WebSocket-Version: 13",
                "User-Agent: AOT-Group-Control",
                f"X-Agent-Secret: {secret}",
                "",
                "",
            ]
        )
        sock.sendall(request.encode("utf-8"))
        response = _ws_read_headers(sock).decode(
            "iso-8859-1",
            errors="replace",
        )
        status = response.split("\r\n", 1)[0]
        if " 101 " not in (status + " "):
            raise AotRelayError("websocket_upgrade_rejected")
        headers = {}
        for line in response.split("\r\n")[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(
            hashlib.sha1(
                (
                    key
                    + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                ).encode("ascii")
            ).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise AotRelayError("websocket_accept_invalid")
        sock.settimeout(20)
        return sock
    except Exception:
        try:
            raw_sock.close()
        except Exception:
            pass
        raise


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    processed = data.get("processed_action_ids")
    if not isinstance(processed, list):
        processed = []
    clean = []
    seen = set()
    for item in processed:
        action_id = normalize_action_id(item)
        if action_id and action_id not in seen:
            seen.add(action_id)
            clean.append(action_id)
    data["processed_action_ids"] = clean[-PROCESSED_ACTIONS_MAX:]
    return data


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_name(
        STATE_PATH.name + f".tmp-{os.getpid()}"
    )
    try:
        temp.write_text(
            json.dumps(
                state,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temp, STATE_PATH)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def action_already_processed(
    state: dict[str, Any],
    action_id: str,
) -> bool:
    return action_id in state.get("processed_action_ids", [])


def mark_action_processed(
    state: dict[str, Any],
    action_id: str,
) -> None:
    values = [
        item
        for item in state.get("processed_action_ids", [])
        if item != action_id
    ]
    values.append(action_id)
    state["processed_action_ids"] = values[-PROCESSED_ACTIONS_MAX:]
    _save_state(state)


def _launch_package(package: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._]+", package):
        raise AotRelayError("invalid_package")
    cmd = (
        "/system/bin/cmd package resolve-activity --brief "
        "-a android.intent.action.MAIN "
        "-c android.intent.category.LAUNCHER "
        + package
    )
    resolved = controller._root_run(cmd).strip().splitlines()
    activity = resolved[-1].strip() if resolved else ""
    if not activity.startswith(package + "/"):
        raise AotRelayError("package_activity_not_resolved")
    controller._root_run(
        "/system/bin/am start -W --display 0 "
        "-a android.intent.action.MAIN "
        "-c android.intent.category.LAUNCHER "
        "-n "
        + activity
        + " >/dev/null"
    )
    time.sleep(2)


def _preview_payload() -> tuple[str | None, int, str | None]:
    try:
        data = controller.screenshot_bytes()
    except Exception:
        return None, 0, None
    if len(data) > MAX_PREVIEW_BYTES:
        return None, len(data), hashlib.sha256(data).hexdigest()
    return (
        base64.b64encode(data).decode("ascii"),
        len(data),
        hashlib.sha256(data).hexdigest(),
    )


def _send_ack(
    cfg: dict[str, str],
    ack: dict[str, Any],
) -> None:
    endpoint = worker_origin(
        cfg["worker_report_url"]
    ) + "/aot/control/ack"
    status, data = _http_json(
        endpoint,
        cfg["agent_report_secret"],
        body=ack,
    )
    if status != 200 or data.get("ok") is not True:
        raise AotRelayError(
            "ack_delivery_failed:"
            + str(data.get("error") or status)
        )


def _execute_action(action: dict[str, Any], precondition: str) -> dict[str, Any]:
    kind = action.get("kind")
    if kind == "tap_selector":
        selector = action.get("resource_id")
        if not isinstance(selector, str) or not selector:
            raise AotRelayError("invalid_tap_selector")
        return controller.tap_selector(
            selector,
            precondition,
        )
    if kind == "swipe":
        return controller.swipe_normalized(
            float(action["x1"]),
            float(action["y1"]),
            float(action["x2"]),
            float(action["y2"]),
            duration_ms=int(action.get("duration_ms", 300)),
            expected_fingerprint=precondition,
        )
    if kind == "back":
        return controller.press_back(precondition)
    raise AotRelayError("unsupported_action_kind")


def follower_loop(
    *,
    session_id: str,
    reference_device: str,
    open_package: str | None,
) -> int:
    local_id = normalize_device_id(_read_small(DEVICE_ID_PATH))
    if not local_id:
        raise AotRelayError("invalid_local_device_id")
    group = _read_small(DEVICE_GROUP_PATH).upper()
    if group not in {"NOVA", "MARMOT"}:
        raise AotRelayError("invalid_local_device_group")
    if not controller.root_available():
        raise AotRelayError("root_not_available")
    if open_package:
        _launch_package(open_package)
    cfg = load_agent_config()
    url = websocket_url(
        cfg["worker_report_url"],
        device_id=local_id,
        role="follower",
        session_id=session_id,
    )
    sock = ws_connect(
        url,
        cfg["agent_report_secret"],
    )
    state = _load_state()
    print(f"AOT_FOLLOWER={local_id}")
    print(f"AOT_SESSION={session_id}")
    print("AOT_FOLLOWER_CHANNEL=CONNECTED")
    try:
        while True:
            try:
                opcode, payload = _ws_recv_frame(sock)
            except socket.timeout:
                _ws_send_frame(sock, 0x9, b"")
                continue
            if opcode == 0x8:
                raise ConnectionError("websocket_closed")
            if opcode == 0x9:
                _ws_send_frame(sock, 0xA, payload)
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
            if message.get("type") != "aot_action":
                continue
            if message.get("protocol") != PROTOCOL_VERSION:
                continue
            action_id = normalize_action_id(
                message.get("action_id")
            )
            if not action_id:
                continue
            if (
                normalize_session_id(message.get("session_id"))
                != session_id
            ):
                continue
            if (
                normalize_device_id(
                    message.get("reference_device_id")
                )
                != reference_device
            ):
                continue
            expires_at = int(message.get("expires_at") or 0)
            if expires_at <= int(time.time() * 1000):
                _send_ack(
                    cfg,
                    {
                        "protocol": PROTOCOL_VERSION,
                        "session_id": session_id,
                        "reference_device_id": reference_device,
                        "follower_device_id": local_id,
                        "action_id": action_id,
                        "status": "expired",
                        "executed": False,
                    },
                )
                continue
            if action_already_processed(state, action_id):
                _send_ack(
                    cfg,
                    {
                        "protocol": PROTOCOL_VERSION,
                        "session_id": session_id,
                        "reference_device_id": reference_device,
                        "follower_device_id": local_id,
                        "action_id": action_id,
                        "status": "duplicate",
                        "executed": False,
                    },
                )
                print(f"AOT_ACTION_DUPLICATE={action_id}")
                continue
            precondition = str(
                message.get("precondition") or ""
            ).strip()
            current = controller.snapshot(
                include_nodes=False
            )
            if current.get("fingerprint") != precondition:
                mark_action_processed(
                    state,
                    action_id,
                )
                _send_ack(
                    cfg,
                    {
                        "protocol": PROTOCOL_VERSION,
                        "session_id": session_id,
                        "reference_device_id": reference_device,
                        "follower_device_id": local_id,
                        "action_id": action_id,
                        "status": "out_of_sync",
                        "executed": False,
                        "before_fingerprint": current.get(
                            "fingerprint"
                        ),
                    },
                )
                print(f"AOT_OUT_OF_SYNC={action_id}")
                continue
            try:
                result = _execute_action(
                    message.get("action") or {},
                    precondition,
                )
            except controller.AotControllerError:
                mark_action_processed(
                    state,
                    action_id,
                )
                _send_ack(
                    cfg,
                    {
                        "protocol": PROTOCOL_VERSION,
                        "session_id": session_id,
                        "reference_device_id": reference_device,
                        "follower_device_id": local_id,
                        "action_id": action_id,
                        "status": "error",
                        "executed": False,
                    },
                )
                print(f"AOT_ACTION_ERROR={action_id}")
                continue
            mark_action_processed(
                state,
                action_id,
            )
            preview_b64, preview_bytes, preview_sha = (
                _preview_payload()
            )
            ack = {
                "protocol": PROTOCOL_VERSION,
                "session_id": session_id,
                "reference_device_id": reference_device,
                "follower_device_id": local_id,
                "action_id": action_id,
                "status": "success",
                "executed": True,
                "before_fingerprint": result.get(
                    "before_fingerprint"
                ),
                "after_fingerprint": result.get(
                    "after_fingerprint"
                ),
                "screen_changed": bool(
                    result.get("screen_changed")
                ),
                "preview_bytes": preview_bytes,
                "preview_sha256": preview_sha,
            }
            if preview_b64:
                ack["preview_b64"] = preview_b64
            _send_ack(cfg, ack)
            print(f"AOT_ACTION_SUCCESS={action_id}")
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _recv_ack(
    sock: socket.socket,
    *,
    action_id: str,
    follower_id: str,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        remain = max(
            1,
            int(deadline - time.monotonic()),
        )
        sock.settimeout(remain)
        try:
            opcode, payload = _ws_recv_frame(sock)
        except socket.timeout:
            continue
        if opcode == 0x8:
            raise AotRelayError("reference_websocket_closed")
        if opcode == 0x9:
            _ws_send_frame(sock, 0xA, payload)
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
        if normalize_device_id(
            message.get("follower_device_id")
        ) != follower_id:
            continue
        return message
    raise AotRelayError("ack_timeout")


def _dispatch_action(
    cfg: dict[str, str],
    payload: dict[str, Any],
) -> None:
    endpoint = worker_origin(
        cfg["worker_report_url"]
    ) + "/aot/control/action"
    status, data = _http_json(
        endpoint,
        cfg["agent_report_secret"],
        body=payload,
    )
    if status != 200 or data.get("ok") is not True:
        raise AotRelayError(
            "action_dispatch_failed:"
            + str(data.get("error") or status)
        )


def _save_preview_from_ack(
    ack: dict[str, Any],
    follower_id: str,
) -> pathlib.Path | None:
    value = ack.get("preview_b64")
    if not isinstance(value, str) or not value:
        return None
    try:
        data = base64.b64decode(
            value,
            validate=True,
        )
    except Exception:
        raise AotRelayError("preview_base64_invalid")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AotRelayError("preview_not_png")
    expected_sha = ack.get("preview_sha256")
    actual_sha = hashlib.sha256(data).hexdigest()
    if (
        isinstance(expected_sha, str)
        and expected_sha
        and expected_sha != actual_sha
    ):
        raise AotRelayError("preview_sha_mismatch")
    path = pathlib.Path(
        f"/storage/emulated/0/Download/AOT_Preview_{follower_id}.png"
    )
    temp = path.with_name(
        path.name + f".tmp-{os.getpid()}"
    )
    try:
        temp.write_bytes(data)
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
    return path


def _make_action_id(prefix: str) -> str:
    return (
        f"{prefix}-{int(time.time() * 1000)}-"
        f"{uuid.uuid4().hex[:8]}"
    )


def _find_selector_state(
    resource_id: str,
) -> tuple[str, bool]:
    snap = controller.snapshot(include_nodes=True)
    matches = [
        node
        for node in snap.get("nodes", [])
        if node.get("resource_id") == resource_id
    ]
    if len(matches) != 1:
        raise AotRelayError(
            f"selector_match_count_{len(matches)}"
        )
    return (
        str(snap.get("fingerprint") or ""),
        bool(matches[0].get("clickable")),
    )


def reference_test(
    *,
    session_id: str,
    follower_id: str,
    selector: str,
    open_package: str,
) -> int:
    local_id = normalize_device_id(_read_small(DEVICE_ID_PATH))
    if not local_id:
        raise AotRelayError("invalid_local_device_id")
    if local_id == follower_id:
        raise AotRelayError("reference_equals_follower")
    if not controller.root_available():
        raise AotRelayError("root_not_available")
    cfg = load_agent_config()
    health_url = worker_origin(
        cfg["worker_report_url"]
    ) + "/aot/control/health"
    health_status, health = _http_json(
        health_url,
        cfg["agent_report_secret"],
        method="GET",
    )
    if (
        health_status != 200
        or health.get("ok") is not True
        or health.get("protocol") != PROTOCOL_VERSION
    ):
        raise AotRelayError("aot_worker_not_ready")
    _launch_package(open_package)
    ref_ws = ws_connect(
        websocket_url(
            cfg["worker_report_url"],
            device_id=local_id,
            role="reference",
            session_id=session_id,
        ),
        cfg["agent_report_secret"],
    )
    try:
        before_fp, clickable = _find_selector_state(selector)
        if not before_fp or not clickable:
            raise AotRelayError("reference_selector_not_ready")
        tap_action_id = _make_action_id("tap")
        local_tap = controller.tap_selector(
            selector,
            before_fp,
        )
        dispatch = {
            "protocol": PROTOCOL_VERSION,
            "session_id": session_id,
            "reference_device_id": local_id,
            "target_device_ids": [follower_id],
            "action_id": tap_action_id,
            "expires_at": int(time.time() * 1000) + 15000,
            "precondition": before_fp,
            "action": {
                "kind": "tap_selector",
                "resource_id": selector,
            },
        }
        _dispatch_action(cfg, dispatch)
        tap_ack = _recv_ack(
            ref_ws,
            action_id=tap_action_id,
            follower_id=follower_id,
        )
        if tap_ack.get("status") != "success":
            raise AotRelayError(
                "follower_tap_status:"
                + str(tap_ack.get("status"))
            )
        if (
            tap_ack.get("after_fingerprint")
            != local_tap.get("after_fingerprint")
        ):
            raise AotRelayError(
                "follower_after_fingerprint_mismatch"
            )
        preview_path = _save_preview_from_ack(
            tap_ack,
            follower_id,
        )
        print("GROUP_TAP=SUCCESS")
        print("GROUP_SYNC=YES")
        if preview_path:
            print(f"PREVIEW_SAVED={preview_path}")
        else:
            print("PREVIEW_SAVED=NO")

        _dispatch_action(cfg, dispatch)
        duplicate_ack = _recv_ack(
            ref_ws,
            action_id=tap_action_id,
            follower_id=follower_id,
        )
        if duplicate_ack.get("status") != "duplicate":
            raise AotRelayError("dedupe_failed")
        print("ACK_DEDUPE=SUCCESS")

        account_fp = str(
            local_tap.get("after_fingerprint") or ""
        )
        back_action_id = _make_action_id("back")
        local_back = controller.press_back(account_fp)
        back_dispatch = {
            "protocol": PROTOCOL_VERSION,
            "session_id": session_id,
            "reference_device_id": local_id,
            "target_device_ids": [follower_id],
            "action_id": back_action_id,
            "expires_at": int(time.time() * 1000) + 15000,
            "precondition": account_fp,
            "action": {"kind": "back"},
        }
        _dispatch_action(cfg, back_dispatch)
        back_ack = _recv_ack(
            ref_ws,
            action_id=back_action_id,
            follower_id=follower_id,
        )
        if back_ack.get("status") != "success":
            raise AotRelayError(
                "follower_back_status:"
                + str(back_ack.get("status"))
            )
        if (
            back_ack.get("after_fingerprint")
            != local_back.get("after_fingerprint")
        ):
            raise AotRelayError(
                "follower_back_fingerprint_mismatch"
            )
        print("GROUP_BACK=SUCCESS")

        guard_action_id = _make_action_id("guard")
        guard_dispatch = {
            "protocol": PROTOCOL_VERSION,
            "session_id": session_id,
            "reference_device_id": local_id,
            "target_device_ids": [follower_id],
            "action_id": guard_action_id,
            "expires_at": int(time.time() * 1000) + 15000,
            "precondition": "0" * 24,
            "action": {
                "kind": "tap_selector",
                "resource_id": selector,
            },
        }
        _dispatch_action(cfg, guard_dispatch)
        guard_ack = _recv_ack(
            ref_ws,
            action_id=guard_action_id,
            follower_id=follower_id,
        )
        if (
            guard_ack.get("status") != "out_of_sync"
            or guard_ack.get("executed") is not False
        ):
            raise AotRelayError("out_of_sync_guard_failed")
        print("GROUP_OUT_OF_SYNC=SUCCESS")
        print("OUT_OF_SYNC_TAP_EXECUTED=NO")

        print("AOT_PHASE3_TWO_DEVICE=SUCCESS")
        return 0
    finally:
        try:
            ref_ws.close()
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AOT Group Control realtime relay"
    )
    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    follower = sub.add_parser("follower")
    follower.add_argument("--session", required=True)
    follower.add_argument(
        "--reference-device",
        required=True,
    )
    follower.add_argument("--open-package")

    reference = sub.add_parser("reference-test")
    reference.add_argument("--session", required=True)
    reference.add_argument("--follower", required=True)
    reference.add_argument(
        "--selector",
        default=(
            "org.swiftapps.swiftbackup:id/nav_account"
        ),
    )
    reference.add_argument(
        "--open-package",
        default="org.swiftapps.swiftbackup",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        session_id = normalize_session_id(args.session)
        if not session_id:
            raise AotRelayError("invalid_session_id")
        if args.command == "follower":
            reference = normalize_device_id(
                args.reference_device
            )
            if not reference:
                raise AotRelayError(
                    "invalid_reference_device"
                )
            return follower_loop(
                session_id=session_id,
                reference_device=reference,
                open_package=args.open_package,
            )
        if args.command == "reference-test":
            follower = normalize_device_id(args.follower)
            if not follower:
                raise AotRelayError(
                    "invalid_follower_device"
                )
            return reference_test(
                session_id=session_id,
                follower_id=follower,
                selector=args.selector,
                open_package=args.open_package,
            )
        raise AotRelayError("unknown_command")
    except AotRelayError as exc:
        print("AOT_RELAY=FAILED")
        print("REASON=" + str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
