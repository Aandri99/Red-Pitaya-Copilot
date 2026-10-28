#!/usr/bin/env python3
from __future__ import annotations

import json
import secrets
import shutil
import unittest
from pathlib import Path

from critical_parameter_review import inspect_contract


class CriticalParameterReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        scripts_root = Path(__file__).resolve().parent
        self.test_root = scripts_root / f".critical-parameter-test-{secrets.token_hex(8)}"
        self.test_root.mkdir()
        self.addCleanup(self._cleanup)
        self.app = self.test_root / "review_fixture"
        (self.app / "protocols").mkdir(parents=True)
        manifest = {
            "project": {"slug": "review_fixture"},
            "application": {
                "routes": [
                    "/review_fixture/api/configure",
                    "/review_fixture/api/parameters",
                ]
            },
        }
        (self.app / "redpitaya-project.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        self.contract = {
            "schema_version": 1,
            "instrument": "review_fixture",
            "review_required_before_apply": True,
            "parameters": [
                {
                    "id": "frequency_hz",
                    "label": "Frequency",
                    "category": "dsp",
                    "editable": True,
                    "data_type": "number",
                    "units": "Hz",
                    "default": 1000.0,
                    "minimum": 1.0,
                    "maximum": 100000.0,
                    "step": 1.0,
                    "safety_class": "measurement",
                    "explanation": "Sets the coherent reference.",
                    "change_effect": "Restarts the averaging window.",
                },
                {
                    "id": "sample_clock_hz",
                    "label": "Sample clock",
                    "category": "hardware",
                    "editable": False,
                    "data_type": "integer",
                    "units": "Hz",
                    "value": 125000000,
                    "safety_class": "rebuild_required",
                    "explanation": "Pins the FPGA timing contract.",
                    "change_effect": "Requires a rebuild.",
                },
            ],
            "presets": [
                {
                    "id": "default_setup",
                    "label": "Default",
                    "description": "Prepare the reviewed default.",
                    "values": {"frequency_hz": 1000.0},
                }
            ],
        }
        self.contract_path = self.app / "protocols" / "critical-parameters.json"
        self._write_contract()

    def _cleanup(self) -> None:
        scripts_root = Path(__file__).resolve().parent
        resolved = self.test_root.resolve(strict=False)
        if (
            resolved.parent != scripts_root
            or not resolved.name.startswith(".critical-parameter-test-")
            or resolved == scripts_root
        ):
            raise AssertionError(f"refusing unsafe test cleanup: {resolved}")
        if resolved.exists():
            shutil.rmtree(resolved)

    def _write_contract(self) -> None:
        self.contract_path.write_text(
            json.dumps(self.contract), encoding="utf-8"
        )

    def test_valid_contract(self) -> None:
        result = inspect_contract(self.app)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(len(result.sha256), 64)

    def test_preset_cannot_reference_fixed_parameter(self) -> None:
        self.contract["presets"][0]["values"] = {"sample_clock_hz": 1}
        self._write_contract()
        result = inspect_contract(self.app)
        self.assertFalse(result.ok)
        self.assertTrue(any("non-editable" in item for item in result.errors))

    def test_configure_requires_parameter_route(self) -> None:
        manifest_path = self.app / "redpitaya-project.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["application"]["routes"] = ["/review_fixture/api/configure"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        result = inspect_contract(self.app)
        self.assertFalse(result.ok)
        self.assertTrue(any("/api/parameters" in item for item in result.errors))


if __name__ == "__main__":
    unittest.main(verbosity=2)
