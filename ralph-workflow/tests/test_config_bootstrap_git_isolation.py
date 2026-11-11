from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Repo
from loguru import logger

from ralph.config.bootstrap import ensure_local_configs

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(5),
]


def _git(repo_root: Path, *args: str) -> str:
    command, *rest = args
    repo = Repo(repo_root)
    try:
        if command == "add":
            return repo.git.add(*rest)
        if command == "commit":
            return repo.git.commit(*rest)
        if command == "log":
            return repo.git.log(*rest)
        if command == "status":
            return repo.git.status(*rest)
        if command == "ls-files":
            return repo.git.ls_files(*rest)
        if command == "check-ignore":
            return repo.git.check_ignore(*rest)
    finally:
        repo.close()
    raise AssertionError(f"unsupported git command: {command}")


def _init_repo_with_initial_commit(repo_root: Path) -> None:
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
    out = _git(repo_root, "log", "--format=%s")
    return [line for line in out.splitlines() if line.strip()]


def _non_gitignore_new_subjects(repo_root: Path, subjects_before: set[str]) -> list[str]:
    return [
        s
        for s in _commit_subjects(repo_root)
        if s not in subjects_before and not s.startswith("chore(gitignore):")
    ]


class _WarningCapture:
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
    repo_root = tmp_path
    _init_repo_with_initial_commit(repo_root)
    agent_dir = repo_root / ".agent"
    agent_dir.mkdir(parents=True)
    config_path = agent_dir / "ralph-workflow.toml"
    config_path.write_text("# placeholder\n", encoding="utf-8")
    _git(repo_root, "add", ".agent/ralph-workflow.toml")
    _git(repo_root, "commit", "-m", "track ralph config", "--no-gpg-sign")

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
    regenerated = [r for r in results if r.path == config_path]
    assert regenerated and regenerated[0].action == "regenerated"

    porcelain = [
        line
        for line in _git(repo_root, "status", "--porcelain").splitlines()
        if line.strip() and not line.endswith("README.md")
    ]
    assert porcelain == [], f"Expected no dirty paths besides README.md; got {porcelain!r}"

    assert (agent_dir / "ralph-workflow.toml.bak").exists()
    tracked = _git(repo_root, "ls-files")
    assert not any(".bak" in line for line in tracked.splitlines())


def test_tracked_symlink_config_regeneration_commits_dedicated_chore(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path
    _init_repo_with_initial_commit(repo_root)
    real_path = repo_root / "real.toml"
    real_path.write_text("source\n", encoding="utf-8")
    agent_dir = repo_root / ".agent"
    agent_dir.mkdir(parents=True)
    config_path = agent_dir / "ralph-workflow.toml"
    config_path.symlink_to(real_path)
    _git(repo_root, "add", ".agent/ralph-workflow.toml", "real.toml")
    _git(repo_root, "commit", "-m", "track symlink config", "--no-gpg-sign")
    real_path.write_text("user edit\n", encoding="utf-8")
    subjects_before = set(_commit_subjects(repo_root))

    with _WarningCapture() as captured:
        results = ensure_local_configs(agent_dir, force=True)

    assert not any("has no pre-write hash recorded" in msg for msg in captured.messages), (
        f"Tracked-symlink regeneration must record a pre-write hash; got {captured.messages!r}"
    )
    assert not any("was already dirty at HEAD" in msg for msg in captured.messages), (
        f"Tracked-symlink regeneration must not see HEAD-dirty paths; got {captured.messages!r}"
    )
    new_subjects = _non_gitignore_new_subjects(repo_root, subjects_before)
    assert "chore(config): update ralph-workflow.toml" in new_subjects, (
        f"Expected a dedicated chore(config) commit for ralph-workflow.toml; "
        f"new commits: {new_subjects!r}"
    )
    regenerated = [r for r in results if r.path == config_path]
    assert regenerated and regenerated[0].action == "regenerated"

    porcelain = _git(repo_root, "status", "--porcelain")
    assert ".agent/ralph-workflow.toml" not in porcelain, (
        f"Tracked symlink must be committed; porcelain: {porcelain!r}"
    )
    assert "real.toml" in porcelain, (
        f"User-dirty real.toml must stay uncommitted; porcelain: {porcelain!r}"
    )


def test_nonignored_first_creation_commits_dedicated_chore(tmp_path: Path) -> None:
    repo_root = tmp_path
    _init_repo_with_initial_commit(repo_root)
    agent_dir = repo_root / ".agent"
    subjects_before = set(_commit_subjects(repo_root))

    results = ensure_local_configs(agent_dir, force=True)

    config_path = agent_dir / "ralph-workflow.toml"
    created = [r for r in results if r.path == config_path]
    assert created and created[0].action == "created", (
        f"First-creation must report 'created'; got {[r.action for r in results]!r}"
    )
    new_subjects = _non_gitignore_new_subjects(repo_root, subjects_before)
    assert "chore(config): update ralph-workflow.toml" in new_subjects, (
        f"Expected a dedicated chore(config) commit for ralph-workflow.toml; "
        f"new commits: {new_subjects!r}"
    )
    porcelain = _git(repo_root, "status", "--porcelain", "--", str(config_path))
    assert porcelain.strip() == "", (
        f"First-created nonignored config must be clean; got {porcelain!r}"
    )


def test_ignored_first_creation_commits_nothing_and_warns_not(tmp_path: Path) -> None:
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
    assert new_subjects == [], f"Ignored-path regeneration must not commit; got {new_subjects!r}"
    regenerated = [r for r in results if r.path == config_path]
    assert regenerated and regenerated[0].action == "regenerated"
