"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

import threading
import time as _time_module
from itertools import chain, repeat
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from ralph.agents.completion_signals import CompletionSignals, extract_explicit_completion
from ralph.agents.execution_state import (
    AgentExecutionState,
    GenericExecutionStrategy,
    OpenCodeExecutionStrategy,
)
from ralph.agents.invoke import (
    AgentInvocationError,
    InvokeOptions,
    OpenCodeResumableExitError,
    _build_opencode_command,
    _BuildCommandOptions,
    _check_process_result,
    _CompletionCheckOptions,
    _IdleStreamTimeoutError,
    _read_lines_from_process,
)
from ralph.agents.registry import _builtin_agents
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.process.liveness import FakeLivenessProbe
from ralph.recovery.classifier import FailureCategory, FailureClassifier
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Iterator
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


class _RegistryInstance:
    def __init__(self, agent_config: AgentConfig) -> None:
        self._agent_config = agent_config

    def get(self, name: str) -> AgentConfig | None:
        del name
        return self._agent_config


class _RegistryFactory:
    _agent_config: AgentConfig

    @classmethod
    def from_config(cls, config: UnifiedConfig) -> _RegistryInstance:
        del config
        return _RegistryInstance(cls._agent_config)


def _registry_factory_for(agent_config: AgentConfig) -> type[_RegistryFactory]:
    class _Configured(_RegistryFactory):
        pass

    _Configured._agent_config = agent_config
    return _Configured


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
            prompt_file: str,
            *,
            options: InvokeOptions | None = None,
        ) -> Iterator[object]:
            del config, prompt_file
            seen_session_ids.append(options.session_id if options is not None else None)
            seen_phases.append(options.phase if options is not None else None)
            if len(seen_session_ids) == 1:

                def _first() -> Iterator[object]:
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
            config: AgentConfig,
            prompt_file: str,
            *,
            options: InvokeOptions | None = None,
        ) -> Iterator[object]:
            del config, prompt_file, options

            def _first() -> Iterator[object]:
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


# ---------------------------------------------------------------------------
# (j) _check_process_result: explicit completion / artifact / neither
# ---------------------------------------------------------------------------


class TestCheckProcessResultCompletionSeam:
    """_check_process_result end-to-end completion contract with OpenCodeExecutionStrategy."""

    def test_explicit_completion_without_artifact_does_not_raise(self, tmp_path: Path) -> None:
        """declare_complete in output prevents OpenCodeResumableExitError even without artifact."""
        # development phase requires .agent/artifacts/development_result.json; not created here.
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)
        raw_output = ["Task declared complete: session_id=abc, summary=done, timestamp=1"]

        _check_process_result(
            handle,  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
            "opencode",
            raw_output,
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                phase="development",
            ),
        )
        # No exception raised means explicit_complete=True -> TERMINAL_COMPLETE

    def test_artifact_present_without_explicit_completion_does_not_raise(
        self, tmp_path: Path
    ) -> None:
        """Artifact on disk -> TERMINAL_COMPLETE without needing declare_complete."""
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "development_result.json").write_text("{}")

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        _check_process_result(
            handle,  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
            "opencode",
            [],  # no declare_complete marker
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                phase="development",
            ),
        )
        # No exception raised means required_artifact_present=True -> TERMINAL_COMPLETE

    def test_neither_signal_nor_artifact_raises_resumable_exit(
        self, tmp_path: Path
    ) -> None:
        """No explicit completion and no artifact -> OpenCodeResumableExitError."""
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        with pytest.raises(OpenCodeResumableExitError):
            _check_process_result(
                handle,  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                "opencode",
                [],  # no declare_complete marker
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    phase="development",  # has required artifact but file doesn't exist
                ),
            )


# ---------------------------------------------------------------------------
# (k) extract_explicit_completion detects declare_complete marker
# ---------------------------------------------------------------------------


class TestExtractExplicitCompletion:
    """extract_explicit_completion scans raw NDJSON output for the declare_complete marker."""

    def test_detects_marker_in_raw_output(self) -> None:
        raw = [
            '{"type": "text", "content": "Working..."}',
            "Task declared complete: session_id=x, summary=done",
        ]
        assert extract_explicit_completion(raw) is True

    def test_returns_false_when_no_marker(self) -> None:
        raw = [
            '{"type": "text", "content": "Working..."}',
            '{"type": "tool_use", "tool": "read_file"}',
        ]
        assert extract_explicit_completion(raw) is False

    def test_returns_false_for_empty_output(self) -> None:
        assert extract_explicit_completion([]) is False


# ---------------------------------------------------------------------------
# (l) _read_lines_from_process resets idle clock on WAITING_ON_CHILD
# ---------------------------------------------------------------------------


