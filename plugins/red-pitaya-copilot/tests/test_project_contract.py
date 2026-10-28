from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = PLUGIN_ROOT / "scripts" / "validate_redpitaya_project.py"
GENERATOR_PATH = PLUGIN_ROOT / "scripts" / "new_redpitaya_project.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


validator = load_module("project_validator", VALIDATOR_PATH)
generator = load_module("project_generator", GENERATOR_PATH)


class Args:
    slug = "generated_monitor"
    title = "Generated Monitor"
    os_release = "3.00-57"
    model = "z20_125"
    components = "app-fpga"
    app_architecture = "hybrid"
    role = "acquisition"
    hostname = "fixture.invalid"
    service_port = 9187
    authentication = "none"


class ProjectContractTests(unittest.TestCase):
    def validate_fixture(self, name: str):
        path = PLUGIN_ROOT / "test-fixtures" / "projects" / name
        return validator.Validator(json.loads(path.read_text())).validate()

    def test_valid_os2_fixture(self):
        findings = self.validate_fixture("valid-app-os2.json")
        self.assertFalse([item for item in findings if item.severity == "error"])

    def test_valid_os3_fixture(self):
        findings = self.validate_fixture("valid-hybrid-os3.json")
        self.assertFalse([item for item in findings if item.severity == "error"])

    def test_invalid_artifact_is_rejected(self):
        findings = self.validate_fixture("invalid-os3-artifact.json")
        self.assertTrue(
            any(
                item.path == "$.fpga.artifact_name"
                and item.severity == "error"
                for item in findings
            )
        )

    def test_unknown_nested_property_is_rejected(self):
        manifest = generator.build_manifest(Args())
        manifest["application"]["made_up_setting"] = True
        findings = validator.Validator(manifest).validate()
        self.assertTrue(
            any(
                item.path == "$.application.made_up_setting"
                and item.severity == "error"
                for item in findings
            )
        )

    def test_unsafe_staging_path_is_rejected(self):
        manifest = generator.build_manifest(Args())
        manifest["deployment"]["staging_root"] = "/tmp/../root/unsafe"
        findings = validator.Validator(manifest).validate()
        self.assertTrue(
            any(
                item.path == "$.deployment.staging_root"
                and item.severity == "error"
                for item in findings
            )
        )

    def test_secret_bearing_key_is_rejected(self):
        manifest = generator.build_manifest(Args())
        manifest["cloud_token"] = "must-not-be-stored"
        findings = validator.Validator(manifest).validate()
        self.assertTrue(
            any(
                item.path == "$.cloud_token" and item.severity == "error"
                for item in findings
            )
        )

    def test_generator_output_validates(self):
        manifest = generator.build_manifest(Args())
        self.assertEqual(manifest["application"]["authentication"], "none")
        findings = validator.Validator(manifest).validate()
        self.assertFalse([item for item in findings if item.severity == "error"])

    def test_operator_token_is_explicit_opt_in(self):
        args = Args()
        args.authentication = "operator-token"
        manifest = generator.build_manifest(args)
        self.assertEqual(
            manifest["application"]["authentication"],
            "operator-token",
        )
        findings = validator.Validator(manifest).validate()
        self.assertFalse([item for item in findings if item.severity == "error"])

    def test_invalid_authentication_mode_is_rejected(self):
        manifest = generator.build_manifest(Args())
        manifest["application"]["authentication"] = "temporary-token"
        findings = validator.Validator(manifest).validate()
        self.assertTrue(
            any(
                item.path == "$.application.authentication"
                and item.severity == "error"
                for item in findings
            )
        )

    def test_legacy_missing_authentication_has_migration_warning(self):
        manifest = generator.build_manifest(Args())
        del manifest["application"]["authentication"]
        findings = validator.Validator(manifest).validate()
        self.assertFalse([item for item in findings if item.severity == "error"])
        self.assertTrue(
            any(
                item.path == "$.application.authentication"
                and item.severity == "warning"
                for item in findings
            )
        )

    def test_generator_fpga_defaults_match_canonical_register_contract(self):
        manifest = generator.build_manifest(Args())
        contract_path = (
            PLUGIN_ROOT
            / "skills"
            / "build-red-pitaya-fpga"
            / "assets"
            / "register-contract.example.json"
        )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        registers = {item["name"]: item for item in contract["registers"]}
        base = int(contract["base_address"], 16)
        abi_address = base + int(registers["ABI_VERSION"]["offset"], 16)
        self.assertEqual(
            manifest["fpga"]["register_contract"],
            "register-contract.json",
        )
        self.assertEqual(
            manifest["fpga"]["version_register"],
            f"0x{abi_address:08X}",
        )
        self.assertEqual(
            manifest["fpga"]["magic_value"],
            registers["MAGIC"]["reset"],
        )

    def test_generator_refuses_short_slug(self):
        args = Args()
        args.slug = "x"
        with self.assertRaises(ValueError):
            generator.build_manifest(args)


if __name__ == "__main__":
    unittest.main()
