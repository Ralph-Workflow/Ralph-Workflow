"""Tests for ralph.skills.manager.SkillManager."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ralph.skills._state import CapabilityEntry, CapabilityStatus
from ralph.skills.manager import SkillManager


class TestSkillManager:
    @pytest.fixture
    def manager(self, tmp_path: pytest.TempdirFactory) -> SkillManager:
        state_path = tmp_path / "state.json"
        return SkillManager(state_path=state_path)

    def test_ensure_baseline_capabilities_stamps_web_search_and_visit_url_with_version(
        self,
        manager: SkillManager,
        tmp_path: pytest.TempdirFactory,
    ) -> None:
        with (
            patch("ralph.skills.manager.install_baseline_skills") as mock_install,
            patch(
                "ralph.skills.manager._find_configured_docs_mcp_url",
                return_value=None,
            ),
            patch(
                "ralph.skills.manager._get_ralph_version",
                return_value="1.2.3",
            ),
        ):
            mock_install.return_value = (
                CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                [],
            )
            result = manager.ensure_baseline_capabilities(
                workspace_root=tmp_path,
            )

        assert result.web_search.status == CapabilityStatus.INSTALLED_HEALTHY
        assert result.web_search.ralph_version == "1.2.3"
        assert result.visit_url.status == CapabilityStatus.INSTALLED_HEALTHY
        assert result.visit_url.ralph_version == "1.2.3"

    def test_ensure_baseline_capabilities_marks_skills_needs_repair_on_failure(
        self,
        manager: SkillManager,
        tmp_path: pytest.TempdirFactory,
    ) -> None:
        with (
            patch("ralph.skills.manager.install_baseline_skills") as mock_install,
            patch(
                "ralph.skills.manager._find_configured_docs_mcp_url",
                return_value=None,
            ),
            patch(
                "ralph.skills.manager._get_ralph_version",
                return_value="1.0.0",
            ),
        ):
            mock_install.return_value = (
                CapabilityEntry(status=CapabilityStatus.NEEDS_REPAIR),
                ["obra/superpowers"],
            )
            result = manager.ensure_baseline_capabilities(
                workspace_root=tmp_path,
            )

        assert result.skills.status == CapabilityStatus.NEEDS_REPAIR

    def test_check_baseline_health_returns_four_key_dict(
        self,
        manager: SkillManager,
        tmp_path: pytest.TempdirFactory,
    ) -> None:
        # First set up a state with healthy entries
        with (
            patch("ralph.skills.manager.install_baseline_skills") as mock_install,
            patch(
                "ralph.skills.manager._find_configured_docs_mcp_url",
                return_value=None,
            ),
            patch(
                "ralph.skills.manager._get_ralph_version",
                return_value="1.0.0",
            ),
        ):
            mock_install.return_value = (
                CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                [],
            )
            manager.ensure_baseline_capabilities(workspace_root=tmp_path)

        health = manager.check_baseline_health()
        assert isinstance(health, dict)
        assert set(health.keys()) == {"web_search", "visit_url", "docs_mcp", "skills"}
        assert all(isinstance(v, bool) for v in health.values())

    def test_check_baseline_health_detects_outdated_on_version_mismatch(
        self,
        manager: SkillManager,
        tmp_path: pytest.TempdirFactory,
    ) -> None:
        # Set up with old version
        with (
            patch("ralph.skills.manager.install_baseline_skills") as mock_install,
            patch(
                "ralph.skills.manager._find_configured_docs_mcp_url",
                return_value=None,
            ),
            patch(
                "ralph.skills.manager._get_ralph_version",
                return_value="1.0.0",
            ),
        ):
            mock_install.return_value = (
                CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                [],
            )
            manager.ensure_baseline_capabilities(workspace_root=tmp_path)

        # Now simulate ralph upgrade
        with patch(
            "ralph.skills.manager._get_ralph_version",
            return_value="2.0.0",
        ):
            health = manager.check_baseline_health()

        # Old version entry should now be outdated
        assert health["web_search"] is False
        assert health["visit_url"] is False

    def test_check_baseline_health_no_change_when_versions_match(
        self,
        manager: SkillManager,
        tmp_path: pytest.TempdirFactory,
    ) -> None:
        version = "1.0.0"
        with (
            patch("ralph.skills.manager.install_baseline_skills") as mock_install,
            patch(
                "ralph.skills.manager._find_configured_docs_mcp_url",
                return_value=None,
            ),
            patch(
                "ralph.skills.manager._get_ralph_version",
                return_value=version,
            ),
        ):
            mock_install.return_value = (
                CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY),
                [],
            )
            manager.ensure_baseline_capabilities(workspace_root=tmp_path)

        with patch(
            "ralph.skills.manager._get_ralph_version",
            return_value=version,
        ):
            health = manager.check_baseline_health()

        # Should remain healthy since versions match
        assert health["web_search"] is True
        assert health["visit_url"] is True

    def test_check_skills_for_updates_updates_state_when_update_found(
        self,
        manager: SkillManager,
        tmp_path: pytest.TempdirFactory,
    ) -> None:
        # Set up with old state
        with (
            patch("ralph.skills.manager.install_baseline_skills") as mock_install,
            patch(
                "ralph.skills.manager._find_configured_docs_mcp_url",
                return_value=None,
            ),
            patch(
                "ralph.skills.manager._get_ralph_version",
                return_value="1.0.0",
            ),
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
            result = manager.check_skills_for_updates()
            assert result is True
            mock_check.assert_called_once()



