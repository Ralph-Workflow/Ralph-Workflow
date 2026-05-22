"""Tests for ralph.skills._installer check_skills_update_available."""

from unittest.mock import patch

from ralph.skills._installer import check_skills_update_available


class TestCheckSkillsUpdateAvailable:
    def test_returns_true_when_update_available_text_in_output(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = b""
            mock_run.return_value.stderr = b"  obra/superpowers - update available\n"
            result = check_skills_update_available()
            assert result is True

    def test_returns_true_when_updates_available_plural_in_output(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = b"obra/superpowers - updates available\n"
            mock_run.return_value.stderr = b""
            result = check_skills_update_available()
            assert result is True

    def test_returns_false_on_non_zero_exit(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            result = check_skills_update_available()
            assert result is False

    def test_returns_false_on_timeout(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = TimeoutError()
            result = check_skills_update_available()
            assert result is False

    def test_returns_false_when_no_update_text(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = b"obra/superpowers - installed\n"
            mock_run.return_value.stderr = b""
            result = check_skills_update_available()
            assert result is False

    def test_case_insensitive_detection(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = b"UPDATE AVAILABLE\n"
            mock_run.return_value.stderr = b""
            result = check_skills_update_available()
            assert result is True
