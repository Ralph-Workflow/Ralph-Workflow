"""Tests for ralph.mcp.tools.exec_overlay."""

from __future__ import annotations

import sys
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

import pytest

from ralph.mcp.tools._exec_completed_process import _CompletedProcessAdapter
from ralph.mcp.tools._exec_run_deps import ExecRunDeps
from ralph.mcp.tools.exec import run_command
from ralph.mcp.tools.exec_overlay import create_ephemeral_overlay
from tests.mock_workspace_root import MockWorkspaceRoot


class TestCreateEphemeralOverlay:
    def test_mirrors_workspace_files(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "root.txt").write_text("root", encoding="utf-8")

        with create_ephemeral_overlay(workspace) as overlay:
            assert overlay.is_dir()
            assert overlay != workspace
            assert (overlay / "root.txt").read_text(encoding="utf-8") == "root"

    def test_yields_path_inside_temp_dir(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        with create_ephemeral_overlay(workspace) as overlay:
            assert overlay.is_dir()
            assert overlay != workspace
            assert overlay.parent != workspace.parent

    def test_mirrors_nested_directories(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        nested_file = workspace / "nested" / "inner.txt"
        nested_file.parent.mkdir(parents=True)
        nested_file.write_text("inner", encoding="utf-8")

        with create_ephemeral_overlay(workspace) as overlay:
            assert (overlay / "nested" / "inner.txt").read_text(encoding="utf-8") == "inner"

    def test_dereferences_symlinked_files(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        target = workspace / "target.txt"
        target.write_text("real content", encoding="utf-8")
        link = workspace / "link.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are not supported in this environment")

        with create_ephemeral_overlay(workspace) as overlay:
            copied = overlay / "link.txt"
            assert copied.exists()
            assert not copied.is_symlink()
            assert copied.read_text(encoding="utf-8") == "real content"

    def test_writes_through_copied_symlinks_do_not_escape(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        target = workspace / "target.txt"
        target.write_text("original", encoding="utf-8")
        link = workspace / "link.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are not supported in this environment")

        with create_ephemeral_overlay(workspace) as overlay:
            (overlay / "link.txt").write_text("overlay", encoding="utf-8")

        assert target.read_text(encoding="utf-8") == "original"

    def test_isolates_writes_from_real_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "file.txt").write_text("original", encoding="utf-8")

        with create_ephemeral_overlay(workspace) as overlay:
            (overlay / "file.txt").write_text("modified", encoding="utf-8")
            (overlay / "new_file.txt").write_text("new", encoding="utf-8")

        assert (workspace / "file.txt").read_text(encoding="utf-8") == "original"
        assert not (workspace / "new_file.txt").exists()

    def test_excludes_generated_dirs(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        exclusions = {
            ".venv": "lib.py",
            ".mypy_cache": "cache.json",
            ".pytest_cache": "cache.json",
            "__pycache__": "module.pyc",
            "node_modules": "package.json",
            ".tox": "tox.ini",
            ".nox": "session.log",
        }
        for directory, filename in exclusions.items():
            path = workspace / directory
            path.mkdir(parents=True)
            (path / filename).write_text(directory, encoding="utf-8")

        with create_ephemeral_overlay(workspace) as overlay:
            for directory, filename in exclusions.items():
                assert not (overlay / directory / filename).exists()

    def test_cleanup_removes_temporary_overlay(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        with create_ephemeral_overlay(workspace) as overlay:
            overlay_path = overlay
            assert overlay_path.exists()

        assert not overlay_path.exists()

    def test_cleanup_on_exception(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        overlay_path = workspace

        with pytest.raises(RuntimeError), create_ephemeral_overlay(workspace) as overlay:
            overlay_path = overlay
            raise RuntimeError("boom")

        assert not overlay_path.exists()

    def test_handles_empty_workspace(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        with create_ephemeral_overlay(workspace) as overlay:
            assert overlay.is_dir()
            assert overlay != workspace

    def test_includes_regular_repo_git_directory(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        git_dir = workspace / ".git"
        git_dir.mkdir(parents=True)
        (git_dir / "refs" / "heads").mkdir(parents=True)
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (git_dir / "config").write_text("[core]\n\trepositoryformatversion = 0\n", encoding="utf-8")

        with create_ephemeral_overlay(workspace) as overlay:
            assert (overlay / ".git").is_dir()
            head = (overlay / ".git" / "HEAD").read_text(encoding="utf-8")
            assert head == "ref: refs/heads/main\n"

    def test_worktree_git_file_creates_private_gitdir(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        shared_gitdir = tmp_path / "shared-git"
        worktree_gitdir = shared_gitdir / "worktrees" / "sample"
        worktree_gitdir.mkdir(parents=True)
        (shared_gitdir / "refs" / "heads").mkdir(parents=True)
        (shared_gitdir / "objects").mkdir(parents=True)
        (shared_gitdir / "refs" / "heads" / "main").write_text("abcdef\n", encoding="utf-8")
        (shared_gitdir / "packed-refs").write_text("# packed refs\n", encoding="utf-8")
        (worktree_gitdir / "commondir").write_text("../..\n", encoding="utf-8")
        (worktree_gitdir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (worktree_gitdir / "index").write_bytes(b"index-bytes")
        (worktree_gitdir / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n\tbare = false\n",
            encoding="utf-8",
        )
        (workspace / ".git").write_text(f"gitdir: {worktree_gitdir}\n", encoding="utf-8")
        (workspace / "note.txt").write_text("hello", encoding="utf-8")

        with create_ephemeral_overlay(workspace) as overlay:
            overlay_git = overlay / ".git"
            assert overlay_git.is_file()
            private_gitdir = Path(overlay_git.read_text(encoding="utf-8").split(":", 1)[1].strip())
            assert private_gitdir.is_dir()
            assert not str(private_gitdir).startswith(str(shared_gitdir))
            assert (private_gitdir / "HEAD").read_text(encoding="utf-8") == "ref: refs/heads/main\n"
            assert (private_gitdir / "index").read_bytes() == b"index-bytes"
            refs_main = private_gitdir / "refs" / "heads" / "main"
            assert refs_main.read_text(encoding="utf-8") == "abcdef\n"
            alternates = private_gitdir / "objects" / "info" / "alternates"
            assert alternates.read_text(encoding="utf-8") == f"{shared_gitdir / 'objects'}\n"
            config_text = (private_gitdir / "config").read_text(encoding="utf-8")
            assert f"worktree = {overlay}" in config_text

    def test_malformed_worktree_git_pointer_falls_back_to_copied_file(
        self, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / ".git").write_text("not a gitdir pointer\n", encoding="utf-8")

        with create_ephemeral_overlay(workspace) as overlay:
            overlay_git = overlay / ".git"
            assert overlay_git.is_file()
            assert overlay_git.read_text(encoding="utf-8") == "not a gitdir pointer\n"


class TestRunCommandOverlayIntegration:
    def test_run_command_passes_overlay_cwd_to_runner(self, tmp_path: Path) -> None:
        seen_cwd: list[Path] = []
        overlay_dir = tmp_path / "overlay"
        overlay_dir.mkdir()

        def fake_runner(
            command: list[str], cwd: Path, timeout_seconds: float | None
        ) -> _CompletedProcessAdapter:
            seen_cwd.append(cwd)
            return _CompletedProcessAdapter(stdout=b"", stderr=b"", returncode=0)

        def fake_overlay(workspace_root: Path) -> AbstractContextManager[Path]:
            del workspace_root
            return nullcontext(overlay_dir)

        workspace = MockWorkspaceRoot(tmp_path)
        run_command(
            "echo",
            [],
            workspace,
            1000,
            deps=ExecRunDeps(runner=fake_runner, overlay_factory=fake_overlay),
        )

        assert len(seen_cwd) == 1
        assert seen_cwd[0] == overlay_dir

    def test_run_command_uses_real_overlay_by_default(self, tmp_path: Path) -> None:
        seen_cwd: list[Path] = []
        workspace = MockWorkspaceRoot(tmp_path)

        def fake_runner(
            command: list[str], cwd: Path, timeout_seconds: float | None
        ) -> _CompletedProcessAdapter:
            del command, timeout_seconds
            seen_cwd.append(cwd)
            return _CompletedProcessAdapter(stdout=b"", stderr=b"", returncode=0)

        run_command("echo", [], workspace, 1000, deps=ExecRunDeps(runner=fake_runner))

        assert len(seen_cwd) == 1
        assert seen_cwd[0] != tmp_path
        assert not seen_cwd[0].exists()


class TestRunCommandOverlaySubprocessE2E:
    @pytest.mark.subprocess_e2e
    def test_run_command_writes_only_in_overlay(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        target = workspace / "created.txt"
        script = (
            "from pathlib import Path; "
            "Path('created.txt').write_text('overlay', encoding='utf-8')"
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


@pytest.mark.timeout_seconds(30)
class TestOverlayIsolationEndToEnd:
    @pytest.mark.subprocess_e2e
    def test_overlay_keeps_source_workspace_unchanged(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "marker.txt").write_text("source", encoding="utf-8")
        script = (
            "from pathlib import Path; "
            "Path('marker.txt').write_text('overlay', encoding='utf-8')"
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

    @pytest.mark.subprocess_e2e
    def test_real_subprocess_rewrites_source_workspace_env_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        overlay_dir = tmp_path / "overlay"
        overlay_dir.mkdir()
        monkeypatch.setenv("RALPH_TEST_SOURCE_PATH", str(workspace))

        def fake_overlay(workspace_root: Path) -> AbstractContextManager[Path]:
            del workspace_root
            return nullcontext(overlay_dir)

        result = run_command(
            sys.executable,
            [
                "-c",
                (
                    "import os; "
                    "print(os.environ.get('RALPH_TEST_SOURCE_PATH', '')); "
                    "print(os.environ.get('PWD', ''))"
                ),
            ],
            workspace,
            5000,
            deps=ExecRunDeps(overlay_factory=fake_overlay),
        )

        source_path, pwd = result.stdout.decode(encoding="utf-8").splitlines()
        assert result.returncode == 0
        assert source_path == str(overlay_dir)
        assert pwd != str(workspace)
        assert pwd in {"", str(overlay_dir)}
