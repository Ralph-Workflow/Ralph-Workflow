"""Integration tests for the production SubagentPidRegistry wiring.

The Trustworthy Idle Watchdog product spec (R1, R5) requires that a
process is a real subagent iff (a) it is a live descendant of the
supervised agent PID AND (b) it is REGISTERED in the shared
``SubagentPidRegistry`` by the transport's authoritative
``SubagentPidSource``. The registry is the FILTERED signal the
watchdog defers on; the broader ``descendant_snapshot()`` count is
the bug source.

These tests exercise the production wiring end-to-end:

    AgentRegistry.build_subagent_pid_registry(transport)
        -> (SubagentPidRegistry, SubagentPidSource)
        -> injected into BaseExecutionStrategy(subagent_pid_source=...)
        -> BaseExecutionStrategy.classify_quiet(handle, probe)
            returns WAITING_ON_CHILD iff subagent_pid_source has
            registered PIDs (NOT based on handle.has_live_descendants())

    ParserClass(subagent_pid_registry=...)
        -> stores the registry as ``self._subagent_pid_registry``
        -> parser events can register PIDs into the shared registry

    ParserClass with registry
        -> parser.parse(...) still works as expected
        -> the stored registry is exposed via the public attribute

These tests are pure black-box:

    * No real subprocess. No real time. No real filesystem.
    * ``FakeHandle`` and ``FakeLivenessProbe`` are the canonical
      black-box fakes for the BaseExecutionStrategy surface.
    * The registry is exercised directly with synthetic PIDs.

The previous development pass flagged this gap: the
``AgentRegistry.from_config`` path never threaded a shared
``SubagentPidRegistry`` into production and the parser constructors
silently discarded the registry. These tests prove the production
plumbing now wires the registry through every layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from ralph.agents.catalog import default_catalog
from ralph.agents.execution_state import (
    AgentExecutionState,
    BaseExecutionStrategy,
    strategy_for_transport,
)
from ralph.agents.idle_watchdog import SubagentPidRegistry
from ralph.agents.parsers import (
    AgyParser,
    ClaudeInteractiveParser,
    ClaudeParser,
    CodexParser,
    GeminiParser,
    GenericParser,
    PiParser,
)
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport
from ralph.config.models import UnifiedConfig
from ralph.process.child_liveness import ChildLivenessRegistry
from ralph.process.liveness import FakeLivenessProbe
from ralph.process.monitor import SubagentPidSource

if TYPE_CHECKING:
    from ralph.agents.parsers.base import AgentParser


@dataclass
class _FakeHandle:
    """Minimal handle stub exposing ``has_live_descendants``.

    Used to prove that ``BaseExecutionStrategy.classify_quiet`` does
    NOT consult the broader ``has_live_descendants`` count when a
    ``SubagentPidSource`` is injected (the R1/R2 invariant). When
    ``has_descendants`` is True, only a non-empty filtered PID set
    should force WAITING_ON_CHILD; an empty filtered set MUST
    return ACTIVE even with helpers alive.
    """

    has_descendants: bool = False
    returncode: int = 0


def _active_strategy_with_source(
    source: SubagentPidSource,
) -> BaseExecutionStrategy:
    """Build a ``BaseExecutionStrategy`` with an injected SubagentPidSource."""
    return BaseExecutionStrategy(subagent_pid_source=source)


# ---------------------------------------------------------------------------
# AgentRegistry.build_subagent_pid_registry wiring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("transport", list(AgentTransport))
def test_agent_registry_build_subagent_pid_registry_per_transport(
    transport: AgentTransport,
) -> None:
    """``AgentRegistry.build_subagent_pid_registry`` returns a per-transport pair.

    For each supported ``AgentTransport``, the helper MUST return a
    ``(SubagentPidRegistry, SubagentPidSource)`` pair where the source
    filters by the transport's source label. This is the production
    entry point the analysis flagged as missing.
    """
    agent_registry = AgentRegistry()
    registry, source = agent_registry.build_subagent_pid_registry(transport)
    assert isinstance(registry, SubagentPidRegistry)
    assert isinstance(source, SubagentPidSource)
    # Registering a PID for the correct transport source makes it
    # visible via the per-transport filtered source. Nanocoder is
    # mapped to the ``generic`` source label internally (no
    # dedicated ``make_nanocoder_*`` factory); use the canonical
    # label the factory binds.
    canonical_source = "generic" if transport == AgentTransport.NANOCODER else transport.value
    registry.register(12345, source=canonical_source, now=0.0)
    assert 12345 in source.known_subagent_pids()
    # A PID registered for a DIFFERENT transport is invisible (R1
    # isolation between per-transport filtered views).
    other_transport = (
        AgentTransport.CLAUDE if transport != AgentTransport.CLAUDE else AgentTransport.PI
    )
    other_registry, other_source = agent_registry.build_subagent_pid_registry(other_transport)
    other_source_label = (
        "generic" if other_transport == AgentTransport.NANOCODER else other_transport.value
    )
    other_registry.register(67890, source=other_source_label, now=0.0)
    assert 67890 not in source.known_subagent_pids()
    assert 67890 in other_source.known_subagent_pids()


def test_agent_registry_build_subagent_pid_registry_rejects_unknown_transport() -> None:
    """An unknown transport label raises ``ValueError`` -- no silent fallback."""
    agent_registry = AgentRegistry()
    with pytest.raises(ValueError, match="no SubagentPidSource factory"):
        agent_registry.build_subagent_pid_registry("not-a-transport")


def test_agent_registry_from_config_provides_subagent_registry_helper() -> None:
    """``AgentRegistry.from_config`` returns an instance with the helper attached.

    The canonical pipeline constructs the registry via
    ``AgentRegistry.from_config(config)`` and then calls
    ``build_subagent_pid_registry(transport)`` to obtain the per-
    invocation registry + source. This test proves the helper is
    available on the result of the canonical constructor.
    """
    config = UnifiedConfig()
    agent_registry = AgentRegistry.from_config(config)
    assert hasattr(agent_registry, "build_subagent_pid_registry")
    for transport in AgentTransport:
        registry, source = agent_registry.build_subagent_pid_registry(transport)
        assert isinstance(registry, SubagentPidRegistry)
        assert isinstance(source, SubagentPidSource)


# ---------------------------------------------------------------------------
# BaseExecutionStrategy.classify_quiet uses filtered signal ONLY
# ---------------------------------------------------------------------------


def test_classify_quiet_returns_waiting_when_filtered_source_has_pids() -> None:
    """A registered subagent PID forces WAITING_ON_CHILD (R1)."""
    agent_registry = AgentRegistry()
    registry, source = agent_registry.build_subagent_pid_registry(AgentTransport.CLAUDE)
    registry.register(4242, source="claude", now=0.0)
    strategy = _active_strategy_with_source(source)
    handle = _FakeHandle(has_descendants=True)
    probe = FakeLivenessProbe(active=False)
    state = strategy.classify_quiet(handle, probe)
    assert state == AgentExecutionState.WAITING_ON_CHILD


def test_classify_quiet_returns_active_when_filtered_source_empty_even_with_descendants() -> None:
    """Helper descendants without a registered PID MUST NOT defer the watchdog.

    R3 (hard ceiling fires with helpers alive): when the broader
    descendant tree contains shell helpers like ``npm test`` /
    ``cargo build`` BUT the filtered registry is empty, the
    watchdog's quiet-state MUST return ACTIVE. ``has_live_descendants``
    is the BUG SOURCE; the filtered source is the canonical signal.
    """
    agent_registry = AgentRegistry()
    _, source = agent_registry.build_subagent_pid_registry(AgentTransport.CLAUDE)
    strategy = _active_strategy_with_source(source)
    handle = _FakeHandle(has_descendants=True)
    probe = FakeLivenessProbe(active=False)
    state = strategy.classify_quiet(handle, probe)
    assert state == AgentExecutionState.ACTIVE


def test_classify_quiet_uses_registry_snapshot_when_no_pid_source() -> None:
    """A ChildLivenessRegistry snapshot with records forces WAITING_ON_CHILD.

    When only a ``ChildLivenessRegistry`` is injected (the OpenCode
    path -- no ``SubagentPidSource``), the registry's filtered
    snapshot is the canonical signal. ``handle.has_live_descendants``
    MUST NOT be consulted.
    """
    registry = ChildLivenessRegistry(
        progress_ttl=60.0,
        heartbeat_ttl=60.0,
        stale_label_ttl=60.0,
        exit_reconcile=5.0,
    )
    registry.register_child("child-A", "agent:test:", pid=9001)
    registry.record_progress("child-A")
    strategy = BaseExecutionStrategy(registry=registry)
    handle = _FakeHandle(has_descendants=True)
    probe = FakeLivenessProbe(active=False)
    state = strategy.classify_quiet(handle, probe)
    assert state == AgentExecutionState.WAITING_ON_CHILD


def test_classify_quiet_empty_registry_returns_active_even_with_descendants() -> None:
    """Empty ChildLivenessRegistry with ``has_descendants=True`` returns ACTIVE.

    The OpenCode path: a ChildLivenessRegistry is injected but
    has no records (the supervised agent dispatched no real
    subagents). Helper descendants visible to psutil MUST NOT
    block the watchdog.
    """
    registry = ChildLivenessRegistry(
        progress_ttl=60.0,
        heartbeat_ttl=60.0,
        stale_label_ttl=60.0,
        exit_reconcile=5.0,
    )
    strategy = BaseExecutionStrategy(registry=registry)
    handle = _FakeHandle(has_descendants=True)
    probe = FakeLivenessProbe(active=False)
    state = strategy.classify_quiet(handle, probe)
    assert state == AgentExecutionState.ACTIVE


def test_strategy_for_transport_threads_subagent_pid_source() -> None:
    """``strategy_for_transport(transport, subagent_pid_source=...)`` wires the source.

    The factory MUST accept and forward the injected source so the
    per-invocation SubagentPidRegistry reaches the strategy. Without
    this, the production wiring in ``invoke_agent`` cannot thread the
    registry into the strategy layer.
    """
    agent_registry = AgentRegistry()
    _, source = agent_registry.build_subagent_pid_registry(AgentTransport.CLAUDE)
    strategy = strategy_for_transport(
        AgentTransport.CLAUDE,
        subagent_pid_source=source,
    )
    assert strategy._subagent_pid_source is source


# ---------------------------------------------------------------------------
# Parser constructors retain the registry (forward-compat for future wiring)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("transport_label", "parser_cls"),
    [
        ("claude", ClaudeParser),
        ("claude_interactive", ClaudeInteractiveParser),
        ("codex", CodexParser),
        ("pi", PiParser),
        ("agy", AgyParser),
        ("generic", GenericParser),
        ("gemini", GeminiParser),
    ],
)
def test_parser_constructor_stores_subagent_pid_registry(
    transport_label: str,
    parser_cls: type[AgentParser],
) -> None:
    """Each parser constructor MUST STORE the registry, not discard it.

    The previous pass silently discarded the registry with ``del``.
    The fix: store as ``self._subagent_pid_registry`` so future code
    paths can register PIDs into the shared registry without
    re-plumbing the constructor signature.
    """
    # Gemini is a parser-only transport (no AgentTransport enum
    # entry); construct a bare ``SubagentPidRegistry`` so the test
    # is independent of the production factory map.
    if transport_label == "gemini":
        registry = SubagentPidRegistry()
    else:
        agent_registry = AgentRegistry()
        registry, _ = agent_registry.build_subagent_pid_registry(AgentTransport(transport_label))
    parser = parser_cls(subagent_pid_registry=registry)
    assert getattr(parser, "_subagent_pid_registry", None) is registry


def test_parser_default_constructor_keeps_registry_none() -> None:
    """Constructing a parser without a registry keeps the attribute None.

    The fix must NOT regress the default ``parser_factory()`` zero-arg
    call (used by the legacy plumbing in ``smoke_plumbing`` and
    ``commit_plumbing``).
    """
    parser = ClaudeParser()
    assert getattr(parser, "_subagent_pid_registry", None) is None
    parser = CodexParser()
    assert getattr(parser, "_subagent_pid_registry", None) is None
    parser = GeminiParser()
    assert getattr(parser, "_subagent_pid_registry", None) is None


def test_parser_with_registry_can_still_parse_lines() -> None:
    """Constructing a parser with a registry MUST NOT regress parsing.

    The ``parse()`` path must continue to produce the same
    ``AgentOutputLine`` stream regardless of whether the registry
    is supplied. This is a regression guard for the constructor
    change.
    """
    agent_registry = AgentRegistry()
    registry, _ = agent_registry.build_subagent_pid_registry(AgentTransport.CODEX)
    parser = CodexParser(subagent_pid_registry=registry)
    lines = ['{"type": "text", "content": "hello world"}']
    events = list(parser.parse(iter(lines)))
    assert events, "parser.parse MUST still yield events with a registry"
    assert events[0].content == "hello world"


# ---------------------------------------------------------------------------
# End-to-end: registry → source → strategy → watchdog state path
# ---------------------------------------------------------------------------


def test_end_to_end_filtered_count_is_visible_to_strategy_classify_quiet() -> None:
    """Full pipeline: register PID → strategy sees WAITING_ON_CHILD via filtered source.

    This is the integration contract: a PID registered into the
    shared ``SubagentPidRegistry`` (the production entry point) is
    immediately visible to ``strategy.classify_quiet`` through the
    per-transport ``SubagentPidSource`` adapter. Without the
    wiring this test exercises, the production code never
    threads the registry from construction into the strategy
    layer.
    """
    agent_registry = AgentRegistry()
    registry, source = agent_registry.build_subagent_pid_registry(AgentTransport.CLAUDE)
    strategy = strategy_for_transport(
        AgentTransport.CLAUDE,
        subagent_pid_source=source,
    )
    handle = _FakeHandle(has_descendants=False)
    probe = FakeLivenessProbe(active=False)

    # Initial state: no registered PIDs -> ACTIVE.
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.ACTIVE

    # Register a real subagent PID for the Claude transport.
    registry.register(99001, source="claude", now=0.0)
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.WAITING_ON_CHILD

    # Unregister the PID -> back to ACTIVE.
    registry.unregister(99001)
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.ACTIVE


def test_catalog_default_seeded_transports_have_subagent_pid_registry_factory() -> None:
    """Every default-catalog seeded transport has a subagent pid registry factory.

    The default catalog is the production seeding surface; the helper
    method on ``AgentRegistry`` MUST be able to build a registry for
    every transport seeded there. This guards against a future PR that
    adds a new transport to the default catalog but forgets to wire
    the matching ``make_*_subagent_pid_source`` factory.
    """
    agent_registry = AgentRegistry()
    catalog = default_catalog()
    seeded_transports = {
        support.spec.transport
        for support in catalog._entries.values()
        if hasattr(support.spec, "transport")
    }
    for transport in seeded_transports:
        registry, source = agent_registry.build_subagent_pid_registry(transport)
        assert isinstance(registry, SubagentPidRegistry)
        assert isinstance(source, SubagentPidSource)