class TestReadLinesFromProcessIdleClockReset:
    """_read_lines_from_process resets idle clock when classify_quiet returns WAITING_ON_CHILD."""

    def test_waiting_on_child_resets_clock_without_terminating_handle(self) -> None:
        """WAITING_ON_CHILD resets last_activity; handle is only terminated when ACTIVE fires."""
        stop_event = threading.Event()

        class _BlockingStdout:
            def __iter__(self) -> _BlockingStdout:
                return self

            def __next__(self) -> str:
                stop_event.wait(10)
                raise StopIteration

        class _TestHandle:
            returncode: int | None = None
            stdout = _BlockingStdout()
            stderr = SimpleNamespace(read=lambda: "")
            terminate_count: int = 0

            def terminate(self, grace_period_s: float | None = None) -> None:
                del grace_period_s
                self.terminate_count += 1
                stop_event.set()
                self.returncode = -15

            def __enter__(self) -> _TestHandle:
                return self

            def __exit__(self, *_: object) -> bool:
                return False

            def wait(self, timeout: float | None = None) -> int | None:
                del timeout
                return self.returncode

            def poll(self) -> int | None:
                return self.returncode

        handle = _TestHandle()

        class _OnceThenActive(OpenCodeExecutionStrategy):
            """Returns WAITING_ON_CHILD on first classify_quiet call, ACTIVE on second."""

            def __init__(self) -> None:
                self.call_count = 0

            def classify_quiet(
                self, handle: object, liveness_probe: object
            ) -> AgentExecutionState:
                self.call_count += 1
                if self.call_count == 1:
                    return AgentExecutionState.WAITING_ON_CHILD
                return AgentExecutionState.ACTIVE

        strategy = _OnceThenActive()
        probe = FakeLivenessProbe(active=False)

        # Values: start (0.0), first check (1.1), after reset (1.1), second check (2.2)
        monotonic_vals = iter([0.0, 1.1, 1.1, 2.2])

        expected_classify_quiet_calls = 2
        with (
            patch("ralph.agents.invoke._IDLE_POLL_INTERVAL_SECONDS", 0.0),
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
            pytest.raises(_IdleStreamTimeoutError),
        ):
            list(
                _read_lines_from_process(
                    handle,  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                    idle_timeout_seconds=1.0,
                    execution_strategy=strategy,
                    liveness_probe=probe,
                )
            )

        # Strategy was called twice: first WAITING_ON_CHILD (reset), second ACTIVE (terminate)
        assert strategy.call_count == expected_classify_quiet_calls, (
            f"Expected 2 classify_quiet calls (reset then terminate); got {strategy.call_count}"
        )
        # Handle was terminated once (on ACTIVE, not on WAITING_ON_CHILD)
        assert handle.terminate_count == 1, (
            f"Expected 1 termination; got {handle.terminate_count}"
        )


# ---------------------------------------------------------------------------
# (m) Quiet parent with live child: positive path — process completes normally
# ---------------------------------------------------------------------------


class TestOpenCodeQuietParentWithLiveChildSuccessPath:
    """Quiet parent with active liveness probe: clock resets, parent produces output, no timeout."""

    def test_quiet_opencode_parent_with_ralph_tracked_child_resets_idle_clock(
        self,
    ) -> None:
        """OpenCode quiet parent with active liveness probe does not time out.

        Integration path: _read_lines_from_process with OpenCodeExecutionStrategy.
        When classify_quiet returns WAITING_ON_CHILD (live child tracked by Ralph),
        last_activity resets; once the parent then produces output and finishes,
        no _IdleStreamTimeoutError is raised.
        """
        output_ready = threading.Event()

        class _BlockingThenOneLineStdout:
            """Blocks until output_ready is set, then emits one line and finishes."""

            def __init__(self) -> None:
                self._emitted = False

            def __iter__(self) -> _BlockingThenOneLineStdout:
                return self

            def __next__(self) -> str:
                if not self._emitted:
                    output_ready.wait(10)
                    self._emitted = True
                    return '{"type":"result"}\n'
                raise StopIteration

        class _TestHandle:
            returncode: int | None = 0
            stdout = _BlockingThenOneLineStdout()
            stderr = SimpleNamespace(read=lambda: "")
            terminate_count: int = 0

            def terminate(self, grace_period_s: float | None = None) -> None:
                del grace_period_s
                self.terminate_count += 1

            def __enter__(self) -> _TestHandle:
                return self

            def __exit__(self, *_: object) -> bool:
                return False

            def wait(self, timeout: float | None = None) -> int | None:
                del timeout
                return self.returncode

            def poll(self) -> int | None:
                return self.returncode

        class _WaitingThenDoneStrategy(OpenCodeExecutionStrategy):
            """Returns WAITING_ON_CHILD and unblocks stdout on first classify_quiet call."""

            def __init__(self) -> None:
                self.call_count = 0

            def classify_quiet(
                self, handle: object, liveness_probe: object
            ) -> AgentExecutionState:
                self.call_count += 1
                output_ready.set()
                return AgentExecutionState.WAITING_ON_CHILD

        handle = _TestHandle()
        strategy = _WaitingThenDoneStrategy()
        probe = FakeLivenessProbe(active=True)

        # First 3 monotonic values drive the initial timeout trigger and reset.
        # Unlimited 1.5 values ensure no second timeout fires (1.5 - 1.1 = 0.4 < 1.0).
        monotonic_vals = chain([0.0, 1.1, 1.1], repeat(1.5))

        with (
            patch("ralph.agents.invoke._IDLE_POLL_INTERVAL_SECONDS", 0.0),
            patch.object(
                _time_module, "monotonic", side_effect=lambda: next(monotonic_vals)
            ),
        ):
            collected = list(
                _read_lines_from_process(
                    handle,  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                    idle_timeout_seconds=1.0,
                    execution_strategy=strategy,
                    liveness_probe=probe,
                )
            )

        assert collected == ['{"type":"result"}\n'], (
            f"Expected output line after WAITING_ON_CHILD reset; got {collected!r}"
        )
        assert strategy.call_count >= 1, (
            "classify_quiet must have been called at least once (clock reset happened)"
        )
        assert handle.terminate_count == 0, (
            f"Handle must not be terminated when idle timeout never fires; "
            f"got {handle.terminate_count} termination(s)"
        )
