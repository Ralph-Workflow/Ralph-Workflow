from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline.integration_resolution import RebaseOutcomeKind
from ralph.pipeline.landed_rebase_reconciler import reconcile_landed_rebase
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.reducer import PhaseDecision, decide_phase_transition

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.subprocess_e2e


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(root: Path) -> Path:
    _git(root, "init", "-q", "-b", "main")
    _git(
        root,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "a",
    )
    return root


def _stale(target: str = "main") -> RebaseState:
    return RebaseState(
        last_outcome=RebaseOutcomeKind.REBASED,
        fast_forwarded=False,
        last_reason="target advanced concurrently (not an ancestor of feature)",
        last_target=target,
    )


def _decide(state: RebaseState) -> PhaseDecision:
    return decide_phase_transition(state, None, integration_record=None, rebase_state=state)


def test_a_stale_unlanded_record_is_cleared_when_git_shows_the_work_in_the_target(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    reconciled = reconcile_landed_rebase(_stale(), root)
    assert _decide(reconciled) is not PhaseDecision.BLOCK_FOR_RECOVERY


def test_work_that_is_not_in_the_target_still_holds(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _git(root, "checkout", "-q", "-b", "feature")
    _git(
        root,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "b",
    )
    reconciled = reconcile_landed_rebase(_stale(), root)
    assert _decide(reconciled) is PhaseDecision.BLOCK_FOR_RECOVERY


def test_an_unknown_target_leaves_the_record_alone(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    stale = _stale(target="no-such-branch")
    assert reconcile_landed_rebase(stale, root) == stale
