"""Regression coverage for integration recovery that outlives a spent ladder."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from ralph.config.models import UnifiedConfig
from ralph.git import merge, merge_obstructions
from ralph.git.merge import MERGE_STATE_NONE
from ralph.pipeline import run_loop, runner
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.pipeline.integration_resolution_types import IntegrationResolutionVerdict


@dataclass(frozen=True, slots=True)
class RecoveryObservation:
    result: PipelineState
    exit_code: int | None
    dispatched: tuple[PipelineState, ...]
    saved: tuple[PipelineState, ...]
    warnings: tuple[str, ...]
    waits: tuple[float, ...]


def run_recovery_scenario(
    monkeypatch: MonkeyPatch, state: PipelineState, *, startup: bool = False
) -> RecoveryObservation:
    """Drive the real loop against Git evidence cleared by an injected clock."""
    dispatched: list[PipelineState] = []
    saved: list[PipelineState] = []
    warnings: list[str] = []
    waits: list[float] = []
    recovered = False
    workspace = MemoryWorkspace("/memory/recovery")
    workspace.write("main", "before")
    workspace.write("HEAD", "feature")

    def sleep(delay: float) -> None:
        nonlocal recovered
        assert delay > 0
        assert dispatched == [], "ordinary dispatch must wait for integration recovery"
        assert saved[-1].is_waiting_state is True
        waits.append(delay)
        assert len(waits) <= 2, "recovered repository must allow progress"
        recovered = len(waits) == 2
        if recovered:
            workspace.write("main", workspace.read("HEAD"))

    def save(current: PipelineState, _ctx: run_loop._LoopContext) -> None:
        workspace.write("checkpoint.json", current.model_dump_json())
        saved.append(PipelineState.model_validate_json(workspace.read("checkpoint.json")))

    def inspect(root: Path, rebase: RebaseState) -> IntegrationResolutionVerdict:
        return inspect_integration_resolution(
            root,
            rebase,
            porcelain=lambda _root: (True, "" if recovered else "UU shared.txt\n"),
            rebase_active=lambda _root: False,
            merge_status=lambda _root: MERGE_STATE_NONE,
        )

    def dispatch(*, state: PipelineState, **_kwargs: object) -> int:
        assert recovered, "unresolved integration must never reach the phase executor"
        assert state.is_waiting_state is False
        assert saved[-1].rebase.integration_unresolved is False
        assert workspace.read("main") == workspace.read("HEAD")
        dispatched.append(state)
        return 0

    def warn(_scope: str, _channel: str, message: str) -> None:
        warnings.append(message)

    def exhausted(*_args: object) -> bool:
        return not recovered

    def recover(*_args: object) -> None:
        return None

    def sync_target(_ctx: run_loop._LoopContext) -> tuple[Path, str]:
        return workspace.root, "main"

    def observe_sha(_root: Path, ref: str) -> tuple[str, bool]:
        return workspace.read(ref), True

    def ancestry(_root: Path, ancestor: str, descendant: str) -> bool:
        return workspace.read(ancestor) == workspace.read(descendant)

    def legacy(_root: Path) -> RebaseState:
        return state.rebase

    def startup_outcome(current: PipelineState, _ctx: run_loop._LoopContext) -> PipelineState:
        return current

    display = SimpleNamespace(emit_warn_line=warn)
    latest_state: list[PipelineState] = []
    ctx = cast(
        "run_loop._LoopContext",
        SimpleNamespace(
            policy_bundle=SimpleNamespace(
                pipeline=PipelinePolicy(
                    entry_phase=str(state.phase),
                    phases={
                        str(state.phase): PhaseDefinition(
                            drain="development", transitions=PhaseTransition(on_success="complete")
                        )
                    },
                )
            ),
            active_display=display,
            config=UnifiedConfig(),
            workspace_scope=WorkspaceScope(workspace.root),
            latest_state=latest_state,
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
        ),
    )
    monkeypatch.setattr(run_loop, "inspect_integration_resolution", inspect)
    monkeypatch.setattr(run_loop, "_exhaustion_still_binds", exhausted)
    monkeypatch.setattr(run_loop, "_run_integration_conflict_resolution", recover)
    monkeypatch.setattr(run_loop, "_run_startup_integration", recover)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", recover)
    monkeypatch.setattr(run_loop, "_configured_sync_target", sync_target)
    monkeypatch.setattr(merge, "observe_branch_sha", observe_sha)
    monkeypatch.setattr(
        merge_obstructions,
        "ancestry_state",
        ancestry,
    )
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", save)
    monkeypatch.setattr(runner, "run_pipeline_step", dispatch)
    monkeypatch.setattr(run_loop, "legacy_rebase_startup_block", legacy)
    monkeypatch.setattr(run_loop, "_apply_startup_rebase_outcomes", startup_outcome)
    loop = run_loop._run_inner_loop if startup else run_loop._run_inner_loop_after_startup
    result, _previous, exit_code = loop(state, ctx, str(state.phase))
    return RecoveryObservation(
        result, exit_code, tuple(dispatched), tuple(saved), tuple(warnings), tuple(waits)
    )


def test_exhausted_conflict_ladder_waits_until_recovery_before_ordinary_dispatch(
    monkeypatch: MonkeyPatch,
) -> None:
    state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_action="conflict",
            last_reason="unresolved shared.txt",
            resolution_exhausted=True,
            conflict_strategies_tried=(
                "rebase_resolver: unresolved shared.txt",
                "merge_instead: unresolved shared.txt",
            ),
        ),
    )

    observed = run_recovery_scenario(monkeypatch, state)

    assert observed.exit_code == 0
    assert observed.waits and all(delay > 0 for delay in observed.waits)
    assert [dispatched.phase for dispatched in observed.dispatched] == ["development"]
    assert any("CRITICAL" in warning for warning in observed.warnings)
    assert any(saved.is_waiting_state for saved in observed.saved)
    assert observed.result.rebase.integration_unresolved is False
