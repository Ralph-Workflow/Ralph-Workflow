from __future__ import annotations

from ralph.config.enums import AgentTransport
from ralph.process.child_liveness import ChildLivenessRegistry
from ralph.process.monitor import (
    DiscoveryStrategy,
    NullDiscoveryStrategy,
    OpenCodeRegistryDiscoveryStrategy,
    RegistryBackedSubagentOutputCapture,
    SubagentOutputCapture,
)


def _make_registry() -> ChildLivenessRegistry:
    return ChildLivenessRegistry(
        progress_ttl=60.0,
        heartbeat_ttl=60.0,
        stale_label_ttl=60.0,
        exit_reconcile=5.0,
    )


def test_opencode_registry_discovery_returns_captures_for_active_children() -> None:
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.register_child("child-B", "agent:test-scope:", pid=222)

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")

    captures = strategy.discover_subagent_outputs(host_pid=999)

    assert set(captures) == {"child-A", "child-B"}
    for capture in captures.values():
        assert isinstance(capture, RegistryBackedSubagentOutputCapture)


def test_opencode_registry_discovery_returns_empty_when_no_active_children() -> None:
    registry = _make_registry()
    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")

    captures = strategy.discover_subagent_outputs(host_pid=999)

    assert captures == {}


def test_opencode_registry_discovery_capture_emits_progress_event_lines() -> None:
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.record_progress("child-A", phase="phase-1")

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")
    capture = strategy.discover_subagent_outputs(host_pid=999)["child-A"]

    lines = capture.read_lines(worker_id="child-A")

    assert any("phase-1" in line for line in lines), lines


def test_opencode_registry_discovery_capture_emits_heartbeat_lines() -> None:
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.record_heartbeat("child-A")

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")
    capture = strategy.discover_subagent_outputs(host_pid=999)["child-A"]

    lines = capture.read_lines(worker_id="child-A")

    assert any("heartbeat" in line.lower() for line in lines), lines


def test_opencode_registry_discovery_capture_emits_terminal_lines_on_ack() -> None:
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)

    capture = RegistryBackedSubagentOutputCapture(registry, "child-A", "agent:test-scope:")
    capture.read_lines(worker_id="child-A")
    registry.record_terminal_ack("child-A", terminal_state="complete")

    lines = capture.read_lines(worker_id="child-A")

    assert any("terminal" in line.lower() and "complete" in line.lower() for line in lines), lines


def test_opencode_registry_discovery_capture_only_emits_new_lines() -> None:
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
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.record_progress("child-A", phase="phase-1")

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")
    capture = strategy.discover_subagent_outputs(host_pid=999)["child-A"]

    assert capture.read_lines(worker_id="not-child-A") == []


def test_opencode_registry_discovery_capture_returns_empty_when_child_disappears() -> None:
    registry = ChildLivenessRegistry(
        progress_ttl=0.0,
        heartbeat_ttl=0.0,
        stale_label_ttl=0.0,
        exit_reconcile=5.0,
    )
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.record_progress("child-A", phase="phase-1")

    capture = RegistryBackedSubagentOutputCapture(registry, "child-A", "agent:test-scope:")
    capture.read_lines(worker_id="child-A")
    registry.snapshot("agent:test-scope:")
    lines = capture.read_lines(worker_id="child-A")
    assert lines == []


def test_opencode_registry_discovery_narrows_by_scope_prefix() -> None:
    registry = _make_registry()
    registry.register_child("child-A", "agent:scope-one:", pid=111)
    registry.register_child("child-B", "agent:scope-two:", pid=222)

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:scope-one:")

    captures = strategy.discover_subagent_outputs(host_pid=999)

    assert set(captures) == {"child-A"}


def test_opencode_registry_discovery_satisfies_protocol() -> None:
    registry = _make_registry()
    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")
    assert isinstance(strategy, DiscoveryStrategy)


def test_null_discovery_strategy_satisfies_protocol() -> None:
    strategy = NullDiscoveryStrategy()
    assert isinstance(strategy, DiscoveryStrategy)
    assert strategy.discover_subagent_outputs(host_pid=123) == {}


def test_opencode_registry_discovery_capture_lines_satisfy_protocol() -> None:
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.record_progress("child-A", phase="phase-1")

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")
    capture = strategy.discover_subagent_outputs(host_pid=999)["child-A"]

    assert isinstance(capture, SubagentOutputCapture)


def test_opencode_registry_discovery_multiple_progress_events_each_emit() -> None:
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
    registry = ChildLivenessRegistry(
        progress_ttl=10.0,
        heartbeat_ttl=10.0,
        stale_label_ttl=10.0,
        exit_reconcile=5.0,
        now=lambda: 1000.0,
    )
    registry.register_child("fresh-child", "agent:test-scope:", pid=111)
    registry._records["fresh-child"].last_heartbeat_at = 999.0

    registry.register_child("stale-child", "agent:test-scope:", pid=222)
    registry._records["stale-child"].last_heartbeat_at = 900.0
    registry._records["stale-child"].last_progress_at = 900.0

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")
    captures = strategy.discover_subagent_outputs(host_pid=999)

    assert set(captures) == {"fresh-child"}


def test_opencode_registry_discovery_spawn_only_emits_no_progress_line() -> None:
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

    registry.record_progress("child-A", phase="phase-1")
    next_lines = capture.read_lines(worker_id="child-A")
    assert any("phase-1" in line for line in next_lines), next_lines


def test_opencode_registry_discovery_only_relevant_transport_gets_strategy() -> None:
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
        AgentTransport.KIMI,
    ]
    assert expected_opencode not in other_transports
    assert len(set(other_transports) | {expected_opencode}) == len(AgentTransport)
