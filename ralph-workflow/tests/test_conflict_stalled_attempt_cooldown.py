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


def test_stalled_attempt_is_classified_unavailable_and_budget_charged() -> None:
    failure = _controller().classify_conflict_attempt(
        None,
        agent="primary",
        outcome=ConflictAttemptOutcome.STALLED,
        unresolved_count=9,
    )
    assert failure.is_unavailable
    assert failure.unavailability_reason is not None
    assert failure.counts_against_budget


def test_no_activity_attempt_is_classified_unavailable_and_budget_charged() -> None:
    failure = _controller().classify_conflict_attempt(
        None, agent="primary", outcome=ConflictAttemptOutcome.NO_ACTIVITY
    )
    assert failure.is_unavailable
    assert failure.counts_against_budget


def test_attempt_that_made_progress_is_not_penalised() -> None:
    failure = _controller().classify_conflict_attempt(
        None,
        agent="primary",
        outcome=ConflictAttemptOutcome.UNRESOLVED_REMAINING,
        unresolved_count=3,
    )
    assert not failure.is_unavailable
    assert not failure.counts_against_budget


def test_stalled_attempt_benches_agent_and_selection_moves_on() -> None:
    controller = _controller()
    session = ResolutionSession(recovery_controller=controller)
    assert controller.preferred_agent_index(PHASE_RESOLUTION, _AGENTS).agent == "primary"

    classify_failed_resolution_attempt(
        session,
        "primary",
        None,
        candidates=_AGENTS,
        failed_index=0,
        outcome=ConflictAttemptOutcome.STALLED,
        unresolved_count=9,
    )

    selection = controller.preferred_agent_index(PHASE_RESOLUTION, _AGENTS)
    assert selection.agent == "fallback"
    assert any(name == "primary" for name, _reason in selection.skipped_reasons)


def test_sole_candidate_stalled_is_not_immediately_reselectable() -> None:
    controller = _controller()
    session = ResolutionSession(recovery_controller=controller)
    classify_failed_resolution_attempt(
        session,
        "primary",
        None,
        candidates=("primary",),
        failed_index=0,
        outcome=ConflictAttemptOutcome.STALLED,
        unresolved_count=9,
    )
    assert controller.preferred_agent_index(PHASE_RESOLUTION, ("primary",)).agent is None
    assert controller.earliest_available_wait_ms(PHASE_RESOLUTION, ["primary"]) > 0


def test_driver_never_reinvokes_a_stalled_agent_while_it_is_cooling_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two", "three")
    )
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
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

    assert len(called) == len(set(called)), f"cooling-down agent re-invoked: {called}"
