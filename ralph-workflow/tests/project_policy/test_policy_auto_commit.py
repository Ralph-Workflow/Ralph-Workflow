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
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from git import Actor, Repo

from ralph.project_policy._auto_commit import (
    POLICY_AUTO_COMMIT_SUBJECT,
    commit_policy_updates,
)

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.subprocess_e2e


@pytest.fixture
def fake_create_commit() -> MagicMock:
    fake_sha = "f" * 40
    return MagicMock(return_value=fake_sha)


def _init_repo_with_commit(repo_root: Path) -> None:
    Repo.init(repo_root)
    repo = Repo(repo_root)
    try:
        repo.config_writer().set_value("user", "name", "Test Author").release()
        repo.config_writer().set_value("user", "email", "test@example.com").release()
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit("initial", author=actor, committer=actor)
    finally:
        repo.close()


@pytest.mark.timeout_seconds(5)
def test_policy_auto_commit_subject_and_scoped_staging(
    tmp_path: Path, fake_create_commit: MagicMock
) -> None:
    _init_repo_with_commit(tmp_path)
    policy_dir = tmp_path / "docs" / "ralph-workflow-policy"
    policy_dir.mkdir(parents=True)
    (policy_dir / "testing-policy.md").write_text("policy", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("agents", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("print()", encoding="utf-8")

    staged: list[list[str]] = []

    def spy_stage(_root: Path | str, files: list[str]) -> None:
        staged.append(list(files))

    sha = commit_policy_updates(tmp_path, fake_create_commit, stage_fn=spy_stage)

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
    tmp_path: Path, fake_create_commit: MagicMock
) -> None:
    """A migration candidate carrying the migrated marker is auto-committed;
    a candidate WITHOUT the marker (unrelated user edits) is never swept in."""
    _init_repo_with_commit(tmp_path)
    (tmp_path / "AGENTS.md").write_text("agents", encoding="utf-8")
    (tmp_path / "CONTRIBUTING.md").write_text(
        "# Contributing\n\n"
        "<!-- ralph-workflow-policy:migrated -> docs/ralph-workflow-policy/testing-policy.md -->\n",
        encoding="utf-8",
    )
    (tmp_path / "TESTING.md").write_text(
        "# Testing\n\nuser notes, no migration marker\n", encoding="utf-8"
    )

    staged: list[list[str]] = []

    def spy_stage(_root: Path | str, files: list[str]) -> None:
        staged.append(list(files))

    sha = commit_policy_updates(tmp_path, fake_create_commit, stage_fn=spy_stage)

    assert sha == "f" * 40
    flat = [path for batch in staged for path in batch]
    assert "CONTRIBUTING.md" in flat
    assert "TESTING.md" not in flat


@pytest.mark.timeout_seconds(5)
def test_policy_auto_commit_skips_clean_tree(
    tmp_path: Path, fake_create_commit: MagicMock
) -> None:
    _init_repo_with_commit(tmp_path)
    (tmp_path / "unrelated.py").write_text("print()", encoding="utf-8")

    sha = commit_policy_updates(tmp_path, fake_create_commit)

    assert sha is None
    fake_create_commit.assert_not_called()


@pytest.mark.timeout_seconds(5)
def test_policy_auto_commit_skips_non_git_workspace(
    tmp_path: Path, fake_create_commit: MagicMock
) -> None:
    (tmp_path / "AGENTS.md").write_text("agents", encoding="utf-8")

    sha = commit_policy_updates(tmp_path, fake_create_commit)

    assert sha is None
    fake_create_commit.assert_not_called()
