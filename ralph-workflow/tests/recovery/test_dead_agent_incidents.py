from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pytest

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import ChannelName, StuckKind, WatchdogFireReason, classify_stuck
from ralph.agents.idle_watchdog.idle_watchdog import IdleWatchdog
from ralph.agents.idle_watchdog.timeout_policy import TimeoutPolicy
from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    InactivityTimeoutOpts,
    ProcessLineReader,
    ProcessReaderCtx,
    QuotaExhaustedError,
    TerminationRecorder,
    attempt_facts_from_exception,
    check_process_result,
    is_provider_quota_output_line,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.agent_config import AgentConfig
from ralph.pipeline.agent_retry_decision import resolve_retry_intent
from ralph.pipeline.events.agent_failure_event import AgentFailureEvent
from ralph.process.manager import ManagedProcess, ProcessManager, ProcessRecord, ProcessStatus
from ralph.recovery import dead_agent_probe
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact
from ralph.recovery.attempt_facts import TerminationIssuer
from ralph.recovery.canary_probe import ProbeOutcome, ProbeResult
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.probe import DefaultWorkspaceChangeProbe
from tests.recovery._scenario_harness import AgentAttemptResult, run_scenario

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.process.teardown import ProcessTeardown

PHASE = "development"

INCIDENT_1_AGY_ERROR_STEP = '{"type":"error_message","message":"provider rejected the request"}'

INCIDENT_2_CODEX_TOOL_USE_LINE = (
    '{"type":"item.completed","item":{"type":"file_read","path":"README.md"}}'
)

INCIDENT_3_CODEX_TOOL_RESULT_LINE = (
    '{"error":null,"content":"Your account has credits exhausted for this period."}'
)

INCIDENT_4_OPENCODE_TOOL_USE_LINE = '{"type":"tool_use","tool":"task","children":3}'

INCIDENT_4_OPENCODE_TRANSPORT_ERROR_LINE = '{"type":"error","message":"the session ended abruptly"}'


