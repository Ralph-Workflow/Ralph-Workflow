from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.config.models import UnifiedConfig
from ralph.pipeline import runner
from ralph.pipeline.effects import (
    ExitFailureEffect,
    ExitSuccessEffect,
    PreparePromptEffect,
    SaveCheckpointEffect,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PolicyBundle

if TYPE_CHECKING:
    from ralph.policy.models import AgentsPolicy, ArtifactsPolicy, PipelinePolicy


def _default_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": True}})


def _stub_state() -> PipelineState:
    return PipelineState(phase="development", rebase=RebaseState())


def _policy_bundle(
    default_policy: tuple[AgentsPolicy, PipelinePolicy, ArtifactsPolicy],
) -> PolicyBundle:
    agents, pipeline, artifacts = default_policy
    return PolicyBundle.model_construct(
        agents=agents,
        pipeline=pipeline,
        artifacts=artifacts,
    )


def test_phase_transition_integration_events_cover_every_non_commit_event() -> None:
    all_events = set(PipelineEvent)
    expected = all_events - {PipelineEvent.COMMIT_SUCCESS}
    assert set(runner._PHASE_TRANSITION_INTEGRATION_EVENTS) == expected, (
        "_PHASE_TRANSITION_INTEGRATION_EVENTS must be every PipelineEvent "
        "other than COMMIT_SUCCESS; missing: "
        f"{sorted(expected - runner._PHASE_TRANSITION_INTEGRATION_EVENTS)}, "
        f"extra: {sorted(runner._PHASE_TRANSITION_INTEGRATION_EVENTS - expected)}"
    )


def test_non_commit_events_invoke_phase_transition_seam(monkeypatch) -> None:
    config = _default_config()
    workspace_scope = MagicMock()
    state = _stub_state()
    outcome = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    integrate = MagicMock(return_value=outcome)
    monkeypatch.setattr(runner, "auto_integrate_on_phase_transition", integrate)

    result = runner._integrate_on_phase_transition(
        event=PipelineEvent.AGENT_FAILURE,
        config=config,
        workspace_scope=workspace_scope,
        state=state,
        display=MagicMock(),
        policy_bundle=None,
        registry=None,
    )

    assert result is outcome
    assert integrate.call_count == 1


def test_non_commit_events_with_clean_worktree_do_catch_up(monkeypatch) -> None:
    config = _default_config()
    workspace_scope = MagicMock()
    state = _stub_state()
    integrate = MagicMock(return_value=RebaseState(last_action="rebased"))
    monkeypatch.setattr(runner, "auto_integrate_on_phase_transition", integrate)

    runner._integrate_on_phase_transition(
        event=PipelineEvent.CHECKPOINT_SAVED,
        config=config,
        workspace_scope=workspace_scope,
        state=state,
        display=MagicMock(),
        policy_bundle=None,
        registry=None,
    )

    assert integrate.call_count == 1
    assert integrate.call_args.args == (config, workspace_scope, state.rebase)


def test_inline_event_for_effect_maps_every_inline_effect() -> None:
    mapped = {
        SaveCheckpointEffect(): PipelineEvent.CHECKPOINT_SAVED,
        PreparePromptEffect(phase="development"): PipelineEvent.PROMPT_PREPARED,
        ExitSuccessEffect(): PipelineEvent.COMPLETE,
        ExitFailureEffect(reason="boom"): PipelineEvent.FAILED,
    }
    for effect, expected in mapped.items():
        assert runner._inline_event_for_effect(effect) is expected, (
            f"inline effect {type(effect).__name__} must map to {expected!r}, "
            f"got {runner._inline_event_for_effect(effect)!r}"
        )


def test_inline_path_threads_outcome_into_returned_state(monkeypatch) -> None:
    config = _default_config()
    state = _stub_state()
    expected_outcome = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    integrate = MagicMock(return_value=expected_outcome)
    monkeypatch.setattr(runner, "_integrate_on_phase_transition", integrate)
    monkeypatch.setattr(runner.ckpt, "save", lambda *_args: None)
    workspace_scope = MagicMock()
    display = MagicMock()

    from ralph.pipeline.runner import handle_inline_effect

    inline_result = handle_inline_effect(
        effect=SaveCheckpointEffect(),
        state=state,
        pipeline_policy=SimpleNamespace(),
        artifacts_policy=SimpleNamespace(),
        workspace_scope=workspace_scope,
        config=config,
        display=display,
    )

    assert inline_result is not None
    assert inline_result.rebase is state.rebase


def test_run_pipeline_step_inline_path_runs_boundary_integration(
    monkeypatch,
    default_policy,
) -> None:
    from ralph.config.enums import Verbosity
    from ralph.display.context import make_display_context

    bundle = _policy_bundle(default_policy)
    state = _stub_state()
    expected_outcome = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    integrate = MagicMock(return_value=expected_outcome)
    monkeypatch.setattr(runner, "_integrate_on_phase_transition", integrate)
    monkeypatch.setattr(
        runner, "call_determine_effect_from_policy", lambda *_a, **_k: SaveCheckpointEffect()
    )
    monkeypatch.setattr(
        runner, "reducer_reduce", lambda s, e, p, recovery=None, routing_timing=None: (s, [])
    )
    monkeypatch.setattr(runner.ckpt, "save", MagicMock())
    monkeypatch.setattr(runner, "_save_checkpoint_or_log", lambda *a, **k: None)

    display_context = make_display_context()
    display = runner.ParallelDisplay(display_context)
    registry = MagicMock()
    registry.get.return_value = None

    workspace_scope = MagicMock()
    workspace_scope.root = Path("/workspace")

    result = runner._run_pipeline_step(
        state=state,
        policy_bundle=bundle,
        workspace_scope=workspace_scope,
        config=_default_config(),
        display=display,
        display_context=display_context,
        verbosity=Verbosity.QUIET,
        registry=registry,
        pipeline_subscriber=None,
    )

    assert integrate.call_count == 1
    assert integrate.call_args.kwargs["event"] is PipelineEvent.CHECKPOINT_SAVED
    assert isinstance(result, PipelineState)
    assert result.rebase is expected_outcome
    assert result.phase == "development"


def test_boundary_integration_does_not_change_state_phase(monkeypatch) -> None:
    config = _default_config()
    state = _stub_state()
    integrate = MagicMock(return_value=RebaseState(last_action="rebased"))
    monkeypatch.setattr(runner, "auto_integrate_on_phase_transition", integrate)

    result = runner._integrate_on_phase_transition(
        event=PipelineEvent.AGENT_RETRY,
        config=config,
        workspace_scope=MagicMock(),
        state=state,
        display=MagicMock(),
        policy_bundle=None,
        registry=None,
    )

    assert result is not None
    assert isinstance(result, RebaseState)


def test_non_state_returning_inline_effect_does_not_attempt_copy(
    monkeypatch,
    default_policy,
) -> None:
    from ralph.config.enums import Verbosity
    from ralph.display.context import make_display_context

    bundle = _policy_bundle(default_policy)
    state = _stub_state()
    expected_outcome = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    integrate = MagicMock(return_value=expected_outcome)
    monkeypatch.setattr(runner, "_integrate_on_phase_transition", integrate)
    monkeypatch.setattr(
        runner,
        "call_determine_effect_from_policy",
        lambda *_a, **_k: ExitSuccessEffect(),
    )
    monkeypatch.setattr(
        runner, "reducer_reduce", lambda s, e, p, recovery=None, routing_timing=None: (s, [])
    )
    monkeypatch.setattr(runner.ckpt, "save", MagicMock())
    monkeypatch.setattr(runner, "_save_checkpoint_or_log", lambda *a, **k: None)

    display_context = make_display_context()
    display = runner.ParallelDisplay(display_context)
    registry = MagicMock()
    registry.get.return_value = None

    workspace_scope = MagicMock()
    workspace_scope.root = Path("/workspace")

    result = runner._run_pipeline_step(
        state=state,
        policy_bundle=bundle,
        workspace_scope=workspace_scope,
        config=_default_config(),
        display=display,
        display_context=display_context,
        verbosity=Verbosity.QUIET,
        registry=registry,
        pipeline_subscriber=None,
    )

    assert isinstance(result, int)
    assert integrate.call_count == 1
    assert integrate.call_args.kwargs["event"] is PipelineEvent.COMPLETE


def test_inline_path_skips_integration_when_helper_returns_none(monkeypatch) -> None:
    sentinel = SimpleNamespace(__class__=type("UnknownEffect", (), {}))
    integrate = MagicMock()
    monkeypatch.setattr(runner, "_integrate_on_phase_transition", integrate)
    assert runner._inline_event_for_effect(sentinel) is None


def test_phase_transition_seam_handles_non_pipelineevent_input() -> None:
    config = _default_config()
    state = _stub_state()
    integrate = MagicMock(return_value=None)
    result = runner._integrate_on_phase_transition(
        event="not-a-pipeline-event",
        config=config,
        workspace_scope=MagicMock(),
        state=state,
        display=MagicMock(),
        policy_bundle=None,
        registry=None,
    )
    assert result is None
    integrate.assert_not_called()


def test_exit_success_inline_conflict_returns_failure_not_success(
    monkeypatch,
    default_policy,
) -> None:
    from ralph.config.enums import Verbosity
    from ralph.display.context import make_display_context

    bundle = _policy_bundle(default_policy)
    state = _stub_state()
    conflict = RebaseState(last_action="conflict", last_reason="resolver exhausted")
    monkeypatch.setattr(runner, "_integrate_on_phase_transition", MagicMock(return_value=conflict))
    monkeypatch.setattr(
        runner, "call_determine_effect_from_policy", lambda *_a, **_k: ExitSuccessEffect()
    )
    monkeypatch.setattr(
        runner, "reducer_reduce", lambda s, e, p, recovery=None, routing_timing=None: (s, [])
    )
    monkeypatch.setattr(runner.ckpt, "save", MagicMock())
    monkeypatch.setattr(runner, "_save_checkpoint_or_log", lambda *a, **k: None)
    display_context = make_display_context()
    display = runner.ParallelDisplay(display_context)
    registry = MagicMock()
    registry.get.return_value = None
    workspace_scope = MagicMock()
    workspace_scope.root = Path("/workspace")

    result = runner._run_pipeline_step(
        state=state,
        policy_bundle=bundle,
        workspace_scope=workspace_scope,
        config=_default_config(),
        display=display,
        display_context=display_context,
        verbosity=Verbosity.QUIET,
        registry=registry,
        pipeline_subscriber=None,
    )

    assert result == 1
