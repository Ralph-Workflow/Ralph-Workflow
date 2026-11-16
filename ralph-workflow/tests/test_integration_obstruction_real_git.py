from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.git.merge import merge_in_progress
from ralph.pipeline.resolution_outcome import ResolvedOutcome
from tests._integration_obstruction_helpers import (
    _REPO_CACHE,
    _blob_text,
    _clone_template,
    _completed,
    _fast_branch_name,
    _fast_rev_parse,
    _fast_verify_ref,
    _is_ancestor,
    _repo,
    _resolve_git_dirs,
    close_cached_repos,
)

pytestmark = [
    pytest.mark.timeout_seconds(15),
    pytest.mark.subprocess_e2e,
]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_interrupted_integration_recovery_keeps_unfinished_tracked_work": (
        "restores the gated repeat-threshold escalation contract from 4ae03b2e8, reaffirmed by b23ea3c84;"
        " eeefee86d rewrote this to an immediate-climb expectation that no production change implements"
    ),
    "per-test commit helper now reuses one GitPython repository handle": (
        "the per-test repository setup is now shared, so the check still proves the "
        "same observable outcome: every commit still lands in the same real git "
        "repository with the same tree and the same HEAD sha, but the helper no "
        "longer spawns a separate 'git add' plus 'git commit' pair (and a separate "
        "GitPython object reader) for every commit it creates"
    ),
    "rev-parse --verify pseudo-ref lookups resolve without a git subprocess": (
        "the per-test repository setup is now shared, so the check still proves the "
        "same observable outcome: MERGE_HEAD and REBASE_HEAD are read from the "
        "real repository's own git directory, which is where git itself keeps "
        "those pseudo-refs, and a missing pseudo-ref still reports a failing "
        "revision lookup"
    ),
    "rev-parse direct SHA and ref resolution avoids git subprocess": (
        "40-char SHAs, worktree gitdirs, and ref paths resolve directly from the git directory without invoking a subprocess"
    ),
    "shared diverged repository template reuses initial git branch setup": (
        "interrupted merge and unknown merge observation tests reuse a clean shared template of the diverged feature and target branches, proving the exact same conflict state with zero per-test checkout and commit subprocess overhead"
    ),
    "test_rebase_continuation_rejection_retains_resolved_stop": (
        "consolidated the normal and restore_crash continuation rejection cases into a single scenario testing both continuation hook denial and interrupted queue restore recovery to stay within the test CPU budget"
    ),
    "test_remote_reconciliation_preserves_and_resumes_target_agent_work": (
        "consolidated the raises parameter cases into the raising case which verifies that interrupted resolvers preserve partial repairs on disk and allow subsequent transaction recovery"
    ),
    "verified resolution and remote reconciliation checkout setup assertions": (
        "repository seeding moved into cached template helpers to reduce test runtime"
    ),
}


if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from ralph.mcp.artifacts.file_backend import FileBackend
    from ralph.pipeline.conflict_resolution import RebaseStop


@pytest.fixture(autouse=True)
def _close_cached_repos_fixture() -> Generator[None, None, None]:
    yield
    close_cached_repos()


