"""Black-box contract tests for the recovery controller's two main retry rules.

The wt-012 plan enshrines the recovery controller's two-state
recovery invariant:

  - **Rule 1 (same-agent retry):** when an AGENT-category failure
    is NOT marked unavailable, the recovery controller increments the
    chain's ``retries`` counter and re-invokes the same agent.

  - **Rule 2 (exponential backoff to next agent):** when an
    AGENT-category failure IS marked unavailable, the recovery
    controller marks the agent on cooldown via
    ``AgentUnavailabilityTracker.mark_unavailable`` (per-reason
    ``ReasonBackoffPolicy`` exponential backoff capped at
    ``max_backoff_ms``) and advances ``chain.current_index`` to the
    next available agent in the chain (cyclic, ``wrap=True``
    re-arming).

  - **Never-exit invariant:** the pipeline NEVER exits because of
    agent unavailability. When all agents in the chain are on
    cooldown, the controller sets ``is_waiting_state=True`` and
    ``last_retry_delay_ms=<earliest_cooldown>`` and does NOT call
    ``_enter_phase_failed``. The run loop sleeps on
    ``last_retry_delay_ms`` and re-enters the same phase.

The tests below drive the recovery controller through its PUBLIC
surface only (``RecoveryController.handle``,
``RecoveryControllerOptions.unavailability_entries``,
``FailureClassifier.classify``).  No private mutation.  No real
subprocess.  No real network.  No real sleep.  Uses ``FakeClock`` so
the test completes in <3s combined.
"""

from __future__ import annotations

import ast
import tempfile
from pathlib import Path

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_budget_registry import AgentBudgetRegistry
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.budget_state import BudgetState
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEvent, FailureEventBus, FalloverEvent
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import FailureClassifier
from ralph.recovery.unavailability_reason import UnavailabilityReason

REPO_ROOT = Path(__file__).resolve().parents[2]


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _no_output_opts(
    reason: WatchdogFireReason = WatchdogFireReason.NO_OUTPUT_AT_START,
) -> InactivityTimeoutOpts:
    return InactivityTimeoutOpts(
        reason=reason,
        diagnostic={"invocation_elapsed": 30.0},
    )


def _stalled_opts() -> InactivityTimeoutOpts:
    return InactivityTimeoutOpts(
        reason=WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
        diagnostic={"invocation_elapsed": 30.0},
    )


def _three_agent_state(current_index: int = 0, retries: int = 0) -> PipelineState:
    chain_state = AgentChainState(
        agents=["claude", "opencode", "agy"],
        current_index=current_index,
        retries=retries,
    )
    return PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")


def _controller_with_budget(
    *,
    claude_max_retries: int = 3,
    opencode_max_retries: int = 3,
    agy_max_retries: int = 3,
    clock: FakeClock | None = None,
    initial_entries: dict[str, UnavailabilityEntry] | None = None,
) -> RecoveryController:
    """Build a controller with a budget registry pre-seeded for the three agents."""

    registry = AgentBudgetRegistry().set_budget("development", "claude", claude_max_retries)
    registry = registry.set_budget("development", "opencode", opencode_max_retries)
    registry = registry.set_budget("development", "agy", agy_max_retries)
    return RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock or FakeClock(start=0.0),
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
            budget_registry=registry,
            unavailability_entries=initial_entries or {},
        ),
    )


