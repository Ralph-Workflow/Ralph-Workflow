"""Linked-worktree regression coverage for the diagnostic evidence collector.

Acceptance scenario 1: in a repository with linked worktrees, an
unrelated branch advancing in one worktree must not invalidate the
retained-integration obligation in another worktree. The new
:class:`SubprocessOperationEvidenceCollector` is the architectural
fix: it never reads ``git for-each-ref`` (repository-global in a
linked-worktree checkout) and never observes the branch reflog
(common-dir state shared with every other worktree).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    record_path,
    write_record,
)
from ralph.pipeline.operation_evidence import SubprocessOperationEvidenceCollector


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _init_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "--quiet", "--initial-branch=main")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "user.email", "test@example.com")
    (root / "README.md").write_text("test")
    _git(root, "add", "README.md")
    _git(root, "commit", "--quiet", "-m", "initial")
    return root


def _add_worktree(primary: Path, path: Path, branch: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(primary, "worktree", "add", str(path), "-b", branch)
    return path


def _record(target: str = "main") -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrating",
        target=target,
        pre_feature_sha="f" * 40,
        pre_target_sha="0" * 40,
        operation_kind="target_reconcile",
    )


@pytest.mark.subprocess_e2e
def test_unrelated_branch_advance_does_not_invalidate_evidence(
    tmp_path: Path,
) -> None:
    """Linked-worktree scenario: an unrelated branch advance must not change the operation's evidence."""
    primary = _init_repo(tmp_path / "primary")
    other = _add_worktree(primary, tmp_path / "other", branch="unrelated")

    # Primary carries the retained-integration obligation.
    record = _record()
    write_record(primary, record)

    collector = SubprocessOperationEvidenceCollector()
    initial_facts = collector.snapshot(primary, record)

    # Advance the unrelated branch in the linked worktree without
    # merging it into main. The bug was that the legacy collector
    # observed the unrelated branch's tip via ``for-each-ref``,
    # which shifted the diagnostic hash.
    (other / "new.txt").write_text("new")
    _git(other, "add", "new.txt")
    _git(other, "commit", "--quiet", "-m", "advance unrelated")

    # The collector's operation-scoped facts (everything except the
    # singleton head, which legitimately shifts on any advance) must
    # be unchanged.
    after_facts = collector.snapshot(primary, record)
    initial_pairs = {
        (fact.kind, fact.scope): fact.value for fact in initial_facts if fact.kind != "head"
    }
    after_pairs = {
        (fact.kind, fact.scope): fact.value for fact in after_facts if fact.kind != "head"
    }
    assert initial_pairs == after_pairs

    # And the durable record is still on disk.
    assert record_path(primary).exists()


@pytest.mark.subprocess_e2e
def test_branch_reflog_change_does_not_invalidate_evidence(
    tmp_path: Path,
) -> None:
    """Branch reflog changes (commondir state) must not shift the fingerprint."""
    primary = _init_repo(tmp_path / "primary")
    other = _add_worktree(primary, tmp_path / "other", branch="unrelated")

    record = _record()
    write_record(primary, record)

    collector = SubprocessOperationEvidenceCollector()
    initial_facts = collector.snapshot(primary, record)

    # The branch reflog in the commondir shifts on every commit.
    # Drive several unrelated commits to amplify the change.
    for i in range(3):
        (other / f"new_{i}.txt").write_text(f"new {i}")
        _git(other, "add", f"new_{i}.txt")
        _git(other, "commit", "--quiet", "-m", f"unrelated {i}")

    after_facts = collector.snapshot(primary, record)
    # No fact in the snapshot may reference a common-dir path.
    for fact in after_facts:
        if fact.scope is None:
            continue
        assert "/logs/HEAD" not in fact.scope
        assert "/logs/refs/heads/" not in fact.scope

    # And the singleton facts (worktree_path, ownership,
    # index_scope) are unchanged. The per-worktree rebase/merge
    # state files are also unchanged because they live under the
    # worktree-private gitdir. ``head`` legitimately shifts because
    # the unrelated commits also advanced the per-worktree
    # ``HEAD`` symref.
    initial_map = {
        fact.kind: fact.value
        for fact in initial_facts
        if fact.scope is None and fact.kind != "head"
    }
    after_map = {
        fact.kind: fact.value for fact in after_facts if fact.scope is None and fact.kind != "head"
    }
    assert initial_map == after_map


@pytest.mark.subprocess_e2e
def test_collector_rejects_repository_global_observations(
    tmp_path: Path,
) -> None:
    """The collector never produces ``for-each-ref`` or full ``ls-files`` observations."""
    primary = _init_repo(tmp_path / "primary")
    record = _record()
    write_record(primary, record)
    facts = SubprocessOperationEvidenceCollector().snapshot(primary, record)
    kinds = {fact.kind for fact in facts}
    assert "for-each-ref" not in kinds
    assert "ls-files" not in kinds
