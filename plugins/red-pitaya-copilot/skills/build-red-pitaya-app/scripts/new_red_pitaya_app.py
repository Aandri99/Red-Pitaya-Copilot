#!/usr/bin/env python3
"""Create a version-pinned Red Pitaya application from bundled templates."""

from __future__ import annotations

import argparse
import binascii
import json
import math
import re
import shutil
import struct
import sys
import time
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path


BACKENDS = (
    "static",
    "nginx-lua",
    "controllerhf",
    "python-service",
    "cpp-service",
    "hybrid",
)
SERVICE_BACKENDS = {"python-service", "cpp-service", "hybrid"}
BACKEND_ARCHITECTURES = {
    "static": "static",
    "nginx-lua": "lua-api",
    "controllerhf": "native-controller",
    "python-service": "service",
    "cpp-service": "service",
    "hybrid": "hybrid",
}
BACKEND_OVERLAYS = {
    "static": (),
    "nginx-lua": ("nginx-lua",),
    "controllerhf": ("controllerhf",),
    "python-service": ("python-service",),
    "cpp-service": ("cpp-service",),
    "hybrid": ("python-service", "hybrid"),
}
TEXT_NAMES = {"Makefile"}
TEXT_SUFFIXES = {
    ".html", ".css", ".js", ".json", ".lua", ".conf", ".sh",
    ".py", ".service", ".cpp", ".cc", ".c", ".hpp", ".h",
}
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
ROLE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
HUMAN_TEXT_PUNCTUATION = frozenset(" .,:;_-/()+[]'")


def rename_with_transient_retry(source: Path, destination: Path) -> None:
    """Rename once atomically, tolerating only short-lived Windows file locks."""
    delays = (0.0, 0.025, 0.05, 0.1, 0.2, 0.4)
    last_error: OSError | None = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            source.rename(destination)
            return
        except PermissionError as exc:
            last_error = exc
            if destination.exists() or not source.exists():
                raise
    assert last_error is not None
    raise last_error


def websocket_nginx_block(slug: str, service_port: int | None) -> str:
    """Return the exact bounded WebSocket proxy or an empty string."""
    if service_port is None:
        raise ValueError("WebSocket proxy requires an explicit service port")
    return f"""location = /{slug}/ws {{
    proxy_pass http://127.0.0.1:{service_port}/ws;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $http_host;
    proxy_connect_timeout 1s;
    proxy_read_timeout 35s;
    proxy_send_timeout 10s;
    proxy_buffering off;
}}"""


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_shared_contract() -> tuple[dict, dict[str, dict], dict]:
    root = plugin_root()
    schema_path = root / "schemas" / "redpitaya-project.schema.json"
    models_path = root / "profiles" / "board-models.json"
    if not schema_path.is_file() or not models_path.is_file():
        raise FileNotFoundError("shared schema/profiles are missing from the plugin")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    models_doc = json.loads(models_path.read_text(encoding="utf-8"))
    models = models_doc.get("models")
    if not isinstance(models, dict) or not models:
        raise ValueError("board-models.json has no models")
    profiles: dict[str, dict] = {}
    for release in ("2.00-35", "2.07-48", "3.00-57"):
        path = root / "profiles" / f"os-{release}.json"
        profile = json.loads(path.read_text(encoding="utf-8"))
        if profile.get("os_release") != release:
            raise ValueError(f"profile release mismatch: {path}")
        profiles[release] = profile
    return schema, profiles, models_doc


def validate_human_text(
    value: str,
    label: str,
    maximum: int,
    *,
    allow_empty: bool = False,
) -> None:
    """Accept display text that is safe in every bundled template context."""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    if (not allow_empty and not value) or len(value) > maximum:
        minimum = 0 if allow_empty else 1
        raise ValueError(f"{label} must contain {minimum}-{maximum} characters")
    invalid = sorted(
        {
            character
            for character in value
            if not (character.isalnum() or character in HUMAN_TEXT_PUNCTUATION)
        }
    )
    if invalid:
        rendered = " ".join(f"U+{ord(character):04X}" for character in invalid)
        raise ValueError(
            f"{label} contains characters unsafe for the generated HTML, JSON, "
            f"systemd and source templates: {rendered}"
        )


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)
    )