def _stub_canary_probe_boundary(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    probed: list[str] = []

    def fake_run_canary_probe(config: AgentConfig, **_kwargs: object) -> ProbeResult:
        probed.append(config.cmd)
        return ProbeResult(outcome=ProbeOutcome.INCONCLUSIVE, detail="scripted")

    monkeypatch.setattr(dead_agent_probe, "run_canary_probe", fake_run_canary_probe)
    return probed


@runtime_checkable
class _TerminationIssuerHolder(Protocol):
    @property
    def termination_issuer(self) -> str | None: ...


def _termination_issuer(record: object) -> str | None:
    if isinstance(record, _TerminationIssuerHolder):
        return record.termination_issuer
    return None


@dataclass(frozen=True)
class RecordedAttemptFact:
    phase: str
    agent: str
    artifact_valid: bool
    workspace_changed: bool
    free_retry_category: bool


class RecordingRecoveryController(RecoveryController):
    def __init__(self, *, options: RecoveryControllerOptions) -> None:
        super().__init__(options=options)
        self.recorded_facts: list[RecordedAttemptFact] = []

    def record_attempt_fact(
        self,
        phase: str,
        agent: str,
        *,
        artifact_valid: bool,
        workspace_changed: bool,
        free_retry_category: bool = False,
    ) -> str | None:
        self.recorded_facts.append(
            RecordedAttemptFact(
                phase=phase,
                agent=agent,
                artifact_valid=artifact_valid,
                workspace_changed=workspace_changed,
                free_retry_category=free_retry_category,
            )
        )
        return super().record_attempt_fact(
            phase,
            agent,
            artifact_valid=artifact_valid,
            workspace_changed=workspace_changed,
            free_retry_category=free_retry_category,
        )


class FakeTeardown:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def teardown_subtree(self, host_pid: int, *, issuer: str) -> None:
        self.calls.append((host_pid, issuer))


class FakeManagedHandle(ManagedProcess):
    stdin = None

    def __init__(self, *, returncode: int | None, issuer: str | None) -> None:
        self._fake_returncode = returncode
        self._issuer = issuer
        self.terminated: list[float | None] = []

    @property
    def pid(self) -> int:
        return 424242

    @property
    def stdout(self) -> None:
        return None

    @property
    def stderr(self) -> None:
        return None

    @property
    def returncode(self) -> int | None:
        return self._fake_returncode

    def terminate(self, grace_period_s: float | None = None, **_kwargs: object) -> None:
        self.terminated.append(grace_period_s)

    def poll(self) -> int | None:
        return self._fake_returncode

    def termination_issuer(self) -> str | None:
        return self._issuer


class FakeSyncProcess:
    def __init__(self, returncode: int | None) -> None:
        self.pid = 424242
        self.returncode = returncode


def _ledger_entry(controller: RecoveryController, agent: str) -> dict[str, object]:
    return _snapshot_entry(controller.ledger.snapshot(), agent)


def _snapshot_entry(snapshot: dict[str, object], agent: str) -> dict[str, object]:
    entries = snapshot.get("entries")
    if not isinstance(entries, dict):
        return {}
    entry = entries.get(f"{PHASE}:{agent}")
    if not isinstance(entry, dict):
        return {}
    return entry


def _counter(entry: dict[str, object], key: str) -> int:
    value = entry.get(key)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0


def _run_incident_1(
    tmp_path: Path,
) -> tuple[RecordingRecoveryController, list[str], list[str]]:
    controller = RecordingRecoveryController(
        options=RecoveryControllerOptions(
            clock=FakeClock(start=0.0),
            workspace_probe=DefaultWorkspaceChangeProbe(),
        )
    )

    def script(agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        if agent == "fallback":
            return AgentAttemptResult(produce_artifact=True)
        return AgentAttemptResult(
            produce_artifact=False,
            modify_workspace=False,
            output_lines=(INCIDENT_1_AGY_ERROR_STEP,),
            raise_error=QuotaExhaustedError(agent, "credits exhausted for this account"),
        )

    outcome = run_scenario(
        tmp_path,
        script=script,
        agents=("agy", "fallback"),
        controller=controller,
        max_iterations=4,
    )
    return controller, outcome.invocations, outcome.logs


def test_incident_1_typed_failure_routes_through_the_skip_same_agent_retries_hint() -> None:
    typed_intent = resolve_retry_intent(
        QuotaExhaustedError("agy", "credits exhausted for this account"),
        phase=PHASE,
        agent="agy",
        session_id=None,
        inactivity_error_type=AgentInactivityTimeoutError,
    )
    ordinary_intent = resolve_retry_intent(
        AgentInvocationError("codex", -15, "", [INCIDENT_2_CODEX_TOOL_USE_LINE]),
        phase=PHASE,
        agent="codex",
        session_id=None,
        inactivity_error_type=AgentInactivityTimeoutError,
    )

    assert typed_intent is not None
    assert typed_intent.skip_same_agent_retries is True, (
        "the typed quota failure is the route that skips same-agent retries "
        f"(observed: {typed_intent.skip_same_agent_retries!r})"
    )
    assert typed_intent.failure_reason == "QuotaExhaustedError", (
        f"the typed route names its failure (observed: {typed_intent.failure_reason!r})"
    )
    assert ordinary_intent is None or ordinary_intent.skip_same_agent_retries is False, (
        f"the ordinary kill route does not skip same-agent retries (observed: {ordinary_intent!r})"
    )


def test_incident_1_dead_agent_typed_failure_is_recorded_as_an_attempt_fact(tmp_path: Path) -> None:
    controller, invocations, logs = _run_incident_1(tmp_path)

    assert controller.recorded_facts, (
        "the typed quota failure must be recorded as an attempt fact like every other route "
        "that ends an attempt; observed route: the reducer returns through "
        "_handle_skip_same_agent_retries before record_attempt_fact, so nothing durable is "
        f"written and the dead agent is re-selected (observed recorded facts: "
        f"{controller.recorded_facts!r}; observed invocations: {invocations}; observed logs: "
        f"{[line for line in logs if 'failed with code' in line]})"
    )
    fact = controller.recorded_facts[0]
    assert fact.phase == PHASE
    assert fact.agent == "agy"
    assert fact.artifact_valid is False
    assert fact.workspace_changed is False, (
        "the agy attempt produced no work at all, so the recorded workspace change must be "
        f"False (observed: {fact.workspace_changed!r})"
    )


def test_incident_1_dead_agent_typed_failure_increments_the_ledger_consecutive_counter(
    tmp_path: Path,
) -> None:
    controller, invocations, _logs = _run_incident_1(tmp_path)

    entry = _ledger_entry(controller, "agy")
    assert entry.get("consecutive_unproductive") == 1, (
        "the dead agy attempt must count as one unproductive attempt so the ledger can reach "
        "its threshold; observed route: the typed failure returns before record_attempt_fact, "
        f"so the ledger stays empty and the agent is re-selected (observed invocations: "
        f"{invocations}; observed ledger entry: {entry!r})"
    )
    assert entry.get("total_unproductive") == 1, (
        f"the dead agy attempt must count towards the total budget (observed ledger entry: {entry!r})"
    )


def test_incident_1_dead_agent_typed_failure_reaches_the_agent_keyed_cooldown_tracker(
    tmp_path: Path,
) -> None:
    controller, _invocations, _logs = _run_incident_1(tmp_path)

    tracker_snapshot = controller.unavailability_store.snapshot()
    timeouts = tracker_snapshot.get("unavailable_timeouts")
    assert isinstance(timeouts, dict) and "agy" in timeouts, (
        "the dead agy attempt must reach AgentUnavailabilityTracker.mark_unavailable so the "
        "agent is benched instead of being re-selected forever; observed route: the typed "
        "failure never reaches the classifier, so the tracker stays empty "
        f"(observed tracker snapshot: {tracker_snapshot!r})"
    )
    assert controller.unavailability_store.is_available(PHASE, "agy") is False, (
        f"a benched agent must not be selectable (observed tracker snapshot: {tracker_snapshot!r})"
    )


def test_incident_2_codex_kill_route_records_the_attempt_fact_with_workspace_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probed = _stub_canary_probe_boundary(monkeypatch)
    controller = RecordingRecoveryController(
        options=RecoveryControllerOptions(
            clock=FakeClock(start=0.0),
            workspace_probe=DefaultWorkspaceChangeProbe(),
        )
    )

    def script(agent: str, _phase: str, _attempt: int, _workspace: object) -> AgentAttemptResult:
        if agent == "codex_b":
            return AgentAttemptResult(produce_artifact=True)
        return AgentAttemptResult(
            produce_artifact=False,
            modify_workspace=False,
            output_lines=(INCIDENT_2_CODEX_TOOL_USE_LINE,),
            raise_error=AgentInvocationError(
                agent,
                -15,
                "",
                [INCIDENT_2_CODEX_TOOL_USE_LINE],
            ),
        )

    outcome = run_scenario(
        tmp_path,
        script=script,
        agents=("codex", "codex_b"),
        controller=controller,
        max_iterations=6,
    )

    assert controller.recorded_facts, (
        "the ordinary kill route must record the attempt fact; observed route: the reducer "
        "records the fact and then re-selects the same agent "
        f"(observed recorded facts: {controller.recorded_facts!r}; observed invocations: "
        f"{outcome.invocations})"
    )
    first = controller.recorded_facts[0]
    assert first.agent == "codex"
    assert first.artifact_valid is False
    assert first.workspace_changed is False, (
        "the codex session only performed a read-only tool call, so the recorded workspace "
        f"change must be False (observed: {first.workspace_changed!r})"
    )
    assert outcome.probe_invocations, (
        "the dead-agent canary probe must run through the injected scripted seam "
        "(observed probe invocations: none)"
    )
    real_spawns = [
        line
        for line in outcome.logs
        if "Invoking agent:" in line and "canary_probe_prompt.md" in line
    ]
    assert real_spawns == [], (
        "the fake scenario must never launch a real agent CLI from the canary probe "
        f"(observed real spawns: {real_spawns})"
    )
    entry = _ledger_entry(controller, "codex")
    assert _counter(entry, "total_unproductive") >= 1, (
        f"the ordinary route must advance the ledger counter (observed ledger entry: {entry!r})"
    )
    assert controller.ledger.is_available(PHASE, "codex") is False
    assert outcome.invocations == ["codex", "codex", "codex", "codex_b"]
    assert outcome.exit_code == 0
    assert outcome.final_state.phase == "complete"
    assert probed, "the killed codex attempt must still reach the recovery probe boundary"


def test_ledger_resets_the_consecutive_counter_when_the_workspace_changed() -> None:
    ledger = AgentFailureLedger(clock=FakeClock(start=0.0))

    ledger.record(
        AttemptFact(phase=PHASE, agent="agy", artifact_valid=False, workspace_changed=True)
    )
    ledger.record(
        AttemptFact(phase=PHASE, agent="agy", artifact_valid=False, workspace_changed=False)
    )
    entry = _snapshot_entry(ledger.snapshot(), "agy")

    assert entry.get("consecutive_unproductive") == 1, (
        "a workspace change resets the consecutive counter, so a productive agent never "
        f"reaches the cooldown threshold (observed entry: {entry!r})"
    )
    assert entry.get("total_unproductive") == 2, (
        f"the total budget keeps counting (observed entry: {entry!r})"
    )


def test_ledger_reaches_the_cooldown_after_three_attempts_without_a_workspace_change() -> None:
    ledger = AgentFailureLedger(clock=FakeClock(start=0.0))

    for _attempt in range(3):
        ledger.record(
            AttemptFact(phase=PHASE, agent="agy", artifact_valid=False, workspace_changed=False)
        )
    entry = _snapshot_entry(ledger.snapshot(), "agy")

    assert entry.get("consecutive_unproductive") == 3, (
        f"the dead agent would reach the consecutive threshold (observed entry: {entry!r})"
    )
    assert entry.get("cooldown_reason") == "consecutive_unproductive_limit_reached", (
        f"the ledger would bench the dead agent on the third attempt (observed entry: {entry!r})"
    )


def test_unknown_workspace_change_is_not_collapsed_into_a_decided_value() -> None:
    observation = AgentFailureEvent(
        phase=PHASE,
        agent="codex",
        artifact_valid=False,
        workspace_changed=None,
    )

    assert observation.workspace_changed is None, (
        "the attempt fact must keep the unknown workspace-change case distinguishable"
    )
    assert observation.resolved_workspace_changed() is None, (
        "the unknown workspace-change case must not be collapsed into False: a session whose "
        "workspace change could not be observed must not be recorded as 'no work' "
        f"(observed resolved value: {observation.resolved_workspace_changed()!r})"
    )


def test_incident_3_codex_tool_result_with_a_null_error_is_not_a_quota_signal() -> None:
    assert is_provider_quota_output_line(INCIDENT_3_CODEX_TOOL_RESULT_LINE) is False, (
        "a tool result whose own error field is null must never be read as a provider quota "
        "signal, even when the quoted file content mentions exhausted credits "
        "(observed: the live quota detector fires on this line)"
    )


def test_incident_3_quota_detector_does_not_terminate_a_healthy_session_on_a_tool_result_line(
    tmp_path: Path,
) -> None:
    teardown: ProcessTeardown = FakeTeardown()
    handle = FakeManagedHandle(returncode=None, issuer=None)
    ctx = ProcessReaderCtx(
        config=AgentConfig(cmd="codex"),
        policy=TimeoutPolicy(idle_timeout_seconds=60.0),
        process_teardown=teardown,
        workspace_path=tmp_path,
    )
    reader = ProcessLineReader(handle, ctx, FakeClock())
    try:
        reader._terminate_for_quota(INCIDENT_3_CODEX_TOOL_RESULT_LINE)
        terminated = list(handle.terminated)
        teardown_calls = list(teardown.calls)
        quota_error = reader._quota_error
    finally:
        reader._unsubscribe()

    assert terminated == [], (
        "a tool-result line quoting provider policy text must not terminate the session "
        f"(observed terminate calls: {terminated})"
    )
    assert teardown_calls == [], (
        "a tool-result line quoting provider policy text must not tear the process tree down "
        f"(observed teardown calls: {teardown_calls})"
    )
    assert quota_error is None, (
        "a tool-result line quoting provider policy text must not raise a quota failure "
        f"(observed: {quota_error!r})"
    )


def test_incident_3_user_prompt_quota_words_do_not_trigger_the_quota_detector() -> None:
    prompt_line = '{"type":"user_prompt","content":"your credits exhausted for this period"}'

    assert is_provider_quota_output_line(prompt_line) is False, (
        "a user prompt that quotes quota wording must never be read as a provider quota signal"
    )


def test_incident_4_an_unattributed_signal_end_records_unknown_and_the_observed_code() -> None:
    handle = FakeManagedHandle(returncode=-15, issuer=None)
    recorder = TerminationRecorder()

    with pytest.raises(AgentInvocationError) as excinfo:
        check_process_result(
            handle,
            "opencode",
            [INCIDENT_4_OPENCODE_TOOL_USE_LINE],
            termination_recorder=recorder,
        )

    error = excinfo.value
    facts = attempt_facts_from_exception(error)
    assert facts is not None
    assert facts.termination is not None
    assert facts.termination.issuer is TerminationIssuer.UNKNOWN, (
        "a kill nobody attributed is unknown, never an agent exit "
        f"(observed: {facts.termination.issuer!r})"
    )
    assert facts.termination.observed_returncode == -15
    assert error.issuer == TerminationIssuer.UNKNOWN.value, (
        f"the failure carries the recorded issuer instead of None (observed: {error.issuer!r})"
    )


def test_incident_4_the_issuer_is_kept_for_signal_codes_and_recorded_for_plain_exits() -> None:
    killed = FakeManagedHandle(returncode=-15, issuer="invoke:process")
    with pytest.raises(AgentInvocationError) as killed_error:
        check_process_result(
            killed,
            "opencode",
            [INCIDENT_4_OPENCODE_TOOL_USE_LINE],
        )

    assert killed_error.value.issuer == "invoke:process", (
        "a SIGTERM kill with a known issuer must keep the issuer "
        f"(observed: {killed_error.value.issuer!r})"
    )
    assert killed_error.value.failure_origin == "intentional_termination", (
        f"the origin must say the end was intentional (observed: {killed_error.value.failure_origin!r})"
    )

    plain = FakeManagedHandle(returncode=1, issuer="invoke:process")
    recorder = TerminationRecorder()
    with pytest.raises(AgentInvocationError) as plain_error:
        check_process_result(
            plain,
            "opencode",
            [INCIDENT_4_OPENCODE_TOOL_USE_LINE],
            termination_recorder=recorder,
        )

    assert plain_error.value.issuer == TerminationIssuer.AGENT_EXIT.value, (
        "an ordinary non-zero exit is recorded as the agent's own exit "
        f"(observed issuer: {plain_error.value.issuer!r})"
    )
    assert plain_error.value.failure_origin == "agent", (
        f"a non-signal exit is attributed to the agent (observed: {plain_error.value.failure_origin!r})"
    )


def test_incident_4_the_reported_code_and_the_process_manager_code_are_separate_values() -> None:
    record = ProcessRecord(
        pid=424242,
        pgid=424242,
        command=("opencode",),
        cwd=None,
        started_at=datetime.now(UTC),
        status=ProcessStatus.KILLED,
        returncode=-15,
        label="invoke:opencode",
    )
    handle = ManagedProcess(
        FakeSyncProcess(0),
        record,
        ProcessManager(),
    )

    assert handle.returncode == 0, (
        "the code reported upwards comes from the agent handle, which prefers the exit status it "
        f"observed itself (observed: {handle.returncode!r})"
    )
    assert record.returncode == -15, (
        "the code the process manager logged comes from its own record, which is a separate "
        f"value (observed: {record.returncode!r})"
    )


def test_incident_4_the_process_manager_is_the_only_writer_of_a_termination_issuer() -> None:
    manager = ProcessManager()

    assert manager.termination_issuer(424242) is None, (
        "a process that was never terminated through the manager has no termination issuer"
    )

    manager.record_terminal_reason(424242, "operator_cancellation", "managed_process.terminate")

    assert manager.termination_issuer(424242) == "managed_process.terminate", (
        "the issuer is recorded only by the manager-mediated termination call "
        f"(observed issuer: {manager.termination_issuer(424242)!r})"
    )


def test_incident_4_each_candidate_kill_source_leaves_a_distinguishable_signature() -> None:
    idle_watchdog_kill = AgentInactivityTimeoutError(
        "opencode",
        18.0,
        [],
        InactivityTimeoutOpts(reason=WatchdogFireReason.NO_PROGRESS_QUIET),
    )
    post_exit_grace_kill = AgentInactivityTimeoutError(
        "opencode",
        5.0,
        [],
        InactivityTimeoutOpts(reason=WatchdogFireReason.PROCESS_EXIT_HANG),
    )
    quota_detector_kill = QuotaExhaustedError("opencode", INCIDENT_4_OPENCODE_TOOL_USE_LINE)

    assert idle_watchdog_kill.failure_origin == "watchdog_observation", (
        "the supervisor idle/liveness kill reports a watchdog observation "
        f"(observed: {idle_watchdog_kill.failure_origin!r})"
    )
    assert idle_watchdog_kill.returncode == -1, (
        f"the supervisor idle/liveness kill reports its own code (observed: {idle_watchdog_kill.returncode!r})"
    )
    assert post_exit_grace_kill.failure_origin == "watchdog_observation", (
        "the post-exit grace kill reports a watchdog observation "
        f"(observed: {post_exit_grace_kill.failure_origin!r})"
    )
    assert post_exit_grace_kill.reason == WatchdogFireReason.PROCESS_EXIT_HANG.value, (
        f"the post-exit grace kill names its reason (observed: {post_exit_grace_kill.reason!r})"
    )
    assert quota_detector_kill.failure_origin == "agent", (
        "the quota detector kill is reported as an agent failure "
        f"(observed: {quota_detector_kill.failure_origin!r})"
    )
    assert quota_detector_kill.returncode == 0, (
        f"the quota detector kill is reported with code 0 (observed: {quota_detector_kill.returncode!r})"
    )
    assert quota_detector_kill.skip_same_agent_retries is True, (
        "the quota detector kill asks for the same agent to be skipped "
        f"(observed: {quota_detector_kill.skip_same_agent_retries!r})"
    )


def test_incident_4_every_known_killer_records_an_issuer_instead_of_the_unattributed_signature() -> (
    None
):
    unattributed_signature = ("agent", None, 0)
    supervisor = AgentInactivityTimeoutError(
        "opencode",
        18.0,
        [],
        InactivityTimeoutOpts(reason=WatchdogFireReason.NO_PROGRESS_QUIET),
    )
    watchdog = AgentInactivityTimeoutError(
        "opencode",
        30.0,
        [],
        InactivityTimeoutOpts(reason=WatchdogFireReason.DESCENDANT_HANG),
    )
    quota = QuotaExhaustedError("opencode", INCIDENT_4_OPENCODE_TOOL_USE_LINE)

    signatures = {
        "supervisor": (supervisor.failure_origin, supervisor.issuer, supervisor.returncode),
        "watchdog": (watchdog.failure_origin, watchdog.issuer, watchdog.returncode),
        "quota": (quota.failure_origin, quota.issuer, quota.returncode),
    }

    assert all(signature != unattributed_signature for signature in signatures.values()), (
        f"a kill that records no issuer cannot be attributed: {signatures!r}"
    )
    assert len({signature[1] for signature in signatures.values()}) == len(signatures), (
        f"each killer must record its own distinguishable issuer: {signatures!r}"
    )
    assert all(isinstance(signature[1], str) and signature[1] for signature in signatures.values())


def test_incident_4_the_recovery_controller_never_kills_the_agent_process() -> None:
    controller = RecoveryController()
    killing_members = sorted(
        name
        for name in dir(controller)
        if "kill" in name.lower() or "terminat" in name.lower() or "signal" in name.lower()
    )

    assert killing_members == [], (
        "a recovery timeout can only re-route the attempt, it cannot be the source of the "
        f"SIGTERM (observed members: {killing_members!r})"
    )


def test_incident_4_a_kill_must_record_who_ended_the_session() -> None:
    quota_detector_kill = QuotaExhaustedError("opencode", INCIDENT_4_OPENCODE_TOOL_USE_LINE)
    supervisor_kill = AgentInactivityTimeoutError(
        "opencode",
        18.0,
        [],
        InactivityTimeoutOpts(reason=WatchdogFireReason.NO_PROGRESS_QUIET),
    )

    assert quota_detector_kill.issuer is not None, (
        "a kill must record which component ended the session, so the reported failure must "
        f"carry a termination issuer (observed issuer: {quota_detector_kill.issuer!r})"
    )
    assert supervisor_kill.issuer is not None, (
        "a kill must record which component ended the session, so the reported failure must "
        f"carry a termination issuer (observed issuer: {supervisor_kill.issuer!r})"
    )


def test_incident_4_the_attempt_record_carries_the_termination_record() -> None:
    event = AgentFailureEvent(
        phase=PHASE,
        agent="opencode",
        artifact_valid=False,
        workspace_changed=False,
    )

    assert _termination_issuer(event) is not None, (
        "the per-attempt record must carry the termination issuer so the end of the session "
        "can be attributed (observed record fields: phase, agent, artifact_valid, "
        "workspace_changed, reason, already_charged)"
    )


def test_incident_4_subagent_child_session_activity_counts_as_supervisor_liveness() -> None:
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(TimeoutPolicy(idle_timeout_seconds=60.0), clock)
    watchdog.record_invocation_start()

    for _update in range(33):
        watchdog.record_subagent_work(description="child session store update")

    summary = watchdog.last_evidence_summary(clock.monotonic())
    liveness_channel = summary.by_name(ChannelName.SUBAGENT_LIVENESS)
    output_channel = summary.by_name(ChannelName.SUBAGENT_OUTPUT)

    assert liveness_channel is not None
    assert liveness_channel.counter == 33, (
        f"the forwarded child-session store updates are counted (observed: {liveness_channel.counter!r})"
    )
    assert liveness_channel.can_defer is False, (
        "without live OS descendants the side-channel liveness channel cannot defer the "
        f"supervisor (observed: {liveness_channel.can_defer!r})"
    )
    assert output_channel is not None
    assert output_channel.tier.value == "first_party", (
        "the forwarded child-session activity is carried by the first-party subagent-output "
        f"channel (observed tier: {output_channel.tier.value!r})"
    )
    first_party = summary.first_party_fresh(30.0)
    assert first_party is not None, (
        "subagent child-session activity must count as liveness for the supervisor "
        "(observed: the first-party lookup found nothing)"
    )
    assert first_party.channel_name == ChannelName.SUBAGENT_OUTPUT, (
        "the liveness must come from the subagent-output channel "
        f"(observed: {first_party.channel_name.value!r})"
    )
    kind = classify_stuck(
        is_waiting_state=False,
        connectivity_state="online",
        evidence_summary=summary,
        classify_quiet=lambda: AgentExecutionState.ACTIVE,
        activity_evidence_ttl_seconds=30.0,
    )
    assert kind == StuckKind.THINKING, (
        "a session whose only evidence is subagent child-session activity must be classified as "
        f"active, not stuck (observed: {kind!r})"
    )