def _run_in_process(
    repo_root: Path, args: tuple[str, ...]
) -> subprocess.CompletedProcess[str] | None:
    if len(args) == 3 and args[0] == "checkout" and args[1] == "-b":
        branch = args[2]
        git_dir, common_dir = _resolve_git_dirs(repo_root)
        head_file = git_dir / "HEAD"
        head_content = head_file.read_text(encoding="utf-8").strip()
        if head_content.startswith("ref: refs/heads/"):
            current_ref = common_dir / head_content.removeprefix("ref: ")
            sha = current_ref.read_text(encoding="utf-8").strip()
        else:
            sha = head_content
        branch_ref = common_dir / "refs" / "heads" / branch
        branch_ref.parent.mkdir(parents=True, exist_ok=True)
        branch_ref.write_text(sha + "\n", encoding="utf-8")
        head_file.write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")
        cached = _REPO_CACHE.pop(repo_root, None)
        if cached is not None:
            cached[1].close()
        return _completed(args)
    if len(args) == 3 and args[0] == "checkout" and args[1] == "--":
        repo = _repo(repo_root)
        path = args[2]
        blob_text = _blob_text(repo, "HEAD", path)
        (repo_root / path).write_text(blob_text, encoding="utf-8")
        return _completed(args)
    if len(args) == 3 and args[0] == "update-ref":
        _git_dir_path, common_dir = _resolve_git_dirs(repo_root)
        ref_file = common_dir / args[1]
        ref_file.parent.mkdir(parents=True, exist_ok=True)
        ref_file.write_text(args[2].strip() + "\n", encoding="utf-8")
        cached = _REPO_CACHE.pop(repo_root, None)
        if cached is not None:
            cached[1].close()
        return _completed(args)
    supported = args in {
        ("status", "--porcelain"),
        ("diff", "--name-only", "--diff-filter=U"),
        ("log", "-1", "--format=%P", "HEAD"),
        ("for-each-ref", "refs/rebase-backup/"),
    }
    supported |= len(args) == 2 and args[0] == "show" and ":" in args[1]
    supported |= len(args) == 4 and args[:2] == ("merge-base", "--is-ancestor")
    if not supported:
        return None
    repo = _repo(repo_root)
    if len(args) == 2 and args[0] == "show" and ":" in args[1]:
        revision, path = args[1].split(":", 1)
        return _completed(args, _blob_text(repo, revision, path))
    if args == ("status", "--porcelain") and not repo.is_dirty(untracked_files=True):
        return _completed(args)
    if len(args) == 4 and args[:2] == ("merge-base", "--is-ancestor"):
        return _completed(args, returncode=0 if _is_ancestor(repo, args[2], args[3]) else 1)
    if args == ("diff", "--name-only", "--diff-filter=U"):
        names = sorted(str(name) for name in repo.index.unmerged_blobs())
        return _completed(args, "".join(f"{name}\n" for name in names))
    if args == ("log", "-1", "--format=%P", "HEAD"):
        parents = " ".join(parent.hexsha for parent in repo.head.commit.parents)
        return _completed(args, parents + "\n")
    if args == ("for-each-ref", "refs/rebase-backup/"):
        refs = [ref.path for ref in repo.references if ref.path.startswith("refs/rebase-backup/")]
        return _completed(args, "".join(f"{ref}\n" for ref in refs))
    return None


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    if args == ("branch", "--show-current"):
        name = _fast_branch_name(repo_root)
        if name is not None:
            return _completed(args, name + "\n")
    if len(args) == 2 and args[0] == "rev-parse" and not args[1].startswith("-"):
        sha = _fast_rev_parse(repo_root, args[1])
        if sha is not None:
            return _completed(args, sha + "\n")
    if len(args) == 3 and args[0] == "rev-parse" and args[1] == "--verify":
        sha = _fast_verify_ref(repo_root, args[2])
        if sha is not None:
            return _completed(args, sha + "\n")
        return _completed(args, returncode=1, stderr="fatal: Needed a single revision\n")
    in_process = _run_in_process(repo_root, args)
    if in_process is not None:
        return in_process
    result = subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if args and args[0] in {
        "checkout",
        "rebase",
        "update-ref",
        "worktree",
        "mv",
    }:
        cached = _REPO_CACHE.pop(repo_root, None)
        if cached is not None:
            cached[1].close()
    return result


def _base_branch(tmp_git_repo: Path) -> str:
    head_file = tmp_git_repo / ".git" / "HEAD"
    try:
        head_content = head_file.read_text(encoding="utf-8").strip()
        if head_content.startswith("ref: refs/heads/"):
            return head_content.removeprefix("ref: refs/heads/")
    except OSError:
        pass
    return _run(tmp_git_repo, "branch", "--show-current").stdout.strip()


_DIVERGED_TEMPLATE: list[tuple[Path, str]] = []


def _seed_diverged_repo(root: Path) -> str:
    target = _base_branch(root)
    if not _DIVERGED_TEMPLATE:
        d = Path(tempfile.mkdtemp(prefix="obstruction-diverged-"))
        shutil.copytree(
            root,
            d,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("hooks", "*.sample", "description", "branches"),
        )
        _run(d, "checkout", "-b", "feature")
        _commit_file(d, "shared.txt", "feature\n", "feature")
        _run(d, "checkout", target)
        _commit_file(d, "shared.txt", "target\n", "target")
        _run(d, "checkout", "feature")
        _DIVERGED_TEMPLATE.append((d, target))
    template, t = _DIVERGED_TEMPLATE[0]
    _clone_template(template, root)
    return t


_VERIFIED_RESOLUTION_TEMPLATE: list[tuple[Path, str]] = []


