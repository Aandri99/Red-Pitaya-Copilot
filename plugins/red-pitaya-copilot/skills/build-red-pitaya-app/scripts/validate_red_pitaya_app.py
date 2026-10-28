#!/usr/bin/env python3
"""Deterministically validate a version-pinned Red Pitaya application folder."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from critical_parameter_review import inspect_contract


BACKENDS = (
    "static",
    "nginx-lua",
    "controllerhf",
    "python-service",
    "cpp-service",
    "hybrid",
)
EXPECTED_ARCHITECTURE = {
    "static": "static",
    "nginx-lua": "lua-api",
    "controllerhf": "native-controller",
    "python-service": "service",
    "cpp-service": "service",
    "hybrid": "hybrid",
}
TEXT_NAMES = {"Makefile"}
TEXT_SUFFIXES = {
    ".html", ".css", ".js", ".json", ".lua", ".conf", ".sh", ".py",
    ".md", ".service", ".cpp", ".cc", ".c", ".hpp", ".h",
}
COMMON_REQUIRED = (
    "redpitaya-project.json",
    "index.html",
    "css/app.css",
    "js/app.js",
    "info/info.json",
    "info/icon.png",
    "info/icon/128.png",
    "info/icon/256.png",
    "info/icon/512.png",
    "fpga.sh",
)
ARCH_REQUIRED = {
    "static": (),
    "nginx-lua": ("nginx.conf", "lua/api.lua"),
    "controllerhf": ("Makefile", "src/main.cpp", "src/controller_contract.h"),
    "python-service": ("nginx.conf", "service/app_service.py"),
    "cpp-service": ("nginx.conf", "service/main.cpp", "service/Makefile"),
    "hybrid": ("nginx.conf", "lua/api.lua", "service/app_service.py"),
}


@dataclass(frozen=True)
class Finding:
    severity: str
    path: str
    message: str


class Report:
    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def error(self, path: str, message: str) -> None:
        self.findings.append(Finding("error", path, message))

    def warning(self, path: str, message: str) -> None:
        self.findings.append(Finding("warning", path, message))

    @property
    def errors(self) -> list[Finding]:
        return [item for item in self.findings if item.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [item for item in self.findings if item.severity == "warning"]


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_shared_validator(report: Report) -> ModuleType | None:
    path = plugin_root() / "scripts" / "validate_redpitaya_project.py"
    if not path.is_file():
        report.error("redpitaya-project.json", f"shared validator missing: {path}")
        return None
    spec = importlib.util.spec_from_file_location("_rp_shared_validator", path)
    if spec is None or spec.loader is None:
        report.error("redpitaya-project.json", "cannot load shared validator")
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def is_text_file(path: Path) -> bool:
    return path.name in TEXT_NAMES or path.suffix.lower() in TEXT_SUFFIXES


def relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def read_text(root: Path, path: Path, report: Report) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        report.error(relative(root, path), "must be valid UTF-8 text")
    except OSError as exc:
        report.error(relative(root, path), f"cannot read file: {exc}")
    return ""


def load_json(root: Path, path: Path, report: Report) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        report.error(relative(root, path), f"invalid JSON: {exc}")
        return {}
    if not isinstance(value, dict):
        report.error(relative(root, path), "JSON root must be an object")
        return {}
    return value


def identify_backend(root: Path, manifest: dict, report: Report) -> str | None:
    app = manifest.get("application")
    if not isinstance(app, dict):
        return None
    architecture = app.get("architecture")
    if architecture == "static":
        return "static"
    if architecture == "lua-api":
        return "nginx-lua"
    if architecture == "native-controller":
        return "controllerhf"
    if architecture == "hybrid":
        return "hybrid"
    if architecture == "service":
        has_python = (root / "service" / "app_service.py").is_file()
        has_cpp = (root / "service" / "main.cpp").is_file()
        if has_python == has_cpp:
            report.error(
                "application.architecture",
                "service requires exactly one of Python or C++ service sources",
            )
            return None
        return "python-service" if has_python else "cpp-service"
    report.error("application.architecture", f"unsupported app architecture: {architecture!r}")
    return None


def validate_manifest(
    root: Path,
    manifest: dict,
    report: Report,
    expected_os: str | None,
    expected_backend: str | None,
) -> str | None:
    module = load_shared_validator(report)
    if module is not None:
        for finding in module.Validator(manifest).validate():
            if finding.severity == "error":
                report.error(f"manifest{finding.path[1:]}", finding.message)
            else:
                report.warning(f"manifest{finding.path[1:]}", finding.message)

    project = manifest.get("project", {})
    target = manifest.get("target", {})
    app = manifest.get("application", {})
    slug = project.get("slug")
    if slug != root.name:
        report.error("manifest.project.slug", "must match the application folder name")
    if app.get("slug") != root.name:
        report.error("manifest.application.slug", "must match the application folder name")
    if expected_os and target.get("os_release") != expected_os:
        report.error(
            "manifest.target.os_release",
            f"expected {expected_os!r}, found {target.get('os_release')!r}",
        )
    backend = identify_backend(root, manifest, report)
    if expected_backend and backend != expected_backend:
        report.error("manifest.application.architecture", f"expected backend {expected_backend!r}")
    if backend and app.get("architecture") != EXPECTED_ARCHITECTURE[backend]:
        report.error(
            "manifest.application.architecture",
            f"{backend} requires {EXPECTED_ARCHITECTURE[backend]!r}",
        )
    return backend


def validate_structure(root: Path, backend: str | None, manifest: dict, report: Report) -> None:
    for item in COMMON_REQUIRED:
        if not (root / item).is_file():
            report.error(item, "required file is missing")
    if backend is None:
        return
    for item in ARCH_REQUIRED[backend]:
        if not (root / item).is_file():
            report.error(item, f"required for {backend}")

    service_name = manifest.get("application", {}).get("service_name")
    unit_files = list((root / "service").glob("*.service")) if (root / "service").is_dir() else []
    if backend in {"python-service", "cpp-service", "hybrid"}:
        if len(unit_files) != 1:
            report.error("service", "service backend requires exactly one .service unit")
        elif unit_files[0].name != service_name:
            report.error(relative(root, unit_files[0]), "unit filename must match manifest service_name")
    elif unit_files:
        report.error("service", "non-service backend must not contain a systemd unit")

    app = manifest.get("application", {})
    controller_binary = app.get("controller_binary")
    service_backends = {"python-service", "cpp-service", "hybrid"}
    launcher_controller = (
        backend in service_backends and controller_binary == "controllerhf.so"
    )

    if (
        backend in service_backends
        and manifest.get("target", {}).get("os_release")
        in {"2.00-35", "2.07-48"}
        and controller_binary is None
    ):
        report.warning(
            "manifest.application.controller_binary",
            "OS 2.00/2.07 service route may work while the stock /bazaar?apps= "
            "launcher omits the tile; add an exact-target passive "
            "controllerhf.so launcher or obtain explicit direct-route-only "
            "acceptance before deployment",
        )

    if launcher_controller:
        for item in (
            "launcher/Makefile",
            "launcher/src/main.cpp",
            "launcher/src/main.h",
        ):
            if not (root / item).is_file():
                report.error(item, "required for a service launcher controller")
    elif backend in service_backends and controller_binary is not None:
        report.error(
            "manifest.application.controller_binary",
            "service launcher controller must be null or 'controllerhf.so'",
        )

    if backend == "static":
        for forbidden in ("nginx.conf", "lua", "service", "src", "controllerhf.so"):
            if (root / forbidden).exists():
                report.error(forbidden, "static backend must not include executable/server components")
    if (
        backend != "controllerhf"
        and not launcher_controller
        and (root / "controllerhf.so").exists()
    ):
        report.error("controllerhf.so", "binary is inconsistent with selected backend")


def collect_text(root: Path, report: Report) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_file() and is_text_file(path):
            result[relative(root, path)] = read_text(root, path, report)
    return result


def validate_placeholders_and_safety(text_files: dict[str, str], report: Report) -> None:
    secret_assignment = re.compile(
        r"(?i)\b(password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*['\"][^'\"]+['\"]"
    )
    unsafe_patterns = (
        (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+/(?:\s|$)"), "broad root deletion"),
        (re.compile(r"mount\s+[^\n]*remount[^\n]*\s/\s"), "broad root remount"),
        (re.compile(r"chmod\s+-R\s+777\b"), "world-writable recursive permissions"),
        (re.compile(r"\bshell\s*=\s*True\b"), "shell execution"),
        (re.compile(r"\bos\.system\s*\("), "os.system execution"),
        (re.compile(r"\bio\.popen\s*\("), "Lua shell execution"),
        (re.compile(r"\bloadstring\s*\("), "dynamic Lua execution"),
    )
    for path, text in text_files.items():
        if "SCAFFOLD_HARDWARE_INTEGRATION_REQUIRED" in text:
            report.warning(
                path,
                "hardware status/safe-stop integration is still the non-ready scaffold",
            )
        if "SCAFFOLD_PRIVILEGE_REVIEW_REQUIRED" in text:
            report.warning(
                path,
                "explicit root/CAP_SYS_RAWIO service policy still requires target-specific review",
            )
        if "SCAFFOLD_AUTHENTICATION_REVIEW_REQUIRED" in text:
            report.warning(
                path,
                "state-changing routes still require a proven target authentication boundary",
            )
        if re.search(r"__[A-Z0-9_]+__", text) or "[TODO:" in text:
            report.error(path, "contains unresolved template placeholder")
        if secret_assignment.search(text):
            report.error(path, "possible hardcoded secret")
        for pattern, message in unsafe_patterns:
            if pattern.search(text):
                report.error(path, message)
        if (
            "Access-Control-Allow-Origin" in text
            and re.search(r"Access-Control-Allow-Origin[^\n]*['\"]\*['\"]", text)
        ):
            report.error(path, "wildcard CORS is forbidden for generated board APIs")


def validate_authentication_policy(
    manifest: dict,
    text_files: dict[str, str],
    report: Report,
) -> None:
    app = manifest.get("application", {})
    if not isinstance(app, dict):
        return
    mode = app.get("authentication", "none")
    if mode not in {"none", "operator-token"}:
        return

    runtime_files = {
        path: text
        for path, text in text_files.items()
        if path == "index.html"
        or path == "nginx.conf"
        or path.startswith(("js/", "lua/", "service/", "src/", "launcher/src/"))
    }
    token_markers = (
        re.compile(r"(?i)\boperator[\s_-]*token\b"),
        re.compile(r"(?i)\bauthorization\b[^\n]{0,120}\bbearer\b"),
        re.compile(r"(?i)\bbearer\s+(?:\$\{|['\"]|\+)"),
        re.compile(r"(?i)\btoken(?:Available|Required|Missing)\b"),
        re.compile(r"(?i)\b(?:paste|enter|provide)\b[^\n]{0,80}\btoken\b"),
    )
    matched_paths = sorted(
        path
        for path, text in runtime_files.items()
        if any(pattern.search(text) for pattern in token_markers)
    )

    if mode == "none":
        for path in matched_paths:
            report.error(
                path,
                "application.authentication is 'none' but runtime code requests "
                "or enforces an application token; remove the token gate or "
                "explicitly opt in with 'operator-token'",
            )
        return

    if not matched_paths:
        report.warning(
            "manifest.application.authentication",
            "'operator-token' is selected but no recognizable runtime token "
            "boundary was found; implement and test token transport, rotation, "
            "scope, restart behavior and failure messages before deployment",
        )


def validate_metadata(root: Path, manifest: dict, report: Report) -> None:
    path = root / "info" / "info.json"
    if not path.is_file():
        return
    info = load_json(root, path, report)
    project = manifest.get("project", {})
    expected = {
        "name": project.get("title"),
        "version": project.get("version"),
        "description": project.get("description"),
    }
    for key, value in expected.items():
        if info.get(key) != value:
            report.error(f"info/info.json:{key}", "must match redpitaya-project.json")
    revision = info.get("revision")
    if not isinstance(revision, str) or not revision.strip():
        report.error("info/info.json:revision", "must be a non-empty string")


def png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        header = path.read_bytes()[:24]
    except OSError:
        return None
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", header[16:24])


def validate_icons(root: Path, report: Report) -> None:
    expected = {
        "info/icon.png": (128, 128),
        "info/icon/128.png": (128, 128),
        "info/icon/256.png": (256, 256),
        "info/icon/512.png": (512, 512),
    }
    for item, size in expected.items():
        path = root / item
        if path.is_file() and png_dimensions(path) != size:
            report.error(item, f"must be a valid {size[0]}x{size[1]} PNG")


def manifest_actions(manifest: dict) -> set[str]:
    actions: set[str] = set()
    routes = manifest.get("application", {}).get("routes", [])
    if isinstance(routes, list):
        for route in routes:
            if isinstance(route, str) and "/api/" in route:
                actions.add(route.rsplit("/", 1)[-1])
    return actions


def validate_routes(
    root: Path,
    backend: str | None,
    manifest: dict,
    text_files: dict[str, str],
    report: Report,
) -> None:
    if backend is None:
        return
    actions = manifest_actions(manifest)
    routes_value = manifest.get("application", {}).get("routes", [])
    routes = {
        route for route in routes_value if isinstance(route, str)
    } if isinstance(routes_value, list) else set()
    websocket_route = f"/{root.name}/ws"
    websocket_declared = websocket_route in routes
    if backend in {"nginx-lua", "python-service", "cpp-service", "hybrid"}:
        required_actions = {"status", "version", "stop"}
        if not required_actions.issubset(actions):
            report.error(
                "manifest.application.routes",
                f"API backends require at least {sorted(required_actions)}",
            )
        if len(actions) > 32:
            report.error(
                "manifest.application.routes",
                "API backends may declare at most 32 explicit actions",
            )
        expected_api_routes = {
            f"/{root.name}/api/{action}" for action in actions
        }
        non_api_routes = routes - expected_api_routes - {websocket_route}
        if non_api_routes:
            report.error(
                "manifest.application.routes",
                f"unexpected non-API routes: {sorted(non_api_routes)}",
            )
        nginx = text_files.get("nginx.conf", "")
        match = re.search(r"/api/\(([^)]+)\)", nginx)
        nginx_actions = set(match.group(1).split("|")) if match else set()
        if nginx_actions != actions:
            report.error("nginx.conf", "Nginx action allow-list must exactly match manifest routes")
        if f"^/{root.name}/api/" not in nginx:
            report.error("nginx.conf", "API route prefix must match final app slug")
        if "client_max_body_size" not in nginx:
            report.error("nginx.conf", "must set a bounded client body size")

        javascript = text_files.get("js/app.js", "")
        js_actions = set(re.findall(r'run\("([a-zA-Z0-9_-]+)"', javascript))
        if not {"status", "stop"}.issubset(js_actions):
            report.error("js/app.js", "client must implement status and stop actions")
        if f'apiRoot: "/{root.name}/api"' not in javascript:
            report.error("js/app.js", "client API prefix must match app slug")
        expected_routes = set(expected_api_routes)
        if websocket_declared:
            expected_routes.add(websocket_route)
        if routes != expected_routes:
            report.error(
                "manifest.application.routes",
                f"routes must equal the explicit API/WebSocket allow-list {sorted(expected_routes)}",
            )

    if websocket_declared:
        if backend not in {"python-service", "hybrid"}:
            report.error(
                "manifest.application.routes",
                "the generated WebSocket transport is supported only by "
                "python-service and hybrid",
            )
        nginx = text_files.get("nginx.conf", "")
        websocket_checks = (
            f"location = /{root.name}/ws",
            f"127.0.0.1:",
            "/ws;",
            "proxy_http_version 1.1",
            "proxy_set_header Upgrade $http_upgrade",
            'proxy_set_header Connection "upgrade"',
            "proxy_buffering off",
        )
        for required in websocket_checks:
            if required not in nginx:
                report.error(
                    "nginx.conf",
                    f"declared WebSocket route is missing: {required}",
                )
        source = text_files.get("service/app_service.py", "")
        route_markers = (
            'self.path == "/ws"',
            'action == "ws"',
        )
        if not any(marker in source for marker in route_markers):
            report.error(
                "service/app_service.py",
                "declared WebSocket route lacks an exact /ws dispatch marker",
            )
        for required in (
            "Sec-WebSocket-Accept",
            "WEBSOCKET_MAX_MESSAGE_BYTES",
            "WEBSOCKET_IDLE_TIMEOUT_SECONDS",
            "WEBSOCKET_MAX_MESSAGES_PER_SECOND",
            "ThreadingHTTPServer",
        ):
            if required not in source:
                report.error(
                    "service/app_service.py",
                    f"declared WebSocket route lacks implementation marker: {required}",
                )
        protocol_path = root / "protocols" / "websocket-v1.json"
        if not protocol_path.is_file():
            report.error(
                "protocols/websocket-v1.json",
                "declared WebSocket route requires a versioned protocol contract",
            )
        else:
            protocol = load_json(root, protocol_path, report)
            if protocol.get("protocol_version") != 1:
                report.error(
                    "protocols/websocket-v1.json:protocol_version",
                    "must equal 1",
                )
            if protocol.get("route") != websocket_route:
                report.error(
                    "protocols/websocket-v1.json:route",
                    "must match the manifest WebSocket route",
                )
            limits = protocol.get("limits")
            if not isinstance(limits, dict):
                report.error(
                    "protocols/websocket-v1.json:limits",
                    "must define bounded protocol limits",
                )
            elif (
                limits.get("maximum_message_bytes") != 4096
                or limits.get("slow_client_policy") != "disconnect"
                or limits.get("fragmented_messages") != "reject"
                or limits.get("binary_messages") != "reject"
            ):
                report.error(
                    "protocols/websocket-v1.json:limits",
                    "must match the generated bounded implementation",
                )
        javascript = text_files.get("js/app.js", "")
        for required in (
            "websocketEnabled: true",
            f'websocketPath: "{websocket_route}"',
            "service_epoch",
            "message.sequence",
            "scheduleWebsocketReconnect",
        ):
            if required not in javascript:
                report.error(
                    "js/app.js",
                    f"declared WebSocket route lacks client behavior: {required}",
                )
    else:
        if (root / "protocols" / "websocket-v1.json").exists():
            report.error(
                "protocols/websocket-v1.json",
                "protocol contract exists but no WebSocket route is declared",
            )
        if f"location = /{root.name}/ws" in text_files.get("nginx.conf", ""):
            report.error(
                "nginx.conf",
                "WebSocket route is exposed but absent from the manifest",
            )

    if backend in {"nginx-lua", "hybrid"}:
        lua = text_files.get("lua/api.lua", "")
        for action in actions:
            if f'"{action}"' not in lua:
                report.error("lua/api.lua", f"manifest action is not handled: {action}")
        if "ngx.var.uri" not in lua:
            report.error("lua/api.lua", "must derive action from request URI")
    if backend in {"python-service", "hybrid"}:
        source = text_files.get("service/app_service.py", "")
        for action in actions:
            if f'"/{action}"' not in source:
                report.error("service/app_service.py", f"manifest action is not implemented: {action}")
    if backend == "cpp-service":
        source = text_files.get("service/main.cpp", "")
        for action in actions:
            if f'"/{action}"' not in source:
                report.error("service/main.cpp", f"manifest action is not implemented: {action}")


def python_service_ports(source: str) -> set[int]:
    """Return only the default assigned to argparse's exact ``--port`` option."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    ports: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "--port"
        ):
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "default"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, int)
                and not isinstance(keyword.value.value, bool)
            ):
                ports.add(keyword.value.value)
    return ports


