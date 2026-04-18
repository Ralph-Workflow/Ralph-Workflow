"""Tests for config propagation across git worktrees."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config.loader import GLOBAL_CONFIG_PATH, load_config
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

MAIN_DEVELOPER_ITERS = 8
CHILD_DEVELOPER_ITERS = 3


def test_load_config_uses_main_worktree_as_propagation_layer(
    monkeypatch,
    tmp_path: Path,
) -> None:
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    (main_repo / ".agent").mkdir()
    (main_repo / ".agent" / "ralph-workflow.toml").write_text(
        "[general]\ndeveloper_iters = 8\n",
        encoding="utf-8",
    )

    child_worktree = tmp_path / "feature-worktree"
    child_worktree.mkdir()

    monkeypatch.chdir(child_worktree)
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
    )

    config = load_config(
        workspace_scope=WorkspaceScope(
            root=child_worktree,
            local_config_path=child_worktree / ".agent" / "ralph-workflow.toml",
            propagated_config_paths=(main_repo / ".agent" / "ralph-workflow.toml",),
        )
    )

    assert config.general.developer_iters == MAIN_DEVELOPER_ITERS


def test_load_config_prefers_child_worktree_local_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    (main_repo / ".agent").mkdir()
    (main_repo / ".agent" / "ralph-workflow.toml").write_text(
        "[general]\ndeveloper_iters = 8\n",
        encoding="utf-8",
    )

    child_worktree = tmp_path / "feature-worktree"
    child_worktree.mkdir()
    (child_worktree / ".agent").mkdir()
    (child_worktree / ".agent" / "ralph-workflow.toml").write_text(
        "[general]\ndeveloper_iters = 3\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(child_worktree)
    monkeypatch.setattr(
        "ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / GLOBAL_CONFIG_PATH.name
    )

    config = load_config(
        workspace_scope=WorkspaceScope(
            root=child_worktree,
            local_config_path=child_worktree / ".agent" / "ralph-workflow.toml",
            propagated_config_paths=(main_repo / ".agent" / "ralph-workflow.toml",),
        )
    )

    assert config.general.developer_iters == CHILD_DEVELOPER_ITERS
