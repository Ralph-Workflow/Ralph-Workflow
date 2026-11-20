from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import AgentConfig
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.canary_probe import (
    PROBE_COULD_NOT_RUN_DETAIL,
    ProbeOutcome,
    ProbeResult,
)
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.dead_agent_probe import (
    PROBE_REASON_AGENT_RESOLUTION_FAILED,
    PROBE_REASON_AGENT_UNRESOLVED,
    PROBE_REASON_BUDGET_EXHAUSTED,
    PROBE_REASON_INTERNAL_ERROR,
    build_dead_agent_probe_runner,
)
from ralph.recovery.unavailability_reason import UnavailabilityReason

if TYPE_CHECKING:
    from collections.abc import Callable

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_the_runner_resolves_the_agent_and_returns_the_probe_outcome": (
        "build_dead_agent_probe_runner widened to return the richer ProbeResult carrier; "
        "assertion checks result.outcome is ProbeOutcome.FAILED_REPRODUCING_NO_WORK"
    ),
    "test_the_runner_stops_probing_once_the_per_run_budget_is_spent": (
        "build_dead_agent_probe_runner widened to return the richer ProbeResult carrier; "
        "assertion checks results[:2] .outcome are ProbeOutcome.PASSED"
    ),
    "test_an_unresolvable_agent_yields_no_outcome_and_spends_no_budget": (
        "build_dead_agent_probe_runner widened to return the richer ProbeResult carrier; "
        "assertion checks result.outcome is ProbeOutcome.PASSED"
    ),
    "test_the_runner_stops_probing_once_the_per_run_budget_is_spent (budget route reason)": (
        "the fail-open routes now return an inconclusive ProbeResult carrying the reason "
        "that fired instead of a bare None, so the budget route returns the "
        "budget_exhausted reason and the assertion pins that carrier"
    ),
    "test_an_unresolvable_agent_yields_no_outcome_and_spends_no_budget (unresolved route reason)": (
        "the fail-open routes now return an inconclusive ProbeResult carrying the reason "
        "that fired instead of a bare None, so the unresolved-agent route returns the "
        "agent_unresolved reason and the assertion pins that carrier"
    ),
    "test_a_crashing_probe_yields_no_outcome (probe crash route reason)": (
        "the fail-open routes now return an inconclusive ProbeResult carrying the reason "
        "that fired instead of a bare None, so the probe-crash route returns the "
        "probe_internal_error reason and the assertion pins that carrier"
    ),
    "test_the_max_probe_route_reports_the_budget_reason_and_leaves_the_agent_available": (
        "new coverage: the per-run probe budget route returns an inconclusive probe "
        "result whose reason names the budget, and the verdict record carries it while "
        "the agent stays selectable"
    ),
    "test_the_unresolved_agent_route_reports_its_reason_and_leaves_the_agent_available": (
        "new coverage: the unresolved-agent route returns an inconclusive probe result "
        "whose reason names the unresolved agent, and the verdict record carries it "
        "while the agent stays selectable"
    ),
    "test_the_crashing_probe_route_reports_the_internal_error_reason_and_leaves_the_agent_available": (
        "new coverage: the probe-crash route returns an inconclusive probe result whose "
        "reason names the probe failure, and the verdict record carries it while the "
        "agent stays selectable"
    ),
    "test_the_agent_resolution_failure_route_reports_its_reason_and_leaves_the_agent_available": (
        "new coverage: the agent-resolution-failure route returns an inconclusive probe "
        "result whose reason names the failed resolution instead of a bare None, and the "
        "verdict record carries it while the agent stays selectable"
    ),
}

PHASE = "development"
AGENT = "claude"
UNKNOWN_AGENT = "unknown"


def _result(outcome: ProbeOutcome) -> ProbeResult:
    return ProbeResult(outcome=outcome, detail="scripted")