def extract_service_ports(text_files: dict[str, str]) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    patterns = {
        "nginx.conf": r"127\.0\.0\.1:(\d+)",
        "service unit": r"(?:--port\s+)(\d+)",
        "C++ service": r"int port = (\d+);",
    }
    sources = {
        "nginx.conf": text_files.get("nginx.conf", ""),
        "service unit": "\n".join(
            value for path, value in text_files.items() if path.endswith(".service")
        ),
        "C++ service": text_files.get("service/main.cpp", ""),
    }
    for name, pattern in patterns.items():
        values = {int(match) for match in re.findall(pattern, sources[name])}
        if values:
            result[name] = values
    python_ports = python_service_ports(
        text_files.get("service/app_service.py", "")
    )
    if python_ports:
        result["Python service"] = python_ports
    return result


def parse_systemd_unit(
    path: str,
    text: str,
    report: Report,
) -> dict[str, dict[str, str]]:
    """Parse the deliberately small generated unit format and reject ambiguity."""
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.endswith("\\"):
            report.error(path, f"line {line_number}: continuations are not allowed")
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            if section in sections:
                report.error(path, f"line {line_number}: duplicate [{section}] section")
            current = sections.setdefault(section, {})
            continue
        if current is None or "=" not in line:
            report.error(path, f"line {line_number}: malformed unit directive")
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in current:
            report.error(path, f"line {line_number}: empty or duplicate directive {key!r}")
            continue
        current[key] = value
    return sections


