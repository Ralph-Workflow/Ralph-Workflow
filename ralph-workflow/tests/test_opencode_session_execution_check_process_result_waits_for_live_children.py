"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

import threading
import time as _time_module
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    CompletionCheckOptions,
    OpenCodeResumableExitError,
    check_process_result,
    wait_for_descendants_then_recheck,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.process.child_liveness import (
    ChildActivitySnapshot,
)
from ralph.process.liveness import FakeLivenessProbe
from tests.fake_handle import _FakeHandle

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.process.manager import ManagedProcess


# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).
_check_process_result = check_process_result
_CompletionCheckOptions = CompletionCheckOptions


class TestCheckProcessResultWaitsForLiveChildren:
    """_check_process_result waits for child agents before raising OpenCodeResumableExitError."""

    def test_raises_resumable_exit_when_wait_times_out_without_artifact(
        self, tmp_path: Path
    ) -> None:
        """Probe stays active throughout wait; deadline expires → OpenCodeResumableExitError.

        This tests the real _wait_for_descendants_then_recheck with a probe that
        never transitions to inactive.
        """
        probe = FakeLivenessProbe(active=True)  # Always active

        # Fake evaluate_completion to always return no signals
        def _fake_evaluate_completion(
            workspace: object,
            raw_output: object,
            *,
            required_artifact: object = None,
            run_id: object = None,
            sentinel_secret: object = None,
            receipt_secret: object = None,
        ) -> object:
            return CompletionSignals(False, False, ())

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)

        # Time: 0.0 → 0.1 → 0.2 → deadline at 0.1 (probe always active)
        # First call (t=0.0): probe active → WAITING_ON_CHILD, wait
        # Second call (t=0.1): still within deadline, probe still active → WAITING_ON_CHILD, wait
        # Third call (t=0.2): past deadline → RESUMABLE_CONTINUE (timeout fallback)
        call_count = [0]
        monotonic_vals = iter([0.0, 0.1, 0.2])

        def _fake_event_wait(self: object, timeout: object = None) -> object:
            if timeout is not None and timeout == _DESCENDANT_WAIT_POLL_SECONDS:
                call_count[0] += 1
                return None
            return threading.Event.wait(self, timeout)

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
            patch.object(threading.Event, "wait", _fake_event_wait),
            pytest.raises(OpenCodeResumableExitError),
        ):
            _check_process_result(
                cast("ManagedProcess", handle),
                "opencode",
                [],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    liveness_probe=probe,
                    required_artifact=RequiredArtifact(
                        phase="development",
                        artifact_type="development_result",
                        artifact_path=".agent/artifacts/development_result.md",
                        markdown_path=None,
                        normalizer=None,
                    ),
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        descendant_wait_timeout_seconds=0.1,
                    ),
                    evaluate_completion_fn=_fake_evaluate_completion,
                ),
            )

    def test_grace_window_runs_even_when_no_children_at_exit_time(self, tmp_path: Path) -> None:
        """Grace window always runs for OpenCode rc=0 exits without completion signals."""
        probe = FakeLivenessProbe(active=False)

        evaluate_calls = [0]

        def _fake_evaluate_completion(
            workspace: object,
            raw_output: object,
            *,
            required_artifact: object = None,
            run_id: object = None,
            sentinel_secret: object = None,
            receipt_secret: object = None,
        ) -> object:
            evaluate_calls[0] += 1
            return CompletionSignals(False, False, ())

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=False)

        monotonic_vals = iter([0.0, 0.5, 1.0])
        poll_count = [0]

        def _fake_event_wait(self: object, timeout: object = None) -> None:
            if timeout is not None and timeout == _DESCENDANT_WAIT_POLL_SECONDS:
                poll_count[0] += 1

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
            patch.object(threading.Event, "wait", _fake_event_wait),
            pytest.raises(OpenCodeResumableExitError),
        ):
            _check_process_result(
                cast("ManagedProcess", handle),
                "opencode",
                [],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    liveness_probe=probe,
                    required_artifact=RequiredArtifact(
                        phase="development",
                        artifact_type="development_result",
                        artifact_path=".agent/artifacts/development_result.md",
                        markdown_path=None,
                        normalizer=None,
                    ),
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        parent_exit_grace_seconds=1.0,
                        descendant_wait_timeout_seconds=30.0,
                    ),
                    evaluate_completion_fn=_fake_evaluate_completion,
                ),
            )

        assert evaluate_calls[0] > 1, (
            f"Grace window must poll evaluate_completion; got {evaluate_calls[0]} calls"
        )
        assert poll_count[0] >= 1, "Grace window must sleep at least once before expiring"

    def test_wait_helper_returns_resumable_continue_on_timeout_with_children_alive(
        self, tmp_path: Path
    ) -> None:
        """Direct test: _wait_for_descendants_then_recheck returns RESUMABLE_CONTINUE on timeout.

        When the probe stays active and the deadline expires, the helper must
        return RESUMABLE_CONTINUE (not WAITING_ON_CHILD) so the caller raises.
        """
        probe = FakeLivenessProbe(active=True)  # Always active

        def _fake_evaluate_completion(
            workspace: object,
            raw_output: object,
            *,
            required_artifact: object = None,
            run_id: object = None,
            sentinel_secret: object = None,
            receipt_secret: object = None,
        ) -> object:
            return CompletionSignals(False, False, ())

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)

        # Time: 0.0 → 0.1 → deadline at 0.1 (after two 0.05s polls)
        # First call (t=0.0): WAITING_ON_CHILD, wait
        # Second call (t=0.1): still WAITING_ON_CHILD, deadline hit → RESUMABLE_CONTINUE
        # Time advances 0.05s per monotonic call; deadline is 0.1s. The loop
        # polls, waits (with a clamped wait when the remaining deadline is
        # smaller than the poll interval), and then rechecks the deadline.
        monotonic_vals = iter([0.0, 0.05, 0.1, 0.15])
        poll_count = [0]

        def _fake_event_wait(self: object, timeout: object = None) -> object:
            # Count every wait issued by the loop; tolerate clamped final waits.
            if timeout is not None and timeout > 0:
                poll_count[0] += 1
            return None

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
            patch.object(threading.Event, "wait", _fake_event_wait),
        ):
            result = wait_for_descendants_then_recheck(
                cast("ManagedProcess", handle),
                CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    liveness_probe=probe,
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        descendant_wait_timeout_seconds=0.1,
                    ),
                    evaluate_completion_fn=_fake_evaluate_completion,
                ),
                [],
            )

        # After deadline with children still alive, helper returns RESUMABLE_CONTINUE
        assert result == AgentExecutionState.RESUMABLE_CONTINUE, (
            f"Expected RESUMABLE_CONTINUE after timeout with children alive; got {result!r}"
        )

    def test_artifact_appears_during_wait_produces_terminal_complete(self, tmp_path: Path) -> None:
        """Artifact appears during wait; no error raised (TERMINAL_COMPLETE).

        Sequence:
        - t=0.0: initial classify_exit → WAITING_ON_CHILD (no artifact yet)
        - t=0.0: wait 0.5s (poll interval)
        - t=0.5: second evaluate_completion → artifact now present → TERMINAL_COMPLETE

        The wait helper is invoked and correctly returns TERMINAL_COMPLETE when
        the required artifact appears between polls, so _check_process_result
        does not raise OpenCodeResumableExitError.
        """
        probe = FakeLivenessProbe(active=True)  # Children alive initially
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)

        # Track which call to evaluate_completion we're on:
        # 1. _check_process_result initial check → no artifact
        # 2. _wait_for_descendants_then_recheck first poll → no artifact
        # 3. _wait_for_descendants_then_recheck second poll → artifact appears!
        call_count = [0]
        _artifact_appears_on = 3  # initial check + first loop poll + second loop poll

        def _fake_evaluate_completion(
            workspace: object,
            raw_output: object,
            *,
            required_artifact: object = None,
            run_id: object = None,
            sentinel_secret: object = None,
            receipt_secret: object = None,
        ) -> object:
            call_count[0] += 1
            if call_count[0] >= _artifact_appears_on:
                # Simulate artifact appearing between polls 1 and 2
                return CompletionSignals(False, True, ("development_result",))
            return CompletionSignals(False, False, ())

        # FakeClock: t=0.0 → sleep(0.5) → t=0.5 → artifact appears (call 3) → TERMINAL_COMPLETE
        _check_process_result(
            cast("ManagedProcess", handle),
            "opencode",
            [],
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                liveness_probe=probe,
                policy=TimeoutPolicy(
                    idle_timeout_seconds=None,
                    descendant_wait_timeout_seconds=0.6,
                ),
                evaluate_completion_fn=_fake_evaluate_completion,
            ),
            _clock=FakeClock(0.0),
        )
        # No exception raised because artifact appeared during wait → TERMINAL_COMPLETE

    def test_explicit_complete_appears_during_wait_produces_terminal_complete(
        self, tmp_path: Path
    ) -> None:
        """Explicit completion marker appears during wait; no error raised (TERMINAL_COMPLETE).

        Same pattern as artifact test but for explicit_complete signal.
        """
        probe = FakeLivenessProbe(active=True)
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)

        call_count = [0]
        _artifact_appears_on = 3  # initial check + first loop poll + second loop poll

        def _fake_evaluate_completion(
            workspace: object,
            raw_output: object,
            *,
            required_artifact: object = None,
            run_id: object = None,
            sentinel_secret: object = None,
            receipt_secret: object = None,
        ) -> object:
            call_count[0] += 1
            if call_count[0] >= _artifact_appears_on:
                # Simulate explicit_complete appearing between polls. The
                # completion marker alone is no longer terminal; corroborate
                # it with the completion sentinel (written by the real
                # declare_complete MCP tool).
                return CompletionSignals(True, False, (), completion_sentinel_present=True)
            return CompletionSignals(False, False, ())

        # FakeClock: t=0.0 → sleep(0.5) → t=0.5 → explicit_complete (call 3) → TERMINAL_COMPLETE
        _check_process_result(
            cast("ManagedProcess", handle),
            "opencode",
            [],
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                liveness_probe=probe,
                policy=TimeoutPolicy(
                    idle_timeout_seconds=None,
                    descendant_wait_timeout_seconds=0.6,
                ),
                evaluate_completion_fn=_fake_evaluate_completion,
            ),
            _clock=FakeClock(0.0),
        )
        # No exception raised because explicit_complete appeared during wait

    def test_descendants_finish_during_wait_produces_terminal_complete(
        self, tmp_path: Path
    ) -> None:
        """OS-level descendants finish during wait; no error raised (TERMINAL_COMPLETE).

        Tests the handle.has_live_descendants() path transitioning from True to False
        during the wait window.
        """
        # Probe is inactive (Ralph-tracked agents done), but OS descendants alive initially
        probe = FakeLivenessProbe(active=False)
        strategy = OpenCodeExecutionStrategy()

        # Track whether descendants are alive
        descendants_alive = [True]

        class _TrackingFakeHandle:
            returncode = 0

            def has_live_descendants(self) -> bool:
                return descendants_alive[0]

        handle = _TrackingFakeHandle()

        call_count = [0]
        _artifact_appears_on = 3  # initial check + first loop poll + second loop poll

        def _fake_evaluate_completion_with_artifact(
            workspace: object,
            raw_output: object,
            *,
            required_artifact: object = None,
            run_id: object = None,
            sentinel_secret: object = None,
            receipt_secret: object = None,
        ) -> object:
            call_count[0] += 1
            if call_count[0] >= _artifact_appears_on:
                # After descendants finish, artifact appears
                return CompletionSignals(False, True, ("development_result",))
            return CompletionSignals(False, False, ())

        # FakeClock: t=0.0 → sleep(0.5) → t=0.5 → artifact appears (call 3) → TERMINAL_COMPLETE
        _check_process_result(
            cast("ManagedProcess", handle),
            "opencode",
            [],
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                liveness_probe=probe,
                policy=TimeoutPolicy(
                    idle_timeout_seconds=None,
                    descendant_wait_timeout_seconds=0.6,
                ),
                evaluate_completion_fn=_fake_evaluate_completion_with_artifact,
            ),
            _clock=FakeClock(0.0),
        )
        # No exception raised because descendants finished and artifact appeared during wait

    def test_wait_helper_timeout_then_final_recheck_finds_completion(self, tmp_path: Path) -> None:
        """Deadline expires but completion appears exactly at deadline; final recheck catches it.

        The final recheck (added to fix the timeout gap) must evaluate completion one more
        time after the deadline elapses, rather than blindly returning RESUMABLE_CONTINUE.
        """
        probe = FakeLivenessProbe(active=True)  # Stays active until deadline
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)

        call_count = [0]
        _artifact_appears_on = 2  # first loop poll + final recheck

        def _fake_evaluate_completion(
            workspace: object,
            raw_output: object,
            *,
            required_artifact: object = None,
            run_id: object = None,
            sentinel_secret: object = None,
            receipt_secret: object = None,
        ) -> object:
            call_count[0] += 1
            # First poll (inside wait loop): no completion
            # Second call (final recheck after deadline): completion appears!
            if call_count[0] >= _artifact_appears_on:
                return CompletionSignals(False, True, ("development_result",))
            return CompletionSignals(False, False, ())

        # t[0]=0.0: deadline = 0.5; t[1]=0.0: loop check True -> poll (call 1, no signals);
        # t[2]=0.5: loop check False -> final recheck (call 2) -> artifact -> TERMINAL_COMPLETE
        monotonic_vals = iter([0.0, 0.0, 0.5])

        def _fake_event_wait(self: object, timeout: object = None) -> object:
            return None

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
            patch.object(threading.Event, "wait", _fake_event_wait),
        ):
            result = wait_for_descendants_then_recheck(
                cast("ManagedProcess", handle),
                CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    liveness_probe=probe,
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        descendant_wait_timeout_seconds=0.5,
                    ),
                    evaluate_completion_fn=_fake_evaluate_completion,
                ),
                [],
            )

        # Final recheck caught the completion → TERMINAL_COMPLETE, not RESUMABLE_CONTINUE
        assert result == AgentExecutionState.TERMINAL_COMPLETE, (
            f"Expected TERMINAL_COMPLETE from final recheck; got {result!r}"
        )

    def test_grace_window_catches_artifact_appearing_after_exit_with_no_children(
        self, tmp_path: Path
    ) -> None:
        """Artifact appears during grace window; no OpenCodeResumableExitError raised.

        Bug scenario: OpenCode exits rc=0, no children visible, no signals at exact exit
        moment. Grace window polls and finds artifact on second evaluate_completion call.
        """
        probe = FakeLivenessProbe(active=False)
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=False)

        call_count = [0]

        def _fake_evaluate_completion(
            workspace: object,
            raw_output: object,
            *,
            required_artifact: object = None,
            run_id: object = None,
            sentinel_secret: object = None,
            receipt_secret: object = None,
        ) -> object:
            call_count[0] += 1
            if call_count[0] == 1:
                return CompletionSignals(False, False, ())
            return CompletionSignals(False, True, ("development_result",))

        monotonic_vals = iter([0.0, 0.5, 1.0])

        def _fake_event_wait(self: object, timeout: object = None) -> None:
            pass

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
            patch.object(threading.Event, "wait", _fake_event_wait),
        ):
            _check_process_result(
                cast("ManagedProcess", handle),
                "opencode",
                [],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    liveness_probe=probe,
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        parent_exit_grace_seconds=1.0,
                        descendant_wait_timeout_seconds=30.0,
                    ),
                    evaluate_completion_fn=_fake_evaluate_completion,
                ),
            )
        # No exception raised means artifact found during grace -> TERMINAL_COMPLETE

    def test_grace_window_raises_resumable_when_no_signal_and_no_children(
        self, tmp_path: Path
    ) -> None:
        """No signals and no children throughout grace -> OpenCodeResumableExitError raised."""
        probe = FakeLivenessProbe(active=False)
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=False)

        def _fake_evaluate_completion(
            workspace: object,
            raw_output: object,
            *,
            required_artifact: object = None,
            run_id: object = None,
            sentinel_secret: object = None,
            receipt_secret: object = None,
        ) -> object:
            return CompletionSignals(False, False, ())

        monotonic_vals = iter([0.0, 0.5, 1.0])

        def _fake_event_wait(self: object, timeout: object = None) -> object:
            return None

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
            patch.object(threading.Event, "wait", _fake_event_wait),
            pytest.raises(OpenCodeResumableExitError),
        ):
            _check_process_result(
                cast("ManagedProcess", handle),
                "opencode",
                [],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    liveness_probe=probe,
                    required_artifact=RequiredArtifact(
                        phase="development",
                        artifact_type="development_result",
                        artifact_path=".agent/artifacts/development_result.md",
                        markdown_path=None,
                        normalizer=None,
                    ),
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        parent_exit_grace_seconds=1.0,
                        descendant_wait_timeout_seconds=30.0,
                    ),
                    evaluate_completion_fn=_fake_evaluate_completion,
                ),
            )

    def test_grace_window_escalates_to_descendant_wait_when_children_appear(
        self, tmp_path: Path
    ) -> None:
        """Child appears during grace; escalates to descendant wait; raises after timeout.

        Proves the two-window composition: grace detects a late-appearing child and
        escalates to the existing descendant wait, which eventually times out and raises.
        """

        class _FlippingProbe:
            """Returns False on first call, True on subsequent calls."""

            def __init__(self) -> None:
                self.call_count = 0

            def any_agent_active(self, label_prefix: str) -> bool:
                self.call_count += 1
                return self.call_count > 1

            def child_snapshot(self, scope_prefix: str) -> ChildActivitySnapshot:
                active = self.any_agent_active(scope_prefix)
                return ChildActivitySnapshot(
                    scope_prefix=scope_prefix,
                    has_process=active,
                    has_fresh_label=active,
                    has_fresh_progress=active,
                    oldest_live_child_seconds=None,
                    active_count=1 if active else 0,
                    terminal_count=0,
                )

        probe = _FlippingProbe()
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=False)

        def _fake_evaluate_completion(
            workspace: object,
            raw_output: object,
            *,
            required_artifact: object = None,
            run_id: object = None,
            sentinel_secret: object = None,
            receipt_secret: object = None,
        ) -> object:
            return CompletionSignals(False, False, ())

        # [0] grace deadline calc; [1] grace loop check -> probe call 2 -> WAITING_ON_CHILD
        # [2] descendant deadline calc; [3] descendant loop check -> WAITING_ON_CHILD -> sleep
        # [4] descendant loop exit -> final recheck -> RESUMABLE_CONTINUE
        monotonic_vals = iter([0.0, 0.5, 0.5, 1.0, 2.5])

        def _fake_event_wait(self: object, timeout: object = None) -> object:
            return None

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
            patch.object(threading.Event, "wait", _fake_event_wait),
            pytest.raises(OpenCodeResumableExitError),
        ):
            _check_process_result(
                cast("ManagedProcess", handle),
                "opencode",
                [],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    liveness_probe=probe,
                    required_artifact=RequiredArtifact(
                        phase="development",
                        artifact_type="development_result",
                        artifact_path=".agent/artifacts/development_result.md",
                        markdown_path=None,
                        normalizer=None,
                    ),
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        parent_exit_grace_seconds=1.0,
                        descendant_wait_timeout_seconds=2.0,
                    ),
                    evaluate_completion_fn=_fake_evaluate_completion,
                ),
            )

        # Descendant wait engaged: more probe calls than grace-only scenario requires.
        # Grace-only: 2 calls (initial classify_exit + grace loop that found child).
        # Descendant wait adds more calls; total must exceed grace-only count.
        _grace_only_probe_calls = 2
        assert probe.call_count > _grace_only_probe_calls, (
            f"Expected >2 probe calls proving descendant wait engaged; got {probe.call_count}"
        )

    def test_artifact_present_at_exit_with_live_children_is_terminal(self, tmp_path: Path) -> None:
        """Current-run receipt at exit time is TERMINAL_COMPLETE even with live children.

        Regression for wt-97: an agent that exits rc=0 with children still alive must not
        be retried when the required artifact is already on disk at exit time.
        signals.required_artifact_present=True takes priority over live-child evidence.

        The legacy on-disk ``.agent/artifacts/<type>.json``-only fallback
        was removed (analysis how_to_fix item 3): a stale canonical
        artifact from a previous run can no longer satisfy the current
        run's completion gate. The hardened contract requires a
        current-run receipt at ``.agent/receipts/<run_id>/<type>.json``.
        """
        run_id = "seam-waits-on-disk-run-id"
        artifact_path = tmp_path / ".agent" / "artifacts" / "development_result.md"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            "---\n"
            "type: development_result\n"
            "status: completed\n"
            "---\n\n"
            "## Summary\n\n- [SUM-1] done\n\n"
            "## Files Changed\n\n- [F-1] src/x.py\n",
            encoding="utf-8",
        )
        receipt_dir = tmp_path / ".agent" / "receipts" / run_id
        receipt_dir.mkdir(parents=True, exist_ok=True)
        (receipt_dir / "development_result.json").write_text(
            f'{{"run_id": "{run_id}", "artifact_type": "development_result"}}',
            encoding="utf-8",
        )

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)
        probe = FakeLivenessProbe(active=True)  # children still running

        _check_process_result(
            cast("ManagedProcess", handle),
            "opencode",
            [],
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                completion_run_id=run_id,
                liveness_probe=probe,
                required_artifact=RequiredArtifact(
                    phase="development",
                    artifact_type="development_result",
                    artifact_path=".agent/artifacts/development_result.md",
                    markdown_path=None,
                    normalizer=None,
                ),
                policy=TimeoutPolicy(
                    idle_timeout_seconds=None,
                    parent_exit_grace_seconds=0.0,
                    descendant_wait_timeout_seconds=0.0,
                ),
            ),
        )
