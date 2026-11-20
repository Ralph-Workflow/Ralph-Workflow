from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.git.rebase.rebase_continuation import rebase_in_progress_at
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.pipeline._landed_predicate import operation_landed
from ralph.pipeline._pending_rebase_continue import (
    prepare_pending_rebase,
    resume_pending_rebase,
)
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    write_record,
)

if TYPE_CHECKING:
    from ralph.git.git_run_result import GitRunResult

pytestmark = [pytest.mark.timeout_seconds(15), pytest.mark.subprocess_e2e]
_OPTIONS = GitRunOptions(timeout=10)


def _git(root: Path, *args: str) -> GitRunResult:
    return run_git(args, cwd=root, label="pending-rebase-repin-test", options=_OPTIONS)


def _value(root: Path, *args: str) -> str:
    result = _git(root, *args)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _blob(root: Path, ref: str, path: str) -> str:
    result = _git(root, "cat-file", "blob", f"{ref}:{path}")
    assert result.returncode == 0, result.stderr
    return result.stdout


def _commit(root: Path, path: str, content: str, message: str) -> str:
    (root / path).write_text(content, encoding="utf-8")
    assert _git(root, "add", path).returncode == 0
    assert _git(root, "commit", "-m", message).returncode == 0
    return _value(root, "rev-parse", "HEAD")


def _init_repo(root: Path) -> None:
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    assert _git(root, "config", "commit.gpgsign", "false").returncode == 0


def _seed_conflicting_rebase(root: Path) -> tuple[str, str]:
    _init_repo(root)
    _commit(root, "base.txt", "base\n", "base commit")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    _commit(root, "shared.txt", "feature one\n", "feature one")
    feature = _commit(root, "other.txt", "feature two\n", "feature two")
    assert _git(root, "switch", "main").returncode == 0
    (root / "shared.txt").write_text("target\n", encoding="utf-8")
    (root / "other.txt").write_text("target two\n", encoding="utf-8")
    assert _git(root, "add", "shared.txt", "other.txt").returncode == 0
    assert _git(root, "commit", "-m", "target commit").returncode == 0
    onto = _value(root, "rev-parse", "HEAD")
    assert _git(root, "switch", "feature").returncode == 0
    assert _git(root, "rebase", "main").returncode != 0
    return feature, onto


def _resolve_stop(root: Path, path: str, content: str) -> None:
    (root / path).write_text(content, encoding="utf-8")
    assert _git(root, "add", path).returncode == 0


def test_resume_pending_rebase_repins_absent_stop_snapshot_and_advances_the_rebase(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    feature, onto = _seed_conflicting_rebase(root)
    first_stop = _value(root, "rev-parse", "--verify", "REBASE_HEAD")
    _resolve_stop(root, "shared.txt", "feature one\n")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feature,
        pre_target_sha=onto,
        rebase_continue_pending=True,
        rebase_continue_head=None,
        rebase_continue_stop=None,
        rebase_continue_tree=None,
    )
    write_record(root, record)

    outcome = resume_pending_rebase(root, record)

    assert outcome == "rebase advanced to another conflict; retained for agent continuation"
    assert rebase_in_progress_at(root)
    advanced_stop = _value(root, "rev-parse", "--verify", "REBASE_HEAD")
    assert advanced_stop == feature
    assert advanced_stop != first_stop
    assert _value(root, "rev-parse", "HEAD") != onto
    retained = read_record(root)
    assert retained is not None
    assert retained.resolving_rebase is True
    assert retained.rebase_continue_pending is False
    assert retained.rebase_continue_head is None
    assert retained.rebase_continue_stop is None
    assert retained.rebase_continue_tree is None

    _resolve_stop(root, "other.txt", "feature two\n")
    observed_head = _value(root, "rev-parse", "HEAD")
    observed_stop = _value(root, "rev-parse", "--verify", "REBASE_HEAD")
    observed_tree = _value(root, "write-tree")
    prepare_pending_rebase(root)
    pinned = read_record(root)
    assert pinned is not None
    assert pinned.rebase_continue_head == observed_head
    assert pinned.rebase_continue_stop == observed_stop
    assert pinned.rebase_continue_tree == observed_tree

    final = resume_pending_rebase(root, pinned)

    assert isinstance(final, IntegrationRecord)
    assert not rebase_in_progress_at(root)
    head = _value(root, "rev-parse", "HEAD")
    assert _value(root, "merge-base", "--is-ancestor", onto, head) == ""
    assert _blob(root, head, "shared.txt") == "feature one\n"
    assert _blob(root, head, "other.txt") == "feature two\n"
    assert final.phase == "integrated"
    assert final.integrated_feature_sha == head
    assert final.rebase_continue_head is None
    assert final.rebase_continue_stop is None
    assert final.rebase_continue_tree is None
    assert operation_landed(root, final).landed is True


def test_resume_pending_rebase_refuses_stale_anchor_and_retains_the_record(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    feature, onto = _seed_conflicting_rebase(root)
    observed_head = _value(root, "rev-parse", "HEAD")
    observed_stop = _value(root, "rev-parse", "--verify", "REBASE_HEAD")
    _resolve_stop(root, "shared.txt", "feature one\n")

    stale_head = feature
    assert stale_head != observed_head
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feature,
        pre_target_sha=onto,
        rebase_continue_pending=True,
        rebase_continue_head=stale_head,
        rebase_continue_stop=observed_stop,
        rebase_continue_tree=None,
    )
    write_record(root, record)

    outcome = resume_pending_rebase(root, record)

    assert outcome == "pending rebase snapshot incomplete; continuation retained"
    retained = read_record(root)
    assert retained is not None
    assert retained.rebase_continue_pending is True
    assert retained.rebase_continue_head == stale_head
    assert retained.rebase_continue_stop == observed_stop
    assert retained.rebase_continue_tree is None
    decision = operation_landed(root, retained)
    assert decision.landed is False
    assert decision.proof == "none"
    assert decision.observed_head == observed_head
    assert decision.observed_head != stale_head
    assert f"rebase_continue_head={stale_head}" in decision.expected


def test_a_rebase_aborted_under_the_resolver_is_never_recorded_as_a_pending_continuation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    feature, onto = _seed_conflicting_rebase(root)
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feature,
        pre_target_sha=onto,
        resolving_rebase=True,
    )
    write_record(root, record)
    assert _git(root, "rebase", "--abort").returncode == 0
    _resolve_stop(root, "shared.txt", "target\n")

    with pytest.raises(ValueError, match="no rebase is in progress"):
        prepare_pending_rebase(root, "main")

    retained = read_record(root)
    assert retained == record
    assert _value(root, "rev-parse", "HEAD") == feature
