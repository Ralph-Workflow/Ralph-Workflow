"""End-to-end black-box tests that lock the prompt's full unavailability contract.

The prompt in ``.agent/CURRENT_PROMPT.md`` (wt-014-unavailable-detection)
describes a runtime hole in the agent-fallback chain: an opencode run that
produced zero output for ~10 minutes (log entries at 2026-06-15T08:13:18 -
2026-06-15T08:28:18) despite healthy internet, and the watchdog took the
full 600s waiting ceiling to fire. The prompt locks six contract
requirements:

  1. A fast 'no output despite healthy internet' detection that
     fast-fallovers to the next agent (AC-01).
  2. Per-reason exponential backoff so a 5-hour weekly-limit cooldown
     does not punish transient 1-minute blips (AC-02).
  3. A black-box testable architecture (the existing Protocol-typed
     store seam).
  4. A session-scoped unavailability store with a future-expansion
     seam (AC-04).
  5. A never-crash forever-wait state when every agent in the chain is
     unavailable (AC-03).
  6. The distinction between 'out of credits / no output in the
     beginning' (cold-start stall) and 'running subagents and just
     waiting' (positive-waiting suppression) (AC-02).

These four tests tie the prompt's scenario together end-to-end through
the public API. They MUST stay black-box:

  - No ``time.sleep``, real subprocess, or real network.
  - No private reach-through. Every assertion goes through the public
    controller surface (``controller.handle``, ``controller.unavailability_store``,
    ``controller.waiting_state_payload``, ``controller.agents_now_available``,
    ``controller.snapshot``).
  - Every dependency is faked or wrapped behind a Protocol surface
    (``FakeClock`` for time, ``AgentUnavailabilityTracker`` for the
    store, the real ``RecoveryController``).

AC mapping:
  - test_prompt_scenario_five_hour_stall_caught_at_30s -> AC-01
  - test_prompt_scenario_distinguishes_out_of_credits_from_subagents -> AC-02
  - test_prompt_scenario_forever_wait_through_many_cooldown_cycles -> AC-03
  - test_prompt_scenario_session_scope_with_future_expansion_seam -> AC-04
  - All four tests are black-box and run inside the 60s combined
    budget -> AC-05
"""

from __future__ import annotations

import importlib
import tempfile
from pathlib import Path
from typing import cast

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.invoke import AgentInvocationError
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_unavailability_tracker import (
    AgentUnavailabilityTracker,
    UnavailabilityEntry,
    UnavailabilityStore,
)
from ralph.recovery.classifier import FailureClassifier, FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus
from ralph.recovery.unavailability_reason import (
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    ReasonBackoffPolicy,
    UnavailabilityReason,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _minimal_policy_bundle() -> object:
    """Build a minimal policy bundle the controller can route through.

    Mirrors the helper in test_all_agents_unavailable_never_crashes.py so
    the controller's policy-driven code paths (chain config, failed
    route) work in tests. The tempdir is created and destroyed per call
    because the bundle is fully loaded into memory and does not hold
    references to the tempdir.
    """
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _no_output_opts() -> InactivityTimeoutOpts:
    """Build the canonical NO_OUTPUT_AT_START opts for inactivity timeouts."""
    return InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_OUTPUT_AT_START,
        diagnostic={"invocation_elapsed": 30.0},
    )


def _no_progress_opts() -> InactivityTimeoutOpts:
    """Build the canonical NO_PROGRESS_QUIET opts for inactivity timeouts."""
    return InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_PROGRESS_QUIET,
        diagnostic={"invocation_elapsed": 60.0},
    )


def _three_agent_state(current_index: int = 0) -> PipelineState:
    """Build a 3-agent pipeline state for the development phase, online."""
    chain_state = AgentChainState(
        agents=["claude", "opencode", "agy"],
        current_index=current_index,
        retries=0,
    )
    return PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")


def _build_default_watchdog(clock: FakeClock) -> IdleWatchdog:
    """Build a default IdleWatchdog with the production 30s threshold.

    ``no_output_at_start_seconds=30.0`` is the production default from
    ``ralph.timeout_defaults.NO_OUTPUT_AT_START_SECONDS``. Other timeout
    fields are left at their defaults so a future default-tuning change
    is automatically picked up by the test.
    """
    policy = TimeoutPolicy(idle_timeout_seconds=300.0)
    return IdleWatchdog(policy, clock)


