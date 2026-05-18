"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.execution_state import (
    ClaudeInteractiveExecutionStrategy,
)
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    CompletionCheckOptions,
    OpenCodeResumableExitError,
    check_process_result,
)
from ralph.phases.required_artifacts import RequiredArtifact

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.process.manager import ManagedProcess


# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).
_check_process_result = check_process_result
_CompletionCheckOptions = CompletionCheckOptions


class TestCheckProcessResultClaudeInteractiveSeam:
    """Completion contract with ClaudeInteractiveExecutionStrategy."""

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

    def test_explicit_completion_without_artifact_does_not_raise(self, tmp_path: Path) -> None:
        """declare_complete marker prevents OpenCodeResumableExitError without artifact."""
        strategy = ClaudeInteractiveExecutionStrategy()
        handle = _FakeHandle(returncode=0)
        raw_output = ["Task declared complete: session_id=abc, summary=done, timestamp=1"]

        _check_process_result(
            cast("ManagedProcess", handle),
            "claude",
            raw_output,
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
            ),
        )

    def test_no_artifact_requirement_still_requires_explicit_completion(
        self, tmp_path: Path
    ) -> None:
        """ClaudeInteractiveExecutionStrategy still requires an explicit completion signal."""
        strategy = ClaudeInteractiveExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            _check_process_result(
                cast("ManagedProcess", handle),
                "claude",
                [],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
                ),
            )

    def test_artifact_present_without_explicit_completion_does_not_raise(
        self, tmp_path: Path
    ) -> None:
        """Artifact on disk produces TERMINAL_COMPLETE without declare_complete."""
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "development_result.json").write_text('{"summary": "done"}')

        strategy = ClaudeInteractiveExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        _check_process_result(
            cast("ManagedProcess", handle),
            "claude",
            [],
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                required_artifact=RequiredArtifact(
                    phase="development",
                    artifact_type="development_result",
                    json_path=".agent/artifacts/development_result.json",
                    markdown_path=None,
                    normalizer=None,
                ),
            ),
        )

    def test_neither_signal_nor_artifact_raises_resumable_exit(self, tmp_path: Path) -> None:
        """No explicit completion and no artifact -> OpenCodeResumableExitError."""
        strategy = ClaudeInteractiveExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            _check_process_result(
                cast("ManagedProcess", handle),
                "claude",
                [],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    required_artifact=RequiredArtifact(
                        phase="development",
                        artifact_type="development_result",
                        json_path=".agent/artifacts/development_result.json",
                        markdown_path=None,
                        normalizer=None,
                    ),
                    policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
                ),
            )


_FakeHandle = TestCheckProcessResultClaudeInteractiveSeam._FakeHandle
