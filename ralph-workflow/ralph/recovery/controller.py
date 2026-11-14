from __future__ import annotations

import ast
from dataclasses import replace
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from ralph.agents.invoke._completion_termination import attempt_facts_for_failure
from ralph.agents.timeout_clock import Clock, SystemClock
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.pipeline import progress
from ralph.pipeline._wait_reason import WaitReason
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_retry_intent import (
    cleared_agent_retry_intent,
    resume_agent_retry_intent,
)
from ralph.pipeline.effects import ExitFailureEffect
from ralph.pipeline.state import FalloverRecord
from ralph.policy.models import PhaseRetryPolicy
from ralph.policy.resolution_names import RESOLUTION_DRAIN_NAME
from ralph.policy.validation import (
    CheckpointPolicyMismatchError,
    PolicyValidationError,
)
from ralph.recovery.agent_failure_ledger import (
    AgentFailureLedger,
    AgentStatus,
    AttemptFact,
    Cooldown,
    EligibilityWait,
    Spent,
    Unavailable,
    WaitKind,
    eligibility_wait_from_statuses,
)
from ralph.recovery.agent_selection import (
    AgentSelection,
    agent_availability,
    format_selection_evidence,
    select_preferred_agent,
)
from ralph.recovery.agent_unavailability_tracker import (
    AgentUnavailabilityTracker,
    UnavailabilityStore,
)
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.attempt_outcome_kind import AttemptOutcomeKind
from ralph.recovery.attribution import attribute_attempt
from ralph.recovery.classifier import (
    ClassifiedFailure,
    FailureCategory,
    FailureClassifier,
    FailureContext,
)
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome
from ralph.recovery.cycle_cap import CycleCap
from ralph.recovery.dead_agent_verdict import TypedSignal
from ralph.recovery.end_attempt import end_attempt
from ralph.recovery.events import FailureEvent, FailureEventBus, FalloverEvent
from ralph.recovery.false_bench_feedback import FalseBenchFeedback
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.timeout_defaults import (
    AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT,
    AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT,
)

__all__ = [
    "ENGINE_ORIGIN_REASONS",
    "RecoveryController",
    "RecoveryControllerOptions",
    "build_dead_agent_verdict",
    "build_typed_agent_signal",
    "compute_backoff_ms",
    "is_configuration_failure",
]


ENGINE_ORIGIN_REASONS: frozenset[str] = frozenset({str(FailureCategory.USER_CONFIG)})

_ENGINE_ORIGIN_MESSAGE_MARKERS: tuple[str, ...] = (
    "Contract mismatch",
    "is not bound",
    "is not defined",
    "Checkpoint was saved at phase",
    "no longer exists in pipeline.toml",
)


def is_configuration_failure(
    exc: BaseException | None = None,
    reason: str | None = None,
) -> bool:
    if exc is not None:
        if isinstance(exc, (PolicyValidationError, CheckpointPolicyMismatchError)):
            return True
        name = type(exc).__name__
        if name in {
            "PolicyValidationError",
            "CheckpointPolicyMismatchError",
            "ArtifactContractMismatchError",
            "DrainNotBoundError",
            "UnknownAgentError",
            "McpConfigError",
        }:
            return True
        msg = str(exc)
        if any(marker in msg for marker in _ENGINE_ORIGIN_MESSAGE_MARKERS):
            return True
    return reason is not None and reason in ENGINE_ORIGIN_REASONS


_REQUIRED_TWO_STATE_METHODS: frozenset[str] = frozenset(
    {"_mark_agent_unavailable", "_apply_chain_retry"}
)

_SPENT_RETRY_EXHAUSTED_REASON = "retry_exhausted"

_STORE_BENCHED_REASON = "unavailable"


_CONTROLLER_SOURCE_TREE_CACHE: dict[str, tuple[str, ast.Module]] = {}


def _controller_source_tree(
    *,
    parse_fn: Callable[[str], ast.Module],
    read_fn: Callable[[], str],
) -> tuple[str, ast.Module]:
    if _CONTROLLER_SOURCE_TREE_CACHE:
        return next(iter(_CONTROLLER_SOURCE_TREE_CACHE.values()))
    source = read_fn()
    try:
        tree = parse_fn(source)
    except SyntaxError as exc:
        msg = (
            "recovery/controller.py failed to parse during the two-state"
            " invariant check. The controller source is broken; the two-state"
            " recovery invariant cannot be verified."
            f" Parser error: {exc}"
        )
        raise RuntimeError(msg) from exc
    _CONTROLLER_SOURCE_TREE_CACHE["source"] = (source, tree)
    return source, tree


def _parse_source(source: str) -> ast.Module:
    return ast.parse(source)


def _reset_controller_source_tree_cache() -> None:
    _CONTROLLER_SOURCE_TREE_CACHE.clear()


def _assert_two_state_invariant() -> None:
    _source, tree = _controller_source_tree(
        parse_fn=_parse_source,
        read_fn=lambda: Path(__file__).resolve().read_text(encoding="utf-8"),
    )
    class_node: ast.ClassDef | None = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "RecoveryController":
            class_node = node
            break
    if class_node is None:
        msg = (
            "Two-state invariant violated: RecoveryController class not found"
            " in ralph.recovery.controller. The two-state recovery invariant"
            " requires a single RecoveryController class to own the two"
            " recovery paths. Restore the class definition or update the"
            " import-time invariant in ralph.recovery.controller."
        )
        raise RuntimeError(msg)
    method_names = {m.name for m in class_node.body if isinstance(m, ast.FunctionDef)}
    missing = _REQUIRED_TWO_STATE_METHODS - method_names
    if missing:
        missing_list = ", ".join(sorted(missing))
        msg = (
            "Two-state recovery invariant violated: RecoveryController is missing"
            f" required method(s): {missing_list}. The two MAIN RULES require"
            " exactly two recovery paths: (1) exponential backoff to the next"
            " agent via _mark_agent_unavailable, and (2) same-agent retry via"
            " _apply_chain_retry. Restore the missing method(s) or update the"
            " import-time invariant in ralph.recovery.controller and the test"
            " in tests/recovery/test_two_state_invariant.py in the same commit."
        )
        raise RuntimeError(msg)


_assert_two_state_invariant()


def _find_all_agents_unavailable_if(func_body: list[ast.stmt]) -> ast.If | None:
    for stmt in func_body:
        if not isinstance(stmt, ast.If):
            continue
        try:
            test_src = ast.unparse(stmt.test)
        except Exception:
            continue
        if "self._is_agent_available" in test_src and "all(" in test_src:
            return stmt
    return None


def _find_enter_phase_failed_call(func_body: list[ast.stmt]) -> ast.stmt | None:
    found_stmt: ast.stmt | None = None
    for stmt in func_body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "_enter_phase_failed":
                found_stmt = stmt
    return found_stmt


