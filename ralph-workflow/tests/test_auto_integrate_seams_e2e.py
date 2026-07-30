"""Fast wiring contracts for the four auto-integration entry seams."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha
from ralph.git.operations import get_head_sha
from ralph.pipeline import run_loop, runner
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
            }
        }
    )


def _outcome() -> RebaseState:
    return RebaseState(
        last_action="rebased",
        last_target="main",
        fast_forwarded=True,
    )


def test_startup_seam_returns_injected_integration_outcome(
    monkeypatch: MonkeyPatch,
) -> None:
    """Startup forwards its config, scope, state, and resolver contract."""
    expected = _outcome()
    integration = MagicMock(return_value=expected)
    monkeypatch.setattr(run_loop, "auto_integrate_on_phase_transition", integration)
    conflict_resolver = object()
    stop_resolver = object()
    monkeypatch.setattr(
        run_loop,
        "build_agent_conflict_resolver",
        lambda **_kwargs: conflict_resolver,
    )
    monkeypatch.setattr(
        run_loop,
        "build_agent_rebase_stop_resolver",
        lambda **_kwargs: stop_resolver,
    )
    scope = WorkspaceScope(Path("/workspace"))
    ctx = SimpleNamespace(
        config=_config(),
        workspace_scope=scope,
        pipeline_deps=None,
        policy_bundle=MagicMock(),
        registry=MagicMock(),
        display_context=MagicMock(),
        active_display=MagicMock(),
    )

    actual = run_loop._run_startup_integration(ctx)

    assert actual is expected
    assert integration.call_args.args == (ctx.config, scope, RebaseState())
    assert integration.call_args.kwargs["conflict_resolver"] is conflict_resolver
    assert integration.call_args.kwargs["rebase_stop_resolver"] is stop_resolver


def test_phase_transition_seam_returns_injected_outcome(
    monkeypatch: MonkeyPatch,
) -> None:
    """A boundary event reaches the integration adapter exactly once."""
    expected = _outcome()
    integration = MagicMock(return_value=expected)
    monkeypatch.setattr(runner, "auto_integrate_on_phase_transition", integration)
    scope = WorkspaceScope(Path("/workspace"))
    state = PipelineState(phase="development", rebase=RebaseState())

    actual = runner._integrate_on_phase_transition(
        event=runner.PipelineEvent.AGENT_SUCCESS,
        config=_config(),
        workspace_scope=scope,
        state=state,
        display=MagicMock(),
        policy_bundle=None,
        registry=None,
    )

    assert actual is expected
    assert integration.call_args.args == (_config(), scope, state.rebase)


def test_fan_out_join_threads_injected_outcome_into_state(
    monkeypatch: MonkeyPatch,
) -> None:
    """The coordinator join preserves state and replaces only rebase outcome."""
    expected = _outcome()
    integration = MagicMock(return_value=expected)
    monkeypatch.setattr(runner, "auto_integrate_on_phase_transition", integration)
    state = PipelineState(phase="development", rebase=RebaseState())

    joined = runner._integrate_after_fan_out(
        state=state,
        config=_config(),
        workspace_scope=WorkspaceScope(Path("/workspace")),
        display=MagicMock(),
        policy_bundle=None,
        registry=None,
    )

    assert joined.phase == state.phase
    assert joined.rebase is expected
    assert integration.call_count == 1


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(5)
def test_linked_worktree_landing_updates_checked_out_mainline(
    tmp_git_repo: Path,
) -> None:
    """Real Git proves the worktree-aware fast-forward updates ref and files."""
    main = _run(tmp_git_repo, "branch", "--show-current").stdout.strip()
    feature = tmp_git_repo.parent / "feature"
    assert _run(
        tmp_git_repo,
        "worktree",
        "add",
        "-b",
        "feature",
        str(feature),
    ).returncode == 0
    (feature / "feature.txt").write_text("feature\n", encoding="utf-8")
    assert _run(feature, "add", "feature.txt").returncode == 0
    assert _run(feature, "commit", "-m", "feature").returncode == 0

    outcome = auto_integrate_after_commit(
        UnifiedConfig.model_validate(
            {
                "general": {
                    "auto_integrate_enabled": True,
                    "auto_integrate_target": main,
                }
            }
        ),
        WorkspaceScope(feature),
        RebaseState(),
    )

    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert branch_sha(feature, main) == get_head_sha(feature)
    assert (tmp_git_repo / "feature.txt").read_text(encoding="utf-8") == "feature\n"
