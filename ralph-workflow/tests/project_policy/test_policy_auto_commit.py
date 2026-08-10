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

from ralph.git.scoped_auto_commit import list_dirty_paths
from ralph.project_policy._auto_commit import (
    POLICY_AUTO_COMMIT_SUBJECT,
    commit_policy_updates,
)

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.subprocess_e2e


@pytest.fixture
def fake_create_commit() -> MagicMock:
    return MagicMock(return_value="f" * 40)


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
def test_gate_scripts_written_by_the_policy_agent_are_committed(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
    """Remediation-authored gate scripts outside policy scope are committed."""
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
        authored_paths=frozenset({"scripts/verify.sh", "Makefile"}),
    )

    assert "scripts/verify.sh" in staged
    assert "Makefile" in staged
    assert "docs/ralph-workflow-policy/verification-policy.md" in staged


@pytest.mark.timeout_seconds(5)
def test_the_users_own_uncommitted_work_is_never_swept_in(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
    """A pre-run user edit is excluded while authored work is committed."""
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
        authored_paths=frozenset({"scripts/verify.sh"}),
    )

    assert "scripts/verify.sh" in staged
    assert "my_feature.py" not in staged


@pytest.mark.timeout_seconds(5)
def test_engine_scratch_is_never_committed(
    tmp_git_repo: Path, fake_create_commit: MagicMock
) -> None:
    """Engine scratch is never committed, even when it appears in authored paths."""
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
        authored_paths=frozenset({"scripts/verify.sh", ".agent/tmp/policy_remediation_prompt.md"}),
    )

    assert "scripts/verify.sh" in staged
    assert not any(path.startswith(".agent/") for path in staged), staged


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
    """Probe output is not remediation-authored content and is never staged."""
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
        authored_paths=frozenset({"scripts/verify.sh"}),
    )

    assert "scripts/verify.sh" in staged
    assert ".coverage" not in staged
    assert "coverage.xml" not in staged
