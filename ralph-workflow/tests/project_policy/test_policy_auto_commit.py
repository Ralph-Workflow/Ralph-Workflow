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


@pytest.mark.timeout_seconds(5)
def test_gate_scripts_written_by_the_policy_agent_are_committed(
    tmp_path: Path, fake_create_commit: MagicMock
) -> None:
    """A gate script the remediation agent writes lives OUTSIDE the canonical
    policy directory (scripts/, a Makefile, a CI workflow). It must still be
    committed deterministically -- left uncommitted it dirties the working tree
    for the next agent and trips the commit-cleanup phase."""
    _init_repo_with_commit(tmp_path)
    pre_run_dirty = list_dirty_paths(tmp_path)

    # The remediation agent writes its policy AND the gate script that backs the
    # RALPH-COMMAND it just declared.
    policy_dir = tmp_path / "docs" / "ralph-workflow-policy"
    policy_dir.mkdir(parents=True)
    (policy_dir / "verification-policy.md").write_text("policy", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n", encoding="utf-8"
    )
    (tmp_path / "Makefile").write_text("verify:\n\t./scripts/verify.sh\n", encoding="utf-8")

    staged: list[str] = []
    commit_policy_updates(
        tmp_path,
        fake_create_commit,
        stage_fn=lambda _root, paths: staged.extend(paths),
        pre_run_dirty=pre_run_dirty,
        authored_paths=frozenset({"scripts/verify.sh", "Makefile"}),
    )

    assert "scripts/verify.sh" in staged, "the gate script must be committed"
    assert "Makefile" in staged, "the target that wires the gate must be committed"
    assert "docs/ralph-workflow-policy/verification-policy.md" in staged


@pytest.mark.timeout_seconds(5)
def test_the_users_own_uncommitted_work_is_never_swept_in(
    tmp_path: Path, fake_create_commit: MagicMock
) -> None:
    """THE SAFETY PROPERTY. Committing outside a fixed scope is only safe because
    the file set is a DIFF against a pre-run snapshot: a file the user was already
    editing was dirty before the policy run started, so it is excluded."""
    _init_repo_with_commit(tmp_path)

    # The user is midway through editing their own code.
    (tmp_path / "my_feature.py").write_text("work in progress", encoding="utf-8")
    pre_run_dirty = list_dirty_paths(tmp_path)
    assert "my_feature.py" in pre_run_dirty

    # Now the policy agent runs and writes a gate script.
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    staged: list[str] = []
    commit_policy_updates(
        tmp_path,
        fake_create_commit,
        stage_fn=lambda _root, paths: staged.extend(paths),
        pre_run_dirty=pre_run_dirty,
        authored_paths=frozenset({"scripts/verify.sh"}),
    )

    assert "scripts/verify.sh" in staged, "the agent's gate script IS committed"
    assert "my_feature.py" not in staged, (
        "the user's in-progress work must NEVER be swept into an automated commit"
    )


