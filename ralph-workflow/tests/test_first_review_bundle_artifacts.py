"""The standalone first-review bundle must ship canonical Markdown artifacts."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec

import_module("ralph.mcp.artifacts.markdown.specs")

_ARTIFACTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "first-review-bundle"
    / ".agent"
    / "artifacts"
)
_EXPECTED_ARTIFACT_TYPES = frozenset(
    {"plan", "development_result", "issues", "fix_result"}
)


def test_first_review_bundle_contains_only_expected_markdown_artifacts() -> None:
    artifact_paths = sorted(path for path in _ARTIFACTS_DIR.iterdir() if path.is_file())

    assert {path.suffix for path in artifact_paths} == {".md"}
    assert {path.stem for path in artifact_paths} == _EXPECTED_ARTIFACT_TYPES


def test_first_review_bundle_artifacts_validate_with_registered_specs() -> None:
    for artifact_type in sorted(_EXPECTED_ARTIFACT_TYPES):
        artifact_path = _ARTIFACTS_DIR / f"{artifact_type}.md"
        content = artifact_path.read_text(encoding="utf-8")

        _, diagnostics = parse_and_validate(content, get_spec(artifact_type))
        errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]

        assert errors == [], (
            f"{artifact_path} must validate cleanly; got: "
            + "; ".join(
                f"line {diagnostic.line} [{diagnostic.rule_id}] {diagnostic.message}"
                for diagnostic in errors
            )
        )
