from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.config.conflict_resolution_config import ConflictResolutionConfig
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact, Cooldown
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason
from tests._conflict_resolution_phase_parity_seams import (
    _config,
    _CooldownClock,
    _install_seams,
    _policy_bundle,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "restored_gated_e6e382c62_version": (
        "4959fb143 rewrote these checks for a conflict-recovery redesign that is reverted; the file retu"
        "rns to the gated e6e382c62 version, which pins the restored behaviour"
    ),
    "cooldown boundary test now drives full conflict resolution pipeline across non-identity boundaries": (
        "extended boundary test from a standalone ledger snapshot check to execute "
        "the real conflict resolution pipeline with mock boundaries, verifying cooldown "
        "enforcement end-to-end"
    ),
    "test_retained_continuation_and_ordinary_phase_share_one_controller_ledger": (
        "retained continuation must observe the same RecoveryController ledger cooldown "
        "as the ordinary phase, including when the controller is reconstructed process-locally "
        "from the ambient effect dependencies; it declares a 3 s per-test budget because "
        "its call alone takes 1.1-1.3 s when run by itself, so the 1 s default failed the "
        "publication gate on main under host load"
    ),
}


@pytest.mark.parametrize(
    ("issuer", "status"),
    (
        (TerminationIssuer.AGENT_EXIT, "exit:1"),
        (TerminationIssuer.AGENT_EXIT, "signal:15"),
        (TerminationIssuer.SUPERVISOR_IDLE, "watchdog_kill"),
        (TerminationIssuer.RECOVERY_TIMEOUT, "timeout"),
        (TerminationIssuer.UNKNOWN, "provider_failure"),
        (TerminationIssuer.UNKNOWN, "invalid_result"),
    ),
)
def test_failure_kinds_use_shared_finalization(issuer: TerminationIssuer, status: str) -> None:
    controller = RecoveryController(options=RecoveryControllerOptions(clock=FakeClock()))
    outcome = controller.finalize_attempt(
        PHASE_RESOLUTION,
        "resolver",
        facts=AttemptFacts(
            exit_status=status,
            termination=TerminationRecord.create(issuer),
        ),
        attempt_failed=True,
    )
    assert controller.last_attempt_outcome is outcome


@pytest.mark.parametrize(
    "boundary",
    ("restated_prompt", "rung", "strategy", "moved_target", "supervisor_restart"),
)
def test_cooldown_survives_non_identity_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    clock = _CooldownClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            unavailability_entries={
                "resolver-1": UnavailabilityEntry(
                    unavailable_until_ms=7000,
                    reason=UnavailabilityReason.PROCESS_EXITED,
                    attempt=1,
                    base_backoff_ms=5000,
                    max_backoff_ms=18000000,
                )
            },
        )
    )
    controller.ledger.record(
        AttemptFact(phase=PHASE_RESOLUTION, agent="resolver-1", artifact_valid=False)
    )

    assert not controller.agent_eligibility(PHASE_RESOLUTION, "resolver-1").is_eligible
    assert controller.earliest_available_wait_ms(PHASE_RESOLUTION, ["resolver-1"]) == 7000

    clock.advance(2.0)
    assert not controller.agent_eligibility(PHASE_RESOLUTION, "resolver-1").is_eligible
    assert controller.earliest_available_wait_ms(PHASE_RESOLUTION, ["resolver-1"]) == 5000

    target = "main"
    strategy_history: tuple[str, ...] = ()
    session_to_use = ResolutionSession(recovery_controller=controller)
    restated_called: list[Path] = []

    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("resolver-1",))

    if boundary == "restated_prompt":

        def custom_materializer(
            *,
            root: Path,
            target: str,
            conflicted_paths: Sequence[str],
            round_index: int,
            round_cap: int,
            session: ResolutionSession,
            stop: object,
            strategy_history: tuple[str, ...],
        ) -> Path | None:
            _ = (target, conflicted_paths, round_cap, session, stop, strategy_history)
            p = root / f"restated_prompt_{round_index}.txt"
            p.write_text("Restated problem prompt: please resolve cleanly.", encoding="utf-8")
            restated_called.append(p)
            return p

        monkeypatch.setattr(driver_module, "materialize_round_prompt", custom_materializer)

    elif boundary == "rung":
        strategy_history = ("rebase:rung-1", "rebase:rung-2")

    elif boundary == "strategy":
        strategy_history = ("rebase", "merge")

    elif boundary == "moved_target":
        target = "main-moved-target"

    elif boundary in ("supervisor_restart", "restart"):
        snapshot = controller.snapshot()
        restored_controller = RecoveryController.from_snapshot(snapshot, clock=clock)
        assert isinstance(
            restored_controller.agent_eligibility(PHASE_RESOLUTION, "resolver-1"), Cooldown
        )
        session_to_use = ResolutionSession(recovery_controller=restored_controller)

    called_at: list[float] = []

    def invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        _ = (agent_name, prompt_path, round_index)
        called_at.append(clock.monotonic())
        return True

    _install_seams(monkeypatch, surviving_per_round=[[]])
    token = install_executors(clock=clock)
    try:
        resolved = run_conflict_resolution_pipeline(
            root=tmp_path,
            target=target,
            config=_config(),
            pipeline_deps=None,
            workspace_scope=None,
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=None,
            invoke=invoke,
            session=session_to_use,
            strategy_history=strategy_history,
        )
    finally:
        reset_executors(token)

    assert resolved is True
    assert called_at == [7.0]
    assert clock.monotonic() == 7.0
    if boundary == "restated_prompt":
        assert len(restated_called) == 1
    assert session_to_use.recovery_controller is not None
    assert session_to_use.recovery_controller.agent_eligibility(
        PHASE_RESOLUTION, "resolver-1"
    ).is_eligible


