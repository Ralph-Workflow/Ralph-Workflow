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
from ralph.recovery.attribution import attribute_attempt
from ralph.recovery.canary_probe import ProbeOutcome
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.dead_agent_verdict import (
    decide_dead_agent_verdict,
    verdict_record,
    verdict_starts_cooldown,
)
from ralph.recovery.unavailability_reason import UnavailabilityReason

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.policy.models import PipelinePolicy

from tests.recovery._corpus_derive import CorpusCase, load_corpus, load_fixtures

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_replaying_the_labelled_corpus_yields_zero_false_cooldowns_and_reports_the_rate": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_replaying_the_labelled_corpus_yields_zero_false_benches_and_reports_the_rate with an unchanged body"
    ),
    "test_no_negative_is_ever_left_on_a_dead_agent_cooldown (renamed)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_no_negative_is_ever_marked_spent_or_left_unavailable with an unchanged body"
    ),
    "test_the_genuine_positive_case_cools_down_on_the_first_attempt_when_the_probe_fails (renamed)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_the_genuine_positive_case_benches_on_the_first_attempt_when_the_probe_fails with an unchanged body"
    ),
    "test_every_corpus_case_expected_attribution_matches_attribute_attempt": (
        "operator rule: an agent is either on cooldown or not; the check passes the peer signal from the fixture so peer-identical failures are attributed to peers_fail_identically"
    ),
    "test_every_corpus_case_produces_a_schema_v2_verdict_record_with_its_termination": (
        "operator rule: an agent is either on cooldown or not; the check passes the peer signal from the fixture when building the verdict record"
    ),
    "test_ac8_corpus_cases_are_attributed_to_their_intended_rules": (
        "operator rule: an agent is either on cooldown or not; the check asserts that the AC8 corpus cases pin their intended attribution rules (satisfied_outcome and peers_fail_identically) rather than mid_run_activity"
    ),
    "test_every_labelled_negative_stays_clean_and_the_corpus_contains_each_incident": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; the check is unchanged and now reads the renamed cooldown vocabulary (benches_agent -> starts_cooldown, verdict_benches_agent -> verdict_starts_cooldown, false_bench_* -> false_cooldown_*, CANDIDATE_EXHAUSTED -> NO_RUNNABLE_CANDIDATE, exhaustion_reason -> halt_reason, 'spent/unavailable' -> 'on cooldown')"
    ),
    "test_no_negative_is_ever_left_on_a_dead_agent_cooldown": "operator rule: an agent is either on cooldown or not; the spent state is gone, so a negative must not be excluded without a bench and any exclusion must expire once the clock passes it",
    "test_the_genuine_positive_case_cools_down_on_the_first_attempt_when_the_probe_fails": "operator rule: an agent is either on cooldown or not; the bench is a finite dead_agent_no_progress cooldown, so the test asserts it expires instead of asserting the agent is not spent",
}

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
    dead_agent_cooldown: bool
    excluded_without_cooldown: bool
    cooldown_expires: bool
    probes_run: int


def _policy() -> PipelinePolicy:
    with tempfile.TemporaryDirectory() as directory:
        return load_policy(Path(directory) / ".agent").pipeline


def _agents_by_case() -> dict[str, str]:
    return {str(fixture["id"]): str(fixture["agent"]) for fixture in load_fixtures()}


def _replay(case: CorpusCase, agent: str, probe_outcome: ProbeOutcome | None) -> Replay:
    probes: list[str] = []

    def probe_runner(_phase: str, probed_agent: str) -> ProbeOutcome | None:
        probes.append(probed_agent)
        return probe_outcome

    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
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
    dead_agent_cooldown = isinstance(reasons, dict) and reasons.get(agent) == DEAD
    store = controller.unavailability_store
    excluded_without_cooldown = not store.is_available(PHASE, agent) and not dead_agent_cooldown
    wait_ms = store.earliest_unavailable_wait_ms(PHASE, [agent])
    clock.advance(wait_ms / 1000.0 + 0.001)
    cooldown_expires = (
        "spent_agents" not in controller.snapshot()
        and store.is_available(PHASE, agent)
        and controller.ledger.status(PHASE, agent).is_eligible
    )
    return Replay(
        case.id,
        probe_outcome,
        dead_agent_cooldown,
        excluded_without_cooldown,
        cooldown_expires,
        len(probes),
    )


def _outcomes_for(case: CorpusCase) -> tuple[ProbeOutcome | None, ...]:
    termination = case.facts.termination
    unattributed = termination is None or termination.issuer is TerminationIssuer.UNKNOWN
    return HEALTHY_AGENT_PROBE_OUTCOMES if unattributed else EVERY_PROBE_OUTCOME


@pytest.fixture(scope="module")
def corpus() -> tuple[CorpusCase, ...]:
    return load_corpus()


@pytest.fixture(scope="module")
def corpus_replays(
    corpus: tuple[CorpusCase, ...],
) -> dict[tuple[str, ProbeOutcome | None], Replay]:
    agents = _agents_by_case()
    return {
        (case.id, outcome): _replay(case, agents[case.id], outcome)
        for case in corpus
        for outcome in _outcomes_for(case)
    }


