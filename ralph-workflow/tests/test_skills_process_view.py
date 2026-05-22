"""Tests for ralph.skills._process_view."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.skills._process_view import SkillsProcessView, has_machine_global_skills

if TYPE_CHECKING:
    import pytest


def test_has_machine_global_skills_false_when_directory_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert has_machine_global_skills() is False


def test_has_machine_global_skills_true_when_skill_file_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    skills_dir = tmp_path / ".claude" / "plugins" / "ralph-workflow-skills" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "using-superpowers.md").write_text("# using-superpowers\n", encoding="utf-8")
    assert has_machine_global_skills() is True


def test_skills_process_view_materializes_and_cleans_up_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RALPH_SKILLS_PROCESS_DIR", raising=False)
    with SkillsProcessView() as target_dir:
        assert target_dir.exists()
        assert len(list(target_dir.glob("*.md"))) == 17
        assert os.environ["RALPH_SKILLS_PROCESS_DIR"] == str(target_dir)
    assert "RALPH_SKILLS_PROCESS_DIR" not in os.environ
    assert not target_dir.exists()


def test_skills_process_view_target_dir_is_not_deleted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("RALPH_SKILLS_PROCESS_DIR", raising=False)
    with SkillsProcessView(target_dir=tmp_path) as target_dir:
        assert target_dir == tmp_path
        assert len(list(target_dir.glob("*.md"))) == 17
    assert tmp_path.exists()
    assert len(list(tmp_path.glob("*.md"))) == 17
    assert "RALPH_SKILLS_PROCESS_DIR" not in os.environ
