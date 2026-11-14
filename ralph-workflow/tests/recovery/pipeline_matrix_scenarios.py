from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.pipeline.agent_entry_point import AgentEntryPoint, registered_runners
from ralph.pipeline.attempt_finalizer import AttemptFinalization
from ralph.pipeline.attempt_runner import (
    AttemptInvoke,
    build_entry_point_recovery_controller,
)
from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.recovery.agent_config_fingerprint import agent_config_fingerprint
from ralph.recovery.agent_unavailability_tracker import AgentUnavailabilityTracker
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.canary_probe import ProbeOutcome
from ralph.recovery.connectivity_state import ConnectivityState
from ralph.recovery.dead_agent_verdict import TypedSignal
from ralph.recovery.unavailability_reason import UnavailabilityReason
from tests.recovery._corpus_derive import CorpusCase, load_corpus, load_fixtures

if TYPE_CHECKING:
    from ralph.pipeline.attempt_result import AttemptResult
    from ralph.recovery.attempt_outcome import AttemptOutcome
    from ralph.recovery.controller import RecoveryController


@runtime_checkable
class _AttemptFinalizer(Protocol):
    def __call__(self, finalization: AttemptFinalization) -> AttemptOutcome: ...


PHASE = "development"
RUN_ID = "matrix-run"
AGENT_A = "agent_a"
AGENT_B = "agent_b"
AGENTS = (AGENT_A, AGENT_B)

HEALTHY_PROBE_OUTCOMES = (ProbeOutcome.PASSED, ProbeOutcome.INCONCLUSIVE, None)
EVERY_PROBE_OUTCOME = (*HEALTHY_PROBE_OUTCOMES, ProbeOutcome.FAILED_REPRODUCING_NO_WORK)


def termination_facts(
    issuer: TerminationIssuer = TerminationIssuer.AGENT_EXIT,
    *,
    observed: int | None = 1,
    reported: int | None = 1,
    liveness: bool | None = False,
    artifact_valid: bool = False,
    workspace_changed: bool | None = False,
    event_kinds: tuple[str, ...] = (),
    tool_calls: tuple[str, ...] = (),
) -> AttemptFacts:
    return AttemptFacts(
        event_kinds=event_kinds,
        tool_calls=tool_calls,
        artifact_valid=artifact_valid,
        workspace_changed=workspace_changed,
        exit_status=f"exit_{reported}" if reported is not None else None,
        liveness_observed=liveness,
        termination=TerminationRecord.create(
            issuer,
            observed_returncode=observed,
            reported_returncode=reported,
            liveness_observed=liveness,
        ),
    )


DEAD_SIGNAL = TypedSignal(
    name="QuotaExhaustedError",
    provider_originated=True,
    agent_initiated=True,
)
MISSING_CREDENTIALS_SIGNAL = TypedSignal(
    name="MissingCredentialsError",
    provider_originated=True,
    agent_initiated=True,
)
BROKEN_AGENT_SIGNAL = TypedSignal(
    name="BrokenAgentExitError",
    provider_originated=True,
    agent_initiated=True,
)

_BENCH_BACKOFF = 15.0


