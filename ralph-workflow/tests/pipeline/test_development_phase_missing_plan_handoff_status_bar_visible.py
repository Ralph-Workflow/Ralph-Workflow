from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.config.verbosity import Verbosity
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.status_bar import StatusBar, StatusBarModel
from ralph.pipeline import run_loop as run_loop_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.run_loop import (
    _LoopContext,
    _run_inner_loop,
)
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.prompts._missing_plan_handoff_error import MissingPlanHandoffError
from ralph.recovery.connectivity import ConnectivityState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


class _TtyLikeStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


def _load_default_policy_bundle() -> PolicyBundle:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _make_parallel_display(width: int = 120) -> ParallelDisplay:
    buf = _TtyLikeStringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=width,
        color_system="standard",
    )
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx)


def _patched_update_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> list[StatusBarModel]:
    captured: list[StatusBarModel] = []

    real_update_status_bar = ParallelDisplay.update_status_bar

    def recording_update_status_bar(self: ParallelDisplay, model: StatusBarModel) -> None:
        if not isinstance(model, StatusBarModel):
            msg = f"update_status_bar requires a StatusBarModel, got {type(model).__name__}"
            raise TypeError(msg)
        captured.append(model)
        real_update_status_bar(self, model)

    monkeypatch.setattr(ParallelDisplay, "update_status_bar", recording_update_status_bar)
    return captured


def _make_loop_context(
    *,
    active_display: ParallelDisplay,
    workspace_root: Path,
    policy_bundle: PolicyBundle,
) -> _LoopContext:
    buf = _TtyLikeStringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=120,
        color_system="standard",
    )
    display_context = make_display_context(console=console, env={})

    class _OnlineMonitor:
        current_state: ConnectivityState = ConnectivityState.ONLINE

    return _LoopContext(
        policy_bundle=policy_bundle,
        workspace_scope=WorkspaceScope(
            root=workspace_root, allowed_roots=frozenset({workspace_root})
        ),
        config=None,
        active_display=active_display,
        display_context=display_context,
        effective_verbosity=Verbosity.NORMAL,
        registry=type("R", (), {})(),
        effective_pipeline_subscriber=None,
        controller=type("C", (), {})(),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=_OnlineMonitor(),
        sleep=lambda _s: None,
        is_quiet=False,
        snapshot_registry=None,
    )


def _patch_run_pipeline_step_for_single_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_run_step = runner_module.run_pipeline_step
    call_count = {"n": 0}

    def wrapper(*args: object, **kwargs: object) -> PipelineState | int:
        call_count["n"] += 1
        if call_count["n"] >= 2:
            return 0
        return real_run_step(*args, **kwargs)

    monkeypatch.setattr(runner_module, "run_pipeline_step", wrapper)


def _patch_materialize_to_raise_for_development_only(
    monkeypatch: pytest.MonkeyPatch,
) -> list[PipelineState]:
    real_materialize_prepared = runner_module.materialize_prepared_prompt
    real_materialize_agent = runner_module.materialize_agent_prompt_if_needed
    states_observed: list[PipelineState] = []

    dev_msg = (
        "Template 'developer_iteration.jinja' requires an existing plan handoff at .agent/PLAN.md"
    )

    def raising_materialize_prepared(
        *args: object,
        state: PipelineState | None = None,
        **kwargs: object,
    ) -> None:
        states_observed.append(state)
        if state is not None and str(state.phase) == "development":
            raise MissingPlanHandoffError(dev_msg)
        real_materialize_prepared(*args, **kwargs)

    def raising_materialize_agent(
        *args: object,
        **kwargs: object,
    ) -> None:
        if args:
            effect = args[0]
            phase = getattr(effect, "phase", None)
            if phase is not None and str(phase) == "development":
                state = args[1] if len(args) > 1 else kwargs.get("state")
                if state is not None:
                    states_observed.append(state)
                raise MissingPlanHandoffError(dev_msg)
        real_materialize_agent(*args, **kwargs)

    monkeypatch.setattr(runner_module, "materialize_prepared_prompt", raising_materialize_prepared)
    monkeypatch.setattr(
        runner_module, "materialize_agent_prompt_if_needed", raising_materialize_agent
    )
    return states_observed


