from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy, WatchdogFireReason
from ralph.agents.invoke import (
    AgentInvocationError,
    AgentRunCtx,
    AttemptEnded,
    InvokeOptions,
    ProcessLineReader,
    ProcessReaderCtx,
    TerminationRecorder,
    check_process_result,
    invoke_agent,
    run_pty_and_read_lines,
    run_subprocess_and_read_lines,
    termination_issuer_for_exception,
    termination_issuer_for_fire_reason,
    termination_issuer_for_teardown_issuer,
)
from ralph.agents.timeout_clock import FakeClock, SystemClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.attempt_runner import (
    build_entry_point_recovery_controller,
    run_agent_attempt,
)
from ralph.pipeline.attempt_runner_deps import (
    AttemptRunnerDeps,
    RawAgentInvoke,
    raw_attempt_invoke,
)
from ralph.pipeline.events import PipelineEvent
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer
from tests.recovery._fake_managed_handle import FakeManagedHandle

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.invoke.attempt_supervision import AttemptSupervisor
    from ralph.pipeline.attempt_result import AttemptResult
    from ralph.pipeline.events import AgentFailureEvent


class _Handle(FakeManagedHandle):
    def terminate(self, grace_period_s: float | None = None, **_kwargs: object) -> None:
        super().terminate(grace_period_s)


def _reader(tmp_path: Path, handle: FakeManagedHandle | None = None) -> ProcessLineReader:
    ctx = ProcessReaderCtx(
        config=AgentConfig(cmd="fake-agent", transport=AgentTransport.GENERIC),
        policy=TimeoutPolicy(idle_timeout_seconds=60.0, process_monitor_enabled=False),
        workspace_path=tmp_path,
    )
    return ProcessLineReader(handle or _Handle(), ctx, FakeClock())


def test_failure_observation_sink_carries_attempt_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt = tmp_path / "PROMPT.md"
    prompt.write_text("work\n", encoding="utf-8")
    captured: list[AgentFailureEvent] = []

    def fail(_cmd: list[str], _ctx: object) -> object:
        raise AgentInvocationError("fake-agent", 1, "failed")

    import ralph.agents.invoke as invoke_module

    monkeypatch.setattr(invoke_module, "run_subprocess_and_read_lines", fail)
    options = InvokeOptions(
        agent_name="fake-agent",
        phase="development",
        failure_observation_sink=captured.append,
        workspace_monitor_factory=lambda *_args, **_kwargs: None,
    )

    with pytest.raises(AgentInvocationError):
        list(invoke_agent(AgentConfig(cmd="fake-agent"), str(prompt), options=options))

    assert len(captured) == 1
    assert isinstance(captured[0].facts, AttemptFacts)
    assert captured[0].facts.termination is not None
    assert captured[0].facts.termination.issuer is TerminationIssuer.AGENT_EXIT


def test_quota_kill_records_quota_detector_issuer(tmp_path: Path) -> None:
    reader = _reader(tmp_path)
    try:
        reader._terminate_for_quota("rate limit reached")
        with pytest.raises(AgentInvocationError) as exc_info:
            reader._raise_if_quota_exhausted()
    finally:
        reader._unsubscribe()

    error = exc_info.value
    assert isinstance(error, AttemptEnded)
    facts = error.facts
    assert facts.termination is not None
    assert facts.termination.issuer is TerminationIssuer.QUOTA_DETECTOR


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (WatchdogFireReason.NO_PROGRESS_QUIET, TerminationIssuer.SUPERVISOR_IDLE),
        (WatchdogFireReason.PROCESS_EXIT_HANG, TerminationIssuer.POST_EXIT_WATCHDOG),
        (WatchdogFireReason.DESCENDANT_HANG, TerminationIssuer.POST_EXIT_WATCHDOG),
        (WatchdogFireReason.SESSION_CEILING_EXCEEDED, TerminationIssuer.RECOVERY_TIMEOUT),
        (WatchdogFireReason.OPERATOR_CAP_REACHED, TerminationIssuer.UNKNOWN),
    ],
)
def test_watchdog_fire_reason_maps_to_attributed_issuer(
    reason: WatchdogFireReason, expected: TerminationIssuer
) -> None:
    assert termination_issuer_for_fire_reason(reason) is expected