@dataclass(frozen=True)
class MatrixScenario:
    @dataclass(frozen=True)
    class Script:
        agent: str = AGENT_A
        facts: AttemptFacts = field(default_factory=AttemptFacts)
        failed: bool = True
        workspace_changed: bool | None = None
        typed_signal: TypedSignal | None = None
        retryable_reason: str | None = None
        returncode: int = 1
        message: str = "unseen provider wording"
        parsed_output: tuple[str, ...] = ()
        advance_before_seconds: float = 0.0

    @dataclass(frozen=True)
    class Observation:
        outcome_kind: str
        availability: bool
        wait_ms: int
        ledger_status: str
        ledger_entries: dict[str, object]
        verdict_record: object
        false_bench_count: int
        invocations: tuple[str, ...]
        probe_calls: tuple[str, ...]
        outcome_kinds: tuple[str, ...] = ()
        waiting_payload: tuple[tuple[str, int, int], ...] = ()
        unavailability_reasons: dict[str, str] = field(default_factory=dict)

        @property
        def wait_positive(self) -> bool:
            return self.wait_ms > 0

        @property
        def parity(self) -> tuple[object, ...]:
            return (
                self.outcome_kind,
                self.availability,
                self.wait_positive,
                self.ledger_status,
                self.verdict_record,
                self.false_bench_count,
                self.waiting_payload,
                tuple(sorted(self.unavailability_reasons.items())),
            )

    name: str
    acceptance: tuple[str, ...]
    attempts: tuple[MatrixScenario.Script, ...]
    probe_outcome: ProbeOutcome | None = ProbeOutcome.PASSED
    probe_raises: bool = False
    connectivity: ConnectivityState | None = None
    precool: tuple[tuple[str, UnavailabilityReason], ...] = ()
    expected_kind: str | None = None
    no_ledger_charge: bool = False
    corpus_id: str | None = None


