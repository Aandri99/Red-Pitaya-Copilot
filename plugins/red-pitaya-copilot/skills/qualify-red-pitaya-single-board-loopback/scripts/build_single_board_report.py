#!/usr/bin/env python3
"""Build deterministic JSON, CSV and Markdown evidence from one offline run."""

from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path
from typing import Any

import validate_single_board_plan
from single_board_common import (
    QualificationError,
    as_bool,
    as_id,
    as_int,
    as_list,
    as_number,
    as_object,
    as_text,
    bind_file,
    canonical_sha256,
    exact_keys,
    exclusive_project_output,
    load_json,
    parse_utc,
    verify_binding,
    write_exclusive,
    write_json_exclusive,
)


OBSERVATION_KEYS = {
    "observation_schema_version",
    "qualification_id",
    "plan_sha256",
    "mode",
    "started_at_utc",
    "completed_at_utc",
    "board_machine_id",
    "ssh_host_key_sha256",
    "phases",
    "run_record",
    "acquisition_restore_readback",
    "final_output_disabled",
    "final_output_verification",
    "zero_dead_time_claimed",
    "raw_artifacts",
    "notes",
}
PHASE_KEYS = {
    "phase_id",
    "started_at_utc",
    "ended_at_utc",
    "result",
    "output_enabled_observed",
    "emitted_candidate_count",
    "detected_candidate_count",
    "measured_peak_volts",
    "overrange",
    "watchdog_cutoff_ready",
    "writer_owner",
    "loaded_fpga_build_id_observed",
    "writer_inventory_result_observed",
    "measurement_provenance",
}


def _criterion(
    name: str,
    passed: bool,
    observed: Any,
    expected: Any,
    unit: str = "",
) -> dict[str, Any]:
    return {
        "criterion": name,
        "passed": bool(passed),
        "observed": observed,
        "expected": expected,
        "unit": unit,
    }


def _validate_run_record(
    project_root: Path,
    binding: Any,
    plan: dict[str, Any],
    started_at,
) -> dict[str, Any]:
    path = verify_binding(project_root, binding, "run record")
    if binding["path"] != plan["execution_contract"]["run_record_path"]:
        raise QualificationError("run record path differs from immutable plan")
    record = load_json(path, "run record")
    exact_keys(
        record,
        {
            "run_record_schema_version",
            "qualification_id",
            "plan_sha256",
            "single_use_nonce",
            "started_at_utc",
            "executor_id",
            "writer_id",
            "creation_method",
        },
        "run record",
    )
    if (
        record["run_record_schema_version"] != 1
        or record["qualification_id"] != plan["qualification_id"]
        or record["plan_sha256"] != plan["plan_sha256"]
        or record["single_use_nonce"]
        != plan["execution_contract"]["single_use_nonce"]
        or record["writer_id"] != plan["execution_contract"]["writer_id"]
        or record["creation_method"] != "exclusive-create"
    ):
        raise QualificationError("single-use run record is stale, replayed or mismatched")
    record_start = parse_utc(record["started_at_utc"], "run record started_at")
    if record_start != started_at:
        raise QualificationError("run record start differs from observations")
    as_id(record["executor_id"], "run record executor_id")
    auth = plan["authorization"]
    if not (
        parse_utc(auth["recorded_at_utc"], "authorization recorded_at")
        <= record_start
        <= parse_utc(auth["expires_at_utc"], "authorization expires_at")
    ):
        raise QualificationError("single-use run began outside authorization window")
    return record


