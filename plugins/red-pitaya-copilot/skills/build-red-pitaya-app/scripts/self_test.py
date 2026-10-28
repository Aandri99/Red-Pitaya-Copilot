#!/usr/bin/env python3
"""Run the Red Pitaya app skill's complete offline acceptance test.

This test intentionally has no third-party dependencies.  It creates all
temporary artifacts beneath the skill directory, invokes the public scaffold
and validation command-line interfaces, and removes only the exact guarded
temporary directory that it created.

Network safety:
* the only permitted address is 127.0.0.1;
* no hostname lookup is performed;
* no Red Pitaya or other hardware is contacted.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import py_compile
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = SKILL_ROOT.parents[1]
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
SCAFFOLD_SCRIPT = SCRIPTS_ROOT / "new_red_pitaya_app.py"
VALIDATOR_SCRIPT = SCRIPTS_ROOT / "validate_red_pitaya_app.py"
PACKAGER_SCRIPT = SCRIPTS_ROOT / "package_red_pitaya_app.py"
PACKAGER_TEST_SCRIPT = SCRIPTS_ROOT / "test_package_red_pitaya_app.py"
VALIDATOR_TEST_SCRIPT = SCRIPTS_ROOT / "test_validate_red_pitaya_app.py"
PROJECT_SCRIPT = PLUGIN_ROOT / "scripts" / "new_redpitaya_project.py"
TEMP_PREFIX = ".self-test-"
LOOPBACK = "127.0.0.1"
OS_RELEASES = ("2.00-35", "2.07-48", "3.00-57")
BACKENDS = (
    "static",
    "nginx-lua",
    "controllerhf",
    "python-service",
    "cpp-service",
    "hybrid",
)
SERVICE_BACKENDS = {"python-service", "cpp-service", "hybrid"}
PYTHON_WEBSOCKET_BACKENDS = {"python-service", "hybrid"}
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
PROCESS_TIMEOUT_SECONDS = 15.0
SERVICE_START_TIMEOUT_SECONDS = 8.0
SOCKET_TIMEOUT_SECONDS = 3.0


class CheckFailure(RuntimeError):
    """A self-test assertion failed."""


@dataclass
class CheckResult:
    name: str
    outcome: str
    detail: str = ""


class Reporter:
    """Collect results while allowing independent checks to continue."""

    def __init__(self) -> None:
        self.results: list[CheckResult] = []

    def pass_check(self, name: str, detail: str = "") -> None:
        self.results.append(CheckResult(name, "PASS", detail))
        print(f"[PASS] {name}{': ' + detail if detail else ''}")

    def fail_check(self, name: str, error: BaseException | str) -> None:
        detail = str(error)
        self.results.append(CheckResult(name, "FAIL", detail))
        print(f"[FAIL] {name}: {detail}")

    def skip_check(self, name: str, detail: str) -> None:
        self.results.append(CheckResult(name, "SKIP", detail))
        print(f"[SKIP] {name}: {detail}")

    @property
    def failures(self) -> list[CheckResult]:
        return [result for result in self.results if result.outcome == "FAIL"]

    def summary(self) -> None:
        totals = {
            outcome: sum(result.outcome == outcome for result in self.results)
            for outcome in ("PASS", "FAIL", "SKIP")
        }
        print(
            "\nSelf-test summary: "
            f"{totals['PASS']} passed, {totals['FAIL']} failed, "
            f"{totals['SKIP']} skipped"
        )
        if self.failures:
            print("Failed checks:")
            for result in self.failures:
                print(f"  - {result.name}: {result.detail}")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def guarded_temp_path(path: Path) -> Path:
    """Return a resolved test path only when it is the exact safe shape."""
    skill_root = SKILL_ROOT.resolve(strict=True)
    resolved = path.resolve(strict=False)
    require(resolved != skill_root, "temporary path cannot equal the skill root")
    require(
        resolved.parent == skill_root,
        f"temporary path escaped the skill root: {resolved}",
    )
    require(
        resolved.name.startswith(TEMP_PREFIX)
        and len(resolved.name) > len(TEMP_PREFIX),
        f"temporary path lacks the required {TEMP_PREFIX!r} prefix: {resolved}",
    )
    require(not path.is_symlink(), f"temporary path must not be a symlink: {path}")
    return resolved


def make_temp_root() -> Path:
    # Do not use tempfile.mkdtemp here.  Python 3.13 applies a Windows-specific
    # restrictive ACL for mode 0o700 directories; under some sandboxed launch
    # tokens that can prevent the creating process from traversing the result.
    for _attempt in range(100):
        raw = SKILL_ROOT / f"{TEMP_PREFIX}{secrets.token_hex(8)}"
        try:
            raw.mkdir()
        except FileExistsError:
            continue
        resolved = guarded_temp_path(raw)
        require(resolved.is_dir(), f"temporary directory was not created: {resolved}")
        return resolved
    raise CheckFailure("could not allocate a unique guarded temporary directory")


def cleanup_temp_root(path: Path) -> None:
    """Remove only the guarded temporary directory created by this script."""
    resolved = guarded_temp_path(path)
    if not resolved.exists():
        return
    require(resolved.is_dir(), f"refusing to remove non-directory: {resolved}")
    last_error: OSError | None = None
    for attempt in range(5):
        try:
            shutil.rmtree(resolved)
            last_error = None
            break
        except OSError as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(0.1 * (attempt + 1))
    if last_error is not None:
        raise CheckFailure(f"could not remove guarded temporary directory: {last_error}")
    require(not resolved.exists(), f"temporary cleanup was incomplete: {resolved}")


def run_command(arguments: list[str], *, timeout: float = PROCESS_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments,
        cwd=SKILL_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        command = " ".join(arguments)
        raise CheckFailure(
            f"command failed with exit code {result.returncode}: {command}\n"
            f"stdout:\n{result.stdout.strip()}\n"
            f"stderr:\n{result.stderr.strip()}"
        )
    return result


def parse_json_output(result: subprocess.CompletedProcess[str], label: str) -> dict:
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CheckFailure(
            f"{label} did not return one JSON document: {exc}; "
            f"stdout={result.stdout!r}"
        ) from exc
    require(isinstance(value, dict), f"{label} JSON output must be an object")
    return value


def run_expected_failure(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments,
        cwd=SKILL_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=PROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    require(result.returncode != 0, "command unexpectedly succeeded")
    return result


def reserve_loopback_port() -> int:
    """Ask the OS for a currently free loopback TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK, 0))
        address, port = probe.getsockname()
        require(address == LOOPBACK, f"port probe bound an unexpected address: {address}")
        require(1024 <= port <= 65535, f"invalid loopback port selected: {port}")
        return int(port)