def validate_service(
    root: Path,
    backend: str | None,
    manifest: dict,
    text_files: dict[str, str],
    report: Report,
) -> None:
    if backend not in {"python-service", "cpp-service", "hybrid"}:
        return
    unit_items = [(path, text) for path, text in text_files.items() if path.endswith(".service")]
    if len(unit_items) != 1:
        return
    unit_path, unit = unit_items[0]
    parsed_unit = parse_systemd_unit(unit_path, unit, report)
    slug = manifest.get("project", {}).get("slug")
    title = manifest.get("project", {}).get("title")
    service_port = manifest.get("application", {}).get("service_port")
    if backend in {"python-service", "hybrid"}:
        expected_exec = (
            f"/usr/bin/python3 /opt/redpitaya/www/apps/{slug}/service/"
            f"app_service.py --host 127.0.0.1 --port {service_port}"
        )
    else:
        expected_exec = (
            f"/opt/redpitaya/www/apps/{slug}/service/build/{slug}-service "
            f"--port {service_port}"
        )
    required_unit = {
        "Unit": {
            "Description": f"Red Pitaya {title} service",
            "After": "network.target",
        },
        "Service": {
            "Type": "simple",
            "User": "root",
            "Group": "root",
            "WorkingDirectory": f"/opt/redpitaya/www/apps/{slug}/service",
            "ExecStart": expected_exec,
            "Restart": "on-failure",
            "RestartSec": "2",
            "UMask": "0077",
            "NoNewPrivileges": "true",
            "PrivateTmp": "true",
            "ProtectSystem": "strict",
            "ProtectHome": "true",
            "ProtectKernelTunables": "true",
            "ProtectKernelModules": "true",
            "ProtectControlGroups": "true",
            "CapabilityBoundingSet": "CAP_SYS_RAWIO",
            "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
            "RuntimeDirectory": f"redpitaya-{slug}",
            "StateDirectory": f"redpitaya-{slug}",
        },
        "Install": {
            "WantedBy": "multi-user.target",
        },
    }
    optional_service_directives = {
        "AmbientCapabilities": "CAP_SYS_RAWIO",
        "RuntimeDirectoryMode": "0700",
        "StateDirectoryMode": "0700",
        "ReadWritePaths": "/run/lock",
    }
    unit_mismatch = set(parsed_unit) != set(required_unit)
    if not unit_mismatch:
        for section_name, required_values in required_unit.items():
            actual_values = parsed_unit.get(section_name, {})
            allowed_values = dict(required_values)
            if section_name == "Service":
                allowed_values.update(optional_service_directives)
            if any(
                actual_values.get(key) != value
                for key, value in required_values.items()
            ):
                unit_mismatch = True
                break
            if set(actual_values) - set(allowed_values):
                unit_mismatch = True
                break
            if any(
                key in actual_values and actual_values[key] != value
                for key, value in allowed_values.items()
            ):
                unit_mismatch = True
                break
    if unit_mismatch:
        report.error(
            unit_path,
            "systemd unit must match the generated required directives, exact "
            "ExecStart and reviewed optional hardening allow-list",
        )

    ports = extract_service_ports(text_files)
    all_ports = {port for values in ports.values() for port in values}
    if len(all_ports) != 1:
        report.error("service", f"service port must match across source/unit/Nginx: {ports}")
    elif not 1024 <= next(iter(all_ports)) <= 65535:
        report.error("service", "service port must be between 1024 and 65535")
    else:
        manifest_port = manifest.get("application", {}).get("service_port")
        actual_port = next(iter(all_ports))
        if manifest_port != actual_port:
            report.error(
                "manifest.application.service_port",
                f"must equal the generated service port {actual_port}",
            )
    nginx = text_files.get("nginx.conf", "")
    if "127.0.0.1:" not in nginx:
        report.error("nginx.conf", "service upstream must bind/proxy through loopback")

    if backend in {"python-service", "hybrid"}:
        path = root / "service" / "app_service.py"
        if path.is_file():
            source = text_files.get("service/app_service.py", "")
            try:
                ast.parse(source, filename=str(path))
            except SyntaxError as exc:
                report.error("service/app_service.py", f"Python syntax error: {exc}")
            if 'args.host != "127.0.0.1"' not in source:
                report.error("service/app_service.py", "must reject non-loopback bind addresses")
            if "SIGTERM" not in source or "safe_state" not in source:
                report.error("service/app_service.py", "must implement SIGTERM and safe-state handling")
            if "origin_allowed_for_mutation" not in source:
                report.error(
                    "service/app_service.py",
                    "must enforce a same-origin policy for browser mutations",
                )
            if "Content-Type" not in source or re.search(
                r'X-[A-Za-z0-9-]+-Request',
                source,
            ) is None:
                report.error(
                    "service/app_service.py",
                    "must require JSON plus a custom non-simple mutation header",
                )
            if "self.close_connection = True" not in source:
                report.error(
                    "service/app_service.py",
                    "must close rejected requests whose body was not consumed",
                )
    if backend == "cpp-service":
        binary = root / "service" / "build" / f"{root.name}-service"
        if not binary.is_file():
            report.warning(
                relative(root, binary),
                "not built; compile against the exact target OS/sysroot before deployment",
            )


