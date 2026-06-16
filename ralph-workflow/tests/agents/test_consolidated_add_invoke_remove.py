"""Black-box end-to-end test for registering, invoking, and unregistering agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents import (
    AgentCatalog,
    AgentRegistry,
    AgentSupport,
    default_catalog,
    invoke_agent,
    register_agent_support,
)
from ralph.agents.invoke import InvokeOptions
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.registration import register_agent_support_to_catalog
from ralph.config.enums import AgentTransport
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import pytest


class FakeParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        yield AgentOutputLine(type="raw", content=line, raw=line)


class FakeStrategy:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass


def test_consolidated_headless_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(str(MCP_ENDPOINT_ENV), raising=False)
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    pty_called = []
    sub_called = []

    monkeypatch.setattr(
        "ralph.agents.invoke.run_pty_and_read_lines",
        lambda cmd, ctx, extras=None: (pty_called.append(cmd) or iter(["pty line"])),
    )
    monkeypatch.setattr(
        "ralph.agents.invoke.run_subprocess_and_read_lines",
        lambda cmd, ctx: (sub_called.append(cmd) or iter(["sub line"])),
    )

    # 1. Isolation check using fresh_catalog
    fresh_catalog = AgentCatalog()
    isolated_registry = AgentRegistry(catalog=fresh_catalog)
    name = "test-headless-flow-agent"

    # (a) build fake headless support
    support = AgentSupport.from_registration_kwargs(
        name=name,
        transport=AgentTransport.GENERIC,
        parser_factory=FakeParser,
        strategy_factory=FakeStrategy,
        interactive=False,
    )

    # (b) add it to fresh AgentCatalog
    register_agent_support_to_catalog(name, support, fresh_catalog)
    isolated_registry.register(name, support.config)

    # Verify config and pty requirements on isolated catalog
    config = isolated_registry.get(name)
    assert config is not None
    assert fresh_catalog.get(name) is not None
    assert fresh_catalog.get(name).spec.requires_pty is False

    # (d) call unregister and assert it is removed from both registry and isolated catalog
    isolated_registry.unregister(name)
    assert name not in isolated_registry.agents
    assert fresh_catalog.get(name) is None

    # 2. Invoke check using default_catalog
    default_registry = AgentRegistry()
    register_agent_support(
        name=name,
        transport=AgentTransport.GENERIC,
        parser_factory=FakeParser,
        strategy_factory=FakeStrategy,
        agent_registry=default_registry,
        interactive=False,
    )
    options = InvokeOptions(workspace_path=tmp_path, show_progress=False)
    res = list(invoke_agent(support.config, str(prompt_file), options=options))
    assert "sub line" in res
    assert len(sub_called) == 1
    assert len(pty_called) == 0

    # Teardown default catalog
    default_registry.unregister(name)
    assert name not in default_registry.agents
    assert default_catalog().get(name) is None


def test_consolidated_interactive_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(str(MCP_ENDPOINT_ENV), raising=False)
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello", encoding="utf-8")

    pty_called = []
    sub_called = []

    monkeypatch.setattr(
        "ralph.agents.invoke.run_pty_and_read_lines",
        lambda cmd, ctx, extras=None: (pty_called.append(cmd) or iter(["pty line"])),
    )
    monkeypatch.setattr(
        "ralph.agents.invoke.run_subprocess_and_read_lines",
        lambda cmd, ctx: (sub_called.append(cmd) or iter(["sub line"])),
    )

    # 1. Isolation check using fresh_catalog
    fresh_catalog = AgentCatalog()
    isolated_registry = AgentRegistry(catalog=fresh_catalog)
    name = "test-interactive-flow-agent"

    # (a) build fake interactive support
    support = AgentSupport.from_registration_kwargs(
        name=name,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        parser_factory=FakeParser,
        strategy_factory=FakeStrategy,
        interactive=True,
    )

    # (b) add it to fresh AgentCatalog
    register_agent_support_to_catalog(name, support, fresh_catalog)
    isolated_registry.register(name, support.config)

    # Verify config and pty requirements
    config = isolated_registry.get(name)
    assert config is not None
    assert fresh_catalog.get(name) is not None
    assert fresh_catalog.get(name).spec.requires_pty is True

    # (d) call unregister and assert it is removed from both registry and isolated catalog
    isolated_registry.unregister(name)
    assert name not in isolated_registry.agents
    assert fresh_catalog.get(name) is None

    # 2. Invoke check using default_catalog
    default_registry = AgentRegistry()
    register_agent_support(
        name=name,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        parser_factory=FakeParser,
        strategy_factory=FakeStrategy,
        agent_registry=default_registry,
        interactive=True,
    )
    options = InvokeOptions(workspace_path=tmp_path, show_progress=False)
    res = list(invoke_agent(support.config, str(prompt_file), options=options))
    assert "pty line" in res
    assert len(pty_called) == 1
    assert len(sub_called) == 0

    # Teardown default catalog
    default_registry.unregister(name)
    assert name not in default_registry.agents
    assert default_catalog().get(name) is None
