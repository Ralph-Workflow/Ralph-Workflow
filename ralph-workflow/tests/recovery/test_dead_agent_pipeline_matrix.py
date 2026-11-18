from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

import pytest

from ralph.checked_accessors import as_mapping, as_sequence
from ralph.pipeline.agent_entry_point import (
    AgentEntryPoint,
    registered_entry_points,
    registered_runners,
)
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer
from ralph.recovery.canary_probe import ProbeOutcome
from ralph.recovery.controller import RecoveryController
from ralph.recovery.unavailability_reason import (
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    UnavailabilityReason,
)
from tests.recovery import pipeline_matrix_scenarios
from tests.recovery._corpus_derive import CorpusCase, load_corpus
from tests.recovery.pipeline_matrix_scenarios import (
    AGENT_A,
    AGENT_B,
    AGENTS,
    DEAD_SIGNAL,
    PHASE,
    SCENARIOS,
    MatrixScenario,
    corpus_cases,
    corpus_scenarios,
    matrix_entry_points,
    run_matrix_scenario,
    run_matrix_scenario_with_selection,
    run_via_finalize_attempt,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_ac12_unavailability_store_snapshot_restore_round_trip": (
        "assert prune_expired via public return code rather than inspecting private "
        "serialized snapshot timeouts key per single owner policy"
    ),
}


_ENTRY_POINT_MODULES = (
    "ralph.pipeline.runner",
    "ralph.pipeline.parallel.worker_runtime",
    "ralph.pipeline._integration_continuation",
    "ralph.pipeline.conflict_resolution.session",
    "ralph.pipeline.plumbing.commit_plumbing",
    "ralph.pipeline.plumbing.smoke_plumbing",
    "ralph.project_policy.cli_integration",
    "ralph._session_runtime_runtime",
    "ralph.recovery.canary_probe",
)

for _module_name in _ENTRY_POINT_MODULES:
    import_module(_module_name)

ENTRY_POINTS = registered_entry_points()
NON_PROBE_ENTRY_POINTS = tuple(
    entry_point for entry_point in ENTRY_POINTS if entry_point is not AgentEntryPoint.CANARY_PROBE
)
MAIN_PHASE_LOOP = AgentEntryPoint.MAIN_PHASE_LOOP
ALL_SCENARIOS = (*SCENARIOS, *corpus_scenarios())
_NEGATIVE_CORPUS_IDS = frozenset(case.id for case in corpus_cases() if case.label == "negative")
NEGATIVE_CORPUS_SCENARIOS = tuple(
    scenario for scenario in ALL_SCENARIOS if scenario.corpus_id in _NEGATIVE_CORPUS_IDS
)
DEAD = str(UnavailabilityReason.DEAD_AGENT_NO_PROGRESS)
_BENCH_POLICY = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY[UnavailabilityReason.DEAD_AGENT_NO_PROGRESS]
_PROBE_MODE_CONTRACT = (
    "probe-mode contract (docs/agents/broken-agent-detection.md): the canary probe "
    "records facts and the issuer and reports the outcome, but it runs no nested "
    "probe, benches nothing and charges no ledger"
)


def _scenario_ids(scenarios: tuple[MatrixScenario, ...]) -> list[str]:
    return [scenario.name for scenario in scenarios]


def _scenario(name: str) -> MatrixScenario:
    return next(scenario for scenario in SCENARIOS if scenario.name == name)


def _assert_never_spent(
    observation: MatrixScenario.Observation, controller: RecoveryController
) -> None:
    snapshot = controller.snapshot()
    assert observation.ledger_status != "spent"
    assert snapshot["spent_agents"] == {}


def _assert_one_ordinary_ledger_charge(
    observation: MatrixScenario.Observation,
    agent: str,
) -> None:
    entries = observation.ledger_entries
    assert len(entries) == 1, (
        "AC7 ordinary ledger accounting: a probe that produced no verdict leaves "
        f"exactly one ledger entry for the agent; observed entries={entries!r}"
    )
    entry = as_mapping(next(iter(entries.values())))
    assert entry.get("phase") == PHASE
    assert entry.get("agent") == agent
    assert entry.get("is_spent") is False
    assert entry.get("cooldown_until_ms") is None
    assert entry.get("cooldown_reason") is None
    assert entry.get("unavailable_reason") is None


def _assert_corpus_expectations(
    scenario: MatrixScenario,
    observation: MatrixScenario.Observation,
    controller: RecoveryController,
) -> None:
    case = next(item for item in corpus_cases() if item.id == scenario.corpus_id)
    agent = scenario.attempts[-1].agent
    record = observation.verdict_record if isinstance(observation.verdict_record, dict) else {}
    reasons = controller.unavailability_store.snapshot().get("unavailability_reasons", {})
    assert case.expected_attribution in {"agent", "runtime", "task_or_environment", "unknown"}
    if case.expected_attribution in {"runtime", "task_or_environment"}:
        assert case.expected_bench is False
        assert case.expected_verdict != "agent_dead"
    if case.label == "negative":
        assert observation.outcome_kind != "agent_dead"
        assert observation.availability is True
        assert reasons.get(agent) != DEAD
        if isinstance(record, dict):
            assert record.get("verdict") != "agent_dead"
        return
    assert case.expected_bench is True
    assert case.expected_verdict == "agent_dead"
    assert observation.invocations == (agent,)
    if scenario.probe_outcome is ProbeOutcome.FAILED_REPRODUCING_NO_WORK:
        assert observation.outcome_kind == "agent_dead"
        assert observation.availability is False
        assert observation.probe_calls == (agent,)
        assert reasons.get(agent) == DEAD
    else:
        assert observation.outcome_kind != "agent_dead"
        assert observation.availability is True
        assert reasons.get(agent) != DEAD