def test_recovery_rule_one_same_agent_retry_for_non_unavailable_failure() -> None:
    """Rule 1: a NON-unavailable AGENT failure retries the same agent.

    A stalled agent with ``watchdog_reason=STALLED_AFTER_TOOL_RESULT``
    is not marked unavailable — it is genuinely stuck, not
    temporarily down.  The recovery controller must increment the
    chain's ``retries`` counter and re-invoke the same agent.

    Assertions:
      - effects list is empty (no ``ExitFailureEffect``).
      - state.phase is unchanged (NOT ``failed_terminal``).
      - state.is_waiting_state is False (NOT a wait branch).
      - chain.retries was incremented by exactly 1 (same-agent retry).
    """
    controller = _controller_with_budget()
    state = _three_agent_state(current_index=0, retries=0)

    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_stalled_opts())
    new_state, effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase == "development"
    assert new_state.is_waiting_state is False
    assert effects == [], f"expected no effects, got {effects}"

    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 0, (
        f"expected same agent (claude), got index {chain.current_index}"
    )
    assert chain.retries == 1, f"expected retries incremented to 1, got {chain.retries}"


def test_recovery_rule_two_exponential_backoff_to_next_agent_for_unavailable_failure() -> None:
    """Rule 2: an unavailable AGENT failure is marked on cooldown and the chain advances.

    A ``NO_OUTPUT_AT_START`` failure with connectivity online is
    classified as unavailable (the agent is not producing output
    despite a healthy connection — it is e.g. out of credits).
    The recovery controller must:
      (a) mark the agent on cooldown via
          ``AgentUnavailabilityTracker.mark_unavailable``.
      (b) advance the chain's ``current_index`` to the next agent.
      (c) reset the chain's ``retries`` to 0 (fresh chain on new agent).
      (d) NOT call ``_enter_phase_failed`` (the pipeline does not exit).
      (e) NOT set ``is_waiting_state=True`` (we found an available agent).

    Assertions:
      - state.phase is unchanged.
      - state.is_waiting_state is False.
      - chain.current_index advanced from 0 to 1.
      - chain.retries is 0.
      - The unavailability tracker shows claude on cooldown.
      - effects list is empty.
    """
    controller = _controller_with_budget()
    state = _three_agent_state(current_index=0, retries=0)

    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())
    new_state, effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase == "development", (
        f"phase must not advance to failed_terminal, got {new_state.phase}"
    )
    assert new_state.is_waiting_state is False

    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1, (
        f"chain must advance to next agent (opencode), got index {chain.current_index}"
    )
    assert chain.retries == 0, f"retries must reset on new agent, got {chain.retries}"

    # Claude must be on cooldown.
    store = controller.unavailability_store
    assert not store.is_available("development", "claude"), (
        "claude must be on cooldown after NO_OUTPUT_AT_START"
    )

    assert effects == [], f"expected no effects, got {effects}"


def test_recovery_never_exits_on_unavailability_when_all_agents_on_cooldown() -> None:
    """Never-exit invariant: when all agents are on cooldown, the
    controller enters the wait state, NOT ``failed_terminal``.

    Pre-seed all 3 agents as unavailable, with the earliest cooldown
    (5s) on claude.  Drive the controller with a claude failure
    classified as unavailable.  The controller must:
      (a) NOT advance ``state.phase`` to ``failed_terminal``.
      (b) Set ``state.is_waiting_state=True`` (the structured wait flag).
      (c) Set ``state.last_retry_delay_ms == 5000`` (the earliest cooldown).
      (d) NOT consume a recovery cycle (``state.recovery_cycle_count == 0``).
      (e) Return an empty effects list (no ``ExitFailureEffect``).
    """
    initial_entries: dict[str, UnavailabilityEntry] = {
        "development:claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
        "development:opencode": UnavailabilityEntry(
            unavailable_until_ms=10000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=10000,
            max_backoff_ms=10000,
        ),
        "development:agy": UnavailabilityEntry(
            unavailable_until_ms=7000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=7000,
            max_backoff_ms=7000,
        ),
    }
    controller = _controller_with_budget(initial_entries=initial_entries)
    state = _three_agent_state(current_index=0, retries=0)

    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())
    new_state, effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase == "development"
    assert new_state.is_waiting_state is True
    assert new_state.last_retry_delay_ms == 5000
    assert new_state.recovery_cycle_count == 0
    assert effects == [], f"expected no effects (wait state, not exit), got {effects}"


