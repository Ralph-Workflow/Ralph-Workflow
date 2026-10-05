"""Parameterized black-box clean-tree tests for the non-bootstrap deterministic writers (wt-12).

For each deterministic writer surface (skill sync, skill install, policy
preflight, gitignore seed, config autowire) the test runs the writer FOR
REAL against a fresh git repository and asserts the wt-12 contract:

* after the writer call, the writer's own paths are CLEAN in
  ``git status --porcelain`` (the deterministic chore commit captured
  them -- nothing is left dirty for the committing agent);
* when content actually changed, a fixed-message commit exists at HEAD
  (the writer's pinned deterministic subject line);
* a no-op second invocation creates NO new commit.

The bootstrap ``_copy_with_backup`` writer is EXCLUDED here: it is
covered by :mod:`tests.test_config_bootstrap_git_isolation`. There is
NO cross-unit xfail: every case in this file must pass on its own.

All tests use real git (per-test repos via the shared ``tmp_git_repo``
template fixture) and run under the ``subprocess_e2e`` marker on the
default ``make test`` profile via
``ralph.test_suites.REQUIRED_AUTO_INTEGRATE_E2E_FILES``, inside the
IMMUTABLE 60 s combined verify budget.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from git import Actor, Repo

from ralph.git.operations import create_commit, stage_files
from ralph.git.scoped_auto_commit import ScopedCommitStatus
from ralph.language_detector.models import ProjectStack
from ralph.project_policy import preflight
from ralph.skills._auto_commit import SKILL_AUTO_COMMIT_SUBJECT, commit_skill_writes
from ralph.skills._content import BASELINE_SKILL_NAMES
from ralph.skills._installer import install_project_baseline_skills_with_diff

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(15),
]


# ---------------------------------------------------------------------------
# Shared real-git helpers
# ---------------------------------------------------------------------------


def _head_subject(repo_root: Path) -> str:
    """Return the HEAD commit subject line of the repo at ``repo_root``."""
    repo = Repo(repo_root)
    try:
        return repo.head.commit.message.splitlines()[0]
    finally:
        repo.close()


def _commit_count(repo_root: Path) -> int:
    repo = Repo(repo_root)
    try:
        return int(repo.git.rev_list("--count", "HEAD"))
    finally:
        repo.close()


def _porcelain(repo_root: Path) -> list[str]:
    """Return ``git status --porcelain`` lines (tracked + untracked)."""
    repo = Repo(repo_root)
    try:
        status: str = str(repo.git.status("--porcelain"))
        return [line for line in status.splitlines() if line]
    finally:
        repo.close()


def _paths_clean(repo_root: Path, paths: list[str]) -> bool:
    """True when none of ``paths`` appears dirty in ``git status --porcelain``."""
    dirty = {line[3:].strip('"') for line in _porcelain(repo_root)}
    return not (dirty & set(paths))


def _track_and_commit(repo_root: Path, files: list[str], message: str) -> None:
    """Stage ``files`` and commit them so the writer's diff is a real change."""
    repo = Repo(repo_root)
    try:
        repo.index.add(files)
        actor = Actor("Test Author", "test@example.com")
        repo.index.commit(message, author=actor, committer=actor)
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# 1. Skill sync (pipeline-run sync helper)
# ---------------------------------------------------------------------------


def test_skill_sync_leaves_skill_paths_clean_with_fixed_subject_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale tracked skill is overwritten and committed by the sync."""
    from ralph.cli.commands import run as run_module

    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    name = BASELINE_SKILL_NAMES[0]
    canonical = tmp_path / ".opencode" / "skills" / name
    canonical.mkdir(parents=True, exist_ok=True)
    (canonical / "SKILL.md").write_text("# stale\n", encoding="utf-8")
    (canonical / ".ralph-managed.json").write_text(
        '{"managed_by": "ralph-workflow", "installed_content_sha256": "deadbeef"}',
        encoding="utf-8",
    )
    Repo.init(tmp_path)
    _track_and_commit(tmp_path, [".opencode"], "seed stale skill")

    mock_manager = MagicMock()
    mock_manager.check_skills_for_updates.return_value = False
    monkeypatch.setattr(run_module, "SkillManager", lambda *a, **kw: mock_manager)
    monkeypatch.setattr(run_module, "_project_skills_need_install", lambda _root: True)
    # The gitignore/exclude seeders are separate writer surfaces with
    # their own tests; stub them here so this test pins ONLY the skill
    # sync writer's cleanliness contract.
    monkeypatch.setattr(
        "ralph.config.bootstrap.auto_seed_default_gitignore", lambda _root: None
    )
    monkeypatch.setattr(
        "ralph.config.bootstrap.auto_seed_default_git_exclude", lambda _root: None
    )

    run_module._sync_shipped_skills_on_pipeline_run(workspace_root=tmp_path)

    assert _head_subject(tmp_path) == SKILL_AUTO_COMMIT_SUBJECT
    assert _paths_clean(tmp_path, [f".opencode/skills/{name}/SKILL.md"]), (
        f"skill sync must leave its own paths clean; got: {_porcelain(tmp_path)}"
    )


# ---------------------------------------------------------------------------
# 2. Skill install (producer-level wrapper)
# ---------------------------------------------------------------------------


def test_skill_install_commits_clean_and_noop_second_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Install commits the fixed subject, leaves the tree clean, and a
    second run is a no-op (no new commit)."""
    home = tmp_path / "fake-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    name = BASELINE_SKILL_NAMES[0]
    canonical = tmp_path / ".opencode" / "skills" / name
    canonical.mkdir(parents=True, exist_ok=True)
    (canonical / "SKILL.md").write_text("# stale\n", encoding="utf-8")
    (canonical / ".ralph-managed.json").write_text(
        '{"managed_by": "ralph-workflow", "installed_content_sha256": "deadbeef"}',
        encoding="utf-8",
    )
    Repo.init(tmp_path)
    _track_and_commit(tmp_path, [".opencode"], "seed stale skill")
    commits_before = _commit_count(tmp_path)

    with patch("pathlib.Path.home", return_value=home):
        outcome = install_project_baseline_skills_with_diff(tmp_path)
        result = commit_skill_writes(
            tmp_path,
            written_paths=outcome.written_paths,
            pre_contents=outcome.pre_contents,
            create_commit_fn=create_commit,
            stage_fn=stage_files,
        )

    assert result.status is ScopedCommitStatus.CREATED
    assert _head_subject(tmp_path) == SKILL_AUTO_COMMIT_SUBJECT
    assert _paths_clean(tmp_path, [f".opencode/skills/{name}/SKILL.md"]), (
        f"install must leave its own paths clean; got: {_porcelain(tmp_path)}"
    )

    # No-op second run: content now matches the bundle, so the diff is
    # empty and no commit is created.
    with patch("pathlib.Path.home", return_value=home):
        second = install_project_baseline_skills_with_diff(tmp_path)

    assert second.written_paths == [], (
        "the no-op second install run must record no written paths"
    )
    assert _commit_count(tmp_path) == commits_before + 1, (
        "the no-op second run must not create a new commit"
    )