SCENARIOS = (
    MatrixScenario(
        "failed_probe_benches",
        ("AC1", "AC2", "AC5", "AC10", "AC13", "AC16", "AC18"),
        (MatrixScenario.Script(facts=termination_facts(reported=0), returncode=0),),
        ProbeOutcome.FAILED_REPRODUCING_NO_WORK,
        expected_kind="agent_dead",
    ),
    MatrixScenario(
        "ac1_chain_selects_b_after_bench",
        ("AC1",),
        (
            MatrixScenario.Script(
                facts=termination_facts(
                    observed=0,
                    reported=0,
                    event_kinds=("error",),
                ),
                returncode=0,
                message="provider rejected this request for an unprecedented reason",
            ),
            MatrixScenario.Script(
                agent=AGENT_B,
                failed=False,
                facts=AttemptFacts(artifact_valid=True),
            ),
        ),
        ProbeOutcome.FAILED_REPRODUCING_NO_WORK,
        expected_kind="completed",
    ),
    MatrixScenario(
        "ac2_post_event_kill_failed_canary",
        ("AC2",),
        (
            MatrixScenario.Script(
                facts=termination_facts(
                    TerminationIssuer.UNKNOWN,
                    observed=137,
                    reported=137,
                    liveness=False,
                    event_kinds=("error",),
                ),
                returncode=137,
                message="provider stopped for a never-before-seen reason",
            ),
            MatrixScenario.Script(
                agent=AGENT_B,
                failed=False,
                facts=AttemptFacts(artifact_valid=True),
            ),
        ),
        ProbeOutcome.FAILED_REPRODUCING_NO_WORK,
        expected_kind="completed",
    ),
    MatrixScenario(
        "probe_could_not_run_rc1",
        ("AC16",),
        (
            MatrixScenario.Script(
                facts=termination_facts(
                    TerminationIssuer.UNKNOWN,
                    observed=1,
                    reported=1,
                    liveness=False,
                ),
                returncode=1,
            ),
        ),
        ProbeOutcome.INCONCLUSIVE,
        expected_kind="unknown",
        no_ledger_charge=True,
    ),
    MatrixScenario(
        "runtime_live_end",
        ("AC3", "AC7"),
        (
            MatrixScenario.Script(
                facts=termination_facts(
                    TerminationIssuer.QUOTA_DETECTOR,
                    observed=-15,
                    reported=0,
                    liveness=True,
                    event_kinds=("tool_result",),
                )
            ),
        ),
        expected_kind="runtime_ended",
        no_ledger_charge=True,
    ),
    MatrixScenario(
        "quota_words_are_data",
        ("AC1", "AC4"),
        (
            MatrixScenario.Script(
                facts=termination_facts(),
                message="connection timed out",
                parsed_output=("tool result: credits exhausted",),
            ),
        ),
        expected_kind="unknown",
    ),
    MatrixScenario(
        "workspace_progress",
        ("AC6",),
        (
            MatrixScenario.Script(
                facts=termination_facts(workspace_changed=True), workspace_changed=True
            ),
        ),
        expected_kind="unknown",
    ),
    MatrixScenario(
        "slow_productive_mid_run_error",
        ("AC6",),
        (
            MatrixScenario.Script(
                facts=AttemptFacts(
                    artifact_valid=False,
                    workspace_changed=True,
                    workspace_changed_by_tool=True,
                    elapsed_to_terminal_ms=90_000,
                    liveness_observed=True,
                    event_kinds=("error",),
                    tool_calls=("edit_file",),
                    termination=TerminationRecord.create(
                        TerminationIssuer.AGENT_EXIT,
                        observed_returncode=1,
                        reported_returncode=1,
                        liveness_observed=True,
                    ),
                ),
                workspace_changed=True,
                failed=True,
                returncode=1,
                message="transient mid-run error event",
            ),
        ),
        ProbeOutcome.PASSED,
        expected_kind="unknown",
    ),
    MatrixScenario(
        "probe_unavailable",
        ("AC7",),
        (MatrixScenario.Script(facts=termination_facts()),),
        None,
        True,
        expected_kind="unknown",
    ),
    MatrixScenario(
        "existing_outcome",
        ("AC8",),
        (MatrixScenario.Script(facts=termination_facts(artifact_valid=True)),),
        expected_kind="task_or_environment_failure",
        no_ledger_charge=True,
    ),
    MatrixScenario(
        "identical_peers",
        ("AC8",),
        (
            MatrixScenario.Script(agent=AGENT_A, facts=termination_facts()),
            MatrixScenario.Script(agent=AGENT_B, facts=termination_facts()),
        ),
        expected_kind="task_or_environment_failure",
    ),
    MatrixScenario(
        "unattributed_live_end",
        ("AC2", "AC13", "AC14"),
        (
            MatrixScenario.Script(
                facts=AttemptFacts(
                    artifact_valid=False,
                    workspace_changed=None,
                    elapsed_to_terminal_ms=97_900,
                    exit_status="exit_0",
                    liveness_observed=True,
                ),
                returncode=0,
            ),
        ),
        expected_kind="unknown",
    ),
    MatrixScenario(
        "post_exit_watchdog",
        ("AC15",),
        (
            MatrixScenario.Script(
                facts=termination_facts(
                    TerminationIssuer.POST_EXIT_WATCHDOG,
                    observed=143,
                    reported=0,
                    liveness=True,
                    event_kinds=("tool_result",),
                ),
                returncode=0,
            ),
        ),
        expected_kind="runtime_ended",
        no_ledger_charge=True,
    ),
    MatrixScenario(
        "ac17_pending_subagents_turn_end",
        ("AC17",),
        (
            MatrixScenario.Script(
                facts=AttemptFacts(
                    event_kinds=("tool_use", "tool_result"),
                    tool_calls=("Agent",),
                    artifact_valid=False,
                    workspace_changed=None,
                    exit_status="exit_0",
                    liveness_observed=True,
                    subagent_activity_observed=True,
                    termination=TerminationRecord.create(
                        TerminationIssuer.UNKNOWN,
                        observed_returncode=0,
                        reported_returncode=0,
                        liveness_observed=True,
                    ),
                ),
                returncode=0,
            ),
        ),
        ProbeOutcome.PASSED,
        expected_kind="runtime_ended",
        no_ledger_charge=True,
    ),
    MatrixScenario(
        "silent_exit_passing_probe_never_benched",
        ("AC5",),
        (MatrixScenario.Script(facts=termination_facts(reported=0), returncode=0),),
        ProbeOutcome.PASSED,
        expected_kind="unknown",
        no_ledger_charge=True,
    ),
    MatrixScenario(
        "typed_signal_missing_credentials_online",
        ("AC10", "AC16"),
        (MatrixScenario.Script(typed_signal=MISSING_CREDENTIALS_SIGNAL),),
        ProbeOutcome.PASSED,
        connectivity=ConnectivityState.ONLINE,
        expected_kind="agent_dead",
    ),
    MatrixScenario(
        "typed_signal_missing_credentials_offline",
        ("AC10", "AC16"),
        (MatrixScenario.Script(typed_signal=MISSING_CREDENTIALS_SIGNAL),),
        ProbeOutcome.PASSED,
        connectivity=ConnectivityState.OFFLINE,
        expected_kind="agent_dead",
    ),
    MatrixScenario(
        "typed_signal_broken_agent_online",
        ("AC10", "AC16"),
        (MatrixScenario.Script(typed_signal=BROKEN_AGENT_SIGNAL),),
        ProbeOutcome.PASSED,
        connectivity=ConnectivityState.ONLINE,
        expected_kind="agent_dead",
    ),
    MatrixScenario(
        "typed_signal_broken_agent_offline",
        ("AC10", "AC16"),
        (MatrixScenario.Script(typed_signal=BROKEN_AGENT_SIGNAL),),
        ProbeOutcome.PASSED,
        connectivity=ConnectivityState.OFFLINE,
        expected_kind="agent_dead",
    ),
    MatrixScenario(
        "typed_signal_bench_online",
        ("AC10", "AC16"),
        (MatrixScenario.Script(typed_signal=DEAD_SIGNAL),),
        ProbeOutcome.PASSED,
        connectivity=ConnectivityState.ONLINE,
        expected_kind="agent_dead",
    ),
    MatrixScenario(
        "typed_signal_bench_offline",
        ("AC10", "AC16"),
        (MatrixScenario.Script(typed_signal=DEAD_SIGNAL),),
        ProbeOutcome.PASSED,
        connectivity=ConnectivityState.OFFLINE,
        expected_kind="agent_dead",
    ),
    MatrixScenario(
        "bench_escalation",
        ("AC10",),
        (
            MatrixScenario.Script(typed_signal=DEAD_SIGNAL),
            MatrixScenario.Script(typed_signal=DEAD_SIGNAL),
        ),
        ProbeOutcome.PASSED,
        expected_kind="agent_dead",
    ),
    MatrixScenario(
        "false_bench_feedback",
        ("AC12",),
        (
            MatrixScenario.Script(typed_signal=DEAD_SIGNAL),
            MatrixScenario.Script(
                failed=False,
                facts=AttemptFacts(artifact_valid=True),
                advance_before_seconds=_BENCH_BACKOFF,
            ),
        ),
        ProbeOutcome.PASSED,
        expected_kind="completed",
    ),
    MatrixScenario(
        "bench_while_peer_cooling",
        ("AC11",),
        (MatrixScenario.Script(typed_signal=DEAD_SIGNAL),),
        ProbeOutcome.PASSED,
        precool=((AGENT_B, UnavailabilityReason.OUT_OF_CREDITS),),
        expected_kind="agent_dead",
    ),
)


