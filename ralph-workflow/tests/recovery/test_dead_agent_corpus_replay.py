from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_retry_intent import AgentRetryIntent
from ralph.pipeline.events import AgentFailureEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.attempt_facts import TerminationIssuer
from ralph.recovery.canary_probe import ProbeOutcome
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy

from tests.recovery._corpus_derive import CorpusCase, load_corpus, load_fixtures

PHASE = "development"
PEER = "peer_agent"
DEAD = str(UnavailabilityReason.DEAD_AGENT_NO_PROGRESS)
HEALTHY_AGENT_PROBE_OUTCOMES = (ProbeOutcome.PASSED, ProbeOutcome.INCONCLUSIVE, None)
EVERY_PROBE_OUTCOME = (*HEALTHY_AGENT_PROBE_OUTCOMES, ProbeOutcome.FAILED_REPRODUCING_NO_WORK)
REQUIRED_INCIDENTS = frozenset({1, 2, 3, 4, 5, 6})


@dataclass(frozen=True)
class Replay:
    case_id: str
    probe_outcome: ProbeOutcome | None
    benched: bool
    spent: bool
    probes_run: int


def _policy() -> PipelinePolicy:
    with tempfile.TemporaryDirectory() as directory:
        return load_policy(Path(directory) / ".agent").pipeline


def _agent_of(case_id: str) -> str:
    fixtures = {str(fixture["id"]): fixture for fixture in load_fixtures()}
    return str(fixtures[case_id]["agent"])


def _replay(case: CorpusCase, probe_outcome: ProbeOutcome | None) -> Replay:
    agent = _agent_of(case.id)
    probes: list[str] = []

    def probe_runner(_phase: str, probed_agent: str) -> ProbeOutcome | None:
        probes.append(probed_agent)
        return probe_outcome

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=FakeClock(start=0.0),
            dead_agent_probe_runner=probe_runner,
        )
    )
    state = PipelineState(
        phase=PHASE,
        phase_chains={PHASE: AgentChainState(agents=[agent, PEER], current_index=0, retries=0)},
        last_connectivity_state="online",
        agent_retry_intent=AgentRetryIntent(),
    )
    event = AgentFailureEvent(
        phase=PHASE,
        agent=agent,
        artifact_valid=case.facts.artifact_valid,
        workspace_changed=case.facts.workspace_changed,
        facts=case.facts,
    )

    reduce(state, event, _policy(), recovery=controller)

    reasons = controller.unavailability_store.snapshot().get("unavailability_reasons", {})
    benched = isinstance(reasons, dict) and reasons.get(agent) == DEAD
    spent = (
        controller.ledger.status(PHASE, agent).is_spent
        or bool(controller.snapshot()["spent_agents"])
        or (not controller.unavailability_store.is_available(PHASE, agent) and not benched)
    )
    return Replay(case.id, probe_outcome, benched, spent, len(probes))


def _outcomes_for(case: CorpusCase) -> tuple[ProbeOutcome | None, ...]:
    termination = case.facts.termination
    unattributed = termination is None or termination.issuer is TerminationIssuer.UNKNOWN
    return HEALTHY_AGENT_PROBE_OUTCOMES if unattributed else EVERY_PROBE_OUTCOME


def _negative_replays() -> list[Replay]:
    return [
        _replay(case, outcome)
        for case in load_corpus()
        if case.label == "negative"
        for outcome in _outcomes_for(case)
    ]


def test_replaying_the_labelled_corpus_yields_zero_false_benches_and_reports_the_rate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    replays = _negative_replays()
    false_benches = [replay for replay in replays if replay.benched]
    report = f"false_benches={len(false_benches)}/{len(replays)}"

    with capsys.disabled():
        print(f"\ndead-agent corpus replay: {report}")

    assert report == f"false_benches=0/{len(replays)}"
    assert len(replays) >= 3 * 9, (
        "every negative must be replayed under every healthy probe outcome"
    )


def test_no_negative_is_ever_marked_spent_or_left_unavailable() -> None:
    offenders = [replay for replay in _negative_replays() if replay.spent]

    assert offenders == []


def test_the_genuine_agy_case_is_cleared_by_a_passing_probe() -> None:
    agy = next(case for case in load_corpus() if case.label == "positive")

    assert _replay(agy, ProbeOutcome.PASSED).benched is False


def test_the_genuine_agy_case_benches_on_the_first_attempt_when_the_probe_fails() -> None:
    positives = [case for case in load_corpus() if case.label == "positive"]

    assert [case.id for case in positives] == ["agy_quota_error_step"]
    replay = _replay(positives[0], ProbeOutcome.FAILED_REPRODUCING_NO_WORK)

    assert replay.benched is True
    assert replay.spent is False
    assert replay.probes_run == 1


@pytest.mark.parametrize("outcome", [ProbeOutcome.INCONCLUSIVE, None])
def test_the_genuine_agy_case_stays_unknown_when_the_probe_cannot_decide(
    outcome: ProbeOutcome | None,
) -> None:
    agy = next(case for case in load_corpus() if case.label == "positive")

    assert _replay(agy, outcome).benched is False


def test_every_labelled_negative_stays_clean_and_the_corpus_contains_each_incident() -> None:
    fixtures = load_fixtures()
    incidents: set[int] = set()
    for fixture in fixtures:
        declared = fixture.get("incidents") or [fixture.get("incident")]
        assert isinstance(declared, list)
        incidents.update(value for value in declared if isinstance(value, int))
    negatives = [case for case in load_corpus() if case.label == "negative"]

    assert incidents >= REQUIRED_INCIDENTS, (
        f"the corpus lacks incidents {sorted(REQUIRED_INCIDENTS - incidents)}"
    )
    assert negatives
    for case in negatives:
        assert case.expected_bench is False
        assert case.expected_verdict != "agent_dead"
        assert not any(replay.benched for replay in _negative_replays_for(case))


def _negative_replays_for(case: CorpusCase) -> list[Replay]:
    return [_replay(case, outcome) for outcome in _outcomes_for(case)]


def test_a_new_fixture_is_replayed_without_editing_the_test() -> None:
    ids = {case.id for case in load_corpus()}

    assert {
        "claude_live_session_end_no_completion",
        "opencode_post_exit_watchdog_descendant_hang",
    } <= ids
