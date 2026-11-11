from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate import (
    IntegrationRecord,
    recover_incomplete_integration,
)
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit as _auto_integrate_after_commit,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.git.git_call_policy import GitCallClass
    from ralph.git.git_run_result import GitRunResult
    from ralph.git.subprocess_runner import GitRunOptions

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def auto_integrate_after_commit(*args: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("sleep", lambda _seconds: None)
    kwargs.setdefault("jitter", lambda: 0.0)
    return _auto_integrate_after_commit(*args, **kwargs)


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
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    from git import Repo

    repo = Repo(repo_root)
    repo.index.add([filename])
    commit = repo.index.commit(message)
    return commit.hexsha


def _build_config(
    tmp_git_repo: Path,
    *,
    enabled: bool = True,
    target: str | None = None,
) -> UnifiedConfig:
    general: dict[str, bool | str] = {
        "auto_integrate_enabled": enabled,
    }
    if target is not None:
        general["auto_integrate_target"] = target
    payload: dict[str, dict[str, bool | str]] = {"general": general}
    return UnifiedConfig.model_validate(payload)


class _Snapshot(TypedDict):
    head: str
    refs: dict[str, str]
    worktree: str


def _snapshot(tmp_git_repo: Path) -> _Snapshot:
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


def test_disabled_returns_none_and_repo_byte_unchanged(tmp_git_repo: Path) -> None:
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "f.txt", "f\n", "f")
    before = _snapshot(tmp_git_repo)
    config = _build_config(tmp_git_repo, enabled=False)
    scope = WorkspaceScope(tmp_git_repo)
    result = auto_integrate_after_commit(config, scope, RebaseState())
    assert result is None
    after = _snapshot(tmp_git_repo)
    assert before == after


def test_default_enabled_with_no_config_set(tmp_git_repo: Path) -> None:
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    config = UnifiedConfig.model_validate({})
    assert config.general.auto_integrate_enabled is True
    assert config.general.auto_integrate_target == "main"
    base = _base_branch(tmp_git_repo)
    if base != "main":
        _run(tmp_git_repo, "branch", "-m", base, "main")
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.last_action in {"rebased", "merged", "skipped"}
    main_sha = _run(tmp_git_repo, "rev-parse", "refs/heads/main").stdout.strip()
    feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert main_sha == feature_sha


def test_on_target_branch_skips_with_zero_mutation(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "main_only.txt", "main\n", "main only")
    before = _snapshot(tmp_git_repo)
    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert outcome.last_reason == "on target branch"
    assert outcome.last_target == base
    after = _snapshot(tmp_git_repo)
    assert before == after


