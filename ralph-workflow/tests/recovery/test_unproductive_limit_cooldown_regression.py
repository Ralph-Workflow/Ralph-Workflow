from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.agent_failure_ledger import Cooldown
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.timeout_defaults import AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT

PHASE = "development_commit_review"
MINIMAX = "opencode/minimax-coding-plan/MiniMax-M3"
CODEX = "codex"
CHAIN = (MINIMAX, CODEX)
OUT_OF_CREDITS_ESCALATIONS = 12


def _controller(clock: FakeClock) -> RecoveryController:
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock))
    controller.register_phase_chain(PHASE, CHAIN)
    return controller


def _cooldown(controller: RecoveryController, agent: str) -> Cooldown:
    status = controller.agent_eligibility(PHASE, agent)
    assert isinstance(status, Cooldown), status
    return status


def test_an_agent_past_the_total_unproductive_limit_is_retried_after_its_own_cooldown() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)
    for _ in range(OUT_OF_CREDITS_ESCALATIONS):
        controller.record_confirmed_unavailability(
            PHASE, CODEX, UnavailabilityReason.OUT_OF_CREDITS
        )
    codex = _cooldown(controller, CODEX)

    for _ in range(AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT):
        controller.record_attempt_fact(PHASE, MINIMAX, artifact_valid=False, workspace_changed=True)

    minimax = _cooldown(controller, MINIMAX)
    assert minimax.until_ms < codex.until_ms
    now_ms = int(clock.monotonic() * 1000)
    assert controller.earliest_eligible_wait_ms(PHASE, CHAIN) == minimax.until_ms - now_ms

    clock.advance((minimax.until_ms - now_ms) / 1000.0)

    assert controller.preferred_agent_index(PHASE, CHAIN).agent == MINIMAX
    assert not controller.agent_eligibility(PHASE, CODEX).is_eligible


def test_the_unproductive_limit_cooldown_names_its_reason_and_is_shorter_than_out_of_credits() -> (
    None
):
    clock = FakeClock(start=0.0)
    controller = _controller(clock)

    for _ in range(AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT):
        controller.record_attempt_fact(PHASE, MINIMAX, artifact_valid=False, workspace_changed=True)

    minimax = _cooldown(controller, MINIMAX)
    assert minimax.reason == str(UnavailabilityReason.UNPRODUCTIVE_LIMIT)
    assert controller.preferred_agent_index(PHASE, CHAIN).agent == CODEX