def slug_for(os_release: str, backend: str) -> str:
    os_token = os_release.replace(".", "").replace("-", "")
    backend_token = backend.replace("-", "_")
    return f"selftest_{os_token}_{backend_token}"


def scaffold_arguments(
    temp_root: Path,
    os_release: str,
    backend: str,
    port: int | None,
    *,
    force: bool = False,
) -> list[str]:
    slug = slug_for(os_release, backend)
    arguments = [
        sys.executable,
        str(SCAFFOLD_SCRIPT),
        "--slug",
        slug,
        "--title",
        f"Self Test {os_release} {backend}",
        "--description",
        "Offline generated-app acceptance fixture",
        "--version",
        "1.2.3",
        "--revision",
        "self-test",
        "--os-release",
        os_release,
        "--model",
        "z20_125",
        "--role",
        "self_test",
        "--backend",
        backend,
        "--output",
        str(temp_root),
    ]
    if backend in SERVICE_BACKENDS:
        require(port is not None, f"{backend} requires a test service port")
        arguments.extend(("--service-port", str(port)))
    else:
        require(port is None, f"{backend} must not receive a service port")
    if backend in PYTHON_WEBSOCKET_BACKENDS:
        arguments.append("--websocket")
    if force:
        arguments.append("--force")
    return arguments


def scaffold_app(
    temp_root: Path,
    os_release: str,
    backend: str,
    port: int | None,
    *,
    force: bool = False,
) -> tuple[Path, dict]:
    result = run_command(
        scaffold_arguments(
            temp_root,
            os_release,
            backend,
            port,
            force=force,
        )
    )
    payload = parse_json_output(result, "scaffolder")
    require(payload.get("ok") is True, "scaffolder did not report ok=true")
    require(payload.get("os_release") == os_release, "scaffolder OS result mismatch")
    require(payload.get("backend") == backend, "scaffolder backend result mismatch")
    require(payload.get("websocket") is (backend in PYTHON_WEBSOCKET_BACKENDS), "scaffolder WebSocket result mismatch")
    app_path = Path(str(payload.get("path", ""))).resolve(strict=True)
    require(app_path.parent == temp_root.resolve(), "generated app escaped temporary root")
    require(app_path.name == slug_for(os_release, backend), "generated app slug mismatch")
    return app_path, payload


def validate_app(app_path: Path, os_release: str, backend: str) -> dict:
    result = run_command(
        [
            sys.executable,
            str(VALIDATOR_SCRIPT),
            str(app_path),
            "--os-release",
            os_release,
            "--backend",
            backend,
            "--json",
        ]
    )
    payload = parse_json_output(result, "validator")
    require(payload.get("ok") is True, "validator did not report ok=true")
    errors = payload.get("errors")
    require(errors == [], f"validator returned unexpected errors: {errors!r}")
    return payload


