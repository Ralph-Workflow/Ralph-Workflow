from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.chain_candidates import remember_dead_surface
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_failure_ledger import (
    AgentFailureLedger,
    AttemptFact,
    Cooldown,
    Eligible,
    Spent,
)
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus
from ralph.recovery.unavailability_reason import UnavailabilityReason

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle

_CONFLICTED = ["a.py", "b.py"]
_CONFIG = UnifiedConfig.model_validate({"general": {}})


def _minimal_policy_bundle() -> PolicyBundle:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _install_conflict_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.git.conflict_stage_entries import ConflictStageEntries
    from ralph.pipeline.conflict_resolution import sight as sight_module

    monkeypatch.setattr(driver_module, "unmerged_paths", lambda _root: list(_CONFLICTED))
    monkeypatch.setattr(
        driver_module,
        "paths_with_conflict_markers",
        lambda _root, _paths: list(_CONFLICTED),
    )
    monkeypatch.setattr(
        driver_module,
        "resolution_chain_agents",
        lambda _bundle: ("primary", "fallback"),
    )
    monkeypatch.setattr(
        driver_module,
        "render_conflict_prompt",
        lambda *, root, **_kwargs: root / "resolution-prompt.md",
    )
    monkeypatch.setattr(
        sight_module,
        "conflict_stage_entries",
        lambda _root, _paths: ConflictStageEntries({}),
    )


def _run_mixed_spent_and_demoted_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    cooldown_elapsed: bool,
) -> tuple[RecoveryController, ResolutionSession]:
    _install_conflict_seams(monkeypatch)
    clock = FakeClock(start=0.0)
    policy_bundle = _minimal_policy_bundle()
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=policy_bundle,
            event_bus=FailureEventBus(),
        )
    )
    controller.note_retry_exhaustion(PHASE_RESOLUTION, "primary")
    controller.unavailability_store.mark_unavailable(
        PHASE_RESOLUTION, "fallback", UnavailabilityReason.NO_OUTPUT_AT_START
    )
    if cooldown_elapsed:
        clock.advance(60.0)

    session = ResolutionSession(recovery_controller=controller)
    session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    remember_dead_surface(session, "fallback")
    session.rounds_spent = 1

    success = run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=_CONFIG,
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=policy_bundle,
        display=None,
        display_context=None,
        session=session,
        strategy_history=("previous-strategy",),
    )

    assert success is False
    return controller, session


def test_earliest_wait_ms_empty_agents_returns_zero() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock)

    assert ledger.earliest_wait_ms("development", []) == 0
    assert ledger.earliest_wait_ms("development", ()) == 0


def test_earliest_wait_ms_eligible_agents_returns_zero() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock)

    assert ledger.earliest_wait_ms("development", ["agent-a"]) == 0
    assert ledger.earliest_wait_ms("development", ["agent-a", "agent-b"]) == 0


def test_earliest_wait_ms_partially_eligible_returns_zero() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=2, total_limit=4)

    ledger.record(AttemptFact(phase="development", agent="agent-a", artifact_valid=False))
    outcome_a = ledger.record(
        AttemptFact(phase="development", agent="agent-a", artifact_valid=False)
    )
    assert isinstance(outcome_a.status, Cooldown)

    assert ledger.earliest_wait_ms("development", ["agent-a", "agent-b"]) == 0

    for _ in range(4):
        ledger.record(
            AttemptFact(
                phase="development",
                agent="agent-c",
                artifact_valid=False,
                workspace_changed=True,
            )
        )
    assert isinstance(ledger.status("development", "agent-c"), Spent)

    assert ledger.earliest_wait_ms("development", ["agent-a", "agent-b", "agent-c"]) == 0


def test_earliest_wait_ms_cooldown_returns_positive_int() -> None:
    clock = FakeClock(start=10.0)
    ledger = AgentFailureLedger(
        clock=clock,
        consecutive_limit=2,
        base_cooldown_seconds=30.0,
    )

    ledger.record(AttemptFact(phase="development", agent="agent-a", artifact_valid=False))
    ledger.record(AttemptFact(phase="development", agent="agent-a", artifact_valid=False))

    wait_a = ledger.earliest_wait_ms("development", ["agent-a"])
    assert wait_a == 30_000
    assert isinstance(wait_a, int)
    assert wait_a > 0

    clock.advance(5.0)
    ledger.record(AttemptFact(phase="development", agent="agent-b", artifact_valid=False))
    ledger.record(AttemptFact(phase="development", agent="agent-b", artifact_valid=False))

    assert ledger.earliest_wait_ms("development", ["agent-a", "agent-b"]) == 25_000


