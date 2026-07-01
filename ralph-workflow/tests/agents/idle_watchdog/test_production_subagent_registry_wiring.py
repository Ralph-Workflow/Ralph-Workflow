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

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents import invoke as ralph_invoke
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
    get_parser,
)
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.process.child_liveness import ChildLivenessRegistry
from ralph.process.liveness import FakeLivenessProbe
from ralph.process.monitor import SubagentPidSource

if TYPE_CHECKING:
    from collections.abc import Iterator

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
    # visible via the per-transport filtered source. Every supported
    # ``AgentTransport`` member is bound to its canonical source label
    # (``transport.value``) -- including Nanocoder, which has its own
    # ``make_nanocoder_subagent_pid_source`` factory since the
    # watchdog's per-transport ``SubagentPidSource`` filter (R1) is
    # keyed on the ``AgentTransport`` enum, not the parser.
    registry.register(12345, source=transport.value, now=0.0)
    assert 12345 in source.known_subagent_pids()
    # A PID registered for a DIFFERENT transport is invisible (R1
    # isolation between per-transport filtered views).
    other_transport = (
        AgentTransport.CLAUDE if transport != AgentTransport.CLAUDE else AgentTransport.PI
    )
    other_registry, other_source = agent_registry.build_subagent_pid_registry(other_transport)
    other_registry.register(67890, source=other_transport.value, now=0.0)
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

    The parametrize list is keyed on the eight supported
    ``AgentTransport`` enum members that have a corresponding parser
    class (every transport except ``OPENCODE`` -- OpenCode's parser is
    constructed with the production ``parser_factory`` call, not via
    the bare constructor, so it has its own dedicated wiring test).
    ``gemini`` has its own dedicated regression test below
    (``test_gemini_parser_registers_pid_from_child_progress``) because
    the public factory path uses a parser-bound source label distinct
    from its ``AgentTransport`` (``GENERIC``); the regression test
    pins the explicit behavior so a future PR cannot silently drop
    ``gemini``-labeled registrations the way the prior bare
    ``except Exception`` pattern did.
    """
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
    parser = GenericParser()
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


# ---------------------------------------------------------------------------
# Parser registration hook (R5 production wiring)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("transport_label", "parser_cls"),
    [
        ("claude", ClaudeParser),
        ("codex", CodexParser),
        ("pi", PiParser),
        ("agy", AgyParser),
        ("generic", GenericParser),
    ],
)
def test_parser_registers_pid_from_structured_event_when_registry_wired(
    transport_label: str,
    parser_cls: type[AgentParser],
) -> None:
    """Structured event carrying a PID MUST register it into the shared registry.

    The R5 production wiring requires the parser's ``_dispatch_json_object``
    path to call the registry registration hook for every observed
    structured event. When an event carries an embedded PID, the parser
    registers it via ``SubagentPidRegistry.register``. When the event
    has no PID, the hook is a no-op (and the registry stays empty).

    The parametrize list is the subset of parser keys that are
    also supported ``AgentTransport`` members. ``gemini`` is
    covered by the dedicated regression test
    ``test_gemini_parser_registers_pid_from_child_progress`` below
    (the public factory path uses the parser-bound ``"gemini"``
    source label even though the catalog maps Gemini to the
    ``GENERIC`` transport, so it sits outside the
    ``AgentTransport``-keyed parametrizations).
    """
    registry = SubagentPidRegistry()
    parser = parser_cls(
        subagent_pid_registry=registry,
        subagent_source_label=transport_label,
    )
    # Pre-condition: empty registry.
    assert len(registry) == 0

    # Drive an event that carries a PID at the top level.
    pid = 55555
    line_with_pid = '{"type": "child_progress", "pid": ' + str(pid) + ', "content": "x"}'
    events = list(parser.parse(iter([line_with_pid])))
    # Parser still emits the same typed event (registry is a side-effect hook).
    assert events, "parser MUST still emit an event for child_progress line"
    assert pid in registry.known_pids()
    identity = next(iter(registry.snapshot()))
    assert identity.source == transport_label

    # Drive an event with no PID -> no-op for the registry.
    line_without_pid = '{"type": "text", "content": "hello"}'
    events2 = list(parser.parse(iter([line_without_pid])))
    assert events2
    # No new PIDs registered.
    assert len(registry) == 1


def test_parser_registration_hook_no_op_when_registry_none() -> None:
    """The registration hook is a no-op when no registry was provided.

    The legacy zero-arg ``parser_factory()`` call MUST continue to work
    without raising on PID-less events or PID-carrying events. The
    hook silently skips when ``_subagent_pid_registry`` is ``None``.
    """
    parser = CodexParser()  # zero-arg legacy call
    line = '{"type": "child_progress", "pid": 99999}'
    events = list(parser.parse(iter([line])))
    assert events  # parser still emits
    assert getattr(parser, "_subagent_pid_registry", None) is None


def test_parser_registration_hook_no_op_when_source_label_none() -> None:
    """The registration hook is a no-op when no source label was provided.

    A parser constructed with a registry but no source label (e.g. via
    a legacy caller that passes only the registry kwarg) MUST NOT
    register PIDs -- the source label is what attributes a PID to the
    right transport for the per-transport ``SubagentPidSource`` filter.
    Without the label the registration could leak cross-transport.
    """
    registry = SubagentPidRegistry()
    parser = CodexParser(subagent_pid_registry=registry)
    assert parser._subagent_source_label is None
    line = '{"type": "child_progress", "pid": 12345}'
    list(parser.parse(iter([line])))
    # No registration happened because no source label was provided.
    assert 12345 not in registry.known_pids()


# ---------------------------------------------------------------------------
# get_parser production wiring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("parser_key", "transport_label"),
    [
        ("claude", "claude"),
        ("codex", "codex"),
        ("pi", "pi"),
        ("agy", "agy"),
        ("generic", "generic"),
    ],
)
def test_get_parser_threads_registry_and_source_label(
    parser_key: str,
    transport_label: str,
) -> None:
    """``get_parser(parser_key, subagent_pid_registry=..., subagent_source_label=...)`` wires both.

    The previous pass silently instantiated parsers as ``parser_cls()``
    with no registry. The fix: ``get_parser`` MUST accept and forward
    the registry + source label kwargs so the parser's registration
    hook fires for PID-carrying events.

    The parametrize list is the subset of parser keys that are
    also supported ``AgentTransport`` members. ``gemini`` is
    covered by the dedicated regression test
    ``test_get_parser_gemini_threads_registry_and_source_label`` below
    (the public factory path uses the parser-bound ``"gemini"``
    source label even though the catalog maps Gemini to the
    ``GENERIC`` transport, so it sits outside the
    ``AgentTransport``-keyed parametrizations).
    """
    registry = SubagentPidRegistry()
    parser = get_parser(
        parser_key,
        subagent_pid_registry=registry,
        subagent_source_label=transport_label,
    )
    assert parser._subagent_pid_registry is registry
    assert parser._subagent_source_label == transport_label


def test_get_parser_default_kwargs_keep_registry_none() -> None:
    """Legacy zero-arg ``get_parser(parser_key)`` MUST keep the registry and source label ``None``.

    The fix MUST NOT regress the legacy ``get_parser('claude')``
    zero-arg call used by the smoke and commit plumbing.
    """
    parser = get_parser("claude")
    assert getattr(parser, "_subagent_pid_registry", None) is None
    assert getattr(parser, "_subagent_source_label", None) is None


# ---------------------------------------------------------------------------
# Production invocation flow wires the SubagentPidSource into the strategy
# ---------------------------------------------------------------------------


def test_invoke_agent_threads_subagent_pid_source_into_strategy_for_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``invoke_agent`` MUST thread ``subagent_pid_source=`` into ``strategy_for_command``.

    This is the integration test for the production wiring path the
    analysis flagged as missing: ``invoke_agent`` constructs a
    per-invocation shared ``SubagentPidRegistry`` +
    ``SubagentPidSource`` from ``AgentRegistry.build_subagent_pid_registry``
    and threads the source into ``strategy_for_command(...)`` so
    ``BaseExecutionStrategy.classify_quiet`` uses the FILTERED signal.

    The test inspects the ``strategy_for_command`` call site directly
    via monkeypatch so no real subprocess is launched and no wall-clock
    sleep is required. The argument-recording monkeypatch captures the
    kwargs passed in and the test asserts ``subagent_pid_source`` is a
    non-``None`` ``SubagentPidSource`` instance -- proving the wiring
    is live end-to-end.

    The subprocess/PTY runners are ALSO monkeypatched to a no-op
    generator so the post-strategy-for_command code path (which would
    otherwise spawn the real ``claude -p`` binary and wait for it to
    exit non-zero because no login session is available) does not
    contribute to wall-clock cost. The test's contract is the wiring
    UP TO and INCLUDING the ``strategy_for_command`` call; the
    subprocess execution path is covered by the dedicated
    ``tests/test_subprocess_agent_executor*.py`` tests under the
    ``subprocess_e2e`` marker.
    """
    captured: dict[str, object] = {}

    def _spy(*args: object, **kwargs: object) -> BaseExecutionStrategy:
        captured.update(kwargs)
        return BaseExecutionStrategy(
            label_scope=cast("str | None", kwargs.get("label_scope")),
            registry=cast("ChildLivenessRegistry | None", kwargs.get("registry")),
            subagent_pid_source=cast(
                "SubagentPidSource | None", kwargs.get("subagent_pid_source")
            ),
        )

    def _empty_generator(*_args: object, **_kwargs: object) -> Iterator[str]:
        # The subprocess/PTY runners are typed as ``Iterator[str]``
        # generators; an empty generator returns immediately so the
        # post-strategy_for_command code path executes in microseconds
        # rather than spawning ``claude -p`` and waiting for the
        # login-required exit. ``if False`` keeps this a generator
        # function under mypy and ruff.
        if False:
            yield ""

    # ``strategy_for_command`` is imported into ``invoke`` at module
    # load via ``from ralph.agents.execution_state import strategy_for_command``,
    # so the canonical patch target is the ``invoke`` module's own
    # reference (NOT the source module -- rebinding the source has no
    # effect on the already-imported name). The pytest ``monkeypatch``
    # fixture handles the cleanup automatically on teardown so this
    # test file remains free of any suppression markers.
    monkeypatch.setattr(ralph_invoke, "strategy_for_command", _spy)
    # Block the real subprocess/PTY execution so the test verifies the
    # wiring contract in <1ms rather than waiting for ``claude -p`` to
    # fail with a login-required exit. The ``invoke`` module imports
    # both runners at module load (``from ralph.agents.invoke._pty ...
    # import run_pty_and_read_lines`` etc.) so the canonical patch
    # target is the ``invoke`` module's own reference, mirroring the
    # ``strategy_for_command`` patch above.
    monkeypatch.setattr(
        ralph_invoke, "run_subprocess_and_read_lines", _empty_generator
    )
    monkeypatch.setattr(ralph_invoke, "run_pty_and_read_lines", _empty_generator)

    # Build a minimal AgentConfig and InvokeOptions so the
    # ``invoke_agent`` flow reaches the ``strategy_for_command``
    # call site. The test only inspects the captured kwargs; any
    # downstream failure is acceptable (we monkeypatch the call).
    config = AgentConfig(
        cmd="claude -p",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )
    with contextlib.suppress(Exception):
        list(ralph_invoke.invoke_agent(config, "PROMPT.md"))

    assert "subagent_pid_source" in captured, (
        "invoke_agent MUST pass subagent_pid_source= into strategy_for_command"
    )
    source = captured["subagent_pid_source"]
    assert isinstance(source, SubagentPidSource), (
        f"subagent_pid_source must be a SubagentPidSource instance, got {type(source).__name__}"
    )


