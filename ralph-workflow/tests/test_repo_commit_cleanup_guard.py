from __future__ import annotations

import importlib.resources
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline.state import PipelineState

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable

RETIRED_IDENT: str = "commit_" + "cleanup"
RETIRED_HYPHEN_IDENT: str = "commit-" + "cleanup"
RETIRED_BYTES: bytes = RETIRED_IDENT.encode("utf-8")
RETIRED_HYPHEN_BYTES: bytes = RETIRED_HYPHEN_IDENT.encode("utf-8")

FORBIDDEN_BYTE_STRINGS: tuple[bytes, ...] = (RETIRED_BYTES, RETIRED_HYPHEN_BYTES)
FORBIDDEN_TEXT_STRINGS: tuple[str, ...] = (RETIRED_IDENT, RETIRED_HYPHEN_IDENT)

EXEMPT_PREFIXES: tuple[str, ...] = (
    ".pi-subagents/",
    ".agent/",
    "docs/sphinx/_build/",
    "ralph-workflow/docs/sphinx/_build/",
    "PROMPT.md",
    "ralph-workflow/PROMPT.md",
)


def _is_exempt_path(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/")
    if any(normalized.startswith(prefix) for prefix in EXEMPT_PREFIXES):
        return True
    return normalized.endswith(".jsonl") and ("tests/agents/invoke/fixtures/" in normalized)


def _find_repo_root() -> Path:
    test_path = Path(__file__).resolve()
    for parent in test_path.parents:
        if (parent / ".git").exists():
            return parent
    return test_path.parents[2]


def _get_tracked_files(repo_root: Path) -> list[str]:
    files: list[str] = []
    skip_dirs = {
        ".git",
        ".agent",
        ".pi-subagents",
        ".venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "build",
        "dist",
        "_build",
        "node_modules",
    }
    for root, dirs, filenames in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        rel_root = Path(root).relative_to(repo_root)
        for fname in filenames:
            if fname.startswith("."):
                continue
            files.append((rel_root / fname).as_posix())
    return files


def _scan_file_bytes(file_path: Path, display_name: str) -> list[str]:
    violations: list[str] = []
    try:
        content = file_path.read_bytes()
    except OSError:
        return violations
    for forbidden in FORBIDDEN_BYTE_STRINGS:
        if forbidden in content:
            violations.append(f"{display_name}: contains forbidden identifier")
    return violations


def _scan_traversable(traversable: Traversable) -> list[str]:
    violations: list[str] = []
    if traversable.is_file():
        content = traversable.read_bytes()
        for forbidden in FORBIDDEN_BYTE_STRINGS:
            if forbidden in content:
                violations.append(f"{traversable.name}: contains forbidden identifier")
    elif traversable.is_dir():
        for child in traversable.iterdir():
            violations.extend(_scan_traversable(child))
    return violations


def _scan_schema_recursively(data: object) -> list[str]:
    violations: list[str] = []
    if isinstance(data, str):
        for forbidden in FORBIDDEN_TEXT_STRINGS:
            if forbidden in data:
                violations.append(f"string match: {data}")
    elif isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str):
                for forbidden in FORBIDDEN_TEXT_STRINGS:
                    if forbidden in key:
                        violations.append(f"key match: {key}")
            violations.extend(_scan_schema_recursively(value))
    elif isinstance(data, list):
        for item in data:
            violations.extend(_scan_schema_recursively(item))
    return violations


def test_no_retired_identifier_in_tracked_files() -> None:
    repo_root = _find_repo_root()
    tracked_rel_paths = _get_tracked_files(repo_root)
    violations: list[str] = []
    for rel in tracked_rel_paths:
        if _is_exempt_path(rel):
            continue
        file_path = repo_root / rel
        if not file_path.is_file():
            continue
        violations.extend(_scan_file_bytes(file_path, rel))
    assert not violations, f"Forbidden identifier found in tracked files: {violations}"


@pytest.mark.parametrize(
    "pkg_name",
    [
        "ralph.policy.defaults",
        "ralph.prompts.templates",
    ],
)
def test_no_retired_identifier_in_packaged_resources(pkg_name: str) -> None:
    root = importlib.resources.files(pkg_name)
    violations = _scan_traversable(root)
    assert not violations, f"Forbidden identifier found in package {pkg_name}: {violations}"


def test_no_retired_identifier_in_checkpoint_schema_surface() -> None:
    schema = PipelineState.model_json_schema()
    violations = _scan_schema_recursively(schema)
    assert not violations, f"Forbidden identifier found in PipelineState schema: {violations}"

    raw_schema_text = json.dumps(schema)
    for forbidden in FORBIDDEN_TEXT_STRINGS:
        assert forbidden not in raw_schema_text, (
            f"Forbidden identifier found in PipelineState schema JSON: {forbidden}"
        )

    repo_root = _find_repo_root()
    checkpoint_sample = repo_root / "ralph-workflow" / "checkpoint.json"
    if checkpoint_sample.is_file():
        sample_data: object = json.loads(checkpoint_sample.read_text(encoding="utf-8"))
        sample_violations = _scan_schema_recursively(sample_data)
        assert not sample_violations, (
            f"Forbidden identifier found in sample checkpoint: {sample_violations}"
        )


def test_guard_detects_violation_in_content() -> None:
    fake_content = b"some prefix " + RETIRED_BYTES + b" some suffix"
    detected = any(forbidden in fake_content for forbidden in FORBIDDEN_BYTE_STRINGS)
    assert detected is True


def test_guard_exempts_historical_fixtures() -> None:
    assert _is_exempt_path("ralph-workflow/tests/agents/invoke/fixtures/capture.jsonl") is True
    assert _is_exempt_path("tests/agents/invoke/fixtures/capture.jsonl") is True
    assert _is_exempt_path("ralph-workflow/ralph/pipeline/state.py") is False


def test_schema_scanner_detects_forbidden_vocabulary() -> None:
    fake_schema = {"properties": {"phase": {"enum": ["planning", RETIRED_IDENT]}}}
    violations = _scan_schema_recursively(fake_schema)
    assert len(violations) > 0


def test_file_scanner_flags_violation_when_present(tmp_path: Path) -> None:
    target = tmp_path / "bad.txt"
    target.write_bytes(b"prefix " + RETIRED_BYTES + b" suffix")
    violations = _scan_file_bytes(target, "bad.txt")
    assert len(violations) == 1
    assert "bad.txt" in violations[0]


def test_traversable_scanner_flags_violation_when_present(tmp_path: Path) -> None:
    bad_resource = tmp_path / "bad.jinja"
    bad_resource.write_bytes(b"content " + RETIRED_BYTES)
    violations = _scan_traversable(bad_resource)
    assert len(violations) == 1
    assert "bad.jinja" in violations[0]
