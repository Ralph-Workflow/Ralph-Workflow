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
from ralph.agents.invoke import (
    CompletionCheckOptions,
    check_process_result,
)
from ralph.process.child_liveness import (
    ChildLivenessRegistry,
)
from ralph.process.liveness import DefaultLivenessProbe

# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).
_check_process_result = check_process_result
_CompletionCheckOptions = CompletionCheckOptions


class TestRegistryBackedClassifyExit:
    """classify_exit uses registry terminal_count to confirm all children exited."""

    class _FakeHandle:
        returncode: int = 0
        stdout = None
        stderr = None

        def __init__(self, *, returncode: int = 0, has_descendants: bool = False) -> None:
            self.returncode = returncode
            self._has_descendants = has_descendants

        def has_live_descendants(self) -> bool:
            return self._has_descendants

        def descendant_snapshot(self) -> tuple[int, float | None]:
            return (1 if self._has_descendants else 0, 5.0 if self._has_descendants else None)

        def poll(self) -> int | None:
            return self.returncode

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

    def test_classify_exit_terminal_complete_when_all_children_acked(self) -> None:
        """classify_exit returns TERMINAL_COMPLETE when registry shows all children done."""

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

        assert state == AgentExecutionState.TERMINAL_COMPLETE, (
            f"Expected TERMINAL_COMPLETE after all children acked; got {state!r}"
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


_FakeHandle = TestRegistryBackedClassifyExit._FakeHandle