def test_normal_exit_records_agent_exit_and_matching_codes() -> None:
    recorder = TerminationRecorder()

    check_process_result(
        FakeManagedHandle(reported_returncode=0, observed_returncode=0),
        "fake-agent",
        termination_recorder=recorder,
    )

    facts = recorder.facts()
    assert facts.termination is not None
    assert facts.termination.issuer is TerminationIssuer.AGENT_EXIT
    assert facts.termination.return_code_discrepancy is False


def test_unattributed_signal_is_unknown_never_agent_exit() -> None:
    error = AgentInvocationError("fake-agent", -15)

    assert termination_issuer_for_exception(error) is TerminationIssuer.UNKNOWN
    assert termination_issuer_for_teardown_issuer(None) is TerminationIssuer.UNKNOWN


@pytest.mark.parametrize(
    ("observed", "reported", "discrepancy"),
    [(-15, 0, True), (0, 0, False)],
)
def test_observed_and_reported_return_codes_are_reconciled(
    observed: int, reported: int, discrepancy: bool
) -> None:
    recorder = TerminationRecorder()

    check_process_result(
        FakeManagedHandle(
            reported_returncode=reported,
            observed_returncode=observed,
        ),
        "fake-agent",
        termination_recorder=recorder,
    )

    termination = recorder.facts().termination
    assert termination is not None
    assert termination.observed_returncode == observed
    assert termination.reported_returncode == reported
    assert termination.return_code_discrepancy is discrepancy


def test_unknown_liveness_stays_unknown() -> None:
    recorder = TerminationRecorder()
    facts = recorder.facts()

    assert facts.liveness_observed is None
    assert facts.subagent_activity_observed is None
    assert facts.termination is not None
    assert facts.termination.liveness_observed is None


def test_every_runtime_termination_component_has_an_issuer() -> None:
    components = {
        "normal_agent_exit": TerminationIssuer.AGENT_EXIT,
        "quota_detector": TerminationIssuer.QUOTA_DETECTOR,
        "supervisor_idle": termination_issuer_for_fire_reason(WatchdogFireReason.NO_PROGRESS_QUIET),
        "post_exit_watchdog": termination_issuer_for_fire_reason(
            WatchdogFireReason.PROCESS_EXIT_HANG
        ),
        "explicit_teardown": termination_issuer_for_teardown_issuer("invoke:process"),
        "unattributed_teardown": termination_issuer_for_teardown_issuer(None),
    }

    assert components == {
        "normal_agent_exit": TerminationIssuer.AGENT_EXIT,
        "quota_detector": TerminationIssuer.QUOTA_DETECTOR,
        "supervisor_idle": TerminationIssuer.SUPERVISOR_IDLE,
        "post_exit_watchdog": TerminationIssuer.POST_EXIT_WATCHDOG,
        "explicit_teardown": TerminationIssuer.OPERATOR,
        "unattributed_teardown": TerminationIssuer.UNKNOWN,
    }


class _SuccessfulPipeHandle:
    def __init__(self, lines: tuple[str, ...]) -> None:
        self.stdout = iter(lines)
        self.stderr = None
        self.stdin = None
        self.pid = 4242
        self.returncode = 0

    def __enter__(self) -> _SuccessfulPipeHandle:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def poll(self) -> int:
        return 0

    def terminate(self, *_args: object, **_kwargs: object) -> None:
        return None


def _pipe_ctx(tmp_path: Path) -> AgentRunCtx:
    return AgentRunCtx(
        config=AgentConfig(cmd="fake-agent", transport=AgentTransport.GENERIC),
        show_progress=False,
        extra_env=None,
        workspace_path=tmp_path,
        policy=TimeoutPolicy(
            idle_timeout_seconds=2.0,
            process_monitor_enabled=False,
        ),
    )


def _successful_pipe_invoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> RawAgentInvoke:
    def _invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str]:
        del config, prompt_file, options
        monkeypatch.setattr(
            "ralph.agents.invoke._process_reader.get_process_manager",
            lambda: SimpleNamespace(
                spawn=lambda *_a, **_k: _SuccessfulPipeHandle(_PIPE_LINES),
                register_listener=lambda *_a, **_k: lambda: None,
            ),
        )
        return run_subprocess_and_read_lines(["fake-agent"], _pipe_ctx(tmp_path))

    return _invoke


