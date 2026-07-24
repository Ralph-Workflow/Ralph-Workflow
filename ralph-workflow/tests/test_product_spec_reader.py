"""Tests for read_product_spec_artifact."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.product_spec import read_product_spec_artifact

if TYPE_CHECKING:
    from pathlib import Path


def _product_spec_markdown(title: str = "Markdown artifacts") -> str:
    return f"""---
type: product_spec
---

## Title

- [T-1] {title}

## Scope

- [SC-1] Make markdown the persisted source of truth.

## Goals

- [G-1] Remove downstream JSON parsing.

## Users

- [U-1] Ralph operators.

## Success Criteria

- [CR-1] Canonical markdown loads successfully.
"""


class TestReadProductSpecArtifact:
    """Tests for read_product_spec_artifact."""

    def test_read_returns_none_when_no_artifact(self, tmp_path: Path) -> None:
        """Reader returns None when no artifact file exists."""
        result = read_product_spec_artifact(tmp_path)
        assert result is None

    def test_read_returns_validated_markdown_content(self, tmp_path: Path) -> None:
        """Reader parses the canonical Markdown product specification."""
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "product_spec.md").write_text(
            """\
---
type: product_spec
---

## Title
- [TITLE-1] Markdown artifacts

## Scope
- [SCOPE-1] Remove the retired JSON protocol.

## Goals
- [GOAL-1] Keep Markdown as the source of truth.

## Users
- [USER-1] Ralph operators

## Success Criteria
- [AC-1] Product specifications load from Markdown.
""",
            encoding="utf-8",
        )

        result = read_product_spec_artifact(tmp_path)

        assert result is not None
        assert result["title"] == "Markdown artifacts"
        assert result["scope"] == "Remove the retired JSON protocol."
        assert result["goals"] == ["Keep Markdown as the source of truth."]
        assert result["success_criteria"] == ["Product specifications load from Markdown."]

    def test_read_loads_canonical_markdown_artifact(self, tmp_path: Path) -> None:
        """Reader validates and projects the canonical markdown document."""
        artifacts = tmp_path / ".agent" / "artifacts"
        artifacts.mkdir(parents=True)
        (artifacts / "product_spec.md").write_text(
            _product_spec_markdown(),
            encoding="utf-8",
        )

        result = read_product_spec_artifact(tmp_path)

        assert result is not None
        assert result["title"] == "Markdown artifacts"
        assert result["goals"] == ["Remove downstream JSON parsing."]
        assert result["success_criteria"] == ["Canonical markdown loads successfully."]

    def test_read_prefers_canonical_markdown_over_legacy_json(self, tmp_path: Path) -> None:
        """A stray JSON file cannot override the submitted markdown."""
        artifacts = tmp_path / ".agent" / "artifacts"
        artifacts.mkdir(parents=True)
        (artifacts / "product_spec.md").write_text(
            _product_spec_markdown("Canonical"),
            encoding="utf-8",
        )
        (artifacts / "product_spec.json").write_text(
            "this must never be parsed",
            encoding="utf-8",
        )

        result = read_product_spec_artifact(tmp_path)

        assert result is not None
        assert result["title"] == "Canonical"

    def test_read_ignores_json_only_state(
        self,
        tmp_path: Path,
    ) -> None:
        """A stray JSON file is never parsed or projected."""
        artifacts = tmp_path / ".agent" / "artifacts"
        artifacts.mkdir(parents=True)
        (artifacts / "product_spec.json").write_text(
            '{"type":"product_spec","content":{"title":"must not load"}}',
            encoding="utf-8",
        )

        assert read_product_spec_artifact(tmp_path) is None