def write_wave_icon(path: Path, size: int) -> None:
    """Write a dependency-free RGBA PNG with an instrument waveform."""
    background = (0, 0, 0, 255)
    grid = (42, 42, 42, 255)
    red = (231, 45, 47, 255)
    light_gray = (235, 235, 236, 255)
    pixels = [[background for _ in range(size)] for _ in range(size)]

    for index in range(size):
        if index % max(1, size // 8) == 0:
            for other in range(size):
                pixels[index][other] = grid
                pixels[other][index] = grid

    mid = size // 2
    amplitude = size * 0.22
    thickness = max(1, size // 64)
    for x in range(size):
        phase = (x / max(1, size - 1)) * math.pi * 4
        envelope = 0.25 + 0.75 * math.exp(-((x - size * 0.50) / (size * 0.24)) ** 2)
        y = round(mid - math.sin(phase) * amplitude * envelope)
        for delta in range(-thickness, thickness + 1):
            if 0 <= y + delta < size:
                pixels[y + delta][x] = red

    border = max(2, size // 32)
    for y in range(size):
        for x in range(size):
            if x < border or y < border or x >= size - border or y >= size - border:
                pixels[y][x] = light_gray

    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for red, green, blue, alpha in row:
            raw.extend((red, green, blue, alpha))
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(bytes(raw), level=9))
        + png_chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def is_text_template(path: Path) -> bool:
    return path.name in TEXT_NAMES or path.suffix.lower() in TEXT_SUFFIXES


def replace_tokens(root: Path, values: dict[str, str]) -> None:
    for path in root.rglob("*"):
        if not path.is_file() or not is_text_template(path):
            continue
        text = path.read_text(encoding="utf-8")
        for token, value in values.items():
            text = text.replace(token, value)
        path.write_text(text, encoding="utf-8", newline="\n")


def build_manifest(
    args: argparse.Namespace,
    profile: dict,
    existing: dict | None = None,
) -> dict:
    architecture = BACKEND_ARCHITECTURES[args.backend]
    has_http_api = args.backend in {"nginx-lua", *SERVICE_BACKENDS}
    routes = [f"/{args.slug}/"]
    if has_http_api:
        routes = [
            f"/{args.slug}/api/status",
            f"/{args.slug}/api/version",
            f"/{args.slug}/api/stop",
        ]
    if args.websocket:
        routes.append(f"/{args.slug}/ws")
    service_name = f"{args.slug}.service" if args.backend in SERVICE_BACKENDS else None
    python_abi = (
        str(profile["python_version"])
        if args.backend in {"python-service", "hybrid"}
        else "none"
    )
    controller_binary = "controllerhf.so" if args.backend == "controllerhf" else None
    if has_http_api:
        stop_action = f"POST /{args.slug}/api/stop"
    elif args.backend == "controllerhf":
        stop_action = "native controller shutdown callback must confirm safe output"
    else:
        stop_action = "no hardware action; static application"

    target = {
        "os_release": args.os_release,
        "model": args.model,
        "role": args.role,
    }
    if args.hostname:
        target["hostname"] = args.hostname

    generated = {
        "schema_version": 1,
        "project": {
            "slug": args.slug,
            "title": args.title,
            "version": args.version,
            "description": args.description,
        },
        "target": target,
        "application": {
            "enabled": True,
            "architecture": architecture,
            "authentication": args.authentication,
            "slug": args.slug,
            "routes": routes,
            "controller_binary": controller_binary,
            "service_name": service_name,
            "python_abi": python_abi,
            "service_port": (
                args.service_port if args.backend in SERVICE_BACKENDS else None
            ),
        },
        "fpga": {
            "enabled": False,
            "project": "none",
            "artifact_name": None,
            "device_tree_name": None,
            "loader": "none",
            "register_contract": None,
            "version_register": None,
            "magic_value": None,
            "clock_hz": None,
        },
        "deployment": {
            "app_root": "/opt/redpitaya/www/apps",
            "mountpoint": "/opt/redpitaya",
            "staging_root": "/tmp/redpitaya-deploy/{deployment_id}",
            "backup_root": (
                f"/root/redpitaya-backups/{args.slug}/{{deployment_id}}"
            ),
            "nginx_service": "redpitaya_nginx.service",
            "restore_read_only": True,
            "allow_live_output_test": False,
        },
        "safety": {
            "output_default": "disabled",
            "stop_action": stop_action,
            "physical_test_requires_authorization": True,
            "rollback_required": True,
            "maximum_output_volts": None,
        },
    }
    if existing is None:
        return generated

    preserved = json.loads(json.dumps(existing))
    required_sections = {
        "schema_version",
        "project",
        "target",
        "application",
        "fpga",
        "deployment",
        "safety",
    }
    if set(preserved) - required_sections:
        raise ValueError("existing manifest contains unsupported top-level fields")
    if not required_sections.issubset(preserved):
        raise ValueError("existing manifest is missing one or more required sections")
    expected_stop = generated["safety"]["stop_action"]
    if preserved["safety"].get("stop_action") != expected_stop:
        raise ValueError(
            "existing safety.stop_action does not match the selected backend; "
            f"update and validate it explicitly as {expected_stop!r}"
        )
    preserved["application"] = generated["application"]
    return preserved


def build_critical_parameter_contract(manifest: dict) -> dict:
    """Describe the generated platform/safety settings before app extension."""
    project = manifest["project"]
    target = manifest["target"]
    application = manifest["application"]
    fpga = manifest["fpga"]
    safety = manifest["safety"]

    def fixed(
        identifier: str,
        label: str,
        category: str,
        data_type: str,
        value: object,
        safety_class: str,
        explanation: str,
        change_effect: str,
    ) -> dict:
        return {
            "id": identifier,
            "label": label,
            "category": category,
            "editable": False,
            "data_type": data_type,
            "units": None,
            "value": value,
            "safety_class": safety_class,
            "explanation": explanation,
            "change_effect": change_effect,
        }

    parameters = [
        fixed(
            "target_os_release",
            "Target OS release",
            "deployment",
            "string",
            target["os_release"],
            "redeploy_required",
            "Pins loader, service, native ABI, and web-stack expectations to one exact Red Pitaya OS release.",
            "Changing it requires a new target probe, compatibility review, package, and deployment plan.",
        ),
        fixed(
            "target_model",
            "Target board model",
            "hardware_contract",
            "string",
            target["model"],
            "rebuild_required",
            "Pins FPGA part and fast-I/O assumptions to the selected board profile.",
            "Changing it requires board identification and FPGA/application revalidation.",
        ),
        fixed(
            "application_architecture",
            "Application architecture",
            "application",
            "string",
            application["architecture"],
            "redeploy_required",
            "Defines whether browser, Nginx/Lua, native controller, or a supervised service owns behavior.",
            "Changing it requires lifecycle, route, service, and package review.",
        ),
        fixed(
            "authentication_mode",
            "Authentication mode",
            "security",
            "string",
            application["authentication"],
            "redeploy_required",
            "Declares the application-level access policy independently of deployment authorization.",
            "Changing it requires transport, storage, failure-mode, and access-control tests.",
        ),
        fixed(
            "output_default",
            "Output default",
            "safety",
            "string",
            safety["output_default"],
            "rebuild_required" if fpga["enabled"] else "redeploy_required",
            "Defines the required output state at reset, startup, failure, stop, and shutdown.",
            "Changing it requires a new safe-state design and verification evidence.",
        ),
        fixed(
            "physical_test_requires_authorization",
            "Physical-test authorization",
            "safety",
            "boolean",
            safety["physical_test_requires_authorization"],
            "authorization_required",
            "Keeps physical-output testing separate from ordinary build and deployment work.",
            "Changing it requires an explicit safety-policy decision; generation never grants authorization.",
        ),
        fixed(
            "fpga_enabled",
            "FPGA ownership",
            "hardware_contract",
            "boolean",
            fpga["enabled"],
            "rebuild_required",
            "Declares whether the app package owns and loads a matching FPGA artifact.",
            "Changing it requires the FPGA skill, artifact-family checks, and loader validation.",
        ),
    ]
    if application["service_port"] is not None:
        parameters.append(
            fixed(
                "service_port",
                "Loopback service port",
                "application",
                "integer",
                application["service_port"],
                "redeploy_required",
                "Binds the supervised service only on loopback and must be unused on the exact target.",
                "Changing it requires Nginx, service-unit, manifest, and live port-inventory updates.",
            )
        )
    return {
        "schema_version": 1,
        "instrument": project["slug"],
        "review_required_before_apply": True,
        "parameters": parameters,
        "presets": [],
    }


def load_manifest_mode(
    args: argparse.Namespace,
) -> tuple[Path | None, dict | None]:
    if args.manifest is None:
        required = {
            "--slug": args.slug,
            "--title": args.title,
            "--os-release": args.os_release,
            "--model": args.model,
            "--output": args.output,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(
                "new standalone scaffold is missing " + ", ".join(missing)
            )
        args.description = (
            args.description
            if args.description is not None
            else "Custom Red Pitaya instrument application"
        )
        args.version = args.version or "0.1.0"
        args.role = args.role or "instrument"
        args.authentication = args.authentication or "none"
        return None, None

    if args.output is not None:
        raise ValueError("--output and --manifest are mutually exclusive")
    if args.force:
        raise ValueError("--force is not used with --manifest")
    manifest_path = args.manifest.expanduser().resolve()
    if manifest_path.name != "redpitaya-project.json":
        raise ValueError("--manifest must name redpitaya-project.json")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("--manifest must be a non-symlink regular file")
    try:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read existing manifest: {exc}") from exc
    if not isinstance(existing, dict):
        raise ValueError("existing manifest must be a JSON object")
    project = existing.get("project")
    target = existing.get("target")
    application = existing.get("application")
    if (
        not isinstance(project, dict)
        or not isinstance(target, dict)
        or not isinstance(application, dict)
    ):
        raise ValueError(
            "existing manifest requires project, target and application objects"
        )

    manifest_values = {
        "slug": project.get("slug"),
        "title": project.get("title"),
        "description": project.get("description", ""),
        "version": project.get("version"),
        "os_release": target.get("os_release"),
        "model": target.get("model"),
        "role": target.get("role"),
        "hostname": target.get("hostname"),
    }
    for name, value in manifest_values.items():
        supplied = getattr(args, name)
        if supplied is not None and supplied != value:
            raise ValueError(
                f"--{name.replace('_', '-')} conflicts with the existing manifest"
            )
        setattr(args, name, value)
    existing_authentication = application.get("authentication", "none")
    if args.authentication is not None and (
        args.authentication != existing_authentication
    ):
        raise ValueError(
            "--authentication conflicts with the existing manifest"
        )
    args.authentication = existing_authentication
    if not args.slug or not args.title or not args.os_release or not args.model:
        raise ValueError("existing manifest lacks required project/target identity")

    existing_items = {
        item.resolve()
        for item in manifest_path.parent.iterdir()
    }
    if existing_items != {manifest_path}:
        raise ValueError(
            "--manifest project directory must contain only redpitaya-project.json; "
            "extend an existing application manually instead of scaffolding over it"
        )
    return manifest_path, existing


def validate_args(args: argparse.Namespace, schema: dict, profiles: dict, models_doc: dict) -> None:
    slug_pattern = schema["properties"]["project"]["properties"]["slug"]["pattern"]
    if re.fullmatch(slug_pattern, args.slug) is None:
        raise ValueError(
            "slug must start with lowercase letter, end with lowercase letter/digit, "
            "and contain 3-64 lowercase letters, digits, _ or -"
        )
    if not SEMVER_RE.fullmatch(args.version):
        raise ValueError("version must be semantic versioning, for example 0.1.0")
    if not ROLE_RE.fullmatch(args.role):
        raise ValueError("role must be 2-32 lowercase letters, digits, _ or -")
    validate_human_text(args.title, "title", 100)
    validate_human_text(
        args.description,
        "description",
        500,
        allow_empty=True,
    )
    validate_human_text(args.revision, "revision", 128)
    if args.os_release not in profiles:
        raise ValueError(f"unsupported OS release: {args.os_release}")
    if args.model not in models_doc["models"]:
        raise ValueError(f"unsupported model profile: {args.model}")
    if args.backend in SERVICE_BACKENDS:
        if args.service_port is None:
            raise ValueError(f"--service-port is required for {args.backend}")
        if not 1024 <= args.service_port <= 65535:
            raise ValueError("service port must be between 1024 and 65535")
    elif args.service_port is not None:
        raise ValueError(f"--service-port is not used by {args.backend}")
    if args.websocket and args.backend not in {"python-service", "hybrid"}:
        raise ValueError(
            "--websocket is supported only by python-service and hybrid backends"
        )
    if args.hostname and (
        len(args.hostname) > 253
        or any(character.isspace() for character in args.hostname)
    ):
        raise ValueError("hostname must be a non-empty DNS name or IP without whitespace")


def create_app(args: argparse.Namespace) -> Path:
    schema, profiles, models_doc = load_shared_contract()
    manifest_path, existing_manifest = load_manifest_mode(args)
    validate_args(args, schema, profiles, models_doc)
    profile = profiles[args.os_release]

    skill_root = Path(__file__).resolve().parents[1]
    template_root = skill_root / "assets" / "app-template"
    common = template_root / "common"
    if not common.is_dir():
        raise FileNotFoundError(f"common template not found: {common}")

    if manifest_path is not None:
        destination = manifest_path.parent
        output_root = destination.parent
    else:
        output_root = Path(args.output).expanduser().resolve()
        destination = output_root / args.slug
    backup_path: Path | None = None
    destination_existed = destination.exists()
    if destination.exists():
        if manifest_path is None and not args.force:
            raise FileExistsError(f"destination exists: {destination}; use --force to replace it")
        if destination.is_symlink() or destination.resolve().parent != output_root:
            raise ValueError("refusing to replace a symlink or destination outside output root")
    output_root.mkdir(parents=True, exist_ok=True)
    staging_parent = (
        output_root / f".{args.slug}.scaffold-staging-{uuid.uuid4().hex}"
    )
    staging_destination = staging_parent / args.slug
    staging_parent.mkdir()
    try:
        shutil.copytree(common, staging_destination)

        for overlay_name in BACKEND_OVERLAYS[args.backend]:
            overlay = template_root / "backends" / overlay_name
            if not overlay.is_dir():
                raise FileNotFoundError(f"backend template not found: {overlay}")
            shutil.copytree(overlay, staging_destination, dirs_exist_ok=True)

        service_unit = staging_destination / "service" / "app.service"
        if service_unit.exists():
            service_unit.rename(service_unit.with_name(f"{args.slug}.service"))

        api_enabled = args.backend in {"nginx-lua", *SERVICE_BACKENDS}
        replace_tokens(
            staging_destination,
            {
                "__APP_SLUG__": args.slug,
                "__APP_TITLE__": args.title,
                "__APP_DESCRIPTION__": args.description,
                "__APP_VERSION__": args.version,
                "__APP_REVISION__": args.revision,
                "__BACKEND__": args.backend,
                "__API_ENABLED__": "true" if api_enabled else "false",
                "__WEBSOCKET_ENABLED__": "true" if args.websocket else "false",
                "__WEBSOCKET_NGINX_BLOCK__": (
                    websocket_nginx_block(args.slug, args.service_port)
                    if args.websocket
                    else ""
                ),
                "__OS_RELEASE__": args.os_release,
                "__TARGET_MODEL__": args.model,
                "__SERVICE_PORT__": str(args.service_port or 0),
            },
        )

        manifest = build_manifest(args, profile, existing_manifest)
        (staging_destination / "redpitaya-project.json").write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        parameter_contract_path = (
            staging_destination / "protocols" / "critical-parameters.json"
        )
        parameter_contract_path.parent.mkdir(parents=True, exist_ok=True)
        parameter_contract_path.write_text(
            json.dumps(build_critical_parameter_contract(manifest), indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

        protocol = staging_destination / "protocols" / "websocket-v1.json"
        if not args.websocket and protocol.exists():
            protocol.unlink()
            if protocol.parent.is_dir() and not any(protocol.parent.iterdir()):
                protocol.parent.rmdir()

        icon_dir = staging_destination / "info" / "icon"
        for size in (128, 256, 512):
            write_wave_icon(icon_dir / f"{size}.png", size)
        write_wave_icon(icon_dir.parent / "icon.png", 128)

        for executable in (
            staging_destination / "fpga.sh",
            staging_destination / "service" / "app_service.py",
        ):
            if executable.exists():
                executable.chmod(0o755)

        if destination_existed:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = output_root / f".{args.slug}.backup-{timestamp}"
            if backup.exists():
                raise FileExistsError(f"backup destination already exists: {backup}")
            rename_with_transient_retry(destination, backup)
            backup_path = backup
        try:
            rename_with_transient_retry(staging_destination, destination)
        except Exception:
            if backup_path is not None and not destination.exists():
                rename_with_transient_retry(backup_path, destination)
                backup_path = None
            raise
        staging_parent.rmdir()
    except Exception:
        if (
            staging_parent.exists()
            and not staging_parent.is_symlink()
            and staging_parent.resolve().parent == output_root
            and staging_parent.name.startswith(f".{args.slug}.scaffold-staging-")
        ):
            shutil.rmtree(staging_parent)
        raise

    if backup_path is not None:
        print(
            f"existing destination moved to recoverable backup: {backup_path}",
            file=sys.stderr,
        )

    payload = {
        "ok": True,
        "slug": args.slug,
        "path": str(destination),
        "manifest": str(destination / "redpitaya-project.json"),
        "os_release": args.os_release,
        "model": args.model,
        "backend": args.backend,
        "architecture": BACKEND_ARCHITECTURES[args.backend],
        "authentication": args.authentication,
        "service_port": args.service_port,
        "websocket": args.websocket,
        "backup_path": str(backup_path) if backup_path else None,
        "manifest_mode": manifest_path is not None,
    }
    print(json.dumps(payload, indent=2))
    return destination


def build_parser() -> argparse.ArgumentParser:
    _schema, profiles, models_doc = load_shared_contract()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", help="application folder and URL identifier")
    parser.add_argument("--title", help="human-facing application title")
    parser.add_argument("--description")
    parser.add_argument("--version")
    parser.add_argument("--revision", default="initial")
    parser.add_argument(
        "--os-release", "--target-os", dest="os_release",
        choices=tuple(profiles),
    )
    parser.add_argument("--model", choices=tuple(models_doc["models"]))
    parser.add_argument("--role")
    parser.add_argument("--hostname")
    parser.add_argument("--backend", required=True, choices=BACKENDS)
    parser.add_argument(
        "--authentication",
        choices=("none", "operator-token"),
        default=None,
        help=(
            "application-level access control; defaults to none. "
            "operator-token records an explicit opt-in but still requires "
            "a reviewed implementation before deployment"
        ),
    )
    parser.add_argument(
        "--service-port", type=int,
        help="required reserved loopback port for service/hybrid backends",
    )
    parser.add_argument(
        "--websocket",
        action="store_true",
        help=(
            "add the bounded status-only WebSocket protocol; valid only for "
            "python-service or hybrid"
        ),
    )
    parser.add_argument("--output", help="parent output directory for a standalone scaffold")
    parser.add_argument(
        "--manifest",
        type=Path,
        help=(
            "existing project-first redpitaya-project.json; its directory must "
            "otherwise be empty and all non-application sections are preserved"
        ),
    )
    parser.add_argument(
        "--force", action="store_true",
        help=(
            "move only the exact resolved destination app folder to a timestamped "
            "sibling backup, then create the new scaffold"
        ),
    )
    return parser


def main(argv: list[str]) -> int:
    try:
        create_app(build_parser().parse_args(argv))
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