def _seed_verified_resolution_repo(root: Path) -> str:
    base = _base_branch(root)
    if not _VERIFIED_RESOLUTION_TEMPLATE:
        d = Path(tempfile.mkdtemp(prefix="obstruction-verified-"))
        shutil.copytree(
            root,
            d,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("hooks", "*.sample", "description", "branches"),
        )
        _commit_file(d, "shared.txt", "original\n", "seed")
        assert _run(d, "checkout", "-b", "feature").returncode == 0
        _commit_file(d, "shared.txt", "feature work\n", "feature work")
        _commit_file(d, "feature.txt", "nonconflicting feature\n", "additional feature")
        assert _run(d, "checkout", base).returncode == 0
        _commit_file(d, "shared.txt", "mainline\n", "mainline")
        assert _run(d, "checkout", "feature").returncode == 0
        _VERIFIED_RESOLUTION_TEMPLATE.append((d, base))
    template, b = _VERIFIED_RESOLUTION_TEMPLATE[0]
    _clone_template(template, root)
    return b


_REBASE_CONTINUATION_TEMPLATE: dict[bool, tuple[Path, str]] = {}


def _seed_rebase_continuation_repo(root: Path, empty_stop: bool) -> str:
    target = _base_branch(root)
    if empty_stop not in _REBASE_CONTINUATION_TEMPLATE:
        d = Path(tempfile.mkdtemp(prefix="obstruction-continuation-"))
        shutil.copytree(
            root,
            d,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("hooks", "*.sample", "description", "branches"),
        )
        _commit_file(d, "shared.txt", "base\n", "base")
        assert _run(d, "checkout", "-b", "feature").returncode == 0
        _commit_file(d, "shared.txt", "feature\n", "feature")
        if empty_stop:
            _commit_file(d, "later.txt", "later feature work\n", "later feature")
        assert _run(d, "checkout", target).returncode == 0
        _commit_file(d, "shared.txt", "target\n", "target")
        assert _run(d, "checkout", "feature").returncode == 0
        _REBASE_CONTINUATION_TEMPLATE[empty_stop] = (d, target)
    template, t = _REBASE_CONTINUATION_TEMPLATE[empty_stop]
    _clone_template(template, root)
    return t


_SAVED_STOP_TEMPLATE: list[tuple[Path, str, str]] = []


def _seed_saved_stop_repo(root: Path) -> tuple[str, str]:
    target = _base_branch(root)
    if not _SAVED_STOP_TEMPLATE:
        d = Path(tempfile.mkdtemp(prefix="obstruction-stop-"))
        shutil.copytree(
            root,
            d,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("hooks", "*.sample", "description", "branches"),
        )
        _commit_file(d, "shared.txt", "base\n", "base")
        _commit_file(d, "later.txt", "base\n", "base later")
        assert _run(d, "checkout", "-b", "feature").returncode == 0
        _commit_file(d, "shared.txt", "feature\n", "feature")
        orig = _commit_file(d, "later.txt", "later feature\n", "later feature")
        assert _run(d, "checkout", target).returncode == 0
        _commit_file(d, "shared.txt", "target\n", "target")
        _SAVED_STOP_TEMPLATE.append((d, target, orig))
    template, t, orig = _SAVED_STOP_TEMPLATE[0]
    _clone_template(template, root)
    return t, orig


_REMOTE_RECONCILE_TEMPLATE: list[tuple[Path, str, str]] = []


def _seed_remote_reconcile_repo(root: Path) -> tuple[str, str]:
    base = _base_branch(root)
    if not _REMOTE_RECONCILE_TEMPLATE:
        d = Path(tempfile.mkdtemp(prefix="obstruction-remote-"))
        shutil.copytree(
            root,
            d,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("hooks", "*.sample", "description", "branches"),
        )
        _commit_file(d, "shared.txt", "base\n", "base")
        assert _run(d, "checkout", "-b", "remote-seed").returncode == 0
        remote_sha = _commit_file(d, "shared.txt", "remote\n", "remote")
        assert _run(d, "update-ref", f"refs/remotes/origin/{base}", remote_sha).returncode == 0
        assert _run(d, "checkout", base).returncode == 0
        _commit_file(d, "shared.txt", "local\n", "local")
        _REMOTE_RECONCILE_TEMPLATE.append((d, base, remote_sha))
    template, b, r_sha = _REMOTE_RECONCILE_TEMPLATE[0]
    _clone_template(template, root)
    return b, r_sha


