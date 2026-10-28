#!/usr/bin/env python3
"""Advance one deployment gate using a hash-bound local evidence file.

This tool is offline. It never contacts a board and never executes a deployment
step. It refuses in-place plan edits so every transition remains reviewable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import validate_deployment_plan


MUTABLE_GATES = {
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
PREREQUISITES = {
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
UTC_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
SECRET_RE = re.compile(
    r"(password|passwd|secret|private[_-]?key|access[_-]?token|api[_-]?key)",
    re.IGNORECASE,
)


class GateError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative_file(project_root: Path, path: Path) -> tuple[Path, str]:
    root = project_root.resolve()
    candidate = path.resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise GateError("evidence file must be inside project root") from exc
    if candidate.is_symlink() or not candidate.is_file():
        raise GateError("evidence must be a non-symlink regular file")
    size = candidate.stat().st_size
    if size < 1:
        raise GateError("evidence file must not be empty")
    if size > 50 * 1024 * 1024:
        raise GateError("evidence file exceeds the 50 MiB receipt limit")
    return candidate, relative.as_posix()


def validate_timestamp(value: str) -> None:
    if not UTC_RE.fullmatch(value):
        raise GateError("recorded-at-utc must use YYYY-MM-DDTHH:MM:SSZ")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise GateError(f"recorded-at-utc is not a real UTC timestamp: {exc}") from exc


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read valid {label} JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GateError(f"{label} must contain a JSON object")
    return value


def validate_authorization_evidence(
    path: Path,
    gate: str,
    recorded_at_utc: str,
    plan: dict[str, Any],
) -> dict[str, Any]:
    record = load_json(path, "authorization evidence")
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
    if set(record) != expected_keys:
        raise GateError(
            "authorization evidence must contain exactly "
            f"{sorted(expected_keys)}"
        )
    if record.get("authorization_schema_version") != 2:
        raise GateError("authorization_schema_version must equal 2")
    routine_deployment = bool(
        gate in ROUTINE_DEPLOYMENT_GATES
        and record.get("gate") == ROUTINE_DEPLOYMENT_EVIDENCE_GATE
        and record.get("authorization_scope") == ROUTINE_DEPLOYMENT_SCOPE
    )
    if record.get("result") != "authorized" or (
        not routine_deployment and record.get("gate") != gate
    ):
        raise GateError("authorization evidence gate/result does not authorize this gate")
    authorization_time_text = str(record.get("recorded_at_utc", ""))
    validate_timestamp(authorization_time_text)
    authorization_time = datetime.strptime(
        authorization_time_text,
        "%Y-%m-%dT%H:%M:%SZ",
    )
    transition_time = datetime.strptime(recorded_at_utc, "%Y-%m-%dT%H:%M:%SZ")
    if routine_deployment:
        if authorization_time > transition_time:
            raise GateError(
                "routine deployment authorization cannot postdate the transition"
            )
    elif authorization_time_text != recorded_at_utc:
        raise GateError(
            "authorization evidence recorded_at_utc must match the transition"
        )
    probe_time = str((plan.get("probe") or {}).get("captured_at", ""))
    try:
        if authorization_time < datetime.strptime(
            probe_time,
            "%Y-%m-%dT%H:%M:%SZ",
        ):
            raise GateError(
                "authorization timestamp predates the normalized probe capture"
            )
    except ValueError as exc:
        raise GateError("plan probe capture time is invalid") from exc
    if not routine_deployment and record.get("authorization_scope") != AUTHORIZATION_SCOPE[gate]:
        raise GateError(
            f"authorization_scope must equal {AUTHORIZATION_SCOPE[gate]!r}"
        )
    if record.get("deployment_id") != plan.get("deployment_id"):
        raise GateError("authorization deployment_id does not match this plan")
    if record.get("plan_core_sha256") != plan.get("plan_core_sha256"):
        raise GateError("authorization plan_core_sha256 does not match this plan")
    target = plan.get("target")
    if not isinstance(target, dict):
        raise GateError("plan target is invalid")
    identity = target.get("identity")
    connection = target.get("connection")
    machine_id = identity.get("machine_id") if isinstance(identity, dict) else None
    fingerprint = (
        connection.get("ssh_host_key_sha256")
        if isinstance(connection, dict)
        else None
    )
    if record.get("target_machine_id") != machine_id:
        raise GateError("authorization target_machine_id does not match this plan")
    if record.get("ssh_host_key_sha256") != fingerprint:
        raise GateError("authorization SSH fingerprint does not match this plan")
    for key in ("approver_identity", "authorization_reference"):
        text = record.get(key)
        if (
            not isinstance(text, str)
            or not text.strip()
            or text != text.strip()
            or len(text) > 200
            or any(ord(char) < 32 or ord(char) == 127 for char in text)
        ):
            raise GateError(f"authorization {key} must contain 1-200 safe characters")
    summary = str(record.get("summary", "")).strip()
    if not summary or len(summary) > 500:
        raise GateError("authorization summary must contain 1-500 characters")
    for key, value in record.items():
        if SECRET_RE.search(key):
            raise GateError("authorization evidence must not contain credentials")
        if isinstance(value, str) and "PRIVATE KEY" in value.upper():
            raise GateError("authorization evidence must not contain private keys")
    return record


def reject_reused_authorization(
    plan: dict[str, Any],
    project_root: Path,
    evidence_path: Path,
    record: dict[str, Any],
) -> None:
    new_hash = sha256_file(evidence_path)
    new_reference = record["authorization_reference"]
    for existing_gate in AUTHORIZATION_SCOPE:
        receipt = plan.get("gate_evidence", {}).get(existing_gate)
        if not isinstance(receipt, dict):
            continue
        existing_path_text = receipt.get("path")
        existing_record: dict[str, Any] | None = None
        existing_path: Path | None = None
        if isinstance(existing_path_text, str):
            existing_path = (project_root / existing_path_text).resolve()
            if existing_path.is_file():
                existing_record = load_json(
                    existing_path,
                    f"authorization evidence for {existing_gate}",
                )
        reusable_routine_authorization = bool(
            existing_gate in ROUTINE_DEPLOYMENT_GATES
            and record.get("gate") == ROUTINE_DEPLOYMENT_EVIDENCE_GATE
            and record.get("authorization_scope") == ROUTINE_DEPLOYMENT_SCOPE
            and isinstance(existing_record, dict)
            and existing_record.get("gate") == ROUTINE_DEPLOYMENT_EVIDENCE_GATE
            and existing_record.get("authorization_scope") == ROUTINE_DEPLOYMENT_SCOPE
        )
        if receipt.get("sha256") == new_hash and not reusable_routine_authorization:
            raise GateError(
                f"authorization evidence is already used by gate {existing_gate!r}"
            )
        if existing_path == evidence_path and not reusable_routine_authorization:
            raise GateError(
                f"authorization evidence path is already used by gate {existing_gate!r}"
            )
        if isinstance(existing_record, dict):
            if (
                existing_record.get("authorization_reference") == new_reference
                and not reusable_routine_authorization
            ):
                raise GateError(
                    "authorization_reference is already used by gate "
                    f"{existing_gate!r}"
                )


def advance_gate(
    plan: dict[str, Any],
    project_root: Path,
    gate: str,
    evidence_file: Path,
    recorded_at_utc: str,
    summary: str,
) -> dict[str, Any]:
    errors, _warnings = validate_deployment_plan.validate(
        plan,
        project_root,
        require_commit_gates=False,
    )
    if errors:
        raise GateError(
            "input plan is invalid; resolve before transition: " + " | ".join(errors)
        )
    gates = plan["gates"]
    if gates.get(gate) is not False:
        raise GateError(f"gate {gate!r} is not pending")
    missing = sorted(
        required for required in PREREQUISITES[gate] if gates.get(required) is not True
    )
    if missing:
        raise GateError(
            f"gate {gate!r} prerequisites are not satisfied: {', '.join(missing)}"
        )
    contract = plan["contract"]
    if (
        gate in {"fpga_release_reviewed", "fpga_load_authorized"}
        and contract["fpga"]["enabled"] is not True
    ):
        raise GateError(
            f"cannot record {gate} when contract.fpga.enabled is false"
        )
    if (
        gate == "functional_output_test_authorized"
        and contract["deployment"]["allow_live_output_test"] is not True
    ):
        raise GateError(
            "cannot authorize physical output test when the contract disallows it"
        )

    validate_timestamp(recorded_at_utc)
    transition_time = datetime.strptime(recorded_at_utc, "%Y-%m-%dT%H:%M:%SZ")
    for prior_gate, receipt in plan.get("gate_evidence", {}).items():
        if not isinstance(receipt, dict):
            continue
        prior_text = str(receipt.get("recorded_at_utc", ""))
        try:
            prior_time = datetime.strptime(prior_text, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise GateError(
                f"prior gate {prior_gate!r} has an invalid timestamp"
            ) from exc
        if transition_time < prior_time:
            raise GateError(
                f"transition timestamp predates prior gate {prior_gate!r}"
            )
    summary = summary.strip()
    if not summary or len(summary) > 500:
        raise GateError("summary must contain 1-500 characters")
    evidence_path, evidence_relative = safe_relative_file(
        project_root, evidence_file
    )
    if gate in AUTHORIZATION_SCOPE:
        authorization = validate_authorization_evidence(
            evidence_path,
            gate,
            recorded_at_utc,
            plan,
        )
        if authorization.get("summary") != summary:
            raise GateError(
                "authorization evidence summary must exactly match --summary"
            )
        reject_reused_authorization(
            plan,
            project_root,
            evidence_path,
            authorization,
        )

    updated = json.loads(json.dumps(plan))
    updated["gates"][gate] = True
    updated["gate_evidence"][gate] = {
        "kind": "operator-evidence",
        "gate": gate,
        "path": evidence_relative,
        "sha256": sha256_file(evidence_path),
        "size": evidence_path.stat().st_size,
        "recorded_at_utc": recorded_at_utc,
        "summary": summary,
    }
    final_errors, _final_warnings = validate_deployment_plan.validate(
        updated,
        project_root,
        require_commit_gates=False,
    )
    if final_errors:
        raise GateError(
            "transition produced an invalid plan: " + " | ".join(final_errors)
        )
    return updated


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--gate", required=True, choices=tuple(sorted(MUTABLE_GATES)))
    parser.add_argument("--evidence-file", required=True, type=Path)
    parser.add_argument("--recorded-at-utc", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.output.resolve() == args.plan.resolve():
            raise GateError("refusing in-place plan edit; choose a new output file")
        if args.output.exists():
            raise GateError(f"output already exists: {args.output}")
        plan = load_json(args.plan, "deployment plan")
        updated = advance_gate(
            plan,
            args.project_root,
            args.gate,
            args.evidence_file,
            args.recorded_at_utc,
            args.summary,
        )
        args.output.write_text(
            json.dumps(updated, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(
            f"Recorded gate {args.gate!r} in {args.output}; no board was contacted."
        )
        return 0
    except (OSError, GateError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