# ---------------------------------------------------------------------------
# Gemini parser R1 wiring (regression for the prior silent no-op)
# ---------------------------------------------------------------------------


def test_gemini_parser_registers_pid_from_child_progress() -> None:
    """``get_parser('gemini', subagent_pid_registry=..., subagent_source_label='gemini')``
    MUST register PID-carrying events into the shared registry.

    Regression for the prior silent no-op: the public Gemini factory
    path constructed a parser with ``subagent_source_label='gemini'``
    and parsed a PID-carrying ``child_progress`` event, but the
    underlying ``SubagentPidRegistry.register`` call raised
    ``ValueError`` because ``'gemini'`` was missing from the
    canonical ``_SUBAGENT_SOURCES`` set; the bare ``except Exception``
    clause in ``NdjsonParserBase._try_register_subagent_pid_from_obj``
    silently swallowed the rejection and the PID was never
    registered, losing the watchdog's R1 subagent signal.

    The fix:

      * ``'gemini'`` is added to the canonical ``_SUBAGENT_SOURCES``
        set in ``ralph/agents/idle_watchdog/_subagent_identity.py`` so
        ``SubagentPidRegistry.register`` accepts the parser-bound
        source label.
      * ``NdjsonParserBase._try_register_subagent_pid_from_obj``
        narrows the exception clause from ``except Exception`` to
        ``except ValueError`` so other exception types propagate
        instead of being silently dropped.

    This test pins BOTH invariants via the public ``get_parser``
    factory path:

      1. ``events`` is non-empty (parser still emits its typed event).
      2. ``registry.known_pids()`` contains the emitted PID.
      3. ``registry.snapshot()[0].source == 'gemini'`` (the parser
         source label is preserved through registration).

    The test uses no real subprocess, no real wall-clock sleep, and
    no real filesystem I/O -- it is a pure-Python black-box fixture
    that satisfies ``audit_test_policy``.
    """
    registry = SubagentPidRegistry()
    parser = get_parser(
        "gemini",
        subagent_pid_registry=registry,
        subagent_source_label="gemini",
    )
    pid = 424242
    line = '{"type": "child_progress", "pid": ' + str(pid) + ', "content": "x"}'
    events = list(parser.parse(iter([line])))

    assert events, "parser MUST still emit an event for child_progress line"
    assert pid in registry.known_pids(), (
        f"Gemini parser must register pid {pid} into the shared registry; "
        f"got known_pids={sorted(registry.known_pids())}"
    )
    identity = next(iter(registry.snapshot()))
    assert identity.source == "gemini", (
        f"identity.source must be 'gemini' (the parser-bound label), "
        f"got {identity.source!r}"
    )


