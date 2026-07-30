"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

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
            handle,
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
                handle,
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
                handle,
                "opencode",
                [],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
                ),
            )

    def test_required_receipt_needs_completion_sentinel(
        self, tmp_path: Path
    ) -> None:
        """A current-run receipt is necessary but not sufficient for completion.

        The legacy on-disk ``.agent/artifacts/<type>.json``-only fallback
        was removed (analysis how_to_fix item 3): a stale canonical
        artifact from a previous run can no longer satisfy the current
        run's completion gate. The hardened contract requires both the
        current-run receipt and the explicit completion sentinel.
        """
        run_id = "seam-opencode-on-disk-run-id"
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "development_result.md").write_text(
            "---\n"
            "type: development_result\n"
            "status: completed\n"
            "---\n\n"
            "## Summary\n\n- [SUM-1] done\n\n"
            "## Files Changed\n\n- [F-1] src/x.py\n",
            encoding="utf-8",
        )
        receipt_dir = tmp_path / ".agent" / "receipts" / run_id
        receipt_dir.mkdir(parents=True)
        (receipt_dir / "development_result.json").write_text(
            f'{{"run_id": "{run_id}", "artifact_type": "development_result"}}',
            encoding="utf-8",
        )

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        options = _CompletionCheckOptions(
            execution_strategy=strategy,
            workspace_path=tmp_path,
            completion_run_id=run_id,
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
            ),
        )

        with pytest.raises(OpenCodeResumableExitError):
            _check_process_result(
                handle,
                "opencode",
                [],
                options,
            )

        sentinel = tmp_path / ".agent" / f"completion_seen_{run_id}.json"
        sentinel.write_text(f'{{"run_id": "{run_id}"}}', encoding="utf-8")
        _check_process_result(
            handle,
            "opencode",
            [],
            options,
        )

    def test_neither_signal_nor_artifact_raises_resumable_exit(self, tmp_path: Path) -> None:
        """Missing sentinel and required receipt produces a resumable exit."""
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            _check_process_result(
                handle,
                "opencode",
                [],  # no declare_complete marker
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    required_artifact=RequiredArtifact(
                        phase="development",
                        artifact_type="development_result",
                        artifact_path=".agent/artifacts/development_result.md",
                        markdown_path=None,
                        normalizer=None,
                    ),
                    policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
                ),
            )

    def test_pi_length_stop_without_artifact_raises_context_exhausted(self, tmp_path: Path) -> None:
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
                handle,
                "pi/zai/glm-5.2",
                raw_output,
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    required_artifact=RequiredArtifact(
                        phase="development",
                        artifact_type="development_result",
                        artifact_path=".agent/artifacts/development_result.md",
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
                handle,
                "opencode",
                [],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    required_artifact=RequiredArtifact(
                        phase="development",
                        artifact_type="development_result",
                        artifact_path=".agent/artifacts/development_result.md",
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
                handle,
                "opencode",
                [],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    required_artifact=RequiredArtifact(
                        phase="development",
                        artifact_type="development_result",
                        artifact_path=".agent/artifacts/development_result.md",
                        markdown_path=None,
                        normalizer=None,
                    ),
                    policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
                ),
            )

    def test_optional_artifact_absent_without_sentinel_raises(
        self, tmp_path: Path
    ) -> None:
        """Optional artifact policy relaxes the receipt, not declaration."""
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            _check_process_result(
                handle,
                "opencode",
                [],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    required_artifact=RequiredArtifact(
                        phase="development",
                        artifact_type="development_result",
                        artifact_path=".agent/artifacts/development_result.md",
                        markdown_path=None,
                        normalizer=None,
                        artifact_required=False,
                    ),
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        parent_exit_grace_seconds=0.0,
                    ),
                ),
            )

    def test_optional_artifact_malformed_without_sentinel_raises_at_completion_check(
        self, tmp_path: Path
    ) -> None:
        """A malformed optional file cannot replace the mandatory sentinel."""
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "development_result.json").write_text("not-valid-json")

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            _check_process_result(
                handle,
                "opencode",
                [],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    required_artifact=RequiredArtifact(
                        phase="development",
                        artifact_type="development_result",
                        artifact_path=".agent/artifacts/development_result.md",
                        markdown_path=None,
                        normalizer=None,
                        artifact_required=False,
                    ),
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        parent_exit_grace_seconds=0.0,
                    ),
                ),
            )