@pytest.mark.timeout_seconds(5)
def test_engine_scratch_is_never_committed(
    tmp_path: Path, fake_create_commit: MagicMock
) -> None:
    """.agent/ is engine-owned scratch (prompts, artifacts, caches). It becomes
    dirty during every policy run and must never be committed."""
    _init_repo_with_commit(tmp_path)
    pre_run_dirty = list_dirty_paths(tmp_path)

    (tmp_path / ".agent" / "tmp").mkdir(parents=True)
    (tmp_path / ".agent" / "tmp" / "policy_remediation_prompt.md").write_text(
        "prompt", encoding="utf-8"
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    staged: list[str] = []
    commit_policy_updates(
        tmp_path,
        fake_create_commit,
        stage_fn=lambda _root, paths: staged.extend(paths),
        pre_run_dirty=pre_run_dirty,
        authored_paths=frozenset(
            {"scripts/verify.sh", ".agent/tmp/policy_remediation_prompt.md"}
        ),
    )

    assert "scripts/verify.sh" in staged
    assert not any(path.startswith(".agent/") for path in staged), staged


@pytest.mark.timeout_seconds(5)
def test_without_a_snapshot_nothing_outside_the_policy_scope_is_committed(
    tmp_path: Path, fake_create_commit: MagicMock
) -> None:
    """Passing no snapshot disables out-of-scope attribution entirely -- the
    conservative default, preserving the original scoped behavior."""
    _init_repo_with_commit(tmp_path)
    policy_dir = tmp_path / "docs" / "ralph-workflow-policy"
    policy_dir.mkdir(parents=True)
    (policy_dir / "verification-policy.md").write_text("policy", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    staged: list[str] = []
    commit_policy_updates(
        tmp_path,
        fake_create_commit,
        stage_fn=lambda _root, paths: staged.extend(paths),
    )

    assert "docs/ralph-workflow-policy/verification-policy.md" in staged
    assert "scripts/verify.sh" not in staged


@pytest.mark.timeout_seconds(5)
def test_user_wip_on_an_in_scope_file_is_never_swept_in(
    tmp_path: Path, fake_create_commit: MagicMock
) -> None:
    """REGRESSION (found by review). AGENTS.md and the canonical policy dir are
    Ralph-OWNED, so they are in scope. That makes them ours to WRITE -- it does
    NOT make the user's uncommitted edit to them ours to COMMIT.

    Before the fix, pre_run_dirty was only subtracted from the out-of-scope
    extension, so a user mid-edit on AGENTS.md had their work silently committed.
    """
    _init_repo_with_commit(tmp_path)

    # The user is mid-edit on two Ralph-owned, in-scope files.
    (tmp_path / "AGENTS.md").write_text("my work in progress", encoding="utf-8")
    policy_dir = tmp_path / "docs" / "ralph-workflow-policy"
    policy_dir.mkdir(parents=True)
    (policy_dir / "testing-policy.md").write_text("my draft policy", encoding="utf-8")
    pre_run_dirty = list_dirty_paths(tmp_path)

    # The policy run then writes a DIFFERENT policy file of its own.
    (policy_dir / "linting-policy.md").write_text("agent wrote this", encoding="utf-8")

    staged: list[str] = []
    commit_policy_updates(
        tmp_path,
        fake_create_commit,
        stage_fn=lambda _root, paths: staged.extend(paths),
        pre_run_dirty=pre_run_dirty,
    )

    assert "docs/ralph-workflow-policy/linting-policy.md" in staged
    assert "AGENTS.md" not in staged, "the user's in-progress AGENTS.md must not be committed"
    assert "docs/ralph-workflow-policy/testing-policy.md" not in staged, (
        "an in-scope file the user was already editing must not be committed"
    )


@pytest.mark.timeout_seconds(5)
def test_gate_probe_detritus_is_never_committed(
    tmp_path: Path, fake_create_commit: MagicMock
) -> None:
    """REGRESSION (found by review). The ANALYSIS phase RUNS every declared gate
    as a probe, and probes drop build detritus (.coverage, coverage.xml,
    *.tsbuildinfo) into the tree. Those are side effects of READING the project,
    not authored content.

    Attribution is therefore scoped to what the REMEDIATION agent wrote
    (authored_paths), not to "whatever became dirty during the run".
    """
    _init_repo_with_commit(tmp_path)
    pre_run_dirty = list_dirty_paths(tmp_path)

    # The remediation agent authored a gate script...
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    authored = frozenset({"scripts/verify.sh"})

    # ...and the analysis agent's probe then dropped coverage output.
    (tmp_path / ".coverage").write_text("probe detritus", encoding="utf-8")
    (tmp_path / "coverage.xml").write_text("probe detritus", encoding="utf-8")

    staged: list[str] = []
    commit_policy_updates(
        tmp_path,
        fake_create_commit,
        stage_fn=lambda _root, paths: staged.extend(paths),
        pre_run_dirty=pre_run_dirty,
        authored_paths=authored,
    )

    assert "scripts/verify.sh" in staged
    assert ".coverage" not in staged, "a gate probe's output is not authored content"
    assert "coverage.xml" not in staged