def _assert_never_exit_invariant() -> None:
    _source, tree = _controller_source_tree(
        parse_fn=_parse_source,
        read_fn=lambda: Path(__file__).resolve().read_text(encoding="utf-8"),
    )
    class_node: ast.ClassDef | None = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "RecoveryController":
            class_node = node
            break
    if class_node is None:
        msg = (
            "Never-exit invariant violated: RecoveryController class not found"
            " in ralph.recovery.controller. The never-exit invariant requires"
            " a single RecoveryController class to own the never-exit"
            " recovery branch. Restore the class definition or update the"
            " import-time invariant in ralph.recovery.controller."
        )
        raise RuntimeError(msg)
    handle_retry_node: ast.FunctionDef | None = None
    for m in class_node.body:
        if isinstance(m, ast.FunctionDef) and m.name == "_handle_retry_progression":
            handle_retry_node = m
            break
    if handle_retry_node is None:
        msg = (
            "Never-exit invariant violated: RecoveryController is missing"
            " the _handle_retry_progression method. The never-exit"
            " invariant requires the all-agents-unavailable branch in"
            " _handle_retry_progression to return before"
            " _enter_phase_failed is reachable. Restore the method or"
            " update the import-time invariant in ralph.recovery.controller."
        )
        raise RuntimeError(msg)

    func_body: list[ast.stmt] = list(handle_retry_node.body)
    all_agents_unavailable_if = _find_all_agents_unavailable_if(func_body)
    if all_agents_unavailable_if is None:
        msg = (
            "Never-exit invariant violated: RecoveryController."
            "_handle_retry_progression is missing the all-agents-unavailable"
            " ``if all(not self._is_agent_available(phase, agent) for"
            " agent in chain.agents):`` branch. The never-exit invariant"
            " requires this branch to return BEFORE _enter_phase_failed"
            " is reachable. Restore the branch in _handle_retry_progression"
            " or update the import-time invariant in"
            " ralph.recovery.controller."
        )
        raise RuntimeError(msg)

    enter_phase_failed_call = _find_enter_phase_failed_call(func_body)
    if enter_phase_failed_call is None:
        msg = (
            "Never-exit invariant violated: RecoveryController."
            "_handle_retry_progression is missing the"
            " _enter_phase_failed call. The never-exit invariant requires"
            " the all-agents-unavailable branch to return BEFORE"
            " _enter_phase_failed is reachable. Restore the call in"
            " _handle_retry_progression or update the import-time"
            " invariant in ralph.recovery.controller."
        )
        raise RuntimeError(msg)

    all_agents_unavailable_index = func_body.index(all_agents_unavailable_if)
    enter_phase_failed_index = func_body.index(enter_phase_failed_call)
    if all_agents_unavailable_index >= enter_phase_failed_index:
        msg = (
            "Never-exit invariant violated: the all-agents-unavailable"
            " ``if`` statement in RecoveryController._handle_retry_progression"
            " appears at body index"
            f" {all_agents_unavailable_index} which is NOT before the"
            f" _enter_phase_failed call at body index"
            f" {enter_phase_failed_index}. The never-exit invariant"
            " requires the all-agents-unavailable branch to be at a LOWER"
            " body index (i.e. earlier in the function body) than"
            " _enter_phase_failed so the branch's return statement"
            " short-circuits before the failure call. Reorder the"
            " branches in _handle_retry_progression or update the"
            " import-time invariant in ralph.recovery.controller and the"
            " test in tests/recovery/test_two_state_invariant.py in the"
            " same commit."
        )
        raise RuntimeError(msg)

    has_return = any(isinstance(node, ast.Return) for node in ast.walk(all_agents_unavailable_if))
    if not has_return:
        msg = (
            "Never-exit invariant violated: the all-agents-unavailable"
            " ``if`` statement in RecoveryController._handle_retry_progression"
            " does not contain a ``return`` statement. The never-exit"
            " invariant requires the branch to return"
            " ``state.copy_with(is_waiting_state=True,"
            " last_retry_delay_ms=...)`` and an empty effects list so the"
            " run loop sleeps on ``last_retry_delay_ms`` and re-enters"
            " the same phase. Add a return statement to the branch or"
            " update the import-time invariant in"
            " ralph.recovery.controller and the test in"
            " tests/recovery/test_two_state_invariant.py in the same"
            " commit."
        )
        raise RuntimeError(msg)


_assert_never_exit_invariant()

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ralph.pipeline.effects import Effect
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import AgentChainConfig, PipelinePolicy, PolicyBundle
    from ralph.recovery.attempt_outcome import AttemptOutcome
    from ralph.recovery.attribution import AttributionVerdict
    from ralph.recovery.canary_probe import ProbeOutcome
    from ralph.recovery.dead_agent_verdict import DeadAgentVerdict, PersistedVerdict
    from ralph.workspace.probe import WorkspaceChangeProbe

    _DeadAgentVerdictPredicate = Callable[[DeadAgentVerdict], bool]
    _RuntimeIssuedPredicate = Callable[[AttemptFacts], bool]
    _DecideDeadAgentVerdict = Callable[
        [AttemptFacts, AttributionVerdict, ProbeOutcome | None, TypedSignal | None],
        PersistedVerdict,
    ]


# Typed conflict attempts that produced no progress must reach the shared
# cooldown system like every other unproductive agent attempt.
_UNPRODUCTIVE_CONFLICT_OUTCOMES: dict[ConflictAttemptOutcome, UnavailabilityReason] = {
    ConflictAttemptOutcome.STALLED: UnavailabilityReason.DEAD_AGENT_NO_PROGRESS,
    ConflictAttemptOutcome.NO_ACTIVITY: UnavailabilityReason.NO_OUTPUT_AT_START,
}


def _typed_conflict_attempt_reason(
    outcome: ConflictAttemptOutcome,
    unresolved_count: int,
) -> str:
    if outcome.value == "unresolved_remaining":
        return f"agent reported success but {unresolved_count} conflicted path(s) remain"
    if outcome.value == "stalled":
        return (
            f"agent reported success but made no progress: {unresolved_count} "
            "conflicted path(s) remain unchanged"
        )
    if outcome.value == "no_activity":
        return "candidate produced no activity before it exited"
    return "conflict attempt failed"


def _build_exit_failure_effect(*, reason: str) -> Effect:
    return ExitFailureEffect(reason=reason)


def _build_fallover_record(
    *,
    phase: str,
    from_agent: str,
    to_agent: str,
    timestamp_iso: str,
) -> FalloverRecord:
    return FalloverRecord(
        phase=phase,
        from_agent=from_agent,
        to_agent=to_agent,
        timestamp_iso=timestamp_iso,
    )


class _RetryHintPath(Protocol):
    def __call__(self, phase: str, *, pipeline_policy: PipelinePolicy | None = None) -> str: ...


def _get_required_artifact_helpers() -> tuple[Callable[[str, str], str], _RetryHintPath]:
    module = import_module("ralph.phases.required_artifacts")
    namespace = cast("dict[str, object]", module.__dict__)
    build_retry_hint = cast("Callable[[str, str], str]", namespace["build_retry_hint"])
    retry_hint_path = cast("_RetryHintPath", namespace["retry_hint_path"])
    return build_retry_hint, retry_hint_path


def compute_backoff_ms(base_ms: int, attempt: int, max_ms: int = 30_000) -> int:
    exponent_factor: int = 2**attempt
    delay: int = base_ms * exponent_factor
    return min(delay, max_ms)


def _dead_agent_verdict_namespace() -> dict[str, object]:
    module = import_module("ralph.recovery.dead_agent_verdict")
    return cast("dict[str, object]", module.__dict__)


def _typed_signal_type(namespace: dict[str, object]) -> type[TypedSignal]:
    candidate = namespace["TypedSignal"]
    if candidate is not TypedSignal:
        raise TypeError("ralph.recovery.dead_agent_verdict.TypedSignal must be a TypedSignal type")
    return TypedSignal


def build_typed_agent_signal(
    signal_name: str | None,
    signal_provider_originated: bool,
    facts: AttemptFacts | None,
) -> TypedSignal | None:
    if signal_name is None:
        return None
    namespace = _dead_agent_verdict_namespace()
    typed_signal_type = _typed_signal_type(namespace)
    runtime_issued = cast(
        "_RuntimeIssuedPredicate",
        namespace["runtime_issued_termination_is_not_agent_fault"],
    )
    agent_initiated = True
    if facts is not None:
        agent_initiated = not runtime_issued(facts)
    return typed_signal_type(
        name=signal_name,
        provider_originated=signal_provider_originated,
        agent_initiated=agent_initiated,
    )


def build_dead_agent_verdict(
    facts: AttemptFacts,
    *,
    attribution: AttributionVerdict | None = None,
    probe_outcome: ProbeOutcome | None = None,
    typed_signal: TypedSignal | None = None,
) -> PersistedVerdict:
    namespace = _dead_agent_verdict_namespace()
    decide = cast(
        "_DecideDeadAgentVerdict",
        namespace["decide_dead_agent_verdict"],
    )
    effective_attribution = attribution
    if effective_attribution is None:
        effective_attribution = attribute_attempt(
            facts,
            typed_signal_observed=typed_signal is not None,
            typed_signal_provider_originated=bool(
                typed_signal is not None and typed_signal.provider_originated
            ),
        )
    return decide(facts, effective_attribution, probe_outcome, typed_signal)


_HEALTHY_VERDICT_VALUE = "healthy"

_MIN_PEER_CHAIN_AGENTS = 2


def _verdict_benches_agent(verdict: PersistedVerdict) -> bool:
    namespace = _dead_agent_verdict_namespace()
    predicate = cast(
        "_DeadAgentVerdictPredicate",
        namespace["verdict_benches_agent"],
    )
    return predicate(verdict.verdict)


