"""Boundary execution seam: owner cleanup-ladder rung consumption.

:func:`ralph.pipeline.run_loop._post_recovery_verdict` runs the
boundary's recovery pass. Before any mutation it consumes the typed
owner verdict exactly as the planning-sync gate does: when the verdict
selects a cleanup-ladder rung, the rung dispatches through the SAME
shared seam helpers (:func:`run_loop._dispatch_owner_ladder_rung` /
:func:`run_loop._execute_owner_ladder_rung`) exactly once per decision,
with the decision-time ``expected_target_sha``, and the seam re-observes
after each dispatch. A failed cleanup advances to the next real rung —
never a repeated identical action — and when the ladder heals the tree
the conflict resolver is never dispatched. ``REQUIRE_INTERVENTION``-
overlaid verdicts never dispatch a rung.

Everything is stubbed at the module boundary: the owner seam serves
canned verdicts, the ladder executor and the conflict resolver are
recorders, the resolution gate is a controllable flag. No real Git, no
subprocess, no sleeps; tmp_path only satisfies the ``.git`` probe.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline import run_loop
from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline._integration_owner_seam import PlanningSyncVerdict
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
) -> PlanningSyncVerdict:
    """Build the typed owner verdict the boundary seam consumes."""
    return PlanningSyncVerdict(
        action=action,
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
    """Minimal stand-in for the resolution gate's verdict."""

    def __init__(self, *, dispatch_allowed: bool) -> None:
        self.dispatch_allowed = dispatch_allowed


class _Gate:
    """Controllable resolution gate standing in for the live re-inspection."""

    def __init__(self, *, dispatch_allowed: bool) -> None:
        self.dispatch_allowed = dispatch_allowed
        self.inspections = 0

    def __call__(self, _root: Path, _rebase: RebaseState) -> _GateVerdict:
        self.inspections += 1
        return _GateVerdict(dispatch_allowed=self.dispatch_allowed)


@pytest.fixture
def boundary_env(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Install the recorder stubs every boundary test uses; returns the event log."""
    events: list[str] = []
    monkeypatch.setattr(run_loop, "_announce_conflict_resolution_entry", lambda *_a, **_k: None)
    monkeypatch.setattr(run_loop, "read_record", lambda _root: None)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", lambda *_a, **_k: None)
    monkeypatch.setattr(run_loop, "_run_startup_integration", lambda *_a, **_k: None)
    monkeypatch.setattr(
        run_loop,
        "_run_integration_conflict_resolution",
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
    """Drive one _post_recovery_verdict call with canned owner verdicts.

    ``heal_after`` dispatches flip the re-inspection gate to
    ``dispatch_allowed`` (``None`` never heals: the ladder fails).
    """
    gate = _Gate(dispatch_allowed=False)

    def _dispatch(_ctx: object, _root: object, _verdict: object, _target: object) -> None:
        events.append("dispatch")
        if heal_after is not None and events.count("dispatch") >= heal_after:
            gate.dispatch_allowed = True

    monkeypatch.setattr(run_loop, "_execute_owner_ladder_rung", _dispatch)
    monkeypatch.setattr(run_loop, "inspect_integration_resolution", gate)
    verdict_iter = iter(verdicts)
    monkeypatch.setattr(run_loop, "decide_planning_sync", lambda *a, **k: next(verdict_iter))
    result = run_loop._post_recovery_verdict(
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
    """One selected rung: dispatched once, re-observed, resolver never runs."""
    result, gate = _drive_boundary(
        monkeypatch,
        tmp_path,
        boundary_env,
        [_rung_verdict(CleanupLadderRung.QUARANTINE)],
        heal_after=1,
    )

    assert result is None  # dispatch allowed: the caller proceeds
    assert boundary_env == ["dispatch"]
    assert gate.inspections == 1


def test_boundary_failed_cleanup_advances_to_next_rung_without_repeating(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    boundary_env: list[str],
) -> None:
    """Failed rung 3 → rung 4 executes; the identical rung is never re-run."""
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
            # The observation did not change: the saturated answer must
            # not re-dispatch rung 4; the ladder yields to the recovery
            # pass, which runs exactly once.
            _rung_verdict(
                CleanupLadderRung.FRESH_WORKTREE,
                action=IntegrationAction.REINTEGRATE_AGAINST_TARGET,
            ),
        ],
        heal_after=None,
    )

    assert result is not None  # still blocking: the verdict is returned
    assert boundary_env == ["dispatch", "dispatch", "resolver"]
    assert gate.inspections == 3  # one re-inspection per dispatch, one post-recovery


def test_boundary_intervention_overlaid_rung_never_dispatches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    boundary_env: list[str],
) -> None:
    """A REQUIRE_INTERVENTION-overlaid rung verdict falls through, no dispatch."""
    result, gate = _drive_boundary(
        monkeypatch,
        tmp_path,
        boundary_env,
        [
            _rung_verdict(
                CleanupLadderRung.QUARANTINE,
                action=IntegrationAction.REQUIRE_INTERVENTION,
            )
        ],
        heal_after=None,
    )

    assert result is not None
    assert boundary_env == ["resolver"]
    assert gate.inspections == 1


def test_shared_ladder_helper_is_used_by_both_seams(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    boundary_env: list[str],
) -> None:
    """Planning gate and boundary seam both route through _dispatch_owner_ladder_rung."""
    del boundary_env
    helper_calls: list[str] = []
    real_helper = run_loop._dispatch_owner_ladder_rung

    def _recording_helper(*args: object) -> object:
        helper_calls.append("call")
        return real_helper(*args)

    monkeypatch.setattr(run_loop, "_dispatch_owner_ladder_rung", _recording_helper)

    # Boundary seam drive: a rung verdict that heals on re-observation.
    _result, boundary_gate = _drive_boundary(
        monkeypatch,
        tmp_path,
        [],
        [_rung_verdict(CleanupLadderRung.QUARANTINE)],
        heal_after=1,
    )
    assert boundary_gate.inspections == 1
    assert helper_calls == ["call"]

    # Planning-sync gate drive: one dispatch, then the identical rung
    # re-selected under the unchanged observation breaks the loop.
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
    _after, blocked = run_loop._ensure_planning_in_sync(
        PipelineState(phase="planning"), _ctx(tmp_path), "planning"
    )
    assert blocked is not None
    assert helper_calls == ["call", "call", "call"]
