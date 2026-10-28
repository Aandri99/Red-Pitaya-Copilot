#!/usr/bin/env python3
"""Loopback-only service scaffold for __APP_TITLE__."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import select
import signal
import socket
import struct
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


APP_VERSION = "__APP_VERSION__"
MAX_BODY_BYTES = 4096
WEBSOCKET_PROTOCOL_VERSION = 1
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WEBSOCKET_MAX_MESSAGE_BYTES = 4096
WEBSOCKET_MAX_MESSAGES_PER_SECOND = 5
WEBSOCKET_MAX_CLIENTS = 8
WEBSOCKET_STATUS_INTERVAL_SECONDS = 2.0
WEBSOCKET_IDLE_TIMEOUT_SECONDS = 30.0
shutdown_requested = threading.Event()
websocket_slots = threading.BoundedSemaphore(WEBSOCKET_MAX_CLIENTS)
safe_state: bool | None = None
service_epoch = datetime.now(timezone.utc).isoformat()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def recv_exact(connection: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = connection.recv(length - len(chunks))
        if not chunk:
            raise ConnectionError("WebSocket peer closed the connection")
        chunks.extend(chunk)
    return bytes(chunks)


def read_websocket_frame(connection: socket.socket) -> tuple[int, bytes]:
    first, second = recv_exact(connection, 2)
    final = bool(first & 0x80)
    reserved = first & 0x70
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if not final:
        raise ValueError("fragmented WebSocket messages are not supported")
    if reserved:
        raise ValueError("WebSocket extension bits are not supported")
    if not masked:
        raise ValueError("client WebSocket frames must be masked")
    if length == 126:
        length = struct.unpack("!H", recv_exact(connection, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(connection, 8))[0]
    if opcode >= 0x8 and length > 125:
        raise ValueError("WebSocket control frame is too large")
    if opcode == 0x8 and length == 1:
        raise ValueError("WebSocket close payload cannot contain one byte")
    if length > WEBSOCKET_MAX_MESSAGE_BYTES:
        raise ValueError("WebSocket message exceeds the configured size limit")
    mask = recv_exact(connection, 4)
    payload = bytearray(recv_exact(connection, length))
    for index in range(length):
        payload[index] ^= mask[index % 4]
    return opcode, bytes(payload)


def websocket_frame(opcode: int, payload: bytes) -> bytes:
    length = len(payload)
    if length <= 125:
        header = bytes((0x80 | opcode, length))
    elif length <= 65535:
        header = bytes((0x80 | opcode, 126)) + struct.pack("!H", length)
    else:
        header = bytes((0x80 | opcode, 127)) + struct.pack("!Q", length)
    return header + payload


def send_websocket_json(
    connection: socket.socket, payload: dict[str, object]
) -> None:
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(encoded) > WEBSOCKET_MAX_MESSAGE_BYTES:
        raise ValueError("server WebSocket message exceeds configured size limit")
    connection.sendall(websocket_frame(0x1, encoded))


def send_websocket_close(
    connection: socket.socket, code: int, reason: str
) -> None:
    payload = struct.pack("!H", code) + reason.encode("utf-8")[:123]
    try:
        connection.sendall(websocket_frame(0x8, payload))
    except OSError:
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "__APP_SLUG__/__APP_VERSION__"
    protocol_version = "HTTP/1.1"

    def log_message(self, message: str, *args: object) -> None:
        record = {
            "time": utc_now(),
            "remote": self.client_address[0],
            "message": message % args,
        }
        print(json.dumps(record, separators=(",", ":")), flush=True)

    def send_json(self, status_code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status_code: int, code: str, message: str) -> None:
        self.send_json(status_code, {
            "ok": False,
            "error": {"code": code, "message": message},
        })

    def status_payload(self) -> dict[str, object]:
        return {
            "ok": True,
            "status": {
                "service": "integration_required",
                "hardware_ready": False,
                "safe_state": safe_state,
                "stop_implemented": False,
                "version": APP_VERSION,
                "server_time": utc_now(),
                "service_epoch": service_epoch,
            },
        }

    def websocket_message(
        self, message_type: str, **fields: object
    ) -> dict[str, object]:
        self.websocket_sequence += 1
        return {
            "type": message_type,
            "protocol_version": WEBSOCKET_PROTOCOL_VERSION,
            "service_epoch": service_epoch,
            "sequence": self.websocket_sequence,
            **fields,
        }

    def websocket_origin_allowed(self) -> bool:
        origin = self.headers.get("Origin", "")
        host = self.headers.get("Host", "")
        if not origin or not host:
            return False
        try:
            parsed_origin = urlparse(origin)
            parsed_host = urlparse("//" + host)
            origin_host = parsed_origin.hostname
            expected_host = parsed_host.hostname
            if parsed_origin.scheme not in {"http", "https"}:
                return False
            default_port = 443 if parsed_origin.scheme == "https" else 80
            origin_port = parsed_origin.port or default_port
            expected_port = parsed_host.port or default_port
        except ValueError:
            return False
        return bool(
            origin_host
            and expected_host
            and origin_host.lower() == expected_host.lower()
            and origin_port == expected_port
        )

    def upgrade_to_websocket(self) -> bool:
        if self.headers.get("Upgrade", "").lower() != "websocket":
            self.send_error_json(426, "upgrade_required", "WebSocket upgrade required")
            return False
        connection_tokens = {
            token.strip().lower()
            for token in self.headers.get("Connection", "").split(",")
        }
        if "upgrade" not in connection_tokens:
            self.send_error_json(400, "invalid_upgrade", "Invalid Connection header")
            return False
        if self.headers.get("Sec-WebSocket-Version") != "13":
            self.send_error_json(400, "unsupported_version", "WebSocket version 13 required")
            return False
        if not self.websocket_origin_allowed():
            self.send_error_json(403, "origin_rejected", "Same-origin WebSocket required")
            return False
        key = self.headers.get("Sec-WebSocket-Key", "")
        try:
            decoded_key = base64.b64decode(key, validate=True)
        except (ValueError, TypeError):
            decoded_key = b""
        if len(decoded_key) != 16:
            self.send_error_json(400, "invalid_key", "Invalid WebSocket key")
            return False
        accept = base64.b64encode(
            hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()
        ).decode("ascii")
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.close_connection = True
        return True

    def websocket_status(self) -> dict[str, object]:
        return self.websocket_message(
            "status",
            status=self.status_payload()["status"],
        )

    def handle_websocket_text(
        self, payload: bytes
    ) -> dict[str, object]:
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self.websocket_message(
                "error",
                error={"code": "invalid_json", "message": "Message must be UTF-8 JSON"},
            )
        if not isinstance(value, dict):
            return self.websocket_message(
                "error",
                error={"code": "invalid_message", "message": "Message must be an object"},
            )
        message_type = value.get("type")
        if message_type == "status" and set(value) == {"type"}:
            return self.websocket_status()
        if message_type == "ping" and set(value) == {"type", "request_id"}:
            request_id = value.get("request_id")
            if not isinstance(request_id, str) or len(request_id) > 64:
                return self.websocket_message(
                    "error",
                    error={
                        "code": "invalid_request_id",
                        "message": "request_id must be a string of at most 64 characters",
                    },
                )
            return self.websocket_message(
                "pong",
                request_id=request_id,
                server_time=utc_now(),
            )
        return self.websocket_message(
            "error",
            error={
                "code": "unknown_message",
                "message": "Allowed client message types are ping and status",
            },
        )

    def handle_websocket(self) -> None:
        if not websocket_slots.acquire(blocking=False):
            self.send_error_json(
                503,
                "websocket_capacity",
                "WebSocket client capacity reached",
            )
            return
        try:
            if not self.upgrade_to_websocket():
                return
            self.websocket_sequence = 0
            connection = self.connection
            connection.settimeout(2.0)
            last_client_message = time.monotonic()
            last_status = 0.0
            rate_window_started = time.monotonic()
            rate_window_count = 0
            try:
                send_websocket_json(
                    connection,
                    self.websocket_message(
                        "hello",
                        heartbeat_seconds=10,
                        max_message_bytes=WEBSOCKET_MAX_MESSAGE_BYTES,
                        max_clients=WEBSOCKET_MAX_CLIENTS,
                    ),
                )
                while not shutdown_requested.is_set():
                    now = time.monotonic()
                    if now - last_client_message > WEBSOCKET_IDLE_TIMEOUT_SECONDS:
                        send_websocket_close(connection, 1001, "idle timeout")
                        return
                    if now - last_status >= WEBSOCKET_STATUS_INTERVAL_SECONDS:
                        send_websocket_json(connection, self.websocket_status())
                        last_status = now

                    readable, _writable, _exceptional = select.select(
                        [connection], [], [], 0.5
                    )
                    if not readable:
                        continue
                    opcode, payload = read_websocket_frame(connection)
                    last_client_message = time.monotonic()
                    if last_client_message - rate_window_started >= 1.0:
                        rate_window_started = last_client_message
                        rate_window_count = 0
                    rate_window_count += 1
                    if rate_window_count > WEBSOCKET_MAX_MESSAGES_PER_SECOND:
                        send_websocket_close(connection, 1008, "message rate exceeded")
                        return
                    if opcode == 0x8:
                        send_websocket_close(connection, 1000, "normal closure")
                        return
                    if opcode == 0x9:
                        connection.sendall(websocket_frame(0xA, payload))
                        continue
                    if opcode == 0xA:
                        continue
                    if opcode != 0x1:
                        send_websocket_close(connection, 1003, "text messages only")
                        return
                    send_websocket_json(
                        connection,
                        self.handle_websocket_text(payload),
                    )
            except (ConnectionError, OSError, ValueError):
                send_websocket_close(connection, 1002, "protocol or transport error")
        finally:
            websocket_slots.release()

    def do_GET(self) -> None:
        if self.path == "/ws":
            self.handle_websocket()
            return
        if self.path in ("/status", "/version"):
            self.send_json(200, self.status_payload())
            return
        self.send_error_json(404, "not_found", "Unknown endpoint")

    def origin_allowed_for_mutation(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        host = self.headers.get("Host", "")
        try:
            parsed = urlparse(origin)
        except ValueError:
            return False
        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.netloc.lower() == host.lower()
        )

    def do_POST(self) -> None:
        if self.path != "/stop":
            self.close_connection = True
            self.send_error_json(404, "not_found", "Unknown endpoint")
            return

        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            self.close_connection = True
            self.send_error_json(
                409,
                "content_type_required",
                "Mutations require Content-Type: application/json",
            )
            return
        if self.headers.get("X-RedPitaya-Request") != "1":
            self.close_connection = True
            self.send_error_json(
                409,
                "request_header_required",
                "Mutations require X-RedPitaya-Request: 1",
            )
            return
        if not self.origin_allowed_for_mutation():
            self.close_connection = True
            self.send_error_json(
                409,
                "origin_rejected",
                "Cross-origin mutation rejected",
            )
            return

        length_text = self.headers.get("Content-Length")
        if length_text is None:
            self.close_connection = True
            self.send_error_json(
                411,
                "content_length_required",
                "Content-Length is required",
            )
            return
        try:
            length = int(length_text)
        except ValueError:
            self.close_connection = True
            self.send_error_json(400, "invalid_length", "Invalid Content-Length")
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self.close_connection = True
            self.send_error_json(413, "body_too_large", "Request body is too large")
            return
        body = self.rfile.read(length)
        try:
            parsed = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_error_json(400, "invalid_json", "Body must be valid JSON")
            return
        if parsed != {}:
            self.send_error_json(400, "unexpected_fields", "Stop accepts only an empty object")
            return

        # SCAFFOLD_HARDWARE_INTEGRATION_REQUIRED
        # Replace this rejection only after implementing and testing the single
        # resource owner's idempotent safe-stop operation.
        self.send_error_json(
            501,
            "integration_required",
            "Safe-stop hardware integration is not implemented",
        )

    def do_PUT(self) -> None:
        self.close_connection = True
        self.send_error_json(405, "method_not_allowed", "Method not allowed")

    def do_DELETE(self) -> None:
        self.close_connection = True
        self.send_error_json(405, "method_not_allowed", "Method not allowed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=__SERVICE_PORT__)
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        parser.error("the scaffold may bind only to 127.0.0.1")
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    return args


def request_shutdown(_signum: int, _frame: object) -> None:
    shutdown_requested.set()


def main() -> int:
    args = parse_args()
    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.timeout = 0.5
    print(json.dumps({
        "time": utc_now(),
        "event": "service_started",
        "host": args.host,
        "port": args.port,
        "service_epoch": service_epoch,
    }, separators=(",", ":")), flush=True)
    try:
        while not shutdown_requested.is_set():
            server.handle_request()
    finally:
        server.server_close()
        print(json.dumps({
            "time": utc_now(),
            "event": "service_stopped",
            "safe_state": safe_state,
        }, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