def corpus_cases() -> tuple[CorpusCase, ...]:
    return load_corpus()


def corpus_agents() -> dict[str, str]:
    fixtures = load_fixtures()
    return {str(fixture["id"]): str(fixture["agent"]) for fixture in fixtures}


def probe_outcomes_for(case: CorpusCase) -> tuple[ProbeOutcome | None, ...]:
    termination = case.facts.termination
    unattributed = termination is None or termination.issuer is TerminationIssuer.UNKNOWN
    return HEALTHY_PROBE_OUTCOMES if unattributed else EVERY_PROBE_OUTCOME


def corpus_scenarios() -> tuple[MatrixScenario, ...]:
    agents = corpus_agents()
    scenarios: list[MatrixScenario] = []
    for case in load_corpus():
        for outcome in probe_outcomes_for(case):
            label = "probe_none" if outcome is None else outcome.value
            scenarios.append(
                MatrixScenario(
                    name=f"corpus_{case.id}__{label}",
                    acceptance=("AC9",),
                    attempts=(MatrixScenario.Script(agent=agents[case.id], facts=case.facts),),
                    probe_outcome=outcome,
                    corpus_id=case.id,
                )
            )
    return tuple(scenarios)


def matrix_entry_points() -> tuple[AgentEntryPoint, ...]:
    return tuple(AgentEntryPoint)