def _fail_open(reason: str) -> ProbeResult:
    return ProbeResult(
        outcome=ProbeOutcome.INCONCLUSIVE,
        detail=PROBE_COULD_NOT_RUN_DETAIL,
        reason=reason,
    )


def _resolver(name: str) -> AgentConfig | None:
    return AgentConfig(cmd=name) if name != "unknown" else None


def _failed_facts() -> AttemptFacts:
    return AttemptFacts(
        artifact_valid=False,
        workspace_changed=False,
        exit_status="exit_1",
        termination=TerminationRecord.create(
            TerminationIssuer.AGENT_EXIT,
            observed_returncode=1,
            reported_returncode=1,
        ),
    )


def _controller(runner: Callable[[str, str], ProbeResult | None]) -> RecoveryController:
    return RecoveryController(
        options=RecoveryControllerOptions(
            clock=FakeClock(start=0.0),
            dead_agent_probe_runner=runner,
        )
    )


def _assert_no_dead_agent_cooldown(controller: RecoveryController, agent: str) -> None:
    reasons = controller.unavailability_store.snapshot().get("unavailability_reasons", {})
    assert isinstance(reasons, dict)
    assert reasons.get(agent) != str(UnavailabilityReason.DEAD_AGENT_NO_PROGRESS)


def test_the_runner_resolves_the_agent_and_returns_the_probe_outcome() -> None:
    seen: list[str] = []

    def probe(config: AgentConfig) -> ProbeResult:
        seen.append(config.cmd)
        return _result(ProbeOutcome.FAILED_REPRODUCING_NO_WORK)

    runner = build_dead_agent_probe_runner(_resolver, probe, max_probes=2)

    result = runner("development", "claude")
    assert result is not None
    assert result.outcome is ProbeOutcome.FAILED_REPRODUCING_NO_WORK
    assert seen == ["claude"]


def test_the_runner_stops_probing_once_the_per_run_budget_is_spent() -> None:
    calls: list[str] = []

    def probe(config: AgentConfig) -> ProbeResult:
        calls.append(config.cmd)
        return _result(ProbeOutcome.PASSED)

    runner = build_dead_agent_probe_runner(_resolver, probe, max_probes=2)

    results = [runner("development", "claude") for _ in range(4)]

    assert len(calls) == 2
    assert [r.outcome for r in results[:2] if r is not None] == [
        ProbeOutcome.PASSED,
        ProbeOutcome.PASSED,
    ]
    assert results[2:] == [
        _fail_open(PROBE_REASON_BUDGET_EXHAUSTED),
        _fail_open(PROBE_REASON_BUDGET_EXHAUSTED),
    ]


def test_an_unresolvable_agent_yields_no_outcome_and_spends_no_budget() -> None:
    calls: list[str] = []

    def probe(config: AgentConfig) -> ProbeResult:
        calls.append(config.cmd)
        return _result(ProbeOutcome.PASSED)

    runner = build_dead_agent_probe_runner(_resolver, probe, max_probes=1)

    assert runner("development", "unknown") == _fail_open(PROBE_REASON_AGENT_UNRESOLVED)
    result = runner("development", "claude")
    assert result is not None
    assert result.outcome is ProbeOutcome.PASSED
    assert calls == ["claude"]


def test_a_crashing_probe_yields_no_outcome() -> None:
    def probe(config: AgentConfig) -> ProbeResult:
        raise RuntimeError(config.cmd)

    runner = build_dead_agent_probe_runner(_resolver, probe, max_probes=1)

    assert runner("development", "claude") == _fail_open(PROBE_REASON_INTERNAL_ERROR)


