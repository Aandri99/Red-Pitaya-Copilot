#!/usr/bin/env python3
"""Dependency-free helpers for offline single-board qualification artifacts."""

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
    """Raised when an offline artifact is unsafe, inconsistent or unverifiable."""


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


def exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing {missing}")
        if extra:
            details.append(f"unexpected {extra}")
        raise QualificationError(f"{label} keys invalid: {', '.join(details)}")


def as_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QualificationError(f"{label} must be an object")
    return value


def as_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise QualificationError(f"{label} must be an array")
    return value


def as_text(
    value: Any,
    label: str,
    *,
    maximum: int = 500,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise QualificationError(f"{label} must be a string")
    if value != value.strip():
        raise QualificationError(f"{label} has surrounding whitespace")
    if not value and not allow_empty:
        raise QualificationError(f"{label} must not be empty")
    if len(value) > maximum:
        raise QualificationError(f"{label} exceeds {maximum} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise QualificationError(f"{label} contains control characters")
    return value


def as_id(value: Any, label: str) -> str:
    text = as_text(value, label, maximum=96)
    if not SAFE_ID_RE.fullmatch(text):
        raise QualificationError(f"{label} is not a safe identifier")
    return text


def as_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise QualificationError(f"{label} must be true or false")
    return value


def as_int(
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


def as_number(
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
        raise QualificationError(f"{label} must be finite")
    if minimum is not None and number < minimum:
        raise QualificationError(f"{label} must be at least {minimum}")
    if exclusive_minimum is not None and number <= exclusive_minimum:
        raise QualificationError(f"{label} must be greater than {exclusive_minimum}")
    if maximum is not None and number > maximum:
        raise QualificationError(f"{label} must be at most {maximum}")
    return number


def as_fingerprint(value: Any, label: str) -> str:
    text = as_text(value, label, maximum=60)
    if not SSH_SHA256_RE.fullmatch(text):
        raise QualificationError(f"{label} is not an OpenSSH SHA256 fingerprint")
    payload = text.removeprefix("SHA256:")
    payload += "=" * ((4 - len(payload) % 4) % 4)
    try:
        decoded = base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise QualificationError(f"{label} fingerprint encoding is invalid") from exc
    if len(decoded) != 32:
        raise QualificationError(f"{label} must decode to 32 bytes")
    return text


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificationError(f"cannot read valid {label} JSON {path}: {exc}") from exc
    return as_object(value, label)


def resolve_input(
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
        raise QualificationError(f"{label} is not a regular file")
    size = resolved.stat().st_size
    if size < 1 or size > maximum_bytes:
        raise QualificationError(f"{label} has an unsafe size")
    return resolved, relative.as_posix()


def bind_file(project_root: Path, path: Path, label: str) -> tuple[dict[str, Any], Path]:
    resolved, relative = resolve_input(project_root, path, label)
    return {
        "path": relative,
        "sha256": sha256_file(resolved),
        "size_bytes": resolved.stat().st_size,
    }, resolved


def verify_binding(project_root: Path, value: Any, label: str) -> Path:
    binding = as_object(value, label)
    exact_keys(binding, {"path", "sha256", "size_bytes"}, label)
    relative = Path(as_text(binding["path"], f"{label}.path"))
    if relative.is_absolute() or ".." in relative.parts:
        raise QualificationError(f"{label}.path must be project-relative")
    path, canonical = resolve_input(project_root, relative, f"{label}.path")
    if canonical != relative.as_posix():
        raise QualificationError(f"{label}.path is not canonical")
    expected = as_text(binding["sha256"], f"{label}.sha256", maximum=64)
    if not HASH_RE.fullmatch(expected) or sha256_file(path) != expected:
        raise QualificationError(f"{label} file hash no longer matches")
    if path.stat().st_size != as_int(binding["size_bytes"], f"{label}.size_bytes", minimum=1):
        raise QualificationError(f"{label} file size no longer matches")
    return path


def safe_relative(value: Any, label: str) -> str:
    text = as_text(value, label)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise QualificationError(f"{label} must be project-relative")
    return path.as_posix()


def exclusive_project_output(project_root: Path, path: Path, label: str) -> Path:
    root = project_root.resolve()
    candidate = path if path.is_absolute() else root / path
    if candidate.exists() or candidate.is_symlink():
        raise QualificationError(f"{label} already exists")
    if not candidate.parent.is_dir():
        raise QualificationError(f"{label} parent directory does not exist")
    current = candidate.parent
    while True:
        if current.is_symlink():
            raise QualificationError(f"{label} path contains a symlink")
        if current.resolve() == root:
            break
        if current.parent == current:
            raise QualificationError(f"{label} must be inside project root")
        current = current.parent
    try:
        candidate.resolve().relative_to(root)
    except ValueError as exc:
        raise QualificationError(f"{label} must be inside project root") from exc
    return candidate.resolve()


def write_exclusive(path: Path, payload: bytes, label: str) -> None:
    if path.exists() or path.is_symlink():
        raise QualificationError(f"{label} already exists")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
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
    write_exclusive(
        path,
        (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        label,
    )


def delivered_vpeak(output_vpeak: float, attenuation_db: float, uncertainty_db: float) -> float:
    worst_attenuation = max(0.0, attenuation_db - uncertainty_db)
    return output_vpeak * (10.0 ** (-worst_attenuation / 20.0))
