from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.display.auto_integrate_message import format_auto_integrate_message
from ralph.pipeline.auto_integrate_record import read_record, record_path
from ralph.pipeline.auto_integrate_terminal import verify_and_cleanup_backup
from ralph.pipeline.auto_integrate_transaction import operation_marker_path
from ralph.pipeline.integration_actions import (
    GitOps,
    default_git_ops,
    install_executors,
    reset_executors,
)
from ralph.pipeline.integration_attempt_ledger import AttemptLedger, attempt_ledger_path
from tests._scenario_harness import commit_file, git
from tests.scenarios._content_equivalence import assert_content_equivalence, drive_integration

if TYPE_CHECKING:
    from tests.scenarios._integration_run import IntegrationRun

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_publication_success_with_cleanup_failure_keeps_remote_landing": (
        "the removed assertion on git rev-parse origin/main with check=False could not "
        "prove publication because rev-parse echoes an unresolved argument while returning "
        "nonzero; the specification requires the real remote publication fact, now proved "
        "by reading refs/heads/main before and after the deterministic cleanup crash"
    ),
}

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(12),
]


def _remote_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
                "auto_integrate_remote_enabled": True,
                "auto_integrate_remote": "origin",
            }
        }
    )


def _feature(root: Path) -> str:
    commit_file(root, "base.txt", "base\n", "base")
    git(root, "checkout", "-q", "-b", "feature")
    return commit_file(root, "feature.txt", "feature\n", "feature")


class _CleanupCrashGitOps:
    def __init__(self, delegate: GitOps) -> None:
        self.delegate = delegate
        self.crashes = 0

    def rebase_onto(self, root: Path, target: str):
        return self.delegate.rebase_onto(root, target)

    def abort_rebase(self, root: Path) -> None:
        self.delegate.abort_rebase(root)

    def continue_rebase(self, root: Path) -> None:
        self.delegate.continue_rebase(root)

    def abort_merge(self, root: Path) -> bool:
        return self.delegate.abort_merge(root)

    def continue_merge(self, root: Path) -> bool:
        return self.delegate.continue_merge(root)

    def abort_cherry_pick(self, root: Path) -> bool:
        return self.delegate.abort_cherry_pick(root)

    def continue_cherry_pick(self, root: Path) -> bool:
        return self.delegate.continue_cherry_pick(root)

    def stage_all(self, root: Path) -> bool:
        return self.delegate.stage_all(root)

    def commit(self, root: Path, *, on_failure=None) -> bool:
        return self.delegate.commit(root, on_failure=on_failure)

    def fast_forward_target(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed=None,
    ):
        return self.delegate.fast_forward_target(
            root,
            target,
            feature_sha,
            reclaim_target_worktree=reclaim_target_worktree,
            on_reclaimed=on_reclaimed,
        )

    def land_feature(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed=None,
    ):
        return self.delegate.land_feature(
            root,
            target,
            feature_sha,
            reclaim_target_worktree=reclaim_target_worktree,
            on_reclaimed=on_reclaimed,
        )

    def create_backup_ref(
        self,
        root: Path,
        pre_feature_sha: str | None,
        owning_worktree: str | None = None,
    ) -> str | None:
        return self.delegate.create_backup_ref(root, pre_feature_sha, owning_worktree)

    def delete_backup_ref(self, root: Path, backup_ref: str | None) -> None:
        self.delegate.delete_backup_ref(root, backup_ref)
        self.crashes += 1
        raise RuntimeError("injected cleanup crash after verified local landing")

    def reattach_head(self, root: Path, branch: str) -> bool:
        return self.delegate.reattach_head(root, branch)


def _drive_with_cleanup_crash(
    root: Path, *, config: UnifiedConfig | None = None
) -> tuple[IntegrationRun, _CleanupCrashGitOps]:
    crashing = _CleanupCrashGitOps(default_git_ops())
    token = install_executors(git_ops=crashing)
    try:
        run = drive_integration(root, config=config)
    finally:
        reset_executors(token)
    return run, crashing


