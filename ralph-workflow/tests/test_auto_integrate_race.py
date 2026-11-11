from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]

_NO_INTEGRATION_BACKOFF = {"sleep": lambda _seconds: None, "jitter": lambda: 0.0}


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
    reclaim_target_worktree: bool = True,
) -> UnifiedConfig:
    payload: dict[str, object] = {
        "general": {
            "auto_integrate_enabled": enabled,
            "auto_integrate_reclaim_target_worktree": reclaim_target_worktree,
        }
    }
    if target is not None:
        payload["general"]["auto_integrate_target"] = target
    return UnifiedConfig.model_validate(payload)


def test_cas_race_target_advances_concurrently_via_orchestration(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ralph.pipeline.auto_integrate_ff as _ai_ff_mod

    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    pre_landing_base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "concurrent.txt", "concurrent\n", "concurrent")
    post_landing_base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert pre_landing_base_sha != post_landing_base_sha
    _run(tmp_git_repo, "checkout", "feature")

    def _stale_observe_branch_sha(_repo_root: object, _name: str) -> tuple[str | None, bool]:
        return pre_landing_base_sha, True

    monkeypatch.setattr(_ai_ff_mod, "observe_branch_sha", _stale_observe_branch_sha)

    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState(), **_NO_INTEGRATION_BACKOFF)
    assert outcome is not None
    assert outcome.fast_forwarded is False, (
        f"AC-08 race: fast_forwarded must be False when the target"
        f" moves between observation and CAS, got {outcome.fast_forwarded!r}"
    )
    assert outcome.last_reason is not None and (
        "advanced concurrently" in outcome.last_reason or "CAS mismatch" in outcome.last_reason
    ), f"AC-08 race: last_reason must mention the concurrent landing, got {outcome.last_reason!r}"
    final_base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert final_base_sha == post_landing_base_sha, (
        f"AC-08 race: target ref must be byte-unchanged, expected"
        f" {post_landing_base_sha!r}, got {final_base_sha!r}"
    )


def test_fast_forward_target_missing_branch_is_reported(tmp_git_repo: Path) -> None:
    from ralph.pipeline.auto_integrate_ff import fast_forward_target

    _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()

    ok, reason = fast_forward_target(tmp_git_repo, "definitely-not-a-branch", feature_sha)
    assert ok is False, f"AC-08 ff: missing target branch must report ok=False, got ok={ok!r}"
    assert reason == "target branch missing at fast-forward time", (
        f"AC-08 ff: defensive reason for missing branch must match"
        f" the producer literal, got {reason!r}"
    )


def test_unreadable_target_is_retryable_but_a_missing_one_is_not(
    tmp_path: Path, tmp_git_repo: Path
) -> None:
    from ralph.pipeline.auto_integrate_ff import (
        fast_forward_target,
        is_retryable_fast_forward_failure,
    )

    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()

    unreadable_ok, unreadable_reason = fast_forward_target(tmp_path, "main", feature_sha)
    assert unreadable_ok is False
    assert is_retryable_fast_forward_failure(unreadable_reason), (
        f"an unreadable target must be retried, got reason={unreadable_reason!r}"
    )

    _missing_ok, missing_reason = fast_forward_target(
        tmp_git_repo, "definitely-not-a-branch", feature_sha
    )
    assert missing_reason == "target branch missing at fast-forward time"
    assert not is_retryable_fast_forward_failure(missing_reason), (
        "a genuinely absent target must NOT burn the attempt budget"
    )


