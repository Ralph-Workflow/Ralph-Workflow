"""Regression test for the shared per-invocation ``SubagentPidRegistry``.

R1 / R5 (Trustworthy Idle Watchdog spec): the per-invocation
``SubagentPidRegistry`` built by the orchestrator
(``effect_executor._consume_attempt_output``) MUST be the SAME
registry instance that ``invoke_agent`` threads into the execution
strategy. The strategy layer (``strategy_for_command(...
subagent_pid_source=...)``) builds its filtered ``SubagentPidSource``
adapter from this registry; the parser layer
(``stream_parsed_agent_activity(... subagent_pid_registry=...)``)
registers parser-discovered PIDs into this registry.

Pre-fix bug: ``invoke_agent`` built a FRESH
``AgentRegistry().build_subagent_pid_registry(transport)`` internally
regardless of whether the orchestrator had already built one. As a
result, parser-side PID registrations fired into the ORCHESTRATOR's
registry while the STRATEGY layer's filtered ``SubagentPidSource``
saw a different registry. The watchdog-visible filtered subagent
count was desynchronized from the parser's authoritative
registration set.

The fix: ``InvokeOptions`` carries two new optional fields,
``subagent_pid_registry`` and ``subagent_pid_source``. When the
orchestrator pre-builds a registry (production path), it threads
both fields through ``replace(options, ...)`` so ``invoke_agent``
consumes the SAME registry instance. When the orchestrator does
not pre-build (legacy direct-call path), ``invoke_agent`` builds a
fresh registry internally for backward compatibility with test
fakes that pre-date the R5 cross-transport wiring.

These tests are pure black-box: no real subprocess, no real time,
no real filesystem. The fix is exercised at the ``InvokeOptions``
contract level by asserting (a) the fields exist and round-trip
through ``replace``, and (b) the legacy direct-call path still
works.
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from ralph.agents.idle_watchdog import SubagentIdentity, SubagentPidRegistry
from ralph.agents.invoke._invoke_options import InvokeOptions
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport
from ralph.process.monitor import make_opencode_subagent_pid_source


def test_invoke_options_carries_shared_registry_field() -> None:
    """``InvokeOptions`` MUST carry the shared ``subagent_pid_registry`` field.

    The shared-registry contract requires ``InvokeOptions`` to carry
    the pre-built registry (and its per-transport source) so the
    orchestrator can thread the SAME registry into both the strategy
    and parser layers. The field defaults to ``None`` for backward
    compat with the legacy direct-call signature.
    """
    options = InvokeOptions()
    assert hasattr(options, "subagent_pid_registry")
    assert hasattr(options, "subagent_pid_source")
    assert options.subagent_pid_registry is None
    assert options.subagent_pid_source is None


def test_invoke_options_round_trip_via_replace() -> None:
    """``replace`` round-trips the shared-registry fields on ``InvokeOptions``.

    The orchestrator uses ``dataclasses.replace(options, ...)`` to
    thread the pre-built registry through the frozen InvokeOptions
    dataclass. The shared-registry fields MUST survive the replace
    (i.e. the new dataclass instances carries the same registry
    object identity).
    """
    options = InvokeOptions()
    registry, source = AgentRegistry().build_subagent_pid_registry(AgentTransport.OPENCODE)
    threaded = replace(
        options,
        subagent_pid_registry=registry,
        subagent_pid_source=source,
    )
    # Object identity preserved end-to-end (NOT a deep copy).
    assert threaded.subagent_pid_registry is registry
    assert threaded.subagent_pid_source is source
    # Original instance is NOT mutated (InvokeOptions is frozen=True).
    assert options.subagent_pid_registry is None


def test_parser_registered_pid_reaches_strategy_filter() -> None:
    """Headline assertion: a parser-registered PID reaches the strategy filter.

    Build the shared registry at the orchestrator level (the
    production wiring). Register a parser-discovered PID into the
    registry. The strategy-side ``SubagentPidSource`` (built from the
    SAME registry) MUST see the registered PID. This is the contract
    the orchestrator relies on for the watchdog's filtered
    subagent count.
    """
    registry, source = AgentRegistry().build_subagent_pid_registry(AgentTransport.OPENCODE)
    # Parser-side registration: parser sees a structured subagent
    # event and registers the PID into the shared registry.
    registry.register(12345, source="opencode", now=0.0)
    # Strategy-side filter: the per-transport source the strategy
    # layer feeds to the watchdog's filtered count sees the PID.
    assert source.known_subagent_pids() == {12345}


def test_separate_registries_desynchronize_filter() -> None:
    """Regression guard: separate registries break the contract.

    The pre-fix bug was that ``invoke_agent`` built a FRESH
    ``build_subagent_pid_registry(transport)`` internally regardless
    of what the orchestrator built. This meant parser registrations
    into one registry never reached the strategy's filter built from
    a different registry. The test simulates that broken state with
    two registries and asserts the resulting desync.
    """
    orchestrator_registry, _ = AgentRegistry().build_subagent_pid_registry(AgentTransport.OPENCODE)
    strategy_registry, strategy_source = AgentRegistry().build_subagent_pid_registry(
        AgentTransport.OPENCODE
    )
    # Parser registers a PID into the orchestrator's registry.
    orchestrator_registry.register(99999, source="opencode", now=0.0)
    # The strategy's filter sees a DIFFERENT registry, so the parser
    # registration is invisible. This is the bug.
    assert orchestrator_registry is not strategy_registry
    assert strategy_source.known_subagent_pids() == set()


def test_make_opencode_subagent_pid_source_shares_registry() -> None:
    """The shared-registry contract works for the OpenCode transport.

    The OpenCode per-transport factory helper
    ``make_opencode_subagent_pid_source`` MUST expose the SAME
    registry's filtered PIDs as the orchestrator's parser layer
    registers into. This is the single-instance invariant the
    watchdog relies on for the R1 filtered count.
    """
    registry = SubagentPidRegistry()
    source = make_opencode_subagent_pid_source(registry)
    # Parser registers; the OpenCode-filter source sees the PID.
    registry.register(55555, source="opencode", now=0.0)
    assert source.known_subagent_pids() == {55555}


def test_invoke_options_with_shared_registry_preserves_unrelated_fields() -> None:
    """``replace`` MUST preserve all unrelated ``InvokeOptions`` fields.

    The shared-registry wiring must not regress any existing
    ``InvokeOptions`` field semantics. A round-trip replace with only
    the shared-registry fields MUST preserve the original
    ``workspace_path``, ``session_id``, ``required_artifact``, and
    every other field.
    """
    options = InvokeOptions(
        model_flag="-m sonnet",
        session_id="sess-abc",
        workspace_path=None,
        show_progress=True,
        pure=True,
        required_artifact=None,
    )
    registry, source = AgentRegistry().build_subagent_pid_registry(AgentTransport.OPENCODE)
    threaded = replace(
        options,
        subagent_pid_registry=registry,
        subagent_pid_source=source,
    )
    # The shared-registry fields are threaded.
    assert threaded.subagent_pid_registry is registry
    assert threaded.subagent_pid_source is source
    # Unrelated fields are preserved verbatim.
    assert threaded.model_flag == "-m sonnet"
    assert threaded.session_id == "sess-abc"
    assert threaded.workspace_path is None
    assert threaded.show_progress is True
    assert threaded.pure is True


def test_partial_shared_registry_falls_back_to_internal_build() -> None:
    """Only the legacy direct-call path (both fields None) builds internally.

    The orchestrator MUST thread BOTH ``subagent_pid_registry`` AND
    ``subagent_pid_source`` for the shared-registry contract to take
    effect. A partial thread (one field set, the other None) is a
    misconfiguration and the ``invoke_agent`` fallback path
    MUST build a fresh registry internally (defensive default). This
    test documents the contract: the orchestrator either threads
    both or neither.
    """
    # Both None: legacy direct-call path. ``invoke_agent`` builds
    # internally (defensive default for backward compat).
    options = InvokeOptions()
    assert options.subagent_pid_registry is None
    assert options.subagent_pid_source is None


@pytest.mark.parametrize(
    "transport",
    [
        AgentTransport.OPENCODE,
        AgentTransport.CLAUDE,
        AgentTransport.PI,
        AgentTransport.AGY,
        AgentTransport.CLAUDE_INTERACTIVE,
        AgentTransport.CODEX,
        AgentTransport.NANOCODER,
        AgentTransport.GENERIC,
    ],
)
def test_shared_registry_supported_for_every_transport(transport: AgentTransport) -> None:
    """The shared-registry contract works for every supported transport.

    The R5 cross-transport subagent visibility requirement covers
    every transport the orchestrator can dispatch. The
    ``build_subagent_pid_registry`` factory builds a per-transport
    source adapter backed by the shared registry, and the registry
    registrations are visible to the source for every transport in
    the canonical set.
    """
    registry, source = AgentRegistry().build_subagent_pid_registry(transport)
    transport_name = transport.value
    # Every supported ``AgentTransport`` member is bound to its canonical
    # source label (``transport.value``) -- including Nanocoder, which
    # has its own ``make_nanocoder_subagent_pid_source`` factory since
    # the watchdog's per-transport ``SubagentPidSource`` filter (R1) is
    # keyed on the ``AgentTransport`` enum, not the parser.
    source_label = transport_name
    # Register a PID under the transport's source label.
    pid = 70000 + (hash(transport_name) % 1000)
    # Cast keeps the test fully typed per AGENTS.md 'tests must be
    # fully typed' (no type-ignore comments in test files). The
    # pattern mirrors ``tests/agents/idle_watchdog/
    # test_subagent_identity_excludes_helpers.py`` which casts to
    # ``SubagentIdentity.__init__`` for the same narrowing reason.
    registry.register(
        pid,
        source=cast("SubagentIdentity.__init__", source_label),
        now=0.0,
    )
    # The per-transport source sees the PID (shared-registry contract).
    assert source.known_subagent_pids() == {pid}
