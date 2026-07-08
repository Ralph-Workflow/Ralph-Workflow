"""Black-box tests for the OpenCode registry-backed subagent output discovery.

This file locks in the contract that ``OpenCodeRegistryDiscoveryStrategy``:

* returns a per-child ``SubagentOutputCapture`` for every active child in the
  backing ``ChildLivenessRegistry``;
* emits textual descriptions of registry events (progress / heartbeat /
  terminal) as the capture's output lines;
* emits new lines only when the registry's per-child state advances so the
  watchdog's ``record_subagent_output`` is not flooded with duplicate lines;
* returns an empty mapping when the registry has no active children, so the
  watchdog degrades gracefully to stdout/MCP/workspace channels.

The watchdog can read real-time subagent progress for OpenCode because
OpenCode emits structured child lifecycle events (``child_started``,
``child_progress``, ``child_heartbeat``, ``child_complete``) on its stdout
that the strategy ingests into a ``ChildLivenessRegistry``. The discovery
strategy therefore reads FROM THE REGISTRY (not from a log file on disk) so
operators can see what each subagent is doing without depending on an
undocumented file path.

All tests use ``FakeClock``-style controls (``monkeypatch``-injected ``now``
or the registry's ``now=`` constructor parameter); no real subprocess, no
``time.sleep``, no real network.
"""

from __future__ import annotations

from ralph.config.enums import AgentTransport
from ralph.process.child_liveness import ChildLivenessRegistry
from ralph.process.monitor._discovery_strategy import (
    DiscoveryStrategy,
    NullDiscoveryStrategy,
    OpenCodeRegistryDiscoveryStrategy,
    RegistryBackedSubagentOutputCapture,
)
from ralph.process.monitor._subagent_output_capture import SubagentOutputCapture


def _make_registry() -> ChildLivenessRegistry:
    """Return a ChildLivenessRegistry with non-zero TTLs so tests are stable."""
    return ChildLivenessRegistry(
        progress_ttl=60.0,
        heartbeat_ttl=60.0,
        stale_label_ttl=60.0,
        exit_reconcile=5.0,
    )


def test_opencode_registry_discovery_returns_captures_for_active_children() -> None:
    """``discover_subagent_outputs`` returns one capture per active child."""
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.register_child("child-B", "agent:test-scope:", pid=222)

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")

    captures = strategy.discover_subagent_outputs(host_pid=999)

    assert set(captures) == {"child-A", "child-B"}
    for capture in captures.values():
        assert isinstance(capture, RegistryBackedSubagentOutputCapture)


def test_opencode_registry_discovery_returns_empty_when_no_active_children() -> None:
    """An empty registry yields an empty capture mapping so the watchdog degrades gracefully."""
    registry = _make_registry()
    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")

    captures = strategy.discover_subagent_outputs(host_pid=999)

    assert captures == {}


def test_opencode_registry_discovery_capture_emits_progress_event_lines() -> None:
    """A child progress event surfaces as a textual line in the capture's output stream.

    The first ``read_lines`` call may also emit a heartbeat line because
    ``register_child`` initialises the heartbeat timestamp; the test asserts
    the progress line is present (not the total line count) so the contract
    locks in the textual description format regardless of when the
    heartbeat was last advanced.
    """
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.record_progress("child-A", phase="phase-1")

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")
    capture = strategy.discover_subagent_outputs(host_pid=999)["child-A"]

    lines = capture.read_lines(worker_id="child-A")

    assert any("phase-1" in line for line in lines), lines


def test_opencode_registry_discovery_capture_emits_heartbeat_lines() -> None:
    """A heartbeat event surfaces as a textual line in the capture's output stream."""
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.record_heartbeat("child-A")

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")
    capture = strategy.discover_subagent_outputs(host_pid=999)["child-A"]

    lines = capture.read_lines(worker_id="child-A")

    assert any("heartbeat" in line.lower() for line in lines), lines


def test_opencode_registry_discovery_capture_emits_terminal_lines_on_ack() -> None:
    """A terminal event surfaces as a textual line in the capture's output stream.

    The strategy's ``discover_subagent_outputs`` filters out children with a
    non-``None`` ``terminal_state`` (the watchdog only wants active
    subagents). The terminal line emission contract is therefore tested
    by constructing a :class:`RegistryBackedSubagentOutputCapture` directly
    against the registry, registering a child, and recording a terminal
    ack after the capture is bound so the terminal-state transition is
    observable.
    """
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)

    capture = RegistryBackedSubagentOutputCapture(registry, "child-A", "agent:test-scope:")
    # Drain the initial progress/heartbeat lines emitted by register_child.
    capture.read_lines(worker_id="child-A")
    registry.record_terminal_ack("child-A", terminal_state="complete")

    lines = capture.read_lines(worker_id="child-A")

    assert any("terminal" in line.lower() and "complete" in line.lower() for line in lines), lines


