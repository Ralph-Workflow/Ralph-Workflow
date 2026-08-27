"""RecoveryController: single owner of failure classification, budget, and fallover.

Never-Exit Invariant
--------------------

The recovery controller is the second half of the recovery contract
documented in ``ralph.agents.idle_watchdog``. The pipeline NEVER exits
because of agent unavailability. This is enforced by the
all-agents-unavailable wait branch (in
``_handle_retry_progression``) and priority agent selection in
``preferred_agent_index``:

  - **All-agents-unavailable wait branch**: when every agent in the
    chain is on cooldown, the controller returns
    ``state.copy_with(last_retry_delay_ms=<earliest cooldown>,
    is_waiting_state=True)`` and does NOT call
    ``_enter_phase_failed``. The run loop sleeps on
    ``last_retry_delay_ms`` and re-enters the same phase. The
    ``is_waiting_state`` flag is the structured contract the run
    loop keys off; ``last_error`` text is operator-readable context
    only and is not parsed by the run loop.

  - **Priority Agent Selection**: after any failure, agent selection
    evaluates the chain in priority order and picks the highest-priority
    agent that is available and not spent. Earlier agents whose cooldown
    has expired are preferred over lower-priority agents.

The pipeline has recovery states: exponential backoff to
the next available agent (``AgentUnavailabilityTracker.mark_unavailable``) and
retry with the same agent. The
all-agents-unavailable wait branch is a third observable effect
(``is_waiting_state=True``) but it is NOT a third state -- it is a
transient holding pattern that the run loop interprets as
"continue the same phase after the cooldown expires". The
controller never reaches ``failed_terminal`` via this path.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.timeout_clock import Clock, SystemClock
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.pipeline import progress
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_retry_intent import (
    cleared_agent_retry_intent,
    resume_agent_retry_intent,
)
from ralph.pipeline.effects import ExitFailureEffect
from ralph.pipeline.state import FalloverRecord
from ralph.recovery._broken_agent_same_shape_error import BrokenAgentSameShapeLimitError
from ralph.recovery._broken_agent_same_shape_tracker import (
    BrokenAgentFingerprint,
    BrokenAgentSameShapeTracker,
)
from ralph.recovery._same_shape_retry_tracker import (
    SameShapeRetryLoopError,
    SameShapeRetryTracker,
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
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.classifier import (
    ClassifiedFailure,
    FailureCategory,
    FailureClassifier,
    FailureContext,
)
from ralph.recovery.cycle_cap import CycleCap
from ralph.recovery.events import FailureEvent, FailureEventBus, FalloverEvent
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions

__all__ = ["RecoveryController", "RecoveryControllerOptions", "compute_backoff_ms"]


# ---------------------------------------------------------------------------
# Two-state invariant (locked at import time)
# ---------------------------------------------------------------------------
#
# There are two types of retries: exponential backoff to the next agent, OR
# retry with the same agent. There is NEVER a state where we skip an agent
# permanently. All agents are recoverable. We never exit the pipeline because
# of agent unavailability.
#
# This invariant is enforced at module import time by walking the
# ``RecoveryController`` class source via ``ast`` and asserting that the
# two recovery paths are still wired. The check uses ``if/raise RuntimeError``
# (NOT ``assert``) so the invariant survives ``python -O`` per AGENTS.md.
# A future PR that needs to introduce a new recovery state MUST update this
# check and the test in ``tests/recovery/test_two_state_invariant.py`` in the
# same commit.

_REQUIRED_TWO_STATE_METHODS: frozenset[str] = frozenset(
    {"_mark_agent_unavailable", "_apply_chain_retry"}
)


# Module-level cache slot for the self-source AST tree shared by the
# two import-time invariant functions below. Reading + parsing the
# controller's own source twice (once per invariant) is wasteful; the
# helper ``_controller_source_tree`` memoizes the tuple so both
# invariant calls share a single read+parse per process import.
# ``_reset_controller_source_tree_cache`` is the test seam: it drops
# the cached tuple so each test starts from a clean slate. A dict is
# used (rather than a bare ``Optional`` global) so the cache can be
# mutated without ``global`` statements (which ruff ``PLW0603``
# discourages).
_CONTROLLER_SOURCE_TREE_CACHE: dict[  # bounded-accumulator-ok: cleared
    str, tuple[str, ast.Module]
] = {}


def _controller_source_tree(
    *,
    parse_fn: Callable[[str], ast.Module],
    read_fn: Callable[[], str],
) -> tuple[str, ast.Module]:
    """Return ``(source, tree)`` for the controller module, cached.

    Reads ``read_fn()`` and parses it with ``parse_fn`` only on the
    first invocation (or after ``_reset_controller_source_tree_cache``);
    subsequent calls return the cached tuple. The two invariant
    functions call this with ``read_fn=lambda: Path(__file__).read_text(...)``
    and ``parse_fn=_parse_source`` so the source is read and parsed exactly
    ONCE per process import instead of twice.

    ``parse_fn`` and ``read_fn`` are injected (no ambient open/
    Path.read_text) so the helper stays deterministic and testable.
    ``SyntaxError`` is wrapped in ``RuntimeError`` so the existing
    import-time invariant contract is preserved verbatim.
    """
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
    """Thin wrapper around ``ast.parse`` to avoid mypy's overload warnings
    when passed as a ``Callable[[str], ast.Module]`` argument."""
    return ast.parse(source)


def _reset_controller_source_tree_cache() -> None:
    """Clear the cached ``(source, tree)`` tuple for tests.

    Production callers should not need to call this: the cache is
    correct for the whole process lifetime because the controller
    module is the same source the cache holds. Tests inject custom
    ``parse_fn``/``read_fn`` and need a fresh slot per case.
    """
    _CONTROLLER_SOURCE_TREE_CACHE.clear()


def _assert_two_state_invariant() -> None:
    """Verify the two-state recovery invariant is wired into ``RecoveryController``.

    The check reads the controller's source file from disk (not a compiled
    bytecode cache) so it always reflects the current source. The only
    methods required to be present on ``RecoveryController`` are the two
    that correspond to the two MAIN RULES:

      1. ``_mark_agent_unavailable`` -- exponential backoff to the next
         agent via ``AgentUnavailabilityTracker.mark_unavailable``.
      2. ``_apply_chain_retry`` -- same-agent retry via
         ``AgentChain.with_retry_increment``.

    Any future addition to this set MUST be paired with an update to the
    import-time invariant and the test in
    ``tests/recovery/test_two_state_invariant.py`` so the locked contract
    is always in sync with the production code.

    The read+parse is shared with ``_assert_never_exit_invariant`` via
    ``_controller_source_tree`` so the controller source is read and
    parsed exactly ONCE per process import (wt-024 P1 perf fix).
    """
    _source, tree = _controller_source_tree(
        parse_fn=_parse_source,
        # filesystem-read-ok: import-time self-inspection is cached once by _controller_source_tree.
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


# ---------------------------------------------------------------------------
# Never-exit invariant (locked at import time)
# ---------------------------------------------------------------------------
#
# The pipeline NEVER exits because of agent unavailability. This is
# enforced by the all-agents-unavailable wait branch in
# ``_handle_retry_progression``: the branch sets
# ``state.is_waiting_state=True`` and ``state.last_retry_delay_ms``
# and returns BEFORE the ``_enter_phase_failed`` call is reachable.
# The run loop sleeps on ``last_retry_delay_ms`` and re-enters the
# same phase. The pipeline never reaches ``failed_terminal`` via
# this path.
#
# This invariant is enforced at module import time by walking the
# ``RecoveryController._handle_retry_progression`` function source
# via ``ast`` and asserting that:
#   1. The all-agents-unavailable ``if`` statement (the
#      ``if all(not self._is_agent_available(phase, agent) for
#      agent in chain.agents):`` block) appears at a LOWER body
#      index (i.e. earlier in the function body) than the
#      ``_enter_phase_failed`` call. The all-agents-unavailable
#      branch MUST be reached first so the return statement
#      inside it short-circuits before the failure call.
#   2. The all-agents-unavailable branch itself contains at least
#      one ``ast.Return`` node (the existing source has
#      ``return state.copy_with(...), [], updated_evt`` inside
#      the branch).
#
# The check uses ``if/raise RuntimeError`` (NOT ``assert``) so the
# invariant survives ``python -O`` per AGENTS.md 'Non-negotiables'.
# A future PR that introduces a third state that exits the
# pipeline when all agents are on cooldown will fail at import
# time with a ``RuntimeError`` naming both invariants and pointing
# to ``tests/recovery/test_two_state_invariant.py`` for the
# test-level pin.


def _find_all_agents_unavailable_if(func_body: list[ast.stmt]) -> ast.If | None:
    """Find the all-agents-unavailable ``if`` statement in a function body.

    The branch is identified by unparsing its test and checking for
    the substrings ``self._is_agent_available`` and ``all(`` -- the
    canonical source form of the wait branch's test is
    ``all((not self._is_agent_available(phase, agent) for agent in
    chain.agents))`` (the outer parens wrap the generator
    expression). The function returns the FIRST such ``ast.If`` in
    the body (lowest body index, i.e. the one closest to the top of
    the function).
    """
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
    """Find the STATEMENT containing the ``_enter_phase_failed`` call.

    Returns the top-level statement in ``func_body`` that contains
    an ``_enter_phase_failed`` call (typically an ``ast.Assign``
    like ``failed_state = self._enter_phase_failed(...)``). The
    check asserts the all-agents-unavailable branch is at a LOWER
    body index than this statement so the branch's return
    short-circuits before the failure call.

    Returns the LAST such statement in source order (the deepest
    call site in the function body).
    """
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
    """Verify the never-exit invariant is wired into ``_handle_retry_progression``.

    Asserts the all-agents-unavailable ``if`` statement in
    ``_handle_retry_progression`` appears at a LOWER body index
    than the ``_enter_phase_failed`` call AND the branch itself
    contains a ``Return`` statement. A future PR that introduces
    a third state that exits the pipeline when all agents are on
    cooldown will fail this check at module import time.

    The read+parse is shared with ``_assert_two_state_invariant`` via
    ``_controller_source_tree`` so the controller source is read and
    parsed exactly ONCE per process import (wt-024 P1 perf fix).
    """
    _source, tree = _controller_source_tree(
        parse_fn=_parse_source,
        # filesystem-read-ok: import-time self-inspection is cached once by _controller_source_tree.
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
    from collections.abc import Callable, Sequence

    from ralph.pipeline.effects import Effect
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import AgentChainConfig
    from ralph.recovery.unavailability_reason import UnavailabilityReason


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


def _get_required_artifact_helpers() -> tuple[Callable[[str, str], str], Callable[[str], str]]:
    # Lazy import to avoid circular dependency via ralph.phases import chain
    module = import_module("ralph.phases.required_artifacts")
    namespace = cast("dict[str, object]", module.__dict__)
    build_retry_hint = cast(
        "Callable[[str, str], str]", namespace["build_retry_hint"]
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    retry_hint_path = cast(
        "Callable[[str], str]", namespace["retry_hint_path"]
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    return build_retry_hint, retry_hint_path


def compute_backoff_ms(base_ms: int, attempt: int, max_ms: int = 30_000) -> int:
    """Compute exponential backoff delay with cap.

    Args:
        base_ms: Base delay in milliseconds.
        attempt: Current retry attempt (0-indexed).
        max_ms: Maximum delay cap in milliseconds.

    Returns:
        Delay in milliseconds, capped at max_ms.
    """
    exponent_factor: int = 2**attempt
    delay: int = base_ms * exponent_factor
    return min(delay, max_ms)


class RecoveryController:
    """Single conceptual owner of recovery logic.

    Handles classification, budget debiting, chain fallover, and cycle cap.
    Delegates nothing to the reducer's internal retry counter when active.
    """

    def __init__(
        self,
        *,
        options: RecoveryControllerOptions | None = None,
    ) -> None:
        opts = options or RecoveryControllerOptions()
        self._cap = CycleCap(cap=opts.cycle_cap)
        self._classifier = opts.classifier or FailureClassifier()
        self._bus = opts.event_bus or FailureEventBus()
        self._registry = opts.budget_registry or AgentBudgetRegistry()
        self._policy_bundle = opts.policy_bundle
        self._technical_retry_cap = max(0, opts.technical_retry_cap)
        self._clock: Clock = opts.clock or SystemClock()
        self._backoff_attempts: dict[str, int] = opts.backoff_attempts or {}
        # R6: same-shape retry loop bound. The tracker is constructed
        # eagerly here so the limit is validated at controller
        # construction time (the tracker's own ``__post_init__`` rejects
        # limits < 1) instead of at the first fire. The per-fire
        # ``prior_fingerprint`` / ``prior_consecutive`` arguments are
        # keyed by ``failure.watchdog_reason`` so a multi-phase run
        # tracks the bound per-watchdog-reason: a no_output_at_start
        # loop in development and a no_progress_quiet loop in commit
        # are independent shapes.
        self._same_shape_tracker = SameShapeRetryTracker(limit=opts.same_shape_retry_limit)
        self._broken_agent_same_shape_tracker = BrokenAgentSameShapeTracker(
            limit=opts.broken_agent_same_shape_limit
        )
        # The per-(phase, watchdog_reason) tracker state: each entry
        # stores the prior fingerprint four-tuple, the consecutive
        # count, and a progress snapshot (total_retries,
        # total_agent_calls) the controller uses to compute the
        # ``no_new_artifact_since_prior`` and
        # ``workspace_change_since_prior`` booleans for the next fire.
        # The booleans are derived (not stored) so the next call only
        # needs the prior metrics to compare against.
        self._same_shape_state: dict[
            str, tuple[tuple[str, str, bool, bool], int, tuple[int, int]]
        ] = {}  # bounded-accumulator-ok: bounded by phase x fire_reason; lives only for the controller instance
        self._broken_agent_same_shape_state: dict[
            str, tuple[BrokenAgentFingerprint, int]
        ] = {}  # bounded-accumulator-ok: bounded by phase; lives only for the controller instance
        self._spent_agents: dict[str, set[str]] = (
            {}
        )  # bounded-accumulator-ok: keyed by phase, drained by reset_backoff
        if opts.unavailability_store is not None:
            self._unavailability_tracker: UnavailabilityStore = opts.unavailability_store
        else:
            self._unavailability_tracker = AgentUnavailabilityTracker(
                clock=self._clock,
                backoff_policy=opts.unavailability_backoff_policy,
                initial_timeouts=opts.unavailable_timeouts,
                initial_entries=opts.unavailability_entries,
            )

    @property
    def event_bus(self) -> FailureEventBus:
        return self._bus

    @property
    def unavailability_store(self) -> UnavailabilityStore:
        """Public access to the unavailability store (Protocol-typed).

        Callers MUST consume the store through this property, not through
        the private ``_unavailability_tracker`` attribute. The Protocol is
        the seam for a future persistent (sqlite, redis, file)
        implementation; the in-memory ``AgentUnavailabilityTracker`` is the
        default when ``RecoveryControllerOptions.unavailability_store`` is
        not provided.
        """
        return self._unavailability_tracker

    @property
    def budget_registry(self) -> AgentBudgetRegistry:
        return self._registry

    def handle(
        self,
        state: PipelineState,
        raw_failure: BaseException | str,
        context: FailureContext,
    ) -> tuple[PipelineState, list[Effect], FailureEvent]:
        """Classify a failure and compute the recovery transition.

        Args:
            state: Current pipeline state.
            raw_failure: The raw exception or string error message.
            context: Phase/agent context and optional pre-classified failure.

        Returns:
            Tuple of (new_state, effects, failure_event).
        """
        phase = context.phase
        agent = context.agent
        retry_in_session = context.retry_in_session
        failure = context.classified_failure or self._classifier.classify(
            raw_failure,
            phase=phase,
            agent=agent,
            connectivity_state=state.last_connectivity_state,
        )

        chain = state.chain_for_phase(phase)
        chain_capacity = 0
        retry_delay_ms = 0
        is_agent_unavailable = failure.is_unavailable and agent is not None

        if chain is not None:
            chain_capacity = max(0, len(chain.agents) - chain.current_index - 1)

            # Compute retry delay from chain config only when the failing
            # agent is currently available. An unavailable agent is skipped
            # (not retried), so no retry delay is charged for it.
            if agent is not None and failure.counts_against_budget and not is_agent_unavailable:
                retry_delay_ms = self._compute_retry_delay(phase, agent)

        failure_evt = FailureEvent(
            timestamp=datetime.now(UTC),
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

        # ALWAYS set last_failure_category and last_retry_delay_ms on state first.
        # Also clear the structured wait-state flag: the wait state is a
        # per-handle-call outcome, so each new failure classification starts
        # from a clean slate. The wait branch (further down) re-asserts
        # ``is_waiting_state=True`` when it actually enters the wait state.
        new_state = state.copy_with(
            last_failure_category=str(failure.category),
            last_retry_delay_ms=retry_delay_ms,
            is_waiting_state=False,
        )

        if failure.category in (
            FailureCategory.ENVIRONMENTAL,
            FailureCategory.ARTIFACT_VALIDATION,
            FailureCategory.AMBIGUOUS,
        ):
            self._log_technical_failure(phase, failure)
            new_state = new_state.copy_with(last_error=failure.reason)
            new_state, effects, failure_evt = self._handle_technical_retry_exhaustion(
                new_state,
                failure,
                phase,
                agent,
                retry_in_session=retry_in_session,
                failure_evt=failure_evt,
            )
            return new_state, effects, failure_evt

        if failure.category == FailureCategory.USER_CONFIG:
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

        # AGENT category: debit budget and handle chain progression
        if failure.is_unavailable and agent is not None:
            self._mark_agent_unavailable(phase, agent, reason=failure.unavailability_reason)
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
            # Populate ``last_agent_session_id`` from the watchdog's
            # captured session id so the downstream ``_apply_chain_retry``
            # consumer (which already does
            # ``resume_agent_retry_intent(state.last_agent_session_id)``)
            # emits a resume intent with the captured id instead of
            # starting a fresh session. The branch is mutually exclusive
            # with ``failure.reset_session`` above (a stale-session reset
            # means the captured id is irrelevant and the chain retry
            # uses ``cleared_agent_retry_intent()`` -- the pre-fix
            # behavior). When ``retry_in_session`` is False the resume
            # path is not taken anyway, so this branch must not populate
            # ``last_agent_session_id`` for an unrelated retry.
            new_state = new_state.copy_with(
                last_agent_session_id=failure.resumable_session_id,
            )

        if agent is not None and not is_agent_unavailable:
            self._registry = self._registry.debit(phase, agent, failure)
            # Track backoff attempt for retry-delay growth on non-unavailable
            # agent failures. Unavailable attempts are tracked inside
            # _mark_agent_unavailable because they use a separate cooldown.
            if failure.counts_against_budget:
                key = f"{phase}:{agent}"
                self._backoff_attempts[key] = self._backoff_attempts.get(key, 0) + 1

        # R6: same-shape retry loop bound. The check fires only for
        # watchdog-driven kills on the AGENT path (``watchdog_reason``
        # populated and ``category == AGENT``) AND when the controller
        # is about to RESUME the prior session (``retry_in_session``
        # is True). Non-watchdog failures do not loop on the same
        # shape and are unaffected. The wait-state branch
        # (all-agents-unavailable) is excluded because no resume is
        # happening there -- the controller is sleeping on the
        # earliest cooldown, not retrying the same session. A test
        # that loops ``handle()`` 30 times against a chain where every
        # agent is on cooldown would otherwise fire the bound even
        # though the controller never resumes; the never-crash
        # invariant is the contract being tested there. The check
        # runs BEFORE the retry/resume decision so a bound loop on
        # the resume path raises ``SameShapeRetryLoopError``
        # instead of resuming. The exception is converted into a
        # terminal failure here so the pipeline emits an exit-failure
        # effect with the fingerprint evidence rather than silently
        # discarding the loop.
        broken_agent_bound_exception = self._check_broken_agent_same_shape_bound(
            phase, agent, failure, chain
        )
        if broken_agent_bound_exception is not None:
            logger.error(
                "broken-agent same-shape bound exceeded in phase={} agent={}: {}",
                phase,
                agent,
                broken_agent_bound_exception,
            )
            return (
                self._enter_phase_failed(
                    new_state,
                    str(broken_agent_bound_exception),
                    failure.category,
                ),
                [],
                failure_evt,
            )

        if retry_in_session:
            bound_exception = self._check_same_shape_bound(phase, failure, state)
            if bound_exception is not None:
                logger.error(
                    "same-shape retry loop exceeded in phase={} agent={} reason={}: {}",
                    phase,
                    agent,
                    failure.watchdog_reason,
                    bound_exception,
                )
                return (
                    self._enter_phase_failed(
                        new_state,
                        str(bound_exception),
                        failure.category,
                    ),
                    [],
                    failure_evt,
                )

        new_state, effects, failure_evt = self._handle_agent_budget_exhaustion(
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
            # Cycle exceeded: no retry delay
            return (
                new_state.copy_with(last_retry_delay_ms=0),
                [_build_exit_failure_effect(reason=exit_reason)],
                failure_evt,
            )

        return new_state, effects, failure_evt

    def _log_technical_failure(self, phase: str, failure: ClassifiedFailure) -> None:
        """Log a no-budget-debit failure before routing it to technical retry."""
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
        """Mark an agent as unavailable until the computed backoff expires.

        Uses exponential backoff based on the per-reason policy, capped at
        the reason's max_backoff_ms. Returns the computed backoff in ms
        (also capped at max_backoff_ms so callers that consume the return
        value get the same value the store recorded).
        """
        entry = self._unavailability_tracker.mark_unavailable(phase, agent, reason)
        multiplier: int = pow(2, entry.attempt)
        computed = entry.base_backoff_ms * multiplier
        return min(computed, entry.max_backoff_ms)

    def _is_agent_available(self, phase: str, agent: str) -> bool:
        """Return True when the agent is not currently marked unavailable."""
        return self._unavailability_tracker.is_available(phase, agent)

    def _check_broken_agent_same_shape_bound(
        self,
        phase: str,
        agent: str | None,
        failure: ClassifiedFailure,
        chain: AgentChainState | None,
    ) -> BrokenAgentSameShapeLimitError | None:
        """Fail a sole-agent chain after repeated identical broken-agent failures."""
        if agent is None or chain is None or len(chain.agents) != 1:
            return None
        exception = failure.original_exception
        if type(exception).__name__ != "BrokenAgentExitError":
            return None
        broken_reason = cast(
            "object", getattr(exception, "reason", None)
        )  # cast-policy: seam: structural boundary (typed exception attribute)
        if not isinstance(broken_reason, str):
            return None
        prior = self._broken_agent_same_shape_state.get(phase)
        prior_fingerprint = prior[0] if prior is not None else None
        prior_consecutive = prior[1] if prior is not None else 0
        try:
            fingerprint, consecutive = self._broken_agent_same_shape_tracker.record_failure(
                broken_agent_reason=broken_reason,
                agent_name=agent,
                prior_fingerprint=prior_fingerprint,
                prior_consecutive=prior_consecutive,
            )
        except BrokenAgentSameShapeLimitError as exc:
            logger.warning(
                "broken-agent same-shape bound reached: phase={} agent={} consecutive={} limit={}",
                phase,
                agent,
                exc.consecutive,
                exc.limit,
            )
            return exc
        self._broken_agent_same_shape_state[phase] = (fingerprint, consecutive)
        return None

    def _check_same_shape_bound(
        self,
        phase: str,
        failure: ClassifiedFailure,
        state: PipelineState,
    ) -> SameShapeRetryLoopError | None:
        """Return a ``SameShapeRetryLoopError`` when the bound fires, else None.

        The check is the R6 contract. It fires ONLY for watchdog-driven
        kills on the AGENT path (``failure.watchdog_reason`` populated);
        a non-watchdog failure (network, artifact validation, user config)
        does not loop on the same shape and is unaffected. The check
        also updates ``self._same_shape_state`` with the new
        fingerprint and progress snapshot so the NEXT fire compares
        against the right prior.

        Progress signals used for the fingerprint booleans:

          - ``no_new_artifact_since_prior`` -- True when no artifact
            landed between the prior fire and the current one. The
            controller does not have a direct artifact-creation
            counter in state; the conservative default is True
            (assume no artifact landed) when prior state is missing.
            The fingerprint still detects a loop on ``watchdog_reason``
            + ``failure.reason`` regardless of artifact changes.
          - ``workspace_change_since_prior`` -- True when the metrics
            counters (``total_retries``, ``total_agent_calls``) did
            NOT advance between the prior fire and the current one.
            ``total_retries`` increments on every watchdog-driven
            retry attempt and ``total_agent_calls`` increments on
            every agent invocation, so a flat (or absent) prior
            snapshot is the conservative default (True: no progress).

        Both booleans are pessimistic defaults: when prior state is
        absent (first fire) or progress cannot be determined (the
        metrics counters are unavailable), we assume no progress so
        the bound still catches a genuine loop. A run that DID make
        progress between fires will record the advanced metrics in
        ``_same_shape_state`` and the NEXT call will see False.
        """
        if failure.watchdog_reason is None:
            return None
        if failure.category != FailureCategory.AGENT:
            return None

        key = f"{phase}:{failure.watchdog_reason}"
        prior_state = self._same_shape_state.get(key)
        prior_fingerprint = prior_state[0] if prior_state is not None else None
        prior_consecutive = prior_state[1] if prior_state is not None else 0

        current_metrics: tuple[int, int] = (
            int(state.metrics.total_retries),
            int(state.metrics.total_agent_calls),
        )
        prior_metrics = prior_state[2] if prior_state is not None else current_metrics

        no_new_artifact: bool = current_metrics == prior_metrics
        workspace_change: bool = not no_new_artifact

        try:
            new_fingerprint, new_consecutive = self._same_shape_tracker.record_fire(
                fire_reason=str(failure.watchdog_reason),
                diagnostic_signature=str(failure.reason),
                no_new_artifact_since_prior=no_new_artifact,
                workspace_change_since_prior=not workspace_change,
                prior_fingerprint=prior_fingerprint,
                prior_consecutive=prior_consecutive,
            )
        except SameShapeRetryLoopError as exc:
            return exc

        self._same_shape_state[key] = (new_fingerprint, new_consecutive, current_metrics)
        return None

    def _increment_chain_retries(self, state: PipelineState, phase: str) -> PipelineState:
        """Increment chain.retries for the given phase without debiting the budget."""
        chain = state.chain_for_phase(phase)
        if chain is None:
            return state
        return state.with_phase_chain(phase, chain.with_retry_increment())

    def _handle_technical_retry_exhaustion(
        self,
        state: PipelineState,
        failure: ClassifiedFailure,
        phase: str,
        agent: str | None,
        *,
        retry_in_session: bool = False,
        failure_evt: FailureEvent,
    ) -> tuple[PipelineState, list[Effect], FailureEvent]:
        return self._handle_retry_progression(
            state,
            failure,
            phase,
            agent,
            retry_in_session=retry_in_session,
            max_retries=self._technical_retry_cap,
            use_budget=False,
            failure_evt=failure_evt,
        )

    def _apply_chain_retry(
        self,
        state: PipelineState,
        phase: str,
        chain: AgentChainState,
        *,
        retry_in_session: bool,
    ) -> PipelineState:
        """Apply a single retry to the chain and optionally preserve the agent session."""
        retried_state = state.with_phase_chain(phase, chain.with_retry_increment())
        if retry_in_session and state.last_agent_session_id:
            return retried_state.copy_with(
                agent_retry_intent=resume_agent_retry_intent(
                    state.last_agent_session_id,
                ),
            )
        return retried_state.copy_with(agent_retry_intent=cleared_agent_retry_intent())

    def _chain_config_for_phase(self, phase: str) -> AgentChainConfig | None:
        """Resolve the AgentChainConfig backing the given phase, or None."""
        if self._policy_bundle is None:
            return None
        phase_def = self._policy_bundle.pipeline.phases.get(phase)
        drain_name = phase_def.drain if phase_def is not None else phase
        drain_config = self._policy_bundle.agents.agent_drains.get(drain_name)
        if drain_config is None:
            return None
        return self._policy_bundle.agents.agent_chains.get(drain_config.chain)

    def _compute_retry_delay(
        self,
        phase: str,
        agent: str | None,
    ) -> int:
        """Compute the retry delay for a given phase and agent.

        Uses the chain's retry_delay_ms from policy configuration.
        """
        chain_config = self._chain_config_for_phase(phase)
        if chain_config is None:
            return 0

        # Get backoff attempt count for this phase:agent
        key = f"{phase}:{agent}" if agent else phase
        attempt = self._backoff_attempts.get(key, 0)

        return compute_backoff_ms(chain_config.retry_delay_ms, attempt)

    def reset_backoff(self, phase: str, agent: str | None) -> None:
        """Reset backoff counter for a phase/agent after successful invocation."""
        key = f"{phase}:{agent}" if agent else phase
        self._backoff_attempts.pop(key, None)
        self._spent_agents.pop(phase, None)
        if agent is not None:
            self._unavailability_tracker.reset_backoff(phase, agent)

    def note_retry_exhaustion(self, phase: str, agent: str) -> None:
        """Mark an agent's retry allowance as spent for the current round of a phase."""
        self._spent_agents.setdefault(phase, set()).add(agent)

    def classify_conflict_attempt(
        self,
        raw_failure: BaseException | str,
        *,
        agent: str,
        phase: str = "rebase_conflict_resolution",
    ) -> ClassifiedFailure:
        """Classify a failed conflict-resolution invoke without pipeline state."""
        return self._classifier.classify(raw_failure, phase=phase, agent=agent)

    def next_conflict_candidate(
        self, candidates: Sequence[str], *, failed_index: int
    ) -> int:
        """Advance past a failed candidate; never restart at the head of the chain."""
        if not candidates:
            return 0
        return min(failed_index + 1, len(candidates))

    def preferred_agent_index(
        self,
        phase: str,
        agents: Sequence[str],
        *,
        current_index: int | None = None,
        current_allowance_spent: bool = False,
    ) -> AgentSelection:
        """Select the highest-priority selectable agent for a phase.

        Builds AgentAvailability rows for all agents in phase, evaluates priority selection,
        and logs the selection decision to the transcript.
        """
        snap = self._unavailability_tracker.snapshot()
        cooldowns_dict = snap.get("unavailable_timeouts", {})
        now_ms = int(self._clock.monotonic() * 1000)
        spent_set = self._spent_agents.get(phase, set())

        rows: list[tuple[str, bool, int, bool]] = []
        for idx, agent in enumerate(agents):
            key = f"{phase}:{agent}"
            timeout_ms = cooldowns_dict.get(key)
            cooldown_ms = 0
            if isinstance(timeout_ms, int):
                cooldown_ms = max(0, timeout_ms - now_ms)

            avail = self._is_agent_available(phase, agent)
            is_spent = (
                agent in spent_set
                or (idx == current_index and current_allowance_spent)
            )

            rows.append(
                agent_availability(
                    agent,
                    avail,
                    cooldown_ms,
                    is_spent,
                )
            )

        selection = select_preferred_agent(rows)
        logger.bind(recovery=True).info(format_selection_evidence(phase, selection))
        return selection

    def _write_session_reset_hint(
        self,
        phase: str,
        failure: ClassifiedFailure,
        *,
        backend: FileBackend = DEFAULT_FILE_BACKEND,
    ) -> None:
        """Write a retry hint file describing the stale-session failure.

        Args:
            phase: Pipeline phase where the failure occurred.
            failure: Classified failure with stale-session detail.
        """
        build_retry_hint, retry_hint_path = _get_required_artifact_helpers()

        detail = (
            "Previous session id was invalid; restart with fresh session."
            f" Original failure: {failure.raw_message}"
        )
        hint_content = build_retry_hint(phase, detail)
        hint_file = Path(retry_hint_path(phase))
        try:
            backend.mkdir(hint_file.parent, parents=True, exist_ok=True)
            write_text_if_changed(backend, hint_file, hint_content, encoding="utf-8")
        except OSError:
            logger.warning("Failed to write session reset hint to {}", hint_file)

    def _handle_agent_budget_exhaustion(
        self,
        state: PipelineState,
        failure: ClassifiedFailure,
        phase: str,
        agent: str | None,
        *,
        retry_in_session: bool = False,
        failure_evt: FailureEvent,
    ) -> tuple[PipelineState, list[Effect], FailureEvent]:
        """Handle agent failure with budget debit and chain progression."""
        return self._handle_retry_progression(
            state,
            failure,
            phase,
            agent,
            retry_in_session=retry_in_session,
            max_retries=self._get_max_retries_for_chain(phase),
            use_budget=True,
            failure_evt=failure_evt,
        )

    def _handle_retry_progression(
        self,
        state: PipelineState,
        failure: ClassifiedFailure,
        phase: str,
        agent: str | None,
        *,
        retry_in_session: bool,
        max_retries: int,
        use_budget: bool,
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
        budget_state = (
            self._registry.get(phase, current_agent)
            if use_budget and current_agent is not None
            else None
        )
        current_allowance_spent = (
            (budget_state is not None and budget_state.exhausted)
            or (budget_state is None and chain.retries >= max_retries)
        )
        if current_allowance_spent and current_agent is not None:
            self.note_retry_exhaustion(phase, current_agent)

        selection = self.preferred_agent_index(
            phase,
            chain.agents,
            current_index=chain.current_index,
            current_allowance_spent=current_allowance_spent,
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
                timestamp_iso=datetime.now(UTC).isoformat(),
            )
            fallover_evt = FalloverEvent.now(
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
            # Defense-in-depth: a resumable watchdog kill carries a
            # captured session id, and the PROMPT requires the killed
            # session to be resumed in place rather than starting a
            # fresh session. The classifier now routes resumable
            # ``no_output_at_start`` kills to ``is_unavailable=False``
            # so the same-agent retry path is taken in the common
            # case; the fallover branch here also preserves the
            # captured session id so a defensive miss (e.g. a chain
            # of length 1 that always falls over) does not silently
            # drop the captured id and start a fresh session.
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

        # If every agent in the chain is temporarily unavailable, preserve the
        # session and wait until the earliest cooldown expires instead of
        # terminating the run. The run loop will sleep on last_retry_delay_ms
        # and then retry the same phase. Otherwise the chain is truly exhausted
        # (available agents have no budget/retries left), so fail the phase.
        if all(not self._is_agent_available(phase, agent) for agent in chain.agents):
            wait_ms = self._earliest_unavailable_wait_ms(phase, chain)
            unavail_reason = (
                failure.unavailability_reason.value if failure.unavailability_reason else "unknown"
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
                    # Structured wait-state flag: the run loop keys off this
                    # boolean to detect the wait state. ``last_error`` text
                    # is operator-readable context only and is NOT a contract
                    # the run loop parses. The flag is the single source of
                    # truth; setting it here ensures the WAITING / RESUMED
                    # structured log path is taken on the next loop
                    # iteration.
                    is_waiting_state=True,
                ),
                [],
                updated_evt,
            )

        new_state = state.copy_with(recovery_cycle_count=state.recovery_cycle_count + 1)
        failed_state = self._enter_phase_failed(new_state, failure.reason, failure.category)
        # Enforce the cap HERE, at the single funnel where a phase gives up and
        # the cycle counter advances, not only on the AGENT path in ``handle``.
        # The technical categories (ENVIRONMENTAL / ARTIFACT_VALIDATION /
        # AMBIGUOUS) return to their caller before reaching that check, so the
        # count they increment was never read. ``_enter_phase_failed`` routes to
        # ``failed_route``, whose recovery hop re-enters ``previous_phase`` and
        # resets the chain's retry counter, so nothing else bounded them: the
        # pipeline ping-ponged between the two forever. A ``TypeError`` in the
        # commit effect did exactly that -- an unclassifiable failure roughly
        # ten times a second, unbounded. ``CycleCap`` documents itself as the
        # guard against "a persistently-failing handler looping silently
        # forever", and this is the funnel where it has to fire to mean it.
        #
        # The phase still advances to ``failed_route`` first: the exit effect
        # reports why the run is stopping, and the state must describe where it
        # stopped. This mirrors the AGENT-path check, which likewise runs after
        # the phase has been failed.
        if self._cap.is_exceeded(failed_state.recovery_cycle_count):
            exit_reason = self._cap.exit_reason(
                failed_state.recovery_cycle_count,
                str(failure.category),
                failure.reason[:200],
            )
            logger.error("Recovery cycle cap exceeded: {}", exit_reason)
            # Cycle exceeded: no retry delay
            return (
                failed_state.copy_with(last_retry_delay_ms=0),
                [_build_exit_failure_effect(reason=exit_reason)],
                failure_evt,
            )
        return failed_state, [], failure_evt

    def earliest_available_wait_ms(self, phase: str, agents: list[str]) -> int:
        """Return milliseconds until any agent in ``agents`` becomes available."""
        if any(self._is_agent_available(phase, agent) for agent in agents):
            return 0
        return self._unavailability_tracker.earliest_unavailable_wait_ms(phase, agents)

    def _earliest_unavailable_wait_ms(
        self,
        phase: str,
        chain: AgentChainState,
    ) -> int:
        """Return milliseconds until the first unavailable chain agent becomes available."""
        return self.earliest_available_wait_ms(phase, chain.agents)

    def _get_max_retries_for_chain(self, phase: str) -> int:
        """Get max_retries from policy for the chain used by this phase."""
        chain_config = self._chain_config_for_phase(phase)
        if chain_config is None:
            return 3
        return chain_config.max_retries

    def snapshot(self) -> dict[str, object]:
        """Return a runtime observability snapshot of recovery state."""
        tracker_snapshot = self._unavailability_tracker.snapshot()
        merged_attempts = dict(self._backoff_attempts)
        merged_attempts.update(
            cast("dict[str, int]", tracker_snapshot.get("backoff_attempts", {}))
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        return {
            "cycle_cap": self._cap.cap,
            "budgets": {
                f"{phase}:{agent}": {
                    "max_retries": budget.max_retries,
                    "consumed": budget.consumed,
                    "remaining": budget.remaining,
                    "exhausted": budget.exhausted,
                }
                for (phase, agent), budget in self._registry.items()
            },
            "backoff_attempts": merged_attempts,
            "technical_retry_cap": self._technical_retry_cap,
            "unavailable_timeouts": tracker_snapshot["unavailable_timeouts"],
        }

    def waiting_state_payload(self, phase: str, agents: list[str]) -> list[tuple[str, int, int]]:
        """Return the per-agent cooldown payload for the all-agents-unavailable
        WAITING / RESUMED structured logs.

        Each tuple is ``(agent, attempt, cooldown_ms_remaining)`` where
        ``cooldown_ms_remaining`` is the time in milliseconds until the
        agent becomes available (0 if the agent is already available).
        This is the single public surface for the run loop's WAITING
        log; the run loop MUST NOT reach through to the private
        ``_unavailability_tracker`` or the tracker's ``_clock``.

        Args:
            phase: The pipeline phase (e.g. "development").
            agents: The agent chain in policy order.

        Returns:
            A list of ``(agent, attempt, cooldown_ms_remaining)`` tuples,
            one per agent in the chain. Order matches ``agents`` input
            order. Each cooldown is a non-negative int.
        """
        snap = self._unavailability_tracker.snapshot()
        cooldowns_dict = snap.get("unavailable_timeouts", {})
        attempts_dict = snap.get("backoff_attempts", {})
        # Derive the wall-clock from the controller's clock seam so the
        # payload is consistent with the in-memory tracker. The tracker's
        # ``_clock`` is private; ``snapshot()`` is the public surface, and
        # the controller's own ``_clock`` is the seam. This keeps the
        # tracker swappable behind the UnavailabilityStore Protocol
        # without leaking its internal clock field.
        now_ms = int(self._clock.monotonic() * 1000)
        result: list[tuple[str, int, int]] = []
        for agent in agents:
            key = f"{phase}:{agent}"
            timeout_ms = cooldowns_dict.get(key)
            attempt = attempts_dict.get(key, 0)
            attempt_int = int(attempt) if isinstance(attempt, int) else 0
            cooldown_ms = 0
            if isinstance(timeout_ms, int):
                cooldown_ms = max(0, timeout_ms - now_ms)
            result.append((agent, attempt_int, cooldown_ms))
        return result

    def agents_now_available(self, phase: str, agents: list[str]) -> list[str]:
        """Return the subset of ``agents`` that are currently available.

        Convenience wrapper around the public store surface for the run
        loop's RESUMED log. Callers MUST use this method instead of
        reaching through to the private ``_unavailability_tracker``.

        Args:
            phase: The pipeline phase.
            agents: The agent chain in policy order.

        Returns:
            A list of agent names that are currently available, preserving
            the input order.
        """
        return [a for a in agents if self._unavailability_tracker.is_available(phase, a)]

    def _enter_phase_failed(
        self,
        state: PipelineState,
        reason: str,
        category: object,
    ) -> PipelineState:
        """Enter the terminal failure phase.

        Uses policy.declared.failed_route when available, raising a RuntimeError
        if policy is not set (signals missing policy at a routing call site).
        """
        if self._policy_bundle is None:
            raise RuntimeError(
                "_enter_phase_failed requires policy_bundle to be set on the controller. "
                "Without policy, the runtime cannot determine the failure route. "
                "Set policy_bundle when constructing RecoveryController."
            )
        self._spent_agents.pop(state.phase, None)
        failed_route = self._policy_bundle.pipeline.recovery.failed_route
        return progress.advance_phase(
            state,
            failed_route,
            policy=self._policy_bundle.pipeline,
        ).copy_with(
            last_error=reason,
            recovery_epoch=state.recovery_epoch + 1,
            last_failure_category=str(category),
            last_retry_delay_ms=0,
        )
