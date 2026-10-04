"""Black-box tests for the project-policy remediation auto-commit.

Mirrors the wt-025 skill auto-commit contract for policy readiness: after
the preflight (or the remediation loop) leaves the project READY, the
changed policy surfaces are committed deterministically so the next run's
development agent never sees the drift in its working tree.

Pins:

* deterministic subject ``chore(policy): sync project-policy readiness``;
* selective staging — only the policy scopes (``docs/ralph-workflow-policy/``,
  ``AGENTS.md``, ``CLAUDE.md``) are staged, unrelated dirty files are not;
* no-commit when the policy surfaces are clean;
* no-commit on a non-git workspace.
* wt-012: the ``authored_paths`` scope expansion is REMOVED. Remediation
  agent writes (gate scripts) stay in the agent commit flow. The
  deterministic policy chore commit is restricted to the policy
  surfaces themselves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from git import Actor, Repo

from ralph.git.commit_result import CommitCreationResult
from ralph.git.scoped_auto_commit import (
    ScopedCommitStatus,
    capture_pre_write_contents,
    list_dirty_paths,
)
from ralph.project_policy._auto_commit import (
    POLICY_AUTO_COMMIT_SUBJECT,
    commit_policy_updates,
    commit_policy_writes,
)
from ralph.project_policy.agents_md import bootstrap, condense_placeholder_block
from ralph.project_policy.markers import AGENTS_MD
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.subprocess_e2e


@pytest.fixture
def fake_create_commit() -> MagicMock:
    return MagicMock(return_value=CommitCreationResult.created("f" * 40))


@pytest.mark.timeout_seconds(5)
def test_policy_auto_commit_subject_and_scoped_staging(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
    policy_dir = tmp_git_repo / "docs" / "ralph-workflow-policy"
    policy_dir.mkdir(parents=True)
    (policy_dir / "testing-policy.md").write_text("policy", encoding="utf-8")
    (tmp_git_repo / "AGENTS.md").write_text("agents", encoding="utf-8")
    (tmp_git_repo / "unrelated.py").write_text("print()", encoding="utf-8")
    staged: list[list[str]] = []

    def spy_stage(_root: Path | str, files: list[str]) -> None:
        staged.append(list(files))

    sha = commit_policy_updates(tmp_git_repo, fake_create_commit, stage_fn=spy_stage)

    assert sha == "f" * 40
    message = fake_create_commit.call_args[0][1]
    assert message.splitlines()[0] == POLICY_AUTO_COMMIT_SUBJECT
    assert "docs/ralph-workflow-policy/testing-policy.md" in message
    assert staged, "stage_fn must be invoked"
    flat = [path for batch in staged for path in batch]
    assert "AGENTS.md" in flat
    assert "docs/ralph-workflow-policy/testing-policy.md" in flat
    assert "unrelated.py" not in flat


@pytest.mark.timeout_seconds(5)
def test_migrated_candidate_files_are_committed(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
    """A migration candidate with the marker is committed; unrelated edits are not."""
    (tmp_git_repo / "AGENTS.md").write_text("agents", encoding="utf-8")
    (tmp_git_repo / "CONTRIBUTING.md").write_text(
        "# Contributing\n\n"
        "<!-- ralph-workflow-policy:migrated -> docs/ralph-workflow-policy/testing-policy.md -->\n",
        encoding="utf-8",
    )
    (tmp_git_repo / "TESTING.md").write_text(
        "# Testing\n\nuser notes, no migration marker\n", encoding="utf-8"
    )
    staged: list[list[str]] = []

    def spy_stage(_root: Path | str, files: list[str]) -> None:
        staged.append(list(files))

    sha = commit_policy_updates(tmp_git_repo, fake_create_commit, stage_fn=spy_stage)

    assert sha == "f" * 40
    flat = [path for batch in staged for path in batch]
    assert "CONTRIBUTING.md" in flat
    assert "TESTING.md" not in flat


@pytest.mark.timeout_seconds(5)
def test_policy_auto_commit_skips_clean_tree(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
    (tmp_git_repo / "unrelated.py").write_text("print()", encoding="utf-8")

    assert commit_policy_updates(tmp_git_repo, fake_create_commit) is None
    fake_create_commit.assert_not_called()


@pytest.mark.timeout_seconds(5)
def test_policy_auto_commit_skips_non_git_workspace(
    tmp_path: Path, fake_create_commit: MagicMock
) -> None:
    (tmp_path / "AGENTS.md").write_text("agents", encoding="utf-8")

    assert commit_policy_updates(tmp_path, fake_create_commit) is None
    fake_create_commit.assert_not_called()


@pytest.mark.timeout_seconds(5)
def test_gate_scripts_written_by_the_policy_agent_are_not_committed(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
    """wt-012: remediation-authored gate scripts stay in the agent commit flow.

    The deterministic policy chore commit must never ride an agent's
    authoring. A gate script the REMEDIATION agent wrote is left
    dirty so the agent / user commit flow owns it; the policy
    commit ONLY commits the policy surfaces themselves.
    """
    pre_run_dirty = list_dirty_paths(tmp_git_repo)
    policy_dir = tmp_git_repo / "docs" / "ralph-workflow-policy"
    policy_dir.mkdir(parents=True)
    (policy_dir / "verification-policy.md").write_text("policy", encoding="utf-8")
    (tmp_git_repo / "scripts").mkdir()
    (tmp_git_repo / "scripts" / "verify.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n", encoding="utf-8"
    )
    (tmp_git_repo / "Makefile").write_text("verify:\n\t./scripts/verify.sh\n", encoding="utf-8")
    staged: list[str] = []

    commit_policy_updates(
        tmp_git_repo,
        fake_create_commit,
        stage_fn=lambda _root, paths: staged.extend(paths),
        pre_run_dirty=pre_run_dirty,
    )

    assert "docs/ralph-workflow-policy/verification-policy.md" in staged
    # Gate scripts outside the policy scope are NOT swept in -- they
    # stay in the agent commit flow per the wt-012 contract.
    assert "scripts/verify.sh" not in staged
    assert "Makefile" not in staged


@pytest.mark.timeout_seconds(5)
def test_the_users_own_uncommitted_work_is_never_swept_in(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
    """A pre-run user edit is excluded; no other paths are swept in either."""
    (tmp_git_repo / "my_feature.py").write_text("work in progress", encoding="utf-8")
    pre_run_dirty = list_dirty_paths(tmp_git_repo)
    assert "my_feature.py" in pre_run_dirty
    (tmp_git_repo / "scripts").mkdir()
    (tmp_git_repo / "scripts" / "verify.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    staged: list[str] = []

    commit_policy_updates(
        tmp_git_repo,
        fake_create_commit,
        stage_fn=lambda _root, paths: staged.extend(paths),
        pre_run_dirty=pre_run_dirty,
    )

    assert "my_feature.py" not in staged
    # Gate scripts outside the policy scope are NOT swept in.
    assert "scripts/verify.sh" not in staged


@pytest.mark.timeout_seconds(5)
def test_engine_scratch_is_never_committed(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
    """Engine scratch is never committed, regardless of how the path is sourced."""
    pre_run_dirty = list_dirty_paths(tmp_git_repo)
    (tmp_git_repo / ".agent" / "tmp").mkdir(parents=True)
    (tmp_git_repo / ".agent" / "tmp" / "policy_remediation_prompt.md").write_text(
        "prompt", encoding="utf-8"
    )
    (tmp_git_repo / "scripts").mkdir()
    (tmp_git_repo / "scripts" / "verify.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    staged: list[str] = []

    commit_policy_updates(
        tmp_git_repo,
        fake_create_commit,
        stage_fn=lambda _root, paths: staged.extend(paths),
        pre_run_dirty=pre_run_dirty,
    )

    assert not any(path.startswith(".agent/") for path in staged), staged
    assert "scripts/verify.sh" not in staged


@pytest.mark.timeout_seconds(5)
def test_without_a_snapshot_nothing_outside_the_policy_scope_is_committed(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
    """Without attribution, the conservative policy scope remains in force."""
    policy_dir = tmp_git_repo / "docs" / "ralph-workflow-policy"
    policy_dir.mkdir(parents=True)
    (policy_dir / "verification-policy.md").write_text("policy", encoding="utf-8")
    (tmp_git_repo / "scripts").mkdir()
    (tmp_git_repo / "scripts" / "verify.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    staged: list[str] = []

    commit_policy_updates(
        tmp_git_repo,
        fake_create_commit,
        stage_fn=lambda _root, paths: staged.extend(paths),
    )

    assert "docs/ralph-workflow-policy/verification-policy.md" in staged
    assert "scripts/verify.sh" not in staged


@pytest.mark.timeout_seconds(5)
def test_user_wip_on_an_in_scope_file_is_never_swept_in(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
    """A pre-run edit in a Ralph-owned path is still the user's work."""
    (tmp_git_repo / "AGENTS.md").write_text("my work in progress", encoding="utf-8")
    policy_dir = tmp_git_repo / "docs" / "ralph-workflow-policy"
    policy_dir.mkdir(parents=True)
    (policy_dir / "testing-policy.md").write_text("my draft policy", encoding="utf-8")
    pre_run_dirty = list_dirty_paths(tmp_git_repo)
    (policy_dir / "linting-policy.md").write_text("agent wrote this", encoding="utf-8")
    staged: list[str] = []

    commit_policy_updates(
        tmp_git_repo,
        fake_create_commit,
        stage_fn=lambda _root, paths: staged.extend(paths),
        pre_run_dirty=pre_run_dirty,
    )

    assert "docs/ralph-workflow-policy/linting-policy.md" in staged
    assert "AGENTS.md" not in staged
    assert "docs/ralph-workflow-policy/testing-policy.md" not in staged