_PIPE_LINES = ("first line\n", "second line\n")


def _successful_pty_invoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> RawAgentInvoke:
    class _FakePtyLineReader:
        completion_exit_sent = True

        def __init__(
            self,
            *_args: object,
            supervisor: AttemptSupervisor | None = None,
            **_kwargs: object,
        ) -> None:
            self._supervisor = supervisor

        def read_lines(self) -> Iterator[str]:
            assert self._supervisor is not None
            self._supervisor.recorder.observe_event("tool_use", "read")
            yield from _PTY_LINES

        def discover_captured_session_id(self) -> None:
            return None

    handle = _SuccessfulPtyHandle()
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_runner.get_process_manager",
        lambda: SimpleNamespace(
            spawn_pty=lambda *_a, **_k: handle,
            register_listener=lambda *_a, **_k: lambda: None,
        ),
    )
    monkeypatch.setattr("ralph.agents.invoke._pty_runner.PtyLineReader", _FakePtyLineReader)

    def _invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str]:
        del config, prompt_file, options
        return run_pty_and_read_lines(["fake-agent"], _pipe_ctx(tmp_path))

    return _invoke


class _SuccessfulPtyHandle:
    pid = 4242
    returncode = 0

    def __enter__(self) -> _SuccessfulPtyHandle:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def poll(self) -> int:
        return 0

    def terminate(self, *_args: object, **_kwargs: object) -> None:
        return None


_PTY_LINES = ("working\n", "done\n")


def _run_raw_attempt(invoke: RawAgentInvoke) -> AttemptResult:
    config = UnifiedConfig(
        agents={"fake-agent": AgentConfig(cmd="fake-agent", transport=AgentTransport.GENERIC)}
    )
    agent_config = config.agents["fake-agent"]
    deps = AttemptRunnerDeps(SystemClock(), raw_attempt_invoke(invoke), None, None, None)
    controller = build_entry_point_recovery_controller(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        config=config,
        policy_bundle=None,
        deps=deps,
    )
    return run_agent_attempt(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        invocation=agent_config,
        config=config,
        controller=controller,
        deps=deps,
        prompt_file="prompt.md",
    )


def test_pipe_route_raw_attempt_carries_recorder_facts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    carried = raw_attempt_invoke(_successful_pipe_invoke(monkeypatch, tmp_path))(
        AgentConfig(cmd="fake-agent", transport=AgentTransport.GENERIC),
        prompt_file="prompt.md",
    )

    assert isinstance(carried, AttemptEnded)
    assert carried.facts.event_kinds == ("output_line",)
    assert carried.facts.termination is not None
    assert carried.facts.termination.issuer is TerminationIssuer.AGENT_EXIT


def test_pty_route_raw_attempt_carries_recorder_facts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    carried = raw_attempt_invoke(_successful_pty_invoke(monkeypatch, tmp_path))(
        AgentConfig(cmd="fake-agent", transport=AgentTransport.GENERIC),
        prompt_file="prompt.md",
    )

    assert isinstance(carried, AttemptEnded)
    assert carried.facts.event_kinds == ("tool_use",)
    assert carried.facts.termination is not None
    assert carried.facts.termination.issuer is TerminationIssuer.AGENT_EXIT


def test_raw_attempt_result_facts_match_the_recorder_kinds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _run_raw_attempt(_successful_pipe_invoke(monkeypatch, tmp_path))

    assert result.event is PipelineEvent.AGENT_SUCCESS
    assert result.facts.event_kinds == ("output_line",)
    assert result.facts.artifact_valid is True
    assert result.facts.termination is not None
    assert result.facts.termination.issuer is TerminationIssuer.AGENT_EXIT


def test_pty_raw_attempt_result_facts_match_the_recorder_kinds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _run_raw_attempt(_successful_pty_invoke(monkeypatch, tmp_path))

    assert result.event is PipelineEvent.AGENT_SUCCESS
    assert result.facts.event_kinds == ("tool_use",)
    assert result.facts.artifact_valid is True
