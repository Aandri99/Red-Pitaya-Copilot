#!/usr/bin/env python3
"""Validate the shared Red Pitaya project contract with no third-party modules."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


OS_RELEASES = {"2.00-35", "2.07-48", "3.00-57"}
MODELS = {
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
}
ARCHITECTURES = {
    "none",
    "static",
    "lua-api",
    "native-controller",
    "service",
    "hybrid",
}
AUTHENTICATION_MODES = {"none", "operator-token"}
TOP_KEYS = {
    "schema_version",
    "project",
    "target",
    "application",
    "fpga",
    "deployment",
    "safety",
}
PROJECT_KEYS = {"slug", "title", "version", "description"}
TARGET_KEYS = {
    "os_release",
    "model",
    "role",
    "hostname",
    "expected_serial_or_mac_suffix",
}
APPLICATION_KEYS = {
    "enabled",
    "architecture",
    "authentication",
    "slug",
    "routes",
    "controller_binary",
    "service_name",
    "python_abi",
    "service_port",
}
APPLICATION_REQUIRED_KEYS = APPLICATION_KEYS - {"authentication"}
FPGA_KEYS = {
    "enabled",
    "project",
    "artifact_name",
    "device_tree_name",
    "loader",
    "register_contract",
    "version_register",
    "magic_value",
    "clock_hz",
}
DEPLOYMENT_KEYS = {
    "app_root",
    "mountpoint",
    "staging_root",
    "backup_root",
    "nginx_service",
    "restore_read_only",
    "allow_live_output_test",
}
SAFETY_KEYS = {
    "output_default",
    "stop_action",
    "physical_test_requires_authorization",
    "rollback_required",
    "maximum_output_volts",
}


@dataclass
class Finding:
    severity: str
    path: str
    message: str


class Validator:
    def __init__(self, data: Any):
        self.data = data
        self.findings: list[Finding] = []

    def error(self, path: str, message: str) -> None:
        self.findings.append(Finding("error", path, message))

    def warning(self, path: str, message: str) -> None:
        self.findings.append(Finding("warning", path, message))

    def require_dict(self, value: Any, path: str) -> dict:
        if not isinstance(value, dict):
            self.error(path, "must be an object")
            return {}
        return value

    def require_keys(self, obj: dict, path: str, keys: set[str]) -> None:
        for key in sorted(keys):
            if key not in obj:
                self.error(f"{path}.{key}", "is required")

    def reject_unknown_keys(
        self, obj: dict, path: str, allowed_keys: set[str]
    ) -> None:
        for key in sorted(set(obj) - allowed_keys):
            self.error(f"{path}.{key}", "unknown property")

    def reject_secret_keys(self, value: Any, path: str = "$") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if re.search(r"(password|passwd|secret|private.?key|token)", key, re.I):
                    self.error(
                        f"{path}.{key}",
                        "secret-bearing keys are forbidden in the project manifest",
                    )
                self.reject_secret_keys(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self.reject_secret_keys(child, f"{path}[{index}]")

    def require_safe_child_path(
        self, value: Any, path: str, parent: str
    ) -> None:
        if not isinstance(value, str):
            self.error(path, "must be a POSIX path string")
            return
        candidate = PurePosixPath(value)
        required_parent = PurePosixPath(parent)
        if (
            not candidate.is_absolute()
            or candidate == required_parent
            or required_parent not in candidate.parents
            or ".." in candidate.parts
        ):
            self.error(path, f"must be a direct or nested child of {parent}")

    def validate(self) -> list[Finding]:
        root = self.require_dict(self.data, "$")
        self.require_keys(root, "$", TOP_KEYS)
        unknown = set(root) - TOP_KEYS
        for key in sorted(unknown):
            self.error(f"$.{key}", "unknown top-level property")
        if root.get("schema_version") != 1:
            self.error("$.schema_version", "must equal 1")

        project = self.require_dict(root.get("project"), "$.project")
        self.require_keys(project, "$.project", {"slug", "title", "version"})
        self.reject_unknown_keys(project, "$.project", PROJECT_KEYS)
        slug = project.get("slug")
        if not isinstance(slug, str) or not re.fullmatch(
            r"[a-z][a-z0-9_-]{1,62}[a-z0-9]", slug
        ):
            self.error("$.project.slug", "has an invalid app/project slug")
        if not isinstance(project.get("title"), str) or not project.get("title"):
            self.error("$.project.title", "must be a non-empty string")
        elif len(project["title"]) > 100:
            self.error("$.project.title", "must be 100 characters or fewer")
        if "description" in project and (
            not isinstance(project["description"], str)
            or len(project["description"]) > 500
        ):
            self.error(
                "$.project.description",
                "must be a string of 500 characters or fewer",
            )
        if not isinstance(project.get("version"), str) or not re.fullmatch(
            r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?",
            project.get("version", ""),
        ):
            self.error("$.project.version", "must be a semantic version")

        target = self.require_dict(root.get("target"), "$.target")
        self.require_keys(target, "$.target", {"os_release", "model", "role"})
        self.reject_unknown_keys(target, "$.target", TARGET_KEYS)
        os_release = target.get("os_release")
        model = target.get("model")
        if os_release not in OS_RELEASES:
            self.error("$.target.os_release", f"must be one of {sorted(OS_RELEASES)}")
        if model not in MODELS:
            self.error("$.target.model", f"must be one of {sorted(MODELS)}")
        if not isinstance(target.get("role"), str) or not re.fullmatch(
            r"[a-z][a-z0-9_-]{1,31}", target.get("role", "")
        ):
            self.error(
                "$.target.role",
                "must be 2-32 lowercase letters, digits, underscores or hyphens",
            )
        if not target.get("hostname"):
            self.warning(
                "$.target.hostname",
                "not set; deployment must discover and record the exact board",
            )
        elif not isinstance(target.get("hostname"), str) or len(
            target["hostname"]
        ) > 253:
            self.error("$.target.hostname", "must be a valid-length hostname")
        serial_suffix = target.get("expected_serial_or_mac_suffix")
        if serial_suffix is not None and (
            not isinstance(serial_suffix, str)
            or not re.fullmatch(r"[A-Fa-f0-9:-]{4,32}", serial_suffix)
        ):
            self.error(
                "$.target.expected_serial_or_mac_suffix",
                "must contain 4-32 hexadecimal, colon or hyphen characters",
            )

        app = self.require_dict(root.get("application"), "$.application")
        self.require_keys(
            app, "$.application", APPLICATION_REQUIRED_KEYS
        )
        self.reject_unknown_keys(app, "$.application", APPLICATION_KEYS)
        if not isinstance(app.get("enabled"), bool):
            self.error("$.application.enabled", "must be a boolean")
        architecture = app.get("architecture")
        if architecture not in ARCHITECTURES:
            self.error(
                "$.application.architecture",
                f"must be one of {sorted(ARCHITECTURES)}",
            )
        if app.get("enabled") is False and architecture != "none":
            self.error(
                "$.application.architecture",
                "must be 'none' when application.enabled is false",
            )
        if app.get("enabled") is True and architecture == "none":
            self.error(
                "$.application.architecture",
                "cannot be 'none' when application.enabled is true",
            )
        authentication = app.get("authentication")
        if authentication is None:
            authentication = "none"
            self.warning(
                "$.application.authentication",
                "legacy manifest omits the application access-control mode; "
                "treating it as 'none' and requiring an explicit value before deployment",
            )
        elif authentication not in AUTHENTICATION_MODES:
            self.error(
                "$.application.authentication",
                f"must be one of {sorted(AUTHENTICATION_MODES)}",
            )
        if app.get("enabled") is False and authentication != "none":
            self.error(
                "$.application.authentication",
                "must be 'none' when application.enabled is false",
            )
        if slug and app.get("slug") != slug:
            self.error(
                "$.application.slug",
                "must match project.slug to keep folder and URL contracts stable",
            )
        if not isinstance(app.get("slug"), str) or not re.fullmatch(
            r"[a-z][a-z0-9_-]{1,62}[a-z0-9]", app.get("slug", "")
        ):
            self.error("$.application.slug", "has an invalid app slug")
        routes = app.get("routes")
        if not isinstance(routes, list) or any(
            not isinstance(route, str)
            or not re.fullmatch(r"/[A-Za-z0-9_./{}:-]+", route)
            for route in routes if isinstance(routes, list)
        ):
            self.error("$.application.routes", "must be an array of absolute routes")
        elif len(routes) != len(set(routes)):
            self.error("$.application.routes", "must not contain duplicates")
        if app.get("enabled") is True and isinstance(routes, list) and not routes:
            self.error("$.application.routes", "must contain at least one app route")
        if app.get("enabled") is False:
            expected_disabled = {
                "controller_binary": None,
                "service_name": None,
                "python_abi": "none",
                "service_port": None,
            }
            if routes not in ([], None):
                self.error(
                    "$.application.routes",
                    "must be empty when application is disabled",
                )
            for key, expected_value in expected_disabled.items():
                if app.get(key) != expected_value:
                    self.error(
                        f"$.application.{key}",
                        f"must equal {expected_value!r} when disabled",
                    )
        if architecture == "native-controller" and app.get(
            "controller_binary"
        ) != "controllerhf.so":
            self.error(
                "$.application.controller_binary",
                "native-controller requires target-built controllerhf.so",
            )
        if architecture in {"service", "hybrid"} and not re.fullmatch(
            r"[A-Za-z0-9_.@-]+\.service", str(app.get("service_name") or "")
        ):
            self.error(
                "$.application.service_name",
                "service and hybrid architectures require a .service unit name",
            )
        service_port = app.get("service_port")
        if architecture in {"service", "hybrid"}:
            if (
                not isinstance(service_port, int)
                or isinstance(service_port, bool)
                or not 1024 <= service_port <= 65535
            ):
                self.error(
                    "$.application.service_port",
                    "service and hybrid architectures require an integer port "
                    "between 1024 and 65535",
                )
        elif service_port is not None:
            self.error(
                "$.application.service_port",
                "must be null for non-service architectures",
            )
        expected_python = (
            "3.10"
            if os_release in {"2.00-35", "2.07-48"}
            else "3.12"
        )
        if architecture in {"service", "hybrid"} and app.get(
            "python_abi"
        ) not in {"none", expected_python}:
            self.error(
                "$.application.python_abi",
                f"target {os_release} expects Python {expected_python}",
            )

        fpga = self.require_dict(root.get("fpga"), "$.fpga")
        self.require_keys(
            fpga,
            "$.fpga",
            FPGA_KEYS,
        )
        self.reject_unknown_keys(fpga, "$.fpga", FPGA_KEYS)
        if not isinstance(fpga.get("enabled"), bool):
            self.error("$.fpga.enabled", "must be a boolean")
        if fpga.get("enabled"):
            if not isinstance(fpga.get("project"), str) or not re.fullmatch(
                r"[A-Za-z0-9_.-]+", fpga.get("project", "")
            ):
                self.error("$.fpga.project", "has an invalid FPGA project name")
            artifact = fpga.get("artifact_name")
            expected = (
                "fpga.bit.bin"
                if os_release in {"2.00-35", "2.07-48"}
                else "fpga.bin"
            )
            if artifact != expected:
                self.error(
                    "$.fpga.artifact_name",
                    f"target {os_release} requires packaged name {expected!r}",
                )
            if fpga.get("loader") not in {"overlay.sh", "fpgautil"}:
                self.error(
                    "$.fpga.loader",
                    "enabled FPGA requires overlay.sh or fpgautil",
                )
            for key in ("register_contract", "version_register", "magic_value"):
                if not fpga.get(key):
                    self.error(
                        f"$.fpga.{key}",
                        "is required for an enabled custom FPGA",
                    )
            for key in ("version_register", "magic_value"):
                value = fpga.get(key)
                if value is not None and (
                    not isinstance(value, str)
                    or not re.fullmatch(r"0x[0-9A-Fa-f]{8}", value)
                ):
                    self.error(
                        f"$.fpga.{key}",
                        "must be an eight-digit hexadecimal word",
                    )
            if not isinstance(fpga.get("clock_hz"), int) or isinstance(
                fpga.get("clock_hz"), bool
            ) or fpga.get("clock_hz", 0) <= 0:
                self.error("$.fpga.clock_hz", "must be a positive integer")
            if fpga.get("device_tree_name") != "fpga.dtbo":
                self.error(
                    "$.fpga.device_tree_name",
                    "must be fpga.dtbo for the supported overlay workflow",
                )
        else:
            if fpga.get("project") != "none":
                self.error("$.fpga.project", "must be 'none' when FPGA is disabled")
            if fpga.get("loader") != "none":
                self.error("$.fpga.loader", "must be 'none' when FPGA is disabled")
            for key in (
                "artifact_name",
                "device_tree_name",
                "register_contract",
                "version_register",
                "magic_value",
                "clock_hz",
            ):
                if fpga.get(key) is not None:
                    self.error(
                        f"$.fpga.{key}", "must be null when FPGA is disabled"
                    )

        deployment = self.require_dict(root.get("deployment"), "$.deployment")
        self.require_keys(
            deployment,
            "$.deployment",
            {
                "app_root",
                "mountpoint",
                "staging_root",
                "backup_root",
                "nginx_service",
                "restore_read_only",
                "allow_live_output_test",
            },
        )
        self.reject_unknown_keys(
            deployment, "$.deployment", DEPLOYMENT_KEYS
        )
        expected_deploy = {
            "app_root": "/opt/redpitaya/www/apps",
            "mountpoint": "/opt/redpitaya",
            "nginx_service": "redpitaya_nginx.service",
            "restore_read_only": True,
        }
        for key, value in expected_deploy.items():
            if deployment.get(key) != value:
                self.error(f"$.deployment.{key}", f"must equal {value!r}")
        self.require_safe_child_path(
            deployment.get("staging_root"),
            "$.deployment.staging_root",
            "/tmp",
        )
        self.require_safe_child_path(
            deployment.get("backup_root"),
            "$.deployment.backup_root",
            "/root",
        )
        expected_staging_root = "/tmp/redpitaya-deploy/{deployment_id}"
        if deployment.get("staging_root") != expected_staging_root:
            self.error(
                "$.deployment.staging_root",
                f"must equal {expected_staging_root!r}",
            )
        expected_backup_root = (
            f"/root/redpitaya-backups/{slug}/{{deployment_id}}"
            if isinstance(slug, str)
            else None
        )
        if expected_backup_root and deployment.get("backup_root") != expected_backup_root:
            self.error(
                "$.deployment.backup_root",
                f"must equal {expected_backup_root!r}",
            )
        if not isinstance(deployment.get("allow_live_output_test"), bool):
            self.error(
                "$.deployment.allow_live_output_test",
                "must be a boolean",
            )

        safety = self.require_dict(root.get("safety"), "$.safety")
        self.require_keys(safety, "$.safety", SAFETY_KEYS)
        self.reject_unknown_keys(safety, "$.safety", SAFETY_KEYS)
        expected_safety = {
            "output_default": "disabled",
            "physical_test_requires_authorization": True,
            "rollback_required": True,
        }
        for key, value in expected_safety.items():
            if safety.get(key) != value:
                self.error(f"$.safety.{key}", f"must equal {value!r}")
        if not safety.get("stop_action"):
            self.error("$.safety.stop_action", "must describe an idempotent stop")
        maximum_output = safety.get("maximum_output_volts")
        if maximum_output is not None and (
            not isinstance(maximum_output, (int, float))
            or isinstance(maximum_output, bool)
            or maximum_output <= 0
        ):
            self.error(
                "$.safety.maximum_output_volts",
                "must be null or a positive number",
            )

        self.reject_secret_keys(root)
        return self.findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    try:
        data = json.loads(args.manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"error: file not found: {args.manifest}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON: {exc}", file=sys.stderr)
        return 2

    findings = Validator(data).validate()
    if args.json_output:
        print(json.dumps([finding.__dict__ for finding in findings], indent=2))
    else:
        for finding in findings:
            print(
                f"{finding.severity.upper()}: {finding.path}: {finding.message}"
            )
        errors = sum(item.severity == "error" for item in findings)
        warnings = sum(item.severity == "warning" for item in findings)
        print(f"Summary: {errors} error(s), {warnings} warning(s)")

    has_errors = any(item.severity == "error" for item in findings)
    has_warnings = any(item.severity == "warning" for item in findings)
    return 1 if has_errors or (args.strict and has_warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