def verify_red_pitaya_theme(app_path: Path) -> None:
    css = (app_path / "css" / "app.css").read_text(encoding="utf-8").lower()
    index = (app_path / "index.html").read_text(encoding="utf-8").lower()
    for color in ("#2a2a2a", "#e72d2f", "#000000", "#ffffff", "#ebebec"):
        require(color in css, f"generated theme is missing company color {color}")
    require("#00b8b8" not in css, "legacy teal remains in generated theme")
    require("red pitaya instrument application" in index, "brand eyebrow is missing")
    require('class="brand-mark"' in index, "brand mark is missing")
    require('name="theme-color" content="#000000"' in index, "theme color is missing")
    for element_id in (
        "statepill",
        "connectionvalue",
        "servicevalue",
        "safevalue",
        "versionvalue",
        "timevalue",
        "refreshbtn",
        "stopbtn",
        "transportnote",
        "log",
        "toast",
    ):
        require(f'id="{element_id}"' in index, f"functional DOM ID {element_id} is missing")


def compile_generated_python(app_path: Path) -> int:
    sources = sorted(app_path.rglob("*.py"))
    for source in sources:
        py_compile.compile(str(source), doraise=True)
    return len(sources)


def check_generated_javascript(app_path: Path, node: str) -> int:
    sources = sorted(app_path.rglob("*.js"))
    for source in sources:
        run_command([node, "--check", str(source)])
    return len(sources)


def exercise_force_backup(
    temp_root: Path,
    app_path: Path,
    port: int | None,
) -> tuple[Path, Path]:
    marker_name = ".recoverable-self-test-marker"
    marker_value = "original scaffold retained by recoverable --force backup\n"
    marker = app_path / marker_name
    marker.write_text(marker_value, encoding="utf-8", newline="\n")
    original_manifest = (app_path / "redpitaya-project.json").read_bytes()

    replacement, payload = scaffold_app(
        temp_root,
        "3.00-57",
        "static",
        port,
        force=True,
    )
    require(replacement == app_path, "--force created an unexpected destination")
    backup_text = payload.get("backup_path")
    require(isinstance(backup_text, str) and backup_text, "--force did not report backup_path")
    backup = Path(backup_text).resolve(strict=True)
    require(backup.parent == temp_root.resolve(), "backup escaped temporary root")
    expected_name = re.compile(r"^\.selftest_30057_static\.backup-\d{8}T\d{6}Z$")
    require(expected_name.fullmatch(backup.name) is not None, f"unexpected backup name: {backup.name}")
    require(not backup.is_symlink(), "backup must not be a symlink")
    require((backup / marker_name).read_text(encoding="utf-8") == marker_value, "backup did not preserve the marker")
    require((backup / "redpitaya-project.json").read_bytes() == original_manifest, "backup changed the original manifest")
    require(not (replacement / marker_name).exists(), "replacement incorrectly retained original marker")

    validate_app(replacement, "3.00-57", "static")
    restore_root = temp_root / "restore-proof"
    restore_root.mkdir()
    restored = restore_root / app_path.name
    shutil.copytree(backup, restored)
    validate_app(restored, "3.00-57", "static")
    require((restored / marker_name).read_text(encoding="utf-8") == marker_value, "restored backup lost its marker")
    return replacement, backup


def exercise_project_first_manifest(temp_root: Path) -> tuple[Path, Path]:
    slug = "projectfirst_contract"
    project = temp_root / slug
    manifest_path = project / "redpitaya-project.json"
    port = reserve_loopback_port()
    run_command(
        [
            sys.executable,
            str(PROJECT_SCRIPT),
            "--slug",
            slug,
            "--title",
            "Project First Contract",
            "--os-release",
            "3.00-57",
            "--model",
            "z20_125",
            "--components",
            "app-fpga",
            "--app-architecture",
            "service",
            "--role",
            "acquisition",
            "--service-port",
            str(port),
            "--output",
            str(manifest_path),
        ]
    )
    original = json.loads(manifest_path.read_text(encoding="utf-8"))
    result = run_command(
        [
            sys.executable,
            str(SCAFFOLD_SCRIPT),
            "--manifest",
            str(manifest_path),
            "--backend",
            "python-service",
            "--service-port",
            str(port),
            "--websocket",
            "--revision",
            "self-test",
        ]
    )
    payload = parse_json_output(result, "manifest-mode scaffolder")
    require(payload.get("manifest_mode") is True, "manifest mode was not reported")
    generated = Path(str(payload.get("path", ""))).resolve(strict=True)
    require(generated == project.resolve(), "manifest mode changed the project path")
    backup = Path(str(payload.get("backup_path", ""))).resolve(strict=True)
    require(backup.parent == temp_root.resolve(), "manifest backup escaped test root")
    require(
        json.loads((backup / "redpitaya-project.json").read_text(encoding="utf-8"))
        == original,
        "manifest-mode backup did not preserve the original contract",
    )
    updated = json.loads(
        (generated / "redpitaya-project.json").read_text(encoding="utf-8")
    )
    for section in ("project", "target", "fpga", "deployment", "safety"):
        require(updated[section] == original[section], f"manifest mode changed {section}")
    require(
        updated["application"]["architecture"] == "service"
        and updated["application"]["service_port"] == port,
        "manifest mode did not create the selected application contract",
    )
    validate_app(generated, "3.00-57", "python-service")
    return generated, backup