@pytest.mark.timeout_seconds(5)
def test_gate_probe_detritus_is_never_committed(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
    """Probe output is not remediation-authored content and is never staged.

    wt-012: gate scripts (whether remediation-authored or probe-detritus
    adjacent) are NOT swept into the deterministic policy commit. The
    policy commit only commits the policy surfaces themselves.
    """
    pre_run_dirty = list_dirty_paths(tmp_git_repo)
    (tmp_git_repo / "scripts").mkdir()
    (tmp_git_repo / "scripts" / "verify.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_git_repo / ".coverage").write_text("probe detritus", encoding="utf-8")
    (tmp_git_repo / "coverage.xml").write_text("probe detritus", encoding="utf-8")
    staged: list[str] = []

    commit_policy_updates(
        tmp_git_repo,
        fake_create_commit,
        stage_fn=lambda _root, paths: staged.extend(paths),
        pre_run_dirty=pre_run_dirty,
    )

    assert "scripts/verify.sh" not in staged
    assert ".coverage" not in staged
    assert "coverage.xml" not in staged


# --- wt-012 acceptance: producer-level commit_policy_writes isolation ---------


@pytest.mark.timeout_seconds(5)
def test_commit_policy_writes_skips_path_already_dirty_at_head(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
    """wt-012 (g): agent-edited AGENTS.md + real condense -> not in the policy chore commit.

    The producer-level helper records the pre-write content hash
    BEFORE ``condense_placeholder_block`` rewrites AGENTS.md. If the
    file was already dirty at HEAD (an agent edited it during the
    run), the path is SKIPPED with a warning and stays in the agent
    flow; it can never enter the fixed-message policy commit.
    """
    ws = FsWorkspace(tmp_git_repo)
    bootstrap(ws)

    repo = Repo(tmp_git_repo)
    try:
        actor = Actor("Test Author", "test@example.com")
        repo.index.add([AGENTS_MD])
        repo.index.commit("initial bootstrap", author=actor, committer=actor)
    finally:
        repo.close()

    # Agent edit: changes AGENTS.md outside managed block BEFORE condense runs.
    current_content = (tmp_git_repo / AGENTS_MD).read_text(encoding="utf-8")
    agent_addition = "\n## Agent Section\n\nAgent added this section mid-run.\n"
    (tmp_git_repo / AGENTS_MD).write_text(current_content + agent_addition, encoding="utf-8")

    # Real condense operation
    pre_contents = capture_pre_write_contents(tmp_git_repo, [AGENTS_MD])
    condensed = condense_placeholder_block(ws)
    assert condensed == [AGENTS_MD]

    result = commit_policy_writes(
        tmp_git_repo,
        written_paths=condensed,
        pre_contents=pre_contents,
        create_commit_fn=fake_create_commit,
    )

    # The path was already dirty at HEAD (the agent edit) so the
    # producer-level commit must SKIP it, NOT commit.
    assert result.status is ScopedCommitStatus.SKIPPED, (
        f"Path already dirty at HEAD must be SKIPPED, not CREATED/FAILED; got: {result.status}"
    )
    assert AGENTS_MD in result.skipped_paths
    fake_create_commit.assert_not_called()
    after_content = (tmp_git_repo / AGENTS_MD).read_text(encoding="utf-8")
    assert "Agent added this section mid-run." in after_content


@pytest.mark.timeout_seconds(5)
def test_commit_policy_writes_real_condense_clean_at_head_creates_commit(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
    """wt-012: real condense on clean AGENTS.md at HEAD creates chore(policy) commit."""
    ws = FsWorkspace(tmp_git_repo)
    bootstrap(ws)

    repo = Repo(tmp_git_repo)
    try:
        actor = Actor("Test Author", "test@example.com")
        repo.index.add([AGENTS_MD])
        repo.index.commit("initial bootstrap", author=actor, committer=actor)
    finally:
        repo.close()

    pre_contents = capture_pre_write_contents(tmp_git_repo, [AGENTS_MD])
    condensed = condense_placeholder_block(ws)
    assert condensed == [AGENTS_MD]

    result = commit_policy_writes(
        tmp_git_repo,
        written_paths=condensed,
        pre_contents=pre_contents,
        create_commit_fn=fake_create_commit,
    )

    assert result.status is ScopedCommitStatus.CREATED
    fake_create_commit.assert_called_once()


@pytest.mark.timeout_seconds(5)
def test_commit_policy_writes_commits_clean_path(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
    """wt-012: a pre-write-clean path is committed at the producer boundary."""
    from git import Actor, Repo

    repo = Repo(tmp_git_repo)
    try:
        actor = Actor("Test Author", "test@example.com")
        initial = tmp_git_repo / AGENTS_MD
        initial.write_text("# AGENTS.md -- initial\n", encoding="utf-8")
        repo.index.add([AGENTS_MD])
        repo.index.commit("initial", author=actor, committer=actor)
    finally:
        repo.close()

    # Simulate the preflight's own write: pre-write hash matches HEAD,
    # post-write differs. The producer-level helper commits the diff.
    pre_contents = capture_pre_write_contents(tmp_git_repo, [AGENTS_MD])
    (tmp_git_repo / AGENTS_MD).write_text(
        "# AGENTS.md -- preflight write\n", encoding="utf-8"
    )

    result = commit_policy_writes(
        tmp_git_repo,
        written_paths=[AGENTS_MD],
        pre_contents=pre_contents,
        create_commit_fn=fake_create_commit,
    )

    assert result.status is ScopedCommitStatus.CREATED
    assert result.sha == "f" * 40
    fake_create_commit.assert_called_once()
    message = fake_create_commit.call_args[0][1]
    assert message.splitlines()[0] == POLICY_AUTO_COMMIT_SUBJECT


@pytest.mark.timeout_seconds(5)
def test_commit_policy_writes_noop_when_no_writes(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
    """Empty written_paths -> NOOP, no commit, no error."""
    result = commit_policy_writes(
        tmp_git_repo,
        written_paths=[],
        pre_contents={},
        create_commit_fn=fake_create_commit,
    )
    assert result.status is ScopedCommitStatus.NOOP
    fake_create_commit.assert_not_called()


@pytest.mark.timeout_seconds(5)
def test_commit_policy_writes_creates_new_starter_file(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
    """wt-012: brand-new starter file committed with dedicated chore(policy) commit."""
    policy_starter = "docs/ralph-workflow-policy/testing-policy.md"
    pre_contents = capture_pre_write_contents(tmp_git_repo, [policy_starter])
    assert pre_contents[policy_starter] is None

    abs_starter = tmp_git_repo / policy_starter
    abs_starter.parent.mkdir(parents=True, exist_ok=True)
    abs_starter.write_text("# Testing Policy\n", encoding="utf-8")

    result = commit_policy_writes(
        tmp_git_repo,
        written_paths=[policy_starter],
        pre_contents=pre_contents,
        create_commit_fn=fake_create_commit,
    )

    assert result.status is ScopedCommitStatus.CREATED
    assert result.sha == "f" * 40
    fake_create_commit.assert_called_once()
    message = fake_create_commit.call_args[0][1]
    assert message.splitlines()[0] == POLICY_AUTO_COMMIT_SUBJECT


@pytest.mark.timeout_seconds(10)
def test_commit_policy_writes_rollback_preserves_staged_deletion(
    tmp_git_repo: Path,
) -> None:
    """wt-012: commit failure rolls back index, preserving exact pre-staged deletions, new and modified files."""
    from git import Actor, Repo

    deleted_file = tmp_git_repo / "deleted.txt"
    deleted_file.write_text("to delete\n", encoding="utf-8")
    mod_file = tmp_git_repo / "mod.txt"
    mod_file.write_text("initial mod\n", encoding="utf-8")
    repo = Repo(tmp_git_repo)
    try:
        actor = Actor("Test Author", "test@example.com")
        repo.index.add(["deleted.txt", "mod.txt"])
        repo.index.commit("add files", author=actor, committer=actor)
        repo.index.remove(["deleted.txt"], working_tree=True)
        mod_file.write_text("modified content\n", encoding="utf-8")
        repo.index.add(["mod.txt"])
        new_file = tmp_git_repo / "staged_new.txt"
        new_file.write_text("brand new\n", encoding="utf-8")
        repo.index.add(["staged_new.txt"])
        exact_index_before = repo.git.ls_files("--stage")
        exact_diff_before = repo.git.diff("--cached")
    finally:
        repo.close()

    policy_starter = "docs/ralph-workflow-policy/testing-policy.md"
    pre_contents = capture_pre_write_contents(tmp_git_repo, [policy_starter])
    abs_starter = tmp_git_repo / policy_starter
    abs_starter.parent.mkdir(parents=True, exist_ok=True)
    abs_starter.write_text("# Testing Policy\n", encoding="utf-8")

    failing_create_commit = MagicMock(return_value=CommitCreationResult.failed("forced commit failure"))

    result = commit_policy_writes(
        tmp_git_repo,
        written_paths=[policy_starter],
        pre_contents=pre_contents,
        create_commit_fn=failing_create_commit,
    )
    assert result.status is ScopedCommitStatus.FAILED

    repo = Repo(tmp_git_repo)
    try:
        exact_index_after = repo.git.ls_files("--stage")
        exact_diff_after = repo.git.diff("--cached")
        assert exact_index_after == exact_index_before, "Index must be byte-for-byte identical after rollback"
        assert exact_diff_after == exact_diff_before, "Cached diff must be byte-for-byte identical after rollback"
    finally:
        repo.close()


@pytest.mark.timeout_seconds(5)
def test_commit_policy_writes_noop_preserves_exact_index(
    tmp_git_repo: Path,
) -> None:
    """wt-012: policy no-op with pre-staged files preserves index byte-for-byte without crash."""
    from git import Actor, Repo

    policy_starter = "docs/ralph-workflow-policy/testing-policy.md"
    abs_starter = tmp_git_repo / policy_starter
    abs_starter.parent.mkdir(parents=True, exist_ok=True)
    abs_starter.write_text("# Testing Policy\n", encoding="utf-8")

    repo = Repo(tmp_git_repo)
    try:
        actor = Actor("Test Author", "test@example.com")
        repo.index.add([policy_starter])
        repo.index.commit("commit starter", author=actor, committer=actor)

        # Unrelated staged file
        unrelated = tmp_git_repo / "unrelated.txt"
        unrelated.write_text("staged\n", encoding="utf-8")
        repo.index.add(["unrelated.txt"])
        exact_index_before = repo.git.ls_files("--stage")
        exact_diff_before = repo.git.diff("--cached")
    finally:
        repo.close()

    pre_contents = capture_pre_write_contents(tmp_git_repo, [policy_starter])
    abs_starter.write_text("# Testing Policy\n", encoding="utf-8")  # no change

    result = commit_policy_writes(
        tmp_git_repo,
        written_paths=[policy_starter],
        pre_contents=pre_contents,
        create_commit_fn=lambda *a, **k: None,
    )
    assert result.status is ScopedCommitStatus.NOOP

    repo = Repo(tmp_git_repo)
    try:
        exact_index_after = repo.git.ls_files("--stage")
        exact_diff_after = repo.git.diff("--cached")
        assert exact_index_after == exact_index_before, "No-op must preserve index exactly"
        assert exact_diff_after == exact_diff_before, "No-op must preserve cached diff exactly"
    finally:
        repo.close()