def test_exhausted_integration_attempts_are_recorded_and_not_over_promised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from loguru import logger

    import ralph.pipeline.auto_integrate as _ai_mod

    base = "main"
    attempts: list[int] = []
    monkeypatch.setattr(
        _ai_mod,
        "_auto_integrate_resolve_context",
        lambda _config, _scope: (tmp_path, "feature", base, "refreshed"),
    )
    monkeypatch.setattr(
        _ai_mod,
        "_auto_integrate_check_skip_conditions",
        lambda _root, _branch, _target: None,
    )
    monkeypatch.setattr(
        _ai_mod,
        "observe_conflict_identity",
        lambda _root, _target: "feature-target-identity",
    )
    monkeypatch.setattr(
        _ai_mod,
        "resolver_allowed",
        lambda _state, _target, _identity, **_kwargs: True,
    )
    monkeypatch.setattr(
        _ai_mod,
        "_refresh_target",
        lambda _config, _root, _target: "refreshed",
    )

    def _retrying_runner(*_args: object, **_kwargs: object) -> tuple[RebaseState, bool]:
        attempts.append(len(attempts) + 1)
        return (
            RebaseState(
                last_action="rebased",
                last_target=base,
                last_reason="target advanced concurrently (CAS mismatch)",
                fast_forwarded=False,
            ),
            True,
        )

    monkeypatch.setattr(_ai_mod, "_integrate_once", _retrying_runner)

    lines: list[str] = []
    sink_id = logger.add(lines.append, level="INFO", format="{message}")
    try:
        outcome = auto_integrate_after_commit(
            _build_config(tmp_path, target=base),
            WorkspaceScope(tmp_path),
            RebaseState(),
            **_NO_INTEGRATION_BACKOFF,
        )
    finally:
        logger.remove(sink_id)

    assert outcome is not None
    assert outcome.fast_forwarded is False
    assert outcome.last_reason is not None
    assert "exhausted 3 integration attempts" in outcome.last_reason, (
        f"the recorded reason must say the attempt budget was spent, got {outcome.last_reason!r}"
    )
    assert "advanced concurrently" in outcome.last_reason
    assert attempts == [1, 2, 3]

    promised_retries = [line for line in lines if "re-integrating onto the moved target" in line]
    assert len(promised_retries) == _ai_mod._MAX_INTEGRATION_ATTEMPTS - 1, (
        "the re-integration line must be emitted only when another attempt"
        f" will actually run, got {promised_retries!r}"
    )


def test_fast_forward_target_non_ancestor_is_reported(tmp_git_repo: Path) -> None:
    from ralph.pipeline.auto_integrate_ff import fast_forward_target

    base = _base_branch(tmp_git_repo)
    base_seed_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "base_only.txt", "base only\n", "base only")
    base_ref_before = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_ref_before != base_seed_sha, (
        "setup: base must have advanced past the seed for the divergence to be real"
    )
    feature_sha = _run(tmp_git_repo, "rev-parse", "refs/heads/feature").stdout.strip()

    ok, reason = fast_forward_target(tmp_git_repo, base, feature_sha)
    assert ok is False, f"AC-08 ff: non-ancestor target must report ok=False, got ok={ok!r}"
    assert reason == ("target advanced concurrently (not an ancestor of feature)"), (
        f"AC-08 ff: defensive reason for non-ancestor target must"
        f" match the producer literal, got {reason!r}"
    )
    base_ref_after = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_ref_after == base_ref_before, (
        f"AC-08 ff: target ref must be byte-unchanged on a refused"
        f" non-ancestor ff, before={base_ref_before!r}"
        f" after={base_ref_after!r}"
    )