def _registered_route(entry_point: AgentEntryPoint) -> Callable[..., AttemptResult]:
    runners = registered_runners()
    if entry_point not in runners:
        raise AssertionError(f"entry point has no registered route: {entry_point.value}")
    return runners[entry_point]


def matrix_config() -> UnifiedConfig:
    return UnifiedConfig(
        agents={
            AGENT_A: AgentConfig(cmd="fake-agent-a"),
            AGENT_B: AgentConfig(cmd="fake-agent-b"),
        }
    )


def run_matrix_scenario(
    entry_point: AgentEntryPoint,
    scenario: MatrixScenario,
) -> tuple[MatrixScenario.Observation, RecoveryController, FakeClock]:
    clock = FakeClock(start=0.0)
    invocations: list[str] = []
    probe_calls: list[str] = []
    scripts = list(scenario.attempts)

    def probe_runner(_phase: str, agent: str) -> ProbeOutcome | None:
        probe_calls.append(agent)
        if scenario.probe_raises:
            raise RuntimeError("probe infrastructure unavailable")
        return scenario.probe_outcome

    def invoke(target: object, *_args: object, **kwargs: object) -> object:
        if not isinstance(target, InvokeAgentEffect):
            raise AssertionError("matrix invocation requires an InvokeAgentEffect")
        effect = target
        script = scripts.pop(0)
        if script.agent != effect.agent_name:
            raise AssertionError(f"expected {script.agent}, got {effect.agent_name}")
        invocations.append(effect.agent_name)
        finalizer = kwargs["attempt_finalizer"]
        if not isinstance(finalizer, _AttemptFinalizer):
            raise AssertionError("matrix invocation requires an attempt finalizer")
        exc = _script_exception(script) if script.failed else None
        finalizer(
            AttemptFinalization(
                1,
                script.failed,
                exc,
                script.workspace_changed,
                script.typed_signal,
                script.retryable_reason,
            )
        )
        return PipelineEvent.AGENT_FAILURE if script.failed else PipelineEvent.AGENT_SUCCESS

    deps = _deps(clock, invoke, probe_runner, scenario)
    config = matrix_config()
    controller = _controller(entry_point, config, deps, scenario)
    outcome_kinds: list[str] = []
    runner = _registered_route(entry_point)
    for script in scenario.attempts:
        _advance_and_prune(clock, controller, script)
        result = runner(
            effect=InvokeAgentEffect(
                agent_name=script.agent,
                phase=PHASE,
                prompt_file="prompt.md",
            ),
            config=config,
            controller=controller,
            deps=deps,
            state=None,
            run_id=RUN_ID,
        )
        outcome_kinds.append(result.outcome.kind.value)
    return (
        _observe(
            controller,
            scenario,
            outcome_kinds[-1],
            tuple(invocations),
            tuple(probe_calls),
            tuple(outcome_kinds),
        ),
        controller,
        clock,
    )