def test_gemini_parser_registers_pid_via_direct_constructor() -> None:
    """Constructing ``GeminiParser(subagent_pid_registry=..., subagent_source_label='gemini')``
    directly also registers PIDs (the bare-constructor path mirrors the
    factory path).

    The bare-constructor path uses the same ``_try_register_subagent_pid_from_obj``
    hook as the factory path; this test pins the contract for callers
    that construct ``GeminiParser`` directly without going through
    ``get_parser``.
    """
    registry = SubagentPidRegistry()
    parser = GeminiParser(
        subagent_pid_registry=registry,
        subagent_source_label="gemini",
    )
    pid = 314159
    line = '{"type": "child_progress", "pid": ' + str(pid) + ', "content": "x"}'
    events = list(parser.parse(iter([line])))

    assert events
    assert pid in registry.known_pids()
    identity = next(iter(registry.snapshot()))
    assert identity.source == "gemini"


def test_gemini_parser_registration_no_op_when_registry_none() -> None:
    """The Gemini parser registration hook is a no-op when no registry is provided.

    Mirrors the existing per-parser no-op test for Codex / Claude / etc.
    so a future refactor that wires a default registry into Gemini by
    accident is caught.
    """
    parser = GeminiParser()  # zero-arg legacy call
    line = '{"type": "child_progress", "pid": 99999}'
    events = list(parser.parse(iter([line])))
    assert events  # parser still emits
    assert getattr(parser, "_subagent_pid_registry", None) is None


