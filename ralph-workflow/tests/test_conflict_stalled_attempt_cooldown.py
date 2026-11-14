"""A stalled conflict-resolution attempt must hit the shared cooldown system."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    classify_failed_resolution_attempt,
)
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.policy.loader import load_policy
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus
from tests._conflict_resolution_phase_parity_seams import (
    _CONFLICTED,
    _config,
    _install_seams,
    _policy_bundle,
)

if TYPE_CHECKING:
    import pytest

_AGENTS = ("primary", "fallback")


def _controller() -> RecoveryController:
    with tempfile.TemporaryDirectory() as d:
        bundle = load_policy(Path(d) / ".agent")
    return RecoveryController(
        options=RecoveryControllerOptions(
            clock=FakeClock(start=0.0),
            policy_bundle=bundle,
            event_bus=FailureEventBus(),
        )
    )


def _stall(controller: RecoveryController, session: ResolutionSession, agent: str) -> None:
    classify_failed_resolution_attempt(
        session,
        agent,
        None,
        candidates=_AGENTS,
        failed_index=_AGENTS.index(agent),
        outcome=ConflictAttemptOutcome.STALLED,
        unresolved_count=9,
    )


def test_stalled_attempts_follow_the_shared_three_strike_cooldown() -> None:
    controller = _controller()
    session = ResolutionSession(recovery_controller=controller)
    limit = controller.ledger.consecutive_limit

    for _ in range(limit - 1):
        _stall(controller, session, "primary")
        assert controller.preferred_agent_index(PHASE_RESOLUTION, _AGENTS).agent == "primary"

    _stall(controller, session, "primary")

    selection = controller.preferred_agent_index(PHASE_RESOLUTION, _AGENTS)
    assert selection.agent == "fallback"
    assert controller.earliest_available_wait_ms(PHASE_RESOLUTION, ["primary"]) > 0


def test_stall_is_charged_even_when_the_invocation_already_reported_success() -> None:
    """Production shape: the agent run finalizes its own attempt as a clean exit.

    The driver then finds the conflicts untouched. That stall must still be
    charged to the shared ledger, or the same agent is re-invoked forever.
    """
    controller = _controller()
    session = ResolutionSession(recovery_controller=controller)
    limit = controller.ledger.consecutive_limit

    for ordinal in range(limit):
        controller.finalize_attempt(
            PHASE_RESOLUTION,
            "primary",
            facts=AttemptFacts(artifact_valid=True),
            attempt_failed=False,
            attempt_key=f"conflict-resolution:primary:{ordinal}",
        )
        _stall(controller, session, "primary")

    assert controller.preferred_agent_index(PHASE_RESOLUTION, _AGENTS).agent == "fallback"
    assert controller.earliest_available_wait_ms(PHASE_RESOLUTION, ["primary"]) > 0


def test_no_activity_attempts_follow_the_shared_three_strike_cooldown() -> None:
    controller = _controller()
    session = ResolutionSession(recovery_controller=controller)
    for _ in range(controller.ledger.consecutive_limit):
        classify_failed_resolution_attempt(
            session,
            "primary",
            None,
            candidates=_AGENTS,
            failed_index=0,
            outcome=ConflictAttemptOutcome.NO_ACTIVITY,
        )
    assert controller.preferred_agent_index(PHASE_RESOLUTION, _AGENTS).agent == "fallback"


def test_driver_never_reinvokes_a_stalled_agent_while_it_is_cooling_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two", "three")
    )
    clock = FakeClock()
    token = install_executors(clock=clock)
    try:
        _install_seams(monkeypatch, surviving_per_round=[_CONFLICTED] * 40)
        called: list[str] = []

        def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
            called.append(agent_name)
            return True

        assert (
            run_conflict_resolution_pipeline(
                root=tmp_path,
                target="main",
                config=_config(),
                pipeline_deps=None,
                workspace_scope=None,
                policy_bundle=_policy_bundle(),
                display=None,
                display_context=None,
                invoke=_invoke,
                clock=clock.monotonic,
                session=ResolutionSession(max_rounds_per_stop=3),
            )
            is False
        )
    finally:
        reset_executors(token)

    limit = 3
    for agent in set(called):
        assert called.count(agent) <= limit, f"cooling-down agent re-invoked: {called}"
    assert len(called) <= limit * 3