def test_dirty_target_worktree_opt_out_leaves_ref_and_files_unchanged(
    tmp_git_repo: Path,
) -> None:
    base = _base_branch(tmp_git_repo)
    wt_branch = "wt-target"
    _commit(tmp_git_repo, "conflict.txt", "base content\n", "seed base")
    _run(tmp_git_repo, "branch", wt_branch, base)
    _commit(tmp_git_repo, "base_marker.txt", "base marker\n", "base marker")
    _run(tmp_git_repo, "checkout", "-b", "feature")
    feature_sha = _commit(
        tmp_git_repo,
        "conflict.txt",
        "feature content\n",
        "feature rewrites conflict.txt",
    )
    wt_branch_sha_before = _run(tmp_git_repo, "rev-parse", f"refs/heads/{wt_branch}").stdout.strip()
    assert feature_sha != wt_branch_sha_before
    wt_path = tmp_git_repo.parent / f"{tmp_git_repo.name}-wt"
    _run(tmp_git_repo, "worktree", "add", str(wt_path), wt_branch)
    try:
        tracked_in_wt = wt_path / "conflict.txt"
        tracked_in_wt.write_text("worktree dirty content\n", encoding="utf-8")
        wt_status = _run(wt_path, "status", "--porcelain", "--untracked-files=no").stdout.strip()
        assert "conflict.txt" in wt_status, (
            f"preflight: linked worktree must report the tracked dirty file, got {wt_status!r}"
        )
        config = _build_config(tmp_git_repo, target=wt_branch, reclaim_target_worktree=False)
        scope = WorkspaceScope(tmp_git_repo)
        outcome = auto_integrate_after_commit(
            config, scope, RebaseState(), **_NO_INTEGRATION_BACKOFF
        )
        assert outcome is not None
        assert outcome.last_action in {"rebased", "merged"}, (
            f"AC-10: integration must still reach the rebase phase"
            f" (last_action reflects the rebase outcome), got"
            f" last_action={outcome.last_action!r}"
        )
        assert outcome.last_target == wt_branch
        assert outcome.fast_forwarded is False, (
            "AC-10: shared target ref MUST NOT advance while the "
            "checked-out sibling worktree refused merge --ff-only -- "
            "the previous CAS fallback silently desynced that checkout"
        )
        assert outcome.last_reason is not None, (
            "AC-08: integration MUST surface a loud retryable skip "
            "when the target's checked-out worktree refused merge --ff-only"
        )
        assert "merge --ff-only" in outcome.last_reason or "checked out" in outcome.last_reason, (
            f"AC-08: skip reason must name merge --ff-only and the "
            f"checked-out sibling, got {outcome.last_reason!r}"
        )
        wt_branch_sha_after_dirty = _run(
            tmp_git_repo, "rev-parse", f"refs/heads/{wt_branch}"
        ).stdout.strip()
        assert wt_branch_sha_after_dirty == wt_branch_sha_before, (
            f"AC-10: target ref MUST be byte-unchanged while the "
            f"sibling worktree refuses merge --ff-only, before="
            f"{wt_branch_sha_before!r} after={wt_branch_sha_after_dirty!r}"
        )
        assert (wt_path / "conflict.txt").exists(), (
            "AC-10: dirty file in the target worktree must not be removed"
        )
        assert (wt_path / "conflict.txt").read_text(
            encoding="utf-8"
        ) == "worktree dirty content\n", (
            "AC-10: dirty file in the target worktree must not be modified"
        )
        wt_status_after = _run(
            wt_path, "status", "--porcelain", "--untracked-files=no"
        ).stdout.strip()
        assert "conflict.txt" in wt_status_after, (
            f"AC-10: linked worktree must remain dirty, got status={wt_status_after!r}"
        )
        tracked_in_wt.write_text("base content\n", encoding="utf-8")
        wt_status_clean = _run(
            wt_path, "status", "--porcelain", "--untracked-files=no"
        ).stdout.strip()
        assert wt_status_clean == "", (
            f"preflight: worktree must be clean before next-seam test, got {wt_status_clean!r}"
        )
        outcome_clean = auto_integrate_after_commit(
            config, scope, RebaseState(), **_NO_INTEGRATION_BACKOFF
        )
        assert outcome_clean is not None
        assert outcome_clean.fast_forwarded is True, (
            f"AC-09: next clean seam MUST land via merge --ff-only "
            f"with fast_forwarded=True, got {outcome_clean!r}"
        )
        wt_branch_sha_after_clean = _run(
            tmp_git_repo, "rev-parse", f"refs/heads/{wt_branch}"
        ).stdout.strip()
        assert wt_branch_sha_after_clean != wt_branch_sha_before, (
            "AC-09: clean seam MUST advance the target ref"
        )
        assert wt_branch_sha_after_clean == feature_sha, (
            f"AC-09: clean seam MUST land the target at the rebased "
            f"feature tip, got {wt_branch_sha_after_clean!r} vs "
            f"{feature_sha!r}"
        )
    finally:
        _run(tmp_git_repo, "worktree", "remove", "--force", str(wt_path))
