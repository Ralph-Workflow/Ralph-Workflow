"""Tests for ralph.skills.manager.SkillManager."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from ralph.skills._capability_entry import CapabilityEntry
from ralph.skills._capability_status import CapabilityStatus
from ralph.skills._recheck_policy import RecheckPolicy
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


def test_ensure_baseline_stamps_web_search_degraded_when_ddgs_unavailable(
    tmp_path: Path,
) -> None:
    manager = SkillManager(state_path=tmp_path / "state.json")
    with (
        patch("ralph.skills.manager.install_baseline_skills") as mock_install,
        patch("ralph.skills.manager._find_configured_docs_mcp_url", return_value=None),
        patch("ralph.skills.manager._get_ralph_version", return_value="1.0.0"),
        patch("ralph.skills.manager._web_search_is_available", return_value=False),
        patch("ralph.skills.manager._visit_url_is_available", return_value=True),
    ):
        mock_install.return_value = (
            CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            [],
        )
        result = manager.ensure_baseline_capabilities(workspace_root=tmp_path)

    assert result.web_search.status == CapabilityStatus.INSTALLED_DEGRADED
    assert result.web_search.last_check_fail_iso != ""


def test_ensure_baseline_stamps_visit_url_needs_repair_when_extraction_unavailable(
    tmp_path: Path,
) -> None:
    manager = SkillManager(state_path=tmp_path / "state.json")
    with (
        patch("ralph.skills.manager.install_baseline_skills") as mock_install,
        patch("ralph.skills.manager._find_configured_docs_mcp_url", return_value=None),
        patch("ralph.skills.manager._get_ralph_version", return_value="1.0.0"),
        patch("ralph.skills.manager._web_search_is_available", return_value=True),
        patch("ralph.skills.manager._visit_url_is_available", return_value=False),
    ):
        mock_install.return_value = (
            CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            [],
        )
        result = manager.ensure_baseline_capabilities(workspace_root=tmp_path)

    assert result.visit_url.status == CapabilityStatus.NEEDS_REPAIR
    assert result.visit_url.last_check_fail_iso != ""


def test_get_docs_mcp_available_cache_hit_returns_healthy(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    manager = SkillManager(state_path=state_path)
    with (
        patch("ralph.skills.manager.install_baseline_skills") as mock_install,
        patch("ralph.skills.manager._find_configured_docs_mcp_url", return_value="http://localhost:6280/mcp"),
        patch("ralph.skills.manager._get_ralph_version", return_value="1.0.0"),
        patch("ralph.skills.manager._web_search_is_available", return_value=True),
        patch("ralph.skills.manager._visit_url_is_available", return_value=True),
        patch("ralph.skills.manager.probe_docs_mcp", return_value=True),
    ):
        mock_install.return_value = (
            CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            [],
        )
        manager.ensure_baseline_capabilities(workspace_root=tmp_path)

    cached_manager = SkillManager(
        state_path=state_path,
        policy=RecheckPolicy(healthy_recheck_hours=10_000),
    )
    with patch(
        "ralph.skills.manager.probe_docs_mcp",
        side_effect=AssertionError("cache hit should not probe"),
    ):
        assert cached_manager.get_docs_mcp_available(workspace_root=tmp_path) is True


def test_get_docs_mcp_available_probe_hit_saves_healthy(tmp_path: Path) -> None:
    manager = SkillManager(state_path=tmp_path / "state.json")
    with (
        patch("ralph.skills.manager._find_configured_docs_mcp_url", return_value="http://localhost:6280/mcp"),
        patch("ralph.skills.manager.probe_docs_mcp", return_value=True),
    ):
        assert manager.get_docs_mcp_available(workspace_root=tmp_path) is True


def test_get_docs_mcp_available_no_url_saves_not_installed(tmp_path: Path) -> None:
    manager = SkillManager(state_path=tmp_path / "state.json")
    with patch("ralph.skills.manager._find_configured_docs_mcp_url", return_value=None):
        assert manager.get_docs_mcp_available(workspace_root=tmp_path) is False


def test_check_baseline_health_marks_outdated_on_version_mismatch(
    tmp_path: Path,
) -> None:
    manager = SkillManager(state_path=tmp_path / "state.json")
    with (
        patch("ralph.skills.manager.install_baseline_skills") as mock_install,
        patch("ralph.skills.manager._find_configured_docs_mcp_url", return_value=None),
        patch("ralph.skills.manager._get_ralph_version", return_value="0.9.0"),
        patch("ralph.skills.manager._web_search_is_available", return_value=True),
        patch("ralph.skills.manager._visit_url_is_available", return_value=True),
    ):
        mock_install.return_value = (
            CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
            [],
        )
        manager.ensure_baseline_capabilities(workspace_root=tmp_path)

    with patch("ralph.skills.manager._get_ralph_version", return_value="1.0.0"):
        health = manager.check_baseline_health()

    assert health["web_search"] is False
    assert health["visit_url"] is False


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
