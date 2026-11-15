from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline import run_loop, runner
from ralph.pipeline.integration_resolution import RESOLVED
from ralph.pipeline.integration_resolution_types import IntegrationResolutionVerdict
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_dirty_sync_dispatches_normal_commit_before_planning": "the new pending-rollback case continues without the branch landing because the refused rollback stops the commit route; the landed check still holds for every other case",
}


def _policy() -> PipelinePolicy:
    return PipelinePolicy(
        entry_phase="planning",
        phases={
            "planning": PhaseDefinition(
                drain="planning", transitions=PhaseTransition(on_success="complete")
            ),
            "tidy": PhaseDefinition(
                drain="commit",
                role="commit_review",
                transitions=PhaseTransition(on_success="package"),
            ),
            "package": PhaseDefinition(
                drain="commit", role="commit", transitions=PhaseTransition(on_success="complete")
            ),
        },
    )


@pytest.mark.parametrize(
    "commit_succeeds, obstructs, pending_rollback",
    [(True, True, False), (False, True, False), (True, False, False), (True, True, True)],
)
def test_dirty_sync_dispatches_normal_commit_before_planning(
    monkeypatch: pytest.MonkeyPatch, commit_succeeds: bool, obstructs: bool, pending_rollback: bool
) -> None:
    workspace = MemoryWorkspace()
    workspace.write("work", "completed change")
    dispatched: list[str] = []
    saved: list[PipelineState] = []
    waits: list[float] = []
    landed = False

    def dirty(_root: Path) -> bool:
        return workspace.exists("work")

    def gap(_ctx: run_loop._LoopContext) -> str | None:
        return None if landed else "branch behind main"

    def save(state: PipelineState, _ctx: run_loop._LoopContext) -> None:
        saved.append(PipelineState.model_validate_json(state.model_dump_json()))

    def integrate(_ctx: run_loop._LoopContext, _rebase: RebaseState) -> RebaseState:
        nonlocal landed
        if obstructs and dirty(workspace.root):
            return RebaseState(
                last_action="conflict",
                last_reason="merge refused: local changes would be overwritten: work",
            )
        landed = True
        return RebaseState(last_action="fast_forwarded", fast_forwarded=True)

    def step(*, state: PipelineState, **_kwargs: object) -> PipelineState | int:
        dispatched.append(str(state.phase))
        if state.phase == "tidy":
            return state.copy_with(phase="package")
        if state.phase == "package":
            if commit_succeeds:
                workspace.delete("work")
            return state.copy_with(phase="complete")
        assert state.phase == "planning"
        assert landed or pending_rollback
        return 0

    def sleep(delay: float) -> None:
        waits.append(delay)
        assert dispatched == ["tidy", "package"]
        assert dirty(workspace.root)
        raise _ObservedCooldownError

    ctx = cast(
        "run_loop._LoopContext",
        SimpleNamespace(
            policy_bundle=SimpleNamespace(pipeline=_policy()),
            config=UnifiedConfig(),
            workspace_scope=WorkspaceScope(workspace.root),
            active_display=SimpleNamespace(),
            latest_state=[],
            sleep=sleep,
            pipeline_deps=None,
            connectivity_monitor=SimpleNamespace(current_state=None),
            display_context=None,
            effective_verbosity=None,
            registry=None,
            effective_pipeline_subscriber=None,
            controller=None,
            config_path=None,
            cli_overrides=None,
            monitor_stop=None,
            snapshot_registry=None,
        ),
    )
    monkeypatch.setattr("ralph.git.operations.has_uncommitted_changes", dirty)
    monkeypatch.setattr(run_loop, "_planning_sync_gap", gap)
    monkeypatch.setattr(
        run_loop,
        "inspect_integration_resolution",
        lambda *_args, write_back=None: IntegrationResolutionVerdict(RESOLVED),
    )
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", save)
    monkeypatch.setattr(run_loop, "_run_startup_integration", integrate)
    monkeypatch.setattr(run_loop, "_push_status_bar_if_changed", lambda *_args: None)
    monkeypatch.setattr(
        runner, "emit_phase_transition_if_changed", lambda *_args, **_kwargs: "planning"
    )
    monkeypatch.setattr(runner, "run_pipeline_step", step)
    rollbacks: list[str | None] = []

    def refuse_rollback(state: PipelineState, *_args: object, **_kwargs: object) -> PipelineState:
        rollbacks.append(state.rollback_ref)
        return state.copy_with(rollback_pending=False, last_error="rollback refused")

    monkeypatch.setattr(runner, "execute_pending_rollback", refuse_rollback)
    run_inner_loop = run_loop._run_inner_loop_after_startup

    if pending_rollback:
        outcome = run_inner_loop(_REJECTED, ctx, "planning")
        _assert_pending_rollback_runs_before_commit_review(outcome, dispatched, rollbacks)
        assert run_loop._route_planning_commit(outcome[0], ctx) is outcome[0]
        assert "tidy" not in dispatched
        return

    if commit_succeeds:
        state, _previous, code = run_inner_loop(PipelineState(phase="planning"), ctx, "planning")
        assert code == 0
        assert state.phase == "planning"
        assert dispatched == (["tidy", "package", "planning"] if obstructs else ["planning"])
        assert waits == []
        assert workspace.exists("work") is not obstructs
        assert (
            any(item.integration_commit_resume_phase == "planning" for item in saved) is obstructs
        )
    else:
        with pytest.raises(_ObservedCooldownError):
            run_inner_loop(PipelineState(phase="planning"), ctx, "planning")
        assert waits and all(delay > 0 for delay in waits)
        assert landed is False


_REJECTED_REF = "refs/ralph/proposals/run/1-1"
_REJECTED = PipelineState(phase="planning", rollback_pending=True, rollback_ref=_REJECTED_REF)


def _assert_pending_rollback_runs_before_commit_review(
    outcome: tuple[PipelineState, str, int | None],
    dispatched: list[str],
    rollbacks: list[str | None],
) -> None:
    state, _previous, code = outcome
    assert code == 0
    assert "tidy" not in dispatched
    assert rollbacks == [_REJECTED_REF]
    assert state.rollback_ref == _REJECTED_REF
    assert state.rollback_pending is False


class _ObservedCooldownError(Exception):
    pass
