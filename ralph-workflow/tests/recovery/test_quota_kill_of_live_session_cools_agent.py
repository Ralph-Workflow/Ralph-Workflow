from __future__ import annotations

from pathlib import Path

import pytest
from loguru import logger

from ralph.agents.invoke import QuotaExhaustedError
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.dead_agent_verdict import TypedSignal

pytestmark = pytest.mark.timeout_seconds(5)

PHASE = "development_commit_review"
CODEX = "codex/gpt-5.6-terra[effort=low]"
FALLBACK = "opencode/opencode-omniroute/zai/glm-5.3-flash"
CHAIN = [CODEX, FALLBACK]
RATE_LIMIT_EVENT = (
    '{"type":"error","message":"Reconnecting... 1/5 (exceeded retry limit, '
    'last status: 429 Too Many Requests, request id: 9fd4286a)"}'
)


def _controller(clock: FakeClock) -> RecoveryController:
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=load_policy(Path(__file__).parents[2] / "ralph" / "policy" / "defaults"),
            dead_agent_probe_runner=lambda _phase, _agent: None,
        )
    )
    controller.register_phase_chain(PHASE, CHAIN)
    return controller


def _live_session_quota_kill_facts() -> AttemptFacts:
    return AttemptFacts(
        event_kinds=("output_line", "error_line"),
        artifact_valid=False,
        elapsed_to_terminal_ms=568,
        liveness_observed=True,
        subagent_activity_observed=False,
        termination=TerminationRecord.create(
            TerminationIssuer.QUOTA_DETECTOR,
            observed_returncode=-15,
            reported_returncode=-15,
            liveness_observed=True,
        ),
        typed_signal=TypedSignal(name="QuotaExhaustedError", provider_originated=True),
    )


def _finalize_rate_limited_attempt(controller: RecoveryController, ordinal: int) -> None:
    facts = _live_session_quota_kill_facts()
    controller.finalize_attempt(
        PHASE,
        CODEX,
        facts=facts,
        attempt_failed=True,
        typed_signal=facts.typed_signal,
        exc=QuotaExhaustedError("codex", RATE_LIMIT_EVENT, returncode=-15),
        attempt_key=f"main_phase_loop:{PHASE}:{CODEX}:{ordinal}",
    )


def _cooldown_remaining_ms(controller: RecoveryController) -> int:
    return controller.earliest_available_wait_ms(PHASE, [CODEX])


def test_rate_limited_live_session_falls_over_on_the_next_fresh_selection() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)

    _finalize_rate_limited_attempt(controller, 1)
    clock.advance(1)

    assert controller.preferred_agent_index(PHASE, CHAIN).agent == FALLBACK
    assert _cooldown_remaining_ms(controller) == 59_000


def test_repeated_rate_limits_never_hand_the_cycle_back_to_the_limited_agent() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)
    selected: list[str | None] = []

    for ordinal in range(1, 6):
        _finalize_rate_limited_attempt(controller, ordinal)
        clock.advance(11)
        selected.append(controller.preferred_agent_index(PHASE, CHAIN).agent)

    assert selected == [FALLBACK] * 5


def test_handled_quota_failure_logs_one_rate_limit_cooldown() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)
    state = PipelineState(
        phase=PHASE,
        phase_chains={PHASE: AgentChainState(agents=CHAIN, current_index=0, retries=0)},
        last_connectivity_state="online",
    )
    logs: list[str] = []
    sink_id = logger.add(logs.append, level="INFO", format="{message}")
    try:
        controller.handle(
            state,
            QuotaExhaustedError(CODEX, RATE_LIMIT_EVENT, returncode=-15),
            FailureContext(phase=PHASE, agent=CODEX),
            facts=_live_session_quota_kill_facts(),
        )
    finally:
        logger.remove(sink_id)

    cooldown_lines = [line for line in logs if "cooldown_decision" in line]
    assert len(cooldown_lines) == 1
    assert "reason=out_of_credits" in cooldown_lines[0]
    assert _cooldown_remaining_ms(controller) == 60_000
    assert controller.preferred_agent_index(PHASE, CHAIN).agent == FALLBACK