def validate_native(
    root: Path,
    backend: str | None,
    manifest: dict,
    text_files: dict[str, str],
    report: Report,
) -> None:
    launcher_controller = (
        backend in {"python-service", "cpp-service", "hybrid"}
        and manifest.get("application", {}).get("controller_binary")
        == "controllerhf.so"
    )
    if backend != "controllerhf" and not launcher_controller:
        return
    source_path = "launcher/src/main.cpp" if launcher_controller else "src/main.cpp"
    source = text_files.get(source_path, "")
    exports = (
        (
            "rp_app_desc", "rp_app_init", "rp_app_exit", "UpdateSignals",
            "UpdateParams", "UpdateBinarySignals", "OnNewParams",
            "OnNewSignals", "PostUpdateSignals", "PostUpdateBinarySignals",
        )
        if launcher_controller
        else (
            "rp_app_desc", "rp_app_init", "rp_app_exit", "rp_set_params",
            "rp_get_params", "rp_get_signals", "UpdateSignals",
            "UpdateParams", "OnNewParams", "OnNewSignals",
            "PostUpdateSignals",
        )
    )
    for symbol in exports:
        if re.search(rf"\b{re.escape(symbol)}\s*\(", source) is None:
            report.error(source_path, f"missing controller export: {symbol}")

    binary = root / "controllerhf.so"
    if not binary.is_file():
        report.warning(
            "controllerhf.so",
            "not built; build against the exact target OS/sysroot and reconcile canonical ABI",
        )
        return
    try:
        header = binary.read_bytes()[:20]
    except OSError as exc:
        report.error("controllerhf.so", f"cannot read ELF header: {exc}")
        return
    if len(header) < 20 or header[:4] != b"\x7fELF":
        report.error("controllerhf.so", "must be an ELF binary")
        return
    if header[4] != 1 or header[5] != 1:
        report.error("controllerhf.so", "Zynq target requires ELF32 little-endian")
    machine = struct.unpack("<H", header[18:20])[0]
    if machine != 40:
        report.error("controllerhf.so", f"expected EM_ARM (40), found e_machine {machine}")


