"""Tests for config propagation across nested workspace scopes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke import InvokeOptions, _policy_from_options
from ralph.config.loader import GLOBAL_CONFIG_PATH, load_config
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

_MAIN_WAITING_INTERVAL = 45.0
_CHILD_WAITING_INTERVAL = 90.0
_CUSTOM_WAITING_INTERVAL = 60.0
_CUSTOM_SUSPECT_THRESHOLD = 120.0
_IDLE_TIMEOUT = 300.0


def test_load_config_uses_main_worktree_as_propagation_layer(
    monkeypatch,
    tmp_path: Path,
) -> None:
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    (main_repo / ".agent").mkdir()
    (main_repo / ".agent" / "ralph-workflow.toml").write_text(
        f"[general]\nagent_waiting_status_interval_seconds = {_MAIN_WAITING_INTERVAL}\n",
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

    assert config.general.agent_waiting_status_interval_seconds == _MAIN_WAITING_INTERVAL


def test_load_config_prefers_child_worktree_local_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    (main_repo / ".agent").mkdir()
    (main_repo / ".agent" / "ralph-workflow.toml").write_text(
        f"[general]\nagent_waiting_status_interval_seconds = {_MAIN_WAITING_INTERVAL}\n",
        encoding="utf-8",
    )

    child_worktree = tmp_path / "feature-worktree"
    child_worktree.mkdir()
    (child_worktree / ".agent").mkdir()
    (child_worktree / ".agent" / "ralph-workflow.toml").write_text(
        f"[general]\nagent_waiting_status_interval_seconds = {_CHILD_WAITING_INTERVAL}\n",
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

    assert config.general.agent_waiting_status_interval_seconds == _CHILD_WAITING_INTERVAL


def test_waiting_status_interval_propagates_to_timeout_policy() -> None:
    """Non-default waiting_status_interval_seconds reaches TimeoutPolicy via InvokeOptions."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
        waiting_status_interval_seconds=_CUSTOM_WAITING_INTERVAL,
    )
    policy = _policy_from_options(opts)

    assert policy.waiting_status_interval_seconds == _CUSTOM_WAITING_INTERVAL


def test_suspect_threshold_propagates_to_timeout_policy() -> None:
    """Non-default suspect_waiting_on_child_seconds reaches TimeoutPolicy via InvokeOptions."""
    opts = InvokeOptions(
        idle_timeout_seconds=_IDLE_TIMEOUT,
        suspect_waiting_on_child_seconds=_CUSTOM_SUSPECT_THRESHOLD,
    )
    policy = _policy_from_options(opts)

    assert policy.suspect_waiting_on_child_seconds == _CUSTOM_SUSPECT_THRESHOLD
