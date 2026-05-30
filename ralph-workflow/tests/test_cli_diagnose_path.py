"""Black-box CLI tests verifying that ralph --diagnose renders PATH availability."""

from __future__ import annotations

import gc
import shutil as _shutil
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from ralph.agents.availability import check_agent_availability
from ralph.agents.registry import AgentRegistry
from ralph.cli.commands.diagnose import build_next_steps, check_agents
from ralph.cli.main import app
from ralph.config.enums import JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.theme import RALPH_THEME

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


@pytest.mark.timeout_seconds(3)
def test_diagnose_renders_agent_path_column(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ralph --diagnose must render an agent name with a PATH status on the same row."""
    del clean_env
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    runner.invoke(app, ["--init", "default"], catch_exceptions=False)

    # Force GC to flush any deferred file-buffer cleanup from shutil.copy2
    # that Python 3.14's stricter ResourceWarning would otherwise surface
    # under xdist process recycling.
    gc.collect()

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
    del clean_env
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
        f"Expected 'my-alias' to be 'available' (python on PATH), got: {result_map['my-alias']}"
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
    del clean_env
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
    buf_console = Console(file=buf, force_terminal=False, theme=RALPH_THEME)
    ctx = make_display_context(console=buf_console, env={})

    with (
        patch("ralph.cli.commands.diagnose.load_config", return_value=cfg),
        patch(
            "ralph.cli.commands.diagnose.AgentRegistry.from_config",
            return_value=base_registry,
        ),
    ):
        check_agents(None, display_context=ctx)

    output = buf.getvalue()
    lines = output.splitlines()

    alias_lines = [line for line in lines if "my-alias" in line]
    assert alias_lines, f"Expected 'my-alias' row in diagnose output.\nFull output:\n{output}"

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


def test_build_next_steps_no_prompt_recommends_init() -> None:
    """_build_next_steps must recommend ralph --init when PROMPT.md is absent."""
    steps = build_next_steps(
        validation_ok=True,
        agent_missing=False,
        prompt_exists=False,
        prompt_has_sentinel=False,
    )
    combined = " ".join(steps)
    assert "ralph --init" in combined, (
        f"Expected 'ralph --init' in next-steps when prompt_exists=False, got: {steps}"
    )


def test_build_next_steps_sentinel_recommends_edit() -> None:
    """_build_next_steps must recommend editing PROMPT.md when sentinel is present."""
    steps = build_next_steps(
        validation_ok=True,
        agent_missing=False,
        prompt_exists=True,
        prompt_has_sentinel=True,
    )
    combined = " ".join(steps)
    sentinel_mentioned = (
        "starter-prompt" in combined.lower()
        or "sentinel" in combined.lower()
        or "marker" in combined.lower()
    )
    assert sentinel_mentioned, (
        "Expected sentinel/marker reference in next-steps "
        f"when prompt_has_sentinel=True, got: {steps}"
    )
    assert "PROMPT.md" in combined, (
        f"Expected 'PROMPT.md' in next-steps when prompt_has_sentinel=True, got: {steps}"
    )


def test_build_next_steps_agent_missing_recommends_install() -> None:
    """_build_next_steps must recommend agent installation when an agent is missing."""
    steps = build_next_steps(
        validation_ok=True,
        agent_missing=True,
        prompt_exists=True,
        prompt_has_sentinel=False,
    )
    combined = " ".join(steps)
    assert "claude" in combined.lower() or "opencode" in combined.lower(), (
        f"Expected agent name in next-steps when agent_missing=True, got: {steps}"
    )
    assert "install" in combined.lower(), (
        f"Expected 'install' in next-steps when agent_missing=True, got: {steps}"
    )


def test_build_next_steps_agent_missing_includes_agy_url() -> None:
    """_build_next_steps must include the AGY install URL in missing-agent guidance."""
    steps = build_next_steps(
        validation_ok=True,
        agent_missing=True,
        prompt_exists=True,
        prompt_has_sentinel=False,
    )
    combined = " ".join(steps)
    assert "https://github.com/google-antigravity/antigravity-cli" in combined


def test_build_next_steps_validation_failed_recommends_regenerate() -> None:
    """_build_next_steps must mention regenerate-config when validation failed."""
    steps = build_next_steps(
        validation_ok=False,
        agent_missing=False,
        prompt_exists=True,
        prompt_has_sentinel=False,
    )
    combined = " ".join(steps)
    assert "regenerate-config" in combined or "validation failed" in combined.lower(), (
        f"Expected regenerate-config hint when validation_ok=False, got: {steps}"
    )


def test_build_next_steps_all_ok_recommends_run() -> None:
    """_build_next_steps must recommend running ralph when everything is ready."""
    steps = build_next_steps(
        validation_ok=True,
        agent_missing=False,
        prompt_exists=True,
        prompt_has_sentinel=False,
    )
    combined = " ".join(steps)
    assert "ralph" in combined, (
        f"Expected 'ralph' run recommendation in next-steps when all ok, got: {steps}"
    )


@pytest.mark.timeout_seconds(3)
def test_diagnose_next_steps_panel_rendered_in_cli(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ralph --diagnose must render a 'Next steps' panel in its output."""
    del clean_env
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    runner.invoke(app, ["--init", "default"], catch_exceptions=False)
    gc.collect()

    result = runner.invoke(app, ["--diagnose"], catch_exceptions=False)

    assert "Next steps" in result.output, (
        f"Expected 'Next steps' panel in --diagnose output, got: {result.output}"
    )
    assert "getting-started" in result.output, (
        f"Expected 'getting-started' pointer in --diagnose Next steps panel, got: {result.output}"
    )


@pytest.mark.timeout_seconds(3)
def test_diagnose_next_steps_points_to_getting_started_when_no_prompt(
    clean_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ralph --diagnose next steps must mention ralph --init when PROMPT.md is absent."""
    del clean_env
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)

    # Initialize global configs but do NOT create PROMPT.md
    runner.invoke(app, ["--check-config"], catch_exceptions=False)

    result = runner.invoke(app, ["--diagnose"], catch_exceptions=False)

    assert "ralph --init" in result.output, (
        f"Expected 'ralph --init' in Next steps when PROMPT.md absent, got: {result.output}"
    )
