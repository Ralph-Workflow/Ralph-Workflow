"""Tests for ralph.skills._process_view."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.skills._content import (
    BASELINE_SKILL_NAMES,
    get_skill_content,
    get_skill_metadata,
)
from ralph.skills._process_view import SkillsProcessView, has_machine_global_skills

if TYPE_CHECKING:
    import pytest


def test_has_machine_global_skills_false_when_directory_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert has_machine_global_skills() is False


def test_has_machine_global_skills_false_when_partial_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    skills_dir = tmp_path / ".claude" / "skills" / "using-superpowers"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("# using-superpowers\n", encoding="utf-8")
    assert has_machine_global_skills() is False


def test_has_machine_global_skills_true_when_all_skills_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    skills_dir = tmp_path / ".claude" / "skills"
    for name in BASELINE_SKILL_NAMES:
        skill_dir = skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(get_skill_content(name), encoding="utf-8")
    (skills_dir / "metadata.json").write_text(
        json.dumps(get_skill_metadata(), indent=2) + "\n",
        encoding="utf-8",
    )
    for name in BASELINE_SKILL_NAMES:
        ((skills_dir / name) / ".ralph-managed.json").write_text(
            json.dumps({"managed_by": "ralph-workflow", "skill": name}) + "\n",
            encoding="utf-8",
        )
    assert has_machine_global_skills() is True


def test_has_machine_global_skills_false_when_conflicting_user_skill_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    skills_dir = tmp_path / ".claude" / "skills"
    for name in BASELINE_SKILL_NAMES:
        skill_dir = skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(get_skill_content(name), encoding="utf-8")
    (skills_dir / "using-superpowers" / "SKILL.md").write_text(
        "# user override\n",
        encoding="utf-8",
    )
    (skills_dir / "metadata.json").write_text(
        json.dumps(get_skill_metadata(), indent=2) + "\n",
        encoding="utf-8",
    )
    assert has_machine_global_skills() is False


def test_skills_process_view_materializes_and_cleans_up_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RALPH_SKILLS_PROCESS_DIR", raising=False)
    with SkillsProcessView() as target_dir:
        assert target_dir.exists()
        assert len(list(target_dir.glob("*.md"))) == len(BASELINE_SKILL_NAMES)
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
        assert len(list(target_dir.glob("*.md"))) == len(BASELINE_SKILL_NAMES)
    assert tmp_path.exists()
    assert len(list(tmp_path.glob("*.md"))) == len(BASELINE_SKILL_NAMES)
    assert "RALPH_SKILLS_PROCESS_DIR" not in os.environ


def test_skills_process_view_merges_personal_and_project_skills(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("RALPH_SKILLS_PROCESS_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    project_root = tmp_path / "workspace"
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    personal_skill = tmp_path / ".claude" / "skills" / "personal-helper"
    personal_skill.mkdir(parents=True)
    (personal_skill / "SKILL.md").write_text("# personal-helper\n", encoding="utf-8")

    project_skill = project_root / ".claude" / "skills" / "project-helper"
    project_skill.mkdir(parents=True)
    (project_skill / "SKILL.md").write_text("# project-helper\n", encoding="utf-8")

    with SkillsProcessView() as target_dir:
        personal_content = (target_dir / "personal-helper.md").read_text(encoding="utf-8")
        project_content = (target_dir / "project-helper.md").read_text(encoding="utf-8")
        assert personal_content == "# personal-helper\n"
        assert project_content == "# project-helper\n"
