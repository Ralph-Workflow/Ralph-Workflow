"""All-mock contracts for the post-commit auto-integrate runner seam.

Drives the real runner loop through the commit phase while every external
collaborator is mocked; no test in this file starts git or writes a repository.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.config.enums import Verbosity
from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import CommitEffect, ExitSuccessEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.rebase_state import RebaseState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.policy.models import PolicyBundle


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _install_runner_display_context(monkeypatch: MonkeyPatch) -> None:
    ctx = make_display_context(force_width=120)
    monkeypatch.setattr(runner_module, "make_display_context", lambda **_kwargs: ctx)


@pytest.fixture(autouse=True)
def _stub_workspace_scope_and_policy(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path)
    )
    monkeypatch.setattr(
        runner_module, "load_policy_or_die", lambda _path: _load_default_policy_bundle()
    )


def _run_commit_phase(
    monkeypatch: MonkeyPatch,
    *,
    integration: object,
) -> tuple[MagicMock, MagicMock]:
    """Run one mocked commit phase and then exit the real runner loop."""
    commit_effect = CommitEffect(message_file="/dev/null")
    calls = 0

    def _determine(*_args: object, **_kwargs: object) -> CommitEffect | ExitSuccessEffect:
        nonlocal calls
        calls += 1
        return commit_effect if calls == 1 else ExitSuccessEffect()

    state = MagicMock()
    state.phase = "development_commit"
    state.rebase = RebaseState()
    state.copy_with = MagicMock(return_value=state)
    reduced = MagicMock(return_value=(state, []))
    monkeypatch.setattr(runner_module, "determine_effect_from_policy", _determine)
    monkeypatch.setattr(runner_module, "execute_commit_effect", lambda *_a, **_k: PipelineEvent.COMMIT_SUCCESS)
    monkeypatch.setattr(runner_module, "materialize_agent_prompt_if_needed", lambda *_a, **_k: None)
    monkeypatch.setattr(runner_module, "clear_cycle_baseline", lambda *_a, **_k: None)
    monkeypatch.setattr(runner_module, "auto_integrate_after_commit", integration)
    monkeypatch.setattr(runner_module, "reducer_reduce", reduced)
    monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
    _install_runner_display_context(monkeypatch)
    runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)
    return state, reduced


def test_commit_success_threads_rebase_state_into_next_state(monkeypatch: MonkeyPatch) -> None:
    """Plan step 5: a successful integration outcome is persisted through the runner."""
    outcome = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    state, reducer = _run_commit_phase(monkeypatch, integration=lambda *_args, **_kwargs: outcome)

    assert reducer.call_args.args[1] is PipelineEvent.COMMIT_SUCCESS
    rebase_calls = [call for call in state.copy_with.call_args_list if "rebase" in call.kwargs]
    assert rebase_calls[-1].kwargs["rebase"] is outcome


def test_commit_skipped_does_not_invoke_auto_integrate(monkeypatch: MonkeyPatch) -> None:
    """Plan step 5: COMMIT_SKIPPED does not call the integration boundary."""
    integration = MagicMock()
    commit_effect = CommitEffect(message_file="/dev/null")
    state = MagicMock()
    state.phase = "development_commit"
    state.rebase = RebaseState()
    state.copy_with = MagicMock(return_value=state)
    calls = 0

    def _determine(*_args: object, **_kwargs: object) -> CommitEffect | ExitSuccessEffect:
        nonlocal calls
        calls += 1
        return commit_effect if calls == 1 else ExitSuccessEffect()

    monkeypatch.setattr(runner_module, "determine_effect_from_policy", _determine)
    monkeypatch.setattr(runner_module, "execute_commit_effect", lambda *_a, **_k: PipelineEvent.COMMIT_SKIPPED)
    monkeypatch.setattr(runner_module, "materialize_agent_prompt_if_needed", lambda *_a, **_k: None)
    monkeypatch.setattr(runner_module, "clear_cycle_baseline", lambda *_a, **_k: None)
    monkeypatch.setattr(runner_module, "auto_integrate_after_commit", integration)
    monkeypatch.setattr(runner_module, "reducer_reduce", MagicMock(return_value=(state, [])))
    monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
    _install_runner_display_context(monkeypatch)
    runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

    integration.assert_not_called()
    assert all("rebase" not in call.kwargs for call in state.copy_with.call_args_list)


def test_commit_conflict_outcome_does_not_halt_run(monkeypatch: MonkeyPatch) -> None:
    """Prompt AC-07, plan step 6: conflict is persisted and the runner reduces success.

    This is a behavioral contract pin, not extra branch coverage: conflict and
    rebased outcomes currently traverse the same non-None seam branch.
    """
    conflict = RebaseState(
        last_action="conflict",
        last_reason="rebase and endpoint merge both conflicted",
        last_target="main",
        fast_forwarded=False,
    )
    state, reducer = _run_commit_phase(monkeypatch, integration=lambda *_args, **_kwargs: conflict)

    assert reducer.call_args.args[1] is PipelineEvent.COMMIT_SUCCESS
    rebase_calls = [call for call in state.copy_with.call_args_list if "rebase" in call.kwargs]
    assert rebase_calls[-1].kwargs["rebase"] is conflict


def test_auto_integrate_exception_does_not_halt_run(monkeypatch: MonkeyPatch) -> None:
    """Prompt AC-07, plan step 6: an integration exception is swallowed by the runner."""
    integration = MagicMock(side_effect=RuntimeError("integration blew up"))
    state, reducer = _run_commit_phase(monkeypatch, integration=integration)

    integration.assert_called_once()
    assert reducer.call_args.args[1] is PipelineEvent.COMMIT_SUCCESS
    assert all("rebase" not in call.kwargs for call in state.copy_with.call_args_list)


def test_commit_success_passes_conflict_resolver_to_integration(
    monkeypatch: MonkeyPatch,
) -> None:
    """The seam hands auto_integrate_after_commit a dev-agent conflict resolver."""
    captured: dict[str, object] = {}

    def _integration(*_args: object, **kwargs: object) -> RebaseState:
        captured.update(kwargs)
        return RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)

    _run_commit_phase(monkeypatch, integration=_integration)

    resolver = captured.get("conflict_resolver")
    assert resolver is not None, "seam must pass a conflict_resolver"
    assert callable(resolver)


def test_phase_transition_event_runs_boundary_integration(
    monkeypatch: MonkeyPatch,
) -> None:
    """A successful non-commit phase event triggers the boundary hook."""
    outcome = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    hook = MagicMock(return_value=outcome)
    monkeypatch.setattr(runner_module, "auto_integrate_on_phase_transition", hook)
    state = MagicMock()
    state.phase = "development_analysis"
    state.rebase = RebaseState()

    result = runner_module._maybe_auto_integrate(
        effect=object(),
        event=PipelineEvent.AGENT_SUCCESS,
        commit_phase_def=None,
        config=MagicMock(),
        workspace_scope=MagicMock(),
        state=state,
        display=MagicMock(),
        policy_bundle=_load_default_policy_bundle(),
        registry=MagicMock(),
    )

    hook.assert_called_once()
    assert result is outcome


def test_commit_skipped_still_integrates_via_boundary_hook(
    monkeypatch: MonkeyPatch,
) -> None:
    """COMMIT_SKIPPED means a clean tree: the boundary hook must catch up.

    The commit-boundary integration (rebase triggered by a NEW commit)
    stays COMMIT_SUCCESS-only, but a skipped commit still re-integrates
    via the phase-transition hook so a target that moved during the
    cycle is caught immediately.
    """
    outcome = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    hook = MagicMock(return_value=outcome)
    monkeypatch.setattr(runner_module, "auto_integrate_on_phase_transition", hook)
    monkeypatch.setattr(runner_module, "clear_cycle_baseline", lambda *_a, **_k: None)
    state = MagicMock()
    state.phase = "development_commit"
    state.rebase = RebaseState()
    commit_def = _load_default_policy_bundle().pipeline.phases["development_commit"]

    result = runner_module._maybe_auto_integrate(
        effect=CommitEffect(message_file="/dev/null"),
        event=PipelineEvent.COMMIT_SKIPPED,
        commit_phase_def=commit_def,
        config=MagicMock(),
        workspace_scope=MagicMock(),
        state=state,
        display=MagicMock(),
        policy_bundle=_load_default_policy_bundle(),
        registry=MagicMock(),
    )

    hook.assert_called_once()
    assert result is outcome


@pytest.mark.parametrize(
    "event",
    [
        PipelineEvent.REVIEW_ISSUES_FOUND,
        PipelineEvent.ALL_WORKERS_COMPLETE,
        PipelineEvent.COMPLETE,
    ],
)
def test_additional_transition_events_trigger_boundary_hook(
    monkeypatch: MonkeyPatch, event: PipelineEvent
) -> None:
    """Review-issues, fan-out completion, and run completion also integrate."""
    hook = MagicMock(return_value=None)
    monkeypatch.setattr(runner_module, "auto_integrate_on_phase_transition", hook)
    state = MagicMock()
    state.phase = "development"
    state.rebase = RebaseState()

    runner_module._maybe_auto_integrate(
        effect=object(),
        event=event,
        commit_phase_def=None,
        config=MagicMock(),
        workspace_scope=MagicMock(),
        state=state,
        display=MagicMock(),
        policy_bundle=_load_default_policy_bundle(),
        registry=MagicMock(),
    )

    hook.assert_called_once()


def test_phase_transition_hook_not_called_on_failure_events(
    monkeypatch: MonkeyPatch,
) -> None:
    """Failure / retry events never trigger the boundary hook."""
    hook = MagicMock()
    monkeypatch.setattr(runner_module, "auto_integrate_on_phase_transition", hook)
    state = MagicMock()
    state.phase = "development"
    state.rebase = RebaseState()

    for event in (
        PipelineEvent.AGENT_FAILURE,
        PipelineEvent.AGENT_RETRY,
        PipelineEvent.INTERRUPTED,
        PipelineEvent.FAILED,
    ):
        result = runner_module._maybe_auto_integrate(
            effect=object(),
            event=event,
            commit_phase_def=None,
            config=MagicMock(),
            workspace_scope=MagicMock(),
            state=state,
            display=MagicMock(),
            policy_bundle=_load_default_policy_bundle(),
            registry=MagicMock(),
        )
        assert result is None
    hook.assert_not_called()


def test_log_outcome_skip_emits_warn_line() -> None:
    """A skipped integration is surfaced as a WARN line, not an info line."""
    display = MagicMock()
    outcome = RebaseState(
        last_action="skipped",
        last_reason="preconditions not met: example",
        last_target="main",
        fast_forwarded=False,
    )

    runner_module._log_auto_integrate_outcome(display, outcome)

    display.emit_warn_line.assert_called_once()
    unit_id, tag, message = display.emit_warn_line.call_args.args
    assert unit_id == "run"
    assert tag == "auto-integrate"
    assert "preconditions not met: example" in message
    display.emit.assert_not_called()


def test_log_outcome_conflict_emits_warn_line() -> None:
    """A conflict outcome is surfaced as a WARN line."""
    display = MagicMock()
    outcome = RebaseState(
        last_action="conflict",
        last_reason="rebase and endpoint merge both conflicted",
        last_target="main",
        fast_forwarded=False,
    )

    runner_module._log_auto_integrate_outcome(display, outcome)

    display.emit_warn_line.assert_called_once()
    display.emit.assert_not_called()


def test_log_outcome_success_emits_info_line() -> None:
    """A successful integration keeps the ordinary activity line."""
    display = MagicMock()
    outcome = RebaseState(
        last_action="rebased", last_target="main", fast_forwarded=True
    )

    runner_module._log_auto_integrate_outcome(display, outcome)

    display.emit.assert_called_once()
    display.emit_warn_line.assert_not_called()


def test_startup_integration_runs_before_loop(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """An old branch is integrated onto the target at run start.

    The loop preamble must run the boundary-integration hook BEFORE the
    first phase executes so a run started on a stale branch begins from
    the current target tip (planning must never read stale code).
    """
    from ralph.pipeline import run_loop as run_loop_module

    outcome = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    hook = MagicMock(return_value=outcome)
    monkeypatch.setattr(
        run_loop_module, "auto_integrate_on_phase_transition", hook
    )
    state = MagicMock()
    state.phase = "complete"
    state.rebase = RebaseState()
    state.copy_with = MagicMock(return_value=state)
    ctx = MagicMock()
    ctx.workspace_scope = WorkspaceScope(tmp_path)
    ctx.policy_bundle.pipeline.terminal_phase = "complete"
    saved = MagicMock()
    monkeypatch.setattr(run_loop_module._runner_module, "save_checkpoint_or_log", saved)
    monkeypatch.setattr(
        run_loop_module._runner_module,
        "_checkpoint_path",
        lambda _scope: tmp_path / ".agent" / "checkpoint.json",
    )

    run_loop_module._run_inner_loop(state, ctx, prev_phase="complete")

    hook.assert_called_once()
    assert state.copy_with.call_args.kwargs["rebase"] is outcome


def test_recovery_outcome_persisted_to_state_and_checkpoint(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Plan step 5: recovery result reaches state and the canonical checkpoint writer."""
    from ralph.pipeline import run_loop as run_loop_module

    recovered = RebaseState(last_action="recovered", last_target="main")
    checkpoint_path = tmp_path / ".agent" / "checkpoint.json"
    state = MagicMock()
    state.phase = "complete"
    state.rebase = RebaseState()
    state.copy_with = MagicMock(return_value=state)
    ctx = MagicMock()
    ctx.workspace_scope = WorkspaceScope(tmp_path)
    ctx.policy_bundle.pipeline.terminal_phase = "complete"
    saved = MagicMock()

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.recover_incomplete_integration",
        lambda _scope: recovered,
    )
    monkeypatch.setattr(run_loop_module._runner_module, "save_checkpoint_or_log", saved)
    monkeypatch.setattr(run_loop_module._runner_module, "_checkpoint_path", lambda _scope: checkpoint_path)

    run_loop_module._run_inner_loop(state, ctx, prev_phase="complete")

    assert state.copy_with.call_args.kwargs["rebase"] is recovered
    assert saved.call_args.args[0] is state
    assert saved.call_args.kwargs["path"] == checkpoint_path
