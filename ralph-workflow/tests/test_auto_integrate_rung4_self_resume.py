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

_NO_INTEGRATION_BACKOFF = {"sleep": lambda _seconds: None, "jitter": lambda: 0.0}


def _run(repo_root: Path, *args: str, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _base_branch(tmp_git_repo: Path) -> str:
    head_file = tmp_git_repo / ".git" / "HEAD"
    try:
        head_content = head_file.read_text(encoding="utf-8").strip()
        if head_content.startswith("ref: refs/heads/"):
            return head_content.removeprefix("ref: refs/heads/")
    except OSError:
        pass
    from git import Repo

    return Repo(tmp_git_repo).active_branch.name


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    from git import Repo

    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo = Repo(repo_root)
    try:
        repo.index.add([filename])
        return repo.index.commit(message).hexsha
    finally:
        repo.close()


def _build_config(target: str) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
            }
        }
    )


def _diverged_two_commits(tmp_git_repo: Path) -> tuple[str, str]:
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "shared.txt", "seed\n", "seed")
    base_seed_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    feature_sha = _commit(tmp_git_repo, "shared.txt", "feature edit\n", "feature edit")
    return base, feature_sha


def test_rung4_condition_self_resumes_on_next_seam_after_it_clears(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, feature_sha = _diverged_two_commits(tmp_git_repo)
    blocked = True

    def _rung4_precondition(_root: Path) -> None:
        if blocked:
            raise RebasePreconditionError("shallow clone; run git fetch --unshallow")

    monkeypatch.setattr(
        auto_integrate_module,
        "check_rebase_preconditions",
        _rung4_precondition,
    )

    outcome = auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        **_NO_INTEGRATION_BACKOFF,
    )
    assert outcome is not None, "rung-4 condition must surface a recorded skip, not a silent None"
    assert "preconditions not met" in (outcome.last_reason or ""), (
        f"rung-4 skip must name the precondition failure; got last_reason={outcome.last_reason!r}"
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
        **_NO_INTEGRATION_BACKOFF,
    )
    assert outcome_second is not None, (
        "after clearing rung-4 the next seam must produce a "
        "recorded outcome (silent None would be a regression "
        "of the silent-skip bug class)"
    )
    assert outcome_second.last_action in {"rebased", "merged"}, (
        f"after clearing rung-4 the next seam must land; got {outcome_second.last_action!r}"
    )
    assert outcome_second.fast_forwarded is True, (
        "after clearing rung-4 the next seam must fast-forward the target to the feature tip"
    )
    target_head = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert target_head == feature_sha, (
        f"target {base} should be at {feature_sha}, got {target_head}"
    )
