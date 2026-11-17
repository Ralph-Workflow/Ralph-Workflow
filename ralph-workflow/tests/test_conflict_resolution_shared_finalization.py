from __future__ import annotations

import dataclasses
from io import StringIO
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.agents.timeout_clock import FakeClock
from ralph.config.conflict_resolution_config import ConflictResolutionConfig
from ralph.display.context import make_display_context
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    ensure_session_controller,
    invoke_resolution_agent,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact, Cooldown, Spent
from ralph.recovery.agent_unavailability_tracker import (
    AgentUnavailabilityTracker,
    UnavailabilityEntry,
)
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.workspace.scope import WorkspaceScope
from tests._conflict_resolution_phase_parity_seams import (
    _config,
    _CooldownClock,
    _install_seams,
    _policy_bundle,
)
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "cooldown boundary test now drives full conflict resolution pipeline across non-identity boundaries": (
        "extended boundary test from a standalone ledger snapshot check to execute "
        "the real conflict resolution pipeline with mock boundaries, verifying cooldown "
        "enforcement end-to-end"
    ),
    "test_retained_continuation_and_ordinary_phase_share_one_controller_ledger": (
        "retained continuation must observe the same RecoveryController ledger cooldown "
        "as the ordinary phase, including when the controller is reconstructed process-locally "
        "from the ambient effect dependencies"
    ),
    "test_resolver_failure_records_on_run_shared_controller": (
        "a resolver failure injected through finalize_attempt on the run's shared "
        "RecoveryController must land in that ledger (Cooldown/Spent), not a session-private copy"
    ),
    "test_agent_cooled_by_another_phase_is_not_launched_by_resolver": (
        "an agent cooled via the shared tracker in another phase must not be launched by the "
        "resolver; refusal is owned by the shared effect-executor launch gate after the "
        "session adopts the run's RecoveryController"
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


def test_resolver_failure_records_on_run_shared_controller() -> None:
    clock = FakeClock()
    run_controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=AgentFailureLedger(clock=clock, consecutive_limit=1, total_limit=1),
            agent_max_consecutive_unproductive=1,
            agent_max_total_unproductive=1,
        )
    )
    private_controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            ledger=AgentFailureLedger(clock=clock, consecutive_limit=1, total_limit=1),
            agent_max_consecutive_unproductive=1,
            agent_max_total_unproductive=1,
        )
    )
    session = ResolutionSession(recovery_controller=private_controller)
    context = make_display_context(console=Console(file=StringIO(), force_terminal=False))
    pipeline_deps = dataclasses.replace(
        make_test_pipeline_deps(context, recovery_clock=clock),
        recovery_controller=run_controller,
        ledger=run_controller.ledger,
    )

    bound = ensure_session_controller(
        session,
        _policy_bundle(),
        pipeline_deps=pipeline_deps,
        config=_config(),
    )
    assert bound is run_controller
    assert session.recovery_controller is run_controller

    run_controller.finalize_attempt(
        PHASE_RESOLUTION,
        "resolver-shared",
        facts=AttemptFacts(artifact_valid=False),
        attempt_failed=True,
    )

    status = run_controller.agent_eligibility(PHASE_RESOLUTION, "resolver-shared")
    assert isinstance(status, (Cooldown, Spent))
    assert not status.is_eligible
    assert private_controller.agent_eligibility(PHASE_RESOLUTION, "resolver-shared").is_eligible


def test_agent_cooled_by_another_phase_is_not_launched_by_resolver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.pipeline.effect_executor import (
        install_agent_effect_dependencies,
        reset_agent_effect_dependencies,
    )

    del monkeypatch
    clock = FakeClock()
    store = AgentUnavailabilityTracker(clock=clock)
    store.mark_unavailable("development", "cooled-agent", UnavailabilityReason.OUT_OF_CREDITS)
    run_controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            unavailability_store=store,
            ledger=AgentFailureLedger(clock=clock, consecutive_limit=1, total_limit=1),
        )
    )
    session = ResolutionSession()
    context = make_display_context(console=Console(file=StringIO(), force_terminal=False))
    pipeline_deps = dataclasses.replace(
        make_test_pipeline_deps(context, recovery_clock=clock),
        recovery_controller=run_controller,
        unavailability_store=store,
        ledger=run_controller.ledger,
    )
    ensure_session_controller(
        session,
        _policy_bundle(),
        pipeline_deps=pipeline_deps,
        config=_config(),
    )

    executed: list[str] = []

    def _stub_runner(effect: object, *args: object, **kwargs: object) -> PipelineEvent:
        del args, kwargs
        if isinstance(effect, InvokeAgentEffect):
            executed.append(effect.agent_name)
        return PipelineEvent.AGENT_SUCCESS

    prompt = tmp_path / "prompt.md"
    prompt.write_text("resolve conflict", encoding="utf-8")
    token = install_agent_effect_dependencies(
        agent_runner=_stub_runner,
        recovery_controller=run_controller,
    )
    try:
        result = invoke_resolution_agent(
            agent_name="cooled-agent",
            prompt_path=prompt,
            config=_config(),
            pipeline_deps=pipeline_deps,
            workspace_scope=WorkspaceScope(tmp_path),
            policy_bundle=_policy_bundle(),
            display=None,
            display_context=context,
            session=session,
        )
    finally:
        reset_agent_effect_dependencies(token)

    assert result.event == PipelineEvent.AGENT_FAILURE
    assert executed == []
    assert not run_controller.agent_eligibility(PHASE_RESOLUTION, "cooled-agent").is_eligible
