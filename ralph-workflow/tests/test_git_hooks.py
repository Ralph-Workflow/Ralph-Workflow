"""Unit tests for git hook management."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.hooks import (
    HOOK_MARKER,
    RALPH_HOOK_NAMES,
    get_hooks_dir,
    install_hooks_in_repo,
    reinstall_hooks_if_tampered,
    uninstall_hooks,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_install_hooks_creates_hook_files(tmp_git_repo: Path) -> None:
    """Hooks should be created for every managed name."""
    install_hooks_in_repo(tmp_git_repo)

    hooks_dir = get_hooks_dir(tmp_git_repo)
    for hook_name in RALPH_HOOK_NAMES:
        hook_path = hooks_dir / hook_name
        assert hook_path.exists(), f"{hook_name} hook should exist"
        content = hook_path.read_text()
        assert HOOK_MARKER in content, f"{hook_name} hook should contain marker"


def test_reinstall_hooks_if_tampered_recreates_hook(tmp_git_repo: Path) -> None:
    """Tampering should trigger hook reinstallation."""
    install_hooks_in_repo(tmp_git_repo)
    hooks_dir = get_hooks_dir(tmp_git_repo)
    pre_commit = hooks_dir / "pre-commit"
    original = pre_commit.read_text()
    assert HOOK_MARKER in original

    pre_commit.chmod(0o755)
    pre_commit.write_text("#!/usr/bin/env bash\necho tampered\nexit 0\n")
    assert HOOK_MARKER not in pre_commit.read_text()

    replaced = reinstall_hooks_if_tampered(logger=logger, repo_root=tmp_git_repo)
    assert replaced
    assert HOOK_MARKER in pre_commit.read_text()


def test_uninstall_hooks_removes_managed_files(tmp_git_repo: Path) -> None:
    """Uninstall should clean up managed hooks."""
    install_hooks_in_repo(tmp_git_repo)

    hooks_dir = get_hooks_dir(tmp_git_repo)
    assert any((hooks_dir / name).exists() for name in RALPH_HOOK_NAMES)

    removed = uninstall_hooks(logger=logger, repo_root=tmp_git_repo)
    assert removed
    assert all(not (hooks_dir / name).exists() for name in RALPH_HOOK_NAMES)