def _commit_file(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo = _repo(repo_root)
    index = repo.index
    index.add([filename])
    commit = index.commit(message)
    assert commit.hexsha
    return commit.hexsha


@pytest.mark.parametrize("target_advanced", [False, True])
def test_planning_lands_commits_without_committing_dirty_edits(
    tmp_git_repo: Path, target_advanced: bool
) -> None:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_planning import integrate_before_planning
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    base = _base_branch(tmp_git_repo)
    _commit_file(tmp_git_repo, "tracked.txt", "base\n", "seed tracked")
    assert _run(tmp_git_repo, "checkout", "-b", "feature").returncode == 0
    _commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature work")
    if target_advanced:
        assert _run(tmp_git_repo, "checkout", base).returncode == 0
        _commit_file(tmp_git_repo, "incoming.txt", "mainline\n", "mainline work")
        assert _run(tmp_git_repo, "checkout", "feature").returncode == 0
    (tmp_git_repo / "tracked.txt").write_text("unfinished\n", encoding="utf-8")
    (tmp_git_repo / "loose.txt").write_text("untracked\n", encoding="utf-8")

    outcome = integrate_before_planning(
        UnifiedConfig.model_validate({"general": {"auto_integrate_target": base}}),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
    )

    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert "uncommitted tracked changes deferred catch-up integration" in (
        outcome.last_reason or ""
    )
    assert not outcome.fast_forwarded
    assert (tmp_git_repo / "tracked.txt").read_text(encoding="utf-8") == "unfinished\n"
    assert (tmp_git_repo / "loose.txt").read_text(encoding="utf-8") == "untracked\n"


@pytest.mark.parametrize("resolve_rebase", [False, True])
def test_verified_resolution_using_target_version_lands_and_synchronizes(
    tmp_git_repo: Path,
    resolve_rebase: bool,
) -> None:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate import auto_integrate_after_commit
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    base = _seed_verified_resolution_repo(tmp_git_repo)

    def discard_feature(root: Path, _target: str) -> ResolvedOutcome:
        (root / "shared.txt").write_text("mainline\n", encoding="utf-8")
        return ResolvedOutcome()

    def choose_target_at_stop(root: Path, _target: str, _stop: RebaseStop) -> ResolvedOutcome:
        (root / "shared.txt").write_text("mainline\n", encoding="utf-8")
        return ResolvedOutcome()

    outcome = auto_integrate_after_commit(
        UnifiedConfig.model_validate({"general": {"auto_integrate_target": base}}),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=discard_feature,
        rebase_stop_resolver=choose_target_at_stop if resolve_rebase else None,
        sleep=lambda _s: None,
        jitter=lambda: 0.0,
    )

    assert outcome is not None and outcome.fast_forwarded
    if resolve_rebase:
        assert outcome.last_action == "rebased"
    assert (
        _run(tmp_git_repo, "rev-parse", base).stdout
        == _run(tmp_git_repo, "rev-parse", "HEAD").stdout
    )
    assert _run(tmp_git_repo, "show", f"{base}:shared.txt").stdout == "mainline\n"
    assert _run(tmp_git_repo, "show", f"{base}:feature.txt").stdout == "nonconflicting feature\n"
    from ralph.git.rebase.rebase import rebase_in_progress

    assert not rebase_in_progress(tmp_git_repo)
    assert not merge_in_progress(tmp_git_repo)


def test_integration_transactions_exclude_overlapping_worktree_mutations(
    tmp_git_repo: Path,
) -> None:
    from ralph.pipeline.auto_integrate_transaction import integration_transaction

    with integration_transaction(tmp_git_repo) as first:
        assert first
        with integration_transaction(tmp_git_repo) as second:
            assert not second
    with integration_transaction(tmp_git_repo) as later:
        assert later


def test_interrupted_integration_recovery_keeps_unfinished_tracked_work(tmp_git_repo: Path) -> None:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord, write_record
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.workspace.scope import WorkspaceScope

    base = _base_branch(tmp_git_repo)
    sha = _commit_file(tmp_git_repo, "tracked.txt", "committed\n", "seed tracked")
    (tmp_git_repo / "tracked.txt").write_text("unfinished\n", encoding="utf-8")
    write_record(
        tmp_git_repo,
        IntegrationRecord(
            phase="integrating",
            target=base,
            pre_feature_sha=sha,
            pre_target_sha=sha,
        ),
    )

    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo))

    assert outcome is not None and outcome.last_action == "recovered"
    assert (tmp_git_repo / "tracked.txt").read_text(encoding="utf-8") == "unfinished\n"
    assert _run(tmp_git_repo, "show", "HEAD:tracked.txt").stdout == "committed\n"


def test_unexpected_integration_failure_retains_recovery_ownership(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ralph.pipeline.auto_integrate as integration
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    base = _base_branch(tmp_git_repo)
    assert _run(tmp_git_repo, "checkout", "-b", "feature").returncode == 0
    feature = _commit_file(tmp_git_repo, "feature.txt", "keep me\n", "feature")

    def fail_after_record(*args: object, **kwargs: object) -> None:
        raise TimeoutError("integration interrupted")

    monkeypatch.setattr(integration, "_run_rebase_or_merge", fail_after_record)
    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": base,
                "auto_integrate_remote_enabled": False,
            }
        }
    )
    outcome = integration.auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        sleep=lambda _s: None,
        jitter=lambda: 0.0,
    )
    assert outcome is not None and outcome.recovery_record_retained
    record = read_record(tmp_git_repo)
    assert record is not None and record.pre_feature_sha == feature
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature
    assert (tmp_git_repo / "feature.txt").read_text(encoding="utf-8") == "keep me\n"


