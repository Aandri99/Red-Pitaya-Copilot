#!/usr/bin/env python3
"""Adversarial tests for reviewed optional systemd unit directives.

The tests are fully offline.  They scaffold one local Python-service
application, mutate only its generated unit, and invoke the public validator
CLI.  No hostname is resolved and no network or hardware operation is used.
"""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
SCAFFOLD_SCRIPT = SCRIPTS_ROOT / "new_red_pitaya_app.py"
VALIDATOR_SCRIPT = SCRIPTS_ROOT / "validate_red_pitaya_app.py"
TEMP_PREFIX = ".systemd-validator-test-"
APP_SLUG = "systemd_validator_fixture"
OS_RELEASE = "2.07-48"
BACKEND = "python-service"
SERVICE_PORT = 19176

SAFE_OPTIONAL_SERVICE_DIRECTIVES = {
    "AmbientCapabilities": "CAP_SYS_RAWIO",
    "RuntimeDirectoryMode": "0700",
    "StateDirectoryMode": "0700",
    "ReadWritePaths": "/run/lock",
}

UNSAFE_ALTERNATE_VALUES = {
    "AmbientCapabilities": "CAP_SYS_ADMIN",
    "RuntimeDirectoryMode": "0777",
    "StateDirectoryMode": "0777",
    "ReadWritePaths": "/",
}


