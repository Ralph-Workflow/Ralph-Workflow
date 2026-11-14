from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit as _auto_integrate_after_commit,
)
from ralph.pipeline.auto_integrate import (
    auto_integrate_on_phase_transition as _auto_integrate_on_phase_transition,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.resolution_outcome import (
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
    WaitingOutcome,
)
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def auto_integrate_after_commit(*args: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("sleep", lambda _seconds: None)
    kwargs.setdefault("jitter", lambda: 0.0)
    return _auto_integrate_after_commit(*args, **kwargs)


def auto_integrate_on_phase_transition(*args: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("sleep", lambda _seconds: None)
    kwargs.setdefault("jitter", lambda: 0.0)
    return _auto_integrate_on_phase_transition(*args, **kwargs)


def _run(repo_root: Path, *args: str, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
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


def _build_config(
    tmp_git_repo: Path,
    *,
    enabled: bool = True,
    target: str | None = None,
    remote_enabled: bool = False,
) -> UnifiedConfig:
    payload: dict[str, object] = {
        "general": {
            "auto_integrate_enabled": enabled,
            "auto_integrate_remote_enabled": remote_enabled,
        }
    }
    if target is not None:
        payload["general"]["auto_integrate_target"] = target
    return UnifiedConfig.model_validate(payload)


def _snapshot(tmp_git_repo: Path) -> dict[str, object]:
    head = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    refs: dict[str, str] = {}
    out = _run(tmp_git_repo, "for-each-ref", "--format=%(refname) %(objectname)")
    for line in out.stdout.splitlines():
        if not line:
            continue
        name, sha = line.split(" ", 1)
        refs[name] = sha
    return {
        "head": head,
        "refs": refs,
        "worktree": _run(tmp_git_repo, "status", "--porcelain").stdout,
    }


def _diverged_conflicting_repo(tmp_git_repo: Path) -> str:
    base = _base_branch(tmp_git_repo)
    base_seed_sha = _commit(tmp_git_repo, "base_seed.txt", "base seed\n", "base seed")
    _run(tmp_git_repo, "branch", "feature", base_seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 1\n", "base shared 1")
    _run(tmp_git_repo, "checkout", "feature")
    return base


def test_rebase_failed_falls_back_to_endpoint_merge(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ralph.pipeline.auto_integrate_rebase_merge as _ai_mod
    from ralph.git.rebase._rebase_kind import RebaseKind
    from ralph.git.rebase.rebase import RebaseFailed
    from ralph.git.rebase.rebase_kinds import RebaseErrorKind

    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feature only\n", "feat")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "base.txt", "base only\n", "base")
    _run(tmp_git_repo, "checkout", "feature")

    def _fake_rebase_onto(target: str, *, repo_root: Path, options: object = None) -> object:
        return RebaseFailed(
            kind=RebaseErrorKind(kind=RebaseKind.UNKNOWN, metadata={"details": "simulated failure"})
        )

    monkeypatch.setattr(_ai_mod, "rebase_onto", _fake_rebase_onto)

    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_after_commit(config, WorkspaceScope(tmp_git_repo), RebaseState())
    assert outcome is not None
    assert outcome.last_action == "merged", (
        f"a failed rebase must fall back to the endpoint merge, got"
        f" last_action={outcome.last_action!r} reason={outcome.last_reason!r}"
    )
    assert outcome.fast_forwarded is True
    head_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha == head_sha
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""


def test_conflict_resolver_failure_retains_progress_and_records_conflict(
    tmp_git_repo: Path,
) -> None:
    base = _diverged_conflicting_repo(tmp_git_repo)
    before = _snapshot(tmp_git_repo)

    def _resolver(root: Path, target: str) -> RetryLaterOutcome:
        return RetryLaterOutcome(on_change="conflict resolution incomplete")

    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=_resolver,
    )
    assert outcome is not None
    assert outcome.last_action == "conflict"
    assert outcome.last_reason is not None
    assert "conflict resolution incomplete" in outcome.last_reason
    after = _snapshot(tmp_git_repo)
    assert after["head"] == before["head"]
    assert outcome.recovery_record_retained
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert (git_dir / "MERGE_HEAD").exists()


def test_conflict_resolver_exception_retains_progress_and_records_conflict(
    tmp_git_repo: Path,
) -> None:
    base = _diverged_conflicting_repo(tmp_git_repo)
    before = _snapshot(tmp_git_repo)

    def _resolver(root: Path, target: str) -> StopResolutionOutcome:
        raise RuntimeError("resolver blew up")

    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=_resolver,
    )
    assert outcome is not None
    assert outcome.last_action == "conflict"
    after = _snapshot(tmp_git_repo)
    assert after["head"] == before["head"]
    assert outcome.recovery_record_retained


def test_auto_integrate_regression_deterministic_ff_refusal_does_not_retry(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ralph.pipeline.auto_integrate as _ai_mod

    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feature only\n", "feat")
    calls: list[int] = []

    def _dirty_target_ff(
        repo_root: Path, target: str, feature_sha: str, **_kwargs: object
    ) -> WaitingOutcome:
        calls.append(1)
        return WaitingOutcome(
            external_condition="target worktree dirty",
            resumes_when="target worktree becomes clean",
        )

    monkeypatch.setattr(_ai_mod, "_fast_forward_target", _dirty_target_ff)

    outcome = auto_integrate_after_commit(
        _build_config(tmp_git_repo, target=base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
    )

    assert outcome is not None
    assert outcome.fast_forwarded is False
    assert outcome.last_reason == "target worktree dirty"
    assert calls == [1]


def test_ff_race_retries_integration_onto_moved_target(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ralph.pipeline.auto_integrate as _ai_mod

    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feature only\n", "feat")

    real_ff = _ai_mod._fast_forward_target
    calls: list[int] = []
    intervening_shas: list[str] = []

    def _flaky_ff(
        repo_root: Path, target: str, feature_sha: str, **_kwargs: object
    ) -> StopResolutionOutcome:
        calls.append(1)
        if len(calls) == 1:
            _run(repo_root, "checkout", target)
            intervening_sha = _commit(
                repo_root, "intervening.txt", "intervening\n", "concurrent target move"
            )
            _run(repo_root, "checkout", "feature")
            intervening_shas.append(intervening_sha)
            assert (
                intervening_sha
                == _run(repo_root, "rev-parse", f"refs/heads/{target}").stdout.strip()
            )
            return RetryLaterOutcome(on_change="target advanced concurrently (CAS mismatch)")
        return real_ff(repo_root, target, feature_sha)

    monkeypatch.setattr(_ai_mod, "_fast_forward_target", _flaky_ff)

    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_after_commit(config, WorkspaceScope(tmp_git_repo), RebaseState())
    assert outcome is not None
    assert len(calls) == 2, "ff race must trigger exactly one immediate retry"
    assert outcome.fast_forwarded is True, (
        f"retry must land the fast-forward, got action={outcome.last_action!r}"
        f" reason={outcome.last_reason!r}"
    )
    head_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha == head_sha
    assert (
        _run(tmp_git_repo, "merge-base", "--is-ancestor", intervening_shas[0], "HEAD").returncode
        == 0
    )


def test_phase_transition_integrates_when_target_moved(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feature only\n", "feat")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "base.txt", "base only\n", "base moved")
    _run(tmp_git_repo, "checkout", "feature")

    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_on_phase_transition(
        config, WorkspaceScope(tmp_git_repo), RebaseState()
    )
    assert outcome is not None
    assert outcome.last_action in {"rebased", "merged"}
    assert outcome.fast_forwarded is True
    head_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha == head_sha


def test_phase_transition_records_a_skip_on_a_dirty_worktree(
    tmp_git_repo: Path,
) -> None:
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feature only\n", "feat")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "base.txt", "base only\n", "base moved")
    _run(tmp_git_repo, "checkout", "feature")
    (tmp_git_repo / "feat.txt").write_text("uncommitted work\n", encoding="utf-8")
    before = _snapshot(tmp_git_repo)

    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_on_phase_transition(
        config, WorkspaceScope(tmp_git_repo), RebaseState()
    )
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert outcome.last_target == base
    assert outcome.last_reason is not None
    assert "worktree not clean" in outcome.last_reason
    after = _snapshot(tmp_git_repo)
    assert before == after


def test_phase_transition_silent_when_nothing_to_integrate(
    tmp_git_repo: Path,
) -> None:
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    before = _snapshot(tmp_git_repo)
    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_on_phase_transition(
        config, WorkspaceScope(tmp_git_repo), RebaseState()
    )
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert outcome.last_reason == "no commits beyond target"
    assert _snapshot(tmp_git_repo) == before


def test_resolver_that_only_edits_files_still_lands_the_merge(
    tmp_git_repo: Path,
) -> None:
    base = _diverged_conflicting_repo(tmp_git_repo)
    feature_sha_before = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()

    def _resolver(root: Path, target: str) -> ResolvedOutcome:
        (root / "shared.txt").write_text("edited only\n", encoding="utf-8")
        return ResolvedOutcome()

    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=_resolver,
    )

    assert outcome is not None
    assert outcome.last_action == "merged", (
        f"a git-less resolver must still land the merge, got"
        f" last_action={outcome.last_action!r} reason={outcome.last_reason!r}"
    )
    assert outcome.fast_forwarded is True
    head_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert head_sha != feature_sha_before
    assert (tmp_git_repo / "shared.txt").read_text() == "edited only\n"
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha == head_sha


def test_resolver_leaving_conflict_markers_is_rejected(tmp_git_repo: Path) -> None:
    base = _diverged_conflicting_repo(tmp_git_repo)
    before = _snapshot(tmp_git_repo)

    def _resolver(root: Path, target: str) -> ResolvedOutcome:
        (root / "shared.txt").write_text(
            "<<<<<<< HEAD\nfeature version\n=======\nbase version 1\n>>>>>>> main\n",
            encoding="utf-8",
        )
        return ResolvedOutcome()

    config = _build_config(tmp_git_repo, target=base)
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=_resolver,
    )

    assert outcome is not None
    assert outcome.last_action == "conflict"
    assert outcome.fast_forwarded is False
    after = _snapshot(tmp_git_repo)
    assert after["head"] == before["head"], "no merge commit may be created"
    before_refs = before["refs"]
    after_refs = after["refs"]
    assert isinstance(before_refs, dict)
    assert isinstance(after_refs, dict)
    assert after_refs[f"refs/heads/{base}"] == before_refs[f"refs/heads/{base}"]


@pytest.mark.parametrize(
    "half_resolved",
    [
        "<<<<<<< HEAD\nfeature version\nbase version 1\n",
        "feature version\nbase version 1\n>>>>>>> main\n",
    ],
    ids=["opening-fence-only", "closing-fence-only"],
)
def test_resolver_regression_lone_conflict_marker_is_rejected(
    tmp_git_repo: Path, half_resolved: str
) -> None:
    base = _diverged_conflicting_repo(tmp_git_repo)
    before = _snapshot(tmp_git_repo)

    def _resolver(root: Path, target: str) -> ResolvedOutcome:
        (root / "shared.txt").write_text(half_resolved, encoding="utf-8")
        return ResolvedOutcome()

    outcome = auto_integrate_after_commit(
        _build_config(tmp_git_repo, target=base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=_resolver,
    )

    assert outcome is not None
    assert outcome.last_action == "conflict"
    assert outcome.fast_forwarded is False
    after = _snapshot(tmp_git_repo)
    assert after["head"] == before["head"], "no merge commit may be created"
    before_refs = before["refs"]
    after_refs = after["refs"]
    assert isinstance(before_refs, dict)
    assert isinstance(after_refs, dict)
    assert after_refs[f"refs/heads/{base}"] == before_refs[f"refs/heads/{base}"]
    assert (tmp_git_repo / ".git" / "MERGE_HEAD").exists()
    assert outcome.recovery_record_retained
