"""Focused tests for the fail-closed integration dispatch invariant."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.git.merge import MERGE_STATE_NONE
from ralph.git.subprocess_runner import GitRunResult
from ralph.pipeline import effect_executor, runner
from ralph.pipeline.effect_executor import execute_agent_effect
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.integration_resolution import (
    EXHAUSTED,
    RECOVERABLE,
    RESOLVED,
    assert_non_resolution_dispatch_allowed,
    inspect_integration_resolution,
)
from ralph.pipeline.integration_resolution_types import IntegrationResolutionVerdict
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.workspace.scope import WorkspaceScope


@pytest.mark.parametrize(
    "porcelain",
    (
        "UU src/a.py\n",
        "AA src/a.py\n",
        "DD src/a.py\n",
        "AU src/a.py\n",
        "UD src/a.py\n",
        "DU src/a.py\n",
        "UA src/a.py\n",
        " M other.py\nUU src/a.py\n?? scratch.txt\n",
    ),
)
def test_unmerged_paths_block_non_resolution_dispatch(
    tmp_path: Path, porcelain: str
) -> None:
    """Every unmerged porcelain code is blocking integration evidence."""
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, porcelain),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.status is RECOVERABLE
    assert verdict.recovery_executor == "rebase_conflict_resolution"
    with pytest.raises(RuntimeError, match="unmerged paths remain"):
        assert_non_resolution_dispatch_allowed("development_commit", verdict)


@pytest.mark.parametrize(
    "porcelain",
    (
        " M src/a.py\n",
        "M  src/a.py\n",
        "?? scratch.txt\n",
        "A  src/new.py\n",
        "D  src/gone.py\n",
        "R  src/a.py -> src/b.py\n",
        "C  src/a.py -> src/b.py\n",
        " M src/a.py\nM  src/b.py\n?? scratch.txt\nA  src/c.py\n",
    ),
)
def test_ordinary_uncommitted_work_never_blocks_dispatch(
    tmp_path: Path, porcelain: str
) -> None:
    """Uncommitted work is the normal development state, not integration evidence.

    Regression guard: classifying any non-empty porcelain as unresolved
    integration deadlocked every run in a dirty worktree. The verdict named
    a recovery executor that itself defers on a dirty worktree, so the block
    could never clear and the pipeline exited with zero agent calls.
    """
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, porcelain),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.status is RESOLVED
    assert verdict.dispatch_allowed
    assert verdict.reasons == ()
    assert_non_resolution_dispatch_allowed("development", verdict)


@pytest.mark.parametrize(
    ("rebase_active", "merge_status", "expected_reason"),
    (
        (True, MERGE_STATE_NONE, "rebase is in progress"),
        (False, "in_progress", "merge is in progress"),
    ),
)
def test_in_progress_integration_blocks_even_with_clean_porcelain(
    tmp_path: Path, rebase_active: bool, merge_status: str, expected_reason: str
) -> None:
    """An in-progress rebase or merge blocks regardless of porcelain output."""
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, " M src/a.py\n"),
        rebase_active=lambda _: rebase_active,
        merge_status=lambda _: merge_status,
    )

    assert verdict.status is RECOVERABLE
    assert verdict.recovery_executor == "rebase_conflict_resolution"
    assert any(expected_reason in reason for reason in verdict.reasons)


def test_unreadable_porcelain_still_fails_closed(tmp_path: Path) -> None:
    """An inspection that cannot run remains unsafe."""
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (False, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.status is RECOVERABLE
    assert not verdict.dispatch_allowed


def test_final_agent_invocation_fence_rejects_forced_ordinary_phase_bypass(tmp_path: Path) -> None:
    """The final fence raises before a blocked ordinary agent can start."""
    effect = InvokeAgentEffect(
        agent_name="developer",
        phase="development",
        prompt_file="PROMPT.md",
        drain="development",
    )
    state = PipelineState(phase="development").copy_with(
        rebase=RebaseState(last_action="conflict")
    )

    with pytest.raises(RuntimeError, match="cannot dispatch 'development'"):
        execute_agent_effect(
            effect,
            MagicMock(),
            MagicMock(),
            WorkspaceScope(tmp_path),
            display_context=MagicMock(),
            state=state,
        )


def test_final_fence_checks_live_verdict_without_pipeline_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Standalone plumbing cannot bypass the final fence by omitting state."""
    effect = InvokeAgentEffect(
        agent_name="developer", phase="development", prompt_file="PROMPT.md", drain="development"
    )
    observed_states: list[RebaseState] = []

    def _blocked(_root: Path, rebase: RebaseState) -> IntegrationResolutionVerdict:
        observed_states.append(rebase)
        return IntegrationResolutionVerdict(RECOVERABLE, ("unmerged paths remain from an unfinished rebase or merge: a.py",), "rebase_conflict_resolution")

    monkeypatch.setattr(effect_executor, "inspect_integration_resolution", _blocked)

    with pytest.raises(RuntimeError, match="cannot dispatch 'development'"):
        execute_agent_effect(
            effect,
            MagicMock(),
            MagicMock(),
            WorkspaceScope(tmp_path),
            display_context=MagicMock(),
        )

    assert observed_states == [RebaseState()]


