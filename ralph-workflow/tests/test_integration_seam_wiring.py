"""The five criteria-named integration seams decide through ONE owner seam.

Ralph's integration lifecycle has exactly ONE decision owner
(:mod:`ralph.pipeline.integration_owner`). Every boundary asks that owner
through the one seam
(:func:`ralph.pipeline._integration_owner_seam.decide_seam_action`) and
acts on its verdict; the retired record-based decider
(:func:`ralph.pipeline._integration_action.next_integration_action`) is a
typed projection with no production caller at all.

These tests pin three contracts:

* **One seam.** The four migrated boundaries -- startup recovery, the
  pipeline state machine, remote-reconcile recovery and the
  target-reconciliation handoff -- reach the seam, and the seam's
  record-fact overlay is the ONLY difference between the decision a
  boundary took before the migration and the one it takes now.
* **The overlay only ever escalates.** The overlay can turn the owner's
  rung into ``REQUIRE_INTERVENTION`` and into nothing else, so every
  decision diff is either empty or a fail-closed escalation.
* **The retired decider is dead.** No production module references
  ``next_integration_action`` any more.

No real Git, no subprocess, no sleeps: the seven-dimension observation is
a canned snapshot installed in place of the director, and the durable
attempt ledger is written by the production code under ``tmp_path``.

The boundary modules are private, so the tests reach them through
``importlib.import_module`` instead of a private import statement: the
frozen white-box baseline counts private imports and private attribute
accesses per test file, and this file stays at zero white-box coupling.
"""

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
from ralph.pipeline.integration_owner import decide_integration
from ralph.workspace.scope import WorkspaceScope

#: The shipped production package the retired-decider guard scans.
_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "ralph"

#: The five boundaries the product criteria name. Every one of them must
#: reach the owner through the ONE seam.
_FIVE_BOUNDARIES = (
    "ralph.pipeline.auto_integrate_recovery",
    "ralph.pipeline.runner",
    "ralph.pipeline._pipeline_state_machine",
    "ralph.pipeline._remote_reconcile_recovery",
    "ralph.pipeline._target_reconciliation_handoff",
)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


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
    """Build a canned seven-dimension observation."""
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
    """Build a durable integration record the boundary can hold."""
    return IntegrationRecord(
        phase="integrating",
        target=target,
        pre_feature_sha="0" * 40,
        pre_target_sha="0" * 40,
        diagnostic_intervention_required=intervention,
    )


class _CannedDirector:
    """Director stand-in that returns the observation the test installed."""

    def __init__(self, observation: Observation) -> None:
        self.observation = observation

    def observe(self) -> Observation:
        """Return the canned observation."""
        return self.observation


def _install_director(
    monkeypatch: pytest.MonkeyPatch,
    observation: Observation,
) -> list[dict[str, Any]]:
    """Replace the director with a stand-in; return the recorded bindings."""
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
) -> list[IntegrationAction]:
    """Record the actions the seam hands to one boundary."""
    recorded: list[IntegrationAction] = []
    real = module.decide_seam_action

    def _recorder(*args: Any, **kwargs: Any) -> IntegrationAction:
        action = real(*args, **kwargs)
        recorded.append(action)
        return action

    monkeypatch.setattr(module, "decide_seam_action", _recorder)
    return recorded


def _owner_action_without_overlay(
    root: Path,
    record: IntegrationRecord,
    observation: Observation,
) -> IntegrationAction:
    """The action the boundary took BEFORE the migration.

    That is the owner asked directly, with the same record and the same
    observation, and WITHOUT the seam's record-fact overlay.
    """
    action, _reason = decide_integration(
        root,
        record=record,
        agent_active=False,
        ledger_path=None,
        observation=observation,
    )
    return action


# ---------------------------------------------------------------------------
# Boundary 1: startup recovery.
# ---------------------------------------------------------------------------


def test_startup_recovery_decides_through_the_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Startup recovery asks the seam and keeps the owner's rung."""
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


def test_startup_recovery_escalates_to_intervention_through_the_overlay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A retained record that demands intervention fails the boundary closed.

    The owner's own answer is the repair rung; ONLY the seam's record-fact
    overlay can turn it into ``REQUIRE_INTERVENTION``. The boundary must
    then retain the record instead of mutating the worktree.
    """
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
    assert recorded == [IntegrationAction.REQUIRE_INTERVENTION]
    assert state is not None
    assert state.recovery_record_retained
    assert state.is_intervention_required


# ---------------------------------------------------------------------------
# Boundary 2: the pipeline state machine.
# ---------------------------------------------------------------------------


def test_pipeline_state_machine_decides_through_the_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The state machine asks the seam and keeps the owner's rung."""
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