def _validate_restore(
    project_root: Path,
    binding: Any,
    plan: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    path = verify_binding(project_root, binding, "acquisition restore readback")
    record = load_json(path, "acquisition restore readback")
    exact_keys(
        record,
        {
            "restore_evidence_schema_version",
            "qualification_id",
            "board_machine_id",
            "ssh_host_key_sha256",
            "captured_at_utc",
            "input_state",
            "comparison_method",
        },
        "acquisition restore readback",
    )
    if (
        record["restore_evidence_schema_version"] != 1
        or record["qualification_id"] != plan["qualification_id"]
        or record["board_machine_id"] != plan["board"]["identity"]["machine_id"]
        or record["ssh_host_key_sha256"]
        != plan["board"]["connection"]["ssh_host_key_sha256"]
    ):
        raise QualificationError("acquisition restore evidence identity mismatch")
    parse_utc(record["captured_at_utc"], "restore readback captured_at")
    as_text(record["comparison_method"], "restore comparison_method")
    expected_state = plan["acquisition"]["pre_state"]["input_state"]
    return record, record["input_state"] == expected_state


def evaluate(
    project_root: Path,
    plan: dict[str, Any],
    observations: dict[str, Any],
) -> dict[str, Any]:
    exact_keys(observations, OBSERVATION_KEYS, "observations")
    if observations["observation_schema_version"] != 1:
        raise QualificationError("observation_schema_version must equal 1")
    for key in ("qualification_id", "plan_sha256", "mode"):
        if observations[key] != plan[
            "qualification_id" if key == "qualification_id" else key
        ]:
            raise QualificationError(f"observations {key} mismatch")
    if observations["board_machine_id"] != plan["board"]["identity"]["machine_id"]:
        raise QualificationError("observation board identity mismatch")
    if (
        observations["ssh_host_key_sha256"]
        != plan["board"]["connection"]["ssh_host_key_sha256"]
    ):
        raise QualificationError("observation host-key mismatch")
    started = parse_utc(observations["started_at_utc"], "observations started_at")
    completed = parse_utc(observations["completed_at_utc"], "observations completed_at")
    if completed < started:
        raise QualificationError("observations complete before they start")
    if as_bool(
        observations["zero_dead_time_claimed"], "observations zero_dead_time_claimed"
    ):
        raise QualificationError("zero-dead-time claims are forbidden by this qualification")
    phase_values = as_list(observations["phases"], "observations phases")
    if len(phase_values) != len(plan["phases"]):
        raise QualificationError("observation phase count differs from plan")
    phases: list[dict[str, Any]] = []
    prior_end = None
    baseline_end = None
    one_shot: dict[str, Any] | None = None
    release_verified = False
    writer_inventory_verified = False
    for index, raw in enumerate(phase_values):
        phase = as_object(raw, f"observations.phases[{index}]")
        exact_keys(phase, PHASE_KEYS, f"observations.phases[{index}]")
        planned = plan["phases"][index]
        if phase["phase_id"] != planned["phase_id"]:
            raise QualificationError("observation phase order differs from plan")
        phase_start = parse_utc(phase["started_at_utc"], f"{phase['phase_id']} started_at")
        phase_end = parse_utc(phase["ended_at_utc"], f"{phase['phase_id']} ended_at")
        if phase_end < phase_start or phase_start < started or phase_end > completed:
            raise QualificationError(f"{phase['phase_id']} timestamps are invalid")
        if prior_end is not None and phase_start < prior_end:
            raise QualificationError("observation phases overlap or are out of order")
        prior_end = phase_end
        result = as_text(phase["result"], f"{phase['phase_id']} result", maximum=16)
        if result not in {"pass", "fail", "skipped"}:
            raise QualificationError(f"{phase['phase_id']} result is invalid")
        output_observed = as_bool(
            phase["output_enabled_observed"], f"{phase['phase_id']} output_enabled"
        )
        if output_observed and not planned["output_allowed"]:
            raise QualificationError("output occurred outside the plan-bound one-shot phase")
        emitted = as_int(
            phase["emitted_candidate_count"],
            f"{phase['phase_id']} emitted_candidate_count",
            minimum=0,
        )
        detected = as_int(
            phase["detected_candidate_count"],
            f"{phase['phase_id']} detected_candidate_count",
            minimum=0,
        )
        peak = as_number(
            phase["measured_peak_volts"],
            f"{phase['phase_id']} measured_peak_volts",
            minimum=0,
        )
        overrange = as_bool(phase["overrange"], f"{phase['phase_id']} overrange")
        watchdog_ready = as_bool(
            phase["watchdog_cutoff_ready"], f"{phase['phase_id']} watchdog_cutoff_ready"
        )
        writer = phase["writer_owner"]
        if writer is not None:
            writer = as_id(writer, f"{phase['phase_id']} writer_owner")
        loaded_build_id = phase["loaded_fpga_build_id_observed"]
        if loaded_build_id is not None:
            loaded_build_id = as_text(
                loaded_build_id,
                f"{phase['phase_id']} loaded_fpga_build_id_observed",
                maximum=64,
            )
        writer_inventory_result = phase["writer_inventory_result_observed"]
        if writer_inventory_result is not None:
            writer_inventory_result = as_text(
                writer_inventory_result,
                f"{phase['phase_id']} writer_inventory_result_observed",
                maximum=32,
            )
        if phase["measurement_provenance"] != "measured":
            raise QualificationError("phase evidence must be labelled measured")
        if phase["phase_id"] == "baseline":
            baseline_end = phase_end
            if output_observed or emitted or detected:
                raise QualificationError("baseline contains output or event candidates")
        if phase["phase_id"] == "verify-loaded-fpga-and-writers":
            if plan["mode"] != "active":
                raise QualificationError(
                    "read-only observations contain active FPGA/writer verification"
                )
            expected_build_id = plan["fpga_release"]["loaded_build_id"]
            if loaded_build_id != expected_build_id:
                raise QualificationError(
                    "loaded FPGA build identity drifted before output"
                )
            if writer_inventory_result != "exclusive":
                raise QualificationError(
                    "writer inventory is not exclusive immediately before output"
                )
            release_verified = True
            writer_inventory_verified = True
        elif loaded_build_id is not None or writer_inventory_result is not None:
            raise QualificationError(
                "FPGA/writer re-verification values appear outside their planned phase"
            )
        if phase["phase_id"] == "low-amplitude-one-shot":
            if baseline_end is None or phase_start < baseline_end:
                raise QualificationError("output began before baseline completed")
            if (
                phase_end - phase_start
            ).total_seconds() > plan["execution_contract"][
                "maximum_output_window_seconds"
            ]:
                raise QualificationError("observed one-shot exceeded the <=5 s output window")
            if not output_observed:
                raise QualificationError("one-shot phase lacks observed output enable")
            if writer != plan["execution_contract"]["writer_id"]:
                raise QualificationError("one-shot writer does not own the plan-bound lease")
            if not watchdog_ready:
                raise QualificationError("one-shot began without independent cutoff readiness")
            one_shot = {
                **phase,
                "emitted_candidate_count": emitted,
                "detected_candidate_count": detected,
                "measured_peak_volts": peak,
                "overrange": overrange,
            }
        phases.append(
            {
                **phase,
                "emitted_candidate_count": emitted,
                "detected_candidate_count": detected,
                "measured_peak_volts": peak,
                "overrange": overrange,
                "writer_owner": writer,
                "loaded_fpga_build_id_observed": loaded_build_id,
                "writer_inventory_result_observed": writer_inventory_result,
            }
        )

    raw_bindings = []
    for index, binding in enumerate(as_list(observations["raw_artifacts"], "raw_artifacts")):
        verify_binding(project_root, binding, f"raw_artifacts[{index}]")
        raw_bindings.append(binding)
    if not raw_bindings:
        raise QualificationError("at least one measured raw artifact is required")
    notes = [
        as_text(item, "observation note", maximum=500)
        for item in as_list(observations["notes"], "observations notes")
    ]
    criteria: list[dict[str, Any]] = []
    run_record = None
    restore_record = None
    if plan["mode"] == "active":
        if observations["run_record"] is None:
            raise QualificationError("active observations require a run-record binding")
        if observations["acquisition_restore_readback"] is None:
            raise QualificationError("active observations require restore readback")
        run_record = _validate_run_record(
            project_root, observations["run_record"], plan, started
        )
        restore_record, restore_match = _validate_restore(
            project_root, observations["acquisition_restore_readback"], plan
        )
        criteria.append(
            _criterion("acquisition_state_restored_exactly", restore_match, restore_match, True)
        )
        criteria.extend(
            [
                _criterion(
                    "loaded_fpga_release_identity_verified",
                    release_verified,
                    plan["fpga_release"]["loaded_build_id"],
                    plan["fpga_release"]["loaded_build_id"],
                ),
                _criterion(
                    "writer_inventory_exclusive",
                    writer_inventory_verified,
                    "exclusive" if writer_inventory_verified else "not verified",
                    "exclusive",
                ),
                _criterion(
                    "fpga_timing_constraints_drc_release",
                    all(
                        plan["fpga_release"]["release_status"][name]["status"]
                        == "passed"
                        for name in ("timing", "constraints", "drc")
                    ),
                    {
                        name: plan["fpga_release"]["release_status"][name]["status"]
                        for name in ("timing", "constraints", "drc")
                    },
                    {"timing": "passed", "constraints": "passed", "drc": "passed"},
                ),
            ]
        )
        if one_shot is None:
            raise QualificationError("active observations lack the one-shot phase")
        emitted = one_shot["emitted_candidate_count"]
        detected = one_shot["detected_candidate_count"]
        fraction = None if emitted == 0 else detected / emitted
        loss = None if emitted == 0 else max(0, emitted - detected) / emitted
        acceptance = plan["acceptance"]
        expected_emitted = plan["one_shot"]["expected_emitted_candidate_count"]
        criteria.extend(
            [
                _criterion(
                    "emitted_candidate_count",
                    emitted == expected_emitted,
                    emitted,
                    expected_emitted,
                    "candidates",
                ),
                _criterion(
                    "detected_candidate_fraction",
                    fraction is not None
                    and acceptance["minimum_detection_fraction"]
                    <= fraction
                    <= acceptance["maximum_detection_fraction"],
                    fraction,
                    [
                        acceptance["minimum_detection_fraction"],
                        acceptance["maximum_detection_fraction"],
                    ],
                    "ratio",
                ),
                _criterion(
                    "candidate_loss_fraction",
                    loss is not None
                    and loss <= acceptance["maximum_candidate_loss_fraction"],
                    loss,
                    acceptance["maximum_candidate_loss_fraction"],
                    "ratio",
                ),
                _criterion(
                    "measured_input_peak",
                    one_shot["measured_peak_volts"]
                    <= acceptance["maximum_measured_input_volts_peak"],
                    one_shot["measured_peak_volts"],
                    acceptance["maximum_measured_input_volts_peak"],
                    "Vpeak",
                ),
                _criterion(
                    "input_overrange",
                    not (
                        acceptance["require_no_overrange"] and one_shot["overrange"]
                    ),
                    one_shot["overrange"],
                    False,
                ),
            ]
        )
    else:
        if observations["run_record"] is not None:
            raise QualificationError("read-only observations must not contain run record")
        if observations["acquisition_restore_readback"] is not None:
            raise QualificationError("read-only observations must not claim restoration")

    all_phase_pass = all(phase["result"] == "pass" for phase in phases)
    final_disabled = as_bool(
        observations["final_output_disabled"], "observations final_output_disabled"
    )
    criteria.extend(
        [
            _criterion("all_planned_phases_passed", all_phase_pass, all_phase_pass, True),
            _criterion("final_output_disabled", final_disabled, final_disabled, True),
        ]
    )
    as_text(
        observations["final_output_verification"],
        "observations final_output_verification",
    )
    status = "PASS" if all(item["passed"] for item in criteria) else "FAIL"
    return {
        "result_schema_version": 1,
        "result_kind": "red-pitaya-single-board-loopback-evidence",
        "qualification_id": plan["qualification_id"],
        "plan_sha256": plan["plan_sha256"],
        "mode": plan["mode"],
        "status": status,
        "board": plan["board"],
        "topology": {
            "output_channel": "OUT1",
            "input_channel": "IN1",
            "same_board_machine_id": plan["board"]["identity"]["machine_id"],
        },
        "timing": {
            "started_at_utc": observations["started_at_utc"],
            "completed_at_utc": observations["completed_at_utc"],
        },
        "candidate_accounting": (
            None
            if one_shot is None
            else {
                "emitted_candidate_count": one_shot["emitted_candidate_count"],
                "detected_candidate_count": one_shot["detected_candidate_count"],
                "detection_fraction": (
                    None
                    if one_shot["emitted_candidate_count"] == 0
                    else one_shot["detected_candidate_count"]
                    / one_shot["emitted_candidate_count"]
                ),
                "zero_dead_time_claimed": False,
                "interpretation": plan["execution_contract"]["counter_terms"][
                    "interpretation"
                ],
            }
        ),
        "loaded_fpga_release": plan["fpga_release"],
        "writer_inventory": (
            None
            if plan["writer_inventory"] is None
            else {
                "result": plan["writer_inventory"]["result"],
                "cooperative_lock_id": plan["writer_inventory"][
                    "cooperative_lock_id"
                ],
                "plan_lock_participant": plan["writer_inventory"][
                    "plan_lock_participant"
                ],
                "bypass_capable_writers": plan["writer_inventory"][
                    "bypass_capable_writers"
                ],
                "review_reference": plan["writer_inventory"]["review_reference"],
            }
        ),
        "criteria": criteria,
        "phases": phases,
        "single_use_run_record": run_record,
        "acquisition_restore_readback": restore_record,
        "raw_artifacts": raw_bindings,
        "notes": notes,
        "evidence_core_sha256": "",
    }


def _csv_payload(evidence: dict[str, Any]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=["criterion", "passed", "observed", "expected", "unit"],
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(evidence["criteria"])
    return stream.getvalue().encode("utf-8")


def _markdown(evidence: dict[str, Any]) -> bytes:
    board = evidence["board"]
    lines = [
        "# Red Pitaya single-board OUT1-to-IN1 qualification",
        "",
        f"**Result: {evidence['status']}**",
        "",
        f"- Qualification: `{evidence['qualification_id']}`",
        f"- Board hostname: `{board['identity']['hostname']}`",
        f"- Machine ID: `{board['identity']['machine_id']}`",
        f"- SSH host key: `{board['connection']['ssh_host_key_sha256']}`",
        f"- Mode: `{evidence['mode']}`",
        f"- Plan SHA-256: `{evidence['plan_sha256']}`",
        "",
        "## Acceptance",
        "",
        "| Criterion | Pass | Observed | Expected | Unit |",
        "|---|---:|---|---|---|",
    ]
    for item in evidence["criteria"]:
        lines.append(
            f"| {item['criterion']} | {'yes' if item['passed'] else 'no'} | "
            f"{item['observed']} | {item['expected']} | {item['unit']} |"
        )
    if evidence["loaded_fpga_release"] is not None:
        release = evidence["loaded_fpga_release"]
        lines.extend(
            [
                "",
                "## Loaded FPGA release",
                "",
                f"- Build ID: `{release['loaded_build_id']}`",
                f"- Artifact SHA-256: `{release['artifact_sha256']}`",
                f"- Package SHA-256: `{release['package_sha256']}`",
                f"- Register contract SHA-256: `{release['register_contract_sha256']}`",
                "- Release gates: timing `passed`; constraints `passed`; DRC `passed`",
            ]
        )
    if evidence["writer_inventory"] is not None:
        inventory = evidence["writer_inventory"]
        lines.extend(
            [
                "",
                "## Generator writer control",
                "",
                f"- Inventory result: `{inventory['result']}`",
                f"- Plan-lock participant: `{inventory['plan_lock_participant']}`",
                "- Reviewed bypass-capable writers: none",
            ]
        )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            "Emitted and detected values are candidate counts for the bounded run. "
            "This report makes no zero-dead-time or lossless-continuous-acquisition claim.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--evidence-json", required=True, type=Path)
    parser.add_argument("--measurements-csv", required=True, type=Path)
    parser.add_argument("--report-md", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        evidence_path = exclusive_project_output(
            args.project_root, args.evidence_json, "evidence output"
        )
        csv_path = exclusive_project_output(
            args.project_root, args.measurements_csv, "CSV output"
        )
        report_path = exclusive_project_output(
            args.project_root, args.report_md, "report output"
        )
        plan_binding, plan_path = bind_file(args.project_root, args.plan, "plan")
        observation_binding, observation_path = bind_file(
            args.project_root, args.observations, "observations"
        )
        plan = load_json(plan_path, "single-board plan")
        validate_single_board_plan.validate_plan(plan, args.project_root)
        observations = load_json(observation_path, "observations")
        evidence = evaluate(args.project_root, plan, observations)
        evidence["inputs"] = {
            "plan": plan_binding,
            "observations": observation_binding,
        }
        evidence["evidence_core_sha256"] = canonical_sha256(
            {
                key: value
                for key, value in evidence.items()
                if key != "evidence_core_sha256"
            }
        )
        write_json_exclusive(evidence_path, evidence, "evidence output")
        write_exclusive(csv_path, _csv_payload(evidence), "CSV output")
        write_exclusive(report_path, _markdown(evidence), "report output")
        print(
            f"Wrote {evidence['status']} evidence "
            f"{evidence['evidence_core_sha256']}. No board was contacted."
        )
        return 0
    except (OSError, QualificationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