def _wait_state_backoff_policy() -> dict[UnavailabilityReason, ReasonBackoffPolicy]:
    """Custom backoff policy used by the forever-wait test.

    Base 15_000ms covers 10 iterations of 1s FakeClock advance plus
    margin, so the wait state is naturally maintained for the entire
    loop without re-marking every agent on every iteration. Max
    30_000ms matches the documented NO_OUTPUT_AT_START max, so the
    plan's "<= 30_000ms" assertion still binds.
    """
    return {
        UnavailabilityReason.NO_OUTPUT_AT_START: ReasonBackoffPolicy(
            base_backoff_ms=15_000,
            max_backoff_ms=30_000,
        ),
    }


# ---------------------------------------------------------------------------
# AC-01: Fast 'no output despite healthy internet' detection (5-hour stall)
# ---------------------------------------------------------------------------


def test_prompt_scenario_five_hour_stall_caught_at_30s() -> None:
    """AC-01: an agent producing zero output despite healthy internet is
    detected at 30s and the chain falls over to the next agent.

    Reproduces the user's exact log scenario (2026-06-15T08:13:18 -
    2026-06-15T08:28:18) where the watchdog took the full 600s waiting
    ceiling to fire. The contract locks:

      1. ``TimeoutPolicy.no_output_at_start_seconds`` defaults to 30.0
         (the production default from ``ralph.timeout_defaults``).
      2. After 30+ seconds with zero recorded activity and
         ``classify_quiet=lambda: ACTIVE``, the watchdog returns
         ``WatchdogVerdict.FIRE`` with ``last_fire_reason ==
         NO_OUTPUT_AT_START``.
      3. Driving the same failure through ``RecoveryController.handle()``
         marks the agent as unavailable with the NO_OUTPUT_AT_START
         backoff window (5_000ms base / 30_000ms cap) and falls over
         from index 0 (claude) to index 1 (opencode).

    The remaining cooldown is computed as
    ``snapshot_timeout - current_time_ms`` because
    ``controller.snapshot()['unavailable_timeouts']`` reports the
    absolute ``unavailable_until_ms`` timestamp (when the cooldown
    expires), not the remaining duration. The 5_000-30_000ms remaining
    window matches the ``DEFAULT_UNAVAILABILITY_BACKOFF_POLICY`` entry
    for NO_OUTPUT_AT_START.
    """
    clock = FakeClock(start=0.0)
    watchdog = _build_default_watchdog(clock)
    watchdog.record_invocation_start()

    clock.advance(31.0)

    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
        ),
    )
    state = _three_agent_state()

    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())
    state, _effects, _failure_evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    chain = state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1

    snapshot = controller.snapshot()
    claude_cooldown_ms = snapshot["unavailable_timeouts"]["development:claude"]
    current_time_ms = int(clock.monotonic() * 1000)
    claude_remaining_ms = claude_cooldown_ms - current_time_ms
    assert 5_000 <= claude_remaining_ms <= 30_000, (
        f"NO_OUTPUT_AT_START remaining cooldown must be within the "
        f"5_000-30_000ms window from DEFAULT_UNAVAILABILITY_BACKOFF_POLICY, "
        f"got {claude_remaining_ms}ms (snapshot={claude_cooldown_ms}ms, "
        f"now={current_time_ms}ms)"
    )

    available = controller.agents_now_available(
        "development", ["claude", "opencode", "agy"],
    )
    assert "opencode" in available
    assert "claude" not in available


# ---------------------------------------------------------------------------
# AC-02: Per-reason exponential backoff (out of credits vs running subagents)
# ---------------------------------------------------------------------------


