#!/usr/bin/env python3
"""Generate an immutable two-board loopback qualification plan offline.

This program only reads local files and writes one new JSON plan. It has no
network, SSH, subprocess or hardware-control code.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import re
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from qualification_common import (
    HASH_RE,
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
    require_ssh_sha256_fingerprint,
    resolve_project_file,
    sha256_file,
    voltage_after_attenuation,
    write_json_exclusive,
)


REQUIRED_METRICS = (
    "emitted_count",
    "local_detected_count",
    "remote_detected_count",
    "local_delay_seconds",
    "remote_delay_seconds",
    "local_gain_ratio",
    "remote_gain_ratio",
    "local_correlation",
    "remote_correlation",
    "local_polarity",
    "remote_polarity",
    "local_width_seconds",
    "remote_width_seconds",
    "local_energy_volt2_seconds",
    "remote_energy_volt2_seconds",
    "local_ringing_hz",
    "remote_ringing_hz",
    "local_phase_degrees",
    "remote_phase_degrees",
    "detector_dead_time_seconds",
    "local_data_loss_fraction",
    "remote_data_loss_fraction",
    "local_drop_count",
    "remote_drop_count",
    "counter_reset_behavior",
    "counter_rollover_behavior",
)

NUMERIC_METRICS = frozenset(
    metric
    for metric in REQUIRED_METRICS
    if metric not in {"local_polarity", "remote_polarity",
                      "counter_reset_behavior", "counter_rollover_behavior"}
)

ACTIVE_REQUIRED_CAPABILITIES = frozenset(
    {"event-counter", "safe-stop", "single-writer", "watchdog"}
)

ACTIVE_REQUIRED_REGISTER_ROLES = frozenset(
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

AUTHORIZATION_GATES = {
    "generator": (
        "stage_authorized",
        "precommit_live_actions_authorized",
        "functional_output_test_authorized",
    ),
    "acquisition": (
        "stage_authorized",
        "precommit_live_actions_authorized",
    ),
}

AUTHORIZATION_SCOPES = {
    "stage_authorized": "stage",
    "precommit_live_actions_authorized": "precommit-live-actions",
    "functional_output_test_authorized": "physical-output-test",
}


def deployment_modules() -> tuple[Any, Any, Any]:
    """Load the sibling deployment skill without copying its safety logic."""
    scripts = (
        Path(__file__).resolve().parents[2]
        / "deploy-red-pitaya-project"
        / "scripts"
    )
    if not scripts.is_dir():
        raise QualificationError(
            "required sibling skill deploy-red-pitaya-project is missing"
        )
    scripts_text = str(scripts)
    if scripts_text not in sys.path:
        sys.path.insert(0, scripts_text)
    try:
        import analyze_probe
        import project_contract
        import validate_deployment_plan
    except ImportError as exc:
        raise QualificationError(
            f"cannot load deployment skill validators: {exc}"
        ) from exc
    expected_root = scripts.resolve()
    for module in (analyze_probe, project_contract, validate_deployment_plan):
        module_file = Path(module.__file__).resolve()
        try:
            module_file.relative_to(expected_root)
        except ValueError as exc:
            raise QualificationError(
                f"refusing unexpected deployment validator module {module_file}"
            ) from exc
    return analyze_probe, project_contract, validate_deployment_plan


def validate_project_relative_path(value: Any, label: str) -> str:
    text = require_safe_text(value, label, maximum=500)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise QualificationError(f"{label} must be project-relative")
    return path.as_posix()


def validate_probe_report(
    report: dict[str, Any],
    role: str,
    created_at,
    max_age_seconds: int,
) -> dict[str, Any]:
    if report.get("report_schema_version") != 1:
        raise QualificationError(f"{role} probe report_schema_version must equal 1")
    if report.get("report_kind") != "red-pitaya-probe-report":
        raise QualificationError(f"{role} probe report_kind is invalid")
    if report.get("compatible") is not True:
        raise QualificationError(f"{role} probe is not compatible")
    if report.get("blocking_errors") != []:
        raise QualificationError(f"{role} probe has blocking errors")
    if report.get("warnings") != []:
        raise QualificationError(
            f"{role} probe has unresolved warnings; qualify only exact evidence"
        )

    connection = require_object(report.get("connection"), f"{role} probe.connection")
    identity = require_object(report.get("identity"), f"{role} probe.identity")
    target = require_object(report.get("target"), f"{role} probe.target")
    evidence = require_object(report.get("evidence"), f"{role} probe.evidence")

    if connection.get("host_key_verified") is not True:
        raise QualificationError(f"{role} SSH host key is not verified")
    fingerprint = require_ssh_sha256_fingerprint(
        connection.get("ssh_host_key_sha256"),
        f"{role} SSH fingerprint",
    )
    address = require_safe_text(
        connection.get("address"),
        f"{role} connection.address",
        maximum=255,
    )
    try:
        ipaddress.ip_address(address)
    except ValueError as exc:
        raise QualificationError(f"{role} connection.address is not a valid IP") from exc
    machine_id = require_safe_text(
        identity.get("machine_id"), f"{role} machine_id", maximum=128
    )
    expected_role = target.get("expected_role")
    roles = require_list(identity.get("roles"), f"{role} identity.roles")
    if expected_role != role or target.get("role_exact") is not True or role not in roles:
        raise QualificationError(f"{role} probe does not bind the exact {role} role")

    captured = parse_utc(evidence.get("captured_at"), f"{role} probe captured_at")
    age = (created_at - captured).total_seconds()
    if age < 0:
        raise QualificationError(f"{role} probe capture is in the future")
    if age > max_age_seconds:
        raise QualificationError(
            f"{role} probe is stale ({age:.0f}s > {max_age_seconds}s)"
        )
    require_safe_text(
        evidence.get("probe_source"), f"{role} probe source", maximum=500
    )
    observed_os = require_safe_text(
        target.get("observed_os_release"), f"{role} observed OS", maximum=64
    )
    observed_model = require_safe_text(
        target.get("observed_model"), f"{role} observed model", maximum=128
    )
    if target.get("expected_os_release") != observed_os:
        raise QualificationError(f"{role} expected/observed OS differ")
    if target.get("expected_model") != observed_model:
        raise QualificationError(f"{role} expected/observed model differ")

    return {
        "role": role,
        "connection": {
            "host": require_safe_text(
                connection.get("host"), f"{role} connection.host", maximum=255
            ),
            "address": address,
            "username": require_safe_text(
                connection.get("username"),
                f"{role} connection.username",
                maximum=64,
            ),
            "ssh_host_key_sha256": fingerprint,
        },
        "identity": {
            "hostname": require_safe_text(
                identity.get("hostname"), f"{role} hostname", maximum=255
            ),
            "machine_id": machine_id,
            "serial": require_safe_text(
                identity.get("serial", ""),
                f"{role} serial",
                maximum=255,
                allow_empty=True,
            ),
            "mac_addresses": [
                require_safe_text(item, f"{role} MAC", maximum=64)
                for item in require_list(
                    identity.get("mac_addresses"), f"{role} MAC addresses"
                )
            ],
        },
        "target": {
            "os_release": observed_os,
            "model": observed_model,
        },
        "captured_at_utc": evidence["captured_at"],
        "probe_source": evidence["probe_source"],
    }


def validate_topology(value: Any) -> dict[str, Any]:
    topology = require_object(value, "request.topology")
    require_exact_keys(
        topology,
        {
            "generator_output_channel",
            "generator_local_input",
            "acquisition_remote_input",
            "fanout_description",
            "fanout_verified",
            "shared_reference_verified",
            "local_path",
            "remote_path",
        },
        "request.topology",
    )
    output_channel = require_safe_text(
        topology["generator_output_channel"],
        "topology.generator_output_channel",
        maximum=64,
    )

    def input_endpoint(raw: Any, label: str) -> dict[str, Any]:
        endpoint = require_object(raw, label)
        require_exact_keys(
            endpoint,
            {
                "channel",
                "range_volts_peak",
                "coupling",
                "termination_ohms",
                "range_evidence_path",
            },
            label,
        )
        return {
            "channel": require_safe_text(
                endpoint["channel"], f"{label}.channel", maximum=64
            ),
            "range_volts_peak": require_number(
                endpoint["range_volts_peak"],
                f"{label}.range_volts_peak",
                exclusive_minimum=0,
            ),
            "coupling": require_safe_text(
                endpoint["coupling"], f"{label}.coupling", maximum=32
            ),
            "termination_ohms": require_number(
                endpoint["termination_ohms"],
                f"{label}.termination_ohms",
                exclusive_minimum=0,
            ),
            "range_evidence_path": validate_project_relative_path(
                endpoint["range_evidence_path"],
                f"{label}.range_evidence_path",
            ),
        }

    def signal_path(raw: Any, label: str) -> dict[str, Any]:
        path = require_object(raw, label)
        require_exact_keys(
            path,
            {"attenuation_db", "attenuation_uncertainty_db", "evidence_path"},
            label,
        )
        return {
            "attenuation_db": require_number(
                path["attenuation_db"], f"{label}.attenuation_db", minimum=0
            ),
            "attenuation_uncertainty_db": require_number(
                path["attenuation_uncertainty_db"],
                f"{label}.attenuation_uncertainty_db",
                minimum=0,
            ),
            "evidence_path": validate_project_relative_path(
                path["evidence_path"],
                f"{label}.evidence_path",
            ),
        }

    local_input = input_endpoint(
        topology["generator_local_input"], "topology.generator_local_input"
    )
    remote_input = input_endpoint(
        topology["acquisition_remote_input"], "topology.acquisition_remote_input"
    )
    if output_channel in {local_input["channel"], remote_input["channel"]}:
        raise QualificationError("output and input channel labels must be distinct")
    if require_bool(topology["fanout_verified"], "topology.fanout_verified") is not True:
        raise QualificationError("physical fanout must be verified")
    if (
        require_bool(
            topology["shared_reference_verified"],
            "topology.shared_reference_verified",
        )
        is not True
    ):
        raise QualificationError("shared signal reference/ground must be verified")
    return {
        "generator_output_channel": output_channel,
        "generator_local_input": local_input,
        "acquisition_remote_input": remote_input,
        "fanout_description": require_safe_text(
            topology["fanout_description"],
            "topology.fanout_description",
            maximum=500,
        ),
        "fanout_verified": True,
        "shared_reference_verified": True,
        "local_path": signal_path(topology["local_path"], "topology.local_path"),
        "remote_path": signal_path(topology["remote_path"], "topology.remote_path"),
    }


def validate_safety(value: Any) -> dict[str, Any]:
    safety = require_object(value, "request.safety")
    require_exact_keys(
        safety,
        {
            "maximum_output_volts_peak",
            "voltage_unit",
            "output_limit_evidence_path",
            "deployment_maximum_output_to_vpeak_factor",
            "input_headroom_fraction",
            "one_shot_ceiling_fraction",
            "safe_output_state",
            "emergency_stop_action",
            "emergency_stop_verification",
            "single_writer_id",
            "watchdog_timeout_seconds",
            "maximum_runtime_seconds",
            "restore_acquisition_action",
            "restore_acquisition_verification",
        },
        "request.safety",
    )
    safe_state = require_safe_text(
        safety["safe_output_state"], "safety.safe_output_state", maximum=64
    )
    if safe_state != "disabled":
        raise QualificationError("safety.safe_output_state must equal 'disabled'")
    if safety["voltage_unit"] != "Vpeak":
        raise QualificationError("safety.voltage_unit must equal 'Vpeak'")
    conversion = require_number(
        safety["deployment_maximum_output_to_vpeak_factor"],
        "safety.deployment_maximum_output_to_vpeak_factor",
        exclusive_minimum=0,
    )
    maximum_runtime = require_number(
        safety["maximum_runtime_seconds"],
        "safety.maximum_runtime_seconds",
        exclusive_minimum=0,
    )
    watchdog = require_number(
        safety["watchdog_timeout_seconds"],
        "safety.watchdog_timeout_seconds",
        exclusive_minimum=0,
    )
    if watchdog >= maximum_runtime:
        raise QualificationError("watchdog timeout must be below maximum runtime")
    return {
        "maximum_output_volts_peak": require_number(
            safety["maximum_output_volts_peak"],
            "safety.maximum_output_volts_peak",
            exclusive_minimum=0,
        ),
        "voltage_unit": "Vpeak",
        "output_limit_evidence_path": validate_project_relative_path(
            safety["output_limit_evidence_path"],
            "safety.output_limit_evidence_path",
        ),
        "deployment_maximum_output_to_vpeak_factor": conversion,
        "input_headroom_fraction": require_number(
            safety["input_headroom_fraction"],
            "safety.input_headroom_fraction",
            exclusive_minimum=0,
            maximum=1,
        ),
        "one_shot_ceiling_fraction": require_number(
            safety["one_shot_ceiling_fraction"],
            "safety.one_shot_ceiling_fraction",
            exclusive_minimum=0,
            maximum=0.25,
        ),
        "safe_output_state": safe_state,
        "emergency_stop_action": require_safe_text(
            safety["emergency_stop_action"],
            "safety.emergency_stop_action",
            maximum=500,
        ),
        "emergency_stop_verification": require_safe_text(
            safety["emergency_stop_verification"],
            "safety.emergency_stop_verification",
            maximum=500,
        ),
        "single_writer_id": require_safe_id(
            safety["single_writer_id"], "safety.single_writer_id"
        ),
        "watchdog_timeout_seconds": watchdog,
        "maximum_runtime_seconds": maximum_runtime,
        "restore_acquisition_action": require_safe_text(
            safety["restore_acquisition_action"],
            "safety.restore_acquisition_action",
            maximum=500,
        ),
        "restore_acquisition_verification": require_safe_text(
            safety["restore_acquisition_verification"],
            "safety.restore_acquisition_verification",
            maximum=500,
        ),
    }


def validate_driver_expectation(value: Any) -> dict[str, Any]:
    driver = require_object(value, "request.driver_expectation")
    require_exact_keys(
        driver,
        {
            "classification",
            "expected_magic_ascii",
            "expected_abi_major",
            "minimum_abi_minor",
            "expected_driver_version",
            "approved_contract_sha256",
            "required_capabilities",
            "contract_review_path",
            "advanced_review_path",
        },
        "request.driver_expectation",
    )
    classification = driver["classification"]
    if classification not in {"pdg1", "approved-advanced", "unknown-read-only"}:
        raise QualificationError("driver classification is unsupported")
    capabilities = [
        require_safe_id(item, "driver required capability")
        for item in require_list(
            driver["required_capabilities"], "driver.required_capabilities"
        )
    ]
    if len(set(capabilities)) != len(capabilities):
        raise QualificationError("driver required capabilities contain duplicates")

    if classification == "unknown-read-only":
        for key in (
            "expected_magic_ascii",
            "expected_abi_major",
            "minimum_abi_minor",
            "expected_driver_version",
            "approved_contract_sha256",
            "contract_review_path",
            "advanced_review_path",
        ):
            if driver[key] is not None:
                raise QualificationError(
                    f"driver.{key} must be null for unknown-read-only"
                )
        if capabilities:
            raise QualificationError(
                "unknown-read-only cannot claim required write capabilities"
            )
        return {
            "classification": classification,
            "expected_magic_ascii": None,
            "expected_abi_major": None,
            "minimum_abi_minor": None,
            "expected_driver_version": None,
            "approved_contract_sha256": None,
            "required_capabilities": [],
            "contract_review_path": None,
            "advanced_review_path": None,
        }

    magic = require_safe_text(
        driver["expected_magic_ascii"], "driver.expected_magic_ascii", maximum=16
    )
    if classification == "pdg1" and magic != "PDG1":
        raise QualificationError("pdg1 classification requires magic ASCII PDG1")
    contract_hash = require_safe_text(
        driver["approved_contract_sha256"],
        "driver.approved_contract_sha256",
        maximum=64,
    )
    if not HASH_RE.fullmatch(contract_hash):
        raise QualificationError("driver approved contract SHA-256 is invalid")
    abi_major = require_int(
        driver["expected_abi_major"], "driver.expected_abi_major", minimum=0
    )
    minimum_minor = require_int(
        driver["minimum_abi_minor"], "driver.minimum_abi_minor", minimum=0
    )
    driver_version = require_safe_text(
        driver["expected_driver_version"],
        "driver.expected_driver_version",
        maximum=100,
    )
    contract_review_path = validate_project_relative_path(
        driver["contract_review_path"], "driver.contract_review_path"
    )
    review_path = driver["advanced_review_path"]
    if classification == "pdg1":
        if review_path is not None:
            raise QualificationError("pdg1 advanced_review_path must be null")
    else:
        review_path = require_safe_text(
            review_path, "driver.advanced_review_path", maximum=500
        )
        parsed_review_path = Path(review_path)
        if parsed_review_path.is_absolute() or ".." in parsed_review_path.parts:
            raise QualificationError(
                "driver.advanced_review_path must be project-relative"
            )
    return {
        "classification": classification,
        "expected_magic_ascii": magic,
        "expected_abi_major": abi_major,
        "minimum_abi_minor": minimum_minor,
        "expected_driver_version": driver_version,
        "approved_contract_sha256": contract_hash,
        "required_capabilities": sorted(capabilities),
        "contract_review_path": contract_review_path,
        "advanced_review_path": review_path,
    }


def validate_acquisition(value: Any) -> dict[str, Any]:
    acquisition = require_object(value, "request.acquisition")
    require_exact_keys(
        acquisition,
        {"local", "remote", "baseline", "restore_required", "counter_policy"},
        "request.acquisition",
    )

    def endpoint(raw: Any, label: str) -> dict[str, Any]:
        record = require_object(raw, label)
        require_exact_keys(
            record,
            {
                "sample_rate_hz",
                "record_samples",
                "pretrigger_samples",
                "trigger_mode",
                "sample_units",
            },
            label,
        )
        samples = require_int(
            record["record_samples"], f"{label}.record_samples", minimum=2
        )
        pretrigger = require_int(
            record["pretrigger_samples"],
            f"{label}.pretrigger_samples",
            minimum=0,
        )
        if pretrigger >= samples:
            raise QualificationError(f"{label}.pretrigger_samples must be below record")
        return {
            "sample_rate_hz": require_number(
                record["sample_rate_hz"],
                f"{label}.sample_rate_hz",
                exclusive_minimum=0,
            ),
            "record_samples": samples,
            "pretrigger_samples": pretrigger,
            "trigger_mode": require_safe_text(
                record["trigger_mode"], f"{label}.trigger_mode", maximum=100
            ),
            "sample_units": require_safe_text(
                record["sample_units"], f"{label}.sample_units", maximum=32
            ),
        }

    baseline = require_object(acquisition["baseline"], "acquisition.baseline")
    require_exact_keys(
        baseline, {"duration_seconds", "minimum_records"}, "acquisition.baseline"
    )
    counter = require_object(
        acquisition["counter_policy"], "acquisition.counter_policy"
    )
    require_exact_keys(
        counter,
        {"counter_width_bits", "reset_semantics", "rollover_method"},
        "acquisition.counter_policy",
    )
    if (
        require_bool(
            acquisition["restore_required"], "acquisition.restore_required"
        )
        is not True
    ):
        raise QualificationError("acquisition restore_required must be true")
    return {
        "local": endpoint(acquisition["local"], "acquisition.local"),
        "remote": endpoint(acquisition["remote"], "acquisition.remote"),
        "baseline": {
            "duration_seconds": require_number(
                baseline["duration_seconds"],
                "acquisition.baseline.duration_seconds",
                exclusive_minimum=0,
            ),
            "minimum_records": require_int(
                baseline["minimum_records"],
                "acquisition.baseline.minimum_records",
                minimum=1,
            ),
        },
        "restore_required": True,
        "counter_policy": {
            "counter_width_bits": require_int(
                counter["counter_width_bits"],
                "counter_policy.counter_width_bits",
                minimum=8,
                maximum=128,
            ),
            "reset_semantics": require_safe_text(
                counter["reset_semantics"],
                "counter_policy.reset_semantics",
                maximum=200,
            ),
            "rollover_method": require_safe_text(
                counter["rollover_method"],
                "counter_policy.rollover_method",
                maximum=200,
            ),
        },
    }


def validate_sequence(
    value: Any,
    mode: str,
    maximum_output: float,
    one_shot_fraction: float,
) -> dict[str, Any]:
    sequence = require_object(value, "request.sequence")
    require_exact_keys(
        sequence, {"deterministic_seed", "one_shot", "modes"}, "request.sequence"
    )
    seed = require_int(
        sequence["deterministic_seed"],
        "sequence.deterministic_seed",
        minimum=0,
        maximum=(2**63) - 1,
    )

    def common_step(
        raw: Any,
        label: str,
        *,
        one_shot: bool,
    ) -> dict[str, Any]:
        step = require_object(raw, label)
        expected = {
            "step_id",
            "mode_name",
            "amplitude_volts_peak",
            "pulse_width_seconds",
            "polarity",
            "capture_window_seconds",
            "expected_emitted_count",
        }
        if not one_shot:
            expected |= {"duration_seconds", "repetition_count"}
        require_exact_keys(step, expected, label)
        amplitude = require_number(
            step["amplitude_volts_peak"],
            f"{label}.amplitude_volts_peak",
            exclusive_minimum=0,
            maximum=maximum_output,
        )
        polarity = step["polarity"]
        if polarity not in {"positive", "negative", "bipolar"}:
            raise QualificationError(f"{label}.polarity is invalid")
        normalized = {
            "step_id": require_safe_id(step["step_id"], f"{label}.step_id"),
            "mode_name": require_safe_text(
                step["mode_name"], f"{label}.mode_name", maximum=100
            ),
            "amplitude_volts_peak": amplitude,
            "pulse_width_seconds": require_number(
                step["pulse_width_seconds"],
                f"{label}.pulse_width_seconds",
                exclusive_minimum=0,
            ),
            "polarity": polarity,
            "capture_window_seconds": require_number(
                step["capture_window_seconds"],
                f"{label}.capture_window_seconds",
                exclusive_minimum=0,
            ),
            "expected_emitted_count": require_int(
                step["expected_emitted_count"],
                f"{label}.expected_emitted_count",
                minimum=1,
            ),
        }
        if one_shot:
            if normalized["expected_emitted_count"] != 1:
                raise QualificationError("one-shot expected_emitted_count must equal 1")
            if amplitude > maximum_output * one_shot_fraction:
                raise QualificationError(
                    "one-shot amplitude exceeds its declared low-amplitude ceiling"
                )
        else:
            normalized["duration_seconds"] = require_number(
                step["duration_seconds"],
                f"{label}.duration_seconds",
                exclusive_minimum=0,
            )
            normalized["repetition_count"] = require_int(
                step["repetition_count"],
                f"{label}.repetition_count",
                minimum=1,
            )
        return normalized

    modes_raw = require_list(sequence["modes"], "sequence.modes")
    if mode == "read-only":
        if sequence["one_shot"] is not None or modes_raw:
            raise QualificationError("read-only plans cannot contain output steps")
        return {"deterministic_seed": seed, "one_shot": None, "modes": []}

    one_shot = common_step(sequence["one_shot"], "sequence.one_shot", one_shot=True)
    if not modes_raw:
        raise QualificationError("active qualification requires at least one mode")
    modes = [
        common_step(item, f"sequence.modes[{index}]", one_shot=False)
        for index, item in enumerate(modes_raw)
    ]
    step_ids = [one_shot["step_id"], *(item["step_id"] for item in modes)]
    if len(set(step_ids)) != len(step_ids):
        raise QualificationError("sequence step_id values must be unique")
    if max(item["amplitude_volts_peak"] for item in modes) <= one_shot[
        "amplitude_volts_peak"
    ]:
        raise QualificationError(
            "one-shot must precede at least one strictly higher-amplitude mode"
        )
    return {"deterministic_seed": seed, "one_shot": one_shot, "modes": modes}


def validate_acceptance(value: Any) -> dict[str, Any]:
    acceptance = require_object(value, "request.acceptance")
    require_exact_keys(acceptance, {"criteria"}, "request.acceptance")
    criteria = require_list(acceptance["criteria"], "acceptance.criteria")
    normalized: list[dict[str, Any]] = []
    observed: set[str] = set()
    for index, raw in enumerate(criteria):
        label = f"acceptance.criteria[{index}]"
        criterion = require_object(raw, label)
        require_exact_keys(
            criterion,
            {"metric", "unit", "minimum", "maximum", "allowed_values"},
            label,
        )
        metric = require_safe_id(criterion["metric"], f"{label}.metric")
        if metric not in REQUIRED_METRICS:
            raise QualificationError(f"{label}.metric is not a required metric")
        if metric in observed:
            raise QualificationError(f"duplicate acceptance metric {metric}")
        observed.add(metric)
        unit = require_safe_text(
            criterion["unit"], f"{label}.unit", maximum=64, allow_empty=True
        )
        if metric in NUMERIC_METRICS:
            if criterion["allowed_values"] is not None:
                raise QualificationError(
                    f"{metric} is numeric and cannot use allowed_values"
                )
            minimum = (
                None
                if criterion["minimum"] is None
                else require_number(
                    criterion["minimum"], f"{label}.minimum"
                )
            )
            maximum = (
                None
                if criterion["maximum"] is None
                else require_number(
                    criterion["maximum"], f"{label}.maximum"
                )
            )
            if minimum is None and maximum is None:
                raise QualificationError(
                    f"{metric} requires minimum and/or maximum"
                )
            if minimum is not None and maximum is not None and minimum > maximum:
                raise QualificationError(f"{metric} minimum exceeds maximum")
            allowed = None
        else:
            if criterion["minimum"] is not None or criterion["maximum"] is not None:
                raise QualificationError(
                    f"{metric} is categorical and cannot use numeric bounds"
                )
            allowed_raw = require_list(
                criterion["allowed_values"], f"{label}.allowed_values"
            )
            allowed = [
                require_safe_text(item, f"{label}.allowed_values", maximum=100)
                for item in allowed_raw
            ]
            if not allowed or len(set(allowed)) != len(allowed):
                raise QualificationError(
                    f"{metric} allowed_values must be unique and non-empty"
                )
            minimum = maximum = None
        normalized.append(
            {
                "metric": metric,
                "unit": unit,
                "minimum": minimum,
                "maximum": maximum,
                "allowed_values": allowed,
            }
        )
    missing = sorted(set(REQUIRED_METRICS) - observed)
    if missing:
        raise QualificationError(
            f"acceptance criteria omit required metrics: {', '.join(missing)}"
        )
    return {"criteria": sorted(normalized, key=lambda item: item["metric"])}


def validate_request(request: dict[str, Any]) -> dict[str, Any]:
    require_exact_keys(
        request,
        {
            "request_schema_version",
            "qualification_id",
            "created_at_utc",
            "mode",
            "probe_max_age_seconds",
            "purpose",
            "topology",
            "safety",
            "driver_expectation",
            "acquisition",
            "sequence",
            "acceptance",
        },
        "qualification request",
    )
    if request["request_schema_version"] != 1:
        raise QualificationError("request_schema_version must equal 1")
    mode = request["mode"]
    if mode not in {"active", "read-only"}:
        raise QualificationError("request.mode must be active or read-only")
    topology = validate_topology(request["topology"])
    safety = validate_safety(request["safety"])
    driver = validate_driver_expectation(request["driver_expectation"])
    if mode == "active" and driver["classification"] == "unknown-read-only":
        raise QualificationError("unknown drivers are read-only; active mode is forbidden")
    if mode == "read-only" and driver["classification"] != "unknown-read-only":
        raise QualificationError(
            "read-only fallback is reserved for an unknown driver contract"
        )
    sequence = validate_sequence(
        request["sequence"],
        mode,
        safety["maximum_output_volts_peak"],
        safety["one_shot_ceiling_fraction"],
    )
    return {
        "request_schema_version": 1,
        "qualification_id": require_safe_id(
            request["qualification_id"], "request.qualification_id"
        ),
        "created_at_utc": request["created_at_utc"],
        "mode": mode,
        "probe_max_age_seconds": require_int(
            request["probe_max_age_seconds"],
            "request.probe_max_age_seconds",
            minimum=1,
            maximum=86400,
        ),
        "purpose": require_safe_text(
            request["purpose"], "request.purpose", maximum=500
        ),
        "topology": topology,
        "safety": safety,
        "driver_expectation": driver,
        "acquisition": validate_acquisition(request["acquisition"]),
        "sequence": sequence,
        "acceptance": validate_acceptance(request["acceptance"]),
    }


def validate_driver_contract(
    contract: dict[str, Any],
    contract_hash: str,
    expectation: dict[str, Any],
) -> dict[str, Any]:
    required = {
        "schema_version",
        "namespace",
        "magic_ascii",
        "abi_major",
        "abi_minor",
        "register_base",
        "address_span",
        "capabilities",
        "registers",
    }
    require_exact_keys(contract, required, "driver contract")
    if contract["schema_version"] != 1:
        raise QualificationError("driver contract schema_version must equal 1")
    magic = require_safe_text(
        contract["magic_ascii"], "driver contract magic_ascii", maximum=16
    )
    abi_major = require_int(
        contract["abi_major"], "driver contract abi_major", minimum=0
    )
    abi_minor = require_int(
        contract["abi_minor"], "driver contract abi_minor", minimum=0
    )
    capabilities = sorted(
        require_safe_id(item, "driver contract capability")
        for item in require_list(
            contract["capabilities"], "driver contract capabilities"
        )
    )
    if len(set(capabilities)) != len(capabilities):
        raise QualificationError("driver contract capabilities contain duplicates")
    register_base_text = require_safe_text(
        contract["register_base"], "driver contract register_base", maximum=32
    )
    address_span_text = require_safe_text(
        contract["address_span"], "driver contract address_span", maximum=32
    )
    if not HEX_RE.fullmatch(register_base_text):
        raise QualificationError("driver contract register_base must be hexadecimal")
    if not HEX_RE.fullmatch(address_span_text):
        raise QualificationError("driver contract address_span must be hexadecimal")
    register_base = int(register_base_text, 16)
    address_span = int(address_span_text, 16)
    if register_base % 4:
        raise QualificationError("driver contract register_base must be 32-bit aligned")
    if address_span < 4 or address_span % 4:
        raise QualificationError(
            "driver contract address_span must be a positive 32-bit multiple"
        )

    registers: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    seen_roles: set[str] = set()
    seen_offsets: set[int] = set()
    for index, raw in enumerate(
        require_list(contract["registers"], "driver contract registers")
    ):
        label = f"driver contract registers[{index}]"
        register = require_object(raw, label)
        require_exact_keys(
            register, {"name", "offset", "access", "role"}, label
        )
        name = require_safe_id(register["name"], f"{label}.name")
        role = require_safe_id(register["role"], f"{label}.role")
        offset_text = require_safe_text(
            register["offset"], f"{label}.offset", maximum=32
        )
        if not HEX_RE.fullmatch(offset_text):
            raise QualificationError(f"{label}.offset must be hexadecimal")
        offset = int(offset_text, 16)
        if offset % 4 or offset >= address_span:
            raise QualificationError(
                f"{label}.offset must be aligned and inside address_span"
            )
        access = register["access"]
        if access not in {"ro", "rw", "wo", "w1c"}:
            raise QualificationError(f"{label}.access is unsupported")
        if name in seen_names or role in seen_roles or offset in seen_offsets:
            raise QualificationError(
                "driver contract register names, roles and offsets must be unique"
            )
        seen_names.add(name)
        seen_roles.add(role)
        seen_offsets.add(offset)
        registers.append(
            {
                "name": name,
                "offset": f"0x{offset:x}",
                "access": access,
                "role": role,
            }
        )

    if expectation["classification"] != "unknown-read-only":
        if contract_hash != expectation["approved_contract_sha256"]:
            raise QualificationError("driver contract hash is not the approved hash")
        if magic != expectation["expected_magic_ascii"]:
            raise QualificationError("driver contract magic does not match expectation")
        if abi_major != expectation["expected_abi_major"]:
            raise QualificationError("driver contract ABI major does not match")
        if abi_minor < expectation["minimum_abi_minor"]:
            raise QualificationError("driver contract ABI minor is too old")
        missing_caps = sorted(
            set(expectation["required_capabilities"]) - set(capabilities)
        )
        if missing_caps:
            raise QualificationError(
                "driver contract lacks required capabilities: "
                + ", ".join(missing_caps)
            )
        missing_roles = sorted(ACTIVE_REQUIRED_REGISTER_ROLES - seen_roles)
        if missing_roles:
            raise QualificationError(
                "active driver contract lacks mandatory register roles: "
                + ", ".join(missing_roles)
            )
        if not registers:
            raise QualificationError(
                "active driver contract must define a non-empty register map"
            )
    return {
        "sha256": contract_hash,
        "namespace": require_safe_text(
            contract["namespace"], "driver contract namespace", maximum=100
        ),
        "magic_ascii": magic,
        "abi_major": abi_major,
        "abi_minor": abi_minor,
        "register_base": f"0x{register_base:x}",
        "address_span": f"0x{address_span:x}",
        "capabilities": capabilities,
        "register_count": len(registers),
        "register_roles": sorted(seen_roles),
    }


def validate_driver_evidence(
    evidence: dict[str, Any],
    generator: dict[str, Any],
    created_at,
    max_age_seconds: int,
    contract_summary: dict[str, Any],
    mode: str,
    writer_id: str,
) -> dict[str, Any]:
    require_exact_keys(
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
        raise QualificationError("driver evidence schema version must equal 1")
    captured = parse_utc(evidence["captured_at_utc"], "driver evidence captured_at")
    age = (created_at - captured).total_seconds()
    if age < 0 or age > max_age_seconds:
        raise QualificationError("driver evidence is future-dated or stale")
    if evidence["board_machine_id"] != generator["identity"]["machine_id"]:
        raise QualificationError("driver evidence machine identity mismatch")
    if (
        evidence["ssh_host_key_sha256"]
        != generator["connection"]["ssh_host_key_sha256"]
    ):
        raise QualificationError("driver evidence SSH identity mismatch")
    if require_bool(evidence["read_only_probe"], "driver read_only_probe") is not True:
        raise QualificationError("driver evidence must come from a read-only probe")
    if (
        require_bool(
            evidence["output_enabled_readback"],
            "driver output_enabled_readback",
        )
        is not False
    ):
        raise QualificationError("generator output must be disabled during probing")
    writer_owner = evidence["writer_owner"]
    if writer_owner not in (None, writer_id):
        raise QualificationError("a different writer already owns the generator")
    observed = require_object(evidence["contract"], "driver evidence.contract")
    require_exact_keys(
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
        "driver evidence.contract",
    )
    comparisons = {
        "magic_ascii": contract_summary["magic_ascii"],
        "abi_major": contract_summary["abi_major"],
        "abi_minor": contract_summary["abi_minor"],
        "register_contract_sha256": contract_summary["sha256"],
        "register_base": contract_summary["register_base"],
        "address_span": contract_summary["address_span"],
        "capabilities": contract_summary["capabilities"],
    }
    for key, expected in comparisons.items():
        if observed.get(key) != expected:
            raise QualificationError(f"driver evidence contract {key} mismatch")
    driver_version = require_safe_text(
        observed["driver_version"], "driver evidence driver_version", maximum=100
    )
    if (
        mode == "active"
        and driver_version
        != contract_summary.get("expected_driver_version")
    ):
        raise QualificationError("driver evidence version mismatch")
    access = "read-only"
    if mode == "active":
        missing = sorted(
            ACTIVE_REQUIRED_CAPABILITIES - set(contract_summary["capabilities"])
        )
        if missing:
            raise QualificationError(
                "active driver lacks mandatory capabilities: " + ", ".join(missing)
            )
        access = "single-writer-active"
    return {
        "captured_at_utc": evidence["captured_at_utc"],
        "source": require_safe_text(
            evidence["source"], "driver evidence source", maximum=500
        ),
        "output_enabled_readback": False,
        "writer_owner": writer_owner,
        "access_decision": access,
        "contract": {**contract_summary, "driver_version": driver_version},
    }


def validate_contract_review(
    project_root: Path,
    review_path_text: str | None,
    expectation: dict[str, Any],
    contract: dict[str, Any],
    created_at,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if expectation["classification"] == "unknown-read-only":
        return None, None
    if review_path_text is None:
        raise QualificationError("active driver classifications require contract review")
    binding, path = file_binding(
        project_root, Path(review_path_text), "independent driver contract review"
    )
    review = load_json_object(path, "independent driver contract review")
    require_exact_keys(
        review,
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
        "independent driver contract review",
    )
    if review["review_schema_version"] != 1:
        raise QualificationError("driver contract review schema must equal 1")
    if review["classification"] != expectation["classification"]:
        raise QualificationError("driver contract review classification mismatch")
    if review["contract_sha256"] != contract["sha256"]:
        raise QualificationError("driver contract review hash mismatch")
    if review["magic_ascii"] != contract["magic_ascii"]:
        raise QualificationError("driver contract review magic mismatch")
    if review["abi_major"] != contract["abi_major"]:
        raise QualificationError("driver contract review ABI major mismatch")
    if review["abi_minor"] != contract["abi_minor"]:
        raise QualificationError("driver contract review ABI minor mismatch")
    if review["driver_version"] != expectation["expected_driver_version"]:
        raise QualificationError("driver contract review version mismatch")
    approved_capabilities = sorted(
        require_safe_id(item, "contract review approved capability")
        for item in require_list(
            review["approved_capabilities"],
            "contract review approved_capabilities",
        )
    )
    if approved_capabilities != contract["capabilities"]:
        raise QualificationError("driver contract review capabilities mismatch")
    if (
        require_bool(
            review["independent_from_qualification_request"],
            "contract review independent_from_qualification_request",
        )
        is not True
    ):
        raise QualificationError(
            "driver contract review must be independent of the qualification request"
        )
    reviewed_at = parse_utc(
        review["reviewed_at_utc"], "driver contract review timestamp"
    )
    if reviewed_at > created_at:
        raise QualificationError("driver contract review is future-dated")
    require_safe_text(
        review["reviewer_identity"], "driver contract review reviewer", maximum=200
    )
    require_safe_text(
        review["review_reference"], "driver contract review reference", maximum=200
    )
    return binding, review


def validate_advanced_review(
    project_root: Path,
    review_path_text: str | None,
    expectation: dict[str, Any],
    contract: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if expectation["classification"] != "approved-advanced":
        return None, None
    if review_path_text is None:
        raise QualificationError("approved-advanced requires a separate review path")
    binding, path = file_binding(
        project_root, Path(review_path_text), "advanced driver review"
    )
    review = load_json_object(path, "advanced driver review")
    require_exact_keys(
        review,
        {
            "review_schema_version",
            "classification",
            "contract_sha256",
            "magic_ascii",
            "abi_major",
            "reviewed_at_utc",
            "reviewer_identity",
            "review_reference",
        },
        "advanced driver review",
    )
    if review["review_schema_version"] != 1:
        raise QualificationError("advanced driver review schema must equal 1")
    if review["classification"] != "approved-advanced":
        raise QualificationError("advanced driver review classification mismatch")
    if review["contract_sha256"] != contract["sha256"]:
        raise QualificationError("advanced review contract hash mismatch")
    if review["magic_ascii"] != contract["magic_ascii"]:
        raise QualificationError("advanced review magic mismatch")
    if review["abi_major"] != contract["abi_major"]:
        raise QualificationError("advanced review ABI major mismatch")
    parse_utc(review["reviewed_at_utc"], "advanced review timestamp")
    require_safe_text(
        review["reviewer_identity"], "advanced review reviewer", maximum=200
    )
    require_safe_text(
        review["review_reference"], "advanced review reference", maximum=200
    )
    return binding, review


def validate_probe_provenance(
    project_root: Path,
    raw_probe_path: Path,
    target_manifest_path: Path,
    host_key_evidence_path: Path,
    normalized_report: dict[str, Any],
    normalized_report_binding: dict[str, Any],
    role: str,
    created_at,
    max_age_seconds: int,
) -> dict[str, Any]:
    analyzer, contracts, _ = deployment_modules()
    raw_binding, raw_file = file_binding(
        project_root, raw_probe_path, f"{role} raw probe"
    )
    manifest_binding, manifest_file = file_binding(
        project_root, target_manifest_path, f"{role} target manifest"
    )
    host_key_binding, host_key_file = file_binding(
        project_root, host_key_evidence_path, f"{role} host-key verification"
    )
    try:
        manifest = contracts.load_and_validate_manifest(manifest_file)
        fresh_report, status = analyzer.analyze(
            load_json_object(raw_file, f"{role} raw probe"),
            manifest,
            role,
        )
    except Exception as exc:
        if isinstance(exc, QualificationError):
            raise
        raise QualificationError(
            f"{role} raw probe/manifest reanalysis failed: {exc}"
        ) from exc
    if status != 0 or fresh_report.get("blocking_errors") != []:
        raise QualificationError(f"{role} raw probe does not pass fresh analysis")
    if fresh_report.get("warnings") != []:
        raise QualificationError(f"{role} raw probe has unresolved warnings")
    if fresh_report != normalized_report:
        raise QualificationError(
            f"{role} normalized report differs from fresh raw-probe analysis"
        )

    host_key = load_json_object(host_key_file, f"{role} host-key verification")
    require_exact_keys(
        host_key,
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
        f"{role} host-key verification",
    )
    if host_key["host_key_evidence_schema_version"] != 1:
        raise QualificationError(f"{role} host-key evidence schema must equal 1")
    if host_key["role"] != role or host_key["result"] != "verified":
        raise QualificationError(f"{role} host-key evidence role/result mismatch")
    captured = parse_utc(
        host_key["captured_at_utc"], f"{role} host-key captured_at_utc"
    )
    age = (created_at - captured).total_seconds()
    if age < 0 or age > max_age_seconds:
        raise QualificationError(f"{role} host-key evidence is future-dated or stale")
    connection = normalized_report["connection"]
    comparisons = {
        "host": connection["host"],
        "address": connection["address"],
        "ssh_host_key_sha256": connection["ssh_host_key_sha256"],
    }
    for key, expected in comparisons.items():
        if host_key[key] != expected:
            raise QualificationError(f"{role} host-key evidence {key} mismatch")
    require_safe_text(
        host_key["algorithm"], f"{role} host-key algorithm", maximum=64
    )
    require_safe_text(
        host_key["verification_method"],
        f"{role} host-key verification_method",
        maximum=500,
    )
    require_safe_text(
        host_key["verified_by"], f"{role} host-key verified_by", maximum=200
    )
    require_safe_text(
        host_key["verification_reference"],
        f"{role} host-key verification_reference",
        maximum=200,
    )
    return {
        "raw_probe": raw_binding,
        "target_manifest": manifest_binding,
        "host_key_verification": host_key_binding,
        "normalized_report": normalized_report_binding,
        "fresh_analysis": {
            "analyzer": "deploy-red-pitaya-project/analyze_probe.py",
            "status": "compatible-warning-free",
        },
    }


def validate_physical_evidence(
    project_root: Path,
    topology: dict[str, Any],
    safety: dict[str, Any],
) -> dict[str, Any]:
    def bind_json(path_text: str, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
        binding, path = file_binding(project_root, Path(path_text), label)
        return binding, load_json_object(path, label)

    output_binding, output = bind_json(
        safety["output_limit_evidence_path"], "generator output-limit evidence"
    )
    require_exact_keys(
        output,
        {
            "evidence_schema_version",
            "kind",
            "board_role",
            "channel",
            "maximum_output_volts_peak",
            "voltage_unit",
            "deployment_maximum_output_unit",
            "deployment_maximum_output_to_vpeak_factor",
            "source",
            "reviewed_at_utc",
        },
        "generator output-limit evidence",
    )
    if (
        output["evidence_schema_version"] != 1
        or output["kind"] != "output-limit-rating"
        or output["board_role"] != "generator"
        or output["channel"] != topology["generator_output_channel"]
        or output["voltage_unit"] != "Vpeak"
        or output["deployment_maximum_output_unit"] != "Vpeak"
    ):
        raise QualificationError("generator output-limit evidence contract mismatch")
    if not math.isclose(
        require_number(
            output["maximum_output_volts_peak"],
            "output-limit maximum_output_volts_peak",
            exclusive_minimum=0,
        ),
        safety["maximum_output_volts_peak"],
        rel_tol=1e-12,
        abs_tol=1e-15,
    ):
        raise QualificationError("output-limit evidence voltage differs from request")
    evidence_factor = require_number(
        output["deployment_maximum_output_to_vpeak_factor"],
        "output-limit deployment conversion factor",
        exclusive_minimum=0,
    )
    if not math.isclose(evidence_factor, 1.0, rel_tol=0, abs_tol=0):
        raise QualificationError(
            "deployment maximum_output_volts must be explicitly rated in Vpeak"
        )
    if not math.isclose(
        evidence_factor,
        safety["deployment_maximum_output_to_vpeak_factor"],
        rel_tol=0,
        abs_tol=0,
    ):
        raise QualificationError("output-limit conversion factor differs from request")
    require_safe_text(output["source"], "output-limit source", maximum=500)
    parse_utc(output["reviewed_at_utc"], "output-limit reviewed_at_utc")

    bindings: dict[str, Any] = {"generator_output_limit": output_binding}
    for role, endpoint_name in (
        ("local", "generator_local_input"),
        ("remote", "acquisition_remote_input"),
    ):
        endpoint = topology[endpoint_name]
        range_binding, rating = bind_json(
            endpoint["range_evidence_path"], f"{role} input range evidence"
        )
        require_exact_keys(
            rating,
            {
                "evidence_schema_version",
                "kind",
                "path_role",
                "channel",
                "range_volts_peak",
                "voltage_unit",
                "coupling",
                "termination_ohms",
                "source",
                "reviewed_at_utc",
            },
            f"{role} input range evidence",
        )
        if (
            rating["evidence_schema_version"] != 1
            or rating["kind"] != "input-range-rating"
            or rating["path_role"] != role
            or rating["channel"] != endpoint["channel"]
            or rating["voltage_unit"] != "Vpeak"
            or rating["coupling"] != endpoint["coupling"]
        ):
            raise QualificationError(f"{role} input range evidence contract mismatch")
        for key in ("range_volts_peak", "termination_ohms"):
            if not math.isclose(
                require_number(
                    rating[key], f"{role} input range evidence {key}",
                    exclusive_minimum=0,
                ),
                endpoint[key],
                rel_tol=1e-12,
                abs_tol=1e-15,
            ):
                raise QualificationError(
                    f"{role} input range evidence {key} differs from request"
                )
        require_safe_text(rating["source"], f"{role} range source", maximum=500)
        parse_utc(rating["reviewed_at_utc"], f"{role} range reviewed_at_utc")
        bindings[f"{role}_input_range"] = range_binding

        path = topology[f"{role}_path"]
        path_binding, calibration = bind_json(
            path["evidence_path"], f"{role} path calibration"
        )
        require_exact_keys(
            calibration,
            {
                "evidence_schema_version",
                "kind",
                "path_role",
                "attenuation_db",
                "attenuation_uncertainty_db",
                "voltage_unit",
                "measurement_method",
                "instrument_reference",
                "measured_at_utc",
            },
            f"{role} path calibration",
        )
        if (
            calibration["evidence_schema_version"] != 1
            or calibration["kind"] != "signal-path-calibration"
            or calibration["path_role"] != role
            or calibration["voltage_unit"] != "Vpeak"
        ):
            raise QualificationError(f"{role} path calibration contract mismatch")
        for key in ("attenuation_db", "attenuation_uncertainty_db"):
            if not math.isclose(
                require_number(
                    calibration[key],
                    f"{role} path calibration {key}",
                    minimum=0,
                ),
                path[key],
                rel_tol=1e-12,
                abs_tol=1e-15,
            ):
                raise QualificationError(
                    f"{role} path calibration {key} differs from request"
                )
        require_safe_text(
            calibration["measurement_method"],
            f"{role} path measurement_method",
            maximum=500,
        )
        require_safe_text(
            calibration["instrument_reference"],
            f"{role} path instrument_reference",
            maximum=200,
        )
        parse_utc(calibration["measured_at_utc"], f"{role} path measured_at_utc")
        bindings[f"{role}_path_calibration"] = path_binding
    return bindings


def validate_acquisition_state(
    state: dict[str, Any],
    generator: dict[str, Any],
    acquisition: dict[str, Any],
    topology: dict[str, Any],
    created_at,
    max_age_seconds: int,
) -> dict[str, Any]:
    require_exact_keys(
        state,
        {
            "state_schema_version",
            "captured_at_utc",
            "capture_method",
            "generator_machine_id",
            "acquisition_machine_id",
            "generator_input_state",
            "acquisition_input_state",
            "restore_verification_method",
        },
        "acquisition state",
    )
    if state["state_schema_version"] != 1:
        raise QualificationError("acquisition state schema version must equal 1")
    captured = parse_utc(state["captured_at_utc"], "acquisition state captured_at")
    age = (created_at - captured).total_seconds()
    if age < 0 or age > max_age_seconds:
        raise QualificationError("acquisition state is future-dated or stale")
    if state["generator_machine_id"] != generator["identity"]["machine_id"]:
        raise QualificationError("acquisition state generator identity mismatch")
    if state["acquisition_machine_id"] != acquisition["identity"]["machine_id"]:
        raise QualificationError("acquisition state remote identity mismatch")
    def endpoint_state(raw: Any, label: str, expected_channel: str) -> dict[str, Any]:
        item = require_object(raw, label)
        require_exact_keys(
            item,
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
            label,
        )
        if item["state_format_version"] != 1:
            raise QualificationError(f"{label} state_format_version must equal 1")
        channel = require_safe_text(item["channel"], f"{label}.channel", maximum=64)
        if channel != expected_channel:
            raise QualificationError(f"{label}.channel differs from topology")
        return {
            "state_format_version": 1,
            "channel": channel,
            "enabled": require_bool(item["enabled"], f"{label}.enabled"),
            "decimation": require_int(
                item["decimation"], f"{label}.decimation", minimum=1
            ),
            "trigger_source": require_safe_text(
                item["trigger_source"], f"{label}.trigger_source", maximum=100
            ),
            "trigger_level_volts": require_number(
                item["trigger_level_volts"], f"{label}.trigger_level_volts"
            ),
            "trigger_delay_samples": require_int(
                item["trigger_delay_samples"],
                f"{label}.trigger_delay_samples",
            ),
            "gain_mode": require_safe_text(
                item["gain_mode"], f"{label}.gain_mode", maximum=64
            ),
            "coupling": require_safe_text(
                item["coupling"], f"{label}.coupling", maximum=32
            ),
            "termination_ohms": require_number(
                item["termination_ohms"],
                f"{label}.termination_ohms",
                exclusive_minimum=0,
            ),
            "sample_units": require_safe_text(
                item["sample_units"], f"{label}.sample_units", maximum=32
            ),
        }
    generator_input_state = endpoint_state(
        state["generator_input_state"],
        "generator input state",
        topology["generator_local_input"]["channel"],
    )
    acquisition_input_state = endpoint_state(
        state["acquisition_input_state"],
        "acquisition input state",
        topology["acquisition_remote_input"]["channel"],
    )
    return {
        "captured_at_utc": state["captured_at_utc"],
        "capture_method": require_safe_text(
            state["capture_method"], "acquisition state capture_method", maximum=500
        ),
        "generator_input_state": generator_input_state,
        "acquisition_input_state": acquisition_input_state,
        "restore_verification_method": require_safe_text(
            state["restore_verification_method"],
            "acquisition state restore_verification_method",
            maximum=500,
        ),
    }


def deployment_plan_core_sha256(plan: dict[str, Any]) -> str:
    core = {
        key: value
        for key, value in plan.items()
        if key not in {"gates", "gate_evidence", "plan_core_sha256"}
    }
    return canonical_sha256(core)


def validate_authorization_receipt(
    project_root: Path,
    deployment_plan: dict[str, Any],
    gate: str,
    target: dict[str, Any],
) -> dict[str, Any]:
    gates = require_object(deployment_plan.get("gates"), "deployment plan gates")
    evidence = require_object(
        deployment_plan.get("gate_evidence"), "deployment plan gate evidence"
    )
    if gates.get(gate) is not True:
        raise QualificationError(f"deployment gate {gate} is not authorized")
    receipt = require_object(evidence.get(gate), f"deployment receipt {gate}")
    if receipt.get("gate") != gate:
        raise QualificationError(f"{gate} deployment receipt gate mismatch")
    receipt_path = Path(
        require_safe_text(receipt.get("path"), f"{gate} receipt path", maximum=500)
    )
    if receipt_path.is_absolute() or ".." in receipt_path.parts:
        raise QualificationError(f"{gate} receipt path must be project-relative")
    binding, path = file_binding(project_root, receipt_path, f"{gate} receipt")
    if receipt.get("sha256") != binding["sha256"]:
        raise QualificationError(f"{gate} receipt hash differs from deployment plan")
    if receipt.get("size") != binding["size_bytes"]:
        raise QualificationError(f"{gate} receipt size differs from deployment plan")
    authorization = load_json_object(path, f"{gate} authorization")
    if authorization.get("authorization_schema_version") != 2:
        raise QualificationError(f"{gate} authorization schema is not version 2")
    expected = {
        "gate": gate,
        "result": "authorized",
        "authorization_scope": AUTHORIZATION_SCOPES[gate],
        "deployment_id": deployment_plan.get("deployment_id"),
        "plan_core_sha256": deployment_plan.get("plan_core_sha256"),
        "target_machine_id": target["identity"]["machine_id"],
        "ssh_host_key_sha256": target["connection"]["ssh_host_key_sha256"],
    }
    for key, expected_value in expected.items():
        if authorization.get(key) != expected_value:
            raise QualificationError(f"{gate} authorization {key} mismatch")
    require_safe_text(
        authorization.get("approver_identity"),
        f"{gate} approver_identity",
        maximum=200,
    )
    require_safe_text(
        authorization.get("authorization_reference"),
        f"{gate} authorization_reference",
        maximum=200,
    )
    parse_utc(authorization.get("recorded_at_utc"), f"{gate} authorization time")
    if receipt.get("recorded_at_utc") != authorization["recorded_at_utc"]:
        raise QualificationError(f"{gate} receipt timestamp mismatch")
    if receipt.get("summary") != authorization.get("summary"):
        raise QualificationError(f"{gate} receipt summary mismatch")
    return {
        "gate": gate,
        "receipt": binding,
        "authorization_reference": authorization["authorization_reference"],
        "recorded_at_utc": authorization["recorded_at_utc"],
    }


def validate_deployment_authorizations(
    project_root: Path,
    bundle: dict[str, Any],
    generator: dict[str, Any],
    acquisition: dict[str, Any],
    probe_provenance: dict[str, Any],
    request: dict[str, Any],
    request_binding: dict[str, Any],
    created_at,
) -> dict[str, Any]:
    require_exact_keys(
        bundle,
        {
            "authorization_bundle_schema_version",
            "source_skill",
            "generator_plan",
            "acquisition_plan",
            "qualification_binding",
        },
        "authorization bundle",
    )
    if bundle["authorization_bundle_schema_version"] != 1:
        raise QualificationError("authorization bundle schema must equal 1")
    if bundle["source_skill"] != "deploy-red-pitaya-project":
        raise QualificationError(
            "authorization bundle must delegate to deploy-red-pitaya-project"
        )
    normalized: dict[str, Any] = {
        "source_skill": "deploy-red-pitaya-project",
    }
    _, _, deploy_validator = deployment_modules()
    seen_references: set[str] = set()
    seen_receipt_hashes: set[str] = set()
    seen_receipt_paths: set[str] = set()
    for role, target in (("generator", generator), ("acquisition", acquisition)):
        plan_text = require_safe_text(
            bundle[f"{role}_plan"], f"authorization {role}_plan", maximum=500
        )
        plan_relative = Path(plan_text)
        if plan_relative.is_absolute() or ".." in plan_relative.parts:
            raise QualificationError(
                f"authorization {role}_plan must be project-relative"
            )
        plan_binding, plan_path = file_binding(
            project_root, plan_relative, f"{role} deployment plan"
        )
        plan = load_json_object(plan_path, f"{role} deployment plan")
        errors, warnings = deploy_validator.validate(
            plan, project_root.resolve(), False
        )
        if errors or warnings:
            details = "; ".join([*errors, *warnings])
            raise QualificationError(
                f"{role} deployment plan failed sibling deep validation: {details}"
            )
        core_hash = deployment_plan_core_sha256(plan)
        if plan.get("plan_core_sha256") != core_hash:
            raise QualificationError(f"{role} deployment plan core hash is invalid")
        plan_target = require_object(plan.get("target"), f"{role} plan target")
        plan_identity = require_object(
            plan_target.get("identity"), f"{role} plan target identity"
        )
        plan_connection = require_object(
            plan_target.get("connection"), f"{role} plan target connection"
        )
        if plan_target.get("role") != role:
            raise QualificationError(f"{role} deployment plan role mismatch")
        if plan_identity.get("machine_id") != target["identity"]["machine_id"]:
            raise QualificationError(f"{role} deployment plan machine mismatch")
        if (
            plan_connection.get("ssh_host_key_sha256")
            != target["connection"]["ssh_host_key_sha256"]
        ):
            raise QualificationError(f"{role} deployment plan SSH identity mismatch")
        project = require_object(plan.get("project"), f"{role} plan project")
        if (
            project.get("manifest_sha256")
            != probe_provenance[role]["target_manifest"]["sha256"]
        ):
            raise QualificationError(
                f"{role} deployment plan manifest differs from probe provenance"
            )
        if (
            project.get("probe_report_sha256")
            != probe_provenance[role]["normalized_report"]["sha256"]
        ):
            raise QualificationError(
                f"{role} deployment plan probe differs from qualification input"
            )
        receipts = [
            validate_authorization_receipt(project_root, plan, gate, target)
            for gate in AUTHORIZATION_GATES[role]
        ]
        for receipt in receipts:
            reference = receipt["authorization_reference"]
            if reference in seen_references:
                raise QualificationError(
                    "authorization_reference must be unique across all gates"
                )
            seen_references.add(reference)
            receipt_hash = receipt["receipt"]["sha256"]
            receipt_path = receipt["receipt"]["path"]
            if receipt_hash in seen_receipt_hashes or receipt_path in seen_receipt_paths:
                raise QualificationError(
                    "authorization receipt file/hash must be unique across gates"
                )
            seen_receipt_hashes.add(receipt_hash)
            seen_receipt_paths.add(receipt_path)
        normalized[role] = {
            "deployment_id": require_safe_id(
                plan.get("deployment_id"), f"{role} deployment_id"
            ),
            "plan_core_sha256": core_hash,
            "plan_file": plan_binding,
            "receipts": receipts,
        }

    binding_text = require_safe_text(
        bundle["qualification_binding"],
        "authorization qualification_binding",
        maximum=500,
    )
    binding_path = Path(binding_text)
    if binding_path.is_absolute() or ".." in binding_path.parts:
        raise QualificationError(
            "authorization qualification_binding must be project-relative"
        )
    binding_file, binding_resolved = file_binding(
        project_root, binding_path, "qualification authorization binding"
    )
    qualification_binding = load_json_object(
        binding_resolved, "qualification authorization binding"
    )
    require_exact_keys(
        qualification_binding,
        {
            "qualification_authorization_schema_version",
            "source_skill",
            "result",
            "qualification_id",
            "request_sha256",
            "generator_deployment_id",
            "generator_plan_core_sha256",
            "generator_output_receipt_sha256",
            "acquisition_deployment_id",
            "acquisition_plan_core_sha256",
            "maximum_output_volts_peak",
            "voltage_unit",
            "single_use_nonce",
            "recorded_at_utc",
            "expires_at_utc",
            "approver_identity",
            "authorization_reference",
        },
        "qualification authorization binding",
    )
    if (
        qualification_binding["qualification_authorization_schema_version"] != 1
        or qualification_binding["source_skill"] != "deploy-red-pitaya-project"
        or qualification_binding["result"] != "authorized"
    ):
        raise QualificationError("qualification authorization binding is invalid")
    generator_output_receipt = next(
        item
        for item in normalized["generator"]["receipts"]
        if item["gate"] == "functional_output_test_authorized"
    )
    expected = {
        "qualification_id": request["qualification_id"],
        "request_sha256": request_binding["sha256"],
        "generator_deployment_id": normalized["generator"]["deployment_id"],
        "generator_plan_core_sha256": normalized["generator"]["plan_core_sha256"],
        "generator_output_receipt_sha256": generator_output_receipt["receipt"][
            "sha256"
        ],
        "acquisition_deployment_id": normalized["acquisition"]["deployment_id"],
        "acquisition_plan_core_sha256": normalized["acquisition"]["plan_core_sha256"],
        "maximum_output_volts_peak": request["safety"][
            "maximum_output_volts_peak"
        ],
        "voltage_unit": "Vpeak",
    }
    for key, expected_value in expected.items():
        if qualification_binding.get(key) != expected_value:
            raise QualificationError(
                f"qualification authorization binding {key} mismatch"
            )
    recorded = parse_utc(
        qualification_binding["recorded_at_utc"],
        "qualification authorization recorded_at_utc",
    )
    expires = parse_utc(
        qualification_binding["expires_at_utc"],
        "qualification authorization expires_at_utc",
    )
    if (
        recorded < created_at
        or expires <= recorded
        or expires - recorded > timedelta(hours=1)
        or (recorded - created_at).total_seconds()
        > request["probe_max_age_seconds"]
    ):
        raise QualificationError(
            "qualification authorization is not fresh at request creation"
        )
    require_safe_id(
        qualification_binding["single_use_nonce"],
        "qualification authorization single_use_nonce",
    )
    require_safe_text(
        qualification_binding["approver_identity"],
        "qualification authorization approver_identity",
        maximum=200,
    )
    require_safe_text(
        qualification_binding["authorization_reference"],
        "qualification authorization authorization_reference",
        maximum=200,
    )
    normalized["qualification_binding"] = {
        "file": binding_file,
        "single_use_nonce": qualification_binding["single_use_nonce"],
        "recorded_at_utc": qualification_binding["recorded_at_utc"],
        "expires_at_utc": qualification_binding["expires_at_utc"],
        "authorization_reference": qualification_binding[
            "authorization_reference"
        ],
    }
    return normalized


def make_phases(request: dict[str, Any]) -> list[dict[str, Any]]:
    if request["mode"] == "read-only":
        phases = [
            ("bind-identities", "read-only", False),
            ("driver-contract-read-only", "read-only", False),
            ("baseline-output-disabled", "read-only", False),
            ("final-safe-readback", "read-only", False),
        ]
    else:
        phases = [
            ("bind-identities", "read-only", False),
            ("safe-stop-before-baseline", "live-write", False),
            ("baseline-output-disabled", "read-only", False),
            ("arm-watchdog-and-writer-lease", "live-write", False),
            ("low-amplitude-one-shot", "physical-output", True),
        ]
        phases.extend(
            (
                f"mode-{mode['step_id']}",
                "physical-output",
                True,
            )
            for mode in request["sequence"]["modes"]
        )
        phases.extend(
            [
                ("safe-stop-after-output", "live-write", False),
                ("counter-reset-rollover-checks", "live-write", False),
                ("restore-acquisition-state", "live-write", False),
                ("final-safe-verification", "read-only", False),
            ]
        )
    return [
        {
            "ordinal": index,
            "step_id": step_id,
            "action_class": action_class,
            "output_allowed": output_allowed,
            "emergency_stop_required": True,
        }
        for index, (step_id, action_class, output_allowed) in enumerate(phases, 1)
    ]


def build_plan(
    project_root: Path,
    request_path: Path,
    generator_probe_path: Path,
    acquisition_probe_path: Path,
    generator_raw_probe_path: Path,
    acquisition_raw_probe_path: Path,
    generator_target_manifest_path: Path,
    acquisition_target_manifest_path: Path,
    generator_host_key_evidence_path: Path,
    acquisition_host_key_evidence_path: Path,
    driver_evidence_path: Path,
    driver_contract_path: Path,
    acquisition_state_path: Path,
    authorization_bundle_path: Path | None,
) -> dict[str, Any]:
    request_binding, request_file = file_binding(
        project_root, request_path, "qualification request"
    )
    request = validate_request(load_json_object(request_file, "qualification request"))
    created_at = parse_utc(request["created_at_utc"], "request.created_at_utc")

    generator_binding, generator_file = file_binding(
        project_root, generator_probe_path, "generator probe report"
    )
    acquisition_binding, acquisition_file = file_binding(
        project_root, acquisition_probe_path, "acquisition probe report"
    )
    generator_report = load_json_object(
        generator_file, "generator probe report"
    )
    acquisition_report = load_json_object(
        acquisition_file, "acquisition probe report"
    )
    generator = validate_probe_report(
        generator_report,
        "generator",
        created_at,
        request["probe_max_age_seconds"],
    )
    acquisition = validate_probe_report(
        acquisition_report,
        "acquisition",
        created_at,
        request["probe_max_age_seconds"],
    )
    if generator["identity"]["machine_id"] == acquisition["identity"]["machine_id"]:
        raise QualificationError("generator and acquisition must be different boards")
    if (
        generator["connection"]["ssh_host_key_sha256"]
        == acquisition["connection"]["ssh_host_key_sha256"]
    ):
        raise QualificationError(
            "generator and acquisition SSH identities must be different"
        )
    probe_provenance = {
        "generator": validate_probe_provenance(
            project_root,
            generator_raw_probe_path,
            generator_target_manifest_path,
            generator_host_key_evidence_path,
            generator_report,
            generator_binding,
            "generator",
            created_at,
            request["probe_max_age_seconds"],
        ),
        "acquisition": validate_probe_provenance(
            project_root,
            acquisition_raw_probe_path,
            acquisition_target_manifest_path,
            acquisition_host_key_evidence_path,
            acquisition_report,
            acquisition_binding,
            "acquisition",
            created_at,
            request["probe_max_age_seconds"],
        ),
    }
    physical_evidence = validate_physical_evidence(
        project_root, request["topology"], request["safety"]
    )

    contract_binding, contract_file = file_binding(
        project_root, driver_contract_path, "generator driver contract"
    )
    contract_summary = validate_driver_contract(
        load_json_object(contract_file, "generator driver contract"),
        contract_binding["sha256"],
        request["driver_expectation"],
    )
    contract_summary["expected_driver_version"] = request[
        "driver_expectation"
    ]["expected_driver_version"]
    contract_review_binding, contract_review = validate_contract_review(
        project_root,
        request["driver_expectation"]["contract_review_path"],
        request["driver_expectation"],
        contract_summary,
        created_at,
    )
    advanced_review_binding, advanced_review = validate_advanced_review(
        project_root,
        request["driver_expectation"]["advanced_review_path"],
        request["driver_expectation"],
        contract_summary,
    )

    driver_binding, driver_file = file_binding(
        project_root, driver_evidence_path, "generator driver evidence"
    )
    driver_gate = validate_driver_evidence(
        load_json_object(driver_file, "generator driver evidence"),
        generator,
        created_at,
        request["probe_max_age_seconds"],
        contract_summary,
        request["mode"],
        request["safety"]["single_writer_id"],
    )
    driver_gate["classification"] = request["driver_expectation"]["classification"]
    driver_gate["contract_review"] = contract_review
    driver_gate["advanced_review"] = advanced_review

    state_binding, state_file = file_binding(
        project_root, acquisition_state_path, "acquisition pre-state"
    )
    acquisition_state = validate_acquisition_state(
        load_json_object(state_file, "acquisition pre-state"),
        generator,
        acquisition,
        request["topology"],
        created_at,
        request["probe_max_age_seconds"],
    )

    authorization_binding = None
    authorizations = None
    if request["mode"] == "active":
        if authorization_bundle_path is None:
            raise QualificationError(
                "active qualification requires deployment authorization bundle"
            )
        authorization_binding, authorization_file = file_binding(
            project_root,
            authorization_bundle_path,
            "deployment authorization bundle",
        )
        authorizations = validate_deployment_authorizations(
            project_root,
            load_json_object(authorization_file, "deployment authorization bundle"),
            generator,
            acquisition,
            probe_provenance,
            request,
            request_binding,
            created_at,
        )
        generator_plan = load_json_object(
            resolve_project_file(
                project_root,
                Path(authorizations["generator"]["plan_file"]["path"]),
                "generator deployment plan",
            )[0],
            "generator deployment plan",
        )
        deployment_safety = require_object(
            require_object(
                generator_plan.get("contract"), "generator deployment contract"
            ).get("safety"),
            "generator deployment safety",
        )
        declared_ceiling = deployment_safety.get("maximum_output_volts")
        if declared_ceiling is None:
            raise QualificationError(
                "generator deployment contract has no positive output ceiling"
            )
        deployment_ceiling_vpeak = require_number(
            declared_ceiling,
            "deployment safety maximum_output_volts",
            exclusive_minimum=0,
        ) * request["safety"]["deployment_maximum_output_to_vpeak_factor"]
        if (
            request["safety"]["maximum_output_volts_peak"]
            > deployment_ceiling_vpeak
        ):
            raise QualificationError(
                "qualification output ceiling exceeds deployment safety contract"
            )
        if (
            require_object(
                generator_plan["contract"].get("deployment"),
                "generator deployment settings",
            ).get("allow_live_output_test")
            is not True
        ):
            raise QualificationError(
                "generator deployment contract does not allow live output tests"
            )
    elif authorization_bundle_path is not None:
        raise QualificationError(
            "read-only qualification must not carry live authorization receipts"
        )

    safety = request["safety"]
    topology = request["topology"]
    path_limits: dict[str, Any] = {}
    for path_name, input_name in (
        ("local_path", "generator_local_input"),
        ("remote_path", "acquisition_remote_input"),
    ):
        signal_path = topology[path_name]
        worst_case_attenuation = max(
            0.0,
            signal_path["attenuation_db"]
            - signal_path["attenuation_uncertainty_db"],
        )
        predicted = voltage_after_attenuation(
            safety["maximum_output_volts_peak"], worst_case_attenuation
        )
        ceiling = (
            topology[input_name]["range_volts_peak"]
            * safety["input_headroom_fraction"]
        )
        if predicted > ceiling:
            raise QualificationError(
                f"{path_name} worst-case input {predicted:.9g} Vpeak exceeds "
                f"declared headroom ceiling {ceiling:.9g} Vpeak"
            )
        path_limits[path_name] = {
            "worst_case_attenuation_db": worst_case_attenuation,
            "predicted_max_input_volts_peak": predicted,
            "headroom_ceiling_volts_peak": ceiling,
        }

    baseline_time = request["acquisition"]["baseline"]["duration_seconds"]
    if request["mode"] == "active":
        one_shot_time = request["sequence"]["one_shot"]["capture_window_seconds"]
        modes_time = sum(
            mode["duration_seconds"] * mode["repetition_count"]
            for mode in request["sequence"]["modes"]
        )
        expected_emitted_by_step = {
            request["sequence"]["one_shot"]["step_id"]: request["sequence"][
                "one_shot"
            ]["expected_emitted_count"],
            **{
                mode["step_id"]: mode["expected_emitted_count"]
                * mode["repetition_count"]
                for mode in request["sequence"]["modes"]
            },
        }
    else:
        one_shot_time = modes_time = 0.0
        expected_emitted_by_step = {}
    planned_signal_time = baseline_time + one_shot_time + modes_time
    if planned_signal_time > safety["maximum_runtime_seconds"]:
        raise QualificationError(
            "declared baseline/output windows exceed maximum runtime"
        )

    inputs = {
        "request": request_binding,
        "generator_probe_report": generator_binding,
        "acquisition_probe_report": acquisition_binding,
        "generator_driver_evidence": driver_binding,
        "generator_driver_contract": contract_binding,
        "driver_contract_review": contract_review_binding,
        "advanced_driver_review": advanced_review_binding,
        "acquisition_pre_state": state_binding,
        "deployment_authorization_bundle": authorization_binding,
        "generator_raw_probe": probe_provenance["generator"]["raw_probe"],
        "acquisition_raw_probe": probe_provenance["acquisition"]["raw_probe"],
        "generator_target_manifest": probe_provenance["generator"][
            "target_manifest"
        ],
        "acquisition_target_manifest": probe_provenance["acquisition"][
            "target_manifest"
        ],
        "generator_host_key_evidence": probe_provenance["generator"][
            "host_key_verification"
        ],
        "acquisition_host_key_evidence": probe_provenance["acquisition"][
            "host_key_verification"
        ],
        **physical_evidence,
    }
    plan: dict[str, Any] = {
        "plan_schema_version": 1,
        "plan_kind": "red-pitaya-loopback-qualification",
        "qualification_id": request["qualification_id"],
        "created_at_utc": request["created_at_utc"],
        "mode": request["mode"],
        "purpose": request["purpose"],
        "inputs": inputs,
        "boards": {
            "generator": generator,
            "acquisition": acquisition,
        },
        "probe_provenance": probe_provenance,
        "physical_evidence": physical_evidence,
        "topology": topology,
        "safety": {
            **safety,
            "path_limits": path_limits,
            "planned_signal_windows_seconds": planned_signal_time,
        },
        "driver_gate": driver_gate,
        "authorizations": authorizations,
        "acquisition": {
            **request["acquisition"],
            "pre_state": acquisition_state,
        },
        "sequence": request["sequence"],
        "phases": make_phases(request),
        "execution_contract": {
            "single_use": request["mode"] == "active",
            "single_use_nonce": (
                authorizations["qualification_binding"]["single_use_nonce"]
                if authorizations is not None
                else None
            ),
            "authorization_expires_at_utc": (
                authorizations["qualification_binding"]["expires_at_utc"]
                if authorizations is not None
                else None
            ),
            "live_executor_must_exclusive_create_run_record": (
                request["mode"] == "active"
            ),
        },
        "measurement_contract": {
            "required_metrics": list(REQUIRED_METRICS),
            "expected_emitted_by_step": expected_emitted_by_step,
            "expected_emitted_count_total": sum(
                expected_emitted_by_step.values()
            ),
            "waveform_labels": {
                "generator-preview": "reconstructed",
                "generator-local-input": "measured",
                "acquisition-remote-input": "measured",
            },
            "count_rule": (
                "Use authoritative hardware counters when the approved contract "
                "advertises event-counter; never substitute UI preview counts."
            ),
        },
        "acceptance": request["acceptance"],
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
    parser.add_argument("--generator-probe-report", required=True, type=Path)
    parser.add_argument("--acquisition-probe-report", required=True, type=Path)
    parser.add_argument("--generator-raw-probe", required=True, type=Path)
    parser.add_argument("--acquisition-raw-probe", required=True, type=Path)
    parser.add_argument("--generator-target-manifest", required=True, type=Path)
    parser.add_argument("--acquisition-target-manifest", required=True, type=Path)
    parser.add_argument(
        "--generator-host-key-evidence", required=True, type=Path
    )
    parser.add_argument(
        "--acquisition-host-key-evidence", required=True, type=Path
    )
    parser.add_argument("--generator-driver-evidence", required=True, type=Path)
    parser.add_argument("--generator-driver-contract", required=True, type=Path)
    parser.add_argument("--acquisition-pre-state", required=True, type=Path)
    parser.add_argument("--authorization-bundle", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = build_plan(
            args.project_root,
            args.request,
            args.generator_probe_report,
            args.acquisition_probe_report,
            args.generator_raw_probe,
            args.acquisition_raw_probe,
            args.generator_target_manifest,
            args.acquisition_target_manifest,
            args.generator_host_key_evidence,
            args.acquisition_host_key_evidence,
            args.generator_driver_evidence,
            args.generator_driver_contract,
            args.acquisition_pre_state,
            args.authorization_bundle,
        )
        output = project_output_path(
            args.project_root, args.output, "qualification plan output"
        )
        write_json_exclusive(output, plan, "qualification plan output")
        print(
            f"Wrote immutable offline qualification plan {output} "
            f"(SHA-256 {plan['plan_sha256']}). No board was contacted."
        )
        return 0
    except (OSError, QualificationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