@pytest.mark.timeout_seconds(15)
def test_development_phase_missing_plan_handoff_recovers_and_status_bar_remains_visible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        run_loop_module,
        "_apply_connectivity_check",
        lambda current_state, _monitor: current_state,
    )
    pipeline_bundle = _load_default_policy_bundle()
    pipeline_policy = pipeline_bundle.pipeline

    assert pipeline_policy.entry_phase == "planning", (
        "Default pipeline must compile entry_phase='planning' "
        "(defaults/pipeline.toml entry_block='developer_iteration' → first child phase)."
    )

    pd = _make_parallel_display(width=120)
    assert pd._ctx.console.is_terminal is True
    assert pd._ctx.console.file.isatty() is True
    assert isinstance(pd.status_bar, StatusBar)

    captured = _patched_update_recorder(monkeypatch)

    states_observed = _patch_materialize_to_raise_for_development_only(monkeypatch)

    _patch_run_pipeline_step_for_single_iteration(monkeypatch)

    state = PipelineState(
        phase="development",
        previous_phase=None,
        checkpoint_saved_count=0,
        recovery_epoch=0,
    )

    loop_ctx = _make_loop_context(
        active_display=pd,
        workspace_root=tmp_path,
        policy_bundle=pipeline_bundle,
    )

    final_state, _prev_phase, early_exit = _run_inner_loop(
        state, loop_ctx, prev_phase="development"
    )

    assert early_exit == 0, (
        f"_run_inner_loop must exit via the int early-exit branch "
        f"after one bounded recovery iteration; got {early_exit!r}"
    )

    assert states_observed, (
        "AC-11: materialize_prepared_prompt must have been called at "
        "least once; got empty observations list"
    )
    assert str(states_observed[0].phase) == "development", (
        f"AC-11: materialize_prepared_prompt must observe the "
        f"development state; got phase={states_observed[0].phase!r}"
    )

    assert len(captured) >= 1, (
        f"AC-11: ParallelDisplay.update_status_bar must be invoked at "
        f"least once via the production run-loop path; "
        f"got {len(captured)} captures"
    )
    planning_pushes = [m for m in captured if m.phase_label.startswith("Planning")]
    assert planning_pushes, (
        f"AC-11: at least one captured StatusBarModel must have "
        f"phase_label starting with 'Planning'; got "
        f"phase_labels={[m.phase_label for m in captured]!r}"
    )

    planning_push = planning_pushes[0]
    assert isinstance(planning_push, StatusBarModel)
    assert planning_push.workspace_root == str(tmp_path), (
        f"AC-11: workspace_root on the recovered-phase push must be "
        f"the active workspace root; got {planning_push.workspace_root!r}"
    )
    assert planning_push.phase_label.startswith("Planning"), (
        f"AC-11: phase_label on the captured push must start with "
        f"'Planning'; got {planning_push.phase_label!r}"
    )

    last_model = pd.status_bar.last_model
    assert last_model is not None, (
        "AC-11: StatusBar.last_model must be populated after recovery; "
        "the operator's footer would otherwise be stale"
    )
    assert last_model.phase_label.startswith("Planning"), (
        f"AC-11: StatusBar.last_model.phase_label must reflect the "
        f"recovered planning phase; got {last_model.phase_label!r}"
    )
    assert last_model.workspace_root == str(tmp_path), (
        f"AC-11: StatusBar.last_model.workspace_root must be the "
        f"active workspace root; got {last_model.workspace_root!r}"
    )

    assert final_state.phase == "planning", (
        f"AC-11: final state.phase must be 'planning' (entry_phase); got {final_state.phase!r}"
    )
    assert final_state.recovery_epoch == 1, (
        f"AC-11: final state.recovery_epoch must be 1 after one "
        f"bounded recovery iteration; got {final_state.recovery_epoch}"
    )
    assert "plan handoff" in (final_state.last_error or ""), (
        f"AC-11: final state.last_error must carry the underlying "
        f"MissingPlanHandoffError message; got {final_state.last_error!r}"
    )
