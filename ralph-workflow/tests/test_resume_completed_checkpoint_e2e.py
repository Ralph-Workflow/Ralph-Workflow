from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline import run_loop
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import cached_default_policy_bundle

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=False, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _resume(root: Path) -> PipelineState:
    bundle = cached_default_policy_bundle()
    completed = PipelineState(
        phase=bundle.pipeline.terminal_phase,
        current_drain=bundle.pipeline.terminal_phase,
        previous_phase="development_final_commit",
        approved_tree_sha="0" * 40,
        commit_approval_required=True,
    )
    return run_loop.resolve_initial_state(
        UnifiedConfig(), bundle, completed, None, None, None, None, workspace_root=root
    )


def test_resuming_a_finished_run_with_nothing_left_stays_complete(tmp_git_repo: Path) -> None:
    resumed = _resume(tmp_git_repo)

    assert resumed.phase == cached_default_policy_bundle().pipeline.terminal_phase


def test_resuming_a_finished_run_with_a_dropped_final_commit_commits_the_leftover_work(
    tmp_git_repo: Path,
) -> None:
    (tmp_git_repo / "leftover.txt").write_text("staged but never committed\n", encoding="utf-8")
    _git(tmp_git_repo, "add", "leftover.txt")

    resumed = _resume(tmp_git_repo)

    assert resumed.phase == "development_final_commit_review"
    assert resumed.commit.agent_invoked is False
    assert resumed.commit_approval_required is False
    assert resumed.approved_tree_sha is None
