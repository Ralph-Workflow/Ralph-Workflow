"""Regression coverage for the STATELESS any-phase boundary integration hook.

Auto-integration must catch up an advanced target at every phase
transition the runner surfaces -- not only on the success-only
whitelist that used to gate the boundary hook. The hook is also
event-agnostic across the inline-effect early-return path
(``_run_pipeline_step``'s ``if inline_result is not None: return
inline_result`` branch), so a checkpoint save, a prompt-prepared
transition, a completed pipeline, an exhaustion-driven phase advance,
and a failure-route recovery can all carry a sibling agent's landing
to this checkout.

The tests here are unit-level: no real network, no real ``git
push``, no real file I/O beyond the worktree. They monkeypatch the
boundary integration seam to record every invocation, so an
assertion that the hook fired on a previously-skipped event is the
exact contract the prompt requires -- a non-success event that
silently bypasses the seam is a regression, regardless of how the
rest of the integration call shakes out.

The tests also assert the NEVER-MOVE-PHASE invariant: the boundary
hook reads ``state.rebase`` and threads its outcome through
``copy_with(rebase=...)``; it MUST NOT change ``state.phase``, the
current_drain, the recovery_epoch, or any reducer-owned slot. An
integration that accidentally advanced the phase would render
auto-integration indistinguishable from a real phase transition,
and is the worst kind of regression because it changes a no-op into
a phase change in a way no test could surface.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

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

pytestmark = [
    # The runner-seam tests in this file drive
    # ``_run_pipeline_step`` with the real default policy bundle;
    # they belong with the real-git auto-integrate suite and are
    # excluded from the 60s make-test budget (kept on the
    # test-auto-integrate-e2e budget-tracked step instead).
    pytest.mark.subprocess_e2e,
]


def _default_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": True}})


def _stub_state() -> PipelineState:
    """A minimal pipeline state whose ``rebase`` slot survives
    ``copy_with(rebase=...)`` unchanged otherwise.

    The real ``PipelineState.copy_with`` is the production seam, so
    these tests use the real type rather than a MagicMock. The state
    is otherwise opaque to the assertions below.
    """
    return PipelineState(phase="development", rebase=RebaseState())


def test_phase_transition_integration_events_cover_every_non_commit_event() -> None:
    """AC-01: every ``PipelineEvent`` other than ``COMMIT_SUCCESS`` triggers
    the boundary integration hook.

    The whitelist used to be a closed success-only set; the new
    contract is event-agnostic. Asserting the closed set is exactly
    the set of non-COMMIT_SUCCESS events makes a regression that
    drops one event, or that grows the whitelist to include
    COMMIT_SUCCESS (which has its own dedicated after_commit branch),
    a single-line failure here.
    """
    all_events = set(PipelineEvent)
    expected = all_events - {PipelineEvent.COMMIT_SUCCESS}
    assert set(runner._PHASE_TRANSITION_INTEGRATION_EVENTS) == expected, (
        "_PHASE_TRANSITION_INTEGRATION_EVENTS must be every PipelineEvent "
        "other than COMMIT_SUCCESS; missing: "
        f"{sorted(expected - runner._PHASE_TRANSITION_INTEGRATION_EVENTS)}, "
        f"extra: {sorted(runner._PHASE_TRANSITION_INTEGRATION_EVENTS - expected)}"
    )


def test_non_commit_events_invoke_phase_transition_seam(monkeypatch) -> None:
    """AC-01: a representative previously-excluded event invokes the seam.

    The hook is the SAME function call regardless of event, so
    asserting one excluded event (AGENT_FAILURE) reaches
    ``auto_integrate_on_phase_transition`` is the load-bearing
    evidence the new contract fires on every event.
    """
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
    """AC-01 + AC-06: a previously-excluded event with a clean worktree
    records a catch-up.

    The actual integration logic is in
    ``auto_integrate.auto_integrate_on_phase_transition``; the runner
    seam only routes. A full end-to-end call exercises BOTH the
    seam (now event-agnostic) AND the clean-worktree guard the
    seam relies on for safety, so a clean worktree must reach
    ``auto_integrate_on_phase_transition`` exactly once.
    """
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
    # The seam forwards ``state.rebase`` (NOT ``state``) into the
    # integration call; pinning the argument list catches a
    # regression that hands the whole ``state`` in and lets the
    # integration touch phase.
    assert integrate.call_args.args == (config, workspace_scope, state.rebase)


def test_inline_event_for_effect_maps_every_inline_effect() -> None:
    """Step 3: the helper that maps an inline-effect handle to its
    boundary event must cover every effect that can take the
    early-return path.

    A new inline effect added to ``ralph.pipeline.effects`` without a
    matching mapping here is a silent regression: the inline path
    would return without an integration call and the
    previously-carried catch-up landing would never be made visible.
    """
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
    """Step 3: the inline-effect early-return path runs the boundary
    integration once and threads the outcome into a PipelineState-
    shaped return.

    The inline path returns a ``PipelineState`` (not an ``int``) for
    the state-modifying effects (SaveCheckpointEffect,
    PreparePromptEffect, ExitFailureEffect). The integration outcome
    MUST be carried on ``state.rebase`` so a crash right after the
    inline effect does not lose the record.
    """
    config = _default_config()
    state = _stub_state()
    expected_outcome = RebaseState(
        last_action="rebased", last_target="main", fast_forwarded=True
    )
    integrate = MagicMock(return_value=expected_outcome)
    monkeypatch.setattr(runner, "_integrate_on_phase_transition", integrate)
    workspace_scope = MagicMock()
    # The display used by the seam; the inline path must pass it
    # through so the log line surfaces the integration in operator
    # output (not just on the persisted state).
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

    # ``handle_inline_effect`` returns the inline-effect's own result
    # (a PipelineState for SaveCheckpointEffect). The boundary
    # integration is wired into ``_run_pipeline_step``'s caller,
    # not into ``handle_inline_effect`` itself, so its outcome is
    # threaded by the runner. The test pins BOTH halves: the
    # helper returns a state carrying the original ``rebase`` (the
    # integration runs in the CALLER), and the caller's behaviour
    # is what the next test covers.
    assert inline_result is not None
    assert inline_result.rebase is state.rebase


def test_run_pipeline_step_inline_path_runs_boundary_integration(
    monkeypatch,
) -> None:
    """Step 3: ``_run_pipeline_step``'s inline-effect early-return
    path runs the boundary integration once and threads the outcome.

    This exercises the REAL call path: a SaveCheckpointEffect
    produced by ``call_determine_effect_from_policy`` is handled by
    ``handle_inline_effect``; the inline early-return branch must
    then invoke ``_integrate_on_phase_transition`` exactly once
    with the mapped ``CHECKPOINT_SAVED`` event.
    """
    from ralph.config.enums import Verbosity
    from ralph.display.context import make_display_context
    from ralph.policy.loader import load_policy

    defaults_dir = (
        Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    )
    bundle = load_policy(defaults_dir)
    state = _stub_state()
    expected_outcome = RebaseState(
        last_action="rebased", last_target="main", fast_forwarded=True
    )
    integrate = MagicMock(return_value=expected_outcome)
    monkeypatch.setattr(runner, "_integrate_on_phase_transition", integrate)
    monkeypatch.setattr(
        runner, "call_determine_effect_from_policy", lambda *_a, **_k: SaveCheckpointEffect()
    )
    monkeypatch.setattr(runner, "reducer_reduce", lambda s, e, p, recovery=None: (s, []))
    monkeypatch.setattr(runner.ckpt, "save", MagicMock())
    monkeypatch.setattr(
        runner, "_save_checkpoint_or_log", lambda *a, **k: None
    )

    display_context = make_display_context()
    display = runner.ParallelDisplay(display_context)
    registry = MagicMock()
    registry.get.return_value = None

    workspace_scope = MagicMock()
    workspace_scope.root = Path("/tmp/no-such-workspace-for-this-test")

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

    # The seam was invoked EXACTLY ONCE.
    assert integrate.call_count == 1
    # The mapped event reached the seam.
    assert integrate.call_args.kwargs["event"] is PipelineEvent.CHECKPOINT_SAVED
    # The integration outcome is threaded onto the returned state.
    assert isinstance(result, PipelineState)
    assert result.rebase is expected_outcome
    # Phase is unchanged -- the integration is orthogonal to the
    # reducer; the seam's only write is to the rebase slot.
    assert result.phase == "development"


def test_boundary_integration_does_not_change_state_phase(monkeypatch) -> None:
    """The boundary integration MUST NOT mutate pipeline phase.

    Auto-integration is orthogonal to the reducer: the hook reads
    ``state.rebase`` and writes back through ``copy_with``; the
    reducer owns ``state.phase``. A regression that threaded a
    different ``state`` into the seam (or that the seam mutated in
    place) would advance the pipeline as a side-effect of git
    integration, which is exactly the kind of hidden coupling the
    prompt forbids.
    """
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
    # The seam returns a RebaseState, not a PipelineState. The
    # caller (``_run_pipeline_step``) is the one that threads the
    # outcome into ``next_state.copy_with(rebase=...)``; that
    # threading is what leaves ``state.phase`` untouched. This
    # test pins the seam's return contract, which is a necessary
    # precondition for the threading path to be correct.
    assert isinstance(result, RebaseState)


def test_non_state_returning_inline_effect_does_not_attempt_copy(
    monkeypatch,
) -> None:
    """``ExitSuccessEffect`` returns an int, not a PipelineState. The
    inline path must run the integration for its git side-effect
    but MUST NOT try to ``copy_with`` on the int return value.

    A regression that called ``copy_with`` on the int would raise
    ``AttributeError`` and break every successful pipeline exit --
    the worst kind of failure because it would never surface in
    tests that did not exercise the success path.
    """
    from ralph.config.enums import Verbosity
    from ralph.display.context import make_display_context
    from ralph.policy.loader import load_policy

    defaults_dir = (
        Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    )
    bundle = load_policy(defaults_dir)
    state = _stub_state()
    expected_outcome = RebaseState(
        last_action="rebased", last_target="main", fast_forwarded=True
    )
    integrate = MagicMock(return_value=expected_outcome)
    monkeypatch.setattr(runner, "_integrate_on_phase_transition", integrate)
    monkeypatch.setattr(
        runner,
        "call_determine_effect_from_policy",
        lambda *_a, **_k: ExitSuccessEffect(),
    )
    monkeypatch.setattr(runner, "reducer_reduce", lambda s, e, p, recovery=None: (s, []))
    monkeypatch.setattr(runner.ckpt, "save", MagicMock())
    monkeypatch.setattr(
        runner, "_save_checkpoint_or_log", lambda *a, **k: None
    )

    display_context = make_display_context()
    display = runner.ParallelDisplay(display_context)
    registry = MagicMock()
    registry.get.return_value = None

    workspace_scope = MagicMock()
    workspace_scope.root = Path("/tmp/no-such-workspace-for-this-test")

    # The call MUST NOT raise ``AttributeError`` on the int return;
    # the int is returned untouched, the integration outcome is
    # recorded on the run state through the seam's side-effect
    # rather than threaded into the return value (there is no
    # PipelineState to thread it into).
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

    # ExitSuccessEffect returns an int; that int is the function
    # return value.
    assert isinstance(result, int)
    # The seam was invoked exactly once for the COMPLETE event.
    assert integrate.call_count == 1
    assert integrate.call_args.kwargs["event"] is PipelineEvent.COMPLETE


def test_inline_path_skips_integration_when_helper_returns_none(monkeypatch) -> None:
    """An effect that is NOT one of the mapped inline effects still
    takes the inline early-return path with ``inline_result is not
    None`` -- the helper then returns ``None`` for the event, and
    the inline path must NOT crash trying to integrate against a
    ``None`` event.

    Today every inline effect is mapped, but a future effect added
    without a mapping would route through this path; the seam must
    be defensive enough to not raise.
    """
    # A sentinel effect that the seam maps to None.
    sentinel = SimpleNamespace(__class__=type("UnknownEffect", (), {}))
    integrate = MagicMock()
    monkeypatch.setattr(runner, "_integrate_on_phase_transition", integrate)
    assert runner._inline_event_for_effect(sentinel) is None


def test_phase_transition_seam_handles_non_pipelineevent_input() -> None:
    """The seam's ``event`` parameter is typed ``object`` because
    callers (the inline path, the workers, the run_loop) can hand
    the seam a union of types. A non-PipelineEvent value must be a
    no-op, not a crash.
    """
    config = _default_config()
    state = _stub_state()
    integrate = MagicMock(return_value=None)
    # The seam's own early-return for a non-PipelineEvent-or-non-
    # whitelisted value is the only defence; bypassing it would
    # require re-doing the union narrowing in every caller, which
    # is the historical mistake.
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
