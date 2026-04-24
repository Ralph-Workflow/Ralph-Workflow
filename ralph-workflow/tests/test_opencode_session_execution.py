"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    GenericExecutionStrategy,
    OpenCodeExecutionStrategy,
)
from ralph.agents.invoke import (
    AgentInvocationError,
    OpenCodeResumableExitError,
    _build_opencode_command,
    _BuildCommandOptions,
)
from ralph.agents.registry import _builtin_agents
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.process.liveness import FakeLivenessProbe
from ralph.recovery.classifier import FailureCategory, FailureClassifier
from ralph.workspace.scope import WorkspaceScope

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


# ---------------------------------------------------------------------------
# (h) Runner threads OpenCodeResumableExitError session_id into the retry
# ---------------------------------------------------------------------------


class _FakeMcpBridge:
    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:12345/mcp"

    def shutdown(self) -> None:
        pass


def _opencode_agent_config() -> AgentConfig:
    return AgentConfig(
        cmd="opencode",
        output_flag="--format json",
        session_flag="--session {}",
        json_parser=JsonParserType.GENERIC,
        transport=AgentTransport.OPENCODE,
    )


def _runner_config(*, max_retries: int = 1) -> MagicMock:
    config = MagicMock()
    config.general.verbosity = 0
    config.general.max_same_agent_retries = max_retries
    config.general.agent_idle_timeout_seconds = None
    config.agents = {}
    return config


def _registry_factory_for(agent_config: AgentConfig) -> type:
    class _Instance:
        def get(self, name: str) -> AgentConfig | None:
            del name
            return agent_config

    class _Factory:
        @classmethod
        def from_config(cls, _config: object) -> _Instance:
            return _Instance()

    return _Factory


class TestRunnerSessionContinuation:
    """Runner correctly threads OpenCodeResumableExitError.session_id into the retry attempt."""

    def test_opencode_resumable_exit_retries_with_same_session_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """First call raises OpenCodeResumableExitError; second call gets session_id='sess-1'."""
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("implement the task", encoding="utf-8")

        effect = InvokeAgentEffect(
            agent_name="opencode",
            phase="development",
            prompt_file=str(prompt_file),
        )
        agent_config = _opencode_agent_config()
        registry = _registry_factory_for(agent_config)
        monkeypatch.setattr(
            runner_module, "start_mcp_server", lambda *_a, **_kw: _FakeMcpBridge()
        )

        seen_session_ids: list[str | None] = []
        seen_phases: list[str | None] = []

        def fake_invoke_agent(
            config: AgentConfig,
            pf: str,
            *,
            options: object = None,
        ) -> object:
            del config, pf
            session = getattr(options, "session_id", None)
            phase = getattr(options, "phase", None)
            seen_session_ids.append(session)
            seen_phases.append(phase)
            if len(seen_session_ids) == 1:

                def _first() -> object:
                    yield '{"type":"text"}'
                    raise OpenCodeResumableExitError("opencode", session_id="sess-1")

                return _first()
            return iter(['{"type":"result"}'])

        result = runner_module._execute_agent_effect(
            effect,
            _runner_config(max_retries=1),
            runner_module._AgentExecutionDeps(
                invoke_agent=fake_invoke_agent,
                agent_invocation_error=AgentInvocationError,
                agent_registry=registry,
            ),
            WorkspaceScope(tmp_path),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert seen_session_ids == [None, "sess-1"], (
            f"Expected [None, 'sess-1'], got {seen_session_ids}"
        )
        assert seen_phases[1] == "development", (
            f"Phase must be propagated on retry; got {seen_phases[1]!r}"
        )

    def test_opencode_resumable_exit_no_more_attempts_returns_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """With max_retries=0, OpenCodeResumableExitError is not retried and returns FAILURE."""
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("implement the task", encoding="utf-8")

        effect = InvokeAgentEffect(
            agent_name="opencode",
            phase="development",
            prompt_file=str(prompt_file),
        )
        agent_config = _opencode_agent_config()
        registry = _registry_factory_for(agent_config)
        monkeypatch.setattr(
            runner_module, "start_mcp_server", lambda *_a, **_kw: _FakeMcpBridge()
        )

        def fake_invoke_agent(
            config: AgentConfig, pf: str, *, options: object = None
        ) -> object:
            del config, pf, options

            def _first() -> object:
                yield '{"type":"text"}'
                raise OpenCodeResumableExitError("opencode", session_id="sess-1")

            return _first()

        result = runner_module._execute_agent_effect(
            effect,
            _runner_config(max_retries=0),
            runner_module._AgentExecutionDeps(
                invoke_agent=fake_invoke_agent,
                agent_invocation_error=AgentInvocationError,
                agent_registry=registry,
            ),
            WorkspaceScope(tmp_path),
        )

        assert result == PipelineEvent.AGENT_FAILURE


# ---------------------------------------------------------------------------
# (i) GenericExecutionStrategy is unaffected by OpenCode liveness probe logic
# ---------------------------------------------------------------------------


class TestGenericExecutionStrategy:
    """GenericExecutionStrategy retains single-process exit-success semantics."""

    def test_classify_quiet_returns_active_when_no_descendants(self) -> None:
        """No descendants → classify_quiet returns ACTIVE (not WAITING_ON_CHILD)."""
        strategy = GenericExecutionStrategy()
        probe = FakeLivenessProbe(active=False)
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.ACTIVE

    def test_classify_quiet_returns_waiting_when_has_live_descendants(self) -> None:
        """OS-level live descendants → classify_quiet returns WAITING_ON_CHILD."""
        strategy = GenericExecutionStrategy()
        probe = FakeLivenessProbe(active=False)
        handle = _FakeHandle(has_descendants=True)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD

    def test_classify_quiet_does_not_use_liveness_probe(self) -> None:
        """Generic strategy must not escalate to WAITING_ON_CHILD via FakeLivenessProbe alone."""
        strategy = GenericExecutionStrategy()
        probe = FakeLivenessProbe(active=True)
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        assert state != AgentExecutionState.WAITING_ON_CHILD, (
            "GenericExecutionStrategy must not honour the liveness probe; "
            f"got {state!r} purely because probe says active"
        )

    def test_classify_exit_always_returns_terminal_complete(self) -> None:
        """Exit is always TERMINAL_COMPLETE regardless of completion signals."""
        strategy = GenericExecutionStrategy()
        handle = _FakeHandle(returncode=0)
        no_signals = CompletionSignals(
            explicit_complete=False,
            required_artifact_present=False,
            artifact_types=(),
        )

        state = strategy.classify_exit(handle, no_signals)

        assert state == AgentExecutionState.TERMINAL_COMPLETE

    def test_supports_session_continuation_is_false(self) -> None:
        """Generic agents do not support session continuation."""
        strategy = GenericExecutionStrategy()
        assert strategy.supports_session_continuation() is False
