"""End-to-end regression scenarios for OpenCode child liveness classification.

Three operator-critical scenarios tested as black-box via the public execution strategy API:

1. Child exited cleanly — terminal ack seen → TERMINAL_COMPLETE, no indefinite waiting.
2. Child hung — process label exists but progress/heartbeat expired → RESUMABLE_CONTINUE
   (stale process existence alone must NOT hold WAITING_ON_CHILD open).
3. Child still working — progress lease keeps renewing → WAITING_ON_CHILD maintained.

Also includes post-exit path integration tests that drive check_process_result and
PostExitWatchdog with FakeClock to validate the planned end-to-end behaviors.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import AgentExecutionState, OpenCodeExecutionStrategy
from ralph.agents.idle_watchdog import (
    TimeoutPolicy,
)
from ralph.agents.invoke import (
    CompletionCheckOptions,
    OpenCodeResumableExitError,
    check_process_result,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.process.child_liveness import ChildLivenessRegistry
from ralph.process.liveness import DefaultLivenessProbe

if TYPE_CHECKING:
    from ralph.process.manager import ManagedProcess
from tests.integration._fake_handle_post_exit import _FakeHandlePostExit


class TestPostExitViaCheckProcessResult:
    """Integration tests exercising check_process_result + PostExitWatchdog with FakeClock."""

    def test_terminal_ack_produces_clean_exit_viacheck_process_result(
        self, tmp_path: pytest.FixtureRequest
    ) -> None:
        """After terminal ack, check_process_result completes without raising."""
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
        check_process_result(
            cast("ManagedProcess", handle),
            "opencode",
            [],
            CompletionCheckOptions(
                execution_strategy=strategy,
                liveness_probe=probe,
                workspace_path=tmp_path,
                required_artifact=RequiredArtifact(
                    phase="development",
                    artifact_type="development_result",
                    json_path=".agent/artifacts/development_result.json",
                    markdown_path=None,
                    normalizer=None,
                ),
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
            check_process_result(
                cast("ManagedProcess", handle),
                "opencode",
                [],
                CompletionCheckOptions(
                    execution_strategy=strategy,
                    liveness_probe=probe,
                    workspace_path=tmp_path,
                    required_artifact=RequiredArtifact(
                        phase="development",
                        artifact_type="development_result",
                        json_path=".agent/artifacts/development_result.json",
                        markdown_path=None,
                        normalizer=None,
                    ),
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


def _no_signals(
    workspace: object = None,
    raw_output: object = None,
    *,
    required_artifact: object = None,
    run_id: object = None,
    sentinel_secret: object = None,
    receipt_secret: object = None,
) -> CompletionSignals:
    return CompletionSignals(
        explicit_complete=False,
        required_artifact_present=False,
        artifact_types=(),
    )