def exercise_malicious_metadata_rejection(temp_root: Path) -> None:
    slug = "malicious_metadata"
    result = run_expected_failure(
        [
            sys.executable,
            str(SCAFFOLD_SCRIPT),
            "--slug",
            slug,
            "--title",
            "</title><img src=x onerror=alert(1)>",
            "--description",
            "must be rejected",
            "--version",
            "1.0.0",
            "--revision",
            "self-test",
            "--os-release",
            "3.00-57",
            "--model",
            "z20_125",
            "--role",
            "self_test",
            "--backend",
            "static",
            "--output",
            str(temp_root),
        ]
    )
    require(
        "unsafe for the generated HTML" in result.stderr,
        "metadata rejection did not explain the cross-context safety rule",
    )
    require(not (temp_root / slug).exists(), "rejected metadata created an app")


class BufferedSocket:
    """Minimal buffered reader that preserves bytes after HTTP headers."""

    def __init__(self, connection: socket.socket) -> None:
        self.connection = connection
        self.buffer = bytearray()

    def read_exact(self, length: int) -> bytes:
        while len(self.buffer) < length:
            chunk = self.connection.recv(max(4096, length - len(self.buffer)))
            if not chunk:
                raise CheckFailure("WebSocket peer closed before the expected bytes arrived")
            self.buffer.extend(chunk)
        value = bytes(self.buffer[:length])
        del self.buffer[:length]
        return value

    def read_until(self, marker: bytes, maximum: int) -> bytes:
        while marker not in self.buffer:
            require(len(self.buffer) < maximum, "HTTP upgrade headers exceeded the size limit")
            chunk = self.connection.recv(4096)
            if not chunk:
                raise CheckFailure("peer closed during the WebSocket HTTP upgrade")
            self.buffer.extend(chunk)
        index = self.buffer.index(marker) + len(marker)
        value = bytes(self.buffer[:index])
        del self.buffer[:index]
        return value


def read_server_frame(reader: BufferedSocket) -> tuple[int, bytes]:
    first, second = reader.read_exact(2)
    require(first & 0x80 == 0x80, "server sent a fragmented WebSocket frame")
    require(first & 0x70 == 0, "server set an unexpected WebSocket RSV bit")
    require(second & 0x80 == 0, "server WebSocket frames must not be masked")
    opcode = first & 0x0F
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", reader.read_exact(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", reader.read_exact(8))[0]
    require(length <= 4096, f"server frame exceeded protocol limit: {length}")
    return opcode, reader.read_exact(length)


def masked_client_frame(opcode: int, payload: bytes) -> bytes:
    require(len(payload) <= 4096, "test client payload exceeds protocol limit")
    mask = os.urandom(4)
    length = len(payload)
    if length <= 125:
        header = bytes((0x80 | opcode, 0x80 | length))
    elif length <= 65535:
        header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
    else:
        header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", length)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return header + mask + masked


def unmasked_client_frame(opcode: int, payload: bytes) -> bytes:
    """Build a deliberately invalid short client frame for rejection testing."""
    require(len(payload) <= 125, "unmasked rejection fixture must be a short frame")
    return bytes((0x80 | opcode, len(payload))) + payload


def send_client_json(connection: socket.socket, payload: dict) -> None:
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    connection.sendall(masked_client_frame(0x1, encoded))


def receive_json(
    reader: BufferedSocket,
    wanted_type: str,
    observed: list[dict],
    *,
    maximum_frames: int = 12,
) -> dict:
    for _index in range(maximum_frames):
        opcode, payload = read_server_frame(reader)
        if opcode == 0x9:
            reader.connection.sendall(masked_client_frame(0xA, payload))
            continue
        require(opcode == 0x1, f"expected text frame, received opcode {opcode}")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CheckFailure(f"server frame was not UTF-8 JSON: {exc}") from exc
        require(isinstance(value, dict), "server WebSocket message must be an object")
        observed.append(value)
        if value.get("type") == wanted_type:
            return value
    raise CheckFailure(f"did not receive WebSocket message type {wanted_type!r}")


