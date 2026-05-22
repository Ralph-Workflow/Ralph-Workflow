"""Tests for AGY execution contract: completion signaling via ClaudeInteractiveExecutionStrategy.

These tests prove that AGY uses ClaudeInteractiveExecutionStrategy (which enforces
the completion contract) and that clean exit without declare_complete or required
artifact raises OpenCodeResumableExitError.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.execution_state.claude_interactive_execution_strategy import (
    ClaudeInteractiveExecutionStrategy,
)
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    CompletionCheckOptions,
    OpenCodeResumableExitError,
    check_process_result,
)
from ralph.config.enums import AgentTransport
from ralph.phases.required_artifacts import RequiredArtifact
from tests.fake_handle import _FakeHandle

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.process.manager import ManagedProcess


# Local alias: tests call the same public function but under the name matching
# the original private symbol (before the public alias was added to __init__.py).
_check_process_result = check_process_result
_CompletionCheckOptions = CompletionCheckOptions


class TestAgyExecutionContract:
    """Completion contract tests for AGY transport."""

    def test_strategy_for_transport_agy_returns_claude_interactive(self) -> None:
        """Factory maps AgentTransport.AGY to ClaudeInteractiveExecutionStrategy."""
        strategy = strategy_for_transport(AgentTransport.AGY)
        assert isinstance(strategy, ClaudeInteractiveExecutionStrategy)

    def test_clean_exit_without_completion_signal_raises_resumable(
        self, tmp_path: Path
    ) -> None:
        """AGY exit-0 with no declare_complete and no artifact raises OpenCodeResumableExitError."""
        strategy = strategy_for_transport(AgentTransport.AGY)
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            _check_process_result(
                cast("ManagedProcess", handle),
                "agy",
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

    def test_declare_complete_marker_satisfies_completion_contract(
        self, tmp_path: Path
    ) -> None:
        """AGY raw output containing declare_complete marker does not raise."""
        strategy = strategy_for_transport(AgentTransport.AGY)
        handle = _FakeHandle(returncode=0)
        raw_output = ["Task declared complete: session_id=abc, summary=done, timestamp=1"]

        _check_process_result(
            cast("ManagedProcess", handle),
            "agy",
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
            ),
        )

    def test_artifact_on_disk_satisfies_completion_contract(
        self, tmp_path: Path
    ) -> None:
        """AGY with artifact on disk does not raise even without declare_complete."""
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "development_result.json").write_text('{"summary": "done"}')

        strategy = strategy_for_transport(AgentTransport.AGY)
        handle = _FakeHandle(returncode=0)

        _check_process_result(
            cast("ManagedProcess", handle),
            "agy",
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
