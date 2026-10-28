#!/usr/bin/env python3
"""Offline tests for the two-board qualification skill."""

from __future__ import annotations

import base64
import json
import shutil
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import build_qualification_report
import generate_qualification_plan
import validate_qualification_plan
from qualification_common import (
    QualificationError,
    canonical_sha256,
    file_binding,
    resolve_project_file,
    sha256_file,
)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def fingerprint(byte: int) -> str:
    encoded = base64.b64encode(bytes([byte]) * 32).decode("ascii").rstrip("=")
    return "SHA256:" + encoded


def utc(seconds: int) -> str:
    base = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Fixture:
    def __init__(self, root: Path, mode: str = "active") -> None:
        self.root = root
        self.mode = mode
        self.generator_machine = "1" * 32
        self.acquisition_machine = "2" * 32
        self.generator_fingerprint = fingerprint(1)
        self.acquisition_fingerprint = fingerprint(2)
        self.paths = {
            "request": root / "inputs" / "request.json",
            "generator_probe": root / "evidence" / "generator-probe.json",
            "acquisition_probe": root / "evidence" / "acquisition-probe.json",
            "generator_raw_probe": root / "evidence" / "generator-raw-probe.json",
            "acquisition_raw_probe": root / "evidence" / "acquisition-raw-probe.json",
            "generator_manifest": root / "deploy" / "generator-manifest.json",
            "acquisition_manifest": root / "deploy" / "acquisition-manifest.json",
            "generator_host_key": root / "evidence" / "generator-host-key.json",
            "acquisition_host_key": root / "evidence" / "acquisition-host-key.json",
            "driver_evidence": root / "evidence" / "driver-evidence.json",
            "driver_contract": root / "contracts" / "driver-contract.json",
            "contract_review": root / "contracts" / "driver-contract-review.json",
            "acquisition_state": root / "evidence" / "acquisition-state.json",
            "output_limit": root / "calibration" / "output-limit.json",
            "local_input_range": root / "calibration" / "local-input-range.json",
            "remote_input_range": root / "calibration" / "remote-input-range.json",
            "local_path": root / "calibration" / "local-path.json",
            "remote_path": root / "calibration" / "remote-path.json",
            "qualification_binding": root / "deploy" / "qualification-binding.json",
            "authorization_bundle": root / "evidence" / "authorization-bundle.json",
            "plan": root / "qualification-plan.json",
            "observations": root / "observations.json",
        }
        for path in self.paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
        self._write_inputs()

    @staticmethod
    def raw_probe(
        role: str,
        machine_id: str,
        fingerprint_value: str,
        address: str,
    ) -> dict:
        return {
            "probe_schema_version": 1,
            "captured_at": utc(0),
            "probe_source": "offline test fixture from exact read-only probe",
            "connection": {
                "host": f"rp-{role}.local",
                "address": address,
                "username": "root",
                "host_key_verified": True,
                "ssh_host_key_sha256": fingerprint_value,
            },
            "identity": {
                "hostname": f"rp-{role}",
                "machine_id": machine_id,
                "serial": f"serial-{role}",
                "mac_addresses": [
                    "02:00:00:00:00:01"
                    if role == "generator"
                    else "02:00:00:00:00:02"
                ],
                "roles": [role],
            },
            "os": {"release": "3.00-57"},
            "board": {
                "model": "STEMlab 125-14 Z20",
                "model_code": "z20_125",
                "profiles_model_code": "z20_125",
                "monitor_model_name": "STEMlab 125-14 Z20",
                "monitor_model_id": "3",
            },
            "fpga": {
                "expected_artifact": "fpga.bin",
                "loader_script": "/opt/redpitaya/sbin/overlay.sh",
                "monitor_fpga_version": "0.94",
            },
            "storage": {
                "opt_redpitaya": {
                    "target": "/opt/redpitaya",
                    "source": "/dev/mmcblk0p1",
                    "fstype": "vfat",
                    "options": ["rw"],
                }
            },
        }

    @staticmethod
    def manifest(role: str, include_output: bool) -> dict:
        slug = f"qualification_{role}"
        return {
            "schema_version": 1,
            "project": {
                "slug": slug,
                "title": f"Qualification {role}",
                "version": "1.0.0",
                "description": "Offline qualification deployment fixture.",
            },
            "target": {
                "os_release": "3.00-57",
                "model": "z20_125",
                "role": role,
                "hostname": f"rp-{role}.local",
                "expected_serial_or_mac_suffix": (
                    "0001" if role == "generator" else "0002"
                ),
            },
            "application": {
                "enabled": False,
                "architecture": "none",
                "authentication": "none",
                "slug": slug,
                "routes": [],
                "controller_binary": None,
                "service_name": None,
                "python_abi": "none",
                "service_port": None,
            },
            "fpga": {
                "enabled": False,
                "project": "none",
                "artifact_name": None,
                "device_tree_name": None,
                "loader": "none",
                "register_contract": None,
                "version_register": None,
                "magic_value": None,
                "clock_hz": None,
            },
            "deployment": {
                "app_root": "/opt/redpitaya/www/apps",
                "mountpoint": "/opt/redpitaya",
                "staging_root": "/tmp/redpitaya-deploy/{deployment_id}",
                "backup_root": f"/root/redpitaya-backups/{slug}/{{deployment_id}}",
                "nginx_service": "redpitaya_nginx.service",
                "restore_read_only": True,
                "allow_live_output_test": include_output,
            },
            "safety": {
                "output_default": "disabled",
                "stop_action": "Disable output and verify output-enable readback is false.",
                "physical_test_requires_authorization": True,
                "rollback_required": True,
                "maximum_output_volts": 0.2,
            },
        }

    @staticmethod
    def host_key_evidence(
        role: str, address: str, fingerprint_value: str
    ) -> dict:
        return {
            "host_key_evidence_schema_version": 1,
            "role": role,
            "captured_at_utc": utc(20),
            "host": f"rp-{role}.local",
            "address": address,
            "algorithm": "ssh-ed25519",
            "ssh_host_key_sha256": fingerprint_value,
            "verification_method": "Independent console label and key comparison.",
            "verified_by": "offline-test-operator",
            "verification_reference": f"host-key-{role}-20260729",
            "result": "verified",
        }

    def _criteria(self) -> list[dict]:
        units = {
            "emitted_count": "events",
            "local_detected_count": "events",
            "remote_detected_count": "events",
            "local_delay_seconds": "s",
            "remote_delay_seconds": "s",
            "local_gain_ratio": "ratio",
            "remote_gain_ratio": "ratio",
            "local_correlation": "ratio",
            "remote_correlation": "ratio",
            "local_polarity": "",
            "remote_polarity": "",
            "local_width_seconds": "s",
            "remote_width_seconds": "s",
            "local_energy_volt2_seconds": "V2s",
            "remote_energy_volt2_seconds": "V2s",
            "local_ringing_hz": "Hz",
            "remote_ringing_hz": "Hz",
            "local_phase_degrees": "deg",
            "remote_phase_degrees": "deg",
            "detector_dead_time_seconds": "s",
            "local_data_loss_fraction": "ratio",
            "remote_data_loss_fraction": "ratio",
            "local_drop_count": "events",
            "remote_drop_count": "events",
            "counter_reset_behavior": "",
            "counter_rollover_behavior": "",
        }
        categorical = {
            "local_polarity": ["positive"],
            "remote_polarity": ["positive"],
            "counter_reset_behavior": ["epoch-advanced"],
            "counter_rollover_behavior": ["modulo-wrap-preserved"],
        }
        criteria = []
        for metric in generate_qualification_plan.REQUIRED_METRICS:
            if metric in categorical:
                minimum = maximum = None
                allowed = categorical[metric]
            elif "correlation" in metric or "data_loss_fraction" in metric:
                minimum, maximum, allowed = 0.0, 1.0, None
            elif "phase_degrees" in metric:
                minimum, maximum, allowed = -180.0, 180.0, None
            elif "delay_seconds" in metric:
                minimum, maximum, allowed = -1.0, 1.0, None
            else:
                minimum, maximum, allowed = 0.0, 1_000_000_000.0, None
            criteria.append(
                {
                    "metric": metric,
                    "unit": units[metric],
                    "minimum": minimum,
                    "maximum": maximum,
                    "allowed_values": allowed,
                }
            )
        return criteria

    def _write_inputs(self) -> None:
        contract = {
            "schema_version": 1,
            "namespace": "openpd-generator",
            "magic_ascii": "PDG1",
            "abi_major": 1,
            "abi_minor": 2,
            "register_base": "0x40000000",
            "address_span": "0x100",
            "capabilities": [
                "event-counter",
                "safe-stop",
                "single-writer",
                "watchdog",
            ],
            "registers": [
                {"name": "MAGIC", "offset": "0x0", "access": "ro", "role": "magic"},
                {
                    "name": "ABI_VERSION",
                    "offset": "0x4",
                    "access": "ro",
                    "role": "abi-version",
                },
                {
                    "name": "CONTROL",
                    "offset": "0x8",
                    "access": "rw",
                    "role": "control",
                },
                {
                    "name": "STATUS",
                    "offset": "0xc",
                    "access": "ro",
                    "role": "status",
                },
                {
                    "name": "WATCHDOG_TIMEOUT",
                    "offset": "0x10",
                    "access": "rw",
                    "role": "watchdog-timeout",
                },
                {
                    "name": "WATCHDOG_KICK",
                    "offset": "0x14",
                    "access": "wo",
                    "role": "watchdog-kick",
                },
                {
                    "name": "EVENT_COUNT_LO",
                    "offset": "0x18",
                    "access": "ro",
                    "role": "event-counter-low",
                },
                {
                    "name": "EVENT_COUNT_HI",
                    "offset": "0x1c",
                    "access": "ro",
                    "role": "event-counter-high",
                },
            ],
        }
        write_json(self.paths["driver_contract"], contract)
        contract_hash = sha256_file(self.paths["driver_contract"])
        if self.mode == "active":
            classification = "pdg1"
            expected_magic = "PDG1"
            abi_major = 1
            abi_minor = 1
            approved_hash = contract_hash
            expected_driver_version = "fixture-1"
            capabilities = [
                "event-counter",
                "safe-stop",
                "single-writer",
                "watchdog",
            ]
            contract_review_path = "contracts/driver-contract-review.json"
            one_shot = {
                "step_id": "one",
                "mode_name": "qualification-impulse",
                "amplitude_volts_peak": 0.01,
                "pulse_width_seconds": 0.00001,
                "polarity": "positive",
                "capture_window_seconds": 1.0,
                "expected_emitted_count": 1,
            }
            modes = [
                {
                    "step_id": "mode-a",
                    "mode_name": "bounded-burst",
                    "amplitude_volts_peak": 0.05,
                    "pulse_width_seconds": 0.00002,
                    "polarity": "positive",
                    "capture_window_seconds": 1.0,
                    "expected_emitted_count": 10,
                    "duration_seconds": 10.0,
                    "repetition_count": 1,
                }
            ]
        else:
            classification = "unknown-read-only"
            expected_magic = abi_major = abi_minor = approved_hash = None
            expected_driver_version = None
            capabilities = []
            contract_review_path = None
            one_shot = None
            modes = []
        if self.mode == "active":
            write_json(
                self.paths["contract_review"],
                {
                    "review_schema_version": 1,
                    "classification": "pdg1",
                    "contract_sha256": contract_hash,
                    "magic_ascii": "PDG1",
                    "abi_major": 1,
                    "abi_minor": 2,
                    "driver_version": "fixture-1",
                    "approved_capabilities": sorted(capabilities),
                    "independent_from_qualification_request": True,
                    "reviewed_at_utc": utc(-3600),
                    "reviewer_identity": "independent-driver-reviewer",
                    "review_reference": "pdg1-release-fixture-1",
                },
            )
        request = {
            "request_schema_version": 1,
            "qualification_id": "offline-qualification-001",
            "created_at_utc": utc(60),
            "mode": self.mode,
            "probe_max_age_seconds": 300,
            "purpose": "Qualify declared generator fanout into local and remote inputs.",
            "topology": {
                "generator_output_channel": "declared-output-1",
                "generator_local_input": {
                    "channel": "declared-local-input-1",
                    "range_volts_peak": 1.0,
                    "coupling": "declared-dc",
                    "termination_ohms": 50.0,
                    "range_evidence_path": "calibration/local-input-range.json",
                },
                "acquisition_remote_input": {
                    "channel": "declared-remote-input-1",
                    "range_volts_peak": 1.0,
                    "coupling": "declared-dc",
                    "termination_ohms": 50.0,
                    "range_evidence_path": "calibration/remote-input-range.json",
                },
                "fanout_description": (
                    "Operator-inspected passive fanout from generator output to "
                    "both declared inputs."
                ),
                "fanout_verified": True,
                "shared_reference_verified": True,
                "local_path": {
                    "attenuation_db": 3.0,
                    "attenuation_uncertainty_db": 0.5,
                    "evidence_path": "calibration/local-path.json",
                },
                "remote_path": {
                    "attenuation_db": 6.0,
                    "attenuation_uncertainty_db": 0.5,
                    "evidence_path": "calibration/remote-path.json",
                },
            },
            "safety": {
                "maximum_output_volts_peak": 0.1,
                "voltage_unit": "Vpeak",
                "output_limit_evidence_path": "calibration/output-limit.json",
                "deployment_maximum_output_to_vpeak_factor": 1.0,
                "input_headroom_fraction": 0.8,
                "one_shot_ceiling_fraction": 0.2,
                "safe_output_state": "disabled",
                "emergency_stop_action": "Use reviewed application safe-stop action.",
                "emergency_stop_verification": (
                    "Read output-enable state and independently observe quiet input."
                ),
                "single_writer_id": "qualification-writer-001",
                "watchdog_timeout_seconds": 2.0,
                "maximum_runtime_seconds": 120.0,
                "restore_acquisition_action": "Restore exact pre-state snapshot.",
                "restore_acquisition_verification": (
                    "Read back settings and compare pre-state hash."
                ),
            },
            "driver_expectation": {
                "classification": classification,
                "expected_magic_ascii": expected_magic,
                "expected_abi_major": abi_major,
                "minimum_abi_minor": abi_minor,
                "expected_driver_version": expected_driver_version,
                "approved_contract_sha256": approved_hash,
                "required_capabilities": capabilities,
                "contract_review_path": contract_review_path,
                "advanced_review_path": None,
            },
            "acquisition": {
                "local": {
                    "sample_rate_hz": 1_000_000.0,
                    "record_samples": 4096,
                    "pretrigger_samples": 1024,
                    "trigger_mode": "declared-threshold",
                    "sample_units": "V",
                },
                "remote": {
                    "sample_rate_hz": 1_000_000.0,
                    "record_samples": 4096,
                    "pretrigger_samples": 1024,
                    "trigger_mode": "declared-threshold",
                    "sample_units": "V",
                },
                "baseline": {
                    "duration_seconds": 5.0,
                    "minimum_records": 5,
                },
                "restore_required": True,
                "counter_policy": {
                    "counter_width_bits": 64,
                    "reset_semantics": "new epoch, lifetime count preserved",
                    "rollover_method": "boundary injection while output disabled",
                },
            },
            "sequence": {
                "deterministic_seed": 12345,
                "one_shot": one_shot,
                "modes": modes,
            },
            "acceptance": {"criteria": self._criteria()},
        }
        write_json(self.paths["request"], request)
        write_json(
            self.paths["output_limit"],
            {
                "evidence_schema_version": 1,
                "kind": "output-limit-rating",
                "board_role": "generator",
                "channel": "declared-output-1",
                "maximum_output_volts_peak": 0.1,
                "voltage_unit": "Vpeak",
                "deployment_maximum_output_unit": "Vpeak",
                "deployment_maximum_output_to_vpeak_factor": 1.0,
                "source": "Reviewed output-path laboratory rating.",
                "reviewed_at_utc": utc(-3600),
            },
        )
        for role, path_name, channel in (
            ("local", "local_input_range", "declared-local-input-1"),
            ("remote", "remote_input_range", "declared-remote-input-1"),
        ):
            write_json(
                self.paths[path_name],
                {
                    "evidence_schema_version": 1,
                    "kind": "input-range-rating",
                    "path_role": role,
                    "channel": channel,
                    "range_volts_peak": 1.0,
                    "voltage_unit": "Vpeak",
                    "coupling": "declared-dc",
                    "termination_ohms": 50.0,
                    "source": f"Reviewed {role} input configuration and rating.",
                    "reviewed_at_utc": utc(-3600),
                },
            )
        for role, path_name, attenuation in (
            ("local", "local_path", 3.0),
            ("remote", "remote_path", 6.0),
        ):
            write_json(
                self.paths[path_name],
                {
                    "evidence_schema_version": 1,
                    "kind": "signal-path-calibration",
                    "path_role": role,
                    "attenuation_db": attenuation,
                    "attenuation_uncertainty_db": 0.5,
                    "voltage_unit": "Vpeak",
                    "measurement_method": "Traceable swept-amplitude comparison.",
                    "instrument_reference": f"fixture-calibrator-{role}",
                    "measured_at_utc": utc(-3600),
                },
            )
        generator_raw = self.raw_probe(
            "generator",
            self.generator_machine,
            self.generator_fingerprint,
            "192.0.2.10",
        )
        acquisition_raw = self.raw_probe(
            "acquisition",
            self.acquisition_machine,
            self.acquisition_fingerprint,
            "192.0.2.11",
        )
        generator_manifest = self.manifest("generator", True)
        acquisition_manifest = self.manifest("acquisition", False)
        write_json(
            self.paths["generator_raw_probe"], generator_raw
        )
        write_json(
            self.paths["acquisition_raw_probe"], acquisition_raw
        )
        write_json(self.paths["generator_manifest"], generator_manifest)
        write_json(self.paths["acquisition_manifest"], acquisition_manifest)
        analyzer, _, _ = generate_qualification_plan.deployment_modules()
        generator_report, generator_status = analyzer.analyze(
            generator_raw, generator_manifest, "generator"
        )
        acquisition_report, acquisition_status = analyzer.analyze(
            acquisition_raw, acquisition_manifest, "acquisition"
        )
        if generator_status or acquisition_status:
            raise AssertionError("offline raw-probe fixture did not analyze cleanly")
        if generator_report["warnings"] or acquisition_report["warnings"]:
            raise AssertionError("offline raw-probe fixture produced warnings")
        write_json(self.paths["generator_probe"], generator_report)
        write_json(self.paths["acquisition_probe"], acquisition_report)
        write_json(
            self.paths["generator_host_key"],
            self.host_key_evidence(
                "generator", "192.0.2.10", self.generator_fingerprint
            ),
        )
        write_json(
            self.paths["acquisition_host_key"],
            self.host_key_evidence(
                "acquisition", "192.0.2.11", self.acquisition_fingerprint
            ),
        )
        write_json(
            self.paths["driver_evidence"],
            {
                "driver_evidence_schema_version": 1,
                "captured_at_utc": utc(30),
                "source": "Read-only driver identity and status register snapshot.",
                "board_machine_id": self.generator_machine,
                "ssh_host_key_sha256": self.generator_fingerprint,
                "read_only_probe": True,
                "output_enabled_readback": False,
                "writer_owner": None,
                "contract": {
                    "magic_ascii": "PDG1",
                    "abi_major": 1,
                    "abi_minor": 2,
                    "register_contract_sha256": contract_hash,
                    "register_base": "0x40000000",
                    "address_span": "0x100",
                    "capabilities": [
                        "event-counter",
                        "safe-stop",
                        "single-writer",
                        "watchdog",
                    ],
                    "driver_version": "fixture-1",
                },
            },
        )
        write_json(
            self.paths["acquisition_state"],
            {
                "state_schema_version": 1,
                "captured_at_utc": utc(30),
                "capture_method": "Read-only local and remote acquisition status.",
                "generator_machine_id": self.generator_machine,
                "acquisition_machine_id": self.acquisition_machine,
                "generator_input_state": {
                    "state_format_version": 1,
                    "channel": "declared-local-input-1",
                    "enabled": False,
                    "decimation": 1,
                    "trigger_source": "disabled",
                    "trigger_level_volts": 0.0,
                    "trigger_delay_samples": 0,
                    "gain_mode": "lv",
                    "coupling": "declared-dc",
                    "termination_ohms": 50.0,
                    "sample_units": "V",
                },
                "acquisition_input_state": {
                    "state_format_version": 1,
                    "channel": "declared-remote-input-1",
                    "enabled": False,
                    "decimation": 1,
                    "trigger_source": "disabled",
                    "trigger_level_volts": 0.0,
                    "trigger_delay_samples": 0,
                    "gain_mode": "lv",
                    "coupling": "declared-dc",
                    "termination_ohms": 50.0,
                    "sample_units": "V",
                },
                "restore_verification_method": "Exact readback comparison.",
            },
        )
        if self.mode == "active":
            generator_plan = self._deployment_plan(
                "generator",
                self.generator_machine,
                self.generator_fingerprint,
                include_output=True,
            )
            acquisition_plan = self._deployment_plan(
                "acquisition",
                self.acquisition_machine,
                self.acquisition_fingerprint,
                include_output=False,
            )
            write_json(self.root / "deploy" / "generator-plan.json", generator_plan)
            write_json(self.root / "deploy" / "acquisition-plan.json", acquisition_plan)
            generator_output_receipt = generator_plan["gate_evidence"][
                "functional_output_test_authorized"
            ]
            write_json(
                self.paths["qualification_binding"],
                {
                    "qualification_authorization_schema_version": 1,
                    "source_skill": "deploy-red-pitaya-project",
                    "result": "authorized",
                    "qualification_id": request["qualification_id"],
                    "request_sha256": sha256_file(self.paths["request"]),
                    "generator_deployment_id": generator_plan["deployment_id"],
                    "generator_plan_core_sha256": generator_plan[
                        "plan_core_sha256"
                    ],
                    "generator_output_receipt_sha256": generator_output_receipt[
                        "sha256"
                    ],
                    "acquisition_deployment_id": acquisition_plan["deployment_id"],
                    "acquisition_plan_core_sha256": acquisition_plan[
                        "plan_core_sha256"
                    ],
                    "maximum_output_volts_peak": 0.1,
                    "voltage_unit": "Vpeak",
                    "single_use_nonce": "offline-run-nonce-001",
                    "recorded_at_utc": utc(70),
                    "expires_at_utc": utc(1800),
                    "approver_identity": "offline-test-approver",
                    "authorization_reference": "qualification-binding-001",
                },
            )
            write_json(
                self.paths["authorization_bundle"],
                {
                    "authorization_bundle_schema_version": 1,
                    "source_skill": "deploy-red-pitaya-project",
                    "generator_plan": "deploy/generator-plan.json",
                    "acquisition_plan": "deploy/acquisition-plan.json",
                    "qualification_binding": "deploy/qualification-binding.json",
                },
            )

    def _deployment_plan(
        self,
        role: str,
        machine_id: str,
        fingerprint_value: str,
        *,
        include_output: bool,
    ) -> dict:
        generate_qualification_plan.deployment_modules()
        deploy_plan = __import__("generate_deployment_plan")
        deploy_gate = __import__("record_deployment_gate")
        deployment_id = f"{role}-deployment-001"
        config_path = self.root / "deploy" / f"{role}-marker.json"
        file_map_path = self.root / "deploy" / f"{role}-file-map.json"
        write_json(
            config_path,
            {"fixture": "qualification", "role": role, "output": include_output},
        )
        slug = f"qualification_{role}"
        write_json(
            file_map_path,
            {
                "schema_version": 1,
                "files": [
                    {
                        "source": f"deploy/{role}-marker.json",
                        "destination": (
                            f"/etc/redpitaya/{slug}/qualification-marker.json"
                        ),
                        "component": "configuration",
                        "mode": "0644",
                    }
                ],
            },
        )
        manifest_path = self.paths[f"{role}_manifest"]
        probe_path = self.paths[f"{role}_probe"]
        plan = deploy_plan.build_plan(
            manifest_path,
            probe_path,
            self.root,
            file_map_path,
            deployment_id,
        )
        sequence = [
            ("dry_run_reviewed", None),
            ("stage_authorized", "stage"),
            ("stage_hashes_verified", None),
            (
                "precommit_live_actions_authorized",
                "precommit-live-actions",
            ),
        ]
        if include_output:
            sequence.extend(
                [
                    ("safe_stop_confirmed", None),
                    ("backup_verified", None),
                    ("commit_authorized", "commit"),
                    (
                        "functional_output_test_authorized",
                        "physical-output-test",
                    ),
                ]
            )
        base_second = 10 if role == "generator" else 30
        for offset, (gate, scope) in enumerate(sequence):
            timestamp = utc(base_second + offset)
            summary = f"Offline fixture evidence for {role} {gate}."
            evidence_path = (
                self.root / "deploy" / f"{role}-{offset:02d}-{gate}.json"
            )
            if scope is None:
                evidence_path.write_text(summary + "\n", encoding="utf-8")
            else:
                write_json(
                    evidence_path,
                    {
                        "authorization_schema_version": 2,
                        "gate": gate,
                        "result": "authorized",
                        "recorded_at_utc": timestamp,
                        "authorization_scope": scope,
                        "deployment_id": plan["deployment_id"],
                        "plan_core_sha256": plan["plan_core_sha256"],
                        "target_machine_id": machine_id,
                        "ssh_host_key_sha256": fingerprint_value,
                        "approver_identity": "offline-test-approver",
                        "authorization_reference": f"auth-{role}-{gate}",
                        "summary": summary,
                    },
                )
            plan = deploy_gate.advance_gate(
                plan,
                self.root,
                gate,
                evidence_path,
                timestamp,
                summary,
            )
        return plan

    def build_plan(self) -> dict:
        plan = generate_qualification_plan.build_plan(
            self.root,
            self.paths["request"],
            self.paths["generator_probe"],
            self.paths["acquisition_probe"],
            self.paths["generator_raw_probe"],
            self.paths["acquisition_raw_probe"],
            self.paths["generator_manifest"],
            self.paths["acquisition_manifest"],
            self.paths["generator_host_key"],
            self.paths["acquisition_host_key"],
            self.paths["driver_evidence"],
            self.paths["driver_contract"],
            self.paths["acquisition_state"],
            (
                self.paths["authorization_bundle"]
                if self.mode == "active"
                else None
            ),
        )
        write_json(self.paths["plan"], plan)
        return plan

    def observations(self, plan: dict) -> dict:
        artifact_dir = self.root / "artifacts"
        artifact_dir.mkdir(exist_ok=True)
        bindings = {}
        for channel in (
            "generator-preview",
            "generator-local-input",
            "acquisition-remote-input",
        ):
            path = artifact_dir / f"{channel}.csv"
            path.write_text("t,value\n0,0\n1,1\n", encoding="utf-8")
            bindings[channel] = file_binding(self.root, path, channel)[0]
        required_artifact_paths = {}
        artifact_kinds = set(build_qualification_report.REQUIRED_ARTIFACT_KINDS)
        if plan["mode"] == "active":
            artifact_kinds.add("single-use-run-record")
        for kind in artifact_kinds:
            path = artifact_dir / f"{kind}.json"
            if kind == "acquisition-restore-readback":
                write_json(
                    path,
                    {
                        "restore_evidence_schema_version": 1,
                        "qualification_id": plan["qualification_id"],
                        "generator_machine_id": plan["boards"]["generator"][
                            "identity"
                        ]["machine_id"],
                        "acquisition_machine_id": plan["boards"]["acquisition"][
                            "identity"
                        ]["machine_id"],
                        "captured_at_utc": utc(500),
                        "generator_input_state": plan["acquisition"]["pre_state"][
                            "generator_input_state"
                        ],
                        "acquisition_input_state": plan["acquisition"][
                            "pre_state"
                        ]["acquisition_input_state"],
                        "comparison_result": "exact-match",
                    },
                )
            elif kind == "single-use-run-record":
                write_json(
                    path,
                    {
                        "run_record_schema_version": 1,
                        "qualification_id": plan["qualification_id"],
                        "plan_sha256": plan["plan_sha256"],
                        "single_use_nonce": plan["execution_contract"][
                            "single_use_nonce"
                        ],
                        "started_at_utc": utc(120),
                        "executor_id": "offline-fixture-executor",
                        "writer_id": plan["safety"]["single_writer_id"],
                        "creation_method": "exclusive-create",
                    },
                )
            else:
                path.write_text(
                    json.dumps({"fixture": kind}, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            required_artifact_paths[kind] = path

        phase_count = len(plan["phases"])
        timeline = []
        for index, phase in enumerate(plan["phases"]):
            timeline.append(
                {
                    "ordinal": phase["ordinal"],
                    "step_id": phase["step_id"],
                    "status": "passed",
                    "started_at_utc": utc(120 + index),
                    "ended_at_utc": utc(121 + index),
                    "emergency_stop_available": True,
                }
            )
        runtime = float(phase_count)
        emitted_total = plan["measurement_contract"]["expected_emitted_count_total"]
        values = {
            "emitted_count": emitted_total,
            "local_detected_count": emitted_total,
            "remote_detected_count": emitted_total,
            "local_delay_seconds": 0.000001,
            "remote_delay_seconds": 0.000002,
            "local_gain_ratio": 0.8,
            "remote_gain_ratio": 0.5,
            "local_correlation": 0.99,
            "remote_correlation": 0.98,
            "local_polarity": "positive",
            "remote_polarity": "positive",
            "local_width_seconds": 0.00002,
            "remote_width_seconds": 0.00002,
            "local_energy_volt2_seconds": 0.000001,
            "remote_energy_volt2_seconds": 0.0000005,
            "local_ringing_hz": 100000.0,
            "remote_ringing_hz": 100000.0,
            "local_phase_degrees": 1.0,
            "remote_phase_degrees": 2.0,
            "detector_dead_time_seconds": 0.00001,
            "local_data_loss_fraction": 0.0,
            "remote_data_loss_fraction": 0.0,
            "local_drop_count": 0,
            "remote_drop_count": 0,
            "counter_reset_behavior": "epoch-advanced",
            "counter_rollover_behavior": "modulo-wrap-preserved",
        }
        criteria = {
            item["metric"]: item for item in plan["acceptance"]["criteria"]
        }
        measured_counts = {
            "emitted_count",
            "local_detected_count",
            "remote_detected_count",
            "local_drop_count",
            "remote_drop_count",
        }
        measurements = [
            {
                "metric": metric,
                "scope": "overall",
                "value": values[metric],
                "unit": criteria[metric]["unit"],
                "method": "Fixture calculation over hash-bound measured waveforms.",
                "provenance": "measured" if metric in measured_counts else "derived",
            }
            for metric in generate_qualification_plan.REQUIRED_METRICS
        ]
        pre_state_hash = plan["inputs"]["acquisition_pre_state"]["sha256"]
        return {
            "observation_schema_version": 1,
            "qualification_id": plan["qualification_id"],
            "plan_sha256": plan["plan_sha256"],
            "started_at_utc": utc(120),
            "ended_at_utc": utc(120 + phase_count),
            "executor_id": "offline-fixture-executor",
            "writer_id": plan["safety"]["single_writer_id"],
            "boards": {
                role: {
                    "machine_id": plan["boards"][role]["identity"]["machine_id"],
                    "ssh_host_key_sha256": plan["boards"][role]["connection"][
                        "ssh_host_key_sha256"
                    ],
                }
                for role in ("generator", "acquisition")
            },
            "driver_gate": {
                "classification": plan["driver_gate"]["classification"],
                "contract_sha256": plan["driver_gate"]["contract"]["sha256"],
                "magic_ascii": plan["driver_gate"]["contract"]["magic_ascii"],
                "abi_major": plan["driver_gate"]["contract"]["abi_major"],
                "abi_minor": plan["driver_gate"]["contract"]["abi_minor"],
                "access_decision": plan["driver_gate"]["access_decision"],
            },
            "authorization_bundle_sha256": (
                None
                if plan["inputs"]["deployment_authorization_bundle"] is None
                else plan["inputs"]["deployment_authorization_bundle"]["sha256"]
            ),
            "authorization_single_use_nonce": plan["execution_contract"][
                "single_use_nonce"
            ],
            "timeline": timeline,
            "waveforms": [
                {
                    "channel": channel,
                    "label": plan["measurement_contract"]["waveform_labels"][channel],
                    "artifact": bindings[channel],
                }
                for channel in bindings
            ],
            "measurements": measurements,
            "integrity": {
                "emitted_count": emitted_total,
                "local_detected_count": emitted_total,
                "remote_detected_count": emitted_total,
                "local_drop_count": 0,
                "remote_drop_count": 0,
                "local_data_loss_fraction": 0.0,
                "remote_data_loss_fraction": 0.0,
                "local_sequence_gaps": 0,
                "remote_sequence_gaps": 0,
                "fifo_high_water": 2,
                "fifo_capacity": 16,
                "dead_time_seconds": 0.00001,
            },
            "counter_checks": {
                "reset": {
                    "behavior": "epoch-advanced",
                    "before": emitted_total,
                    "after": emitted_total,
                    "epoch_before": 1,
                    "epoch_after": 2,
                    "counter_width_bits": 64,
                    "method": "Observed new epoch while preserving lifetime count.",
                    "passed": True,
                },
                "rollover": {
                    "behavior": "modulo-wrap-preserved",
                    "before": (2**64) - 2,
                    "after": 3,
                    "epoch_before": 2,
                    "epoch_after": 2,
                    "counter_width_bits": 64,
                    "method": "Boundary injection with output disabled.",
                    "passed": True,
                },
            },
            "safety": {
                "baseline_completed_before_output": True,
                "output_disabled_during_baseline": True,
                "emergency_stop_available_all_phases": True,
                "watchdog_armed": self.mode == "active",
                "watchdog_timeout_seconds": (
                    plan["safety"]["watchdog_timeout_seconds"]
                    if self.mode == "active"
                    else 0.0
                ),
                "maximum_commanded_output_volts_peak": (
                    0.05 if self.mode == "active" else 0.0
                ),
                "safe_stop_invoked": self.mode == "active",
                "output_disabled_after": True,
                "single_writer_preserved": True,
                "runtime_seconds": runtime,
                "acquisition_restored": True,
                "restored_state_sha256": pre_state_hash,
            },
            "artifacts": [
                {
                    "kind": kind,
                    "provenance": "measured",
                    "file": file_binding(
                        self.root, path, f"{kind} artifact"
                    )[0],
                }
                for kind, path in required_artifact_paths.items()
            ],
            "notes": ["Synthetic offline fixture; no board was contacted."],
        }


class QualificationToolsTest(unittest.TestCase):
    class _WorkspaceTemporaryDirectory:
        def __init__(self) -> None:
            parent = Path(__file__).resolve().parent / ".test-tmp"
            parent.mkdir(exist_ok=True)
            self.path = parent / uuid.uuid4().hex
            self.path.mkdir()
            self.name = str(self.path)

        def cleanup(self) -> None:
            parent = Path(__file__).resolve().parent / ".test-tmp"
            resolved = self.path.resolve()
            resolved.relative_to(parent.resolve())
            shutil.rmtree(resolved, ignore_errors=True)
            try:
                parent.rmdir()
            except OSError:
                pass

    def fixture(
        self, mode: str = "active"
    ) -> tuple[_WorkspaceTemporaryDirectory, Fixture]:
        temporary_root = Path(__file__).resolve().parent / ".test-tmp"
        temporary_root.mkdir(exist_ok=True)
        temporary = self._WorkspaceTemporaryDirectory()
        fixture = Fixture(Path(temporary.name), mode)
        return temporary, fixture

    def test_active_plan_deep_validation(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        validate_qualification_plan.validate_plan(plan, fixture.root)
        self.assertEqual(plan["driver_gate"]["access_decision"], "single-writer-active")
        self.assertEqual(
            plan["phases"][2]["step_id"], "baseline-output-disabled"
        )
        output_ordinals = [
            phase["ordinal"] for phase in plan["phases"] if phase["output_allowed"]
        ]
        self.assertGreater(min(output_ordinals), 3)

    def test_identity_mismatch_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        evidence = json.loads(fixture.paths["driver_evidence"].read_text())
        evidence["board_machine_id"] = "f" * 32
        write_json(fixture.paths["driver_evidence"], evidence)
        with self.assertRaisesRegex(QualificationError, "identity mismatch"):
            fixture.build_plan()

    def test_same_board_in_both_roles_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        report = json.loads(fixture.paths["acquisition_probe"].read_text())
        report["identity"]["machine_id"] = fixture.generator_machine
        write_json(fixture.paths["acquisition_probe"], report)
        with self.assertRaisesRegex(QualificationError, "different boards"):
            fixture.build_plan()

    def test_voltage_headroom_violation_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        request = json.loads(fixture.paths["request"].read_text())
        request["topology"]["generator_local_input"]["range_volts_peak"] = 0.01
        write_json(fixture.paths["request"], request)
        rating = json.loads(fixture.paths["local_input_range"].read_text())
        rating["range_volts_peak"] = 0.01
        write_json(fixture.paths["local_input_range"], rating)
        binding = json.loads(fixture.paths["qualification_binding"].read_text())
        binding["request_sha256"] = sha256_file(fixture.paths["request"])
        write_json(fixture.paths["qualification_binding"], binding)
        with self.assertRaisesRegex(QualificationError, "headroom ceiling"):
            fixture.build_plan()

    def test_contract_hash_mismatch_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        request = json.loads(fixture.paths["request"].read_text())
        request["driver_expectation"]["approved_contract_sha256"] = "0" * 64
        write_json(fixture.paths["request"], request)
        with self.assertRaisesRegex(QualificationError, "approved hash"):
            fixture.build_plan()

    def test_stale_probe_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        probe = json.loads(fixture.paths["generator_probe"].read_text())
        probe["evidence"]["captured_at"] = utc(-1000)
        write_json(fixture.paths["generator_probe"], probe)
        with self.assertRaisesRegex(QualificationError, "stale"):
            fixture.build_plan()

    def test_deployment_voltage_ceiling_is_enforced(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        request = json.loads(fixture.paths["request"].read_text())
        request["safety"]["maximum_output_volts_peak"] = 0.3
        write_json(fixture.paths["request"], request)
        output_limit = json.loads(fixture.paths["output_limit"].read_text())
        output_limit["maximum_output_volts_peak"] = 0.3
        write_json(fixture.paths["output_limit"], output_limit)
        binding = json.loads(fixture.paths["qualification_binding"].read_text())
        binding["request_sha256"] = sha256_file(fixture.paths["request"])
        binding["maximum_output_volts_peak"] = 0.3
        write_json(fixture.paths["qualification_binding"], binding)
        with self.assertRaisesRegex(QualificationError, "deployment safety"):
            fixture.build_plan()

    def test_driver_readback_mismatch_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        evidence = json.loads(fixture.paths["driver_evidence"].read_text())
        evidence["contract"]["magic_ascii"] = "EVIL"
        write_json(fixture.paths["driver_evidence"], evidence)
        with self.assertRaisesRegex(QualificationError, "magic_ascii mismatch"):
            fixture.build_plan()

    def test_driver_version_mismatch_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        evidence = json.loads(fixture.paths["driver_evidence"].read_text())
        evidence["contract"]["driver_version"] = "unapproved-version"
        write_json(fixture.paths["driver_evidence"], evidence)
        with self.assertRaisesRegex(QualificationError, "version mismatch"):
            fixture.build_plan()

    def test_unknown_driver_is_read_only(self) -> None:
        temporary, fixture = self.fixture("read-only")
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        validate_qualification_plan.validate_plan(plan, fixture.root)
        self.assertEqual(plan["driver_gate"]["access_decision"], "read-only")
        self.assertFalse(any(phase["output_allowed"] for phase in plan["phases"]))
        self.assertIsNone(plan["authorizations"])

    def test_unknown_driver_cannot_be_active(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        request = json.loads(fixture.paths["request"].read_text())
        request["driver_expectation"] = {
            "classification": "unknown-read-only",
            "expected_magic_ascii": None,
            "expected_abi_major": None,
            "minimum_abi_minor": None,
            "expected_driver_version": None,
            "approved_contract_sha256": None,
            "required_capabilities": [],
            "contract_review_path": None,
            "advanced_review_path": None,
        }
        write_json(fixture.paths["request"], request)
        with self.assertRaisesRegex(QualificationError, "active mode is forbidden"):
            fixture.build_plan()

    def test_plan_tampering_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        plan["safety"]["maximum_output_volts_peak"] *= 2
        with self.assertRaisesRegex(QualificationError, "content hash"):
            validate_qualification_plan.validate_plan(plan, fixture.root)

    def test_authorization_identity_mismatch_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan_path = fixture.root / "deploy" / "generator-plan.json"
        deployment = json.loads(plan_path.read_text())
        deployment["target"]["identity"]["machine_id"] = "f" * 32
        write_json(plan_path, deployment)
        with self.assertRaisesRegex(
            QualificationError, "deep validation|plan_core_sha256"
        ):
            fixture.build_plan()

    def test_report_bundle_is_emitted_and_valid(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        observations = fixture.observations(plan)
        write_json(fixture.paths["observations"], observations)
        outputs = [
            fixture.root / "result" / "evidence.json",
            fixture.root / "result" / "measurements.csv",
            fixture.root / "result" / "report.md",
        ]
        outputs[0].parent.mkdir()
        code = build_qualification_report.main(
            [
                "--project-root",
                str(fixture.root),
                "--plan",
                str(fixture.paths["plan"]),
                "--observations",
                str(fixture.paths["observations"]),
                "--generated-at-utc",
                utc(600),
                "--evidence-json",
                str(outputs[0]),
                "--measurements-csv",
                str(outputs[1]),
                "--report-md",
                str(outputs[2]),
            ]
        )
        self.assertEqual(code, 0)
        evidence = json.loads(outputs[0].read_text())
        self.assertEqual(evidence["overall_result"], "PASS")
        self.assertEqual(
            evidence["evidence_core_sha256"],
            canonical_sha256(
                {
                    key: value
                    for key, value in evidence.items()
                    if key != "evidence_core_sha256"
                }
            ),
        )
        self.assertIn("emitted_count", outputs[1].read_text())
        self.assertIn("measured", outputs[2].read_text())
        self.assertIn("reconstructed", outputs[2].read_text())

    def test_mislabeled_reconstructed_waveform_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        observations = fixture.observations(plan)
        observations["waveforms"][0]["label"] = "measured"
        write_json(fixture.paths["observations"], observations)
        plan_binding = file_binding(fixture.root, fixture.paths["plan"], "plan")[0]
        obs_binding = file_binding(
            fixture.root, fixture.paths["observations"], "observations"
        )[0]
        with self.assertRaisesRegex(QualificationError, "must be labelled"):
            build_qualification_report.build_evidence(
                plan,
                plan_binding,
                observations,
                obs_binding,
                fixture.root,
                utc(600),
            )

    def test_report_can_record_failed_acceptance(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        observations = fixture.observations(plan)
        for row in observations["measurements"]:
            if row["metric"] == "local_correlation":
                row["value"] = 2.0
        write_json(fixture.paths["observations"], observations)
        evidence = build_qualification_report.build_evidence(
            plan,
            file_binding(fixture.root, fixture.paths["plan"], "plan")[0],
            observations,
            file_binding(
                fixture.root, fixture.paths["observations"], "observations"
            )[0],
            fixture.root,
            utc(600),
        )
        self.assertEqual(evidence["overall_result"], "FAIL")

    def test_emitted_count_shortfall_is_a_failed_gate(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        observations = fixture.observations(plan)
        observations["integrity"]["emitted_count"] -= 1
        for row in observations["measurements"]:
            if row["metric"] == "emitted_count":
                row["value"] -= 1
        write_json(fixture.paths["observations"], observations)
        evidence = build_qualification_report.build_evidence(
            plan,
            file_binding(fixture.root, fixture.paths["plan"], "plan")[0],
            observations,
            file_binding(
                fixture.root, fixture.paths["observations"], "observations"
            )[0],
            fixture.root,
            utc(600),
        )
        self.assertEqual(evidence["overall_result"], "FAIL")
        gate = next(
            item
            for item in evidence["gate_results"]
            if item["gate"] == "integrity:emitted-count-vs-plan"
        )
        self.assertFalse(gate["passed"])

    def test_commanded_voltage_must_match_sequence(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        observations = fixture.observations(plan)
        observations["safety"]["maximum_commanded_output_volts_peak"] = 0.04
        write_json(fixture.paths["observations"], observations)
        with self.assertRaisesRegex(
            QualificationError, "differs from immutable sequence"
        ):
            build_qualification_report.build_evidence(
                plan,
                file_binding(fixture.root, fixture.paths["plan"], "plan")[0],
                observations,
                file_binding(
                    fixture.root, fixture.paths["observations"], "observations"
                )[0],
                fixture.root,
                utc(600),
            )

    def test_missing_safety_artifact_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        observations = fixture.observations(plan)
        observations["artifacts"] = [
            item
            for item in observations["artifacts"]
            if item["kind"] != "safety-readbacks"
        ]
        write_json(fixture.paths["observations"], observations)
        with self.assertRaisesRegex(QualificationError, "required evidence artifacts"):
            build_qualification_report.build_evidence(
                plan,
                file_binding(fixture.root, fixture.paths["plan"], "plan")[0],
                observations,
                file_binding(
                    fixture.root, fixture.paths["observations"], "observations"
                )[0],
                fixture.root,
                utc(600),
            )

    def test_counter_width_mismatch_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        observations = fixture.observations(plan)
        observations["counter_checks"]["rollover"]["counter_width_bits"] = 32
        write_json(fixture.paths["observations"], observations)
        with self.assertRaisesRegex(QualificationError, "counter width"):
            build_qualification_report.build_evidence(
                plan,
                file_binding(fixture.root, fixture.paths["plan"], "plan")[0],
                observations,
                file_binding(
                    fixture.root, fixture.paths["observations"], "observations"
                )[0],
                fixture.root,
                utc(600),
            )

    def test_output_overwrite_is_refused(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        fixture.paths["plan"].write_text("occupied", encoding="utf-8")
        code = generate_qualification_plan.main(
            [
                "--project-root",
                str(fixture.root),
                "--request",
                str(fixture.paths["request"]),
                "--generator-probe-report",
                str(fixture.paths["generator_probe"]),
                "--acquisition-probe-report",
                str(fixture.paths["acquisition_probe"]),
                "--generator-raw-probe",
                str(fixture.paths["generator_raw_probe"]),
                "--acquisition-raw-probe",
                str(fixture.paths["acquisition_raw_probe"]),
                "--generator-target-manifest",
                str(fixture.paths["generator_manifest"]),
                "--acquisition-target-manifest",
                str(fixture.paths["acquisition_manifest"]),
                "--generator-host-key-evidence",
                str(fixture.paths["generator_host_key"]),
                "--acquisition-host-key-evidence",
                str(fixture.paths["acquisition_host_key"]),
                "--generator-driver-evidence",
                str(fixture.paths["driver_evidence"]),
                "--generator-driver-contract",
                str(fixture.paths["driver_contract"]),
                "--acquisition-pre-state",
                str(fixture.paths["acquisition_state"]),
                "--authorization-bundle",
                str(fixture.paths["authorization_bundle"]),
                "--output",
                str(fixture.paths["plan"]),
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(fixture.paths["plan"].read_text(), "occupied")

    def test_fabricated_minimal_deployment_plan_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        fake = {
            "deployment_id": "fabricated",
            "plan_core_sha256": "0" * 64,
            "target": {
                "role": "generator",
                "identity": {"machine_id": fixture.generator_machine},
                "connection": {
                    "ssh_host_key_sha256": fixture.generator_fingerprint
                },
            },
            "gates": {
                "stage_authorized": True,
                "precommit_live_actions_authorized": True,
                "functional_output_test_authorized": True,
            },
            "gate_evidence": {},
        }
        write_json(fixture.root / "deploy" / "generator-plan.json", fake)
        with self.assertRaisesRegex(QualificationError, "deep validation"):
            fixture.build_plan()

    def test_raw_probe_substitution_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        raw = json.loads(fixture.paths["generator_raw_probe"].read_text())
        raw["identity"]["serial"] = "substituted-serial"
        write_json(fixture.paths["generator_raw_probe"], raw)
        with self.assertRaisesRegex(QualificationError, "fresh raw-probe analysis"):
            fixture.build_plan()

    def test_host_key_evidence_substitution_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        evidence = json.loads(fixture.paths["generator_host_key"].read_text())
        evidence["ssh_host_key_sha256"] = fingerprint(9)
        write_json(fixture.paths["generator_host_key"], evidence)
        with self.assertRaisesRegex(QualificationError, "host-key evidence.*mismatch"):
            fixture.build_plan()

    def test_calibration_substitution_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        calibration = json.loads(fixture.paths["local_path"].read_text())
        calibration["attenuation_db"] = 30.0
        write_json(fixture.paths["local_path"], calibration)
        with self.assertRaisesRegex(QualificationError, "calibration.*differs"):
            fixture.build_plan()

    def test_ambiguous_voltage_unit_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        request = json.loads(fixture.paths["request"].read_text())
        request["safety"]["voltage_unit"] = "Vpp"
        write_json(fixture.paths["request"], request)
        with self.assertRaisesRegex(QualificationError, "must equal 'Vpeak'"):
            fixture.build_plan()

    def test_empty_driver_register_map_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        contract = json.loads(fixture.paths["driver_contract"].read_text())
        contract["registers"] = []
        expectation = json.loads(fixture.paths["request"].read_text())[
            "driver_expectation"
        ]
        expectation["approved_contract_sha256"] = "a" * 64
        with self.assertRaisesRegex(
            QualificationError, "mandatory register roles|non-empty register map"
        ):
            generate_qualification_plan.validate_driver_contract(
                contract, "a" * 64, expectation
            )

    def test_expired_qualification_binding_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        binding = json.loads(fixture.paths["qualification_binding"].read_text())
        binding["expires_at_utc"] = utc(55)
        write_json(fixture.paths["qualification_binding"], binding)
        with self.assertRaisesRegex(QualificationError, "not fresh"):
            fixture.build_plan()

    def test_incomplete_acquisition_prestate_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        state = json.loads(fixture.paths["acquisition_state"].read_text())
        state["generator_input_state"] = {}
        write_json(fixture.paths["acquisition_state"], state)
        with self.assertRaisesRegex(QualificationError, "keys are invalid"):
            fixture.build_plan()

    def test_restore_readback_semantic_mismatch_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        observations = fixture.observations(plan)
        restore_item = next(
            item
            for item in observations["artifacts"]
            if item["kind"] == "acquisition-restore-readback"
        )
        restore_path = fixture.root / restore_item["file"]["path"]
        restore = json.loads(restore_path.read_text())
        restore["generator_input_state"]["decimation"] = 8
        write_json(restore_path, restore)
        restore_item["file"] = file_binding(
            fixture.root, restore_path, "mutated restore readback"
        )[0]
        write_json(fixture.paths["observations"], observations)
        with self.assertRaisesRegex(
            QualificationError, "restore readback generator_input_state mismatch"
        ):
            build_qualification_report.build_evidence(
                plan,
                file_binding(fixture.root, fixture.paths["plan"], "plan")[0],
                observations,
                file_binding(
                    fixture.root, fixture.paths["observations"], "observations"
                )[0],
                fixture.root,
                utc(600),
            )

    def test_single_use_nonce_mismatch_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        observations = fixture.observations(plan)
        observations["authorization_single_use_nonce"] = "replayed-nonce"
        write_json(fixture.paths["observations"], observations)
        with self.assertRaisesRegex(QualificationError, "single-use.*nonce"):
            build_qualification_report.build_evidence(
                plan,
                file_binding(fixture.root, fixture.paths["plan"], "plan")[0],
                observations,
                file_binding(
                    fixture.root, fixture.paths["observations"], "observations"
                )[0],
                fixture.root,
                utc(600),
            )

    def test_symlink_input_is_refused(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        with mock.patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(QualificationError, "symlink"):
                resolve_project_file(
                    fixture.root, fixture.paths["request"], "adversarial input"
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