def test_no_commits_beyond_target_skips_with_zero_mutation(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip() == feature_sha
    before = _snapshot(tmp_git_repo)
    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert outcome.last_reason == "no commits beyond target"
    after = _snapshot(tmp_git_repo)
    assert before == after


def test_no_target_branch_resolved_skips(tmp_git_repo: Path) -> None:
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "f.txt", "f\n", "f")
    config = _build_config(tmp_git_repo, target="non-existent-branch-xyz")
    scope = WorkspaceScope(tmp_git_repo)
    before = _snapshot(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert (
        outcome.last_reason
        == "local integration target branch does not exist: non-existent-branch-xyz"
    )
    after = _snapshot(tmp_git_repo)
    assert before == after


def test_detached_head_records_skipped_skip(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    base_sha_before = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    head_sha_before = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "checkout", "--detach", head_sha_before)
    before = _snapshot(tmp_git_repo)
    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None, "detached HEAD must yield a recorded RebaseState (AC-02), not None"
    assert outcome.last_action == "skipped"
    assert outcome.last_reason == "detached HEAD"
    assert _snapshot(tmp_git_repo) == before
    assert _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip() == base_sha_before
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    assert not record_file.exists()


def test_feature_ahead_pure_fast_forward(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    feature_sha = _commit(tmp_git_repo, "feat_ahead.txt", "feat ahead\n", "feat ahead")
    commit_count_before = int(_run(tmp_git_repo, "rev-list", "--count", "HEAD").stdout.strip())
    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.last_action in {"rebased"}
    assert outcome.last_target == base
    new_base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert new_base_sha == feature_sha
    commit_count_after = int(_run(tmp_git_repo, "rev-list", "--count", "HEAD").stdout.strip())
    assert commit_count_after == commit_count_before


def test_diverged_clean_rebase_then_ff(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "base_seed.txt", "base seed\n", "base seed")
    base_seed_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    feature_sha_before_rebase = _commit(tmp_git_repo, "feat_div.txt", "feat div\n", "feat div")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "base_div.txt", "base div\n", "base div")
    _run(tmp_git_repo, "checkout", "feature")
    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.last_action in {"rebased"}
    assert outcome.last_target == base
    feature_sha_after = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    base_sha_after = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha_after == feature_sha_after
    assert feature_sha_after != feature_sha_before_rebase


@pytest.mark.timeout_seconds(30)
def test_rebase_conflict_then_clean_endpoint_merge(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    (tmp_git_repo / "shared.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
    _run(tmp_git_repo, "add", "shared.txt")
    _run(tmp_git_repo, "commit", "-m", "shared seed")
    a_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", a_sha)
    _run(tmp_git_repo, "checkout", "feature")
    (tmp_git_repo / "shared.txt").write_text("line1\nFEATURE\nline3\n", encoding="utf-8")
    _run(tmp_git_repo, "add", "shared.txt")
    _run(tmp_git_repo, "commit", "-m", "feature: D1 modify line 2")
    (tmp_git_repo / "shared.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
    _run(tmp_git_repo, "add", "shared.txt")
    _run(tmp_git_repo, "commit", "-m", "feature: D2 revert D1")
    feature_tip_before = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    _run(tmp_git_repo, "checkout", base)
    (tmp_git_repo / "shared.txt").write_text("line1\nBASE\nline3\n", encoding="utf-8")
    _run(tmp_git_repo, "add", "shared.txt")
    _run(tmp_git_repo, "commit", "-m", "base: B modify line 2")
    b_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "checkout", "feature")
    preflight = _run(tmp_git_repo, "rebase", base)
    assert preflight.returncode != 0, (
        "preflight: expected rebase to conflict so the AC-06 scenario is real"
    )
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert (git_dir / "rebase-apply").exists() or (git_dir / "rebase-merge").exists(), (
        "preflight: expected rebase-apply/rebase-merge state on disk"
    )
    _run(tmp_git_repo, "rebase", "--abort")
    assert not (git_dir / "rebase-apply").exists()
    assert not (git_dir / "rebase-merge").exists()
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature_tip_before

    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.last_action == "merged", (
        f"AC-06: expected last_action='merged' (clean endpoint merge after"
        f" rebase conflict), got last_action={outcome.last_action!r}"
        f" reason={outcome.last_reason!r}"
    )
    assert outcome.last_target == base
    assert outcome.fast_forwarded is True
    assert not (git_dir / "rebase-apply").exists(), (
        "AC-06: rebase-apply must be gone after abort_rebase"
    )
    assert not (git_dir / "rebase-merge").exists(), (
        "AC-06: rebase-merge must be gone after abort_rebase"
    )
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == "", (
        "AC-06: working tree must be clean after abort + merge"
    )
    head_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    head_parents = _run(tmp_git_repo, "log", "-1", "--format=%P", "HEAD").stdout.strip()
    assert len(head_parents.split()) == 2, (
        f"AC-06: HEAD must be a merge commit with 2 parents, got parents={head_parents!r}"
    )
    assert head_sha != feature_tip_before
    base_sha_after = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha_after == head_sha, (
        f"AC-06: target ref {base_sha_after!r} must equal HEAD {head_sha!r}"
    )
    b_ancestor = _run(tmp_git_repo, "log", "-1", "--format=%H", f"{head_sha}^2").stdout.strip()
    assert b_ancestor == b_sha, (
        f"AC-06: HEAD^2 must be base's pre-integration tip {b_sha!r}, got {b_ancestor!r}"
    )


def test_rebase_conflict_then_clean_merge_records_merged_action(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ralph.pipeline.auto_integrate_rebase_merge as _ai_mod
    from ralph.git.rebase.rebase import RebaseConflicts

    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feature only\n", "feat")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "base.txt", "base only\n", "base")
    _run(tmp_git_repo, "checkout", "feature")

    def _fake_rebase_onto(target: str, *, repo_root: Path, options: object = None) -> object:
        return RebaseConflicts(files=["shared.txt"])

    monkeypatch.setattr(_ai_mod, "rebase_onto", _fake_rebase_onto)
    monkeypatch.setattr("ralph.git.rebase.rebase.rebase_onto", _fake_rebase_onto)

    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.last_action == "merged", (
        f"expected 'merged' (clean endpoint merge after rebase conflict),"
        f" got last_action={outcome.last_action!r} reason={outcome.last_reason!r}"
    )
    assert outcome.last_target == base
    git_dir_out = _run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip()
    git_dir = Path(git_dir_out)
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert not (git_dir / "rebase-apply").exists()
    assert not (git_dir / "rebase-merge").exists()
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""
    head_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha == head_sha
    assert outcome.fast_forwarded is True


def test_both_rebase_and_merge_conflict(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "base_seed.txt", "base seed\n", "base seed")
    base_seed_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 1\n", "base shared 1")
    _commit(tmp_git_repo, "shared2.txt", "base shared 2\n", "base shared 2")
    _run(tmp_git_repo, "checkout", "feature")
    feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    before = _snapshot(tmp_git_repo)
    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.last_action == "conflict", (
        f"AC-07: both rebase and endpoint merge conflicted, so the"
        f" outcome must be recorded as a conflict, got {outcome.last_action!r}"
    )
    assert outcome.last_reason == "rebase and endpoint merge both conflicted"
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature_sha
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""
    git_dir = Path(_run(tmp_git_repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (tmp_git_repo / git_dir).resolve()
    assert not (git_dir / "rebase-apply").exists()
    assert not (git_dir / "rebase-merge").exists()
    assert not (git_dir / "MERGE_HEAD").exists()
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    assert not record_file.exists()
    after = _snapshot(tmp_git_repo)
    assert after["head"] == before["head"], (
        "AC-07: feature HEAD must be identical to its pre-integration state"
    )
    assert after["worktree"] == before["worktree"], (
        "AC-07: working tree must be identical to its pre-integration state"
    )


def test_compare_and_swap_branch_rejects_stale_expected_sha(tmp_git_repo: Path) -> None:
    import ralph.git.merge as _merge_mod

    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat.txt", "feat\n", "feat")
    pre_landing_base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "concurrent.txt", "concurrent\n", "concurrent")
    post_landing_base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert pre_landing_base_sha != post_landing_base_sha
    target_feature_sha = _run(tmp_git_repo, "rev-parse", "feature").stdout.strip()
    cas_ok = _merge_mod.compare_and_swap_branch(
        tmp_git_repo, base, pre_landing_base_sha, target_feature_sha
    )
    assert cas_ok is False, "AC-08 CAS must fail when expected_old_sha is stale"
    final_base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert final_base_sha == post_landing_base_sha


def test_no_push_invocation(tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ralph.git.merge as _merge_mod
    import ralph.git.operations as _operations_mod
    import ralph.git.rebase.rebase_continuation as _continuation_mod
    import ralph.git.rebase.rebase_preconditions as _preconditions_mod
    import ralph.git.rebase.subprocess_executor as _executor_mod
    import ralph.git.subprocess_runner as _runner_mod

    recorded: list[tuple[str, ...]] = []
    real_run_git = _runner_mod.run_git

    def _recording_run_git(
        args: Sequence[str],
        *,
        cwd: Path | None,
        label: str,
        options: GitRunOptions | None = None,
        call_class: GitCallClass | None = None,
    ) -> GitRunResult:
        recorded.append(tuple(args))
        return real_run_git(
            args,
            cwd=cwd,
            label=label,
            options=options,
            call_class=call_class,
        )

    for mod in (
        _runner_mod,
        _merge_mod,
        _executor_mod,
        _continuation_mod,
        _preconditions_mod,
        _operations_mod,
    ):
        monkeypatch.setattr(mod, "run_git", _recording_run_git)

    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "base_div.txt", "base\n", "base div")
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "feat_div.txt", "feat\n", "feat div")
    config = _build_config(tmp_git_repo, target=base)
    scope = WorkspaceScope(tmp_git_repo)
    outcome = auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None
    assert outcome.last_action in {"rebased"}

    push_argvs = [argv for argv in recorded if "push" in argv]
    assert push_argvs == [], (
        f"auto_integrate must never invoke git push; recorded argvs "
        f"containing 'push': {push_argvs[:5]}"
    )


def test_recovery_with_record_and_no_in_progress_rebase_restores_head(
    tmp_git_repo: Path,
) -> None:
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    pre_feature_sha = _commit(tmp_git_repo, "f.txt", "f\n", "f")
    pre_target_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    record = IntegrationRecord(
        phase="integrating",
        target=base,
        pre_feature_sha=pre_feature_sha,
        pre_target_sha=pre_target_sha,
    )
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(record.model_dump_json(), encoding="utf-8")
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is not None
    assert outcome.last_action in {"recovered", "skipped"}
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == pre_feature_sha


def test_recovery_killed_after_clean_rebase_before_ff(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    pre_feature_sha = _commit(tmp_git_repo, "f.txt", "f\n", "f")
    pre_target_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    record = IntegrationRecord(
        phase="integrated",
        target=base,
        pre_feature_sha=pre_feature_sha,
        pre_target_sha=pre_target_sha,
        integrated_feature_sha=_run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip(),
    )
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(record.model_dump_json(), encoding="utf-8")
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is not None
    assert outcome.last_action in {"recovered", "skipped"}
    assert not record_file.exists()
    feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha == feature_sha


def test_recovery_killed_after_clean_merge_before_ff(tmp_git_repo: Path) -> None:
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    pre_feature_sha = _commit(tmp_git_repo, "f.txt", "f\n", "f")
    pre_target_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    record = IntegrationRecord(
        phase="integrated",
        target=base,
        pre_feature_sha=pre_feature_sha,
        pre_target_sha=pre_target_sha,
        integrated_feature_sha=_run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip(),
    )
    record_file = tmp_git_repo / ".agent" / "auto_integrate_in_progress.json"
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(record.model_dump_json(), encoding="utf-8")
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is not None
    assert outcome.last_action in {"recovered", "skipped"}
    assert not record_file.exists()


def test_recovery_no_record_is_a_noop(tmp_git_repo: Path) -> None:
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit(tmp_git_repo, "f.txt", "f\n", "f")
    _run(tmp_git_repo, "rebase", _base_branch(tmp_git_repo))
    _run(tmp_git_repo, "rebase", "--abort")
    before = _snapshot(tmp_git_repo)
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))
    assert outcome is None
    after = _snapshot(tmp_git_repo)
    assert before == after
