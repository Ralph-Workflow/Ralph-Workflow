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
    assert "scripts/verify.sh" not in staged
    assert "Makefile" not in staged


@pytest.mark.timeout_seconds(5)
def test_the_users_own_uncommitted_work_is_never_swept_in(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
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
    assert "scripts/verify.sh" not in staged


@pytest.mark.timeout_seconds(5)
def test_engine_scratch_is_never_committed(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
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


@pytest.mark.timeout_seconds(5)
def test_commit_policy_writes_skips_path_already_dirty_at_head(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
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

    (tmp_git_repo / AGENTS_MD).write_text("# AGENTS.md -- agent edit\n", encoding="utf-8")

    condense_form = "<!-- ralph-managed:begin -->\n<!-- ralph-managed:end -->\n"
    pre_contents = capture_pre_write_contents(tmp_git_repo, [AGENTS_MD])
    (tmp_git_repo / AGENTS_MD).write_text(condense_form, encoding="utf-8")

    result = commit_policy_writes(
        tmp_git_repo,
        written_paths=[AGENTS_MD],
        pre_contents=pre_contents,
        create_commit_fn=fake_create_commit,
    )

    assert result.status is ScopedCommitStatus.SKIPPED, (
        f"Path already dirty at HEAD must be SKIPPED, not CREATED/FAILED; got: {result.status}"
    )
    assert AGENTS_MD in result.skipped_paths
    fake_create_commit.assert_not_called()


@pytest.mark.timeout_seconds(5)
def test_commit_policy_writes_commits_clean_path(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
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
    result = commit_policy_writes(
        tmp_git_repo,
        written_paths=[],
        pre_contents={},
        create_commit_fn=fake_create_commit,
    )
    assert result.status is ScopedCommitStatus.NOOP
    fake_create_commit.assert_not_called()


@pytest.mark.timeout_seconds(5)
def test_commit_policy_writes_real_git_end_to_end(tmp_git_repo: Path) -> None:
    from git import Repo

    from ralph.git.operations import create_commit, stage_files

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

    wip = tmp_git_repo / "agent_wip.py"
    wip.write_text("# agent WIP\n", encoding="utf-8")
    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["agent_wip.py"])
    finally:
        repo.close()

    pre_contents = capture_pre_write_contents(tmp_git_repo, [AGENTS_MD])

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

    assert result.status is ScopedCommitStatus.CREATED, (
        f"real-git commit must land; got: {result!r}"
    )
    assert result.sha is not None
    assert len(result.sha) == 40

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
        assert head.message.splitlines()[0] == POLICY_AUTO_COMMIT_SUBJECT
        head_tree_paths = {entry.path for entry in head.tree}
        assert "agent_wip.py" not in head_tree_paths
        index_paths = {path for path, _ in repo.index.entries}
        assert "agent_wip.py" in index_paths
    finally:
        repo.close()

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
