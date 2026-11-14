from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.auto_integrate_transition import DIAGNOSTIC_BUDGET_SPENT_REASON
from ralph.pipeline.integration_attempt_ledger import (
    LAST_RESORT_WAIT_KEY,
    AttemptLedger,
)
from ralph.pipeline.integration_decision import CleanupLadderRung
from ralph.pipeline.integration_diagnostic import (
    COMPLETION_IN_PLACE_HANDOFF_KEY,
    DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    PLANNING_HANDOFF_FILENAME,
    budget_spent_record,
    check_all_last_resort_conditions,
    check_last_resort_agent_eligibility,
    check_last_resort_preconditions,
    check_target_worktree_context,
    completion_in_place_handoff_record,
    create_planning_handoff_record,
    ensure_last_resort_preservation,
    escalate_record_fact_outcome,
    is_disabled_signature_step,
    is_last_resort_disabled_for_caller,
    ladder_terminal_state,
    next_resolver,
    observe_workspace_change,
    read_planning_handoff_record,
)
from ralph.pipeline.integration_ladder import (
    LAST_RESORT_DISABLED_NO_SIGNATURE,
    LAST_RESORT_PRECONDITIONS,
    LAST_RESORT_TERMINAL_NO_SIGNATURE,
    LAST_RESORT_WAIT_SECONDS,
    LadderStepResult,
)
from ralph.pipeline.integration_owner import RecordFactOutcome
from ralph.pipeline.last_resort_agent_pool import LastResortAgentPool
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact
from ralph.recovery.controller import RecoveryController
from tests._integration_ladder_fakes import (
    _SIG,
    _FakePlumbing,
    _ledger,
    _seed_budget,
    _seed_salvage,
    _seed_terminal_rungs,
)

pytestmark = pytest.mark.timeout_seconds(30)


class ScenarioClock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self._now = now

    def now(self) -> float:
        return self._now

    def now_utc(self) -> datetime:
        return datetime.fromtimestamp(self._now, tz=UTC)

    def epoch(self) -> float:
        return self._now

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def sleep(self, seconds: float) -> None:
        self._now += seconds

    def wait_for_event(self, event: threading.Event, seconds: float) -> bool:
        del event, seconds
        return False


def _record(**overrides: object) -> IntegrationRecord:
    base: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
    }
    base.update(overrides)
    return IntegrationRecord.model_validate(base)


def _seed_all_preconditions(ledger: AttemptLedger) -> None:
    _seed_terminal_rungs(ledger)
    _seed_salvage(ledger)
    _seed_budget(ledger)


def test_public_functions_pinned(tmp_path: Path) -> None:
    rec = _record(merge_commit_repair_attempts=1)
    stamped = budget_spent_record(rec)
    assert stamped.merge_commit_repair_attempts == 2
    assert stamped.repair_cap_reason == DIAGNOSTIC_BUDGET_SPENT_REASON

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    run_git(["init", "-b", "main"], cwd=repo_dir, label="test-init")
    assert check_target_worktree_context(repo_dir) is None

    controller = RecoveryController(ledger=AgentFailureLedger(clock=ScenarioClock()))
    observed: list[str] = []
    observe_workspace_change(controller, repo_dir, lambda: observed.append("ran"))
    assert observed == ["ran"]

    step = LadderStepResult(
        CleanupLadderRung.LAST_RESORT, "terminal", LAST_RESORT_TERMINAL_NO_SIGNATURE
    )
    assert is_disabled_signature_step(step) is True
    step_disabled = LadderStepResult(
        CleanupLadderRung.LAST_RESORT, "failed", LAST_RESORT_DISABLED_NO_SIGNATURE
    )
    assert is_disabled_signature_step(step_disabled) is True
    step_other = LadderStepResult(CleanupLadderRung.NORMAL_CLEANUP, "failed", "other")
    assert is_disabled_signature_step(step_other) is False

    assert ladder_terminal_state(repo_dir, rec) is None
    assert next_resolver(controller, ("agent1", "agent2")) is not None