def test_fall_over_then_wait_with_two_agents() -> None:
    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock, ledger=ledger))
    for agent in ("first", "second"):
        controller.finalize_attempt(
            PHASE_RESOLUTION,
            agent,
            facts=AttemptFacts(artifact_valid=False),
            attempt_failed=True,
        )
    assert controller.preferred_agent_index(PHASE_RESOLUTION, ("first", "second")).agent is None
    assert controller.earliest_available_wait_ms(PHASE_RESOLUTION, ("first", "second")) > 0


def test_resolver_config_has_no_active_resolution_cap() -> None:
    assert "total_resolution_cap_seconds" not in ConflictResolutionConfig.model_fields


@pytest.mark.parametrize(
    ("issuer", "status"),
    (
        (TerminationIssuer.AGENT_EXIT, "exit:1"),
        (TerminationIssuer.AGENT_EXIT, "signal:15"),
        (TerminationIssuer.SUPERVISOR_IDLE, "watchdog_kill"),
        (TerminationIssuer.RECOVERY_TIMEOUT, "timeout"),
        (TerminationIssuer.UNKNOWN, "provider_failure"),
        (TerminationIssuer.UNKNOWN, "invalid_result"),
    ),
)
def test_resolver_and_ordinary_phase_produce_identical_classification_and_lifecycle(
    issuer: TerminationIssuer, status: str
) -> None:
    clock_res = FakeClock(start=100.0)
    ledger_res = AgentFailureLedger(clock=clock_res, consecutive_limit=1)
    controller_res = RecoveryController(
        options=RecoveryControllerOptions(clock=clock_res, ledger=ledger_res)
    )

    clock_dev = FakeClock(start=100.0)
    ledger_dev = AgentFailureLedger(clock=clock_dev, consecutive_limit=1)
    controller_dev = RecoveryController(
        options=RecoveryControllerOptions(clock=clock_dev, ledger=ledger_dev)
    )

    facts = AttemptFacts(
        exit_status=status,
        termination=TerminationRecord.create(issuer),
    )

    outcome_res = controller_res.finalize_attempt(
        PHASE_RESOLUTION,
        "agent-1",
        facts=facts,
        attempt_failed=True,
    )
    outcome_dev = controller_dev.finalize_attempt(
        "development",
        "agent-1",
        facts=facts,
        attempt_failed=True,
    )

    assert outcome_res.kind == outcome_dev.kind
    assert outcome_res.reason == outcome_dev.reason

    eligibility_res = controller_res.agent_eligibility(PHASE_RESOLUTION, "agent-1")
    eligibility_dev = controller_dev.agent_eligibility("development", "agent-1")
    assert eligibility_res == eligibility_dev

    wait_res = controller_res.earliest_available_wait_ms(PHASE_RESOLUTION, ("agent-1",))
    wait_dev = controller_dev.earliest_available_wait_ms("development", ("agent-1",))
    assert wait_res == wait_dev
    assert wait_res > 0

    sel_res = controller_res.preferred_agent_index(PHASE_RESOLUTION, ("agent-1",))
    sel_dev = controller_dev.preferred_agent_index("development", ("agent-1",))
    assert sel_res.agent is None
    assert sel_dev.agent is None
    assert sel_res.index == sel_dev.index

    clock_res.advance(wait_res / 1000.0 + 1.0)
    clock_dev.advance(wait_dev / 1000.0 + 1.0)

    sel_after_res = controller_res.preferred_agent_index(PHASE_RESOLUTION, ("agent-1",))
    sel_after_dev = controller_dev.preferred_agent_index("development", ("agent-1",))
    assert sel_after_res.agent == "agent-1"
    assert sel_after_dev.agent == "agent-1"


