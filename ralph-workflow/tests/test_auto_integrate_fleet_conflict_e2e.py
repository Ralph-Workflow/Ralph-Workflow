"""Representative real-Git proof for conflict resolution across linked worktrees.

The declined-resolution rollback and replay-counter cases were deleted here:
the same contracts are covered by focused rebase-loop and single-checkout E2E
tests. This file retains the one OS/Git interaction those tests cannot prove:
an in-place resolved rebase advances the ref shared by two worktrees.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStop

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]

_RESOLVED_CONTENT = "agent-a intent + agent-b intent\n"


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    path = repo_root / filename
    path.write_text(content, encoding="utf-8")
    assert _run(repo_root, "add", filename).returncode == 0
    assert _run(repo_root, "commit", "-m", message).returncode == 0
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def test_resolved_rebase_advances_the_ref_shared_by_both_worktrees(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """A linked feature resolves in place and lands linearly on shared main."""
    target = (
        _run(tmp_git_repo, "symbolic-ref", "--quiet", "HEAD")
        .stdout.strip()
        .removeprefix("refs/heads/")
    )
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed shared")
    seed = branch_sha(tmp_git_repo, target)
    assert seed is not None
    feature = tmp_path / "agent-a"
    assert _run(
        tmp_git_repo, "worktree", "add", "-b", "feature", str(feature), seed
    ).returncode == 0
    _commit(feature, "shared.txt", "agent-a intent\n", "agent A edits shared")
    agent_b_sha = _commit(
        tmp_git_repo, "shared.txt", "agent-b intent\n", "agent B lands"
    )
    seen: list[RebaseStop] = []

    def _resolve(root: Path, _target: str, stop: RebaseStop) -> bool:
        seen.append(stop)
        for relative in stop.conflicted_files:
            (root / relative).write_text(_RESOLVED_CONTENT, encoding="utf-8")
        return True

    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
            }
        }
    )
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(feature),
        RebaseState(),
        rebase_stop_resolver=_resolve,
        sleep=lambda _seconds: None,
        jitter=lambda: 0.0,
    )

    assert outcome is not None
    assert outcome.last_action == "rebased"
    assert outcome.fast_forwarded is True
    assert seen
    feature_tip = _run(feature, "rev-parse", "HEAD").stdout.strip()
    assert branch_sha(feature, target) == feature_tip
    assert branch_sha(tmp_git_repo, target) == feature_tip
    assert (
        _run(feature, "merge-base", "--is-ancestor", agent_b_sha, feature_tip).returncode
        == 0
    )
    assert _run(feature, "log", "--merges", "--format=%H").stdout.strip() == ""
    assert _run(feature, "show", "HEAD:shared.txt").stdout == _RESOLVED_CONTENT
