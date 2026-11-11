from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline import IntegrationAction, PlanningSyncVerdict, run_loop
from ralph.pipeline.integration_decision import CleanupLadderRung
from ralph.pipeline.integration_observation import (
    DirtShape,
    HeadState,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeDispatch,
    WorktreeState,
    WorktreeVerdict,
)
from ralph.pipeline.integration_owner import RecordFactOutcome
from ralph.pipeline.state import PipelineState

if TYPE_CHECKING:
    from ralph.pipeline.rebase_state import RebaseState

pytestmark = pytest.mark.timeout_seconds(10)


def _ctx(tmp_path: Path) -> SimpleNamespace:
    (tmp_path / ".git").mkdir(exist_ok=True)
    return SimpleNamespace(
        config=SimpleNamespace(
            general=SimpleNamespace(
                auto_integrate_enabled=True,
                auto_integrate_target="main",
                auto_integrate_remote="origin",
            )
        ),
        workspace_scope=SimpleNamespace(root=tmp_path),
        policy_bundle=SimpleNamespace(
            pipeline=SimpleNamespace(
                entry_phase="planning",
                phases={"development": SimpleNamespace(drain="development", role="execution")},
            )
        ),
        active_display=None,
        latest_state=[],
    )


def _rung_verdict(
    rung: CleanupLadderRung | None,
    *,
    action: IntegrationAction = IntegrationAction.CONTINUE_GIT_OPERATION,
    record_fact: RecordFactOutcome | None = None,
) -> PlanningSyncVerdict:
    return PlanningSyncVerdict(
        action=action,
        record_fact=record_fact,
        observation=Observation(
            target_vs_feature=TargetVsFeature.BEHIND,
            target_vs_remote=TargetVsRemote.EQUAL,
            worktree=WorktreeState.TRACKED_CHANGES,
            head=HeadState.ATTACHED,
            in_progress_kind=InProgressKind.NONE,
            in_progress_ownership=InProgressOwnership.OWNED,
            record=IntegrationRecordState.NONE,
            agent_active=False,
        ),
        worktree=WorktreeVerdict(
            state=WorktreeState.TRACKED_CHANGES,
            is_clean=False,
            dispatch=WorktreeDispatch.BLOCKED,
            reason="tracked changes",
            dirt_shape=DirtShape.UNSTAGED_MODIFICATION,
        ),
        dirt_shape=DirtShape.UNSTAGED_MODIFICATION,
        in_sync=False,
        may_plan=False,
        reason="held: cleanup ladder",
        ladder_rung=rung,
    )


class _GateVerdict:
    def __init__(self, *, dispatch_allowed: bool) -> None:
        self.dispatch_allowed = dispatch_allowed


class _Gate:
    def __init__(self, *, dispatch_allowed: bool) -> None:
        self.dispatch_allowed = dispatch_allowed
        self.inspections = 0

    def __call__(self, _root: Path, _rebase: RebaseState) -> _GateVerdict:
        self.inspections += 1
        return _GateVerdict(dispatch_allowed=self.dispatch_allowed)


@pytest.fixture
def boundary_env(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(run_loop, "announce_conflict_resolution_entry", lambda *_a, **_k: None)
    monkeypatch.setattr(run_loop, "read_record", lambda _root: None)
    monkeypatch.setattr(run_loop, "save_recovered_rebase_checkpoint", lambda *_a, **_k: None)
    monkeypatch.setattr(run_loop, "run_startup_integration", lambda *_a, **_k: None)
    monkeypatch.setattr(
        run_loop,
        "run_integration_conflict_resolution",
        lambda *_a, **_k: events.append("resolver"),
    )
    return events


def _drive_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    events: list[str],
    verdicts: list[PlanningSyncVerdict],
    *,
    heal_after: int | None,
) -> tuple[object, _Gate]:
    gate = _Gate(dispatch_allowed=False)

    def _dispatch(_ctx: object, _root: object, _verdict: object, _target: object) -> None:
        events.append("dispatch")
        if heal_after is not None and events.count("dispatch") >= heal_after:
            gate.dispatch_allowed = True

    monkeypatch.setattr(run_loop, "execute_owner_ladder_rung", _dispatch)
    monkeypatch.setattr(run_loop, "inspect_integration_resolution", gate)
    verdict_iter = iter(verdicts)
    monkeypatch.setattr(run_loop, "decide_planning_sync", lambda *a, **k: next(verdict_iter))
    result = run_loop.post_recovery_verdict(
        _ctx(tmp_path),
        PipelineState(phase="development"),
        "development",
        SimpleNamespace(),
    )
    return result, gate