def validate_fpga_noop(
    root: Path,
    manifest: dict,
    text_files: dict[str, str],
    report: Report,
) -> None:
    fpga = manifest.get("fpga")
    if isinstance(fpga, dict) and fpga.get("enabled") is True:
        return
    if (root / "fpga.conf").exists():
        report.error(
            "fpga.conf",
            "app-only package must omit fpga.conf; stock launchers treat "
            "literal 'none' as a bitstream path and reject the app",
        )
    script = text_files.get("fpga.sh", "")
    if "exit 0" not in script:
        report.error("fpga.sh", "app-only scaffold must be an explicit no-op")


def validate_critical_parameters(
    root: Path,
    manifest: dict,
    text_files: dict[str, str],
    report: Report,
) -> None:
    actions = manifest_actions(manifest)
    contract_path = root / "protocols" / "critical-parameters.json"
    if "configure" in actions and not contract_path.is_file():
        report.error(
            "protocols/critical-parameters.json",
            "configure route requires a versioned critical-parameter review contract",
        )
        return
    if not contract_path.is_file():
        return
    result = inspect_contract(root)
    for message in result.errors:
        report.error("protocols/critical-parameters.json", message)
    if "configure" not in actions:
        return
    if "parameters" not in actions:
        report.error(
            "manifest.application.routes",
            "configure route requires an explicit read-only parameters route",
        )
    javascript = text_files.get("js/app.js", "")
    for marker in ('run("parameters")', "reviewConfiguration", "reviewedSignature"):
        if marker not in javascript:
            report.error(
                "js/app.js",
                f"critical-parameter review lacks client behavior: {marker}",
            )
    index = text_files.get("index.html", "")
    for marker in ("parameterReview", "Review configuration", "Confirm and apply"):
        if marker not in index:
            report.error(
                "index.html",
                f"critical-parameter review lacks visible UI marker: {marker}",
            )


