"""AC-15: rung-4 self-resume tests.

For rung-4 conditions (a condition that cannot be repaired by
the integration attempt itself, e.g. a shallow clone), the
spec requires:

* the diagnostic to be emitted at every seam, AND
* the moment the condition clears, integration to land on
  the very next seam with no restart and no manual reset.

Subprocess_e2e tests: real git repositories, real
``auto_integrate_after_commit`` calls, deterministic. No
network fetch -- the rung-4 case here is synthetic and the
``clear`` step is local-only, matching the spec's local-only
integration policy (R3).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.rebase.rebase_preconditions import RebasePreconditionError
from ralph.pipeline import auto_integrate as auto_integrate_module
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]


def _run(repo_root: Path, *args: str, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo_root`` with a configurable timeout."""
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _base_branch(tmp_git_repo: Path) -> str:
    """Return the seed template's default branch name."""
    out = _run(tmp_git_repo, "symbolic-ref", "--quiet", "HEAD")
    return out.stdout.strip().removeprefix("refs/heads/")


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run(repo_root, "add", filename)
    _run(repo_root, "commit", "-m", message)
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _build_config(target: str) -> UnifiedConfig:
    """Build a real ``UnifiedConfig`` with auto-integrate enabled."""
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
                "auto_integrate_fetch_enabled": False,
            }
        }
    )


def _diverged_two_commits(tmp_git_repo: Path) -> tuple[str, str]:
    """Build the canonical diverged-feature setup.

    Returns ``(base_branch_name, feature_sha)``. The feature
    branch sits ONE commit ahead of base; integration must
    fast-forward base to feature for a clean land.

    Leaves the worktree checked out on ``feature`` (NOT
    ``base``), because ``auto_integrate_after_commit`` short-
    circuits with ``current_branch == target`` and the test
    must exercise the rebase path.
    """
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    base_seed_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    feature_sha = _commit(tmp_git_repo, "shared.txt", "feature edit\n", "feature edit")
    # Stay on ``feature`` so the integration runs the rebase
    # path; checking out ``base`` would short-circuit with
    # ``current_branch == target`` before rebase is even tried.
    return base, feature_sha


def test_rung4_condition_self_resumes_on_next_seam_after_it_clears(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cleared rung-4 condition lands at the next public seam (AC-15)."""
    base, feature_sha = _diverged_two_commits(tmp_git_repo)
    blocked = True

    def _rung4_precondition(_root: Path) -> None:
        if blocked:
            raise RebasePreconditionError(
                "shallow clone; run git fetch --unshallow"
            )

    monkeypatch.setattr(
        auto_integrate_module,
        "check_rebase_preconditions",
        _rung4_precondition,
    )

    outcome = auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
    )
    assert outcome is not None, (
        "rung-4 condition must surface a recorded skip, "
        "not a silent None"
    )
    assert "preconditions not met" in (outcome.last_reason or ""), (
        f"rung-4 skip must name the precondition failure; "
        f"got last_reason={outcome.last_reason!r}"
    )
    assert "shallow" in (outcome.last_reason or "").lower(), (
        f"rung-4 diagnostic must name the shallow condition; "
        f"got last_reason={outcome.last_reason!r}"
    )

    blocked = False
    outcome_second = auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
    )
    assert outcome_second is not None, (
        "after clearing rung-4 the next seam must produce a "
        "recorded outcome (silent None would be a regression "
        "of the silent-skip bug class)"
    )
    assert outcome_second.last_action in {"rebased", "merged"}, (
        f"after clearing rung-4 the next seam must land; got "
        f"{outcome_second.last_action!r}"
    )
    assert outcome_second.fast_forwarded is True, (
        "after clearing rung-4 the next seam must fast-forward "
        "the target to the feature tip"
    )
    target_head = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert target_head == feature_sha, (
        f"target {base} should be at {feature_sha}, got {target_head}"
    )
