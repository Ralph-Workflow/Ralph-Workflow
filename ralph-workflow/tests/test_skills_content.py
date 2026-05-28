"""Tests for ralph.skills._content."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.skills._content import (
    BASELINE_SKILL_NAMES,
    get_skill_content,
    get_skill_metadata,
    list_skill_names,
    materialize_skills_to_dir,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_baseline_skill_names_are_canonical_and_complete() -> None:
    assert list_skill_names() == BASELINE_SKILL_NAMES
    assert len(BASELINE_SKILL_NAMES) >= 17
    assert "open-design--frontend-slides" in BASELINE_SKILL_NAMES


def test_each_skill_has_substantial_content() -> None:
    metadata = get_skill_metadata()
    for name in BASELINE_SKILL_NAMES:
        content = get_skill_content(name)
        expected_internal_name = metadata["skill_sources"].get(name, {}).get("upstream_name", name)
        assert (
            content.startswith(f"# {name}")
            or content.startswith(f"---\nname: {name}\n")
            or content.startswith(f"---\nname: {expected_internal_name}\n")
        )
        assert len(content.split()) >= 150


def test_get_skill_content_raises_for_unknown_name() -> None:
    with pytest.raises(ValueError):
        get_skill_content("definitely-not-a-real-skill")


def test_skill_metadata_exposes_upstream_provenance() -> None:
    metadata = get_skill_metadata()
    assert metadata["source_repo"] == "multiple"
    assert "https://github.com/obra/superpowers" in metadata["source_repos"]
    assert "https://github.com/affaan-m/ECC" in metadata["source_repos"]
    assert metadata["source_commit"]
    assert metadata["mirrored_at"]
    skills = metadata["skills"]
    assert isinstance(skills, list)
    assert tuple(skills) == BASELINE_SKILL_NAMES
    assert metadata["skill_sources"]["security-review"]["repo"] == "https://github.com/affaan-m/ECC"
    assert metadata["bundles"]["open-design--frontend-slides"] == "open-design"
    assert (
        metadata["skill_sources"]["open-design--frontend-slides"]["catalog_repo"]
        == "https://github.com/nexu-io/open-design"
    )
    assert (
        metadata["skill_sources"]["open-design--frontend-slides"]["repo"]
        == "https://github.com/zarazhangrui/frontend-slides"
    )


def test_materialize_skills_to_dir_writes_all_skills(tmp_path: Path) -> None:
    metadata = get_skill_metadata()
    written = materialize_skills_to_dir(tmp_path)
    assert written == list(BASELINE_SKILL_NAMES)
    written_files = sorted(p.name for p in tmp_path.glob("*.md"))
    assert written_files == sorted(f"{name}.md" for name in BASELINE_SKILL_NAMES)
    for name in BASELINE_SKILL_NAMES:
        path = tmp_path / f"{name}.md"
        content = path.read_text(encoding="utf-8")
        expected_internal_name = metadata["skill_sources"].get(name, {}).get("upstream_name", name)
        assert (
            content.startswith(f"# {name}")
            or content.startswith(f"---\nname: {name}\n")
            or content.startswith(f"---\nname: {expected_internal_name}\n")
        )
