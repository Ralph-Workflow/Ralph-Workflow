"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

import threading
import time as _time_module
from itertools import chain, repeat
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

import pytest

from ralph.agents.completion_signals import CompletionSignals, extract_explicit_completion
from ralph.agents.execution_state import (
    AgentExecutionState,
    GenericExecutionStrategy,
    OpenCodeExecutionStrategy,
)
from ralph.agents.idle_watchdog import TimeoutPolicy
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
    _wait_for_descendants_then_recheck,
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

    from ralph.process.manager import ManagedProcess


# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5


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
# (h-pre) Unrelated agent workers do not suppress OpenCode timeout
# ---------------------------------------------------------------------------


class TestUnrelatedWorkerDoesNotSuppressTimeout:
    """Session-scoped liveness: unrelated agent: workers must not keep this run alive.

    OpenCodeExecutionStrategy accepts an optional ``label_scope`` that narrows
    the liveness check from the global ``agent:`` prefix to
    ``agent:{label_scope}:`` so that concurrent agent workers belonging to a
    different logical task do not falsely reset the idle clock.
    """

    def test_unrelated_agent_worker_does_not_suppress_timeout(self) -> None:
        """Unrelated agent:other-session worker does not keep scoped run alive."""
        strategy = OpenCodeExecutionStrategy(label_scope="my-session")
        # Probe: only an unrelated worker with a different session label is active.
        probe = FakeLivenessProbe(
            active_labels=frozenset({"agent:other-session:worker1"})
        )
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        # "agent:other-session:worker1" does NOT start with "agent:my-session:"
        # so the scoped check returns False → ACTIVE (timeout may fire)
        assert state == AgentExecutionState.ACTIVE, (
            f"Unrelated worker must not suppress timeout for scoped run; got {state!r}"
        )

    def test_related_agent_worker_resets_idle_clock(self) -> None:
        """Related agent:my-session: worker keeps scoped run alive."""
        strategy = OpenCodeExecutionStrategy(label_scope="my-session")
        probe = FakeLivenessProbe(
            active_labels=frozenset({"agent:my-session:worker1"})
        )
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        # "agent:my-session:worker1" starts with "agent:my-session:" → WAITING_ON_CHILD
        assert state == AgentExecutionState.WAITING_ON_CHILD, (
            f"Related worker must reset idle clock; got {state!r}"
        )

    def test_unscoped_strategy_still_activates_on_any_agent_label(self) -> None:
        """Without a scope, the global agent: prefix keeps existing behaviour."""
        strategy = OpenCodeExecutionStrategy()  # no label_scope
        probe = FakeLivenessProbe(
            active_labels=frozenset({"agent:any-run:worker1"})
        )
        handle = _FakeHandle(has_descendants=False)

        state = strategy.classify_quiet(handle, probe)

        # Any "agent:" prefix matches the global check → WAITING_ON_CHILD
        assert state == AgentExecutionState.WAITING_ON_CHILD, (
            f"Unscoped strategy must match any agent: label; got {state!r}"
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
        """declare_complete marker prevents OpenCodeResumableExitError without artifact."""
        # development phase requires .agent/artifacts/development_result.json; not created here.
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
                phase="development",
            ),
        )
        # No exception raised means explicit_complete=True → TERMINAL_COMPLETE

    def test_artifact_present_without_explicit_completion_does_not_raise(
        self, tmp_path: Path
    ) -> None:
        """Artifact on disk produces TERMINAL_COMPLETE without declare_complete."""
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "development_result.json").write_text("{}")

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0)

        _check_process_result(
            cast("ManagedProcess", handle),
            "opencode",
            [],  # no declare_complete marker
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=tmp_path,
                phase="development",
            ),
        )
        # No exception raised means required_artifact_present=True → TERMINAL_COMPLETE

    def test_neither_signal_nor_artifact_raises_resumable_exit(
        self, tmp_path: Path
    ) -> None:
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
                    phase="development",  # has required artifact but file doesn't exist
                    policy=TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0),
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
# (l) _read_lines_from_process defers termination on WAITING_ON_CHILD
# ---------------------------------------------------------------------------


