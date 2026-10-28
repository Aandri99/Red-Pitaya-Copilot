#!/usr/bin/env python3
"""Run every offline validation supplied by Red Pitaya Copilot."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


class CheckFailure(RuntimeError):
    """Raised when a suite check cannot continue."""


def run(label: str, command: list[str], cwd: Path) -> None:
    print(f"\n=== {label} ===", flush=True)
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print(
            completed.stderr,
            end="" if completed.stderr.endswith("\n") else "\n",
            file=sys.stderr,
        )
    if completed.returncode != 0:
        raise CheckFailure(
            f"{label} failed with exit code {completed.returncode}: "
            + " ".join(command)
        )


def check_json() -> None:
    paths = sorted(ROOT.rglob("*.json"))
    for path in paths:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CheckFailure(f"invalid JSON {path.relative_to(ROOT)}: {exc}") from exc
    print(f"JSON syntax: {len(paths)} files passed")


def check_python_syntax() -> None:
    paths = sorted(ROOT.rglob("*.py"))
    for path in paths:
        try:
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec")
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            raise CheckFailure(
                f"invalid Python {path.relative_to(ROOT)}: {exc}"
            ) from exc
    print(f"Python syntax: {len(paths)} files passed")


def check_javascript_syntax() -> None:
    node = shutil.which("node")
    if not node:
        raise CheckFailure(
            "Node.js is required for JavaScript syntax validation but was not found"
        )
    paths = sorted(ROOT.rglob("*.js"))
    for path in paths:
        completed = subprocess.run(
            [node, "--check", str(path)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise CheckFailure(
                f"invalid JavaScript {path.relative_to(ROOT)}: {detail}"
            )
    print(f"JavaScript syntax: {len(paths)} files passed")


def check_markdown_links() -> None:
    checked = 0
    for markdown in sorted(ROOT.rglob("*.md")):
        text = markdown.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK_RE.findall(text):
            target = raw_target.strip()
            if (
                not target
                or target.startswith(("#", "http://", "https://", "mailto:"))
                or target.startswith("<http")
            ):
                continue
            target = target.split("#", 1)[0]
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            candidate = (markdown.parent / target).resolve()
            try:
                candidate.relative_to(ROOT.resolve())
            except ValueError as exc:
                raise CheckFailure(
                    f"Markdown link escapes plugin root in "
                    f"{markdown.relative_to(ROOT)}: {raw_target}"
                ) from exc
            if not candidate.exists():
                raise CheckFailure(
                    f"broken Markdown link in {markdown.relative_to(ROOT)}: "
                    f"{raw_target}"
                )
            checked += 1
    print(f"Relative Markdown links: {checked} links passed")


def main() -> int:
    try:
        print(f"Plugin root: {ROOT}")
        check_json()
        check_python_syntax()
        check_javascript_syntax()
        check_markdown_links()
        run(
            "Shared manifest/profile tests",
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_*.py",
                "-v",
            ],
            ROOT,
        )
        run(
            "Application matrix",
            [sys.executable, "self_test.py"],
            ROOT / "skills" / "build-red-pitaya-app" / "scripts",
        )
        run(
            "FPGA evidence/package tests",
            [sys.executable, "self_test.py"],
            ROOT / "skills" / "build-red-pitaya-fpga" / "scripts",
        )
        run(
            "Deployment plan/gate tests",
            [sys.executable, "test_deployment_tools.py"],
            ROOT / "skills" / "deploy-red-pitaya-project" / "scripts",
        )
        run(
            "Two-board qualification tests",
            [sys.executable, "test_qualification_tools.py"],
            ROOT
            / "skills"
            / "qualify-red-pitaya-loopback-system"
            / "scripts",
        )
        run(
            "Single-board qualification tests",
            [sys.executable, "test_single_board_tools.py"],
            ROOT
            / "skills"
            / "qualify-red-pitaya-single-board-loopback"
            / "scripts",
        )
        print("\nALL OFFLINE SUITE CHECKS PASSED")
        return 0
    except CheckFailure as exc:
        print(f"\nSUITE FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
