"""Tests for config propagation across git worktrees."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from git import Repo

from ralph.config.loader import GLOBAL_CONFIG_PATH, load_config
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from pathlib import Path

MAIN_DEVELOPER_ITERS = 8
CHILD_DEVELOPER_ITERS = 3


def _init_repo(path: Path) -> None:
    repo = Repo.init(path)
    repo.config_writer().set_value("user", "name", "Test User").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()
    readme = path / "README.md"
    readme.write_text("main", encoding="utf-8")
    repo.index.add(["README.md"])
    repo.index.commit("initial commit")


def _add_worktree(main_repo: Path, worktree: Path) -> None:
    subprocess.run(
        ["git", "worktree", "add", str(worktree)],
        cwd=main_repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_load_config_uses_main_worktree_as_propagation_layer(
    monkeypatch,
    tmp_path: Path,
) -> None:
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    _init_repo(main_repo)
    (main_repo / ".agent").mkdir()
    (main_repo / ".agent" / "ralph-workflow.toml").write_text(
        "[general]\ndeveloper_iters = 8\n",
        encoding="utf-8",
    )

    child_worktree = tmp_path / "feature-worktree"
    _add_worktree(main_repo, child_worktree)

    monkeypatch.chdir(child_worktree)
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
    )

    config = load_config(workspace_scope=resolve_workspace_scope(child_worktree))

    assert config.general.developer_iters == MAIN_DEVELOPER_ITERS


def test_load_config_prefers_child_worktree_local_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    _init_repo(main_repo)
    (main_repo / ".agent").mkdir()
    (main_repo / ".agent" / "ralph-workflow.toml").write_text(
        "[general]\ndeveloper_iters = 8\n",
        encoding="utf-8",
    )

    child_worktree = tmp_path / "feature-worktree"
    _add_worktree(main_repo, child_worktree)
    (child_worktree / ".agent").mkdir()
    (child_worktree / ".agent" / "ralph-workflow.toml").write_text(
        "[general]\ndeveloper_iters = 3\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(child_worktree)
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
    )

    config = load_config(workspace_scope=resolve_workspace_scope(child_worktree))

    assert config.general.developer_iters == CHILD_DEVELOPER_ITERS