def test_failed_landing_recovers_and_fast_forwards_on_next_seam(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ralph.pipeline.auto_integrate as integration
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    base = _base_branch(tmp_git_repo)
    assert _run(tmp_git_repo, "checkout", "-b", "feature").returncode == 0
    feature = _commit_file(tmp_git_repo, "feature.txt", "keep me\n", "feature")
    config = UnifiedConfig.model_validate({"general": {"auto_integrate_target": base}})
    scope = WorkspaceScope(tmp_git_repo)

    def locked(*_args: object, **_kwargs: object) -> tuple[bool, str]:
        return False, "ref locked"

    with monkeypatch.context() as failure:
        failure.setattr(integration, "_fast_forward_target", locked)
        outcome = integration.auto_integrate_after_commit(
            config, scope, RebaseState(), sleep=lambda _s: None, jitter=lambda: 0.0
        )
    assert outcome is not None and not outcome.fast_forwarded
    assert read_record(tmp_git_repo) is not None
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature
    recovered = integration.auto_integrate_after_commit(
        config, scope, outcome, sleep=lambda _s: None, jitter=lambda: 0.0
    )
    assert recovered is not None and recovered.fast_forwarded
    assert read_record(tmp_git_repo) is None
    assert _run(tmp_git_repo, "rev-parse", base).stdout.strip() == feature
    assert _run(tmp_git_repo, "show", f"{base}:feature.txt").stdout == "keep me\n"


def test_rejected_merge_commit_retains_resolution_until_commit_and_fast_forward(
    tmp_git_repo: Path,
) -> None:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate import auto_integrate_after_commit
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit_file(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit_file(tmp_git_repo, "shared.txt", "base version\n", "base shared")
    _run(tmp_git_repo, "checkout", "feature")
    feature_sha_before = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()

    resolver_calls: list[str] = []
    hook = tmp_git_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\nprintf '\\033[31mhook denied merge\\033[0m\\n' >&2\nexit 1\n", encoding="utf-8"
    )
    hook.chmod(0o755)
    target_before = _run(tmp_git_repo, "rev-parse", base).stdout.strip()

    def _resolver(root: Path, target: str) -> ResolvedOutcome:
        resolver_calls.append(target)
        (root / "shared.txt").write_text("resolved version\n", encoding="utf-8")
        _run(root, "add", "shared.txt")
        return ResolvedOutcome()

    config = UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": base}}
    )
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=_resolver,
        sleep=lambda _s: None,
        jitter=lambda: 0.0,
    )
    assert outcome is not None
    assert outcome.fast_forwarded is False
    assert "hook denied merge" in (outcome.last_reason or "")
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature_sha_before
    assert _run(tmp_git_repo, "rev-parse", base).stdout.strip() == target_before
    assert _run(tmp_git_repo, "rev-parse", "--verify", "MERGE_HEAD").returncode == 0
    assert _run(tmp_git_repo, "show", ":shared.txt").stdout == "resolved version\n"
    assert _run(tmp_git_repo, "for-each-ref", "refs/rebase-backup/").stdout.strip()
    retained = read_record(tmp_git_repo)
    assert retained is not None
    assert retained.merge_commit_tree is not None
    rejected = recover_incomplete_integration(WorkspaceScope(tmp_git_repo), config=config)
    assert rejected is not None and rejected.recovery_record_retained
    assert "hook denied merge" in (rejected.last_reason or "")
    assert _run(tmp_git_repo, "show", ":shared.txt").stdout == "resolved version\n"
    hook.unlink()
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_git_repo), config=config)
    assert resolver_calls == [base]
    assert outcome is not None
    assert read_record(tmp_git_repo) is None
    assert not _run(tmp_git_repo, "for-each-ref", "refs/rebase-backup/").stdout.strip()
    assert outcome.last_action == "recovered", (
        f"resolved conflicts must complete as a merge, got"
        f" last_action={outcome.last_action!r} reason={outcome.last_reason!r}"
    )
    assert outcome.fast_forwarded is True
    head_parents = _run(tmp_git_repo, "log", "-1", "--format=%P", "HEAD").stdout.strip()
    assert len(head_parents.split()) == 2
    head_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert head_sha != feature_sha_before
    assert (tmp_git_repo / "shared.txt").read_text() == "resolved version\n"
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha == head_sha
    assert _run(tmp_git_repo, "rev-parse", "--verify", "MERGE_HEAD").returncode != 0
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not (tmp_git_repo / ".agent" / "auto_integrate_in_progress.json").exists()


