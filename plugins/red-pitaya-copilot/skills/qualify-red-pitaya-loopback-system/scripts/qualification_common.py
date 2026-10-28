#!/usr/bin/env python3
"""Shared, dependency-free helpers for offline qualification artifacts."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
SSH_SHA256_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}=?$")
UTC_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)


class QualificationError(ValueError):
    """Raised when an offline qualification artifact is unsafe or invalid."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not UTC_RE.fullmatch(value):
        raise QualificationError(f"{label} must use YYYY-MM-DDTHH:MM:SSZ")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise QualificationError(f"{label} is not a real UTC timestamp") from exc
    return parsed.replace(tzinfo=timezone.utc)


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QualificationError(f"{label} must be a JSON object")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise QualificationError(f"{label} must be a JSON array")
    return value


def require_exact_keys(
    value: dict[str, Any],
    expected: set[str],
    label: str,
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing {missing}")
        if extra:
            details.append(f"unexpected {extra}")
        raise QualificationError(f"{label} keys are invalid: {', '.join(details)}")


def require_safe_text(
    value: Any,
    label: str,
    *,
    maximum: int = 500,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise QualificationError(f"{label} must be a string")
    if value != value.strip():
        raise QualificationError(f"{label} must not have surrounding whitespace")
    if not allow_empty and not value:
        raise QualificationError(f"{label} must not be empty")
    if len(value) > maximum:
        raise QualificationError(f"{label} exceeds {maximum} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise QualificationError(f"{label} contains control characters")
    return value


def require_safe_id(value: Any, label: str) -> str:
    text = require_safe_text(value, label, maximum=96)
    if not SAFE_ID_RE.fullmatch(text):
        raise QualificationError(
            f"{label} must contain only letters, digits, dot, underscore or hyphen"
        )
    return text


def require_ssh_sha256_fingerprint(value: Any, label: str) -> str:
    text = require_safe_text(value, label, maximum=60)
    if not SSH_SHA256_RE.fullmatch(text):
        raise QualificationError(f"{label} is not an OpenSSH SHA256 fingerprint")
    payload = text.removeprefix("SHA256:")
    padded = payload + ("=" * ((4 - len(payload) % 4) % 4))
    try:
        decoded = base64.b64decode(padded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise QualificationError(f"{label} is not valid base64") from exc
    if len(decoded) != 32:
        raise QualificationError(f"{label} must decode to exactly 32 bytes")
    return text


def require_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise QualificationError(f"{label} must be true or false")
    return value


def require_int(
    value: Any,
    label: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if type(value) is not int:
        raise QualificationError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise QualificationError(f"{label} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise QualificationError(f"{label} must be at most {maximum}")
    return value


def require_number(
    value: Any,
    label: str,
    *,
    minimum: float | None = None,
    exclusive_minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QualificationError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise QualificationError(f"{label} must be a finite number")
    if minimum is not None and number < minimum:
        raise QualificationError(f"{label} must be at least {minimum}")
    if exclusive_minimum is not None and number <= exclusive_minimum:
        raise QualificationError(f"{label} must be greater than {exclusive_minimum}")
    if maximum is not None and number > maximum:
        raise QualificationError(f"{label} must be at most {maximum}")
    return number


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        data = path.read_text(encoding="utf-8")
        value = json.loads(data)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificationError(f"cannot read valid {label} JSON {path}: {exc}") from exc
    return require_object(value, label)


def resolve_project_file(
    project_root: Path,
    path: Path,
    label: str,
    *,
    maximum_bytes: int = 50 * 1024 * 1024,
) -> tuple[Path, str]:
    root = project_root.resolve()
    candidate = path if path.is_absolute() else root / path
    if candidate.is_symlink():
        raise QualificationError(f"{label} must not be a symlink")
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise QualificationError(f"{label} must be inside project root") from exc
    if not resolved.is_file():
        raise QualificationError(f"{label} is not a regular file: {resolved}")
    size = resolved.stat().st_size
    if size < 1:
        raise QualificationError(f"{label} must not be empty")
    if size > maximum_bytes:
        raise QualificationError(
            f"{label} exceeds the {maximum_bytes}-byte offline evidence limit"
        )
    return resolved, relative.as_posix()


def file_binding(
    project_root: Path,
    path: Path,
    label: str,
) -> tuple[dict[str, Any], Path]:
    resolved, relative = resolve_project_file(project_root, path, label)
    return (
        {
            "path": relative,
            "sha256": sha256_file(resolved),
            "size_bytes": resolved.stat().st_size,
        },
        resolved,
    )


def verify_file_binding(
    project_root: Path,
    binding: Any,
    label: str,
) -> Path:
    record = require_object(binding, label)
    require_exact_keys(record, {"path", "sha256", "size_bytes"}, label)
    relative = Path(require_safe_text(record["path"], f"{label}.path", maximum=500))
    if relative.is_absolute() or ".." in relative.parts:
        raise QualificationError(f"{label}.path must be a safe project-relative path")
    resolved, canonical_relative = resolve_project_file(
        project_root,
        relative,
        f"{label}.path",
    )
    if canonical_relative != relative.as_posix():
        raise QualificationError(f"{label}.path is not canonical")
    expected_hash = require_safe_text(record["sha256"], f"{label}.sha256", maximum=64)
    if not HASH_RE.fullmatch(expected_hash):
        raise QualificationError(f"{label}.sha256 is invalid")
    if sha256_file(resolved) != expected_hash:
        raise QualificationError(f"{label} file hash no longer matches")
    size = require_int(record["size_bytes"], f"{label}.size_bytes", minimum=1)
    if resolved.stat().st_size != size:
        raise QualificationError(f"{label} file size no longer matches")
    return resolved


def exclusive_output_path(path: Path, label: str) -> Path:
    if path.exists() or path.is_symlink():
        raise QualificationError(f"{label} already exists: {path}")
    parent = path.parent
    if parent.is_symlink():
        raise QualificationError(f"{label} parent must not be a symlink")
    if not parent.exists() or not parent.is_dir():
        raise QualificationError(f"{label} parent directory does not exist: {parent}")
    return path


def project_output_path(project_root: Path, path: Path, label: str) -> Path:
    root = project_root.resolve()
    candidate = path if path.is_absolute() else root / path
    if candidate.exists() or candidate.is_symlink():
        raise QualificationError(f"{label} already exists: {candidate}")
    lexical_parent = candidate.parent
    if not lexical_parent.exists() or not lexical_parent.is_dir():
        raise QualificationError(
            f"{label} parent directory does not exist: {lexical_parent}"
        )
    current = lexical_parent
    while True:
        if current.is_symlink():
            raise QualificationError(f"{label} path contains a symlink: {current}")
        if current.resolve() == root:
            break
        if current.parent == current:
            raise QualificationError(f"{label} must be inside project root")
        current = current.parent
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise QualificationError(f"{label} must be inside project root") from exc
    return resolved


def write_bytes_exclusive(path: Path, payload: bytes, label: str) -> None:
    exclusive_output_path(path, label)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def write_json_exclusive(path: Path, value: Any, label: str) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    write_bytes_exclusive(path, payload, label)


def voltage_after_attenuation(volts_peak: float, attenuation_db: float) -> float:
    return volts_peak * (10.0 ** (-attenuation_db / 20.0))
