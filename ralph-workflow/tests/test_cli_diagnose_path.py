"""Black-box CLI tests verifying that ralph --diagnose renders PATH availability."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ralph.cli.main import app

KNOWN_DEFAULT_AGENTS = ("claude", "opencode")


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
    """ralph --diagnose must render an agent name with a PATH status on the same row."""
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    # Bootstrap configs so diagnose has a valid environment to report on
    runner.invoke(app, ["--init", "default"], catch_exceptions=False)

    result = runner.invoke(app, ["--diagnose"], catch_exceptions=False)

    output = result.output
    lines = output.splitlines()

    # At least one line must contain both a known default agent name
    # and a PATH status token on the same line.
    path_tokens = ("on PATH", "missing")
    found = any(
        any(agent in line for agent in KNOWN_DEFAULT_AGENTS)
        and any(token in line for token in path_tokens)
        for line in lines
    )
    assert found, (
        "Expected at least one output line to contain both an agent name "
        f"({', '.join(KNOWN_DEFAULT_AGENTS)}) and a PATH status "
        f"({', '.join(path_tokens)}).\nFull output:\n{output}"
    )
