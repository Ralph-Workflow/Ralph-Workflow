"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

import json

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from ralph.process.child_liveness import (
    ChildLivenessRegistry,
)
from ralph.process.liveness import DefaultLivenessProbe
from tests.fake_handle import _FakeHandle

# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).


class TestRegistryBackedClassifyExit:
    """classify_exit uses registry terminal_count to confirm all children exited."""

    def test_observe_line_routes_progress_event_to_registry(self) -> None:
        """A child_progress JSON line routed via observe_line updates registry progress."""

        t = [0.0]
        reg = ChildLivenessRegistry(
            progress_ttl=45.0,
            heartbeat_ttl=15.0,
            stale_label_ttl=10.0,
            exit_reconcile=5.0,
            now=lambda: t[0],
        )
        strategy = OpenCodeExecutionStrategy(label_scope="scope/a", registry=reg)
        # Register via spawn event so scope_prefix matches what observe_line uses.
        spawn_line = json.dumps({"type": "child_started", "child_id": "c1"})
        strategy.observe_line(spawn_line)
        progress_line = json.dumps(
            {"type": "child_progress", "child_id": "c1", "phase": "tool_call"}
        )
        strategy.observe_line(progress_line)

        probe = DefaultLivenessProbe(registry=reg)
        snap = probe.child_snapshot("agent:scope/a:")
        assert snap.has_fresh_progress is True

    def test_observe_line_routes_terminal_ack_to_registry(self) -> None:
        """A child_complete JSON line routes terminal ack into the registry."""

        t = [0.0]
        reg = ChildLivenessRegistry(
            progress_ttl=45.0,
            heartbeat_ttl=15.0,
            stale_label_ttl=10.0,
            exit_reconcile=5.0,
            now=lambda: t[0],
        )
        strategy = OpenCodeExecutionStrategy(label_scope="scope/a", registry=reg)
        spawn_line = json.dumps({"type": "child_started", "child_id": "c1"})
        strategy.observe_line(spawn_line)
        terminal_line = json.dumps(
            {"type": "child_complete", "child_id": "c1", "terminal_state": "complete"}
        )
        strategy.observe_line(terminal_line)

        probe = DefaultLivenessProbe(registry=reg)
        snap = probe.child_snapshot("agent:scope/a:")
        assert snap.terminal_count == 1
        assert snap.active_count == 0

    def test_classify_exit_regression_terminal_child_does_not_complete_parent(self) -> None:
        """S-5: terminal child evidence cannot replace the parent's durable sentinel."""

        t = [0.0]
        reg = ChildLivenessRegistry(
            progress_ttl=45.0,
            heartbeat_ttl=15.0,
            stale_label_ttl=10.0,
            exit_reconcile=5.0,
            now=lambda: t[0],
        )
        strategy = OpenCodeExecutionStrategy(label_scope="scope/a", registry=reg)
        spawn_line = json.dumps({"type": "child_started", "child_id": "c1"})
        strategy.observe_line(spawn_line)
        terminal_line = json.dumps({"type": "child_complete", "child_id": "c1"})
        strategy.observe_line(terminal_line)

        probe = DefaultLivenessProbe(registry=reg)
        handle = _FakeHandle(returncode=0, has_descendants=False)
        signals = CompletionSignals(False, False, ())

        state = strategy.classify_exit(handle, signals, liveness_probe=probe)

        assert state == AgentExecutionState.RESUMABLE_CONTINUE, (
            "A completed child cannot complete its parent without the parent's "
            f"durable completion evidence; got {state!r}"
        )

    def test_classify_exit_waiting_when_child_has_fresh_progress(self) -> None:
        """classify_exit stays WAITING_ON_CHILD when registry shows fresh progress."""

        t = [0.0]
        reg = ChildLivenessRegistry(
            progress_ttl=45.0,
            heartbeat_ttl=15.0,
            stale_label_ttl=10.0,
            exit_reconcile=5.0,
            now=lambda: t[0],
        )
        strategy = OpenCodeExecutionStrategy(label_scope="scope/a", registry=reg)
        spawn_line = json.dumps({"type": "child_started", "child_id": "c1"})
        strategy.observe_line(spawn_line)
        progress_line = json.dumps({"type": "child_progress", "child_id": "c1"})
        strategy.observe_line(progress_line)

        probe = DefaultLivenessProbe(registry=reg)
        handle = _FakeHandle(returncode=0, has_descendants=False)
        signals = CompletionSignals(False, False, ())

        state = strategy.classify_exit(handle, signals, liveness_probe=probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD, (
            f"Expected WAITING_ON_CHILD with fresh progress; got {state!r}"
        )