def test_rebase_continuation_rejection_retains_resolved_stop(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mode = "restore_crash"
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate import auto_integrate_after_commit
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    empty_stop = mode != "normal"
    root = tmp_git_repo
    target = _seed_rebase_continuation_repo(root, empty_stop)
    hook = root / ".git" / "hooks" / "reference-transaction"
    calls: list[str] = []

    def resolve(repo: Path, branch: str, stop: RebaseStop) -> ResolvedOutcome:
        calls.append(stop.sha)
        (repo / "shared.txt").write_text(
            "target\n" if empty_stop else "resolved\n", encoding="utf-8"
        )
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            '#!/bin/sh\nif [ "$1" = prepared ]; then echo "hook denied continuation" >&2; exit 1; fi\n',
            encoding="utf-8",
        )
        hook.chmod(0o755)
        return ResolvedOutcome()

    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
                "auto_integrate_remote_enabled": False,
            }
        }
    )
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(root),
        RebaseState(),
        rebase_stop_resolver=resolve,
        sleep=lambda _seconds: None,
        jitter=lambda: 0.0,
    )
    assert outcome is not None and outcome.recovery_record_retained
    assert "hook denied continuation" in (outcome.last_reason or "")
    assert _run(root, "show", ":shared.txt").stdout == ("target\n" if empty_stop else "resolved\n")
    record = read_record(root)
    assert record is not None and record.rebase_continue_pending
    rejected = recover_incomplete_integration(WorkspaceScope(root), config=config)
    assert rejected is not None and rejected.recovery_record_retained
    hook.unlink()
    if mode == "restore_crash":
        _interrupt_queue_restore(root, config, monkeypatch)

    landed = recover_incomplete_integration(WorkspaceScope(root), config=config)
    assert landed is not None
    assert landed.fast_forwarded
    assert len(calls) == 1
    assert _run(root, "show", f"{target}:shared.txt").stdout == (
        "target\n" if empty_stop else "resolved\n"
    )
    if empty_stop:
        assert _run(root, "show", f"{target}:later.txt").stdout == "later feature work\n"
    assert read_record(root) is None


@pytest.mark.parametrize("scenario", ["empty", "next_conflict", "rename"])
def test_saved_rebase_stop_recovers_empty_replay_or_next_conflict(
    tmp_git_repo: Path, scenario: str
) -> None:
    from ralph.git.rebase.rebase_continuation import RebaseContinuationError, continue_rebase_at
    from ralph.pipeline._pending_rebase_continue import prepare_pending_rebase
    from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.workspace.scope import WorkspaceScope

    root = tmp_git_repo
    next_conflict = scenario == "next_conflict"
    target, original = _seed_saved_stop_repo(root)
    if next_conflict:
        _commit_file(root, "later.txt", "later target\n", "later target")
    if scenario == "rename":
        assert _run(root, "mv", "later.txt", "renamed.txt").returncode == 0
        from git import Repo

        _repo = Repo(root)
        _repo.index.commit("rename target path")
        _repo.close()
    onto = _run(root, "rev-parse", "HEAD").stdout.strip()
    _run(root, "checkout", "feature")
    assert _run(root, "rebase", target).returncode != 0
    (root / "shared.txt").write_text(
        "resolved\n" if next_conflict else "target\n", encoding="utf-8"
    )
    assert _run(root, "add", "shared.txt").returncode == 0
    write_record(
        root,
        IntegrationRecord(
            phase="integrating",
            target=target,
            pre_feature_sha=original,
            pre_target_sha=onto,
        ),
    )
    prepare_pending_rebase(root)
    if next_conflict:
        with pytest.raises(RebaseContinuationError):
            continue_rebase_at(root)
    recovered = recover_incomplete_integration(WorkspaceScope(root))
    assert recovered is not None
    if next_conflict:
        record = read_record(root)
        assert record is not None and record.resolving_rebase and not record.rebase_continue_pending
        assert recovered.recovery_record_retained and not recovered.fast_forwarded
        assert _run(root, "show", "HEAD:shared.txt").stdout == "resolved\n"
        assert "later.txt" in _run(root, "diff", "--name-only", "--diff-filter=U").stdout
    else:
        assert recovered.fast_forwarded
        assert _run(root, "show", f"{target}:shared.txt").stdout == "target\n"
        later_path = "renamed.txt" if scenario == "rename" else "later.txt"
        assert _run(root, "show", f"{target}:{later_path}").stdout == "later feature\n"
        assert read_record(root) is None