def direct_http_json(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    require(method in {"GET", "POST", "PUT"}, f"unexpected self-test HTTP method: {method}")
    require(path.startswith("/"), "self-test HTTP path must be absolute")
    connection = http.client.HTTPConnection(
        LOOPBACK,
        port,
        timeout=SOCKET_TIMEOUT_SECONDS,
    )
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload_bytes = response.read()
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CheckFailure(
                f"{method} {path} returned non-JSON body: {payload_bytes!r}"
            ) from exc
        require(isinstance(payload, dict), f"{method} {path} JSON body must be an object")
        return response.status, payload
    finally:
        connection.close()


def wait_for_service(port: int, process: subprocess.Popen[str]) -> dict:
    deadline = time.monotonic() + SERVICE_START_TIMEOUT_SECONDS
    last_error = "service did not answer"
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            stdout, stderr = process.communicate()
            raise CheckFailure(
                f"service exited during startup with {return_code}; "
                f"stdout={stdout!r}; stderr={stderr!r}"
            )
        try:
            status, payload = direct_http_json(port, "GET", "/status")
            require(status == 200, f"GET /status returned HTTP {status}")
            return payload
        except (OSError, CheckFailure, http.client.HTTPException) as exc:
            last_error = str(exc)
            time.sleep(0.05)
    raise CheckFailure(f"service was not ready within the deadline: {last_error}")


def websocket_handshake(port: int) -> tuple[socket.socket, BufferedSocket]:
    connection = socket.create_connection(
        (LOOPBACK, port),
        timeout=SOCKET_TIMEOUT_SECONDS,
    )
    connection.settimeout(SOCKET_TIMEOUT_SECONDS)
    try:
        return validate_websocket_handshake(connection, port)
    except BaseException:
        connection.close()
        raise


def rejected_websocket_origin_status(port: int, origin: str) -> str:
    """Return the status line for an upgrade with a deliberately wrong origin."""
    connection = socket.create_connection(
        (LOOPBACK, port),
        timeout=SOCKET_TIMEOUT_SECONDS,
    )
    connection.settimeout(SOCKET_TIMEOUT_SECONDS)
    try:
        reader = BufferedSocket(connection)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET /ws HTTP/1.1\r\n"
            f"Host: {LOOPBACK}:{port}\r\n"
            f"Origin: {origin}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "\r\n"
        ).encode("ascii")
        connection.sendall(request)
        headers = reader.read_until(b"\r\n\r\n", maximum=16384)
        return headers.decode("iso-8859-1").split("\r\n", 1)[0]
    finally:
        connection.close()


def websocket_upgrade_status(port: int) -> str:
    """Return the HTTP status line for a same-origin upgrade attempt."""
    connection = socket.create_connection(
        (LOOPBACK, port),
        timeout=SOCKET_TIMEOUT_SECONDS,
    )
    connection.settimeout(SOCKET_TIMEOUT_SECONDS)
    try:
        reader = BufferedSocket(connection)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET /ws HTTP/1.1\r\n"
            f"Host: {LOOPBACK}:{port}\r\n"
            f"Origin: http://{LOOPBACK}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "\r\n"
        ).encode("ascii")
        connection.sendall(request)
        headers = reader.read_until(b"\r\n\r\n", maximum=16384)
        return headers.decode("iso-8859-1").split("\r\n", 1)[0]
    finally:
        connection.close()


def validate_websocket_handshake(
    connection: socket.socket,
    port: int,
) -> tuple[socket.socket, BufferedSocket]:
    reader = BufferedSocket(connection)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        "GET /ws HTTP/1.1\r\n"
        f"Host: {LOOPBACK}:{port}\r\n"
        f"Origin: http://{LOOPBACK}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "\r\n"
    ).encode("ascii")
    connection.sendall(request)
    header_bytes = reader.read_until(b"\r\n\r\n", maximum=16384)
    try:
        header_text = header_bytes.decode("iso-8859-1")
    except UnicodeDecodeError as exc:
        connection.close()
        raise CheckFailure(f"upgrade response headers were invalid: {exc}") from exc
    lines = header_text.split("\r\n")
    require(
        len(lines) >= 2 and lines[0].startswith("HTTP/1.1 101 "),
        f"RFC 6455 upgrade must return HTTP/1.1 101, received {lines[0]!r}",
    )
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        require(":" in line, f"malformed HTTP upgrade header: {line!r}")
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    require(headers.get("upgrade", "").lower() == "websocket", "missing Upgrade: websocket")
    connection_tokens = {
        token.strip().lower()
        for token in headers.get("connection", "").split(",")
    }
    require("upgrade" in connection_tokens, "missing Connection: Upgrade")
    expected_accept = base64.b64encode(
        hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()
    ).decode("ascii")
    require(
        headers.get("sec-websocket-accept") == expected_accept,
        "Sec-WebSocket-Accept did not match the RFC 6455 challenge",
    )
    return connection, reader


def assert_message_envelope(message: dict, expected_type: str) -> None:
    require(message.get("type") == expected_type, f"expected {expected_type!r} message")
    require(message.get("protocol_version") == 1, "protocol_version must equal 1")
    require(isinstance(message.get("service_epoch"), str), "service_epoch must be a string")
    sequence = message.get("sequence")
    require(isinstance(sequence, int) and sequence > 0, "sequence must be a positive integer")