def _assert_probe_mode_records_facts_and_issuer(
    observation: MatrixScenario.Observation,
) -> None:
    record = observation.verdict_record
    if record is None:
        return
    record_mapping = as_mapping(record)
    assert "facts" in record_mapping, _PROBE_MODE_CONTRACT
    facts_record = as_mapping(record_mapping["facts"])
    assert "liveness_observed" in facts_record, _PROBE_MODE_CONTRACT
    termination_value = record_mapping.get("termination")
    if termination_value is None:
        return
    termination_record = as_mapping(termination_value)
    assert isinstance(termination_record.get("issuer"), str), _PROBE_MODE_CONTRACT


def _assert_observation(
    entry_point: AgentEntryPoint,
    scenario: MatrixScenario,
    observation: MatrixScenario.Observation,
    controller: RecoveryController,
) -> None:
    assert observation.invocations == tuple(script.agent for script in scenario.attempts)
    _assert_never_spent(observation, controller)
    if scenario.corpus_id is not None and entry_point is not AgentEntryPoint.CANARY_PROBE:
        _assert_corpus_expectations(scenario, observation, controller)
    if entry_point is AgentEntryPoint.CANARY_PROBE:
        _assert_probe_mode_records_facts_and_issuer(observation)
        assert observation.probe_calls == ()
        assert observation.availability is True
        assert observation.wait_ms == 0
        assert observation.wait_positive is False
        assert observation.ledger_status == "eligible"
        assert observation.ledger_entries == {}
        assert observation.false_bench_count == 0
        agent = scenario.attempts[-1].agent
        store = controller.unavailability_store
        assert store.is_available(PHASE, agent) is True
        assert store.earliest_unavailable_wait_ms(PHASE, [agent]) == 0
        assert agent not in store.snapshot().get("unavailability_reasons", {})
        if scenario.expected_kind is not None:
            if (
                scenario.expected_kind == "agent_dead"
                and scenario.attempts[-1].typed_signal is None
            ):
                assert observation.outcome_kind != "agent_dead"
            else:
                assert observation.outcome_kind == scenario.expected_kind
        return
    if scenario.expected_kind is not None:
        assert observation.outcome_kind == scenario.expected_kind
    if scenario.no_ledger_charge:
        assert observation.ledger_status == "eligible"
        assert observation.wait_ms == 0
    last_attempt = scenario.attempts[-1]
    if scenario.probe_raises:
        _assert_one_ordinary_ledger_charge(observation, last_attempt.agent)
    if "AC10" in scenario.acceptance and last_attempt.typed_signal is not None:
        assert observation.availability is False
        assert observation.wait_positive is True
        assert observation.wait_ms > 0
        store = controller.unavailability_store
        assert store.is_available(PHASE, last_attempt.agent) is False
        assert store.earliest_unavailable_wait_ms(PHASE, [last_attempt.agent]) > 0
        expected_reason = scenario.expected_reason or DEAD
        assert (
            store.snapshot().get("unavailability_reasons", {}).get(last_attempt.agent)
            == expected_reason
        )
        assert observation.unavailability_reasons.get(last_attempt.agent) == expected_reason


