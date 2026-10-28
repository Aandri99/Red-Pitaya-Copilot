#!/usr/bin/env python3
"""Comprehensive offline tests for the single-board loopback skill."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import build_single_board_report
import generate_single_board_plan
import validate_single_board_plan
from single_board_common import (
    QualificationError,
    bind_file,
    sha256_file,
)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def utc(seconds: int) -> str:
    base = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_twin_fixture_class():
    scripts = (
        Path(__file__).resolve().parents[2]
        / "qualify-red-pitaya-loopback-system"
        / "scripts"
    )
    module_path = scripts / "test_qualification_tools.py"
    if not module_path.is_file():
        raise RuntimeError("sibling two-board test fixture is missing")
    scripts_text = str(scripts)
    if scripts_text not in sys.path:
        sys.path.insert(0, scripts_text)
    spec = importlib.util.spec_from_file_location(
        "_redpitaya_two_board_fixture", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load sibling two-board fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Fixture


TwinFixture = load_twin_fixture_class()


class Scratch:
    """Workspace-local scratch tree without Windows TemporaryDirectory ACLs."""

    def __init__(self) -> None:
        parent = Path(__file__).resolve().parent / ".test-runs"
        parent.mkdir(exist_ok=True)
        self.path = parent / f"run-{uuid.uuid4().hex}"
        self.path.mkdir()

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
        try:
            self.path.parent.rmdir()
        except OSError:
            pass


class Fixture:
    def __init__(self, root: Path, mode: str = "active") -> None:
        self.root = root
        self.mode = mode
        self.twin = TwinFixture(root, "active")
        self.machine = self.twin.generator_machine
        self.fingerprint = self.twin.generator_fingerprint
        self.paths = {
            "request": root / "inputs" / "single-request.json",
            "probe": self.twin.paths["generator_probe"],
            "raw_probe": self.twin.paths["generator_raw_probe"],
            "manifest": self.twin.paths["generator_manifest"],
            "host_key": self.twin.paths["generator_host_key"],
            "topology": root / "evidence" / "single-topology.json",
            "driver_evidence": self.twin.paths["driver_evidence"],
            "driver_contract": self.twin.paths["driver_contract"],
            "contract_review": self.twin.paths["contract_review"],
            "acquisition_state": root / "evidence" / "single-acquisition-state.json",
            "writer_inventory": root / "evidence" / "writer-inventory.json",
            "fpga_identity": root / "evidence" / "loaded-fpga-identity.json",
            "fpga_package": root / "fpga" / "qualified-package.json",
            "fpga_artifact": root / "fpga" / "system.bit",
            "fpga_release_receipt": root / "fpga" / "release-receipt.json",
            "fpga_release_review": root / "fpga" / "release-review.json",
            "fpga_timing_report": root / "fpga" / "reports" / "timing.rpt",
            "fpga_constraints_report": root
            / "fpga"
            / "reports"
            / "constraints.rpt",
            "fpga_drc_report": root / "fpga" / "reports" / "drc.rpt",
            "watchdog": root / "evidence" / "single-watchdog.json",
            "qualification_binding": root
            / "deploy"
            / "single-qualification-binding.json",
            "authorization_bundle": root
            / "evidence"
            / "single-authorization-bundle.json",
            "deployment_plan": root / "deploy" / "generator-plan.json",
            "plan": root / "plans" / "single-plan.json",
            "observations": root / "evidence" / "single-observations.json",
            "run_record": root / "evidence" / "run-record.json",
            "restore": root / "evidence" / "restore-readback.json",
            "raw_capture": root / "raw" / "in1-capture.csv",
            "result_json": root / "results" / "evidence.json",
            "result_csv": root / "results" / "criteria.csv",
            "result_md": root / "results" / "report.md",
        }
        for path in self.paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
        self._write_inputs()

    def request(self) -> dict:
        classification = "pdg1" if self.mode == "active" else "unknown-read-only"
        one_shot = (
            {
                "amplitude_volts_peak": 0.01,
                "pulse_width_seconds": 0.00001,
                "capture_window_seconds": 1.0,
                "polarity": "positive",
                "deterministic_seed": 12345,
                "expected_emitted_candidate_count": 1,
            }
            if self.mode == "active"
            else None
        )
        expectation = {
            "classification": classification,
            "expected_magic_ascii": "PDG1" if self.mode == "active" else None,
            "expected_abi_major": 1 if self.mode == "active" else None,
            "minimum_abi_minor": 1 if self.mode == "active" else None,
            "expected_driver_version": "fixture-1" if self.mode == "active" else None,
            "approved_contract_sha256": (
                sha256_file(self.paths["driver_contract"])
                if self.mode == "active"
                else None
            ),
            "required_capabilities": (
                ["event-counter", "safe-stop", "single-writer", "watchdog"]
                if self.mode == "active"
                else []
            ),
            "contract_review_path": (
                "contracts/driver-contract-review.json"
                if self.mode == "active"
                else None
            ),
        }
        return {
            "request_schema_version": 1,
            "qualification_id": "single-board-offline-001",
            "created_at_utc": utc(60),
            "mode": self.mode,
            "purpose": "Qualify one exact Red Pitaya with OUT1 physically connected to IN1.",
            "probe_max_age_seconds": 300,
            "topology": {
                "output_channel": "OUT1",
                "input_channel": "IN1",
                "connection": "Reviewed 50-ohm coax from OUT1 directly to IN1.",
                "physically_verified": True,
                "shared_reference_verified": True,
            },
            "driver_expectation": expectation,
            "acquisition": {
                "sample_rate_hz": 1_000_000.0,
                "record_samples": 4096,
                "pretrigger_samples": 1024,
                "trigger_mode": "threshold",
                "sample_units": "V",
                "baseline_duration_seconds": 2.0,
                "minimum_baseline_records": 2,
            },
            "safety": {
                "maximum_output_volts_peak": 0.1,
                "input_headroom_fraction": 0.8,
                "maximum_output_window_seconds": 5.0,
                "writer_id": "single-board-writer-001",
                "watchdog_timeout_seconds": 2.0,
                "emergency_stop_action": "Use reviewed generator safe-stop command.",
                "emergency_stop_verification": "Read output disabled and observe quiet IN1.",
                "restore_action": "Restore exact hash-bound IN1 pre-state.",
                "restore_verification": "Read every IN1 field back and compare exactly.",
            },
            "one_shot": one_shot,
            "acceptance": {
                "minimum_detection_fraction": 1.0,
                "maximum_detection_fraction": 1.0,
                "maximum_candidate_loss_fraction": 0.0,
                "maximum_measured_input_volts_peak": 0.8,
                "require_no_overrange": True,
            },
            "run_record_path": "evidence/run-record.json",
        }

    def _write_inputs(self) -> None:
        request = self.request()
        write_json(self.paths["request"], request)
        write_json(
            self.paths["topology"],
            {
                "evidence_schema_version": 1,
                "captured_at_utc": utc(30),
                "board_machine_id": self.machine,
                "ssh_host_key_sha256": self.fingerprint,
                "output_channel": "OUT1",
                "input_channel": "IN1",
                "physically_verified": True,
                "shared_reference_verified": True,
                "physical_output_isolated": self.mode == "read-only",
                "connection_description": "Reviewed direct OUT1-to-IN1 coax loopback.",
                "output_limit_volts_peak": 0.2,
                "input_range_volts_peak": 1.0,
                "attenuation_db": 3.0,
                "attenuation_uncertainty_db": 0.5,
                "coupling": "DC",
                "termination_ohms": 50.0,
                "calibration_reference": "fixture-calibration-001",
                "rating_reference": "fixture-rating-001",
                "verified_by": "offline-test-operator",
            },
        )
        twin_state = json.loads(self.twin.paths["acquisition_state"].read_text())
        write_json(
            self.paths["acquisition_state"],
            {
                "state_schema_version": 1,
                "captured_at_utc": utc(30),
                "capture_method": "Read-only acquisition status snapshot.",
                "board_machine_id": self.machine,
                "ssh_host_key_sha256": self.fingerprint,
                "input_state": {
                    **twin_state["generator_input_state"],
                    "channel": "IN1",
                },
                "restore_verification_method": "Exact field-for-field readback.",
            },
        )
        if self.mode == "read-only":
            driver = json.loads(self.paths["driver_evidence"].read_text())
            driver["contract"] = {
                "magic_ascii": "UNKN",
                "abi_major": 0,
                "abi_minor": 0,
                "register_contract_sha256": "0" * 64,
                "register_base": "0x0",
                "address_span": "0x4",
                "capabilities": [],
                "driver_version": "unknown",
            }
            write_json(self.paths["driver_evidence"], driver)
            return
        self.paths["fpga_package"].write_text(
            '{"package":"offline-qualified-fixture","version":1}\n',
            encoding="utf-8",
            newline="\n",
        )
        self.paths["fpga_artifact"].write_bytes(
            b"OFFLINE RED PITAYA FPGA FIXTURE ARTIFACT v1\n"
        )
        self.paths["fpga_timing_report"].write_text(
            "TIMING STATUS: PASSED\nWNS: 0.250 ns\n",
            encoding="utf-8",
            newline="\n",
        )
        self.paths["fpga_constraints_report"].write_text(
            "CONSTRAINT STATUS: PASSED\nAll clocks and IO constrained.\n",
            encoding="utf-8",
            newline="\n",
        )
        self.paths["fpga_drc_report"].write_text(
            "DRC STATUS: PASSED\nNo blocking violations.\n",
            encoding="utf-8",
            newline="\n",
        )
        build_id = sha256_file(self.paths["fpga_artifact"])
        contract_hash = sha256_file(self.paths["driver_contract"])
        write_json(
            self.paths["fpga_release_receipt"],
            {
                "release_receipt_schema_version": 1,
                "result": "qualified",
                "build_id": build_id,
                "package_sha256": sha256_file(self.paths["fpga_package"]),
                "artifact_sha256": sha256_file(self.paths["fpga_artifact"]),
                "register_contract_sha256": contract_hash,
                "created_at_utc": utc(-60),
                "issuer_identity": "offline-fixture-build-system",
                "receipt_reference": "fpga-release-receipt-001",
            },
        )
        write_json(
            self.paths["fpga_release_review"],
            {
                "fpga_release_review_schema_version": 1,
                "result": "approved",
                "build_id": build_id,
                "package_sha256": sha256_file(self.paths["fpga_package"]),
                "artifact_sha256": sha256_file(self.paths["fpga_artifact"]),
                "release_receipt_sha256": sha256_file(
                    self.paths["fpga_release_receipt"]
                ),
                "register_contract_sha256": contract_hash,
                "timing": {
                    "status": "passed",
                    "report_path": "fpga/reports/timing.rpt",
                    "report_sha256": sha256_file(self.paths["fpga_timing_report"]),
                    "summary": "Timing closure passed with positive slack.",
                },
                "constraints": {
                    "status": "passed",
                    "report_path": "fpga/reports/constraints.rpt",
                    "report_sha256": sha256_file(
                        self.paths["fpga_constraints_report"]
                    ),
                    "summary": "Clock and IO constraints are complete.",
                },
                "drc": {
                    "status": "passed",
                    "report_path": "fpga/reports/drc.rpt",
                    "report_sha256": sha256_file(self.paths["fpga_drc_report"]),
                    "summary": "No blocking design-rule violations.",
                },
                "independent_from_builder": True,
                "reviewed_at_utc": utc(-30),
                "reviewer_identity": "offline-fixture-release-reviewer",
                "review_reference": "fpga-release-review-001",
            },
        )
        write_json(
            self.paths["fpga_identity"],
            {
                "fpga_identity_evidence_schema_version": 1,
                "captured_at_utc": utc(45),
                "board_machine_id": self.machine,
                "ssh_host_key_sha256": self.fingerprint,
                "read_only_probe": True,
                "loaded_build_id": build_id,
                "build_identity_source": (
                    "Read-only hardware build-identity register snapshot."
                ),
                "qualified_package_path": "fpga/qualified-package.json",
                "qualified_package_sha256": sha256_file(
                    self.paths["fpga_package"]
                ),
                "artifact_path": "fpga/system.bit",
                "loaded_artifact_sha256": sha256_file(
                    self.paths["fpga_artifact"]
                ),
                "release_receipt_path": "fpga/release-receipt.json",
                "qualified_release_receipt_sha256": sha256_file(
                    self.paths["fpga_release_receipt"]
                ),
                "release_review_path": "fpga/release-review.json",
                "register_contract_sha256": contract_hash,
            },
        )
        writer_id = request["safety"]["writer_id"]
        non_writer = lambda identifier, state, evidence: {
            "identifier": identifier,
            "write_capable": False,
            "state": state,
            "control": "none",
            "owner": None,
            "evidence": evidence,
        }
        write_json(
            self.paths["writer_inventory"],
            {
                "writer_inventory_schema_version": 1,
                "captured_at_utc": utc(45),
                "board_machine_id": self.machine,
                "ssh_host_key_sha256": self.fingerprint,
                "read_only_inventory": True,
                "categories": {
                    "legacy_web_apis": {
                        "inspection_method": "Inspect installed web API handlers and routes.",
                        "observations": [
                            non_writer(
                                "legacy-web-api",
                                "absent",
                                "No generator write route is installed.",
                            )
                        ],
                    },
                    "lua_endpoints": {
                        "inspection_method": "Inspect Lua applications and endpoints.",
                        "observations": [
                            non_writer(
                                "lua-generator-endpoints",
                                "absent",
                                "No Lua generator endpoint is installed.",
                            )
                        ],
                    },
                    "root_helpers": {
                        "inspection_method": "Inspect privileged helper inventory.",
                        "observations": [
                            non_writer(
                                "root-generator-helpers",
                                "absent",
                                "No bypass-capable root helper was found.",
                            )
                        ],
                    },
                    "services": {
                        "inspection_method": "Inspect service units, processes and owners.",
                        "observations": [
                            {
                                "identifier": writer_id,
                                "write_capable": True,
                                "state": "ready-not-owner",
                                "control": "plan-lock-participant",
                                "owner": writer_id,
                                "evidence": (
                                    "Only the plan executor can acquire the generator lease."
                                ),
                            }
                        ],
                    },
                    "devmem_tools": {
                        "inspection_method": "Inspect /dev/mem tools and active command lines.",
                        "observations": [
                            non_writer(
                                "devmem-tools",
                                "absent",
                                "No active or scheduled /dev/mem writer was found.",
                            )
                        ],
                    },
                },
                "cooperative_lock_id": "single-board-generator-lock",
                "cooperative_lock_scope": (
                    "All reviewed generator register write paths on the exact board."
                ),
                "cooperative_lock_bypass_reviewed": True,
                "all_processes_reviewed": True,
                "bypass_capable_writers": [],
                "result": "exclusive",
                "reviewed_by": "offline-fixture-writer-reviewer",
                "review_reference": "writer-inventory-review-001",
            },
        )
        write_json(
            self.paths["watchdog"],
            {
                "watchdog_evidence_schema_version": 1,
                "captured_at_utc": utc(40),
                "board_machine_id": self.machine,
                "ssh_host_key_sha256": self.fingerprint,
                "watchdog_type": "hardware-backed",
                "independent_from_writer": True,
                "timeout_seconds": 1.0,
                "cutoff_method": "Hardware timeout clears output-enable without writer action.",
                "verification_result": "passed",
                "output_disabled_after_cutoff": True,
                "verification_reference": "fixture-watchdog-cutoff-001",
                "verified_by": "offline-test-reviewer",
            },
        )
        deployment = json.loads(self.paths["deployment_plan"].read_text())
        receipt = deployment["gate_evidence"]["functional_output_test_authorized"]
        write_json(
            self.paths["qualification_binding"],
            {
                "qualification_authorization_schema_version": 1,
                "source_skill": "deploy-red-pitaya-project",
                "result": "authorized",
                "qualification_id": request["qualification_id"],
                "request_sha256": sha256_file(self.paths["request"]),
                "deployment_id": deployment["deployment_id"],
                "deployment_plan_core_sha256": deployment["plan_core_sha256"],
                "functional_output_receipt_sha256": receipt["sha256"],
                "maximum_output_volts_peak": 0.1,
                "voltage_unit": "Vpeak",
                "single_use_nonce": "single-board-nonce-001",
                "recorded_at_utc": utc(70),
                "expires_at_utc": utc(1800),
                "approver_identity": "offline-test-approver",
                "authorization_reference": "single-board-auth-001",
            },
        )
        write_json(
            self.paths["authorization_bundle"],
            {
                "authorization_bundle_schema_version": 1,
                "source_skill": "deploy-red-pitaya-project",
                "deployment_plan": "deploy/generator-plan.json",
                "qualification_binding": "deploy/single-qualification-binding.json",
            },
        )

    def build_plan(self, *, write: bool = True) -> dict:
        plan = generate_single_board_plan.build_plan(
            self.root,
            self.paths["request"],
            self.paths["probe"],
            self.paths["raw_probe"],
            self.paths["manifest"],
            self.paths["host_key"],
            self.paths["topology"],
            self.paths["driver_evidence"],
            self.paths["driver_contract"],
            self.paths["acquisition_state"],
            self.paths["writer_inventory"] if self.mode == "active" else None,
            self.paths["fpga_identity"] if self.mode == "active" else None,
            self.paths["watchdog"] if self.mode == "active" else None,
            self.paths["authorization_bundle"] if self.mode == "active" else None,
        )
        if write:
            write_json(self.paths["plan"], plan)
        return plan

    def observations(self, plan: dict, *, detected: int = 1) -> dict:
        self.paths["raw_capture"].write_text(
            "sample,volts\n0,0.0000\n1,0.0100\n",
            encoding="utf-8",
            newline="\n",
        )
        run_binding = None
        restore_binding = None
        if self.mode == "active":
            write_json(
                self.paths["run_record"],
                {
                    "run_record_schema_version": 1,
                    "qualification_id": plan["qualification_id"],
                    "plan_sha256": plan["plan_sha256"],
                    "single_use_nonce": plan["execution_contract"]["single_use_nonce"],
                    "started_at_utc": utc(120),
                    "executor_id": "offline-executor",
                    "writer_id": plan["execution_contract"]["writer_id"],
                    "creation_method": "exclusive-create",
                },
            )
            write_json(
                self.paths["restore"],
                {
                    "restore_evidence_schema_version": 1,
                    "qualification_id": plan["qualification_id"],
                    "board_machine_id": self.machine,
                    "ssh_host_key_sha256": self.fingerprint,
                    "captured_at_utc": utc(140),
                    "input_state": plan["acquisition"]["pre_state"]["input_state"],
                    "comparison_method": "Exact semantic JSON field comparison.",
                },
            )
            run_binding = bind_file(self.root, self.paths["run_record"], "run")[0]
            restore_binding = bind_file(self.root, self.paths["restore"], "restore")[0]
        phases = []
        second = 120
        for phase in plan["phases"]:
            is_one = phase["phase_id"] == "low-amplitude-one-shot"
            phases.append(
                {
                    "phase_id": phase["phase_id"],
                    "started_at_utc": utc(second),
                    "ended_at_utc": utc(second + 1),
                    "result": "pass",
                    "output_enabled_observed": is_one,
                    "emitted_candidate_count": 1 if is_one else 0,
                    "detected_candidate_count": detected if is_one else 0,
                    "measured_peak_volts": 0.01 if is_one else 0.001,
                    "overrange": False,
                    "watchdog_cutoff_ready": is_one,
                    "writer_owner": (
                        plan["execution_contract"]["writer_id"] if is_one else None
                    ),
                    "loaded_fpga_build_id_observed": (
                        plan["fpga_release"]["loaded_build_id"]
                        if phase["phase_id"]
                        == "verify-loaded-fpga-and-writers"
                        else None
                    ),
                    "writer_inventory_result_observed": (
                        "exclusive"
                        if phase["phase_id"]
                        == "verify-loaded-fpga-and-writers"
                        else None
                    ),
                    "measurement_provenance": "measured",
                }
            )
            second += 2
        return {
            "observation_schema_version": 1,
            "qualification_id": plan["qualification_id"],
            "plan_sha256": plan["plan_sha256"],
            "mode": plan["mode"],
            "started_at_utc": utc(120),
            "completed_at_utc": utc(second),
            "board_machine_id": self.machine,
            "ssh_host_key_sha256": self.fingerprint,
            "phases": phases,
            "run_record": run_binding,
            "acquisition_restore_readback": restore_binding,
            "final_output_disabled": True,
            "final_output_verification": "Measured output-disable readback and quiet IN1.",
            "zero_dead_time_claimed": False,
            "raw_artifacts": [
                bind_file(self.root, self.paths["raw_capture"], "raw capture")[0]
            ],
            "notes": ["Synthetic offline fixture; no board was contacted."],
        }


class SingleBoardQualificationTests(unittest.TestCase):
    def fixture(self, mode: str = "active") -> tuple[Scratch, Fixture]:
        temporary = Scratch()
        fixture = Fixture(temporary.path, mode)
        return temporary, fixture

    def test_same_board_plan_is_deeply_valid(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        validate_single_board_plan.validate_plan(plan, fixture.root)
        self.assertTrue(plan["execution_contract"]["one_exact_board"])
        self.assertEqual(plan["topology"]["board_machine_id"], plan["board"]["identity"]["machine_id"])
        self.assertNotIn("boards", plan)

    def test_wrong_topology_identity_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        topology = json.loads(fixture.paths["topology"].read_text())
        topology["board_machine_id"] = "f" * 32
        write_json(fixture.paths["topology"], topology)
        with self.assertRaisesRegex(QualificationError, "topology board identity"):
            fixture.build_plan()

    def test_stale_authorization_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        auth = json.loads(fixture.paths["qualification_binding"].read_text())
        auth["recorded_at_utc"] = utc(10)
        auth["expires_at_utc"] = utc(20)
        write_json(fixture.paths["qualification_binding"], auth)
        with self.assertRaisesRegex(QualificationError, "stale|expired"):
            fixture.build_plan()

    def test_replayed_nonce_run_record_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        write_json(fixture.paths["run_record"], {"already": "consumed"})
        with self.assertRaisesRegex(QualificationError, "replayed"):
            fixture.build_plan()

    def test_vpeak_headroom_overflow_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        request = json.loads(fixture.paths["request"].read_text())
        request["one_shot"]["amplitude_volts_peak"] = 0.1
        request["acceptance"]["maximum_measured_input_volts_peak"] = 0.05
        write_json(fixture.paths["request"], request)
        auth = json.loads(fixture.paths["qualification_binding"].read_text())
        auth["request_sha256"] = sha256_file(fixture.paths["request"])
        write_json(fixture.paths["qualification_binding"], auth)
        with self.assertRaisesRegex(QualificationError, "Vpeak/headroom overflow"):
            fixture.build_plan()

    def test_missing_watchdog_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(QualificationError, "watchdog evidence"):
            generate_single_board_plan.build_plan(
                fixture.root,
                fixture.paths["request"],
                fixture.paths["probe"],
                fixture.paths["raw_probe"],
                fixture.paths["manifest"],
                fixture.paths["host_key"],
                fixture.paths["topology"],
                fixture.paths["driver_evidence"],
                fixture.paths["driver_contract"],
                fixture.paths["acquisition_state"],
                fixture.paths["writer_inventory"],
                fixture.paths["fpga_identity"],
                None,
                fixture.paths["authorization_bundle"],
            )

    def test_generator_contract_mismatch_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        evidence = json.loads(fixture.paths["driver_evidence"].read_text())
        evidence["contract"]["magic_ascii"] = "BAD1"
        write_json(fixture.paths["driver_evidence"], evidence)
        with self.assertRaisesRegex(QualificationError, "contract mismatch"):
            fixture.build_plan()

    def test_active_mode_requires_writer_inventory_and_fpga_identity(self) -> None:
        for omitted, expected in (
            ("writer", "writer inventory evidence"),
            ("fpga", "loaded FPGA identity evidence"),
        ):
            with self.subTest(omitted=omitted):
                temporary, fixture = self.fixture()
                self.addCleanup(temporary.cleanup)
                with self.assertRaisesRegex(QualificationError, expected):
                    generate_single_board_plan.build_plan(
                        fixture.root,
                        fixture.paths["request"],
                        fixture.paths["probe"],
                        fixture.paths["raw_probe"],
                        fixture.paths["manifest"],
                        fixture.paths["host_key"],
                        fixture.paths["topology"],
                        fixture.paths["driver_evidence"],
                        fixture.paths["driver_contract"],
                        fixture.paths["acquisition_state"],
                        (
                            None
                            if omitted == "writer"
                            else fixture.paths["writer_inventory"]
                        ),
                        (
                            None
                            if omitted == "fpga"
                            else fixture.paths["fpga_identity"]
                        ),
                        fixture.paths["watchdog"],
                        fixture.paths["authorization_bundle"],
                    )

    def test_loaded_build_id_mismatch_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        identity = json.loads(fixture.paths["fpga_identity"].read_text())
        identity["loaded_build_id"] = "b" * 64
        write_json(fixture.paths["fpga_identity"], identity)
        with self.assertRaisesRegex(QualificationError, "release receipt binding"):
            fixture.build_plan()

    def test_fpga_package_artifact_and_receipt_tampering_is_rejected(self) -> None:
        for target, expected in (
            ("fpga_package", "qualified FPGA package hash mismatch"),
            ("fpga_artifact", "loaded FPGA artifact hash mismatch"),
            ("fpga_release_receipt", "FPGA release receipt hash mismatch"),
        ):
            with self.subTest(target=target):
                temporary, fixture = self.fixture()
                self.addCleanup(temporary.cleanup)
                path = fixture.paths[target]
                path.write_bytes(path.read_bytes() + b"TAMPERED\n")
                with self.assertRaisesRegex(QualificationError, expected):
                    fixture.build_plan()

    def test_blocked_failed_or_incomplete_fpga_release_gate_is_rejected(self) -> None:
        for gate, status in (
            ("timing", "blocked"),
            ("constraints", "failed"),
            ("drc", "incomplete"),
        ):
            with self.subTest(gate=gate, status=status):
                temporary, fixture = self.fixture()
                self.addCleanup(temporary.cleanup)
                review = json.loads(
                    fixture.paths["fpga_release_review"].read_text()
                )
                review[gate]["status"] = status
                write_json(fixture.paths["fpga_release_review"], review)
                with self.assertRaisesRegex(
                    QualificationError,
                    rf"FPGA {gate} release status is blocked, failed or incomplete",
                ):
                    fixture.build_plan()

    def test_writer_inventory_must_cover_every_bypass_category(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        inventory = json.loads(fixture.paths["writer_inventory"].read_text())
        del inventory["categories"]["devmem_tools"]
        write_json(fixture.paths["writer_inventory"], inventory)
        with self.assertRaisesRegex(QualificationError, "categories.*keys"):
            fixture.build_plan()

    def test_bypass_capable_writer_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        inventory = json.loads(fixture.paths["writer_inventory"].read_text())
        item = inventory["categories"]["legacy_web_apis"]["observations"][0]
        item.update(
            {
                "write_capable": True,
                "state": "running",
                "control": "bypass",
                "evidence": "Adversarial legacy API can write registers directly.",
            }
        )
        write_json(fixture.paths["writer_inventory"], inventory)
        with self.assertRaisesRegex(QualificationError, "bypass-capable"):
            fixture.build_plan()

    def test_writer_inventory_requires_one_exact_plan_lock_participant(self) -> None:
        for variant, expected in (
            ("none", "exactly one plan-lock participant"),
            ("wrong", "does not match immutable writer_id"),
            ("duplicate", "duplicate writer inventory identifier"),
        ):
            with self.subTest(variant=variant):
                temporary, fixture = self.fixture()
                self.addCleanup(temporary.cleanup)
                inventory = json.loads(fixture.paths["writer_inventory"].read_text())
                observations = inventory["categories"]["services"]["observations"]
                if variant == "none":
                    observations.clear()
                elif variant == "wrong":
                    observations[0]["identifier"] = "unexpected-writer"
                    observations[0]["owner"] = "unexpected-writer"
                else:
                    observations.append(dict(observations[0]))
                write_json(fixture.paths["writer_inventory"], inventory)
                with self.assertRaisesRegex(QualificationError, expected):
                    fixture.build_plan()

    def test_loaded_fpga_identity_drift_before_output_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        observations = fixture.observations(plan)
        verify_phase = next(
            phase
            for phase in observations["phases"]
            if phase["phase_id"] == "verify-loaded-fpga-and-writers"
        )
        verify_phase["loaded_fpga_build_id_observed"] = "c" * 64
        with self.assertRaisesRegex(QualificationError, "identity drifted"):
            build_single_board_report.evaluate(fixture.root, plan, observations)

    def test_nonexclusive_writer_reread_before_output_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        observations = fixture.observations(plan)
        verify_phase = next(
            phase
            for phase in observations["phases"]
            if phase["phase_id"] == "verify-loaded-fpga-and-writers"
        )
        verify_phase["writer_inventory_result_observed"] = "bypass-found"
        with self.assertRaisesRegex(QualificationError, "not exclusive"):
            build_single_board_report.evaluate(fixture.root, plan, observations)

    def test_output_before_baseline_is_rejected(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        observations = fixture.observations(plan)
        observations["phases"][0]["output_enabled_observed"] = True
        with self.assertRaisesRegex(QualificationError, "outside the plan-bound"):
            build_single_board_report.evaluate(fixture.root, plan, observations)

    def test_restore_mismatch_yields_fail(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        observations = fixture.observations(plan)
        restore = json.loads(fixture.paths["restore"].read_text())
        restore["input_state"]["decimation"] = 8
        write_json(fixture.paths["restore"], restore)
        observations["acquisition_restore_readback"] = bind_file(
            fixture.root, fixture.paths["restore"], "restore"
        )[0]
        evidence = build_single_board_report.evaluate(
            fixture.root, plan, observations
        )
        self.assertEqual(evidence["status"], "FAIL")
        restore_criterion = next(
            item
            for item in evidence["criteria"]
            if item["criterion"] == "acquisition_state_restored_exactly"
        )
        self.assertFalse(restore_criterion["passed"])

    def test_pass_and_fail_reports_are_written(self) -> None:
        for detected, expected in ((1, "PASS"), (0, "FAIL")):
            with self.subTest(expected=expected):
                temporary, fixture = self.fixture()
                self.addCleanup(temporary.cleanup)
                plan = fixture.build_plan()
                write_json(fixture.paths["observations"], fixture.observations(plan, detected=detected))
                status = build_single_board_report.main(
                    [
                        "--project-root",
                        str(fixture.root),
                        "--plan",
                        str(fixture.paths["plan"]),
                        "--observations",
                        str(fixture.paths["observations"]),
                        "--evidence-json",
                        str(fixture.paths["result_json"]),
                        "--measurements-csv",
                        str(fixture.paths["result_csv"]),
                        "--report-md",
                        str(fixture.paths["result_md"]),
                    ]
                )
                self.assertEqual(status, 0)
                evidence = json.loads(fixture.paths["result_json"].read_text())
                self.assertEqual(evidence["status"], expected)
                self.assertIn(f"**Result: {expected}**", fixture.paths["result_md"].read_text())
                self.assertIn("criterion,passed", fixture.paths["result_csv"].read_text())

    def test_tampered_input_invalidates_plan(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        topology = json.loads(fixture.paths["topology"].read_text())
        topology["calibration_reference"] = "tampered"
        write_json(fixture.paths["topology"], topology)
        with self.assertRaisesRegex(QualificationError, "hash no longer matches"):
            validate_single_board_plan.validate_plan(plan, fixture.root)

    def test_read_only_unknown_driver_has_no_output_path(self) -> None:
        temporary, fixture = self.fixture("read-only")
        self.addCleanup(temporary.cleanup)
        plan = fixture.build_plan()
        validate_single_board_plan.validate_plan(plan, fixture.root)
        self.assertTrue(all(not phase["output_allowed"] for phase in plan["phases"]))
        self.assertEqual(plan["driver_gate"]["write_access"], "forbidden")
        self.assertIsNone(plan["authorization"])

    def test_overwrite_is_refused(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        fixture.paths["plan"].write_text("occupied", encoding="utf-8")
        result = generate_single_board_plan.main(
            [
                "--project-root",
                str(fixture.root),
                "--request",
                str(fixture.paths["request"]),
                "--probe-report",
                str(fixture.paths["probe"]),
                "--raw-probe",
                str(fixture.paths["raw_probe"]),
                "--target-manifest",
                str(fixture.paths["manifest"]),
                "--host-key-evidence",
                str(fixture.paths["host_key"]),
                "--topology-evidence",
                str(fixture.paths["topology"]),
                "--driver-evidence",
                str(fixture.paths["driver_evidence"]),
                "--driver-contract",
                str(fixture.paths["driver_contract"]),
                "--acquisition-pre-state",
                str(fixture.paths["acquisition_state"]),
                "--writer-inventory-evidence",
                str(fixture.paths["writer_inventory"]),
                "--fpga-identity-evidence",
                str(fixture.paths["fpga_identity"]),
                "--watchdog-evidence",
                str(fixture.paths["watchdog"]),
                "--authorization-bundle",
                str(fixture.paths["authorization_bundle"]),
                "--output",
                str(fixture.paths["plan"]),
            ]
        )
        self.assertEqual(result, 2)
        self.assertEqual(fixture.paths["plan"].read_text(), "occupied")

    def test_symlink_input_is_refused(self) -> None:
        temporary, fixture = self.fixture()
        self.addCleanup(temporary.cleanup)
        link = fixture.root / "evidence" / "topology-link.json"
        original = Path.is_symlink

        def fake_is_symlink(path: Path) -> bool:
            return path == link or original(path)

        with mock.patch.object(Path, "is_symlink", fake_is_symlink):
            with self.assertRaisesRegex(QualificationError, "symlink"):
                generate_single_board_plan.build_plan(
                    fixture.root,
                    fixture.paths["request"],
                    fixture.paths["probe"],
                    fixture.paths["raw_probe"],
                    fixture.paths["manifest"],
                    fixture.paths["host_key"],
                    link,
                    fixture.paths["driver_evidence"],
                    fixture.paths["driver_contract"],
                    fixture.paths["acquisition_state"],
                    fixture.paths["writer_inventory"],
                    fixture.paths["fpga_identity"],
                    fixture.paths["watchdog"],
                    fixture.paths["authorization_bundle"],
                )

    def test_scripts_have_no_live_network_or_process_calls(self) -> None:
        script_dir = Path(__file__).resolve().parent
        forbidden = (
            "import socket",
            "import requests",
            "import urllib",
            "import subprocess",
            "from socket",
            "from requests",
            "from urllib",
            "from subprocess",
        )
        for path in (
            script_dir / "generate_single_board_plan.py",
            script_dir / "validate_single_board_plan.py",
            script_dir / "build_single_board_report.py",
        ):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"{path.name} contains {token}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