def live_test_python_service(app_path: Path, port: int) -> None:
    service_script = app_path / "service" / "app_service.py"
    require(service_script.is_file(), f"generated service is missing: {service_script}")
    process = subprocess.Popen(
        [
            sys.executable,
            str(service_script),
            "--host",
            LOOPBACK,
            "--port",
            str(port),
        ],
        cwd=service_script.parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    connection: socket.socket | None = None
    try:
        initial_http = wait_for_service(port, process)
        require(initial_http.get("ok") is True, "GET /status did not report ok=true")
        status_object = initial_http.get("status")
        require(isinstance(status_object, dict), "GET /status lacks status object")
        require(
            status_object.get("service") == "integration_required",
            "untouched scaffold must report integration_required",
        )
        require(
            status_object.get("hardware_ready") is False,
            "untouched scaffold must not claim hardware ready",
        )
        require(
            status_object.get("safe_state") is None,
            "untouched scaffold must report safe state as unknown",
        )
        require(
            status_object.get("stop_implemented") is False,
            "untouched scaffold must report stop_implemented=false",
        )

        stop_code, stop_payload = direct_http_json(
            port,
            "POST",
            "/stop",
            body=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": "2",
                "X-RedPitaya-Request": "1",
                "Origin": f"http://{LOOPBACK}:{port}",
            },
        )
        require(stop_code == 501, f"stub safe-stop returned HTTP {stop_code}, expected 501")
        require(
            stop_payload.get("error", {}).get("code") == "integration_required",
            "stub safe-stop did not return integration_required",
        )

        missing_header_code, missing_header = direct_http_json(
            port,
            "POST",
            "/stop",
            body=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": "2"},
        )
        require(
            missing_header_code == 409,
            f"mutation without custom header returned {missing_header_code}",
        )
        require(
            missing_header.get("error", {}).get("code")
            == "request_header_required",
            "mutation without custom header was not explicitly rejected",
        )

        cross_origin_code, cross_origin = direct_http_json(
            port,
            "POST",
            "/stop",
            body=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": "2",
                "X-RedPitaya-Request": "1",
                "Origin": "https://attacker.invalid",
            },
        )
        require(
            cross_origin_code == 409,
            f"cross-origin mutation returned {cross_origin_code}",
        )
        require(
            cross_origin.get("error", {}).get("code") == "origin_rejected",
            "cross-origin mutation was not explicitly rejected",
        )

        http_code, unknown_http = direct_http_json(
            port,
            "PUT",
            "/status",
            body=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": "2"},
        )
        require(http_code == 405, f"unknown HTTP mutation returned {http_code}, expected 405")
        require(unknown_http.get("ok") is False, "unknown HTTP mutation was not rejected")

        wrong_origin_port = 1024 if port != 1024 else 1025
        rejected_status = rejected_websocket_origin_status(
            port,
            f"http://{LOOPBACK}:{wrong_origin_port}",
        )
        require(
            rejected_status.startswith("HTTP/1.1 403 "),
            f"cross-port WebSocket origin was not rejected: {rejected_status!r}",
        )

        connection, reader = websocket_handshake(port)
        observed: list[dict] = []
        hello = receive_json(reader, "hello", observed)
        assert_message_envelope(hello, "hello")
        require(hello.get("sequence") == 1, "new connection must start at sequence 1")
        require(hello.get("max_message_bytes") == 4096, "hello advertised the wrong message limit")
        require(
            isinstance(hello.get("heartbeat_seconds"), (int, float)),
            "hello lacks heartbeat_seconds",
        )

        first_status = receive_json(reader, "status", observed)
        assert_message_envelope(first_status, "status")
        require(
            isinstance(first_status.get("status"), dict)
            and first_status["status"].get("safe_state") is None,
            "initial WebSocket status must not claim an integrated safe state",
        )

        send_client_json(connection, {"type": "status"})
        requested_status = receive_json(reader, "status", observed)
        assert_message_envelope(requested_status, "status")
        require(
            requested_status["status"].get("service") == "integration_required",
            "requested WebSocket status hid the integration-required state",
        )

        request_id = "offline-self-test"
        send_client_json(connection, {"type": "ping", "request_id": request_id})
        pong = receive_json(reader, "pong", observed)
        assert_message_envelope(pong, "pong")
        require(pong.get("request_id") == request_id, "pong did not echo request_id")

        send_client_json(
            connection,
            {"type": "set_output", "enabled": True, "value": 1.0},
        )
        error = receive_json(reader, "error", observed)
        assert_message_envelope(error, "error")
        error_body = error.get("error")
        require(isinstance(error_body, dict), "unknown mutation lacks error object")
        require(
            error_body.get("code") == "unknown_message",
            f"unknown mutation returned unexpected error: {error_body!r}",
        )

        epochs = {message.get("service_epoch") for message in observed}
        require(len(epochs) == 1, f"service_epoch changed within one connection: {epochs!r}")
        sequences = [message.get("sequence") for message in observed]
        require(
            all(isinstance(value, int) for value in sequences),
            "one or more messages lacks an integer sequence",
        )
        require(
            all(left < right for left, right in zip(sequences, sequences[1:])),
            f"message sequences are not strictly increasing: {sequences!r}",
        )

        final_http_code, final_http = direct_http_json(port, "GET", "/status")
        require(final_http_code == 200, "final GET /status failed")
        require(
            final_http.get("status", {}).get("safe_state") is None,
            "unknown mutation changed the unknown scaffold safe state",
        )

        # RFC 6455 requires every client frame to be masked. The server must
        # close a connection that sends an unmasked text frame.
        connection.sendall(unmasked_client_frame(0x1, b'{"type":"status"}'))
        opcode, close_payload = read_server_frame(reader)
        require(opcode == 0x8, "unmasked client frame did not trigger close")
        require(
            len(close_payload) >= 2
            and struct.unpack("!H", close_payload[:2])[0] == 1002,
            "unmasked client frame did not receive protocol-error close code 1002",
        )
        connection.close()
        connection = None

        # A reconnect within the same process keeps the service epoch but starts
        # a fresh connection-scoped sequence.
        connection, reconnect_reader = websocket_handshake(port)
        reconnect_observed: list[dict] = []
        reconnect_hello = receive_json(
            reconnect_reader,
            "hello",
            reconnect_observed,
        )
        assert_message_envelope(reconnect_hello, "hello")
        require(
            reconnect_hello.get("service_epoch") == hello.get("service_epoch"),
            "service epoch changed without a process restart",
        )
        require(
            reconnect_hello.get("sequence") == 1,
            "reconnected stream did not reset its connection-scoped sequence",
        )
        connection.sendall(masked_client_frame(0x8, struct.pack("!H", 1000)))
        connection.close()
        connection = None

        # Keep the bounded server from growing one thread per untrusted client.
        # Eight upgraded clients occupy every advertised slot; the ninth must
        # be rejected before the RFC 6455 upgrade.
        capacity_connections: list[socket.socket] = []
        try:
            for _index in range(8):
                capacity_connection, capacity_reader = websocket_handshake(port)
                capacity_connections.append(capacity_connection)
                capacity_hello = receive_json(capacity_reader, "hello", [])
                require(
                    capacity_hello.get("max_clients") == 8,
                    "hello advertised the wrong WebSocket client cap",
                )
            capacity_status = websocket_upgrade_status(port)
            require(
                capacity_status.startswith("HTTP/1.1 503 "),
                f"ninth WebSocket client was not rejected: {capacity_status!r}",
            )
        finally:
            for capacity_connection in capacity_connections:
                try:
                    capacity_connection.close()
                except OSError:
                    pass
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=3.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=3.0)


