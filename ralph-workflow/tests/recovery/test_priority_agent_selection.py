from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _no_output_opts() -> InactivityTimeoutOpts:
    return InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_OUTPUT_AT_START,
        diagnostic={"invocation_elapsed": 30.0},
    )


def _three_agent_state(current_index: int = 1) -> PipelineState:
    chain_state = AgentChainState(
        agents=["claude", "opencode", "agy"],
        current_index=current_index,
        retries=0,
    )
    return PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")


def test_return_to_preferred_agent_after_cooldown_expiry() -> None:
    clock = FakeClock(start=0.0)
    initial_entries = {
        "claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
    }
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            unavailability_entries=initial_entries,
        )
    )
    state = _three_agent_state(current_index=1)
    clock.advance(6.0)

    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    new_state, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="opencode"),
    )
    chain = new_state.chain_for_phase(new_state.phase)
    assert chain is not None
    assert chain.current_index == 0


def test_priority_beats_proximity() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    new_state, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="opencode"),
    )
    chain = new_state.chain_for_phase(new_state.phase)
    assert chain is not None
    assert chain.current_index == 0


def test_priority_beats_same_agent_retry() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    new_state, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="opencode"),
    )
    chain = new_state.chain_for_phase(new_state.phase)
    assert chain is not None
    assert chain.current_index == 0


def test_cooldown_is_never_picked() -> None:
    clock = FakeClock(start=0.0)
    initial_entries = {
        "claude": UnavailabilityEntry(
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
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            unavailability_entries=initial_entries,
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    new_state, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="opencode"),
    )
    chain = new_state.chain_for_phase(new_state.phase)
    assert chain is not None
    assert chain.current_index != 0


def test_all_in_cooldown_still_waits() -> None:
    clock = FakeClock(start=0.0)
    initial_entries = {
        "claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
        "opencode": UnavailabilityEntry(
            unavailable_until_ms=8000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=8000,
            max_backoff_ms=8000,
        ),
        "agy": UnavailabilityEntry(
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
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            unavailability_entries=initial_entries,
        )
    )
    state = _three_agent_state(current_index=2)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("agy", 30.0, opts=opts)
    new_state, effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="agy"),
    )
    assert new_state.is_waiting_state is True
    assert new_state.last_retry_delay_ms == 5000
    assert new_state.phase == "development"
    assert effects == []


def test_transcript_visibility() -> None:
    clock = FakeClock(start=0.0)
    initial_entries = {
        "claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
    }
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            unavailability_entries=initial_entries,
        )
    )
    state = _three_agent_state(current_index=1)
    logs: list[str] = []

    def sink(msg: Any) -> None:
        logs.append(str(msg))

    sink_id = logger.add(sink, level="INFO", format="{message}")
    try:
        opts = _no_output_opts()
        exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
        _new_state, _effects, _evt = controller.handle(
            state,
            exc,
            FailureContext(phase="development", agent="opencode"),
        )
    finally:
        logger.remove(sink_id)

    assert any("Selected agent" in line for line in logs)
    assert any("cooldown (5000ms remaining, reason=no_output_at_start)" in line for line in logs)


def test_non_unavailable_failures_re_prefer() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _three_agent_state(current_index=1)
    exc = RuntimeError("generic failure")
    new_state, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="opencode"),
    )
    chain = new_state.chain_for_phase(new_state.phase)
    assert chain is not None
    assert chain.current_index == 0


def test_two_consecutive_invocations_keep_priority_selection() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    ctx = FailureContext(phase="development", agent="opencode")

    state_after_first = controller.handle(state, exc, ctx)[0]
    chain = state_after_first.chain_for_phase(state_after_first.phase)
    assert chain is not None
    assert chain.current_index == 0
    assert chain.agents[chain.current_index] == "claude"

    state_after_second = controller.handle(state_after_first, exc, ctx)[0]
    chain2 = state_after_second.chain_for_phase(state_after_second.phase)
    assert chain2 is not None
    assert chain2.current_index == 0
    assert chain2.agents[chain2.current_index] == "claude"


