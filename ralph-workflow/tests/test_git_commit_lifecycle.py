from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Repo
from pytest import MonkeyPatch

from ralph.git import operations as operations_module
from ralph.git.commit_result import CommitCreationStatus
from ralph.git.errors import GitOperationError
from ralph.git.git_run_result import GitRunResult

if TYPE_CHECKING:
    from ralph.git.subprocess_runner import GitRunOptions


def _stage_change(repo: Repo, repo_root: Path, name: str, content: str) -> None:
    path = repo_root / name
    path.write_text(content, encoding="utf-8")
    repo.index.add([name])


@pytest.mark.subprocess_e2e
def test_create_commit_cas_race_does_not_publish_candidate(
    tmp_git_repo: Path, monkeypatch: MonkeyPatch
) -> None:
    repo = Repo(tmp_git_repo)
    try:
        expected_head = repo.head.commit.hexsha
        _stage_change(repo, tmp_git_repo, "work.txt", "candidate work\n")
        original_run_git = operations_module.run_git
        advanced = False

        def racing_run_git(
            args: tuple[str, ...], *, cwd: Path, label: str, options: GitRunOptions | None = None
        ) -> GitRunResult:
            nonlocal advanced
            result = original_run_git(args, cwd=cwd, label=label, options=options)
            if label == "git-write-tree" and not advanced:
                advanced = True
                marker = tmp_git_repo / "race.txt"
                marker.write_text("concurrent\n", encoding="utf-8")
                repo.index.add(["race.txt"])
                repo.index.commit("concurrent advance")
            return result

        monkeypatch.setattr(operations_module, "run_git", racing_run_git)
        result = operations_module.create_commit(
            tmp_git_repo, "candidate", expected_head=expected_head
        )
        assert result.status is CommitCreationStatus.ALREADY_ADVANCED
        assert repo.head.commit.message.startswith("concurrent advance")
        assert not repo.git.log("--all", "--format=%s", "--grep=candidate").strip()
    finally:
        repo.close()


@pytest.mark.subprocess_e2e
def test_create_commit_cas_failure_with_unchanged_head_raises_operation_error(
    tmp_git_repo: Path, monkeypatch: MonkeyPatch
) -> None:
    repo = Repo(tmp_git_repo)
    try:
        expected_head = repo.head.commit.hexsha
        _stage_change(repo, tmp_git_repo, "work.txt", "pending work\n")
        original_run_git = operations_module.run_git

        def failed_update_ref(
            args: tuple[str, ...], *, cwd: Path, label: str, options: GitRunOptions | None = None
        ) -> GitRunResult:
            if label == "git-update-ref-cas":
                return GitRunResult(
                    args=tuple(args), returncode=1, stdout="", stderr="fatal: lock denied\n"
                )
            return original_run_git(args, cwd=cwd, label=label, options=options)

        monkeypatch.setattr(operations_module, "run_git", failed_update_ref)
        with pytest.raises(GitOperationError, match="lock denied"):
            operations_module.create_commit(tmp_git_repo, "candidate", expected_head=expected_head)
        assert repo.head.commit.hexsha == expected_head
    finally:
        repo.close()


@pytest.mark.subprocess_e2e
def test_create_commit_runs_message_hooks_and_preserves_head_when_commit_msg_aborts(
    tmp_git_repo: Path,
) -> None:
    repo = Repo(tmp_git_repo)
    try:
        hooks_dir = tmp_git_repo / ".git" / "hooks"
        prepare_hook = hooks_dir / "prepare-commit-msg"
        commit_msg_hook = hooks_dir / "commit-msg"
        observed_path = tmp_git_repo / "commit-msg-observed"
        prepare_hook.write_text(
            "#!/bin/sh\nprintf '%s' 'prepared: ' > \"$1.tmp\"\n"
            'cat "$1" >> "$1.tmp"\nmv "$1.tmp" "$1"\n',
            encoding="utf-8",
        )
        commit_msg_hook.write_text(
            '#!/bin/sh\ncat "$1" > ' + str(observed_path) + "\n",
            encoding="utf-8",
        )
        prepare_hook.chmod(0o755)
        commit_msg_hook.chmod(0o755)
        expected_head = repo.head.commit.hexsha
        _stage_change(repo, tmp_git_repo, "work.txt", "hooked work\n")

        result = operations_module.create_commit(
            tmp_git_repo, "candidate", expected_head=expected_head
        )

        assert result.status is CommitCreationStatus.CREATED
        assert repo.head.commit.message.startswith("prepared: candidate")
        assert observed_path.read_text(encoding="utf-8").startswith("prepared: candidate")

        head_after_success = repo.head.commit.hexsha
        commit_msg_hook.write_text(
            "#!/bin/sh\nexit 1\n",
            encoding="utf-8",
        )
        commit_msg_hook.chmod(0o755)
        _stage_change(repo, tmp_git_repo, "more.txt", "second pass work\n")

        with pytest.raises(GitOperationError):
            operations_module.create_commit(
                tmp_git_repo, "aborted", expected_head=head_after_success
            )

        assert repo.head.commit.hexsha == head_after_success
    finally:
        repo.close()
