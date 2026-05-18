"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations
from tests.fake_handle import _FakeHandle

import threading
import time as _time_module
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    OpenCodeExecutionStrategy,
)
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    CompletionCheckOptions,
    OpenCodeResumableExitError,
    check_process_result,
)
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.process.liveness import FakeLivenessProbe

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.process.manager import ManagedProcess


# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).
_check_process_result = check_process_result
_CompletionCheckOptions = CompletionCheckOptions


class TestOptionalArtifactCompletion:
    """Optional-artifact phases terminal on clean exit; required phases enforce presence."""



    def test_optional_artifact_absent_with_declare_complete_does_not_raise(
        self, tmp_path: Path
    ) -> None:
        """Optional artifact absent + declare_complete must not raise OpenCodeResumableExitError."""
        ra = RequiredArtifact(
            phase="development",
            artifact_type="development_result",
            json_path=".agent/artifacts/development_result.json",
            markdown_path=None,
            normalizer=None,
            artifact_required=False,
        )
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=False)
        probe = FakeLivenessProbe(active=False)
        output = ['{"type": "result", "message": "Task declared complete: done"}']

        _check_process_result(
            cast("ManagedProcess", handle),
            "opencode",
            output,
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                liveness_probe=probe,
                required_artifact=ra,
                policy=TimeoutPolicy(
                    idle_timeout_seconds=None,
                    parent_exit_grace_seconds=0.0,
                    descendant_wait_timeout_seconds=0.0,
                ),
            ),
        )

    def test_optional_artifact_absent_without_evidence_is_terminal(self, tmp_path: Path) -> None:
        """Optional artifact absent with no evidence must be terminal (not resumable).

        This test exercises the general optional-artifact execution contract using
        a custom RequiredArtifact(artifact_required=False); it does not represent
        the default development phase policy where development_result is required.
        A clean exit (0) with an optional artifact contract is terminal success even
        when the agent produces no artifact and makes no declare_complete call.
        """
        ra = RequiredArtifact(
            phase="development",
            artifact_type="development_result",
            json_path=".agent/artifacts/development_result.json",
            markdown_path=None,
            normalizer=None,
            artifact_required=False,
        )
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=False)
        probe = FakeLivenessProbe(active=False)

        _check_process_result(
            cast("ManagedProcess", handle),
            "opencode",
            [],
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                liveness_probe=probe,
                required_artifact=ra,
                policy=TimeoutPolicy(
                    idle_timeout_seconds=None,
                    parent_exit_grace_seconds=0.0,
                    descendant_wait_timeout_seconds=0.0,
                ),
            ),
        )

    def test_optional_artifact_malformed_present_does_not_raise(self, tmp_path: Path) -> None:
        """Optional artifact present but malformed must not raise OpenCodeResumableExitError.

        _check_process_result is not responsible for validating artifact content.
        When artifact_required=False the exit is terminal regardless of what the
        file contains; content validation is the execution.py layer's job.
        """
        artifact_path = tmp_path / ".agent" / "artifacts" / "development_result.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("not valid json {{{")

        ra = RequiredArtifact(
            phase="development",
            artifact_type="development_result",
            json_path=".agent/artifacts/development_result.json",
            markdown_path=None,
            normalizer=None,
            artifact_required=False,
        )
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=False)
        probe = FakeLivenessProbe(active=False)

        _check_process_result(
            cast("ManagedProcess", handle),
            "opencode",
            [],
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                liveness_probe=probe,
                required_artifact=ra,
                policy=TimeoutPolicy(
                    idle_timeout_seconds=None,
                    parent_exit_grace_seconds=0.0,
                    descendant_wait_timeout_seconds=0.0,
                ),
            ),
        )

    def test_required_artifact_absent_still_raises_resumable(
        self, tmp_path: Path
    ) -> None:
        """Required artifact absent without evidence still raises OpenCodeResumableExitError."""
        ra = RequiredArtifact(
            phase="development_analysis",
            artifact_type="development_analysis_decision",
            json_path=".agent/artifacts/development_analysis_decision.json",
            markdown_path=None,
            normalizer=None,
            artifact_required=True,
        )

        def _fake_evaluate_completion(
            workspace: object, raw_output: object, *, required_artifact: object = None
        ) -> object:
            return CompletionSignals(False, False, ())

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=False)
        probe = FakeLivenessProbe(active=False)

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
                    required_artifact=ra,
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        parent_exit_grace_seconds=1.0,
                        descendant_wait_timeout_seconds=30.0,
                    ),
                    evaluate_completion_fn=_fake_evaluate_completion,
                ),
            )