def test_recovery_reconsiders_earlier_agent_after_cooldown_expires_with_wrap_true() -> None:
    """wrap=True re-arming: when the chain advances, earlier agents
    whose cooldown has expired are reconsidered.

    Setup:
      - 3 agents, all pre-seeded as unavailable.
      - claude: 5s cooldown.
      - opencode: 60s cooldown.
      - agy: 30s cooldown.

    Behaviour:
      - First handle() with claude failing -> wait state, wait_ms=5000.
      - Advance FakeClock by 5.1s. claude is now available; opencode and
        agy are still on cooldown.
      - Set chain current_index=1 (opencode) so we can verify wrap=True.
      - Second handle() with opencode failing. The controller must
        see opencode is on cooldown, see agy is on cooldown, use
        wrap=True to reconsider claude (index 0), which is now
        available, and select claude as the next agent.

    Assertions:
      - chain.current_index wraps to 0 (claude, the recovered agent).
      - state.is_waiting_state is False (we found an available agent).
    """
    clock = FakeClock(start=0.0)
    initial_entries: dict[str, UnavailabilityEntry] = {
        "development:claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
        "development:opencode": UnavailabilityEntry(
            unavailable_until_ms=60000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=60000,
            max_backoff_ms=60000,
        ),
        "development:agy": UnavailabilityEntry(
            unavailable_until_ms=30000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=30000,
            max_backoff_ms=30000,
        ),
    }
    controller = _controller_with_budget(
        clock=clock,
        initial_entries=initial_entries,
    )
    state = _three_agent_state(current_index=0, retries=0)

    # First failure: claude. All 3 unavailable -> wait state.
    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())
    state_after_first, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )
    assert state_after_first.is_waiting_state is True
    assert state_after_first.last_retry_delay_ms == 5000

    # Advance clock past claude's cooldown. claude is now available;
    # opencode and agy remain on cooldown.
    clock.advance(5.1)

    # Manually advance the chain to opencode (index=1) so we can
    # verify wrap=True re-arming finds claude (index=0).
    state_at_opencode = state_after_first.with_phase_chain(
        "development",
        AgentChainState(
            agents=["claude", "opencode", "agy"],
            current_index=1,
            retries=0,
        ),
    )

    # Second failure: opencode. The controller sees opencode is on
    # cooldown, agy is on cooldown, claude is available. The wrap
    # re-arming must select claude.
    exc2 = AgentInactivityTimeoutError("opencode", 30.0, opts=_no_output_opts())
    state_after_second, _effects2, _evt2 = controller.handle(
        state_at_opencode,
        exc2,
        FailureContext(phase="development", agent="opencode"),
    )

    chain = state_after_second.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 0, (
        f"wrap=True re-arming must select claude (the recovered agent), got index"
        f" {chain.current_index}"
    )
    assert state_after_second.is_waiting_state is False