@pytest.fixture(scope="module")
def negative_replays(
    corpus: tuple[CorpusCase, ...],
    corpus_replays: Mapping[tuple[str, ProbeOutcome | None], Replay],
) -> tuple[Replay, ...]:
    return tuple(
        corpus_replays[(case.id, outcome)]
        for case in corpus
        if case.label == "negative"
        for outcome in _outcomes_for(case)
    )


def _negative_replays_for(
    case: CorpusCase,
    corpus_replays: Mapping[tuple[str, ProbeOutcome | None], Replay],
) -> list[Replay]:
    return [corpus_replays[(case.id, outcome)] for outcome in _outcomes_for(case)]


def test_replaying_the_labelled_corpus_yields_zero_false_cooldowns_and_reports_the_rate(
    capsys: pytest.CaptureFixture[str],
    negative_replays: tuple[Replay, ...],
) -> None:
    replays = negative_replays
    false_cooldowns = [replay for replay in replays if replay.dead_agent_cooldown]
    report = f"false_cooldowns={len(false_cooldowns)}/{len(replays)}"

    with capsys.disabled():
        print(f"\ndead-agent corpus replay: {report}")

    assert report == f"false_cooldowns=0/{len(replays)}"
    assert len(replays) >= 3 * 9, (
        "every negative must be replayed under every healthy probe outcome"
    )


def test_no_negative_is_ever_left_on_a_dead_agent_cooldown(
    negative_replays: tuple[Replay, ...],
) -> None:
    offenders = [
        replay
        for replay in negative_replays
        if replay.excluded_without_cooldown or not replay.cooldown_expires
    ]

    assert offenders == []


def test_the_genuine_positive_case_is_cleared_by_a_passing_probe(
    corpus: tuple[CorpusCase, ...],
    corpus_replays: Mapping[tuple[str, ProbeOutcome | None], Replay],
) -> None:
    agy = next(case for case in corpus if case.label == "positive")

    assert corpus_replays[(agy.id, ProbeOutcome.PASSED)].dead_agent_cooldown is False


def test_the_genuine_positive_case_cools_down_on_the_first_attempt_when_the_probe_fails(
    corpus: tuple[CorpusCase, ...],
    corpus_replays: Mapping[tuple[str, ProbeOutcome | None], Replay],
) -> None:
    positives = [case for case in corpus if case.label == "positive"]

    assert [case.id for case in positives] == ["agy_quota_error_step"]
    replay = corpus_replays[(positives[0].id, ProbeOutcome.FAILED_REPRODUCING_NO_WORK)]

    assert replay.dead_agent_cooldown is True
    assert replay.excluded_without_cooldown is False
    assert replay.cooldown_expires is True
    assert replay.probes_run == 1


@pytest.mark.parametrize("outcome", [ProbeOutcome.INCONCLUSIVE, None])
def test_the_genuine_positive_case_stays_unknown_when_the_probe_cannot_decide(
    outcome: ProbeOutcome | None,
    corpus: tuple[CorpusCase, ...],
    corpus_replays: Mapping[tuple[str, ProbeOutcome | None], Replay],
) -> None:
    agy = next(case for case in corpus if case.label == "positive")

    assert corpus_replays[(agy.id, outcome)].dead_agent_cooldown is False


def test_every_labelled_negative_stays_clean_and_the_corpus_contains_each_incident(
    corpus: tuple[CorpusCase, ...],
    corpus_replays: Mapping[tuple[str, ProbeOutcome | None], Replay],
) -> None:
    fixtures = load_fixtures()
    incidents: set[int] = set()
    for fixture in fixtures:
        declared = fixture.get("incidents") or [fixture.get("incident")]
        assert isinstance(declared, list)
        incidents.update(value for value in declared if isinstance(value, int))
    negatives = [case for case in corpus if case.label == "negative"]

    assert incidents >= REQUIRED_INCIDENTS, (
        f"the corpus lacks incidents {sorted(REQUIRED_INCIDENTS - incidents)}"
    )
    assert negatives
    for case in negatives:
        assert case.expected_cooldown is False
        assert case.expected_verdict != "agent_dead"
        assert not any(
            replay.dead_agent_cooldown for replay in _negative_replays_for(case, corpus_replays)
        )


def test_a_new_fixture_is_replayed_without_editing_the_test(
    corpus: tuple[CorpusCase, ...],
) -> None:
    ids = {case.id for case in corpus}

    assert {
        "claude_live_session_end_no_completion",
        "opencode_post_exit_watchdog_descendant_hang",
    } <= ids


def test_the_labelled_corpus_covers_incidents_1_through_7() -> None:
    fixtures = load_fixtures()
    incidents: set[int] = set()
    for fixture in fixtures:
        declared = fixture.get("incidents") or [fixture.get("incident")]
        assert isinstance(declared, list)
        incidents.update(value for value in declared if isinstance(value, int))

    assert set(range(1, 8)) <= incidents, (
        f"the corpus lacks incidents {sorted(set(range(1, 8)) - incidents)}"
    )
    assert any(fixture.get("incident") == 7 for fixture in fixtures), (
        "the corpus must hold the incident 7 probe half (trust dialog)"
    )


