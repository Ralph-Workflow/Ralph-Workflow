"""Real-git proof that nothing in the worktree can strand a branch behind its target.

Regression 2026-10-05: 22 untracked files that ``main`` also tracks made
``git merge main`` refuse to start (exit 2). No merge ran, so no resolver
could act, and planning began 40 commits behind ``main``. Every refusal
shape is driven through real git here: identical leftovers are cleared,
differing local work remains untouched, and safe integrations land the
committed feature tip on the target.

Registered in ``REQUIRED_AUTO_INTEGRATE_E2E_FILES`` so ``make verify``
runs it even though it crosses the real git boundary.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.artifacts.file_backend import FileBackend
    from ralph.pipeline.conflict_resolution import RebaseStop

from ralph.git.merge import merge_in_progress, merge_target_into_current

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


def _base_branch(tmp_git_repo: Path) -> str:
    out = _run(tmp_git_repo, "symbolic-ref", "--quiet", "HEAD")
    return out.stdout.strip().removeprefix("refs/heads/")


def _commit_file(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run(repo_root, "add", filename)
    _run(repo_root, "commit", "-m", message)
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _behind_with_untracked_copy(tmp_git_repo: Path, content: str) -> str:
    """Feature branch behind ``base``, holding an untracked ``incoming.txt``."""
    base = _base_branch(tmp_git_repo)
    _run(tmp_git_repo, "checkout", "-b", "feature")
    _commit_file(tmp_git_repo, "feature.txt", "feature body\n", "feature work")
    _run(tmp_git_repo, "checkout", base)
    _commit_file(tmp_git_repo, "incoming.txt", "mainline body\n", "mainline adds incoming")
    _run(tmp_git_repo, "checkout", "feature")
    (tmp_git_repo / "incoming.txt").write_text(content, encoding="utf-8")
    return base


def test_merge_lands_over_untracked_copy_identical_to_target(tmp_git_repo: Path) -> None:
    """Regression 2026-10-05: an identical untracked leftover no longer blocks the merge."""
    base = _behind_with_untracked_copy(tmp_git_repo, "mainline body\n")

    result = merge_target_into_current(tmp_git_repo, base)

    assert result.outcome == "success"
    assert _run(tmp_git_repo, "merge-base", "--is-ancestor", base, "HEAD").returncode == 0
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""


def test_merge_refusal_over_differing_untracked_file_keeps_it_and_names_it(
    tmp_git_repo: Path,
) -> None:
    """Local untracked work is never deleted; the refusal names the path."""
    base = _behind_with_untracked_copy(tmp_git_repo, "local work\n")

    result = merge_target_into_current(tmp_git_repo, base)

    assert result.outcome == "conflict"
    assert result.reason == "merge refused: untracked files would be overwritten: incoming.txt"
    assert (tmp_git_repo / "incoming.txt").read_text(encoding="utf-8") == "local work\n"
    assert merge_in_progress(tmp_git_repo) is False


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

    assert outcome is not None and outcome.fast_forwarded
    assert (
        _run(tmp_git_repo, "rev-parse", base).stdout
        == _run(tmp_git_repo, "rev-parse", "HEAD").stdout
    )
    assert _run(tmp_git_repo, "show", f"{base}:feature.txt").stdout == "feature\n"
    assert _run(tmp_git_repo, "show", f"{base}:tracked.txt").stdout == "base\n"
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

    base = _base_branch(tmp_git_repo)
    _commit_file(tmp_git_repo, "shared.txt", "original\n", "seed")
    assert _run(tmp_git_repo, "checkout", "-b", "feature").returncode == 0
    _commit_file(tmp_git_repo, "shared.txt", "feature work\n", "feature work")
    _commit_file(tmp_git_repo, "feature.txt", "nonconflicting feature\n", "additional feature")
    assert _run(tmp_git_repo, "checkout", base).returncode == 0
    _commit_file(tmp_git_repo, "shared.txt", "mainline\n", "mainline")
    assert _run(tmp_git_repo, "checkout", "feature").returncode == 0

    def discard_feature(root: Path, _target: str) -> bool:
        (root / "shared.txt").write_text("mainline\n", encoding="utf-8")
        return True

    def choose_target_at_stop(root: Path, _target: str, _stop: RebaseStop) -> bool:
        (root / "shared.txt").write_text("mainline\n", encoding="utf-8")
        return True

    outcome = auto_integrate_after_commit(
        UnifiedConfig.model_validate({"general": {"auto_integrate_target": base}}),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=discard_feature,
        rebase_stop_resolver=choose_target_at_stop if resolve_rebase else None,
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
        outcome = integration.auto_integrate_after_commit(config, scope, RebaseState())
    assert outcome is not None and not outcome.fast_forwarded
    assert read_record(tmp_git_repo) is not None
    assert _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip() == feature
    recovered = integration.auto_integrate_after_commit(config, scope, outcome)
    assert recovered is not None and recovered.fast_forwarded
    assert read_record(tmp_git_repo) is None
    assert _run(tmp_git_repo, "rev-parse", base).stdout.strip() == feature
    assert _run(tmp_git_repo, "show", f"{base}:feature.txt").stdout == "keep me\n"


def test_rejected_merge_commit_retains_resolution_until_commit_and_fast_forward(
    tmp_git_repo: Path,
) -> None:
    """A real hook rejection retains verified edits and retries commit without resolving again."""
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

    def _resolver(root: Path, target: str) -> bool:
        resolver_calls.append(target)
        (root / "shared.txt").write_text("resolved version\n", encoding="utf-8")
        _run(root, "add", "shared.txt")
        return True

    config = UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": base}}
    )
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=_resolver,
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
    # The merge was committed automatically: HEAD is a 2-parent commit.
    head_parents = _run(tmp_git_repo, "log", "-1", "--format=%P", "HEAD").stdout.strip()
    assert len(head_parents.split()) == 2
    head_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert head_sha != feature_sha_before
    # The resolved content landed.
    assert (tmp_git_repo / "shared.txt").read_text() == "resolved version\n"
    # Target fast-forwarded to the merge commit.
    base_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    assert base_sha == head_sha
    # No merge state or crash record left behind.
    assert _run(tmp_git_repo, "rev-parse", "--verify", "MERGE_HEAD").returncode != 0
    assert _run(tmp_git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not (tmp_git_repo / ".agent" / "auto_integrate_in_progress.json").exists()


@pytest.mark.parametrize("mode", ["normal", "empty", "restore_crash"])
def test_rebase_continuation_rejection_retains_resolved_stop(
    tmp_git_repo: Path, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reference hook failure retries the resolved rebase without a second resolver."""
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate import auto_integrate_after_commit
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    empty_stop = mode != "normal"
    root = tmp_git_repo
    target = _base_branch(root)
    _commit_file(root, "shared.txt", "base\n", "base")
    _run(root, "checkout", "-b", "feature")
    _commit_file(root, "shared.txt", "feature\n", "feature")
    if empty_stop:
        _commit_file(root, "later.txt", "later feature work\n", "later feature")
    _run(root, "checkout", target)
    _commit_file(root, "shared.txt", "target\n", "target")
    _run(root, "checkout", "feature")
    hook = root / ".git" / "hooks" / "reference-transaction"
    calls: list[str] = []

    def resolve(repo: Path, branch: str, stop: RebaseStop) -> bool:
        calls.append(stop.sha)
        (repo / "shared.txt").write_text(
            "target\n" if empty_stop else "resolved\n", encoding="utf-8"
        )
        hook.write_text(
            '#!/bin/sh\nif [ "$1" = prepared ]; then echo "hook denied continuation" >&2; exit 1; fi\n',
            encoding="utf-8",
        )
        hook.chmod(0o755)
        return True

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
    """Restart recognizes a verified empty stop or an already-landed stop before the next conflict."""
    from ralph.git.rebase.rebase_continuation import RebaseContinuationError, continue_rebase_at
    from ralph.pipeline._pending_rebase_continue import prepare_pending_rebase
    from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.workspace.scope import WorkspaceScope

    root = tmp_git_repo
    next_conflict = scenario == "next_conflict"
    target = _base_branch(root)
    _commit_file(root, "shared.txt", "base\n", "base")
    _commit_file(root, "later.txt", "base\n", "base later")
    _run(root, "checkout", "-b", "feature")
    _commit_file(root, "shared.txt", "feature\n", "feature")
    original = _commit_file(root, "later.txt", "later feature\n", "later feature")
    _run(root, "checkout", target)
    _commit_file(root, "shared.txt", "target\n", "target")
    if next_conflict:
        _commit_file(root, "later.txt", "later target\n", "later target")
    if scenario == "rename":
        assert _run(root, "mv", "later.txt", "renamed.txt").returncode == 0
        assert _run(root, "commit", "-m", "rename target path").returncode == 0
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
    """Crash immediately after one atomic queue-file write, then release the injected fault."""
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
        _resolve_and_commit,
        endpoint_merge_with_resolution,
    )

    root = tmp_git_repo
    target = _base_branch(root)
    _run(root, "checkout", "-b", "feature")
    _commit_file(root, "shared.txt", "feature\n", "feature")
    _run(root, "checkout", target)
    _commit_file(root, "shared.txt", "target\n", "target")
    _run(root, "checkout", "feature")

    def interrupted(repo: Path, _target: str) -> bool:
        content = (
            "<<<<<<< unfinished\n" if operation.endswith("marker") else "partial agent resolution\n"
        )
        (repo / "shared.txt").write_text(content, encoding="utf-8")
        if operation.endswith("marker"):
            return True
        raise RuntimeError("agent transport disconnected")

    if operation.startswith("merge"):
        result = endpoint_merge_with_resolution(root, target, interrupted)
        assert result is not None and result.outcome == "merge_commit_pending"
        record = read_record(root)
        assert record is not None and record.resolving_merge
        assert _run(root, "rev-parse", "--verify", "MERGE_HEAD").returncode == 0
    else:
        _interrupt_rebase_agent(root, target, interrupted)

    def next_agent(repo: Path, _target: str) -> bool:
        expected = (
            "<<<<<<< unfinished\n" if operation.endswith("marker") else "partial agent resolution\n"
        )
        assert (repo / "shared.txt").read_text(encoding="utf-8") == expected
        (repo / "shared.txt").write_text("completed resolution\n", encoding="utf-8")
        return True

    if operation.startswith("merge"):
        assert _resolve_and_commit(root, target, next_agent)
    else:
        from ralph.pipeline.conflict_resolution.rebase_loop import resolve_rebase_in_progress

        assert resolve_rebase_in_progress(
            root, target, lambda repo, branch, _stop: next_agent(repo, branch)
        )
    assert _run(root, "show", "HEAD:shared.txt").stdout == "completed resolution\n"


