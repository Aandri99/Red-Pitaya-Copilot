#!/usr/bin/env python3
"""Adversarial and reproducibility tests for the offline app packager."""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
PACKAGER = SCRIPTS_ROOT / "package_red_pitaya_app.py"
SCAFFOLDER = SCRIPTS_ROOT / "new_red_pitaya_app.py"
TEMP_PREFIX = ".package-test-"
SLUG = "package_fixture"


def load_packager():
    spec = importlib.util.spec_from_file_location("_app_packager_under_test", PACKAGER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load packager module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PACKAGER_MODULE = load_packager()


class PackageToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = SKILL_ROOT / f"{TEMP_PREFIX}{secrets.token_hex(8)}"
        cls.root.mkdir()
        cls.source_parent = cls.root / "base"
        cls.source_parent.mkdir()
        result = subprocess.run(
            [
                sys.executable,
                str(SCAFFOLDER),
                "--slug",
                SLUG,
                "--title",
                "Offline Package Fixture",
                "--description",
                "Deterministic application package test fixture",
                "--version",
                "1.2.3",
                "--revision",
                "package-test",
                "--os-release",
                "3.00-57",
                "--model",
                "z20_125",
                "--role",
                "control",
                "--backend",
                "static",
                "--output",
                str(cls.source_parent),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "fixture scaffold failed: " + (result.stderr or result.stdout)
            )
        cls.base_app = cls.source_parent / SLUG

    @classmethod
    def tearDownClass(cls) -> None:
        resolved = cls.root.resolve(strict=False)
        skill = SKILL_ROOT.resolve(strict=True)
        if (
            resolved.parent != skill
            or not resolved.name.startswith(TEMP_PREFIX)
            or resolved.is_symlink()
        ):
            raise RuntimeError(f"refusing unsafe test cleanup: {resolved}")
        last_error: OSError | None = None
        for attempt in range(8):
            try:
                shutil.rmtree(resolved)
                return
            except FileNotFoundError:
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.05 * (attempt + 1))
        if last_error is not None:
            raise last_error

    def fresh_dir(self, label: str) -> Path:
        path = self.root / f"{label}-{secrets.token_hex(5)}"
        path.mkdir()
        return path

    def clone_app(self, label: str) -> Path:
        parent = self.fresh_dir(label)
        destination = parent / SLUG
        shutil.copytree(self.base_app, destination)
        return destination

    def run_tool(self, *args: str, expect: int = 0) -> dict:
        result = subprocess.run(
            [sys.executable, str(PACKAGER), "--json", *args],
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
        )
        self.assertEqual(
            result.returncode,
            expect,
            msg=f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"packager returned invalid JSON: {exc}: {result.stdout!r}")
        return payload

    def create_bundle(
        self,
        source: Path,
        label: str,
        *,
        with_zip: bool = False,
    ) -> tuple[Path, Path | None, dict]:
        parent = self.fresh_dir(label)
        output = parent / SLUG
        archive = parent / "transport.zip" if with_zip else None
        args = [
            "create",
            "--app",
            str(source),
            "--output",
            str(output),
            "--os-release",
            "3.00-57",
        ]
        if archive is not None:
            args.extend(["--transport-zip", str(archive)])
        payload = self.run_tool(*args)
        self.assertTrue(payload["ok"])
        return output, archive, payload

    def test_reproducible_directory_receipt_and_transport_zip(self) -> None:
        source_one = self.clone_app("repro-source-one")
        source_two = self.clone_app("repro-source-two")
        package_one, zip_one, payload_one = self.create_bundle(
            source_one, "repro-output-one", with_zip=True
        )
        package_two, zip_two, payload_two = self.create_bundle(
            source_two, "repro-output-two", with_zip=True
        )
        self.assertIsNotNone(zip_one)
        self.assertIsNotNone(zip_two)
        self.assertEqual(
            (package_one / PACKAGER_MODULE.RECEIPT_NAME).read_bytes(),
            (package_two / PACKAGER_MODULE.RECEIPT_NAME).read_bytes(),
        )
        self.assertEqual(zip_one.read_bytes(), zip_two.read_bytes())
        self.assertEqual(
            payload_one["payload_inventory_sha256"],
            payload_two["payload_inventory_sha256"],
        )
        self.assertFalse(payload_one["deployment_authorized"])
        self.assertFalse(payload_one["transport_zip_deployable"])
        self.assertTrue((package_one / PACKAGER_MODULE.BLOCKED_MARKER_NAME).is_file())

        validation = self.run_tool(
            "validate",
            "--package",
            str(package_one),
            "--transport-zip",
            str(zip_one),
        )
        self.assertTrue(validation["valid"])
        self.assertFalse(validation["deployment_ready"])
        self.assertFalse(validation["deployment_authorized"])

    def test_cache_and_junk_are_excluded_and_recorded(self) -> None:
        source = self.clone_app("cache-source")
        cache = source / "service" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "ignored.pyc").write_bytes(b"cache")
        (source / ".DS_Store").write_bytes(b"junk")
        (source / "js" / "scratch.tmp").write_bytes(b"junk")
        package, _archive, _payload = self.create_bundle(source, "cache-output")
        self.assertFalse((package / "service" / "__pycache__").exists())
        self.assertFalse((package / ".DS_Store").exists())
        receipt = json.loads(
            (package / PACKAGER_MODULE.RECEIPT_NAME).read_text(encoding="utf-8")
        )
        self.assertEqual(
            receipt["excluded_cache_paths"],
            [".DS_Store", "js/scratch.tmp", "service/__pycache__/"],
        )

    def test_existing_output_is_never_overwritten(self) -> None:
        source = self.clone_app("overwrite-source")
        parent = self.fresh_dir("overwrite-output")
        output = parent / SLUG
        output.mkdir()
        sentinel = output / "sentinel.txt"
        sentinel.write_text("preserve", encoding="utf-8")
        payload = self.run_tool(
            "create",
            "--app",
            str(source),
            "--output",
            str(output),
            expect=1,
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_output_name_must_equal_slug(self) -> None:
        source = self.clone_app("name-source")
        parent = self.fresh_dir("name-output")
        output = parent / "wrong-name"
        payload = self.run_tool(
            "create",
            "--app",
            str(source),
            "--output",
            str(output),
            expect=1,
        )
        self.assertIn("must exactly match", payload["error"])
        self.assertFalse(output.exists())

    def test_secret_like_filename_is_rejected_before_publication(self) -> None:
        source = self.clone_app("secret-name-source")
        (source / ".env.production").write_text("harmless=true\n", encoding="utf-8")
        parent = self.fresh_dir("secret-name-output")
        output = parent / SLUG
        payload = self.run_tool(
            "create",
            "--app",
            str(source),
            "--output",
            str(output),
            expect=1,
        )
        self.assertIn("secret-like", payload["error"])
        self.assertFalse(output.exists())
        self.assertEqual(list(parent.glob(f".{SLUG}.package-staging-*")), [])

    def test_embedded_credential_is_rejected(self) -> None:
        source = self.clone_app("secret-content-source")
        (source / "notes.txt").write_text(
            'password = "definitely-not-for-packaging"\n',
            encoding="utf-8",
        )
        parent = self.fresh_dir("secret-content-output")
        output = parent / SLUG
        payload = self.run_tool(
            "create",
            "--app",
            str(source),
            "--output",
            str(output),
            expect=1,
        )
        self.assertIn("credential", payload["error"])
        self.assertFalse(output.exists())

    def test_reserved_package_metadata_in_source_is_rejected(self) -> None:
        source = self.clone_app("reserved-source")
        (source / PACKAGER_MODULE.RECEIPT_NAME).write_text("{}\n", encoding="utf-8")
        parent = self.fresh_dir("reserved-output")
        payload = self.run_tool(
            "create",
            "--app",
            str(source),
            "--output",
            str(parent / SLUG),
            expect=1,
        )
        self.assertIn("reserved package metadata", payload["error"])

    def test_symlink_is_rejected(self) -> None:
        source = self.clone_app("symlink-source")
        link = source / "linked-index.html"
        try:
            os.symlink(source / "index.html", link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        parent = self.fresh_dir("symlink-output")
        payload = self.run_tool(
            "create",
            "--app",
            str(source),
            "--output",
            str(parent / SLUG),
            expect=1,
        )
        self.assertIn("symlink/reparse-point", payload["error"])

    @unittest.skipUnless(os.name == "nt", "Windows directory-junction behavior")
    def test_directory_junction_is_rejected(self) -> None:
        source = self.clone_app("junction-source")
        target = self.fresh_dir("junction-target")
        link = source / "linked-directory"
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        if result.returncode != 0:
            self.skipTest(f"junction creation is unavailable: {result.stderr or result.stdout}")
        try:
            parent = self.fresh_dir("junction-output")
            payload = self.run_tool(
                "create",
                "--app",
                str(source),
                "--output",
                str(parent / SLUG),
                expect=1,
            )
            self.assertIn("symlink/reparse-point", payload["error"])
        finally:
            if os.path.lexists(link):
                os.rmdir(link)

    @unittest.skipUnless(os.name == "nt", "Windows root-junction behavior")
    def test_source_root_junction_is_rejected(self) -> None:
        parent = self.fresh_dir("root-junction")
        link = parent / "linked-app"
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(self.base_app)],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        if result.returncode != 0:
            self.skipTest(f"junction creation is unavailable: {result.stderr or result.stdout}")
        try:
            output_parent = self.fresh_dir("root-junction-output")
            payload = self.run_tool(
                "create",
                "--app",
                str(link),
                "--output",
                str(output_parent / SLUG),
                expect=1,
            )
            self.assertIn("must not be a symlink or reparse point", payload["error"])
        finally:
            if os.path.lexists(link):
                os.rmdir(link)

    def test_payload_tamper_and_extra_file_are_detected(self) -> None:
        source = self.clone_app("tamper-source")
        package, _archive, _payload = self.create_bundle(source, "tamper-output")
        (package / "js" / "app.js").write_text(
            (package / "js" / "app.js").read_text(encoding="utf-8")
            + "\n// tampered\n",
            encoding="utf-8",
        )
        payload = self.run_tool(
            "validate", "--package", str(package), expect=1
        )
        self.assertIn("inventory differs", payload["error"])

        source_two = self.clone_app("extra-source")
        package_two, _archive, _payload = self.create_bundle(
            source_two, "extra-output"
        )
        (package_two / "unexpected.txt").write_text("extra", encoding="utf-8")
        payload = self.run_tool(
            "validate", "--package", str(package_two), expect=1
        )
        self.assertIn("inventory differs", payload["error"])

    def test_receipt_tamper_is_detected(self) -> None:
        source = self.clone_app("receipt-source")
        package, _archive, _payload = self.create_bundle(source, "receipt-output")
        receipt_path = package / PACKAGER_MODULE.RECEIPT_NAME
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["deployment_authorized"] = True
        receipt_path.write_bytes(PACKAGER_MODULE.canonical_bytes(receipt))
        payload = self.run_tool(
            "validate", "--package", str(package), expect=1
        )
        self.assertIn("must never grant", payload["error"])

    def test_forged_non_cache_exclusion_is_detected(self) -> None:
        source = self.clone_app("exclusion-receipt-source")
        package, _archive, _payload = self.create_bundle(
            source, "exclusion-receipt-output"
        )
        receipt_path = package / PACKAGER_MODULE.RECEIPT_NAME
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["excluded_cache_paths"] = ["index.html"]
        core = {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
        receipt["receipt_sha256"] = PACKAGER_MODULE.canonical_sha256(core)
        receipt_path.write_bytes(PACKAGER_MODULE.canonical_bytes(receipt))
        payload = self.run_tool(
            "validate", "--package", str(package), expect=1
        )
        self.assertIn("non-cache exclusion", payload["error"])

    def test_transport_zip_tamper_is_detected(self) -> None:
        source = self.clone_app("zip-source")
        package, archive, _payload = self.create_bundle(
            source, "zip-output", with_zip=True
        )
        self.assertIsNotNone(archive)
        with zipfile.ZipFile(archive, "a") as handle:
            handle.writestr("unexpected.txt", b"tamper")
        payload = self.run_tool(
            "validate",
            "--package",
            str(package),
            "--transport-zip",
            str(archive),
            expect=1,
        )
        self.assertIn("inventory/order", payload["error"])

    def test_transport_zip_is_never_overwritten(self) -> None:
        source = self.clone_app("zip-overwrite-source")
        parent = self.fresh_dir("zip-overwrite-output")
        output = parent / SLUG
        archive = parent / "transport.zip"
        archive.write_bytes(b"preserve")
        payload = self.run_tool(
            "create",
            "--app",
            str(source),
            "--output",
            str(output),
            "--transport-zip",
            str(archive),
            expect=1,
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(archive.read_bytes(), b"preserve")
        self.assertFalse(output.exists())

    def test_warning_bundle_fails_require_deployment_ready(self) -> None:
        source = self.clone_app("strict-source")
        package, _archive, payload = self.create_bundle(source, "strict-output")
        self.assertFalse(payload["deployment_ready"])
        result = self.run_tool(
            "validate",
            "--package",
            str(package),
            "--require-deployment-ready",
            expect=1,
        )
        self.assertIn("deployment-blocked", result["error"])

    def test_warning_free_bundle_is_ready_but_never_authorized(self) -> None:
        source = self.clone_app("ready-source")
        manifest_path = source / "redpitaya-project.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["target"]["hostname"] = "rp-package-fixture.local"
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        package, _archive, payload = self.create_bundle(source, "ready-output")
        self.assertTrue(payload["deployment_ready"])
        self.assertFalse(payload["deployment_authorized"])
        self.assertFalse((package / PACKAGER_MODULE.BLOCKED_MARKER_NAME).exists())
        validation = self.run_tool(
            "validate",
            "--package",
            str(package),
            "--require-deployment-ready",
        )
        self.assertTrue(validation["deployment_ready"])
        self.assertFalse(validation["deployment_authorized"])
        self.assertEqual(package.name, manifest["application"]["slug"])
        self.assertEqual(
            json.loads(
                (package / "redpitaya-project.json").read_text(encoding="utf-8")
            ),
            manifest,
        )

    def test_manifest_error_prevents_any_output(self) -> None:
        source = self.clone_app("invalid-manifest-source")
        manifest_path = source / "redpitaya-project.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["target"]["model"] = "not-a-board"
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        parent = self.fresh_dir("invalid-manifest-output")
        output = parent / SLUG
        payload = self.run_tool(
            "create",
            "--app",
            str(source),
            "--output",
            str(output),
            expect=1,
        )
        self.assertIn("validation failed", payload["error"])
        self.assertFalse(output.exists())
        self.assertEqual(list(parent.glob(f".{SLUG}.package-staging-*")), [])

    @unittest.skipUnless(os.name == "nt", "Windows rename retry behavior")
    def test_windows_atomic_rename_retries_transient_sharing_violation(self) -> None:
        parent = self.fresh_dir("rename-retry")
        source = parent / "source"
        destination = parent / "destination"
        source.mkdir()
        real_rename = PACKAGER_MODULE.os.rename
        attempts = {"count": 0}

        def flaky_rename(old, new):
            attempts["count"] += 1
            if attempts["count"] < 3:
                error = PermissionError(errno_value := 13, os.strerror(errno_value))
                error.winerror = 32
                raise error
            return real_rename(old, new)

        with mock.patch.object(PACKAGER_MODULE.os, "rename", side_effect=flaky_rename):
            PACKAGER_MODULE.atomic_rename_noreplace(source, destination)
        self.assertEqual(attempts["count"], 3)
        self.assertTrue(destination.is_dir())
        self.assertFalse(source.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