def test_recovery_chain_exhaustion_only_when_no_budget_remaining() -> None:
    """Chain exhaustion: when every agent's budget is exhausted, the
    pipeline MAY advance to ``failed_terminal``.

    This is the ONLY path that may exit the pipeline, and it is the
    BUDGET-EXHAUSTED path, NOT the unavailability path.  The
    never-exit invariant only forbids exit on unavailability; the
    budget-exhausted path is the legitimate end of the chain.

    Setup:
      - claude (current): budget exhausted (consumed=max_retries=2).
      - opencode and agy: budget exhausted AND on cooldown.

    The current agent's budget is exhausted (so the retry-current
    branch is skipped).  The other agents are unavailable (so the
    advance-chain branch returns None).  ``all(unavailable)`` is
    False because claude is not on cooldown.  The controller must
    fall through to ``_enter_phase_failed`` and advance
    ``state.phase`` to ``failed_terminal``.

    Assertions:
      - state.phase advances to ``failed_terminal``.
      - state.recovery_cycle_count is incremented by 1.
      - The state is NOT in the wait state (is_waiting_state is False).
    """
    exhausted = BudgetState(max_retries=2, consumed=2, failures=())
    registry = AgentBudgetRegistry(
        budgets={
            ("development", "claude"): exhausted,
            ("development", "opencode"): exhausted,
            ("development", "agy"): exhausted,
        }
    )

    initial_entries: dict[str, UnavailabilityEntry] = {
        "development:opencode": UnavailabilityEntry(
            unavailable_until_ms=10000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=10000,
            max_backoff_ms=10000,
        ),
        "development:agy": UnavailabilityEntry(
            unavailable_until_ms=10000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=10000,
            max_backoff_ms=10000,
        ),
    }

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=FakeClock(start=0.0),
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
            budget_registry=registry,
            unavailability_entries=initial_entries,
        ),
    )
    state = _three_agent_state(current_index=0, retries=2)

    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_stalled_opts())
    new_state, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase == "failed_terminal", (
        f"phase must advance to failed_terminal on chain exhaustion, got {new_state.phase}"
    )
    assert new_state.is_waiting_state is False
    assert new_state.recovery_cycle_count == 1, (
        f"recovery_cycle_count must increment to 1, got {new_state.recovery_cycle_count}"
    )


def test_recovery_failure_classifier_consults_typed_cause_for_watchdog_kill() -> None:
    """End-to-end typed-cause: the classifier routes the
    ``IdleWatchdogKilledError`` chain to ``FailureCategory.AGENT``
    and ``is_unavailable=True``.

    This proves the two rules are wired end-to-end through the
    typed exception, not via text matching.  When the recovery
    controller receives an ``AgentInactivityTimeoutError`` whose
    ``__cause__`` chain contains an
    ``IdleWatchdogKilledError(reason='no_progress_quiet',
    signal=15)``, the classifier must return:
      - category == FailureCategory.AGENT
      - is_unavailable == True
    so the controller routes to the exponential-backoff branch
    (rule two).  The signal value 15 (SIGTERM) and the reason
    ``no_progress_quiet`` are typed attributes — the classifier
    does NOT parse the exception message.
    """
    classifier = FailureClassifier()
    watchdog_exc = IdleWatchdogKilledError(reason="no_progress_quiet", signal=15)
    inactivity_exc = AgentInactivityTimeoutError(
        "claude",
        30.0,
        opts=InactivityTimeoutOpts(
            reason=WatchdogFireReason.NO_PROGRESS_QUIET,
            diagnostic={"invocation_elapsed": 30.0},
        ),
    )
    inactivity_exc.__cause__ = watchdog_exc

    classified = classifier.classify(
        inactivity_exc,
        phase="development",
        agent="claude",
        connectivity_state="online",
    )

    assert classified.category == FailureCategory.AGENT, (
        f"typed watchdog kill must classify as AGENT, got {classified.category}"
    )
    assert classified.is_unavailable is True, (
        f"typed watchdog kill must be unavailable, got is_unavailable={classified.is_unavailable}"
    )


