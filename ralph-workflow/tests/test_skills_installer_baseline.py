"""Tests for ralph.skills._installer install_baseline_skills."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.skills._capability_status import CapabilityStatus
from ralph.skills._installer import install_baseline_skills

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_install_baseline_skills_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "skills"
    mock_materialize = MagicMock(return_value=["using-superpowers"])
    monkeypatch.setattr("ralph.skills._installer._installed_skills_dir", lambda: target_dir)
    monkeypatch.setattr(
        "ralph.skills._installer.materialize_skills_to_claude_dir",
        mock_materialize,
    )

    entry, failures = install_baseline_skills()

    assert entry.status == CapabilityStatus.INSTALLED_HEALTHY
    assert failures == []
    mock_materialize.assert_called_once_with(target_dir)


def test_install_baseline_skills_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "skills"

    def _raise(_: Path) -> list[str]:
        raise OSError("boom")

    monkeypatch.setattr("ralph.skills._installer._installed_skills_dir", lambda: target_dir)
    monkeypatch.setattr("ralph.skills._installer.materialize_skills_to_claude_dir", _raise)

    entry, failures = install_baseline_skills()

    assert entry.status == CapabilityStatus.NEEDS_REPAIR
    assert failures == ["skills-materialize-failed"]


def test_install_baseline_skills_writes_metadata_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entry, failures = install_baseline_skills(target_dir=tmp_path)

    assert entry.status == CapabilityStatus.INSTALLED_HEALTHY
    assert failures == []
    assert (tmp_path / "metadata.json").exists()


def test_install_baseline_skills_writes_claude_discoverable_skill_directories(
    tmp_path: Path,
) -> None:
    entry, failures = install_baseline_skills(target_dir=tmp_path)

    assert entry.status == CapabilityStatus.INSTALLED_HEALTHY
    assert failures == []
    skill_file = tmp_path / "using-superpowers" / "SKILL.md"
    assert skill_file.exists()
    assert skill_file.read_text(encoding="utf-8").startswith("---\nname: using-superpowers")


def test_install_baseline_skills_preserves_conflicting_user_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "skills"
    user_skill_dir = target_dir / "using-superpowers"
    user_skill_dir.mkdir(parents=True)
    skill_file = user_skill_dir / "SKILL.md"
    skill_file.write_text("# user version\n", encoding="utf-8")

    monkeypatch.setattr("ralph.skills._installer._installed_skills_dir", lambda: target_dir)

    entry, failures = install_baseline_skills()

    assert entry.status == CapabilityStatus.NEEDS_REPAIR
    assert failures == ["skills-conflict-using-superpowers"]
    assert skill_file.read_text(encoding="utf-8") == "# user version\n"


def test_install_baseline_skills_updates_ralph_managed_skill_in_place(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "skills"
    managed_skill_dir = target_dir / "using-superpowers"
    managed_skill_dir.mkdir(parents=True)
    skill_file = managed_skill_dir / "SKILL.md"
    skill_file.write_text("# old ralph version\n", encoding="utf-8")
    (managed_skill_dir / ".ralph-managed.json").write_text(
        '{"managed_by": "ralph-workflow", "skill": "using-superpowers"}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr("ralph.skills._installer._installed_skills_dir", lambda: target_dir)

    entry, failures = install_baseline_skills()

    assert entry.status == CapabilityStatus.INSTALLED_HEALTHY
    assert failures == []
    assert skill_file.read_text(encoding="utf-8").startswith("---\nname: using-superpowers")


def test_install_baseline_skills_adopts_unmarked_identical_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from ralph.skills._content import get_skill_content

    target_dir = tmp_path / "skills"
    skill_dir = target_dir / "using-superpowers"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(get_skill_content("using-superpowers"), encoding="utf-8")

    monkeypatch.setattr("ralph.skills._installer._installed_skills_dir", lambda: target_dir)

    entry, failures = install_baseline_skills()

    assert entry.status == CapabilityStatus.INSTALLED_HEALTHY
    assert failures == []
    assert (skill_dir / ".ralph-managed.json").exists()