def test_cooldown_then_expiry_restores_highest_priority_agent() -> None:
    clock = FakeClock(start=0.0)
    initial_entries = {
        "claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
    }
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            unavailability_entries=initial_entries,
        )
    )
    state = _three_agent_state(current_index=1)
    exc = RuntimeError("generic failure")
    ctx = FailureContext(phase="development", agent="opencode")

    state_after_first = controller.handle(state, exc, ctx)[0]
    chain = state_after_first.chain_for_phase(state_after_first.phase)
    assert chain is not None
    assert chain.current_index == 1
    assert chain.agents[chain.current_index] == "opencode"

    clock.advance(6.0)
    state_after_expiry = controller.handle(state_after_first, exc, ctx)[0]
    chain2 = state_after_expiry.chain_for_phase(state_after_expiry.phase)
    assert chain2 is not None
    assert chain2.current_index == 0
    assert chain2.agents[chain2.current_index] == "claude"


def test_successful_preferred_agent_remains_preferred_on_later_invocations() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    ctx = FailureContext(phase="development", agent="opencode")

    state_after_first = controller.handle(state, exc, ctx)[0]
    chain = state_after_first.chain_for_phase(state_after_first.phase)
    assert chain is not None
    assert chain.current_index == 0

    controller.reset_backoff("development", "claude")

    state_after_second = controller.handle(state_after_first, exc, ctx)[0]
    chain2 = state_after_second.chain_for_phase(state_after_second.phase)
    assert chain2 is not None
    assert chain2.current_index == 0
    assert chain2.agents[chain2.current_index] == "claude"


def test_invocation_boundary_skips_exhausted_agent_returns_to_preferred() -> None:
    clock = FakeClock(start=0.0)
    initial_entries = {
        "opencode": UnavailabilityEntry(
            unavailable_until_ms=4000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=4000,
            max_backoff_ms=4000,
        ),
        "agy": UnavailabilityEntry(
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
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            unavailability_entries=initial_entries,
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    ctx = FailureContext(phase="development", agent="opencode")

    state_after_first = controller.handle(state, exc, ctx)[0]
    chain = state_after_first.chain_for_phase(state_after_first.phase)
    assert chain is not None
    assert chain.current_index == 0
    assert chain.agents[chain.current_index] == "claude"

    clock.advance(5.0)
    state_after_second = controller.handle(state_after_first, exc, ctx)[0]
    chain2 = state_after_second.chain_for_phase(state_after_second.phase)
    assert chain2 is not None
    assert chain2.current_index == 0
    assert chain2.agents[chain2.current_index] == "claude"


def test_priority_first_selection_consecutive_invocations_always_return_highest_priority() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=20,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )

    for _ in range(10):
        selection = controller.preferred_agent_index("development", ["claude", "opencode", "agy"])
        assert selection.index == 0
        assert selection.agent == "claude"
        assert selection.skipped_reasons == (
            ("opencode", "lower_priority"),
            ("agy", "lower_priority"),
        )


def test_priority_first_selection_returns_to_highest_priority_after_cooldown_expiry() -> None:
    clock = FakeClock(start=0.0)
    initial_entries = {
        "claude": UnavailabilityEntry(
            unavailable_until_ms=4000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=4000,
            max_backoff_ms=4000,
        ),
    }
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=20,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            unavailability_entries=initial_entries,
        )
    )

    selection_first = controller.preferred_agent_index("development", ["claude", "opencode", "agy"])
    assert selection_first.agent == "opencode"
    assert selection_first.skipped_reasons[0][0] == "claude"
    assert selection_first.skipped_reasons[0][1].startswith("cooldown (")
    assert "no_output_at_start" in selection_first.skipped_reasons[0][1]

    clock.advance(5.0)

    selection_second = controller.preferred_agent_index(
        "development", ["claude", "opencode", "agy"]
    )
    assert selection_second.agent == "claude"
    assert selection_second.index == 0


