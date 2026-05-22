"""Tests for ralph.skills._content."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.skills._content import (
    BASELINE_SKILL_NAMES,
    get_skill_content,
    list_skill_names,
    materialize_skills_to_dir,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_baseline_skill_names_are_canonical_and_complete() -> None:
    assert list_skill_names() == BASELINE_SKILL_NAMES
    assert len(BASELINE_SKILL_NAMES) == 17


def test_each_skill_has_substantial_content() -> None:
    for name in BASELINE_SKILL_NAMES:
        content = get_skill_content(name)
        assert content.startswith(f"# {name}")
        assert len(content.split()) >= 150


def test_materialize_skills_to_dir_writes_all_skills(tmp_path: Path) -> None:
    written = materialize_skills_to_dir(tmp_path)
    assert written == list(BASELINE_SKILL_NAMES)
    written_files = sorted(p.name for p in tmp_path.glob("*.md"))
    assert written_files == sorted(f"{name}.md" for name in BASELINE_SKILL_NAMES)
    for name in BASELINE_SKILL_NAMES:
        path = tmp_path / f"{name}.md"
        assert path.read_text(encoding="utf-8").startswith(f"# {name}")