def test_boundary_ladder_heals_without_resolver_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    boundary_env: list[str],
) -> None:
    result, gate = _drive_boundary(
        monkeypatch,
        tmp_path,
        boundary_env,
        [_rung_verdict(CleanupLadderRung.QUARANTINE)],
        heal_after=1,
    )

    assert result is None
    assert boundary_env == ["dispatch"]
    assert gate.inspections == 1


def test_boundary_failed_cleanup_advances_to_next_rung_without_repeating(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    boundary_env: list[str],
) -> None:
    result, gate = _drive_boundary(
        monkeypatch,
        tmp_path,
        boundary_env,
        [
            _rung_verdict(CleanupLadderRung.QUARANTINE),
            _rung_verdict(
                CleanupLadderRung.FRESH_WORKTREE,
                action=IntegrationAction.REINTEGRATE_AGAINST_TARGET,
            ),
            _rung_verdict(
                CleanupLadderRung.FRESH_WORKTREE,
                action=IntegrationAction.REINTEGRATE_AGAINST_TARGET,
            ),
        ],
        heal_after=None,
    )

    assert result is not None
    assert boundary_env == ["dispatch", "dispatch", "resolver"]
    assert gate.inspections == 3


def test_boundary_record_fact_verdict_dispatches_the_preserve_first_rung(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    boundary_env: list[str],
) -> None:
    result, gate = _drive_boundary(
        monkeypatch,
        tmp_path,
        boundary_env,
        [
            _rung_verdict(
                CleanupLadderRung.NORMAL_CLEANUP,
                record_fact=RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT,
            ),
            _rung_verdict(
                CleanupLadderRung.NORMAL_CLEANUP,
                record_fact=RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT,
            ),
        ],
        heal_after=None,
    )

    assert result is not None
    assert boundary_env == ["dispatch", "resolver"]
    assert gate.inspections == 2


def test_shared_ladder_helper_is_used_by_both_seams(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    boundary_env: list[str],
) -> None:
    del boundary_env
    helper_calls: list[str] = []
    real_helper = run_loop.dispatch_owner_ladder_rung

    def _recording_helper(*args: object) -> object:
        helper_calls.append("call")
        return real_helper(*args)

    monkeypatch.setattr(run_loop, "dispatch_owner_ladder_rung", _recording_helper)

    _result, boundary_gate = _drive_boundary(
        monkeypatch,
        tmp_path,
        [],
        [_rung_verdict(CleanupLadderRung.QUARANTINE)],
        heal_after=1,
    )
    assert boundary_gate.inspections == 1
    assert helper_calls == ["call"]

    monkeypatch.setattr(
        "ralph.git.merge.observe_branch_sha",
        lambda _root, _name: ("f" * 40, True),
    )
    monkeypatch.setattr(
        "ralph.git.merge_obstructions.ancestry_state",
        lambda _root, ancestor, _desc: False,
    )
    monkeypatch.setattr("ralph.git.rebase.rebase.rebase_in_progress", lambda _root=None: False)
    monkeypatch.setattr("ralph.git.merge.merge_state", lambda _root: "none")
    planning_verdicts = iter(
        [
            _rung_verdict(CleanupLadderRung.QUARANTINE),
            _rung_verdict(CleanupLadderRung.QUARANTINE),
        ]
    )
    monkeypatch.setattr(run_loop, "decide_planning_sync", lambda *a, **k: next(planning_verdicts))
    _after, blocked = run_loop.ensure_planning_in_sync(
        PipelineState(phase="planning"), _ctx(tmp_path), "planning"
    )
    assert blocked is not None
    assert helper_calls == ["call", "call", "call"]
