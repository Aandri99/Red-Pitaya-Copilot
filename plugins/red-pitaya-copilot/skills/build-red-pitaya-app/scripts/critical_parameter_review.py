#!/usr/bin/env python3
"""Validate and render an instrument's critical-parameter review contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
REQUIRED_TEXT = ("label", "category", "safety_class", "explanation", "change_effect")
NUMERIC_TYPES = {"number", "integer"}


@dataclass(frozen=True)
class ContractResult:
    path: Path
    contract: dict
    sha256: str
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def _valid_value(definition: dict, value: object) -> bool:
    data_type = definition.get("data_type")
    if data_type == "integer":
        valid = type(value) is int
    elif data_type == "number":
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif data_type == "boolean":
        valid = type(value) is bool
    elif data_type == "string":
        valid = isinstance(value, str)
    else:
        return False
    if not valid:
        return False
    if data_type in NUMERIC_TYPES:
        minimum = definition.get("minimum")
        maximum = definition.get("maximum")
        if not isinstance(minimum, (int, float)) or isinstance(minimum, bool):
            return False
        if not isinstance(maximum, (int, float)) or isinstance(maximum, bool):
            return False
        if minimum > maximum or value < minimum or value > maximum:
            return False
    return True


def inspect_contract(app_folder: Path) -> ContractResult:
    root = app_folder.expanduser().resolve()
    path = root / "protocols" / "critical-parameters.json"
    errors: list[str] = []
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return ContractResult(path, {}, "", (f"cannot read contract: {exc}",))
    sha256 = hashlib.sha256(raw).hexdigest()
    try:
        contract = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return ContractResult(path, {}, sha256, (f"invalid UTF-8 JSON: {exc}",))
    if not isinstance(contract, dict):
        return ContractResult(path, {}, sha256, ("contract root must be an object",))

    manifest_path = root / "redpitaya-project.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        manifest = {}
        errors.append(f"cannot read redpitaya-project.json: {exc}")
    manifest_slug = manifest.get("project", {}).get("slug")
    routes = manifest.get("application", {}).get("routes", [])

    if contract.get("schema_version") != 1:
        errors.append("schema_version must equal 1")
    if contract.get("instrument") != manifest_slug or manifest_slug != root.name:
        errors.append("instrument must match the manifest slug and app folder")
    if contract.get("review_required_before_apply") is not True:
        errors.append("review_required_before_apply must be true")

    parameters = contract.get("parameters")
    if not isinstance(parameters, list) or not parameters:
        errors.append("parameters must be a non-empty array")
        parameters = []
    identifiers: set[str] = set()
    editable: dict[str, dict] = {}
    for index, definition in enumerate(parameters):
        prefix = f"parameters[{index}]"
        if not isinstance(definition, dict):
            errors.append(f"{prefix} must be an object")
            continue
        identifier = definition.get("id")
        if not isinstance(identifier, str) or IDENTIFIER_RE.fullmatch(identifier) is None:
            errors.append(f"{prefix}.id must be a lower-case identifier")
            continue
        if identifier in identifiers:
            errors.append(f"duplicate parameter id: {identifier}")
        identifiers.add(identifier)
        for field in REQUIRED_TEXT:
            value = definition.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{prefix}.{field} must be non-empty text")
        if type(definition.get("editable")) is not bool:
            errors.append(f"{prefix}.editable must be boolean")
            continue
        if definition["editable"]:
            editable[identifier] = definition
            if "default" not in definition or not _valid_value(definition, definition.get("default")):
                errors.append(f"{prefix}.default must match its type and range")
            if definition.get("data_type") in NUMERIC_TYPES:
                step = definition.get("step")
                if not isinstance(step, (int, float)) or isinstance(step, bool) or step <= 0:
                    errors.append(f"{prefix}.step must be positive")
        elif "value" not in definition:
            errors.append(f"{prefix}.value is required for fixed parameters")

    presets = contract.get("presets", [])
    if not isinstance(presets, list):
        errors.append("presets must be an array")
        presets = []
    preset_ids: set[str] = set()
    for index, preset in enumerate(presets):
        prefix = f"presets[{index}]"
        if not isinstance(preset, dict):
            errors.append(f"{prefix} must be an object")
            continue
        preset_id = preset.get("id")
        if not isinstance(preset_id, str) or IDENTIFIER_RE.fullmatch(preset_id) is None:
            errors.append(f"{prefix}.id must be a lower-case identifier")
        elif preset_id in preset_ids:
            errors.append(f"duplicate preset id: {preset_id}")
        else:
            preset_ids.add(preset_id)
        if not isinstance(preset.get("label"), str) or not preset["label"].strip():
            errors.append(f"{prefix}.label must be non-empty text")
        if not isinstance(preset.get("description"), str) or not preset["description"].strip():
            errors.append(f"{prefix}.description must be non-empty text")
        values = preset.get("values")
        if not isinstance(values, dict) or not values:
            errors.append(f"{prefix}.values must be a non-empty object")
            continue
        for identifier, value in values.items():
            definition = editable.get(identifier)
            if definition is None:
                errors.append(f"{prefix}.values references non-editable or unknown {identifier}")
            elif not _valid_value(definition, value):
                errors.append(f"{prefix}.values.{identifier} violates its type or range")

    if isinstance(routes, list) and f"/{root.name}/api/configure" in routes:
        if f"/{root.name}/api/parameters" not in routes:
            errors.append("configure route requires a read-only /api/parameters route")

    return ContractResult(path, contract, sha256, tuple(errors))


def render_text(result: ContractResult) -> str:
    lines = [
        f"Critical parameter review: {result.contract.get('instrument', 'unknown')}",
        f"Contract: {result.path}",
        f"SHA-256: {result.sha256}",
        f"Status: {'PASS' if result.ok else 'FAIL'}",
    ]
    for error in result.errors:
        lines.append(f"ERROR: {error}")
    if result.ok:
        for definition in result.contract["parameters"]:
            mode = "runtime" if definition["editable"] else definition["safety_class"]
            value = definition.get("default") if definition["editable"] else definition.get("value")
            units = f" {definition['units']}" if definition.get("units") else ""
            lines.extend(
                (
                    "",
                    f"{definition['label']} [{mode}] = {value}{units}",
                    f"  {definition['explanation']}",
                    f"  Change behavior: {definition['change_effect']}",
                )
            )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate or render protocols/critical-parameters.json."
    )
    parser.add_argument("command", choices=("validate", "review"))
    parser.add_argument("app_folder", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = inspect_contract(args.app_folder)
    if args.json:
        payload = {
            "ok": result.ok,
            "path": str(result.path),
            "contract_hash_sha256": result.sha256,
            "errors": list(result.errors),
        }
        if args.command == "review" and result.ok:
            payload["parameters"] = result.contract["parameters"]
            payload["presets"] = result.contract.get("presets", [])
        print(json.dumps(payload, indent=2))
    else:
        print(render_text(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
