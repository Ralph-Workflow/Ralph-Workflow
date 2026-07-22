"""Deterministic runner-seam regressions for default auto-integration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from ralph.config.models import UnifiedConfig
from ralph.git.merge import WORKTREE_FOUND
from ralph.pipeline import auto_integrate, auto_integrate_ff, runner
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.rebase_state import RebaseState


def _default_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": True}})


def _stub_ff_environment(monkeypatch, root: Path) -> None:
    """Point every fast-forward lookup at a single in-memory worktree."""
    # The fast-forward now reads the target through ``observe_branch_sha``,
    # which reports (sha, query_ok) so a FAILED ``git rev-parse`` can be
    # retried instead of being mistaken for an absent branch. The stub
    # answers "read successfully", which is the environment these tests
    # always described.
    monkeypatch.setattr(
        auto_integrate_ff,
        "observe_branch_sha",
        lambda _root, _branch: ("old-main", True),
    )
    monkeypatch.setattr(auto_integrate_ff, "is_ancestor", lambda *_args: True)
    monkeypatch.setattr(auto_integrate_ff, "find_main_worktree_root", lambda _root: root)
    # The fast-forward now consults ``worktree_lookup``, which reports
    # found / not-checked-out / query-failed instead of collapsing the
    # last two into ``None``. The stub answers "found", which is the
    # same environment these tests always described.
    monkeypatch.setattr(
        auto_integrate_ff,
        "worktree_lookup",
        lambda _root, _branch: (WORKTREE_FOUND, root),
    )


def test_default_config_resolves_main_and_lands_via_ff_only(monkeypatch) -> None:
    """AC-04: a checked-out target lands through ``merge --ff-only``, not the CAS.

    ``merge --ff-only`` advances the ref, the index and the working tree
    together, so it is tried first no matter how dirty that checkout is;
    the CAS advances the ref alone and must stay a fallback.
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

    _stub_ff_environment(monkeypatch, root)
    worktree_ff = MagicMock(return_value=True)
    cas = MagicMock(return_value=True)
    monkeypatch.setattr(auto_integrate_ff, "fast_forward_via_worktree", worktree_ff)
    monkeypatch.setattr(auto_integrate_ff, "compare_and_swap_branch", cas)

    assert target == "main"
    assert auto_integrate_ff.fast_forward_target(root, target, "feature-head") == (True, "")
    assert worktree_ff.call_args.args == (root, "feature-head")
    cas.assert_not_called()


def test_refused_ff_only_falls_back_to_observed_sha_cas(monkeypatch) -> None:
    """AC-04/AC-08: when git refuses the merge, the CAS still lands the ref.

    The CAS oldvalue must remain the SAME observed target SHA the
    ancestry check was bound to, so a concurrent landing between the two
    fails closed instead of overwriting.
    """
    root = Path("/workspace/feature")
    _stub_ff_environment(monkeypatch, root)
    monkeypatch.setattr(
        auto_integrate_ff, "fast_forward_via_worktree", lambda *_args: False
    )
    cas = MagicMock(return_value=True)
    monkeypatch.setattr(auto_integrate_ff, "compare_and_swap_branch", cas)

    assert auto_integrate_ff.fast_forward_target(root, "main", "feature-head") == (True, "")
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