def verify_static_inputs() -> None:
    require(SCAFFOLD_SCRIPT.is_file(), f"missing scaffold script: {SCAFFOLD_SCRIPT}")
    require(VALIDATOR_SCRIPT.is_file(), f"missing validator script: {VALIDATOR_SCRIPT}")
    require(PACKAGER_SCRIPT.is_file(), f"missing packager script: {PACKAGER_SCRIPT}")
    require(
        PACKAGER_TEST_SCRIPT.is_file(),
        f"missing packager tests: {PACKAGER_TEST_SCRIPT}",
    )
    require(
        VALIDATOR_TEST_SCRIPT.is_file(),
        f"missing validator tests: {VALIDATOR_TEST_SCRIPT}",
    )
    require(PROJECT_SCRIPT.is_file(), f"missing project script: {PROJECT_SCRIPT}")
    require(SKILL_ROOT.resolve().is_dir(), f"missing skill root: {SKILL_ROOT}")
    require(LOOPBACK == "127.0.0.1", "loopback safety constant changed")


def run_self_test() -> int:
    reporter = Reporter()
    temp_root: Path | None = None
    generated: dict[tuple[str, str], tuple[Path, int | None]] = {}
    node = shutil.which("node")

    try:
        verify_static_inputs()
        reporter.pass_check("preflight", "local scripts and loopback guard are present")
        temp_root = make_temp_root()
        reporter.pass_check("temporary-root", str(temp_root))

        for os_release in OS_RELEASES:
            for backend in BACKENDS:
                label = f"{os_release}/{backend}"
                port = reserve_loopback_port() if backend in SERVICE_BACKENDS else None
                try:
                    app_path, _payload = scaffold_app(
                        temp_root,
                        os_release,
                        backend,
                        port,
                    )
                    generated[(os_release, backend)] = (app_path, port)
                    reporter.pass_check(f"scaffold {label}", app_path.name)
                except BaseException as exc:
                    reporter.fail_check(f"scaffold {label}", exc)
                    continue

                try:
                    verify_red_pitaya_theme(app_path)
                    reporter.pass_check(
                        f"Red Pitaya theme {label}",
                        "company palette, brand shell, and functional DOM contract",
                    )
                except BaseException as exc:
                    reporter.fail_check(f"Red Pitaya theme {label}", exc)

                try:
                    payload = validate_app(app_path, os_release, backend)
                    warning_count = len(payload.get("warnings", []))
                    reporter.pass_check(
                        f"validate {label}",
                        f"{warning_count} expected build-evidence warning(s)",
                    )
                except BaseException as exc:
                    reporter.fail_check(f"validate {label}", exc)

                try:
                    compiled = compile_generated_python(app_path)
                    reporter.pass_check(
                        f"python-compile {label}",
                        f"{compiled} generated source file(s)",
                    )
                except BaseException as exc:
                    reporter.fail_check(f"python-compile {label}", exc)

                if node is None:
                    reporter.skip_check(
                        f"node-check {label}",
                        "Node.js is unavailable; generated JavaScript was not parsed",
                    )
                else:
                    try:
                        checked = check_generated_javascript(app_path, node)
                        reporter.pass_check(
                            f"node-check {label}",
                            f"{checked} generated source file(s)",
                        )
                    except BaseException as exc:
                        reporter.fail_check(f"node-check {label}", exc)

        force_key = ("3.00-57", "static")
        if force_key not in generated:
            reporter.fail_check(
                "recoverable --force backup",
                "OS 3.00-57 static scaffold was unavailable",
            )
        else:
            try:
                app_path, port = generated[force_key]
                _replacement, backup = exercise_force_backup(temp_root, app_path, port)
                reporter.pass_check(
                    "recoverable --force backup",
                    f"preserved and restored {backup.name}",
                )
            except BaseException as exc:
                reporter.fail_check("recoverable --force backup", exc)

        try:
            generated_project, preserved_contract = exercise_project_first_manifest(
                temp_root
            )
            reporter.pass_check(
                "project-first manifest preservation",
                f"{generated_project.name}; backup {preserved_contract.name}",
            )
        except BaseException as exc:
            reporter.fail_check("project-first manifest preservation", exc)

        try:
            exercise_malicious_metadata_rejection(temp_root)
            reporter.pass_check(
                "malicious metadata rejection",
                "HTML/JSON/systemd/source injection was blocked before file creation",
            )
        except BaseException as exc:
            reporter.fail_check("malicious metadata rejection", exc)

        try:
            validator_tests = subprocess.run(
                [sys.executable, str(VALIDATOR_TEST_SCRIPT)],
                text=True,
                capture_output=True,
                check=False,
                timeout=180,
                cwd=str(SCRIPTS_ROOT),
            )
            require(
                validator_tests.returncode == 0,
                "systemd allowlist validator tests failed:\n"
                + (validator_tests.stdout + validator_tests.stderr)[-6000:],
            )
            match = re.search(r"Ran\s+(\d+)\s+tests?", validator_tests.stderr)
            reporter.pass_check(
                "reviewed optional systemd directive allowlist",
                f"{match.group(1) if match else 'all'} adversarial tests",
            )
        except BaseException as exc:
            reporter.fail_check(
                "reviewed optional systemd directive allowlist",
                exc,
            )

        for os_release in OS_RELEASES:
            for backend in sorted(PYTHON_WEBSOCKET_BACKENDS):
                label = f"loopback WebSocket {os_release}/{backend}"
                item = generated.get((os_release, backend))
                if item is None:
                    reporter.fail_check(label, "generated app was unavailable")
                    continue
                app_path, port = item
                if port is None:
                    reporter.fail_check(label, "generated service port was unavailable")
                    continue
                try:
                    live_test_python_service(app_path, port)
                    reporter.pass_check(
                        label,
                        "HTTP, RFC 6455, status, ping/pong and mutation rejection",
                    )
                except BaseException as exc:
                    reporter.fail_check(label, exc)

        try:
            package_tests = subprocess.run(
                [sys.executable, str(PACKAGER_TEST_SCRIPT)],
                text=True,
                capture_output=True,
                check=False,
                timeout=180,
                cwd=str(SCRIPTS_ROOT),
            )
            require(
                package_tests.returncode == 0,
                "packager adversarial tests failed:\n"
                + (package_tests.stdout + package_tests.stderr)[-6000:],
            )
            match = re.search(r"Ran\s+(\d+)\s+tests?", package_tests.stderr)
            reporter.pass_check(
                "deterministic application packager",
                f"{match.group(1) if match else 'all'} adversarial/reproducibility tests",
            )
        except BaseException as exc:
            reporter.fail_check("deterministic application packager", exc)
    except BaseException as exc:
        reporter.fail_check("self-test harness", exc)
    finally:
        if temp_root is not None:
            try:
                cleanup_temp_root(temp_root)
                reporter.pass_check("temporary-cleanup", "guarded test directory removed")
            except BaseException as exc:
                reporter.fail_check("temporary-cleanup", exc)

    reporter.summary()
    return 1 if reporter.failures else 0


if __name__ == "__main__":
    raise SystemExit(run_self_test())