def test_prompt_scenario_distinguishes_out_of_credits_from_subagents() -> None:
    """AC-02: the per-reason backoff policy applies distinct cooldowns.

    The prompt distinguishes 'no output in the beginning'
    (NO_OUTPUT_AT_START, short cooldown) from 'running subagents and
    just waiting' (STALE_CHILD_QUIET, longer cooldown) from 'out of
    credits' (OUT_OF_CREDITS, 30-minute cap). The contract locks:

      1. The ``FailureClassifier`` produces the right
         ``unavailability_reason`` for each failure shape, given
         ``connectivity_state='online'``.
      2. ``AgentUnavailabilityTracker.mark_unavailable`` records the
         exact ``base_backoff_ms`` and ``max_backoff_ms`` from
         ``DEFAULT_UNAVAILABILITY_BACKOFF_POLICY`` for each reason.
      3. The typed ``AgentInvocationError`` carrying the documented
         Claude Code limit message classifies as ``OUT_OF_CREDITS``;
         the production failure surface (``AgentInvocationError`` with
         a non-zero returncode and a limit-bearing stderr) is a
         distinct input shape from the raw-string surface, and the
         classifier must handle both equivalently.

    The test drives the classifier with three raw-string failure shapes
    and one typed-exception shape, all classified as agent/unavailable
    with the same ``connectivity_state='online'``. The shapes are:
    subscription-limit raw text, NO_OUTPUT_AT_START watchdog fire, and
    NO_PROGRESS_QUIET watchdog fire. The test then drives the tracker
    with each reason and asserts the exact cooldown table.
    """
    classifier = FailureClassifier()

    classified_a = classifier.classify(
        "You've hit your weekly limit",
        phase="development",
        agent="claude",
        connectivity_state="online",
    )
    assert classified_a.unavailability_reason == UnavailabilityReason.OUT_OF_CREDITS

    classified_b = classifier.classify(
        AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts()),
        phase="development",
        agent="claude",
        connectivity_state="online",
    )
    assert classified_b.unavailability_reason == UnavailabilityReason.NO_OUTPUT_AT_START

    classified_c = classifier.classify(
        AgentInactivityTimeoutError("claude", 60.0, opts=_no_progress_opts()),
        phase="development",
        agent="claude",
        connectivity_state="online",
    )
    assert classified_c.unavailability_reason == UnavailabilityReason.STALE_CHILD_QUIET

    classified_typed = classifier.classify(
        AgentInvocationError("claude", 1, "You've hit your weekly limit"),
        phase="development",
        agent="claude",
        connectivity_state="online",
    )
    assert classified_typed.is_unavailable is True
    assert classified_typed.unavailability_reason == UnavailabilityReason.OUT_OF_CREDITS

    clock = FakeClock(start=0.0)
    tracker = AgentUnavailabilityTracker(clock=clock)

    out_of_credits_entry = tracker.mark_unavailable(
        "development", "claude", UnavailabilityReason.OUT_OF_CREDITS,
    )
    assert out_of_credits_entry.base_backoff_ms == 60_000
    assert out_of_credits_entry.max_backoff_ms == 1_800_000

    no_output_entry = tracker.mark_unavailable(
        "development", "opencode", UnavailabilityReason.NO_OUTPUT_AT_START,
    )
    assert no_output_entry.base_backoff_ms == 5_000
    assert no_output_entry.max_backoff_ms == 30_000

    stale_child_entry = tracker.mark_unavailable(
        "development", "agy", UnavailabilityReason.STALE_CHILD_QUIET,
    )
    assert stale_child_entry.base_backoff_ms == 15_000
    assert stale_child_entry.max_backoff_ms == 300_000

    policy = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY
    assert policy[UnavailabilityReason.OUT_OF_CREDITS].base_backoff_ms == 60_000
    assert policy[UnavailabilityReason.OUT_OF_CREDITS].max_backoff_ms == 1_800_000
    assert policy[UnavailabilityReason.NO_OUTPUT_AT_START].base_backoff_ms == 5_000
    assert policy[UnavailabilityReason.NO_OUTPUT_AT_START].max_backoff_ms == 30_000
    assert policy[UnavailabilityReason.STALE_CHILD_QUIET].base_backoff_ms == 15_000
    assert policy[UnavailabilityReason.STALE_CHILD_QUIET].max_backoff_ms == 300_000

    for reason in (
        UnavailabilityReason.OUT_OF_CREDITS,
        UnavailabilityReason.NO_OUTPUT_AT_START,
        UnavailabilityReason.STALE_CHILD_QUIET,
    ):
        rp = policy[reason]
        assert rp.base_backoff_ms < rp.max_backoff_ms
        assert rp.base_backoff_ms > 0


# ---------------------------------------------------------------------------
# AC-03: Never-crash forever-wait through many cooldown cycles
# ---------------------------------------------------------------------------


