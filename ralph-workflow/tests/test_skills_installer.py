"""Tests for ralph.skills._installer install_skill."""

from unittest.mock import patch

from ralph.skills._bundle import SkillInstallSpec
from ralph.skills._installer import install_skill


class TestInstallSkill:
    def test_install_skill_returns_true_on_success(self) -> None:
        spec = SkillInstallSpec(plugin_id="obra/superpowers", display_name="test")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            result = install_skill(spec)
            assert result is True
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][0] == ["claude", "plugin", "install", "obra/superpowers"]

    def test_install_skill_returns_false_on_failure(self) -> None:
        spec = SkillInstallSpec(plugin_id="obra/superpowers", display_name="test")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            result = install_skill(spec)
            assert result is False

    def test_install_skill_respects_timeout(self) -> None:
        spec = SkillInstallSpec(plugin_id="obra/superpowers", display_name="test")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            install_skill(spec, timeout=60)
            call_args = mock_run.call_args
            assert call_args[1]["timeout"] == 60
