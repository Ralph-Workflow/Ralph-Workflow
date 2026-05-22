"""Tests for ralph.skills.manager.SkillManager."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from ralph.skills._capability_entry import CapabilityEntry
from ralph.skills._capability_status import CapabilityStatus
from ralph.skills.manager import SkillManager

if TYPE_CHECKING:
    from pathlib import Path


def test_ensure_baseline_capabilities_marks_skills_needs_repair_on_failure(
    tmp_path: Path,
) -> None:
    manager = SkillManager(state_path=tmp_path / "state.json")
    with (
        patch("ralph.skills.manager.install_baseline_skills") as mock_install,
        patch("ralph.skills.manager._find_configured_docs_mcp_url", return_value=None),
        patch("ralph.skills.manager._get_ralph_version", return_value="1.0.0"),
    ):
        mock_install.return_value = (
            CapabilityEntry(status=CapabilityStatus.NEEDS_REPAIR),
            ["skills-materialize-failed"],
        )
        result = manager.ensure_baseline_capabilities(workspace_root=tmp_path)

    assert result.skills.status == CapabilityStatus.NEEDS_REPAIR


def test_check_baseline_health_returns_keyed_status_map(tmp_path: Path) -> None:
    manager = SkillManager(state_path=tmp_path / "state.json")
    with (
        patch("ralph.skills.manager.install_baseline_skills") as mock_install,
        patch("ralph.skills.manager._find_configured_docs_mcp_url", return_value=None),
        patch("ralph.skills.manager._get_ralph_version", return_value="1.0.0"),
    ):
        mock_install.return_value = (
            CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            [],
        )
        manager.ensure_baseline_capabilities(workspace_root=tmp_path)

    health = manager.check_baseline_health()
    assert set(health) == {"web_search", "visit_url", "docs_mcp", "skills"}
    assert all(isinstance(value, bool) for value in health.values())


def test_check_skills_for_updates_returns_true_when_update_found(tmp_path: Path) -> None:
    manager = SkillManager(state_path=tmp_path / "state.json")
    with (
        patch("ralph.skills.manager.install_baseline_skills") as mock_install,
        patch("ralph.skills.manager._find_configured_docs_mcp_url", return_value=None),
        patch("ralph.skills.manager._get_ralph_version", return_value="1.0.0"),
    ):
        mock_install.return_value = (
            CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            [],
        )
        manager.ensure_baseline_capabilities(workspace_root=tmp_path)

    with patch(
        "ralph.skills.manager.check_skills_update_available",
        return_value=True,
    ) as mock_check:
        assert manager.check_skills_for_updates() is True
        mock_check.assert_called_once()
