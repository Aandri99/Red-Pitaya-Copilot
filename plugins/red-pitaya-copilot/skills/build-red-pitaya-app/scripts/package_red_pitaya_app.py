#!/usr/bin/env python3
"""Create and verify deterministic offline Red Pitaya application bundles.

The directory bundle is the deployable *shape* consumed by the deployment
skill.  This tool never grants deployment authorization and never contacts a
board.  An optional ZIP is a reproducible transport container only.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


TOOL_VERSION = "1.0.0"
RECEIPT_SCHEMA_VERSION = 1
RECEIPT_NAME = "application-package-receipt.json"
BLOCKED_MARKER_NAME = "DEPLOYMENT_BLOCKED.txt"
TRANSPORT_MARKER_NAME = "TRANSPORT_ONLY.txt"
TRANSPORT_MARKER = (
    "TRANSPORT ONLY - NOT A DEPLOYMENT AUTHORIZATION\n"
    "Extract the application directory, validate its receipt, and use the "
    "deployment skill's separate identity, backup, authorization, and rollback "
    "gates.\n"
)
ZIP_COMMENT = b"TRANSPORT ONLY - NOT DEPLOYMENT AUTHORIZATION"
BLOCKED_MARKER = (
    "DEPLOYMENT BLOCKED\n"
    "This offline application bundle contains unresolved validator warnings. "
    "Inspect application-package-receipt.json, resolve the warnings against the "
    "exact target, and create a new bundle. Do not edit this package in place.\n"
)
RESERVED_NAMES = {RECEIPT_NAME, BLOCKED_MARKER_NAME, TRANSPORT_MARKER_NAME}
CACHE_DIR_NAMES = {
    "__pycache__",
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    "node_modules",
}
CACHE_FILE_NAMES = {
    ".coverage",
    ".DS_Store",
    "Thumbs.db",
}
CACHE_FILE_SUFFIXES = {".pyc", ".pyo", ".swp", ".tmp"}
SECRET_NAME_RE = re.compile(
    r"(^\.env(?:\.|$)|(^|[._-])"
    r"(id_rsa|id_ed25519|password|passwd|secret|token|credentials?|"
    r"private[-_]?key|authorized_keys)($|[._-])|"
    r"\.(?:pem|p12|pfx|key)$)",
    re.IGNORECASE,
)
SECRET_ASSIGNMENT_RE = re.compile(
    rb"""(?ix)
    \b(password|passwd|secret|api[_-]?key|access[_-]?token)\b
    \s*[:=]\s*
    (["'])[^\r\n"']+\2
    """
)
PRIVATE_KEY_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
)
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
RENAME_ATTEMPTS = 8
RENAME_INITIAL_DELAY_SECONDS = 0.025
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
RECEIPT_KEYS = {
    "schema_version",
    "kind",
    "tool",
    "classification",
    "attestation",
    "deployment_ready",
    "deployment_authorized",
    "transport_zip_deployable",
    "application",
    "payload_inventory",
    "payload_inventory_sha256",
    "excluded_cache_paths",
    "validators",
    "checks",
    "validation_summary",
    "receipt_sha256",
}


class PackageError(RuntimeError):
    """A package safety, validation, or integrity invariant failed."""


@dataclass(frozen=True)
class FileRecord:
    source: Path
    relative: str
    size: int
    sha256: str
    mode: str

    def inventory_item(self) -> dict[str, Any]:
        return {
            "path": self.relative,
            "size": self.size,
            "sha256": self.sha256,
            "mode": self.mode,
        }


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[3]


def app_validator_path() -> Path:
    return Path(__file__).resolve().with_name("validate_red_pitaya_app.py")


def shared_validator_path() -> Path:
    return plugin_root() / "scripts" / "validate_redpitaya_project.py"


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def unresolved_absolute(path: Path) -> Path:
    """Return an absolute normalized path without following the final object."""
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def resolve_nonlinked_directory(path: Path, label: str) -> Path:
    raw = unresolved_absolute(path)
    if not raw.is_dir():
        raise PackageError(f"{label} directory does not exist: {raw}")
    if is_reparse_or_symlink(raw, raw.lstat()):
        raise PackageError(f"{label} must not be a symlink or reparse point: {raw}")
    return raw.resolve(strict=True)


def is_reparse_or_symlink(path: Path, item_stat: os.stat_result | None = None) -> bool:
    try:
        item_stat = item_stat or path.lstat()
    except OSError as exc:
        raise PackageError(f"cannot inspect path {path}: {exc}") from exc
    if stat.S_ISLNK(item_stat.st_mode):
        return True
    attributes = getattr(item_stat, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse)


def validate_relative_name(relative: str) -> None:
    if not relative or relative.startswith("/") or "\\" in relative:
        raise PackageError(f"unsafe package-relative path: {relative!r}")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise PackageError(f"unsafe package-relative path: {relative!r}")
    if any(any(ord(char) < 32 or ord(char) == 127 for char in part) for part in parts):
        raise PackageError(f"control character in package-relative path: {relative!r}")


def is_cache_path(relative: str, is_directory: bool) -> bool:
    parts = relative.split("/")
    name = parts[-1]
    if any(part in CACHE_DIR_NAMES for part in parts):
        return True
    if is_directory:
        return False
    return (
        name in CACHE_FILE_NAMES
        or Path(name).suffix.lower() in CACHE_FILE_SUFFIXES
        or name.endswith("~")
    )


def reject_secret_name(relative: str) -> None:
    for part in relative.split("/"):
        if SECRET_NAME_RE.search(part):
            raise PackageError(f"refusing secret-like source path: {relative!r}")


def reject_secret_content(path: Path, relative: str) -> None:
    overlap = b""
    with path.open("rb") as handle:
        while True:
            block = handle.read(128 * 1024)
            if not block:
                break
            sample = overlap + block
            if any(marker in sample for marker in PRIVATE_KEY_MARKERS):
                raise PackageError(f"refusing private-key material in {relative!r}")
            if SECRET_ASSIGNMENT_RE.search(sample):
                raise PackageError(
                    f"refusing probable embedded credential in {relative!r}"
                )
            overlap = sample[-1024:]


def normalized_mode(path: Path, item_stat: os.stat_result) -> str:
    executable = bool(item_stat.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    try:
        with path.open("rb") as handle:
            head = handle.read(4)
    except OSError as exc:
        raise PackageError(f"cannot inspect executable format of {path}: {exc}") from exc
    if path.suffix.lower() == ".sh" or head == b"\x7fELF":
        executable = True
    return "0755" if executable else "0644"


def scan_tree(
    root: Path,
    *,
    cache_policy: str,
    allow_package_metadata: bool,
) -> tuple[list[FileRecord], list[str], dict[str, Path]]:
    if not root.is_dir():
        raise PackageError(f"application/package directory does not exist: {root}")
    root_stat = root.lstat()
    if is_reparse_or_symlink(root, root_stat):
        raise PackageError(f"root directory must not be a symlink or reparse point: {root}")

    records: list[FileRecord] = []
    excluded: list[str] = []
    metadata: dict[str, Path] = {}
    total_bytes = 0

    def visit(directory: Path, prefix: str) -> None:
        nonlocal total_bytes
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise PackageError(f"cannot enumerate {directory}: {exc}") from exc
        for entry in entries:
            path = Path(entry.path)
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            relative = relative.replace("\\", "/")
            validate_relative_name(relative)
            try:
                item_stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise PackageError(f"cannot inspect {relative!r}: {exc}") from exc
            if is_reparse_or_symlink(path, item_stat):
                raise PackageError(
                    f"symlink/reparse-point entries are not permitted: {relative!r}"
                )
            is_directory = stat.S_ISDIR(item_stat.st_mode)
            if is_cache_path(relative, is_directory):
                if cache_policy == "reject":
                    raise PackageError(f"cache/junk entry present in package: {relative!r}")
                excluded.append(relative + ("/" if is_directory else ""))
                continue
            if is_directory:
                visit(path, relative)
                continue
            if not stat.S_ISREG(item_stat.st_mode):
                raise PackageError(f"special files are not permitted: {relative!r}")
            if relative in RESERVED_NAMES:
                if not allow_package_metadata:
                    raise PackageError(
                        f"source app contains reserved package metadata: {relative!r}"
                    )
                metadata[relative] = path
                continue
            reject_secret_name(relative)
            if item_stat.st_size > MAX_FILE_BYTES:
                raise PackageError(
                    f"file exceeds {MAX_FILE_BYTES} byte limit: {relative!r}"
                )
            total_bytes += item_stat.st_size
            if total_bytes > MAX_TOTAL_BYTES:
                raise PackageError(
                    f"application exceeds {MAX_TOTAL_BYTES} byte package limit"
                )
            reject_secret_content(path, relative)
            records.append(
                FileRecord(
                    source=path,
                    relative=relative,
                    size=item_stat.st_size,
                    sha256=sha256_file(path),
                    mode=normalized_mode(path, item_stat),
                )
            )

    visit(root, "")
    return (
        sorted(records, key=lambda record: record.relative),
        sorted(excluded),
        metadata,
    )


def load_json_object(path: Path) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PackageError(f"duplicate JSON key {key!r} in {path}")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates)
    except PackageError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"invalid JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PackageError(f"JSON root must be an object: {path}")
    return value


def relative_validator_path(path: Path) -> str:
    try:
        return path.resolve(strict=True).relative_to(plugin_root().resolve(strict=True)).as_posix()
    except (OSError, ValueError) as exc:
        raise PackageError(f"validator is outside the plugin or unavailable: {path}") from exc


def normalize_findings(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise PackageError("validator findings must be an array")
    result: list[dict[str, str]] = []
    for finding in value:
        if not isinstance(finding, dict) or set(finding) != {"severity", "path", "message"}:
            raise PackageError("validator finding has an invalid shape")
        normalized = {
            "severity": str(finding["severity"]),
            "path": str(finding["path"]),
            "message": str(finding["message"]),
        }
        if normalized["severity"] not in {"error", "warning"}:
            raise PackageError("validator finding severity is invalid")
        result.append(normalized)
    return sorted(result, key=lambda item: canonical_bytes(item))


def run_json_command(command: list[str], label: str) -> tuple[int, Any]:
    try:
        result = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PackageError(f"{label} could not run: {exc}") from exc
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        details = result.stderr.strip() or result.stdout[:500]
        raise PackageError(f"{label} returned invalid JSON: {details}") from exc
    return result.returncode, payload


def run_validators(app_root: Path, os_release: str) -> tuple[dict[str, Any], dict[str, Any]]:
    app_validator = app_validator_path().resolve(strict=True)
    shared_validator = shared_validator_path().resolve(strict=True)
    manifest = app_root / "redpitaya-project.json"

    app_build_rc, app_build = run_json_command(
        [
            sys.executable,
            str(app_validator),
            str(app_root),
            "--os-release",
            os_release,
            "--json",
        ],
        "application build validator",
    )
    app_ready_rc, app_ready = run_json_command(
        [
            sys.executable,
            str(app_validator),
            str(app_root),
            "--os-release",
            os_release,
            "--deployment-ready",
            "--json",
        ],
        "application deployment-ready validator",
    )
    project_build_rc, project_build = run_json_command(
        [sys.executable, str(shared_validator), str(manifest), "--json"],
        "shared project validator",
    )
    project_strict_rc, project_strict = run_json_command(
        [sys.executable, str(shared_validator), str(manifest), "--strict", "--json"],
        "strict shared project validator",
    )

    if not isinstance(app_build, dict) or not isinstance(app_ready, dict):
        raise PackageError("application validator JSON root must be an object")
    app_build_errors = normalize_findings(app_build.get("errors"))
    app_build_warnings = normalize_findings(app_build.get("warnings"))
    app_ready_errors = normalize_findings(app_ready.get("errors"))
    app_ready_warnings = normalize_findings(app_ready.get("warnings"))
    project_findings = normalize_findings(project_build)
    project_strict_findings = normalize_findings(project_strict)

    checks = {
        "application_build": {
            "exit_code": app_build_rc,
            "ok": app_build.get("ok") is True,
            "errors": app_build_errors,
            "warnings": app_build_warnings,
        },
        "application_deployment_ready": {
            "exit_code": app_ready_rc,
            "ok": app_ready.get("ok") is True,
            "errors": app_ready_errors,
            "warnings": app_ready_warnings,
        },
        "project_build": {
            "exit_code": project_build_rc,
            "ok": project_build_rc == 0
            and not any(item["severity"] == "error" for item in project_findings),
            "findings": project_findings,
        },
        "project_strict": {
            "exit_code": project_strict_rc,
            "ok": project_strict_rc == 0 and not project_strict_findings,
            "findings": project_strict_findings,
        },
    }
    validators = {
        "application": {
            "path": relative_validator_path(app_validator),
            "sha256": sha256_file(app_validator),
        },
        "project": {
            "path": relative_validator_path(shared_validator),
            "sha256": sha256_file(shared_validator),
        },
    }
    return checks, validators


def validator_errors(checks: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    errors.extend(checks["application_build"]["errors"])
    errors.extend(
        item
        for item in checks["project_build"]["findings"]
        if item["severity"] == "error"
    )
    unique = {canonical_bytes(item): item for item in errors}
    return [unique[key] for key in sorted(unique)]


def validator_warnings(checks: dict[str, Any]) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    warnings.extend(checks["application_build"]["warnings"])
    warnings.extend(
        item
        for item in checks["project_build"]["findings"]
        if item["severity"] == "warning"
    )
    unique = {canonical_bytes(item): item for item in warnings}
    return [unique[key] for key in sorted(unique)]


def require_valid_build(checks: dict[str, Any]) -> None:
    errors = validator_errors(checks)
    if errors or checks["application_build"]["exit_code"] != 0:
        details = " | ".join(
            f"{item['path']}: {item['message']}" for item in errors
        )
        raise PackageError(f"application build validation failed: {details}")
    if checks["project_build"]["exit_code"] != 0:
        raise PackageError("shared project build validation failed")


def is_deployment_ready(checks: dict[str, Any]) -> bool:
    return (
        checks["application_deployment_ready"]["ok"] is True
        and checks["application_deployment_ready"]["exit_code"] == 0
        and checks["project_strict"]["ok"] is True
        and checks["project_strict"]["exit_code"] == 0
        and not validator_errors(checks)
        and not validator_warnings(checks)
    )


def copy_payload(records: Iterable[FileRecord], destination: Path) -> list[FileRecord]:
    copied: list[FileRecord] = []
    for record in records:
        target = destination / Path(record.relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(record.source, target)
        os.chmod(target, int(record.mode, 8))
        observed_hash = sha256_file(target)
        if observed_hash != record.sha256 or target.stat().st_size != record.size:
            raise PackageError(
                f"source changed or copy verification failed: {record.relative!r}"
            )
        copied.append(
            FileRecord(
                source=target,
                relative=record.relative,
                size=record.size,
                sha256=record.sha256,
                mode=record.mode,
            )
        )
    return copied


def transient_rename_error(exc: OSError) -> bool:
    if isinstance(exc, FileExistsError):
        return False
    if os.name == "nt":
        return getattr(exc, "winerror", None) in {5, 32, 33}
    return exc.errno in {errno.EACCES, errno.EBUSY, errno.EINTR, errno.ETXTBSY}


def _posix_rename_noreplace(source: Path, destination: Path) -> bool:
    """Use an OS no-replace primitive when one is available."""
    if os.name != "posix":
        return False
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is not None:
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(-100, source_bytes, -100, destination_bytes, 1)
        if result == 0:
            return True
        observed_errno = ctypes.get_errno()
        if observed_errno == errno.EEXIST:
            raise FileExistsError(destination)
        if observed_errno not in {errno.ENOSYS, errno.EINVAL}:
            raise OSError(observed_errno, os.strerror(observed_errno))
    renamex_np = getattr(libc, "renamex_np", None)
    if renamex_np is not None:
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(source_bytes, destination_bytes, 0x00000004)
        if result == 0:
            return True
        observed_errno = ctypes.get_errno()
        if observed_errno == errno.EEXIST:
            raise FileExistsError(destination)
        if observed_errno not in {errno.ENOSYS, errno.EINVAL}:
            raise OSError(observed_errno, os.strerror(observed_errno))
    return False


def atomic_rename_noreplace(source: Path, destination: Path) -> None:
    delay = RENAME_INITIAL_DELAY_SECONDS
    for attempt in range(RENAME_ATTEMPTS):
        if path_exists(destination):
            raise PackageError(f"refusing to overwrite existing path: {destination}")
        try:
            if os.name == "posix" and _posix_rename_noreplace(source, destination):
                return
            # Windows MoveFileEx without replacement is the behavior of rename.
            # The checked fallback is retained for less common POSIX platforms
            # lacking renameat2/renamex_np.
            os.rename(source, destination)
            return
        except FileExistsError as exc:
            raise PackageError(
                f"refusing to overwrite concurrently-created path: {destination}"
            ) from exc
        except OSError as exc:
            if attempt + 1 >= RENAME_ATTEMPTS or not transient_rename_error(exc):
                raise PackageError(
                    f"cannot atomically publish {destination}: {exc}"
                ) from exc
            time.sleep(delay)
            delay = min(delay * 2, 0.5)
    raise PackageError(f"cannot atomically publish {destination}")


def guarded_remove_staging(path: Path, expected_parent: Path, prefix: str) -> None:
    if not path_exists(path):
        return
    resolved_parent = path.parent.resolve(strict=True)
    if resolved_parent != expected_parent.resolve(strict=True):
        raise PackageError(f"refusing cleanup outside staging parent: {path}")
    if not path.name.startswith(prefix) or is_reparse_or_symlink(path):
        raise PackageError(f"refusing unsafe staging cleanup: {path}")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def make_staging_directory(output: Path) -> Path:
    parent = output.parent.resolve(strict=True)
    for _ in range(100):
        stage = parent / f".{output.name}.package-staging-{secrets.token_hex(8)}"
        try:
            stage.mkdir()
            return stage
        except FileExistsError:
            continue
    raise PackageError("could not allocate a unique package staging directory")


def build_receipt(
    manifest: dict[str, Any],
    records: list[FileRecord],
    excluded: list[str],
    validators: dict[str, Any],
    checks: dict[str, Any],
) -> dict[str, Any]:
    inventory = [record.inventory_item() for record in records]
    warnings = validator_warnings(checks)
    errors = validator_errors(checks)
    deployment_ready = is_deployment_ready(checks)
    project = manifest["project"]
    target = manifest["target"]
    application = manifest["application"]
    core = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "kind": "red-pitaya-application-directory-bundle",
        "tool": {
            "name": "package_red_pitaya_app.py",
            "version": TOOL_VERSION,
        },
        "classification": (
            "deployment-ready-directory-bundle"
            if deployment_ready
            else "deployment-blocked-directory-bundle"
        ),
        "attestation": "self-attested-offline",
        "deployment_ready": deployment_ready,
        "deployment_authorized": False,
        "transport_zip_deployable": False,
        "application": {
            "slug": application["slug"],
            "version": project["version"],
            "os_release": target["os_release"],
            "model": target["model"],
            "architecture": application["architecture"],
            "manifest_sha256": next(
                item["sha256"]
                for item in inventory
                if item["path"] == "redpitaya-project.json"
            ),
        },
        "payload_inventory": inventory,
        "payload_inventory_sha256": canonical_sha256(inventory),
        "excluded_cache_paths": excluded,
        "validators": validators,
        "checks": checks,
        "validation_summary": {
            "error_count": len(errors),
            "warning_count": len(warnings),
            "warnings_block_deployment": True,
            "deployment_blocked": not deployment_ready,
            "blockers": warnings if not errors else errors + warnings,
        },
    }
    return {**core, "receipt_sha256": canonical_sha256(core)}


def write_canonical_json(path: Path, value: Any) -> None:
    path.write_bytes(canonical_bytes(value))
    os.chmod(path, 0o644)


def create_transport_zip(package_root: Path, destination: Path) -> None:
    if destination.suffix.lower() != ".zip":
        raise PackageError("transport ZIP output must end in .zip")
    if path_exists(destination):
        raise PackageError(f"refusing to overwrite existing ZIP: {destination}")
    parent = destination.parent.resolve(strict=True)
    stage = parent / f".{destination.name}.package-staging-{secrets.token_hex(8)}"
    try:
        records, _excluded, metadata = scan_tree(
            package_root,
            cache_policy="reject",
            allow_package_metadata=True,
        )
        all_files = {record.relative: record.source for record in records}
        all_files.update({name: path for name, path in metadata.items()})
        all_modes = {record.relative: record.mode for record in records}
        all_modes.update({name: "0644" for name in metadata})
        with stage.open("xb") as stream:
            with zipfile.ZipFile(
                stream,
                mode="w",
                compression=zipfile.ZIP_STORED,
                strict_timestamps=True,
            ) as archive:
                archive.comment = ZIP_COMMENT
                marker = zipfile.ZipInfo(TRANSPORT_MARKER_NAME, FIXED_ZIP_TIMESTAMP)
                marker.create_system = 3
                marker.compress_type = zipfile.ZIP_STORED
                marker.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(marker, TRANSPORT_MARKER.encode("utf-8"))
                for relative in sorted(all_files):
                    source = all_files[relative]
                    name = f"{package_root.name}/{relative}"
                    info = zipfile.ZipInfo(name, FIXED_ZIP_TIMESTAMP)
                    info.create_system = 3
                    info.compress_type = zipfile.ZIP_STORED
                    mode = int(all_modes[relative], 8)
                    info.external_attr = (stat.S_IFREG | mode) << 16
                    with archive.open(info, "w", force_zip64=True) as member:
                        with source.open("rb") as source_stream:
                            shutil.copyfileobj(source_stream, member, 1024 * 1024)
        atomic_rename_noreplace(stage, destination)
    except Exception:
        guarded_remove_staging(
            stage,
            parent,
            f".{destination.name}.package-staging-",
        )
        raise


def create_package(
    app_root: Path,
    output: Path,
    transport_zip: Path | None,
    expected_os: str | None,
) -> dict[str, Any]:
    app_root = resolve_nonlinked_directory(app_root, "source application")
    output = unresolved_absolute(output).resolve(strict=False)
    if path_exists(output):
        raise PackageError(f"refusing to overwrite existing output: {output}")
    if not output.parent.is_dir():
        raise PackageError(f"output parent directory does not exist: {output.parent}")
    try:
        output.parent.resolve(strict=True).relative_to(app_root)
    except ValueError:
        pass
    else:
        raise PackageError("package output must not be inside the source application")
    if transport_zip is not None:
        transport_zip = transport_zip.expanduser().resolve(strict=False)
        if transport_zip.suffix.lower() != ".zip":
            raise PackageError("transport ZIP output must end in .zip")
        if not transport_zip.parent.is_dir():
            raise PackageError(
                f"transport ZIP parent does not exist: {transport_zip.parent}"
            )
        if path_exists(transport_zip):
            raise PackageError(
                f"refusing to overwrite existing ZIP: {transport_zip}"
            )
        try:
            transport_zip.parent.resolve(strict=True).relative_to(app_root)
        except ValueError:
            pass
        else:
            raise PackageError(
                "transport ZIP output must not be inside the source application"
            )

    records, excluded, metadata = scan_tree(
        app_root,
        cache_policy="exclude",
        allow_package_metadata=False,
    )
    if metadata:
        raise PackageError("source contains reserved package metadata")
    manifest_path = app_root / "redpitaya-project.json"
    manifest = load_json_object(manifest_path)
    try:
        slug = manifest["application"]["slug"]
        os_release = manifest["target"]["os_release"]
    except (KeyError, TypeError) as exc:
        raise PackageError("manifest lacks application.slug or target.os_release") from exc
    if not isinstance(slug, str) or output.name != slug:
        raise PackageError(
            f"package directory name must exactly match application slug {slug!r}"
        )
    if expected_os is not None and os_release != expected_os:
        raise PackageError(
            f"expected OS {expected_os!r}, manifest targets {os_release!r}"
        )
    if os_release not in {"2.00-35", "2.07-48", "3.00-57"}:
        raise PackageError(f"unsupported target OS release: {os_release!r}")

    stage_container = make_staging_directory(output)
    stage = stage_container / output.name
    stage.mkdir()
    stage_parent = output.parent.resolve(strict=True)
    try:
        copied = copy_payload(records, stage)
        # Validate the exact sanitized payload. Cache/junk entries are deliberately
        # omitted before validation so they cannot change architecture detection.
        checks, validators = run_validators(stage, os_release)
        require_valid_build(checks)
        receipt = build_receipt(
            manifest,
            copied,
            excluded,
            validators,
            checks,
        )
        write_canonical_json(stage / RECEIPT_NAME, receipt)
        if not receipt["deployment_ready"]:
            marker = stage / BLOCKED_MARKER_NAME
            marker.write_text(BLOCKED_MARKER, encoding="utf-8", newline="\n")
            os.chmod(marker, 0o644)
        # Validate the exact final staged directory, including metadata.
        validate_package(stage, None, require_deployment_ready=False)
        atomic_rename_noreplace(stage, output)
        stage_container.rmdir()
    except Exception:
        guarded_remove_staging(
            stage_container,
            stage_parent,
            f".{output.name}.package-staging-",
        )
        raise

    zip_hash = None
    if transport_zip is not None:
        create_transport_zip(output, transport_zip)
        validate_transport_zip(output, transport_zip)
        zip_hash = sha256_file(transport_zip)
    return {
        "ok": True,
        "package": str(output),
        "receipt": str(output / RECEIPT_NAME),
        "deployment_ready": receipt["deployment_ready"],
        "deployment_authorized": False,
        "warning_count": receipt["validation_summary"]["warning_count"],
        "payload_inventory_sha256": receipt["payload_inventory_sha256"],
        "receipt_sha256": receipt["receipt_sha256"],
        "transport_zip": str(transport_zip) if transport_zip else None,
        "transport_zip_sha256": zip_hash,
        "transport_zip_deployable": False if transport_zip else None,
    }


def validate_receipt_shape(receipt: dict[str, Any]) -> None:
    if set(receipt) != RECEIPT_KEYS:
        raise PackageError(
            "receipt keys differ from the exact schema: "
            f"missing={sorted(RECEIPT_KEYS - set(receipt))}, "
            f"unknown={sorted(set(receipt) - RECEIPT_KEYS)}"
        )
    if receipt["schema_version"] != RECEIPT_SCHEMA_VERSION:
        raise PackageError("unsupported receipt schema_version")
    if receipt["kind"] != "red-pitaya-application-directory-bundle":
        raise PackageError("receipt kind is invalid")
    if receipt["attestation"] != "self-attested-offline":
        raise PackageError("receipt attestation is invalid")
    if receipt["deployment_authorized"] is not False:
        raise PackageError("offline receipt must never grant deployment authorization")
    if receipt["transport_zip_deployable"] is not False:
        raise PackageError("transport ZIP must never be classified as deployable")
    if not isinstance(receipt["deployment_ready"], bool):
        raise PackageError("receipt deployment_ready must be boolean")
    expected_classification = (
        "deployment-ready-directory-bundle"
        if receipt["deployment_ready"]
        else "deployment-blocked-directory-bundle"
    )
    if receipt["classification"] != expected_classification:
        raise PackageError("receipt classification conflicts with deployment_ready")
    tool = receipt["tool"]
    if tool != {"name": "package_red_pitaya_app.py", "version": TOOL_VERSION}:
        raise PackageError("receipt tool identity/version is invalid")
    excluded = receipt["excluded_cache_paths"]
    if (
        not isinstance(excluded, list)
        or excluded != sorted(set(excluded))
        or any(not isinstance(item, str) for item in excluded)
    ):
        raise PackageError(
            "receipt excluded_cache_paths must be a sorted unique string array"
        )
    for item in excluded:
        directory = item.endswith("/")
        normalized = item[:-1] if directory else item
        validate_relative_name(normalized)
        if not is_cache_path(normalized, directory):
            raise PackageError(
                f"receipt claims a non-cache exclusion: {item!r}"
            )


def validate_package(
    package_root: Path,
    transport_zip: Path | None,
    *,
    require_deployment_ready: bool,
) -> dict[str, Any]:
    package_root = resolve_nonlinked_directory(package_root, "package")
    records, excluded, metadata = scan_tree(
        package_root,
        cache_policy="reject",
        allow_package_metadata=True,
    )
    if excluded:
        raise PackageError("validated package unexpectedly excluded cache paths")
    receipt_path = metadata.get(RECEIPT_NAME)
    if receipt_path is None:
        raise PackageError(f"package is missing {RECEIPT_NAME}")
    if TRANSPORT_MARKER_NAME in metadata:
        raise PackageError("transport-only marker must not be inside directory bundle")
    receipt = load_json_object(receipt_path)
    validate_receipt_shape(receipt)
    if receipt_path.read_bytes() != canonical_bytes(receipt):
        raise PackageError("receipt JSON is not in canonical byte form")
    core = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if receipt["receipt_sha256"] != canonical_sha256(core):
        raise PackageError("receipt_sha256 does not match canonical receipt core")

    manifest = load_json_object(package_root / "redpitaya-project.json")
    try:
        slug = manifest["application"]["slug"]
        os_release = manifest["target"]["os_release"]
    except (KeyError, TypeError) as exc:
        raise PackageError("packaged manifest lacks slug/OS fields") from exc
    if package_root.name != slug:
        raise PackageError("package directory name does not match application slug")

    observed_inventory = [record.inventory_item() for record in records]
    if observed_inventory != receipt["payload_inventory"]:
        raise PackageError("payload inventory differs from receipt")
    if canonical_sha256(observed_inventory) != receipt["payload_inventory_sha256"]:
        raise PackageError("payload inventory hash differs from receipt")
    manifest_item = next(
        (item for item in observed_inventory if item["path"] == "redpitaya-project.json"),
        None,
    )
    if manifest_item is None:
        raise PackageError("payload inventory omits redpitaya-project.json")
    if receipt["application"].get("manifest_sha256") != manifest_item["sha256"]:
        raise PackageError("receipt manifest hash differs from payload")
    expected_application = {
        "slug": manifest["application"]["slug"],
        "version": manifest["project"]["version"],
        "os_release": manifest["target"]["os_release"],
        "model": manifest["target"]["model"],
        "architecture": manifest["application"]["architecture"],
        "manifest_sha256": manifest_item["sha256"],
    }
    if receipt["application"] != expected_application:
        raise PackageError("receipt application identity differs from manifest")

    expected_marker = not receipt["deployment_ready"]
    marker_path = metadata.get(BLOCKED_MARKER_NAME)
    if expected_marker and marker_path is None:
        raise PackageError("deployment-blocked bundle lacks DEPLOYMENT_BLOCKED.txt")
    if not expected_marker and marker_path is not None:
        raise PackageError("deployment-ready bundle contains a blocked marker")
    if marker_path is not None and marker_path.read_text(encoding="utf-8") != BLOCKED_MARKER:
        raise PackageError("DEPLOYMENT_BLOCKED.txt content is invalid")

    current_validators = {
        "application": {
            "path": relative_validator_path(app_validator_path()),
            "sha256": sha256_file(app_validator_path()),
        },
        "project": {
            "path": relative_validator_path(shared_validator_path()),
            "sha256": sha256_file(shared_validator_path()),
        },
    }
    if receipt["validators"] != current_validators:
        raise PackageError("validator provenance differs from the current bundled validators")
    checks, validators = run_validators(package_root, os_release)
    if validators != receipt["validators"] or checks != receipt["checks"]:
        raise PackageError("fresh validator results differ from the receipt")
    require_valid_build(checks)
    observed_ready = is_deployment_ready(checks)
    if receipt["deployment_ready"] is not observed_ready:
        raise PackageError("receipt deployment readiness differs from fresh validation")
    summary = receipt["validation_summary"]
    expected_summary = {
        "error_count": len(validator_errors(checks)),
        "warning_count": len(validator_warnings(checks)),
        "warnings_block_deployment": True,
        "deployment_blocked": not observed_ready,
        "blockers": (
            validator_warnings(checks)
            if not validator_errors(checks)
            else validator_errors(checks) + validator_warnings(checks)
        ),
    }
    if summary != expected_summary:
        raise PackageError("receipt validation_summary differs from fresh validation")
    if require_deployment_ready and not observed_ready:
        raise PackageError("package is valid but remains deployment-blocked")
    if transport_zip is not None:
        validate_transport_zip(package_root, transport_zip)
    return {
        "ok": True,
        "valid": True,
        "package": str(package_root),
        "deployment_ready": observed_ready,
        "deployment_authorized": False,
        "warning_count": summary["warning_count"],
        "payload_inventory_sha256": receipt["payload_inventory_sha256"],
        "receipt_sha256": receipt["receipt_sha256"],
        "transport_zip": str(transport_zip) if transport_zip else None,
        "transport_zip_deployable": False if transport_zip else None,
    }


def validate_transport_zip(package_root: Path, archive_path: Path) -> None:
    archive_path = unresolved_absolute(archive_path)
    if (
        not archive_path.is_file()
        or is_reparse_or_symlink(archive_path, archive_path.lstat())
    ):
        raise PackageError(
            f"transport ZIP is not a non-linked regular file: {archive_path}"
        )
    records, _excluded, metadata = scan_tree(
        package_root,
        cache_policy="reject",
        allow_package_metadata=True,
    )
    all_files = {record.relative: record.source for record in records}
    all_files.update({name: path for name, path in metadata.items()})
    all_modes = {record.relative: record.mode for record in records}
    all_modes.update({name: "0644" for name in metadata})
    expected_names = [TRANSPORT_MARKER_NAME] + [
        f"{package_root.name}/{relative}" for relative in sorted(all_files)
    ]
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if names != expected_names:
                raise PackageError("transport ZIP inventory/order differs from package")
            if archive.comment != ZIP_COMMENT:
                raise PackageError("transport ZIP comment is missing or invalid")
            for info in infos:
                if info.date_time != FIXED_ZIP_TIMESTAMP:
                    raise PackageError(f"ZIP timestamp is not reproducible: {info.filename}")
                if info.is_dir() or "\\" in info.filename:
                    raise PackageError(f"unsafe/non-file ZIP member: {info.filename!r}")
                parts = Path(info.filename).parts
                if info.filename.startswith("/") or ".." in parts:
                    raise PackageError(f"unsafe ZIP member path: {info.filename!r}")
                expected_mode = 0o644
                if info.filename != TRANSPORT_MARKER_NAME:
                    prefix = f"{package_root.name}/"
                    relative = info.filename[len(prefix):]
                    expected_mode = int(all_modes[relative], 8)
                observed_mode = (info.external_attr >> 16) & 0o7777
                if info.create_system != 3 or observed_mode != expected_mode:
                    raise PackageError(
                        f"ZIP mode/platform metadata is invalid: {info.filename}"
                    )
                if info.compress_type != zipfile.ZIP_STORED:
                    raise PackageError(
                        f"ZIP compression method is invalid: {info.filename}"
                    )
            if archive.read(TRANSPORT_MARKER_NAME) != TRANSPORT_MARKER.encode("utf-8"):
                raise PackageError("transport-only marker content is invalid")
            for relative, source in sorted(all_files.items()):
                name = f"{package_root.name}/{relative}"
                digest = hashlib.sha256()
                size = 0
                with archive.open(name, "r") as member:
                    while True:
                        block = member.read(1024 * 1024)
                        if not block:
                            break
                        digest.update(block)
                        size += len(block)
                if digest.hexdigest() != sha256_file(source) or size != source.stat().st_size:
                    raise PackageError(f"ZIP member differs from directory bundle: {name}")
            bad_member = archive.testzip()
            if bad_member is not None:
                raise PackageError(f"transport ZIP CRC failure: {bad_member}")
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, PackageError):
            raise
        raise PackageError(f"invalid transport ZIP: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", dest="as_json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="create a new immutable directory bundle")
    create.add_argument("--app", required=True, type=Path)
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--transport-zip", type=Path)
    create.add_argument(
        "--os-release",
        choices=("2.00-35", "2.07-48", "3.00-57"),
    )

    validate = subparsers.add_parser("validate", help="revalidate package integrity/provenance")
    validate.add_argument("--package", required=True, type=Path)
    validate.add_argument("--transport-zip", type=Path)
    validate.add_argument("--require-deployment-ready", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            payload = create_package(
                args.app,
                args.output,
                args.transport_zip,
                args.os_release,
            )
        else:
            payload = validate_package(
                args.package,
                args.transport_zip,
                require_deployment_ready=args.require_deployment_ready,
            )
    except (PackageError, FileNotFoundError, KeyError, OSError) as exc:
        payload = {"ok": False, "error": str(exc)}
        if args.as_json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Package: {payload['package']}")
        print(f"Deployment ready: {payload['deployment_ready']}")
        print("Deployment authorized: false")
        if payload.get("transport_zip"):
            print(f"Transport ZIP: {payload['transport_zip']}")
            print("Transport ZIP deployable: false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
