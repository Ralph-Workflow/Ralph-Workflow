"""Real-git regression: a prefix-colliding sibling worktree must not block integration.

This repository's own topology is a fleet of linked worktrees whose
branch names share a numeric prefix (``wt-040``, ``wt-040-fix-autorebase``,
...). The worktree-conflict precondition used to decide "this branch is
checked out elsewhere" with a substring test, so any sibling worktree on
a prefix-extended branch raised ``RebasePreconditionError`` and
auto-integration recorded a "preconditions not met" skip instead of
rebasing. This is the end-to-end proof that it now rebases and lands.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.rebase.rebase_preconditions import check_rebase_preconditions
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(15)]


def _run(repo_root: Path, *args: str, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo_root`` with a bounded timeout."""
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run(repo_root, "add", filename)
    _run(repo_root, "commit", "-m", message)
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _build_config(*, target: str) -> UnifiedConfig:
    payload: dict[str, object] = {
        "general": {
            "auto_integrate_enabled": True,
            "auto_integrate_target": target,
        }
    }
    return UnifiedConfig.model_validate(payload)


def test_prefix_colliding_sibling_worktree_does_not_block_integration(
    tmp_git_repo: Path,
    tmp_path: Path,
) -> None:
    """A sibling worktree on 'wt-040-fix-autorebase' must not block 'wt-040'."""
    _run(tmp_git_repo, "branch", "-f", "main", "HEAD")

    # The sibling worktree holds the PREFIX-EXTENDED branch name.
    sibling = tmp_path / "sibling"
    added = _run(tmp_git_repo, "worktree", "add", str(sibling), "-b", "wt-040-fix-autorebase")
    assert added.returncode == 0, added.stderr

    # Feature branch is ahead of main...
    _run(tmp_git_repo, "checkout", "-b", "wt-040")
    _commit(tmp_git_repo, "feature.txt", "feature\n", "feature work")

    # ...and main has moved on independently, so a real rebase is required.
    main_wt = tmp_path / "mainline"
    added_main = _run(tmp_git_repo, "worktree", "add", str(main_wt), "main")
    assert added_main.returncode == 0, added_main.stderr
    _commit(main_wt, "mainline.txt", "mainline\n", "mainline work")
    _run(tmp_git_repo, "worktree", "remove", str(main_wt))

    # The precondition itself must no longer see a conflict.
    check_rebase_preconditions(tmp_git_repo)

    config = _build_config(target="main")
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())

    assert outcome is not None
    reason = outcome.last_reason or ""
    assert not reason.startswith("preconditions not met"), (
        f"integration was skipped by a false worktree conflict: {reason}"
    )
    assert outcome.last_action in {"rebased", "merged"}, outcome
    assert outcome.fast_forwarded is True, outcome
