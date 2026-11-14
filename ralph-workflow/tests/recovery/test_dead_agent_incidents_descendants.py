from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog.timeout_policy import TimeoutPolicy
from ralph.agents.invoke import TerminationRecorder
from ralph.agents.timeout_clock import FakeClock
from ralph.process.manager import ProcessManager, ProcessRecord, ProcessStatus
from ralph.recovery.attempt_facts import TerminationIssuer
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

if TYPE_CHECKING:
    from pathlib import Path

PHASE = "development"

INCIDENT_6_OPENCODE_TOOL_USE_LINE = (
    '{"type":"tool_use","sessionID":"s_0001","part":{"type":"tool","tool":"read_file",'
    '"callID":"c_0001","state":{"status":"completed","input":{"path":"docs/agents/verification.md"},'
    '"output":"verification"}}}'
)


class _DescendantFakeHandle:
    def __init__(self, *, has_descendants: bool = True) -> None:
        self.returncode = 0
        self.issuer = None
        self._has_descendants = has_descendants
        self.pid = 424242

    def poll(self) -> int | None:
        return self.returncode

    def has_live_descendants(self) -> bool:
        return self._has_descendants

    def descendant_snapshot(self) -> tuple[int, float | None]:
        return (1 if self._has_descendants else 0, 5.0 if self._has_descendants else None)


def _ledger_entry(controller: RecoveryController, agent: str) -> dict[str, object]:
    entries = controller.ledger.snapshot().get("entries")
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


def test_incident_5_mcp_server_kill_is_post_exit_teardown_not_session_failure_cause() -> None:
    from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer

    manager = ProcessManager()
    parent_record = ProcessRecord(
        pid=5001,
        pgid=5001,
        command=("claude",),
        cwd=None,
        started_at=datetime.now(UTC),
        status=ProcessStatus.RUNNING,
        returncode=None,
        label="invoke:claude",
    )
    child_record = ProcessRecord(
        pid=5002,
        pgid=5001,
        command=("mcp-server",),
        cwd=None,
        started_at=datetime.now(UTC),
        status=ProcessStatus.RUNNING,
        returncode=None,
        label="mcp:server",
    )
    manager._records[5001] = parent_record
    manager._records[5002] = child_record

    parent_record.status = ProcessStatus.EXITED
    parent_record.returncode = 0

    manager.record_teardown(
        5002,
        issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN,
        outcome="signalled",
    )
    child_record.status = ProcessStatus.KILLED
    child_record.returncode = -15

    assert parent_record.returncode == 0, (
        f"parent exit code must remain 0 (observed: {parent_record.returncode!r})"
    )
    assert child_record.returncode == -15, (
        f"child process was reaped during teardown (observed: {child_record.returncode!r})"
    )
    assert manager.termination_issuer(5001) is None, (
        "parent exit was voluntary and carries no manager kill issuer"
    )
    child_outcomes = manager.list_termination_outcomes().get(5002, [])
    assert any(
        outcome.get("stage") == "teardown_subtree"
        and outcome.get("issuer") == ProcessTerminationIssuer.INVOKE_TEARDOWN.value
        for outcome in child_outcomes
    ), "child process carries the teardown issuer, confirming it was post-exit cleanup"


def test_incident_5_reported_code_zero_and_discrepancy_reporting() -> None:
    from ralph.recovery.attempt_facts import TerminationRecord, has_return_code_discrepancy

    matching_term = TerminationRecord.create(
        TerminationIssuer.UNKNOWN,
        observed_returncode=0,
        reported_returncode=0,
        liveness_observed=True,
    )
    assert matching_term.reported_returncode == 0
    assert matching_term.observed_returncode == 0
    assert has_return_code_discrepancy(matching_term) is False, (
        "when observed and reported return codes are identical, no discrepancy is flagged"
    )

    conflicting_term = TerminationRecord.create(
        TerminationIssuer.UNKNOWN,
        observed_returncode=-15,
        reported_returncode=0,
        liveness_observed=True,
    )
    assert conflicting_term.reported_returncode == 0
    assert conflicting_term.observed_returncode == -15
    assert has_return_code_discrepancy(conflicting_term) is True, (
        "when observed code and reported code conflict for the process, discrepancy must be True"
    )


