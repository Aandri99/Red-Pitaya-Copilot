#!/usr/bin/env python3
"""Normalize an offline Red Pitaya probe and compare it with a project manifest.

This program performs no network access and never modifies a target board.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import ipaddress
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from project_contract import (
    ContractError,
    load_and_validate_manifest,
    load_board_profile,
    load_os_profile,
    read_json_object,
)

SECRET_KEY_RE = re.compile(
    r"(password|passwd|secret|private[_-]?key|access[_-]?token|api[_-]?key)",
    re.IGNORECASE,
)
FINGERPRINT_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{20,}={0,2}$")
UTC_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}\.?$)"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$"
)


class ProbeError(ValueError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        return read_json_object(path)
    except ContractError as exc:
        raise ProbeError(str(exc)) from exc


def reject_secrets(value: Any, trail: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if SECRET_KEY_RE.search(key_text):
                raise ProbeError(
                    "probe/manifest contains a secret-like key at "
                    + ".".join((*trail, key_text))
                )
            reject_secrets(child, (*trail, key_text))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_secrets(child, (*trail, str(index)))
    elif isinstance(value, str) and "-----BEGIN " in value and "PRIVATE KEY-----" in value:
        raise ProbeError("probe/manifest contains private-key material")


def nested(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    value: Any = data
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def valid_ssh_sha256_fingerprint(value: str) -> bool:
    if not FINGERPRINT_RE.fullmatch(value):
        return False
    encoded = value.removeprefix("SHA256:")
    try:
        padding = "=" * ((4 - len(encoded) % 4) % 4)
        decoded = base64.b64decode(encoded + padding, validate=True)
    except (ValueError, binascii.Error):
        return False
    return len(decoded) == 32


def analyze(
    probe: dict[str, Any],
    manifest: dict[str, Any] | None,
    expected_role: str | None,
) -> tuple[dict[str, Any], int]:
    reject_secrets(probe)
    if probe.get("probe_schema_version") != 1:
        raise ProbeError("probe_schema_version must equal 1")

    errors: list[str] = []
    warnings: list[str] = []

    observed_os = str(
        nested(probe, "os", "release")
        or nested(probe, "redpitaya", "os_release")
        or ""
    ).strip()
    profiles_model = str(
        nested(probe, "board", "profiles_model_code") or ""
    ).strip()
    declared_model = str(nested(probe, "board", "model_code") or "").strip()
    monitor_model_name = str(
        nested(probe, "board", "monitor_model_name") or ""
    ).strip()
    monitor_model_id = str(
        nested(probe, "board", "monitor_model_id") or ""
    ).strip()
    monitor_fpga_version = str(
        nested(probe, "fpga", "monitor_fpga_version") or ""
    ).strip()
    observed_model = profiles_model
    roles = as_string_list(
        nested(probe, "identity", "roles")
        or nested(probe, "identity", "role")
    )
    host = str(nested(probe, "connection", "host", default="")).strip()
    address = str(nested(probe, "connection", "address", default="")).strip()
    username = str(
        nested(probe, "connection", "username", default="")
    ).strip()
    host_key_verified = nested(
        probe, "connection", "host_key_verified", default=False
    ) is True
    fingerprint = str(
        nested(probe, "connection", "ssh_host_key_sha256", default="")
    ).strip()
    hostname = str(nested(probe, "identity", "hostname", default="")).strip()
    machine_id = str(nested(probe, "identity", "machine_id", default="")).strip()
    serial = str(nested(probe, "identity", "serial", default="")).strip()
    macs = sorted(
        {
            item.lower()
            for item in as_string_list(nested(probe, "identity", "mac_addresses"))
        }
    )
    captured_at = str(probe.get("captured_at", "")).strip()
    probe_source = str(probe.get("probe_source", "")).strip()

    if not host and not address:
        errors.append("probe has neither connection.host nor connection.address")
    if host and not HOSTNAME_RE.fullmatch(host):
        errors.append("connection.host is not a valid DNS/mDNS hostname")
    if address:
        try:
            ipaddress.ip_address(address)
        except ValueError:
            errors.append("connection.address is not a valid IPv4/IPv6 address")
    if not USERNAME_RE.fullmatch(username):
        errors.append(
            "connection.username is missing or invalid; record the exact SSH account"
        )
    if not host_key_verified:
        errors.append("SSH host key is not independently marked as verified")
    if not valid_ssh_sha256_fingerprint(fingerprint):
        errors.append(
            "connection.ssh_host_key_sha256 must decode to exactly 32 bytes"
        )
    if not hostname:
        errors.append("identity.hostname is missing")
    if not machine_id:
        errors.append("identity.machine_id is missing")
    if not serial and not macs:
        errors.append("at least identity.serial or identity.mac_addresses is required")
    if not UTC_RE.fullmatch(captured_at):
        errors.append("captured_at must use exact UTC format YYYY-MM-DDTHH:MM:SSZ")
    else:
        try:
            datetime.strptime(captured_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            errors.append("captured_at is not a real UTC timestamp")
    if not probe_source or len(probe_source) > 200:
        errors.append("probe_source must be a non-empty string of at most 200 characters")
    if not observed_os:
        errors.append("observed Red Pitaya OS release is missing")
    if not profiles_model:
        errors.append(
            "board.profiles_model_code is missing; record the exact output of "
            "/opt/redpitaya/bin/profiles -f"
        )
    if declared_model and observed_model and declared_model != observed_model:
        errors.append(
            f"board.model_code={declared_model!r} disagrees with observed tool "
            f"result {observed_model!r}"
        )
    if not monitor_model_name:
        warnings.append(
            "board.monitor_model_name is missing; collect monitor -n as "
            "corroborating model evidence"
        )
    if not monitor_model_id:
        warnings.append(
            "board.monitor_model_id is missing; collect monitor -i as "
            "corroborating model evidence"
        )
    if not monitor_fpga_version:
        warnings.append(
            "fpga.monitor_fpga_version is missing; collect monitor -f as "
            "separate FPGA-version evidence"
        )
    expected_os = ""
    expected_model = ""
    manifest_role = ""
    manifest_hostname = ""
    expected_suffix = ""
    fpga_enabled = False
    if manifest is not None:
        reject_secrets(manifest)
        expected_os = str(nested(manifest, "target", "os_release") or "").strip()
        expected_model = str(nested(manifest, "target", "model") or "").strip()
        manifest_role = str(nested(manifest, "target", "role") or "").strip()
        manifest_hostname = str(
            nested(manifest, "target", "hostname") or ""
        ).strip()
        expected_suffix = re.sub(
            r"[^A-Fa-f0-9]",
            "",
            str(
                nested(manifest, "target", "expected_serial_or_mac_suffix")
                or ""
            ),
        ).lower()
        fpga_enabled = nested(manifest, "fpga", "enabled", default=False) is True
        if expected_os and observed_os and expected_os != observed_os:
            errors.append(
                f"OS mismatch: manifest expects {expected_os!r}, probe observed {observed_os!r}"
            )
        if expected_model and observed_model and expected_model != observed_model:
            errors.append(
                "model mismatch: manifest expects "
                f"{expected_model!r}, probe observed {observed_model!r}"
            )
        if manifest_hostname:
            identity_short = hostname.split(".", 1)[0].lower()
            expected_short = manifest_hostname.split(".", 1)[0].lower()
            connection_host = host.lower()
            if (
                connection_host != manifest_hostname.lower()
                and identity_short != expected_short
            ):
                errors.append(
                    f"hostname mismatch: manifest expects {manifest_hostname!r}, "
                    f"probe connection/identity observed {host!r}/{hostname!r}"
                )
        if expected_suffix:
            candidates = [
                re.sub(r"[^A-Fa-f0-9]", "", value).lower()
                for value in [serial, *macs]
            ]
            if not any(value.endswith(expected_suffix) for value in candidates):
                errors.append(
                    "neither observed serial nor MAC ends with manifest "
                    f"expected_serial_or_mac_suffix {expected_suffix!r}"
                )
    role_to_match = expected_role or manifest_role
    if expected_role and manifest_role and expected_role != manifest_role:
        errors.append(
            f"--expected-role {expected_role!r} conflicts with manifest "
            f"target.role {manifest_role!r}"
        )
    if role_to_match and role_to_match not in roles:
        errors.append(
            f"expected role {role_to_match!r} is absent from observed roles {roles!r}"
        )

    mount = nested(probe, "storage", "opt_redpitaya", default={})
    if not isinstance(mount, dict):
        mount = {}
    mount_target = str(mount.get("target", "")).strip()
    mount_source = str(mount.get("source", "")).strip()
    mount_fstype = str(mount.get("fstype", "")).strip().lower()
    mount_options = sorted(set(as_string_list(mount.get("options"))))
    if mount_target != "/opt/redpitaya":
        errors.append(
            "storage.opt_redpitaya.target must be the exact /opt/redpitaya mount"
        )
    if not mount_source:
        errors.append("storage.opt_redpitaya.source is missing")
    if not mount_fstype:
        errors.append("storage.opt_redpitaya.fstype is missing")
    elif mount_fstype not in {"vfat", "fat", "msdos"}:
        warnings.append(
            f"/opt/redpitaya filesystem is {mount_fstype!r}, not the expected VFAT family"
        )
    if not mount_options:
        errors.append("storage.opt_redpitaya.options is missing")
    if "ro" not in mount_options and "rw" not in mount_options:
        errors.append("/opt/redpitaya mount options do not state ro or rw")

    profile: dict[str, Any] = {}
    if observed_os:
        try:
            profile = load_os_profile(observed_os)
        except ContractError as exc:
            errors.append(str(exc))
    if observed_model:
        try:
            load_board_profile(observed_model)
        except ContractError as exc:
            errors.append(str(exc))
    loader_artifact = str(
        nested(probe, "fpga", "expected_artifact", default="")
    ).strip()
    loader_script = str(
        nested(probe, "fpga", "loader_script", default="")
    ).strip()
    artifact_value = profile.get("fpga_artifact_filename")
    expected_artifact = (
        str(artifact_value).strip()
        if isinstance(artifact_value, str) and artifact_value.strip()
        else None
    )
    expected_loader = str(profile.get("fpga_loader", ""))
    if expected_artifact and loader_artifact and loader_artifact != expected_artifact:
        errors.append(
            f"FPGA loader expects {loader_artifact!r}, but OS profile requires "
            f"{expected_artifact!r}"
        )
    if expected_artifact and not loader_artifact:
        message = (
            "probe did not record fpga.expected_artifact; inspect the live loader "
            "script before an FPGA deployment"
        )
        (errors if fpga_enabled else warnings).append(message)
    if expected_loader and loader_script and loader_script != expected_loader:
        errors.append(
            f"FPGA loader path {loader_script!r} differs from OS profile "
            f"{expected_loader!r}"
        )
    if expected_loader and not loader_script:
        message = (
            f"probe did not record FPGA loader path; expected {expected_loader!r} "
            "from the selected OS profile"
        )
        (errors if fpga_enabled else warnings).append(message)

    normalized = {
        "report_schema_version": 1,
        "report_kind": "red-pitaya-probe-report",
        "compatible": not errors,
        "blocking_errors": errors,
        "warnings": warnings,
        "connection": {
            "host": host,
            "address": address,
            "username": username,
            "host_key_verified": host_key_verified,
            "ssh_host_key_sha256": fingerprint,
        },
        "identity": {
            "hostname": hostname,
            "machine_id": machine_id,
            "serial": serial,
            "mac_addresses": macs,
            "roles": roles,
        },
        "target": {
            "observed_os_release": observed_os,
            "observed_model": observed_model,
            "expected_os_release": expected_os,
            "expected_model": expected_model,
            "expected_role": role_to_match,
            "expected_hostname": manifest_hostname,
            "expected_serial_or_mac_suffix": expected_suffix,
            "role_exact": bool(role_to_match and role_to_match in roles),
            "profile": f"os-{observed_os}" if profile else "",
            "expected_fpga_artifact": expected_artifact,
            "expected_fpga_loader": expected_loader,
            "model_evidence": {
                "profiles_model_code": profiles_model,
                "monitor_model_name": monitor_model_name,
                "monitor_model_id": monitor_model_id,
            },
            "monitor_fpga_version": monitor_fpga_version,
        },
        "mount": {
            "target": mount_target,
            "source": mount_source,
            "fstype": mount_fstype,
            "options": mount_options,
            "originally_read_only": "ro" in mount_options,
        },
        "evidence": {
            "captured_at": captured_at,
            "probe_source": probe_source,
        },
    }
    return normalized, 0 if not errors else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Normalize an offline Red Pitaya probe and find target mismatches."
    )
    parser.add_argument("probe", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--expected-role")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        probe = read_json(args.probe)
        manifest = load_and_validate_manifest(args.manifest) if args.manifest else None
        report, code = analyze(probe, manifest, args.expected_role)
    except (ProbeError, ContractError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(rendered)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
