#!/usr/bin/env python3
"""Create and validate deterministic OS-specific Red Pitaya FPGA packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

import fpga_build_evidence
from register_contract import ContractError as RegisterContractError
from register_contract import load_json as load_register_json
from register_contract import parse_integer, validate_contract


PACKAGE_SCHEMA_VERSION = 2
MIN_BITSTREAM_BYTES = 1024
MIN_DTBO_BYTES = 40
FDT_MAGIC = bytes.fromhex("d00dfeed")
EVIDENCE_FILENAMES = {
    "build-evidence.json",
    *fpga_build_evidence.REPORT_NAMES.values(),
}
DO_NOT_LOAD_FILENAME = "DO_NOT_LOAD.txt"
RELEASE_REVIEW_REQUIRED_FILENAME = "RELEASE_REVIEW_REQUIRED.txt"
VIVADO_MODEL_BY_PROFILE = {
    "z10_125": "Z10",
    "z10_125_v2": "Z10",
    "z10_125_pro_v2": "Z10",
    "z20_122": "Z20",
    "z20_125": "Z20_14",
    "z20_125_4ch": "Z20_4",
    "z20_125_ll": "Z20_ll",
    "z20_125_v2": "Z20_G2",
    "z20_250": "Z20_250",
    "z20_250a": "Z20_250",
    "z20_250_1_0": "Z20_250",
}
PACKAGE_MANIFEST_KEYS = {
    "schema_version",
    "deterministic",
    "classification",
    "attestation",
    "release_review_required",
    "loadable",
    "target",
    "fpga",
    "files",
}
FILE_RECORD_KEYS = {"sha256", "size", "source_name"}


class PackageError(Exception):
    """Raised for a user-facing packaging or validation error."""


def rename_with_transient_retry(source: Path, destination: Path) -> None:
    """Atomically publish a directory despite short-lived Windows file locks."""
    delays = (0.0, 0.025, 0.05, 0.1, 0.2, 0.4)
    last_error: OSError | None = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            source.rename(destination)
            return
        except PermissionError as exc:
            last_error = exc
            if destination.exists() or not source.exists():
                raise
    assert last_error is not None
    raise last_error


def skill_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def plugin_root() -> Path:
    return skill_dir().parent.parent


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackageError(f"cannot read {label} {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PackageError(
            f"{label} {path}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise PackageError(f"{label} {path} must contain a JSON object")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PackageError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def require_regular_file(path: Path, label: str, minimum_bytes: int = 1) -> None:
    if not path.is_file():
        raise PackageError(f"{label} is not a regular file: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PackageError(f"cannot inspect {label} {path}: {exc}") from exc
    if size < minimum_bytes:
        raise PackageError(
            f"{label} is too small ({size} bytes; minimum {minimum_bytes}): {path}"
        )


def run_shared_manifest_validator(manifest: Path) -> None:
    validator = plugin_root() / "scripts" / "validate_redpitaya_project.py"
    schema = plugin_root() / "schemas" / "redpitaya-project.schema.json"
    require_regular_file(validator, "shared project validator")
    require_regular_file(schema, "shared project schema")
    result = subprocess.run(
        [sys.executable, str(validator), str(manifest)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        details = (result.stdout + result.stderr).strip()
        raise PackageError(
            "shared project contract validation failed"
            + (f":\n{details}" if details else "")
        )


def load_profiles(os_release: str) -> tuple[dict[str, Any], dict[str, Any]]:
    profiles_dir = plugin_root() / "profiles"
    os_profile_path = profiles_dir / f"os-{os_release}.json"
    board_profile_path = profiles_dir / "board-models.json"
    os_profile = load_json(os_profile_path, "OS profile")
    board_profiles = load_json(board_profile_path, "board-model profile")
    if os_profile.get("os_release") != os_release:
        raise PackageError(
            f"OS profile {os_profile_path} does not declare {os_release}"
        )
    return os_profile, board_profiles


def validate_project_for_fpga(
    manifest_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    require_regular_file(manifest_path, "project manifest")
    run_shared_manifest_validator(manifest_path)
    manifest = load_json(manifest_path, "project manifest")
    target = manifest.get("target")
    fpga = manifest.get("fpga")
    if not isinstance(target, dict) or not isinstance(fpga, dict):
        raise PackageError("project manifest target/fpga objects are required")
    if fpga.get("enabled") is not True:
        raise PackageError("$.fpga.enabled must be true for FPGA packaging")
    os_release = target.get("os_release")
    model = target.get("model")
    if not isinstance(os_release, str) or not isinstance(model, str):
        raise PackageError("$.target.os_release and $.target.model must be strings")
    os_profile, board_profiles = load_profiles(os_release)
    models = board_profiles.get("models")
    if not isinstance(models, dict) or model not in models:
        raise PackageError(f"unknown board profile in $.target.model: {model!r}")
    if model not in VIVADO_MODEL_BY_PROFILE:
        raise PackageError(f"no audited Vivado MODEL mapping exists for {model!r}")
    expected_artifact = os_profile.get("fpga_artifact_filename")
    if fpga.get("artifact_name") != expected_artifact:
        raise PackageError(
            f"$.fpga.artifact_name must be {expected_artifact!r} for {os_release}"
        )
    expected_dtbo = os_profile.get("fpga_device_tree_filename")
    declared_dtbo = fpga.get("device_tree_name")
    if declared_dtbo is not None and declared_dtbo != expected_dtbo:
        raise PackageError(
            f"$.fpga.device_tree_name must be null or {expected_dtbo!r}"
        )
    if fpga.get("loader") not in {"overlay.sh", "fpgautil"}:
        raise PackageError(
            "$.fpga.loader must be overlay.sh or fpgautil when FPGA is enabled"
        )
    return manifest, os_profile


def resolve_contract_path(
    manifest_path: Path,
    manifest: dict[str, Any],
    supplied_path: Path | None,
) -> Path | None:
    declared = manifest["fpga"].get("register_contract")
    if declared is None:
        if supplied_path is not None:
            raise PackageError(
                "--register-contract was supplied but $.fpga.register_contract is null"
            )
        return None
    if not isinstance(declared, str) or not declared:
        raise PackageError("$.fpga.register_contract must be null or a path string")
    expected = (manifest_path.parent / declared).resolve()
    actual = supplied_path.resolve() if supplied_path is not None else expected
    if actual != expected:
        raise PackageError(
            "--register-contract must resolve to the path declared by "
            f"$.fpga.register_contract: {expected}"
        )
    require_regular_file(actual, "register contract")
    return actual


def validate_register_contract_against_manifest(
    contract_path: Path, manifest: dict[str, Any]
) -> None:
    try:
        contract = load_register_json(contract_path)
        findings = validate_contract(contract, strict=True)
    except RegisterContractError as exc:
        raise PackageError(f"register contract error: {exc}") from exc
    if findings:
        details = "\n".join(f"  {item.format()}" for item in findings)
        raise PackageError(f"register contract validation failed:\n{details}")

    registers = {
        item["name"]: item
        for item in contract.get("registers", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    magic_register = registers["MAGIC"]
    abi_register = registers["ABI_VERSION"]
    expected_magic = manifest["fpga"].get("magic_value")
    expected_version_address = manifest["fpga"].get("version_register")
    if expected_magic is None or expected_version_address is None:
        raise PackageError(
            "$.fpga.magic_value and $.fpga.version_register are required "
            "when a register contract is declared"
        )
    contract_magic = parse_integer(magic_register["reset"], "$.MAGIC.reset")
    manifest_magic = parse_integer(expected_magic, "$.fpga.magic_value")
    if contract_magic != manifest_magic:
        raise PackageError(
            "$.fpga.magic_value does not match register contract MAGIC reset"
        )
    base = parse_integer(contract["base_address"], "$.base_address")
    abi_offset = parse_integer(abi_register["offset"], "$.ABI_VERSION.offset")
    manifest_version_address = parse_integer(
        expected_version_address, "$.fpga.version_register"
    )
    if base + abi_offset != manifest_version_address:
        raise PackageError(
            "$.fpga.version_register does not equal contract base + "
            "ABI_VERSION offset"
        )


def validate_dtbo(path: Path) -> None:
    require_regular_file(path, "device-tree overlay", MIN_DTBO_BYTES)
    try:
        fpga_build_evidence.inspect_fdt(path)
    except fpga_build_evidence.EvidenceError as exc:
        raise PackageError(f"device-tree overlay structure is invalid: {exc}") from exc


def validate_bitstream(path: Path) -> None:
    require_regular_file(path, "Bootgen bitstream", MIN_BITSTREAM_BYTES)
    try:
        fpga_build_evidence.inspect_xilinx_bitstream(path)
    except fpga_build_evidence.EvidenceError as exc:
        raise PackageError(f"bitstream structure is invalid: {exc}") from exc


def artifact_record(path: Path, source_name: str) -> dict[str, Any]:
    return {
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
        "source_name": source_name,
    }


def write_text(path: Path, content: str) -> None:
    try:
        path.write_text(content, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise PackageError(f"cannot write {path}: {exc}") from exc


def create_deterministic_zip(package_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        raise PackageError(f"ZIP output already exists: {zip_path}")
    try:
        with zipfile.ZipFile(
            zip_path,
            mode="x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for path in sorted(package_dir.iterdir(), key=lambda item: item.name):
                if not path.is_file():
                    continue
                info = zipfile.ZipInfo(path.name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                info.create_system = 3
                archive.writestr(info, path.read_bytes(), compresslevel=9)
    except (OSError, zipfile.BadZipFile) as exc:
        try:
            zip_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise PackageError(f"cannot create ZIP {zip_path}: {exc}") from exc


def package(
    manifest_path: Path,
    bitstream: Path,
    output_dir: Path,
    dtbo: Path | None,
    register_contract: Path | None,
    build_evidence: Path,
    make_zip: bool,
) -> None:
    manifest_path = manifest_path.resolve()
    bitstream = bitstream.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise PackageError(f"output directory already exists: {output_dir}")
    zip_path = output_dir.with_suffix(".zip")
    if make_zip and zip_path.exists():
        raise PackageError(f"ZIP output already exists: {zip_path}")

    manifest, os_profile = validate_project_for_fpga(manifest_path)
    if bitstream.suffix.lower() == ".bit":
        raise PackageError(
            "raw Vivado .bit files are forbidden; supply a Bootgen binary"
        )
    if not bitstream.name.lower().endswith(".bin"):
        raise PackageError("bitstream input must be a Bootgen .bin/.bit.bin file")
    validate_bitstream(bitstream)

    declared_dtbo = manifest["fpga"].get("device_tree_name")
    if declared_dtbo is not None and dtbo is None:
        raise PackageError(
            "--dtbo is required because $.fpga.device_tree_name is not null"
        )
    if declared_dtbo is None and dtbo is not None:
        raise PackageError(
            "--dtbo was supplied but $.fpga.device_tree_name is null"
        )
    if dtbo is not None:
        dtbo = dtbo.resolve()
        validate_dtbo(dtbo)

    contract_path = resolve_contract_path(
        manifest_path, manifest, register_contract
    )
    if contract_path is not None:
        validate_register_contract_against_manifest(contract_path, manifest)
    if dtbo is None or contract_path is None:
        raise PackageError(
            "FPGA packaging requires both a device-tree overlay and register contract"
        )
    build_evidence = build_evidence.resolve()
    try:
        evidence = fpga_build_evidence.validate_evidence(
            build_evidence,
            manifest_path,
            bitstream,
            dtbo,
            contract_path,
        )
    except fpga_build_evidence.EvidenceError as exc:
        raise PackageError(f"build evidence is invalid: {exc}") from exc
    evidence_dir = build_evidence.parent
    actual_evidence_files = {
        path.name for path in evidence_dir.iterdir() if path.is_file()
    }
    if actual_evidence_files != EVIDENCE_FILENAMES:
        raise PackageError(
            "evidence directory must contain exactly "
            f"{sorted(EVIDENCE_FILENAMES)}; found {sorted(actual_evidence_files)}"
        )
    if any(path.is_dir() or path.is_symlink() for path in evidence_dir.iterdir()):
        raise PackageError("evidence directory must contain regular files only")
    classification = str(evidence["classification"])

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = (
        output_dir.parent / f".{output_dir.name}.staging-{uuid.uuid4().hex}"
    )
    temporary_path.mkdir()
    output_created_by_this_run = False
    try:
        artifact_name = os_profile["fpga_artifact_filename"]
        copied_bitstream = temporary_path / artifact_name
        shutil.copyfile(bitstream, copied_bitstream)
        if dtbo is not None:
            shutil.copyfile(
                dtbo, temporary_path / os_profile["fpga_device_tree_filename"]
            )
        if contract_path is not None:
            shutil.copyfile(contract_path, temporary_path / "register-contract.json")
        shutil.copyfile(manifest_path, temporary_path / "redpitaya-project.json")
        for name in sorted(EVIDENCE_FILENAMES):
            shutil.copyfile(evidence_dir / name, temporary_path / name)
        if classification == "test-fixture":
            write_text(
                temporary_path / DO_NOT_LOAD_FILENAME,
                "TEST FIXTURE ONLY - NOT A LOADABLE RED PITAYA FPGA RELEASE\n",
            )
        else:
            write_text(
                temporary_path / RELEASE_REVIEW_REQUIRED_FILENAME,
                "SELF-ATTESTED RELEASE CANDIDATE - NOT AUTHORIZED TO LOAD\n"
                "A separate deployment-bound FPGA release-review receipt is "
                "required before any live load.\n",
            )

        payload_sources: dict[str, str] = {
            artifact_name: bitstream.name,
            "redpitaya-project.json": manifest_path.name,
        }
        if dtbo is not None:
            payload_sources[os_profile["fpga_device_tree_filename"]] = dtbo.name
        if contract_path is not None:
            payload_sources["register-contract.json"] = contract_path.name
        for name in sorted(EVIDENCE_FILENAMES):
            payload_sources[name] = name
        if classification == "test-fixture":
            payload_sources[DO_NOT_LOAD_FILENAME] = "generated safety marker"
        else:
            payload_sources[RELEASE_REVIEW_REQUIRED_FILENAME] = (
                "generated release-review marker"
            )

        files = {
            name: artifact_record(temporary_path / name, source)
            for name, source in sorted(payload_sources.items())
        }
        package_manifest = {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "deterministic": True,
            "classification": classification,
            "attestation": evidence["attestation"],
            "release_review_required": evidence["release_review_required"],
            "loadable": evidence["loadable"],
            "target": {
                "os_release": manifest["target"]["os_release"],
                "model": manifest["target"]["model"],
                "vivado_model": VIVADO_MODEL_BY_PROFILE[
                    manifest["target"]["model"]
                ],
            },
            "fpga": {
                "project": manifest["fpga"]["project"],
                "artifact_name": artifact_name,
                "device_tree_name": manifest["fpga"]["device_tree_name"],
                "loader": manifest["fpga"]["loader"],
                "version_register": manifest["fpga"].get("version_register"),
                "magic_value": manifest["fpga"].get("magic_value"),
                "clock_hz": manifest["fpga"].get("clock_hz"),
            },
            "files": files,
        }
        write_text(
            temporary_path / "package-manifest.json",
            json.dumps(package_manifest, indent=2, sort_keys=True) + "\n",
        )
        sum_targets = sorted([*files.keys(), "package-manifest.json"])
        sums = "".join(
            f"{sha256_file(temporary_path / name)}  {name}\n"
            for name in sum_targets
        )
        write_text(temporary_path / "SHA256SUMS", sums)
        rename_with_transient_retry(temporary_path, output_dir)
        output_created_by_this_run = True
        validate_package(manifest_path, output_dir)
        if make_zip:
            create_deterministic_zip(output_dir, zip_path)
    except Exception:
        if temporary_path.exists():
            shutil.rmtree(temporary_path)
        if output_created_by_this_run and output_dir.exists():
            # Only remove output created by this invocation and only if it carries
            # the package marker. Never remove an arbitrary pre-existing directory.
            marker = output_dir / "package-manifest.json"
            if marker.is_file():
                shutil.rmtree(output_dir)
        if make_zip:
            try:
                zip_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    print(f"FPGA package created: {output_dir}")
    print(f"  classification: {classification}")
    print(f"  attestation: {evidence['attestation']}")
    print(f"  release review required: {evidence['release_review_required']}")
    print(f"  loadable: {evidence['loadable']}")
    print(f"  OS: {manifest['target']['os_release']}")
    print(f"  model: {manifest['target']['model']}")
    print(f"  artifact: {manifest['fpga']['artifact_name']}")
    print(f"  sha256: {sha256_file(output_dir / manifest['fpga']['artifact_name'])}")
    if make_zip:
        print(f"  ZIP: {zip_path}")
        print(f"  ZIP sha256: {sha256_file(zip_path)}")


def parse_sha256sums(path: Path) -> dict[str, str]:
    require_regular_file(path, "SHA256SUMS")
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PackageError(f"cannot read {path}: {exc}") from exc
    for index, line in enumerate(lines, start=1):
        if len(line) < 67 or line[64:66] != "  ":
            raise PackageError(f"invalid SHA256SUMS line {index}")
        digest, name = line[:64], line[66:]
        if any(char not in "0123456789abcdef" for char in digest):
            raise PackageError(f"invalid SHA-256 on SHA256SUMS line {index}")
        if not name or Path(name).name != name or name in result:
            raise PackageError(f"invalid/duplicate filename on SHA256SUMS line {index}")
        result[name] = digest
    return result


def validate_package(manifest_path: Path, package_dir: Path) -> None:
    manifest_path = manifest_path.resolve()
    package_dir = package_dir.resolve()
    manifest, os_profile = validate_project_for_fpga(manifest_path)
    if not package_dir.is_dir():
        raise PackageError(f"package directory does not exist: {package_dir}")

    package_manifest_path = package_dir / "package-manifest.json"
    package_manifest = load_json(package_manifest_path, "package manifest")
    if set(package_manifest) != PACKAGE_MANIFEST_KEYS:
        raise PackageError(
            "package-manifest.json must contain exactly "
            f"{sorted(PACKAGE_MANIFEST_KEYS)}"
        )
    if package_manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise PackageError(
            "package-manifest.json schema_version must equal "
            f"{PACKAGE_SCHEMA_VERSION}"
        )
    if package_manifest.get("deterministic") is not True:
        raise PackageError("package-manifest.json deterministic must be true")
    expected_target = {
        "os_release": manifest["target"]["os_release"],
        "model": manifest["target"]["model"],
        "vivado_model": VIVADO_MODEL_BY_PROFILE[manifest["target"]["model"]],
    }
    if package_manifest.get("target") != expected_target:
        raise PackageError("package target does not match project manifest")

    package_fpga = package_manifest.get("fpga")
    if not isinstance(package_fpga, dict):
        raise PackageError("package-manifest.json fpga must be an object")
    expected_fpga = {
        "project": manifest["fpga"]["project"],
        "artifact_name": manifest["fpga"]["artifact_name"],
        "device_tree_name": manifest["fpga"]["device_tree_name"],
        "loader": manifest["fpga"]["loader"],
        "version_register": manifest["fpga"].get("version_register"),
        "magic_value": manifest["fpga"].get("magic_value"),
        "clock_hz": manifest["fpga"].get("clock_hz"),
    }
    if package_fpga != expected_fpga:
        raise PackageError("package FPGA metadata does not match project manifest")

    files = package_manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise PackageError("package-manifest.json files must be a nonempty object")
    expected_payload = {
        os_profile["fpga_artifact_filename"],
        "redpitaya-project.json",
    }
    if manifest["fpga"]["device_tree_name"] is not None:
        expected_payload.add(os_profile["fpga_device_tree_filename"])
    if manifest["fpga"]["register_contract"] is not None:
        expected_payload.add("register-contract.json")
    expected_payload.update(EVIDENCE_FILENAMES)
    classification = package_manifest.get("classification")
    attestation = package_manifest.get("attestation")
    release_review_required = package_manifest.get("release_review_required")
    loadable = package_manifest.get("loadable")
    if classification not in {"release-candidate", "test-fixture"}:
        raise PackageError("package classification is invalid")
    if attestation != fpga_build_evidence.ATTESTATION:
        raise PackageError("package attestation must be self-attested")
    if release_review_required is not (classification == "release-candidate"):
        raise PackageError(
            "package release_review_required flag conflicts with classification"
        )
    if loadable is not False:
        raise PackageError(
            "offline package loadable must always be false; authorization is external"
        )
    if classification == "test-fixture":
        expected_payload.add(DO_NOT_LOAD_FILENAME)
    else:
        expected_payload.add(RELEASE_REVIEW_REQUIRED_FILENAME)
    if set(files) != expected_payload:
        raise PackageError(
            f"package payload mismatch: expected {sorted(expected_payload)}, "
            f"found {sorted(files)}"
        )

    actual_regular_files = {
        path.name for path in package_dir.iterdir() if path.is_file()
    }
    expected_regular_files = expected_payload | {
        "package-manifest.json",
        "SHA256SUMS",
    }
    if actual_regular_files != expected_regular_files:
        raise PackageError(
            f"unexpected/missing package files: expected "
            f"{sorted(expected_regular_files)}, found {sorted(actual_regular_files)}"
        )
    if any(path.is_dir() or path.is_symlink() for path in package_dir.iterdir()):
        raise PackageError(
            "package directory must contain regular, non-symlink files only"
        )

    for name, record in files.items():
        if not isinstance(record, dict) or set(record) != FILE_RECORD_KEYS:
            raise PackageError(f"invalid file record for {name}")
        path = package_dir / name
        require_regular_file(path, f"package file {name}")
        expected_hash = record.get("sha256")
        expected_size = record.get("size")
        source_name = record.get("source_name")
        if (
            not isinstance(source_name, str)
            or not source_name
            or "\x00" in source_name
        ):
            raise PackageError(f"invalid source_name for {name}")
        if (
            isinstance(expected_size, bool)
            or not isinstance(expected_size, int)
            or expected_size < 1
        ):
            raise PackageError(f"invalid size record for {name}")
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(char not in "0123456789abcdef" for char in expected_hash)
        ):
            raise PackageError(f"invalid SHA-256 record for {name}")
        if sha256_file(path) != expected_hash:
            raise PackageError(f"SHA-256 mismatch for {name}")
        if path.stat().st_size != expected_size:
            raise PackageError(f"size mismatch for {name}")

    bitstream_path = package_dir / os_profile["fpga_artifact_filename"]
    validate_bitstream(bitstream_path)
    if any(path.name.lower().endswith(".bit") for path in package_dir.iterdir()):
        raise PackageError("raw .bit file found in package")

    if manifest["fpga"]["device_tree_name"] is not None:
        validate_dtbo(package_dir / os_profile["fpga_device_tree_filename"])

    if (package_dir / "redpitaya-project.json").read_bytes() != manifest_path.read_bytes():
        raise PackageError("packaged redpitaya-project.json differs from input manifest")

    if manifest["fpga"]["register_contract"] is not None:
        validate_register_contract_against_manifest(
            package_dir / "register-contract.json", manifest
        )
    try:
        packaged_evidence = fpga_build_evidence.validate_evidence(
            package_dir / "build-evidence.json",
            package_dir / "redpitaya-project.json",
            bitstream_path,
            package_dir / os_profile["fpga_device_tree_filename"],
            package_dir / "register-contract.json",
            allow_packaged_names=True,
        )
    except fpga_build_evidence.EvidenceError as exc:
        raise PackageError(f"packaged build evidence is invalid: {exc}") from exc
    if packaged_evidence["classification"] != classification:
        raise PackageError("package classification differs from build evidence")
    if packaged_evidence["attestation"] != attestation:
        raise PackageError("package attestation differs from build evidence")
    if packaged_evidence["release_review_required"] is not release_review_required:
        raise PackageError(
            "package release_review_required differs from build evidence"
        )
    if packaged_evidence["loadable"] is not loadable:
        raise PackageError("package loadable differs from build evidence")
    fixture_marker = package_dir / DO_NOT_LOAD_FILENAME
    review_marker = package_dir / RELEASE_REVIEW_REQUIRED_FILENAME
    if classification == "test-fixture":
        require_regular_file(fixture_marker, DO_NOT_LOAD_FILENAME)
        if "NOT A LOADABLE" not in fixture_marker.read_text(encoding="utf-8"):
            raise PackageError("test-fixture safety marker is invalid")
        if review_marker.exists():
            raise PackageError(
                "test-fixture package must not contain release-review marker"
            )
    else:
        require_regular_file(
            review_marker,
            RELEASE_REVIEW_REQUIRED_FILENAME,
        )
        marker_text = review_marker.read_text(encoding="utf-8")
        if (
            "NOT AUTHORIZED TO LOAD" not in marker_text
            or "deployment-bound" not in marker_text
        ):
            raise PackageError("release-review safety marker is invalid")
        if fixture_marker.exists():
            raise PackageError(
                "release-candidate package must not contain DO_NOT_LOAD.txt"
            )

    sums = parse_sha256sums(package_dir / "SHA256SUMS")
    expected_sum_names = expected_payload | {"package-manifest.json"}
    if set(sums) != expected_sum_names:
        raise PackageError("SHA256SUMS filename set is incorrect")
    for name, expected_hash in sums.items():
        if sha256_file(package_dir / name) != expected_hash:
            raise PackageError(f"SHA256SUMS mismatch for {name}")

    print(f"FPGA package structurally valid: {package_dir}")
    print(f"  classification: {classification}")
    print(f"  attestation: {attestation}")
    print(f"  release review required: {release_review_required}")
    print(f"  loadable: {loadable}")
    print(f"  OS: {manifest['target']['os_release']}")
    print(f"  model: {manifest['target']['model']}")
    print(f"  files: {len(expected_regular_files)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or validate an OS-specific Red Pitaya FPGA package."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    package_parser = subparsers.add_parser("package", help="create a new package")
    package_parser.add_argument("--manifest", type=Path, required=True)
    package_parser.add_argument("--bitstream", type=Path, required=True)
    package_parser.add_argument("--output-dir", type=Path, required=True)
    package_parser.add_argument("--dtbo", type=Path)
    package_parser.add_argument("--register-contract", type=Path)
    package_parser.add_argument(
        "--build-evidence",
        type=Path,
        required=True,
        help="path to build-evidence.json created by fpga_build_evidence.py",
    )
    package_parser.add_argument("--zip", action="store_true", dest="make_zip")

    validate_parser = subparsers.add_parser(
        "validate", help="validate an existing package"
    )
    validate_parser.add_argument("--manifest", type=Path, required=True)
    validate_parser.add_argument("--package-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "package":
            package(
                args.manifest,
                args.bitstream,
                args.output_dir,
                args.dtbo,
                args.register_contract,
                args.build_evidence,
                args.make_zip,
            )
        else:
            validate_package(args.manifest, args.package_dir)
        return 0
    except (PackageError, RegisterContractError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR filesystem operation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
