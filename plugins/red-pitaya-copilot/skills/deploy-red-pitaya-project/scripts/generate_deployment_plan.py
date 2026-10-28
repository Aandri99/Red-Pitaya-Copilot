#!/usr/bin/env python3
"""Generate an offline, deterministic Red Pitaya deployment plan.

The plan describes files and semantic gates. It intentionally executes no SSH,
copy, mount, service, Nginx or FPGA command.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import analyze_probe as probe_analyzer
from project_contract import (
    ContractError,
    load_and_validate_manifest,
    load_os_profile,
    read_json_object,
)

DEPLOYMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
MODE_RE = re.compile(r"^0?[0-7]{3,4}$")
REMOTE_PATH_RE = re.compile(
    r"^/(?:[A-Za-z0-9][A-Za-z0-9._@+-]*/)*"
    r"[A-Za-z0-9][A-Za-z0-9._@+-]*$"
)
SECRET_NAME_RE = re.compile(
    r"(^\.env(?:\.|$)|(^|[._-])"
    r"(id_rsa|id_ed25519|password|passwd|secret|token|credentials?|"
    r"private[-_]?key|authorized_keys)($|[._-])|"
    r"\.(?:pem|p12|pfx|key)$)",
    re.IGNORECASE,
)
ALLOWED_COMPONENTS = {
    "app",
    "nginx",
    "service",
    "fpga",
    "device-tree",
    "fpga-profile",
    "device-tree-profile",
    "configuration",
    "other",
}
PROTECTED_DESTINATIONS = {
    "/etc/systemd/system/ssh.service",
    "/etc/systemd/system/sshd.service",
    "/etc/systemd/system/nginx.service",
    "/etc/systemd/system/redpitaya_nginx.service",
    "/opt/redpitaya/sbin/overlay.sh",
    "/opt/redpitaya/sbin/fpga.sh",
}


class PlanError(ValueError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    try:
        return read_json_object(path)
    except ContractError as exc:
        raise PlanError(str(exc)) from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return sha256_bytes(encoded)


def plan_core_sha256(plan: dict[str, Any]) -> str:
    core = {
        key: value
        for key, value in plan.items()
        if key not in {"gates", "gate_evidence", "plan_core_sha256"}
    }
    return canonical_sha256(core)


def ensure_remote_path(value: str) -> PurePosixPath:
    if value != value.strip():
        raise PlanError(
            f"remote destination contains leading or trailing whitespace: {value!r}"
        )
    if not REMOTE_PATH_RE.fullmatch(value):
        raise PlanError(
            "remote destination must use canonical ASCII path components and "
            f"contain no whitespace, control characters or shell metacharacters: {value!r}"
        )
    path = PurePosixPath(value)
    if not path.is_absolute():
        raise PlanError(f"remote destination must be absolute: {value!r}")
    if ".." in path.parts or "." in path.parts:
        raise PlanError(f"remote destination contains traversal: {value!r}")
    if str(path) != value:
        raise PlanError(f"remote destination is not canonical: {value!r}")
    if str(path) in {"/", "/opt", "/opt/redpitaya", "/etc", "/usr", "/root"}:
        raise PlanError(f"remote destination is too broad: {value!r}")
    if value in PROTECTED_DESTINATIONS:
        raise PlanError(f"remote destination is protected: {value!r}")
    if value.startswith("/opt/redpitaya/sbin/") or value.startswith(
        "/opt/redpitaya/bin/"
    ):
        raise PlanError(f"Red Pitaya loader/tool path is protected: {value!r}")
    allowed_prefixes = (
        "/opt/redpitaya/",
        "/etc/systemd/system/",
        "/etc/redpitaya/",
        "/usr/local/bin/",
        "/usr/local/libexec/",
        "/usr/local/share/",
    )
    if not any(str(path).startswith(prefix) for prefix in allowed_prefixes):
        raise PlanError(
            f"remote destination {value!r} is outside the allow-listed deployment roots"
        )
    return path


def safe_local_file(project_root: Path, relative: str) -> Path:
    if Path(relative).is_absolute():
        raise PlanError(f"source must be relative to project root: {relative!r}")
    if ".." in Path(relative).parts:
        raise PlanError(f"source contains traversal: {relative!r}")
    candidate = (project_root / relative).resolve()
    root = project_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PlanError(f"source escapes project root: {relative!r}") from exc
    if candidate.is_symlink():
        raise PlanError(f"symlink sources are not permitted: {relative!r}")
    if not candidate.exists():
        raise PlanError(f"source does not exist: {relative!r}")
    return candidate


def project_relative_path(project_root: Path, path: Path, label: str) -> str:
    try:
        relative = path.resolve().relative_to(project_root.resolve())
    except ValueError as exc:
        raise PlanError(f"{label} must be inside project root") from exc
    return relative.as_posix()


def source_files(path: Path) -> Iterable[tuple[Path, Path]]:
    if path.is_file():
        yield path, Path(path.name)
        return
    if not path.is_dir():
        raise PlanError(f"source is not a regular file or directory: {path}")
    for candidate in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        if candidate.is_symlink():
            raise PlanError(f"symlink inside source directory is not permitted: {candidate}")
        if candidate.is_file():
            yield candidate, candidate.relative_to(path)


def infer_mode(path: Path, requested: str | None) -> str:
    if requested:
        if not MODE_RE.fullmatch(requested):
            raise PlanError(f"invalid file mode {requested!r}")
        return f"{int(requested, 8):04o}"
    executable = bool(path.stat().st_mode & stat.S_IXUSR)
    return "0755" if executable else "0644"


def ensure_not_secret(path: Path) -> None:
    if SECRET_NAME_RE.search(path.name):
        raise PlanError(f"refusing secret-like source filename: {path.name!r}")
    head = path.read_bytes()[:4096]
    if b"-----BEGIN " in head and b"PRIVATE KEY-----" in head:
        raise PlanError(f"refusing private-key material in {path}")
    if b"password=" in head.lower() or b"passwd=" in head.lower():
        raise PlanError(f"refusing probable embedded password in {path}")


def verify_probe(
    report: dict[str, Any], expected_os: str, expected_model: str, expected_role: str
) -> dict[str, Any]:
    if report.get("report_schema_version") != 1:
        raise PlanError("probe report report_schema_version must equal 1")
    if report.get("compatible") is not True:
        raise PlanError("probe report is not compatible; resolve blocking_errors")
    warnings = report.get("warnings")
    if not isinstance(warnings, list):
        raise PlanError("probe report warnings must be an array")
    if warnings:
        raise PlanError(
            "probe report contains unresolved warnings; complete the probe before "
            "deployment planning: " + " | ".join(str(item) for item in warnings)
        )
    target = report.get("target")
    identity = report.get("identity")
    connection = report.get("connection")
    mount = report.get("mount")
    if not all(isinstance(item, dict) for item in (target, identity, connection, mount)):
        raise PlanError("probe report is missing target/identity/connection/mount objects")
    if target.get("observed_os_release") != expected_os:
        raise PlanError("probe OS does not match manifest target.os_release")
    if target.get("observed_model") != expected_model:
        raise PlanError("probe model does not match manifest target.model")
    if target.get("expected_role") != expected_role or target.get("role_exact") is not True:
        raise PlanError("probe role does not match manifest target.role")
    if connection.get("host_key_verified") is not True:
        raise PlanError("probe report does not confirm SSH host-key verification")
    fingerprint = str(connection.get("ssh_host_key_sha256", ""))
    if not probe_analyzer.valid_ssh_sha256_fingerprint(fingerprint):
        raise PlanError(
            "probe report SSH fingerprint must decode to exactly 32 bytes"
        )
    host = str(connection.get("host", ""))
    address = str(connection.get("address", ""))
    if not host and not address:
        raise PlanError("probe report has no connection host/address")
    if host and not probe_analyzer.HOSTNAME_RE.fullmatch(host):
        raise PlanError("probe report connection host is invalid")
    if address:
        try:
            ipaddress.ip_address(address)
        except ValueError as exc:
            raise PlanError("probe report connection address is invalid") from exc
    username = str(connection.get("username", "")).strip()
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", username):
        raise PlanError("probe report does not contain an exact valid SSH username")
    evidence = report.get("evidence")
    if not isinstance(evidence, dict):
        raise PlanError("probe report evidence must be an object")
    captured_at = str(evidence.get("captured_at", "")).strip()
    if not captured_at:
        raise PlanError("probe report evidence.captured_at is required")
    try:
        datetime.strptime(captured_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise PlanError("probe report capture time is not valid UTC") from exc
    if not str(evidence.get("probe_source", "")).strip():
        raise PlanError("probe report evidence.probe_source is required")
    if mount.get("target") != "/opt/redpitaya":
        raise PlanError("probe report mount target is not exact /opt/redpitaya")
    if not str(mount.get("source", "")).strip():
        raise PlanError("probe report mount source is required")
    if not str(mount.get("fstype", "")).strip():
        raise PlanError("probe report mount filesystem type is required")
    options = mount.get("options")
    if not isinstance(options, list) or not options:
        raise PlanError("probe report mount options must be a non-empty array")
    if mount.get("originally_read_only") is not ("ro" in options):
        raise PlanError("probe report original mount-state flag is inconsistent")
    return {
        "connection": connection,
        "identity": identity,
        "mount": mount,
        "evidence": evidence,
        "warnings": warnings,
    }


def expand_file_map(
    project_root: Path,
    file_map: dict[str, Any],
    os_release: str,
    expected_fpga_artifact: str | None,
) -> list[dict[str, Any]]:
    unknown_root_keys = sorted(set(file_map) - {"schema_version", "files"})
    if unknown_root_keys:
        raise PlanError(
            "file map contains unknown keys: " + ", ".join(unknown_root_keys)
        )
    if file_map.get("schema_version") != 1:
        raise PlanError("file map schema_version must equal 1")
    mappings = file_map.get("files")
    if not isinstance(mappings, list) or not mappings:
        raise PlanError("file map files must be a non-empty array")
    result: list[dict[str, Any]] = []
    destinations: set[str] = set()
    fpga_artifacts = 0

    for index, mapping in enumerate(mappings):
        if not isinstance(mapping, dict):
            raise PlanError(f"file map entry {index} must be an object")
        unknown_entry_keys = sorted(
            set(mapping) - {"source", "destination", "component", "mode"}
        )
        if unknown_entry_keys:
            raise PlanError(
                f"file map entry {index} contains unknown keys: "
                + ", ".join(unknown_entry_keys)
            )
        source_text = str(mapping.get("source", "")).strip()
        destination_value = mapping.get("destination", "")
        if not isinstance(destination_value, str):
            raise PlanError(f"entry {index} destination must be a string")
        destination_text = destination_value
        component = str(mapping.get("component", "")).strip()
        requested_mode = mapping.get("mode")
        if component not in ALLOWED_COMPONENTS:
            raise PlanError(
                f"entry {index} component must be one of {sorted(ALLOWED_COMPONENTS)}"
            )
        source = safe_local_file(project_root, source_text)
        destination_root = ensure_remote_path(destination_text)
        is_directory = source.is_dir()
        for local_file, relative in source_files(source):
            ensure_not_secret(local_file)
            if is_directory:
                destination = destination_root.joinpath(*relative.parts)
            else:
                destination = destination_root
                if destination_text.endswith("/"):
                    destination = destination_root / local_file.name
            destination_string = str(destination)
            ensure_remote_path(destination_string)
            if destination_string in destinations:
                raise PlanError(f"duplicate remote destination: {destination_string}")
            destinations.add(destination_string)
            if component in {"fpga", "fpga-profile"}:
                if component == "fpga":
                    fpga_artifacts += 1
                expected = expected_fpga_artifact
                if destination.name != expected:
                    raise PlanError(
                        f"OS {os_release} FPGA artifact must be installed as {expected!r}; "
                        f"got {destination.name!r}"
                    )
            relative_local = local_file.resolve().relative_to(project_root.resolve())
            result.append(
                {
                    "source": relative_local.as_posix(),
                    "destination": destination_string,
                    "component": component,
                    "mode": infer_mode(local_file, str(requested_mode) if requested_mode else None),
                    "size": local_file.stat().st_size,
                    "sha256": sha256_file(local_file),
                }
            )

    if fpga_artifacts > 1:
        raise PlanError("a plan may contain only one primary FPGA artifact")
    return sorted(result, key=lambda item: (item["destination"], item["source"]))


def is_beneath(destination: str, root: str) -> bool:
    return destination.startswith(root.rstrip("/") + "/")


def validate_component_destination(
    item: dict[str, Any],
    manifest: dict[str, Any],
    profile: dict[str, Any],
) -> None:
    component = item["component"]
    destination = item["destination"]
    application = manifest["application"]
    fpga = manifest["fpga"]
    deployment = manifest["deployment"]
    slug = application["slug"]
    app_root = f"{deployment['app_root'].rstrip('/')}/{slug}"
    profile_template = profile.get("fpga_profile_root_template")
    profile_root = None
    if isinstance(profile_template, str) and profile_template:
        profile_root = profile_template.format(
            model=manifest["target"]["model"],
            profile=slug,
        )

    if component == "app":
        allowed = is_beneath(destination, app_root)
        expectation = f"beneath {app_root}/"
    elif component == "nginx":
        allowed = (
            destination == f"{app_root}/nginx.conf"
            or is_beneath(destination, f"{app_root}/nginx")
        )
        expectation = (
            f"exactly {app_root}/nginx.conf or beneath {app_root}/nginx/"
        )
    elif component == "service":
        service_name = application.get("service_name")
        allowed = bool(
            service_name
            and destination == f"/etc/systemd/system/{service_name}"
        )
        expectation = (
            f"exactly /etc/systemd/system/{service_name}"
            if service_name
            else "no destination because application.service_name is null"
        )
    elif component == "fpga":
        allowed = bool(
            fpga.get("enabled")
            and destination == f"{app_root}/{fpga.get('artifact_name')}"
        )
        expectation = f"exactly {app_root}/{fpga.get('artifact_name')}"
    elif component == "device-tree":
        allowed = bool(
            fpga.get("enabled")
            and destination == f"{app_root}/{fpga.get('device_tree_name')}"
        )
        expectation = f"exactly {app_root}/{fpga.get('device_tree_name')}"
    elif component == "fpga-profile":
        allowed = bool(
            fpga.get("enabled")
            and profile_root
            and destination == f"{profile_root}/{fpga.get('artifact_name')}"
        )
        expectation = (
            f"exactly {profile_root}/{fpga.get('artifact_name')}"
            if profile_root
            else "no destination because this OS profile has no verified FPGA profile layout"
        )
    elif component == "device-tree-profile":
        allowed = bool(
            fpga.get("enabled")
            and profile_root
            and destination == f"{profile_root}/{fpga.get('device_tree_name')}"
        )
        expectation = (
            f"exactly {profile_root}/{fpga.get('device_tree_name')}"
            if profile_root
            else "no destination because this OS profile has no verified FPGA profile layout"
        )
    elif component == "configuration":
        register_contract = f"{app_root}/register-contract.json"
        project_config_root = f"/etc/redpitaya/{slug}"
        allowed = (
            destination == register_contract
            or is_beneath(destination, app_root)
            or is_beneath(destination, project_config_root)
        )
        expectation = (
            f"{register_contract}, beneath {app_root}/, or beneath "
            f"{project_config_root}/"
        )
    elif component == "other":
        allowed_roots = (
            f"/usr/local/share/{slug}",
            f"/usr/local/libexec/{slug}",
        )
        allowed = any(is_beneath(destination, root) for root in allowed_roots)
        allowed = allowed or destination == f"/usr/local/bin/{slug}"
        expectation = (
            f"beneath /usr/local/share/{slug}/ or "
            f"/usr/local/libexec/{slug}/, or exactly /usr/local/bin/{slug}"
        )
    else:
        allowed = False
        expectation = "a known component destination"

    if not allowed:
        raise PlanError(
            f"component {component!r} destination {destination!r} is not "
            f"manifest-derived; expected {expectation}"
        )


def validate_payload_against_contract(
    files: list[dict[str, Any]],
    manifest: dict[str, Any],
    profile: dict[str, Any],
) -> None:
    application = manifest["application"]
    fpga = manifest["fpga"]
    deployment = manifest["deployment"]
    components = {item["component"] for item in files}
    app_prefix = f"{deployment['app_root']}/{application['slug']}/"

    for item in files:
        validate_component_destination(item, manifest, profile)

    if not application["enabled"] and components.intersection({"app", "nginx"}):
        raise PlanError(
            "file map contains app/nginx components while application.enabled is false"
        )
    if application["enabled"]:
        for item in files:
            if item["component"] in {"app", "nginx"} and not item[
                "destination"
            ].startswith(app_prefix):
                raise PlanError(
                    f"{item['component']} destination must be beneath {app_prefix!r}"
                )
    if application["architecture"] == "native-controller":
        binary = application["controller_binary"]
        if not any(
            item["destination"].startswith(app_prefix)
            and PurePosixPath(item["destination"]).name == PurePosixPath(binary).name
            for item in files
        ):
            raise PlanError(
                "native-controller contract requires its controller_binary in the payload"
            )
    if application["service_name"]:
        service_name = application["service_name"]
        if not any(
            item["component"] == "service"
            and PurePosixPath(item["destination"]).name == service_name
            for item in files
        ):
            raise PlanError(
                f"service contract requires unit {service_name!r} in the payload"
            )

    app_fpga_destination = f"{app_prefix}{fpga.get('artifact_name')}"
    app_dt_destination = f"{app_prefix}{fpga.get('device_tree_name')}"
    fpga_payload = [
        item for item in files
        if item["component"] == "fpga"
        or (item["component"] == "app" and item["destination"] == app_fpga_destination)
    ]
    dt_payload = [
        item for item in files
        if item["component"] == "device-tree"
        or (item["component"] == "app" and item["destination"] == app_dt_destination)
    ]
    fpga_profile_payload = [
        item for item in files if item["component"] == "fpga-profile"
    ]
    dt_profile_payload = [
        item for item in files if item["component"] == "device-tree-profile"
    ]
    if not fpga["enabled"] and (
        fpga_payload or dt_payload or fpga_profile_payload or dt_profile_payload
    ):
        raise PlanError(
            "file map contains FPGA/device-tree components while fpga.enabled is false"
        )
    if fpga["enabled"]:
        if len(fpga_payload) != 1:
            raise PlanError("fpga.enabled requires exactly one component=fpga file")
        if len(dt_payload) != 1:
            raise PlanError(
                "fpga.enabled requires exactly one component=device-tree file"
            )
        profile_template = profile.get("fpga_profile_root_template")
        if fpga.get("loader") == "overlay.sh" and profile_template:
            if len(fpga_profile_payload) != 1:
                raise PlanError(
                    "overlay.sh deployment requires exactly one component=fpga-profile file"
                )
            if len(dt_profile_payload) != 1:
                raise PlanError(
                    "overlay.sh deployment requires exactly one component=device-tree-profile file"
                )
            if fpga_payload[0]["sha256"] != fpga_profile_payload[0]["sha256"]:
                raise PlanError(
                    "app and permanent-profile FPGA artifact hashes differ"
                )
            if dt_payload[0]["sha256"] != dt_profile_payload[0]["sha256"]:
                raise PlanError(
                    "app and permanent-profile device-tree hashes differ"
                )
        if PurePosixPath(fpga_payload[0]["destination"]).name != fpga["artifact_name"]:
            raise PlanError("FPGA payload filename conflicts with fpga.artifact_name")
        if PurePosixPath(dt_payload[0]["destination"]).name != fpga["device_tree_name"]:
            raise PlanError(
                "device-tree payload filename conflicts with fpga.device_tree_name"
            )
        if fpga["artifact_name"] != profile["fpga_artifact_filename"]:
            raise PlanError("FPGA contract conflicts with selected OS profile")
        contract_payload = [
            item
            for item in files
            if item["component"] == "configuration"
            and PurePosixPath(item["destination"]).name == "register-contract.json"
        ]
        if len(contract_payload) != 1:
            raise PlanError(
                "fpga.enabled requires exactly one configuration payload named "
                "register-contract.json"
            )


def validate_application_bundle(
    file_map: dict[str, Any],
    project_root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
) -> dict[str, Any] | None:
    application = manifest["application"]
    if application["enabled"] is not True:
        return None

    mappings = file_map.get("files")
    if not isinstance(mappings, list):
        raise PlanError("file map files must be an array")
    app_mappings = [
        item
        for item in mappings
        if isinstance(item, dict) and item.get("component") == "app"
    ]
    if len(app_mappings) != 1:
        raise PlanError(
            "application.enabled requires exactly one component=app bundle mapping"
        )
    mapping = app_mappings[0]
    source_text = mapping.get("source")
    destination_text = mapping.get("destination")
    if not isinstance(source_text, str) or not source_text:
        raise PlanError("application bundle source must be a non-empty string")
    if not isinstance(destination_text, str):
        raise PlanError("application bundle destination must be a string")
    expected_destination = (
        f"{manifest['deployment']['app_root'].rstrip('/')}/"
        f"{application['slug']}"
    )
    ensure_remote_path(destination_text)
    if destination_text != expected_destination:
        raise PlanError(
            "application bundle mapping must be rooted exactly at "
            f"{expected_destination!r}"
        )
    bundle_root = safe_local_file(project_root, source_text)
    if not bundle_root.is_dir():
        raise PlanError("application bundle source must be a directory")
    bundle_manifest_path = bundle_root / "redpitaya-project.json"
    if not bundle_manifest_path.is_file() or bundle_manifest_path.is_symlink():
        raise PlanError(
            "application bundle must contain a regular redpitaya-project.json"
        )
    bundle_manifest = load_object(bundle_manifest_path)
    if bundle_manifest != manifest:
        raise PlanError(
            "application bundle redpitaya-project.json differs from the "
            "deployment manifest"
        )

    validator = (
        Path(__file__).resolve().parents[3]
        / "skills"
        / "build-red-pitaya-app"
        / "scripts"
        / "validate_red_pitaya_app.py"
    )
    if not validator.is_file():
        raise PlanError(f"application validator is missing: {validator}")
    result = subprocess.run(
        [
            sys.executable,
            str(validator),
            str(bundle_root),
            "--os-release",
            str(manifest["target"]["os_release"]),
            "--deployment-ready",
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        validation = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PlanError(
            "application deployment-ready validator returned invalid JSON"
            + (f": {result.stderr.strip()}" if result.stderr.strip() else "")
        ) from exc
    if (
        result.returncode != 0
        or not isinstance(validation, dict)
        or validation.get("ok") is not True
        or validation.get("deployment_ready") is not True
        or validation.get("errors") != []
        or validation.get("warnings") != []
    ):
        details = result.stderr.strip() or json.dumps(validation, sort_keys=True)
        raise PlanError(
            "application bundle is not deployment-ready; preserve native-build "
            f"and target warnings until resolved: {details}"
        )

    inventory = []
    for local_file, relative in source_files(bundle_root):
        ensure_not_secret(local_file)
        inventory.append(
            {
                "path": relative.as_posix(),
                "size": local_file.stat().st_size,
                "sha256": sha256_file(local_file),
            }
        )
    receipt_core = {
        "kind": "deployment-ready-app-validation",
        "mode": "deployment-ready",
        "result": "pass",
        "error_count": 0,
        "warning_count": 0,
        "bundle_inventory_sha256": canonical_sha256(inventory),
        "bundle_manifest_sha256": sha256_file(bundle_manifest_path),
        "validator_sha256": sha256_file(validator),
    }
    return {
        "source_root": project_relative_path(
            project_root, bundle_root, "application bundle"
        ),
        "destination_root": expected_destination,
        "bundle_manifest_path": project_relative_path(
            project_root,
            bundle_manifest_path,
            "application bundle manifest",
        ),
        "bundle_manifest_sha256": receipt_core["bundle_manifest_sha256"],
        "bundle_inventory_sha256": receipt_core["bundle_inventory_sha256"],
        "validator": project_relative_path(
            Path(__file__).resolve().parents[3],
            validator,
            "application validator",
        ),
        "validator_sha256": receipt_core["validator_sha256"],
        "validation_receipt": {
            **receipt_core,
            "receipt_sha256": canonical_sha256(receipt_core),
        },
    }


def validate_fpga_release_package(
    files: list[dict[str, Any]],
    project_root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
) -> dict[str, Any] | None:
    if manifest["fpga"]["enabled"] is not True:
        return None
    fpga_files = [
        item for item in files
        if item["component"] in {"fpga", "fpga-profile"}
    ]
    dtbo_files = [
        item for item in files
        if item["component"] in {"device-tree", "device-tree-profile"}
    ]
    # Prefer the permanent-profile copies when both app and profile files are
    # mapped. They must come from the validated release package and are hash-
    # matched to the app copies by validate_payload_against_contract().
    fpga_files = sorted(
        fpga_files,
        key=lambda item: item["component"] != "fpga-profile",
    )[:1]
    dtbo_files = sorted(
        dtbo_files,
        key=lambda item: item["component"] != "device-tree-profile",
    )[:1]
    contract_files = [
        item
        for item in files
        if item["component"] == "configuration"
        and PurePosixPath(item["destination"]).name == "register-contract.json"
    ]
    if len(fpga_files) != 1 or len(dtbo_files) != 1 or len(contract_files) != 1:
        raise PlanError(
            "FPGA release package requires one bitstream, one device tree and "
            "one register-contract payload"
        )
    local_paths = [
        (project_root / item["source"]).resolve()
        for item in (fpga_files[0], dtbo_files[0], contract_files[0])
    ]
    package_dir = local_paths[0].parent
    if any(path.parent != package_dir for path in local_paths[1:]):
        raise PlanError(
            "FPGA bitstream, device tree and register contract must come from "
            "the same validated package directory"
        )
    package_manifest_path = package_dir / "package-manifest.json"
    build_evidence_path = package_dir / "build-evidence.json"
    if not package_manifest_path.is_file() or not build_evidence_path.is_file():
        raise PlanError(
            "FPGA source directory lacks package-manifest.json or "
            "build-evidence.json"
        )
    package_tool = (
        Path(__file__).resolve().parents[3]
        / "skills"
        / "build-red-pitaya-fpga"
        / "scripts"
        / "fpga_package.py"
    )
    if not package_tool.is_file():
        raise PlanError(f"FPGA package validator is missing: {package_tool}")
    result = subprocess.run(
        [
            sys.executable,
            str(package_tool),
            "validate",
            "--manifest",
            str(manifest_path),
            "--package-dir",
            str(package_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        details = (result.stdout + result.stderr).strip()
        raise PlanError(
            "FPGA package validation failed"
            + (f":\n{details}" if details else "")
        )
    package_manifest = load_object(package_manifest_path)
    if package_manifest.get("classification") != "release-candidate":
        raise PlanError(
            "deployment planning rejects synthetic/test-fixture FPGA packages; "
            "only a structurally valid release-candidate may enter a blocked plan"
        )
    if package_manifest.get("attestation") != "self-attested":
        raise PlanError("FPGA release candidate must declare self-attested provenance")
    if package_manifest.get("release_review_required") is not True:
        raise PlanError("FPGA release candidate must require independent release review")
    if package_manifest.get("loadable") is not False:
        raise PlanError(
            "offline FPGA package metadata must never claim load permission"
        )
    files_record = package_manifest.get("files")
    if not isinstance(files_record, dict):
        raise PlanError("FPGA package files record is invalid")
    for required_name in (
        manifest["fpga"]["artifact_name"],
        manifest["fpga"]["device_tree_name"],
        "register-contract.json",
        "build-evidence.json",
        "RELEASE_REVIEW_REQUIRED.txt",
    ):
        if required_name not in files_record:
            raise PlanError(f"FPGA package is missing {required_name!r}")
    return {
        "classification": "release-candidate",
        "attestation": "self-attested",
        "package_validation_passed": True,
        "package_declared_loadable": False,
        "release_review_required": True,
        "package_grants_load_permission": False,
        "package_manifest_path": project_relative_path(
            project_root,
            package_manifest_path,
            "FPGA package manifest",
        ),
        "package_manifest_sha256": sha256_file(package_manifest_path),
        "build_evidence_sha256": sha256_file(build_evidence_path),
        "register_contract_sha256": sha256_file(
            package_dir / "register-contract.json"
        ),
        "validator": project_relative_path(
            Path(__file__).resolve().parents[3],
            package_tool,
            "FPGA package validator",
        ),
        "validator_sha256": sha256_file(package_tool),
    }


def materialize_root(template: str, deployment_id: str, prefix: str) -> str:
    if "{deployment_id}" in template:
        value = template.replace("{deployment_id}", deployment_id)
    else:
        value = f"{template}-{deployment_id}"
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise PlanError(f"unsafe deployment root materialized from {template!r}")
    if not str(path).startswith(prefix) or str(path) in {"/tmp", "/root"}:
        raise PlanError(f"deployment root {value!r} is outside required prefix {prefix!r}")
    return str(path)


def build_plan(
    manifest_path: Path,
    report_path: Path,
    project_root: Path,
    file_map_path: Path,
    deployment_id: str,
) -> dict[str, Any]:
    if not DEPLOYMENT_ID_RE.fullmatch(deployment_id):
        raise PlanError(
            "deployment ID must match [A-Za-z0-9][A-Za-z0-9._-]{0,95}"
        )
    try:
        manifest = load_and_validate_manifest(manifest_path)
    except ContractError as exc:
        raise PlanError(str(exc)) from exc
    report = load_object(report_path)
    file_map = load_object(file_map_path)
    target_contract = manifest["target"]
    deployment_contract = manifest["deployment"]
    safety_contract = manifest["safety"]
    application_contract = manifest["application"]
    fpga_contract = manifest["fpga"]
    os_release = target_contract["os_release"]
    model = target_contract["model"]
    role = target_contract["role"]
    profile = load_os_profile(os_release)
    expected_fpga_artifact = profile["fpga_artifact_filename"]
    observed = verify_probe(report, os_release, model, role)
    files = expand_file_map(
        project_root, file_map, os_release, expected_fpga_artifact
    )
    validate_payload_against_contract(files, manifest, profile)
    application_provenance = validate_application_bundle(
        file_map,
        project_root,
        manifest_path,
        manifest,
    )
    fpga_provenance = validate_fpga_release_package(
        files,
        project_root,
        manifest_path,
        manifest,
    )
    if not files:
        raise PlanError("file map expanded to zero files")

    touches_opt = any(
        item["destination"].startswith("/opt/redpitaya/") for item in files
    )
    components = sorted({item["component"] for item in files})
    stage_root = materialize_root(
        deployment_contract["staging_root"], deployment_id, "/tmp/"
    )
    backup_root = materialize_root(
        deployment_contract["backup_root"], deployment_id, "/root/"
    )
    manifest_bytes = manifest_path.read_bytes()
    map_bytes = file_map_path.read_bytes()
    manifest_relative = project_relative_path(
        project_root, manifest_path, "manifest"
    )
    report_relative = project_relative_path(
        project_root, report_path, "probe report"
    )
    file_map_relative = project_relative_path(
        project_root, file_map_path, "file map"
    )

    steps = [
        {
            "id": "reprobe-identity",
            "phase": "preflight",
            "description": "Repeat read-only immutable identity and mount probe.",
            "required": True,
        },
        {
            "id": "authorize-stage",
            "phase": "preflight",
            "description": (
                "Record stage authorization before the first target write; "
                "use one routine-deployment authorization by default."
            ),
            "required": True,
        },
        {
            "id": "stage-files",
            "phase": "stage",
            "description": f"Transfer only planned files beneath {stage_root}.",
            "required": True,
        },
        {
            "id": "verify-stage-hashes",
            "phase": "stage",
            "description": "Compare every staged SHA-256, size and path with this plan.",
            "required": True,
        },
        {
            "id": "authorize-precommit-live-actions",
            "phase": "precommit",
            "description": (
                "Record precommit authorization before safe-stop or board-side "
                "backup; reuse routine-deployment evidence by default."
            ),
            "required": True,
        },
        {
            "id": "confirm-safe-stop",
            "phase": "commit",
            "description": "Invoke and independently verify the project safe-stop contract.",
            "required": True,
        },
        {
            "id": "backup-live-paths",
            "phase": "commit",
            "description": f"Back up exact live destinations beneath {backup_root}.",
            "required": True,
        },
    ]
    if fpga_contract["enabled"]:
        steps.append(
            {
                "id": "review-fpga-release",
                "phase": "precommit",
                "description": (
                    "Independently review the self-attested release candidate "
                    "and record a deployment-bound release-review receipt. "
                    "Package metadata grants no load permission."
                ),
                "required": True,
            }
        )
    if touches_opt:
        steps.extend(
            [
                {
                    "id": "remount-opt-redpitaya-rw",
                    "phase": "commit",
                    "description": "If originally read-only, remount only /opt/redpitaya read-write.",
                    "required": True,
                },
                {
                    "id": "restore-opt-redpitaya-mount",
                    "phase": "cleanup",
                    "description": "Restore /opt/redpitaya to its exact original ro/rw state.",
                    "required": True,
                },
            ]
        )
    steps.extend(
        [
            {
                "id": "install-verified-stage",
                "phase": "commit",
                "description": "Install from verified staging paths using planned destinations and modes.",
                "required": True,
            },
            {
                "id": "verify-live-hashes",
                "phase": "verify",
                "description": "Compare all live file hashes and modes with this plan.",
                "required": True,
            },
            {
                "id": "activate-configurations",
                "phase": "verify",
                "description": "Validate and activate only planned Nginx/service/FPGA components.",
                "required": True,
            },
            {
                "id": "acceptance-safe-first",
                "phase": "verify",
                "description": "Run identity, mount, service, status, safe-stop and authorized functional tests.",
                "required": True,
            },
            {
                "id": "rollback-readiness",
                "phase": "verify",
                "description": "Verify backup manifest and rollback procedure remain available.",
                "required": True,
            },
        ]
    )

    gates = {
        "host_key_verified": True,
        "identity_exact": True,
        "os_exact": True,
        "model_exact": True,
        "role_exact": True,
        "dry_run_reviewed": False,
        "stage_authorized": False,
        "stage_hashes_verified": False,
        "precommit_live_actions_authorized": False,
        "safe_stop_confirmed": False,
        "backup_verified": False,
        "commit_authorized": False,
        "fpga_release_reviewed": False,
        "fpga_load_authorized": False,
        "functional_output_test_authorized": False,
    }
    probe_report_sha256 = sha256_bytes(report_path.read_bytes())
    probe_report_size = report_path.stat().st_size
    gate_evidence: dict[str, dict[str, Any] | None] = {}
    for gate, state in gates.items():
        if state and gate in {
            "host_key_verified",
            "identity_exact",
            "os_exact",
            "model_exact",
            "role_exact",
        }:
            gate_evidence[gate] = {
                "kind": "probe-report",
                "gate": gate,
                "path": report_relative,
                "sha256": probe_report_sha256,
                "size": probe_report_size,
                "recorded_at_utc": observed["evidence"]["captured_at"],
                "summary": (
                    "Satisfied by the compatible, warning-free normalized "
                    "probe report."
                ),
            }
        else:
            gate_evidence[gate] = None

    plan = {
        "plan_schema_version": 1,
        "plan_kind": "red-pitaya-deployment-plan",
        "deployment_id": deployment_id,
        "plan_core_sha256": "",
        "project": {
            "manifest_path": manifest_relative,
            "manifest_sha256": sha256_bytes(manifest_bytes),
            "probe_report_path": report_relative,
            "probe_report_sha256": probe_report_sha256,
            "file_map_path": file_map_relative,
            "file_map_sha256": sha256_bytes(map_bytes),
        },
        "target": {
            "os_release": os_release,
            "model": model,
            "role": role,
            "expected_fpga_artifact": expected_fpga_artifact,
            "expected_fpga_device_tree": profile["fpga_device_tree_filename"],
            "expected_fpga_loader": profile["fpga_loader"],
            "connection": observed["connection"],
            "identity": observed["identity"],
            "mount": observed["mount"],
        },
        "contract": manifest,
        "scope": {
            "components": components,
            "touches_opt_redpitaya": touches_opt,
            "file_count": len(files),
            "byte_count": sum(item["size"] for item in files),
        },
        "paths": {
            "stage_root": stage_root,
            "backup_root": backup_root,
        },
        "probe": {
            "captured_at": observed["evidence"]["captured_at"],
            "probe_source": observed["evidence"]["probe_source"],
            "warnings": observed["warnings"],
        },
        "application_provenance": application_provenance,
        "fpga_provenance": fpga_provenance,
        "gates": gates,
        "gate_evidence": gate_evidence,
        "files": files,
        "steps": steps,
        "rollback": {
            "required_if_commit_starts": True,
            "preserve_safe_stop": True,
            "restore_replaced_files": True,
            "remove_new_files": True,
            "restore_original_mount_state": True,
            "rerun_acceptance": True,
        },
    }
    plan["plan_core_sha256"] = plan_core_sha256(plan)
    return plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a deterministic offline Red Pitaya deployment plan."
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--probe-report", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--file-map", required=True, type=Path)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = build_plan(
            args.manifest,
            args.probe_report,
            args.project_root,
            args.file_map,
            args.deployment_id,
        )
    except (OSError, PlanError, ContractError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    args.output.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        f"Created {args.output} with {len(plan['files'])} files; "
        "no board was contacted."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
