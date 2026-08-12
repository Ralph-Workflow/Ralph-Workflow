"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
from tests.fake_handle import _FakeHandle

if TYPE_CHECKING:
    from pathlib import Path


# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).


class TestCheckProcessResultClaudeInteractiveSeam:
    """Completion contract with ClaudeInteractiveExecutionStrategy."""

    def test_explicit_completion_with_sentinel_does_not_raise(self, tmp_path: Path) -> None:
        """declare_complete marker plus completion sentinel is terminal without artifact.

        The plain-text marker alone is no longer authoritative: it can be spoofed
        by ordinary agent output. The completion sentinel written by the real
        declare_complete MCP tool provides the required corroboration.
        """
        strategy = ClaudeInteractiveExecutionStrategy()
        handle = _FakeHandle(returncode=0)
        raw_output = ["Task declared complete: session_id=abc, summary=done, timestamp=1"]
        sentinel = tmp_path / ".agent" / "completion_seen_abc.json"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text('{"run_id": "abc"}', encoding="utf-8")

        check_process_result(
            handle,
            "claude",
            raw_output,
            CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                captured_session_id="abc",
            ),
        )

    def test_no_artifact_requirement_still_requires_explicit_completion(
        self, tmp_path: Path
    ) -> None:
        """ClaudeInteractiveExecutionStrategy still requires an explicit completion signal."""
        strategy = ClaudeInteractiveExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            check_process_result(
                handle,
                "claude",
                [],
                CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
                ),
            )

    def test_required_receipt_needs_completion_sentinel(self, tmp_path: Path) -> None:
        """Interactive Claude applies the receipt-plus-sentinel conjunction.

        A stale canonical artifact from a previous run cannot satisfy the
        current run's completion gate. The hardened contract requires a
        current-run receipt plus the explicit completion sentinel. This test
        writes the legacy receipt file specifically to exercise the supported
        DB-to-file read fallback.
        """
        run_id = "seam-claude-on-disk-run-id"
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

        strategy = ClaudeInteractiveExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        options = CompletionCheckOptions(
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
            check_process_result(
                handle,
                "claude",
                [],
                options,
            )

        sentinel = tmp_path / ".agent" / f"completion_seen_{run_id}.json"
        sentinel.write_text(f'{{"run_id": "{run_id}"}}', encoding="utf-8")
        check_process_result(
            handle,
            "claude",
            [],
            options,
        )

    def test_neither_signal_nor_artifact_raises_resumable_exit(self, tmp_path: Path) -> None:
        """Missing sentinel and required receipt produces a resumable exit."""
        strategy = ClaudeInteractiveExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            check_process_result(
                handle,
                "claude",
                [],
                CompletionCheckOptions(
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