# ---------------------------------------------------------------------------
# 3. Policy preflight
# ---------------------------------------------------------------------------


def test_policy_preflight_leaves_policy_paths_clean_with_commit(
    tmp_git_repo: Path,
) -> None:
    """The preflight's own writes (AGENTS.md, CLAUDE.md, policy starters)
    are committed at the preflight boundary -- nothing left dirty."""
    from ralph.workspace.fs import FsWorkspace

    ws = FsWorkspace(tmp_git_repo)
    changed = preflight.run_policy_readiness_preflight(
        ws, ProjectStack(primary_language="Python")
    ).changed_files

    assert changed, "a fresh repo preflight must have policy writes to commit"
    assert _paths_clean(tmp_git_repo, changed), (
        f"preflight must leave its own paths clean; got: {_porcelain(tmp_git_repo)}"
    )


# ---------------------------------------------------------------------------
# 4. Gitignore seed
# ---------------------------------------------------------------------------


def test_gitignore_seed_commits_clean_and_noop_second_call(
    tmp_git_repo: Path,
    tmp_path: Path,
) -> None:
    """The default-gitignore seed commits with its fixed subject and a
    second call appends nothing (no new commit)."""
    from ralph.config.bootstrap import auto_seed_default_gitignore

    # tmp_git_repo comes from the shared template (a copied repo); the
    # copy keeps the template's initial commit so HEAD exists.
    appended = auto_seed_default_gitignore(tmp_git_repo)

    assert appended, "a template repo without .gitignore must seed patterns"
    assert _head_subject(tmp_git_repo) == "chore(gitignore): seed ralph defaults"
    assert _paths_clean(tmp_git_repo, [".gitignore"]), (
        f"gitignore seed must leave .gitignore clean; got: {_porcelain(tmp_git_repo)}"
    )
    commits_after_first = _commit_count(tmp_git_repo)

    second = auto_seed_default_gitignore(tmp_git_repo)
    assert second == [], "the idempotent second call must append nothing"
    assert _commit_count(tmp_git_repo) == commits_after_first, (
        "the no-op second call must not create a new commit"
    )


# ---------------------------------------------------------------------------
# 5. Config autowire
# ---------------------------------------------------------------------------


def test_config_autowire_leaves_config_clean_with_fixed_subject(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repo-local autowire commits ``chore(config): update agent
    configuration`` and leaves the config clean."""
    from ralph.config.agent_detection import autowire_chains_to_detected_agent

    defaults = (
        Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    )
    main_config = tmp_git_repo / "ralph-workflow.toml"
    main_config.write_text(
        (defaults / "ralph-workflow.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _track_and_commit(tmp_git_repo, ["ralph-workflow.toml"], "seed main config")
    commits_before = _commit_count(tmp_git_repo)

    monkeypatch.setattr("ralph.config.agent_detection.shutil.which", lambda _: None)

    result = autowire_chains_to_detected_agent(main_config, detected=["codex"])

    assert sorted(result or []) == ["claude"]
    assert _head_subject(tmp_git_repo) == "chore(config): update agent configuration"
    assert _paths_clean(tmp_git_repo, ["ralph-workflow.toml"]), (
        f"autowire must leave the config clean; got: {_porcelain(tmp_git_repo)}"
    )
    assert _commit_count(tmp_git_repo) == commits_before + 1

    # No-op: the chains are already rewritten to codex, so a re-run with
    # the same detection changes nothing and creates no commit.
    monkeypatch.setattr("ralph.config.agent_detection.shutil.which", lambda _: None)
    second = autowire_chains_to_detected_agent(main_config, detected=["codex"])
    assert second == "chains-customized", (
        "rewritten chains must be detected as customized (no-op branch)"
    )
    assert _commit_count(tmp_git_repo) == commits_before + 1, (
        "the no-op second autowire must not create a new commit"
    )