@pytest.mark.parametrize("entry_point", ENTRY_POINTS, ids=[ep.value for ep in ENTRY_POINTS])
def test_matrix_scenarios_execute_the_registered_route(
    entry_point: AgentEntryPoint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _scenario("workspace_progress")
    real_runners = registered_runners()
    registered = real_runners[entry_point]
    dispatched: list[object] = []

    def spy(*args: object, **kwargs: object) -> object:
        dispatched.append(registered)
        return registered(*args, **kwargs)

    def spying_runners() -> Mapping[AgentEntryPoint, object]:
        return {ep: spy if ep is entry_point else fn for ep, fn in real_runners.items()}

    monkeypatch.setattr(pipeline_matrix_scenarios, "registered_runners", spying_runners)
    run_matrix_scenario(entry_point, scenario)
    assert dispatched == [registered]


@pytest.mark.parametrize("entry_point", ENTRY_POINTS, ids=[ep.value for ep in ENTRY_POINTS])
def test_matrix_scenarios_fail_when_the_registered_route_is_misdirected(
    entry_point: AgentEntryPoint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _scenario("workspace_progress")
    real_runners = registered_runners()

    def raising_route(*_args: object, **_kwargs: object) -> object:
        raise AssertionError(f"registered route misdirected for {entry_point.value}")

    def broken_runners() -> Mapping[AgentEntryPoint, object]:
        return {**real_runners, entry_point: raising_route}

    monkeypatch.setattr(pipeline_matrix_scenarios, "registered_runners", broken_runners)
    with pytest.raises(AssertionError, match="registered route misdirected"):
        run_matrix_scenario(entry_point, scenario)


def test_completeness_every_entry_point_and_corpus_fixture_is_in_the_matrix() -> None:
    assert set(AgentEntryPoint) == set(registered_entry_points()) == set(matrix_entry_points())
    assert set(matrix_entry_points()) == set(ENTRY_POINTS)
    covered = {scenario.corpus_id for scenario in corpus_scenarios() if scenario.corpus_id}
    assert covered == {case.id for case in load_corpus()}


def test_completeness_every_acceptance_criterion_is_tagged() -> None:
    expected = {f"AC{number}" for number in range(1, 19)}
    actual = {acceptance for scenario in ALL_SCENARIOS for acceptance in scenario.acceptance}

    assert actual == expected


def test_completeness_the_ac17_scenario_and_the_incident_7_fixtures_are_registered() -> None:
    ac17 = [scenario.name for scenario in SCENARIOS if "AC17" in scenario.acceptance]
    assert ac17 == ["ac17_pending_subagents_turn_end"]
    incident_7 = sorted(
        {
            scenario.corpus_id
            for scenario in corpus_scenarios()
            if scenario.corpus_id is not None and "incident_7" in scenario.corpus_id
        }
    )
    assert incident_7 == [
        "incident_7_claude_probe_trust_dialog_rc1",
        "incident_7_claude_stop_hook_pending_subagents",
    ]
    assert len(incident_7) == len(set(incident_7))
    corpus_ids = {scenario.corpus_id for scenario in ALL_SCENARIOS if scenario.corpus_id}
    for scenario_id in incident_7:
        assert scenario_id in corpus_ids, f"{scenario_id} is not a registered corpus scenario"


@pytest.mark.parametrize("entry_point", ENTRY_POINTS, ids=[ep.value for ep in ENTRY_POINTS])
@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=_scenario_ids(ALL_SCENARIOS))
def test_matrix_scenario_over_every_entry_point(
    entry_point: AgentEntryPoint,
    scenario: MatrixScenario,
) -> None:
    observation, controller, _clock = run_matrix_scenario(entry_point, scenario)

    _assert_observation(entry_point, scenario, observation, controller)


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=_scenario_ids(ALL_SCENARIOS))
def test_parity_across_non_probe_entry_points(scenario: MatrixScenario) -> None:
    baseline, _controller, _clock = run_matrix_scenario(MAIN_PHASE_LOOP, scenario)
    for entry_point in NON_PROBE_ENTRY_POINTS:
        if entry_point is MAIN_PHASE_LOOP:
            continue
        observation, controller, _clock = run_matrix_scenario(entry_point, scenario)

        assert observation.parity == baseline.parity, entry_point.value
        _assert_never_spent(observation, controller)


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=_scenario_ids(ALL_SCENARIOS))
def test_runner_and_finalize_attempt_parity(scenario: MatrixScenario) -> None:
    runner_observation, runner_controller, _clock = run_matrix_scenario(MAIN_PHASE_LOOP, scenario)
    finalize_observation, finalize_controller, _clock = run_via_finalize_attempt(
        MAIN_PHASE_LOOP, scenario
    )

    assert finalize_observation.parity == runner_observation.parity
    assert finalize_observation.probe_calls == runner_observation.probe_calls
    assert finalize_controller.ledger.snapshot() == runner_controller.ledger.snapshot()
    assert (
        finalize_controller.unavailability_store.snapshot()
        == runner_controller.unavailability_store.snapshot()
    )


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=_scenario_ids(ALL_SCENARIOS))
def test_reducer_style_finalize_claims_the_outcome_without_double_charge(
    scenario: MatrixScenario,
) -> None:
    observation, controller, _clock = run_matrix_scenario(MAIN_PHASE_LOOP, scenario)
    last = scenario.attempts[-1]
    ledger_before = controller.ledger.snapshot()
    tracker_before = controller.unavailability_store.snapshot()
    false_benches_before = controller.false_bench_feedback.count
    verdict_before = controller.last_verdict_record

    outcome = controller.finalize_attempt(
        PHASE,
        last.agent,
        facts=last.facts,
        attempt_failed=last.failed,
        typed_signal=last.typed_signal,
        exc=None,
        retryable_reason=last.retryable_reason,
        attempt_key=None,
    )

    assert outcome.kind.value == observation.outcome_kind
    assert controller.ledger.snapshot() == ledger_before
    assert controller.unavailability_store.snapshot() == tracker_before
    assert controller.false_bench_feedback.count == false_benches_before
    assert controller.last_verdict_record == verdict_before


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=_scenario_ids(ALL_SCENARIOS))
def test_canary_probe_owner_managed_exemption_records_without_charge_bench_or_probe(
    scenario: MatrixScenario,
) -> None:
    observation, controller, _clock = run_matrix_scenario(AgentEntryPoint.CANARY_PROBE, scenario)

    assert observation.probe_calls == ()
    assert observation.availability is True
    assert observation.ledger_entries == {}
    assert observation.false_bench_count == 0
    _assert_never_spent(observation, controller)


