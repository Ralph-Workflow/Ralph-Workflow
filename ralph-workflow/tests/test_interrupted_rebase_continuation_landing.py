from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger as loguru_logger

from ralph.git.subprocess_runner import GitRunOptions, GitRunResult, run_git
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

_FEATURE_AUTHOR = "Feature Author <feature@example.com>"
_FEATURE_MESSAGE = "feat: rewrite the shared file\n\nBody line kept verbatim."


def _git(root: Path, *args: str) -> GitRunResult:
    return run_git(
        args,
        cwd=root,
        label="interrupted-continuation-test",
        options=GitRunOptions(timeout=5),
    )


def _out(root: Path, *args: str) -> str:
    result = _git(root, *args)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit(root: Path, path: str, content: str, *extra: str) -> str:
    (root / path).write_text(content, encoding="utf-8")
    _out(root, "add", path)
    _out(root, "commit", *extra)
    return _out(root, "rev-parse", "HEAD")


@dataclass(frozen=True)
class _Interrupted:
    feature: str
    onto: str
    resolved_tree: str


def _interrupted_continuation(root: Path) -> _Interrupted:
    unique = f"{root.parent.name}\n"
    _commit(root, "notes.txt", unique, "-m", "base notes")
    _commit(root, "shared.txt", "base\n", "-m", "base shared")
    _out(root, "switch", "-c", "feature")
    feature = _commit(
        root,
        "shared.txt",
        "feature\n",
        "--author",
        _FEATURE_AUTHOR,
        "-m",
        _FEATURE_MESSAGE,
    )
    _out(root, "switch", "main")
    _commit(root, "main.txt", "main only\n", "-m", "main file")
    onto = _commit(root, "shared.txt", "main\n", "-m", "main shared")
    _out(root, "switch", "feature")
    assert _git(root, "rebase", "main").returncode != 0
    (root / "shared.txt").write_text("resolved\n", encoding="utf-8")
    _out(root, "add", "shared.txt")
    resolved_tree = _out(root, "write-tree")
    _out(root, "rebase", "--abort")
    _out(root, "read-tree", resolved_tree)
    _out(root, "checkout-index", "-f", "-a")
    (root / "notes.txt").write_text(f"{unique}unrelated local edit\n", encoding="utf-8")
    write_record(
        root,
        IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha=feature,
            pre_target_sha=onto,
            resolving_rebase=True,
            rebase_continue_pending=True,
            rebase_continue_head=feature,
            rebase_continue_tree=resolved_tree,
            owning_worktree=str(root),
        ),
    )
    assert _out(root, "rev-parse", "HEAD") == feature
    assert _git(root, "merge-base", "--is-ancestor", onto, "HEAD").returncode != 0
    return _Interrupted(feature=feature, onto=onto, resolved_tree=resolved_tree)


def _critical_lines(action: Callable[[], None]) -> list[str]:
    captured: list[str] = []
    handler = loguru_logger.add(
        lambda message: captured.append(str(message)),
        level="CRITICAL",
        format="{message}",
    )
    try:
        action()
    finally:
        loguru_logger.remove(handler)
    return captured


def test_interrupted_verified_continuation_is_landed_and_fast_forwarded(
    tmp_git_repo: Path,
) -> None:
    root = tmp_git_repo
    state = _interrupted_continuation(root)

    outcome = recover_incomplete_integration(WorkspaceScope(root))

    assert outcome is not None
    assert outcome.fast_forwarded
    head = _out(root, "rev-parse", "HEAD")
    assert head != state.feature
    assert _out(root, "show", "-s", "--format=%P", head) == state.onto
    assert _out(root, "rev-parse", "HEAD^{tree}") == state.resolved_tree
    assert _out(root, "show", "-s", "--format=%an <%ae>", head) == _FEATURE_AUTHOR
    assert _out(root, "show", "-s", "--format=%B", head) == _FEATURE_MESSAGE
    assert _out(root, "show", "-s", "--format=%at", head) == _out(
        root, "show", "-s", "--format=%at", state.feature
    )
    assert _out(root, "rev-parse", "main") == head
    assert _out(root, "symbolic-ref", "HEAD") == "refs/heads/feature"
    assert _out(root, "write-tree") == state.resolved_tree
    assert _out(root, "diff", "--name-only") == "notes.txt"
    assert (root / "notes.txt").read_text(encoding="utf-8").endswith("unrelated local edit\n")
    assert read_record(root) is None
    backups = _out(root, "for-each-ref", "--format=%(objectname)", "refs/ralph-backup/")
    assert backups
    assert any(
        _git(root, "merge-base", "--is-ancestor", state.feature, backup).returncode == 0
        for backup in backups.splitlines()
    )


def test_mismatched_index_keeps_operator_wait_and_reports_it_once(
    tmp_git_repo: Path,
) -> None:
    root = tmp_git_repo
    state = _interrupted_continuation(root)
    (root / "shared.txt").write_text("something else\n", encoding="utf-8")
    _out(root, "add", "shared.txt")
    staged = _out(root, "write-tree")
    assert staged != state.resolved_tree

    def recover_twice() -> None:
        for _ in range(2):
            outcome = recover_incomplete_integration(WorkspaceScope(root))
            assert outcome is not None
            assert outcome.recovery_record_retained

    criticals = _critical_lines(recover_twice)

    assert _out(root, "rev-parse", "HEAD") == state.feature
    assert _out(root, "write-tree") == staged
    assert _out(root, "rev-parse", "main") == state.onto
    record = read_record(root)
    assert record is not None
    assert record.rebase_continue_pending is True
    assert record.rebase_continue_tree == state.resolved_tree
    landing_waits = [line for line in criticals if "resume point: ralph --resume" in line]
    assert len(landing_waits) == 1
    assert state.feature in landing_waits[0]
