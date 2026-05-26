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
        "ralph.skills._installer.materialize_skills_to_dir",
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
    monkeypatch.setattr("ralph.skills._installer.materialize_skills_to_dir", _raise)

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