@pytest.mark.parametrize(
    "entry_point",
    NON_PROBE_ENTRY_POINTS,
    ids=[ep.value for ep in NON_PROBE_ENTRY_POINTS],
)
def test_ac9_corpus_replay_over_the_matrix_reports_zero_false_benches(
    entry_point: AgentEntryPoint,
    capsys: pytest.CaptureFixture[str],
) -> None:
    replays = [run_matrix_scenario(entry_point, scenario) for scenario in NEGATIVE_CORPUS_SCENARIOS]
    false_benches = [replay for replay, _controller, _clock in replays if not replay.availability]
    report = f"false_benches={len(false_benches)}/{len(replays)}"
    matrix_sample = len(replays) * len(NON_PROBE_ENTRY_POINTS)

    with capsys.disabled():
        print(
            f"\ndead-agent pipeline matrix corpus replay: {report} "
            f"entry_point={entry_point.value} matrix_sample={matrix_sample}"
        )

    assert report == f"false_benches=0/{len(replays)}"


def test_ac9_the_genuine_positive_benches_on_the_first_attempt() -> None:
    agy_failed = next(
        scenario
        for scenario in corpus_scenarios()
        if scenario.corpus_id == "agy_quota_error_step"
        and scenario.probe_outcome is ProbeOutcome.FAILED_REPRODUCING_NO_WORK
    )
    observation, _controller, _clock = run_matrix_scenario(MAIN_PHASE_LOOP, agy_failed)
    assert observation.outcome_kind == "agent_dead"
    assert observation.probe_calls == ("agy",)


@pytest.mark.parametrize(
    "entry_point",
    NON_PROBE_ENTRY_POINTS,
    ids=[ep.value for ep in NON_PROBE_ENTRY_POINTS],
)
@pytest.mark.parametrize(
    "scenario_name",
    (
        "ac1_chain_selects_b_after_bench",
        "ac2_post_event_kill_failed_canary",
        "ac2_ordinary_nonzero_exit_selects_b",
    ),
)
def test_ac1_and_ac2_production_selection_dispatches_b_after_a_is_benched(
    entry_point: AgentEntryPoint,
    scenario_name: str,
) -> None:
    observation, controller, _clock, selected = run_matrix_scenario_with_selection(
        entry_point,
        _scenario(scenario_name),
    )

    assert selected == AGENT_B
    assert observation.invocations == (AGENT_A, AGENT_B)
    assert observation.invocations.count(AGENT_A) == 1
    assert observation.outcome_kinds == ("agent_dead", "completed")
    if scenario_name == "ac1_chain_selects_b_after_bench":
        record = observation.verdict_record
        assert isinstance(record, dict)
        facts = as_mapping(record).get("facts")
        assert isinstance(facts, dict)
        elapsed = facts.get("elapsed_to_terminal_ms")
        assert isinstance(elapsed, int)
        assert elapsed < 1000, (
            "the failing attempt must have reached its terminal state within one "
            f"second; observed elapsed_to_terminal_ms={elapsed!r}"
        )
    assert controller.preferred_agent_index(PHASE, AGENTS).agent != AGENT_A, (
        "a benched agent must not be re-selected at a later selection opportunity"
    )
    store = controller.unavailability_store
    assert store.is_available(PHASE, AGENT_A) is False
    assert store.earliest_unavailable_wait_ms(PHASE, [AGENT_A]) > 0
    reasons = store.snapshot().get("unavailability_reasons", {})
    assert reasons.get(AGENT_A) == DEAD
    assert controller.ledger.status(PHASE, AGENT_A).is_spent is False
    assert controller.snapshot()["spent_agents"] == {}


@pytest.mark.parametrize(
    "entry_point",
    NON_PROBE_ENTRY_POINTS,
    ids=[ep.value for ep in NON_PROBE_ENTRY_POINTS],
)
def test_ac6_slow_productive_agent_with_mid_run_error_is_not_benched(
    entry_point: AgentEntryPoint,
) -> None:
    observation, controller, _clock = run_matrix_scenario(
        entry_point, _scenario("slow_productive_mid_run_error")
    )
    assert observation.outcome_kind == "unknown"
    assert observation.availability is True
    assert observation.wait_ms == 0
    assert controller.unavailability_store.is_available(PHASE, AGENT_A) is True
    assert controller.ledger.status(PHASE, AGENT_A).is_spent is False


@pytest.mark.parametrize(
    "entry_point",
    NON_PROBE_ENTRY_POINTS,
    ids=[ep.value for ep in NON_PROBE_ENTRY_POINTS],
)
def test_ac16_probe_could_not_run_keeps_a_selectable(
    entry_point: AgentEntryPoint,
) -> None:
    observation, controller, _clock = run_matrix_scenario(
        entry_point,
        _scenario("probe_could_not_run_rc1"),
    )

    assert observation.outcome_kind == "unknown"
    assert observation.availability is True
    assert observation.wait_ms == 0
    assert observation.ledger_status == "eligible"
    assert controller.snapshot()["spent_agents"] == {}
    assert controller.preferred_agent_index(PHASE, AGENTS).agent == AGENT_A
    _assert_one_ordinary_ledger_charge(observation, AGENT_A)