def test_the_max_probe_route_reports_the_budget_reason_and_leaves_the_agent_available() -> None:
    calls: list[str] = []

    def probe(config: AgentConfig) -> ProbeResult:
        calls.append(config.cmd)
        return _result(ProbeOutcome.PASSED)

    runner = build_dead_agent_probe_runner(_resolver, probe, max_probes=1)
    spent = runner(PHASE, AGENT)
    assert spent is not None
    assert spent.outcome is ProbeOutcome.PASSED
    controller = _controller(runner)

    controller.finalize_attempt(PHASE, AGENT, facts=_failed_facts(), attempt_failed=True)

    record = controller.last_verdict_record
    assert record is not None
    assert record["schema_version"] == 3
    assert record["probe_reason"] == PROBE_REASON_BUDGET_EXHAUSTED
    assert record["probe_outcome"] == "inconclusive"
    assert calls == [AGENT]
    assert controller.unavailability_store.is_available(PHASE, AGENT) is True
    _assert_no_dead_agent_cooldown(controller, AGENT)


def test_the_unresolved_agent_route_reports_its_reason_and_leaves_the_agent_available() -> None:
    calls: list[str] = []

    def probe(config: AgentConfig) -> ProbeResult:
        calls.append(config.cmd)
        return _result(ProbeOutcome.PASSED)

    runner = build_dead_agent_probe_runner(_resolver, probe, max_probes=2)
    controller = _controller(runner)

    controller.finalize_attempt(PHASE, UNKNOWN_AGENT, facts=_failed_facts(), attempt_failed=True)

    record = controller.last_verdict_record
    assert record is not None
    assert record["schema_version"] == 3
    assert record["probe_reason"] == PROBE_REASON_AGENT_UNRESOLVED
    assert record["probe_outcome"] == "inconclusive"
    assert calls == []
    assert controller.unavailability_store.is_available(PHASE, UNKNOWN_AGENT) is True
    _assert_no_dead_agent_cooldown(controller, UNKNOWN_AGENT)


def test_the_crashing_probe_route_reports_the_internal_error_reason_and_leaves_the_agent_available() -> (
    None
):
    def probe(config: AgentConfig) -> ProbeResult:
        raise RuntimeError(config.cmd)

    runner = build_dead_agent_probe_runner(_resolver, probe, max_probes=2)
    controller = _controller(runner)

    controller.finalize_attempt(PHASE, AGENT, facts=_failed_facts(), attempt_failed=True)

    record = controller.last_verdict_record
    assert record is not None
    assert record["schema_version"] == 3
    assert record["probe_reason"] == PROBE_REASON_INTERNAL_ERROR
    assert record["probe_outcome"] == "inconclusive"
    assert controller.unavailability_store.is_available(PHASE, AGENT) is True
    _assert_no_dead_agent_cooldown(controller, AGENT)


def test_the_agent_resolution_failure_route_reports_its_reason_and_leaves_the_agent_available() -> (
    None
):
    calls: list[str] = []

    def resolve_agent(name: str) -> AgentConfig | None:
        raise OSError(f"agent registry unavailable for {name}")

    def probe(config: AgentConfig) -> ProbeResult:
        calls.append(config.cmd)
        return _result(ProbeOutcome.PASSED)

    runner = build_dead_agent_probe_runner(resolve_agent, probe, max_probes=2)
    result = runner(PHASE, AGENT)
    assert result is not None
    assert result.outcome is ProbeOutcome.INCONCLUSIVE
    assert result.detail == PROBE_COULD_NOT_RUN_DETAIL
    assert result.reason == PROBE_REASON_AGENT_RESOLUTION_FAILED
    controller = _controller(runner)

    controller.finalize_attempt(PHASE, AGENT, facts=_failed_facts(), attempt_failed=True)

    record = controller.last_verdict_record
    assert record is not None
    assert record["schema_version"] == 3
    assert record["probe_reason"] == PROBE_REASON_AGENT_RESOLUTION_FAILED
    assert record["probe_outcome"] == "inconclusive"
    assert calls == []
    assert controller.unavailability_store.is_available(PHASE, AGENT) is True
    _assert_no_dead_agent_cooldown(controller, AGENT)