def test_local_landing_survives_publication_failure(tmp_git_repo: Path) -> None:
    feature_sha = _feature(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")
    remote = tmp_git_repo.parent / "rejecting-remote.git"
    git(tmp_git_repo.parent, "init", "-q", "--bare", str(remote))
    git(tmp_git_repo, "remote", "add", "origin", str(remote))
    git(tmp_git_repo, "push", "-q", "origin", f"{target_before}:refs/heads/main")
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    run = drive_integration(tmp_git_repo, config=_remote_config())
    assert run.error is None, (
        f"publication failure must be a lifecycle state, not a crash: {run.error}"
    )
    assert_content_equivalence(
        tmp_git_repo,
        contract="local landing must remain intact when publication fails",
        feature=feature_sha,
        source_refs=("main",),
    )
    assert run.state is not None and not run.state.recovery_record_retained, (
        "publication failure must not turn a verified local landing back into unfinished integration"
    )
    assert run.state.fast_forwarded is True
    assert run.state.last_remote == "origin"
    assert run.state.last_publication_decision == "publication_blocked"
    assert run.state.last_push_status == "hook_rejected"
    assert run.state.last_remote_sync == "already current"
    status = format_auto_integrate_message(
        rebase=run.state,
        remote=run.state.last_remote,
        remote_sync=run.state.last_remote_sync,
    )
    assert "landing (main)" in status
    assert "remote: already current" in status.lower()
    assert "pre-receive hook declined" in (run.state.last_reason or "").lower()
    completion_command_text = "git push -- origin refs/heads/main:refs/heads/main"
    completion_command = tuple(completion_command_text.split()[1:])
    hook.unlink()
    git(tmp_git_repo, *completion_command)
    assert git(remote, "rev-parse", "refs/heads/main") == git(
        tmp_git_repo, "rev-parse", "main"
    )


def test_publication_success_with_cleanup_failure_keeps_remote_landing(tmp_git_repo: Path) -> None:
    feature_sha = _feature(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")
    remote = tmp_git_repo.parent / "remote.git"
    git(tmp_git_repo.parent, "init", "-q", "--bare", str(remote))
    git(tmp_git_repo, "remote", "add", "origin", str(remote))
    git(tmp_git_repo, "push", "-q", "origin", f"{target_before}:refs/heads/main")
    run = drive_integration(tmp_git_repo, config=_remote_config())
    assert run.error is None
    published = git(remote, "rev-parse", "refs/heads/main")
    assert published == feature_sha
    delegate = default_git_ops()
    backup_ref = delegate.create_backup_ref(tmp_git_repo, feature_sha, str(tmp_git_repo))
    crashing = _CleanupCrashGitOps(delegate)
    token = install_executors(git_ops=crashing)
    try:
        with pytest.raises(RuntimeError, match="injected cleanup crash"):
            verify_and_cleanup_backup(tmp_git_repo, backup_ref, feature_sha, False)
    finally:
        reset_executors(token)
        delegate.delete_backup_ref(tmp_git_repo, backup_ref)
    assert crashing.crashes == 1
    assert git(remote, "rev-parse", "refs/heads/main") == published, (
        "publication success must remain true even when cleanup fails"
    )
    assert_content_equivalence(
        tmp_git_repo,
        contract="cleanup failure must not undo publication",
        feature=feature_sha,
        source_refs=("main",),
    )


def test_cleanup_crash_after_landing_is_finished_on_the_next_tick(tmp_git_repo: Path) -> None:
    feature_sha = _feature(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")
    first, crash = _drive_with_cleanup_crash(tmp_git_repo)
    target_after_landing = git(tmp_git_repo, "rev-parse", "main")
    retained_record = read_record(tmp_git_repo)
    assert crash.crashes == 1, "the deterministic cleanup boundary must definitely execute"
    assert first.error is None
    assert first.state is not None
    assert "injected cleanup crash" in (first.state.last_reason or "")
    assert any("injected cleanup crash" in line.content for line in first.operator_lines)
    assert target_after_landing != target_before
    assert retained_record is not None and retained_record.phase == "integrated"
    second = drive_integration(tmp_git_repo)
    assert first.error is None and second.error is None, (
        "cleanup restart must converge without a crash"
    )
    assert git(tmp_git_repo, "rev-parse", "main") == target_after_landing, (
        "the next tick after a cleanup crash must treat an already-landed operation as finished"
    )
    assert read_record(tmp_git_repo) is None, (
        "a landed operation must not retain a record that can retrigger it"
    )
    assert not git(tmp_git_repo, "for-each-ref", "--format=%(refname)", "refs/rebase-backup/")
    ledger_path = attempt_ledger_path(tmp_git_repo)
    assert ledger_path.exists()
    ledger = AttemptLedger(tmp_git_repo).read()
    assert ledger.unknown_attempts == 0
    assert tuple(ledger.repeat_counts.values()) == (1,)
    assert tuple(action for (_observation, action) in ledger.repeat_counts) == ("land",)
    marker = operation_marker_path(tmp_git_repo)
    assert marker is None or not marker.exists()
    assert not record_path(tmp_git_repo).with_suffix(".json.staging").exists()
    assert_content_equivalence(
        tmp_git_repo,
        contract="cleanup recovery must preserve the landed content",
        feature=feature_sha,
        source_refs=(target_after_landing,),
    )
