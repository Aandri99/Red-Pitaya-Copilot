#!/usr/bin/env python3
"""Validate a Red Pitaya register contract and generate language bindings.

This script intentionally uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ACCESS_MODES = {"ro", "rw", "wo", "w1c", "w1s", "rc", "pulse"}
TOP_KEYS = {
    "schema_version",
    "namespace",
    "description",
    "base_address",
    "address_span",
    "bus_width_bits",
    "endianness",
    "registers",
}
REGISTER_KEYS = {
    "name",
    "offset",
    "access",
    "reset",
    "description",
    "units",
    "fields",
}
FIELD_KEYS = {
    "name",
    "lsb",
    "width",
    "description",
    "units",
    "signed",
    "enum",
}
STRICT_REGISTERS = {
    "MAGIC": "ro",
    "ABI_VERSION": "ro",
    "CAPABILITIES": "ro",
    "CONTROL": "rw",
    "STATUS": "ro",
}
NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


@dataclass(frozen=True)
class Finding:
    path: str
    message: str

    def format(self) -> str:
        return f"{self.path}: {self.message}"


class ContractError(Exception):
    """Raised for a user-facing contract or IO error."""


def parse_integer(value: Any, path: str) -> int:
    if isinstance(value, bool):
        raise ContractError(f"{path}: boolean is not an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 0)
        except ValueError as exc:
            raise ContractError(
                f"{path}: expected an integer or 0x-prefixed integer string"
            ) from exc
    raise ContractError(f"{path}: expected an integer or integer string")


def load_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError(
            f"{path}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise ContractError("$: register contract must be a JSON object")
    return data


def add_unknown_key_findings(
    findings: list[Finding], obj: dict[str, Any], allowed: set[str], path: str
) -> None:
    for key in sorted(set(obj) - allowed):
        findings.append(Finding(f"{path}.{key}", "unknown property"))


def require_keys(
    findings: list[Finding], obj: dict[str, Any], required: set[str], path: str
) -> None:
    for key in sorted(required - set(obj)):
        findings.append(Finding(f"{path}.{key}", "required property is missing"))


def validate_contract(data: dict[str, Any], strict: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    add_unknown_key_findings(findings, data, TOP_KEYS, "$")
    require_keys(
        findings,
        data,
        {
            "schema_version",
            "namespace",
            "base_address",
            "address_span",
            "bus_width_bits",
            "registers",
        },
        "$",
    )

    if data.get("schema_version") != 1:
        findings.append(Finding("$.schema_version", "must equal 1"))

    namespace = data.get("namespace")
    if not isinstance(namespace, str) or not NAMESPACE_RE.fullmatch(namespace):
        findings.append(
            Finding(
                "$.namespace",
                "must match ^[a-z][a-z0-9_]{1,31}$",
            )
        )

    description = data.get("description")
    if description is not None and (
        not isinstance(description, str) or not description.strip()
    ):
        findings.append(Finding("$.description", "must be a nonempty string"))

    try:
        base_address = parse_integer(data.get("base_address"), "$.base_address")
    except ContractError as exc:
        findings.append(Finding("$.base_address", str(exc).split(": ", 1)[-1]))
        base_address = -1
    try:
        address_span = parse_integer(data.get("address_span"), "$.address_span")
    except ContractError as exc:
        findings.append(Finding("$.address_span", str(exc).split(": ", 1)[-1]))
        address_span = -1

    if base_address < 0 or base_address > 0xFFFFFFFF:
        findings.append(
            Finding("$.base_address", "must fit an unsigned 32-bit physical address")
        )
    elif base_address % 0x1000:
        findings.append(Finding("$.base_address", "must be 4 KiB aligned"))

    if address_span <= 0 or address_span > 0x100000000:
        findings.append(Finding("$.address_span", "must be between 1 and 2^32"))
    elif address_span & (address_span - 1):
        findings.append(Finding("$.address_span", "must be a power of two"))

    bus_width = data.get("bus_width_bits")
    if bus_width not in {8, 16, 32, 64}:
        findings.append(
            Finding("$.bus_width_bits", "must be one of 8, 16, 32 or 64")
        )
        bus_width = 32
    register_bytes = bus_width // 8

    if data.get("endianness", "little") != "little":
        findings.append(
            Finding("$.endianness", "only little-endian AXI register maps are supported")
        )

    registers = data.get("registers")
    if not isinstance(registers, list) or not registers:
        findings.append(Finding("$.registers", "must be a nonempty array"))
        return findings

    names: set[str] = set()
    offsets: set[int] = set()
    registers_by_name: dict[str, dict[str, Any]] = {}

    for index, register in enumerate(registers):
        path = f"$.registers[{index}]"
        if not isinstance(register, dict):
            findings.append(Finding(path, "must be an object"))
            continue
        add_unknown_key_findings(findings, register, REGISTER_KEYS, path)
        require_keys(
            findings,
            register,
            {"name", "offset", "access", "reset", "description", "fields"},
            path,
        )

        name = register.get("name")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            findings.append(
                Finding(f"{path}.name", "must match ^[A-Z][A-Z0-9_]{0,63}$")
            )
        else:
            if name in names:
                findings.append(Finding(f"{path}.name", f"duplicate register {name}"))
            names.add(name)
            registers_by_name[name] = register

        try:
            offset = parse_integer(register.get("offset"), f"{path}.offset")
        except ContractError as exc:
            findings.append(Finding(f"{path}.offset", str(exc).split(": ", 1)[-1]))
            offset = -1
        if offset < 0:
            findings.append(Finding(f"{path}.offset", "must not be negative"))
        elif offset % register_bytes:
            findings.append(
                Finding(
                    f"{path}.offset",
                    f"must be aligned to {register_bytes} bytes",
                )
            )
        elif address_span > 0 and offset + register_bytes > address_span:
            findings.append(Finding(f"{path}.offset", "register exceeds address_span"))
        elif offset in offsets:
            findings.append(
                Finding(f"{path}.offset", f"duplicate offset 0x{offset:X}")
            )
        offsets.add(offset)

        access = register.get("access")
        if access not in ACCESS_MODES:
            findings.append(
                Finding(
                    f"{path}.access",
                    "must be one of " + ", ".join(sorted(ACCESS_MODES)),
                )
            )

        try:
            reset = parse_integer(register.get("reset"), f"{path}.reset")
        except ContractError as exc:
            findings.append(Finding(f"{path}.reset", str(exc).split(": ", 1)[-1]))
            reset = -1
        max_register_value = (1 << bus_width) - 1
        if reset < 0 or reset > max_register_value:
            findings.append(
                Finding(
                    f"{path}.reset",
                    f"must fit unsigned {bus_width}-bit register",
                )
            )

        desc = register.get("description")
        if not isinstance(desc, str) or not desc.strip():
            findings.append(Finding(f"{path}.description", "must be nonempty"))
        units = register.get("units")
        if units is not None and (
            not isinstance(units, str) or not units.strip()
        ):
            findings.append(Finding(f"{path}.units", "must be a nonempty string"))

        fields = register.get("fields")
        if not isinstance(fields, list):
            findings.append(Finding(f"{path}.fields", "must be an array"))
            continue
        occupied = 0
        field_names: set[str] = set()
        for field_index, field in enumerate(fields):
            field_path = f"{path}.fields[{field_index}]"
            if not isinstance(field, dict):
                findings.append(Finding(field_path, "must be an object"))
                continue
            add_unknown_key_findings(findings, field, FIELD_KEYS, field_path)
            require_keys(
                findings,
                field,
                {"name", "lsb", "width", "description"},
                field_path,
            )
            field_name = field.get("name")
            if not isinstance(field_name, str) or not NAME_RE.fullmatch(field_name):
                findings.append(
                    Finding(
                        f"{field_path}.name",
                        "must match ^[A-Z][A-Z0-9_]{0,63}$",
                    )
                )
            else:
                if field_name in field_names:
                    findings.append(
                        Finding(
                            f"{field_path}.name",
                            f"duplicate field {field_name}",
                        )
                    )
                field_names.add(field_name)
            try:
                lsb = parse_integer(field.get("lsb"), f"{field_path}.lsb")
                width = parse_integer(field.get("width"), f"{field_path}.width")
            except ContractError as exc:
                findings.append(Finding(field_path, str(exc)))
                continue
            if lsb < 0:
                findings.append(Finding(f"{field_path}.lsb", "must not be negative"))
            if width <= 0:
                findings.append(Finding(f"{field_path}.width", "must be positive"))
            if lsb >= 0 and width > 0:
                if lsb + width > bus_width:
                    findings.append(
                        Finding(
                            field_path,
                            f"field exceeds {bus_width}-bit register",
                        )
                    )
                else:
                    mask = ((1 << width) - 1) << lsb
                    if mask & occupied:
                        findings.append(
                            Finding(field_path, "field overlaps another field")
                        )
                    occupied |= mask
            field_desc = field.get("description")
            if not isinstance(field_desc, str) or not field_desc.strip():
                findings.append(
                    Finding(f"{field_path}.description", "must be nonempty")
                )
            if "signed" in field and not isinstance(field["signed"], bool):
                findings.append(Finding(f"{field_path}.signed", "must be boolean"))
            enum = field.get("enum")
            if enum is not None:
                if not isinstance(enum, dict) or not enum:
                    findings.append(
                        Finding(f"{field_path}.enum", "must be a nonempty object")
                    )
                else:
                    seen_enum_values: set[int] = set()
                    for enum_name, enum_value in enum.items():
                        if not NAME_RE.fullmatch(enum_name):
                            findings.append(
                                Finding(
                                    f"{field_path}.enum.{enum_name}",
                                    "enum name must be upper snake case",
                                )
                            )
                        try:
                            parsed = parse_integer(
                                enum_value, f"{field_path}.enum.{enum_name}"
                            )
                        except ContractError as exc:
                            findings.append(
                                Finding(
                                    f"{field_path}.enum.{enum_name}",
                                    str(exc).split(": ", 1)[-1],
                                )
                            )
                            continue
                        if parsed < 0 or parsed >= (1 << max(width, 0)):
                            findings.append(
                                Finding(
                                    f"{field_path}.enum.{enum_name}",
                                    "enum value does not fit field",
                                )
                            )
                        if parsed in seen_enum_values:
                            findings.append(
                                Finding(
                                    f"{field_path}.enum.{enum_name}",
                                    "duplicate enum value",
                                )
                            )
                        seen_enum_values.add(parsed)

    if strict:
        for required_name, expected_access in STRICT_REGISTERS.items():
            register = registers_by_name.get(required_name)
            if register is None:
                findings.append(
                    Finding(
                        "$.registers",
                        f"strict mode requires {required_name}",
                    )
                )
            elif register.get("access") != expected_access:
                findings.append(
                    Finding(
                        f"$.registers.{required_name}.access",
                        f"strict mode requires {expected_access}",
                    )
                )
        control = registers_by_name.get("CONTROL")
        if control is not None:
            try:
                if parse_integer(control.get("reset"), "$.registers.CONTROL.reset") != 0:
                    findings.append(
                        Finding(
                            "$.registers.CONTROL.reset",
                            "strict mode requires safe zero control reset",
                        )
                    )
            except ContractError:
                pass
        magic = registers_by_name.get("MAGIC")
        if magic is not None:
            try:
                if parse_integer(magic.get("reset"), "$.registers.MAGIC.reset") == 0:
                    findings.append(
                        Finding(
                            "$.registers.MAGIC.reset",
                            "strict mode requires a nonzero magic value",
                        )
                    )
            except ContractError:
                pass

    return findings


def normalize_contract(data: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(data))
    result.setdefault("endianness", "little")
    result["base_address"] = f"0x{parse_integer(result['base_address'], '$'):08X}"
    result["address_span"] = f"0x{parse_integer(result['address_span'], '$'):08X}"
    for register in result["registers"]:
        register["offset"] = f"0x{parse_integer(register['offset'], '$'):04X}"
        width = int(result["bus_width_bits"])
        digits = (width + 3) // 4
        register["reset"] = f"0x{parse_integer(register['reset'], '$'):0{digits}X}"
    result["registers"].sort(key=lambda item: parse_integer(item["offset"], "$"))
    return result


def hex_literal(value: int, bits: int) -> str:
    return f"{value:0{(bits + 3) // 4}X}"


def canonical_contract_sha256(data: dict[str, Any]) -> str:
    canonical = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def generate_systemverilog(data: dict[str, Any], contract_sha256: str) -> str:
    prefix = data["namespace"].upper()
    bits = data["bus_width_bits"]
    guard = f"{prefix}_REGS_SVH"
    lines = [
        "// Generated by register_contract.py; do not edit.",
        f"// Canonical contract SHA-256: {contract_sha256}",
        f"`ifndef {guard}",
        f"`define {guard}",
        "",
        f"localparam logic [31:0] {prefix}_BASE_ADDRESS = 32'h{hex_literal(parse_integer(data['base_address'], '$'), 32)};",
        f"localparam logic [31:0] {prefix}_ADDRESS_SPAN = 32'h{hex_literal(parse_integer(data['address_span'], '$'), 32)};",
        "",
    ]
    for register in data["registers"]:
        name = register["name"]
        offset = parse_integer(register["offset"], "$")
        reset = parse_integer(register["reset"], "$")
        lines.append(
            f"localparam logic [31:0] {prefix}_REG_{name} = 32'h{hex_literal(offset, 32)};"
        )
        lines.append(
            f"localparam logic [{bits - 1}:0] {prefix}_RESET_{name} = {bits}'h{hex_literal(reset, bits)};"
        )
        for field in register["fields"]:
            field_name = field["name"]
            lsb = parse_integer(field["lsb"], "$")
            width = parse_integer(field["width"], "$")
            mask = ((1 << width) - 1) << lsb
            lines.append(
                f"localparam int unsigned {prefix}_{name}_{field_name}_LSB = {lsb};"
            )
            lines.append(
                f"localparam logic [{bits - 1}:0] {prefix}_{name}_{field_name}_MASK = {bits}'h{hex_literal(mask, bits)};"
            )
        lines.append("")
    lines.extend([f"`endif // {guard}", ""])
    return "\n".join(lines)


def generate_c_header(data: dict[str, Any], contract_sha256: str) -> str:
    prefix = data["namespace"].upper()
    guard = f"{prefix}_REGS_H"
    bits = data["bus_width_bits"]
    uint_macro = f"UINT{bits}_C"
    lines = [
        "/* Generated by register_contract.py; do not edit. */",
        f"/* Canonical contract SHA-256: {contract_sha256} */",
        f"#ifndef {guard}",
        f"#define {guard}",
        "",
        "#include <stdint.h>",
        "",
        f"#define {prefix}_BASE_ADDRESS UINT32_C(0x{hex_literal(parse_integer(data['base_address'], '$'), 32)})",
        f"#define {prefix}_ADDRESS_SPAN UINT32_C(0x{hex_literal(parse_integer(data['address_span'], '$'), 32)})",
        "",
    ]
    for register in data["registers"]:
        name = register["name"]
        offset = parse_integer(register["offset"], "$")
        reset = parse_integer(register["reset"], "$")
        lines.append(
            f"#define {prefix}_REG_{name} UINT32_C(0x{hex_literal(offset, 32)})"
        )
        lines.append(
            f"#define {prefix}_RESET_{name} {uint_macro}(0x{hex_literal(reset, bits)})"
        )
        for field in register["fields"]:
            field_name = field["name"]
            lsb = parse_integer(field["lsb"], "$")
            width = parse_integer(field["width"], "$")
            mask = ((1 << width) - 1) << lsb
            lines.append(f"#define {prefix}_{name}_{field_name}_LSB {lsb}u")
            lines.append(
                f"#define {prefix}_{name}_{field_name}_MASK {uint_macro}(0x{hex_literal(mask, bits)})"
            )
        lines.append("")
    lines.extend([f"#endif /* {guard} */", ""])
    return "\n".join(lines)


def generate_python(data: dict[str, Any], contract_sha256: str) -> str:
    prefix = data["namespace"].upper()
    lines = [
        '"""Generated register constants; do not edit."""',
        "",
        f'CONTRACT_SHA256 = "{contract_sha256}"',
        "",
        f"{prefix}_BASE_ADDRESS = 0x{parse_integer(data['base_address'], '$'):08X}",
        f"{prefix}_ADDRESS_SPAN = 0x{parse_integer(data['address_span'], '$'):08X}",
        "",
    ]
    for register in data["registers"]:
        name = register["name"]
        offset = parse_integer(register["offset"], "$")
        reset = parse_integer(register["reset"], "$")
        lines.append(f"{prefix}_REG_{name} = 0x{offset:08X}")
        lines.append(f"{prefix}_RESET_{name} = 0x{reset:X}")
        for field in register["fields"]:
            field_name = field["name"]
            lsb = parse_integer(field["lsb"], "$")
            width = parse_integer(field["width"], "$")
            mask = ((1 << width) - 1) << lsb
            lines.append(f"{prefix}_{name}_{field_name}_LSB = {lsb}")
            lines.append(f"{prefix}_{name}_{field_name}_MASK = 0x{mask:X}")
        lines.append("")
    return "\n".join(lines)


def markdown_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def generate_markdown(data: dict[str, Any], contract_sha256: str) -> str:
    lines = [
        f"# {data['namespace']} register contract",
        "",
        "_Generated by `register_contract.py`; do not edit._",
        "",
        f"- Canonical contract SHA-256: `{contract_sha256}`",
        f"- Base address: `0x{parse_integer(data['base_address'], '$'):08X}`",
        f"- Address span: `0x{parse_integer(data['address_span'], '$'):X}`",
        f"- Bus width: `{data['bus_width_bits']}` bits",
        f"- Endianness: `{data.get('endianness', 'little')}`",
        "",
        "| Offset | Register | Access | Reset | Description |",
        "|---:|---|---|---:|---|",
    ]
    for register in data["registers"]:
        offset = parse_integer(register["offset"], "$")
        reset = parse_integer(register["reset"], "$")
        lines.append(
            f"| `0x{offset:04X}` | `{register['name']}` | `{register['access']}` | "
            f"`0x{reset:X}` | {markdown_escape(register['description'])} |"
        )
    for register in data["registers"]:
        if not register["fields"]:
            continue
        lines.extend(
            [
                "",
                f"## {register['name']} fields",
                "",
                "| Bits | Field | Description |",
                "|---:|---|---|",
            ]
        )
        for field in sorted(
            register["fields"],
            key=lambda item: parse_integer(item["lsb"], "$"),
            reverse=True,
        ):
            lsb = parse_integer(field["lsb"], "$")
            width = parse_integer(field["width"], "$")
            bit_range = str(lsb) if width == 1 else f"{lsb + width - 1}:{lsb}"
            lines.append(
                f"| `{bit_range}` | `{field['name']}` | "
                f"{markdown_escape(field['description'])} |"
            )
    lines.append("")
    return "\n".join(lines)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ContractError(f"cannot write {path}: {exc}") from exc


def generate_outputs(data: dict[str, Any], output_dir: Path) -> list[Path]:
    normalized = normalize_contract(data)
    namespace = normalized["namespace"]
    contract_sha256 = canonical_contract_sha256(normalized)
    outputs = {
        output_dir / f"{namespace}_regs.svh": generate_systemverilog(
            normalized, contract_sha256
        ),
        output_dir / f"{namespace}_regs.h": generate_c_header(
            normalized, contract_sha256
        ),
        output_dir / f"{namespace}_regs.py": generate_python(
            normalized, contract_sha256
        ),
        output_dir / f"{namespace}_registers.md": generate_markdown(
            normalized, contract_sha256
        ),
        output_dir / f"{namespace}_registers.json": json.dumps(
            normalized, indent=2, sort_keys=True
        )
        + "\n",
        output_dir / f"{namespace}_contract.sha256": (
            f"{contract_sha256}  canonical-json\n"
        ),
    }
    for path, content in outputs.items():
        atomic_write(path, content)
    return sorted(outputs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and generate a Red Pitaya register contract."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="validate a contract")
    validate_parser.add_argument("contract", type=Path)
    validate_parser.add_argument("--strict", action="store_true")
    validate_parser.add_argument("--json", action="store_true", dest="json_output")

    generate_parser = subparsers.add_parser(
        "generate", help="validate and generate language bindings"
    )
    generate_parser.add_argument("contract", type=Path)
    generate_parser.add_argument("--out-dir", type=Path, required=True)
    generate_parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        data = load_json(args.contract)
        findings = validate_contract(data, strict=args.strict)
        if findings:
            if getattr(args, "json_output", False):
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "errors": [
                                {"path": item.path, "message": item.message}
                                for item in findings
                            ],
                        },
                        indent=2,
                    )
                )
            else:
                for item in findings:
                    print(f"ERROR {item.format()}", file=sys.stderr)
                print(
                    f"Register contract invalid: {len(findings)} error(s)",
                    file=sys.stderr,
                )
            return 1
        if args.command == "validate":
            if args.json_output:
                print(json.dumps({"ok": True, "errors": []}, indent=2))
            else:
                print(f"Register contract valid: {args.contract}")
            return 0
        outputs = generate_outputs(data, args.out_dir)
        print(f"Generated {len(outputs)} files in {args.out_dir}:")
        for path in outputs:
            print(f"  {path.name}")
        return 0
    except ContractError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
