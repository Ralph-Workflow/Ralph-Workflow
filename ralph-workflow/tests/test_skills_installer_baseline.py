"""Tests for ralph.skills._installer install_baseline_skills."""

from unittest.mock import patch

from ralph.skills._bundle import SkillInstallSpec
from ralph.skills._installer import install_baseline_skills
from ralph.skills._state import CapabilityStatus


class TestInstallBaselineSkills:
    def test_all_success_returns_installed_healthy(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            entry, failures = install_baseline_skills()
            assert entry.status == CapabilityStatus.INSTALLED_HEALTHY
            assert failures == []

    def test_all_fail_returns_needs_repair(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            entry, failures = install_baseline_skills()
            assert entry.status == CapabilityStatus.NEEDS_REPAIR
            assert len(failures) == 2  # Both specs failed

    def test_partial_success_returns_installed_degraded(self) -> None:
        call_count = [0]

        def side_effect(*args: object, **kwargs: object) -> object:
            call_count[0] += 1
            return type("obj", (object,), {"returncode": 0 if call_count[0] == 1 else 1})()

        with patch("subprocess.run", side_effect=side_effect):
            entry, failures = install_baseline_skills()
            assert entry.status == CapabilityStatus.INSTALLED_DEGRADED
            assert len(failures) == 1

    def test_custom_bundle_used(self) -> None:
        custom_bundle = (SkillInstallSpec("custom/plugin", "Custom"),)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            install_baseline_skills(custom_bundle)
            call_args = mock_run.call_args
            assert "custom/plugin" in call_args[0][0]
