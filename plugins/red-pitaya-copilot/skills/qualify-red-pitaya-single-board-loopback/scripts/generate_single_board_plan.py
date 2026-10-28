#!/usr/bin/env python3
"""Generate an immutable, offline OUT1-to-IN1 qualification plan."""

from __future__ import annotations

import argparse
import importlib
import math
import re
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from single_board_common import (
    HASH_RE,
    QualificationError,
    as_bool,
    as_fingerprint,
    as_id,
    as_int,
    as_list,
    as_number,
    as_object,
    as_text,
    bind_file,
    canonical_sha256,
    delivered_vpeak,
    exact_keys,
    exclusive_project_output,
    load_json,
    parse_utc,
    safe_relative,
    sha256_file,
    write_json_exclusive,
)


REQUIRED_CAPABILITIES = frozenset(
    {"event-counter", "safe-stop", "single-writer", "watchdog"}
)
REQUIRED_REGISTER_ROLES = frozenset(
    {
        "magic",
        "abi-version",
        "control",
        "status",
        "watchdog-timeout",
        "watchdog-kick",
        "event-counter-low",
        "event-counter-high",
    }
)
HEX_RE = re.compile(r"^0x[0-9a-fA-F]+$")
BUILD_ID_RE = re.compile(r"^[0-9a-f]{32,64}$")
INPUT_KEYS = {
    "request",
    "probe_report",
    "raw_probe",
    "target_manifest",
    "host_key_evidence",
    "topology_evidence",
    "driver_evidence",
    "driver_contract",
    "driver_contract_review",
    "acquisition_pre_state",
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


def deployment_modules() -> tuple[Any, Any, Any]:
    """Load only the sibling deployment skill's offline validators."""
    scripts = (
        Path(__file__).resolve().parents[2]
        / "deploy-red-pitaya-project"
        / "scripts"
    )
    if not scripts.is_dir():
        raise QualificationError("sibling deploy-red-pitaya-project skill is missing")
    scripts_text = str(scripts)
    if scripts_text not in sys.path:
        sys.path.insert(0, scripts_text)
    try:
        analyzer = importlib.import_module("analyze_probe")
        contracts = importlib.import_module("project_contract")
        validator = importlib.import_module("validate_deployment_plan")
    except ImportError as exc:
        raise QualificationError(f"cannot load deployment validators: {exc}") from exc
    expected = scripts.resolve()
    for module in (analyzer, contracts, validator):
        path = Path(module.__file__).resolve()
        try:
            path.relative_to(expected)
        except ValueError as exc:
            raise QualificationError(
                f"refusing unexpected deployment validator module {path}"
            ) from exc
    return analyzer, contracts, validator


def _time_age(created, captured, maximum: int, label: str) -> None:
    age = (created - captured).total_seconds()
    if age < 0 or age > maximum:
        raise QualificationError(f"{label} is future-dated or stale")


def validate_request(value: dict[str, Any]) -> dict[str, Any]:
    exact_keys(
        value,
        {
            "request_schema_version",
            "qualification_id",
            "created_at_utc",
            "mode",
            "purpose",
            "probe_max_age_seconds",
            "topology",
            "driver_expectation",
            "acquisition",
            "safety",
            "one_shot",
            "acceptance",
            "run_record_path",
        },
        "request",
    )
    if value["request_schema_version"] != 1:
        raise QualificationError("request_schema_version must equal 1")
    mode = as_text(value["mode"], "request.mode", maximum=20)
    if mode not in {"active", "read-only"}:
        raise QualificationError("request.mode must be active or read-only")
    topology = as_object(value["topology"], "request.topology")
    exact_keys(
        topology,
        {
            "output_channel",
            "input_channel",
            "connection",
            "physically_verified",
            "shared_reference_verified",
        },
        "request.topology",
    )
    output_channel = as_text(topology["output_channel"], "topology.output_channel", maximum=32)
    input_channel = as_text(topology["input_channel"], "topology.input_channel", maximum=32)
    if output_channel.upper() != "OUT1" or input_channel.upper() != "IN1":
        raise QualificationError("single-board qualification requires OUT1 connected to IN1")
    if as_bool(topology["physically_verified"], "topology.physically_verified") is not True:
        raise QualificationError("OUT1-to-IN1 topology must be physically verified")
    if as_bool(topology["shared_reference_verified"], "topology.shared_reference_verified") is not True:
        raise QualificationError("OUT1-to-IN1 shared reference/ground must be verified")
    topology_clean = {
        "output_channel": output_channel,
        "input_channel": input_channel,
        "connection": as_text(topology["connection"], "topology.connection"),
        "physically_verified": True,
        "shared_reference_verified": True,
    }

    expectation = as_object(value["driver_expectation"], "request.driver_expectation")
    exact_keys(
        expectation,
        {
            "classification",
            "expected_magic_ascii",
            "expected_abi_major",
            "minimum_abi_minor",
            "expected_driver_version",
            "approved_contract_sha256",
            "required_capabilities",
            "contract_review_path",
        },
        "request.driver_expectation",
    )
    classification = as_text(
        expectation["classification"], "driver_expectation.classification", maximum=32
    )
    if classification not in {"pdg1", "approved-advanced", "unknown-read-only"}:
        raise QualificationError("driver classification is invalid")
    if mode == "active" and classification == "unknown-read-only":
        raise QualificationError("unknown generator driver is read-only only")
    if mode == "read-only" and classification != "unknown-read-only":
        raise QualificationError("read-only mode must use unknown-read-only classification")
    if classification == "unknown-read-only":
        for key in (
            "expected_magic_ascii",
            "expected_abi_major",
            "minimum_abi_minor",
            "expected_driver_version",
            "approved_contract_sha256",
            "contract_review_path",
        ):
            if expectation[key] is not None:
                raise QualificationError(f"unknown-read-only requires {key} to be null")
        if expectation["required_capabilities"] != []:
            raise QualificationError("unknown-read-only requires no capabilities")
        expectation_clean = {
            **expectation,
            "classification": classification,
        }
    else:
        digest = as_text(
            expectation["approved_contract_sha256"],
            "driver_expectation.approved_contract_sha256",
            maximum=64,
        )
        if not HASH_RE.fullmatch(digest):
            raise QualificationError("approved_contract_sha256 is invalid")
        capabilities = sorted(
            as_id(item, "driver required capability")
            for item in as_list(
                expectation["required_capabilities"],
                "driver_expectation.required_capabilities",
            )
        )
        if not REQUIRED_CAPABILITIES.issubset(capabilities):
            raise QualificationError("active driver is missing mandatory capabilities")
        expectation_clean = {
            "classification": classification,
            "expected_magic_ascii": as_text(
                expectation["expected_magic_ascii"], "expected_magic_ascii", maximum=16
            ),
            "expected_abi_major": as_int(
                expectation["expected_abi_major"], "expected_abi_major", minimum=0
            ),
            "minimum_abi_minor": as_int(
                expectation["minimum_abi_minor"], "minimum_abi_minor", minimum=0
            ),
            "expected_driver_version": as_text(
                expectation["expected_driver_version"],
                "expected_driver_version",
                maximum=100,
            ),
            "approved_contract_sha256": digest,
            "required_capabilities": capabilities,
            "contract_review_path": safe_relative(
                expectation["contract_review_path"], "contract_review_path"
            ),
        }

    acquisition = as_object(value["acquisition"], "request.acquisition")
    exact_keys(
        acquisition,
        {
            "sample_rate_hz",
            "record_samples",
            "pretrigger_samples",
            "trigger_mode",
            "sample_units",
            "baseline_duration_seconds",
            "minimum_baseline_records",
        },
        "request.acquisition",
    )
    samples = as_int(acquisition["record_samples"], "acquisition.record_samples", minimum=16)
    pretrigger = as_int(
        acquisition["pretrigger_samples"],
        "acquisition.pretrigger_samples",
        minimum=0,
    )
    if pretrigger >= samples:
        raise QualificationError("pretrigger_samples must be less than record_samples")
    acquisition_clean = {
        "sample_rate_hz": as_number(
            acquisition["sample_rate_hz"], "acquisition.sample_rate_hz", exclusive_minimum=0
        ),
        "record_samples": samples,
        "pretrigger_samples": pretrigger,
        "trigger_mode": as_text(
            acquisition["trigger_mode"], "acquisition.trigger_mode", maximum=100
        ),
        "sample_units": as_text(
            acquisition["sample_units"], "acquisition.sample_units", maximum=32
        ),
        "baseline_duration_seconds": as_number(
            acquisition["baseline_duration_seconds"],
            "acquisition.baseline_duration_seconds",
            exclusive_minimum=0,
            maximum=300,
        ),
        "minimum_baseline_records": as_int(
            acquisition["minimum_baseline_records"],
            "acquisition.minimum_baseline_records",
            minimum=1,
        ),
    }

    safety = as_object(value["safety"], "request.safety")
    exact_keys(
        safety,
        {
            "maximum_output_volts_peak",
            "input_headroom_fraction",
            "maximum_output_window_seconds",
            "writer_id",
            "watchdog_timeout_seconds",
            "emergency_stop_action",
            "emergency_stop_verification",
            "restore_action",
            "restore_verification",
        },
        "request.safety",
    )
    safety_clean = {
        "maximum_output_volts_peak": as_number(
            safety["maximum_output_volts_peak"],
            "safety.maximum_output_volts_peak",
            exclusive_minimum=0,
        ),
        "input_headroom_fraction": as_number(
            safety["input_headroom_fraction"],
            "safety.input_headroom_fraction",
            exclusive_minimum=0,
            maximum=1,
        ),
        "maximum_output_window_seconds": as_number(
            safety["maximum_output_window_seconds"],
            "safety.maximum_output_window_seconds",
            exclusive_minimum=0,
            maximum=5,
        ),
        "writer_id": as_id(safety["writer_id"], "safety.writer_id"),
        "watchdog_timeout_seconds": as_number(
            safety["watchdog_timeout_seconds"],
            "safety.watchdog_timeout_seconds",
            exclusive_minimum=0,
            maximum=5,
        ),
        "emergency_stop_action": as_text(
            safety["emergency_stop_action"], "safety.emergency_stop_action"
        ),
        "emergency_stop_verification": as_text(
            safety["emergency_stop_verification"],
            "safety.emergency_stop_verification",
        ),
        "restore_action": as_text(safety["restore_action"], "safety.restore_action"),
        "restore_verification": as_text(
            safety["restore_verification"], "safety.restore_verification"
        ),
    }
    one_shot_value = value["one_shot"]
    if mode == "read-only":
        if one_shot_value is not None:
            raise QualificationError("read-only mode must not contain one_shot")
        one_shot = None
    else:
        one_shot_raw = as_object(one_shot_value, "request.one_shot")
        exact_keys(
            one_shot_raw,
            {
                "amplitude_volts_peak",
                "pulse_width_seconds",
                "capture_window_seconds",
                "polarity",
                "deterministic_seed",
                "expected_emitted_candidate_count",
            },
            "request.one_shot",
        )
        one_shot = {
            "amplitude_volts_peak": as_number(
                one_shot_raw["amplitude_volts_peak"],
                "one_shot.amplitude_volts_peak",
                exclusive_minimum=0,
            ),
            "pulse_width_seconds": as_number(
                one_shot_raw["pulse_width_seconds"],
                "one_shot.pulse_width_seconds",
                exclusive_minimum=0,
                maximum=5,
            ),
            "capture_window_seconds": as_number(
                one_shot_raw["capture_window_seconds"],
                "one_shot.capture_window_seconds",
                exclusive_minimum=0,
                maximum=5,
            ),
            "polarity": as_text(one_shot_raw["polarity"], "one_shot.polarity", maximum=16),
            "deterministic_seed": as_int(
                one_shot_raw["deterministic_seed"], "one_shot.deterministic_seed", minimum=0
            ),
            "expected_emitted_candidate_count": as_int(
                one_shot_raw["expected_emitted_candidate_count"],
                "one_shot.expected_emitted_candidate_count",
                minimum=1,
                maximum=1,
            ),
        }
        if one_shot["capture_window_seconds"] > safety_clean["maximum_output_window_seconds"]:
            raise QualificationError("one-shot capture window exceeds the <=5 s output window")
        if one_shot["amplitude_volts_peak"] > safety_clean["maximum_output_volts_peak"]:
            raise QualificationError("one-shot Vpeak exceeds request safety ceiling")

    acceptance = as_object(value["acceptance"], "request.acceptance")
    exact_keys(
        acceptance,
        {
            "minimum_detection_fraction",
            "maximum_detection_fraction",
            "maximum_candidate_loss_fraction",
            "maximum_measured_input_volts_peak",
            "require_no_overrange",
        },
        "request.acceptance",
    )
    minimum_fraction = as_number(
        acceptance["minimum_detection_fraction"],
        "acceptance.minimum_detection_fraction",
        minimum=0,
        maximum=1,
    )
    maximum_fraction = as_number(
        acceptance["maximum_detection_fraction"],
        "acceptance.maximum_detection_fraction",
        minimum=0,
        maximum=1,
    )
    if minimum_fraction > maximum_fraction:
        raise QualificationError("acceptance detection fraction bounds are reversed")
    acceptance_clean = {
        "minimum_detection_fraction": minimum_fraction,
        "maximum_detection_fraction": maximum_fraction,
        "maximum_candidate_loss_fraction": as_number(
            acceptance["maximum_candidate_loss_fraction"],
            "acceptance.maximum_candidate_loss_fraction",
            minimum=0,
            maximum=1,
        ),
        "maximum_measured_input_volts_peak": as_number(
            acceptance["maximum_measured_input_volts_peak"],
            "acceptance.maximum_measured_input_volts_peak",
            exclusive_minimum=0,
        ),
        "require_no_overrange": as_bool(
            acceptance["require_no_overrange"], "acceptance.require_no_overrange"
        ),
    }
    return {
        "request_schema_version": 1,
        "qualification_id": as_id(value["qualification_id"], "qualification_id"),
        "created_at_utc": parse_utc(
            value["created_at_utc"], "request.created_at_utc"
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": mode,
        "purpose": as_text(value["purpose"], "request.purpose"),
        "probe_max_age_seconds": as_int(
            value["probe_max_age_seconds"],
            "request.probe_max_age_seconds",
            minimum=1,
            maximum=86400,
        ),
        "topology": topology_clean,
        "driver_expectation": expectation_clean,
        "acquisition": acquisition_clean,
        "safety": safety_clean,
        "one_shot": one_shot,
        "acceptance": acceptance_clean,
        "run_record_path": safe_relative(value["run_record_path"], "run_record_path"),
    }


def validate_probe_and_host_key(
    project_root: Path,
    request: dict[str, Any],
    report_path: Path,
    raw_probe_path: Path,
    manifest_path: Path,
    host_key_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    report_binding, report_file = bind_file(project_root, report_path, "probe report")
    raw_binding, raw_file = bind_file(project_root, raw_probe_path, "raw probe")
    manifest_binding, manifest_file = bind_file(
        project_root, manifest_path, "target manifest"
    )
    host_binding, host_file = bind_file(
        project_root, host_key_path, "host-key evidence"
    )
    report = load_json(report_file, "probe report")
    analyzer, contracts, _ = deployment_modules()
    try:
        manifest = contracts.load_and_validate_manifest(manifest_file)
        fresh, status = analyzer.analyze(load_json(raw_file, "raw probe"), manifest, "generator")
    except Exception as exc:
        raise QualificationError(f"raw probe/manifest reanalysis failed: {exc}") from exc
    if status != 0 or fresh.get("compatible") is not True:
        raise QualificationError("probe is not compatible")
    if fresh.get("blocking_errors") != [] or fresh.get("warnings") != []:
        raise QualificationError("probe must be warning-free")
    if fresh != report:
        raise QualificationError("normalized probe differs from fresh raw-probe analysis")
    captured = parse_utc(
        as_object(report.get("evidence"), "probe.evidence").get("captured_at"),
        "probe captured_at",
    )
    created = parse_utc(request["created_at_utc"], "request created_at")
    _time_age(created, captured, request["probe_max_age_seconds"], "probe")
    connection = as_object(report.get("connection"), "probe.connection")
    identity = as_object(report.get("identity"), "probe.identity")
    target = as_object(report.get("target"), "probe.target")
    fingerprint = as_fingerprint(
        connection.get("ssh_host_key_sha256"), "probe SSH fingerprint"
    )
    if connection.get("host_key_verified") is not True:
        raise QualificationError("probe host key is not verified")
    host = load_json(host_file, "host-key evidence")
    exact_keys(
        host,
        {
            "host_key_evidence_schema_version",
            "role",
            "captured_at_utc",
            "host",
            "address",
            "algorithm",
            "ssh_host_key_sha256",
            "verification_method",
            "verified_by",
            "verification_reference",
            "result",
        },
        "host-key evidence",
    )
    if (
        host["host_key_evidence_schema_version"] != 1
        or host["role"] != "generator"
        or host["result"] != "verified"
    ):
        raise QualificationError("host-key evidence role/result mismatch")
    _time_age(
        created,
        parse_utc(host["captured_at_utc"], "host-key captured_at"),
        request["probe_max_age_seconds"],
        "host-key evidence",
    )
    for key in ("host", "address", "ssh_host_key_sha256"):
        if host[key] != connection[key]:
            raise QualificationError(f"host-key evidence {key} mismatch")
    as_text(host["algorithm"], "host-key algorithm", maximum=64)
    as_text(host["verification_method"], "host-key verification_method")
    as_text(host["verified_by"], "host-key verified_by", maximum=200)
    as_text(host["verification_reference"], "host-key reference", maximum=200)
    machine_id = as_text(identity.get("machine_id"), "probe machine_id", maximum=128)
    board = {
        "connection": {
            "host": as_text(connection.get("host"), "probe host", maximum=255),
            "address": as_text(connection.get("address"), "probe address", maximum=100),
            "ssh_host_key_sha256": fingerprint,
        },
        "identity": {
            "hostname": as_text(identity.get("hostname"), "probe hostname", maximum=255),
            "machine_id": machine_id,
            "serial": identity.get("serial"),
            "mac_addresses": identity.get("mac_addresses"),
        },
        "os_release": target.get("observed_os_release"),
        "model": target.get("observed_model"),
        "observed": target,
    }
    return board, {
        "probe_report": report_binding,
        "raw_probe": raw_binding,
        "target_manifest": manifest_binding,
        "host_key_evidence": host_binding,
    }


def validate_topology(
    value: dict[str, Any],
    request: dict[str, Any],
    board: dict[str, Any],
) -> dict[str, Any]:
    exact_keys(
        value,
        {
            "evidence_schema_version",
            "captured_at_utc",
            "board_machine_id",
            "ssh_host_key_sha256",
            "output_channel",
            "input_channel",
            "physically_verified",
            "shared_reference_verified",
            "physical_output_isolated",
            "connection_description",
            "output_limit_volts_peak",
            "input_range_volts_peak",
            "attenuation_db",
            "attenuation_uncertainty_db",
            "coupling",
            "termination_ohms",
            "calibration_reference",
            "rating_reference",
            "verified_by",
        },
        "topology evidence",
    )
    if value["evidence_schema_version"] != 1:
        raise QualificationError("topology evidence schema must equal 1")
    if value["board_machine_id"] != board["identity"]["machine_id"]:
        raise QualificationError("topology board identity mismatch")
    if value["ssh_host_key_sha256"] != board["connection"]["ssh_host_key_sha256"]:
        raise QualificationError("topology host-key identity mismatch")
    if (
        value["output_channel"] != request["topology"]["output_channel"]
        or value["input_channel"] != request["topology"]["input_channel"]
    ):
        raise QualificationError("topology channel contract mismatch")
    for key in ("physically_verified", "shared_reference_verified"):
        if as_bool(value[key], f"topology evidence {key}") is not True:
            raise QualificationError(f"topology evidence {key} must be true")
    isolated = as_bool(value["physical_output_isolated"], "physical_output_isolated")
    output_limit = as_number(
        value["output_limit_volts_peak"], "output_limit_volts_peak", exclusive_minimum=0
    )
    input_range = as_number(
        value["input_range_volts_peak"], "input_range_volts_peak", exclusive_minimum=0
    )
    attenuation = as_number(value["attenuation_db"], "attenuation_db", minimum=0)
    uncertainty = as_number(
        value["attenuation_uncertainty_db"], "attenuation_uncertainty_db", minimum=0
    )
    if request["safety"]["maximum_output_volts_peak"] > output_limit:
        raise QualificationError("request Vpeak exceeds reviewed OUT1 rating")
    if request["acceptance"]["maximum_measured_input_volts_peak"] > input_range:
        raise QualificationError("acceptance Vpeak exceeds reviewed IN1 rating")
    commanded = (
        0.0
        if request["one_shot"] is None
        else request["one_shot"]["amplitude_volts_peak"]
    )
    worst_input = delivered_vpeak(commanded, attenuation, uncertainty)
    headroom = input_range * request["safety"]["input_headroom_fraction"]
    if worst_input > headroom or worst_input > request["acceptance"]["maximum_measured_input_volts_peak"]:
        raise QualificationError("one-shot Vpeak/headroom overflow at IN1")
    if request["mode"] == "read-only" and not isolated:
        raise QualificationError("unknown driver read-only path requires physical output isolation")
    for key in (
        "connection_description",
        "coupling",
        "calibration_reference",
        "rating_reference",
        "verified_by",
    ):
        as_text(value[key], f"topology evidence {key}")
    _time_age(
        parse_utc(request["created_at_utc"], "request created_at"),
        parse_utc(value["captured_at_utc"], "topology evidence captured_at"),
        request["probe_max_age_seconds"],
        "topology evidence",
    )
    return {
        **value,
        "output_limit_volts_peak": output_limit,
        "input_range_volts_peak": input_range,
        "attenuation_db": attenuation,
        "attenuation_uncertainty_db": uncertainty,
        "termination_ohms": as_number(
            value["termination_ohms"], "termination_ohms", exclusive_minimum=0
        ),
        "worst_case_input_volts_peak": worst_input,
        "headroom_limit_volts_peak": headroom,
    }


def _hex(value: Any, label: str) -> int:
    text = as_text(value, label, maximum=32)
    if not HEX_RE.fullmatch(text):
        raise QualificationError(f"{label} must be hexadecimal")
    number = int(text, 16)
    if number % 4:
        raise QualificationError(f"{label} must be 32-bit aligned")
    return number


def validate_driver(
    contract_value: dict[str, Any],
    evidence: dict[str, Any],
    request: dict[str, Any],
    board: dict[str, Any],
    contract_hash: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    exact_keys(
        contract_value,
        {
            "schema_version",
            "namespace",
            "magic_ascii",
            "abi_major",
            "abi_minor",
            "register_base",
            "address_span",
            "capabilities",
            "registers",
        },
        "driver contract",
    )
    if contract_value["schema_version"] != 1:
        raise QualificationError("driver contract schema must equal 1")
    base = _hex(contract_value["register_base"], "register_base")
    span = _hex(contract_value["address_span"], "address_span")
    if span < 4:
        raise QualificationError("driver contract address_span is too small")
    capabilities = sorted(
        as_id(item, "driver capability")
        for item in as_list(contract_value["capabilities"], "driver capabilities")
    )
    registers = as_list(contract_value["registers"], "driver registers")
    if not registers:
        raise QualificationError("driver register map must not be empty")
    roles: set[str] = set()
    offsets: set[int] = set()
    clean_registers = []
    for index, raw in enumerate(registers):
        item = as_object(raw, f"registers[{index}]")
        exact_keys(item, {"name", "offset", "access", "role"}, f"registers[{index}]")
        offset = _hex(item["offset"], f"registers[{index}].offset")
        if offset >= span or offset in offsets:
            raise QualificationError("driver register offset is out of span or duplicated")
        offsets.add(offset)
        role = as_id(item["role"], f"registers[{index}].role")
        roles.add(role)
        clean_registers.append(
            {
                "name": as_id(item["name"], f"registers[{index}].name"),
                "offset": item["offset"].lower(),
                "access": as_text(item["access"], f"registers[{index}].access", maximum=8),
                "role": role,
            }
        )
    expectation = request["driver_expectation"]
    classification = expectation["classification"]
    exact_keys(
        evidence,
        {
            "driver_evidence_schema_version",
            "captured_at_utc",
            "source",
            "board_machine_id",
            "ssh_host_key_sha256",
            "read_only_probe",
            "output_enabled_readback",
            "writer_owner",
            "contract",
        },
        "driver evidence",
    )
    if evidence["driver_evidence_schema_version"] != 1:
        raise QualificationError("driver evidence schema must equal 1")
    _time_age(
        parse_utc(request["created_at_utc"], "request created_at"),
        parse_utc(evidence["captured_at_utc"], "driver evidence captured_at"),
        request["probe_max_age_seconds"],
        "driver evidence",
    )
    if evidence["board_machine_id"] != board["identity"]["machine_id"]:
        raise QualificationError("driver evidence board identity mismatch")
    if evidence["ssh_host_key_sha256"] != board["connection"]["ssh_host_key_sha256"]:
        raise QualificationError("driver evidence host-key mismatch")
    if as_bool(evidence["read_only_probe"], "driver evidence read_only_probe") is not True:
        raise QualificationError("driver discovery must be read-only")
    observed = as_object(evidence["contract"], "driver evidence contract")
    exact_keys(
        observed,
        {
            "magic_ascii",
            "abi_major",
            "abi_minor",
            "register_contract_sha256",
            "register_base",
            "address_span",
            "capabilities",
            "driver_version",
        },
        "driver evidence contract",
    )
    if classification == "unknown-read-only":
        return {
            "classification": classification,
            "write_access": "forbidden",
            "reason": "Unknown driver: observe only; no stop, reset, lease or output writes.",
        }, {
            **contract_value,
            "register_base": hex(base),
            "address_span": hex(span),
            "capabilities": capabilities,
            "registers": clean_registers,
            "sha256": contract_hash,
        }
    if evidence["output_enabled_readback"] is not False:
        raise QualificationError("output must be read back disabled before planning")
    if evidence["writer_owner"] is not None:
        raise QualificationError("generator writer ownership is not free")
    if contract_hash != expectation["approved_contract_sha256"]:
        raise QualificationError("generator register contract hash mismatch")
    if observed["register_contract_sha256"] != contract_hash:
        raise QualificationError("observed generator contract hash mismatch")
    comparisons = {
        "magic_ascii": contract_value["magic_ascii"],
        "abi_major": contract_value["abi_major"],
        "register_base": contract_value["register_base"],
        "address_span": contract_value["address_span"],
        "driver_version": expectation["expected_driver_version"],
    }
    for key, expected in comparisons.items():
        if observed[key] != expected:
            raise QualificationError(f"generator driver contract mismatch: {key}")
    if observed["magic_ascii"] != expectation["expected_magic_ascii"]:
        raise QualificationError("observed driver magic differs from request")
    if observed["abi_major"] != expectation["expected_abi_major"]:
        raise QualificationError("observed driver ABI major differs from request")
    if as_int(observed["abi_minor"], "observed abi_minor", minimum=0) < expectation["minimum_abi_minor"]:
        raise QualificationError("observed driver ABI minor is too old")
    observed_capabilities = sorted(
        as_id(item, "observed capability")
        for item in as_list(observed["capabilities"], "observed capabilities")
    )
    if observed_capabilities != capabilities:
        raise QualificationError("observed generator capabilities mismatch contract")
    if not REQUIRED_CAPABILITIES.issubset(capabilities):
        raise QualificationError("generator contract lacks mandatory active capabilities")
    if not REQUIRED_REGISTER_ROLES.issubset(roles):
        raise QualificationError("generator contract lacks mandatory semantic registers")
    return {
        "classification": classification,
        "write_access": "plan-bound-single-writer",
        "contract_sha256": contract_hash,
        "magic_ascii": contract_value["magic_ascii"],
        "abi_major": contract_value["abi_major"],
        "abi_minor": contract_value["abi_minor"],
        "driver_version": observed["driver_version"],
        "capabilities": capabilities,
        "writer_owner_preflight": None,
        "output_enabled_preflight": False,
    }, {
        **contract_value,
        "register_base": hex(base),
        "address_span": hex(span),
        "capabilities": capabilities,
        "registers": clean_registers,
        "sha256": contract_hash,
    }


def validate_contract_review(
    value: dict[str, Any],
    request: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    exact_keys(
        value,
        {
            "review_schema_version",
            "classification",
            "contract_sha256",
            "magic_ascii",
            "abi_major",
            "abi_minor",
            "driver_version",
            "approved_capabilities",
            "independent_from_qualification_request",
            "reviewed_at_utc",
            "reviewer_identity",
            "review_reference",
        },
        "driver contract review",
    )
    expectation = request["driver_expectation"]
    if (
        value["review_schema_version"] != 1
        or value["classification"] != expectation["classification"]
        or value["contract_sha256"] != contract["sha256"]
        or value["magic_ascii"] != contract["magic_ascii"]
        or value["abi_major"] != contract["abi_major"]
        or value["abi_minor"] != contract["abi_minor"]
        or value["driver_version"] != expectation["expected_driver_version"]
    ):
        raise QualificationError("driver contract review mismatch")
    if sorted(value["approved_capabilities"]) != contract["capabilities"]:
        raise QualificationError("driver contract review capability mismatch")
    if value["independent_from_qualification_request"] is not True:
        raise QualificationError("driver contract review must be independent")
    if parse_utc(
        value["reviewed_at_utc"], "driver contract review timestamp"
    ) > parse_utc(request["created_at_utc"], "request created_at"):
        raise QualificationError("driver contract review is future-dated")
    as_text(value["reviewer_identity"], "driver contract reviewer", maximum=200)
    as_text(value["review_reference"], "driver contract review reference", maximum=200)
    return value


def validate_acquisition_state(
    value: dict[str, Any],
    request: dict[str, Any],
    board: dict[str, Any],
) -> dict[str, Any]:
    exact_keys(
        value,
        {
            "state_schema_version",
            "captured_at_utc",
            "capture_method",
            "board_machine_id",
            "ssh_host_key_sha256",
            "input_state",
            "restore_verification_method",
        },
        "acquisition pre-state",
    )
    if value["state_schema_version"] != 1:
        raise QualificationError("acquisition pre-state schema must equal 1")
    if value["board_machine_id"] != board["identity"]["machine_id"]:
        raise QualificationError("acquisition pre-state board identity mismatch")
    if value["ssh_host_key_sha256"] != board["connection"]["ssh_host_key_sha256"]:
        raise QualificationError("acquisition pre-state host-key mismatch")
    state = as_object(value["input_state"], "acquisition pre-state input_state")
    exact_keys(
        state,
        {
            "state_format_version",
            "channel",
            "enabled",
            "decimation",
            "trigger_source",
            "trigger_level_volts",
            "trigger_delay_samples",
            "gain_mode",
            "coupling",
            "termination_ohms",
            "sample_units",
        },
        "acquisition pre-state input_state",
    )
    if state["state_format_version"] != 1:
        raise QualificationError("input state_format_version must equal 1")
    if state["channel"] != request["topology"]["input_channel"]:
        raise QualificationError("acquisition pre-state channel mismatch")
    as_bool(state["enabled"], "input_state.enabled")
    as_int(state["decimation"], "input_state.decimation", minimum=1)
    as_number(state["trigger_level_volts"], "input_state.trigger_level_volts")
    as_int(state["trigger_delay_samples"], "input_state.trigger_delay_samples")
    as_number(state["termination_ohms"], "input_state.termination_ohms", exclusive_minimum=0)
    for key in ("trigger_source", "gain_mode", "coupling", "sample_units"):
        as_text(state[key], f"input_state.{key}", maximum=100)
    _time_age(
        parse_utc(request["created_at_utc"], "request created_at"),
        parse_utc(value["captured_at_utc"], "acquisition pre-state captured_at"),
        request["probe_max_age_seconds"],
        "acquisition pre-state",
    )
    as_text(value["capture_method"], "acquisition capture_method")
    as_text(value["restore_verification_method"], "restore verification method")
    return value


def validate_writer_inventory(
    value: dict[str, Any],
    request: dict[str, Any],
    board: dict[str, Any],
) -> dict[str, Any]:
    """Prove that the cooperative writer lock has no known bypass path."""
    category_names = {
        "legacy_web_apis",
        "lua_endpoints",
        "root_helpers",
        "services",
        "devmem_tools",
    }
    exact_keys(
        value,
        {
            "writer_inventory_schema_version",
            "captured_at_utc",
            "board_machine_id",
            "ssh_host_key_sha256",
            "read_only_inventory",
            "categories",
            "cooperative_lock_id",
            "cooperative_lock_scope",
            "cooperative_lock_bypass_reviewed",
            "all_processes_reviewed",
            "bypass_capable_writers",
            "result",
            "reviewed_by",
            "review_reference",
        },
        "writer inventory evidence",
    )
    if value["writer_inventory_schema_version"] != 1:
        raise QualificationError("writer inventory schema must equal 1")
    if value["board_machine_id"] != board["identity"]["machine_id"]:
        raise QualificationError("writer inventory board identity mismatch")
    if value["ssh_host_key_sha256"] != board["connection"]["ssh_host_key_sha256"]:
        raise QualificationError("writer inventory host-key mismatch")
    if as_bool(value["read_only_inventory"], "writer inventory read_only_inventory") is not True:
        raise QualificationError("writer inventory collection must be read-only")
    _time_age(
        parse_utc(request["created_at_utc"], "request created_at"),
        parse_utc(value["captured_at_utc"], "writer inventory captured_at"),
        request["probe_max_age_seconds"],
        "writer inventory",
    )
    categories = as_object(value["categories"], "writer inventory categories")
    exact_keys(categories, category_names, "writer inventory categories")
    plan_participants: list[str] = []
    normalized_categories: dict[str, Any] = {}
    for category_name in sorted(category_names):
        category = as_object(
            categories[category_name],
            f"writer inventory categories.{category_name}",
        )
        exact_keys(
            category,
            {"inspection_method", "observations"},
            f"writer inventory categories.{category_name}",
        )
        method = as_text(
            category["inspection_method"],
            f"writer inventory categories.{category_name}.inspection_method",
        )
        observations = []
        identifiers: set[str] = set()
        for index, raw in enumerate(
            as_list(
                category["observations"],
                f"writer inventory categories.{category_name}.observations",
            )
        ):
            label = f"writer inventory {category_name}[{index}]"
            item = as_object(raw, label)
            exact_keys(
                item,
                {
                    "identifier",
                    "write_capable",
                    "state",
                    "control",
                    "owner",
                    "evidence",
                },
                label,
            )
            identifier = as_id(item["identifier"], f"{label}.identifier")
            if identifier in identifiers:
                raise QualificationError(
                    f"duplicate writer inventory identifier in {category_name}"
                )
            identifiers.add(identifier)
            write_capable = as_bool(item["write_capable"], f"{label}.write_capable")
            state = as_text(item["state"], f"{label}.state", maximum=64)
            control = as_text(item["control"], f"{label}.control", maximum=32)
            if control not in {
                "none",
                "disabled",
                "stopped",
                "plan-lock-participant",
                "bypass",
            }:
                raise QualificationError(f"{label}.control is invalid")
            owner = item["owner"]
            if owner is not None:
                owner = as_id(owner, f"{label}.owner")
            if control == "bypass":
                raise QualificationError(
                    f"bypass-capable generator writer found: {identifier}"
                )
            if write_capable:
                if control not in {"disabled", "stopped", "plan-lock-participant"}:
                    raise QualificationError(
                        f"write-capable inventory item {identifier} is uncontrolled"
                    )
                if control == "plan-lock-participant":
                    if (
                        identifier != request["safety"]["writer_id"]
                        or owner not in {None, request["safety"]["writer_id"]}
                    ):
                        raise QualificationError(
                            "plan-lock participant does not match immutable writer_id"
                        )
                    plan_participants.append(identifier)
            elif control != "none":
                raise QualificationError(
                    f"non-writer inventory item {identifier} has writer control"
                )
            observations.append(
                {
                    "identifier": identifier,
                    "write_capable": write_capable,
                    "state": state,
                    "control": control,
                    "owner": owner,
                    "evidence": as_text(item["evidence"], f"{label}.evidence"),
                }
            )
        normalized_categories[category_name] = {
            "inspection_method": method,
            "observations": observations,
        }
    bypass = as_list(
        value["bypass_capable_writers"],
        "writer inventory bypass_capable_writers",
    )
    if bypass:
        raise QualificationError("writer inventory reports bypass-capable writers")
    if (
        as_bool(
            value["cooperative_lock_bypass_reviewed"],
            "writer inventory cooperative_lock_bypass_reviewed",
        )
        is not True
    ):
        raise QualificationError("cooperative lock bypass paths were not reviewed")
    if (
        as_bool(
            value["all_processes_reviewed"],
            "writer inventory all_processes_reviewed",
        )
        is not True
    ):
        raise QualificationError("writer inventory does not cover all processes")
    if value["result"] != "exclusive":
        raise QualificationError("writer inventory result is not exclusive")
    if plan_participants != [request["safety"]["writer_id"]]:
        raise QualificationError(
            "writer inventory must identify exactly one plan-lock participant"
        )
    for key in (
        "cooperative_lock_id",
        "cooperative_lock_scope",
        "reviewed_by",
        "review_reference",
    ):
        as_text(value[key], f"writer inventory {key}")
    return {
        **value,
        "categories": normalized_categories,
        "bypass_capable_writers": [],
        "plan_lock_participant": request["safety"]["writer_id"],
    }


def validate_fpga_release(
    project_root: Path,
    value: dict[str, Any],
    request: dict[str, Any],
    board: dict[str, Any],
    driver_contract_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind the loaded FPGA identity to reviewed package and reports."""
    exact_keys(
        value,
        {
            "fpga_identity_evidence_schema_version",
            "captured_at_utc",
            "board_machine_id",
            "ssh_host_key_sha256",
            "read_only_probe",
            "loaded_build_id",
            "build_identity_source",
            "qualified_package_path",
            "qualified_package_sha256",
            "artifact_path",
            "loaded_artifact_sha256",
            "release_receipt_path",
            "qualified_release_receipt_sha256",
            "release_review_path",
            "register_contract_sha256",
        },
        "FPGA identity evidence",
    )
    if value["fpga_identity_evidence_schema_version"] != 1:
        raise QualificationError("FPGA identity evidence schema must equal 1")
    if value["board_machine_id"] != board["identity"]["machine_id"]:
        raise QualificationError("loaded FPGA board identity mismatch")
    if value["ssh_host_key_sha256"] != board["connection"]["ssh_host_key_sha256"]:
        raise QualificationError("loaded FPGA host-key mismatch")
    if value["read_only_probe"] is not True:
        raise QualificationError("loaded FPGA build identity probe must be read-only")
    _time_age(
        parse_utc(request["created_at_utc"], "request created_at"),
        parse_utc(value["captured_at_utc"], "FPGA identity captured_at"),
        request["probe_max_age_seconds"],
        "loaded FPGA identity",
    )
    build_id = as_text(value["loaded_build_id"], "loaded FPGA build_id", maximum=64)
    if not BUILD_ID_RE.fullmatch(build_id):
        raise QualificationError(
            "loaded FPGA build_id must be 128- to 256-bit lowercase hexadecimal"
        )
    as_text(value["build_identity_source"], "FPGA build_identity_source")
    declared_contract_hash = as_text(
        value["register_contract_sha256"],
        "FPGA identity register_contract_sha256",
        maximum=64,
    )
    if declared_contract_hash != driver_contract_sha256:
        raise QualificationError("loaded FPGA/register contract binding mismatch")

    path_specs = {
        "fpga_package": (
            value["qualified_package_path"],
            value["qualified_package_sha256"],
            "qualified FPGA package",
        ),
        "fpga_artifact": (
            value["artifact_path"],
            value["loaded_artifact_sha256"],
            "loaded FPGA artifact",
        ),
        "fpga_release_receipt": (
            value["release_receipt_path"],
            value["qualified_release_receipt_sha256"],
            "FPGA release receipt",
        ),
        "fpga_release_review": (
            value["release_review_path"],
            None,
            "FPGA release review",
        ),
    }
    bindings: dict[str, Any] = {}
    paths: dict[str, Path] = {}
    for key, (path_text, declared_hash, label) in path_specs.items():
        binding, path = bind_file(
            project_root,
            Path(safe_relative(path_text, f"{label} path")),
            label,
        )
        if declared_hash is not None:
            digest = as_text(declared_hash, f"{label} declared SHA-256", maximum=64)
            if not HASH_RE.fullmatch(digest) or digest != binding["sha256"]:
                raise QualificationError(f"{label} hash mismatch")
        bindings[key] = binding
        paths[key] = path

    receipt = load_json(paths["fpga_release_receipt"], "FPGA release receipt")
    exact_keys(
        receipt,
        {
            "release_receipt_schema_version",
            "result",
            "build_id",
            "package_sha256",
            "artifact_sha256",
            "register_contract_sha256",
            "created_at_utc",
            "issuer_identity",
            "receipt_reference",
        },
        "FPGA release receipt",
    )
    if (
        receipt["release_receipt_schema_version"] != 1
        or receipt["result"] != "qualified"
        or receipt["build_id"] != build_id
        or receipt["package_sha256"] != bindings["fpga_package"]["sha256"]
        or receipt["artifact_sha256"] != bindings["fpga_artifact"]["sha256"]
        or receipt["register_contract_sha256"] != driver_contract_sha256
    ):
        raise QualificationError("loaded FPGA release receipt binding mismatch")
    receipt_created = parse_utc(
        receipt["created_at_utc"], "FPGA release receipt created_at"
    )
    as_text(receipt["issuer_identity"], "FPGA release receipt issuer", maximum=200)
    as_text(receipt["receipt_reference"], "FPGA release receipt reference", maximum=200)

    review = load_json(paths["fpga_release_review"], "FPGA release review")
    exact_keys(
        review,
        {
            "fpga_release_review_schema_version",
            "result",
            "build_id",
            "package_sha256",
            "artifact_sha256",
            "release_receipt_sha256",
            "register_contract_sha256",
            "timing",
            "constraints",
            "drc",
            "independent_from_builder",
            "reviewed_at_utc",
            "reviewer_identity",
            "review_reference",
        },
        "FPGA release review",
    )
    if (
        review["fpga_release_review_schema_version"] != 1
        or review["result"] != "approved"
        or review["build_id"] != build_id
        or review["package_sha256"] != bindings["fpga_package"]["sha256"]
        or review["artifact_sha256"] != bindings["fpga_artifact"]["sha256"]
        or review["release_receipt_sha256"]
        != bindings["fpga_release_receipt"]["sha256"]
        or review["register_contract_sha256"] != driver_contract_sha256
    ):
        raise QualificationError("loaded FPGA release review binding mismatch")
    if review["independent_from_builder"] is not True:
        raise QualificationError("FPGA release review must be independent from builder")
    reviewed_at = parse_utc(review["reviewed_at_utc"], "FPGA review reviewed_at")
    request_created = parse_utc(request["created_at_utc"], "request created_at")
    if receipt_created > reviewed_at or reviewed_at > request_created:
        raise QualificationError(
            "FPGA release receipt/review timestamps are out of order or future-dated"
        )
    as_text(review["reviewer_identity"], "FPGA release reviewer", maximum=200)
    as_text(review["review_reference"], "FPGA release review reference", maximum=200)

    status_bindings: dict[str, Any] = {}
    status_summary: dict[str, Any] = {}
    for status_name, binding_key in (
        ("timing", "fpga_timing_report"),
        ("constraints", "fpga_constraints_report"),
        ("drc", "fpga_drc_report"),
    ):
        status = as_object(review[status_name], f"FPGA review {status_name}")
        exact_keys(
            status,
            {"status", "report_path", "report_sha256", "summary"},
            f"FPGA review {status_name}",
        )
        if status["status"] != "passed":
            raise QualificationError(
                f"FPGA {status_name} release status is blocked, failed or incomplete"
            )
        report_binding, _ = bind_file(
            project_root,
            Path(
                safe_relative(
                    status["report_path"],
                    f"FPGA {status_name} report path",
                )
            ),
            f"FPGA {status_name} report",
        )
        declared = as_text(
            status["report_sha256"],
            f"FPGA {status_name} report SHA-256",
            maximum=64,
        )
        if not HASH_RE.fullmatch(declared) or declared != report_binding["sha256"]:
            raise QualificationError(f"FPGA {status_name} report hash mismatch")
        status_bindings[binding_key] = report_binding
        status_summary[status_name] = {
            "status": "passed",
            "report_sha256": declared,
            "summary": as_text(
                status["summary"], f"FPGA {status_name} summary"
            ),
        }
    bindings.update(status_bindings)
    return {
        "loaded_build_id": build_id,
        "build_identity_source": value["build_identity_source"],
        "package_sha256": bindings["fpga_package"]["sha256"],
        "artifact_sha256": bindings["fpga_artifact"]["sha256"],
        "release_receipt_sha256": bindings["fpga_release_receipt"]["sha256"],
        "release_review_sha256": bindings["fpga_release_review"]["sha256"],
        "register_contract_sha256": driver_contract_sha256,
        "release_status": status_summary,
        "reviewer_identity": review["reviewer_identity"],
        "review_reference": review["review_reference"],
    }, bindings


def validate_watchdog(
    value: dict[str, Any],
    request: dict[str, Any],
    board: dict[str, Any],
) -> dict[str, Any]:
    exact_keys(
        value,
        {
            "watchdog_evidence_schema_version",
            "captured_at_utc",
            "board_machine_id",
            "ssh_host_key_sha256",
            "watchdog_type",
            "independent_from_writer",
            "timeout_seconds",
            "cutoff_method",
            "verification_result",
            "output_disabled_after_cutoff",
            "verification_reference",
            "verified_by",
        },
        "watchdog evidence",
    )
    if value["watchdog_evidence_schema_version"] != 1:
        raise QualificationError("watchdog evidence schema must equal 1")
    if value["board_machine_id"] != board["identity"]["machine_id"]:
        raise QualificationError("watchdog evidence board identity mismatch")
    if value["ssh_host_key_sha256"] != board["connection"]["ssh_host_key_sha256"]:
        raise QualificationError("watchdog evidence host-key mismatch")
    if value["watchdog_type"] not in {"hardware-backed", "independent-supervisor"}:
        raise QualificationError("watchdog must be hardware-backed or independently supervised")
    if value["independent_from_writer"] is not True:
        raise QualificationError("watchdog/cutoff must be independent from the writer")
    timeout = as_number(value["timeout_seconds"], "watchdog timeout", exclusive_minimum=0, maximum=5)
    if timeout > request["safety"]["watchdog_timeout_seconds"]:
        raise QualificationError("watchdog evidence timeout exceeds plan request")
    if value["verification_result"] != "passed" or value["output_disabled_after_cutoff"] is not True:
        raise QualificationError("independent watchdog cutoff evidence is missing or failed")
    _time_age(
        parse_utc(request["created_at_utc"], "request created_at"),
        parse_utc(value["captured_at_utc"], "watchdog evidence captured_at"),
        request["probe_max_age_seconds"],
        "watchdog evidence",
    )
    for key in ("cutoff_method", "verification_reference", "verified_by"):
        as_text(value[key], f"watchdog evidence {key}")
    return {**value, "timeout_seconds": timeout}


def validate_authorization(
    project_root: Path,
    value: dict[str, Any],
    request: dict[str, Any],
    board: dict[str, Any],
    request_hash: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    exact_keys(
        value,
        {
            "authorization_bundle_schema_version",
            "source_skill",
            "deployment_plan",
            "qualification_binding",
        },
        "authorization bundle",
    )
    if value["authorization_bundle_schema_version"] != 1:
        raise QualificationError("authorization bundle schema must equal 1")
    if value["source_skill"] != "deploy-red-pitaya-project":
        raise QualificationError("authorization must derive through deploy-red-pitaya-project")
    plan_binding, plan_path = bind_file(
        project_root,
        Path(safe_relative(value["deployment_plan"], "deployment_plan")),
        "deployment plan",
    )
    binding_binding, binding_path = bind_file(
        project_root,
        Path(safe_relative(value["qualification_binding"], "qualification_binding")),
        "qualification authorization",
    )
    deployment_plan = load_json(plan_path, "deployment plan")
    _, _, validator = deployment_modules()
    errors, warnings = validator.validate(deployment_plan, project_root, True)
    if errors or warnings:
        raise QualificationError(
            "deployment plan failed deep validation: "
            + "; ".join([*errors, *warnings])
        )
    gates = as_object(deployment_plan.get("gates"), "deployment gates")
    for gate in (
        "stage_authorized",
        "precommit_live_actions_authorized",
        "functional_output_test_authorized",
    ):
        if gates.get(gate) is not True:
            raise QualificationError(f"deployment gate {gate} is not authorized")
    target = as_object(deployment_plan.get("target"), "deployment target")
    target_identity = as_object(target.get("identity"), "deployment target identity")
    target_connection = as_object(target.get("connection"), "deployment target connection")
    if target_identity.get("machine_id") != board["identity"]["machine_id"]:
        raise QualificationError("deployment plan board identity mismatch")
    if target_connection.get("ssh_host_key_sha256") != board["connection"]["ssh_host_key_sha256"]:
        raise QualificationError("deployment plan host-key mismatch")
    if target.get("role") != "generator":
        raise QualificationError("deployment plan target role must be generator")
    contract = as_object(deployment_plan.get("contract"), "deployment contract")
    deployment_contract = as_object(contract.get("deployment"), "deployment contract.deployment")
    safety_contract = as_object(contract.get("safety"), "deployment contract.safety")
    if deployment_contract.get("allow_live_output_test") is not True:
        raise QualificationError("deployment contract does not allow live output test")
    ceiling = as_number(
        safety_contract.get("maximum_output_volts"),
        "deployment maximum_output_volts",
        exclusive_minimum=0,
    )
    if request["safety"]["maximum_output_volts_peak"] > ceiling:
        raise QualificationError("request Vpeak exceeds deployment safety ceiling")
    receipt = as_object(
        as_object(deployment_plan.get("gate_evidence"), "deployment gate evidence").get(
            "functional_output_test_authorized"
        ),
        "functional output authorization receipt",
    )
    auth = load_json(binding_path, "qualification authorization")
    exact_keys(
        auth,
        {
            "qualification_authorization_schema_version",
            "source_skill",
            "result",
            "qualification_id",
            "request_sha256",
            "deployment_id",
            "deployment_plan_core_sha256",
            "functional_output_receipt_sha256",
            "maximum_output_volts_peak",
            "voltage_unit",
            "single_use_nonce",
            "recorded_at_utc",
            "expires_at_utc",
            "approver_identity",
            "authorization_reference",
        },
        "qualification authorization",
    )
    if (
        auth["qualification_authorization_schema_version"] != 1
        or auth["source_skill"] != "deploy-red-pitaya-project"
        or auth["result"] != "authorized"
        or auth["qualification_id"] != request["qualification_id"]
        or auth["request_sha256"] != request_hash
        or auth["deployment_id"] != deployment_plan["deployment_id"]
        or auth["deployment_plan_core_sha256"] != deployment_plan["plan_core_sha256"]
        or auth["functional_output_receipt_sha256"] != receipt.get("sha256")
        or auth["voltage_unit"] != "Vpeak"
    ):
        raise QualificationError("qualification authorization binding mismatch")
    authorized_ceiling = as_number(
        auth["maximum_output_volts_peak"],
        "authorized maximum_output_volts_peak",
        exclusive_minimum=0,
    )
    if request["safety"]["maximum_output_volts_peak"] > authorized_ceiling:
        raise QualificationError("request Vpeak exceeds authorization ceiling")
    recorded = parse_utc(auth["recorded_at_utc"], "authorization recorded_at")
    expires = parse_utc(auth["expires_at_utc"], "authorization expires_at")
    request_time = parse_utc(request["created_at_utc"], "request created_at")
    if recorded < request_time or expires <= recorded or expires - recorded > timedelta(hours=1):
        raise QualificationError("qualification authorization is stale or overlong")
    if request_time > expires:
        raise QualificationError("qualification authorization is expired")
    nonce = as_id(auth["single_use_nonce"], "authorization single_use_nonce")
    as_text(auth["approver_identity"], "authorization approver_identity", maximum=200)
    as_text(auth["authorization_reference"], "authorization_reference", maximum=200)
    return {
        "source_skill": "deploy-red-pitaya-project",
        "deployment_id": deployment_plan["deployment_id"],
        "deployment_plan_core_sha256": deployment_plan["plan_core_sha256"],
        "functional_output_receipt_sha256": receipt["sha256"],
        "maximum_output_volts_peak": authorized_ceiling,
        "single_use_nonce": nonce,
        "recorded_at_utc": auth["recorded_at_utc"],
        "expires_at_utc": auth["expires_at_utc"],
        "approver_identity": auth["approver_identity"],
        "authorization_reference": auth["authorization_reference"],
    }, {
        "deployment_plan": plan_binding,
        "qualification_binding": binding_binding,
    }


def build_plan(
    project_root: Path,
    request_path: Path,
    probe_report_path: Path,
    raw_probe_path: Path,
    target_manifest_path: Path,
    host_key_path: Path,
    topology_path: Path,
    driver_evidence_path: Path,
    driver_contract_path: Path,
    acquisition_pre_state_path: Path,
    writer_inventory_path: Path | None,
    fpga_identity_path: Path | None,
    watchdog_path: Path | None,
    authorization_bundle_path: Path | None,
    *,
    enforce_unused_nonce: bool = True,
) -> dict[str, Any]:
    request_binding, request_file = bind_file(project_root, request_path, "request")
    request_raw = load_json(request_file, "request")
    request = validate_request(request_raw)
    request_hash = request_binding["sha256"]
    board, probe_bindings = validate_probe_and_host_key(
        project_root,
        request,
        probe_report_path,
        raw_probe_path,
        target_manifest_path,
        host_key_path,
    )
    topology_binding, topology_file = bind_file(
        project_root, topology_path, "topology evidence"
    )
    topology = validate_topology(
        load_json(topology_file, "topology evidence"), request, board
    )
    driver_contract_binding, driver_contract_file = bind_file(
        project_root, driver_contract_path, "driver contract"
    )
    driver_evidence_binding, driver_evidence_file = bind_file(
        project_root, driver_evidence_path, "driver evidence"
    )
    driver_gate, driver_contract = validate_driver(
        load_json(driver_contract_file, "driver contract"),
        load_json(driver_evidence_file, "driver evidence"),
        request,
        board,
        driver_contract_binding["sha256"],
    )
    review_binding = None
    review = None
    if request["mode"] == "active":
        review_path = Path(request["driver_expectation"]["contract_review_path"])
        review_binding, review_file = bind_file(
            project_root, review_path, "driver contract review"
        )
        review = validate_contract_review(
            load_json(review_file, "driver contract review"), request, driver_contract
        )
    acquisition_binding, acquisition_file = bind_file(
        project_root, acquisition_pre_state_path, "acquisition pre-state"
    )
    acquisition_state = validate_acquisition_state(
        load_json(acquisition_file, "acquisition pre-state"), request, board
    )

    writer_inventory_binding = None
    writer_inventory = None
    fpga_identity_binding = None
    fpga_release = None
    fpga_release_inputs = {
        "fpga_package": None,
        "fpga_artifact": None,
        "fpga_release_receipt": None,
        "fpga_release_review": None,
        "fpga_timing_report": None,
        "fpga_constraints_report": None,
        "fpga_drc_report": None,
    }
    watchdog_binding = None
    watchdog = None
    authorization_binding = None
    authorization = None
    authorization_inputs = {"deployment_plan": None, "qualification_binding": None}
    if request["mode"] == "active":
        if writer_inventory_path is None:
            raise QualificationError(
                "active qualification requires writer inventory evidence"
            )
        if fpga_identity_path is None:
            raise QualificationError(
                "active qualification requires loaded FPGA identity evidence"
            )
        if watchdog_path is None:
            raise QualificationError("active qualification requires watchdog evidence")
        if authorization_bundle_path is None:
            raise QualificationError("active qualification requires deployment authorization")
        writer_inventory_binding, writer_inventory_file = bind_file(
            project_root, writer_inventory_path, "writer inventory evidence"
        )
        writer_inventory = validate_writer_inventory(
            load_json(writer_inventory_file, "writer inventory evidence"),
            request,
            board,
        )
        fpga_identity_binding, fpga_identity_file = bind_file(
            project_root, fpga_identity_path, "FPGA identity evidence"
        )
        fpga_release, fpga_release_inputs = validate_fpga_release(
            project_root,
            load_json(fpga_identity_file, "FPGA identity evidence"),
            request,
            board,
            driver_contract_binding["sha256"],
        )
        watchdog_binding, watchdog_file = bind_file(
            project_root, watchdog_path, "watchdog evidence"
        )
        watchdog = validate_watchdog(
            load_json(watchdog_file, "watchdog evidence"), request, board
        )
        authorization_binding, authorization_file = bind_file(
            project_root, authorization_bundle_path, "authorization bundle"
        )
        authorization, authorization_inputs = validate_authorization(
            project_root,
            load_json(authorization_file, "authorization bundle"),
            request,
            board,
            request_hash,
        )
        run_record = project_root.resolve() / request["run_record_path"]
        try:
            run_record.resolve().relative_to(project_root.resolve())
        except ValueError as exc:
            raise QualificationError("run_record_path escapes project root") from exc
        if enforce_unused_nonce and (run_record.exists() or run_record.is_symlink()):
            raise QualificationError("single-use authorization is replayed: run record exists")
    else:
        if any(
            value is not None
            for value in (
                writer_inventory_path,
                fpga_identity_path,
                watchdog_path,
                authorization_bundle_path,
            )
        ):
            raise QualificationError(
                "read-only mode must omit writer inventory, FPGA identity, "
                "watchdog and authorization"
            )

    phases = [
        {
            "phase_id": "identity-and-safe-state",
            "ordinal": 1,
            "output_allowed": False,
            "purpose": "Re-probe identity and confirm OUT1 disabled or physically isolated.",
        },
        {
            "phase_id": "baseline",
            "ordinal": 2,
            "output_allowed": False,
            "purpose": "Capture IN1 baseline before any output command.",
        },
    ]
    if request["mode"] == "active":
        phases.extend(
            [
                {
                    "phase_id": "verify-loaded-fpga-and-writers",
                    "ordinal": 3,
                    "output_allowed": False,
                    "purpose": (
                        "Re-read the loaded build identity and writer inventory; "
                        "abort on drift or any bypass-capable writer."
                    ),
                },
                {
                    "phase_id": "arm-independent-watchdog",
                    "ordinal": 4,
                    "output_allowed": False,
                    "purpose": "Arm verified cutoff before acquiring the writer lease.",
                },
                {
                    "phase_id": "low-amplitude-one-shot",
                    "ordinal": 5,
                    "output_allowed": True,
                    "purpose": "Emit exactly one plan-bound candidate for no more than five seconds.",
                },
                {
                    "phase_id": "safe-stop",
                    "ordinal": 6,
                    "output_allowed": False,
                    "purpose": "Disable OUT1 and independently verify quiet input.",
                },
                {
                    "phase_id": "restore-acquisition",
                    "ordinal": 7,
                    "output_allowed": False,
                    "purpose": "Restore and exactly read back the bound IN1 pre-state.",
                },
            ]
        )
    else:
        phases.append(
            {
                "phase_id": "read-only-finish",
                "ordinal": 3,
                "output_allowed": False,
                "purpose": "Preserve observations; make no driver or acquisition writes.",
            }
        )

    inputs = {
        "request": request_binding,
        **probe_bindings,
        "topology_evidence": topology_binding,
        "driver_evidence": driver_evidence_binding,
        "driver_contract": driver_contract_binding,
        "driver_contract_review": review_binding,
        "acquisition_pre_state": acquisition_binding,
        "writer_inventory_evidence": writer_inventory_binding,
        "fpga_identity_evidence": fpga_identity_binding,
        **fpga_release_inputs,
        "watchdog_evidence": watchdog_binding,
        "authorization_bundle": authorization_binding,
        **authorization_inputs,
    }
    if set(inputs) != INPUT_KEYS:
        raise QualificationError("internal input binding set is invalid")
    plan = {
        "plan_schema_version": 1,
        "plan_kind": "red-pitaya-single-board-loopback-qualification",
        "qualification_id": request["qualification_id"],
        "created_at_utc": request["created_at_utc"],
        "mode": request["mode"],
        "purpose": request["purpose"],
        "inputs": inputs,
        "board": board,
        "topology": topology,
        "driver_gate": driver_gate,
        "driver_contract": driver_contract,
        "driver_contract_review": review,
        "acquisition": {
            "settings": request["acquisition"],
            "pre_state": acquisition_state,
        },
        "safety": request["safety"],
        "writer_inventory": writer_inventory,
        "fpga_release": fpga_release,
        "watchdog": watchdog,
        "authorization": authorization,
        "one_shot": request["one_shot"],
        "acceptance": request["acceptance"],
        "phases": phases,
        "execution_contract": {
            "one_exact_board": True,
            "writer_id": request["safety"]["writer_id"],
            "cooperative_lock_id": (
                None
                if writer_inventory is None
                else writer_inventory["cooperative_lock_id"]
            ),
            "writer_inventory_exclusive": request["mode"] == "active",
            "cooperative_lock_bypass_inventory_required": (
                request["mode"] == "active"
            ),
            "loaded_fpga_release_required": request["mode"] == "active",
            "loaded_fpga_build_id": (
                None
                if fpga_release is None
                else fpga_release["loaded_build_id"]
            ),
            "loaded_fpga_identity_must_be_reread_before_output": (
                request["mode"] == "active"
            ),
            "run_record_path": request["run_record_path"],
            "single_use_nonce": (
                None if authorization is None else authorization["single_use_nonce"]
            ),
            "baseline_must_precede_output": True,
            "low_amplitude_one_shot_only": request["mode"] == "active",
            "maximum_output_window_seconds": request["safety"][
                "maximum_output_window_seconds"
            ],
            "zero_dead_time_claim_permitted": False,
            "counter_terms": {
                "emitted": "generator hardware candidate counter delta",
                "detected": "IN1 detector candidate count",
                "interpretation": (
                    "Candidate accounting only; it does not prove zero dead time "
                    "or complete capture between records."
                ),
            },
        },
        "plan_sha256": "",
    }
    plan["plan_sha256"] = canonical_sha256(
        {key: value for key, value in plan.items() if key != "plan_sha256"}
    )
    return plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--probe-report", required=True, type=Path)
    parser.add_argument("--raw-probe", required=True, type=Path)
    parser.add_argument("--target-manifest", required=True, type=Path)
    parser.add_argument("--host-key-evidence", required=True, type=Path)
    parser.add_argument("--topology-evidence", required=True, type=Path)
    parser.add_argument("--driver-evidence", required=True, type=Path)
    parser.add_argument("--driver-contract", required=True, type=Path)
    parser.add_argument("--acquisition-pre-state", required=True, type=Path)
    parser.add_argument("--writer-inventory-evidence", type=Path)
    parser.add_argument("--fpga-identity-evidence", type=Path)
    parser.add_argument("--watchdog-evidence", type=Path)
    parser.add_argument("--authorization-bundle", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output = exclusive_project_output(args.project_root, args.output, "plan output")
        plan = build_plan(
            args.project_root,
            args.request,
            args.probe_report,
            args.raw_probe,
            args.target_manifest,
            args.host_key_evidence,
            args.topology_evidence,
            args.driver_evidence,
            args.driver_contract,
            args.acquisition_pre_state,
            args.writer_inventory_evidence,
            args.fpga_identity_evidence,
            args.watchdog_evidence,
            args.authorization_bundle,
        )
        write_json_exclusive(output, plan, "plan output")
        print(
            f"Wrote immutable {plan['mode']} single-board plan "
            f"{plan['plan_sha256']}. No board was contacted."
        )
        return 0
    except (OSError, QualificationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
