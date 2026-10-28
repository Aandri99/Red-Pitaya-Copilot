#!/usr/bin/env python3
"""Deep-validate an immutable single-board OUT1-to-IN1 plan offline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import generate_single_board_plan
from single_board_common import (
    HASH_RE,
    QualificationError,
    as_object,
    as_text,
    canonical_sha256,
    exact_keys,
    load_json,
    verify_binding,
)


TOP_LEVEL_KEYS = {
    "plan_schema_version",
    "plan_kind",
    "qualification_id",
    "created_at_utc",
    "mode",
    "purpose",
    "inputs",
    "board",
    "topology",
    "driver_gate",
    "driver_contract",
    "driver_contract_review",
    "acquisition",
    "safety",
    "writer_inventory",
    "fpga_release",
    "watchdog",
    "authorization",
    "one_shot",
    "acceptance",
    "phases",
    "execution_contract",
    "plan_sha256",
}


def validate_plan(plan: dict[str, Any], project_root: Path) -> None:
    exact_keys(plan, TOP_LEVEL_KEYS, "single-board plan")
    if plan["plan_schema_version"] != 1:
        raise QualificationError("plan_schema_version must equal 1")
    if plan["plan_kind"] != "red-pitaya-single-board-loopback-qualification":
        raise QualificationError("plan_kind is invalid")
    digest = as_text(plan["plan_sha256"], "plan_sha256", maximum=64)
    if not HASH_RE.fullmatch(digest):
        raise QualificationError("plan_sha256 is invalid")
    observed = canonical_sha256(
        {key: value for key, value in plan.items() if key != "plan_sha256"}
    )
    if observed != digest:
        raise QualificationError("plan content hash does not match")
    inputs = as_object(plan["inputs"], "plan.inputs")
    exact_keys(inputs, generate_single_board_plan.INPUT_KEYS, "plan.inputs")
    nullable = {
        "driver_contract_review",
        "writer_inventory_evidence",
        "fpga_identity_evidence",
        "fpga_package",
        "fpga_artifact",
        "fpga_release_receipt",
        "fpga_release_review",
        "fpga_timing_report",
        "fpga_constraints_report",
        "fpga_drc_report",
        "watchdog_evidence",
        "authorization_bundle",
        "deployment_plan",
        "qualification_binding",
    }
    paths: dict[str, Path | None] = {}
    for key in generate_single_board_plan.INPUT_KEYS:
        binding = inputs[key]
        if binding is None:
            if key not in nullable:
                raise QualificationError(f"plan.inputs.{key} cannot be null")
            paths[key] = None
        else:
            paths[key] = verify_binding(project_root, binding, f"plan.inputs.{key}")
    reconstructed = generate_single_board_plan.build_plan(
        project_root,
        paths["request"],  # type: ignore[arg-type]
        paths["probe_report"],  # type: ignore[arg-type]
        paths["raw_probe"],  # type: ignore[arg-type]
        paths["target_manifest"],  # type: ignore[arg-type]
        paths["host_key_evidence"],  # type: ignore[arg-type]
        paths["topology_evidence"],  # type: ignore[arg-type]
        paths["driver_evidence"],  # type: ignore[arg-type]
        paths["driver_contract"],  # type: ignore[arg-type]
        paths["acquisition_pre_state"],  # type: ignore[arg-type]
        paths["writer_inventory_evidence"],
        paths["fpga_identity_evidence"],
        paths["watchdog_evidence"],
        paths["authorization_bundle"],
        enforce_unused_nonce=False,
    )
    if reconstructed != plan:
        raise QualificationError(
            "plan differs from fresh reconstruction of every hash-bound input"
        )
    if plan["execution_contract"].get("one_exact_board") is not True:
        raise QualificationError("plan does not enforce same-board semantics")
    if plan["topology"].get("board_machine_id") != plan["board"]["identity"]["machine_id"]:
        raise QualificationError("OUT1 and IN1 are not bound to the same exact board")
    if plan["topology"].get("output_channel") != "OUT1":
        raise QualificationError("output channel is not OUT1")
    if plan["topology"].get("input_channel") != "IN1":
        raise QualificationError("input channel is not IN1")
    if plan["execution_contract"].get("zero_dead_time_claim_permitted") is not False:
        raise QualificationError("plan improperly permits a zero-dead-time claim")
    phases = plan["phases"]
    baseline_indexes = [
        index for index, phase in enumerate(phases) if phase.get("phase_id") == "baseline"
    ]
    output_indexes = [
        index for index, phase in enumerate(phases) if phase.get("output_allowed") is True
    ]
    if len(baseline_indexes) != 1:
        raise QualificationError("plan must contain exactly one baseline phase")
    if output_indexes and baseline_indexes[0] >= min(output_indexes):
        raise QualificationError("plan permits output before baseline")
    if plan["mode"] == "active":
        if len(output_indexes) != 1:
            raise QualificationError("active plan must contain one output phase")
        if plan["watchdog"] is None or plan["authorization"] is None:
            raise QualificationError("active plan lacks watchdog or authorization")
        if plan["writer_inventory"] is None:
            raise QualificationError("active plan lacks exclusive writer inventory")
        if plan["fpga_release"] is None:
            raise QualificationError("active plan lacks loaded FPGA release identity")
        required_active_bindings = {
            "writer_inventory_evidence",
            "fpga_identity_evidence",
            "fpga_package",
            "fpga_artifact",
            "fpga_release_receipt",
            "fpga_release_review",
            "fpga_timing_report",
            "fpga_constraints_report",
            "fpga_drc_report",
        }
        if any(inputs[key] is None for key in required_active_bindings):
            raise QualificationError("active plan lacks FPGA/writer evidence bindings")
        if (
            plan["writer_inventory"].get("result") != "exclusive"
            or plan["writer_inventory"].get("bypass_capable_writers") != []
            or plan["writer_inventory"].get("plan_lock_participant")
            != plan["execution_contract"]["writer_id"]
            or plan["writer_inventory"].get("cooperative_lock_id")
            != plan["execution_contract"].get("cooperative_lock_id")
        ):
            raise QualificationError("active plan does not prove exclusive writer control")
        release = as_object(plan["fpga_release"], "plan.fpga_release")
        if (
            release.get("loaded_build_id")
            != plan["execution_contract"].get("loaded_fpga_build_id")
            or release.get("register_contract_sha256")
            != inputs["driver_contract"]["sha256"]
        ):
            raise QualificationError("loaded FPGA identity is not contract-bound")
        statuses = as_object(release.get("release_status"), "FPGA release status")
        if set(statuses) != {"timing", "constraints", "drc"} or any(
            as_object(statuses[name], f"FPGA release {name}").get("status")
            != "passed"
            for name in ("timing", "constraints", "drc")
        ):
            raise QualificationError(
                "FPGA timing, constraints and DRC must all be passed"
            )
        if (
            plan["execution_contract"].get(
                "cooperative_lock_bypass_inventory_required"
            )
            is not True
            or plan["execution_contract"].get("loaded_fpga_release_required")
            is not True
            or plan["execution_contract"].get(
                "loaded_fpga_identity_must_be_reread_before_output"
            )
            is not True
        ):
            raise QualificationError("active plan weakens FPGA/writer execution gates")
    elif output_indexes:
        raise QualificationError("read-only plan contains an output phase")
    else:
        if plan["writer_inventory"] is not None or plan["fpga_release"] is not None:
            raise QualificationError("read-only plan contains active FPGA/writer claims")
        active_only_bindings = {
            "writer_inventory_evidence",
            "fpga_identity_evidence",
            "fpga_package",
            "fpga_artifact",
            "fpga_release_receipt",
            "fpga_release_review",
            "fpga_timing_report",
            "fpga_constraints_report",
            "fpga_drc_report",
        }
        if any(inputs[key] is not None for key in active_only_bindings):
            raise QualificationError("read-only plan contains active FPGA/writer bindings")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validate_plan(load_json(args.plan, "single-board plan"), args.project_root)
        print(
            "Single-board plan is valid, deeply input-bound and same-board scoped. "
            "No board was contacted."
        )
        return 0
    except (OSError, QualificationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