@pytest.mark.parametrize(
    "entry_point",
    NON_PROBE_ENTRY_POINTS,
    ids=[ep.value for ep in NON_PROBE_ENTRY_POINTS],
)
def test_ac3_runtime_attribution_label_and_selectability(
    entry_point: AgentEntryPoint,
) -> None:
    observation, controller, _clock = run_matrix_scenario(
        entry_point, _scenario("runtime_live_end")
    )

    assert observation.outcome_kind == "runtime_ended"
    assert observation.availability is True
    assert observation.wait_ms == 0
    assert observation.ledger_status == "eligible"
    assert controller.snapshot()["spent_agents"] == {}
    assert controller.preferred_agent_index(PHASE, AGENTS).agent == AGENT_A
    record = observation.verdict_record
    assert isinstance(record, dict)
    attrib = as_mapping(record.get("attribution", {}))
    assert attrib.get("attribution") == "runtime"
    assert attrib.get("runtime_cause") == "runtime_kill_quota_misread"
    assert attrib.get("counts_toward_agent_state") is False


@pytest.mark.parametrize(
    "entry_point",
    NON_PROBE_ENTRY_POINTS,
    ids=[ep.value for ep in NON_PROBE_ENTRY_POINTS],
)
def test_ac4_prompt_side_quota_words_never_bench(
    entry_point: AgentEntryPoint,
) -> None:
    observation, controller, _clock = run_matrix_scenario(
        entry_point, _scenario("quota_words_in_prompt_are_data")
    )

    assert observation.outcome_kind == "unknown"
    assert observation.availability is True
    assert observation.wait_ms == 0
    assert observation.ledger_status == "eligible"
    assert controller.snapshot()["spent_agents"] == {}
    assert controller.preferred_agent_index(PHASE, AGENTS).agent == AGENT_A
    assert AGENT_A not in controller.unavailability_store.snapshot().get(
        "unavailability_reasons", {}
    )


@pytest.mark.parametrize(
    "entry_point",
    NON_PROBE_ENTRY_POINTS,
    ids=[ep.value for ep in NON_PROBE_ENTRY_POINTS],
)
def test_ac5_failed_probe_benches_with_availability_false_and_mapped_reason(
    entry_point: AgentEntryPoint,
) -> None:
    observation, controller, _clock = run_matrix_scenario(
        entry_point, _scenario("failed_probe_benches")
    )

    assert observation.outcome_kind == "agent_dead"
    assert observation.availability is False
    assert observation.wait_positive is True
    assert observation.wait_ms > 0
    store = controller.unavailability_store
    assert store.is_available(PHASE, AGENT_A) is False
    assert store.earliest_unavailable_wait_ms(PHASE, [AGENT_A]) > 0
    reasons = store.snapshot().get("unavailability_reasons", {})
    assert reasons.get(AGENT_A) == DEAD
    assert observation.unavailability_reasons.get(AGENT_A) == DEAD
    assert controller.ledger.status(PHASE, AGENT_A).is_spent is False
    assert controller.snapshot()["spent_agents"] == {}


@pytest.mark.parametrize(
    "entry_point",
    NON_PROBE_ENTRY_POINTS,
    ids=[ep.value for ep in NON_PROBE_ENTRY_POINTS],
)
def test_ac7_probe_budget_exhausted_keeps_unknown_and_selectable(
    entry_point: AgentEntryPoint,
) -> None:
    observation, controller, _clock = run_matrix_scenario(
        entry_point, _scenario("probe_budget_exhausted")
    )

    assert observation.outcome_kind == "unknown"
    assert observation.availability is True
    assert observation.wait_ms == 0
    assert observation.ledger_status == "eligible"
    assert observation.probe_calls == (AGENT_A,)
    assert controller.snapshot()["spent_agents"] == {}
    assert controller.preferred_agent_index(PHASE, AGENTS).agent == AGENT_A
    _assert_one_ordinary_ledger_charge(observation, AGENT_A)
    record = observation.verdict_record
    if isinstance(record, dict):
        assert as_mapping(record).get("probe_outcome") is None


@pytest.mark.parametrize(
    "entry_point",
    NON_PROBE_ENTRY_POINTS,
    ids=[ep.value for ep in NON_PROBE_ENTRY_POINTS],
)
def test_ac16_positive_proof_only_exit_zero_alone_never_benches(
    entry_point: AgentEntryPoint,
) -> None:
    observation, controller, _clock = run_matrix_scenario(
        entry_point, _scenario("ac16_exit_zero_alone_not_positive_proof")
    )

    assert observation.outcome_kind == "unknown"
    assert observation.availability is True
    assert observation.wait_ms == 0
    assert observation.ledger_status == "eligible"
    assert controller.snapshot()["spent_agents"] == {}
    assert controller.preferred_agent_index(PHASE, AGENTS).agent == AGENT_A
    record = observation.verdict_record
    if isinstance(record, dict):
        assert as_mapping(record).get("probe_outcome") == "inconclusive"


def test_ac16_registered_entry_points_honor_noninteractive_pretrusted_probe_contract() -> None:
    from ralph.agents.invoke import BuildCommandOptions, build_command
    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig
    from ralph.recovery.canary_probe_launch import (
        PROBE_NONINTERACTIVE_FLAGS,
        probe_launch_override,
    )

    assert AgentEntryPoint.CANARY_PROBE in ENTRY_POINTS
    assert AgentEntryPoint.CANARY_PROBE in registered_runners()
    for entry_point in NON_PROBE_ENTRY_POINTS:
        assert entry_point in registered_runners()

    config = AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE)
    flag = PROBE_NONINTERACTIVE_FLAGS[AgentTransport.CLAUDE_INTERACTIVE]
    with probe_launch_override():
        argv = build_command(config, "prompt.md", options=BuildCommandOptions())
    assert flag in argv

    for entry_point in NON_PROBE_ENTRY_POINTS:
        observation, controller, _clock = run_matrix_scenario(
            entry_point, _scenario("probe_could_not_run_rc1")
        )
        assert observation.outcome_kind == "unknown"
        assert observation.availability is True
        assert observation.ledger_status == "eligible"
        assert controller.preferred_agent_index(PHASE, AGENTS).agent == AGENT_A
        assert controller.snapshot()["spent_agents"] == {}