def _interrupt_rebase_agent(
    root: Path, target: str, interrupted: Callable[[Path, str], bool]
) -> None:

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate import auto_integrate_after_commit
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

    def agent(repo: Path, branch: str, _stop: RebaseStop) -> bool:
        return interrupted(repo, branch)

    config = UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": target}}
    )
    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(root),
        RebaseState(),
        rebase_stop_resolver=agent,
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
    target = _base_branch(root)
    _run(root, "checkout", "-b", "feature")
    _commit_file(root, "shared.txt", "feature\n", "feature")
    _run(root, "checkout", target)
    _commit_file(root, "shared.txt", "target\n", "target")
    _run(root, "checkout", "feature")

    def unknown(_root: Path) -> str:
        return MERGE_STATE_UNKNOWN

    with monkeypatch.context() as patch:
        patch.setattr(resolution, "merge_state", unknown)
        pending = resolution.endpoint_merge_with_resolution(root, target, lambda *_args: True)
    assert pending is not None and pending.outcome == "merge_commit_pending"
    assert _run(root, "rev-parse", "--verify", "MERGE_HEAD").returncode == 0

    def resolve(repo: Path, _target: str) -> bool:
        assert "<<<<<<<" in (repo / "shared.txt").read_text(encoding="utf-8")
        (repo / "shared.txt").write_text("resolved\n", encoding="utf-8")
        return True

    assert resolution._resolve_and_commit(root, target, resolve)
    assert _run(root, "show", "HEAD:shared.txt").stdout == "resolved\n"


