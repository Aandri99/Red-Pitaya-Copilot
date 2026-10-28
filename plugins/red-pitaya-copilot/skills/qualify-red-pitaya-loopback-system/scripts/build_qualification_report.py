#!/usr/bin/env python3
"""Validate run observations and emit JSON, CSV and Markdown evidence offline."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
from pathlib import Path
from typing import Any

import validate_qualification_plan
from qualification_common import (
    QualificationError,
    canonical_sha256,
    file_binding,
    load_json_object,
    parse_utc,
    project_output_path,
    require_bool,
    require_exact_keys,
    require_int,
    require_list,
    require_number,
    require_object,
    require_safe_id,
    require_safe_text,
    verify_file_binding,
    write_bytes_exclusive,
)


OBSERVATION_KEYS = {
    "observation_schema_version",
    "qualification_id",
    "plan_sha256",
    "started_at_utc",
    "ended_at_utc",
    "executor_id",
    "writer_id",
    "boards",
    "driver_gate",
    "authorization_bundle_sha256",
    "authorization_single_use_nonce",
    "timeline",
    "waveforms",
    "measurements",
    "integrity",
    "counter_checks",
    "safety",
    "artifacts",
    "notes",
}

INTEGRITY_KEYS = {
    "emitted_count",
    "local_detected_count",
    "remote_detected_count",
    "local_drop_count",
    "remote_drop_count",
    "local_data_loss_fraction",
    "remote_data_loss_fraction",
    "local_sequence_gaps",
    "remote_sequence_gaps",
    "fifo_high_water",
    "fifo_capacity",
    "dead_time_seconds",
}

SAFETY_KEYS = {
    "baseline_completed_before_output",
    "output_disabled_during_baseline",
    "emergency_stop_available_all_phases",
    "watchdog_armed",
    "watchdog_timeout_seconds",
    "maximum_commanded_output_volts_peak",
    "safe_stop_invoked",
    "output_disabled_after",
    "single_writer_preserved",
    "runtime_seconds",
    "acquisition_restored",
    "restored_state_sha256",
}

REQUIRED_ARTIFACT_KINDS = frozenset(
    {
        "command-audit-log",
        "counter-snapshots",
        "safety-readbacks",
        "acquisition-restore-readback",
        "integrity-diagnostics",
    }
)


def validate_board_observations(value: Any, plan: dict[str, Any]) -> dict[str, Any]:
    boards = require_object(value, "observations.boards")
    require_exact_keys(boards, {"generator", "acquisition"}, "observations.boards")
    normalized: dict[str, Any] = {}
    for role in ("generator", "acquisition"):
        record = require_object(boards[role], f"observations.boards.{role}")
        require_exact_keys(
            record,
            {"machine_id", "ssh_host_key_sha256"},
            f"observations.boards.{role}",
        )
        expected = plan["boards"][role]
        if record["machine_id"] != expected["identity"]["machine_id"]:
            raise QualificationError(f"observed {role} machine identity mismatch")
        if (
            record["ssh_host_key_sha256"]
            != expected["connection"]["ssh_host_key_sha256"]
        ):
            raise QualificationError(f"observed {role} SSH identity mismatch")
        normalized[role] = dict(record)
    return normalized


def validate_driver_observation(value: Any, plan: dict[str, Any]) -> dict[str, Any]:
    driver = require_object(value, "observations.driver_gate")
    require_exact_keys(
        driver,
        {
            "classification",
            "contract_sha256",
            "magic_ascii",
            "abi_major",
            "abi_minor",
            "access_decision",
        },
        "observations.driver_gate",
    )
    expected = plan["driver_gate"]
    comparisons = {
        "classification": expected["classification"],
        "contract_sha256": expected["contract"]["sha256"],
        "magic_ascii": expected["contract"]["magic_ascii"],
        "abi_major": expected["contract"]["abi_major"],
        "abi_minor": expected["contract"]["abi_minor"],
        "access_decision": expected["access_decision"],
    }
    for key, expected_value in comparisons.items():
        if driver[key] != expected_value:
            raise QualificationError(f"observed driver {key} mismatch")
    return dict(driver)


def validate_timeline(
    value: Any,
    plan: dict[str, Any],
    run_start,
    run_end,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = require_list(value, "observations.timeline")
    phases = plan["phases"]
    if len(rows) != len(phases):
        raise QualificationError("timeline must contain every planned phase exactly once")
    normalized: list[dict[str, Any]] = []
    gates: list[dict[str, Any]] = []
    previous_end = run_start
    baseline_end = None
    first_output_start = None
    for index, (raw, phase) in enumerate(zip(rows, phases)):
        label = f"observations.timeline[{index}]"
        row = require_object(raw, label)
        require_exact_keys(
            row,
            {
                "ordinal",
                "step_id",
                "status",
                "started_at_utc",
                "ended_at_utc",
                "emergency_stop_available",
            },
            label,
        )
        if row["ordinal"] != phase["ordinal"] or row["step_id"] != phase["step_id"]:
            raise QualificationError("timeline order differs from immutable plan")
        if row["status"] not in {"passed", "failed", "skipped"}:
            raise QualificationError(f"{label}.status is invalid")
        start = parse_utc(row["started_at_utc"], f"{label}.started_at_utc")
        end = parse_utc(row["ended_at_utc"], f"{label}.ended_at_utc")
        if start < run_start or end > run_end or end < start:
            raise QualificationError(f"{label} timestamps are outside the run")
        if start < previous_end:
            raise QualificationError("timeline phases overlap or move backward")
        previous_end = end
        emergency = require_bool(
            row["emergency_stop_available"], f"{label}.emergency_stop_available"
        )
        if phase["step_id"] == "baseline-output-disabled":
            baseline_end = end
        if phase["output_allowed"] and first_output_start is None:
            first_output_start = start
        normalized.append(dict(row))
        gates.append(
            {
                "gate": f"phase:{row['step_id']}",
                "passed": row["status"] == "passed" and emergency,
                "detail": (
                    f"status={row['status']}; emergency_stop_available={emergency}"
                ),
            }
        )
    if first_output_start is not None:
        if baseline_end is None or baseline_end > first_output_start:
            raise QualificationError("baseline was not completed before physical output")
    return normalized, gates


def validate_waveforms(
    value: Any,
    plan: dict[str, Any],
    project_root: Path,
) -> list[dict[str, Any]]:
    waveforms = require_list(value, "observations.waveforms")
    expected_labels = plan["measurement_contract"]["waveform_labels"]
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(waveforms):
        label = f"observations.waveforms[{index}]"
        waveform = require_object(raw, label)
        require_exact_keys(waveform, {"channel", "label", "artifact"}, label)
        channel = require_safe_text(
            waveform["channel"], f"{label}.channel", maximum=100
        )
        if channel not in expected_labels:
            raise QualificationError(f"unexpected waveform channel {channel}")
        if channel in seen:
            raise QualificationError(f"duplicate waveform channel {channel}")
        seen.add(channel)
        if waveform["label"] != expected_labels[channel]:
            raise QualificationError(
                f"{channel} must be labelled {expected_labels[channel]!r}"
            )
        verify_file_binding(project_root, waveform["artifact"], f"{label}.artifact")
        normalized.append(
            {
                "channel": channel,
                "label": waveform["label"],
                "artifact": waveform["artifact"],
            }
        )
    missing = sorted(set(expected_labels) - seen)
    if missing:
        raise QualificationError(
            "waveform evidence is missing channels: " + ", ".join(missing)
        )
    return sorted(normalized, key=lambda item: item["channel"])


def criterion_passed(value: Any, criterion: dict[str, Any]) -> bool:
    allowed = criterion["allowed_values"]
    if allowed is not None:
        return isinstance(value, str) and value in allowed
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    if not math.isfinite(number):
        return False
    minimum = criterion["minimum"]
    maximum = criterion["maximum"]
    return (
        (minimum is None or number >= minimum)
        and (maximum is None or number <= maximum)
    )


def validate_measurements(
    value: Any,
    plan: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = require_list(value, "observations.measurements")
    criteria = {
        item["metric"]: item for item in plan["acceptance"]["criteria"]
    }
    normalized: list[dict[str, Any]] = []
    seen_overall: set[str] = set()
    gates: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        label = f"observations.measurements[{index}]"
        row = require_object(raw, label)
        require_exact_keys(
            row,
            {"metric", "scope", "value", "unit", "method", "provenance"},
            label,
        )
        metric = require_safe_id(row["metric"], f"{label}.metric")
        if metric not in criteria:
            raise QualificationError(f"unexpected measurement metric {metric}")
        scope = require_safe_id(row["scope"], f"{label}.scope")
        if scope == "overall":
            if metric in seen_overall:
                raise QualificationError(f"duplicate overall metric {metric}")
            seen_overall.add(metric)
        unit = require_safe_text(
            row["unit"], f"{label}.unit", maximum=64, allow_empty=True
        )
        criterion = criteria[metric]
        if unit != criterion["unit"]:
            raise QualificationError(f"{metric} measurement unit mismatch")
        value_item = row["value"]
        if criterion["allowed_values"] is None:
            value_item = require_number(value_item, f"{label}.value")
        else:
            value_item = require_safe_text(
                value_item, f"{label}.value", maximum=100
            )
        provenance = row["provenance"]
        if provenance not in {"measured", "derived", "reconstructed"}:
            raise QualificationError(f"{label}.provenance is invalid")
        if metric in {
            "emitted_count",
            "local_detected_count",
            "remote_detected_count",
            "local_drop_count",
            "remote_drop_count",
        } and provenance != "measured":
            raise QualificationError(f"{metric} must be labelled measured")
        passed = criterion_passed(value_item, criterion)
        normalized_row = {
            "metric": metric,
            "scope": scope,
            "value": value_item,
            "unit": unit,
            "method": require_safe_text(
                row["method"], f"{label}.method", maximum=500
            ),
            "provenance": provenance,
            "criterion": criterion,
            "criterion_passed": passed,
        }
        normalized.append(normalized_row)
        if scope == "overall":
            gates.append(
                {
                    "gate": f"metric:{metric}",
                    "passed": passed,
                    "detail": f"value={value_item!r} {unit}".strip(),
                }
            )
    missing = sorted(set(criteria) - seen_overall)
    if missing:
        raise QualificationError(
            "overall measurements omit required metrics: " + ", ".join(missing)
        )
    normalized.sort(key=lambda item: (item["metric"], item["scope"]))
    return normalized, gates


def validate_integrity(
    value: Any,
    measurements: list[dict[str, Any]],
) -> dict[str, Any]:
    integrity = require_object(value, "observations.integrity")
    require_exact_keys(integrity, INTEGRITY_KEYS, "observations.integrity")
    counts = {}
    for key in (
        "emitted_count",
        "local_detected_count",
        "remote_detected_count",
        "local_drop_count",
        "remote_drop_count",
        "local_sequence_gaps",
        "remote_sequence_gaps",
        "fifo_high_water",
        "fifo_capacity",
    ):
        counts[key] = require_int(integrity[key], f"integrity.{key}", minimum=0)
    if counts["fifo_high_water"] > counts["fifo_capacity"]:
        raise QualificationError("FIFO high-water exceeds declared capacity")
    fractions = {}
    for key in ("local_data_loss_fraction", "remote_data_loss_fraction"):
        fractions[key] = require_number(
            integrity[key], f"integrity.{key}", minimum=0, maximum=1
        )
    dead_time = require_number(
        integrity["dead_time_seconds"],
        "integrity.dead_time_seconds",
        minimum=0,
    )
    overall = {
        row["metric"]: row["value"]
        for row in measurements
        if row["scope"] == "overall"
    }
    links = {
        "emitted_count": counts["emitted_count"],
        "local_detected_count": counts["local_detected_count"],
        "remote_detected_count": counts["remote_detected_count"],
        "local_drop_count": counts["local_drop_count"],
        "remote_drop_count": counts["remote_drop_count"],
        "local_data_loss_fraction": fractions["local_data_loss_fraction"],
        "remote_data_loss_fraction": fractions["remote_data_loss_fraction"],
        "detector_dead_time_seconds": dead_time,
    }
    for metric, expected in links.items():
        if overall[metric] != expected:
            raise QualificationError(
                f"integrity.{metric} differs from overall measurement"
            )
    return {**counts, **fractions, "dead_time_seconds": dead_time}


def validate_counter_checks(
    value: Any,
    plan: dict[str, Any],
    measurements: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    checks = require_object(value, "observations.counter_checks")
    require_exact_keys(checks, {"reset", "rollover"}, "observations.counter_checks")
    normalized: dict[str, Any] = {}
    gates: list[dict[str, Any]] = []
    overall = {
        row["metric"]: row["value"]
        for row in measurements
        if row["scope"] == "overall"
    }
    for name, metric in (
        ("reset", "counter_reset_behavior"),
        ("rollover", "counter_rollover_behavior"),
    ):
        record = require_object(checks[name], f"counter_checks.{name}")
        require_exact_keys(
            record,
            {
                "behavior",
                "before",
                "after",
                "epoch_before",
                "epoch_after",
                "counter_width_bits",
                "method",
                "passed",
            },
            f"counter_checks.{name}",
        )
        behavior = require_safe_text(
            record["behavior"], f"counter_checks.{name}.behavior", maximum=100
        )
        if behavior != overall[metric]:
            raise QualificationError(f"{name} behavior differs from measurement")
        width = require_int(
            record["counter_width_bits"],
            f"counter_checks.{name}.counter_width_bits",
            minimum=8,
            maximum=128,
        )
        if width != plan["acquisition"]["counter_policy"]["counter_width_bits"]:
            raise QualificationError(f"{name} counter width differs from plan")
        normalized[name] = {
            "behavior": behavior,
            "before": require_int(
                record["before"], f"counter_checks.{name}.before", minimum=0
            ),
            "after": require_int(
                record["after"], f"counter_checks.{name}.after", minimum=0
            ),
            "epoch_before": require_int(
                record["epoch_before"],
                f"counter_checks.{name}.epoch_before",
                minimum=0,
            ),
            "epoch_after": require_int(
                record["epoch_after"],
                f"counter_checks.{name}.epoch_after",
                minimum=0,
            ),
            "counter_width_bits": width,
            "method": require_safe_text(
                record["method"], f"counter_checks.{name}.method", maximum=500
            ),
            "passed": require_bool(
                record["passed"], f"counter_checks.{name}.passed"
            ),
        }
        limit = 1 << width
        if normalized[name]["before"] >= limit or normalized[name]["after"] >= limit:
            raise QualificationError(
                f"{name} counter value exceeds declared {width}-bit width"
            )
        gates.append(
            {
                "gate": f"counter:{name}",
                "passed": normalized[name]["passed"],
                "detail": (
                    f"behavior={behavior}; width={width}; "
                    f"before={normalized[name]['before']}; "
                    f"after={normalized[name]['after']}"
                ),
            }
        )
    return normalized, gates


def validate_safety_observations(
    value: Any,
    plan: dict[str, Any],
    run_seconds: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    safety = require_object(value, "observations.safety")
    require_exact_keys(safety, SAFETY_KEYS, "observations.safety")
    active = plan["mode"] == "active"
    normalized: dict[str, Any] = {}
    boolean_keys = (
        "baseline_completed_before_output",
        "output_disabled_during_baseline",
        "emergency_stop_available_all_phases",
        "watchdog_armed",
        "safe_stop_invoked",
        "output_disabled_after",
        "single_writer_preserved",
        "acquisition_restored",
    )
    for key in boolean_keys:
        normalized[key] = require_bool(safety[key], f"safety.{key}")
    normalized["watchdog_timeout_seconds"] = require_number(
        safety["watchdog_timeout_seconds"],
        "safety.watchdog_timeout_seconds",
        minimum=0,
    )
    normalized["maximum_commanded_output_volts_peak"] = require_number(
        safety["maximum_commanded_output_volts_peak"],
        "safety.maximum_commanded_output_volts_peak",
        minimum=0,
    )
    normalized["runtime_seconds"] = require_number(
        safety["runtime_seconds"], "safety.runtime_seconds", minimum=0
    )
    normalized["restored_state_sha256"] = require_safe_text(
        safety["restored_state_sha256"],
        "safety.restored_state_sha256",
        maximum=64,
    )
    expected_state_hash = plan["inputs"]["acquisition_pre_state"]["sha256"]
    if normalized["restored_state_sha256"] != expected_state_hash:
        raise QualificationError("restored acquisition state hash mismatch")
    if abs(normalized["runtime_seconds"] - run_seconds) > 1.0:
        raise QualificationError("reported runtime differs from UTC run interval")
    if normalized["runtime_seconds"] > plan["safety"]["maximum_runtime_seconds"]:
        raise QualificationError("run exceeded immutable maximum runtime")
    if (
        normalized["maximum_commanded_output_volts_peak"]
        > plan["safety"]["maximum_output_volts_peak"]
    ):
        raise QualificationError("commanded output exceeded immutable voltage ceiling")
    if active:
        if (
            normalized["watchdog_timeout_seconds"]
            != plan["safety"]["watchdog_timeout_seconds"]
        ):
            raise QualificationError("observed watchdog timeout differs from plan")
        planned_maximum = max(
            plan["sequence"]["one_shot"]["amplitude_volts_peak"],
            *(
                item["amplitude_volts_peak"]
                for item in plan["sequence"]["modes"]
            ),
        )
        if not math.isclose(
            normalized["maximum_commanded_output_volts_peak"],
            planned_maximum,
            rel_tol=1e-12,
            abs_tol=1e-15,
        ):
            raise QualificationError(
                "maximum commanded output differs from immutable sequence"
            )
    else:
        if normalized["maximum_commanded_output_volts_peak"] != 0:
            raise QualificationError("read-only run must command zero output")
        if normalized["watchdog_armed"] or normalized["safe_stop_invoked"]:
            raise QualificationError("read-only run must not perform driver writes")
    gates = [
        {
            "gate": f"safety:{key}",
            "passed": value_item,
            "detail": str(value_item).lower(),
        }
        for key, value_item in normalized.items()
        if key in boolean_keys
    ]
    gates.extend(
        [
            {
                "gate": "safety:runtime-bounded",
                "passed": normalized["runtime_seconds"]
                <= plan["safety"]["maximum_runtime_seconds"],
                "detail": (
                    f"{normalized['runtime_seconds']} <= "
                    f"{plan['safety']['maximum_runtime_seconds']} seconds"
                ),
            },
            {
                "gate": "safety:output-ceiling",
                "passed": normalized["maximum_commanded_output_volts_peak"]
                <= plan["safety"]["maximum_output_volts_peak"],
                "detail": (
                    f"{normalized['maximum_commanded_output_volts_peak']} <= "
                    f"{plan['safety']['maximum_output_volts_peak']} Vpeak"
                ),
            },
        ]
    )
    return normalized, gates


def validate_artifacts(
    value: Any,
    project_root: Path,
    plan: dict[str, Any],
    observations: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = require_list(value, "observations.artifacts")
    normalized = []
    seen_kinds: set[str] = set()
    for index, raw in enumerate(rows):
        label = f"observations.artifacts[{index}]"
        item = require_object(raw, label)
        require_exact_keys(item, {"kind", "provenance", "file"}, label)
        provenance = item["provenance"]
        if provenance not in {"measured", "derived", "reconstructed"}:
            raise QualificationError(f"{label}.provenance is invalid")
        artifact_path = verify_file_binding(
            project_root, item["file"], f"{label}.file"
        )
        kind = require_safe_id(item["kind"], f"{label}.kind")
        if kind in seen_kinds:
            raise QualificationError(f"duplicate artifact kind {kind}")
        seen_kinds.add(kind)
        normalized.append(
            {
                "kind": kind,
                "provenance": provenance,
                "file": item["file"],
            }
        )
        if kind == "acquisition-restore-readback":
            restore = load_json_object(
                artifact_path, "acquisition restore readback"
            )
            require_exact_keys(
                restore,
                {
                    "restore_evidence_schema_version",
                    "qualification_id",
                    "generator_machine_id",
                    "acquisition_machine_id",
                    "captured_at_utc",
                    "generator_input_state",
                    "acquisition_input_state",
                    "comparison_result",
                },
                "acquisition restore readback",
            )
            expected_state = plan["acquisition"]["pre_state"]
            expected_restore = {
                "qualification_id": plan["qualification_id"],
                "generator_machine_id": plan["boards"]["generator"]["identity"][
                    "machine_id"
                ],
                "acquisition_machine_id": plan["boards"]["acquisition"]["identity"][
                    "machine_id"
                ],
                "generator_input_state": expected_state["generator_input_state"],
                "acquisition_input_state": expected_state[
                    "acquisition_input_state"
                ],
                "comparison_result": "exact-match",
            }
            if restore.get("restore_evidence_schema_version") != 1:
                raise QualificationError(
                    "acquisition restore evidence schema must equal 1"
                )
            for key, expected_value in expected_restore.items():
                if restore.get(key) != expected_value:
                    raise QualificationError(
                        f"acquisition restore readback {key} mismatch"
                    )
            parse_utc(
                restore["captured_at_utc"],
                "acquisition restore readback captured_at_utc",
            )
        if kind == "single-use-run-record":
            run_record = load_json_object(artifact_path, "single-use run record")
            require_exact_keys(
                run_record,
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
                "single-use run record",
            )
            expected_run = {
                "qualification_id": plan["qualification_id"],
                "plan_sha256": plan["plan_sha256"],
                "single_use_nonce": plan["execution_contract"][
                    "single_use_nonce"
                ],
                "started_at_utc": observations["started_at_utc"],
                "executor_id": observations["executor_id"],
                "writer_id": observations["writer_id"],
                "creation_method": "exclusive-create",
            }
            if run_record.get("run_record_schema_version") != 1:
                raise QualificationError("single-use run record schema must equal 1")
            for key, expected_value in expected_run.items():
                if run_record.get(key) != expected_value:
                    raise QualificationError(
                        f"single-use run record {key} mismatch"
                    )
    required_kinds = set(REQUIRED_ARTIFACT_KINDS)
    if plan["mode"] == "active":
        required_kinds.add("single-use-run-record")
    missing = sorted(required_kinds - seen_kinds)
    if missing:
        raise QualificationError(
            "observations omit required evidence artifacts: " + ", ".join(missing)
        )
    for item in normalized:
        if item["kind"] in required_kinds and item["provenance"] != "measured":
            raise QualificationError(
                f"required artifact {item['kind']} must be labelled measured"
            )
    return sorted(normalized, key=lambda item: (item["kind"], item["file"]["path"]))


def build_evidence(
    plan: dict[str, Any],
    plan_binding: dict[str, Any],
    observations: dict[str, Any],
    observation_binding: dict[str, Any],
    project_root: Path,
    generated_at_utc: str,
) -> dict[str, Any]:
    require_exact_keys(observations, OBSERVATION_KEYS, "observations")
    if observations["observation_schema_version"] != 1:
        raise QualificationError("observation_schema_version must equal 1")
    if observations["qualification_id"] != plan["qualification_id"]:
        raise QualificationError("observation qualification_id mismatch")
    if observations["plan_sha256"] != plan["plan_sha256"]:
        raise QualificationError("observation plan_sha256 mismatch")
    generated = parse_utc(generated_at_utc, "generated_at_utc")
    started = parse_utc(observations["started_at_utc"], "observations.started_at_utc")
    ended = parse_utc(observations["ended_at_utc"], "observations.ended_at_utc")
    if ended < started:
        raise QualificationError("run end precedes run start")
    if generated < ended:
        raise QualificationError("report generated_at_utc predates run end")
    if plan["mode"] == "active":
        authorization = plan["authorizations"]["qualification_binding"]
        authorized_at = parse_utc(
            authorization["recorded_at_utc"],
            "qualification authorization recorded_at_utc",
        )
        expires_at = parse_utc(
            authorization["expires_at_utc"],
            "qualification authorization expires_at_utc",
        )
        if started < authorized_at or started > expires_at:
            raise QualificationError(
                "active run started outside the qualification authorization window"
            )
    run_seconds = (ended - started).total_seconds()
    executor_id = require_safe_id(
        observations["executor_id"], "observations.executor_id"
    )
    writer_id = require_safe_id(observations["writer_id"], "observations.writer_id")
    if writer_id != plan["safety"]["single_writer_id"]:
        raise QualificationError("observation writer differs from single-writer lease")

    boards = validate_board_observations(observations["boards"], plan)
    driver = validate_driver_observation(observations["driver_gate"], plan)
    expected_authorization_hash = (
        None
        if plan["inputs"]["deployment_authorization_bundle"] is None
        else plan["inputs"]["deployment_authorization_bundle"]["sha256"]
    )
    if observations["authorization_bundle_sha256"] != expected_authorization_hash:
        raise QualificationError("observation authorization bundle hash mismatch")
    expected_nonce = plan["execution_contract"]["single_use_nonce"]
    if observations["authorization_single_use_nonce"] != expected_nonce:
        raise QualificationError("observation single-use authorization nonce mismatch")
    timeline, timeline_gates = validate_timeline(
        observations["timeline"], plan, started, ended
    )
    waveforms = validate_waveforms(observations["waveforms"], plan, project_root)
    measurements, measurement_gates = validate_measurements(
        observations["measurements"], plan
    )
    integrity = validate_integrity(observations["integrity"], measurements)
    emitted_expected = plan["measurement_contract"][
        "expected_emitted_count_total"
    ]
    integrity_gate = {
        "gate": "integrity:emitted-count-vs-plan",
        "passed": integrity["emitted_count"] == emitted_expected,
        "detail": (
            f"observed={integrity['emitted_count']}; "
            f"planned={emitted_expected}"
        ),
    }
    counters, counter_gates = validate_counter_checks(
        observations["counter_checks"], plan, measurements
    )
    safety, safety_gates = validate_safety_observations(
        observations["safety"], plan, run_seconds
    )
    artifacts = validate_artifacts(
        observations["artifacts"], project_root, plan, observations
    )
    notes = [
        require_safe_text(item, "observation note", maximum=1000, allow_empty=False)
        for item in require_list(observations["notes"], "observations.notes")
    ]

    gate_results = (
        timeline_gates
        + measurement_gates
        + [integrity_gate]
        + counter_gates
        + safety_gates
    )
    overall = all(item["passed"] for item in gate_results)
    evidence: dict[str, Any] = {
        "evidence_schema_version": 1,
        "evidence_kind": "red-pitaya-loopback-qualification",
        "qualification_id": plan["qualification_id"],
        "generated_at_utc": generated_at_utc,
        "plan": plan_binding,
        "observations": observation_binding,
        "plan_sha256": plan["plan_sha256"],
        "overall_result": "PASS" if overall else "FAIL",
        "boards": boards,
        "topology": plan["topology"],
        "driver_gate": driver,
        "authorizations": plan["authorizations"],
        "executor_id": executor_id,
        "writer_id": writer_id,
        "timeline": timeline,
        "waveforms": waveforms,
        "measurements": measurements,
        "integrity": integrity,
        "counter_checks": counters,
        "safety": safety,
        "artifacts": artifacts,
        "gate_results": gate_results,
        "notes": notes,
        "evidence_core_sha256": "",
    }
    evidence["evidence_core_sha256"] = canonical_sha256(
        {key: value for key, value in evidence.items() if key != "evidence_core_sha256"}
    )
    return evidence


def csv_payload(evidence: dict[str, Any]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(
        [
            "qualification_id",
            "plan_sha256",
            "evidence_core_sha256",
            "overall_result",
            "metric",
            "scope",
            "value",
            "unit",
            "provenance",
            "method",
            "criterion_passed",
        ]
    )
    for row in evidence["measurements"]:
        writer.writerow(
            [
                evidence["qualification_id"],
                evidence["plan_sha256"],
                evidence["evidence_core_sha256"],
                evidence["overall_result"],
                row["metric"],
                row["scope"],
                row["value"],
                row["unit"],
                row["provenance"],
                row["method"],
                str(row["criterion_passed"]).lower(),
            ]
        )
    return stream.getvalue().encode("utf-8")


def markdown_payload(evidence: dict[str, Any]) -> bytes:
    failed = [item for item in evidence["gate_results"] if not item["passed"]]
    lines = [
        "# Red Pitaya loopback qualification report",
        "",
        f"- Qualification: `{evidence['qualification_id']}`",
        f"- Result: **{evidence['overall_result']}**",
        f"- Generated: `{evidence['generated_at_utc']}`",
        f"- Immutable plan: `{evidence['plan_sha256']}`",
        f"- Evidence core: `{evidence['evidence_core_sha256']}`",
        "",
        "## Bound boards",
        "",
        (
            "- Generator machine / SSH key: "
            f"`{evidence['boards']['generator']['machine_id']}` / "
            f"`{evidence['boards']['generator']['ssh_host_key_sha256']}`"
        ),
        (
            "- Acquisition machine / SSH key: "
            f"`{evidence['boards']['acquisition']['machine_id']}` / "
            f"`{evidence['boards']['acquisition']['ssh_host_key_sha256']}`"
        ),
        "",
        "## Driver gate",
        "",
        (
            f"- Classification: `{evidence['driver_gate']['classification']}`; "
            f"magic `{evidence['driver_gate']['magic_ascii']}`; "
            f"ABI `{evidence['driver_gate']['abi_major']}."
            f"{evidence['driver_gate']['abi_minor']}`"
        ),
        f"- Contract SHA-256: `{evidence['driver_gate']['contract_sha256']}`",
        f"- Access decision: `{evidence['driver_gate']['access_decision']}`",
        "",
        "## Signal evidence labels",
        "",
    ]
    for waveform in evidence["waveforms"]:
        lines.append(
            f"- `{waveform['channel']}`: **{waveform['label']}**, "
            f"`{waveform['artifact']['path']}`"
        )
    lines.extend(
        [
            "",
            "## Measurements",
            "",
            "| Metric | Scope | Value | Unit | Provenance | Pass |",
            "|---|---:|---:|---|---|:---:|",
        ]
    )
    for row in evidence["measurements"]:
        lines.append(
            f"| {row['metric']} | {row['scope']} | {row['value']} | "
            f"{row['unit']} | {row['provenance']} | "
            f"{'yes' if row['criterion_passed'] else 'no'} |"
        )
    lines.extend(["", "## Safety and integrity", ""])
    for key, value in evidence["safety"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(
        [
            "",
            "## Failed gates",
            "",
        ]
    )
    if failed:
        lines.extend(f"- `{item['gate']}`: {item['detail']}" for item in failed)
    else:
        lines.append("- None.")
    if evidence["notes"]:
        lines.extend(["", "## Operator notes", ""])
        lines.extend(f"- {note}" for note in evidence["notes"])
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--generated-at-utc", required=True)
    parser.add_argument("--evidence-json", required=True, type=Path)
    parser.add_argument("--measurements-csv", required=True, type=Path)
    parser.add_argument("--report-md", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    requested_outputs = [
        args.evidence_json,
        args.measurements_csv,
        args.report_md,
    ]
    written: list[Path] = []
    try:
        outputs = [
            project_output_path(args.project_root, path, label)
            for path, label in zip(
                requested_outputs,
                ("evidence JSON", "measurements CSV", "Markdown report"),
            )
        ]
        if len({path.resolve() for path in outputs}) != len(outputs):
            raise QualificationError("report output paths must be distinct")

        plan_binding, plan_path = file_binding(
            args.project_root, args.plan, "qualification plan"
        )
        plan = load_json_object(plan_path, "qualification plan")
        validate_qualification_plan.validate_plan(plan, args.project_root)
        observation_binding, observation_path = file_binding(
            args.project_root, args.observations, "qualification observations"
        )
        observations = load_json_object(
            observation_path, "qualification observations"
        )
        evidence = build_evidence(
            plan,
            plan_binding,
            observations,
            observation_binding,
            args.project_root,
            args.generated_at_utc,
        )
        payloads = (
            (
                outputs[0],
                (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode(
                    "utf-8"
                ),
                "evidence JSON",
            ),
            (
                outputs[1],
                csv_payload(evidence),
                "measurements CSV",
            ),
            (outputs[2], markdown_payload(evidence), "Markdown report"),
        )
        for path, payload, label in payloads:
            write_bytes_exclusive(path, payload, label)
            written.append(path)
        print(
            f"Wrote qualification evidence bundle: {outputs[0]}, "
            f"{outputs[1]}, {outputs[2]}. "
            f"Result={evidence['overall_result']}; no board was contacted."
        )
        return 0
    except (OSError, QualificationError) as exc:
        for path in written:
            try:
                path.unlink()
            except OSError:
                pass
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
