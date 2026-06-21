"""Tests to verify invoke_agent routing decisions for different transports."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import InvokeOptions, invoke_agent
from ralph.agents.registry import AgentRegistry
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport
from ralph.config.models import UnifiedConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def setup_default_registry() -> None:
    # This seeds the default catalog with builtins via registry creation
    AgentRegistry.from_config(UnifiedConfig())


def test_invoke_dispatch_table(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(str(MCP_ENDPOINT_ENV), raising=False)
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    pty_called = []
    sub_called = []

    monkeypatch.setattr(
        "ralph.agents.invoke.run_pty_and_read_lines",
        lambda cmd, ctx, extras=None: pty_called.append(cmd) or iter(["pty line"]),
    )
    monkeypatch.setattr(
        "ralph.agents.invoke.run_subprocess_and_read_lines",
        lambda cmd, ctx: sub_called.append(cmd) or iter(["sub line"]),
    )

    options = InvokeOptions(workspace_path=tmp_path, show_progress=False)

    # Helper to execute invoke and reset call tracking lists
    def check_routing(config: AgentConfig) -> str:
        pty_called.clear()
        sub_called.clear()
        _ = list(invoke_agent(config, str(prompt_file), options=options))
        if pty_called:
            return "pty"
        elif sub_called:
            return "subprocess"
        return "none"

    # (a) transport=CLAUDE_INTERACTIVE (built-in claude, now in default_catalog()) routes to PTY
    config_claude = AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE)
    assert check_routing(config_claude) == "pty"

    # (b) transport=AGY (built-in agy, now in default_catalog()) routes to PTY
    config_agy = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    assert check_routing(config_agy) == "pty"

    # (c) transport=CLAUDE (built-in claude-headless, now in default_catalog()) routes to subprocess
    config_headless = AgentConfig(cmd="claude -p", transport=AgentTransport.CLAUDE)
    assert check_routing(config_headless) == "subprocess"

    # (d) transport=CODEX (built-in codex, now in default_catalog()) routes to subprocess
    config_codex = AgentConfig(cmd="codex exec", transport=AgentTransport.CODEX)
    assert check_routing(config_codex) == "subprocess"

    # (e) unregistered transport=CLAUDE_INTERACTIVE agent routes to subprocess (the new default)
    config_unregistered = AgentConfig(
        cmd="unregistered-claude", transport=AgentTransport.CLAUDE_INTERACTIVE
    )
    assert check_routing(config_unregistered) == "subprocess"
