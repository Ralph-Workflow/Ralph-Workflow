from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from ralph.pipeline import IntegrationAction, integration_director
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    write_record,
)
from ralph.pipeline.integration_decision import CleanupLadderRung
from ralph.pipeline.integration_observation import (
    HeadState,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeState,
)
from ralph.pipeline.integration_owner import (
    RecordFactOutcome,
    decide_integration,
    ladder_rung_for_record_fact_outcome,
)
from ralph.workspace.scope import WorkspaceScope

_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "ralph"

_FIVE_BOUNDARIES = (
    "ralph.pipeline.auto_integrate_recovery",
    "ralph.pipeline.runner",
    "ralph.pipeline._pipeline_state_machine",
    "ralph.pipeline._remote_reconcile_recovery",
    "ralph.pipeline._target_reconciliation_handoff",
    "ralph.pipeline.run_loop",
    "ralph.pipeline.auto_integrate_planning",
    "ralph.pipeline.auto_integrate_catchup",
)


def _observation(
    *,
    target_vs_feature: TargetVsFeature = TargetVsFeature.BEHIND,
    target_vs_remote: TargetVsRemote = TargetVsRemote.EQUAL,
    worktree: WorktreeState = WorktreeState.TRACKED_CHANGES,
    head: HeadState = HeadState.ATTACHED,
    in_progress_kind: InProgressKind = InProgressKind.NONE,
    in_progress_ownership: InProgressOwnership = InProgressOwnership.OWNED,
    record: IntegrationRecordState = IntegrationRecordState.NONE,
    agent_active: bool = False,
) -> Observation:
    return Observation(
        target_vs_feature=target_vs_feature,
        target_vs_remote=target_vs_remote,
        worktree=worktree,
        head=head,
        in_progress_kind=in_progress_kind,
        in_progress_ownership=in_progress_ownership,
        record=record,
        agent_active=agent_active,
    )


def _record(*, intervention: bool = False, target: str = "main") -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrating",
        target=target,
        pre_feature_sha="0" * 40,
        pre_target_sha="0" * 40,
        diagnostic_intervention_required=intervention,
    )


class _CannedDirector:
    def __init__(self, observation: Observation) -> None:
        self.observation = observation

    def observe(self) -> Observation:
        return self.observation


def _install_director(
    monkeypatch: pytest.MonkeyPatch,
    observation: Observation,
) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def _factory(
        root: Path,
        *,
        target: str,
        remote: str,
        agent_active: bool = False,
    ) -> _CannedDirector:
        seen.append(
            {
                "root": root,
                "target": target,
                "remote": remote,
                "agent_active": agent_active,
            }
        )
        return _CannedDirector(observation)

    monkeypatch.setattr(integration_director, "IntegrationDirector", _factory)
    return seen


def _install_seam_recorder(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
) -> list[IntegrationAction | RecordFactOutcome]:
    recorded: list[IntegrationAction | RecordFactOutcome] = []
    real = module.decide_seam_action

    def _recorder(*args: Any, **kwargs: Any) -> IntegrationAction | RecordFactOutcome:
        action = real(*args, **kwargs)
        recorded.append(action)
        return action

    monkeypatch.setattr(module, "decide_seam_action", _recorder)
    return recorded


def _assert_never_a_human_hand_off(
    recorded: list[IntegrationAction | RecordFactOutcome],
) -> None:
    for verdict in recorded:
        assert verdict is not IntegrationAction.REQUIRE_INTERVENTION
        if isinstance(verdict, RecordFactOutcome):
            assert ladder_rung_for_record_fact_outcome(verdict) is CleanupLadderRung.NORMAL_CLEANUP


def _overlay_fails_closed_to_the_ladder(
    owner_action: IntegrationAction,
    record: IntegrationRecord,
    observation: Observation,
) -> RecordFactOutcome:
    record_facts = importlib.import_module("ralph.pipeline._integration_record_facts")
    overlay = record_facts.apply_record_facts(
        owner_action,
        record,
        observation=observation,
    )
    assert isinstance(overlay, RecordFactOutcome)
    assert ladder_rung_for_record_fact_outcome(overlay) is CleanupLadderRung.NORMAL_CLEANUP
    return overlay


def _owner_action_without_overlay(
    root: Path,
    record: IntegrationRecord,
    observation: Observation,
) -> IntegrationAction:
    action, _reason = decide_integration(
        root,
        record=record,
        agent_active=False,
        ledger_path=None,
        observation=observation,
    )
    return action


def test_startup_recovery_decides_through_the_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recovery = importlib.import_module("ralph.pipeline.auto_integrate_recovery")
    observation = _observation(target_vs_feature=TargetVsFeature.UNKNOWN)
    _install_director(monkeypatch, observation)
    record = _record()
    root = tmp_path / "recovery"
    root.mkdir()
    write_record(root, record)

    before = _owner_action_without_overlay(tmp_path / "before", record, observation)
    recorded = _install_seam_recorder(monkeypatch, recovery)
    state = recovery._recover_incomplete_integration_owned(WorkspaceScope(root))

    assert before is IntegrationAction.WAIT_FOR_BOUNDED_RETRY
    assert recorded == [IntegrationAction.WAIT_FOR_BOUNDED_RETRY]
    assert state is not None
    assert state.recovery_record_retained