def test_resolver_and_ordinary_phase_multi_candidate_selection_parity() -> None:
    clock_res = FakeClock(start=50.0)
    ledger_res = AgentFailureLedger(clock=clock_res, consecutive_limit=1)
    controller_res = RecoveryController(
        options=RecoveryControllerOptions(clock=clock_res, ledger=ledger_res)
    )

    clock_dev = FakeClock(start=50.0)
    ledger_dev = AgentFailureLedger(clock=clock_dev, consecutive_limit=1)
    controller_dev = RecoveryController(
        options=RecoveryControllerOptions(clock=clock_dev, ledger=ledger_dev)
    )

    candidates = ("agent-1", "agent-2", "agent-3")

    sel_res_init = controller_res.preferred_agent_index(PHASE_RESOLUTION, candidates)
    sel_dev_init = controller_dev.preferred_agent_index("development", candidates)
    assert sel_res_init.agent == "agent-1"
    assert sel_dev_init.agent == "agent-1"

    controller_res.finalize_attempt(
        PHASE_RESOLUTION,
        "agent-1",
        facts=AttemptFacts(artifact_valid=False),
        attempt_failed=True,
    )
    controller_dev.finalize_attempt(
        "development",
        "agent-1",
        facts=AttemptFacts(artifact_valid=False),
        attempt_failed=True,
    )

    sel_res_after_1 = controller_res.preferred_agent_index(PHASE_RESOLUTION, candidates)
    sel_dev_after_1 = controller_dev.preferred_agent_index("development", candidates)
    assert sel_res_after_1.agent == "agent-2"
    assert sel_dev_after_1.agent == "agent-2"

    controller_res.finalize_attempt(
        PHASE_RESOLUTION,
        "agent-2",
        facts=AttemptFacts(artifact_valid=False),
        attempt_failed=True,
    )
    controller_dev.finalize_attempt(
        "development",
        "agent-2",
        facts=AttemptFacts(artifact_valid=False),
        attempt_failed=True,
    )

    sel_res_after_2 = controller_res.preferred_agent_index(PHASE_RESOLUTION, candidates)
    sel_dev_after_2 = controller_dev.preferred_agent_index("development", candidates)
    assert sel_res_after_2.agent == "agent-3"
    assert sel_dev_after_2.agent == "agent-3"


def test_controller_has_no_secondary_conflict_classifier() -> None:
    assert not hasattr(RecoveryController, "classify_conflict_attempt")
    assert not hasattr(RecoveryController, "_typed_conflict_attempt_reason")


@pytest.mark.timeout_seconds(3)
def test_retained_continuation_and_ordinary_phase_share_one_controller_ledger(
    tmp_path: Path,
) -> None:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord, write_record
    from ralph.pipeline.auto_integrate_recovery_record import (
        compute_continuation_retry_gate_key,
        continue_retained_resolution,
    )
    from ralph.pipeline.effect_executor import (
        install_agent_effect_dependencies,
        reset_agent_effect_dependencies,
    )
    from ralph.pipeline.resolution_attempt_key import ResolutionAttemptKey
    from ralph.recovery.attempt_facts import AttemptFacts

    phase = "rebase_conflict_resolution"
    clock = FakeClock(start=10.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    shared = RecoveryController(options=RecoveryControllerOptions(clock=clock, ledger=ledger))
    shared.register_phase_chain(phase, ["resolver-1", "resolver-2"])

    shared.finalize_attempt(
        phase,
        "resolver-1",
        facts=AttemptFacts(artifact_valid=False),
        attempt_failed=True,
    )
    ordinary_eligible = tuple(
        agent
        for agent, status in shared.chain_eligibility(phase, ["resolver-1", "resolver-2"])
        if status.is_eligible
    )
    assert ordinary_eligible == ("resolver-2",)
    assert not shared.agent_eligibility(phase, "resolver-1").is_eligible

    cfg_dir = tmp_path / ".agent"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    git_dir = tmp_path / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "objects").mkdir(parents=True, exist_ok=True)
    (git_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (git_dir / "refs" / "heads" / "main").write_text("0" * 40 + "\n", encoding="utf-8")
    rebase_dir = git_dir / "rebase-merge"
    rebase_dir.mkdir(parents=True, exist_ok=True)
    (rebase_dir / "head-name").write_text("refs/heads/feature\n", encoding="utf-8")
    (rebase_dir / "orig-head").write_text("feat\n", encoding="utf-8")
    (rebase_dir / "onto").write_text("onto\n", encoding="utf-8")
    (rebase_dir / "stopped-sha").write_text("stop-shared\n", encoding="utf-8")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat",
        pre_target_sha="onto",
        resolving_rebase=True,
    )
    write_record(tmp_path, record)

    explicit_key = compute_continuation_retry_gate_key(
        tmp_path,
        record,
        recovery_controller=shared,
        phase=phase,
        stop_identity="stop-shared",
    )
    assert isinstance(explicit_key, ResolutionAttemptKey)
    assert explicit_key.eligible_agents == ordinary_eligible

    token = install_agent_effect_dependencies(recovery_controller=shared)
    try:
        ambient_key = compute_continuation_retry_gate_key(
            tmp_path,
            record,
            phase=phase,
            stop_identity="stop-shared",
        )
        blocked = continue_retained_resolution(
            tmp_path,
            record,
            None,
            None,
            None,
            phase=phase,
        )
    finally:
        reset_agent_effect_dependencies(token)

    assert isinstance(ambient_key, ResolutionAttemptKey)
    assert ambient_key.eligible_agents == ordinary_eligible
    assert ambient_key == explicit_key
    assert isinstance(blocked, str)
    assert "continuation resolver required" in blocked or "WAITING" in blocked
