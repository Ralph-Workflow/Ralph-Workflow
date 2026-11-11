from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Repo

from ralph.git.rebase.process_result import ProcessResult
from ralph.git.rebase.rebase import (
    ProcessExecutor,
    RebaseConflicts,
    RebaseNoOp,
    rebase_onto,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


_CONFLICT_MENTIONING_UP_TO_DATE = (
    "CONFLICT (content): Merge conflict in shared.txt\n"
    "error: could not apply 0badc0d... local change\n"
    "hint: Your branch is up to date with 'origin/main'.\n"
)

_GENUINE_UP_TO_DATE = "Current branch feature-classify is up to date.\n"

_PLAIN_CONFLICT = (
    "CONFLICT (content): Merge conflict in shared.txt\n"
    "error: could not apply 0badc0d... local change\n"
)


class FakeProcessExecutor(ProcessExecutor):
    stdin = None

    def __init__(self, responses: Mapping[tuple[str, tuple[str, ...]], ProcessResult]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def execute(
        self,
        command: str,
        args: Sequence[str],
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> ProcessResult:
        key = (command, tuple(args))
        self.calls.append(key)
        return self.responses.get(key, ProcessResult(returncode=0, stdout="", stderr=""))


def _checkout_feature(repo_root: Path) -> str:
    head_file = repo_root / ".git" / "HEAD"
    try:
        head_content = head_file.read_text(encoding="utf-8").strip()
        if head_content.startswith("ref: refs/heads/"):
            base = head_content.removeprefix("ref: refs/heads/")
            base_ref = repo_root / ".git" / "refs" / "heads" / base
            if base_ref.exists():
                (repo_root / ".git" / "refs" / "heads" / "feature-classify").write_text(
                    base_ref.read_text(encoding="utf-8"), encoding="utf-8"
                )
                head_file.write_text("ref: refs/heads/feature-classify\n", encoding="utf-8")
                return base
    except OSError:
        pass
    with Repo(repo_root) as repo:
        base = repo.active_branch.name
        repo.git.checkout("-b", "feature-classify")
    return base


def _create_rebase_state(repo_root: Path) -> None:
    Path(repo_root, ".git", "rebase-apply").mkdir(parents=True, exist_ok=True)


def _executor_for(
    base: str, rebase_result: ProcessResult, *, branch_name: str = "feature-classify"
) -> FakeProcessExecutor:
    return FakeProcessExecutor(
        {
            ("git", ("merge-base", "--is-ancestor", "--", base, "HEAD")): ProcessResult(
                returncode=1, stdout="", stderr=""
            ),
            (
                "git",
                (
                    "rebase",
                    "--no-autostash",
                    "--no-autosquash",
                    "--no-update-refs",
                    "--empty=drop",
                    "--",
                    base,
                    branch_name,
                ),
            ): rebase_result,
            (
                "git",
                ("status", "--porcelain", "--untracked-files=no"),
            ): ProcessResult(returncode=0, stdout="UU shared.txt\n", stderr=""),
        }
    )


def test_rebase_regression_failed_rebase_mentioning_up_to_date_is_not_a_noop(
    tmp_git_repo: Path,
) -> None:
    base = _checkout_feature(tmp_git_repo)
    _create_rebase_state(tmp_git_repo)
    executor = _executor_for(
        base,
        ProcessResult(returncode=1, stdout="", stderr=_CONFLICT_MENTIONING_UP_TO_DATE),
    )

    result = rebase_onto(upstream_branch=base, repo_root=tmp_git_repo, executor=executor)

    assert not isinstance(result, RebaseNoOp)
    assert isinstance(result, RebaseConflicts)


def test_genuine_up_to_date_rebase_without_rebase_state_is_still_a_noop(
    tmp_git_repo: Path,
) -> None:
    base = _checkout_feature(tmp_git_repo)
    executor = _executor_for(
        base, ProcessResult(returncode=1, stdout=_GENUINE_UP_TO_DATE, stderr="")
    )

    result = rebase_onto(upstream_branch=base, repo_root=tmp_git_repo, executor=executor)

    assert isinstance(result, RebaseNoOp)


def test_conflicting_rebase_result_is_classified_as_conflicts(
    tmp_git_repo: Path,
) -> None:
    base = _checkout_feature(tmp_git_repo)
    executor = _executor_for(base, ProcessResult(returncode=1, stdout="", stderr=_PLAIN_CONFLICT))

    result = rebase_onto(upstream_branch=base, repo_root=tmp_git_repo, executor=executor)

    assert isinstance(result, RebaseConflicts)


def test_rebase_onto_non_default_target_from_master_is_not_a_phantom_noop(
    tmp_git_repo: Path,
) -> None:
    with Repo(tmp_git_repo) as repo:
        repo.git.branch("develop")
        repo.git.checkout("-B", "master")

    executor = _executor_for(
        "develop",
        ProcessResult(returncode=1, stdout="", stderr=_PLAIN_CONFLICT),
        branch_name="master",
    )

    result = rebase_onto(upstream_branch="develop", repo_root=tmp_git_repo, executor=executor)

    assert not isinstance(result, RebaseNoOp), (
        "a branch named 'master' rebasing onto 'develop' is a real rebase"
    )
    assert isinstance(result, RebaseConflicts)


def test_rebase_onto_the_branch_already_checked_out_is_a_noop(
    tmp_git_repo: Path,
) -> None:
    with Repo(tmp_git_repo) as repo:
        repo.git.checkout("-B", "develop")

    executor = _executor_for(
        "develop",
        ProcessResult(returncode=0, stdout="", stderr=""),
        branch_name="develop",
    )

    result = rebase_onto(upstream_branch="develop", repo_root=tmp_git_repo, executor=executor)

    assert isinstance(result, RebaseNoOp)
