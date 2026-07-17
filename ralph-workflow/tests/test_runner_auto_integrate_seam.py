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
    state, reducer = _run_commit_phase(monkeypatch, integration=lambda *_args: outcome)

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
    state, reducer = _run_commit_phase(monkeypatch, integration=lambda *_args: conflict)

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
