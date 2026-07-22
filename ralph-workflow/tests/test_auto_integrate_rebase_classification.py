"""Regression tests for :func:`ralph.git.rebase.rebase.rebase_onto` classification.

``_rebase_result_from_process`` used to short-circuit ANY non-zero ``git
rebase`` whose combined stdout/stderr merely CONTAINED the substring
``up to date`` into :class:`RebaseNoOp`. A ``RebaseNoOp`` is a
success-shaped outcome, so
:func:`ralph.pipeline.auto_integrate._run_rebase_or_merge` skipped the
endpoint-merge fallback entirely and never aborted the half-applied
rebase; the leftover ``rebase-apply`` / ``rebase-merge`` directory then
failed ``check_rebase_preconditions`` for every subsequent integration,
turning one conflict into a run-long outage.

File-level markers. ``subprocess_e2e`` excludes this file from ``make
test`` (the budget-tracked 60 s step) because :func:`rebase_onto`
resolves the repository through ``_resolve_repo_root`` / ``_git_dir``,
both of which open a real GitPython ``Repo``, so the ``tmp_git_repo``
fixture is required even though the subprocess itself is faked.
``timeout_seconds(5)`` sizes the budget for that real repository I/O,
matching the convention in tests/test_git_rebase.py. This does not
weaken any cap: the file stays out of the 60 s combined budget and
inside the 60 s per-suite cap on ``make test-subprocess-e2e``.
"""

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


#: A real conflicted rebase whose hint text happens to carry the phrase
#: "up to date" -- git prints exactly this shape when the branch being
#: rebased tracks a remote that is itself current.
_CONFLICT_MENTIONING_UP_TO_DATE = (
    "CONFLICT (content): Merge conflict in shared.txt\n"
    "error: could not apply 0badc0d... local change\n"
    "hint: Your branch is up to date with 'origin/main'.\n"
)

#: The genuine no-op shape: no conflict, nothing applied.
_GENUINE_UP_TO_DATE = "Current branch feature-classify is up to date.\n"

#: A conflict with no "up to date" text anywhere.
_PLAIN_CONFLICT = (
    "CONFLICT (content): Merge conflict in shared.txt\n"
    "error: could not apply 0badc0d... local change\n"
)


class FakeProcessExecutor(ProcessExecutor):
    """Executor returning canned :class:`ProcessResult` values per argv."""

    def __init__(
        self, responses: Mapping[tuple[str, tuple[str, ...]], ProcessResult]
    ) -> None:
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
        return self.responses.get(
            key, ProcessResult(returncode=0, stdout="", stderr="")
        )


def _checkout_feature(repo_root: Path) -> str:
    """Check out a feature branch and return the base branch name."""
    with Repo(repo_root) as repo:
        base = repo.active_branch.name
        repo.git.checkout("-b", "feature-classify")
    return base


def _create_rebase_state(repo_root: Path) -> None:
    """Leave the on-disk marker of an unfinished rebase."""
    Path(repo_root, ".git", "rebase-apply").mkdir(parents=True, exist_ok=True)


def _executor_for(
    base: str, rebase_result: ProcessResult
) -> FakeProcessExecutor:
    """Fake executor driving ``rebase_onto`` to ``rebase_result``."""
    return FakeProcessExecutor(
        {
            # Non-zero => the pre-flight "already up-to-date" check in
            # _validate_rebase_request does NOT short-circuit, so the
            # rebase really runs and its result is classified.
            ("git", ("merge-base", "--is-ancestor", base, "HEAD")): ProcessResult(
                returncode=1, stdout="", stderr=""
            ),
            ("git", ("rebase", "--", base)): rebase_result,
            (
                "git",
                ("status", "--porcelain", "--untracked-files=no"),
            ): ProcessResult(returncode=0, stdout="UU shared.txt\n", stderr=""),
        }
    )


def test_rebase_regression_failed_rebase_mentioning_up_to_date_is_not_a_noop(
    tmp_git_repo: Path,
) -> None:
    """A conflicted rebase is never a no-op just because it says 'up to date'."""
    base = _checkout_feature(tmp_git_repo)
    _create_rebase_state(tmp_git_repo)
    executor = _executor_for(
        base,
        ProcessResult(
            returncode=1, stdout="", stderr=_CONFLICT_MENTIONING_UP_TO_DATE
        ),
    )

    result = rebase_onto(
        upstream_branch=base, repo_root=tmp_git_repo, executor=executor
    )

    assert not isinstance(result, RebaseNoOp)
    assert isinstance(result, RebaseConflicts)


def test_genuine_up_to_date_rebase_without_rebase_state_is_still_a_noop(
    tmp_git_repo: Path,
) -> None:
    """The legitimate no-op path is unchanged: no rebase state, no conflict."""
    base = _checkout_feature(tmp_git_repo)
    executor = _executor_for(
        base, ProcessResult(returncode=1, stdout=_GENUINE_UP_TO_DATE, stderr="")
    )

    result = rebase_onto(
        upstream_branch=base, repo_root=tmp_git_repo, executor=executor
    )

    assert isinstance(result, RebaseNoOp)


def test_conflicting_rebase_result_is_classified_as_conflicts(
    tmp_git_repo: Path,
) -> None:
    """A plain content conflict still classifies as :class:`RebaseConflicts`."""
    base = _checkout_feature(tmp_git_repo)
    executor = _executor_for(
        base, ProcessResult(returncode=1, stdout="", stderr=_PLAIN_CONFLICT)
    )

    result = rebase_onto(
        upstream_branch=base, repo_root=tmp_git_repo, executor=executor
    )

    assert isinstance(result, RebaseConflicts)