def test_incident_5_live_session_with_partial_progress_never_marked_spent_or_benched() -> None:
    from ralph.recovery.attempt_facts import AttemptFacts, TerminationRecord

    clock = FakeClock(start=0.0)
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock))

    facts = AttemptFacts(
        event_kinds=("tool_result",),
        tool_calls=("Read",),
        artifact_valid=False,
        workspace_changed=None,
        elapsed_to_terminal_ms=97_900,
        exit_status="exit_0",
        liveness_observed=True,
        termination=TerminationRecord.create(
            TerminationIssuer.UNKNOWN,
            observed_returncode=0,
            reported_returncode=0,
            liveness_observed=True,
        ),
    )

    outcome = controller.finalize_attempt(
        PHASE,
        "claude",
        facts=facts,
        attempt_failed=True,
    )

    assert outcome.kind.value in ("runtime_ended", "unknown"), (
        f"outcome must be non-agent failure (observed: {outcome.kind.value!r})"
    )
    assert controller.snapshot()["spent_agents"] == {}, (
        "a live session with partial progress must never be marked spent after one attempt "
        f"(observed spent: {controller.snapshot()['spent_agents']!r})"
    )
    assert controller.unavailability_store.is_available(PHASE, "claude") is True, (
        "a live session with partial progress must not be benched in unavailability store"
    )
    assert controller.ledger.is_available(PHASE, "claude") is True, (
        "the agent must remain available and selectable in the failure ledger"
    )


def test_incident_6_chronology_parent_graceful_exit_then_post_exit_watchdog_fire(
    tmp_path: Path,
) -> None:
    from ralph.agents.execution_state import OpenCodeExecutionStrategy
    from ralph.agents.invoke import (
        CompletionCheckOptions,
        OpenCodeResumableExitError,
        check_process_result,
    )
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.process.liveness import FakeLivenessProbe
    from ralph.recovery.attempt_facts import has_return_code_discrepancy

    options = CompletionCheckOptions(
        execution_strategy=OpenCodeExecutionStrategy(),
        workspace_path=tmp_path,
        liveness_probe=FakeLivenessProbe(active=True),
        required_artifact=RequiredArtifact(
            phase="planning",
            artifact_type="plan",
            artifact_path=".agent/artifacts/plan.md",
            markdown_path=None,
            normalizer=None,
        ),
        policy=TimeoutPolicy(
            idle_timeout_seconds=None,
            descendant_wait_timeout_seconds=2.0,
            descendant_wait_poll_seconds=0.5,
            parent_exit_grace_seconds=1.0,
        ),
    )
    recorder = TerminationRecorder()
    clock = FakeClock(start=0.0)

    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            _DescendantFakeHandle(has_descendants=True),
            "opencode",
            [INCIDENT_6_OPENCODE_TOOL_USE_LINE],
            options,
            _clock=clock,
            termination_recorder=recorder,
        )

    facts = excinfo.value.facts
    termination = facts.termination
    assert termination is not None, "termination record must be present on failure facts"
    assert termination.issuer is TerminationIssuer.POST_EXIT_WATCHDOG, (
        "parent termination record must carry issuer post_exit_watchdog "
        f"(observed: {termination.issuer!r})"
    )
    assert termination.observed_returncode == 0, (
        f"parent process exited gracefully with 0 (observed: {termination.observed_returncode!r})"
    )
    assert termination.reported_returncode == 0, (
        f"parent process reported code 0 (observed: {termination.reported_returncode!r})"
    )
    assert has_return_code_discrepancy(termination) is False, (
        "parent process observed and reported codes are both 0 so discrepancy must be False"
    )
    assert len(termination.log) >= 2, (
        f"termination log must record chronology (observed: {termination.log!r})"
    )
    assert termination.log[0].issuer is TerminationIssuer.AGENT_EXIT, (
        "chronology step 1: parent process exits first as agent_exit"
    )
    assert termination.log[1].issuer is TerminationIssuer.POST_EXIT_WATCHDOG, (
        "chronology step 2: post-exit watchdog fires afterwards on hung descendants"
    )


