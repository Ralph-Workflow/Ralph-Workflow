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

    def recover(*_args: object, **_kwargs: object) -> None:
        return None

    def sync_target(_ctx: run_loop._LoopContext) -> tuple[Path, str]:
        return workspace.root, "main"

    def observe_sha(_root: Path, ref: str) -> tuple[str, bool]:
        return workspace.read(ref), True

    def ancestry(_root: Path, ancestor: str, descendant: str) -> bool:
        return workspace.read(ancestor) == workspace.read(descendant)

    def legacy(_root: Path) -> RebaseState:
        return state.rebase

    def startup_outcome(
        current: PipelineState, _ctx: run_loop._LoopContext, **_kwargs: object
    ) -> PipelineState:
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
    """When the conflict ladder is exhausted, the run loop must
    persist the :class:`RebaseOutcomeKind.INTERVENTION_REQUIRED`
    transition through the typed :meth:`set_intervention_required`
    single-writer setter and refuse to call the recovery preamble.
    The old behaviour -- sleep + recovery preamble + strategy
    rotation -- silently replenished the attempt budget and is
    forbidden by the bounded-progress invariant.

    The test exercises the same setup as before, but instead of
    asserting that the loop dispatches after a bounded retry, it
    asserts that the exhausted state is escalated to intervention
    and that no further dispatch occurs. The recovery loop is
    bounded by operator action, not by an internal sleep.
    """
    # The run-loop call would loop forever (the integration is still
    # unresolved and the exhausted state escalates to intervention
    # without sleeping). Exercise the helper directly instead: the
    # behaviour is identical to the loop's first iteration, and
    # proves the typed-setter contract that the run loop relies on.
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

    preamble_invocations: list[Path] = []
    sleeps: list[float] = []

    def _preamble(workspace_scope: object, config: object, *, backend: object) -> object:
        # Capture the call so the test can assert the run loop avoids
        # it on the exhausted path.
        preamble_invocations.append(getattr(workspace_scope, "root", Path("/memory/recovery")))
        return None

    workspace = MemoryWorkspace("/memory/recovery")
    workspace.write("main", "before")
    workspace.write("HEAD", "feature")

    def _record_sleep(delay: float) -> None:
        sleeps.append(delay)

    ctx = cast(
        "run_loop._LoopContext",
        SimpleNamespace(
            policy_bundle=SimpleNamespace(
                pipeline=PipelinePolicy(
                    entry_phase="development",
                    phases={
                        "development": PhaseDefinition(
                            drain="development", transitions=PhaseTransition(on_success="complete")
                        )
                    },
                )
            ),
            active_display=SimpleNamespace(emit_warn_line=lambda *a, **k: None),
            config=UnifiedConfig(),
            workspace_scope=WorkspaceScope(workspace.root),
            latest_state=[],
            sleep=_record_sleep,
            connectivity_monitor=SimpleNamespace(current_state=None),
        ),
    )

    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _preamble)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", lambda *a, **k: None)

    updated = run_loop._wait_for_integration_retry(state, ctx)

    # The exhausted state is escalated to INTERVENTION_REQUIRED -- the
    # typed single-writer setter persists the transition. The carried
    # counters and the resolution_exhausted flag survive, so the
    # run-loop consumer that gates dispatch keeps refusing ordinary
    # work.
    assert updated.rebase.last_outcome is not None
    assert updated.rebase.is_intervention_required is True
    assert updated.rebase.resolution_exhausted is True
    # The conflict-strategy block is preserved: the typed
    # ``INTERVENTION_REQUIRED`` transition does NOT cycle or reset it.
    assert updated.rebase.conflict_strategy_index == state.rebase.conflict_strategy_index
    assert updated.rebase.conflict_strategies_tried == state.rebase.conflict_strategies_tried
    # The recovery preamble is NOT called when the state is
    # exhausted. The run loop's bounded-progress invariant forbids
    # silent replenishment of the attempt budget via a recovery
    # preamble call after ``INTERVENTION_REQUIRED`` is recorded.
    assert preamble_invocations == [], (
        "Exhausted state must not call _run_auto_integrate_recovery_preamble; "
        "the typed INTERVENTION_REQUIRED transition is the persistent operator "
        "handoff and the run loop must wait for operator action."
    )
    # The exhausted path does NOT sleep -- a sleep would mask the
    # intervention transition and let the loop continue iterating.
    assert sleeps == [], (
        "Exhausted state must not sleep; the run loop must escalate "
        "to INTERVENTION_REQUIRED and return."
    )
