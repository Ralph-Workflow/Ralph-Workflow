"""Black-box CLI tests verifying that ralph --diagnose renders PATH availability."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ralph.cli.main import app


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set up a clean environment with temporary config and home directories."""
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / ".config"),
        "HOME": str(tmp_path / ".home"),
    }
    for key, val in env.items():
        monkeypatch.setenv(key, val)
        Path(val).mkdir(parents=True, exist_ok=True)
    return env


def test_diagnose_renders_agent_path_column(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ralph --diagnose must render an agent name with a PATH status column."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    # Bootstrap configs so diagnose has a valid environment to report on
    runner.invoke(app, ["--init", "default"], catch_exceptions=False)

    result = runner.invoke(app, ["--diagnose"], catch_exceptions=False)

    output = result.output
    # The diagnose output should contain "on PATH" or "missing" for at least one agent
    has_path_status = "on PATH" in output or "missing" in output
    assert has_path_status, (
        f"Expected diagnose output to contain 'on PATH' or 'missing' "
        f"for agent PATH status column, got:\n{output}"
    )

    # The agents section header should be present
    assert "Agents" in output, (
        f"Expected 'Agents' table in diagnose output, got:\n{output}"
    )