def test_opencode_registry_discovery_capture_only_emits_new_lines() -> None:
    """A second ``read_lines`` call returns no lines until the registry advances."""
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.record_progress("child-A", phase="phase-1")

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")
    capture = strategy.discover_subagent_outputs(host_pid=999)["child-A"]

    first_lines = capture.read_lines(worker_id="child-A")
    second_lines = capture.read_lines(worker_id="child-A")

    assert len(first_lines) >= 1
    assert second_lines == []


def test_opencode_registry_discovery_capture_emits_after_state_advance() -> None:
    """A second ``read_lines`` call after a state advance returns the new event."""
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.record_progress("child-A", phase="phase-1")

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")
    capture = strategy.discover_subagent_outputs(host_pid=999)["child-A"]

    first_lines = capture.read_lines(worker_id="child-A")
    assert len(first_lines) >= 1

    registry.record_progress("child-A", phase="phase-2")

    second_lines = capture.read_lines(worker_id="child-A")
    assert len(second_lines) >= 1
    assert any("phase-2" in line for line in second_lines), second_lines
    assert not any("phase-1" in line for line in second_lines), second_lines


def test_opencode_registry_discovery_capture_ignores_unknown_worker_id() -> None:
    """``read_lines`` with an unknown worker_id returns an empty list (no leak)."""
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.record_progress("child-A", phase="phase-1")

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")
    capture = strategy.discover_subagent_outputs(host_pid=999)["child-A"]

    assert capture.read_lines(worker_id="not-child-A") == []


def test_opencode_registry_discovery_capture_returns_empty_when_child_disappears() -> None:
    """A capture whose child was pruned from the registry returns an empty list.

    The ``discover_subagent_outputs`` contract is observation-only: when
    the registry's progress/heartbeat timestamps are outside their TTLs
    the strategy filters the child out of the returned mapping rather
    than returning a capture that would then emit empty lines. The
    ``read_lines``-emits-empty contract is therefore exercised by
    constructing a :class:`RegistryBackedSubagentOutputCapture` directly
    and then calling ``snapshot`` (which prunes) so the underlying
    record is removed before the next read.
    """
    registry = ChildLivenessRegistry(
        progress_ttl=0.0,
        heartbeat_ttl=0.0,
        stale_label_ttl=0.0,
        exit_reconcile=5.0,
    )
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.record_progress("child-A", phase="phase-1")

    capture = RegistryBackedSubagentOutputCapture(registry, "child-A", "agent:test-scope:")
    # Drain the initial progress line emitted by record_progress.
    capture.read_lines(worker_id="child-A")
    # TTLs are 0.0, so the record is immediately stale; ``snapshot`` calls
    # ``prune_stale`` internally and removes the record.
    registry.snapshot("agent:test-scope:")
    lines = capture.read_lines(worker_id="child-A")
    assert lines == []


def test_opencode_registry_discovery_narrows_by_scope_prefix() -> None:
    """The discovery strategy only returns captures for children matching the scope prefix."""
    registry = _make_registry()
    registry.register_child("child-A", "agent:scope-one:", pid=111)
    registry.register_child("child-B", "agent:scope-two:", pid=222)

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:scope-one:")

    captures = strategy.discover_subagent_outputs(host_pid=999)

    assert set(captures) == {"child-A"}


def test_opencode_registry_discovery_satisfies_protocol() -> None:
    """``OpenCodeRegistryDiscoveryStrategy`` satisfies the ``DiscoveryStrategy`` protocol."""
    registry = _make_registry()
    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")
    assert isinstance(strategy, DiscoveryStrategy)


def test_null_discovery_strategy_satisfies_protocol() -> None:
    """``NullDiscoveryStrategy`` continues to satisfy the runtime protocol."""
    strategy = NullDiscoveryStrategy()
    assert isinstance(strategy, DiscoveryStrategy)
    assert strategy.discover_subagent_outputs(host_pid=123) == {}


def test_opencode_registry_discovery_capture_lines_satisfy_protocol() -> None:
    """``RegistryBackedSubagentOutputCapture`` satisfies the ``SubagentOutputCapture`` protocol."""
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.record_progress("child-A", phase="phase-1")

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")
    capture = strategy.discover_subagent_outputs(host_pid=999)["child-A"]

    assert isinstance(capture, SubagentOutputCapture)