@pytest.mark.parametrize(
    "entry_point",
    NON_PROBE_ENTRY_POINTS,
    ids=[ep.value for ep in NON_PROBE_ENTRY_POINTS],
)
def test_ac17_pending_subagents_recorded_as_unattributed_end(
    entry_point: AgentEntryPoint,
) -> None:
    observation, controller, _clock = run_matrix_scenario(
        entry_point, _scenario("ac17_pending_subagents_turn_end")
    )

    assert observation.outcome_kind == "runtime_ended"
    assert observation.availability is True
    assert observation.wait_ms == 0
    assert observation.ledger_status == "eligible"
    assert controller.snapshot()["spent_agents"] == {}
    assert controller.preferred_agent_index(PHASE, AGENTS).agent == AGENT_A
    record = observation.verdict_record
    assert isinstance(record, dict)
    attrib = as_mapping(record.get("attribution", {}))
    assert attrib.get("attribution") == "runtime"
    assert attrib.get("runtime_cause") == "runtime_kill_unattributed"
    assert attrib.get("counts_toward_agent_state") is False
    term_record = as_mapping(record.get("termination", {}))
    assert term_record.get("issuer") == "unknown"


def _case_for(scenario: MatrixScenario) -> CorpusCase:
    return next(item for item in corpus_cases() if item.id == scenario.corpus_id)


@pytest.mark.parametrize(
    "entry_point",
    NON_PROBE_ENTRY_POINTS,
    ids=[ep.value for ep in NON_PROBE_ENTRY_POINTS],
)
def test_ac10_the_second_confirmed_failure_escalates_the_bench(
    entry_point: AgentEntryPoint,
) -> None:
    _observation, controller, _clock = run_matrix_scenario(
        entry_point, _scenario("bench_escalation")
    )

    wait = controller.unavailability_store.earliest_unavailable_wait_ms(PHASE, [AGENT_A])
    assert wait == _BENCH_POLICY.base_backoff_ms * 2


@pytest.mark.parametrize(
    "entry_point",
    NON_PROBE_ENTRY_POINTS,
    ids=[ep.value for ep in NON_PROBE_ENTRY_POINTS],
)
def test_ac11_the_wait_listing_names_agent_reason_and_remaining_time(
    entry_point: AgentEntryPoint,
) -> None:
    observation, controller, _clock = run_matrix_scenario(
        entry_point, _scenario("bench_while_peer_cooling")
    )

    assert observation.outcome_kind == "agent_dead"
    assert observation.invocations == (AGENT_A,)
    store = controller.unavailability_store
    assert store.is_available(PHASE, AGENT_A) is False
    snapshot = store.snapshot()
    reasons = snapshot.get("unavailability_reasons", {})
    timeouts = snapshot.get("unavailable_timeouts", {})
    assert reasons.get(AGENT_A) == DEAD
    assert reasons.get(AGENT_B) == str(UnavailabilityReason.OUT_OF_CREDITS)
    remaining_a = timeouts.get(AGENT_A)
    remaining_b = timeouts.get(AGENT_B)
    assert isinstance(remaining_a, int) and remaining_a > 0
    assert isinstance(remaining_b, int) and remaining_b > 0
    assert store.earliest_unavailable_wait_ms(PHASE, list(AGENTS)) > 0
    payload = controller.waiting_state_payload(PHASE, list(AGENTS))
    assert {entry[0] for entry in payload} == set(AGENTS)
    assert all(entry[2] > 0 for entry in payload)

    selection = controller.preferred_agent_index(PHASE, list(AGENTS))
    assert selection.agent is None
    assert selection.index is None
    assert len(selection.skipped_reasons) == 2
    skipped_dict = dict(selection.skipped_reasons)
    assert "dead_agent_no_progress" in skipped_dict[AGENT_A]
    assert "out_of_credits" in skipped_dict[AGENT_B]

    eligibility = dict(controller.chain_eligibility(PHASE, list(AGENTS)))
    assert eligibility[AGENT_A].reason == "dead_agent_no_progress"
    assert eligibility[AGENT_B].reason == "out_of_credits"
    assert controller.cycle_cap == 200, (
        "the recovery cap is unchanged: the controller waits with the agent, reason "
        f"and remaining-time listing until cycle {controller.cycle_cap}, and the "
        "existing cap is what terminates the run"
    )