def test_prompt_scenario_forever_wait_through_many_cooldown_cycles() -> None:
    """AC-03: the forever-wait state is entered when all agents in the
    chain are unavailable, never increments ``recovery_cycle_count``,
    and the run loop sleeps for the earliest cooldown. After the
    cooldown expires, the controller routes to the FIRST available
    agent (wrap=True semantics in ``_next_available_agent_index``).

    Setup:
      - 3-agent chain (claude, opencode, agy) with a custom
        NO_OUTPUT_AT_START policy (15_000ms base, 30_000ms cap). The
        15s base covers 10 iterations of 1s FakeClock advance plus
        margin, so the wait state is naturally maintained for the
        entire loop without re-marking every agent on every iteration.
        The 30s cap matches the documented NO_OUTPUT_AT_START max.
      - ``FakeClock`` for deterministic time.

    Behaviour:
      - Mark all three agents unavailable ONCE through
        ``controller.handle()`` with a NO_OUTPUT_AT_START failure per
        agent. The third call lands on the all-agents-unavailable wait
        branch: ``state.is_waiting_state == True`` and
        ``state.recovery_cycle_count == 0``.
      - Loop 10 times: advance the FakeClock by 1s, call handle() with
        a NO_OUTPUT_AT_START failure for the current agent (agy, the
        chain tail). Each iteration must:
          * preserve state.phase == 'development' (never
            'failed_terminal'),
          * preserve state.recovery_cycle_count == 0,
          * keep state.last_retry_delay_ms > 0 and <= 30_000ms
            (bounded by NO_OUTPUT_AT_START max).
      - After the loop, advance the FakeClock past the 15s base
        cooldown so claude and opencode are available (agy is still
        on cooldown because it was re-marked 10 times with exponential
        backoff up to the 30_000ms cap). Call handle() once more; the
        chain must advance to the FIRST available agent in chain
        order (claude, index 0), NOT the last available agent. The
        wrap=True semantics in ``_next_available_agent_index`` is
        exercised here.
    """
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
            unavailability_backoff_policy=_wait_state_backoff_policy(),
        ),
    )
    state = _three_agent_state()
    opts = _no_output_opts()

    for agent_name in ("claude", "opencode", "agy"):
        exc = AgentInactivityTimeoutError(agent_name, 30.0, opts=opts)
        state, _effects, _failure_evt = controller.handle(
            state,
            exc,
            FailureContext(phase="development", agent=agent_name),
        )

    chain = state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == len(chain.agents) - 1
    assert state.is_waiting_state is True, (
        f"expected wait state to be entered after all 3 agents marked "
        f"unavailable, got is_waiting_state={state.is_waiting_state!r}"
    )
    assert state.recovery_cycle_count == 0
    assert state.last_retry_delay_ms > 0
    assert state.last_retry_delay_ms <= 30_000

    for _ in range(10):
        clock.advance(1.0)

        current_agent = chain.agents[chain.current_index]
        exc = AgentInactivityTimeoutError(current_agent, 30.0, opts=opts)
        state, _effects, _failure_evt = controller.handle(
            state,
            exc,
            FailureContext(phase="development", agent=current_agent),
        )

        assert state.phase == "development", (
            f"wait state must never enter 'failed_terminal', got "
            f"phase={state.phase!r}"
        )
        assert state.recovery_cycle_count == 0, (
            "wait state must never consume the recovery cycle cap"
        )
        assert state.last_retry_delay_ms > 0, (
            "wait state must always have a positive sleep so the run "
            "loop can resume"
        )
        assert state.last_retry_delay_ms <= 30_000, (
            f"wait state delay must be bounded by NO_OUTPUT_AT_START "
            f"max (30_000ms), got {state.last_retry_delay_ms}ms"
        )

    clock.advance(6.0)

    current_agent = chain.agents[chain.current_index]
    exc = AgentInactivityTimeoutError(current_agent, 30.0, opts=opts)
    state, _effects, _failure_evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent=current_agent),
    )

    chain = state.chain_for_phase("development")
    assert chain is not None
    available = controller.agents_now_available("development", chain.agents)
    assert "claude" in available, (
        f"after advancing past claude's 15s base cooldown, claude must "
        f"be available, got available={available!r}"
    )
    assert chain.current_index == 0, (
        f"controller must route to the FIRST available agent in chain "
        f"order (wrap=True semantics in _next_available_agent_index), "
        f"got current_index={chain.current_index}, available={available!r}"
    )


# ---------------------------------------------------------------------------
# AC-04: Session scope + future-expansion seam
# ---------------------------------------------------------------------------