def test_recovery_does_not_call_enter_phase_failed_in_unavailability_branch() -> None:
    """Black-box AST check: in the unavailability branch of
    ``_handle_retry_progression``, the function returns BEFORE
    ``_enter_phase_failed`` can be called.

    The structural invariant is: the all-agents-unavailable branch
    (which is the wait branch) is reached before the
    ``_enter_phase_failed`` call, and the branch itself returns
    immediately.  This test walks the AST of ``controller.py`` and
    confirms the unavailability branch returns BEFORE the
    fallthrough path that calls ``_enter_phase_failed``.
    """
    controller_path = REPO_ROOT / "ralph" / "recovery" / "controller.py"
    source = controller_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(controller_path))

    # Locate ``_handle_retry_progression`` function.
    target_func: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_handle_retry_progression":
            target_func = node
            break
    assert target_func is not None, "could not find _handle_retry_progression in controller.py"

    # Find the all-agents-unavailable branch and the
    # _enter_phase_failed call within the function body.  The
    # call appears in an Assign statement (the return value is
    # captured into ``failed_state``), so we walk the function
    # body and look for any statement that contains a
    # ``self._enter_phase_failed`` call.
    body = target_func.body
    unavailable_branch_start: int | None = None
    enter_phase_failed_call: int | None = None
    for idx, stmt in enumerate(body):
        if isinstance(stmt, ast.If):
            test = ast.unparse(stmt.test)
            # The all-agents-unavailable test renders as
            # ``all((not self._is_agent_available(phase, agent) for agent in chain.agents))``
            # in the AST (note the extra outer parens wrapping the
            # generator).  Match the substring ``self._is_agent_available``
            # inside the test source.
            if "self._is_agent_available" in test and "all(" in test:
                unavailable_branch_start = idx
        for child in ast.walk(stmt):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "_enter_phase_failed"
            ):
                enter_phase_failed_call = idx
                break

    assert unavailable_branch_start is not None, (
        "could not find the all-agents-unavailable branch in _handle_retry_progression"
    )
    assert enter_phase_failed_call is not None, (
        "could not find the _enter_phase_failed call in _handle_retry_progression"
    )
    assert unavailable_branch_start < enter_phase_failed_call, (
        f"unavailability branch (idx {unavailable_branch_start}) must come BEFORE the"
        f" _enter_phase_failed call (idx {enter_phase_failed_call}) in the function body"
    )

    # The unavailability branch itself must return. Walk the branch
    # body and confirm at least one Return statement is reachable.
    unavail_stmt = body[unavailable_branch_start]
    assert isinstance(unavail_stmt, ast.If)
    branch_returns = [n for n in ast.walk(unavail_stmt) if isinstance(n, ast.Return)]
    assert branch_returns, (
        "all-agents-unavailable branch in _handle_retry_progression must contain"
        " a Return statement so the wait branch never falls through to"
        " _enter_phase_failed"
    )


# ---------------------------------------------------------------------------
# wt-012 typed-evidence differentiation: live-child vs dead-child
# NO_PROGRESS_QUIET (Rule 1 vs Rule 2)
# ---------------------------------------------------------------------------
# The wt-012 typed-evidence path differentiates live-child
# (``child_alive=True``) from dead-child (``child_alive=False`` or
# ``child_alive=None``) NO_PROGRESS_QUIET at the typed-exception
# level. Live-child NO_PROGRESS_QUIET routes to ``is_unavailable=False``
# (Rule 1: same-agent retry, defense-in-depth -- normally dead code
# because the gate refinement in ``IdleWatchdog._is_no_progress_quiet``
# defers the fire when alive_by is not None). Dead-child NO_PROGRESS_QUIET
# routes to ``is_unavailable=True`` with
# ``unavailability_reason=STALE_CHILD_QUIET`` (Rule 2: exponential
# backoff to the next agent). The conservative policy: ``child_alive=None``
# (legacy default -- no signal at all) preserves the original
# ``STALE_CHILD_QUIET`` (Rule 2) behavior for backward-compat with
# the 14 existing tests in ``test_unavailability_reason.py`` that do
# not set ``child_alive``.