@pytest.mark.parametrize(
    "entry_point",
    NON_PROBE_ENTRY_POINTS,
    ids=[ep.value for ep in NON_PROBE_ENTRY_POINTS],
)
def test_ac12_an_expired_bench_leaves_no_tracker_trace_but_feeds_back(
    entry_point: AgentEntryPoint,
) -> None:
    observation, controller, clock = run_matrix_scenario(
        entry_point, _scenario("false_bench_feedback")
    )

    assert observation.outcome_kind == "completed"
    assert observation.false_bench_count == 1
    assert controller.false_bench_feedback.count == 1
    events = controller.false_bench_feedback.events
    assert len(events) == 1
    assert events[0]["verdict_ref"]
    assert clock.monotonic() >= _BENCH_POLICY.base_backoff_ms / 1000
    snapshot = controller.unavailability_store.snapshot()
    assert snapshot.get("unavailability_reasons", {}).get(AGENT_A) is None
    assert snapshot.get("unavailable_timeouts", {}).get(AGENT_A) is None
    assert controller.unavailability_store.is_available(PHASE, AGENT_A) is True


@pytest.mark.parametrize(
    "entry_point",
    NON_PROBE_ENTRY_POINTS,
    ids=[ep.value for ep in NON_PROBE_ENTRY_POINTS],
)
def test_ac12_success_resets_active_cooldown_and_next_failure_returns_to_base_backoff(
    entry_point: AgentEntryPoint,
) -> None:
    scenario = MatrixScenario(
        "success_resets_active_cooldown",
        ("AC12",),
        (
            MatrixScenario.Script(agent=AGENT_A, typed_signal=DEAD_SIGNAL),
            MatrixScenario.Script(
                agent=AGENT_A,
                failed=False,
                facts=AttemptFacts(artifact_valid=True),
                advance_before_seconds=1.0,
            ),
            MatrixScenario.Script(agent=AGENT_A, typed_signal=DEAD_SIGNAL),
        ),
        ProbeOutcome.PASSED,
    )
    _observation, controller, clock = run_matrix_scenario(entry_point, scenario)
    assert clock.monotonic() == 1.0
    wait = controller.unavailability_store.earliest_unavailable_wait_ms(PHASE, [AGENT_A])
    assert wait == _BENCH_POLICY.base_backoff_ms


@pytest.mark.parametrize(
    "entry_point",
    NON_PROBE_ENTRY_POINTS,
    ids=[ep.value for ep in NON_PROBE_ENTRY_POINTS],
)
def test_ac12_unavailability_store_snapshot_restore_round_trip(
    entry_point: AgentEntryPoint,
) -> None:
    _observation, controller, clock = run_matrix_scenario(
        entry_point, _scenario("typed_signal_bench_online")
    )
    store = controller.unavailability_store
    assert store.is_available(PHASE, AGENT_A) is False
    wait_before = store.earliest_unavailable_wait_ms(PHASE, [AGENT_A])
    assert wait_before > 0

    snap = controller.snapshot()
    restored_controller = RecoveryController.from_snapshot(snap, clock=clock)
    restored_store = restored_controller.unavailability_store
    assert restored_store.is_available(PHASE, AGENT_A) is False
    assert restored_store.earliest_unavailable_wait_ms(PHASE, [AGENT_A]) == wait_before
    assert (
        restored_store.snapshot()["unavailability_reasons"]
        == store.snapshot()["unavailability_reasons"]
    )

    clock.advance((wait_before / 1000.0) + 1.0)
    assert restored_store.is_available(PHASE, AGENT_A) is True
    assert restored_store.earliest_unavailable_wait_ms(PHASE, [AGENT_A]) == 0
    assert restored_store.prune_expired() == 1
    assert restored_store.prune_expired() == 0


@pytest.mark.parametrize(
    "entry_point",
    NON_PROBE_ENTRY_POINTS,
    ids=[ep.value for ep in NON_PROBE_ENTRY_POINTS],
)
def test_ac13_the_verdict_record_carries_the_full_evidence_set(
    entry_point: AgentEntryPoint,
) -> None:
    observation, _controller, _clock = run_matrix_scenario(
        entry_point, _scenario("failed_probe_benches")
    )

    record = observation.verdict_record
    assert isinstance(record, dict)
    assert record["schema_version"] == 2
    for key in ("facts", "attribution", "probe_outcome", "termination"):
        assert key in record
    termination = record["termination"]
    assert termination["issuer"] == "agent_exit"
    assert termination["observed_returncode"] == 1
    assert termination["reported_returncode"] == 0
    assert termination["return_code_discrepancy"] is True
    assert termination["log"]
    assert record["return_code_discrepancy"] is True


@pytest.mark.parametrize(
    "entry_point",
    ENTRY_POINTS,
    ids=[ep.value for ep in ENTRY_POINTS],
)
def test_ac14_unattributed_live_session_end_parity(
    entry_point: AgentEntryPoint,
) -> None:
    scenario = _scenario("unattributed_live_end")
    observation, controller, _clock = run_matrix_scenario(entry_point, scenario)

    assert observation.outcome_kind in ("runtime_ended", "unknown")
    assert observation.availability is True
    assert observation.wait_ms == 0
    assert observation.ledger_status == "eligible"
    assert controller.snapshot()["spent_agents"] == {}

    script = scenario.attempts[-1]
    assert script.facts.liveness_observed is True
    assert "read_file" in script.facts.tool_calls
    assert script.facts.termination is not None
    assert script.facts.termination.issuer is TerminationIssuer.UNKNOWN
    assert script.facts.termination.observed_returncode == -15
    assert script.facts.termination.reported_returncode == 0
    assert script.facts.termination.return_code_discrepancy is True

    record = observation.verdict_record
    if isinstance(record, dict) and entry_point is not AgentEntryPoint.CANARY_PROBE:
        typed_record = as_mapping(record)
        facts_record = as_mapping(typed_record.get("facts", {}))
        assert facts_record.get("liveness_observed") is True
        tool_calls = as_sequence(facts_record.get("tool_calls", []))
        assert "read_file" in tool_calls
        term_record = as_mapping(typed_record.get("termination", {}))
        assert term_record.get("issuer") == "unknown"
        assert term_record.get("observed_returncode") == -15
        assert term_record.get("reported_returncode") == 0
        assert term_record.get("return_code_discrepancy") is True
        attrib = as_mapping(typed_record.get("attribution", {}))
        assert attrib.get("attribution") == "runtime"
        assert attrib.get("runtime_cause") == "runtime_kill_unattributed"


