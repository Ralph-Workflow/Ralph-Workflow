"""Tests for ralph.skills._installer check_skills_update_available."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.skills._content import materialize_skills_to_dir
from ralph.skills._installer import check_skills_update_available

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
    materialize_skills_to_dir(installed_dir)
    monkeypatch.setattr(
        "ralph.skills._installer._installed_skills_dir",
        lambda: installed_dir,
    )
    assert check_skills_update_available() is False


def test_check_skills_update_available_returns_true_when_content_differs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installed_dir = tmp_path / "skills"
    materialize_skills_to_dir(installed_dir)
    (installed_dir / "using-superpowers.md").write_text("changed", encoding="utf-8")
    monkeypatch.setattr(
        "ralph.skills._installer._installed_skills_dir",
        lambda: installed_dir,
    )
    assert check_skills_update_available() is True
