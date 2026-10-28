"""Dependency-free validation for the plugin's shared project contract.

The implementation intentionally interprets the JSON Schema keywords used by
schemas/redpitaya-project.schema.json. It is not a general JSON Schema library.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = PLUGIN_ROOT / "schemas" / "redpitaya-project.schema.json"
PROFILES_ROOT = PLUGIN_ROOT / "profiles"


class ContractError(ValueError):
    pass


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{path} must contain a JSON object")
    return value


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (not isinstance(value, float) or math.isfinite(value))
        )
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _validate(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    expected_types = schema.get("type")
    if expected_types is not None:
        allowed = (
            [expected_types] if isinstance(expected_types, str) else list(expected_types)
        )
        if not any(_matches_type(value, item) for item in allowed):
            errors.append(f"{path}: expected type {allowed}, got {type(value).__name__}")
            return errors

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']!r}")
    forbidden = schema.get("not")
    if isinstance(forbidden, dict) and not _validate(value, forbidden, path):
        errors.append(f"{path}: matches a forbidden schema")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}.{name}: required property is missing")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    errors.append(f"{path}.{name}: additional property is not allowed")
        for name, child_schema in properties.items():
            if name in value:
                errors.extend(_validate(value[name], child_schema, f"{path}.{name}"))

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: must contain at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: must contain at most {schema['maxItems']} item(s)")
        if schema.get("uniqueItems") is True:
            seen: set[str] = set()
            for index, item in enumerate(value):
                encoded = json.dumps(item, sort_keys=True, separators=(",", ":"))
                if encoded in seen:
                    errors.append(f"{path}[{index}]: duplicate array item")
                seen.add(encoded)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_validate(item, item_schema, f"{path}[{index}]"))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path}: does not match pattern {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(f"{path}: must be finite")
            return errors
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: must be <= {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: must be > {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: must be < {schema['exclusiveMaximum']}")

    for clause in schema.get("allOf", []):
        condition = clause.get("if")
        if condition is None:
            errors.extend(_validate(value, clause, path))
        elif not _validate(value, condition, path):
            then_schema = clause.get("then")
            if isinstance(then_schema, dict):
                errors.extend(_validate(value, then_schema, path))
        else:
            else_schema = clause.get("else")
            if isinstance(else_schema, dict):
                errors.extend(_validate(value, else_schema, path))
    return errors


def validate_manifest_object(manifest: dict[str, Any]) -> None:
    schema = read_json_object(SCHEMA_PATH)
    errors = _validate(manifest, schema, "$")
    if errors:
        raise ContractError(
            "redpitaya-project.json violates the shared contract:\n- "
            + "\n- ".join(errors)
        )
    validate_profile_compatibility(manifest)


def load_and_validate_manifest(path: Path) -> dict[str, Any]:
    manifest = read_json_object(path)
    validate_manifest_object(manifest)
    return manifest


def load_os_profile(os_release: str) -> dict[str, Any]:
    path = PROFILES_ROOT / f"os-{os_release}.json"
    profile = read_json_object(path)
    if profile.get("profile_version") != 1:
        raise ContractError(f"{path} profile_version must equal 1")
    if profile.get("os_release") != os_release:
        raise ContractError(f"{path} os_release does not match its filename")
    return profile


def load_board_profile(model: str) -> dict[str, Any]:
    data = read_json_object(PROFILES_ROOT / "board-models.json")
    if data.get("profile_version") != 1:
        raise ContractError("board-models profile_version must equal 1")
    models = data.get("models")
    if not isinstance(models, dict) or model not in models:
        raise ContractError(f"model {model!r} is absent from board-models.json")
    value = models[model]
    if not isinstance(value, dict):
        raise ContractError(f"board profile {model!r} is invalid")
    return value


def validate_profile_compatibility(manifest: dict[str, Any]) -> None:
    target = manifest["target"]
    application = manifest["application"]
    fpga = manifest["fpga"]
    deployment = manifest["deployment"]
    profile = load_os_profile(target["os_release"])
    load_board_profile(target["model"])
    mismatches: list[str] = []

    exact_fields = {
        "deployment.app_root": (
            deployment["app_root"],
            profile["app_root"],
        ),
        "deployment.mountpoint": (
            deployment["mountpoint"],
            profile["redpitaya_mountpoint"],
        ),
        "deployment.nginx_service": (
            deployment["nginx_service"],
            profile["nginx_service"],
        ),
    }
    for label, (actual, expected) in exact_fields.items():
        if actual != expected:
            mismatches.append(f"{label}={actual!r}, profile requires {expected!r}")

    expected_python = profile["python_version"]
    if application["python_abi"] not in {"none", expected_python}:
        mismatches.append(
            f"application.python_abi={application['python_abi']!r}, "
            f"profile requires 'none' or {expected_python!r}"
        )
    if application["architecture"] == "native-controller":
        controller = str(application["controller_binary"])
        if not controller.endswith(profile["native_controller_filename"]):
            mismatches.append(
                "application.controller_binary must end in "
                f"{profile['native_controller_filename']!r}"
            )
    if fpga["enabled"]:
        if fpga["artifact_name"] != profile["fpga_artifact_filename"]:
            mismatches.append(
                f"fpga.artifact_name={fpga['artifact_name']!r}, profile requires "
                f"{profile['fpga_artifact_filename']!r}"
            )
        if fpga["device_tree_name"] != profile["fpga_device_tree_filename"]:
            mismatches.append(
                f"fpga.device_tree_name={fpga['device_tree_name']!r}, profile requires "
                f"{profile['fpga_device_tree_filename']!r}"
            )
        if fpga["loader"] != "overlay.sh":
            mismatches.append(
                f"fpga.loader={fpga['loader']!r}, analyzed profile requires 'overlay.sh'"
            )
    if mismatches:
        raise ContractError(
            "project contract conflicts with selected OS/model profiles:\n- "
            + "\n- ".join(mismatches)
        )