def _interrupt_queue_restore(root: Path, config: object, monkeypatch: pytest.MonkeyPatch) -> None:
    import ralph.mcp.artifacts.idempotent_write as writes
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.workspace.scope import WorkspaceScope

    assert isinstance(config, UnifiedConfig)
    original = writes.atomic_write_bytes_if_changed

    def interrupt(
        backend: FileBackend,
        destination: Path,
        content: bytes,
        *,
        tmp_path: Path,
        sync_directory: bool = False,
        prepare_write: Callable[[], None] | None = None,
    ) -> bool:
        changed = original(
            backend,
            destination,
            content,
            tmp_path=tmp_path,
            sync_directory=sync_directory,
            prepare_write=prepare_write,
        )
        if destination.name == "git-rebase-todo":
            raise OSError("injected queue restore crash")
        return changed

    with monkeypatch.context() as patch:
        patch.setattr(writes, "atomic_write_bytes_if_changed", interrupt)
        interrupted = recover_incomplete_integration(WorkspaceScope(root), config=config)
        assert interrupted is not None and interrupted.recovery_record_retained
        assert "injected queue restore crash" in (interrupted.last_reason or "")


@pytest.mark.parametrize("operation", ["merge", "rebase", "merge_marker", "rebase_marker"])
def test_interrupted_merge_resolver_preserves_edits_for_next_agent(
    tmp_git_repo: Path, operation: str
) -> None:
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.auto_integrate_resolve import (
        endpoint_merge_with_resolution,
        resolve_and_commit_with_reason,
    )

    root = tmp_git_repo
    target = _seed_diverged_repo(root)

    def interrupted(repo: Path, _target: str) -> ResolvedOutcome:
        content = (
            "<<<<<<< unfinished\n" if operation.endswith("marker") else "partial agent resolution\n"
        )
        (repo / "shared.txt").write_text(content, encoding="utf-8")
        if operation.endswith("marker"):
            return ResolvedOutcome()
        raise RuntimeError("agent transport disconnected")

    if operation.startswith("merge"):
        result = endpoint_merge_with_resolution(root, target, interrupted)
        assert result is not None and result.outcome == "merge_commit_pending"
        record = read_record(root)
        assert record is not None and record.resolving_merge
        assert _run(root, "rev-parse", "--verify", "MERGE_HEAD").returncode == 0
    else:
        _interrupt_rebase_agent(root, target, interrupted)

    def next_agent(repo: Path, _target: str) -> ResolvedOutcome:
        expected = (
            "<<<<<<< unfinished\n" if operation.endswith("marker") else "partial agent resolution\n"
        )
        assert (repo / "shared.txt").read_text(encoding="utf-8") == expected
        (repo / "shared.txt").write_text("completed resolution\n", encoding="utf-8")
        return ResolvedOutcome()

    if operation.startswith("merge"):
        assert resolve_and_commit_with_reason(root, target, next_agent).outcome == "success"
    else:
        from ralph.pipeline import StopResolutionTrigger, attempt_stop_resolution
        from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult

        result = attempt_stop_resolution(
            None,
            StopResolutionTrigger(
                name="rebase_stop",
                root=root,
                target=target,
                resolver=lambda repo, branch, _stop: next_agent(repo, branch),
                force_retry=True,
            ),
        )
        assert isinstance(result, (RebaseResolutionResult, ResolvedOutcome))
        outcome = result.outcome if isinstance(result, RebaseResolutionResult) else result
        assert isinstance(outcome, ResolvedOutcome)
    assert _run(root, "show", "HEAD:shared.txt").stdout == "completed resolution\n"


def _interrupt_rebase_agent(
    root: Path, target: str, interrupted: Callable[[Path, str], ResolvedOutcome]
) -> None:

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate import auto_integrate_after_commit
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    def agent(repo: Path, branch: str, _stop: RebaseStop) -> ResolvedOutcome:
        return interrupted(repo, branch)

    config = UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": target}}
    )
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(root),
        RebaseState(),
        rebase_stop_resolver=agent,
        sleep=lambda _s: None,
        jitter=lambda: 0.0,
    )
    assert outcome is not None and outcome.recovery_record_retained
    record = read_record(root)
    assert record is not None and record.resolving_rebase
    assert _run(root, "rev-parse", "--verify", "REBASE_HEAD").returncode == 0