def validate(root: Path, expected_os: str | None, expected_backend: str | None) -> Report:
    report = Report()
    if not root.is_dir():
        report.error(str(root), "application folder not found")
        return report

    manifest_path = root / "redpitaya-project.json"
    manifest = load_json(root, manifest_path, report) if manifest_path.is_file() else {}
    backend = validate_manifest(root, manifest, report, expected_os, expected_backend)
    validate_structure(root, backend, manifest, report)
    text_files = collect_text(root, report)
    validate_placeholders_and_safety(text_files, report)
    validate_authentication_policy(manifest, text_files, report)
    validate_metadata(root, manifest, report)
    validate_icons(root, report)
    validate_routes(root, backend, manifest, text_files, report)
    validate_service(root, backend, manifest, text_files, report)
    validate_native(root, backend, manifest, text_files, report)
    validate_fpga_noop(root, manifest, text_files, report)
    validate_critical_parameters(root, manifest, text_files, report)

    index = text_files.get("index.html", "")
    if "css/app.css?v=" not in index or "js/app.js?v=" not in index:
        report.error("index.html", "CSS and JavaScript must use version cache-busters")
    if "innerHTML" in text_files.get("js/app.js", ""):
        report.warning("js/app.js", "review innerHTML use for injection risk")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app_folder")
    parser.add_argument(
        "--os-release",
        "--target-os",
        dest="os_release",
        choices=("2.00-35", "2.07-48", "3.00-57"),
    )
    parser.add_argument("--backend", choices=BACKENDS)
    parser.add_argument("--strict-warnings", action="store_true")
    parser.add_argument(
        "--deployment-ready",
        action="store_true",
        help="fail on every warning; use only after exact target evidence exists",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.app_folder).expanduser().resolve()
    report = validate(root, args.os_release, args.backend)
    warnings_are_errors = args.strict_warnings or args.deployment_ready
    payload = {
        "ok": not report.errors and not (warnings_are_errors and report.warnings),
        "app": str(root),
        "errors": [item.__dict__ for item in report.errors],
        "warnings": [item.__dict__ for item in report.warnings],
        "strict_warnings": args.strict_warnings,
        "deployment_ready": args.deployment_ready,
    }
    if args.as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Red Pitaya app: {root}")
        for finding in report.findings:
            print(f"{finding.severity.upper()}: {finding.path}: {finding.message}")
        print(
            f"Summary: {len(report.errors)} error(s), "
            f"{len(report.warnings)} warning(s)"
        )
        print(f"Result: {'PASS' if payload['ok'] else 'FAIL'}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