def test_earliest_wait_ms_mixed_cooldown_and_spent_returns_positive_int() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=2, total_limit=3)

    for _ in range(3):
        ledger.record(
            AttemptFact(
                phase="development",
                agent="agent-a",
                artifact_valid=False,
                workspace_changed=True,
            )
        )
    assert isinstance(ledger.status("development", "agent-a"), Spent)

    ledger.record(AttemptFact(phase="development", agent="agent-b", artifact_valid=False))
    ledger.record(AttemptFact(phase="development", agent="agent-b", artifact_valid=False))
    assert isinstance(ledger.status("development", "agent-b"), Cooldown)

    wait = ledger.earliest_wait_ms("development", ["agent-a", "agent-b"])
    assert wait == 60_000
    assert isinstance(wait, int)
    assert wait > 0


def test_earliest_wait_ms_expired_cooldown_returns_zero() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=2, base_cooldown_seconds=10.0)

    ledger.record(AttemptFact(phase="development", agent="agent-a", artifact_valid=False))
    ledger.record(AttemptFact(phase="development", agent="agent-a", artifact_valid=False))
    assert isinstance(ledger.status("development", "agent-a"), Cooldown)

    clock.advance(10.0)
    assert ledger.earliest_wait_ms("development", ["agent-a"]) == 0
    assert isinstance(ledger.status("development", "agent-a"), Eligible)


def test_earliest_wait_ms_single_spent_agent_returns_none() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, total_limit=2)

    ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-a",
            artifact_valid=False,
            workspace_changed=True,
        )
    )
    ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-a",
            artifact_valid=False,
            workspace_changed=True,
        )
    )
    assert isinstance(ledger.status("development", "agent-a"), Spent)
    assert ledger.earliest_wait_ms("development", ["agent-a"]) is None


def test_earliest_wait_ms_all_spent_multiple_agents_returns_none() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, total_limit=2)

    for agent in ("agent-a", "agent-b", "agent-c"):
        for _ in range(2):
            ledger.record(
                AttemptFact(
                    phase="development",
                    agent=agent,
                    artifact_valid=False,
                    workspace_changed=True,
                )
            )
        assert isinstance(ledger.status("development", agent), Spent)

    assert ledger.earliest_wait_ms("development", ["agent-a", "agent-b", "agent-c"]) is None
    assert ledger.earliest_wait_ms("development", ("agent-a", "agent-b", "agent-c")) is None


def test_controller_earliest_available_wait_ms_with_all_spent_ledger() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, total_limit=1)
    ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-a",
            artifact_valid=False,
            workspace_changed=True,
        )
    )
    assert ledger.earliest_wait_ms("development", ["agent-a"]) is None

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=ledger,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
        ),
    )

    wait_ms = controller.earliest_available_wait_ms("development", ["agent-a"])
    assert wait_ms == 0
    assert isinstance(wait_ms, int)


def test_controller_earliest_available_wait_ms_with_cooldown_ledger() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1, base_cooldown_seconds=30.0)
    ledger.record(AttemptFact(phase="development", agent="agent-a", artifact_valid=False))
    assert ledger.earliest_wait_ms("development", ["agent-a"]) == 30_000

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=ledger,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
        ),
    )

    wait_ms = controller.earliest_available_wait_ms("development", ["agent-a"])
    assert wait_ms == 30_000


def test_controller_earliest_available_wait_ms_takes_min_of_tracker_and_ledger() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1, base_cooldown_seconds=45.0)
    ledger.record(AttemptFact(phase="development", agent="agent-a", artifact_valid=False))
    clock.advance(15.0)
    ledger.record(AttemptFact(phase="development", agent="agent-b", artifact_valid=False))
    assert ledger.earliest_wait_ms("development", ["agent-a", "agent-b"]) == 30_000

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=ledger,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
            unavailable_timeouts={"agent-a": 50_000, "agent-b": 35_000},
        ),
    )

    assert controller.earliest_available_wait_ms("development", ["agent-a", "agent-b"]) == 20_000