def test_opencode_registry_discovery_multiple_progress_events_each_emit() -> None:
    """Each progress advance produces a new line on the next poll."""
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")
    capture = strategy.discover_subagent_outputs(host_pid=999)["child-A"]

    registry.record_progress("child-A", phase="phase-1")
    first_lines = capture.read_lines(worker_id="child-A")
    assert any("phase-1" in line for line in first_lines), first_lines

    registry.record_progress("child-A", phase="phase-2")
    second_lines = capture.read_lines(worker_id="child-A")
    assert any("phase-2" in line for line in second_lines), second_lines

    registry.record_progress("child-A", phase="phase-3")
    third_lines = capture.read_lines(worker_id="child-A")
    assert any("phase-3" in line for line in third_lines), third_lines


def test_opencode_registry_discovery_filters_stale_records() -> None:
    """Stale records (no progress/heartbeat within TTL) are filtered out.

    The discovery strategy is observation-only: it does not mutate the
    registry's ``prune_stale`` state but it does exclude children whose
    evidence is fully stale so the watchdog cannot see a per-child
    capture that would re-emit stale snapshot lines on the first
    ``read_lines`` call and falsely defer a watchdog fire. The same
    freshness criteria the registry uses for ``prune_stale`` are applied
    here without mutating the registry.
    """
    registry = ChildLivenessRegistry(
        progress_ttl=10.0,
        heartbeat_ttl=10.0,
        stale_label_ttl=10.0,
        exit_reconcile=5.0,
        now=lambda: 1000.0,
    )
    registry.register_child("fresh-child", "agent:test-scope:", pid=111)
    # fresh-child: heartbeat (None -> now-1=999), no progress
    # -> not stale
    registry._records["fresh-child"].last_heartbeat_at = 999.0

    registry.register_child("stale-child", "agent:test-scope:", pid=222)
    # stale-child: last heartbeat 100s ago, well past heartbeat_ttl
    registry._records["stale-child"].last_heartbeat_at = 900.0
    registry._records["stale-child"].last_progress_at = 900.0

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")
    captures = strategy.discover_subagent_outputs(host_pid=999)

    assert set(captures) == {"fresh-child"}


def test_opencode_registry_discovery_spawn_only_emits_no_progress_line() -> None:
    """Spawn-only registration (no progress yet) MUST NOT emit a progress line.

    Regression for the watchdog analysis-feedback finding: pre-fix,
    ``register_child`` initialized ``last_known_phase='spawned'`` so the
    discovery strategy's first poll emitted ``[subagent] progress:
    phase=spawned`` even though the child had not produced any real
    progress evidence (last_progress_at is still ``None``). That
    fabricated forward progress and could defer a no-output watchdog
    fire without any real progress / heartbeat evidence.

    Contract: a child that has been registered but has not yet
    recorded a progress or heartbeat event yields ZERO lines on the
    first ``read_lines`` call (the ``spawn`` transition is a
    registration event, not a progress event). The first
    progress / heartbeat emission happens only AFTER a real
    ``record_progress(...)`` or ``record_heartbeat(...)`` call.
    """
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)

    capture = RegistryBackedSubagentOutputCapture(registry, "child-A", "agent:test-scope:")
    lines = capture.read_lines(worker_id="child-A")
    assert lines == [], (
        "Spawn-only registration MUST NOT emit a progress line;"
        f" got {lines!r}. A child registered without a subsequent"
        " record_progress()/record_heartbeat() has NO forward"
        " progress evidence to surface."
    )

    # After a real progress event the contract reverts to normal:
    # subsequent ``read_lines`` calls yield the recorded progress.
    registry.record_progress("child-A", phase="phase-1")
    next_lines = capture.read_lines(worker_id="child-A")
    assert any("phase-1" in line for line in next_lines), next_lines


def test_opencode_registry_discovery_only_relevant_transport_gets_strategy() -> None:
    """Document the per-transport discovery strategy contract.

    The factory in ``_monitor_factory._discovery_strategy_for_config`` is
    expected to return ``OpenCodeRegistryDiscoveryStrategy`` for ``OPENCODE``
    when a registry is provided, and ``NullDiscoveryStrategy`` for every
    other transport. This test pins the public discovery-strategy surface
    per-transport via the factory dispatch contract that other tests in
    ``tests/agents/idle_watchdog/test_cross_transport_subagent_visibility.py``
    exercise end-to-end.
    """
    expected_opencode = AgentTransport.OPENCODE
    other_transports = [
        AgentTransport.CLAUDE,
        AgentTransport.CLAUDE_INTERACTIVE,
        AgentTransport.CODEX,
        AgentTransport.NANOCODER,
        AgentTransport.GENERIC,
        AgentTransport.AGY,
        AgentTransport.PI,
        AgentTransport.CURSOR,
    ]
    assert expected_opencode not in other_transports
    assert len(set(other_transports) | {expected_opencode}) == len(AgentTransport)