def run_matrix_scenario_with_selection(
    entry_point: AgentEntryPoint,
    scenario: MatrixScenario,
) -> tuple[MatrixScenario.Observation, RecoveryController, FakeClock, str | None]:
    if len(scenario.attempts) != 2:
        raise AssertionError("selection scenario requires exactly two attempts")
    clock = FakeClock(start=0.0)
    invocations: list[str] = []
    probe_calls: list[str] = []
    scripts = list(scenario.attempts)

    def probe_runner(_phase: str, agent: str) -> ProbeOutcome | None:
        probe_calls.append(agent)
        return scenario.probe_outcome

    def invoke(target: object, *_args: object, **kwargs: object) -> object:
        if not isinstance(target, InvokeAgentEffect):
            raise AssertionError("matrix invocation requires an InvokeAgentEffect")
        script = scripts.pop(0)
        if script.agent != target.agent_name:
            raise AssertionError(f"expected {script.agent}, got {target.agent_name}")
        invocations.append(target.agent_name)
        finalizer = kwargs["attempt_finalizer"]
        if not isinstance(finalizer, _AttemptFinalizer):
            raise AssertionError("matrix invocation requires an attempt finalizer")
        finalizer(
            AttemptFinalization(
                1,
                script.failed,
                _script_exception(script) if script.failed else None,
                script.workspace_changed,
                script.typed_signal,
                script.retryable_reason,
            )
        )
        return PipelineEvent.AGENT_FAILURE if script.failed else PipelineEvent.AGENT_SUCCESS

    deps = _deps(clock, invoke, probe_runner, scenario)
    config = matrix_config()
    controller = _controller(entry_point, config, deps, scenario)
    runner = _registered_route(entry_point)
    first = scenario.attempts[0]
    first_result = runner(
        effect=InvokeAgentEffect(agent_name=first.agent, phase=PHASE, prompt_file="prompt.md"),
        config=config,
        controller=controller,
        deps=deps,
        state=None,
        run_id=RUN_ID,
    )
    selected = controller.preferred_agent_index(PHASE, AGENTS).agent
    second_result = runner(
        effect=InvokeAgentEffect(
            agent_name=selected or "",
            phase=PHASE,
            prompt_file="prompt.md",
        ),
        config=config,
        controller=controller,
        deps=deps,
        state=None,
        run_id=RUN_ID,
    )
    outcome_kinds = (first_result.outcome.kind.value, second_result.outcome.kind.value)
    observation = _observe(
        controller,
        scenario,
        outcome_kinds[-1],
        tuple(invocations),
        tuple(probe_calls),
        outcome_kinds,
    )
    return observation, controller, clock, selected


def run_via_finalize_attempt(
    entry_point: AgentEntryPoint,
    scenario: MatrixScenario,
) -> tuple[MatrixScenario.Observation, RecoveryController, FakeClock]:
    clock = FakeClock(start=0.0)
    probe_calls: list[str] = []

    def probe_runner(_phase: str, agent: str) -> ProbeOutcome | None:
        probe_calls.append(agent)
        if scenario.probe_raises:
            raise RuntimeError("probe infrastructure unavailable")
        return scenario.probe_outcome

    def unreachable_invoke(target: object, *_args: object, **_kwargs: object) -> object:
        raise AssertionError(f"reducer-style finalize must not invoke agents: {target!r}")

    deps = _deps(clock, unreachable_invoke, probe_runner, scenario)
    config = matrix_config()
    controller = _controller(entry_point, config, deps, scenario)
    outcome_kinds: list[str] = []
    for script in scenario.attempts:
        _advance_and_prune(clock, controller, script)
        outcome = controller.finalize_attempt(
            PHASE,
            script.agent,
            facts=script.facts,
            attempt_failed=script.failed,
            typed_signal=script.typed_signal,
            exc=_script_exception(script) if script.failed else None,
            retryable_reason=script.retryable_reason,
            attempt_key=None,
            config_fingerprint=_config_fingerprint(script.agent),
            session_ref=_session_ref(entry_point, script.agent),
        )
        outcome_kinds.append(outcome.kind.value)
    return (
        _observe(
            controller,
            scenario,
            outcome_kinds[-1],
            (),
            tuple(probe_calls),
            tuple(outcome_kinds),
        ),
        controller,
        clock,
    )


def _script_exception(script: MatrixScenario.Script) -> AgentInvocationError:
    exc = AgentInvocationError(
        script.agent,
        script.returncode,
        stderr=script.message,
        parsed_output=list(script.parsed_output),
    )
    exc.facts = script.facts
    return exc


