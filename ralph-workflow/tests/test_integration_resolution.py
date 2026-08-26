"""Focused tests for the fail-closed integration dispatch invariant."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.git.merge import MERGE_STATE_NONE
from ralph.pipeline.integration_resolution import (
    EXHAUSTED,
    RECOVERABLE,
    RESOLVED,
    assert_non_resolution_dispatch_allowed,
    inspect_integration_resolution,
)
from ralph.pipeline.rebase_state import RebaseState


@pytest.mark.parametrize("porcelain", (" M src/a.py\n", "M  src/a.py\n", "?? scratch.txt\n"))
def test_dirty_non_integrating_worktree_permits_commit_dispatch(
    tmp_path: Path, porcelain: str
) -> None:
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, porcelain),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )

    assert verdict.status is RESOLVED
    assert verdict.recovery_executor is None
    assert_non_resolution_dispatch_allowed("development_commit", verdict)


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