def test_the_replay_sample_covers_every_negative_under_every_applicable_probe_outcome(
    corpus: tuple[CorpusCase, ...],
    negative_replays: tuple[Replay, ...],
) -> None:
    negatives = [case for case in corpus if case.label == "negative"]

    assert negatives
    unattributed = [
        case
        for case in negatives
        if case.facts.termination is None
        or case.facts.termination.issuer is TerminationIssuer.UNKNOWN
    ]
    unattributed_ids = {case.id for case in unattributed}
    attributed = [case for case in negatives if case.id not in unattributed_ids]
    expected_total = len(unattributed) * len(HEALTHY_AGENT_PROBE_OUTCOMES) + len(attributed) * len(
        EVERY_PROBE_OUTCOME
    )

    assert expected_total == len(negative_replays), (
        "the reported false-bench rate must cover the full negative-by-outcome cross-product"
    )
    replayed_counts: dict[str, int] = {}
    for replay in negative_replays:
        replayed_counts[replay.case_id] = replayed_counts.get(replay.case_id, 0) + 1
    assert replayed_counts == {case.id: len(_outcomes_for(case)) for case in negatives}


def test_every_corpus_case_produces_a_schema_v2_verdict_record_with_its_termination(
    corpus: tuple[CorpusCase, ...],
) -> None:
    for case in corpus:
        for outcome in _outcomes_for(case):
            verdict = decide_dead_agent_verdict(
                case.facts,
                attribute_attempt(
                    case.facts,
                    peer_failures_identical=case.peers_fail_identically,
                ),
                outcome,
            )
            record = verdict_record(PHASE, case.id, verdict)

            assert record["schema_version"] == 2
            assert record["verdict"] == verdict.verdict.value
            assert record["probe_outcome"] == (outcome.value if outcome is not None else None)
            assert (record["termination"] is None) is (case.facts.termination is None)
            assert verdict.termination is case.facts.termination
            assert record["return_code_discrepancy"] == verdict.return_code_discrepancy
            if case.label == "negative":
                assert verdict_starts_cooldown(verdict.verdict) is False
                assert record["verdict"] != "agent_dead"
            else:
                expected_cooldown = outcome is ProbeOutcome.FAILED_REPRODUCING_NO_WORK
                assert verdict_starts_cooldown(verdict.verdict) is expected_cooldown


def test_every_corpus_case_expected_attribution_matches_attribute_attempt(
    corpus: tuple[CorpusCase, ...],
) -> None:
    from tests.recovery._corpus_derive import ALLOWED_ATTRIBUTIONS

    mismatches: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for case in corpus:
        assert case.expected_attribution in ALLOWED_ATTRIBUTIONS
        seen.add(case.expected_attribution)
        recorded = attribute_attempt(
            case.facts,
            peer_failures_identical=case.peers_fail_identically,
        )
        if recorded.attribution.value != case.expected_attribution:
            mismatches.append((case.id, case.expected_attribution, recorded.attribution.value))
        if case.expected_attribution in {"runtime", "task_or_environment"}:
            assert case.expected_cooldown is False
            assert case.expected_verdict != "agent_dead"
        if case.label == "positive":
            assert case.expected_cooldown is True
            assert case.expected_verdict == "agent_dead"
        if recorded.cleared or recorded.attribution.value in {
            "runtime",
            "task_or_environment",
        }:
            assert case.expected_cooldown is False
        for outcome in _outcomes_for(case):
            verdict = decide_dead_agent_verdict(case.facts, recorded, outcome)
            record = verdict_record(PHASE, case.id, verdict)
            attrib = record.get("attribution")
            assert isinstance(attrib, dict)
            assert attrib.get("attribution") == recorded.attribution.value
    assert mismatches == [], (
        "corpus expected_attribution metadata must match attribute_attempt; "
        f"mismatches (id, expected, recorded)={mismatches!r}"
    )
    assert {"runtime", "task_or_environment", "unknown"} <= seen


def test_ac8_corpus_cases_are_attributed_to_their_intended_rules(
    corpus: tuple[CorpusCase, ...],
) -> None:
    cases = {case.id: case for case in corpus}
    outcome_case = cases["outcome_already_exists"]
    outcome_attrib = attribute_attempt(
        outcome_case.facts,
        peer_failures_identical=outcome_case.peers_fail_identically,
    )
    assert outcome_attrib.reason == "satisfied_outcome"
    assert outcome_attrib.attribution.value == "task_or_environment"

    peers_case = cases["peers_fail_identically"]
    peers_attrib = attribute_attempt(
        peers_case.facts,
        peer_failures_identical=peers_case.peers_fail_identically,
    )
    assert peers_attrib.reason == "peers_fail_identically"
    assert peers_attrib.attribution.value == "task_or_environment"