def test_startup_recovery_fails_closed_into_the_ladder_through_the_overlay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recovery = importlib.import_module("ralph.pipeline.auto_integrate_recovery")
    observation = _observation()
    _install_director(monkeypatch, observation)
    record = _record(intervention=True)
    root = tmp_path / "recovery"
    root.mkdir()
    write_record(root, record)

    before = _owner_action_without_overlay(tmp_path / "before", record, observation)
    recorded = _install_seam_recorder(monkeypatch, recovery)
    state = recovery._recover_incomplete_integration_owned(WorkspaceScope(root))

    assert before is IntegrationAction.RESOLVE_ACTIVE_OPERATION
    assert _overlay_fails_closed_to_the_ladder(before, record, observation) is (
        RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT
    )
    _assert_never_a_human_hand_off(recorded)
    assert state is not None
    assert state.recovery_record_retained
    assert not state.is_intervention_required


def test_pipeline_state_machine_decides_through_the_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_machine = importlib.import_module("ralph.pipeline._pipeline_state_machine")
    observation = _observation()
    _install_director(monkeypatch, observation)
    record = _record()

    before = _owner_action_without_overlay(tmp_path / "before", record, observation)
    recorded = _install_seam_recorder(monkeypatch, state_machine)
    action = state_machine._owner_action(
        tmp_path / "after",
        record,
        config=None,
        agent_active=False,
    )

    assert before is IntegrationAction.RESOLVE_ACTIVE_OPERATION
    assert recorded == [action]
    assert action is IntegrationAction.RESOLVE_ACTIVE_OPERATION


def test_pipeline_state_machine_fails_closed_into_the_ladder_through_the_overlay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_machine = importlib.import_module("ralph.pipeline._pipeline_state_machine")
    observation = _observation()
    _install_director(monkeypatch, observation)
    record = _record(intervention=True)

    before = _owner_action_without_overlay(tmp_path / "before", record, observation)
    recorded = _install_seam_recorder(monkeypatch, state_machine)
    action = state_machine._owner_action(
        tmp_path / "after",
        record,
        config=None,
        agent_active=False,
    )

    assert before is IntegrationAction.RESOLVE_ACTIVE_OPERATION
    assert _overlay_fails_closed_to_the_ladder(before, record, observation) is (
        RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT
    )
    _assert_never_a_human_hand_off(recorded)
    assert action is not IntegrationAction.REQUIRE_INTERVENTION


def test_remote_reconcile_recovery_decides_through_the_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    reconcile = importlib.import_module("ralph.pipeline._remote_reconcile_recovery")
    observation = _observation()
    _install_director(monkeypatch, observation)
    record = _record()

    before = _owner_action_without_overlay(tmp_path / "before", record, observation)
    recorded = _install_seam_recorder(monkeypatch, reconcile)
    reenters = reconcile._owner_reenters_against_target(tmp_path / "after", record, None)

    assert before is IntegrationAction.RESOLVE_ACTIVE_OPERATION
    assert recorded == [IntegrationAction.RESOLVE_ACTIVE_OPERATION]
    assert reenters is True


def test_remote_reconcile_recovery_keeps_a_legacy_intervention_record_retained(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    reconcile = importlib.import_module("ralph.pipeline._remote_reconcile_recovery")
    observation = _observation()
    _install_director(monkeypatch, observation)
    record = _record(intervention=True)

    before = _owner_action_without_overlay(tmp_path / "before", record, observation)
    recorded = _install_seam_recorder(monkeypatch, reconcile)
    reenters = reconcile._owner_reenters_against_target(tmp_path / "after", record, None)

    assert before is IntegrationAction.RESOLVE_ACTIVE_OPERATION
    assert _overlay_fails_closed_to_the_ladder(before, record, observation) is (
        RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT
    )
    _assert_never_a_human_hand_off(recorded)
    assert reenters is False


def _scheduled_landing_record(*, intervention: bool = False) -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="0" * 40,
        reintegrate_pending=True,
        diagnostic_intervention_required=intervention,
    )


def test_target_reconciliation_handoff_decides_through_the_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    handoff = importlib.import_module("ralph.pipeline._target_reconciliation_handoff")
    observation = _observation()
    _install_director(monkeypatch, observation)
    record = _scheduled_landing_record()
    root = tmp_path / "handoff"
    root.mkdir()
    write_record(root, record)
    monkeypatch.setattr(handoff, "get_head_sha", lambda _root: record.pre_feature_sha)

    before = _owner_action_without_overlay(tmp_path / "before", record, observation)
    recorded = _install_seam_recorder(monkeypatch, handoff)
    adopted = handoff.scheduled_landing(root, record.target)

    assert before is IntegrationAction.RESOLVE_ACTIVE_OPERATION
    assert recorded == [IntegrationAction.RESOLVE_ACTIVE_OPERATION]
    assert adopted is not None