def _verdict_counts_toward_agent_state(verdict: PersistedVerdict) -> bool:
    namespace = _dead_agent_verdict_namespace()
    predicate = cast(
        "_DeadAgentVerdictPredicate",
        namespace["verdict_counts_toward_agent_state"],
    )
    return predicate(verdict.verdict)


def _attempt_observation_present(facts: AttemptFacts) -> bool:
    return (
        facts.termination is not None
        or facts.exit_status is not None
        or facts.elapsed_to_terminal_ms is not None
        or bool(facts.event_kinds)
        or bool(facts.tool_calls)
    )


def _charges_agent_ledger(outcome: AttemptOutcome, *, peer_failures_identical: bool) -> bool:
    if outcome.counts_toward_agent_state:
        return True
    if outcome.kind is not AttemptOutcomeKind.TASK_OR_ENVIRONMENT_FAILURE:
        return False
    return not peer_failures_identical


def _without_broken_agent_reason(failure: ClassifiedFailure) -> ClassifiedFailure:
    if failure.unavailability_reason is not UnavailabilityReason.BROKEN_AGENT:
        return failure
    return replace(failure, is_unavailable=False, unavailability_reason=None)


def _without_unavailability_reason(failure: ClassifiedFailure) -> ClassifiedFailure:
    if not failure.is_unavailable and failure.unavailability_reason is None:
        return failure
    return replace(failure, is_unavailable=False, unavailability_reason=None)


def _failure_for_attempt_outcome(
    failure: ClassifiedFailure,
    verdict: PersistedVerdict,
) -> ClassifiedFailure:
    if str(verdict.verdict) == _HEALTHY_VERDICT_VALUE:
        return _without_unavailability_reason(failure)
    if _verdict_counts_toward_agent_state(verdict):
        return _without_broken_agent_reason(failure)
    return _without_unavailability_reason(failure)