def test_incident_6_hung_descendant_is_ordinary_child_and_kill_is_runtime_attributed() -> None:
    from ralph.recovery.attempt_facts import AttemptFacts, TerminationRecord
    from ralph.recovery.attribution import Attribution, RuntimeCause, attribute_attempt
    from ralph.recovery.dead_agent_verdict import (
        DeadAgentVerdict,
        decide_dead_agent_verdict,
        verdict_benches_agent,
    )
    from ralph.recovery.typed_signal import runtime_issued_termination_is_not_agent_fault

    facts = AttemptFacts(
        event_kinds=("tool_result",),
        tool_calls=("read_file",),
        artifact_valid=False,
        workspace_changed=None,
        elapsed_to_terminal_ms=48_600,
        exit_status="exit_0",
        liveness_observed=True,
        termination=TerminationRecord.create(
            TerminationIssuer.POST_EXIT_WATCHDOG,
            observed_returncode=0,
            reported_returncode=0,
            liveness_observed=True,
        ),
    )

    verdict = attribute_attempt(facts)
    assert verdict.attribution is Attribution.RUNTIME, (
        "a watchdog kill of leftover descendants must be attributed to runtime "
        f"(observed attribution: {verdict.attribution!r})"
    )
    assert verdict.runtime_cause is RuntimeCause.KILL_POST_EXIT_WATCHDOG, (
        f"runtime cause must be post-exit watchdog (observed: {verdict.runtime_cause!r})"
    )
    assert verdict.cleared is True, (
        "the agent must be cleared when descendants are killed by the watchdog"
    )
    assert verdict.counts_toward_agent_state is False, (
        "descendant hang must not count toward the agent's failure state"
    )
    assert runtime_issued_termination_is_not_agent_fault(facts) is True, (
        "runtime-issued post-exit watchdog termination is not an agent fault"
    )

    dead_agent_verdict = decide_dead_agent_verdict(facts, verdict, probe_outcome=None)
    assert dead_agent_verdict.verdict is DeadAgentVerdict.NOT_AGENT_FAULT, (
        f"decided verdict must be not_agent_fault (observed: {dead_agent_verdict.verdict!r})"
    )
    assert verdict_benches_agent(dead_agent_verdict.verdict) is False, (
        "not_agent_fault verdict must never bench the agent"
    )


def test_incident_6_code_0_route_flags_discrepancy_only_when_conflicting() -> None:
    from ralph.recovery.attempt_facts import TerminationRecord

    non_conflicting = TerminationRecord.create(
        TerminationIssuer.POST_EXIT_WATCHDOG,
        observed_returncode=0,
        reported_returncode=0,
    )
    assert non_conflicting.return_code_discrepancy is False, (
        "when process manager and reported code both observe 0, discrepancy is False"
    )

    conflicting_kill = TerminationRecord.create(
        TerminationIssuer.POST_EXIT_WATCHDOG,
        observed_returncode=143,
        reported_returncode=0,
    )
    assert conflicting_kill.return_code_discrepancy is True, (
        "when process manager observes 143/SIGTERM while reported code is 0, discrepancy is True"
    )

    conflicting_sigterm = TerminationRecord.create(
        TerminationIssuer.POST_EXIT_WATCHDOG,
        observed_returncode=-15,
        reported_returncode=0,
    )
    assert conflicting_sigterm.return_code_discrepancy is True, (
        "when process manager observes -15 while reported code is 0, discrepancy is True"
    )


def test_incident_6_watchdog_failure_accounting_no_spent_no_bench_selectable() -> None:
    from ralph.recovery.attempt_facts import AttemptFacts, TerminationRecord

    clock = FakeClock(start=0.0)
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock))

    facts = AttemptFacts(
        event_kinds=("tool_result",),
        tool_calls=("read_file",),
        artifact_valid=False,
        workspace_changed=None,
        elapsed_to_terminal_ms=48_600,
        exit_status="exit_0",
        liveness_observed=True,
        termination=TerminationRecord.create(
            TerminationIssuer.POST_EXIT_WATCHDOG,
            observed_returncode=0,
            reported_returncode=0,
            liveness_observed=True,
        ),
    )

    outcome = controller.finalize_attempt(
        PHASE,
        "opencode",
        facts=facts,
        attempt_failed=True,
    )

    assert outcome.kind.value == "runtime_ended", (
        f"post-exit watchdog failure outcome must be runtime_ended (observed: {outcome.kind.value!r})"
    )
    assert controller.snapshot()["spent_agents"] == {}, (
        f"watchdog kill must not mark agent spent (observed: {controller.snapshot()['spent_agents']!r})"
    )
    assert controller.unavailability_store.is_available(PHASE, "opencode") is True, (
        "post-exit watchdog failure must not bench the agent in unavailability store"
    )
    assert controller.ledger.is_available(PHASE, "opencode") is True, (
        "agent must remain selectable in failure ledger"
    )
    entry = _ledger_entry(controller, "opencode")
    assert _counter(entry, "consecutive_unproductive") == 0, (
        f"runtime failure must not charge consecutive unproductive counter (observed: {entry!r})"
    )