def test_controller_all_exhausted_when_all_agents_spent() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, total_limit=1)
    ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-a",
            artifact_valid=False,
            workspace_changed=True,
        )
    )
    ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-b",
            artifact_valid=False,
            workspace_changed=True,
        )
    )

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=ledger,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
        ),
    )

    assert controller.all_exhausted("development", ["agent-a", "agent-b"]) is True


def test_controller_handle_all_spent_does_not_wait() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1, total_limit=1)

    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=ledger,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
        ),
    )

    chain_state = AgentChainState(
        agents=["agent-a", "agent-b"],
        current_index=1,
        retries=0,
    )
    ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-a",
            artifact_valid=False,
            workspace_changed=True,
        )
    )
    assert isinstance(ledger.status("development", "agent-a"), Spent)

    state = PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")

    context = FailureContext(phase="development", agent="agent-b")
    new_state, _effects, _evt = controller.handle(
        state,
        raw_failure="agent-b failed",
        context=context,
        workspace_changed=True,
        artifact_valid=False,
    )

    assert ledger.earliest_wait_ms("development", ["agent-a", "agent-b"]) is None
    assert not new_state.is_waiting_state
    assert new_state.last_retry_delay_ms == 0
    assert new_state.phase == "failed_terminal" or "failed" in new_state.phase


def test_controller_earliest_eligible_wait_ms_reports_no_finite_wait_for_spent_chain() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, total_limit=1)
    ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-a",
            artifact_valid=False,
            workspace_changed=True,
        )
    )
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=ledger,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
        ),
    )

    assert controller.earliest_eligible_wait_ms("development", ["agent-a"]) is None
    assert controller.earliest_available_wait_ms("development", ["agent-a"]) == 0


def test_controller_earliest_eligible_wait_ms_separates_eligible_now_from_no_finite_wait() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
        ),
    )
    controller.note_retry_exhaustion("development", "spent-agent")
    controller.unavailability_store.mark_unavailable(
        "development", "cooled-agent", UnavailabilityReason.NO_OUTPUT_AT_START
    )

    assert controller.earliest_eligible_wait_ms("development", ["spent-agent"]) is None
    assert controller.earliest_eligible_wait_ms("development", ["cooled-agent"]) == 5000
    assert (
        controller.earliest_eligible_wait_ms("development", ["spent-agent", "cooled-agent"]) == 5000
    )

    clock.advance(60.0)

    assert controller.earliest_eligible_wait_ms("development", ["spent-agent", "cooled-agent"]) == 0


@pytest.mark.timeout_seconds(10)
def test_mixed_spent_and_demoted_chain_without_finite_wait_is_not_tool_surface_dead(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    controller, session = _run_mixed_spent_and_demoted_chain(
        monkeypatch,
        tmp_path,
        cooldown_elapsed=True,
    )

    assert controller.earliest_available_wait_ms(PHASE_RESOLUTION, ["primary", "fallback"]) == 0
    assert controller.all_exhausted(PHASE_RESOLUTION, ["primary", "fallback"]) is False
    assert session.terminal_reason is ResolutionTerminationReason.CANDIDATE_EXHAUSTED
    assert session.terminal_reason is not ResolutionTerminationReason.TOOL_SURFACE_DEAD

    message = session.operator_action_message
    assert message is not None
    assert "primary: spent" in message
    assert "fallback: no_output_at_start" in message
    assert "needs operator action: git add -- a.py b.py" in message


@pytest.mark.timeout_seconds(10)
def test_barred_candidate_cooldown_does_not_mask_an_exhausted_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _controller, session = _run_mixed_spent_and_demoted_chain(
        monkeypatch,
        tmp_path,
        cooldown_elapsed=False,
    )

    assert session.terminal_reason is ResolutionTerminationReason.CANDIDATE_EXHAUSTED
    assert session.terminal_reason is not ResolutionTerminationReason.TOOL_SURFACE_DEAD

    message = session.operator_action_message
    assert message is not None
    assert "primary: spent" in message
    assert "fallback: no_output_at_start" in message
    assert "needs operator action: git add -- a.py b.py" in message