class RecoveryController:
    def __init__(
        self,
        *,
        options: RecoveryControllerOptions | None = None,
        ledger: AgentFailureLedger | None = None,
        reset_hint_backend: FileBackend | None = None,
    ) -> None:
        opts = options or RecoveryControllerOptions()
        self._reset_hint_backend: FileBackend = (
            reset_hint_backend if reset_hint_backend is not None else DEFAULT_FILE_BACKEND
        )
        self._phase_chains: dict[str, tuple[str, ...]] = {}
        self._last_selection_state: dict[
            str, tuple[str | None, tuple[tuple[str, bool, bool, bool, str | None], ...]]
        ] = {}
        self._phase_launch_gates: dict[str, bool] = {}
        self._cap = CycleCap(cap=opts.cycle_cap)
        self._classifier = opts.classifier or FailureClassifier()
        self._bus = opts.event_bus or FailureEventBus()
        self._policy_bundle = opts.policy_bundle
        self._clock: Clock = opts.clock or SystemClock()
        self._backoff_attempts: dict[str, int] = dict(opts.backoff_attempts or {})
        self._consecutive_unproductive_limit = (
            opts.agent_max_consecutive_unproductive
            if opts.agent_max_consecutive_unproductive is not None
            else AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT
        )
        self._total_unproductive_limit = (
            opts.agent_max_total_unproductive
            if opts.agent_max_total_unproductive is not None
            else AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT
        )
        if ledger is not None:
            self._ledger = ledger
        elif opts.ledger is not None:
            self._ledger = opts.ledger
        else:
            self._ledger = AgentFailureLedger(
                clock=self._clock,
                consecutive_limit=self._consecutive_unproductive_limit,
                total_limit=self._total_unproductive_limit,
            )
        self._workspace_probe: WorkspaceChangeProbe | None = opts.workspace_probe
        self._dead_agent_probe_runner = opts.dead_agent_probe_runner
        self._last_verdict_record: dict[str, object] | None = None
        self._last_resolved_verdict: PersistedVerdict | None = None
        self._last_attempt_outcome: AttemptOutcome | None = None
        self._last_demoted_reason: str | None = None
        self._dead_agent_verdict_runner = opts.dead_agent_verdict_runner
        self._false_bench_feedback: FalseBenchFeedback = (
            opts.false_bench_feedback
            if opts.false_bench_feedback is not None
            else FalseBenchFeedback()
        )
        self._attempt_outcome_memo: dict[
            str, tuple[AttemptOutcome, str | None, PersistedVerdict | None]
        ] = {}
        self._attempt_outcome_keys: dict[tuple[str, str], list[str]] = {}
        self._claimed_attempt_outcome_keys: set[str] = set()
        self._peer_failure_signatures: dict[str, dict[str, tuple[object, ...]]] = {}
        self._attempt_ordinals: dict[tuple[str, str], int] = {}
        self._spent_agents: dict[str, set[str]] = {
            k: set(v) for k, v in (opts.spent_agents or {}).items()
        }
        if opts.unavailability_store is not None:
            self._unavailability_tracker: UnavailabilityStore = opts.unavailability_store
            existing_ledger: object = getattr(self._unavailability_tracker, "_failure_ledger", None)
            if existing_ledger is None:
                object.__setattr__(self._unavailability_tracker, "_failure_ledger", self._ledger)
        else:
            self._unavailability_tracker = AgentUnavailabilityTracker(
                clock=self._clock,
                backoff_policy=opts.unavailability_backoff_policy,
                initial_timeouts=opts.unavailable_timeouts,
                initial_entries=opts.unavailability_entries,
                initial_backoff_attempts=opts.backoff_attempts,
                ledger=self._ledger,
            )

    @property
    def event_bus(self) -> FailureEventBus:
        return self._bus

    @property
    def ledger(self) -> AgentFailureLedger:
        return self._ledger

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def workspace_probe(self) -> WorkspaceChangeProbe | None:
        return self._workspace_probe

    @property
    def unavailability_store(self) -> UnavailabilityStore:
        return self._unavailability_tracker

    @property
    def cycle_cap(self) -> int:
        return self._cap.cap

    @property
    def false_bench_feedback(self) -> FalseBenchFeedback:
        return self._false_bench_feedback

    @property
    def last_attempt_outcome(self) -> AttemptOutcome | None:
        return self._last_attempt_outcome

    @property
    def last_resolved_verdict(self) -> PersistedVerdict | None:
        return self._last_resolved_verdict

    @property
    def last_demoted_reason(self) -> str | None:
        return self._last_demoted_reason

    def handle(
        self,
        state: PipelineState,
        raw_failure: BaseException | str,
        context: FailureContext,
        *,
        workspace_changed: bool = False,
        artifact_valid: bool = False,
        facts: AttemptFacts | None = None,
    ) -> tuple[PipelineState, list[Effect], FailureEvent]:
        phase = context.phase
        agent = context.agent
        effective_facts = facts
        if effective_facts is None:
            effective_facts = context.facts
        failure = context.classified_failure or self._classifier.classify(
            raw_failure,
            phase=phase,
            agent=agent,
            connectivity_state=state.last_connectivity_state,
            facts=effective_facts,
        )
        if effective_facts is None:
            effective_facts = failure.facts
        chain = state.chain_for_phase(phase)
        if chain is not None and chain.agents:
            self._phase_chains[phase] = tuple(chain.agents)
        current_agent = agent or (
            chain.agents[chain.current_index]
            if chain is not None and chain.agents and chain.current_index < len(chain.agents)
            else None
        )
        attempt_facts = replace(
            effective_facts if effective_facts is not None else AttemptFacts(),
            artifact_valid=artifact_valid,
            workspace_changed=workspace_changed,
        )
        outcome = self.finalize_attempt(
            phase,
            current_agent,
            facts=attempt_facts,
            attempt_failed=True,
            exc=raw_failure if isinstance(raw_failure, BaseException) else None,
            retryable_reason=(
                str(failure.category)
                if failure.category == FailureCategory.USER_CONFIG
                else (raw_failure if isinstance(raw_failure, str) else None)
            ),
        )
        bench_applied = outcome.benches_agent
        if bench_applied:
            failure = replace(
                failure,
                is_unavailable=True,
                unavailability_reason=UnavailabilityReason.DEAD_AGENT_NO_PROGRESS,
            )
        elif self._last_resolved_verdict is not None:
            failure = _failure_for_attempt_outcome(failure, self._last_resolved_verdict)

        if failure.category == FailureCategory.INTEGRATION:
            return self._handle_integration_continuation(state, failure, phase)
        return self._handle_classified_failure(
            state,
            failure,
            context,
            bench_applied=bench_applied,
            sole_demoted_reason=self._last_demoted_reason,
        )

    def next_attempt_ordinal(self, phase: str, agent: str | None) -> int:
        key = (phase, agent or "")
        ordinal = self._attempt_ordinals.get(key, 0) + 1
        self._attempt_ordinals[key] = ordinal
        return ordinal

    def finalize_attempt(
        self,
        phase: str,
        agent: str | None,
        *,
        facts: AttemptFacts,
        attempt_failed: bool,
        typed_signal: TypedSignal | None = None,
        exc: BaseException | None = None,
        retryable_reason: str | None = None,
        probe_mode: bool = False,
        attempt_key: str | None = None,
        config_fingerprint: str | None = None,
        session_ref: Mapping[str, str] | None = None,
        charge_ledger: bool = True,
    ) -> AttemptOutcome:
        if attempt_key is not None:
            memoized = self._attempt_outcome_memo.get(attempt_key)
            if memoized is not None:
                return self._restore_memoized_attempt(memoized)
        else:
            claimed = self._claim_memoized_attempt(phase, agent)
            if claimed is not None:
                return self._restore_memoized_attempt(claimed)

        effective_charge_ledger = charge_ledger and not is_configuration_failure(
            exc, retryable_reason
        )
        outcome, demoted_reason, resolved_verdict = self._finalize_attempt_now(
            phase,
            agent,
            facts=facts,
            attempt_failed=attempt_failed,
            typed_signal=typed_signal,
            exc=exc,
            retryable_reason=retryable_reason,
            probe_mode=probe_mode,
            config_fingerprint=config_fingerprint,
            session_ref=session_ref,
            charge_ledger=effective_charge_ledger,
        )
        self._last_attempt_outcome = outcome
        self._last_demoted_reason = demoted_reason
        self._last_resolved_verdict = resolved_verdict
        if attempt_key is not None:
            self._attempt_outcome_memo[attempt_key] = (outcome, demoted_reason, resolved_verdict)
            self._attempt_outcome_keys.setdefault((phase, agent or ""), []).append(attempt_key)
        return outcome

    def _restore_memoized_attempt(
        self,
        memoized: tuple[AttemptOutcome, str | None, PersistedVerdict | None],
    ) -> AttemptOutcome:
        outcome, demoted_reason, resolved_verdict = memoized
        self._last_attempt_outcome = outcome
        self._last_demoted_reason = demoted_reason
        self._last_resolved_verdict = resolved_verdict
        return outcome

    def _claim_memoized_attempt(
        self,
        phase: str,
        agent: str | None,
    ) -> tuple[AttemptOutcome, str | None, PersistedVerdict | None] | None:
        keys = self._attempt_outcome_keys.get((phase, agent or ""))
        if not keys:
            return None
        for key in reversed(keys):
            if key in self._claimed_attempt_outcome_keys:
                continue
            memoized = self._attempt_outcome_memo.get(key)
            if memoized is None:
                continue
            self._claimed_attempt_outcome_keys.add(key)
            return memoized
        return None

    def _finalize_attempt_now(
        self,
        phase: str,
        agent: str | None,
        *,
        facts: AttemptFacts | None,
        attempt_failed: bool,
        typed_signal: TypedSignal | None,
        exc: BaseException | None,
        retryable_reason: str | None,
        probe_mode: bool,
        config_fingerprint: str | None,
        session_ref: Mapping[str, str] | None,
        charge_ledger: bool,
    ) -> tuple[AttemptOutcome, str | None, PersistedVerdict | None]:
        attempt_facts = facts if facts is not None else AttemptFacts()
        peer_failures_identical = self._peer_failures_identical(phase, agent, attempt_facts)
        signal = attempt_facts.typed_signal
        if signal is None and exc is not None:
            signal = attempt_facts_for_failure(exc).typed_signal
        signal_name = signal.name if signal is not None else None
        signal_provider_originated = signal.provider_originated if signal is not None else False
        effective_signal = typed_signal
        if effective_signal is None and signal is not None:
            effective_signal = signal
        probe_outcome: ProbeOutcome | None = None
        resolved_verdict: PersistedVerdict | None = None
        if not probe_mode:
            resolved_verdict = self._resolve_dead_agent_verdict(
                phase,
                agent,
                facts=facts,
                exc=exc,
                signal_name=signal_name,
                signal_provider_originated=signal_provider_originated,
            )
            if resolved_verdict is not None:
                probe_outcome = resolved_verdict.probe_outcome
                if resolved_verdict.typed_signal is not None:
                    effective_signal = resolved_verdict.typed_signal
        outcome = end_attempt(
            attempt_facts,
            attempt_failed=attempt_failed,
            typed_signal=effective_signal,
            probe_outcome=probe_outcome,
            peer_failures_identical=peer_failures_identical,
            retryable_reason=retryable_reason,
        )
        remembered_verdict = outcome.verdict if outcome.verdict is not None else resolved_verdict
        if remembered_verdict is not None:
            self._remember_verdict(phase, agent, remembered_verdict)
        fingerprint = config_fingerprint
        if fingerprint is None and agent is not None:
            fingerprint = f"agent:{agent}"
        demoted_reason: str | None = None
        if outcome.benches_agent and not probe_mode and agent is not None:
            self.record_confirmed_unavailability(
                phase,
                agent,
                UnavailabilityReason.DEAD_AGENT_NO_PROGRESS,
                outcome.verdict,
            )
            self._false_bench_feedback.note_bench(
                phase,
                agent,
                str(UnavailabilityReason.DEAD_AGENT_NO_PROGRESS),
                config_fingerprint=fingerprint or "",
                verdict_ref=self._last_verdict_record or {},
                session_ref=session_ref or {},
            )
        if (
            _charges_agent_ledger(outcome, peer_failures_identical=peer_failures_identical)
            and not probe_mode
            and charge_ledger
            and agent is not None
        ):
            demoted_reason = self.record_attempt_fact(
                phase,
                agent,
                artifact_valid=attempt_facts.artifact_valid,
                workspace_changed=attempt_facts.workspace_changed,
            )
        if outcome.kind is AttemptOutcomeKind.COMPLETED:
            self.reset_backoff(phase, agent)
            if agent is not None:
                self._false_bench_feedback.note_success(
                    phase,
                    agent,
                    config_fingerprint=fingerprint or "",
                )
            self._peer_failure_signatures.pop(phase, None)
        elif agent is not None and _attempt_observation_present(attempt_facts):
            self._peer_failure_signatures.setdefault(phase, {})[agent] = self._failure_signature(
                attempt_facts
            )
        return outcome, demoted_reason, resolved_verdict

    def _failure_signature(self, facts: AttemptFacts) -> tuple[object, ...]:
        termination = facts.termination
        return (
            facts.artifact_valid,
            str(termination.issuer) if termination is not None else None,
            facts.exit_status,
            facts.workspace_changed,
            tuple(facts.event_kinds),
        )

    def _peer_failures_identical(
        self,
        phase: str,
        agent: str | None,
        facts: AttemptFacts,
    ) -> bool:
        if not _attempt_observation_present(facts):
            return False
        chain = self._get_chain_agents(phase)
        if chain is None or len(chain) < _MIN_PEER_CHAIN_AGENTS:
            return False
        signature = self._failure_signature(facts)
        remembered = self._peer_failure_signatures.get(phase, {})
        return all(remembered.get(other) == signature for other in chain if other != agent)

    def _remember_verdict(self, phase: str, agent: str | None, verdict: PersistedVerdict) -> None:
        namespace = _dead_agent_verdict_namespace()
        record = cast(
            "Callable[[str, str, PersistedVerdict], dict[str, object]]", namespace["verdict_record"]
        )
        self._last_verdict_record = record(phase, agent or "", verdict)

    @property
    def last_verdict_record(self) -> dict[str, object] | None:
        return self._last_verdict_record

    def _is_dead_agent_candidate(
        self,
        signal_name: str | None,
        facts: AttemptFacts | None,
        *,
        attempt_ended: bool,
    ) -> bool:
        if signal_name is not None:
            return True
        if facts is None or facts.artifact_valid:
            return False
        return attempt_ended

    def _resolve_dead_agent_verdict(
        self,
        phase: str,
        agent: str | None,
        *,
        failure: ClassifiedFailure | None = None,
        facts: AttemptFacts | None = None,
        exc: BaseException | None = None,
        signal_name: str | None = None,
        signal_provider_originated: bool = False,
    ) -> PersistedVerdict | None:
        if signal_name is None:
            signal = facts.typed_signal if facts is not None else None
            if signal is None and failure is not None and failure.facts is not None:
                signal = failure.facts.typed_signal
            if signal is None and exc is not None:
                signal = attempt_facts_for_failure(exc).typed_signal
            if signal is not None:
                signal_name = signal.name
                signal_provider_originated = signal.provider_originated
        has_runner = (
            self._dead_agent_probe_runner is not None or self._dead_agent_verdict_runner is not None
        )
        if not has_runner and not (signal_name is not None and signal_provider_originated):
            return None
        effective_facts = facts
        if effective_facts is None and failure is not None:
            effective_facts = failure.facts
        attempt_ended = exc is not None or (
            effective_facts is not None and effective_facts.termination is not None
        )
        if not self._is_dead_agent_candidate(
            signal_name,
            effective_facts,
            attempt_ended=attempt_ended,
        ):
            return None
        if effective_facts is None and self._dead_agent_verdict_runner is None:
            effective_facts = AttemptFacts()
        typed_signal = build_typed_agent_signal(
            signal_name,
            signal_provider_originated,
            effective_facts,
        )
        attribution: AttributionVerdict | None = None
        if effective_facts is not None:
            attribution = attribute_attempt(
                effective_facts,
                typed_signal_observed=typed_signal is not None,
                typed_signal_provider_originated=bool(
                    typed_signal is not None and typed_signal.provider_originated
                ),
            )
        probe_outcome: ProbeOutcome | None = None
        if self._dead_agent_verdict_runner is None and effective_facts is not None:
            preliminary = build_dead_agent_verdict(
                effective_facts,
                attribution=attribution,
                probe_outcome=None,
                typed_signal=typed_signal,
            )
            if _verdict_benches_agent(preliminary) or not _verdict_counts_toward_agent_state(
                preliminary
            ):
                return preliminary
        if self._dead_agent_probe_runner is not None and agent is not None:
            probe_outcome = self._run_probe_fail_open(phase, agent)
        if self._dead_agent_verdict_runner is not None:
            return self._dead_agent_verdict_runner(
                facts=effective_facts,
                attribution=attribution,
                probe_outcome=probe_outcome,
                typed_signal=typed_signal,
            )
        if effective_facts is None:
            return None
        return build_dead_agent_verdict(
            effective_facts,
            attribution=attribution,
            probe_outcome=probe_outcome,
            typed_signal=typed_signal,
        )

    def _run_probe_fail_open(self, phase: str, agent: str) -> ProbeOutcome | None:
        runner = self._dead_agent_probe_runner
        if runner is None:
            return None
        try:
            return runner(phase, agent)
        except Exception as exc:
            logger.warning("dead-agent probe crashed phase={} agent={}: {}", phase, agent, exc)
            return None

    def record_confirmed_unavailability(
        self,
        phase: str,
        agent: str,
        reason: UnavailabilityReason,
        verdict: PersistedVerdict | None = None,
    ) -> int:
        logger.warning(
            "Confirmed agent unavailability: phase={} agent={} reason={} verdict={} "
            "facts={} attribution={} probe_outcome={} termination={} "
            "return_code_discrepancy={}",
            phase,
            agent,
            reason,
            verdict.verdict if verdict is not None else None,
            verdict.facts if verdict is not None else None,
            verdict.attribution if verdict is not None else None,
            verdict.probe_outcome if verdict is not None else None,
            verdict.termination if verdict is not None else None,
            verdict.return_code_discrepancy if verdict is not None else None,
        )
        return self._mark_agent_unavailable(phase, agent, reason)

    def record_confirmed_dead_agent(
        self,
        phase: str,
        agent: str,
        *,
        facts: AttemptFacts | None = None,
        exc: BaseException | None = None,
        signal_name: str | None = None,
        signal_provider_originated: bool = False,
    ) -> PersistedVerdict | None:
        attempt_facts = facts if facts is not None else AttemptFacts()
        typed_signal = (
            build_typed_agent_signal(
                signal_name,
                signal_provider_originated,
                attempt_facts,
            )
            if signal_name is not None
            else None
        )
        outcome = self.finalize_attempt(
            phase,
            agent,
            facts=attempt_facts,
            attempt_failed=True,
            typed_signal=typed_signal,
            exc=exc,
        )
        if outcome.verdict is not None:
            return outcome.verdict
        return self._last_resolved_verdict

    def _get_chain_agents(self, phase: str) -> tuple[str, ...] | None:
        chain_config = self._chain_config_for_phase(phase)
        if chain_config is not None:
            return tuple(chain_config.agents)
        return self._phase_chains.get(phase)

    def register_phase_chain(self, phase: str, agents: Sequence[str]) -> None:
        self._phase_chains[phase] = tuple(agents)

    def record_attempt_fact(
        self,
        phase: str,
        agent: str,
        *,
        artifact_valid: bool,
        workspace_changed: bool | None,
        free_retry_category: bool = False,
    ) -> str | None:
        fact = AttemptFact(
            phase=phase,
            agent=agent,
            artifact_valid=artifact_valid,
            workspace_changed=workspace_changed,
            free_retry_category=free_retry_category,
        )
        outcome = self._ledger.record(fact)

        if outcome.demoted:
            logger.warning(
                "Agent '{}' demoted in phase '{}' (consecutive={}, total={}, reason={})",
                agent,
                phase,
                outcome.consecutive_unproductive,
                outcome.total_unproductive,
                outcome.reason,
            )
            chain_agents = self._get_chain_agents(phase)
            if chain_agents is not None and len(chain_agents) == 1 and chain_agents[0] == agent:
                return (
                    f"Sole agent '{agent}' demoted in phase '{phase}': "
                    f"unproductive attempt limit reached ({outcome.reason})"
                )
            if outcome.status.is_spent:
                self.note_retry_exhaustion(phase, agent)
        return None

    def _handle_classified_failure(
        self,
        state: PipelineState,
        failure: ClassifiedFailure,
        context: FailureContext,
        *,
        bench_applied: bool = False,
        sole_demoted_reason: str | None = None,
    ) -> tuple[PipelineState, list[Effect], FailureEvent]:
        phase, agent = context.phase, context.agent
        retry_in_session = context.retry_in_session
        chain = state.chain_for_phase(phase)
        chain_capacity = 0
        retry_delay_ms = 0
        is_agent_unavailable = (failure.is_unavailable or bench_applied) and agent is not None

        if chain is not None:
            chain_capacity = max(0, len(chain.agents) - chain.current_index - 1)
            self._phase_chains[phase] = tuple(chain.agents)

            if agent is not None and failure.counts_against_budget and not is_agent_unavailable:
                retry_delay_ms = self._compute_retry_delay(phase, agent)

        failure_evt = FailureEvent(
            timestamp=self._clock.now_utc(),
            phase=phase,
            agent=agent,
            category=str(failure.category),
            reason=failure.reason,
            counted_against_budget=failure.counts_against_budget,
            chain_capacity_remaining=chain_capacity,
            recovery_cycle=state.recovery_cycle_count,
            retry_delay_ms=retry_delay_ms,
            watchdog_reason=failure.watchdog_reason,
            unavailability_reason=(
                str(failure.unavailability_reason) if failure.unavailability_reason else None
            ),
        )
        self._bus.publish(failure_evt)

        new_state = state.copy_with(
            last_failure_category=str(failure.category),
            last_retry_delay_ms=retry_delay_ms,
        ).clear_waiting()

        if sole_demoted_reason is not None:
            logger.warning(sole_demoted_reason)
            new_state = new_state.copy_with(recovery_cycle_count=new_state.recovery_cycle_count + 1)
            failed_state = self._enter_phase_failed(
                new_state, sole_demoted_reason, failure.category
            )
            if self._cap.is_exceeded(failed_state.recovery_cycle_count):
                exit_reason = self._cap.exit_reason(
                    failed_state.recovery_cycle_count,
                    str(failure.category),
                    failure.reason[:200],
                )
                logger.error("Recovery cycle cap exceeded: {}", exit_reason)
                return (
                    failed_state.copy_with(last_retry_delay_ms=0),
                    [_build_exit_failure_effect(reason=exit_reason)],
                    failure_evt,
                )
            return (
                failed_state,
                [],
                failure_evt,
            )

        if failure.category in (
            FailureCategory.ENVIRONMENTAL,
            FailureCategory.ARTIFACT_VALIDATION,
            FailureCategory.AMBIGUOUS,
        ):
            self._log_technical_failure(phase, failure)
            new_state = new_state.copy_with(last_error=failure.reason)
            new_state, effects, failure_evt = self._handle_retry_progression(
                new_state,
                failure,
                phase,
                agent,
                retry_in_session=retry_in_session,
                failure_evt=failure_evt,
            )
            return new_state, effects, failure_evt

        if failure.category == FailureCategory.USER_CONFIG:
            if failure.is_unavailable and agent is not None:
                if not bench_applied:
                    cooldown_ms = self._mark_agent_unavailable(
                        phase, agent, reason=failure.unavailability_reason
                    )
                    logger.info(
                        "{} unavailable: {}; cooldown {}ms active",
                        agent,
                        failure.unavailability_reason,
                        cooldown_ms,
                    )
                new_state = new_state.copy_with(
                    last_unavailability_reason=(
                        str(failure.unavailability_reason)
                        if failure.unavailability_reason is not None
                        else None
                    )
                )
            logger.error(
                "User/config failure reached runtime controller in phase={} (bug): {}",
                phase,
                failure.reason[:200],
            )
            return (
                self._enter_phase_failed(new_state, failure.reason, failure.category),
                [],
                failure_evt,
            )

        if failure.is_unavailable and agent is not None and not bench_applied:
            cooldown_ms = self._mark_agent_unavailable(
                phase, agent, reason=failure.unavailability_reason
            )
            logger.info(
                "{} unavailable: {}; cooldown {}ms active",
                agent,
                failure.unavailability_reason,
                cooldown_ms,
            )
            new_state = new_state.copy_with(
                last_unavailability_reason=(
                    str(failure.unavailability_reason) if failure.unavailability_reason else None
                )
            )

        if failure.reset_session:
            logger.warning(
                "Stale session detected in phase={} (session id invalid): {}",
                phase,
                failure.reason[:200],
            )
            new_state = new_state.copy_with(
                last_agent_session_id=None,
                agent_retry_intent=cleared_agent_retry_intent(),
            )
            self._write_session_reset_hint(phase, failure)
        elif retry_in_session and failure.resumable_session_id:
            new_state = new_state.copy_with(
                last_agent_session_id=failure.resumable_session_id,
            )

        if agent is not None and not is_agent_unavailable and failure.counts_against_budget:
            key = f"{phase}:{agent}"
            self._backoff_attempts[key] = self._backoff_attempts.get(key, 0) + 1

        new_state, effects, failure_evt = self._handle_retry_progression(
            new_state,
            failure,
            phase,
            agent,
            retry_in_session=retry_in_session,
            failure_evt=failure_evt,
        )

        if self._cap.is_exceeded(new_state.recovery_cycle_count):
            exit_reason = self._cap.exit_reason(
                new_state.recovery_cycle_count,
                str(failure.category),
                failure.reason[:200],
            )
            logger.error("Recovery cycle cap exceeded: {}", exit_reason)
            return (
                new_state.copy_with(last_retry_delay_ms=0),
                [_build_exit_failure_effect(reason=exit_reason)],
                failure_evt,
            )

        return new_state, effects, failure_evt

    def _log_technical_failure(self, phase: str, failure: ClassifiedFailure) -> None:
        if failure.category == FailureCategory.ENVIRONMENTAL:
            logger.info(
                "Environmental failure in phase={} (not counted against budget): {}",
                phase,
                failure.reason[:200],
            )
            return
        category_label = (
            "Artifact validation"
            if failure.category == FailureCategory.ARTIFACT_VALIDATION
            else "Ambiguous"
        )
        if failure.category == FailureCategory.ARTIFACT_VALIDATION:
            logger.error(
                "VALIDATION FAILURE in phase={} (retry without budget debit): {}",
                phase,
                failure.reason[:200],
            )
            return
        logger.info(
            "{} failure in phase={} (retry without budget debit): {}",
            category_label,
            phase,
            failure.reason[:200],
        )

    def _mark_agent_unavailable(
        self,
        phase: str,
        agent: str,
        reason: UnavailabilityReason | None = None,
    ) -> int:
        entry = self._unavailability_tracker.mark_unavailable(phase, agent, reason)
        multiplier: int = pow(2, entry.attempt)
        computed = entry.base_backoff_ms * multiplier
        return min(computed, entry.max_backoff_ms)

    def _now_ms(self) -> int:
        return int(self._clock.monotonic() * 1000)

    def _tracker_windows(self, *, now_ms: int) -> dict[str, tuple[int, str]]:
        snap = self._unavailability_tracker.snapshot()
        timeouts = snap.get("unavailable_timeouts", {})
        reasons = snap.get("unavailability_reasons", {})
        windows: dict[str, tuple[int, str]] = {}
        for agent, until in timeouts.items():
            if not isinstance(agent, str) or isinstance(until, bool) or not isinstance(until, int):
                continue
            if until <= now_ms:
                continue
            raw_reason = reasons.get(agent)
            reason = raw_reason if isinstance(raw_reason, str) else "unavailable"
            windows[agent] = (until, reason)
        return windows

    def _resolve_eligibility(
        self,
        phase: str,
        agent: str,
        windows: dict[str, tuple[int, str]],
        spent: set[str],
        *,
        now_ms: int,
    ) -> AgentStatus:
        window = windows.get(agent)
        return self._ledger.agent_eligibility(
            phase,
            agent,
            cooldown=window,
            spent_reason=_SPENT_RETRY_EXHAUSTED_REASON if agent in spent else None,
            unavailable_reason=(
                None
                if self._unavailability_tracker.is_available(phase, agent)
                else _STORE_BENCHED_REASON
            ),
        )

    def chain_eligibility(
        self,
        phase: str,
        agents: Sequence[str],
    ) -> tuple[tuple[str, AgentStatus], ...]:
        now_ms = self._now_ms()
        windows = self._tracker_windows(now_ms=now_ms)
        spent = self._spent_agents.get(phase, set())
        return tuple(
            (
                agent,
                self._resolve_eligibility(phase, agent, windows, spent, now_ms=now_ms),
            )
            for agent in agents
        )

    def agent_eligibility(self, phase: str, agent: str) -> AgentStatus:
        return self.chain_eligibility(phase, [agent])[0][1]

    def agents_for_phase(self, phase: str) -> tuple[str, ...]:
        chain = self._get_chain_agents(phase)
        return () if chain is None else chain

    def eligibility_wait(self, phase: str, agents: Sequence[str]) -> EligibilityWait:
        now_ms = self._now_ms()
        statuses = [status for _agent, status in self.chain_eligibility(phase, agents)]
        return eligibility_wait_from_statuses(statuses, now_ms=now_ms)

    def mark_agent_unavailable(self, phase: str, agent: str, *, reason: str) -> None:
        self._ledger.mark_unavailable(phase, agent, reason=reason)

    def _is_agent_spent(self, phase: str, agent: str) -> bool:
        return self.agent_eligibility(phase, agent).is_spent

    def _is_agent_available(self, phase: str, agent: str) -> bool:
        return self.agent_eligibility(phase, agent).is_eligible

    def _increment_chain_retries(self, state: PipelineState, phase: str) -> PipelineState:
        chain = state.chain_for_phase(phase)
        if chain is None:
            return state
        return state.with_phase_chain(phase, chain.with_retry_increment())

    def _apply_chain_retry(
        self,
        state: PipelineState,
        phase: str,
        chain: AgentChainState,
        *,
        retry_in_session: bool,
    ) -> PipelineState:
        retried_state = state.with_phase_chain(phase, chain.with_retry_increment())
        if retry_in_session and state.last_agent_session_id:
            return retried_state.copy_with(
                agent_retry_intent=resume_agent_retry_intent(
                    state.last_agent_session_id,
                ),
            )
        return retried_state.copy_with(agent_retry_intent=cleared_agent_retry_intent())

    def _chain_config_for_phase(self, phase: str) -> AgentChainConfig | None:
        if self._policy_bundle is None:
            return None
        phase_def = self._policy_bundle.pipeline.phases.get(phase)
        drain_name = phase_def.drain if phase_def is not None else phase
        drain_config = self._policy_bundle.agents.agent_drains.get(drain_name)
        if drain_config is None:
            return None
        return self._policy_bundle.agents.agent_chains.get(drain_config.chain)

    def _handle_integration_continuation(
        self,
        state: PipelineState,
        failure: ClassifiedFailure,
        phase: str,
    ) -> tuple[PipelineState, list[Effect], FailureEvent]:
        delay = self._integration_retry_delay(state, phase)
        attempt = state.rebase.integration_retry_attempt
        if compute_backoff_ms(delay, 1) > delay:
            attempt += 1
        event = FailureEvent(
            timestamp=self._clock.now_utc(),
            phase=phase,
            agent=None,
            category=str(failure.category),
            reason=failure.reason,
            counted_against_budget=False,
            chain_capacity_remaining=0,
            recovery_cycle=state.recovery_cycle_count,
            retry_delay_ms=delay,
        )
        self._bus.publish(event)
        return (
            state.copy_with(
                last_error=failure.reason,
                last_failure_category=str(failure.category),
                last_retry_delay_ms=delay,
                rebase=state.rebase.model_copy(update={"integration_retry_attempt": attempt}),
            ).clear_waiting(),
            [],
            event,
        )

    def _integration_retry_delay(self, state: PipelineState, phase: str) -> int:
        chain_config = self._chain_config_for_phase(phase)
        configured = chain_config.retry_delay_ms if chain_config is not None else 0
        base = configured or PhaseRetryPolicy().retry_delay_ms
        delay = compute_backoff_ms(base, 0)
        for _attempt in range(state.rebase.integration_retry_attempt):
            increased = compute_backoff_ms(delay, 1)
            if increased == delay:
                break
            delay = increased
        return delay

    def _compute_retry_delay(
        self,
        phase: str,
        agent: str | None,
    ) -> int:
        chain_config = self._chain_config_for_phase(phase)
        if chain_config is None:
            return 0

        key = f"{phase}:{agent}" if agent else phase
        attempt = self._backoff_attempts.get(key, 0)

        return compute_backoff_ms(chain_config.retry_delay_ms, attempt)

    def reset_backoff(self, phase: str, agent: str | None) -> None:
        key = f"{phase}:{agent}" if agent else phase
        self._backoff_attempts.pop(key, None)
        self._spent_agents.pop(phase, None)
        self._last_selection_state.pop(phase, None)
        if agent is not None:
            self._unavailability_tracker.reset_backoff(phase, agent)

    def note_retry_exhaustion(self, phase: str, agent: str) -> None:
        self._spent_agents.setdefault(phase, set()).add(agent)

    def classify_conflict_attempt(
        self,
        raw_failure: BaseException | str | None,
        *,
        agent: str,
        phase: str = RESOLUTION_DRAIN_NAME,
        connectivity_state: str | None = None,
        outcome: ConflictAttemptOutcome | None = None,
        unresolved_count: int = 0,
    ) -> ClassifiedFailure:
        effective = outcome if outcome is not None else ConflictAttemptOutcome.RAW
        if effective is not ConflictAttemptOutcome.RAW:
            reason = _typed_conflict_attempt_reason(effective, unresolved_count)
            logger.info(
                "conflict_resolution: typed attempt outcome '{}' for agent '{}' in phase '{}': {}",
                effective.value,
                agent,
                phase,
                reason,
            )
            unproductive = _UNPRODUCTIVE_CONFLICT_OUTCOMES.get(effective)
            return ClassifiedFailure(
                category=FailureCategory.AGENT,
                reason=reason,
                attributed_agent=agent,
                attributed_phase=phase,
                counts_against_budget=unproductive is not None,
                original_exception=raw_failure if isinstance(raw_failure, BaseException) else None,
                raw_message="" if raw_failure is None else str(raw_failure),
                is_unavailable=unproductive is not None,
                unavailability_reason=unproductive,
            )
        raw_text: BaseException | str = raw_failure if raw_failure is not None else ""
        return self._classifier.classify(
            raw_text, phase=phase, agent=agent, connectivity_state=connectivity_state
        )

    def next_conflict_candidate(self, candidates: Sequence[str], *, failed_index: int) -> int:
        if not candidates:
            return 0
        return min(failed_index + 1, len(candidates))

    def set_phase_launch_gate(self, phase: str, *, available: bool) -> None:
        self._phase_launch_gates[phase] = available

    def phase_launch_available(self, phase: str) -> bool:
        if not self._phase_launch_gates.get(phase, True):
            return False
        chain = self._phase_chains.get(phase)
        if not chain:
            return True
        return any(status.is_eligible for _agent, status in self.chain_eligibility(phase, chain))

    def preferred_agent_index(
        self,
        phase: str,
        agents: Sequence[str],
        *,
        current_index: int | None = None,
        exclude_current: bool = False,
    ) -> AgentSelection:
        self._phase_chains[phase] = tuple(agents)
        now_ms = self._now_ms()
        windows = self._tracker_windows(now_ms=now_ms)
        spent_set = self._spent_agents.get(phase, set())

        rows: list[tuple[str, bool, int, bool, str | None]] = []
        for idx, agent in enumerate(agents):
            status = self._resolve_eligibility(phase, agent, windows, spent_set, now_ms=now_ms)
            cooldown_ms = 0
            cooldown_reason: str | None = None
            if isinstance(status, Cooldown):
                cooldown_ms = max(0, status.until_ms - now_ms)
                cooldown_reason = status.reason

            avail = status.is_eligible and not (exclude_current and idx == current_index)
            is_spent = status.is_spent

            rows.append(
                agent_availability(
                    agent,
                    avail,
                    cooldown_ms,
                    is_spent,
                    cooldown_reason,
                )
            )

        selection = select_preferred_agent(rows)
        candidate_state = (
            selection.agent,
            tuple(
                (
                    agent,
                    avail,
                    cooldown_ms > 0,
                    is_spent,
                    cooldown_reason if cooldown_ms > 0 else None,
                )
                for agent, avail, cooldown_ms, is_spent, cooldown_reason in rows
            ),
        )
        if not self._phase_launch_gates.get(phase, True):
            self._last_selection_state.pop(phase, None)
            return selection
        if self._last_selection_state.get(phase) != candidate_state:
            self._last_selection_state[phase] = candidate_state
            logger.bind(recovery=True).info(format_selection_evidence(phase, selection))
        return selection

    def _write_session_reset_hint(
        self,
        phase: str,
        failure: ClassifiedFailure,
        *,
        backend: FileBackend | None = None,
    ) -> None:
        file_backend = backend or self._reset_hint_backend
        build_retry_hint, retry_hint_path = _get_required_artifact_helpers()

        detail = (
            "Previous session id was invalid; restart with fresh session."
            f" Original failure: {failure.raw_message}"
        )
        hint_content = build_retry_hint(phase, detail)
        hint_file = Path(
            retry_hint_path(
                phase,
                pipeline_policy=self._policy_bundle.pipeline
                if self._policy_bundle is not None
                else None,
            )
        )
        try:
            file_backend.mkdir(hint_file.parent, parents=True, exist_ok=True)
            write_text_if_changed(file_backend, hint_file, hint_content, encoding="utf-8")
        except OSError:
            logger.warning("Failed to write session reset hint to {}", hint_file)

    def _handle_retry_progression(
        self,
        state: PipelineState,
        failure: ClassifiedFailure,
        phase: str,
        agent: str | None,
        *,
        retry_in_session: bool,
        failure_evt: FailureEvent,
    ) -> tuple[PipelineState, list[Effect], FailureEvent]:
        chain = state.chain_for_phase(phase)
        if chain is None:
            return state, [], failure_evt

        current_agent = agent or (
            chain.agents[chain.current_index]
            if chain.agents and chain.current_index < len(chain.agents)
            else None
        )
        selection = self.preferred_agent_index(
            phase,
            chain.agents,
            current_index=chain.current_index,
        )

        if selection.index == chain.current_index:
            return (
                self._apply_chain_retry(state, phase, chain, retry_in_session=retry_in_session),
                [],
                failure_evt,
            )

        if selection.index is not None:
            next_available_index = selection.index
            next_agent = chain.agents[next_available_index]
            from_agent = current_agent or f"agent[{chain.current_index}]"
            fallover_record = _build_fallover_record(
                phase=phase,
                from_agent=from_agent,
                to_agent=next_agent,
                timestamp_iso=self._clock.now_utc().isoformat(),
            )
            fallover_evt = FalloverEvent(
                timestamp=self._clock.now_utc(),
                phase=phase,
                from_agent=from_agent,
                to_agent=next_agent,
                reason=failure.reason,
                watchdog_reason=failure.watchdog_reason,
                unavailability_reason=(
                    str(failure.unavailability_reason) if failure.unavailability_reason else None
                ),
            )
            self._bus.publish(fallover_evt)

            new_chain = AgentChainState(
                agents=chain.agents,
                current_index=next_available_index,
                retries=0,
            )
            preserve_session_id = failure.watchdog_reason == "no_output_at_start" and bool(
                failure.resumable_session_id
            )
            new_state = (
                state.with_phase_chain(phase, new_chain)
                .copy_with(
                    last_retry_delay_ms=0,
                    last_agent_session_id=(
                        failure.resumable_session_id if preserve_session_id else None
                    ),
                    agent_retry_intent=cleared_agent_retry_intent(),
                )
                .with_fallover_record(fallover_record)
            )
            return new_state, [], failure_evt

        if all(not self._is_agent_available(phase, agent) for agent in chain.agents):
            chain_wait = self.eligibility_wait(phase, chain.agents)
            if chain_wait.kind is WaitKind.COOLING_DOWN:
                wait_ms = chain_wait.wait_ms or 0
                unavail_reason = (
                    failure.unavailability_reason.value
                    if failure.unavailability_reason
                    else "unknown"
                )
                reason = (
                    f"all agents unavailable (last reason: {unavail_reason});"
                    " waiting for cooldown expiry"
                )
                logger.info(
                    "{} in phase={} (wait_ms={} unavailability_reason={})",
                    reason,
                    phase,
                    wait_ms,
                    failure.unavailability_reason,
                )
                updated_evt = replace(failure_evt, retry_delay_ms=wait_ms)
                self._bus.publish(updated_evt)
                return (
                    state.copy_with(
                        last_error=reason,
                        last_retry_delay_ms=wait_ms,
                    ).set_waiting(WaitReason.ALL_AGENTS_UNAVAILABLE, None),
                    [],
                    updated_evt,
                )

        if len(chain.agents) == 1 and not self._is_agent_available(phase, chain.agents[0]):
            sole_agent = chain.agents[0]
            st = self.agent_eligibility(phase, sole_agent)
            demote_reason = (
                st.reason
                if isinstance(st, (Cooldown, Spent, Unavailable)) and st.reason
                else failure.reason
            )
            exhaustion_reason = (
                f"Sole agent '{sole_agent}' demoted in phase '{phase}': "
                f"unproductive attempt limit reached ({demote_reason})"
            )
        elif all(not self._is_agent_available(phase, agent) for agent in chain.agents):
            exhaustion_reason = (
                f"All agents in phase '{phase}' demoted or exhausted: {failure.reason}"
            )
        else:
            exhaustion_reason = failure.reason

        new_state = state.copy_with(recovery_cycle_count=state.recovery_cycle_count + 1)
        failed_state = self._enter_phase_failed(new_state, exhaustion_reason, failure.category)
        if self._cap.is_exceeded(failed_state.recovery_cycle_count):
            exit_reason = self._cap.exit_reason(
                failed_state.recovery_cycle_count,
                str(failure.category),
                failure.reason[:200],
            )
            logger.error("Recovery cycle cap exceeded: {}", exit_reason)
            return (
                failed_state.copy_with(last_retry_delay_ms=0),
                [_build_exit_failure_effect(reason=exit_reason)],
                failure_evt,
            )
        return failed_state, [], failure_evt

    def earliest_eligible_wait_ms(self, phase: str, agents: Sequence[str]) -> int | None:
        if not agents:
            return None
        wait = self.eligibility_wait(phase, agents)
        if wait.kind is WaitKind.AVAILABLE_NOW:
            return 0
        if wait.kind is WaitKind.COOLING_DOWN:
            return wait.wait_ms
        return None

    def earliest_available_wait_ms(self, phase: str, agents: Sequence[str]) -> int:
        wait = self.earliest_eligible_wait_ms(phase, agents)
        return 0 if wait is None else wait

    def all_exhausted(self, phase: str, agents: Sequence[str]) -> bool:
        if not agents:
            return False
        return all(status.is_spent for _agent, status in self.chain_eligibility(phase, agents))

    def snapshot(self) -> dict[str, object]:
        tracker_snapshot = self._unavailability_tracker.snapshot()
        merged_attempts = dict(self._backoff_attempts)
        merged_attempts.update(cast("dict[str, int]", tracker_snapshot.get("backoff_attempts", {})))
        return {
            "cycle_cap": self._cap.cap,
            "backoff_attempts": merged_attempts,
            "agent_max_consecutive_unproductive": self._consecutive_unproductive_limit,
            "agent_max_total_unproductive": self._total_unproductive_limit,
            "ledger": self._ledger.snapshot(),
            "unavailable_timeouts": tracker_snapshot["unavailable_timeouts"],
            "unavailability_reasons": tracker_snapshot.get("unavailability_reasons", {}),
            "spent_agents": {k: sorted(v) for k, v in self._spent_agents.items()},
            "false_bench_feedback": self._false_bench_feedback.snapshot(),
        }

    @classmethod
    def from_snapshot(
        cls,
        snapshot: Mapping[str, object],
        *,
        policy_bundle: PolicyBundle | None = None,
        clock: Clock | None = None,
        classifier: FailureClassifier | None = None,
        event_bus: FailureEventBus | None = None,
    ) -> RecoveryController:
        options = RecoveryControllerOptions.from_snapshot(
            snapshot,
            policy_bundle=policy_bundle,
            clock=clock,
            classifier=classifier,
            event_bus=event_bus,
        )
        return cls(options=options)

    def waiting_state_payload(self, phase: str, agents: list[str]) -> list[tuple[str, int, int]]:
        snap = self._unavailability_tracker.snapshot()
        self._phase_chains[phase] = tuple(agents)
        attempts_dict = snap.get("backoff_attempts", {})
        now_ms = self._now_ms()
        windows = self._tracker_windows(now_ms=now_ms)
        spent_set = self._spent_agents.get(phase, set())
        result: list[tuple[str, int, int]] = []
        for agent in agents:
            attempt = attempts_dict.get(agent, 0)
            attempt_int = int(attempt) if isinstance(attempt, int) else 0
            status = self._resolve_eligibility(phase, agent, windows, spent_set, now_ms=now_ms)
            cooldown_ms = 0
            if isinstance(status, Cooldown):
                cooldown_ms = max(0, status.until_ms - now_ms)
            result.append((agent, attempt_int, cooldown_ms))
        return result

    def agents_now_available(self, phase: str, agents: list[str]) -> list[str]:
        return [
            agent for agent, status in self.chain_eligibility(phase, agents) if status.is_eligible
        ]

    def _enter_phase_failed(
        self,
        state: PipelineState,
        reason: str,
        category: object,
    ) -> PipelineState:
        self._spent_agents.pop(state.phase, None)
        if self._policy_bundle is not None:
            failed_route = self._policy_bundle.pipeline.recovery.failed_route
            return (
                progress.advance_phase(
                    state,
                    failed_route,
                    policy=self._policy_bundle.pipeline,
                )
                .copy_with(
                    last_error=reason,
                    recovery_epoch=state.recovery_epoch + 1,
                    last_failure_category=str(category),
                    last_retry_delay_ms=0,
                )
                .clear_waiting()
            )
        return state.copy_with(
            phase="failed_terminal",
            last_error=reason,
            recovery_epoch=state.recovery_epoch + 1,
            last_failure_category=str(category),
            last_retry_delay_ms=0,
        ).clear_waiting()
