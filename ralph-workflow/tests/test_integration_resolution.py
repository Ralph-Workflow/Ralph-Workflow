"""Focused tests for the fail-closed integration dispatch invariant."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.git.git_run_result import GitRunResult
from ralph.git.merge import MERGE_STATE_NONE
from ralph.pipeline import effect_executor, runner
from ralph.pipeline.auto_integrate_resolution_state import reconcile_stale_unresolved_state
from ralph.pipeline.effect_executor import execute_agent_effect
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.integration_resolution import (
    EXHAUSTED,
    RECOVERABLE,
    RESOLVED,
    IntegrationState,
    TreeReadinessAssessment,
    UnknownKind,
    assert_non_resolution_dispatch_allowed,
    assess_tree_readiness,
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
def test_unmerged_paths_block_non_resolution_dispatch(tmp_path: Path, porcelain: str) -> None:
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


def test_c_quoted_unmerged_path_is_decoded_in_blocking_reason(tmp_path: Path) -> None:
    """Line-oriented porcelain C-quotes non-ASCII paths; the reason must be raw.

    ``git status --porcelain`` quotes paths per ``core.quotePath``, so a
    conflicted file with non-ASCII bytes appears as ``UU "conf\\303\\251lict.py"``.
    The user-facing blocking reason must carry the decoded spelling.
    """
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, 'UU "conf\\303\\251lict.py"\n'),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.status is RECOVERABLE
    reason = "unmerged paths remain from an unfinished rebase or merge: conf\u00e9lict.py"
    assert reason in verdict.reasons
    assert "conf\\303\\251lict.py" not in verdict.reasons[0]


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
def test_ordinary_uncommitted_work_never_blocks_dispatch(tmp_path: Path, porcelain: str) -> None:
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


def test_unreadable_porcelain_is_unknown_not_blocking(tmp_path: Path) -> None:
    """An inspection that cannot run is UNKNOWN, never a blocking reason.

    Treating a timed-out or unreadable probe as evidence of a conflict is
    what deadlocked runs: the recovery executor the verdict named had no
    conflict to repair and no way to prove the repository was repaired.
    """
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (False, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.integration_state is IntegrationState.UNKNOWN
    assert verdict.status is RESOLVED
    assert verdict.dispatch_allowed
    assert verdict.reasons == ()
    assert verdict.recovery_executor is None
    assert verdict.blocking_reasons == ()
    assert verdict.unknown_kinds == (UnknownKind.PORCELAIN_UNREADABLE,)


def test_clean_live_inspection_reconciles_stale_resolution_evidence(tmp_path: Path) -> None:
    """Clean Git state clears persisted conflict evidence before dispatch resumes."""
    stale = RebaseState(
        last_action="conflict",
        unresolved_integration_carried=True,
        resolution_exhausted=True,
        resolution_exhaustion_reason="all strategies failed",
        conflict_strategy_index=4,
        conflict_strategies_tried=("merge_instead: unresolved",),
    )
    verdict = inspect_integration_resolution(
        tmp_path,
        stale,
        porcelain=lambda _: (True, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.status is RESOLVED
    reconciled = reconcile_stale_unresolved_state(stale)
    assert reconciled.integration_unresolved is False
    assert reconciled.resolution_exhausted is False
    assert reconciled.conflict_strategy_index == 0
    assert reconciled.conflict_strategies_tried == ()
    assert reconcile_stale_unresolved_state(reconciled) == reconciled

    next_verdict = inspect_integration_resolution(
        tmp_path,
        reconciled,
        porcelain=lambda _: (True, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )
    assert next_verdict.status is RESOLVED
    assert next_verdict.dispatch_allowed


def test_final_agent_invocation_fence_rejects_forced_ordinary_phase_bypass(tmp_path: Path) -> None:
    """The final fence raises before a blocked ordinary agent can start."""
    effect = InvokeAgentEffect(
        agent_name="developer",
        phase="development",
        prompt_file="PROMPT.md",
        drain="development",
    )
    state = PipelineState(phase="development").copy_with(rebase=RebaseState(last_action="conflict"))

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
        return IntegrationResolutionVerdict(
            RECOVERABLE,
            ("unmerged paths remain from an unfinished rebase or merge: a.py",),
            "rebase_conflict_resolution",
        )

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
        RECOVERABLE,
        ("unmerged paths remain from an unfinished rebase or merge: a.py",),
        "rebase_conflict_resolution",
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


def test_unreadable_git_inspection_is_unknown(tmp_path: Path) -> None:
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (False, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.integration_state is IntegrationState.UNKNOWN
    assert verdict.status is RESOLVED
    assert verdict.integration_ready is False


def test_exhaustion_is_terminal_while_the_conflict_is_still_there(tmp_path: Path) -> None:
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(
            resolution_exhausted=True, resolution_exhaustion_reason="all candidates failed"
        ),
        porcelain=lambda _: (True, "UU src/a.py\n"),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.status is EXHAUSTED
    with pytest.raises(RuntimeError, match="exhausted"):
        assert_non_resolution_dispatch_allowed("planning", verdict)


def test_exhaustion_does_not_outlive_the_conflict_it_describes(tmp_path: Path) -> None:
    """A record of an exhausted chain must not block a clean repository.

    Nothing is unmerged, no rebase or merge is in progress, and every
    probe answered -- so the conflict the record describes is gone,
    however it went. Letting the record win anyway was a latch with no
    exit: it is cleared only by an integration landing, and it blocked
    the run before anything could land, so every later run exited
    against a clean tree until someone edited the record by hand.
    """
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(
            resolution_exhausted=True, resolution_exhaustion_reason="all candidates failed"
        ),
        porcelain=lambda _: (True, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.status is RESOLVED
    assert verdict.dispatch_allowed is True


def test_a_stale_record_still_blocks_when_the_tree_cannot_be_read(tmp_path: Path) -> None:
    """Fail closed: only a VERIFIABLY clean tree may overrule the record."""
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(
            resolution_exhausted=True, resolution_exhaustion_reason="all candidates failed"
        ),
        porcelain=lambda _: (False, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.status is EXHAUSTED


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


def test_tree_readiness_assessment_fields(tmp_path: Path) -> None:
    """Readiness assessment provides separate development_dispatch_allowed and integration_ready."""
    assessment = assess_tree_readiness(
        tmp_path,
        porcelain=lambda _: (True, "M  staged.py\n"),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )
    assert isinstance(assessment, TreeReadinessAssessment)
    assert assessment.development_dispatch_allowed is True
    assert assessment.integration_ready is False
    assert any(
        "staged uncommitted work blocks integration" in r for r in assessment.blocking_reasons
    )

    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, "M  staged.py\n"),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )
    assert verdict.development_dispatch_allowed is True
    assert verdict.integration_ready is False
    assert any("staged uncommitted work blocks integration" in r for r in verdict.blocking_reasons)


@pytest.mark.parametrize("code", (" M file.py\n", "M  file.py\n", "A  new.py\n", "D  gone.py\n"))
def test_uncommitted_work_allows_development_dispatch_but_not_integration(
    tmp_path: Path, code: str
) -> None:
    """Ordinary uncommitted work permits development dispatch while integration is not ready."""
    readiness = assess_tree_readiness(
        tmp_path,
        porcelain=lambda _root: (True, code),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )
    assert readiness.development_dispatch_allowed is True
    assert readiness.integration_ready is False
    assert len(readiness.blocking_reasons) > 0
