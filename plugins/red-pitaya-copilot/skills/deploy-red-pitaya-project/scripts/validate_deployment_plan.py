#!/usr/bin/env python3
"""Validate a Red Pitaya deployment plan without contacting a board."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import generate_deployment_plan as plan_generator
from project_contract import (
    ContractError,
    load_os_profile,
    validate_manifest_object,
)


REQUIRED_STEPS = {
    "reprobe-identity",
    "authorize-stage",
    "stage-files",
    "verify-stage-hashes",
    "authorize-precommit-live-actions",
    "confirm-safe-stop",
    "backup-live-paths",
    "install-verified-stage",
    "verify-live-hashes",
    "activate-configurations",
    "acceptance-safe-first",
    "rollback-readiness",
}
ALL_GATES = {
    "host_key_verified",
    "identity_exact",
    "os_exact",
    "model_exact",
    "role_exact",
    "dry_run_reviewed",
    "stage_authorized",
    "stage_hashes_verified",
    "precommit_live_actions_authorized",
    "safe_stop_confirmed",
    "backup_verified",
    "commit_authorized",
    "fpga_release_reviewed",
    "fpga_load_authorized",
    "functional_output_test_authorized",
}
SECRET_KEY_RE = re.compile(
    r"(password|passwd|secret|private[_-]?key|access[_-]?token|api[_-]?key)",
    re.IGNORECASE,
)
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MODE_RE = re.compile(r"^0[0-7]{3}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
UTC_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
AUTHORIZATION_SCOPE = {
    "stage_authorized": "stage",
    "precommit_live_actions_authorized": "precommit-live-actions",
    "commit_authorized": "commit",
    "fpga_release_reviewed": "fpga-release-review",
    "fpga_load_authorized": "fpga-load",
    "functional_output_test_authorized": "physical-output-test",
}
ROUTINE_DEPLOYMENT_GATES = {
    "stage_authorized",
    "precommit_live_actions_authorized",
    "commit_authorized",
}
ROUTINE_DEPLOYMENT_EVIDENCE_GATE = "routine_deployment_authorized"
ROUTINE_DEPLOYMENT_SCOPE = "deploy"
PLAN_TOP_LEVEL_KEYS = {
    "plan_schema_version",
    "plan_kind",
    "deployment_id",
    "plan_core_sha256",
    "project",
    "target",
    "contract",
    "scope",
    "paths",
    "probe",
    "application_provenance",
    "fpga_provenance",
    "gates",
    "gate_evidence",
    "files",
    "steps",
    "rollback",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid_ssh_fingerprint(value: str) -> bool:
    if not value.startswith("SHA256:"):
        return False
    encoded = value.removeprefix("SHA256:")
    try:
        padding = "=" * ((4 - len(encoded) % 4) % 4)
        decoded = base64.b64decode(encoded + padding, validate=True)
    except (ValueError, binascii.Error):
        return False
    return len(decoded) == 32


def scan_for_secrets(value: Any, errors: list[str], trail: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if SECRET_KEY_RE.search(key_text):
                errors.append("secret-like key: " + ".".join((*trail, key_text)))
            scan_for_secrets(child, errors, (*trail, key_text))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_for_secrets(child, errors, (*trail, str(index)))
    elif isinstance(value, str):
        lowered = value.lower()
        if "-----begin " in lowered and "private key-----" in lowered:
            errors.append("private-key material at " + ".".join(trail))
        if re.search(r"\b(pass(word|wd)?|secret|token)\s*[:=]\s*\S+", value, re.I):
            errors.append("probable embedded credential at " + ".".join(trail))


def valid_remote_path(value: str) -> bool:
    try:
        plan_generator.ensure_remote_path(value)
    except plan_generator.PlanError:
        return False
    return True


def read_project_file(
    project_root: Path,
    relative_text: str,
    label: str,
    errors: list[str],
) -> Path | None:
    relative = Path(relative_text)
    if relative.is_absolute() or ".." in relative.parts:
        errors.append(f"{label} path is unsafe")
        return None
    candidate = (project_root / relative).resolve()
    try:
        candidate.relative_to(project_root.resolve())
    except ValueError:
        errors.append(f"{label} path escapes project root")
        return None
    if candidate.is_symlink() or not candidate.is_file():
        errors.append(f"{label} does not exist as a non-symlink regular file")
        return None
    return candidate


def load_json_object(path: Path, label: str, errors: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"{label} cannot be parsed: {exc}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{label} must contain a JSON object")
        return None
    return value


def materialize_root(template: str, deployment_id: str, prefix: str) -> str:
    if "{deployment_id}" in template:
        value = template.replace("{deployment_id}", deployment_id)
    else:
        value = f"{template}-{deployment_id}"
    path = PurePosixPath(value)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or not str(path).startswith(prefix)
        or str(path) in {"/tmp", "/root"}
    ):
        return ""
    return str(path)


def validate_probe_semantics(
    plan: dict[str, Any],
    report: dict[str, Any],
    report_path: Path,
    errors: list[str],
) -> None:
    if report.get("report_schema_version") != 1:
        errors.append("normalized probe report schema_version is not 1")
    if report.get("report_kind") != "red-pitaya-probe-report":
        errors.append("normalized probe report kind is invalid")
    if report.get("compatible") is not True:
        errors.append("normalized probe report is not compatible")
    if report.get("blocking_errors") != []:
        errors.append("normalized probe report contains blocking errors")
    if report.get("warnings") != []:
        errors.append("normalized probe report contains unresolved warnings")

    report_target = report.get("target")
    report_connection = report.get("connection")
    report_identity = report.get("identity")
    report_mount = report.get("mount")
    report_evidence = report.get("evidence")
    target = plan.get("target")
    probe = plan.get("probe")
    if not all(
        isinstance(value, dict)
        for value in (
            report_target,
            report_connection,
            report_identity,
            report_mount,
            report_evidence,
            target,
            probe,
        )
    ):
        errors.append(
            "normalized probe semantic comparison requires target, connection, "
            "identity, mount and evidence objects"
        )
        return

    if target.get("connection") != report_connection:
        errors.append("plan target.connection differs from normalized probe report")
    if target.get("identity") != report_identity:
        errors.append("plan target.identity differs from normalized probe report")
    if target.get("mount") != report_mount:
        errors.append("plan target.mount differs from normalized probe report")
    if target.get("os_release") != report_target.get("observed_os_release"):
        errors.append("plan target OS differs from normalized probe observation")
    if report_target.get("expected_os_release") != target.get("os_release"):
        errors.append("probe expected OS differs from plan target OS")
    if target.get("model") != report_target.get("observed_model"):
        errors.append("plan target model differs from normalized probe observation")
    if report_target.get("expected_model") != target.get("model"):
        errors.append("probe expected model differs from plan target model")
    if target.get("role") != report_target.get("expected_role"):
        errors.append("plan target role differs from normalized probe report")
    if report_target.get("role_exact") is not True:
        errors.append("normalized probe does not assert exact role")
    if (
        target.get("expected_fpga_artifact")
        != report_target.get("expected_fpga_artifact")
    ):
        errors.append(
            "plan expected FPGA artifact differs from normalized probe report"
        )
    if (
        target.get("expected_fpga_loader")
        != report_target.get("expected_fpga_loader")
    ):
        errors.append(
            "plan expected FPGA loader differs from normalized probe report"
        )
    contract_target = (plan.get("contract") or {}).get("target")
    if isinstance(contract_target, dict):
        if report_target.get("expected_hostname", "") != contract_target.get(
            "hostname",
            "",
        ):
            errors.append(
                "normalized probe expected hostname differs from the contract"
            )
        expected_suffix = re.sub(
            r"[^A-Fa-f0-9]",
            "",
            str(contract_target.get("expected_serial_or_mac_suffix", "")),
        ).lower()
        if (
            report_target.get("expected_serial_or_mac_suffix", "")
            != expected_suffix
        ):
            errors.append(
                "normalized probe serial/MAC suffix differs from the contract"
            )
    if probe.get("captured_at") != report_evidence.get("captured_at"):
        errors.append("plan probe.captured_at differs from normalized probe evidence")
    if probe.get("probe_source") != report_evidence.get("probe_source"):
        errors.append("plan probe.probe_source differs from normalized probe evidence")
    if probe.get("warnings") != report.get("warnings"):
        errors.append("plan probe.warnings differs from normalized probe report")
    captured_at = str(report_evidence.get("captured_at", ""))
    try:
        datetime.strptime(captured_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        errors.append("normalized probe capture time is not a real UTC timestamp")
    if not str(report_evidence.get("probe_source", "")).strip():
        errors.append("normalized probe source is missing")
    if not valid_ssh_fingerprint(
        str(report_connection.get("ssh_host_key_sha256", ""))
    ):
        errors.append("normalized probe SSH fingerprint is malformed")
    if report_connection.get("host_key_verified") is not True:
        errors.append("normalized probe does not verify the SSH host key")

    mount_options = report_mount.get("options")
    if report_mount.get("target") != "/opt/redpitaya":
        errors.append("normalized probe mount target is not /opt/redpitaya")
    if not str(report_mount.get("source", "")).strip():
        errors.append("normalized probe mount source is missing")
    if not str(report_mount.get("fstype", "")).strip():
        errors.append("normalized probe mount filesystem type is missing")
    if not isinstance(mount_options, list):
        errors.append("normalized probe mount options must be an array")
    else:
        expected_ro = "ro" in mount_options
        if report_mount.get("originally_read_only") is not expected_ro:
            errors.append(
                "normalized probe originally_read_only disagrees with mount options"
            )

    project_record = plan.get("project")
    gate_evidence = plan.get("gate_evidence")
    if not isinstance(project_record, dict) or not isinstance(gate_evidence, dict):
        return
    report_hash = sha256_file(report_path)
    report_size = report_path.stat().st_size
    if project_record.get("probe_report_sha256") != report_hash:
        errors.append("project probe report hash differs from normalized probe bytes")
    for gate in (
        "host_key_verified",
        "identity_exact",
        "os_exact",
        "model_exact",
        "role_exact",
    ):
        receipt = gate_evidence.get(gate)
        if not isinstance(receipt, dict):
            continue
        if receipt.get("kind") != "probe-report":
            errors.append(f"gate_evidence.{gate} must be probe-report evidence")
        if receipt.get("path") != project_record.get("probe_report_path"):
            errors.append(f"gate_evidence.{gate}.path differs from normalized probe")
        if receipt.get("sha256") != report_hash:
            errors.append(f"gate_evidence.{gate}.sha256 differs from normalized probe")
        if receipt.get("size") != report_size:
            errors.append(f"gate_evidence.{gate}.size differs from normalized probe")
        if receipt.get("recorded_at_utc") != report_evidence.get("captured_at"):
            errors.append(
                f"gate_evidence.{gate}.recorded_at_utc differs from probe capture"
            )


def validate_authorization_record(
    record: dict[str, Any],
    gate: str,
    receipt: dict[str, Any],
    plan: dict[str, Any],
    errors: list[str],
) -> tuple[str | None, bool]:
    expected_keys = {
        "authorization_schema_version",
        "gate",
        "result",
        "recorded_at_utc",
        "authorization_scope",
        "deployment_id",
        "plan_core_sha256",
        "target_machine_id",
        "ssh_host_key_sha256",
        "approver_identity",
        "authorization_reference",
        "summary",
    }
    prefix = f"authorization evidence for {gate}"
    if set(record) != expected_keys:
        errors.append(f"{prefix} must contain exactly {sorted(expected_keys)}")
    if record.get("authorization_schema_version") != 2:
        errors.append(f"{prefix} schema_version must equal 2")
    routine_deployment = bool(
        gate in ROUTINE_DEPLOYMENT_GATES
        and record.get("gate") == ROUTINE_DEPLOYMENT_EVIDENCE_GATE
        and record.get("authorization_scope") == ROUTINE_DEPLOYMENT_SCOPE
    )
    if record.get("result") != "authorized" or (
        not routine_deployment and record.get("gate") != gate
    ):
        errors.append(f"{prefix} gate/result is invalid")
    if not routine_deployment and record.get("authorization_scope") != AUTHORIZATION_SCOPE[gate]:
        errors.append(f"{prefix} scope is invalid")
    if (
        not routine_deployment
        and record.get("recorded_at_utc") != receipt.get("recorded_at_utc")
    ):
        errors.append(f"{prefix} timestamp differs from gate receipt")
    timestamp = str(record.get("recorded_at_utc", ""))
    if not UTC_RE.fullmatch(timestamp):
        errors.append(f"{prefix} timestamp is invalid")
    else:
        try:
            authorization_time = datetime.strptime(
                timestamp,
                "%Y-%m-%dT%H:%M:%SZ",
            )
        except ValueError:
            errors.append(f"{prefix} timestamp is not a real UTC time")
        else:
            probe_time = str((plan.get("probe") or {}).get("captured_at", ""))
            try:
                captured = datetime.strptime(
                    probe_time,
                    "%Y-%m-%dT%H:%M:%SZ",
                )
            except ValueError:
                errors.append(f"{prefix} cannot compare invalid probe timestamp")
            else:
                if authorization_time < captured:
                    errors.append(f"{prefix} predates the normalized probe")
            if routine_deployment:
                receipt_time_text = str(receipt.get("recorded_at_utc", ""))
                try:
                    receipt_time = datetime.strptime(
                        receipt_time_text,
                        "%Y-%m-%dT%H:%M:%SZ",
                    )
                except ValueError:
                    errors.append(f"{prefix} cannot compare invalid receipt timestamp")
                else:
                    if authorization_time > receipt_time:
                        errors.append(f"{prefix} postdates the gate receipt")
    if record.get("summary") != receipt.get("summary"):
        errors.append(f"{prefix} summary differs from gate receipt")
    if record.get("deployment_id") != plan.get("deployment_id"):
        errors.append(f"{prefix} deployment_id differs from plan")
    if record.get("plan_core_sha256") != plan.get("plan_core_sha256"):
        errors.append(f"{prefix} plan_core_sha256 differs from plan")
    target = plan.get("target")
    identity = target.get("identity") if isinstance(target, dict) else None
    connection = target.get("connection") if isinstance(target, dict) else None
    if record.get("target_machine_id") != (
        identity.get("machine_id") if isinstance(identity, dict) else None
    ):
        errors.append(f"{prefix} target_machine_id differs from plan")
    if record.get("ssh_host_key_sha256") != (
        connection.get("ssh_host_key_sha256")
        if isinstance(connection, dict)
        else None
    ):
        errors.append(f"{prefix} SSH fingerprint differs from plan")
    for key in ("approver_identity", "authorization_reference", "summary"):
        value = record.get(key)
        maximum = 500 if key == "summary" else 200
        if (
            not isinstance(value, str)
            or not value.strip()
            or value != value.strip()
            or len(value) > maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            errors.append(f"{prefix} {key} is invalid")
    reference = record.get("authorization_reference")
    return (reference if isinstance(reference, str) else None, routine_deployment)


def validate(
    plan: dict[str, Any],
    project_root: Path | None,
    require_commit_gates: bool,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    scan_for_secrets(plan, errors)
    if set(plan) != PLAN_TOP_LEVEL_KEYS:
        errors.append(
            "plan top-level keys must exactly match "
            f"{sorted(PLAN_TOP_LEVEL_KEYS)}"
        )
    if require_commit_gates and project_root is None:
        errors.append("--require-commit-gates requires --project-root")

    if plan.get("plan_schema_version") != 1:
        errors.append("plan_schema_version must equal 1")
    if plan.get("plan_kind") != "red-pitaya-deployment-plan":
        errors.append("plan_kind must be red-pitaya-deployment-plan")
    deployment_id = str(plan.get("deployment_id", ""))
    if not ID_RE.fullmatch(deployment_id):
        errors.append("invalid deployment_id")
    recorded_core_hash = str(plan.get("plan_core_sha256", ""))
    if not HASH_RE.fullmatch(recorded_core_hash):
        errors.append("plan_core_sha256 is invalid")
    elif recorded_core_hash != plan_generator.plan_core_sha256(plan):
        errors.append("plan_core_sha256 does not match the immutable plan core")

    target = plan.get("target")
    if not isinstance(target, dict):
        errors.append("target must be an object")
        target = {}
    target_keys = {
        "os_release",
        "model",
        "role",
        "expected_fpga_artifact",
        "expected_fpga_device_tree",
        "expected_fpga_loader",
        "connection",
        "identity",
        "mount",
    }
    if set(target) != target_keys:
        errors.append(f"target keys must exactly match {sorted(target_keys)}")
    os_release = str(target.get("os_release", ""))
    model = str(target.get("model", "")).strip()
    profile: dict[str, Any] = {}
    try:
        profile = load_os_profile(os_release)
    except ContractError as exc:
        errors.append(str(exc))
    if not model:
        errors.append("target.model is required")
    connection = target.get("connection")
    if not isinstance(connection, dict) or connection.get("host_key_verified") is not True:
        errors.append("target connection must confirm host_key_verified")
    elif set(connection) != {
        "host",
        "address",
        "username",
        "host_key_verified",
        "ssh_host_key_sha256",
    }:
        errors.append("target.connection contains missing or unknown fields")
    fingerprint = (
        str(connection.get("ssh_host_key_sha256", ""))
        if isinstance(connection, dict)
        else ""
    )
    if not valid_ssh_fingerprint(fingerprint):
        errors.append(
            "target SSH host-key fingerprint must decode to exactly 32 bytes"
        )
    username = (
        str(connection.get("username", ""))
        if isinstance(connection, dict)
        else ""
    )
    if not USERNAME_RE.fullmatch(username):
        errors.append("target SSH username is missing or invalid")
    identity = target.get("identity")
    if not isinstance(identity, dict):
        errors.append("target.identity must be an object")
    else:
        if set(identity) != {
            "hostname",
            "machine_id",
            "serial",
            "mac_addresses",
            "roles",
        }:
            errors.append("target.identity contains missing or unknown fields")
        if not identity.get("machine_id"):
            errors.append("target.identity.machine_id is required")
        if not identity.get("serial") and not identity.get("mac_addresses"):
            errors.append("target identity requires serial or MAC evidence")
    mount_record = target.get("mount")
    if not isinstance(mount_record, dict):
        errors.append("target.mount must be an object")
    else:
        mount_keys = {
            "target",
            "source",
            "fstype",
            "options",
            "originally_read_only",
        }
        if set(mount_record) != mount_keys:
            errors.append(
                f"target.mount keys must exactly match {sorted(mount_keys)}"
            )
        if mount_record.get("target") != "/opt/redpitaya":
            errors.append("target.mount.target must equal /opt/redpitaya")
        if not str(mount_record.get("source", "")).strip():
            errors.append("target.mount.source is required")

    paths = plan.get("paths")
    if not isinstance(paths, dict):
        errors.append("paths must be an object")
        paths = {}
    if set(paths) != {"stage_root", "backup_root"}:
        errors.append("paths keys must exactly match stage_root and backup_root")

    files = plan.get("files")
    if not isinstance(files, list) or not files:
        errors.append("files must be a non-empty array")
        files = []
    destinations: set[str] = set()
    calculated_bytes = 0
    fpga_count = 0
    for index, item in enumerate(files):
        label = f"files[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        file_keys = {
            "source",
            "destination",
            "component",
            "mode",
            "size",
            "sha256",
        }
        if set(item) != file_keys:
            errors.append(f"{label} keys must exactly match {sorted(file_keys)}")
        source = str(item.get("source", ""))
        destination = str(item.get("destination", ""))
        component = str(item.get("component", ""))
        digest = str(item.get("sha256", ""))
        mode = str(item.get("mode", ""))
        size = item.get("size")
        if Path(source).is_absolute() or ".." in Path(source).parts:
            errors.append(f"{label}.source is not a safe project-relative path")
        if not valid_remote_path(destination):
            errors.append(f"{label}.destination is unsafe: {destination!r}")
        if component not in plan_generator.ALLOWED_COMPONENTS:
            errors.append(f"{label}.component is invalid: {component!r}")
        if destination in destinations:
            errors.append(f"duplicate destination: {destination}")
        destinations.add(destination)
        if not HASH_RE.fullmatch(digest):
            errors.append(f"{label}.sha256 is invalid")
        if not MODE_RE.fullmatch(mode):
            errors.append(f"{label}.mode is invalid")
        if not isinstance(size, int) or size < 0:
            errors.append(f"{label}.size is invalid")
        else:
            calculated_bytes += size
        if component in {"fpga", "fpga-profile"}:
            if component == "fpga":
                fpga_count += 1
            expected = profile.get("fpga_artifact_filename")
            if expected and PurePosixPath(destination).name != expected:
                errors.append(
                    f"{label} FPGA destination must end in {expected!r} for {os_release}"
                )
        if project_root is not None and source:
            local = (project_root / source).resolve()
            try:
                local.relative_to(project_root.resolve())
            except ValueError:
                errors.append(f"{label}.source escapes project root")
                continue
            if not local.is_file():
                errors.append(f"{label}.source does not exist as a regular file")
            else:
                if local.stat().st_size != size:
                    errors.append(f"{label}.size no longer matches the local file")
                if sha256_file(local) != digest:
                    errors.append(f"{label}.sha256 no longer matches the local file")
    if fpga_count > 1:
        errors.append("plan contains more than one primary FPGA artifact")
    if require_commit_gates and any(
        isinstance(item, dict) and item.get("component") == "other"
        for item in files
    ):
        errors.append(
            "component 'other' is not permitted in a committable deployment plan"
        )

    scope = plan.get("scope")
    if not isinstance(scope, dict):
        errors.append("scope must be an object")
        scope = {}
    scope_keys = {
        "components",
        "touches_opt_redpitaya",
        "file_count",
        "byte_count",
    }
    if set(scope) != scope_keys:
        errors.append(f"scope keys must exactly match {sorted(scope_keys)}")
    if scope.get("file_count") != len(files):
        errors.append("scope.file_count does not match files")
    if scope.get("byte_count") != calculated_bytes:
        errors.append("scope.byte_count does not match files")
    calculated_components = sorted(
        {
            str(item.get("component"))
            for item in files
            if isinstance(item, dict)
        }
    )
    if scope.get("components") != calculated_components:
        errors.append("scope.components does not match files")
    touches_opt = any(
        isinstance(item, dict)
        and str(item.get("destination", "")).startswith("/opt/redpitaya/")
        for item in files
    )
    if scope.get("touches_opt_redpitaya") is not touches_opt:
        errors.append("scope.touches_opt_redpitaya does not match destinations")

    steps = plan.get("steps")
    if not isinstance(steps, list):
        errors.append("steps must be an array")
        steps = []
    step_ids = {
        str(step.get("id"))
        for step in steps
        if isinstance(step, dict) and step.get("required") is True
    }
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"steps[{index}] must be an object")
        elif set(step) != {"id", "phase", "description", "required"}:
            errors.append(
                f"steps[{index}] keys must exactly match "
                "id, phase, description and required"
            )
    missing_steps = sorted(REQUIRED_STEPS - step_ids)
    if missing_steps:
        errors.append("missing required steps: " + ", ".join(missing_steps))
    if touches_opt:
        for required in {
            "remount-opt-redpitaya-rw",
            "restore-opt-redpitaya-mount",
        }:
            if required not in step_ids:
                errors.append(f"missing required /opt/redpitaya step: {required}")
    contract_for_steps = plan.get("contract")
    if (
        isinstance(contract_for_steps, dict)
        and isinstance(contract_for_steps.get("fpga"), dict)
        and contract_for_steps["fpga"].get("enabled") is True
        and "review-fpga-release" not in step_ids
    ):
        errors.append("missing required FPGA release-review step")

    rollback = plan.get("rollback")
    rollback_requirements = {
        "required_if_commit_starts",
        "preserve_safe_stop",
        "restore_replaced_files",
        "remove_new_files",
        "restore_original_mount_state",
        "rerun_acceptance",
    }
    if not isinstance(rollback, dict):
        errors.append("rollback must be an object")
    else:
        if set(rollback) != rollback_requirements:
            errors.append(
                "rollback keys must exactly match "
                f"{sorted(rollback_requirements)}"
            )
        for key in sorted(rollback_requirements):
            if rollback.get(key) is not True:
                errors.append(f"rollback.{key} must be true")

    gates = plan.get("gates")
    if not isinstance(gates, dict):
        errors.append("gates must be an object")
        gates = {}
    if set(gates) != ALL_GATES:
        errors.append(f"gates keys must exactly match {sorted(ALL_GATES)}")
    for key, value in gates.items():
        if not isinstance(value, bool):
            errors.append(f"gates.{key} must be a boolean")
    gate_prerequisites = {
        "dry_run_reviewed": {"role_exact"},
        "stage_authorized": {"dry_run_reviewed"},
        "stage_hashes_verified": {"stage_authorized"},
        "precommit_live_actions_authorized": {"stage_hashes_verified"},
        "safe_stop_confirmed": {"precommit_live_actions_authorized"},
        "backup_verified": {"safe_stop_confirmed"},
        "commit_authorized": {"backup_verified"},
        "fpga_release_reviewed": {"commit_authorized"},
        "fpga_load_authorized": {
            "commit_authorized",
            "fpga_release_reviewed",
        },
        "functional_output_test_authorized": {"commit_authorized"},
    }
    for gate, prerequisites in gate_prerequisites.items():
        if gates.get(gate) is True:
            missing = sorted(
                prerequisite
                for prerequisite in prerequisites
                if gates.get(prerequisite) is not True
            )
            if missing:
                errors.append(
                    f"gates.{gate} is true before prerequisites: "
                    + ", ".join(missing)
                )
    evidence_gates = {
        "host_key_verified",
        "identity_exact",
        "os_exact",
        "model_exact",
    }
    for key in sorted(evidence_gates):
        if gates.get(key) is not True:
            errors.append(f"evidence gate {key} is not true")
    if gates.get("role_exact") is not True:
        warnings.append("role_exact is false; do not stage until role is verified")
    if require_commit_gates:
        for key in {
            "role_exact",
            "dry_run_reviewed",
            "stage_authorized",
            "stage_hashes_verified",
            "precommit_live_actions_authorized",
            "safe_stop_confirmed",
            "backup_verified",
            "commit_authorized",
        }:
            if gates.get(key) is not True:
                errors.append(f"commit gate {key} is not true")

    project_record = plan.get("project")
    if not isinstance(project_record, dict):
        errors.append("project must be an object")
        project_record = {}
    project_keys = {
        "manifest_path",
        "manifest_sha256",
        "probe_report_path",
        "probe_report_sha256",
        "file_map_path",
        "file_map_sha256",
    }
    if set(project_record) != project_keys:
        errors.append(f"project keys must exactly match {sorted(project_keys)}")
    probe_record = plan.get("probe")
    if not isinstance(probe_record, dict):
        errors.append("probe must be an object")
        probe_record = {}
    if set(probe_record) != {"captured_at", "probe_source", "warnings"}:
        errors.append(
            "probe keys must exactly match captured_at, probe_source and warnings"
        )
    if not UTC_RE.fullmatch(str(probe_record.get("captured_at", ""))):
        errors.append("probe.captured_at must use YYYY-MM-DDTHH:MM:SSZ")
    if not str(probe_record.get("probe_source", "")).strip():
        errors.append("probe.probe_source is required")
    if probe_record.get("warnings") != []:
        errors.append("probe.warnings must be an empty resolved list")

    gate_evidence = plan.get("gate_evidence")
    if not isinstance(gate_evidence, dict):
        errors.append("gate_evidence must be an object")
        gate_evidence = {}
    if set(gate_evidence) != set(gates):
        errors.append("gate_evidence keys must exactly match gates keys")
    receipt_keys = {
        "kind",
        "gate",
        "path",
        "sha256",
        "size",
        "recorded_at_utc",
        "summary",
    }
    authorization_hashes: dict[str, str] = {}
    authorization_paths: dict[str, str] = {}
    authorization_references: dict[str, tuple[str, bool]] = {}
    for gate, state in gates.items():
        receipt = gate_evidence.get(gate)
        if state is True:
            if not isinstance(receipt, dict):
                errors.append(f"gate_evidence.{gate} is required when gate is true")
                continue
            if set(receipt) != receipt_keys:
                errors.append(
                    f"gate_evidence.{gate} must contain exactly "
                    f"{sorted(receipt_keys)}"
                )
            if receipt.get("gate") != gate:
                errors.append(f"gate_evidence.{gate}.gate must equal {gate!r}")
            if receipt.get("kind") not in {"probe-report", "operator-evidence"}:
                errors.append(f"gate_evidence.{gate}.kind is invalid")
            evidence_path_text = str(receipt.get("path", ""))
            evidence_path = Path(evidence_path_text)
            if evidence_path.is_absolute() or ".." in evidence_path.parts:
                errors.append(f"gate_evidence.{gate}.path is unsafe")
            evidence_hash = str(receipt.get("sha256", ""))
            if not HASH_RE.fullmatch(evidence_hash):
                errors.append(f"gate_evidence.{gate}.sha256 is invalid")
            evidence_size = receipt.get("size")
            if not isinstance(evidence_size, int) or evidence_size < 1:
                errors.append(f"gate_evidence.{gate}.size is invalid")
            if not UTC_RE.fullmatch(str(receipt.get("recorded_at_utc", ""))):
                errors.append(
                    f"gate_evidence.{gate}.recorded_at_utc is invalid"
                )
            if not str(receipt.get("summary", "")).strip():
                errors.append(f"gate_evidence.{gate}.summary is required")
            if receipt.get("kind") == "probe-report":
                if evidence_path_text != project_record.get("probe_report_path"):
                    errors.append(
                        f"gate_evidence.{gate}.path differs from probe report"
                    )
                if evidence_hash != project_record.get("probe_report_sha256"):
                    errors.append(
                        f"gate_evidence.{gate}.sha256 differs from probe report"
                    )
            if gate in AUTHORIZATION_SCOPE:
                if receipt.get("kind") != "operator-evidence":
                    errors.append(
                        f"gate_evidence.{gate}.kind must be operator-evidence"
                    )
                if (
                    evidence_hash in authorization_hashes
                    and not {
                        gate,
                        authorization_hashes[evidence_hash],
                    }.issubset(ROUTINE_DEPLOYMENT_GATES)
                ):
                    errors.append(
                        f"authorization evidence hash is reused by {gate} and "
                        f"{authorization_hashes[evidence_hash]}"
                    )
                else:
                    authorization_hashes[evidence_hash] = gate
                if (
                    evidence_path_text in authorization_paths
                    and not {
                        gate,
                        authorization_paths[evidence_path_text],
                    }.issubset(ROUTINE_DEPLOYMENT_GATES)
                ):
                    errors.append(
                        f"authorization evidence path is reused by {gate} and "
                        f"{authorization_paths[evidence_path_text]}"
                    )
                else:
                    authorization_paths[evidence_path_text] = gate
            if project_root is not None and not evidence_path.is_absolute():
                local_evidence = (project_root / evidence_path).resolve()
                try:
                    local_evidence.relative_to(project_root.resolve())
                except ValueError:
                    errors.append(f"gate_evidence.{gate}.path escapes project root")
                else:
                    if not local_evidence.is_file():
                        errors.append(f"gate_evidence.{gate}.path does not exist")
                    else:
                        if local_evidence.stat().st_size != evidence_size:
                            errors.append(
                                f"gate_evidence.{gate}.size no longer matches"
                            )
                        if sha256_file(local_evidence) != evidence_hash:
                            errors.append(
                                f"gate_evidence.{gate}.sha256 no longer matches"
                            )
                        elif gate in AUTHORIZATION_SCOPE:
                            authorization = load_json_object(
                                local_evidence,
                                f"authorization evidence for {gate}",
                                errors,
                            )
                            if authorization is not None:
                                reference, routine_deployment = validate_authorization_record(
                                    authorization,
                                    gate,
                                    receipt,
                                    plan,
                                    errors,
                                )
                                if reference:
                                    if reference in authorization_references:
                                        existing_gate, existing_routine = (
                                            authorization_references[reference]
                                        )
                                        if not (
                                            routine_deployment
                                            and existing_routine
                                            and gate in ROUTINE_DEPLOYMENT_GATES
                                            and existing_gate in ROUTINE_DEPLOYMENT_GATES
                                        ):
                                            errors.append(
                                                "authorization_reference is reused by "
                                                f"{gate} and {existing_gate}"
                                            )
                                    else:
                                        authorization_references[reference] = (
                                            gate,
                                            routine_deployment,
                                        )
        elif receipt is not None:
            errors.append(
                f"gate_evidence.{gate} must be null while gate is false"
            )

    contract = plan.get("contract")
    if not isinstance(contract, dict):
        errors.append("contract must be an object copied from redpitaya-project.json")
    else:
        try:
            validate_manifest_object(contract)
        except ContractError as exc:
            errors.append(str(exc))
        contract_target = contract.get("target")
        if not isinstance(contract_target, dict):
            errors.append("contract.target must be an object")
            contract_target = {}
        if contract_target.get("os_release") != os_release:
            errors.append("contract.target.os_release differs from plan target")
        if contract_target.get("model") != model:
            errors.append("contract.target.model differs from plan target")
        target_role = str(target.get("role", ""))
        if not target_role:
            errors.append("target.role is required")
        if contract_target.get("role") != target_role:
            errors.append("contract.target.role differs from plan target")
        project = contract.get("project")
        application = contract.get("application")
        fpga = contract.get("fpga")
        deployment = contract.get("deployment")
        safety = contract.get("safety")
        if not all(
            isinstance(item, dict)
            for item in (project, application, fpga, deployment, safety)
        ):
            errors.append(
                "contract project/application/fpga/deployment/safety must all be objects"
            )
        else:
            for index, item in enumerate(files):
                if not isinstance(item, dict):
                    continue
                try:
                    plan_generator.validate_component_destination(
                        item,
                        contract,
                        profile,
                    )
                except plan_generator.PlanError as exc:
                    errors.append(f"files[{index}] destination contract: {exc}")
            expected_stage = materialize_root(
                str(deployment.get("staging_root", "")), deployment_id, "/tmp/"
            )
            expected_backup = materialize_root(
                str(deployment.get("backup_root", "")), deployment_id, "/root/"
            )
            if not expected_stage or paths.get("stage_root") != expected_stage:
                errors.append(
                    "paths.stage_root is not derived from contract.deployment.staging_root"
                )
            if not expected_backup or paths.get("backup_root") != expected_backup:
                errors.append(
                    "paths.backup_root is not derived from contract.deployment.backup_root"
                )
            if deployment.get("mountpoint") != "/opt/redpitaya":
                errors.append("contract.deployment.mountpoint must equal /opt/redpitaya")
            if deployment.get("nginx_service") != "redpitaya_nginx.service":
                errors.append(
                    "contract.deployment.nginx_service must equal redpitaya_nginx.service"
                )
            if deployment.get("restore_read_only") is not True:
                errors.append("contract.deployment.restore_read_only must be true")
            if safety.get("output_default") != "disabled":
                errors.append("contract.safety.output_default must be disabled")
            if safety.get("physical_test_requires_authorization") is not True:
                errors.append(
                    "contract.safety.physical_test_requires_authorization must be true"
                )
            if safety.get("rollback_required") is not True:
                errors.append("contract.safety.rollback_required must be true")
            if not str(safety.get("stop_action", "")).strip():
                errors.append("contract.safety.stop_action is required")
            if fpga.get("enabled") is True and profile:
                if fpga.get("artifact_name") != profile["fpga_artifact_filename"]:
                    errors.append(
                        "contract.fpga.artifact_name conflicts with OS profile"
                    )
                if (
                    fpga.get("device_tree_name")
                    != profile["fpga_device_tree_filename"]
                ):
                    errors.append(
                        "contract.fpga.device_tree_name conflicts with OS profile"
                    )
            application_provenance = plan.get("application_provenance")
            if application.get("enabled") is True:
                if not isinstance(application_provenance, dict):
                    errors.append(
                        "application_provenance is required when "
                        "contract.application.enabled is true"
                    )
                else:
                    application_keys = {
                        "source_root",
                        "destination_root",
                        "bundle_manifest_path",
                        "bundle_manifest_sha256",
                        "bundle_inventory_sha256",
                        "validator",
                        "validator_sha256",
                        "validation_receipt",
                    }
                    if set(application_provenance) != application_keys:
                        errors.append(
                            "application_provenance keys must exactly match "
                            f"{sorted(application_keys)}"
                        )
                    for key in (
                        "bundle_manifest_sha256",
                        "bundle_inventory_sha256",
                        "validator_sha256",
                    ):
                        if not HASH_RE.fullmatch(
                            str(application_provenance.get(key, ""))
                        ):
                            errors.append(
                                f"application_provenance.{key} is invalid"
                            )
                    expected_app_validator = (
                        Path(__file__).resolve().parents[3]
                        / "skills"
                        / "build-red-pitaya-app"
                        / "scripts"
                        / "validate_red_pitaya_app.py"
                    )
                    if (
                        application_provenance.get("validator")
                        != "skills/build-red-pitaya-app/scripts/"
                        "validate_red_pitaya_app.py"
                    ):
                        errors.append(
                            "application_provenance.validator is not the bundled "
                            "app validator"
                        )
                    if (
                        not expected_app_validator.is_file()
                        or sha256_file(expected_app_validator)
                        != application_provenance.get("validator_sha256")
                    ):
                        errors.append(
                            "application validator is missing or hash-mismatched"
                        )
                    receipt = application_provenance.get("validation_receipt")
                    receipt_keys = {
                        "kind",
                        "mode",
                        "result",
                        "error_count",
                        "warning_count",
                        "bundle_inventory_sha256",
                        "bundle_manifest_sha256",
                        "validator_sha256",
                        "receipt_sha256",
                    }
                    if not isinstance(receipt, dict) or set(receipt) != receipt_keys:
                        errors.append(
                            "application validation receipt keys are invalid"
                        )
                    else:
                        receipt_core = {
                            key: receipt[key]
                            for key in receipt_keys
                            if key != "receipt_sha256"
                        }
                        if (
                            receipt.get("kind")
                            != "deployment-ready-app-validation"
                            or receipt.get("mode") != "deployment-ready"
                            or receipt.get("result") != "pass"
                            or receipt.get("error_count") != 0
                            or receipt.get("warning_count") != 0
                        ):
                            errors.append(
                                "application validation receipt is not a clean "
                                "deployment-ready pass"
                            )
                        if (
                            receipt.get("receipt_sha256")
                            != plan_generator.canonical_sha256(receipt_core)
                        ):
                            errors.append(
                                "application validation receipt hash is invalid"
                            )
                        for key in (
                            "bundle_manifest_sha256",
                            "bundle_inventory_sha256",
                            "validator_sha256",
                        ):
                            if receipt.get(key) != application_provenance.get(key):
                                errors.append(
                                    f"application validation receipt {key} "
                                    "differs from provenance"
                                )
            elif application_provenance is not None:
                errors.append(
                    "application_provenance must be null when "
                    "contract.application.enabled is false"
                )
            fpga_provenance = plan.get("fpga_provenance")
            if fpga.get("enabled") is True:
                provenance_keys = {
                    "classification",
                    "attestation",
                    "package_validation_passed",
                    "package_declared_loadable",
                    "release_review_required",
                    "package_grants_load_permission",
                    "package_manifest_path",
                    "package_manifest_sha256",
                    "build_evidence_sha256",
                    "register_contract_sha256",
                    "validator",
                    "validator_sha256",
                }
                if not isinstance(fpga_provenance, dict):
                    errors.append(
                        "fpga_provenance is required when contract.fpga.enabled is true"
                    )
                else:
                    if set(fpga_provenance) != provenance_keys:
                        errors.append(
                            "fpga_provenance must contain exactly "
                            f"{sorted(provenance_keys)}"
                        )
                    if (
                        fpga_provenance.get("classification")
                        != "release-candidate"
                    ):
                        errors.append(
                            "fpga_provenance must be a release-candidate"
                        )
                    if fpga_provenance.get("attestation") != "self-attested":
                        errors.append(
                            "fpga_provenance.attestation must be self-attested"
                        )
                    if fpga_provenance.get("package_validation_passed") is not True:
                        errors.append(
                            "fpga_provenance.package_validation_passed must be true"
                        )
                    if (
                        fpga_provenance.get("package_declared_loadable")
                        is not False
                    ):
                        errors.append(
                            "offline FPGA package must declare loadable=false"
                        )
                    if fpga_provenance.get("release_review_required") is not True:
                        errors.append(
                            "fpga_provenance.release_review_required must be true"
                        )
                    if (
                        fpga_provenance.get("package_grants_load_permission")
                        is not False
                    ):
                        errors.append(
                            "FPGA package metadata must never grant load permission"
                        )
                    for key in (
                        "package_manifest_sha256",
                        "build_evidence_sha256",
                        "register_contract_sha256",
                        "validator_sha256",
                    ):
                        if not HASH_RE.fullmatch(
                            str(fpga_provenance.get(key, ""))
                        ):
                            errors.append(f"fpga_provenance.{key} is invalid")
                    expected_fpga_validator = (
                        Path(__file__).resolve().parents[3]
                        / "skills"
                        / "build-red-pitaya-fpga"
                        / "scripts"
                        / "fpga_package.py"
                    )
                    expected_validator_text = (
                        "skills/build-red-pitaya-fpga/scripts/fpga_package.py"
                    )
                    if fpga_provenance.get("validator") != expected_validator_text:
                        errors.append(
                            "fpga_provenance.validator is not the bundled "
                            "FPGA package validator"
                        )
                    if (
                        not expected_fpga_validator.is_file()
                        or sha256_file(expected_fpga_validator)
                        != fpga_provenance.get("validator_sha256")
                    ):
                        errors.append(
                            "FPGA package validator is missing or hash-mismatched"
                        )
                    provenance_path = Path(
                        str(fpga_provenance.get("package_manifest_path", ""))
                    )
                    if provenance_path.is_absolute() or ".." in provenance_path.parts:
                        errors.append(
                            "fpga_provenance.package_manifest_path is unsafe"
                        )
                    elif project_root is not None:
                        local_package_manifest = (
                            project_root / provenance_path
                        ).resolve()
                        try:
                            local_package_manifest.relative_to(
                                project_root.resolve()
                            )
                        except ValueError:
                            errors.append(
                                "fpga_provenance.package_manifest_path escapes "
                                "project root"
                            )
                        else:
                            if not local_package_manifest.is_file():
                                errors.append(
                                    "fpga_provenance package manifest does not exist"
                                )
                            else:
                                if (
                                    sha256_file(local_package_manifest)
                                    != fpga_provenance.get(
                                        "package_manifest_sha256"
                                    )
                                ):
                                    errors.append(
                                        "fpga_provenance package manifest hash "
                                        "no longer matches"
                                    )
                                try:
                                    local_package = json.loads(
                                        local_package_manifest.read_text(
                                            encoding="utf-8"
                                        )
                                    )
                                except (OSError, json.JSONDecodeError) as exc:
                                    errors.append(
                                        "FPGA package manifest cannot be parsed: "
                                        f"{exc}"
                                    )
                                else:
                                    if (
                                        local_package.get("classification")
                                        != "release-candidate"
                                    ):
                                        errors.append(
                                            "local FPGA package is not a "
                                            "release-candidate"
                                        )
                                    if (
                                        local_package.get("attestation")
                                        != "self-attested"
                                        or local_package.get(
                                            "release_review_required"
                                        )
                                        is not True
                                        or local_package.get("loadable")
                                        is not False
                                    ):
                                        errors.append(
                                            "local FPGA package trust flags are "
                                            "invalid or claim load permission"
                                        )
                                package_dir = local_package_manifest.parent
                                evidence_path = package_dir / "build-evidence.json"
                                contract_path = package_dir / "register-contract.json"
                                if (
                                    not evidence_path.is_file()
                                    or sha256_file(evidence_path)
                                    != fpga_provenance.get(
                                        "build_evidence_sha256"
                                    )
                                ):
                                    errors.append(
                                        "FPGA build evidence is missing or hash-mismatched"
                                    )
                                if (
                                    not contract_path.is_file()
                                    or sha256_file(contract_path)
                                    != fpga_provenance.get(
                                        "register_contract_sha256"
                                    )
                                ):
                                    errors.append(
                                        "FPGA register contract is missing or "
                                        "hash-mismatched"
                                    )
            elif fpga_provenance is not None:
                errors.append(
                    "fpga_provenance must be null when contract.fpga.enabled is false"
                )
            manifest_sha = str(
                (plan.get("project") or {}).get("manifest_sha256", "")
            )
            if not HASH_RE.fullmatch(manifest_sha):
                errors.append("project.manifest_sha256 is invalid")
            if gates.get("functional_output_test_authorized") is True and not deployment.get(
                "allow_live_output_test"
            ):
                errors.append(
                    "functional output test is authorized but contract disallows it"
                )
            if gates.get("fpga_load_authorized") is True and not fpga.get("enabled"):
                errors.append(
                    "FPGA load is authorized but contract.fpga.enabled is false"
                )
            if (
                gates.get("fpga_release_reviewed") is True
                and not fpga.get("enabled")
            ):
                errors.append(
                    "FPGA release review is recorded but contract.fpga.enabled is false"
                )

            if project_root is not None:
                if isinstance(project_record, dict):
                    candidates: dict[str, Path] = {}
                    for path_key, hash_key in (
                        ("manifest_path", "manifest_sha256"),
                        ("probe_report_path", "probe_report_sha256"),
                        ("file_map_path", "file_map_sha256"),
                    ):
                        relative_text = str(project_record.get(path_key, ""))
                        candidate = read_project_file(
                            project_root,
                            relative_text,
                            f"project.{path_key}",
                            errors,
                        )
                        if candidate is None:
                            continue
                        candidates[path_key] = candidate
                        expected_hash = str(project_record.get(hash_key, ""))
                        if sha256_file(candidate) != expected_hash:
                            errors.append(
                                f"project.{hash_key} no longer matches {path_key}"
                            )
                    local_manifest = candidates.get("manifest_path")
                    local_probe = candidates.get("probe_report_path")
                    local_file_map = candidates.get("file_map_path")
                    manifest_object = (
                        load_json_object(
                            local_manifest,
                            "project manifest",
                            errors,
                        )
                        if local_manifest is not None
                        else None
                    )
                    if manifest_object is not None and manifest_object != contract:
                        errors.append(
                            "embedded contract differs from project manifest"
                        )
                    probe_object = (
                        load_json_object(
                            local_probe,
                            "normalized probe report",
                            errors,
                        )
                        if local_probe is not None
                        else None
                    )
                    if probe_object is not None and local_probe is not None:
                        validate_probe_semantics(
                            plan,
                            probe_object,
                            local_probe,
                            errors,
                        )
                    file_map_object = (
                        load_json_object(
                            local_file_map,
                            "deployment file map",
                            errors,
                        )
                        if local_file_map is not None
                        else None
                    )

                    if (
                        application.get("enabled") is True
                        and file_map_object is not None
                        and local_manifest is not None
                    ):
                        try:
                            observed_app_provenance = (
                                plan_generator.validate_application_bundle(
                                    file_map_object,
                                    project_root,
                                    local_manifest,
                                    contract,
                                )
                            )
                        except (
                            OSError,
                            plan_generator.PlanError,
                            ContractError,
                        ) as exc:
                            errors.append(
                                "application deployment-ready provenance "
                                f"revalidation failed: {exc}"
                            )
                        else:
                            if observed_app_provenance != application_provenance:
                                errors.append(
                                    "application_provenance differs from a fresh "
                                    "deployment-ready app validation"
                                )

                    if file_map_object is not None and local_manifest is not None:
                        fresh_files: list[dict[str, Any]] | None = None
                        try:
                            fresh_files = plan_generator.expand_file_map(
                                project_root,
                                file_map_object,
                                os_release,
                                str(profile.get("fpga_artifact_filename", "")),
                            )
                            plan_generator.validate_payload_against_contract(
                                fresh_files,
                                contract,
                                profile,
                            )
                        except (
                            OSError,
                            plan_generator.PlanError,
                            ContractError,
                        ) as exc:
                            errors.append(
                                f"file-map inventory revalidation failed: {exc}"
                            )
                        else:
                            if fresh_files != files:
                                errors.append(
                                    "hash-bound file-map inventory differs from "
                                    "plan.files"
                                )
                            if (
                                require_commit_gates
                                and fpga.get("enabled") is True
                            ):
                                try:
                                    observed_fpga_provenance = (
                                        plan_generator.validate_fpga_release_package(
                                            fresh_files,
                                            project_root,
                                            local_manifest,
                                            contract,
                                        )
                                    )
                                except (
                                    OSError,
                                    plan_generator.PlanError,
                                    ContractError,
                                ) as exc:
                                    errors.append(
                                        "FPGA release package revalidation failed: "
                                        f"{exc}"
                                    )
                                else:
                                    if observed_fpga_provenance != fpga_provenance:
                                        errors.append(
                                            "fpga_provenance differs from a fresh "
                                            "package validation"
                                        )
    if project_root is None:
        warnings.append(
            "project-root was not supplied; probe semantics, authorization files "
            "and bundle provenance were not revalidated"
        )

    return errors, warnings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate an offline Red Pitaya deployment plan."
    )
    parser.add_argument("plan", type=Path)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--require-commit-gates", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read valid plan JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(plan, dict):
        print("ERROR: plan must contain a JSON object", file=sys.stderr)
        return 2
    errors, warnings = validate(plan, args.project_root, args.require_commit_gates)
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        print(f"Plan validation failed with {len(errors)} error(s).", file=sys.stderr)
        return 2
    print(
        f"Plan is structurally valid ({len(plan.get('files', []))} files). "
        "This result does not authorize or execute deployment."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
