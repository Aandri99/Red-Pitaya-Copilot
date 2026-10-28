#!/usr/bin/env python3
"""Resolve a shared Red Pitaya target profile to deterministic FPGA build inputs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


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


class ResolveError(Exception):
    """Raised for deterministic user-facing resolution failures."""


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ResolveError(f"cannot read {label} {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ResolveError(
            f"{label} {path}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise ResolveError(f"{label} {path} must contain an object")
    return data


def validate_manifest(path: Path) -> None:
    validator = plugin_root() / "scripts" / "validate_redpitaya_project.py"
    if not validator.is_file():
        raise ResolveError(f"shared validator is missing: {validator}")
    result = subprocess.run(
        [sys.executable, str(validator), str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        details = (result.stdout + result.stderr).strip()
        raise ResolveError(
            "shared project contract validation failed"
            + (f":\n{details}" if details else "")
        )


def resolve(manifest_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_file():
        raise ResolveError(f"project manifest not found: {manifest_path}")
    validate_manifest(manifest_path)
    manifest = load_json(manifest_path, "project manifest")
    target = manifest["target"]
    profile_name = target["model"]
    os_release = target["os_release"]

    board_profiles = load_json(
        plugin_root() / "profiles" / "board-models.json",
        "board model profiles",
    )
    models = board_profiles.get("models")
    if not isinstance(models, dict) or profile_name not in models:
        raise ResolveError(f"unsupported target.model: {profile_name}")
    if profile_name not in VIVADO_MODEL_BY_PROFILE:
        raise ResolveError(
            f"no audited Vivado MODEL mapping exists for {profile_name}"
        )
    board = models[profile_name]
    if not isinstance(board, dict):
        raise ResolveError(f"invalid board profile record: {profile_name}")

    os_profile = load_json(
        plugin_root() / "profiles" / f"os-{os_release}.json",
        "OS profile",
    )
    if os_profile.get("os_release") != os_release:
        raise ResolveError(f"OS profile does not match {os_release}")

    artifact = os_profile.get("fpga_artifact_filename")
    if manifest["fpga"].get("enabled") is True:
        if manifest["fpga"].get("artifact_name") != artifact:
            raise ResolveError(
                f"fpga.artifact_name must be {artifact!r} for {os_release}"
            )

    return {
        "schema_version": 1,
        "target_profile": profile_name,
        "probe_required": bool(board.get("confirm_on_target", True)),
        "probe_rule": (
            "Read the live stock overlay.sh and use the same model helper as "
            "target_profile. Verified OS 2.00-35 uses monitor -f; newer "
            "releases may use profiles -f. Corroborate with monitor -n, "
            "monitor -i, the matching FPGA directory and physical inventory."
        ),
        "zynq": board.get("zynq"),
        "family": board.get("family"),
        "vivado_model": VIVADO_MODEL_BY_PROFILE[profile_name],
        "vivado_version": "2020.1",
        "os_release": os_release,
        "fpga_artifact_name": artifact,
        "fpga_device_tree_name": os_profile.get("fpga_device_tree_filename"),
        "fpga_loader": os_profile.get("fpga_loader"),
        "fpga_project": manifest["fpga"].get("project"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve target.model to the audited Vivado MODEL and OS package profile."
        )
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = resolve(args.manifest)
    except ResolveError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    if args.json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for key, value in result.items():
            if key == "schema_version":
                continue
            rendered = json.dumps(value) if isinstance(value, bool) else str(value)
            print(f"{key}={rendered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
