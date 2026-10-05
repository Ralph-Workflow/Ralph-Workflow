"""Regression tests: config bootstrap writes must not sweep agent/user work.

wt-12: ``config/bootstrap.py::_copy_with_backup`` used to write
``.agent/ralph-workflow.toml`` (and siblings) without any commit routing, so a
forced regeneration left a deterministic, Ralph-authored change dirty in the
user's tree with no way to land it.

The contract pinned here (real git, per-test ``tmp_path`` repos):

* a tracked config file regenerated with ``force=True`` lands as a dedicated
  ``chore(config): update <filename>`` commit, sweeping NOTHING else —
  unrelated dirty tracked files stay dirty, and the ``.bak`` backup is not
  swept in;
* a first-creation inside a repo that ignores ``.agent/`` writes the file but
  issues NO commit and NO warning (the path is ignored — not Ralph's place to
  commit);
* a forced regeneration of an ignored file likewise stays silent.

The tests use real git and the production ``ensure_local_configs`` flow. They
run well inside the immutable 60s combined verify budget.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest
from git import Repo
from loguru import logger

from ralph.config.bootstrap import ensure_local_configs

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [
    pytest.mark.subprocess_e2e,
    # Real-git setup; each test must stay far inside the per-suite cap and
    # the file must complete in well under 4 seconds (immutable 60s budget).
    pytest.mark.timeout_seconds(5),
]


def _git(repo_root: Path, *args: str) -> str:
    """Run a git command in ``repo_root`` and return stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _init_repo_with_initial_commit(repo_root: Path) -> None:
    """Create a fresh git repo with one initial commit so HEAD exists."""
    repo = Repo.init(repo_root)
    try:
        repo.config_writer().set_value("user", "name", "Test Author").release()
        repo.config_writer().set_value("user", "email", "test@example.com").release()
    finally:
        repo.close()
    (repo_root / "README.md").write_text("# scratch\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-m", "init", "--no-gpg-sign")


def _commit_subjects(repo_root: Path) -> list[str]:
    """Return every commit subject in the repo history."""
    out = _git(repo_root, "log", "--format=%s")
    return [line for line in out.splitlines() if line.strip()]


def _non_gitignore_new_subjects(repo_root: Path, subjects_before: set[str]) -> list[str]:
    """New commit subjects excluding the expected .gitignore auto-seed commit."""
    return [
        s
        for s in _commit_subjects(repo_root)
        if s not in subjects_before and not s.startswith("chore(gitignore):")
    ]


class _WarningCapture:
    """Collect loguru WARNING+ records emitted during a block."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def __enter__(self) -> _WarningCapture:
        self._sink_id = logger.add(self._sink, level="WARNING")
        return self

    def _sink(self, message: object) -> None:
        self.messages.append(str(message))

    def __exit__(self, *args: object) -> None:
        logger.remove(self._sink_id)


def test_tracked_config_regeneration_commits_dedicated_chore(tmp_path: Path) -> None:
    """Forced regeneration of a TRACKED config commits only that file.

    A dedicated ``chore(config): update ralph-workflow.toml`` commit must
    exist; an unrelated dirty tracked file must stay dirty; the ``.bak``
    backup must not be swept into the commit.
    """
    repo_root = tmp_path
    _init_repo_with_initial_commit(repo_root)
    agent_dir = repo_root / ".agent"
    agent_dir.mkdir(parents=True)
    config_path = agent_dir / "ralph-workflow.toml"
    config_path.write_text("# placeholder\n", encoding="utf-8")
    _git(repo_root, "add", ".agent/ralph-workflow.toml")
    _git(repo_root, "commit", "-m", "track ralph config", "--no-gpg-sign")

    # An unrelated tracked file the "user" is mid-edit on.
    (repo_root / "README.md").write_text("# user edit in progress\n", encoding="utf-8")
    subjects_before = set(_commit_subjects(repo_root))

    results = ensure_local_configs(agent_dir, force=True)

    subjects_after = _commit_subjects(repo_root)
    new_subjects = [
        s
        for s in subjects_after
        if s not in subjects_before and not s.startswith("chore(gitignore):")
    ]
    assert "chore(config): update ralph-workflow.toml" in new_subjects, (
        f"Expected a dedicated chore(config) commit for ralph-workflow.toml; "
        f"new commits: {new_subjects!r}"
    )
    # The regeneration actually happened.
    regenerated = [r for r in results if r.path == config_path]
    assert regenerated and regenerated[0].action == "regenerated"

    # ONLY the unrelated dirty file remains in the working tree. (The
    # gitignore seeding of `.agent/`-adjacent engine patterns is itself
    # committed by auto_seed_default_gitignore; what must remain dirty is
    # exactly the user's unrelated edit.)
    porcelain = [
        line
        for line in _git(repo_root, "status", "--porcelain").splitlines()
        if line.strip() and not line.endswith("README.md")
    ]
    assert porcelain == [], f"Expected no dirty paths besides README.md; got {porcelain!r}"

    # The .bak backup exists on disk but was NOT committed.
    assert (agent_dir / "ralph-workflow.toml.bak").exists()
    tracked = _git(repo_root, "ls-files")
    assert not any(".bak" in line for line in tracked.splitlines())


def test_ignored_first_creation_commits_nothing_and_warns_not(tmp_path: Path) -> None:
    """First creation under an ignored path: no commit, no warning."""
    repo_root = tmp_path
    _init_repo_with_initial_commit(repo_root)
    (repo_root / ".gitignore").write_text(".agent/\n", encoding="utf-8")
    _git(repo_root, "add", ".gitignore")
    _git(repo_root, "commit", "-m", "ignore agent dir", "--no-gpg-sign")
    subjects_before = set(_commit_subjects(repo_root))

    agent_dir = repo_root / ".agent"
    with _WarningCapture() as captured:
        ensure_local_configs(agent_dir, force=True)

    assert captured.messages == [], (
        f"Ignored-path first creation must not warn; got {captured.messages!r}"
    )
    new_subjects = _non_gitignore_new_subjects(repo_root, subjects_before)
    assert new_subjects == [], f"Ignored-path creation must not commit; got {new_subjects!r}"
    config_path = agent_dir / "ralph-workflow.toml"
    assert config_path.exists(), "The config file must still be written"
    ignored = _git(repo_root, "check-ignore", str(config_path))
    assert ignored.strip(), "The written config must be ignored by git"


def test_ignored_force_regeneration_stays_silent(tmp_path: Path) -> None:
    """Forced regeneration of an IGNORED config: no warning, no commit."""
    repo_root = tmp_path
    _init_repo_with_initial_commit(repo_root)
    (repo_root / ".gitignore").write_text(".agent/\n", encoding="utf-8")
    _git(repo_root, "add", ".gitignore")
    _git(repo_root, "commit", "-m", "ignore agent dir", "--no-gpg-sign")
    agent_dir = repo_root / ".agent"
    agent_dir.mkdir(parents=True)
    config_path = agent_dir / "ralph-workflow.toml"
    config_path.write_text("# existing ignored config\n", encoding="utf-8")
    subjects_before = set(_commit_subjects(repo_root))

    with _WarningCapture() as captured:
        results = ensure_local_configs(agent_dir, force=True)

    assert captured.messages == [], (
        f"Ignored-path regeneration must not warn; got {captured.messages!r}"
    )
    new_subjects = _non_gitignore_new_subjects(repo_root, subjects_before)
    assert new_subjects == [], (
        f"Ignored-path regeneration must not commit; got {new_subjects!r}"
    )
    regenerated = [r for r in results if r.path == config_path]
    assert regenerated and regenerated[0].action == "regenerated"