def test_target_reconciliation_handoff_adopts_a_legacy_intervention_record_autonomously(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    handoff = importlib.import_module("ralph.pipeline._target_reconciliation_handoff")
    observation = _observation()
    _install_director(monkeypatch, observation)
    record = _scheduled_landing_record(intervention=True)
    root = tmp_path / "handoff"
    root.mkdir()
    write_record(root, record)
    monkeypatch.setattr(handoff, "get_head_sha", lambda _root: record.pre_feature_sha)

    before = _owner_action_without_overlay(tmp_path / "before", record, observation)
    recorded = _install_seam_recorder(monkeypatch, handoff)
    adopted = handoff.scheduled_landing(root, record.target)

    assert before is IntegrationAction.RESOLVE_ACTIVE_OPERATION
    assert _overlay_fails_closed_to_the_ladder(before, record, observation) is (
        RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT
    )
    _assert_never_a_human_hand_off(recorded)
    assert adopted is not None


def test_the_five_named_boundaries_route_through_the_one_seam() -> None:
    for name in _FIVE_BOUNDARIES:
        module = importlib.import_module(name)
        source = inspect.getsource(module)
        assert "decide_seam_action(" in source, f"{name} must ask the owner through the seam"


def test_the_four_migrated_boundaries_no_longer_call_the_owner_directly() -> None:
    for name in (
        "ralph.pipeline.auto_integrate_recovery",
        "ralph.pipeline._pipeline_state_machine",
        "ralph.pipeline._remote_reconcile_recovery",
        "ralph.pipeline._target_reconciliation_handoff",
    ):
        module = importlib.import_module(name)
        source = inspect.getsource(module)
        assert "decide_integration(" not in source, f"{name} must ask through the seam"


def _production_modules_by_package() -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = {}
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(_PACKAGE_ROOT)
        group = relative.parts[0] if len(relative.parts) > 1 else "<root>"
        groups.setdefault(group, []).append(path)
    return groups


_PRODUCTION_GROUPS = _production_modules_by_package()


@pytest.mark.parametrize("group", sorted(_PRODUCTION_GROUPS))
def test_no_production_module_references_the_retired_record_decider(
    group: str,
) -> None:
    offenders: list[str] = []
    for path in _PRODUCTION_GROUPS[group]:
        raw = path.read_bytes()
        if b"next_integration_action" not in raw:
            continue
        tree = ast.parse(raw.decode("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                named = node.id
            elif isinstance(node, ast.Attribute):
                named = node.attr
            else:
                continue
            if named == "next_integration_action":
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_the_retired_decider_is_still_a_typed_projection() -> None:
    from ralph.pipeline import integration_action

    assert integration_action.next_integration_action(None) is (
        IntegrationAction.REQUIRE_INTERVENTION
    )


def test_the_diagnostic_boundary_routes_through_the_one_seam() -> None:
    module = importlib.import_module("ralph.pipeline._integration_diagnostic")
    source = inspect.getsource(module)
    assert "decide_seam_action(" in source, (
        "the diagnostic handoff must ask the owner through the one seam"
    )
    assert "decide_integration(" not in source, (
        "the diagnostic handoff must not compose the owner and the record facts by hand"
    )
    assert "apply_record_facts(" not in source, (
        "the record-fact overlay belongs to the seam, not to the diagnostic"
    )


def test_diagnostic_handoff_decides_through_the_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from ralph.recovery.controller import RecoveryController

    diagnostic = importlib.import_module("ralph.pipeline._integration_diagnostic")
    from ralph.pipeline.operation_evidence_memory import InMemoryOperationEvidenceCollector

    record = _record()
    root = tmp_path / "diagnostic"
    root.mkdir()

    calls: list[dict[str, Any]] = []

    def _spy(root_arg: Path, /, **kwargs: Any) -> IntegrationAction:
        calls.append({"root": root_arg, **kwargs})
        return IntegrationAction.RESOLVE_ACTIVE_OPERATION

    monkeypatch.setattr(diagnostic, "decide_seam_action", _spy)

    offered = diagnostic.handoff_integration_diagnostic(
        root=root,
        record=record,
        failure="boom",
        agents=(),
        invoke=lambda _name, _path: False,
        collector=InMemoryOperationEvidenceCollector(),
        controller=RecoveryController(),
    )

    assert calls == [{"root": root, "record": record}]
    assert offered is False


@pytest.mark.parametrize("group", sorted(_PRODUCTION_GROUPS))
def test_no_production_module_consumes_require_intervention(group: str) -> None:
    vocabulary_path = importlib.import_module("ralph.pipeline._integration_action").__file__
    assert vocabulary_path is not None
    vocabulary = Path(vocabulary_path).name
    offenders: list[str] = []
    for path in _PRODUCTION_GROUPS[group]:
        if path.name == vocabulary:
            continue
        raw = path.read_bytes()
        if b"REQUIRE_INTERVENTION" not in raw:
            continue
        tree = ast.parse(raw.decode("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                named = node.id
            elif isinstance(node, ast.Attribute):
                named = node.attr
            else:
                continue
            if named == "REQUIRE_INTERVENTION":
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
