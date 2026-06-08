"""Tests for ralph.skills._installer check_skills_update_available."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.skills._agent_paths import AgentSkillRoot
from ralph.skills._installer import check_skills_update_available, install_baseline_skills

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_check_skills_update_available_returns_true_when_dir_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "ralph.skills._installer._installed_skills_dir",
        lambda: tmp_path / "missing-skills",
    )
    assert check_skills_update_available() is True


def test_check_skills_update_available_returns_false_when_contents_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installed_dir = tmp_path / "skills"
    install_baseline_skills(target_dir=installed_dir)
    monkeypatch.setattr(
        "ralph.skills._installer._installed_skills_dir",
        lambda: installed_dir,
    )
    # Restrict the check to the canonical Claude root so this test stays focused
    # on the "contents match -> no update" contract. The new "iterate every
    # registered root" behavior is covered separately in
    # tests/test_skills_installer_sibling_symlinks.py.
    fake_canonical = AgentSkillRoot(
        agent="claude",
        path_segments=(str(installed_dir),),
        source_url="",
        is_canonical=True,
    )

    monkeypatch.setattr(
        "ralph.skills._installer.agent_skill_roots",
        lambda: (fake_canonical,),
    )
    assert check_skills_update_available() is False


def test_check_skills_update_available_returns_true_when_content_differs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installed_dir = tmp_path / "skills"
    install_baseline_skills(target_dir=installed_dir)
    (installed_dir / "using-superpowers" / "SKILL.md").write_text("changed", encoding="utf-8")
    monkeypatch.setattr(
        "ralph.skills._installer._installed_skills_dir",
        lambda: installed_dir,
    )
    assert check_skills_update_available() is True


def test_check_skills_update_available_returns_true_when_metadata_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installed_dir = tmp_path / "skills"
    install_baseline_skills(target_dir=installed_dir)
    metadata_path = installed_dir / "metadata.json"
    if metadata_path.exists():
        metadata_path.unlink()
    monkeypatch.setattr(
        "ralph.skills._installer._installed_skills_dir",
        lambda: installed_dir,
    )
    assert check_skills_update_available() is True


def test_check_skills_update_available_returns_true_when_user_conflict_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installed_dir = tmp_path / "skills"
    install_baseline_skills(target_dir=installed_dir)
    (installed_dir / "using-superpowers" / "SKILL.md").write_text(
        "# user override\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "ralph.skills._installer._installed_skills_dir",
        lambda: installed_dir,
    )
    assert check_skills_update_available() is True


def test_check_skills_update_available_returns_true_when_managed_marker_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installed_dir = tmp_path / "skills"
    install_baseline_skills(target_dir=installed_dir)
    (installed_dir / "using-superpowers" / ".ralph-managed.json").unlink()
    monkeypatch.setattr(
        "ralph.skills._installer._installed_skills_dir",
        lambda: installed_dir,
    )
    assert check_skills_update_available() is True
