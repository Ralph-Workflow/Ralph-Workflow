"""Deterministic runner-seam regressions for default auto-integration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate, auto_integrate_ff, runner
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.rebase_state import RebaseState


def _default_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": True}})


def test_default_config_resolves_main_and_lands_dirty_worktree_ref(monkeypatch) -> None:
    """Analysis regression: default worktree integration resolves and lands main.

    This is the in-budget half of the real-Git worktree regression. It
    proves the default-target and dirty-checkout CAS decisions without
    filesystem or subprocess I/O; the E2E test retains the real Git proof.
    """
    config = _default_config()
    root = Path("/workspace/feature")
    monkeypatch.setattr(auto_integrate, "resolve_origin_head_branch", lambda _root: None)
    monkeypatch.setattr(
        auto_integrate,
        "branch_exists",
        lambda _root, branch: branch == "main",
    )

    target = auto_integrate.resolve_integration_target(config, root)

    monkeypatch.setattr(auto_integrate_ff, "branch_sha", lambda _root, _branch: "old-main")
    monkeypatch.setattr(auto_integrate_ff, "is_ancestor", lambda *_args: True)
    monkeypatch.setattr(auto_integrate_ff, "find_main_worktree_root", lambda _root: root)
    monkeypatch.setattr(auto_integrate_ff, "worktree_for_branch", lambda _root, _branch: root)
    monkeypatch.setattr(auto_integrate_ff, "is_repo_clean", lambda _root: False)
    cas = MagicMock(return_value=True)
    monkeypatch.setattr(auto_integrate_ff, "compare_and_swap_branch", cas)

    assert target == "main"
    assert auto_integrate_ff.fast_forward_target(root, target, "feature-head") == (True, "")
    assert cas.call_args.args == (root, "main", "old-main", "feature-head")


def test_commit_seam_invokes_auto_integrate(monkeypatch) -> None:
    """Plan step 2: a successful commit uses the unset-target config path."""
    config = _default_config()
    outcome = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    integrate = MagicMock(return_value=outcome)
    monkeypatch.setattr(runner, "auto_integrate_after_commit", integrate)
    workspace_scope = MagicMock()
    state = SimpleNamespace(rebase=RebaseState())

    actual = runner._maybe_auto_integrate(
        effect=CommitEffect(message_file="message"),
        event=PipelineEvent.COMMIT_SUCCESS,
        commit_phase_def=SimpleNamespace(role="commit"),
        config=config,
        workspace_scope=workspace_scope,
        state=state,
        display=MagicMock(),
    )

    assert actual is outcome
    assert config.general.auto_integrate_target is None
    assert integrate.call_args.args == (config, workspace_scope, state.rebase)


def test_phase_transition_seam_invokes_auto_integrate(monkeypatch) -> None:
    """Plan step 2: a successful phase transition uses the unset-target path."""
    config = _default_config()
    outcome = RebaseState(last_action="rebased", last_target="main", fast_forwarded=True)
    integrate = MagicMock(return_value=outcome)
    monkeypatch.setattr(runner, "auto_integrate_on_phase_transition", integrate)
    workspace_scope = MagicMock()
    state = SimpleNamespace(rebase=RebaseState())

    actual = runner._maybe_auto_integrate(
        effect=object(),
        event=PipelineEvent.AGENT_SUCCESS,
        commit_phase_def=None,
        config=config,
        workspace_scope=workspace_scope,
        state=state,
        display=MagicMock(),
    )

    assert actual is outcome
    assert config.general.auto_integrate_target is None
    assert integrate.call_args.args == (config, workspace_scope, state.rebase)