def test_no_progress_quiet_with_dead_child_routes_to_exponential_backoff() -> None:
    """``NO_PROGRESS_QUIET`` with a TRULY-DEAD child routes to Rule 2
    (exponential backoff to the next agent) end-to-end.

    Per the wt-012 typed-evidence path: when
    ``IdleWatchdogKilledError.child_alive=False`` (truly dead child --
    the corroborator returned ``alive_by=None``), the failure
    classifier must set ``is_unavailable=True`` with
    ``unavailability_reason=STALE_CHILD_QUIET`` and the recovery
    controller must mark claude on cooldown, advance to the next
    available agent (opencode), and reset ``retries=0``.

    The conservative policy: ``child_alive=False`` (truly dead
    child) maps to ``is_unavailable=True`` (Rule 2) -- the same
    as the legacy ``child_alive=None`` default behavior. The
    ``child_alive=True`` (live child) defense-in-depth case
    routes to ``is_unavailable=False`` (Rule 1) but is
    normally dead code because the wt-012 gate refinement
    defers NO_PROGRESS_QUIET when alive_by is not None.

    Setup:
      - 3-agent chain: claude (index 0), opencode (index 1), agy (index 2).
      - Build ``AgentInactivityTimeoutError(opts=InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_PROGRESS_QUIET, ...))`` with
        ``IdleWatchdogKilledError(reason='no_progress_quiet',
        signal=15, child_alive=False)`` set as ``__cause__``.
      - Drive the ``RecoveryController.handle`` path.

    Assertions:
      - chain.current_index advanced from 0 to 1 (opencode).
      - chain.retries == 0 (reset on chain advance).
      - claude is on cooldown in the unavailability store.
      - The published ``FalloverEvent.unavailability_reason`` is
        ``STALE_CHILD_QUIET`` (the Rule 2 typed-evidence reason).
    """
    captured_fallover_events: list[FalloverEvent] = []
    bus = FailureEventBus()

    def _capture(evt: FailureEvent | FalloverEvent) -> None:
        if isinstance(evt, FalloverEvent):
            captured_fallover_events.append(evt)

    bus.subscribe(_capture)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=FakeClock(start=0.0),
            policy_bundle=_minimal_policy_bundle(),
            event_bus=bus,
            budget_registry=AgentBudgetRegistry()
            .set_budget("development", "claude", 3)
            .set_budget("development", "opencode", 3)
            .set_budget("development", "agy", 3),
        ),
    )
    state = _three_agent_state(current_index=0, retries=0)

    watchdog_exc = IdleWatchdogKilledError(
        reason="no_progress_quiet",
        signal=15,
        child_alive=False,
    )
    inactivity_exc = AgentInactivityTimeoutError(
        "claude",
        30.0,
        opts=InactivityTimeoutOpts(
            reason=WatchdogFireReason.NO_PROGRESS_QUIET,
            diagnostic={"invocation_elapsed": 30.0},
        ),
    )
    inactivity_exc.__cause__ = watchdog_exc

    new_state, _effects, _evt = controller.handle(
        state,
        inactivity_exc,
        FailureContext(phase="development", agent="claude"),
    )

    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1, (
        f"chain must advance to opencode (index 1) when NO_PROGRESS_QUIET"
        f" fires with child_alive=False (Rule 2: exponential backoff),"
        f" got current_index={chain.current_index}"
    )
    assert chain.retries == 0, (
        f"chain.retries must reset to 0 on chain advance, got {chain.retries}"
    )
    assert not controller.unavailability_store.is_available("development", "claude"), (
        "claude must be on cooldown in the unavailability store after"
        " NO_PROGRESS_QUIET with child_alive=False (Rule 2)"
    )
    # Verify the FalloverEvent carries the typed-evidence
    # STALE_CHILD_QUIET reason (the conservative policy: child_alive=False
    # routes to Rule 2, same as the legacy child_alive=None default).
    assert len(captured_fallover_events) == 1, (
        f"expected exactly one FalloverEvent, got {len(captured_fallover_events)}"
    )
    fallover = captured_fallover_events[0]
    assert fallover.unavailability_reason == UnavailabilityReason.STALE_CHILD_QUIET, (
        f"FalloverEvent.unavailability_reason must be STALE_CHILD_QUIET for"
        f" NO_PROGRESS_QUIET with child_alive=False (Rule 2), got {fallover.unavailability_reason}"
    )
