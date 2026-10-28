#!/usr/bin/env python3
"""Create and validate machine-bound Red Pitaya FPGA build evidence.

The tool does not invoke Vivado, Bootgen, SSH, or hardware. It binds already
produced artifacts and reports to an explicit release-candidate or test-fixture
record. Caller claims are kept under ``asserted`` and never become trusted
proof. Deterministic byte checks are kept under ``observed``. Offline output is
always self-attested, release-review-gated and non-loadable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import struct
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import register_contract


EVIDENCE_SCHEMA_VERSION = 2
# A verified XC7Z010 Red Pitaya full Bootgen image is 995,936 bytes.  A 1 MiB
# floor therefore rejects a legitimate uncompressed device image.  Size is only
# a coarse truncation screen; packet structure, target evidence and hashes are
# the real gates.
RELEASE_MIN_BITSTREAM_BYTES = 512 * 1024
TEST_MIN_BITSTREAM_BYTES = 1024
MAX_BITSTREAM_SYNC_OFFSET = 1024 * 1024
MAX_BITSTREAM_PACKET_SCAN_WORDS = 256
MAX_DTBO_BYTES = 16 * 1024 * 1024
XILINX_SYNC_WORD = 0xAA995566
FDT_MAGIC = 0xD00DFEED
FDT_HEADER_BYTES = 40
FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9
ATTESTATION = "self-attested"
ASSERTED_ORIGIN = "caller-supplied-untrusted-claims"
REPORT_NAMES = {
    "simulation": "simulation-report.txt",
    "timing": "timing-report.txt",
    "cdc": "cdc-report.txt",
    "drc": "drc-report.txt",
}
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
SECRET_RE = re.compile(
    r"(password|passwd|secret|private[_-]?key|access[_-]?token|api[_-]?key)",
    re.IGNORECASE,
)
UTC_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
REVISION_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_KEYS = {
    "evidence_schema_version",
    "evidence_kind",
    "classification",
    "attestation",
    "release_review_required",
    "loadable",
    "built_at_utc",
    "target",
    "asserted",
    "observed",
    "artifacts",
    "reports",
}
ASSERTED_SOURCE_KEYS = {"repository", "revision", "dirty"}
ASSERTED_TOOL_KEYS = {"vivado_version", "bootgen_version"}
ASSERTED_BUILD_KEYS = {
    "prj",
    "model",
    "part",
    "command",
    "artifact_name",
}
ASSERTED_VERIFICATION_KEYS = {
    "simulation_result",
    "timing_result",
    "wns_ns",
    "tns_ns",
    "unconstrained_paths",
    "critical_warnings",
    "cdc_review_result",
    "drc_result",
    "device_tree_compile_result",
    "reset_review_result",
    "outputs_default_disabled",
}
ARTIFACT_KEYS = {
    "bitstream",
    "device_tree",
    "manifest",
    "register_contract_source",
    "register_contract_canonical_sha256",
}


class EvidenceError(ValueError):
    """Raised for a user-facing evidence error."""


def _read_bounded(path: Path, maximum_bytes: int, label: str) -> bytes:
    """Read a small engineering artifact without accepting unbounded input."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise EvidenceError(f"cannot inspect {label} {path}: {exc}") from exc
    if size > maximum_bytes:
        raise EvidenceError(
            f"{label} is unexpectedly large ({size}; maximum {maximum_bytes})"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise EvidenceError(f"cannot read {label} {path}: {exc}") from exc


def inspect_xilinx_bitstream(path: Path) -> dict[str, Any]:
    """Perform bounded structural checks; this is not authenticity proof."""
    if not path.is_file():
        raise EvidenceError(f"bitstream file is missing: {path}")
    try:
        with path.open("rb") as stream:
            prefix = stream.read(
                MAX_BITSTREAM_SYNC_OFFSET
                + 4
                + (MAX_BITSTREAM_PACKET_SCAN_WORDS * 4)
            )
    except OSError as exc:
        raise EvidenceError(f"cannot inspect bitstream {path}: {exc}") from exc

    candidates = (
        (XILINX_SYNC_WORD.to_bytes(4, "big"), "big"),
        (XILINX_SYNC_WORD.to_bytes(4, "little"), "little"),
    )
    found: tuple[int, str] | None = None
    for marker, byte_order in candidates:
        start = 0
        while True:
            offset = prefix.find(marker, start)
            if offset < 0:
                break
            if offset > MAX_BITSTREAM_SYNC_OFFSET:
                break
            if offset % 4 == 0:
                if found is None or offset < found[0]:
                    found = (offset, byte_order)
                break
            start = offset + 1
    if found is None:
        raise EvidenceError(
            "bitstream does not contain an aligned Xilinx 7-series "
            "0xAA995566 sync word within the first 1048576 bytes"
        )

    sync_offset, byte_order = found
    packet_bytes = prefix[sync_offset + 4 :]
    words_checked = min(
        len(packet_bytes) // 4,
        MAX_BITSTREAM_PACKET_SCAN_WORDS,
    )
    type1_write_offset: int | None = None
    for index in range(words_checked):
        start = index * 4
        word = int.from_bytes(packet_bytes[start : start + 4], byte_order)
        packet_type = (word >> 29) & 0x7
        opcode = (word >> 27) & 0x3
        word_count = word & 0x7FF
        if packet_type == 1 and opcode == 2 and word_count > 0:
            type1_write_offset = sync_offset + 4 + start
            break
    if type1_write_offset is None:
        raise EvidenceError(
            "bitstream sync word is not followed by a Xilinx type-1 write "
            f"packet within {words_checked} words"
        )

    return {
        "format": "xilinx-7series-configuration-binary",
        "check": "aligned-sync-word-and-type1-write-header",
        "sync_word": "0xAA995566",
        "sync_word_offset": sync_offset,
        "word_byte_order": byte_order,
        "type1_write_header_offset": type1_write_offset,
        "packet_words_checked": words_checked,
    }


def _fdt_u32(data: bytes, offset: int, label: str) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise EvidenceError(f"device-tree overlay has truncated {label}")
    return struct.unpack_from(">I", data, offset)[0]


def _fdt_c_string(data: bytes, start: int, end: int, label: str) -> bytes:
    if start < 0 or start >= end:
        raise EvidenceError(f"device-tree overlay has invalid {label} offset")
    nul = data.find(b"\x00", start, end)
    if nul < 0:
        raise EvidenceError(f"device-tree overlay has unterminated {label}")
    return data[start:nul]


def inspect_fdt(path: Path) -> dict[str, Any]:
    """Parse enough of a DTBO/FDT to reject magic-only or malformed blobs."""
    data = _read_bounded(path, MAX_DTBO_BYTES, "device-tree overlay")
    if len(data) < FDT_HEADER_BYTES:
        raise EvidenceError(
            f"device-tree overlay is too small ({len(data)}; minimum 40)"
        )
    (
        magic,
        total_size,
        off_struct,
        off_strings,
        off_mem_rsvmap,
        version,
        last_compatible_version,
        boot_cpuid_phys,
        size_strings,
        size_struct,
    ) = struct.unpack_from(">10I", data, 0)
    if magic != FDT_MAGIC:
        raise EvidenceError(
            "device-tree overlay does not start with FDT magic d00dfeed"
        )
    if total_size != len(data):
        raise EvidenceError(
            "device-tree overlay header totalsize does not equal file size"
        )
    if version != 17 or last_compatible_version != 16:
        raise EvidenceError(
            "device-tree overlay must use FDT version 17 and "
            "last_comp_version 16"
        )
    if off_struct % 4 or off_mem_rsvmap % 8:
        raise EvidenceError("device-tree overlay section alignment is invalid")
    if min(off_struct, off_strings, off_mem_rsvmap) < FDT_HEADER_BYTES:
        raise EvidenceError("device-tree overlay section overlaps its header")
    struct_end = off_struct + size_struct
    strings_end = off_strings + size_strings
    if (
        size_struct < 16
        or size_strings < 1
        or struct_end > total_size
        or strings_end > total_size
    ):
        raise EvidenceError("device-tree overlay section bounds are invalid")
    if max(off_struct, off_strings) < min(struct_end, strings_end):
        raise EvidenceError("device-tree overlay structure and strings overlap")

    reserve_position = off_mem_rsvmap
    reserve_entries = 0
    reserve_end: int | None = None
    while reserve_position + 16 <= total_size:
        address, size = struct.unpack_from(">QQ", data, reserve_position)
        reserve_position += 16
        if address == 0 and size == 0:
            reserve_end = reserve_position
            break
        reserve_entries += 1
    if reserve_end is None:
        raise EvidenceError(
            "device-tree overlay reserve map has no zero terminator"
        )
    for section_start, section_end, label in (
        (off_struct, struct_end, "structure"),
        (off_strings, strings_end, "strings"),
    ):
        if max(off_mem_rsvmap, section_start) < min(reserve_end, section_end):
            raise EvidenceError(
                f"device-tree overlay reserve map overlaps {label} section"
            )

    position = off_struct
    depth = 0
    root_seen = False
    end_seen = False
    node_count = 0
    property_count = 0
    while position < struct_end:
        token_offset = position
        token = _fdt_u32(data, position, "structure token")
        position += 4
        if token == FDT_BEGIN_NODE:
            name = _fdt_c_string(
                data,
                position,
                struct_end,
                "node name",
            )
            position += len(name) + 1
            position = (position + 3) & ~3
            if position > struct_end:
                raise EvidenceError(
                    "device-tree overlay node name exceeds structure bounds"
                )
            if not root_seen:
                if name != b"" or depth != 0:
                    raise EvidenceError(
                        "device-tree overlay first structure node must be root"
                    )
                root_seen = True
            elif depth == 0:
                raise EvidenceError(
                    "device-tree overlay contains a second root node"
                )
            depth += 1
            node_count += 1
        elif token == FDT_END_NODE:
            if depth < 1:
                raise EvidenceError(
                    "device-tree overlay has unmatched FDT_END_NODE"
                )
            depth -= 1
        elif token == FDT_PROP:
            if depth < 1 or position + 8 > struct_end:
                raise EvidenceError(
                    "device-tree overlay has malformed FDT_PROP"
                )
            value_length, name_offset = struct.unpack_from(">II", data, position)
            position += 8
            if value_length > struct_end - position:
                raise EvidenceError(
                    "device-tree overlay property value exceeds structure bounds"
                )
            if name_offset >= size_strings:
                raise EvidenceError(
                    "device-tree overlay property name offset exceeds strings"
                )
            _fdt_c_string(
                data,
                off_strings + name_offset,
                strings_end,
                "property name",
            )
            position += value_length
            position = (position + 3) & ~3
            if position > struct_end:
                raise EvidenceError(
                    "device-tree overlay property padding exceeds structure bounds"
                )
            property_count += 1
        elif token == FDT_NOP:
            continue
        elif token == FDT_END:
            if not root_seen or depth != 0:
                raise EvidenceError(
                    "device-tree overlay ends before all nodes are closed"
                )
            if any(data[position:struct_end]):
                raise EvidenceError(
                    "device-tree overlay has nonzero data after FDT_END"
                )
            end_seen = True
            position = struct_end
        else:
            raise EvidenceError(
                "device-tree overlay has unknown structure token "
                f"0x{token:08x} at byte {token_offset}"
            )
    if not end_seen:
        raise EvidenceError("device-tree overlay has no FDT_END token")
    if property_count < 1:
        raise EvidenceError(
            "device-tree overlay contains no properties and is not usable"
        )

    return {
        "format": "flattened-device-tree",
        "check": "fdt-v17-header-reserve-map-and-token-parse",
        "total_size": total_size,
        "version": version,
        "last_compatible_version": last_compatible_version,
        "boot_cpuid_phys": boot_cpuid_phys,
        "structure_offset": off_struct,
        "structure_size": size_struct,
        "strings_offset": off_strings,
        "strings_size": size_strings,
        "reserve_map_offset": off_mem_rsvmap,
        "reserve_entries": reserve_entries,
        "node_count": node_count,
        "property_count": property_count,
    }


def observe_artifact_structure(
    bitstream_path: Path,
    dtbo_path: Path,
) -> dict[str, Any]:
    return {
        "checker": "offline-structural-parser-v1",
        "bitstream_structure": inspect_xilinx_bitstream(bitstream_path),
        "device_tree_structure": inspect_fdt(dtbo_path),
    }


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[3]


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read valid {label} JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must contain a JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise EvidenceError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise EvidenceError(f"required regular file is missing: {path}")
    size = path.stat().st_size
    if size < 1:
        raise EvidenceError(f"required file is empty: {path}")
    return {
        "name": path.name,
        "size": size,
        "sha256": sha256_file(path),
    }


def parse_bool(value: str) -> bool:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def validate_timestamp(value: str) -> None:
    if not UTC_RE.fullmatch(value):
        raise EvidenceError("built_at_utc must use YYYY-MM-DDTHH:MM:SSZ")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise EvidenceError(f"built_at_utc is not a real UTC timestamp: {exc}") from exc


def reject_secret_text(value: Any, trail: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if SECRET_RE.search(str(key)):
                raise EvidenceError(f"secret-like key is forbidden at {trail}.{key}")
            reject_secret_text(child, f"{trail}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_secret_text(child, f"{trail}[{index}]")
    elif isinstance(value, str):
        lowered = value.lower()
        if "-----begin " in lowered and "private key-----" in lowered:
            raise EvidenceError(f"private-key material is forbidden at {trail}")
        if re.search(r"\b(pass(word|wd)?|secret|token)\s*[:=]\s*\S+", value, re.I):
            raise EvidenceError(f"probable credential is forbidden at {trail}")


def require_exact_keys(
    value: Any,
    expected: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an object")
    if set(value) != expected:
        raise EvidenceError(
            f"{label} must contain exactly {sorted(expected)}"
        )
    return value


def load_target_profiles(manifest: dict[str, Any]) -> tuple[str, str, str, str]:
    target = manifest.get("target")
    fpga = manifest.get("fpga")
    if not isinstance(target, dict) or not isinstance(fpga, dict):
        raise EvidenceError("manifest target and fpga objects are required")
    if fpga.get("enabled") is not True:
        raise EvidenceError("manifest fpga.enabled must be true")
    os_release = str(target.get("os_release", ""))
    model = str(target.get("model", ""))
    if model not in VIVADO_MODEL_BY_PROFILE:
        raise EvidenceError(f"no audited Vivado MODEL mapping for {model!r}")
    profile = read_json(
        plugin_root() / "profiles" / f"os-{os_release}.json",
        "OS profile",
    )
    models = read_json(
        plugin_root() / "profiles" / "board-models.json",
        "board model profile",
    ).get("models")
    if not isinstance(models, dict) or model not in models:
        raise EvidenceError(f"unknown board profile {model!r}")
    zynq = str(models[model].get("zynq", ""))
    artifact_name = str(profile.get("fpga_artifact_filename", ""))
    return os_release, model, zynq, artifact_name


def verify_file_record(
    record: Any,
    path: Path,
    label: str,
    minimum_bytes: int = 1,
    require_name: bool = True,
) -> None:
    if not isinstance(record, dict):
        raise EvidenceError(f"{label} record must be an object")
    expected_keys = {"name", "size", "sha256"}
    if set(record) != expected_keys:
        raise EvidenceError(f"{label} record must contain exactly {sorted(expected_keys)}")
    if not path.is_file():
        raise EvidenceError(f"{label} file is missing: {path}")
    if path.stat().st_size < minimum_bytes:
        raise EvidenceError(
            f"{label} is too small ({path.stat().st_size}; minimum {minimum_bytes})"
        )
    if require_name and record.get("name") != path.name:
        raise EvidenceError(f"{label} name differs from evidence")
    if record.get("size") != path.stat().st_size:
        raise EvidenceError(f"{label} size differs from evidence")
    digest = str(record.get("sha256", ""))
    if not SHA256_RE.fullmatch(digest) or digest != sha256_file(path):
        raise EvidenceError(f"{label} SHA-256 differs from evidence")


def validate_release_gates(evidence: dict[str, Any]) -> None:
    asserted = evidence["asserted"]
    source = asserted["source"]
    tools = asserted["tools"]
    verification = asserted["verification"]
    if source.get("dirty") is not False:
        raise EvidenceError(
            "release-candidate asserted.source.dirty must be false"
        )
    if not REVISION_RE.fullmatch(str(source.get("revision", ""))):
        raise EvidenceError(
            "release-candidate asserted.source.revision must be a 7-64 digit "
            "hexadecimal revision"
        )
    if str(tools.get("vivado_version", "")) != "2020.1":
        raise EvidenceError(
            "release-candidate asserted.tools.vivado_version must be exactly 2020.1"
        )
    if not str(tools.get("bootgen_version", "")).strip():
        raise EvidenceError(
            "release-candidate asserted.tools.bootgen_version is required"
        )
    required_results = {
        "simulation_result": "pass",
        "timing_result": "pass",
        "cdc_review_result": "pass",
        "drc_result": "pass",
        "device_tree_compile_result": "pass",
        "reset_review_result": "pass",
    }
    for key, expected in required_results.items():
        if verification.get(key) != expected:
            raise EvidenceError(
                "release-candidate asserted.verification."
                f"{key} must be {expected!r}"
            )
    wns = verification.get("wns_ns")
    tns = verification.get("tns_ns")
    if (
        isinstance(wns, bool)
        or not isinstance(wns, (int, float))
        or not math.isfinite(wns)
        or wns < 0
    ):
        raise EvidenceError(
            "release-candidate asserted.verification.wns_ns must be >= 0"
        )
    if (
        isinstance(tns, bool)
        or not isinstance(tns, (int, float))
        or not math.isfinite(tns)
        or tns < 0
    ):
        raise EvidenceError(
            "release-candidate asserted.verification.tns_ns must be >= 0"
        )
    if verification.get("unconstrained_paths") != 0:
        raise EvidenceError(
            "release-candidate asserted.verification.unconstrained_paths must equal 0"
        )
    if verification.get("critical_warnings") != []:
        raise EvidenceError(
            "release-candidate asserted.verification.critical_warnings must be empty"
        )
    if verification.get("outputs_default_disabled") is not True:
        raise EvidenceError(
            "release-candidate asserted.verification.outputs_default_disabled "
            "must be true"
        )


def validate_asserted_types(asserted: dict[str, Any]) -> None:
    source = asserted["source"]
    tools = asserted["tools"]
    build = asserted["build"]
    verification = asserted["verification"]
    if not isinstance(source["repository"], str) or not source["repository"].strip():
        raise EvidenceError("asserted.source.repository must be a nonempty string")
    if not isinstance(source["revision"], str) or not source["revision"].strip():
        raise EvidenceError("asserted.source.revision must be a nonempty string")
    if not isinstance(source["dirty"], bool):
        raise EvidenceError("asserted.source.dirty must be boolean")
    for key in ASSERTED_TOOL_KEYS:
        if not isinstance(tools[key], str) or not tools[key].strip():
            raise EvidenceError(f"asserted.tools.{key} must be a nonempty string")
    for key in ASSERTED_BUILD_KEYS:
        if not isinstance(build[key], str) or not build[key].strip():
            raise EvidenceError(f"asserted.build.{key} must be a nonempty string")
    for key in (
        "simulation_result",
        "timing_result",
        "cdc_review_result",
        "drc_result",
        "device_tree_compile_result",
        "reset_review_result",
    ):
        if verification[key] not in {"pass", "fail", "not-run"}:
            raise EvidenceError(
                f"asserted.verification.{key} must be pass, fail or not-run"
            )
    for key in ("wns_ns", "tns_ns"):
        value = verification[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise EvidenceError(
                f"asserted.verification.{key} must be a finite number"
            )
    unconstrained = verification["unconstrained_paths"]
    if (
        isinstance(unconstrained, bool)
        or not isinstance(unconstrained, int)
        or unconstrained < 0
    ):
        raise EvidenceError(
            "asserted.verification.unconstrained_paths must be a nonnegative integer"
        )
    warnings = verification["critical_warnings"]
    if not isinstance(warnings, list) or any(
        not isinstance(item, str) or not item.strip() for item in warnings
    ):
        raise EvidenceError(
            "asserted.verification.critical_warnings must be a list of "
            "nonempty strings"
        )
    if not isinstance(verification["outputs_default_disabled"], bool):
        raise EvidenceError(
            "asserted.verification.outputs_default_disabled must be boolean"
        )


def validate_evidence(
    evidence_path: Path,
    manifest_path: Path,
    bitstream_path: Path,
    dtbo_path: Path,
    register_contract_path: Path,
    allow_packaged_names: bool = False,
) -> dict[str, Any]:
    evidence = read_json(evidence_path, "build evidence")
    reject_secret_text(evidence)
    require_exact_keys(evidence, EVIDENCE_KEYS, "build evidence")
    if evidence.get("evidence_schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise EvidenceError(
            f"evidence_schema_version must equal {EVIDENCE_SCHEMA_VERSION}"
        )
    if evidence.get("evidence_kind") != "red-pitaya-fpga-build-evidence":
        raise EvidenceError(
            "evidence_kind must equal red-pitaya-fpga-build-evidence"
        )
    classification = evidence.get("classification")
    if classification not in {"release-candidate", "test-fixture"}:
        raise EvidenceError("classification must be release-candidate or test-fixture")
    if evidence.get("attestation") != ATTESTATION:
        raise EvidenceError(
            f"attestation must equal {ATTESTATION!r}; offline evidence is not trusted"
        )
    if evidence.get("release_review_required") is not (
        classification == "release-candidate"
    ):
        raise EvidenceError(
            "release_review_required must be true only for release-candidate evidence"
        )
    if evidence.get("loadable") is not False:
        raise EvidenceError(
            "offline build evidence loadable must always be false; loading requires "
            "a separate deployment-bound release-review authorization"
        )
    validate_timestamp(str(evidence.get("built_at_utc", "")))

    manifest = read_json(manifest_path, "project manifest")
    os_release, model, zynq, artifact_name = load_target_profiles(manifest)
    target = evidence.get("target")
    asserted = evidence.get("asserted")
    if not isinstance(target, dict):
        raise EvidenceError("target evidence object is required")
    expected_asserted_keys = {
        "origin",
        "source",
        "tools",
        "build",
        "verification",
    }
    asserted = require_exact_keys(
        asserted,
        expected_asserted_keys,
        "asserted",
    )
    if asserted.get("origin") != ASSERTED_ORIGIN:
        raise EvidenceError(
            f"asserted.origin must equal {ASSERTED_ORIGIN!r}"
        )
    source = require_exact_keys(
        asserted.get("source"),
        ASSERTED_SOURCE_KEYS,
        "asserted.source",
    )
    tools = require_exact_keys(
        asserted.get("tools"),
        ASSERTED_TOOL_KEYS,
        "asserted.tools",
    )
    build = require_exact_keys(
        asserted.get("build"),
        ASSERTED_BUILD_KEYS,
        "asserted.build",
    )
    verification = require_exact_keys(
        asserted.get("verification"),
        ASSERTED_VERIFICATION_KEYS,
        "asserted.verification",
    )
    validate_asserted_types(asserted)
    expected_target = {
        "os_release": os_release,
        "model": model,
        "vivado_model": VIVADO_MODEL_BY_PROFILE[model],
        "zynq": zynq,
    }
    if target != expected_target:
        raise EvidenceError(
            f"evidence target differs from manifest/profile: expected {expected_target}"
        )
    if build.get("prj") != manifest["fpga"]["project"]:
        raise EvidenceError("build.prj differs from manifest fpga.project")
    if build.get("model") != VIVADO_MODEL_BY_PROFILE[model]:
        raise EvidenceError("build.model differs from audited Vivado MODEL")
    if zynq not in str(build.get("part", "")).lower():
        raise EvidenceError(f"build.part must identify the expected {zynq}")
    if build.get("artifact_name") != artifact_name:
        raise EvidenceError(
            f"build.artifact_name must equal OS-specific {artifact_name!r}"
        )
    if not str(build.get("command", "")).strip():
        raise EvidenceError("build.command is required")

    minimum = (
        RELEASE_MIN_BITSTREAM_BYTES
        if classification == "release-candidate"
        else TEST_MIN_BITSTREAM_BYTES
    )
    artifacts = require_exact_keys(
        evidence.get("artifacts"),
        ARTIFACT_KEYS,
        "artifacts",
    )
    verify_file_record(
        artifacts.get("bitstream"),
        bitstream_path,
        "bitstream",
        minimum,
        not allow_packaged_names,
    )
    verify_file_record(
        artifacts.get("device_tree"),
        dtbo_path,
        "device-tree overlay",
        40,
        not allow_packaged_names,
    )
    verify_file_record(
        artifacts.get("manifest"),
        manifest_path,
        "project manifest",
        1,
        not allow_packaged_names,
    )
    verify_file_record(
        artifacts.get("register_contract_source"),
        register_contract_path,
        "register contract",
        1,
        not allow_packaged_names,
    )

    contract = register_contract.load_json(register_contract_path)
    findings = register_contract.validate_contract(contract, strict=True)
    if findings:
        raise EvidenceError("register contract is not strictly valid")
    normalized_contract = register_contract.normalize_contract(contract)
    expected_contract_hash = register_contract.canonical_contract_sha256(
        normalized_contract
    )
    if artifacts.get("register_contract_canonical_sha256") != expected_contract_hash:
        raise EvidenceError("canonical register-contract SHA-256 differs from evidence")

    observed = evidence.get("observed")
    expected_observed = observe_artifact_structure(bitstream_path, dtbo_path)
    if observed != expected_observed:
        raise EvidenceError(
            "observed artifact-structure evidence differs from deterministic parsing"
        )

    reports = evidence.get("reports")
    if not isinstance(reports, dict) or set(reports) != set(REPORT_NAMES):
        raise EvidenceError(
            f"reports must contain exactly {sorted(REPORT_NAMES)}"
        )
    evidence_dir = evidence_path.parent
    for label, expected_name in REPORT_NAMES.items():
        record = reports[label]
        verify_file_record(
            record,
            evidence_dir / expected_name,
            f"{label} report",
        )
        if record.get("name") != expected_name:
            raise EvidenceError(
                f"{label} report must use canonical name {expected_name!r}"
            )

    if classification == "release-candidate":
        validate_release_gates(evidence)
    return evidence


def create_evidence(args: argparse.Namespace) -> Path:
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise EvidenceError(f"output directory already exists: {output_dir}")
    for path, label in (
        (args.manifest, "manifest"),
        (args.bitstream, "bitstream"),
        (args.dtbo, "device-tree overlay"),
        (args.register_contract, "register contract"),
        (args.simulation_report, "simulation report"),
        (args.timing_report, "timing report"),
        (args.cdc_report, "CDC report"),
        (args.drc_report, "DRC report"),
    ):
        if not path.is_file():
            raise EvidenceError(f"{label} is not a regular file: {path}")

    manifest = read_json(args.manifest, "project manifest")
    os_release, model, zynq, artifact_name = load_target_profiles(manifest)
    contract = register_contract.load_json(args.register_contract)
    findings = register_contract.validate_contract(contract, strict=True)
    if findings:
        raise EvidenceError("register contract must pass strict validation")
    normalized_contract = register_contract.normalize_contract(contract)
    validate_timestamp(args.built_at_utc)
    minimum = (
        RELEASE_MIN_BITSTREAM_BYTES
        if args.classification == "release-candidate"
        else TEST_MIN_BITSTREAM_BYTES
    )
    if args.bitstream.stat().st_size < minimum:
        raise EvidenceError(
            f"bitstream is too small ({args.bitstream.stat().st_size}; "
            f"minimum {minimum})"
        )
    observed = observe_artifact_structure(args.bitstream, args.dtbo)

    evidence = {
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "evidence_kind": "red-pitaya-fpga-build-evidence",
        "classification": args.classification,
        "attestation": ATTESTATION,
        "release_review_required": args.classification == "release-candidate",
        "loadable": False,
        "built_at_utc": args.built_at_utc,
        "target": {
            "os_release": os_release,
            "model": model,
            "vivado_model": VIVADO_MODEL_BY_PROFILE[model],
            "zynq": zynq,
        },
        "asserted": {
            "origin": ASSERTED_ORIGIN,
            "source": {
                "repository": args.repository,
                "revision": args.revision,
                "dirty": args.source_dirty,
            },
            "tools": {
                "vivado_version": args.vivado_version,
                "bootgen_version": args.bootgen_version,
            },
            "build": {
                "prj": args.prj,
                "model": args.model,
                "part": args.part,
                "command": args.build_command,
                "artifact_name": artifact_name,
            },
            "verification": {
                "simulation_result": args.simulation_result,
                "timing_result": args.timing_result,
                "wns_ns": args.wns_ns,
                "tns_ns": args.tns_ns,
                "unconstrained_paths": args.unconstrained_paths,
                "critical_warnings": args.critical_warning,
                "cdc_review_result": args.cdc_result,
                "drc_result": args.drc_result,
                "device_tree_compile_result": args.dtc_result,
                "reset_review_result": args.reset_review_result,
                "outputs_default_disabled": args.outputs_default_disabled,
            },
        },
        "observed": observed,
        "artifacts": {
            "bitstream": file_record(args.bitstream),
            "device_tree": file_record(args.dtbo),
            "manifest": file_record(args.manifest),
            "register_contract_source": file_record(args.register_contract),
            "register_contract_canonical_sha256": (
                register_contract.canonical_contract_sha256(normalized_contract)
            ),
        },
        "reports": {},
    }
    reject_secret_text(evidence)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = output_dir.parent / f".{output_dir.name}.staging-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        report_sources = {
            "simulation": args.simulation_report,
            "timing": args.timing_report,
            "cdc": args.cdc_report,
            "drc": args.drc_report,
        }
        for label, source in report_sources.items():
            destination = staging / REPORT_NAMES[label]
            shutil.copyfile(source, destination)
            evidence["reports"][label] = file_record(destination)
        evidence_path = staging / "build-evidence.json"
        evidence_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        validate_evidence(
            evidence_path,
            args.manifest,
            args.bitstream,
            args.dtbo,
            args.register_contract,
        )
        os.replace(staging, output_dir)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    print(
        f"Build evidence created: {output_dir} "
        f"({args.classification}, attestation={evidence['attestation']}, "
        f"release_review_required={evidence['release_review_required']}, "
        f"loadable={evidence['loadable']})"
    )
    return output_dir


def result_choice(value: str) -> str:
    if value not in {"pass", "fail", "not-run"}:
        raise argparse.ArgumentTypeError("expected pass, fail, or not-run")
    return value


def add_common_file_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--bitstream", required=True, type=Path)
    parser.add_argument("--dtbo", required=True, type=Path)
    parser.add_argument("--register-contract", required=True, type=Path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser(
        "create",
        help="bind artifacts and reports into a new evidence directory",
    )
    add_common_file_arguments(create)
    create.add_argument("--output-dir", required=True, type=Path)
    create.add_argument(
        "--classification",
        required=True,
        choices=("release-candidate", "test-fixture"),
    )
    create.add_argument("--built-at-utc", required=True)
    create.add_argument("--repository", required=True)
    create.add_argument("--revision", required=True)
    create.add_argument("--source-dirty", required=True, type=parse_bool)
    create.add_argument("--vivado-version", required=True)
    create.add_argument("--bootgen-version", required=True)
    create.add_argument("--prj", required=True)
    create.add_argument("--model", required=True)
    create.add_argument("--part", required=True)
    create.add_argument("--build-command", required=True)
    create.add_argument("--simulation-report", required=True, type=Path)
    create.add_argument("--timing-report", required=True, type=Path)
    create.add_argument("--cdc-report", required=True, type=Path)
    create.add_argument("--drc-report", required=True, type=Path)
    create.add_argument("--simulation-result", required=True, type=result_choice)
    create.add_argument("--timing-result", required=True, type=result_choice)
    create.add_argument("--wns-ns", required=True, type=float)
    create.add_argument("--tns-ns", required=True, type=float)
    create.add_argument("--unconstrained-paths", required=True, type=int)
    create.add_argument(
        "--critical-warning",
        action="append",
        default=[],
        help="repeat for every unresolved Vivado critical warning",
    )
    create.add_argument("--cdc-result", required=True, type=result_choice)
    create.add_argument("--drc-result", required=True, type=result_choice)
    create.add_argument("--dtc-result", required=True, type=result_choice)
    create.add_argument("--reset-review-result", required=True, type=result_choice)
    create.add_argument(
        "--outputs-default-disabled",
        required=True,
        type=parse_bool,
    )

    validate = subparsers.add_parser(
        "validate",
        help="verify evidence, reports and artifacts remain hash-bound",
    )
    add_common_file_arguments(validate)
    validate.add_argument("--evidence", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            create_evidence(args)
        else:
            evidence = validate_evidence(
                args.evidence,
                args.manifest,
                args.bitstream,
                args.dtbo,
                args.register_contract,
            )
            print(
                "Build evidence valid: "
                f"{args.evidence} ({evidence['classification']}, "
                f"attestation={evidence['attestation']}, "
                f"release_review_required={evidence['release_review_required']}, "
                f"loadable={evidence['loadable']})"
            )
        return 0
    except (EvidenceError, register_contract.ContractError, OSError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
