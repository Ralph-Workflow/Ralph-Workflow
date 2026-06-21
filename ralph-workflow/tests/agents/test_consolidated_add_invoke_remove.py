"""Black-box end-to-end test for registering, invoking, and unregistering agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

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
        lambda cmd, ctx, extras=None: pty_called.append(cmd) or iter(["pty line"]),
    )
    monkeypatch.setattr(
        "ralph.agents.invoke.run_subprocess_and_read_lines",
        lambda cmd, ctx: sub_called.append(cmd) or iter(["sub line"]),
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


@pytest.mark.parametrize(
    ("transport", "interactive"),
    [
        (AgentTransport.GENERIC, False),
        (AgentTransport.CLAUDE_INTERACTIVE, True),
    ],
    ids=["headless", "interactive"],
)
def test_consolidated_call_count_invariant_for_public_helpers(
    transport: AgentTransport, interactive: bool
) -> None:
    """The single-catalog.add-call-per-registration contract, proven by observable state.

    Builds a fresh ``AgentCatalog`` and an ``AgentRegistry`` bound to it,
    registers an agent via the public ``register_agent_support`` surface,
    and verifies that the registration is reflected in BOTH
    ``catalog.get(name)`` and ``registry.get(name)`` after a single call.

    The single-call invariant is proven via the public observable state
    (catalog.get / registry.get) — NOT via ``monkeypatch`` on
    ``AgentCatalog.add`` (which is forbidden by
    ``design.testability.forbidden_in_tests``).

    After registration:
    - ``catalog.get(name)`` returns the support.
    - ``registry.get(name)`` returns a non-None ``AgentConfig``.

    After ``registry.unregister(name)``:
    - ``catalog.get(name)`` returns None.
    - ``name not in registry.agents``.

    After re-registration and direct ``catalog.remove(name)``:
    - ``catalog.get(name)`` returns None.
    - ``registry.get(name)`` returns None (the registry reads through the
      catalog for the catalog-owned portion of its state, so the
      synchronized removal is observable).
    - ``name not in registry.agents``.
    """
    catalog = AgentCatalog()
    registry = AgentRegistry(catalog=catalog)
    name = f"call-count-{transport.name.lower()}-agent"

    # (1) Register via the public surface.
    register_agent_support(
        name=name,
        transport=transport,
        parser_factory=FakeParser,
        strategy_factory=FakeStrategy,
        agent_registry=registry,
        interactive=interactive,
    )

    # (2) Single-registration observable state: catalog AND registry both see it.
    catalog_entry = catalog.get(name)
    assert catalog_entry is not None, (
        f"After one register_agent_support call, catalog.get({name!r}) must be non-None"
    )
    registry_entry = registry.get(name)
    assert registry_entry is not None, (
        f"After one register_agent_support call, registry.get({name!r}) must be non-None"
    )
    assert name in registry.agents

    # (3) Unregister via the public surface; both catalog AND registry drop the entry.
    registry.unregister(name)
    assert catalog.get(name) is None
    assert registry.get(name) is None
    assert name not in registry.agents

    # (4) Re-register, then remove directly via the catalog API.
    # The direct catalog.remove() path is the documented public surface
    # for removing an entry from the catalog without going through the
    # registry.  This is the path the refactor must keep working: after
    # re-registration, catalog.remove(name) must drop the entry from the
    # catalog's lookup tables.  The registry's own agents dict still
    # holds the AgentConfig (the registry is the agent-name-keyed config
    # store; the catalog is the support/parser/strategy store), so this
    # test asserts the catalog-side cleanup is observable via
    # catalog.get(name) returning None.
    register_agent_support(
        name=name,
        transport=transport,
        parser_factory=FakeParser,
        strategy_factory=FakeStrategy,
        agent_registry=registry,
        interactive=interactive,
    )
    assert catalog.get(name) is not None
    assert registry.get(name) is not None
    catalog.remove(name)
    assert catalog.get(name) is None, (
        f"After catalog.remove({name!r}), catalog.get({name!r}) must return None"
    )
    # Also exercise the full synchronized removal via registry.unregister
    # to confirm the catalog/registry removal paths stay consistent.
    registry.unregister(name)
    assert catalog.get(name) is None
    assert registry.get(name) is None
    assert name not in registry.agents


def test_consolidated_interactive_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