@pytest.mark.parametrize("raises", [False, True])
def test_remote_reconciliation_preserves_and_resumes_target_agent_work(
    tmp_git_repo: Path,
    tmp_path: Path,
    raises: bool,
) -> None:
    from ralph.git.rebase.rebase import rebase_in_progress
    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.pipeline.auto_integrate_remote_reconcile import reconcile_target_onto_remote
    from ralph.pipeline.auto_integrate_transaction import integration_transaction
    from ralph.workspace.scope import WorkspaceScope

    base = _base_branch(tmp_git_repo)
    _commit_file(tmp_git_repo, "shared.txt", "base\n", "base")
    assert _run(tmp_git_repo, "checkout", "-b", "remote-seed").returncode == 0
    remote_sha = _commit_file(tmp_git_repo, "shared.txt", "remote\n", "remote")
    assert (
        _run(tmp_git_repo, "update-ref", f"refs/remotes/origin/{base}", remote_sha).returncode == 0
    )
    assert _run(tmp_git_repo, "checkout", base).returncode == 0
    _commit_file(tmp_git_repo, "shared.txt", "local\n", "local")
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

    def finish(root: Path, _target: str, _stop: RebaseStop) -> bool:
        assert root == tmp_git_repo
        assert (root / "shared.txt").read_text(encoding="utf-8") == "valuable partial repair\n"
        calls.append(root)
        (root / "shared.txt").write_text("completed repair\n", encoding="utf-8")
        return True

    with integration_transaction(tmp_git_repo) as acquired:
        assert acquired
        blocked = recover_incomplete_integration(
            WorkspaceScope(feature), rebase_stop_resolver=finish
        )
        assert blocked is not None and blocked.recovery_record_retained
        assert calls == [tmp_git_repo]
    recovered = recover_incomplete_integration(WorkspaceScope(feature), rebase_stop_resolver=finish)
    assert recovered is not None and not recovered.recovery_record_retained
    assert calls == [tmp_git_repo, tmp_git_repo]
    assert not rebase_in_progress(tmp_git_repo)
    assert read_record(feature) is None and read_record(tmp_git_repo) is None
    assert _run(tmp_git_repo, "show", f"{base}:shared.txt").stdout == "completed repair\n"
    assert _run(tmp_git_repo, "merge-base", "--is-ancestor", remote_sha, base).returncode == 0
    assert _run(feature, "rev-parse", "HEAD").stdout == feature_head