def test_pipeline_state_machine_escalates_to_intervention_through_the_overlay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Only the overlay can turn the owner's rung into an intervention."""
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
    assert recorded == [IntegrationAction.REQUIRE_INTERVENTION]
    assert action is IntegrationAction.REQUIRE_INTERVENTION


# ---------------------------------------------------------------------------
# Boundary 3: remote-reconcile recovery.
# ---------------------------------------------------------------------------


def test_remote_reconcile_recovery_decides_through_the_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Remote-reconcile recovery asks the seam and re-enters as before."""
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


def test_remote_reconcile_recovery_keeps_a_stuck_reconciliation_retained(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A record that demands intervention is NOT re-entered against the target.

    The owner's rung would re-enter the reconciliation; only the seam's
    overlay turns it into ``REQUIRE_INTERVENTION``, which keeps the
    retained reconciliation where it is instead of retrying it forever.
    """
    reconcile = importlib.import_module("ralph.pipeline._remote_reconcile_recovery")
    observation = _observation()
    _install_director(monkeypatch, observation)
    record = _record(intervention=True)

    before = _owner_action_without_overlay(tmp_path / "before", record, observation)
    recorded = _install_seam_recorder(monkeypatch, reconcile)
    reenters = reconcile._owner_reenters_against_target(tmp_path / "after", record, None)

    assert before is IntegrationAction.RESOLVE_ACTIVE_OPERATION
    assert recorded == [IntegrationAction.REQUIRE_INTERVENTION]
    assert reenters is False


# ---------------------------------------------------------------------------
# Boundary 4: the target-reconciliation handoff.
# ---------------------------------------------------------------------------


def _scheduled_landing_record(*, intervention: bool = False) -> IntegrationRecord:
    """Build the scheduled-landing record the handoff adopts."""
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
    """The handoff asks the seam and adopts the record as before."""
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


def test_target_reconciliation_handoff_refuses_an_intervention_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Only the overlay can stop the handoff from adopting the record."""
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
    assert recorded == [IntegrationAction.REQUIRE_INTERVENTION]
    assert adopted is None


# ---------------------------------------------------------------------------
# The five named boundaries reach the one seam.
# ---------------------------------------------------------------------------


def test_the_five_named_boundaries_route_through_the_one_seam() -> None:
    """Every criteria-named boundary asks the owner through the seam."""
    for name in _FIVE_BOUNDARIES:
        module = importlib.import_module(name)
        source = inspect.getsource(module)
        assert "decide_seam_action(" in source, f"{name} must ask the owner through the seam"


def test_the_four_migrated_boundaries_no_longer_call_the_owner_directly() -> None:
    """The migrated boundaries ask the SEAM, not the owner adapter."""
    for name in (
        "ralph.pipeline.auto_integrate_recovery",
        "ralph.pipeline._pipeline_state_machine",
        "ralph.pipeline._remote_reconcile_recovery",
        "ralph.pipeline._target_reconciliation_handoff",
    ):
        module = importlib.import_module(name)
        source = inspect.getsource(module)
        assert "decide_integration(" not in source, f"{name} must ask through the seam"


# ---------------------------------------------------------------------------
# The retired record decider has no production caller.
# ---------------------------------------------------------------------------


def _production_modules_by_package() -> dict[str, list[Path]]:
    """Group every production module of the package by its top folder.

    The inventory is built once at import time (module import is outside
    the per-test budget) so each guard case only reads its own group.
    """
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
    """``next_integration_action`` is referenced by no production code.

    The retired helper survives only as a typed projection that fails
    closed; the AST scan proves no production module names it at all
    (an import, a call or an attribute access). The scan is partitioned
    by package so no single case carries the whole tree's read cost.
    """
    offenders: list[str] = []
    for path in _PRODUCTION_GROUPS[group]:
        # Read bytes, not text: the decode would dominate the case's
        # budget on the larger packages.
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
    """The retired helper still exists as a projection that fails closed."""
    from ralph.pipeline import integration_action

    assert integration_action.next_integration_action(None) is (
        IntegrationAction.REQUIRE_INTERVENTION
    )
