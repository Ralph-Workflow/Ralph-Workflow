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
from ralph.project_policy.markers import AGENTS_MD

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
    """wt-012 (g): agent-edited AGENTS.md + condense -> not in the policy chore commit.

    The producer-level helper records the pre-write content hash
    BEFORE ``condense_placeholder_block`` rewrites AGENTS.md. If the
    file was already dirty at HEAD (an agent edited it during the
    run), the path is SKIPPED with a warning and stays in the agent
    flow; it can never enter the fixed-message policy commit.
    """
    # HEAD has no AGENTS.md yet -- the deterministic writer treats
    # that as pre-write hash ``None`` and the commit would proceed.
    # Force the "already dirty at HEAD" case by committing an
    # initial AGENTS.md and then editing it (which the agent did
    # mid-run).
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

    # Agent edit: changes AGENTS.md BEFORE condense runs.
    (tmp_git_repo / AGENTS_MD).write_text("# AGENTS.md -- agent edit\n", encoding="utf-8")

    # Simulate condense rewriting AGENTS.md (the post-READY condense
    # pass). The post-write content is the condense form.
    condense_form = "<!-- ralph-managed:begin -->\n<!-- ralph-managed:end -->\n"
    pre_contents = capture_pre_write_contents(tmp_git_repo, [AGENTS_MD])
    (tmp_git_repo / AGENTS_MD).write_text(condense_form, encoding="utf-8")

    result = commit_policy_writes(
        tmp_git_repo,
        written_paths=[AGENTS_MD],
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
    (tmp_git_repo / AGENTS_MD).write_text("# AGENTS.md -- preflight write\n", encoding="utf-8")

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


# --- wt-012: truthful real-git regression (replaces the fabricated-SHA case) ---


@pytest.mark.timeout_seconds(5)
def test_commit_policy_writes_real_git_end_to_end(tmp_git_repo: Path) -> None:
    """wt-012 truthful test: real ``create_commit`` against a real ``Repo``.

    Replaces the previous fabricated-SHA clean-path assertion. Drives
    the same producer-level contract but with the production
    ``create_commit`` / ``stage_files`` wiring so a real HEAD advance
    is observable. Asserts:

    * the post-commit tree carries the deterministic-writer content,
    * the post-commit ``git log`` shows a commit with the
      ``POLICY_AUTO_COMMIT_SUBJECT`` subject at HEAD,
    * unrelated agent / user edits staged before the commit stay out
      of the chore commit (the isolation contract),
    * a second identical write produces no commit (NOOP / SKIPPED).
    """
    from git import Repo

    from ralph.git.operations import create_commit, stage_files

    # Seed HEAD with the deterministic-writer content at its pre-write
    # state (the writer is about to overwrite this file).
    initial = tmp_git_repo / AGENTS_MD
    initial.write_text("# AGENTS.md -- pre-write\n", encoding="utf-8")
    repo = Repo(tmp_git_repo)
    try:
        from git import Actor

        actor = Actor("Test Author", "test@example.com")
        repo.index.add([AGENTS_MD])
        repo.index.commit("seed", author=actor, committer=actor)
    finally:
        repo.close()

    # Stage an unrelated agent edit that the deterministic writer must
    # NOT sweep in.
    wip = tmp_git_repo / "agent_wip.py"
    wip.write_text("# agent WIP\n", encoding="utf-8")
    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["agent_wip.py"])
    finally:
        repo.close()

    # Pre-write snapshot for the deterministic writer -- the writer
    # captures what was on disk at HEAD for AGENTS.md BEFORE the
    # overwrite. ``git_wip.py`` is intentionally NOT in the pre-write
    # map so the chore commit cannot capture it.
    pre_contents = capture_pre_write_contents(tmp_git_repo, [AGENTS_MD])

    # The deterministic writer overwrites AGENTS.md (its real-world
    # work).
    (tmp_git_repo / AGENTS_MD).write_text(
        "# AGENTS.md -- deterministic writer output\n", encoding="utf-8"
    )

    result = commit_policy_writes(
        tmp_git_repo,
        written_paths=[AGENTS_MD],
        pre_contents=pre_contents,
        create_commit_fn=create_commit,
        stage_fn=stage_files,
    )

    # Real-git CREATED with a real SHA.
    assert result.status is ScopedCommitStatus.CREATED, (
        f"real-git commit must land; got: {result!r}"
    )
    assert result.sha is not None
    assert len(result.sha) == 40  # real git SHA-1

    # HEAD advanced: the deterministic-writer content is at HEAD; the
    # unrelated agent WIP is left dirty.
    repo = Repo(tmp_git_repo)
    try:
        head = repo.head.commit
        assert head.hexsha == result.sha
        assert (tmp_git_repo / AGENTS_MD).read_text(encoding="utf-8") in {
            entry.data_stream.read().decode("utf-8")
            for entry in head.tree
            if entry.path == AGENTS_MD
        } or (tmp_git_repo / AGENTS_MD).read_text(encoding="utf-8") == (
            head.tree / AGENTS_MD
        ).data_stream.read().decode("utf-8")
        # Subject is the pinned literal (commit_message is the message
        # body, first line is the subject).
        assert head.message.splitlines()[0] == POLICY_AUTO_COMMIT_SUBJECT
        # The agent WIP is NOT in the chore commit tree.
        head_tree_paths = {entry.path for entry in head.tree}
        assert "agent_wip.py" not in head_tree_paths
        # The agent WIP is still in the working tree (pre-staged,
        # left for the agent / user commit flow).
        index_paths = {path for path, _ in repo.index.entries}
        assert "agent_wip.py" in index_paths
    finally:
        repo.close()

    # Second identical write: NOOP, no second commit.
    pre_contents_2 = capture_pre_write_contents(tmp_git_repo, [AGENTS_MD])
    (tmp_git_repo / AGENTS_MD).write_text(
        "# AGENTS.md -- deterministic writer output\n", encoding="utf-8"
    )
    result_2 = commit_policy_writes(
        tmp_git_repo,
        written_paths=[AGENTS_MD],
        pre_contents=pre_contents_2,
        create_commit_fn=create_commit,
        stage_fn=stage_files,
    )
    assert result_2.status is ScopedCommitStatus.NOOP, (
        f"second identical write must be NOOP; got: {result_2!r}"
    )
