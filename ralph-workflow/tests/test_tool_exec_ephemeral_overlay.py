"""Tests for ralph/mcp/tools/exec_overlay.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools.exec import run_command
from ralph.mcp.tools.exec_overlay import (
    _ensure_git_isolation,
    _mirror_workspace,
    _setup_private_gitdir,
    create_ephemeral_overlay,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestCreateEphemeralOverlay:
    def test_yields_private_overlay_directory(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "note.txt").write_text("hello", encoding="utf-8")

        with create_ephemeral_overlay(workspace) as overlay:
            assert overlay.is_dir()
            assert overlay != workspace
            assert (overlay / "note.txt").read_text(encoding="utf-8") == "hello"

    def test_copies_nested_directories(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        nested = workspace / "nested" / "dir"
        nested.mkdir(parents=True)
        (nested / "data.txt").write_text("nested", encoding="utf-8")

        with create_ephemeral_overlay(workspace) as overlay:
            assert (overlay / "nested" / "dir" / "data.txt").read_text(encoding="utf-8") == "nested"

    def test_preserves_file_contents(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "a.txt").write_text("alpha", encoding="utf-8")
        (workspace / "b.txt").write_text("bravo", encoding="utf-8")

        with create_ephemeral_overlay(workspace) as overlay:
            assert (overlay / "a.txt").read_text(encoding="utf-8") == "alpha"
            assert (overlay / "b.txt").read_text(encoding="utf-8") == "bravo"

    def test_writes_in_overlay_do_not_touch_source(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        source_file = workspace / "state.txt"
        source_file.write_text("original", encoding="utf-8")

        with create_ephemeral_overlay(workspace) as overlay:
            (overlay / "state.txt").write_text("overlay", encoding="utf-8")

        assert source_file.read_text(encoding="utf-8") == "original"

    def test_overlay_is_cleaned_up_after_exit(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        with create_ephemeral_overlay(workspace) as overlay:
            overlay_path = overlay
            assert overlay_path.exists()

        assert not overlay_path.exists()

    def test_ignores_agent_tmp_directory(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        (workspace / ".agent" / "tmp").mkdir(parents=True)
        (workspace / ".agent" / "tmp" / "ephemeral.txt").write_text("nope", encoding="utf-8")

        with create_ephemeral_overlay(workspace) as overlay:
            assert not (overlay / ".agent" / "tmp" / "ephemeral.txt").exists()

    def test_separate_calls_create_distinct_paths(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        with (
            create_ephemeral_overlay(workspace) as first,
            create_ephemeral_overlay(workspace) as second,
        ):
            assert first != second
            assert first.exists()
            assert second.exists()

    def test_mirror_workspace_omits_git_directory(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / ".git").mkdir()
        (workspace / ".git" / "config").write_text("[core]\n", encoding="utf-8")
        destination = tmp_path / "overlay"
        destination.mkdir()

        _mirror_workspace(workspace, destination)

        assert not (destination / ".git").exists()

    def test_ensure_git_isolation_copies_git_directory(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        source_git = workspace / ".git"
        source_git.mkdir()
        (source_git / "config").write_text("[core]\n", encoding="utf-8")
        overlay = tmp_path / "overlay"
        overlay.mkdir()

        _ensure_git_isolation(workspace, overlay)

        assert (overlay / ".git" / "config").read_text(encoding="utf-8") == "[core]\n"

    def test_setup_private_gitdir_copies_git_metadata(self, tmp_path: Path) -> None:
        source_git = tmp_path / "source-git"
        source_git.mkdir()
        (source_git / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
        overlay_git = tmp_path / "overlay" / ".git"

        _setup_private_gitdir(source_git, overlay_git)

        assert (overlay_git / "HEAD").read_text(encoding="utf-8") == "ref: refs/heads/main"

    def test_private_gitdir_does_not_modify_source_git(self, tmp_path: Path) -> None:
        source_git = tmp_path / "source-git"
        source_git.mkdir()
        (source_git / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
        overlay_git = tmp_path / "overlay" / ".git"

        _setup_private_gitdir(source_git, overlay_git)
        (overlay_git / "HEAD").write_text("ref: refs/heads/feature", encoding="utf-8")

        assert (source_git / "HEAD").read_text(encoding="utf-8") == "ref: refs/heads/main"

    def test_overlay_handles_source_without_git(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        with create_ephemeral_overlay(workspace) as overlay:
            assert not (overlay / ".git").exists()

    def test_overlay_makes_git_commands_available_in_private_copy(self, tmp_git_repo: Path) -> None:
        with create_ephemeral_overlay(tmp_git_repo) as overlay:
            assert (overlay / ".git").exists()


class TestRunCommandOverlayIntegration:
    @pytest.mark.subprocess_e2e
    def test_run_command_writes_only_in_overlay(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        target = workspace / "created.txt"
        script = (
            "from pathlib import Path; Path('created.txt').write_text('overlay', encoding='utf-8')"
        )

        result = run_command("python", ["-c", script], workspace, 5000)

        assert result.returncode == 0
        assert not target.exists()

    @pytest.mark.subprocess_e2e
    def test_git_reset_happens_only_in_overlay(self, tmp_git_repo: Path) -> None:
        readme = tmp_git_repo / "README.md"
        original = readme.read_text(encoding="utf-8")
        readme.write_text("modified in source copy\n", encoding="utf-8")

        result = run_command("git", ["reset", "--hard", "HEAD"], tmp_git_repo, 5000)

        assert result.returncode == 0
        assert readme.read_text(encoding="utf-8") == "modified in source copy\n"
        assert readme.read_text(encoding="utf-8") != original


class TestOverlayIsolationEndToEnd:
    @pytest.mark.subprocess_e2e
    def test_overlay_keeps_source_workspace_unchanged(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "marker.txt").write_text("source", encoding="utf-8")
        script = (
            "from pathlib import Path; Path('marker.txt').write_text('overlay', encoding='utf-8')"
        )

        result = run_command("python", ["-c", script], workspace, 5000)

        assert result.returncode == 0
        assert (workspace / "marker.txt").read_text(encoding="utf-8") == "source"

    @pytest.mark.subprocess_e2e
    def test_overlay_can_run_git_status_without_touching_source(self, tmp_git_repo: Path) -> None:
        before = (tmp_git_repo / "README.md").read_text(encoding="utf-8")

        result = run_command("git", ["status", "--short"], tmp_git_repo, 5000)

        assert result.returncode == 0
        assert (tmp_git_repo / "README.md").read_text(encoding="utf-8") == before
