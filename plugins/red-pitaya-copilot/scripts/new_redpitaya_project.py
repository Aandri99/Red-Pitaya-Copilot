#!/usr/bin/env python3
"""Create the shared redpitaya-project.json contract without external packages."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


OS_RELEASES = ("2.00-35", "2.07-48", "3.00-57")
MODELS = (
    "z10_125",
    "z10_125_v2",
    "z10_125_pro_v2",
    "z20_122",
    "z20_125",
    "z20_125_4ch",
    "z20_125_ll",
    "z20_125_v2",
    "z20_250",
    "z20_250a",
    "z20_250_1_0",
)
ARCHITECTURES = (
    "static",
    "lua-api",
    "native-controller",
    "service",
    "hybrid",
)
SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{1,62}[a-z0-9]$")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Create a versioned contract shared by all Red Pitaya plugin skills."
    )
    result.add_argument("--slug", required=True)
    result.add_argument("--title", required=True)
    result.add_argument("--os-release", required=True, choices=OS_RELEASES)
    result.add_argument("--model", required=True, choices=MODELS)
    result.add_argument(
        "--components",
        "--kind",
        dest="components",
        choices=("app", "fpga", "app-fpga"),
        default="app",
        help="project components; app-fpga means both and is unrelated to the hybrid app architecture",
    )
    result.add_argument(
        "--app-architecture", choices=ARCHITECTURES, default="lua-api"
    )
    result.add_argument(
        "--authentication",
        choices=("none", "operator-token"),
        default="none",
        help=(
            "application-level access control; defaults to none. "
            "operator-token is an explicit opt-in and does not replace "
            "deployment or physical-output authorization"
        ),
    )
    result.add_argument("--role", default="instrument")
    result.add_argument("--hostname")
    result.add_argument(
        "--service-port",
        type=int,
        help="required for service or hybrid application architectures",
    )
    result.add_argument(
        "--output",
        type=Path,
        required=True,
        help="exact new manifest file, normally <project>/redpitaya-project.json",
    )
    return result


def build_manifest(args: argparse.Namespace) -> dict:
    if not SLUG_RE.fullmatch(args.slug):
        raise ValueError(
            "slug must start with a lowercase letter, end with a lowercase "
            "letter or digit, and contain 3-64 lowercase letters, digits, _ or -"
        )
    if not re.fullmatch(r"^[a-z][a-z0-9_-]{1,31}$", args.role):
        raise ValueError("role must be 2-32 lowercase letters, digits, _ or -")

    app_enabled = args.components in ("app", "app-fpga")
    fpga_enabled = args.components in ("fpga", "app-fpga")
    authentication = getattr(args, "authentication", "none")
    if not app_enabled and authentication != "none":
        raise ValueError(
            "--authentication operator-token requires an enabled application"
        )
    artifact = None
    if fpga_enabled:
        artifact = (
            "fpga.bit.bin"
            if args.os_release in {"2.00-35", "2.07-48"}
            else "fpga.bin"
        )

    architecture = args.app_architecture if app_enabled else "none"
    if architecture in ("service", "hybrid"):
        if args.service_port is None or not 1024 <= args.service_port <= 65535:
            raise ValueError(
                "--service-port 1024..65535 is required for service/hybrid "
                "application architectures"
            )
    elif args.service_port is not None:
        raise ValueError(
            "--service-port is valid only for service/hybrid application "
            "architectures"
        )
    if not app_enabled:
        routes = []
    elif architecture in ("lua-api", "service", "hybrid"):
        routes = [
            f"/{args.slug}/api/status",
            f"/{args.slug}/api/version",
            f"/{args.slug}/api/stop",
        ]
    else:
        routes = [f"/{args.slug}/"]
    python_abi = "none"
    if architecture in ("service", "hybrid"):
        python_abi = (
            "3.10"
            if args.os_release in {"2.00-35", "2.07-48"}
            else "3.12"
        )

    target = {
        "os_release": args.os_release,
        "model": args.model,
        "role": args.role,
    }
    if args.hostname:
        target["hostname"] = args.hostname

    controller_binary = (
        "controllerhf.so" if architecture == "native-controller" else None
    )
    service_name = (
        f"{args.slug}.service" if architecture in ("service", "hybrid") else None
    )

    return {
        "schema_version": 1,
        "project": {
            "slug": args.slug,
            "title": args.title,
            "version": "0.1.0",
            "description": f"{args.title} for Red Pitaya",
        },
        "target": target,
        "application": {
            "enabled": app_enabled,
            "architecture": architecture,
            "authentication": authentication if app_enabled else "none",
            "slug": args.slug,
            "routes": routes,
            "controller_binary": controller_binary,
            "service_name": service_name,
            "python_abi": python_abi,
            "service_port": (
                args.service_port
                if architecture in ("service", "hybrid")
                else None
            ),
        },
        "fpga": {
            "enabled": fpga_enabled,
            "project": args.slug if fpga_enabled else "none",
            "artifact_name": artifact,
            "device_tree_name": "fpga.dtbo" if fpga_enabled else None,
            "loader": "overlay.sh" if fpga_enabled else "none",
            "register_contract": (
                "register-contract.json" if fpga_enabled else None
            ),
            "version_register": "0x40600004" if fpga_enabled else None,
            "magic_value": "0x52504631" if fpga_enabled else None,
            "clock_hz": 125000000 if fpga_enabled else None,
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
            "stop_action": (
                f"POST /{args.slug}/api/stop"
                if architecture in ("lua-api", "service", "hybrid")
                else (
                    "native controller shutdown callback must confirm safe output"
                    if architecture == "native-controller"
                    else (
                        "no hardware action; static application"
                        if architecture == "static"
                        else "clear the documented FPGA output-enable bit"
                    )
                )
            ),
            "physical_test_requires_authorization": True,
            "rollback_required": True,
            "maximum_output_volts": None,
        },
    }


def main() -> int:
    args = parser().parse_args()
    try:
        manifest = build_manifest(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    output = args.output.resolve()
    if output.exists():
        print(f"error: refusing to overwrite existing file: {output}", file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
