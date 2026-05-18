"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

from typing import cast

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
    ChildActivitySnapshot,
    ChildLivenessRegistry,
    MutableRecord,
)
from ralph.process.liveness import FakeLivenessProbe

# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).
_check_process_result = check_process_result
_CompletionCheckOptions = CompletionCheckOptions


class TestStaleScopedChildEvidenceTimeout:
    """Regression tests for wt-97-timeout: stale child evidence must not suppress timeout.

    When scoped Ralph child evidence exists (registry/probe shows has_process=True) but
    is stale (no fresh progress/label), raw OS descendant presence alone must NOT keep
    the run in WAITING_ON_CHILD. The timeout must fire via ACTIVE or RESUMABLE_CONTINUE.

    This was the original bug: an agent stuck in WAITING_ON_CHILD for hundreds of seconds
    with workspace_events_since_wait=0 and alive_by=os_descendant only, emitting
    SUSPECTED_FROZEN at 600s but not timing out until the 1800s hard ceiling.
    """

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

    def test_classify_quiet_stale_registry_with_raw_descendants_returns_active(self) -> None:
        """Stale registry evidence + raw descendants must NOT return WAITING_ON_CHILD.

        Bug scenario: registry has a child (has_process=True) but no fresh progress/label.
        The child process is still running as an OS descendant but not making progress.
        Before the fix, raw descendant check kept returning WAITING_ON_CHILD indefinitely.
        """
        # Build a registry with a stale child (registered but no recent progress/heartbeat)
        stale_registry = ChildLivenessRegistry(
            progress_ttl=30.0,
            heartbeat_ttl=30.0,
            stale_label_ttl=60.0,
            exit_reconcile=5.0,
            now=lambda: 1000.0,  # frozen at t=1000
        )
        # Manually create a stale record: registered at t=900, progress/heartbeat at t=960
        # (both stale by t=1000: label_age=100s>60s, heartbeat_age=40s>30s)
        stale_registry._records["child-x"] = MutableRecord(
            child_id="child-x",
            scope_prefix="agent:test:",
            pid=12345,
            started_at=900.0,
            last_progress_at=960.0,
            last_heartbeat_at=960.0,
            last_known_phase="running",
        )

        strategy = OpenCodeExecutionStrategy(label_scope="test", registry=stale_registry)

        # Fake probe that has no child_snapshot (old interface only)
        class _OldStyleProbe:
            def any_agent_active(self, label_prefix: str) -> bool:
                return False  # No active agents in old-style check

        probe = cast("FakeLivenessProbe", _OldStyleProbe())
        # Raw descendants exist (the stuck child process is still running)
        handle = _FakeHandle(has_descendants=True)

        state = strategy.classify_quiet(handle, probe)

        # MUST return ACTIVE so idle timeout can fire, NOT WAITING_ON_CHILD
        assert state == AgentExecutionState.ACTIVE, (
            f"Stale registry + raw descendants must return ACTIVE, not {state!r}. "
            "Raw descendants must not override stale scoped evidence."
        )

    def test_classify_quiet_stale_probe_snapshot_with_raw_descendants_returns_active(
        self,
    ) -> None:
        """Stale probe snapshot + raw descendants must NOT return WAITING_ON_CHILD.

        Similar to above but using probe's child_snapshot directly.
        """
        # Create a stale snapshot: has_process=True but no fresh progress/label
        stale_snapshot = ChildActivitySnapshot(
            scope_prefix="agent:test:",
            has_process=True,  # Child exists
            has_fresh_label=False,  # Stale
            has_fresh_progress=False,  # Stale
            oldest_live_child_seconds=600.0,  # Child running for 600s
            active_count=1,
            terminal_count=0,
        )
        stale_probe = FakeLivenessProbe(snapshot=stale_snapshot)

        strategy = OpenCodeExecutionStrategy(label_scope="test")

        # Raw descendants exist
        handle = _FakeHandle(has_descendants=True)

        state = strategy.classify_quiet(handle, stale_probe)

        # MUST return ACTIVE
        assert state == AgentExecutionState.ACTIVE, (
            f"Stale probe snapshot + raw descendants must return ACTIVE, not {state!r}."
        )

    def test_classify_quiet_fresh_child_still_returns_waiting_on_child(self) -> None:
        """Fresh child progress must still keep the run in WAITING_ON_CHILD.

        This is the guard case: the fix must not cause false positives for
        legitimate child activity.
        """
        # Fresh snapshot: has_process=True AND has fresh progress/label
        fresh_snapshot = ChildActivitySnapshot(
            scope_prefix="agent:test:",
            has_process=True,
            has_fresh_label=True,  # Fresh
            has_fresh_progress=True,  # Fresh
            oldest_live_child_seconds=5.0,
            active_count=1,
            terminal_count=0,
        )
        fresh_probe = FakeLivenessProbe(snapshot=fresh_snapshot)

        strategy = OpenCodeExecutionStrategy(label_scope="test")
        handle = _FakeHandle(has_descendants=True)

        state = strategy.classify_quiet(handle, fresh_probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD, (
            f"Fresh child activity must still return WAITING_ON_CHILD, not {state!r}."
        )

    def test_classify_quiet_no_scoped_evidence_with_raw_descendants_returns_waiting(self) -> None:
        """Raw descendants without any scoped Ralph evidence must return WAITING_ON_CHILD.

        This ensures we don't break the case where Ralph has no scoped child visibility
        (e.g., unmanaged subprocess) but OS-level descendants exist.
        """
        strategy = OpenCodeExecutionStrategy(label_scope="test")

        # No registry, and a probe that reports no scoped children
        class _EmptyProbe:
            def any_agent_active(self, label_prefix: str) -> bool:
                return False

            def child_snapshot(self, scope_prefix: str) -> ChildActivitySnapshot:
                return ChildActivitySnapshot(
                    scope_prefix=scope_prefix,
                    has_process=False,
                    has_fresh_label=False,
                    has_fresh_progress=False,
                    oldest_live_child_seconds=None,
                    active_count=0,
                    terminal_count=0,
                )

        probe = _EmptyProbe()
        # But raw OS descendants exist (unmanaged child process)
        handle = _FakeHandle(has_descendants=True)

        state = strategy.classify_quiet(handle, probe)

        # MUST return WAITING_ON_CHILD because no scoped Ralph evidence exists
        assert state == AgentExecutionState.WAITING_ON_CHILD, (
            "Raw descendants without scoped Ralph evidence must return "
            f"WAITING_ON_CHILD, not {state!r}."
        )

    def test_evidence_precedence_stale_registry_with_raw_descendants_returns_resumable(
        self,
    ) -> None:
        """Stale registry + raw descendants in exit path must return RESUMABLE_CONTINUE.

        Tests _evidence_precedence via classify_exit.
        """
        stale_registry = ChildLivenessRegistry(
            progress_ttl=30.0,
            heartbeat_ttl=30.0,
            stale_label_ttl=60.0,
            exit_reconcile=5.0,
            now=lambda: 1000.0,
        )
        # Manually create a stale record: registered at t=900, progress/heartbeat at t=960
        stale_registry._records["child-x"] = MutableRecord(
            child_id="child-x",
            scope_prefix="agent:test:",
            pid=12345,
            started_at=900.0,
            last_progress_at=960.0,
            last_heartbeat_at=960.0,
            last_known_phase="running",
        )

        strategy = OpenCodeExecutionStrategy(label_scope="test", registry=stale_registry)

        class _OldStyleProbe:
            def any_agent_active(self, label_prefix: str) -> bool:
                return False

        probe = cast("FakeLivenessProbe", _OldStyleProbe())
        handle = _FakeHandle(has_descendants=True)
        signals = CompletionSignals(
            explicit_complete=False,
            required_artifact_present=False,
            artifact_types=(),
        )

        state = strategy.classify_exit(handle, signals, probe)

        # MUST return RESUMABLE_CONTINUE so timeout can fire
        assert state == AgentExecutionState.RESUMABLE_CONTINUE, (
            "Stale registry + raw descendants in exit path must return "
            f"RESUMABLE_CONTINUE, not {state!r}."
        )

    def test_evidence_precedence_stale_probe_snapshot_returns_resumable(self) -> None:
        """Stale probe snapshot in exit path must return RESUMABLE_CONTINUE."""
        stale_snapshot = ChildActivitySnapshot(
            scope_prefix="agent:test:",
            has_process=True,
            has_fresh_label=False,
            has_fresh_progress=False,
            oldest_live_child_seconds=600.0,
            active_count=1,
            terminal_count=0,
        )
        stale_probe = FakeLivenessProbe(snapshot=stale_snapshot)

        strategy = OpenCodeExecutionStrategy(label_scope="test")
        handle = _FakeHandle(has_descendants=True)
        signals = CompletionSignals(
            explicit_complete=False,
            required_artifact_present=False,
            artifact_types=(),
        )

        state = strategy.classify_exit(handle, signals, stale_probe)

        assert state == AgentExecutionState.RESUMABLE_CONTINUE, (
            f"Stale probe snapshot in exit path must return RESUMABLE_CONTINUE, not {state!r}."
        )

    def test_evidence_precedence_fresh_child_still_returns_waiting_on_child(self) -> None:
        """Fresh child in exit path must still return WAITING_ON_CHILD."""
        fresh_snapshot = ChildActivitySnapshot(
            scope_prefix="agent:test:",
            has_process=True,
            has_fresh_label=True,
            has_fresh_progress=True,
            oldest_live_child_seconds=5.0,
            active_count=1,
            terminal_count=0,
        )
        fresh_probe = FakeLivenessProbe(snapshot=fresh_snapshot)

        strategy = OpenCodeExecutionStrategy(label_scope="test")
        handle = _FakeHandle(has_descendants=True)
        signals = CompletionSignals(
            explicit_complete=False,
            required_artifact_present=False,
            artifact_types=(),
        )

        state = strategy.classify_exit(handle, signals, fresh_probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD, (
            f"Fresh child in exit path must still return WAITING_ON_CHILD, not {state!r}."
        )

    def test_evidence_precedence_no_scoped_evidence_with_raw_descendants_returns_waiting(
        self,
    ) -> None:
        """No scoped evidence + raw descendants in exit path must return WAITING_ON_CHILD."""
        strategy = OpenCodeExecutionStrategy(label_scope="test")

        class _EmptyProbe:
            def any_agent_active(self, label_prefix: str) -> bool:
                return False

            def child_snapshot(self, scope_prefix: str) -> ChildActivitySnapshot:
                return ChildActivitySnapshot(
                    scope_prefix=scope_prefix,
                    has_process=False,
                    has_fresh_label=False,
                    has_fresh_progress=False,
                    oldest_live_child_seconds=None,
                    active_count=0,
                    terminal_count=0,
                )

        probe = _EmptyProbe()
        handle = _FakeHandle(has_descendants=True)
        signals = CompletionSignals(
            explicit_complete=False,
            required_artifact_present=False,
            artifact_types=(),
        )

        state = strategy.classify_exit(handle, signals, probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD, (
            "No scoped evidence + raw descendants in exit must return "
            f"WAITING_ON_CHILD, not {state!r}."
        )


_FakeHandle = TestStaleScopedChildEvidenceTimeout._FakeHandle
