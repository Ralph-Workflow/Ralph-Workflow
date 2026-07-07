"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.execution_state import (
    OpenCodeExecutionStrategy,
)
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    AgentInvocationError,
    CompletionCheckOptions,
    OpenCodeResumableExitError,
    PiContextExhaustedExitError,
    check_process_result,
)
from ralph.agents.invoke import _completion as completion_module
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

    def test_explicit_completion_with_sentinel_does_not_raise(self, tmp_path: Path) -> None:
        """declare_complete marker plus completion sentinel is terminal without artifact.

        The plain-text marker alone is no longer authoritative: it can be spoofed
        by ordinary agent output. The completion sentinel written by the real
        declare_complete MCP tool provides the required corroboration.
        """
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)
        raw_output = ["Task declared complete: session_id=abc, summary=done, timestamp=1"]
        sentinel = tmp_path / ".agent" / "completion_seen_abc.json"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text('{"run_id": "abc"}', encoding="utf-8")

        _check_process_result(
            cast("ManagedProcess", handle),
            "opencode",
            raw_output,
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                captured_session_id="abc",
            ),
        )
        # No exception raised means explicit_complete + sentinel → TERMINAL_COMPLETE

    def test_retryable_nonzero_exit_does_not_log_terminal_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=1)
        handle.stderr = io.StringIO("Model returned an empty response with no tool calls")
        seen_errors: list[tuple[object, ...]] = []
        seen_warnings: list[tuple[object, ...]] = []
        monkeypatch.setattr(
            completion_module.logger,
            "error",
            lambda *args, **kwargs: seen_errors.append(args),
        )
        monkeypatch.setattr(
            completion_module.logger,
            "warning",
            lambda *args, **kwargs: seen_warnings.append(args),
        )

        with pytest.raises(AgentInvocationError):
            _check_process_result(
                cast("ManagedProcess", handle),
                "opencode",
                ['{"type":"tool_result","tool":"read_file"}'],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                ),
            )

        assert seen_errors == []
        assert seen_warnings != []

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
        """Current-run receipt produces TERMINAL_COMPLETE without declare_complete.

        The legacy on-disk ``.agent/artifacts/<type>.json``-only fallback
        was removed (analysis how_to_fix item 3): a stale canonical
        artifact from a previous run can no longer satisfy the current
        run's completion gate. The hardened contract requires a
        current-run receipt at ``.agent/receipts/<run_id>/<type>.json``.
        """
        run_id = "seam-opencode-on-disk-run-id"
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "development_result.json").write_text('{"summary": "done"}')
        receipt_dir = tmp_path / ".agent" / "receipts" / run_id
        receipt_dir.mkdir(parents=True)
        (receipt_dir / "development_result.json").write_text(
            f'{{"run_id": "{run_id}", "artifact_type": "development_result"}}',
            encoding="utf-8",
        )

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        _check_process_result(
            cast("ManagedProcess", handle),
            "opencode",
            [],  # no declare_complete marker
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                completion_run_id=run_id,
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

    def test_pi_length_stop_without_artifact_raises_context_exhausted(
        self, tmp_path: Path
    ) -> None:
        """Pi stopReason=length is a context-exhaustion signal, not an artifact retry."""
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)
        raw_output = [
            (
                '{"type":"message_update","message":{"role":"assistant"},'
                '"assistantMessageEvent":{"type":"done","stopReason":"length"}}'
            )
        ]

        with pytest.raises(PiContextExhaustedExitError) as excinfo:
            _check_process_result(
                cast("ManagedProcess", handle),
                "pi/zai/glm-5.2",
                raw_output,
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

        assert excinfo.value.skip_same_agent_retries is True

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
