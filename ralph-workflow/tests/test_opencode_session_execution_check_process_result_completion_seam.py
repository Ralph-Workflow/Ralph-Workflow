"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

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


class TestCheckProcessResultCompletionSeam:
    """_check_process_result end-to-end completion contract with OpenCodeExecutionStrategy."""

    def test_explicit_completion_without_artifact_does_not_raise(self, tmp_path: Path) -> None:
        """declare_complete marker prevents OpenCodeResumableExitError without artifact."""
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)
        raw_output = ["Task declared complete: session_id=abc, summary=done, timestamp=1"]

        _check_process_result(
            cast("ManagedProcess", handle),
            "opencode",
            raw_output,
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
            ),
        )
        # No exception raised means explicit_complete=True → TERMINAL_COMPLETE

    def test_no_artifact_requirement_still_requires_explicit_completion(
        self, tmp_path: Path
    ) -> None:
        """OpenCode without a required artifact must still raise unless it declares complete."""
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            _check_process_result(
                cast("ManagedProcess", handle),
                "opencode",
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

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        _check_process_result(
            cast("ManagedProcess", handle),
            "opencode",
            [],  # no declare_complete marker
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
        # No exception raised means required_artifact_present=True → TERMINAL_COMPLETE

    def test_neither_signal_nor_artifact_raises_resumable_exit(self, tmp_path: Path) -> None:
        """No explicit completion and no artifact -> OpenCodeResumableExitError."""
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            _check_process_result(
                cast("ManagedProcess", handle),
                "opencode",
                [],  # no declare_complete marker
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

    def test_malformed_json_artifact_raises_resumable_exit(self, tmp_path: Path) -> None:
        """An artifact that cannot be parsed as JSON must NOT set required_artifact_present."""
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "development_result.json").write_text("not-valid-json")

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            _check_process_result(
                cast("ManagedProcess", handle),
                "opencode",
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

    def test_empty_json_object_artifact_raises_resumable_exit(self, tmp_path: Path) -> None:
        """An empty JSON dict artifact must NOT set required_artifact_present."""
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "development_result.json").write_text("{}")

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            _check_process_result(
                cast("ManagedProcess", handle),
                "opencode",
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

    def test_optional_artifact_absent_does_not_raise(self, tmp_path: Path) -> None:
        """OpenCode rc=0 without artifact is terminal when artifact_required=False.

        When a phase has an optional artifact contract (artifact_required=False), a clean
        exit is terminal even without the artifact or a declare_complete signal. The
        completion check must not raise OpenCodeResumableExitError in this case.
        """
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        _check_process_result(
            cast("ManagedProcess", handle),
            "opencode",
            [],  # no declare_complete marker
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                required_artifact=RequiredArtifact(
                    phase="development",
                    artifact_type="development_result",
                    json_path=".agent/artifacts/development_result.json",
                    markdown_path=None,
                    normalizer=None,
                    artifact_required=False,
                ),
                policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
            ),
        )
        # No exception raised: artifact_optional=True -> TERMINAL_COMPLETE

    def test_optional_artifact_malformed_does_not_raise_at_completion_check(
        self, tmp_path: Path
    ) -> None:
        """OpenCode rc=0 with a malformed optional artifact is still terminal at completion layer.

        When artifact_required=False, artifact_optional=True is set regardless of whether
        the artifact file exists or is valid. The completion check returns TERMINAL_COMPLETE.
        Malformed-artifact validation is the execution phase's responsibility, not the
        completion check's.
        """
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "development_result.json").write_text("not-valid-json")

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        _check_process_result(
            cast("ManagedProcess", handle),
            "opencode",
            [],  # no declare_complete marker
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                required_artifact=RequiredArtifact(
                    phase="development",
                    artifact_type="development_result",
                    json_path=".agent/artifacts/development_result.json",
                    markdown_path=None,
                    normalizer=None,
                    artifact_required=False,
                ),
                policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
            ),
        )