def test_gemini_parser_registration_no_op_when_source_label_none() -> None:
    """The Gemini parser registration hook is a no-op when no source label is provided.

    A parser constructed with a registry but no source label MUST NOT
    register PIDs -- the source label is what attributes a PID to the
    right transport for the per-transport ``SubagentPidSource`` filter.
    """
    registry = SubagentPidRegistry()
    parser = GeminiParser(subagent_pid_registry=registry)
    assert parser._subagent_source_label is None
    line = '{"type": "child_progress", "pid": 12345}'
    list(parser.parse(iter([line])))
    assert 12345 not in registry.known_pids()


def test_gemini_parser_propagates_non_value_error_registration_failures() -> None:
    """A non-``ValueError`` exception from ``SubagentPidRegistry.register`` MUST propagate.

    The prior ``except Exception`` clause silently dropped every
    registration failure; the fix narrows it to ``except ValueError``
    so programmer errors (``TypeError``, ``AttributeError``,
    ``RuntimeError``) surface to the caller. This test injects a
    registry stub that raises ``TypeError`` and asserts the parser's
    ``parse`` path propagates the error rather than swallowing it.
    """
    sentinel = RuntimeError("programmer-error sentinel from injected registry")

    class _RaisingRegistry:
        def register(
            self,
            pid: int,
            source: str,
            label_prefix: str | None = None,
            *,
            now: float | None = None,
        ) -> object:
            raise sentinel

    parser = GeminiParser(
        subagent_pid_registry=cast("SubagentPidRegistry", _RaisingRegistry()),
        subagent_source_label="gemini",
    )
    line = '{"type": "child_progress", "pid": 7777}'
    with pytest.raises(RuntimeError, match="programmer-error sentinel"):
        list(parser.parse(iter([line])))


def test_gemini_parser_swallows_value_error_registration_failures() -> None:
    """A ``ValueError`` from ``SubagentPidRegistry.register`` is still swallowed.

    The narrowing from ``except Exception`` to ``except ValueError``
    preserves the forward-compat safety net: the parser's primary
    event-emission path must continue to work even when the
    registry's validation rejects a registration (e.g. an unknown
    source label). The test injects a registry stub that raises
    ``ValueError`` and asserts ``parse`` returns the typed event
    WITHOUT re-raising.
    """
    class _ValueErrorRegistry:
        def register(
            self,
            pid: int,
            source: str,
            label_prefix: str | None = None,
            *,
            now: float | None = None,
        ) -> object:
            raise ValueError(f"unknown subagent source {source!r}")

    parser = GeminiParser(
        subagent_pid_registry=cast("SubagentPidRegistry", _ValueErrorRegistry()),
        subagent_source_label="gemini",
    )
    line = '{"type": "child_progress", "pid": 8888}'
    events = list(parser.parse(iter([line])))
    assert events, "parser MUST still emit an event when ValueError is raised"
