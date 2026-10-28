from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


generator = load_module(
    "profile_test_generator",
    PLUGIN_ROOT / "scripts" / "new_redpitaya_project.py",
)
validator = load_module(
    "profile_test_validator",
    PLUGIN_ROOT / "scripts" / "validate_redpitaya_project.py",
)


class ProfileConsistencyTests(unittest.TestCase):
    def load_json(self, relative_path: str):
        return json.loads(
            (PLUGIN_ROOT / relative_path).read_text(encoding="utf-8")
        )

    def test_os_profiles_match_contract_constants(self):
        profiles = {
            release: self.load_json(f"profiles/os-{release}.json")
            for release in generator.OS_RELEASES
        }
        self.assertEqual(set(profiles), validator.OS_RELEASES)
        self.assertEqual(
            profiles["2.00-35"]["fpga_artifact_filename"],
            "fpga.bit.bin",
        )
        self.assertEqual(
            profiles["2.00-35"]["fpga_device_tree_filename"],
            "fpga.dtbo",
        )
        self.assertEqual(profiles["2.07-48"]["fpga_artifact_filename"], "fpga.bit.bin")
        self.assertEqual(profiles["3.00-57"]["fpga_artifact_filename"], "fpga.bin")
        self.assertEqual(
            profiles["2.00-35"]["fpga_profile_root_template"],
            "/opt/redpitaya/fpga/{model}/{profile}",
        )
        self.assertEqual(
            profiles["2.07-48"]["fpga_profile_root_template"],
            "/opt/redpitaya/fpga/{model}/{profile}",
        )
        self.assertNotIn("fpga_profile_root_template", profiles["3.00-57"])
        self.assertEqual(profiles["2.00-35"]["python_version"], "3.10")
        self.assertEqual(profiles["2.07-48"]["python_version"], "3.10")
        self.assertEqual(profiles["3.00-57"]["python_version"], "3.12")
        for release, profile in profiles.items():
            self.assertEqual(profile["os_release"], release)
            self.assertEqual(profile["app_root"], "/opt/redpitaya/www/apps")
            self.assertEqual(profile["redpitaya_mountpoint"], "/opt/redpitaya")
            self.assertEqual(profile["nginx_service"], "redpitaya_nginx.service")
            self.assertTrue(profile["native_rebuild_required"])
            provenance = profile["analysis_provenance"]
            self.assertRegex(provenance["analyzed_on"], r"^2026-07-(29|30)$")
            if release == "2.00-35":
                self.assertIsNone(provenance["source_size_bytes"])
                self.assertIsNone(provenance["source_sha256"])
                self.assertEqual(
                    provenance["ssh_host_key_sha256"],
                    "SHA256:ycMtoQD2+LgpmF5HVF3xrXoJRJcjpZAty4tXCkaNHBc",
                )
                self.assertIn("live rp-f08e87.local", provenance["source_artifact"])
            else:
                self.assertGreater(provenance["source_size_bytes"], 1_000_000)
                self.assertRegex(
                    provenance["source_sha256"],
                    r"^[0-9A-F]{64}$",
                )
                self.assertIn(release, provenance["source_artifact"])

    def test_board_profile_and_contract_model_sets_match(self):
        board_profiles = self.load_json("profiles/board-models.json")
        profile_models = set(board_profiles["models"])
        self.assertEqual(profile_models, set(generator.MODELS))
        self.assertEqual(profile_models, validator.MODELS)
        for model_name, model in board_profiles["models"].items():
            with self.subTest(model=model_name):
                self.assertIn(model["zynq"], {"xc7z010", "xc7z020"})
                self.assertTrue(model["confirm_on_target"])

    def test_schema_is_valid_json_and_declares_all_releases(self):
        schema = self.load_json("schemas/redpitaya-project.schema.json")
        releases = (
            schema["properties"]["target"]["properties"]["os_release"]["enum"]
        )
        self.assertEqual(set(releases), set(generator.OS_RELEASES))
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)

    def test_os_200_generates_and_validates_fpga_contract(self):
        args = generator.parser().parse_args(
            [
                "--slug",
                "os2_fpga_test",
                "--title",
                "OS 2 FPGA Test",
                "--os-release",
                "2.00-35",
                "--model",
                "z10_125",
                "--components",
                "fpga",
                "--role",
                "generator",
                "--output",
                "unused.json",
            ]
        )
        manifest = generator.build_manifest(args)
        self.assertTrue(manifest["fpga"]["enabled"])
        self.assertEqual(manifest["fpga"]["artifact_name"], "fpga.bit.bin")
        findings = validator.Validator(manifest).validate()
        self.assertFalse(
            [item for item in findings if item.severity == "error"],
            findings,
        )


if __name__ == "__main__":
    unittest.main()
