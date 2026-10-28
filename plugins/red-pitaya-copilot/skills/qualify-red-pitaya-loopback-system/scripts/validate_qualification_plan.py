#!/usr/bin/env python3
"""Deep-validate an immutable loopback qualification plan offline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import generate_qualification_plan
from qualification_common import (
    HASH_RE,
    QualificationError,
    canonical_sha256,
    load_json_object,
    require_exact_keys,
    require_object,
    require_safe_text,
    verify_file_binding,
)


TOP_LEVEL_KEYS = {
    "plan_schema_version",
    "plan_kind",
    "qualification_id",
    "created_at_utc",
    "mode",
    "purpose",
    "inputs",
    "boards",
    "probe_provenance",
    "physical_evidence",
    "topology",
    "safety",
    "driver_gate",
    "authorizations",
    "acquisition",
    "sequence",
    "phases",
    "execution_contract",
    "measurement_contract",
    "acceptance",
    "plan_sha256",
}

INPUT_KEYS = {
    "request",
    "generator_probe_report",
    "acquisition_probe_report",
    "generator_driver_evidence",
    "generator_driver_contract",
    "driver_contract_review",
    "advanced_driver_review",
    "acquisition_pre_state",
    "deployment_authorization_bundle",
    "generator_raw_probe",
    "acquisition_raw_probe",
    "generator_target_manifest",
    "acquisition_target_manifest",
    "generator_host_key_evidence",
    "acquisition_host_key_evidence",
    "generator_output_limit",
    "local_input_range",
    "remote_input_range",
    "local_path_calibration",
    "remote_path_calibration",
}


def validate_plan(plan: dict[str, Any], project_root: Path) -> None:
    require_exact_keys(plan, TOP_LEVEL_KEYS, "qualification plan")
    if plan["plan_schema_version"] != 1:
        raise QualificationError("plan_schema_version must equal 1")
    if plan["plan_kind"] != "red-pitaya-loopback-qualification":
        raise QualificationError("plan_kind is invalid")
    plan_hash = require_safe_text(
        plan["plan_sha256"], "plan.plan_sha256", maximum=64
    )
    if not HASH_RE.fullmatch(plan_hash):
        raise QualificationError("plan.plan_sha256 is invalid")
    observed_hash = canonical_sha256(
        {key: value for key, value in plan.items() if key != "plan_sha256"}
    )
    if observed_hash != plan_hash:
        raise QualificationError("plan content hash does not match")

    inputs = require_object(plan["inputs"], "plan.inputs")
    require_exact_keys(inputs, INPUT_KEYS, "plan.inputs")
    resolved: dict[str, Path | None] = {}
    for key in INPUT_KEYS:
        binding = inputs[key]
        if binding is None:
            if key not in {
                "driver_contract_review",
                "advanced_driver_review",
                "deployment_authorization_bundle",
            }:
                raise QualificationError(f"plan.inputs.{key} cannot be null")
            resolved[key] = None
            continue
        resolved[key] = verify_file_binding(
            project_root, binding, f"plan.inputs.{key}"
        )

    reconstructed = generate_qualification_plan.build_plan(
        project_root,
        resolved["request"],  # type: ignore[arg-type]
        resolved["generator_probe_report"],  # type: ignore[arg-type]
        resolved["acquisition_probe_report"],  # type: ignore[arg-type]
        resolved["generator_raw_probe"],  # type: ignore[arg-type]
        resolved["acquisition_raw_probe"],  # type: ignore[arg-type]
        resolved["generator_target_manifest"],  # type: ignore[arg-type]
        resolved["acquisition_target_manifest"],  # type: ignore[arg-type]
        resolved["generator_host_key_evidence"],  # type: ignore[arg-type]
        resolved["acquisition_host_key_evidence"],  # type: ignore[arg-type]
        resolved["generator_driver_evidence"],  # type: ignore[arg-type]
        resolved["generator_driver_contract"],  # type: ignore[arg-type]
        resolved["acquisition_pre_state"],  # type: ignore[arg-type]
        resolved["deployment_authorization_bundle"],
    )
    if reconstructed != plan:
        raise QualificationError(
            "plan differs from a fresh reconstruction of its hash-bound inputs"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = load_json_object(args.plan, "qualification plan")
        validate_plan(plan, args.project_root)
        print(
            f"Qualification plan is valid and input-bound "
            f"(SHA-256 {plan['plan_sha256']}). No board was contacted."
        )
        return 0
    except (OSError, QualificationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