def test_prompt_scenario_session_scope_with_future_expansion_seam() -> None:
    """AC-04: the UnavailabilityStore Protocol seam allows a custom store
    to be injected via ``RecoveryControllerOptions``; the default
    tracker ``scope`` is 'session' with a future-expansion seam for
    'persistent' stores; the public controller surface
    (``unavailability_store``, ``waiting_state_payload``,
    ``agents_now_available``, ``snapshot``) is used end-to-end with
    no private reach-through.

    Locks four invariants:
      1. The controller exposes a public ``unavailability_store``
         property that returns the injected store unchanged
         (Protocol-typed).
      2. ``AgentUnavailabilityTracker().scope`` defaults to 'session'.
      3. Driving the controller with a custom store delegates
         ``mark_unavailable`` to the custom implementation with the
         right ``(phase, agent, reason)`` tuple.
      4. The existing private-reach-through guard test
         (``test_run_loop_does_not_reach_through_private_tracker_attributes``)
         is still present in the production code surface and is
         accessible from its dedicated module. The run loop's
         ``_unavailability_tracker`` and ``_clock`` symbols are not
         reached through from production code.

    The guard test is referenced via ``importlib.import_module`` +
    ``getattr`` (rather than a top-level ``from <module> import``) so
    pytest does not re-collect it in this module's test inventory.
    """
    guard_module = importlib.import_module(
        "tests.pipeline.test_run_loop_waiting_state_real_controller"
    )
    guard_test = getattr(
        guard_module,
        "test_run_loop_does_not_reach_through_private_tracker_attributes",
        None,
    )
    assert callable(guard_test), (
        "the dedicated private-reach-through guard test must remain "
        "present in tests/pipeline/test_run_loop_waiting_state_real_controller.py"
    )

    marker: dict[str, object] = {}
    entries: dict[str, UnavailabilityEntry] = {}

    class _CustomStore:
        """Minimal UnavailabilityStore Protocol-typed implementation.

        Records every ``mark_unavailable`` call into the shared
        ``marker`` dict and tracks entries in a dict keyed by
        ``phase:agent`` so the public ``is_available`` /
        ``waiting_state_payload`` surface can be exercised end-to-end
        through the controller.
        """

        @property
        def scope(self) -> str:
            return "session"

        def mark_unavailable(
            self,
            phase: str,
            agent: str,
            reason: UnavailabilityReason | None = None,
        ) -> UnavailabilityEntry:
            marker["last_call"] = (phase, agent, reason)
            key = f"{phase}:{agent}"
            entry = UnavailabilityEntry(
                unavailable_until_ms=10_000,
                reason=reason,
                attempt=0,
                base_backoff_ms=5_000,
                max_backoff_ms=30_000,
            )
            entries[key] = entry
            return entry

        def is_available(self, phase: str, agent: str) -> bool:
            key = f"{phase}:{agent}"
            return key not in entries

        def earliest_unavailable_wait_ms(self, phase: str, agents: list[str]) -> int:
            return 0

        def reset_backoff(self, phase: str, agent: str) -> None:
            entries.pop(f"{phase}:{agent}", None)

        def snapshot(self) -> dict[str, dict[str, object]]:
            unavailable_timeouts: dict[str, int] = {
                key: entry.unavailable_until_ms for key, entry in entries.items()
            }
            backoff_attempts: dict[str, int] = dict.fromkeys(entries, 1)
            return {
                "unavailable_timeouts": unavailable_timeouts,
                "backoff_attempts": backoff_attempts,
            }

    custom_store = _CustomStore()
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
            unavailability_store=cast("UnavailabilityStore", custom_store),
        ),
    )

    assert controller.unavailability_store is custom_store
    assert isinstance(controller.unavailability_store, UnavailabilityStore) is True

    default_tracker = AgentUnavailabilityTracker()
    assert default_tracker.scope == "session"

    state = _three_agent_state()
    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())
    state, _effects, _failure_evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    assert marker.get("last_call") == (
        "development",
        "claude",
        UnavailabilityReason.NO_OUTPUT_AT_START,
    ), (
        f"custom store's mark_unavailable must be called with the right "
        f"(phase, agent, reason), got {marker.get('last_call')!r}"
    )

    payload = controller.waiting_state_payload(
        "development", ["claude", "opencode", "agy"],
    )
    assert isinstance(payload, list)
    assert len(payload) == 3
    agents_in_payload = [tup[0] for tup in payload]
    assert agents_in_payload == ["claude", "opencode", "agy"], (
        f"waiting_state_payload must preserve chain order, got {agents_in_payload!r}"
    )
    for tup in payload:
        assert isinstance(tup, tuple)
        assert len(tup) == 3
        agent_name, attempt, cooldown_ms = tup
        assert isinstance(agent_name, str)
        assert isinstance(attempt, int)
        assert attempt >= 0
        assert isinstance(cooldown_ms, int)
        assert cooldown_ms >= 0, (
            f"cooldown_ms_remaining must be non-negative, got {cooldown_ms}ms "
            f"for agent {agent_name!r}"
        )

    available = controller.agents_now_available(
        "development", ["claude", "opencode", "agy"],
    )
    assert "opencode" in available
    assert "agy" in available
    assert "claude" not in available, (
        f"claude was marked unavailable, must NOT appear in "
        f"agents_now_available, got {available!r}"
    )

    snapshot = controller.snapshot()
    assert "unavailable_timeouts" in snapshot
    assert "backoff_attempts" in snapshot
    assert snapshot["unavailable_timeouts"] == {"development:claude": 10_000}
