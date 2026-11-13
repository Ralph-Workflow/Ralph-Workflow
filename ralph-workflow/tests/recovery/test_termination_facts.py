from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy, WatchdogFireReason
from ralph.agents.invoke import (
    AgentInvocationError,
    InvokeOptions,
    ProcessLineReader,
    ProcessReaderCtx,
    TerminationRecorder,
    attempt_facts_from_exception,
    check_process_result,
    invoke_agent,
    termination_issuer_for_exception,
    termination_issuer_for_fire_reason,
    termination_issuer_for_teardown_issuer,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer
from tests.recovery._fake_managed_handle import FakeManagedHandle

if TYPE_CHECKING:
    from ralph.pipeline.events import AgentFailureEvent


def _reader(tmp_path: Path, handle: FakeManagedHandle | None = None) -> ProcessLineReader:
    ctx = ProcessReaderCtx(
        config=AgentConfig(cmd="fake-agent", transport=AgentTransport.GENERIC),
        policy=TimeoutPolicy(idle_timeout_seconds=60.0, process_monitor_enabled=False),
        workspace_path=tmp_path,
    )
    return ProcessLineReader(handle or FakeManagedHandle(), ctx, FakeClock())


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

    facts = attempt_facts_from_exception(exc_info.value)
    assert facts is not None
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