def test_runner_dispatch_funnel_ignores_auto_integration_toggle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The earlier runner funnel cannot bypass the invariant when disabled."""
    blocked = IntegrationResolutionVerdict(
        RECOVERABLE, ("unmerged paths remain from an unfinished rebase or merge: a.py",), "rebase_conflict_resolution"
    )
    monkeypatch.setattr(runner, "inspect_integration_resolution", lambda *_args: blocked)
    config = MagicMock()
    config.general.auto_integrate_enabled = False

    with pytest.raises(RuntimeError, match="cannot dispatch 'planning'"):
        runner._assert_integration_dispatch_invariant(
            PipelineState(phase="planning"), WorkspaceScope(tmp_path), config
        )


def test_final_fence_ignores_auto_integration_toggle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Disabling auto-integration cannot disable the dispatch invariant."""
    effect = InvokeAgentEffect(
        agent_name="developer", phase="planning", prompt_file="PROMPT.md", drain="planning"
    )
    monkeypatch.setattr(
        effect_executor,
        "inspect_integration_resolution",
        lambda _root, _rebase: IntegrationResolutionVerdict(
            RECOVERABLE, ("rebase is in progress",), "rebase_conflict_resolution"
        ),
    )
    config = MagicMock()
    config.general.auto_integrate_enabled = False

    with pytest.raises(RuntimeError, match="cannot dispatch 'planning'"):
        execute_agent_effect(
            effect,
            config,
            MagicMock(),
            WorkspaceScope(tmp_path),
            display_context=MagicMock(),
        )


@pytest.mark.parametrize(
    ("state", "rebase_active", "merge_status"),
    (
        (RebaseState(last_action="conflict"), False, MERGE_STATE_NONE),
        (RebaseState(), True, MERGE_STATE_NONE),
        (RebaseState(), False, "in_progress"),
    ),
)
def test_unresolved_evidence_blocks_dispatch(
    tmp_path: Path, state: RebaseState, rebase_active: bool, merge_status: str
) -> None:
    verdict = inspect_integration_resolution(
        tmp_path,
        state,
        porcelain=lambda _: (True, ""),
        rebase_active=lambda _: rebase_active,
        merge_status=lambda _: merge_status,
    )

    assert verdict.status is RECOVERABLE


@pytest.mark.parametrize(
    ("porcelain", "rebase_active", "merge_status", "reason"),
    (
        (
            "UU src/a.py\n",
            False,
            MERGE_STATE_NONE,
            "unmerged paths remain from an unfinished rebase or merge: src/a.py",
        ),
        ("", True, MERGE_STATE_NONE, "rebase is in progress"),
        ("", False, "in_progress", "merge is in progress or merge state is unreadable"),
    ),
)
def test_live_git_evidence_blocks_stale_resolved_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    porcelain: str,
    rebase_active: bool,
    merge_status: str,
    reason: str,
) -> None:
    """Live Git evidence outranks a stale checkpoint that says resolved."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    monkeypatch.setattr(
        "ralph.pipeline.integration_resolution.run_git",
        lambda *_args, **_kwargs: GitRunResult((), 0, porcelain, ""),
    )
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        rebase_active=lambda _root: rebase_active,
        merge_status=lambda _root: merge_status,
    )

    assert verdict.status is RECOVERABLE
    assert reason in verdict.reasons


def test_unreadable_git_inspection_fails_closed(tmp_path: Path) -> None:
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (False, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.status is RECOVERABLE


def test_exhaustion_is_terminal_and_never_dispatches(tmp_path: Path) -> None:
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(resolution_exhausted=True, resolution_exhaustion_reason="all candidates failed"),
        porcelain=lambda _: (True, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.status is EXHAUSTED
    with pytest.raises(RuntimeError, match="exhausted"):
        assert_non_resolution_dispatch_allowed("planning", verdict)


def test_clean_repository_permits_dispatch(tmp_path: Path) -> None:
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.status is RESOLVED
    assert_non_resolution_dispatch_allowed("planning", verdict)
