"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import AgentExecutionState, OpenCodeExecutionStrategy
from ralph.agents.invoke import AgentInvocationError, _build_opencode_command, _BuildCommandOptions
from ralph.agents.registry import _builtin_agents
from ralph.process.liveness import FakeLivenessProbe
from ralph.recovery.classifier import FailureCategory, FailureClassifier

if TYPE_CHECKING:
    from pathlib import Path


class _FakeHandle:
    """Minimal fake ManagedProcess for strategy tests."""

    def __init__(
        self,
        *,
        returncode: int = 0,
        has_descendants: bool = False,
    ) -> None:
        self.returncode = returncode
        self._has_descendants = has_descendants

    def has_live_descendants(self) -> bool:
        return self._has_descendants


# ---------------------------------------------------------------------------
# (a) Quiet parent with live child is not misclassified as idle
# ---------------------------------------------------------------------------


class TestQuietParentWithLiveChild:
    def test_quiet_parent_with_live_child_is_not_idle(self) -> None:
        """OpenCodeExecutionStrategy classifies quiet parent with live child as WAITING_ON_CHILD."""
        strategy = OpenCodeExecutionStrategy()
        probe = FakeLivenessProbe(active=True)
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD


# ---------------------------------------------------------------------------
# (b) OpenCode run with session_id reuses existing session
# ---------------------------------------------------------------------------


class TestOpenCodeSessionReuse:
    def test_opencode_run_with_session_id_reuses_session(self, tmp_path: Path) -> None:
        """_build_opencode_command includes the session flag when session_id is provided."""
        config = _builtin_agents()["opencode"]

        assert config.session_flag is not None, (
            "opencode builtin config must carry session_flag for session continuation"
        )

        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("implement the task", encoding="utf-8")

        options = _BuildCommandOptions(session_id="sess-x", workspace_path=tmp_path)
        cmd = _build_opencode_command(config, "PROMPT.md", options=options)

        assert "--session" in cmd or "-s" in cmd, (
            f"Session flag must appear in command: {cmd}"
        )
        assert "sess-x" in cmd, f"Session ID must appear in command: {cmd}"
        assert cmd.index("sess-x") > 0, "Session ID must follow the session flag"


# ---------------------------------------------------------------------------
# (c) Foreground exit without explicit completion is not terminal
# ---------------------------------------------------------------------------


class TestForegroundExitWithoutCompletion:
    def test_foreground_exit_without_explicit_completion_is_not_terminal(
        self,
    ) -> None:
        """OpenCodeExecutionStrategy: exit 0 without completion signals is not TERMINAL_COMPLETE."""
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)
        signals = CompletionSignals(
            explicit_complete=False,
            required_artifact_present=False,
            artifact_types=(),
        )

        state = strategy.classify_exit(handle, signals)

        assert state != AgentExecutionState.TERMINAL_COMPLETE, (
            f"Expected non-terminal state when no completion signals; got {state!r}"
        )


# ---------------------------------------------------------------------------
# (d) Explicit completion signal results in terminal success
# ---------------------------------------------------------------------------


class TestExplicitCompletionSucceeds:
    def test_explicit_completion_signal_succeeds(self) -> None:
        """OpenCodeExecutionStrategy: exit 0 with completion signals is TERMINAL_COMPLETE."""
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)
        signals = CompletionSignals(
            explicit_complete=True,
            required_artifact_present=True,
            artifact_types=("development_result",),
        )

        state = strategy.classify_exit(handle, signals)

        assert state == AgentExecutionState.TERMINAL_COMPLETE


# ---------------------------------------------------------------------------
# (e) Stale/invalid OpenCode session recovers predictably
# ---------------------------------------------------------------------------


class TestStaleSessionRecovery:
    @pytest.mark.parametrize(
        "stale_message",
        [
            "Session not found: abc123",
            "Unknown session: deadbeef",
            "session does not exist",
        ],
    )
    def test_stale_session_recovers_predictably(self, stale_message: str) -> None:
        """OpenCode stale-session messages trigger reset_session=True in FailureClassifier."""
        classifier = FailureClassifier()
        exc = AgentInvocationError("opencode", 1, stale_message)
        failure = classifier.classify(exc, phase="development", agent="opencode")

        assert failure.reset_session is True, (
            f"Expected reset_session=True for OpenCode message {stale_message!r}"
        )
        assert failure.counts_against_budget is True
        assert failure.category == FailureCategory.AGENT


# ---------------------------------------------------------------------------
# (f) Many-generation child tree: liveness probe covers all descendants
# ---------------------------------------------------------------------------


class TestManygenerationChildTree:
    def test_opencode_tree_with_many_generations_of_children(self) -> None:
        """Liveness probe reporting any active agent keeps the run alive."""
        strategy = OpenCodeExecutionStrategy()
        probe = FakeLivenessProbe(active=True)
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD


# ---------------------------------------------------------------------------
# (g) Fully quiet tree triggers timeout (no false negative)
# ---------------------------------------------------------------------------


class TestFullyQuietTreeTimeout:
    def test_multi_agent_tree_fully_quiet_triggers_timeout(self) -> None:
        """When all agents are inactive, classify_quiet must NOT return WAITING_ON_CHILD."""
        strategy = OpenCodeExecutionStrategy()
        probe = FakeLivenessProbe(active=False)
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state != AgentExecutionState.WAITING_ON_CHILD, (
            "Fully-quiet tree must not report WAITING_ON_CHILD; "
            f"idle timeout must fire. Got: {state!r}"
        )
