"""End-to-end regression scenarios for OpenCode child liveness classification.

Three operator-critical scenarios tested as black-box via the public execution strategy API:

1. Child exited cleanly — terminal ack seen → TERMINAL_COMPLETE, no indefinite waiting.
2. Child hung — process label exists but progress/heartbeat expired → RESUMABLE_CONTINUE
   (stale process existence alone must NOT hold WAITING_ON_CHILD open).
3. Child still working — progress lease keeps renewing → WAITING_ON_CHILD maintained.

Also includes post-exit path integration tests that drive _check_process_result and
PostExitWatchdog with FakeClock to validate the planned end-to-end behaviors.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import AgentExecutionState, OpenCodeExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    OpenCodeResumableExitError,
    _check_process_result,
    _CompletionCheckOptions,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process.child_liveness import ChildLivenessRegistry
from ralph.process.liveness import DefaultLivenessProbe, FakeLivenessProbe

if TYPE_CHECKING:
    from ralph.process.manager import ManagedProcess


class _FakeHandle:
    returncode = 0

    def __init__(self, *, has_descendants: bool = False) -> None:
        self._has_descendants = has_descendants

    def has_live_descendants(self) -> bool:
        return self._has_descendants


def _no_signals() -> CompletionSignals:
    return CompletionSignals(False, False, ())


def _make_registry(*, t: list[float]) -> ChildLivenessRegistry:
    return ChildLivenessRegistry(
        progress_ttl=45.0,
        heartbeat_ttl=15.0,
        stale_label_ttl=10.0,
        exit_reconcile=5.0,
        now=lambda: t[0],
    )


# ---------------------------------------------------------------------------
# Scenario 1: Child exited cleanly via terminal ack
# ---------------------------------------------------------------------------


class TestChildExitedCleanly:
    """Terminal ack causes TERMINAL_COMPLETE; no indefinite waiting."""

    def test_terminal_ack_produces_terminal_complete(self) -> None:
        """After child emits terminal ack, classify_exit returns TERMINAL_COMPLETE."""
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/x", registry=reg)
        probe = FakeLivenessProbe()

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "c1"}))
        strategy.observe_line(json.dumps({"type": "child_progress", "child_id": "c1"}))
        strategy.observe_line(json.dumps({"type": "child_complete", "child_id": "c1"}))

        handle = _FakeHandle(has_descendants=False)
        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)

        assert result == AgentExecutionState.TERMINAL_COMPLETE, (
            f"Expected TERMINAL_COMPLETE after terminal ack; got {result!r}"
        )

    def test_terminal_ack_wins_over_os_descendants(self) -> None:
        """Even if OS descendants still exist, terminal ack takes precedence."""
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/x", registry=reg)
        probe = FakeLivenessProbe()

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "c1"}))
        strategy.observe_line(json.dumps({"type": "child_complete", "child_id": "c1"}))

        # OS descendants still visible (straggler cleanup)
        handle = _FakeHandle(has_descendants=True)
        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)

        assert result == AgentExecutionState.TERMINAL_COMPLETE, (
            f"Terminal ack must outrank OS descendants; got {result!r}"
        )


# ---------------------------------------------------------------------------
# Scenario 2: Child hung — progress/heartbeat expired
# ---------------------------------------------------------------------------


class TestChildHung:
    """Stale process existence alone must NOT hold WAITING_ON_CHILD open.

    When a child was spawned but has not renewed its progress lease within
    progress_ttl, and the stale_label_ttl has also expired, the registry
    provides no fresh evidence.  The parent must NOT keep WAITING_ON_CHILD
    based solely on the stale registry record.  Without OS descendants,
    the result must be RESUMABLE_CONTINUE.
    """

    def test_stale_process_without_fresh_evidence_is_not_waiting(self) -> None:
        """After progress_ttl + stale_label_ttl expire, stale child → RESUMABLE_CONTINUE."""
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/hung", registry=reg)
        probe = FakeLivenessProbe()  # no active labels

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "hung"}))

        # Advance past both stale_label_ttl (10s) and progress_ttl (45s)
        t[0] = 60.0

        handle = _FakeHandle(has_descendants=False)
        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)

        assert result == AgentExecutionState.RESUMABLE_CONTINUE, (
            f"Stale child without fresh evidence must yield RESUMABLE_CONTINUE; got {result!r}"
        )

    def test_stale_process_with_os_descendants_still_waits(self) -> None:
        """Stale registry + live OS descendants → WAITING_ON_CHILD (OS is fallback)."""
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/hung", registry=reg)
        probe = FakeLivenessProbe()

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "hung"}))
        t[0] = 60.0

        handle = _FakeHandle(has_descendants=True)
        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)

        assert result == AgentExecutionState.WAITING_ON_CHILD, (
            f"OS descendants are fallback evidence even for stale registry; got {result!r}"
        )


# ---------------------------------------------------------------------------
# Scenario 3: Child still working — progress lease keeps renewing
# ---------------------------------------------------------------------------


class TestChildStillWorking:
    """Fresh progress renewal keeps parent in WAITING_ON_CHILD."""

    def test_fresh_progress_holds_waiting(self) -> None:
        """Each progress event within progress_ttl maintains WAITING_ON_CHILD."""
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/busy", registry=reg)
        probe = FakeLivenessProbe()

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "busy"}))

        # Simulate periodic progress renewal
        for tick in [5.0, 20.0, 40.0]:
            t[0] = tick
            strategy.observe_line(json.dumps({"type": "child_progress", "child_id": "busy"}))

        handle = _FakeHandle(has_descendants=False)
        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)

        assert result == AgentExecutionState.WAITING_ON_CHILD, (
            f"Fresh progress must keep WAITING_ON_CHILD; got {result!r}"
        )

    def test_progress_renewal_after_near_expiry_resets_freshness(self) -> None:
        """Progress at t=44 resets progress_ttl so at t=85 the child is still fresh."""
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/renew", registry=reg)
        probe = FakeLivenessProbe()

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "r1"}))

        # Initial progress at t=0 (from started event, heartbeat at t=0)
        # Progress renewal at t=44 (1s before ttl)
        t[0] = 44.0
        strategy.observe_line(json.dumps({"type": "child_progress", "child_id": "r1"}))

        # At t=85, 41s since last progress (< 45s progress_ttl) → still fresh
        t[0] = 85.0
        handle = _FakeHandle(has_descendants=False)
        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)

        assert result == AgentExecutionState.WAITING_ON_CHILD, (
            f"Renewed progress should be fresh at t=85 (41s after renewal); got {result!r}"
        )

    def test_progress_expires_without_renewal_yields_resumable(self) -> None:
        """After progress_ttl expires without renewal and no OS descendants → RESUMABLE_CONTINUE."""
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/expired", registry=reg)
        probe = FakeLivenessProbe()

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "e1"}))
        # Progress at t=0 via observe_line (from heartbeat update on spawn)
        strategy.observe_line(json.dumps({"type": "child_progress", "child_id": "e1"}))

        # Advance past progress_ttl=45 and stale_label_ttl=10
        t[0] = 60.0

        handle = _FakeHandle(has_descendants=False)
        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)

        assert result == AgentExecutionState.RESUMABLE_CONTINUE, (
            f"Expired progress without renewal must yield RESUMABLE_CONTINUE; got {result!r}"
        )


# ---------------------------------------------------------------------------
# Post-exit path integration: _check_process_result + PostExitWatchdog
# ---------------------------------------------------------------------------


class _FakeHandlePostExit:
    """ManagedProcess-compatible test double with injectable descendant state."""

    returncode = 0
    stdout = None
    stderr = None

    def __init__(self, *, has_descendants: bool = False) -> None:
        self._has_descendants = has_descendants

    def has_live_descendants(self) -> bool:
        return self._has_descendants

    def descendant_snapshot(self) -> tuple[int, float | None]:
        return (1 if self._has_descendants else 0, 5.0 if self._has_descendants else None)

    def poll(self) -> int | None:
        return 0


class TestPostExitViaCheckProcessResult:
    """Integration tests exercising _check_process_result + PostExitWatchdog with FakeClock."""

    def test_terminal_ack_produces_clean_exit_via_check_process_result(
        self, tmp_path: pytest.FixtureRequest
    ) -> None:
        """After terminal ack, _check_process_result completes without raising."""
        t = [0.0]
        reg = ChildLivenessRegistry(
            progress_ttl=45.0,
            heartbeat_ttl=15.0,
            stale_label_ttl=10.0,
            exit_reconcile=5.0,
            now=lambda: t[0],
        )
        strategy = OpenCodeExecutionStrategy(label_scope="scope/tc", registry=reg)
        probe = DefaultLivenessProbe(registry=reg)

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "tc1"}))
        strategy.observe_line(json.dumps({"type": "child_progress", "child_id": "tc1"}))
        strategy.observe_line(json.dumps({"type": "child_complete", "child_id": "tc1"}))

        handle = _FakeHandlePostExit(has_descendants=False)
        clock = FakeClock()

        # Should not raise because terminal ack → TERMINAL_COMPLETE
        _check_process_result(
            cast("ManagedProcess", handle),
            "opencode",
            [],
            _CompletionCheckOptions(
                execution_strategy=strategy,
                liveness_probe=probe,
                workspace_path=tmp_path,
                phase="development",
                policy=TimeoutPolicy(
                    idle_timeout_seconds=None,
                    parent_exit_grace_seconds=1.0,
                    descendant_wait_poll_seconds=0.01,
                    descendant_wait_timeout_seconds=5.0,
                ),
            ),
            _clock=clock,
        )

    def test_stale_child_with_os_descendants_raises_after_descendant_wait(
        self, tmp_path: pytest.FixtureRequest
    ) -> None:
        """Stale registry + OS descendants → waits for quiesce, then raises when not resolved."""
        t = [0.0]
        reg = ChildLivenessRegistry(
            progress_ttl=45.0,
            heartbeat_ttl=15.0,
            stale_label_ttl=10.0,
            exit_reconcile=5.0,
            now=lambda: t[0],
        )
        strategy = OpenCodeExecutionStrategy(label_scope="scope/hung2", registry=reg)
        probe = DefaultLivenessProbe(registry=reg)

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "hung2"}))
        # Advance past stale_label_ttl without progress
        t[0] = 60.0

        # OS descendants still visible to handle but registry is stale
        handle = _FakeHandlePostExit(has_descendants=True)
        clock = FakeClock()

        with pytest.raises(OpenCodeResumableExitError):
            _check_process_result(
                cast("ManagedProcess", handle),
                "opencode",
                [],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    liveness_probe=probe,
                    workspace_path=tmp_path,
                    phase="development",
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        parent_exit_grace_seconds=0.0,
                        descendant_wait_poll_seconds=0.01,
                        descendant_wait_timeout_seconds=0.05,
                    ),
                ),
                _clock=clock,
            )

    def test_fresh_progress_holds_waiting_until_ttl_expires_then_resumes(
        self, tmp_path: pytest.FixtureRequest
    ) -> None:
        """Fresh progress keeps classify_exit as WAITING_ON_CHILD; expiry yields resumable."""
        t = [0.0]
        reg = ChildLivenessRegistry(
            progress_ttl=45.0,
            heartbeat_ttl=15.0,
            stale_label_ttl=10.0,
            exit_reconcile=5.0,
            now=lambda: t[0],
        )
        strategy = OpenCodeExecutionStrategy(label_scope="scope/working2", registry=reg)
        probe = DefaultLivenessProbe(registry=reg)

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "w2"}))
        strategy.observe_line(json.dumps({"type": "child_progress", "child_id": "w2"}))

        # Advance time to just within progress_ttl: child still fresh
        t[0] = 30.0
        handle = _FakeHandlePostExit(has_descendants=False)

        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)
        assert result == AgentExecutionState.WAITING_ON_CHILD, (
            f"Fresh progress (30s < ttl=45s) should yield WAITING_ON_CHILD; got {result!r}"
        )

        # Advance past progress_ttl: child is now stale
        t[0] = 60.0
        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)
        assert result == AgentExecutionState.RESUMABLE_CONTINUE, (
            f"Stale progress (60s > ttl=45s) should yield RESUMABLE_CONTINUE; got {result!r}"
        )