@pytest.mark.parametrize(
    "entry_point",
    ENTRY_POINTS,
    ids=[ep.value for ep in ENTRY_POINTS],
)
def test_ac15_post_exit_watchdog_attribution_parity(
    entry_point: AgentEntryPoint,
) -> None:
    scenario = _scenario("post_exit_watchdog")
    observation, controller, _clock = run_matrix_scenario(entry_point, scenario)

    assert observation.outcome_kind == "runtime_ended"
    assert observation.availability is True
    assert observation.wait_ms == 0
    assert observation.ledger_status == "eligible"
    assert controller.snapshot()["spent_agents"] == {}

    script = scenario.attempts[-1]
    assert script.facts.liveness_observed is True
    assert "read_file" in script.facts.tool_calls
    assert script.facts.termination is not None
    assert script.facts.termination.issuer is TerminationIssuer.POST_EXIT_WATCHDOG
    assert script.facts.termination.observed_returncode == 143
    assert script.facts.termination.reported_returncode == 0
    assert script.facts.termination.return_code_discrepancy is True

    record = observation.verdict_record
    if isinstance(record, dict) and entry_point is not AgentEntryPoint.CANARY_PROBE:
        typed_record = as_mapping(record)
        facts_record = as_mapping(typed_record.get("facts", {}))
        assert facts_record.get("liveness_observed") is True
        tool_calls = as_sequence(facts_record.get("tool_calls", []))
        assert "read_file" in tool_calls
        term_record = as_mapping(typed_record.get("termination", {}))
        assert term_record.get("issuer") == "post_exit_watchdog"
        assert term_record.get("observed_returncode") == 143
        assert term_record.get("reported_returncode") == 0
        assert term_record.get("return_code_discrepancy") is True
        attrib = as_mapping(typed_record.get("attribution", {}))
        assert attrib.get("attribution") == "runtime"
        assert attrib.get("runtime_cause") == "runtime_kill_post_exit_watchdog"


@pytest.mark.parametrize(
    "entry_point",
    NON_PROBE_ENTRY_POINTS,
    ids=[ep.value for ep in NON_PROBE_ENTRY_POINTS],
)
@pytest.mark.parametrize(
    "scenario_name",
    (
        "typed_signal_missing_credentials_online",
        "typed_signal_missing_credentials_offline",
        "typed_signal_broken_agent_online",
        "typed_signal_broken_agent_offline",
        "typed_signal_bench_online",
        "typed_signal_bench_offline",
    ),
)
def test_ac10_typed_signals_unavailability_positive_wait_and_mapped_reason(
    entry_point: AgentEntryPoint,
    scenario_name: str,
) -> None:
    scenario = _scenario(scenario_name)
    observation, controller, _clock = run_matrix_scenario(entry_point, scenario)
    agent = scenario.attempts[-1].agent

    assert observation.outcome_kind == "agent_dead"
    assert observation.availability is False
    assert observation.wait_positive is True
    assert observation.wait_ms > 0
    store = controller.unavailability_store
    assert store.is_available(PHASE, agent) is False
    assert store.earliest_unavailable_wait_ms(PHASE, [agent]) > 0
    reasons = store.snapshot().get("unavailability_reasons", {})
    assert reasons.get(agent) == DEAD
    assert observation.unavailability_reasons.get(agent) == DEAD


@pytest.mark.parametrize(
    "scenario_name",
    (
        "typed_signal_missing_credentials_online",
        "typed_signal_missing_credentials_offline",
        "typed_signal_broken_agent_online",
        "typed_signal_broken_agent_offline",
        "typed_signal_bench_online",
        "typed_signal_bench_offline",
        "failed_probe_benches",
    ),
)
def test_canary_probe_mode_contract_explicit_exception(
    scenario_name: str,
) -> None:
    scenario = _scenario(scenario_name)
    observation, controller, _clock = run_matrix_scenario(AgentEntryPoint.CANARY_PROBE, scenario)
    _assert_probe_mode_records_facts_and_issuer(observation)
    agent = scenario.attempts[-1].agent

    assert observation.probe_calls == ()
    assert observation.availability is True
    assert observation.wait_ms == 0
    assert observation.wait_positive is False
    assert observation.ledger_status == "eligible"
    assert observation.ledger_entries == {}
    assert observation.false_bench_count == 0
    store = controller.unavailability_store
    assert store.is_available(PHASE, agent) is True
    assert store.earliest_unavailable_wait_ms(PHASE, [agent]) == 0
    assert agent not in store.snapshot().get("unavailability_reasons", {})