class SystemdOptionalDirectiveValidatorTests(unittest.TestCase):
    """Prove that the optional systemd surface is an exact allowlist."""

    temp_root: Path
    app_root: Path
    unit_path: Path
    manifest_path: Path
    js_path: Path
    original_unit: str
    original_manifest: str
    original_js: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_root = cls._make_guarded_temp_root()
        cls.addClassCleanup(cls._cleanup_guarded_temp_root)

        result = subprocess.run(
            [
                sys.executable,
                str(SCAFFOLD_SCRIPT),
                "--slug",
                APP_SLUG,
                "--title",
                "Systemd Validator Fixture",
                "--description",
                "Offline systemd validator adversarial fixture",
                "--version",
                "1.0.0",
                "--revision",
                "unit-test",
                "--os-release",
                OS_RELEASE,
                "--model",
                "z10_125",
                "--role",
                "self_test",
                "--backend",
                BACKEND,
                "--service-port",
                str(SERVICE_PORT),
                "--output",
                str(cls.temp_root),
            ],
            cwd=SCRIPTS_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(
                "could not scaffold validator fixture:\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )

        cls.app_root = cls.temp_root / APP_SLUG
        units = sorted(cls.app_root.rglob("*.service"))
        if len(units) != 1:
            raise AssertionError(
                f"expected exactly one generated unit, found {len(units)}"
            )
        cls.unit_path = units[0]
        cls.original_unit = cls.unit_path.read_text(encoding="utf-8")
        cls.manifest_path = cls.app_root / "redpitaya-project.json"
        cls.original_manifest = cls.manifest_path.read_text(encoding="utf-8")
        cls.js_path = cls.app_root / "js" / "app.js"
        cls.original_js = cls.js_path.read_text(encoding="utf-8")

        result, payload = cls._run_validator()
        if result.returncode != 0 or payload.get("ok") is not True:
            raise AssertionError(
                "unmodified generated fixture did not validate:\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )

    @classmethod
    def _make_guarded_temp_root(cls) -> Path:
        skill_root = SKILL_ROOT.resolve(strict=True)
        for _attempt in range(100):
            candidate = skill_root / f"{TEMP_PREFIX}{secrets.token_hex(8)}"
            try:
                candidate.mkdir()
            except FileExistsError:
                continue
            resolved = candidate.resolve(strict=True)
            if (
                resolved.parent != skill_root
                or not resolved.name.startswith(TEMP_PREFIX)
                or candidate.is_symlink()
            ):
                raise AssertionError(f"unsafe temporary test path: {resolved}")
            return resolved
        raise AssertionError("could not allocate guarded test directory")

    @classmethod
    def _cleanup_guarded_temp_root(cls) -> None:
        path = cls.temp_root.resolve(strict=False)
        skill_root = SKILL_ROOT.resolve(strict=True)
        if (
            path.parent != skill_root
            or not path.name.startswith(TEMP_PREFIX)
            or path == skill_root
        ):
            raise AssertionError(f"refusing unsafe test cleanup path: {path}")
        if path.exists():
            shutil.rmtree(path)

    @classmethod
    def _run_validator(cls) -> tuple[subprocess.CompletedProcess[str], dict]:
        result = subprocess.run(
            [
                sys.executable,
                str(VALIDATOR_SCRIPT),
                str(cls.app_root),
                "--os-release",
                OS_RELEASE,
                "--backend",
                BACKEND,
                "--json",
            ],
            cwd=SCRIPTS_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"validator did not emit JSON: {exc}; stdout={result.stdout!r}"
            ) from exc
        if not isinstance(payload, dict):
            raise AssertionError("validator JSON must be an object")
        return result, payload

    @classmethod
    def _unit_with_directives(cls, directives: dict[str, str]) -> str:
        lines = cls.original_unit.splitlines()
        try:
            service_index = lines.index("[Service]")
        except ValueError as exc:
            raise AssertionError("generated unit lacks [Service]") from exc
        added = [f"{key}={value}" for key, value in directives.items()]
        lines[service_index + 1 : service_index + 1] = added
        return "\n".join(lines) + "\n"

    def setUp(self) -> None:
        self.unit_path.write_text(
            self.original_unit,
            encoding="utf-8",
            newline="\n",
        )
        self.manifest_path.write_text(
            self.original_manifest,
            encoding="utf-8",
            newline="\n",
        )
        self.js_path.write_text(
            self.original_js,
            encoding="utf-8",
            newline="\n",
        )
        launcher = self.app_root / "launcher"
        if launcher.exists():
            shutil.rmtree(launcher)
        binary = self.app_root / "controllerhf.so"
        if binary.exists():
            binary.unlink()
        fpga_config = self.app_root / "fpga.conf"
        if fpga_config.exists():
            fpga_config.unlink()

    def tearDown(self) -> None:
        self.unit_path.write_text(
            self.original_unit,
            encoding="utf-8",
            newline="\n",
        )
        self.manifest_path.write_text(
            self.original_manifest,
            encoding="utf-8",
            newline="\n",
        )
        self.js_path.write_text(
            self.original_js,
            encoding="utf-8",
            newline="\n",
        )
        launcher = self.app_root / "launcher"
        if launcher.exists():
            shutil.rmtree(launcher)
        binary = self.app_root / "controllerhf.so"
        if binary.exists():
            binary.unlink()
        fpga_config = self.app_root / "fpga.conf"
        if fpga_config.exists():
            fpga_config.unlink()

    def _install_service_launcher_fixture(self, *, declare: bool = True) -> None:
        if declare:
            manifest = json.loads(self.original_manifest)
            manifest["application"]["controller_binary"] = "controllerhf.so"
            self.manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )

        source_root = self.app_root / "launcher" / "src"
        source_root.mkdir(parents=True)
        (self.app_root / "launcher" / "Makefile").write_text(
            "all:\n\t@true\n",
            encoding="utf-8",
            newline="\n",
        )
        (source_root / "main.h").write_text(
            "#pragma once\n",
            encoding="utf-8",
            newline="\n",
        )
        exports = (
            "const char *rp_app_desc(void) { return \"fixture\"; }\n"
            "int rp_app_init(void) { return 0; }\n"
            "int rp_app_exit(void) { return 0; }\n"
            "void UpdateSignals(void) {}\n"
            "void UpdateParams(void) {}\n"
            "void UpdateBinarySignals(void) {}\n"
            "void PostUpdateSignals(void) {}\n"
            "void PostUpdateBinarySignals(void) {}\n"
            "void OnNewSignals(void) {}\n"
            "void OnNewParams(void) {}\n"
        )
        (source_root / "main.cpp").write_text(
            exports,
            encoding="utf-8",
            newline="\n",
        )

        header = bytearray(20)
        header[:4] = b"\x7fELF"
        header[4] = 1
        header[5] = 1
        header[18:20] = (40).to_bytes(2, "little")
        (self.app_root / "controllerhf.so").write_bytes(header)

    def assert_systemd_rejected(self, unit_text: str) -> None:
        self.unit_path.write_text(unit_text, encoding="utf-8", newline="\n")
        result, payload = self._run_validator()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIs(payload.get("ok"), False)
        errors = payload.get("errors")
        self.assertIsInstance(errors, list)
        self.assertTrue(
            any(
                isinstance(finding, dict)
                and str(finding.get("path", "")).endswith(".service")
                and "reviewed optional hardening allow-list"
                in str(finding.get("message", ""))
                for finding in errors
            ),
            f"missing exact systemd allowlist rejection: {errors!r}",
        )

    def test_exact_reviewed_optional_values_pass(self) -> None:
        self.unit_path.write_text(
            self._unit_with_directives(SAFE_OPTIONAL_SERVICE_DIRECTIVES),
            encoding="utf-8",
            newline="\n",
        )
        result, payload = self._run_validator()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIs(payload.get("ok"), True)
        self.assertEqual(payload.get("errors"), [])

    def test_os2_service_without_launcher_reports_visibility_warning(self) -> None:
        result, payload = self._run_validator()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIs(payload.get("ok"), True)
        self.assertTrue(
            any(
                finding.get("path")
                == "manifest.application.controller_binary"
                and "/bazaar?apps=" in finding.get("message", "")
                for finding in payload.get("warnings", [])
            ),
            payload.get("warnings"),
        )

    def test_app_only_scaffold_omits_fpga_conf(self) -> None:
        self.assertFalse((self.app_root / "fpga.conf").exists())
        result, payload = self._run_validator()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIs(payload.get("ok"), True)

    def test_default_scaffold_explicitly_disables_application_tokens(self) -> None:
        manifest = json.loads(self.original_manifest)
        self.assertEqual(
            manifest["application"]["authentication"],
            "none",
        )
        self.assertNotIn("operator token", self.original_js.lower())
        self.assertNotIn("authorization", self.original_js.lower())
        self.assertNotIn("bearer", self.original_js.lower())

    def test_no_auth_mode_rejects_hidden_operator_token_gate(self) -> None:
        self.js_path.write_text(
            self.original_js
            + '\nconst operatorTokenRequired = "operator token required";\n',
            encoding="utf-8",
            newline="\n",
        )
        result, payload = self._run_validator()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIs(payload.get("ok"), False)
        self.assertTrue(
            any(
                finding.get("path") == "js/app.js"
                and "application.authentication is 'none'"
                in finding.get("message", "")
                for finding in payload.get("errors", [])
            ),
            payload.get("errors"),
        )

    def test_operator_token_opt_in_requires_reviewed_implementation(self) -> None:
        manifest = json.loads(self.original_manifest)
        manifest["application"]["authentication"] = "operator-token"
        self.manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        result, payload = self._run_validator()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIs(payload.get("ok"), True)
        self.assertTrue(
            any(
                finding.get("path")
                == "manifest.application.authentication"
                and "no recognizable runtime token boundary"
                in finding.get("message", "")
                for finding in payload.get("warnings", [])
            ),
            payload.get("warnings"),
        )

    def test_literal_none_fpga_conf_is_rejected(self) -> None:
        (self.app_root / "fpga.conf").write_text(
            "none\n",
            encoding="utf-8",
            newline="\n",
        )
        result, payload = self._run_validator()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIs(payload.get("ok"), False)
        self.assertTrue(
            any(
                finding.get("path") == "fpga.conf"
                and "must omit" in finding.get("message", "")
                for finding in payload.get("errors", [])
            ),
            payload.get("errors"),
        )

    def test_unknown_service_directive_is_rejected(self) -> None:
        directives = dict(SAFE_OPTIONAL_SERVICE_DIRECTIVES)
        directives["UnreviewedSystemdDirective"] = "true"
        self.assert_systemd_rejected(self._unit_with_directives(directives))

    def test_altered_optional_values_are_rejected(self) -> None:
        for key, unsafe_value in UNSAFE_ALTERNATE_VALUES.items():
            with self.subTest(directive=key, value=unsafe_value):
                directives = dict(SAFE_OPTIONAL_SERVICE_DIRECTIVES)
                directives[key] = unsafe_value
                self.assert_systemd_rejected(
                    self._unit_with_directives(directives)
                )

    def test_declared_service_launcher_controller_passes(self) -> None:
        self._install_service_launcher_fixture()
        result, payload = self._run_validator()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIs(payload.get("ok"), True)
        self.assertEqual(payload.get("errors"), [])

    def test_service_launcher_requires_source_contract(self) -> None:
        self._install_service_launcher_fixture()
        (self.app_root / "launcher" / "src" / "main.h").unlink()
        result, payload = self._run_validator()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIs(payload.get("ok"), False)
        self.assertTrue(
            any(
                finding.get("path") == "launcher/src/main.h"
                for finding in payload.get("errors", [])
            ),
            payload.get("errors"),
        )

    def test_undeclared_service_launcher_binary_is_rejected(self) -> None:
        self._install_service_launcher_fixture(declare=False)
        result, payload = self._run_validator()

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIs(payload.get("ok"), False)
        self.assertTrue(
            any(
                finding.get("path") == "controllerhf.so"
                and "inconsistent" in finding.get("message", "")
                for finding in payload.get("errors", [])
            ),
            payload.get("errors"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