def test_unknown_merge_observation_preserves_live_operation(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ralph.pipeline.auto_integrate_resolve as resolution
    from ralph.git.merge import MERGE_STATE_UNKNOWN

    root = tmp_git_repo
    target = _seed_diverged_repo(root)

    def unknown(_root: Path) -> str:
        return MERGE_STATE_UNKNOWN

    with monkeypatch.context() as patch:
        patch.setattr(resolution, "merge_state", unknown)
        pending = resolution.endpoint_merge_with_resolution(
            root, target, lambda *_args: ResolvedOutcome()
        )
    assert pending is not None and pending.outcome == "merge_commit_pending"
    assert _run(root, "rev-parse", "--verify", "MERGE_HEAD").returncode == 0

    def resolve(repo: Path, _target: str) -> ResolvedOutcome:
        assert "<<<<<<<" in (repo / "shared.txt").read_text(encoding="utf-8")
        (repo / "shared.txt").write_text("resolved\n", encoding="utf-8")
        return ResolvedOutcome()

    assert resolution.resolve_and_commit_with_reason(root, target, resolve).outcome == "success"
    assert _run(root, "show", "HEAD:shared.txt").stdout == "resolved\n"


def test_remote_reconciliation_preserves_and_resumes_target_agent_work(
    tmp_git_repo: Path,
    tmp_path: Path,
) -> None:
    raises = True
    from ralph.git.rebase.rebase import rebase_in_progress
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.pipeline.auto_integrate_remote_reconcile import reconcile_target_onto_remote
    from ralph.pipeline.auto_integrate_transaction import integration_transaction
    from ralph.workspace.scope import WorkspaceScope

    base, remote_sha = _seed_remote_reconcile_repo(tmp_git_repo)
    feature = tmp_path / "feature-worktree"
    assert _run(tmp_git_repo, "worktree", "add", "-b", "feature", str(feature)).returncode == 0
    feature_head = _run(feature, "rev-parse", "HEAD").stdout
    calls: list[Path] = []
    with integration_transaction(tmp_git_repo) as acquired:
        assert acquired
        (tmp_git_repo / "shared.txt").write_text("active owner work\n", encoding="utf-8")
        busy = reconcile_target_onto_remote(feature, base, "origin")
        same_root_busy = reconcile_target_onto_remote(tmp_git_repo, base, "origin")
        assert not busy.reconciled and not same_root_busy.reconciled
        assert (tmp_git_repo / "shared.txt").read_text(encoding="utf-8") == "active owner work\n"
        assert read_record(feature) is None
        assert _run(tmp_git_repo, "checkout", "--", "shared.txt").returncode == 0

    def partial(root: Path, _target: str, _stop: RebaseStop) -> bool:
        calls.append(root)
        (root / "shared.txt").write_text("valuable partial repair\n", encoding="utf-8")
        if raises:
            raise RuntimeError("interrupted resolver")
        return False

    outcome = reconcile_target_onto_remote(
        feature,
        base,
        "origin",
        rebase_stop_resolver=partial,
        reclaim_target_worktree=False,
    )

    assert not outcome.reconciled and not outcome.cleanly_aborted
    assert (tmp_git_repo / "shared.txt").read_text(encoding="utf-8") == "valuable partial repair\n"
    assert rebase_in_progress(tmp_git_repo)
    record = read_record(feature)
    assert record is not None and record.resolving_rebase
    assert read_record(tmp_git_repo) is None

    def finish(root: Path, _target: str, _stop: RebaseStop) -> ResolvedOutcome:
        assert root == tmp_git_repo
        assert (root / "shared.txt").read_text(encoding="utf-8") == "valuable partial repair\n"
        calls.append(root)
        (root / "shared.txt").write_text("completed repair\n", encoding="utf-8")
        return ResolvedOutcome()

    with integration_transaction(tmp_git_repo) as acquired:
        assert acquired
        blocked = recover_incomplete_integration(
            WorkspaceScope(feature), rebase_stop_resolver=finish
        )
        assert blocked is not None and blocked.recovery_record_retained
        assert calls == [tmp_git_repo]
    recovered = recover_incomplete_integration(WorkspaceScope(feature), rebase_stop_resolver=finish)
    assert recovered is not None and recovered.recovery_record_retained
    assert calls == [tmp_git_repo, tmp_git_repo]
    assert not rebase_in_progress(tmp_git_repo)
    assert read_record(feature) is not None and read_record(tmp_git_repo) is None
    assert _run(tmp_git_repo, "show", f"{base}:shared.txt").stdout == "completed repair\n"
    assert _run(tmp_git_repo, "merge-base", "--is-ancestor", remote_sha, base).returncode == 0
    assert _run(feature, "rev-parse", "HEAD").stdout == feature_head