def test_preconditions_checklist_all_conditions(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    initial_checks = check_last_resort_preconditions(
        ledger.read(),
        decided_signature=_SIG,
        fresh_signature=_SIG,
    )
    assert tuple(initial_checks) == LAST_RESORT_PRECONDITIONS
    assert initial_checks["rungs_1_to_5_tried_or_shown_impossible"].startswith("unmet")
    assert initial_checks["salvage_resolver_agent_tried"].startswith("unmet")
    assert initial_checks["salvage_rebase_first_ladder_retried"].startswith("unmet")
    assert initial_checks["salvage_fresh_worktree_rebuild_tried"].startswith("unmet")
    assert initial_checks["ladder_attempt_budget_spent"].startswith("unmet")
    assert initial_checks["fresh_reobservation_confirms_blocking_state"].startswith("met")

    _seed_all_preconditions(ledger)
    seeded_checks = check_last_resort_preconditions(
        ledger.read(),
        decided_signature=_SIG,
        fresh_signature=_SIG,
    )
    for name in LAST_RESORT_PRECONDITIONS:
        assert seeded_checks[name].startswith("met"), name

    healed_checks = check_last_resort_preconditions(
        ledger.read(),
        decided_signature=_SIG,
        fresh_signature="clean|clean|none|none|attached",
    )
    assert healed_checks["fresh_reobservation_confirms_blocking_state"].startswith("unmet")


def test_disabled_for_caller_conditions() -> None:
    assert is_last_resort_disabled_for_caller("", _SIG) is True
    assert is_last_resort_disabled_for_caller(None, _SIG) is True
    assert is_last_resort_disabled_for_caller(_SIG, "different_signature") is True
    assert is_last_resort_disabled_for_caller(_SIG, _SIG) is False


def test_agent_eligibility_wait_proof_contract(tmp_path: Path) -> None:
    clock = ScenarioClock(1000.0)
    agent_ledger = AgentFailureLedger(
        clock=clock, consecutive_limit=1, base_cooldown_seconds=1200.0
    )
    pools = (
        LastResortAgentPool("resolver", ("agent_res_1", "agent_res_2")),
        LastResortAgentPool("planning", ("agent_plan_1",)),
    )
    ledger = _ledger(tmp_path)

    agent_ledger.record(AttemptFact("resolver", "agent_res_1", artifact_valid=False))
    agent_ledger.record(AttemptFact("resolver", "agent_res_2", artifact_valid=False))

    partial_proof = check_last_resort_agent_eligibility(
        ledger,
        agent_ledger,
        pools,
        _SIG,
        wait_seconds=600.0,
        epoch_now=clock.epoch,
    )
    assert partial_proof.waiting is False
    assert partial_proof.authorized is False

    agent_ledger.record(AttemptFact("planning", "agent_plan_1", artifact_valid=False))

    wait_proof = check_last_resort_agent_eligibility(
        ledger,
        agent_ledger,
        pools,
        _SIG,
        wait_seconds=600.0,
        epoch_now=clock.epoch,
    )
    assert wait_proof.waiting is True
    assert wait_proof.authorized is False
    assert wait_proof.next_eligible_agent in ("agent_res_1", "agent_res_2", "agent_plan_1")
    assert LAST_RESORT_WAIT_KEY in ledger.read().ladder_evidence

    raw_payload: object = json.loads(ledger.read().ladder_evidence[LAST_RESORT_WAIT_KEY])
    assert isinstance(raw_payload, dict)
    payload_map: dict[str, object] = {str(k): v for k, v in raw_payload.items()}
    assert payload_map.get("blocking_signature") == _SIG

    assert LAST_RESORT_WAIT_SECONDS == 600.0

    clock.advance(601.0)
    authorized_proof = check_last_resort_agent_eligibility(
        ledger,
        agent_ledger,
        pools,
        _SIG,
        wait_seconds=LAST_RESORT_WAIT_SECONDS,
        epoch_now=clock.epoch,
    )
    assert authorized_proof.authorized is True

    agent_ledger.clear_agent_cooldown("resolver", "agent_res_1")
    cleared_proof = check_last_resort_agent_eligibility(
        ledger,
        agent_ledger,
        pools,
        _SIG,
        wait_seconds=LAST_RESORT_WAIT_SECONDS,
        epoch_now=clock.epoch,
    )
    assert cleared_proof.waiting is False
    assert cleared_proof.authorized is False
    cleared_payload: object = json.loads(ledger.read().ladder_evidence[LAST_RESORT_WAIT_KEY])
    assert isinstance(cleared_payload, dict)
    assert "cleared_epoch_ms" in cleared_payload


def test_check_all_last_resort_conditions_matrix(tmp_path: Path) -> None:
    clock = ScenarioClock(2000.0)
    agent_ledger = AgentFailureLedger(
        clock=clock, consecutive_limit=1, base_cooldown_seconds=1200.0
    )
    pools = (LastResortAgentPool("resolver", ("agent_a",)),)
    ledger = _ledger(tmp_path)
    _seed_all_preconditions(ledger)

    ready, checks, proof = check_all_last_resort_conditions(
        ledger,
        decided_signature="",
        fresh_signature=_SIG,
    )
    assert ready is False
    assert proof is None
    assert checks["fresh_reobservation_confirms_blocking_state"].startswith("unmet")

    ready, checks, proof = check_all_last_resort_conditions(
        ledger,
        decided_signature=_SIG,
        fresh_signature=_SIG,
        eligibility=agent_ledger,
        pools=pools,
        epoch_now=clock.epoch,
    )
    assert ready is False
    assert proof is not None
    assert proof.authorized is False
    assert all(v.startswith("met") for v in checks.values())

    agent_ledger.record(AttemptFact("resolver", "agent_a", artifact_valid=False))
    check_last_resort_agent_eligibility(
        ledger, agent_ledger, pools, _SIG, wait_seconds=600.0, epoch_now=clock.epoch
    )
    clock.advance(605.0)

    ready, checks, proof = check_all_last_resort_conditions(
        ledger,
        decided_signature=_SIG,
        fresh_signature=_SIG,
        eligibility=agent_ledger,
        pools=pools,
        epoch_now=clock.epoch,
    )
    assert ready is True
    assert proof is not None
    assert proof.authorized is True
    assert all(v.startswith("met") for v in checks.values())


def test_preservation_helpers_and_planning_handoff(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    run_git(["init", "-b", "main"], cwd=repo_dir, label="test-init")
    run_git(["config", "user.name", "Ralph Test"], cwd=repo_dir, label="test-cfg")
    run_git(["config", "user.email", "ralph@example.com"], cwd=repo_dir, label="test-cfg")
    f1 = repo_dir / "file.txt"
    f1.write_text("v1\n", encoding="utf-8")
    run_git(["add", "file.txt"], cwd=repo_dir, label="test-add")
    run_git(["commit", "-m", "initial commit"], cwd=repo_dir, label="test-commit")

    f1.write_text("v2\n", encoding="utf-8")

    state_dir = repo_dir / ".agent"
    state_dir.mkdir(parents=True)
    ledger = AttemptLedger(repo_dir, ledger_path=state_dir / "attempt-ledger.json")

    bundle = ensure_last_resort_preservation(
        repo_dir,
        state_dir=state_dir,
        target="main",
        ledger=ledger,
    )
    assert bundle is not None
    assert Path(bundle.manifest_path).is_file()
    assert Path(bundle.worktree_archive).is_file()
    assert Path(bundle.work_diff).is_file()
    assert Path(bundle.intent_record).is_file()
    assert ledger.read().ladder_evidence["preserved:bundle"] == bundle.manifest_path

    handoff_path = create_planning_handoff_record(
        state_dir,
        manifest_path=bundle.manifest_path,
        carried=("commit1", "commit2"),
        not_carried=(),
        next_phase="planning",
        ledger=ledger,
    )
    assert handoff_path.name == PLANNING_HANDOFF_FILENAME
    assert handoff_path.is_file()
    assert ledger.read().ladder_evidence["preserved:planning-handoff"] == str(handoff_path)

    loaded = read_planning_handoff_record(handoff_path)
    assert loaded is not None
    assert loaded["carried"] == ["commit1", "commit2"]
    assert loaded["next_phase"] == "planning"
    assert loaded["bundle"] == bundle.manifest_path


def test_completion_in_place_handoff_record_structure() -> None:
    rec = _record(
        resolving_rebase=True,
        resolving_merge=True,
        diagnostic_intervention_required=True,
    )
    cip = completion_in_place_handoff_record(rec, reason="disabled:no-blocking-signature")
    assert cip.resolving_rebase is False
    assert cip.resolving_merge is False
    assert cip.diagnostic_intervention_required is False
    assert cip.repair_cap_reason == "disabled:no-blocking-signature"
    assert cip.resolution_terminal_marker is None


def test_disabled_signature_autonomous_transition_scenario(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    state_dir = root / ".agent"
    state_dir.mkdir(parents=True)
    ledger = AttemptLedger(root, ledger_path=state_dir / "attempt-ledger.json")
    _seed_all_preconditions(ledger)

    rec = _record(
        merge_commit_repair_attempts=DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
        resolution_gate_key=("rebase", ("src/x.py",), "stop1"),
    )
    write_record(root, rec)

    step = escalate_record_fact_outcome(
        root,
        rec,
        RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT,
        reason=DIAGNOSTIC_BUDGET_SPENT_REASON,
        ledger=ledger,
        git=_FakePlumbing(changed=(), readable_refs=frozenset()),
        state_dir=state_dir,
        decided_signature="",
        fresh_signature="",
    )

    assert step.outcome != "terminal"
    assert step.reason == "autonomous-transition:completion_in_place"
    assert step.evidence.get("next_strategy") == "completion_in_place"

    disk_record = read_record(root)
    assert disk_record is not None
    assert disk_record.resolution_terminal_marker is None
    assert disk_record.diagnostic_intervention_required is False

    evidence = ledger.read().ladder_evidence
    assert evidence.get("transition:autonomous") == "completion_in_place"
    assert evidence.get(COMPLETION_IN_PLACE_HANDOFF_KEY) == "autonomous_transition"