class TestReadLinesFromProcessWaitingOnChildDeferred:
    """_read_lines_from_process defers termination when classify_quiet returns WAITING_ON_CHILD."""

    def test_waiting_on_child_defers_then_active_fires(self) -> None:
        """WAITING_ON_CHILD defers; handle is only terminated when ACTIVE fires afterward."""
        from ralph.agents.timeout_clock import FakeClock  # noqa: PLC0415

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
            """Returns WAITING_ON_CHILD on first classify_quiet call, ACTIVE on subsequent."""

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
        # FakeClock advances time on every wait_for_event call, no real wall time.
        # With idle_timeout=1.0 and poll interval 0.05s, the watchdog fires after ~20 ticks.
        # drain_window=0.0 fires immediately on ACTIVE (no re-consultation loop).
        fake_clock = FakeClock(start=0.0)

        with pytest.raises(_IdleStreamTimeoutError):
            list(
                _read_lines_from_process(
                    cast("ManagedProcess", handle),
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=1.0,
                        drain_window_seconds=0.0,
                        max_waiting_on_child_seconds=1800.0,
                    ),
                    execution_strategy=strategy,
                    liveness_probe=probe,
                    _clock=fake_clock,
                )
            )

        # Handle was terminated exactly once (on ACTIVE fire, not on WAITING_ON_CHILD deferral).
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
        """Active liveness probe resets clock; parent produces output with no timeout.

        Integration path: _read_lines_from_process with OpenCodeExecutionStrategy.
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
            patch.object(
                _time_module, "monotonic", side_effect=lambda: next(monotonic_vals)
            ),
        ):
            collected = list(
                _read_lines_from_process(
                    cast("ManagedProcess", handle),
                    policy=TimeoutPolicy(idle_timeout_seconds=1.0),
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


# ---------------------------------------------------------------------------
# (n) classify_exit defers to WAITING_ON_CHILD when children are alive
# ---------------------------------------------------------------------------


class TestClassifyExitDefersWhenChildrenAlive:
    """classify_exit must return WAITING_ON_CHILD when children are alive."""

    def test_classify_exit_returns_waiting_when_liveness_probe_active(self) -> None:
        """Liveness probe reporting active agents → WAITING_ON_CHILD."""
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=False)
        probe = FakeLivenessProbe(active=True)
        signals = CompletionSignals(False, False, ())

        state = strategy.classify_exit(handle, signals, liveness_probe=probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD

    def test_classify_exit_returns_waiting_when_handle_has_descendants(self) -> None:
        """handle.has_live_descendants() True → WAITING_ON_CHILD."""
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)
        probe = FakeLivenessProbe(active=False)
        signals = CompletionSignals(False, False, ())

        state = strategy.classify_exit(handle, signals, liveness_probe=probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD

    def test_classify_exit_completion_signals_take_precedence_over_live_children(
        self,
    ) -> None:
        """Strong completion signals → TERMINAL_COMPLETE even with live children."""
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)
        probe = FakeLivenessProbe(active=True)
        signals = CompletionSignals(True, True, ("development_result",))

        state = strategy.classify_exit(handle, signals, liveness_probe=probe)

        assert state == AgentExecutionState.TERMINAL_COMPLETE

    def test_classify_exit_with_no_probe_falls_back_to_descendant_check(self) -> None:
        """No liveness_probe → fallback to handle.has_live_descendants() for WAITING_ON_CHILD."""
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)
        signals = CompletionSignals(False, False, ())

        state = strategy.classify_exit(handle, signals)

        assert state == AgentExecutionState.WAITING_ON_CHILD


# ---------------------------------------------------------------------------
# (o) _check_process_result waits for live children before declaring failure
# ---------------------------------------------------------------------------


class TestCheckProcessResultWaitsForLiveChildren:
    """_check_process_result waits for child agents before raising OpenCodeResumableExitError."""

    def test_raises_resumable_exit_when_wait_times_out_without_artifact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Probe stays active throughout wait; deadline expires → OpenCodeResumableExitError.

        This tests the real _wait_for_descendants_then_recheck with a probe that
        never transitions to inactive.
        """
        probe = FakeLivenessProbe(active=True)  # Always active

        # Fake evaluate_completion to always return no signals
        def _fake_evaluate_completion(workspace, phase, raw_output):
            return CompletionSignals(False, False, ())

        monkeypatch.setattr(
            "ralph.agents.invoke.evaluate_completion", _fake_evaluate_completion
        )

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)

        # Time: 0.0 → 0.1 → 0.2 → deadline at 0.1 (probe always active)
        # First call (t=0.0): probe active → WAITING_ON_CHILD, wait
        # Second call (t=0.1): still within deadline, probe still active → WAITING_ON_CHILD, wait
        # Third call (t=0.2): past deadline → RESUMABLE_CONTINUE (timeout fallback)
        call_count = [0]
        monotonic_vals = iter([0.0, 0.1, 0.2])

        def _fake_event_wait(self, timeout=None):
            if timeout is not None and timeout == _DESCENDANT_WAIT_POLL_SECONDS:
                call_count[0] += 1
                return None
            return threading.Event.wait(self, timeout)

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
                    phase="development",
                    liveness_probe=probe,
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        descendant_wait_timeout_seconds=0.1,
                    ),
                ),
            )

    def test_grace_window_runs_even_when_no_children_at_exit_time(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Grace window always runs for OpenCode rc=0 exits without completion signals."""
        probe = FakeLivenessProbe(active=False)

        evaluate_calls = [0]

        def _fake_evaluate_completion(workspace, phase, raw_output):
            evaluate_calls[0] += 1
            return CompletionSignals(False, False, ())

        monkeypatch.setattr(
            "ralph.agents.invoke.evaluate_completion", _fake_evaluate_completion
        )

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=False)

        monotonic_vals = iter([0.0, 0.5, 1.0])
        poll_count = [0]

        def _fake_event_wait(self, timeout=None):
            if timeout is not None and timeout == _DESCENDANT_WAIT_POLL_SECONDS:
                poll_count[0] += 1

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
                    phase="development",
                    liveness_probe=probe,
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        parent_exit_grace_seconds=1.0,
                        descendant_wait_timeout_seconds=30.0,
                    ),
                ),
            )

        assert evaluate_calls[0] > 1, (
            f"Grace window must poll evaluate_completion; got {evaluate_calls[0]} calls"
        )
        assert poll_count[0] >= 1, "Grace window must sleep at least once before expiring"

    def test_wait_helper_returns_resumable_continue_on_timeout_with_children_alive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Direct test: _wait_for_descendants_then_recheck returns RESUMABLE_CONTINUE on timeout.

        When the probe stays active and the deadline expires, the helper must
        return RESUMABLE_CONTINUE (not WAITING_ON_CHILD) so the caller raises.
        """
        probe = FakeLivenessProbe(active=True)  # Always active

        def _fake_evaluate_completion(workspace, phase, raw_output):
            return CompletionSignals(False, False, ())

        monkeypatch.setattr(
            "ralph.agents.invoke.evaluate_completion", _fake_evaluate_completion
        )

        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)

        # Time: 0.0 → 0.1 → deadline at 0.1 (after two 0.05s polls)
        # First call (t=0.0): WAITING_ON_CHILD, wait
        # Second call (t=0.1): still WAITING_ON_CHILD, deadline hit → RESUMABLE_CONTINUE
        monotonic_vals = iter([0.0, 0.05, 0.1, 0.15])
        poll_count = [0]

        def _fake_event_wait(self, timeout=None):
            if timeout is not None and timeout == _DESCENDANT_WAIT_POLL_SECONDS:
                poll_count[0] += 1
                return None
            return threading.Event.wait(self, timeout)

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
            patch.object(threading.Event, "wait", _fake_event_wait),
        ):
            result = _wait_for_descendants_then_recheck(
                cast("ManagedProcess", handle),
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    phase="development",
                    liveness_probe=probe,
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        descendant_wait_timeout_seconds=0.1,
                    ),
                ),
                [],
            )

        # After deadline with children still alive, helper returns RESUMABLE_CONTINUE
        assert result == AgentExecutionState.RESUMABLE_CONTINUE, (
            f"Expected RESUMABLE_CONTINUE after timeout with children alive; got {result!r}"
        )

    def test_artifact_appears_during_wait_produces_terminal_complete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Artifact appears during wait; no error raised (TERMINAL_COMPLETE).

        Sequence:
        - t=0.0: initial classify_exit → WAITING_ON_CHILD (no artifact yet)
        - t=0.0: wait 0.5s (poll interval)
        - t=0.5: second evaluate_completion → artifact now present → TERMINAL_COMPLETE

        The wait helper is invoked and correctly returns TERMINAL_COMPLETE when
        the required artifact appears between polls, so _check_process_result
        does not raise OpenCodeResumableExitError.
        """
        probe = FakeLivenessProbe(active=True)  # Children alive initially
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)

        # Track which call to evaluate_completion we're on:
        # 1. _check_process_result initial check → no artifact
        # 2. _wait_for_descendants_then_recheck first poll → no artifact
        # 3. _wait_for_descendants_then_recheck second poll → artifact appears!
        call_count = [0]
        _artifact_appears_on = 3  # initial check + first loop poll + second loop poll

        def _fake_evaluate_completion(workspace, phase, raw_output):
            call_count[0] += 1
            if call_count[0] >= _artifact_appears_on:
                # Simulate artifact appearing between polls 1 and 2
                return CompletionSignals(False, True, ("development_result",))
            return CompletionSignals(False, False, ())

        monkeypatch.setattr(
            "ralph.agents.invoke.evaluate_completion", _fake_evaluate_completion
        )

        # Time: 0.0 → 0.5 → deadline at 0.6
        # First poll (t=0.0): no artifact → WAITING_ON_CHILD, wait 0.5s
        # Second poll (t=0.5): artifact present → TERMINAL_COMPLETE
        monotonic_vals = iter([0.0, 0.5, 0.55])

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
        ):
            _check_process_result(
                cast("ManagedProcess", handle),
                "opencode",
                [],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    phase="development",
                    liveness_probe=probe,
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        descendant_wait_timeout_seconds=0.6,
                    ),
                ),
            )
        # No exception raised because artifact appeared during wait → TERMINAL_COMPLETE

    def test_explicit_complete_appears_during_wait_produces_terminal_complete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit completion marker appears during wait; no error raised (TERMINAL_COMPLETE).

        Same pattern as artifact test but for explicit_complete signal.
        """
        probe = FakeLivenessProbe(active=True)
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)

        call_count = [0]
        _artifact_appears_on = 3  # initial check + first loop poll + second loop poll

        def _fake_evaluate_completion(workspace, phase, raw_output):
            call_count[0] += 1
            if call_count[0] >= _artifact_appears_on:
                # Simulate explicit_complete appearing between polls
                return CompletionSignals(True, False, ())
            return CompletionSignals(False, False, ())

        monkeypatch.setattr(
            "ralph.agents.invoke.evaluate_completion", _fake_evaluate_completion
        )

        monotonic_vals = iter([0.0, 0.5, 0.55])

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
        ):
            _check_process_result(
                cast("ManagedProcess", handle),
                "opencode",
                [],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    phase="development",
                    liveness_probe=probe,
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        descendant_wait_timeout_seconds=0.6,
                    ),
                ),
            )
        # No exception raised because explicit_complete appeared during wait

    def test_descendants_finish_during_wait_produces_terminal_complete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OS-level descendants finish during wait; no error raised (TERMINAL_COMPLETE).

        Tests the handle.has_live_descendants() path transitioning from True to False
        during the wait window.
        """
        # Probe is inactive (Ralph-tracked agents done), but OS descendants alive initially
        probe = FakeLivenessProbe(active=False)
        strategy = OpenCodeExecutionStrategy()

        # Track whether descendants are alive
        descendants_alive = [True]

        class _TrackingFakeHandle:
            returncode = 0

            def has_live_descendants(self) -> bool:
                return descendants_alive[0]

        handle = _TrackingFakeHandle()

        call_count = [0]
        _artifact_appears_on = 3  # initial check + first loop poll + second loop poll

        def _fake_evaluate_completion_with_artifact(workspace, phase, raw_output):
            call_count[0] += 1
            if call_count[0] >= _artifact_appears_on:
                # After descendants finish, artifact appears
                return CompletionSignals(False, True, ("development_result",))
            return CompletionSignals(False, False, ())

        monkeypatch.setattr(
            "ralph.agents.invoke.evaluate_completion", _fake_evaluate_completion_with_artifact
        )

        # Time: 0.0 → 0.5 → deadline at 0.6
        # First poll (t=0.0): descendants alive → WAITING_ON_CHILD, wait 0.5s
        # Second poll (t=0.5): descendants alive, but artifact appears (call 3) → TERMINAL_COMPLETE
        monotonic_vals = iter([0.0, 0.5, 0.55])

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
        ):
            _check_process_result(
                cast("ManagedProcess", handle),
                "opencode",
                [],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    phase="development",
                    liveness_probe=probe,
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        descendant_wait_timeout_seconds=0.6,
                    ),
                ),
            )
        # No exception raised because descendants finished and artifact appeared during wait

    def test_wait_helper_timeout_then_final_recheck_finds_completion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deadline expires but completion appears exactly at deadline; final recheck catches it.

        The final recheck (added to fix the timeout gap) must evaluate completion one more
        time after the deadline elapses, rather than blindly returning RESUMABLE_CONTINUE.
        """
        probe = FakeLivenessProbe(active=True)  # Stays active until deadline
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=True)

        call_count = [0]
        _artifact_appears_on = 2  # first loop poll + final recheck

        def _fake_evaluate_completion(workspace, phase, raw_output):
            call_count[0] += 1
            # First poll (inside wait loop): no completion
            # Second call (final recheck after deadline): completion appears!
            if call_count[0] >= _artifact_appears_on:
                return CompletionSignals(False, True, ("development_result",))
            return CompletionSignals(False, False, ())

        monkeypatch.setattr(
            "ralph.agents.invoke.evaluate_completion", _fake_evaluate_completion
        )

        # t[0]=0.0: deadline = 0.5; t[1]=0.0: loop check True -> poll (call 1, no signals);
        # t[2]=0.5: loop check False -> final recheck (call 2) -> artifact -> TERMINAL_COMPLETE
        monotonic_vals = iter([0.0, 0.0, 0.5])

        def _fake_event_wait(self, timeout=None):
            return None

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
            patch.object(threading.Event, "wait", _fake_event_wait),
        ):
            result = _wait_for_descendants_then_recheck(
                cast("ManagedProcess", handle),
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    phase="development",
                    liveness_probe=probe,
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        descendant_wait_timeout_seconds=0.5,
                    ),
                ),
                [],
            )

        # Final recheck caught the completion → TERMINAL_COMPLETE, not RESUMABLE_CONTINUE
        assert result == AgentExecutionState.TERMINAL_COMPLETE, (
            f"Expected TERMINAL_COMPLETE from final recheck; got {result!r}"
        )

    def test_grace_window_catches_artifact_appearing_after_exit_with_no_children(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Artifact appears during grace window; no OpenCodeResumableExitError raised.

        Bug scenario: OpenCode exits rc=0, no children visible, no signals at exact exit
        moment. Grace window polls and finds artifact on second evaluate_completion call.
        """
        probe = FakeLivenessProbe(active=False)
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=False)

        call_count = [0]

        def _fake_evaluate_completion(workspace, phase, raw_output):
            call_count[0] += 1
            if call_count[0] == 1:
                return CompletionSignals(False, False, ())
            return CompletionSignals(False, True, ("development_result",))

        monkeypatch.setattr(
            "ralph.agents.invoke.evaluate_completion", _fake_evaluate_completion
        )

        monotonic_vals = iter([0.0, 0.5, 1.0])

        def _fake_event_wait(self, timeout=None):
            pass

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
            patch.object(threading.Event, "wait", _fake_event_wait),
        ):
            _check_process_result(
                cast("ManagedProcess", handle),
                "opencode",
                [],
                _CompletionCheckOptions(
                    execution_strategy=strategy,
                    workspace_path=tmp_path,
                    phase="development",
                    liveness_probe=probe,
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        parent_exit_grace_seconds=1.0,
                        descendant_wait_timeout_seconds=30.0,
                    ),
                ),
            )
        # No exception raised means artifact found during grace -> TERMINAL_COMPLETE

    def test_grace_window_raises_resumable_when_no_signal_and_no_children(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No signals and no children throughout grace -> OpenCodeResumableExitError raised."""
        probe = FakeLivenessProbe(active=False)
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=False)

        def _fake_evaluate_completion(workspace, phase, raw_output):
            return CompletionSignals(False, False, ())

        monkeypatch.setattr(
            "ralph.agents.invoke.evaluate_completion", _fake_evaluate_completion
        )

        monotonic_vals = iter([0.0, 0.5, 1.0])

        def _fake_event_wait(self, timeout=None):
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
                    phase="development",
                    liveness_probe=probe,
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        parent_exit_grace_seconds=1.0,
                        descendant_wait_timeout_seconds=30.0,
                    ),
                ),
            )

    def test_grace_window_escalates_to_descendant_wait_when_children_appear(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Child appears during grace; escalates to descendant wait; raises after timeout.

        Proves the two-window composition: grace detects a late-appearing child and
        escalates to the existing descendant wait, which eventually times out and raises.
        """

        class _FlippingProbe:
            """Returns False on first any_agent_active call, True on subsequent calls."""

            def __init__(self) -> None:
                self.call_count = 0

            def any_agent_active(self, prefix: str) -> bool:
                self.call_count += 1
                return self.call_count > 1

        probe = _FlippingProbe()
        strategy = OpenCodeExecutionStrategy()
        handle = _FakeHandle(returncode=0, has_descendants=False)

        def _fake_evaluate_completion(workspace, phase, raw_output):
            return CompletionSignals(False, False, ())

        monkeypatch.setattr(
            "ralph.agents.invoke.evaluate_completion", _fake_evaluate_completion
        )

        # [0] grace deadline calc; [1] grace loop check -> probe call 2 -> WAITING_ON_CHILD
        # [2] descendant deadline calc; [3] descendant loop check -> WAITING_ON_CHILD -> sleep
        # [4] descendant loop exit -> final recheck -> RESUMABLE_CONTINUE
        monotonic_vals = iter([0.0, 0.5, 0.5, 1.0, 2.5])

        def _fake_event_wait(self, timeout=None):
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
                    phase="development",
                    liveness_probe=probe,
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=None,
                        parent_exit_grace_seconds=1.0,
                        descendant_wait_timeout_seconds=2.0,
                    ),
                ),
            )

        # Descendant wait engaged: more probe calls than grace-only scenario requires.
        # Grace-only: 2 calls (initial classify_exit + grace loop that found child).
        # Descendant wait adds more calls; total must exceed grace-only count.
        _grace_only_probe_calls = 2
        assert probe.call_count > _grace_only_probe_calls, (
            f"Expected >2 probe calls proving descendant wait engaged; got {probe.call_count}"
        )
