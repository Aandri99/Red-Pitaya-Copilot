#!/usr/bin/env python3
"""Local self-tests for the offline Red Pitaya deployment tools."""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

import generate_deployment_plan as plan_generator
import project_contract


SCRIPTS = Path(__file__).resolve().parent
ANALYZE = SCRIPTS / "analyze_probe.py"
GENERATE = SCRIPTS / "generate_deployment_plan.py"
VALIDATE = SCRIPTS / "validate_deployment_plan.py"
RECORD_GATE = SCRIPTS / "record_deployment_gate.py"
PLUGIN_ROOT = SCRIPTS.parents[2]
FPGA_SCRIPTS = (
    PLUGIN_ROOT / "skills" / "build-red-pitaya-fpga" / "scripts"
)
FPGA_EVIDENCE = FPGA_SCRIPTS / "fpga_build_evidence.py"
FPGA_PACKAGE = FPGA_SCRIPTS / "fpga_package.py"
APP_GENERATOR = (
    PLUGIN_ROOT
    / "skills"
    / "build-red-pitaya-app"
    / "scripts"
    / "new_red_pitaya_app.py"
)
REGISTER_CONTRACT = (
    PLUGIN_ROOT
    / "skills"
    / "build-red-pitaya-fpga"
    / "assets"
    / "register-contract.example.json"
)


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_structural_test_bitstream(path: Path, size: int) -> None:
    prefix = b"".join(
        (
            bytes.fromhex("ffffffff"),
            bytes.fromhex("aa995566"),
            struct.pack(">I", 0x20000000),
            struct.pack(">I", 0x30008001),
            struct.pack(">I", 0x00000007),
        )
    )
    path.write_bytes(prefix + bytes(size - len(prefix)))


def write_structural_test_dtbo(path: Path) -> None:
    strings = b"compatible\x00"
    structure = b"".join(
        (
            struct.pack(">I", 1),
            bytes(4),
            struct.pack(">III", 3, 4, 0),
            b"test",
            struct.pack(">I", 2),
            struct.pack(">I", 9),
        )
    )
    reserve_map = bytes(16)
    off_mem_rsvmap = 40
    off_struct = off_mem_rsvmap + len(reserve_map)
    off_strings = off_struct + len(structure)
    total_size = off_strings + len(strings)
    header = struct.pack(
        ">10I",
        0xD00DFEED,
        total_size,
        off_struct,
        off_strings,
        off_mem_rsvmap,
        17,
        16,
        0,
        len(strings),
        len(structure),
    )
    path.write_bytes(header + reserve_map + structure + strings)


def run(*args: object, expect: int = 0) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, *(str(item) for item in args)],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"expected exit {expect}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


class DeploymentToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = (
            Path.cwd() / f".redpitaya-deploy-tools-{uuid.uuid4().hex}"
        ).resolve()
        self.root.mkdir()
        if (
            self.root.parent != Path.cwd().resolve()
            or not self.root.name.startswith(".redpitaya-deploy-tools-")
        ):
            raise RuntimeError("refusing unsafe self-test directory")
        self.manifest = self.root / "redpitaya-project.json"
        self.probe = self.root / "probe.json"
        self.report = self.root / "probe-report.json"
        self.file_map = self.root / "deploy-files.json"
        self.plan = self.root / "deployment-plan.json"
        write_json(
            self.manifest,
            {
                "schema_version": 1,
                "project": {
                    "slug": "test_app",
                    "title": "Test App",
                    "version": "1.0.0",
                    "description": "Custom Red Pitaya instrument application",
                },
                "target": {
                    "os_release": "3.00-57",
                    "model": "z20_125",
                    "role": "acquisition",
                    "hostname": "rp-test.local",
                    "expected_serial_or_mac_suffix": "0001",
                },
                "application": {
                    "enabled": True,
                    "architecture": "static",
                    "authentication": "none",
                    "slug": "test_app",
                    "routes": ["/test_app/"],
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
                    "backup_root": "/root/redpitaya-backups/test_app/{deployment_id}",
                    "nginx_service": "redpitaya_nginx.service",
                    "restore_read_only": True,
                    "allow_live_output_test": False,
                },
                "safety": {
                    "output_default": "disabled",
                    "stop_action": "Use the app Stop action and verify output enable is false.",
                    "physical_test_requires_authorization": True,
                    "rollback_required": True,
                    "maximum_output_volts": None,
                },
            },
        )
        run(
            APP_GENERATOR,
            "--slug",
            "test_app",
            "--title",
            "Test App",
            "--version",
            "1.0.0",
            "--os-release",
            "3.00-57",
            "--model",
            "z20_125",
            "--role",
            "acquisition",
            "--hostname",
            "rp-test.local",
            "--backend",
            "static",
            "--output",
            self.root,
        )
        write_json(
            self.root / "test_app" / "redpitaya-project.json",
            json.loads(self.manifest.read_text(encoding="utf-8")),
        )
        write_json(
            self.probe,
            {
                "probe_schema_version": 1,
                "captured_at": "2026-07-29T12:00:00Z",
                "probe_source": "offline-test-fixture",
                "connection": {
                    "host": "rp-test.local",
                    "address": "192.0.2.10",
                    "username": "root",
                    "host_key_verified": True,
                    "ssh_host_key_sha256": (
                        "SHA256:AAECAwQFBgcICQoLDA0ODxAREhMUFRYX"
                        "GBkaGxwdHh8="
                    ),
                },
                "identity": {
                    "hostname": "rp-test",
                    "machine_id": "11111111111111111111111111111111",
                    "serial": "TEST-Z20-001",
                    "mac_addresses": ["02:00:00:00:00:01"],
                    "roles": ["acquisition"],
                },
                "os": {"release": "3.00-57"},
                "board": {
                    "model": "STEMlab 125-14 Z20",
                    "model_code": "z20_125",
                    "profiles_model_code": "z20_125",
                    "monitor_model_name": "STEMlab 125-14 Z20",
                    "monitor_model_id": "3",
                },
                "storage": {
                    "opt_redpitaya": {
                        "target": "/opt/redpitaya",
                        "source": "/dev/mmcblk0p3",
                        "fstype": "vfat",
                        "options": ["ro", "relatime"],
                    }
                },
                "fpga": {
                    "expected_artifact": "fpga.bin",
                    "loader_script": "/opt/redpitaya/sbin/overlay.sh",
                    "monitor_fpga_version": "0.94",
                },
            },
        )
        write_json(
            self.file_map,
            {
                "schema_version": 1,
                "files": [
                    {
                        "source": "test_app",
                        "destination": "/opt/redpitaya/www/apps/test_app",
                        "component": "app",
                        "mode": "0644",
                    }
                ],
            },
        )

    def test_shared_schema_enforces_numeric_ceiling_and_finite_values(self) -> None:
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["application"]["architecture"] = "service"
        manifest["application"]["service_name"] = "test_app.service"
        manifest["application"]["python_abi"] = "3.12"
        manifest["application"]["service_port"] = 65536
        with self.assertRaisesRegex(project_contract.ContractError, "must be <= 65535"):
            project_contract.validate_manifest_object(manifest)

        manifest["application"]["service_port"] = 9176
        manifest["safety"]["maximum_output_volts"] = float("nan")
        with self.assertRaisesRegex(project_contract.ContractError, "expected type"):
            project_contract.validate_manifest_object(manifest)

    def tearDown(self) -> None:
        if (
            self.root.parent == Path.cwd().resolve()
            and self.root.name.startswith(".redpitaya-deploy-tools-")
        ):
            shutil.rmtree(self.root)

    def make_valid_plan(self) -> dict:
        run(
            ANALYZE,
            self.probe,
            "--manifest",
            self.manifest,
            "--expected-role",
            "acquisition",
            "--output",
            self.report,
        )
        run(
            GENERATE,
            "--manifest",
            self.manifest,
            "--probe-report",
            self.report,
            "--project-root",
            self.root,
            "--file-map",
            self.file_map,
            "--deployment-id",
            "test-20260729T120000Z",
            "--output",
            self.plan,
        )
        return json.loads(self.plan.read_text(encoding="utf-8"))

    def transition_gate(
        self,
        input_plan: Path,
        gate: str,
        sequence: int,
        authorization_scope: str | None = None,
        authorization_reference: str | None = None,
    ) -> Path:
        timestamp = f"2026-07-29T12:{10 + sequence:02d}:00Z"
        summary = f"Offline test evidence for {gate}."
        evidence = self.root / f"evidence-{sequence:02d}-{gate}.json"
        if authorization_scope is None:
            evidence.write_text(
                summary + "\n",
                encoding="utf-8",
            )
        else:
            plan = json.loads(input_plan.read_text(encoding="utf-8"))
            write_json(
                evidence,
                {
                    "authorization_schema_version": 2,
                    "gate": gate,
                    "result": "authorized",
                    "recorded_at_utc": timestamp,
                    "authorization_scope": authorization_scope,
                    "deployment_id": plan["deployment_id"],
                    "plan_core_sha256": plan["plan_core_sha256"],
                    "target_machine_id": plan["target"]["identity"]["machine_id"],
                    "ssh_host_key_sha256": plan["target"]["connection"][
                        "ssh_host_key_sha256"
                    ],
                    "approver_identity": "offline-test-operator",
                    "authorization_reference": (
                        authorization_reference or f"offline-auth-{sequence:02d}"
                    ),
                    "summary": summary,
                },
            )
        output = self.root / f"deployment-plan-{sequence:02d}-{gate}.json"
        run(
            RECORD_GATE,
            "--plan",
            input_plan,
            "--project-root",
            self.root,
            "--gate",
            gate,
            "--evidence-file",
            evidence,
            "--recorded-at-utc",
            timestamp,
            "--summary",
            summary,
            "--output",
            output,
        )
        return output

    def make_commit_ready_plan(self) -> Path:
        self.make_valid_plan()
        current = self.plan
        sequence = [
            ("dry_run_reviewed", None),
            ("stage_authorized", "stage"),
            ("stage_hashes_verified", None),
            (
                "precommit_live_actions_authorized",
                "precommit-live-actions",
            ),
            ("safe_stop_confirmed", None),
            ("backup_verified", None),
            ("commit_authorized", "commit"),
        ]
        for index, (gate, scope) in enumerate(sequence, start=1):
            current = self.transition_gate(current, gate, index, scope)
        return current

    def test_end_to_end_offline_plan(self) -> None:
        plan = self.make_valid_plan()
        self.assertEqual(plan["target"]["expected_fpga_artifact"], "fpga.bin")
        self.assertGreater(plan["scope"]["file_count"], 5)
        self.assertEqual(
            plan["application_provenance"]["destination_root"],
            "/opt/redpitaya/www/apps/test_app",
        )
        result = run(VALIDATE, self.plan, "--project-root", self.root)
        self.assertIn("structurally valid", result.stdout)

    def test_os_mismatch_is_blocking(self) -> None:
        probe = json.loads(self.probe.read_text(encoding="utf-8"))
        probe["os"]["release"] = "2.07-48"
        write_json(self.probe, probe)
        result = run(
            ANALYZE,
            self.probe,
            "--manifest",
            self.manifest,
            "--output",
            self.report,
            expect=2,
        )
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertFalse(report["compatible"])
        self.assertTrue(any("OS mismatch" in item for item in report["blocking_errors"]))
        self.assertEqual(result.returncode, 2)

    def test_wrong_fpga_name_is_rejected(self) -> None:
        (self.root / "fpga-wrong.bin").write_bytes(b"fake")
        write_json(
            self.file_map,
            {
                "schema_version": 1,
                "files": [
                    {
                        "source": "fpga-wrong.bin",
                        "destination": "/opt/redpitaya/fpga/fpga.bit.bin",
                        "component": "fpga",
                        "mode": "0644",
                    }
                ],
            },
        )
        run(
            ANALYZE,
            self.probe,
            "--manifest",
            self.manifest,
            "--expected-role",
            "acquisition",
            "--output",
            self.report,
        )
        result = run(
            GENERATE,
            "--manifest",
            self.manifest,
            "--probe-report",
            self.report,
            "--project-root",
            self.root,
            "--file-map",
            self.file_map,
            "--deployment-id",
            "bad-fpga",
            "--output",
            self.plan,
            expect=2,
        )
        self.assertIn("must be installed as 'fpga.bin'", result.stderr)

    def test_secret_like_source_is_rejected(self) -> None:
        (self.root / "password.txt").write_text("not-even-a-real-secret\n", encoding="utf-8")
        write_json(
            self.file_map,
            {
                "schema_version": 1,
                "files": [
                    {
                        "source": "password.txt",
                        "destination": "/etc/redpitaya/password.txt",
                        "component": "configuration",
                        "mode": "0600",
                    }
                ],
            },
        )
        run(
            ANALYZE,
            self.probe,
            "--manifest",
            self.manifest,
            "--output",
            self.report,
        )
        result = run(
            GENERATE,
            "--manifest",
            self.manifest,
            "--probe-report",
            self.report,
            "--project-root",
            self.root,
            "--file-map",
            self.file_map,
            "--deployment-id",
            "bad-secret",
            "--output",
            self.plan,
            expect=2,
        )
        self.assertIn("secret-like", result.stderr)

    def test_commit_gates_are_separate(self) -> None:
        plan = self.make_valid_plan()
        write_json(self.plan, plan)
        result = run(
            VALIDATE,
            self.plan,
            "--project-root",
            self.root,
            "--require-commit-gates",
            expect=2,
        )
        self.assertIn("commit gate", result.stderr)

    def test_manual_gate_flip_without_evidence_is_rejected(self) -> None:
        plan = self.make_valid_plan()
        plan["gates"]["dry_run_reviewed"] = True
        write_json(self.plan, plan)
        result = run(
            VALIDATE,
            self.plan,
            "--project-root",
            self.root,
            expect=2,
        )
        self.assertIn(
            "gate_evidence.dry_run_reviewed is required",
            result.stderr,
        )

    def test_gate_transition_hash_binds_evidence(self) -> None:
        self.make_valid_plan()
        evidence = self.root / "dry-run-review.txt"
        evidence.write_text(
            "Operator reviewed the complete target, file list, gates and rollback.\n",
            encoding="utf-8",
        )
        reviewed_plan = self.root / "deployment-plan-reviewed.json"
        run(
            RECORD_GATE,
            "--plan",
            self.plan,
            "--project-root",
            self.root,
            "--gate",
            "dry_run_reviewed",
            "--evidence-file",
            evidence,
            "--recorded-at-utc",
            "2026-07-29T12:10:00Z",
            "--summary",
            "Dry-run plan and rollback reviewed.",
            "--output",
            reviewed_plan,
        )
        run(VALIDATE, reviewed_plan, "--project-root", self.root)
        plan = json.loads(reviewed_plan.read_text(encoding="utf-8"))
        self.assertTrue(plan["gates"]["dry_run_reviewed"])
        self.assertEqual(
            plan["gate_evidence"]["dry_run_reviewed"]["path"],
            "dry-run-review.txt",
        )

    def test_probe_semantic_tamper_is_rejected_even_with_new_core_hash(self) -> None:
        plan = self.make_valid_plan()
        plan["target"]["connection"]["address"] = "192.0.2.99"
        plan["plan_core_sha256"] = plan_generator.plan_core_sha256(plan)
        write_json(self.plan, plan)
        result = run(
            VALIDATE,
            self.plan,
            "--project-root",
            self.root,
            expect=2,
        )
        self.assertIn(
            "target.connection differs from normalized probe",
            result.stderr,
        )

    def test_application_provenance_tamper_is_rejected(self) -> None:
        plan = self.make_valid_plan()
        plan["application_provenance"]["validation_receipt"][
            "warning_count"
        ] = 1
        plan["plan_core_sha256"] = plan_generator.plan_core_sha256(plan)
        write_json(self.plan, plan)
        result = run(
            VALIDATE,
            self.plan,
            "--project-root",
            self.root,
            expect=2,
        )
        self.assertIn(
            "application_provenance differs from a fresh",
            result.stderr,
        )

    def test_destination_with_shell_metacharacter_is_rejected(self) -> None:
        file_map = json.loads(self.file_map.read_text(encoding="utf-8"))
        file_map["files"][0]["destination"] = (
            "/opt/redpitaya/www/apps/test_app;touch_bad"
        )
        write_json(self.file_map, file_map)
        run(
            ANALYZE,
            self.probe,
            "--manifest",
            self.manifest,
            "--output",
            self.report,
        )
        result = run(
            GENERATE,
            "--manifest",
            self.manifest,
            "--probe-report",
            self.report,
            "--project-root",
            self.root,
            "--file-map",
            self.file_map,
            "--deployment-id",
            "bad-destination",
            "--output",
            self.plan,
            expect=2,
        )
        self.assertIn("shell metacharacters", result.stderr)

    def test_protected_loader_destination_is_rejected(self) -> None:
        (self.root / "overlay.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        file_map = json.loads(self.file_map.read_text(encoding="utf-8"))
        file_map["files"].append(
            {
                "source": "overlay.sh",
                "destination": "/opt/redpitaya/sbin/overlay.sh",
                "component": "other",
                "mode": "0755",
            }
        )
        write_json(self.file_map, file_map)
        run(
            ANALYZE,
            self.probe,
            "--manifest",
            self.manifest,
            "--output",
            self.report,
        )
        result = run(
            GENERATE,
            "--manifest",
            self.manifest,
            "--probe-report",
            self.report,
            "--project-root",
            self.root,
            "--file-map",
            self.file_map,
            "--deployment-id",
            "protected-loader",
            "--output",
            self.plan,
            expect=2,
        )
        self.assertIn("protected", result.stderr)

    def test_other_component_is_rejected_for_committable_plan(self) -> None:
        (self.root / "misc.txt").write_text("misc\n", encoding="utf-8")
        file_map = json.loads(self.file_map.read_text(encoding="utf-8"))
        file_map["files"].append(
            {
                "source": "misc.txt",
                "destination": "/usr/local/share/test_app/misc.txt",
                "component": "other",
                "mode": "0644",
            }
        )
        write_json(self.file_map, file_map)
        self.make_valid_plan()
        result = run(
            VALIDATE,
            self.plan,
            "--project-root",
            self.root,
            "--require-commit-gates",
            expect=2,
        )
        self.assertIn(
            "component 'other' is not permitted in a committable",
            result.stderr,
        )

    def test_enabled_application_requires_exact_bundle_mapping(self) -> None:
        file_map = json.loads(self.file_map.read_text(encoding="utf-8"))
        file_map["files"][0]["component"] = "configuration"
        write_json(self.file_map, file_map)
        run(
            ANALYZE,
            self.probe,
            "--manifest",
            self.manifest,
            "--output",
            self.report,
        )
        result = run(
            GENERATE,
            "--manifest",
            self.manifest,
            "--probe-report",
            self.report,
            "--project-root",
            self.root,
            "--file-map",
            self.file_map,
            "--deployment-id",
            "missing-app-bundle",
            "--output",
            self.plan,
            expect=2,
        )
        self.assertIn("exactly one component=app bundle", result.stderr)

    def test_application_bundle_manifest_must_equal_deployment_manifest(self) -> None:
        bundle_manifest = self.root / "test_app" / "redpitaya-project.json"
        value = json.loads(bundle_manifest.read_text(encoding="utf-8"))
        value["project"]["version"] = "9.9.9"
        write_json(bundle_manifest, value)
        run(
            ANALYZE,
            self.probe,
            "--manifest",
            self.manifest,
            "--output",
            self.report,
        )
        result = run(
            GENERATE,
            "--manifest",
            self.manifest,
            "--probe-report",
            self.report,
            "--project-root",
            self.root,
            "--file-map",
            self.file_map,
            "--deployment-id",
            "mismatched-app-manifest",
            "--output",
            self.plan,
            expect=2,
        )
        self.assertIn("differs from the deployment manifest", result.stderr)

    def test_stage_and_precommit_authorization_order_is_enforced(self) -> None:
        self.make_valid_plan()
        evidence = self.root / "too-early-stage-hash.txt"
        evidence.write_text("stage hashes\n", encoding="utf-8")
        result = run(
            RECORD_GATE,
            "--plan",
            self.plan,
            "--project-root",
            self.root,
            "--gate",
            "stage_hashes_verified",
            "--evidence-file",
            evidence,
            "--recorded-at-utc",
            "2026-07-29T12:10:00Z",
            "--summary",
            "Stage hash evidence.",
            "--output",
            self.root / "invalid-order.json",
            expect=2,
        )
        self.assertIn("stage_authorized", result.stderr)

        reviewed = self.transition_gate(
            self.plan,
            "dry_run_reviewed",
            1,
        )
        stage_authorized = self.transition_gate(
            reviewed,
            "stage_authorized",
            2,
            "stage",
        )
        staged = self.transition_gate(
            stage_authorized,
            "stage_hashes_verified",
            3,
        )
        result = run(
            RECORD_GATE,
            "--plan",
            staged,
            "--project-root",
            self.root,
            "--gate",
            "safe_stop_confirmed",
            "--evidence-file",
            evidence,
            "--recorded-at-utc",
            "2026-07-29T12:14:00Z",
            "--summary",
            "Safe stop evidence.",
            "--output",
            self.root / "invalid-precommit-order.json",
            expect=2,
        )
        self.assertIn("precommit_live_actions_authorized", result.stderr)

    def test_authorization_is_bound_to_plan_and_cannot_be_reused(self) -> None:
        self.make_valid_plan()
        reviewed = self.transition_gate(
            self.plan,
            "dry_run_reviewed",
            1,
        )
        stage_authorized = self.transition_gate(
            reviewed,
            "stage_authorized",
            2,
            "stage",
            "shared-reference-must-not-reuse",
        )
        staged = self.transition_gate(
            stage_authorized,
            "stage_hashes_verified",
            3,
        )
        plan = json.loads(staged.read_text(encoding="utf-8"))
        evidence = self.root / "reused-reference.json"
        timestamp = "2026-07-29T12:14:00Z"
        summary = "Authorize precommit live actions."
        write_json(
            evidence,
            {
                "authorization_schema_version": 2,
                "gate": "precommit_live_actions_authorized",
                "result": "authorized",
                "recorded_at_utc": timestamp,
                "authorization_scope": "precommit-live-actions",
                "deployment_id": plan["deployment_id"],
                "plan_core_sha256": plan["plan_core_sha256"],
                "target_machine_id": plan["target"]["identity"]["machine_id"],
                "ssh_host_key_sha256": plan["target"]["connection"][
                    "ssh_host_key_sha256"
                ],
                "approver_identity": "offline-test-operator",
                "authorization_reference": "shared-reference-must-not-reuse",
                "summary": summary,
            },
        )
        result = run(
            RECORD_GATE,
            "--plan",
            staged,
            "--project-root",
            self.root,
            "--gate",
            "precommit_live_actions_authorized",
            "--evidence-file",
            evidence,
            "--recorded-at-utc",
            timestamp,
            "--summary",
            summary,
            "--output",
            self.root / "reused-authorization-plan.json",
            expect=2,
        )
        self.assertIn("authorization_reference is already used", result.stderr)

    def test_one_routine_deployment_authorization_covers_routine_gates(self) -> None:
        self.make_valid_plan()
        reviewed = self.transition_gate(
            self.plan,
            "dry_run_reviewed",
            1,
        )
        plan = json.loads(reviewed.read_text(encoding="utf-8"))
        evidence = self.root / "routine-deployment-authorization.json"
        authorization_time = "2026-07-29T12:12:00Z"
        summary = "Authorize routine deployment for this exact plan and target."
        write_json(
            evidence,
            {
                "authorization_schema_version": 2,
                "gate": "routine_deployment_authorized",
                "result": "authorized",
                "recorded_at_utc": authorization_time,
                "authorization_scope": "deploy",
                "deployment_id": plan["deployment_id"],
                "plan_core_sha256": plan["plan_core_sha256"],
                "target_machine_id": plan["target"]["identity"]["machine_id"],
                "ssh_host_key_sha256": plan["target"]["connection"][
                    "ssh_host_key_sha256"
                ],
                "approver_identity": "offline-test-operator",
                "authorization_reference": "routine-deploy-reference-01",
                "summary": summary,
            },
        )

        def apply_routine_gate(
            input_plan: Path,
            gate: str,
            sequence: int,
        ) -> Path:
            output = self.root / f"routine-plan-{sequence:02d}-{gate}.json"
            run(
                RECORD_GATE,
                "--plan",
                input_plan,
                "--project-root",
                self.root,
                "--gate",
                gate,
                "--evidence-file",
                evidence,
                "--recorded-at-utc",
                f"2026-07-29T12:{10 + sequence:02d}:00Z",
                "--summary",
                summary,
                "--output",
                output,
            )
            return output

        stage_authorized = apply_routine_gate(reviewed, "stage_authorized", 2)
        staged = self.transition_gate(
            stage_authorized,
            "stage_hashes_verified",
            3,
        )
        precommit = apply_routine_gate(
            staged,
            "precommit_live_actions_authorized",
            4,
        )
        stopped = self.transition_gate(precommit, "safe_stop_confirmed", 5)
        backed_up = self.transition_gate(stopped, "backup_verified", 6)
        committed = apply_routine_gate(backed_up, "commit_authorized", 7)

        result = run(
            VALIDATE,
            committed,
            "--project-root",
            self.root,
            "--require-commit-gates",
        )
        self.assertIn("structurally valid", result.stdout)

    def test_wrong_authorization_binding_is_rejected(self) -> None:
        self.make_valid_plan()
        reviewed = self.transition_gate(
            self.plan,
            "dry_run_reviewed",
            1,
        )
        plan = json.loads(reviewed.read_text(encoding="utf-8"))
        evidence = self.root / "wrong-binding.json"
        timestamp = "2026-07-29T12:12:00Z"
        summary = "Authorize stage."
        write_json(
            evidence,
            {
                "authorization_schema_version": 2,
                "gate": "stage_authorized",
                "result": "authorized",
                "recorded_at_utc": timestamp,
                "authorization_scope": "stage",
                "deployment_id": "different-deployment",
                "plan_core_sha256": plan["plan_core_sha256"],
                "target_machine_id": plan["target"]["identity"]["machine_id"],
                "ssh_host_key_sha256": plan["target"]["connection"][
                    "ssh_host_key_sha256"
                ],
                "approver_identity": "offline-test-operator",
                "authorization_reference": "wrong-binding-test",
                "summary": summary,
            },
        )
        result = run(
            RECORD_GATE,
            "--plan",
            reviewed,
            "--project-root",
            self.root,
            "--gate",
            "stage_authorized",
            "--evidence-file",
            evidence,
            "--recorded-at-utc",
            timestamp,
            "--summary",
            summary,
            "--output",
            self.root / "wrong-binding-plan.json",
            expect=2,
        )
        self.assertIn("deployment_id does not match", result.stderr)

    def test_commit_validation_requires_project_root_and_fresh_inventory(self) -> None:
        ready = self.make_commit_ready_plan()
        result = run(
            VALIDATE,
            ready,
            "--require-commit-gates",
            expect=2,
        )
        self.assertIn(
            "--require-commit-gates requires --project-root",
            result.stderr,
        )
        run(
            VALIDATE,
            ready,
            "--project-root",
            self.root,
            "--require-commit-gates",
        )
        (self.root / "test_app" / "index.html").write_text(
            "<!doctype html><title>TAMPERED</title>\n",
            encoding="utf-8",
        )
        result = run(
            VALIDATE,
            ready,
            "--project-root",
            self.root,
            "--require-commit-gates",
            expect=2,
        )
        self.assertTrue(
            "sha256 no longer matches" in result.stderr
            or "file inventory" in result.stderr
        )

    def test_probe_requires_audit_metadata_and_username(self) -> None:
        probe = json.loads(self.probe.read_text(encoding="utf-8"))
        probe["captured_at"] = ""
        probe["probe_source"] = ""
        probe["connection"].pop("username")
        write_json(self.probe, probe)
        result = run(
            ANALYZE,
            self.probe,
            "--manifest",
            self.manifest,
            "--output",
            self.report,
            expect=2,
        )
        self.assertIn("username", result.stderr + self.report.read_text())
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertTrue(any("captured_at" in item for item in report["blocking_errors"]))
        self.assertTrue(any("probe_source" in item for item in report["blocking_errors"]))

    def test_probe_warnings_block_plan_generation(self) -> None:
        probe = json.loads(self.probe.read_text(encoding="utf-8"))
        probe["board"]["monitor_model_name"] = ""
        write_json(self.probe, probe)
        run(
            ANALYZE,
            self.probe,
            "--manifest",
            self.manifest,
            "--output",
            self.report,
        )
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertTrue(report["warnings"])
        result = run(
            GENERATE,
            "--manifest",
            self.manifest,
            "--probe-report",
            self.report,
            "--project-root",
            self.root,
            "--file-map",
            self.file_map,
            "--deployment-id",
            "warnings-must-block",
            "--output",
            self.plan,
            expect=2,
        )
        self.assertIn("unresolved warnings", result.stderr)

    def test_profiles_model_code_is_required(self) -> None:
        probe = json.loads(self.probe.read_text(encoding="utf-8"))
        probe["board"]["profiles_model_code"] = ""
        write_json(self.probe, probe)
        run(
            ANALYZE,
            self.probe,
            "--manifest",
            self.manifest,
            "--output",
            self.report,
            expect=2,
        )
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertTrue(
            any(
                "board.profiles_model_code is missing" in item
                for item in report["blocking_errors"]
            )
        )

    def test_shared_contract_violation_is_blocking(self) -> None:
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["target"]["guessed_model"] = "do-not-allow"
        write_json(self.manifest, manifest)
        result = run(
            ANALYZE,
            self.probe,
            "--manifest",
            self.manifest,
            "--output",
            self.report,
            expect=2,
        )
        self.assertIn("additional property is not allowed", result.stderr)

    def test_enabled_application_requires_a_route(self) -> None:
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["application"]["routes"] = []
        write_json(self.manifest, manifest)
        result = run(
            ANALYZE,
            self.probe,
            "--manifest",
            self.manifest,
            "--output",
            self.report,
            expect=2,
        )
        self.assertIn("must contain at least 1 item", result.stderr)

    def test_os2_fpga_artifact_profile(self) -> None:
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["target"]["os_release"] = "2.00-35"
        manifest["application"].update(
            {
                "enabled": False,
                "architecture": "none",
                "routes": [],
                "controller_binary": None,
                "service_name": None,
                "python_abi": "none",
                "service_port": None,
            }
        )
        manifest["fpga"] = {
            "enabled": True,
            "project": "test_fpga",
            "artifact_name": "fpga.bit.bin",
            "device_tree_name": "fpga.dtbo",
            "loader": "overlay.sh",
            "register_contract": "register-contract.json",
            "version_register": "0x40600004",
            "magic_value": "0x52504631",
            "clock_hz": 125000000,
        }
        write_json(self.manifest, manifest)
        probe = json.loads(self.probe.read_text(encoding="utf-8"))
        probe["os"]["release"] = "2.00-35"
        probe["fpga"]["expected_artifact"] = "fpga.bit.bin"
        write_json(self.probe, probe)
        source_bitstream = self.root / "red_pitaya.bit.bin"
        source_dtbo = self.root / "source.dtbo"
        source_contract = self.root / "register-contract.json"
        write_structural_test_bitstream(
            source_bitstream,
            1024 * 1024 + 64,
        )
        write_structural_test_dtbo(source_dtbo)
        shutil.copyfile(REGISTER_CONTRACT, source_contract)
        reports: dict[str, Path] = {}
        for name in ("simulation", "timing", "cdc", "drc"):
            path = self.root / f"{name}.txt"
            path.write_text(
                f"OFFLINE TOOL INTEGRATION TEST {name}: pass\n",
                encoding="utf-8",
            )
            reports[name] = path
        evidence_dir = self.root / "evidence"
        run(
            FPGA_EVIDENCE,
            "create",
            "--manifest",
            self.manifest,
            "--bitstream",
            source_bitstream,
            "--dtbo",
            source_dtbo,
            "--register-contract",
            source_contract,
            "--output-dir",
            evidence_dir,
            "--classification",
            "release-candidate",
            "--built-at-utc",
            "2026-07-29T12:05:00Z",
            "--repository",
            "offline-deployment-tool-test",
            "--revision",
            "0123456789abcdef",
            "--source-dirty",
            "false",
            "--vivado-version",
            "2020.1",
            "--bootgen-version",
            "2020.1",
            "--prj",
            "test_fpga",
            "--model",
            "Z20_14",
            "--part",
            "xc7z020clg400-1",
            "--build-command",
            "offline integration fixture",
            "--simulation-report",
            reports["simulation"],
            "--timing-report",
            reports["timing"],
            "--cdc-report",
            reports["cdc"],
            "--drc-report",
            reports["drc"],
            "--simulation-result",
            "pass",
            "--timing-result",
            "pass",
            "--wns-ns",
            "0.1",
            "--tns-ns",
            "0",
            "--unconstrained-paths",
            "0",
            "--cdc-result",
            "pass",
            "--drc-result",
            "pass",
            "--dtc-result",
            "pass",
            "--reset-review-result",
            "pass",
            "--outputs-default-disabled",
            "true",
        )
        package_dir = self.root / "fpga-package"
        run(
            FPGA_PACKAGE,
            "package",
            "--manifest",
            self.manifest,
            "--bitstream",
            source_bitstream,
            "--dtbo",
            source_dtbo,
            "--register-contract",
            source_contract,
            "--build-evidence",
            evidence_dir / "build-evidence.json",
            "--output-dir",
            package_dir,
        )
        write_json(
            self.file_map,
            {
                "schema_version": 1,
                "files": [
                    {
                        "source": "fpga-package/fpga.bit.bin",
                        "destination": (
                            "/opt/redpitaya/www/apps/test_app/fpga.bit.bin"
                        ),
                        "component": "fpga",
                        "mode": "0644",
                    },
                    {
                        "source": "fpga-package/fpga.dtbo",
                        "destination": "/opt/redpitaya/www/apps/test_app/fpga.dtbo",
                        "component": "device-tree",
                        "mode": "0644",
                    },
                    {
                        "source": "fpga-package/fpga.bit.bin",
                        "destination": (
                            "/opt/redpitaya/fpga/z20_125/test_app/fpga.bit.bin"
                        ),
                        "component": "fpga-profile",
                        "mode": "0644",
                    },
                    {
                        "source": "fpga-package/fpga.dtbo",
                        "destination": (
                            "/opt/redpitaya/fpga/z20_125/test_app/fpga.dtbo"
                        ),
                        "component": "device-tree-profile",
                        "mode": "0644",
                    },
                    {
                        "source": "fpga-package/register-contract.json",
                        "destination": (
                            "/opt/redpitaya/www/apps/test_app/"
                            "register-contract.json"
                        ),
                        "component": "configuration",
                        "mode": "0644",
                    },
                ],
            },
        )
        run(
            ANALYZE,
            self.probe,
            "--manifest",
            self.manifest,
            "--expected-role",
            "acquisition",
            "--output",
            self.report,
        )
        run(
            GENERATE,
            "--manifest",
            self.manifest,
            "--probe-report",
            self.report,
            "--project-root",
            self.root,
            "--file-map",
            self.file_map,
            "--deployment-id",
            "os2-fpga-test",
            "--output",
            self.plan,
        )
        plan = json.loads(self.plan.read_text(encoding="utf-8"))
        self.assertEqual(plan["target"]["expected_fpga_artifact"], "fpga.bit.bin")
        self.assertEqual(
            plan["fpga_provenance"]["attestation"],
            "self-attested",
        )
        self.assertFalse(
            plan["fpga_provenance"]["package_declared_loadable"]
        )
        self.assertTrue(
            plan["fpga_provenance"]["release_review_required"]
        )
        self.assertFalse(
            plan["fpga_provenance"]["package_grants_load_permission"]
        )
        self.assertFalse(plan["gates"]["fpga_release_reviewed"])
        self.assertFalse(plan["gates"]["fpga_load_authorized"])
        self.assertTrue(
            (package_dir / "RELEASE_REVIEW_REQUIRED.txt").is_file()
        )
        run(VALIDATE, self.plan, "--project-root", self.root)
        premature = self.root / "premature-fpga-load.txt"
        premature.write_text("not an authorization\n", encoding="utf-8")
        result = run(
            RECORD_GATE,
            "--plan",
            self.plan,
            "--project-root",
            self.root,
            "--gate",
            "fpga_load_authorized",
            "--evidence-file",
            premature,
            "--recorded-at-utc",
            "2026-07-29T12:30:00Z",
            "--summary",
            "Premature FPGA load attempt.",
            "--output",
            self.root / "premature-fpga-load-plan.json",
            expect=2,
        )
        self.assertIn("fpga_release_reviewed", result.stderr)

        stale_dir = self.root / "stale-profile"
        stale_dir.mkdir()
        stale_bitstream = stale_dir / "fpga.bit.bin"
        stale_bitstream.write_bytes(b"stale-profile-copy")
        stale_map = json.loads(self.file_map.read_text(encoding="utf-8"))
        for entry in stale_map["files"]:
            if entry["component"] == "fpga-profile":
                entry["source"] = "stale-profile/fpga.bit.bin"
        stale_map_path = self.root / "stale-profile-map.json"
        write_json(stale_map_path, stale_map)
        stale_result = run(
            GENERATE,
            "--manifest",
            self.manifest,
            "--probe-report",
            self.report,
            "--project-root",
            self.root,
            "--file-map",
            stale_map_path,
            "--deployment-id",
            "os2-stale-profile-test",
            "--output",
            self.root / "stale-profile-plan.json",
            expect=2,
        )
        self.assertIn(
            "app and permanent-profile FPGA artifact hashes differ",
            stale_result.stderr,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