def _deps(
    clock: FakeClock,
    invoke: AttemptInvoke,
    probe_runner: Callable[[str, str], ProbeOutcome | None],
    scenario: MatrixScenario,
) -> AttemptRunnerDeps:
    connectivity = (lambda: scenario.connectivity) if scenario.connectivity is not None else None
    return AttemptRunnerDeps(
        clock,
        invoke,
        probe_runner,
        None,
        connectivity,
    )


def _controller(
    entry_point: AgentEntryPoint,
    config: UnifiedConfig,
    deps: AttemptRunnerDeps,
    scenario: MatrixScenario,
) -> RecoveryController:
    controller = build_entry_point_recovery_controller(
        entry_point,
        config=config,
        policy_bundle=None,
        deps=deps,
    )
    controller.register_phase_chain(PHASE, AGENTS)
    for agent, reason in scenario.precool:
        controller.unavailability_store.mark_unavailable(PHASE, agent, reason)
    return controller


def _advance_and_prune(
    clock: FakeClock,
    controller: RecoveryController,
    script: MatrixScenario.Script,
) -> None:
    if script.advance_before_seconds <= 0:
        return
    clock.advance(script.advance_before_seconds)
    store = controller.unavailability_store
    if isinstance(store, AgentUnavailabilityTracker):
        store.prune_expired()


def _observe(
    controller: RecoveryController,
    scenario: MatrixScenario,
    outcome_kind: str,
    invocations: tuple[str, ...],
    probe_calls: tuple[str, ...],
    outcome_kinds: tuple[str, ...] = (),
) -> MatrixScenario.Observation:
    agent = scenario.attempts[-1].agent
    ledger_snapshot = controller.ledger.snapshot()
    raw_entries = ledger_snapshot["entries"]
    if not isinstance(raw_entries, dict):
        raise AssertionError("ledger snapshot entries must be a dictionary")
    status = controller.ledger.status(PHASE, agent)
    waiting_payload = tuple(controller.waiting_state_payload(PHASE, list(AGENTS)))
    store_snap = controller.unavailability_store.snapshot()
    raw_reasons = store_snap.get("unavailability_reasons", {})
    unavailability_reasons = dict(raw_reasons) if isinstance(raw_reasons, dict) else {}
    return MatrixScenario.Observation(
        outcome_kind=outcome_kind,
        availability=controller.unavailability_store.is_available(PHASE, agent),
        wait_ms=controller.unavailability_store.earliest_unavailable_wait_ms(PHASE, [agent]),
        ledger_status=(
            "spent" if status.is_spent else "cooldown" if status.is_cooldown else "eligible"
        ),
        ledger_entries=dict(raw_entries),
        verdict_record=_stable(controller.last_verdict_record),
        false_bench_count=controller.false_bench_feedback.count,
        invocations=invocations,
        probe_calls=probe_calls,
        outcome_kinds=outcome_kinds,
        waiting_payload=waiting_payload,
        unavailability_reasons=unavailability_reasons,
    )


def _config_fingerprint(agent: str) -> str:
    config = matrix_config()
    resolved = config.agents.get(agent)
    if resolved is None:
        return f"agent:{agent}"
    transport = resolved.transport
    return agent_config_fingerprint(
        agent,
        cmd=resolved.cmd,
        model=resolved.model,
        model_flag=resolved.model_flag,
        json_parser=resolved.json_parser.value,
        transport=transport.value if transport is not None else None,
    )


def _session_ref(entry_point: AgentEntryPoint, agent: str) -> dict[str, str]:
    return {
        "run_id": RUN_ID,
        "phase": PHASE,
        "agent": agent,
        "entry_point": entry_point.value,
    }


def _stable(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _stable(item)
            for key, item in value.items()
            if "timestamp" not in str(key).casefold()
        }
    if isinstance(value, list | tuple):
        return tuple(_stable(item) for item in value)
    return value
