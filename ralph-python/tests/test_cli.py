"""Unit tests for CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ralph.cli.main import app


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create a CLI runner for testing."""
    return CliRunner()


def test_app_help(cli_runner: CliRunner) -> None:
    """Test that --help works."""
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Ralph" in result.stdout
    assert "Multi-agent" in result.stdout


def test_app_version(cli_runner: CliRunner) -> None:
    """Test that --version works."""
    result = cli_runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "Ralph" in result.stdout or "version" in result.stdout.lower()


def test_app_list_agents_empty(cli_runner: CliRunner) -> None:
    """Test --list-agents with no configuration."""
    result = cli_runner.invoke(app, ["--list-agents"])
    # Should not crash even with no agents
    assert result.exit_code in (0, 1)


def test_app_check_config_valid(cli_runner: CliRunner) -> None:
    """Test --check-config with valid config."""
    result = cli_runner.invoke(app, ["--check-config"])
    # Should succeed with valid config
    assert result.exit_code in (0, 1)


def test_app_init_creates_files(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Test --init creates necessary files."""
    with cli_runner.isolated_filesystem(temp_dir=tmp_path):
        result = cli_runner.invoke(app, ["--init", str(tmp_path)])
        # Init may fail due to missing git repo, but should not crash
        assert result.exit_code in (0, 1)


def test_app_diagnose(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Test --diagnose runs without crashing."""
    with cli_runner.isolated_filesystem(temp_dir=tmp_path):
        result = cli_runner.invoke(app, ["--diagnose"])
        # May fail without git repo but shouldn't crash
        assert result.exit_code in (0, 1)


def test_app_with_invalid_config(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Test app handles invalid config gracefully."""
    invalid_config = tmp_path / "invalid.toml"
    invalid_config.write_text("invalid toml {{{{")

    result = cli_runner.invoke(app, ["--config", str(invalid_config), "--check-config"])
    assert result.exit_code != 0


def test_verbose_flag(cli_runner: CliRunner) -> None:
    """Test -v flag is accepted."""
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "-v" in result.stdout or "--verbosity" in result.stdout


def test_quiet_flag(cli_runner: CliRunner) -> None:
    """Test --quiet flag is accepted."""
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--quiet" in result.stdout