def test_priority_first_selection_keeps_successful_preferred_agent_across_repeated_failures() -> (
    None
):
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=20,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    ctx = FailureContext(phase="development", agent="opencode")

    state_after_first = controller.handle(state, exc, ctx)[0]
    chain = state_after_first.chain_for_phase(state_after_first.phase)
    assert chain is not None
    assert chain.current_index == 0

    controller.reset_backoff("development", "claude")

    state_after_second = controller.handle(state_after_first, exc, ctx)[0]
    chain2 = state_after_second.chain_for_phase(state_after_second.phase)
    assert chain2 is not None
    assert chain2.current_index == 0
    assert chain2.agents[chain2.current_index] == "claude"

    state_after_third = controller.handle(state_after_second, exc, ctx)[0]
    chain3 = state_after_third.chain_for_phase(state_after_third.phase)
    assert chain3 is not None
    assert chain3.current_index == 0
    assert chain3.agents[chain3.current_index] == "claude"


def test_priority_first_selection_persisted_current_index_does_not_introduce_round_robin() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _three_agent_state(current_index=2)
    chain = state.chain_for_phase(state.phase)
    assert chain is not None
    selection = controller.preferred_agent_index(
        state.phase,
        chain.agents,
        current_index=chain.current_index,
    )
    assert selection.index == 0
    assert selection.agent == "claude"


def test_priority_first_selection_spent_at_cursor_picks_highest_priority_unspent() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    ctx = FailureContext(phase="development", agent="opencode")

    state_after = controller.handle(state, exc, ctx)[0]
    chain = state_after.chain_for_phase(state_after.phase)
    assert chain is not None
    assert chain.current_index == 0
    assert chain.agents[chain.current_index] == "claude"


def test_priority_first_selection_picks_only_one_agent_per_call() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    agents = ["claude", "opencode", "agy", "codex", "cursor/auto"]
    for _ in range(20):
        selection = controller.preferred_agent_index("development", agents)
        assert selection.index is not None
        assert selection.index == 0
        assert selection.agent == agents[0]
        assert len(selection.skipped_reasons) == len(agents) - 1


def test_successful_preferred_agent_remains_highest_priority_after_reset() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    ctx = FailureContext(phase="development", agent="opencode")

    state_after_first = controller.handle(state, exc, ctx)[0]
    chain = state_after_first.chain_for_phase(state_after_first.phase)
    assert chain is not None
    assert chain.current_index == 0

    controller.reset_backoff("development", "claude")

    snap = controller.snapshot()
    assert "claude" not in snap["unavailable_timeouts"]
    assert "claude" not in snap["backoff_attempts"]

    state_after_second = controller.handle(state_after_first, exc, ctx)[0]
    chain2 = state_after_second.chain_for_phase(state_after_second.phase)
    assert chain2 is not None
    assert chain2.current_index == 0
    assert chain2.agents[chain2.current_index] == "claude"


def test_success_reset_isolated_other_agents_unchanged() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )

    for i in range(3):
        controller.unavailability_store.mark_unavailable(
            "development", "claude", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        if i < 2:
            clock.advance(60_000)

    clock.advance(60_000)

    for i in range(5):
        controller.unavailability_store.mark_unavailable(
            "development", "opencode", UnavailabilityReason.NO_OUTPUT_AT_START
        )
        if i < 4:
            clock.advance(60_000)

    snap_pre = controller.snapshot()
    assert snap_pre["backoff_attempts"]["claude"] == 3
    assert snap_pre["backoff_attempts"]["opencode"] == 5

    controller.reset_backoff("development", "claude")

    snap_post = controller.snapshot()
    assert "claude" not in snap_post["unavailable_timeouts"]
    assert "claude" not in snap_post["backoff_attempts"]
    assert "opencode" in snap_post["unavailable_timeouts"]
    assert snap_post["backoff_attempts"]["opencode"] == 5
