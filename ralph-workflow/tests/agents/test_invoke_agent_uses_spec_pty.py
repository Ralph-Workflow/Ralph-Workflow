"""Tests to verify that invoke_agent routes to PTY vs subprocess using AgentSpec requires_pty."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.catalog import default_catalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.invoke import InvokeOptions, invoke_agent
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.spec import AgentSpec
from ralph.agents.support import AgentSupport
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import pytest


class FakeAgentParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        yield AgentOutputLine(type="raw", content=line, raw=line)


class FakeAgentStrategy(BaseExecutionStrategy):
    pass


def test_spec_requires_pty_true_uses_pty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(str(MCP_ENDPOINT_ENV), raising=False)
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    support = AgentSupport(
        name="fake-pty-binary",
        spec=AgentSpec(
            name="fake-pty-binary",
            interactive=True,
            requires_pty=True,
            transport=AgentTransport.GENERIC,
        ),
        parser_factory=FakeAgentParser,
        strategy_factory=FakeAgentStrategy,
        config=AgentConfig(cmd="fake-pty-binary", transport=AgentTransport.GENERIC),
    )

    default_catalog().add(support)
    try:
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

        config = AgentConfig(cmd="fake-pty-binary", transport=AgentTransport.GENERIC)
        # Skip the real WorkspaceMonitor watchdog observer: this test
        # only exercises routing decisions (PTY vs subprocess), so the
        # observer's start/stop cost would otherwise eat the 1-second
        # per-test budget on a slow machine.
        options = InvokeOptions(
            workspace_path=tmp_path,
            show_progress=False,
            workspace_monitor_factory=lambda *args, **kwargs: None,
        )

        res = list(invoke_agent(config, str(prompt_file), options=options))

        assert "pty line" in res
        assert len(pty_called) == 1
        assert len(sub_called) == 0
    finally:
        default_catalog().remove("fake-pty-binary")


def test_spec_requires_pty_false_uses_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(str(MCP_ENDPOINT_ENV), raising=False)
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    support = AgentSupport(
        name="fake-sub-binary",
        spec=AgentSpec(
            name="fake-sub-binary",
            requires_pty=False,
            transport=AgentTransport.CLAUDE_INTERACTIVE,
        ),
        parser_factory=FakeAgentParser,
        strategy_factory=FakeAgentStrategy,
        config=AgentConfig(cmd="fake-sub-binary", transport=AgentTransport.CLAUDE_INTERACTIVE),
    )

    default_catalog().add(support)
    try:
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

        config = AgentConfig(cmd="fake-sub-binary", transport=AgentTransport.CLAUDE_INTERACTIVE)
        # Skip the real WorkspaceMonitor watchdog observer: this test
        # only exercises routing decisions (PTY vs subprocess), so the
        # observer's start/stop cost would otherwise eat the 1-second
        # per-test budget on a slow machine.
        options = InvokeOptions(
            workspace_path=tmp_path,
            show_progress=False,
            workspace_monitor_factory=lambda *args, **kwargs: None,
        )

        res = list(invoke_agent(config, str(prompt_file), options=options))

        assert "sub line" in res
        assert len(pty_called) == 0
        assert len(sub_called) == 1
    finally:
        default_catalog().remove("fake-sub-binary")


def test_unregistered_claude_interactive_uses_subprocess_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Registering through register_agent_support is the only path
    to PTY routing for CLAUDE_INTERACTIVE.
    """
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

    config = AgentConfig(cmd="unregistered-binary", transport=AgentTransport.CLAUDE_INTERACTIVE)
    # Skip the real WorkspaceMonitor watchdog observer: this test
    # only exercises routing decisions (PTY vs subprocess), so the
    # observer's start/stop cost would otherwise eat the 1-second
    # per-test budget on a slow machine or under xdist contention.
    options = InvokeOptions(
        workspace_path=tmp_path,
        show_progress=False,
        workspace_monitor_factory=lambda *args, **kwargs: None,
    )

    res = list(invoke_agent(config, str(prompt_file), options=options))

    assert "sub line" in res
    assert len(pty_called) == 0
    assert len(sub_called) == 1


def test_unregistered_codex_uses_subprocess_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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

    config = AgentConfig(cmd="unregistered-binary-codex", transport=AgentTransport.CODEX)
    # Skip the real WorkspaceMonitor watchdog observer: this test
    # only exercises routing decisions (PTY vs subprocess), so the
    # observer's start/stop cost would otherwise eat the 1-second
    # per-test budget on a slow machine.
    options = InvokeOptions(
        workspace_path=tmp_path,
        show_progress=False,
        workspace_monitor_factory=lambda *args, **kwargs: None,
    )

    res = list(invoke_agent(config, str(prompt_file), options=options))

    assert "sub line" in res
    assert len(pty_called) == 0
    assert len(sub_called) == 1
