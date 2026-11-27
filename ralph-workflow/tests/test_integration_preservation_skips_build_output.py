from __future__ import annotations

import tarfile
from pathlib import Path

import pytest
from git import Actor, Repo

from ralph.git.git_command_queue import route_git_method
from ralph.pipeline.integration_preservation_bundle import preserve_integration_state

pytestmark = pytest.mark.subprocess_e2e

_ACTOR = Actor("Ralph Test", "test@ralph.internal")


def _repo_with_work_and_build_output(root: Path) -> Repo:
    repo = Repo.init(root, initial_branch="main")
    with repo.config_writer() as config:
        config.set_value("user", "name", _ACTOR.name)
        config.set_value("user", "email", _ACTOR.email)
    (root / ".gitignore").write_text("target/\n", encoding="utf-8")
    (root / "src.rs").write_text("fn main() {}\n", encoding="utf-8")
    route_git_method(repo, "add", ("-A",))
    route_git_method(repo, "commit", ("-q", "-m", "base"))
    (root / "src.rs").write_text("fn main() { work(); }\n", encoding="utf-8")
    (root / "new_module.rs").write_text("fn work() {}\n", encoding="utf-8")
    build = root / "target" / "debug"
    build.mkdir(parents=True)
    (build / "huge.rlib").write_bytes(b"x" * 4096)
    return repo


def _archived(tmp_path: Path) -> set[str]:
    root = tmp_path / "work"
    root.mkdir()
    _repo_with_work_and_build_output(root)
    bundle = preserve_integration_state(root, state_dir=tmp_path / "state", target="main")
    assert bundle is not None
    with tarfile.open(bundle.worktree_archive) as archive:
        return {member.name for member in archive.getmembers() if member.isfile()}


def test_uncommitted_work_is_preserved(tmp_path: Path) -> None:
    archived = _archived(tmp_path)

    assert "src.rs" in archived
    assert "new_module.rs" in archived
    assert ".gitignore" in archived


def test_ignored_build_output_is_not_copied_into_the_bundle(tmp_path: Path) -> None:
    archived = _archived(tmp_path)

    assert not any(name.startswith("target/") for name in archived), sorted(archived)
