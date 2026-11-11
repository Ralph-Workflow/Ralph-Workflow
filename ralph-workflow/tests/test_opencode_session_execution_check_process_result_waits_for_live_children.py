from __future__ import annotations

import threading
import time as _time_module
from typing import TYPE_CHECKING
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


_DESCENDANT_WAIT_POLL_SECONDS = 0.5


class TestCheckProcessResultWaitsForLiveChildren:
    def test_raises_resumable_exit_when_wait_times_out_without_artifact(
        self, tmp_path: Path
    ) -> None:
        probe = FakeLivenessProbe(active=True)

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

        call_count = [0]
        monotonic_vals = iter([0.0, 0.1, 0.2])

        original_event_wait = threading.Event.wait

        def _fake_event_wait(self: object, timeout: object = None) -> object:
            if timeout is not None and timeout == _DESCENDANT_WAIT_POLL_SECONDS:
                call_count[0] += 1
                return None
            return original_event_wait(self, timeout)

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
            patch.object(threading.Event, "wait", _fake_event_wait),
            pytest.raises(OpenCodeResumableExitError),
        ):
            check_process_result(
                handle,
                "opencode",
                [],
                CompletionCheckOptions(
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
            check_process_result(
                handle,
                "opencode",
                [],
                CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    liveness_probe=probe,
                    required_artifact=RequiredArtifact(
                        phase="planning",
                        artifact_type="plan",
                        artifact_path=".agent/artifacts/plan.md",
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
        probe = FakeLivenessProbe(active=True)

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

        monotonic_vals = iter([0.0, 0.05, 0.1, 0.15])
        poll_count = [0]

        def _fake_event_wait(self: object, timeout: object = None) -> object:
            if timeout is not None and timeout > 0:
                poll_count[0] += 1
            return None

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
            patch.object(threading.Event, "wait", _fake_event_wait),
        ):
            result = wait_for_descendants_then_recheck(
                handle,
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

        assert result == AgentExecutionState.RESUMABLE_CONTINUE, (
            f"Expected RESUMABLE_CONTINUE after timeout with children alive; got {result!r}"
        )

    def test_artifact_appears_during_wait_produces_terminal_complete(self, tmp_path: Path) -> None:
        probe = FakeLivenessProbe(active=True)
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)

        call_count = [0]
        _artifact_appears_on = 3

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
                return CompletionSignals(
                    False,
                    True,
                    ("development_result",),
                    completion_sentinel_present=True,
                    artifact_required=True,
                )
            return CompletionSignals(False, False, ())

        check_process_result(
            handle,
            "opencode",
            [],
            CompletionCheckOptions(
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

    def test_explicit_complete_appears_during_wait_produces_terminal_complete(
        self, tmp_path: Path
    ) -> None:
        probe = FakeLivenessProbe(active=True)
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)

        call_count = [0]
        _artifact_appears_on = 3

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
                return CompletionSignals(True, False, (), completion_sentinel_present=True)
            return CompletionSignals(False, False, ())

        check_process_result(
            handle,
            "opencode",
            [],
            CompletionCheckOptions(
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

    def test_descendants_finish_during_wait_produces_terminal_complete(
        self, tmp_path: Path
    ) -> None:
        probe = FakeLivenessProbe(active=False)
        strategy = OpenCodeExecutionStrategy()

        descendants_alive = [True]

        class _TrackingFakeHandle:
            returncode = 0

            def has_live_descendants(self) -> bool:
                return descendants_alive[0]

        handle = _TrackingFakeHandle()

        call_count = [0]
        _artifact_appears_on = 3

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
                return CompletionSignals(
                    False,
                    True,
                    ("development_result",),
                    completion_sentinel_present=True,
                    artifact_required=True,
                )
            return CompletionSignals(False, False, ())

        check_process_result(
            handle,
            "opencode",
            [],
            CompletionCheckOptions(
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

    def test_wait_helper_timeout_then_final_recheck_finds_completion(self, tmp_path: Path) -> None:
        probe = FakeLivenessProbe(active=True)
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)

        call_count = [0]
        _artifact_appears_on = 2

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
                return CompletionSignals(
                    False,
                    True,
                    ("development_result",),
                    completion_sentinel_present=True,
                    artifact_required=True,
                )
            return CompletionSignals(False, False, ())

        monotonic_vals = iter([0.0, 0.0, 0.5])

        def _fake_event_wait(self: object, timeout: object = None) -> object:
            return None

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
            patch.object(threading.Event, "wait", _fake_event_wait),
        ):
            result = wait_for_descendants_then_recheck(
                handle,
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

        assert result == AgentExecutionState.TERMINAL_COMPLETE, (
            f"Expected TERMINAL_COMPLETE from final recheck; got {result!r}"
        )

    def test_grace_window_catches_artifact_appearing_after_exit_with_no_children(
        self, tmp_path: Path
    ) -> None:
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
            return CompletionSignals(
                False,
                True,
                ("development_result",),
                completion_sentinel_present=True,
                artifact_required=True,
            )

        monotonic_vals = iter([0.0, 0.5, 1.0])

        def _fake_event_wait(self: object, timeout: object = None) -> None:
            pass

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
            patch.object(threading.Event, "wait", _fake_event_wait),
        ):
            check_process_result(
                handle,
                "opencode",
                [],
                CompletionCheckOptions(
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

    def test_grace_window_raises_resumable_when_no_signal_and_no_children(
        self, tmp_path: Path
    ) -> None:
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
            check_process_result(
                handle,
                "opencode",
                [],
                CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    liveness_probe=probe,
                    required_artifact=RequiredArtifact(
                        phase="planning",
                        artifact_type="plan",
                        artifact_path=".agent/artifacts/plan.md",
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

        class _FlippingProbe:
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

        monotonic_vals = iter([0.0, 0.5, 0.5, 1.0, 2.5])

        def _fake_event_wait(self: object, timeout: object = None) -> object:
            return None

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
            patch.object(threading.Event, "wait", _fake_event_wait),
            pytest.raises(OpenCodeResumableExitError),
        ):
            check_process_result(
                handle,
                "opencode",
                [],
                CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    liveness_probe=probe,
                    required_artifact=RequiredArtifact(
                        phase="planning",
                        artifact_type="plan",
                        artifact_path=".agent/artifacts/plan.md",
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

        _grace_only_probe_calls = 2
        assert probe.call_count > _grace_only_probe_calls, (
            f"Expected >2 probe calls proving descendant wait engaged; got {probe.call_count}"
        )

    def test_completed_artifact_at_exit_with_live_children_is_terminal(
        self, tmp_path: Path
    ) -> None:
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
        (tmp_path / ".agent" / f"completion_seen_{run_id}.json").write_text(
            f'{{"run_id": "{run_id}"}}',
            encoding="utf-8",
        )

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)
        probe = FakeLivenessProbe(active=True)

        check_process_result(
            handle,
            "opencode",
            [],
            CompletionCheckOptions(
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
