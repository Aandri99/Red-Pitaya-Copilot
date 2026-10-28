#!/usr/bin/env python3
"""Offline self-test for the deterministic FPGA skill tools."""

from __future__ import annotations

import argparse
import hashlib
import json
import py_compile
import shutil
import struct
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import fpga_package
import fpga_build_evidence
import register_contract
import resolve_fpga_target


SKILL_DIR = Path(__file__).resolve().parent.parent
ASSETS = SKILL_DIR / "assets"
CONTRACT = ASSETS / "register-contract.example.json"
OS200_MANIFEST = ASSETS / "redpitaya-project.os2.00.example.json"
OS2_MANIFEST = ASSETS / "redpitaya-project.os2.example.json"
OS3_MANIFEST = ASSETS / "redpitaya-project.os3.example.json"


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_synthetic_bitstream(path: Path, size: int = 4096) -> None:
    # Structurally plausible only: sync, NOP and one type-1 write header.
    # It is deliberately not a real/loadable design.
    if size < 1024:
        raise ValueError("synthetic fixture must be at least 1024 bytes")
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


def write_synthetic_dtbo(path: Path) -> None:
    # Minimal structurally valid FDT. It is not a target-specific FPGA overlay.
    strings = b"compatible\x00"
    structure = b"".join(
        (
            struct.pack(">I", fpga_build_evidence.FDT_BEGIN_NODE),
            bytes(4),
            struct.pack(">III", fpga_build_evidence.FDT_PROP, 4, 0),
            b"test",
            struct.pack(">I", fpga_build_evidence.FDT_END_NODE),
            struct.pack(">I", fpga_build_evidence.FDT_END),
        )
    )
    reserve_map = bytes(16)
    off_mem_rsvmap = 40
    off_struct = off_mem_rsvmap + len(reserve_map)
    off_strings = off_struct + len(structure)
    total_size = off_strings + len(strings)
    header = struct.pack(
        ">10I",
        fpga_build_evidence.FDT_MAGIC,
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


def create_test_evidence(
    root: Path,
    manifest: Path,
    bitstream: Path,
    dtbo: Path,
    name: str,
    classification: str = "test-fixture",
) -> Path:
    reports: dict[str, Path] = {}
    for label in fpga_build_evidence.REPORT_NAMES:
        path = root / f"source-{label}-report.txt"
        path.write_text(
            f"NON-AUTHORITATIVE {label} REPORT FOR OFFLINE TEST FIXTURE\n",
            encoding="utf-8",
        )
        reports[label] = path
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    model = manifest_data["target"]["model"]
    zynq = json.loads(
        (
            fpga_package.plugin_root() / "profiles" / "board-models.json"
        ).read_text(encoding="utf-8")
    )["models"][model]["zynq"]
    output = root / f"evidence-{name}"
    release_candidate = classification == "release-candidate"
    fpga_build_evidence.create_evidence(
        argparse.Namespace(
            manifest=manifest,
            bitstream=bitstream,
            dtbo=dtbo,
            register_contract=CONTRACT,
            output_dir=output,
            classification=classification,
            built_at_utc="2000-01-01T00:00:00Z",
            repository="reviewed-repository" if release_candidate else "offline-self-test",
            revision="0123456789abcdef" if release_candidate else "TEST-FIXTURE",
            source_dirty=not release_candidate,
            vivado_version="2020.1" if release_candidate else "not-run",
            bootgen_version="2020.1" if release_candidate else "not-run",
            prj=manifest_data["fpga"]["project"],
            model=fpga_package.VIVADO_MODEL_BY_PROFILE[model],
            part=f"{zynq}-test-fixture",
            build_command="not-run: synthetic fixture",
            simulation_report=reports["simulation"],
            timing_report=reports["timing"],
            cdc_report=reports["cdc"],
            drc_report=reports["drc"],
            simulation_result="pass" if release_candidate else "not-run",
            timing_result="pass" if release_candidate else "not-run",
            wns_ns=0.0,
            tns_ns=0.0,
            unconstrained_paths=0,
            critical_warning=[],
            cdc_result="pass" if release_candidate else "not-run",
            drc_result="pass" if release_candidate else "not-run",
            dtc_result="pass" if release_candidate else "not-run",
            reset_review_result="pass" if release_candidate else "not-run",
            outputs_default_disabled=True,
        )
    )
    return output / "build-evidence.json"


@contextmanager
def test_directory():
    path = SKILL_DIR / f".self-test-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        if path.exists():
            shutil.rmtree(path)


class RegisterContractTests(unittest.TestCase):
    def test_example_is_strictly_valid_and_generates(self) -> None:
        data = register_contract.load_json(CONTRACT)
        self.assertEqual(register_contract.validate_contract(data, strict=True), [])
        with test_directory() as output:
            generated = register_contract.generate_outputs(data, output)
            self.assertEqual(len(generated), 6)
            py_compile.compile(
                str(output / "rp_custom_acq_regs.py"),
                doraise=True,
            )
            header = (output / "rp_custom_acq_regs.h").read_text(encoding="utf-8")
            sv = (output / "rp_custom_acq_regs.svh").read_text(encoding="utf-8")
            self.assertIn("RP_CUSTOM_ACQ_REG_MAGIC", header)
            self.assertIn("RP_CUSTOM_ACQ_REG_MAGIC", sv)
            digest = register_contract.canonical_contract_sha256(
                register_contract.normalize_contract(data)
            )
            self.assertIn(digest, header)
            self.assertIn(digest, sv)
            python = (output / "rp_custom_acq_regs.py").read_text(encoding="utf-8")
            self.assertIn(digest, python)
            self.assertEqual(
                (output / "rp_custom_acq_contract.sha256").read_text(
                    encoding="utf-8"
                ),
                f"{digest}  canonical-json\n",
            )

    def test_overlap_duplicate_and_unsafe_reset_are_rejected(self) -> None:
        data = register_contract.load_json(CONTRACT)
        data["registers"][1]["offset"] = data["registers"][0]["offset"]
        data["registers"][3]["reset"] = "0x00000001"
        data["registers"][4]["fields"].append(
            {
                "name": "OVERLAP",
                "lsb": 0,
                "width": 2,
                "description": "Intentional invalid overlap",
            }
        )
        messages = [
            finding.message
            for finding in register_contract.validate_contract(data, strict=True)
        ]
        self.assertTrue(any("duplicate offset" in item for item in messages))
        self.assertTrue(any("safe zero control reset" in item for item in messages))
        self.assertTrue(any("overlaps" in item for item in messages))


class TargetResolverTests(unittest.TestCase):
    def test_os2_and_os3_resolve_exact_artifacts(self) -> None:
        os2 = resolve_fpga_target.resolve(OS2_MANIFEST)
        os3 = resolve_fpga_target.resolve(OS3_MANIFEST)
        self.assertEqual(os2["target_profile"], "z10_125")
        self.assertEqual(os2["vivado_model"], "Z10")
        self.assertTrue(os2["probe_required"])
        self.assertEqual(os2["fpga_artifact_name"], "fpga.bit.bin")
        self.assertEqual(os3["fpga_artifact_name"], "fpga.bin")
        os200 = resolve_fpga_target.resolve(OS200_MANIFEST)
        self.assertEqual(os200["fpga_artifact_name"], "fpga.bit.bin")
        self.assertEqual(os200["os_release"], "2.00-35")

    def test_every_profile_has_a_vivado_mapping(self) -> None:
        profiles = json.loads(
            (
                fpga_package.plugin_root()
                / "profiles"
                / "board-models.json"
            ).read_text(encoding="utf-8")
        )["models"]
        self.assertEqual(
            set(profiles),
            set(resolve_fpga_target.VIVADO_MODEL_BY_PROFILE),
        )
        self.assertEqual(
            resolve_fpga_target.VIVADO_MODEL_BY_PROFILE,
            fpga_package.VIVADO_MODEL_BY_PROFILE,
        )


class BuildEvidenceTests(unittest.TestCase):
    def test_release_candidate_is_self_attested_and_never_loadable(self) -> None:
        with test_directory() as root:
            bitstream = root / "candidate.bin"
            dtbo = root / "synthetic.dtbo"
            write_synthetic_bitstream(
                bitstream,
                fpga_build_evidence.RELEASE_MIN_BITSTREAM_BYTES,
            )
            write_synthetic_dtbo(dtbo)
            evidence_path = create_test_evidence(
                root,
                OS3_MANIFEST,
                bitstream,
                dtbo,
                "self-attested-release",
                classification="release-candidate",
            )
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(evidence["evidence_schema_version"], 2)
            self.assertEqual(evidence["classification"], "release-candidate")
            self.assertEqual(evidence["attestation"], "self-attested")
            self.assertTrue(evidence["release_review_required"])
            self.assertFalse(evidence["loadable"])
            self.assertEqual(
                evidence["asserted"]["origin"],
                "caller-supplied-untrusted-claims",
            )
            self.assertIn("bitstream_structure", evidence["observed"])
            fpga_build_evidence.validate_evidence(
                evidence_path,
                OS3_MANIFEST,
                bitstream,
                dtbo,
                CONTRACT,
            )

    def test_loadable_attestation_and_observation_tampering_are_rejected(self) -> None:
        with test_directory() as root:
            bitstream = root / "synthetic.bin"
            dtbo = root / "synthetic.dtbo"
            write_synthetic_bitstream(bitstream)
            write_synthetic_dtbo(dtbo)
            evidence_path = create_test_evidence(
                root,
                OS3_MANIFEST,
                bitstream,
                dtbo,
                "tamper",
            )
            original = json.loads(evidence_path.read_text(encoding="utf-8"))

            tampered = dict(original)
            tampered["loadable"] = True
            evidence_path.write_text(
                json.dumps(tampered, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                fpga_build_evidence.EvidenceError,
                "loadable must always be false",
            ):
                fpga_build_evidence.validate_evidence(
                    evidence_path,
                    OS3_MANIFEST,
                    bitstream,
                    dtbo,
                    CONTRACT,
                )

            tampered = json.loads(json.dumps(original))
            tampered["attestation"] = "trusted"
            evidence_path.write_text(
                json.dumps(tampered, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                fpga_build_evidence.EvidenceError,
                "attestation must equal",
            ):
                fpga_build_evidence.validate_evidence(
                    evidence_path,
                    OS3_MANIFEST,
                    bitstream,
                    dtbo,
                    CONTRACT,
                )

            tampered = json.loads(json.dumps(original))
            tampered["observed"]["bitstream_structure"]["sync_word_offset"] += 4
            evidence_path.write_text(
                json.dumps(tampered, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                fpga_build_evidence.EvidenceError,
                "differs from deterministic parsing",
            ):
                fpga_build_evidence.validate_evidence(
                    evidence_path,
                    OS3_MANIFEST,
                    bitstream,
                    dtbo,
                    CONTRACT,
                )

            tampered = json.loads(json.dumps(original))
            tampered["asserted"]["verification"]["trusted"] = True
            evidence_path.write_text(
                json.dumps(tampered, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                fpga_build_evidence.EvidenceError,
                "asserted.verification must contain exactly",
            ):
                fpga_build_evidence.validate_evidence(
                    evidence_path,
                    OS3_MANIFEST,
                    bitstream,
                    dtbo,
                    CONTRACT,
                )

    def test_fake_megabyte_text_bitstream_and_magic_only_fdt_are_rejected(self) -> None:
        with test_directory() as root:
            fake_text = root / "fake-text.bin"
            fake_text.write_bytes(
                (b"THIS IS NOT A XILINX BITSTREAM\n" * 40000)[
                    : fpga_build_evidence.RELEASE_MIN_BITSTREAM_BYTES
                ]
            )
            valid_dtbo = root / "valid.dtbo"
            write_synthetic_dtbo(valid_dtbo)
            with self.assertRaisesRegex(
                fpga_build_evidence.EvidenceError,
                "Xilinx 7-series",
            ):
                create_test_evidence(
                    root,
                    OS3_MANIFEST,
                    fake_text,
                    valid_dtbo,
                    "fake-text",
                    classification="release-candidate",
                )

        with test_directory() as root:
            plausible_binary = root / "plausible.bin"
            write_synthetic_bitstream(
                plausible_binary,
                fpga_build_evidence.RELEASE_MIN_BITSTREAM_BYTES,
            )
            fake_dtbo = root / "magic-only.dtbo"
            fake_dtbo.write_bytes(fpga_package.FDT_MAGIC + bytes(60))
            with self.assertRaisesRegex(
                fpga_build_evidence.EvidenceError,
                "totalsize",
            ):
                create_test_evidence(
                    root,
                    OS3_MANIFEST,
                    plausible_binary,
                    fake_dtbo,
                    "fake-fdt",
                    classification="release-candidate",
                )


class PackageTests(unittest.TestCase):
    def test_atomic_publish_retries_only_transient_permission_errors(self) -> None:
        source = mock.Mock()
        destination = mock.Mock()
        source.rename.side_effect = [PermissionError("transient lock"), None]
        source.exists.return_value = True
        destination.exists.return_value = False
        with mock.patch.object(fpga_package.time, "sleep"):
            fpga_package.rename_with_transient_retry(source, destination)
        self.assertEqual(source.rename.call_count, 2)

    def _create_package(
        self,
        root: Path,
        manifest: Path,
        name: str,
        make_zip: bool = False,
        classification: str = "test-fixture",
    ) -> Path:
        bitstream = root / "red_pitaya.bit.bin"
        dtbo = root / "source.dtbo"
        write_synthetic_bitstream(
            bitstream,
            (
                fpga_build_evidence.RELEASE_MIN_BITSTREAM_BYTES
                if classification == "release-candidate"
                else 4096
            ),
        )
        write_synthetic_dtbo(dtbo)
        evidence = create_test_evidence(
            root,
            manifest,
            bitstream,
            dtbo,
            name,
            classification=classification,
        )
        output = root / name
        fpga_package.package(
            manifest,
            bitstream,
            output,
            dtbo,
            CONTRACT,
            evidence,
            make_zip,
        )
        return output

    def test_os2_and_os3_package_names_and_validation(self) -> None:
        with test_directory() as root:
            os200 = self._create_package(root, OS200_MANIFEST, "os200")
            self.assertTrue((os200 / "fpga.bit.bin").is_file())
            self.assertFalse((os200 / "fpga.bin").exists())
            fpga_package.validate_package(OS200_MANIFEST, os200)

        with test_directory() as root:
            os2 = self._create_package(root, OS2_MANIFEST, "os2")
            self.assertTrue((os2 / "fpga.bit.bin").is_file())
            self.assertFalse((os2 / "fpga.bin").exists())
            fpga_package.validate_package(OS2_MANIFEST, os2)

        with test_directory() as root:
            os3 = self._create_package(root, OS3_MANIFEST, "os3")
            self.assertTrue((os3 / "fpga.bin").is_file())
            self.assertFalse((os3 / "fpga.bit.bin").exists())
            self.assertTrue((os3 / "DO_NOT_LOAD.txt").is_file())
            package_manifest = json.loads(
                (os3 / "package-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(package_manifest["target"]["vivado_model"], "Z10")
            self.assertEqual(package_manifest["classification"], "test-fixture")
            self.assertEqual(package_manifest["attestation"], "self-attested")
            self.assertFalse(package_manifest["release_review_required"])
            self.assertFalse(package_manifest["loadable"])
            fpga_package.validate_package(OS3_MANIFEST, os3)

    def test_release_candidate_package_requires_external_review_and_is_not_loadable(
        self,
    ) -> None:
        with test_directory() as root:
            output = self._create_package(
                root,
                OS3_MANIFEST,
                "release-candidate",
                classification="release-candidate",
            )
            self.assertTrue(
                (output / fpga_package.RELEASE_REVIEW_REQUIRED_FILENAME).is_file()
            )
            self.assertFalse((output / fpga_package.DO_NOT_LOAD_FILENAME).exists())
            package_manifest = json.loads(
                (output / "package-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(package_manifest["schema_version"], 2)
            self.assertEqual(package_manifest["attestation"], "self-attested")
            self.assertTrue(package_manifest["release_review_required"])
            self.assertFalse(package_manifest["loadable"])
            fpga_package.validate_package(OS3_MANIFEST, output)

    def test_release_marker_and_offline_loadable_policy_cannot_be_removed(
        self,
    ) -> None:
        with test_directory() as root:
            output = self._create_package(
                root,
                OS3_MANIFEST,
                "release-marker-policy",
                classification="release-candidate",
            )
            marker = output / fpga_package.RELEASE_REVIEW_REQUIRED_FILENAME
            marker.unlink()
            with self.assertRaisesRegex(
                fpga_package.PackageError,
                "unexpected/missing package files",
            ):
                fpga_package.validate_package(OS3_MANIFEST, output)

        with test_directory() as root:
            output = self._create_package(
                root,
                OS3_MANIFEST,
                "release-loadable-policy",
                classification="release-candidate",
            )
            manifest_path = output / "package-manifest.json"
            package_manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            package_manifest["loadable"] = True
            manifest_path.write_text(
                json.dumps(package_manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                fpga_package.PackageError,
                "loadable must always be false",
            ):
                fpga_package.validate_package(OS3_MANIFEST, output)

    def test_zip_is_reproducible(self) -> None:
        with test_directory() as root:
            self._create_package(root, OS3_MANIFEST, "one", make_zip=True)
            self._create_package(root, OS3_MANIFEST, "two", make_zip=True)
            self.assertEqual(
                file_hash(root / "one.zip"),
                file_hash(root / "two.zip"),
            )

    def test_tamper_is_detected(self) -> None:
        with test_directory() as root:
            output = self._create_package(root, OS3_MANIFEST, "tamper")
            artifact = output / "fpga.bin"
            data = bytearray(artifact.read_bytes())
            data[100] ^= 0x01
            artifact.write_bytes(data)
            with self.assertRaisesRegex(fpga_package.PackageError, "SHA-256 mismatch"):
                fpga_package.validate_package(OS3_MANIFEST, output)

    def test_raw_bit_and_bad_dtbo_are_rejected(self) -> None:
        with test_directory() as root:
            raw_bit = root / "unsafe.bit"
            bad_dtbo = root / "bad.dtbo"
            write_synthetic_bitstream(raw_bit)
            bad_dtbo.write_bytes(bytes(64))
            with self.assertRaisesRegex(fpga_package.PackageError, "raw Vivado"):
                fpga_package.package(
                    OS3_MANIFEST,
                    raw_bit,
                    root / "bad-bit-output",
                    bad_dtbo,
                    CONTRACT,
                    root / "unused-evidence.json",
                    False,
                )

            binary = root / "valid-name.bin"
            write_synthetic_bitstream(binary)
            with self.assertRaisesRegex(
                fpga_package.PackageError,
                "FDT magic",
            ):
                fpga_package.package(
                    OS3_MANIFEST,
                    binary,
                    root / "bad-dtbo-output",
                    bad_dtbo,
                    CONTRACT,
                    root / "unused-evidence.json",
                    False,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
