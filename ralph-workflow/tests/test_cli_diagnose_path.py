"""Black-box CLI tests verifying that ralph --diagnose renders PATH availability."""

from __future__ import annotations

import shutil as _shutil
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

import ralph.cli.commands.diagnose as diag_mod
from ralph.agents.availability import check_agent_availability
from ralph.agents.registry import AgentRegistry
from ralph.cli.commands.diagnose import _check_agents
from ralph.cli.main import app
from ralph.config.enums import JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig

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

    runner.invoke(app, ["--init", "default"], catch_exceptions=False)

    result = runner.invoke(app, ["--diagnose"], catch_exceptions=False)

    output = result.output
    lines = output.splitlines()

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


def test_diagnose_alias_with_different_display_name_shows_correct_path_status(
    clean_env: dict[str, str],
) -> None:
    """Configured alias whose registry key differs from cmd must show correct PATH status.

    Guards against the keying bug where path_by_name was keyed by display_name
    while diagnose iterated by registry name, causing aliases to always show 'missing'.
    """
    custom_agent = AgentConfig(
        cmd="python",
        output_flag="--json",
        can_commit=False,
        json_parser=JsonParserType.GENERIC,
        display_name="My Python Agent",
    )
    cfg = UnifiedConfig()
    registry = AgentRegistry.from_config(cfg)
    registry.register("my-alias", custom_agent)

    with patch(
        "shutil.which",
        side_effect=lambda cmd: "/usr/bin/python" if cmd == "python" else None,
    ):
        results = check_agent_availability(registry)

    result_map = dict(results)
    assert "my-alias" in result_map, (
        f"Expected 'my-alias' (registry key) in availability results, got: {list(result_map)}"
    )
    assert result_map["my-alias"] == "available", (
        f"Expected 'my-alias' to be 'available' (python on PATH), "
        f"got: {result_map['my-alias']}"
    )
    assert "My Python Agent" not in result_map, (
        "display_name 'My Python Agent' must not be used as key in availability results"
    )


def test_diagnose_alias_path_status_rendered_in_cli(
    clean_env: dict[str, str],
) -> None:
    """ralph --diagnose must show correct PATH status for a custom alias agent.

    The alias registry key 'my-alias' with cmd 'python' (on PATH) must render
    'on PATH', not 'missing', even though its registry key differs from the cmd name.
    """
    custom_agent = AgentConfig(
        cmd="python",
        output_flag="--json",
        can_commit=False,
        json_parser=JsonParserType.GENERIC,
        display_name="My Python Agent",
    )

    cfg = UnifiedConfig()
    base_registry = AgentRegistry.from_config(cfg)
    base_registry.register("my-alias", custom_agent)

    buf = StringIO()
    original_console = diag_mod.console

    diag_mod.console = Console(file=buf, force_terminal=False)
    try:
        with (
            patch("ralph.cli.commands.diagnose.load_config", return_value=cfg),
            patch(
                "ralph.cli.commands.diagnose.AgentRegistry.from_config",
                return_value=base_registry,
            ),
        ):
            _check_agents(None)
    finally:
        diag_mod.console = original_console

    output = buf.getvalue()
    lines = output.splitlines()

    alias_lines = [line for line in lines if "my-alias" in line]
    assert alias_lines, (
        f"Expected 'my-alias' row in diagnose output.\nFull output:\n{output}"
    )

    alias_line = alias_lines[0]
    assert "on PATH" in alias_line or "missing" in alias_line, (
        f"Expected PATH status on 'my-alias' row, got: {alias_line!r}"
    )

    python_on_path = _shutil.which("python") is not None
    if python_on_path:
        assert "on PATH" in alias_line, (
            f"python is on PATH but 'my-alias' row shows wrong status: {alias_line!r}\n"
            f"Full output:\n{output}"
        )
