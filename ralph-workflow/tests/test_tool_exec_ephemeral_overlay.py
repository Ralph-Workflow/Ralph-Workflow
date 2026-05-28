"""Tests for ralph.mcp.tools.exec_overlay."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

import pytest

import ralph.mcp.tools._exec_completed_process as exec_completed_process
from ralph.mcp.tools import exec_overlay
from ralph.mcp.tools._exec_run_deps import ExecRunDeps
from ralph.mcp.tools.exec import run_command
from tests.mock_workspace_root import MockWorkspaceRoot


def test_mirrors_workspace_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "root.txt").write_text("root", encoding="utf-8")

    with exec_overlay.create_ephemeral_overlay(workspace) as overlay:
        assert overlay.is_dir()
        assert overlay != workspace
        assert (overlay / "root.txt").read_text(encoding="utf-8") == "root"


def test_yields_path_in_isolated_private_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with exec_overlay.create_ephemeral_overlay(workspace) as overlay:
        assert overlay.is_dir()
        assert overlay != workspace
        assert overlay.parent != workspace.parent


def test_create_ephemeral_overlay_yields_unique_temp_roots(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with exec_overlay.create_ephemeral_overlay(workspace) as overlay1:
        overlay1_path = overlay1
        overlay1_parent = overlay1.parent
        with exec_overlay.create_ephemeral_overlay(workspace) as overlay2:
            overlay2_path = overlay2
            overlay2_parent = overlay2.parent
            assert overlay1_path != overlay2_path
            assert overlay1_parent != overlay2_parent
            assert overlay1_path.is_dir()
            assert overlay2_path.is_dir()


def test_mirrors_nested_directories(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    nested_file = workspace / "nested" / "inner.txt"
    nested_file.parent.mkdir(parents=True)
    nested_file.write_text("inner", encoding="utf-8")

    with exec_overlay.create_ephemeral_overlay(workspace) as overlay:
        assert (overlay / "nested" / "inner.txt").read_text(encoding="utf-8") == "inner"


def test_dereferences_symlinked_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.txt"
    target.write_text("real content", encoding="utf-8")
    link = workspace / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not supported in this environment")

    with exec_overlay.create_ephemeral_overlay(workspace) as overlay:
        copied = overlay / "link.txt"
        assert copied.exists()
        assert not copied.is_symlink()
        assert copied.read_text(encoding="utf-8") == "real content"


def test_writes_through_copied_symlinks_do_not_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.txt"
    target.write_text("original", encoding="utf-8")
    link = workspace / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not supported in this environment")

    with exec_overlay.create_ephemeral_overlay(workspace) as overlay:
        (overlay / "link.txt").write_text("overlay", encoding="utf-8")

    assert target.read_text(encoding="utf-8") == "original"


def test_isolates_writes_from_real_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "file.txt").write_text("original", encoding="utf-8")

    with exec_overlay.create_ephemeral_overlay(workspace) as overlay:
        (overlay / "file.txt").write_text("modified", encoding="utf-8")
        (overlay / "new_file.txt").write_text("new", encoding="utf-8")

    assert (workspace / "file.txt").read_text(encoding="utf-8") == "original"
    assert not (workspace / "new_file.txt").exists()


def test_excludes_generated_dirs(tmp_path: Path) -> None:
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

    with exec_overlay.create_ephemeral_overlay(workspace) as overlay:
        for directory, filename in exclusions.items():
            assert not (overlay / directory / filename).exists()


def test_keeps_nested_submodule_like_content_while_skipping_worktree_dirs(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source_git = workspace / ".git"
    linked_admin = source_git / "worktrees" / "feature-a"
    linked_admin.mkdir(parents=True)
    (workspace / ".worktrees" / "feature-a").mkdir(parents=True)
    (workspace / ".worktrees" / "feature-a" / ".git").write_text(
        f"gitdir: {linked_admin}\n", encoding="utf-8"
    )
    (linked_admin / "gitdir").write_text(
        str(workspace / ".worktrees" / "feature-a" / ".git"), encoding="utf-8"
    )
    (workspace / ".worktrees" / "feature-a" / "draft.txt").write_text(
        "skip me", encoding="utf-8"
    )

    submodule_root = workspace / "vendor" / "submodule"
    submodule_root.mkdir(parents=True)
    (submodule_root / ".git").write_text(
        "gitdir: ../.git/modules/vendor/submodule\n", encoding="utf-8"
    )
    (submodule_root / "tracked.txt").write_text("keep me", encoding="utf-8")

    overlay = tmp_path / "overlay"
    exec_overlay._mirror_workspace(workspace, overlay)

    assert not (overlay / ".worktrees" / "feature-a").exists()
    assert not (overlay / ".worktrees").exists()
    assert (overlay / "vendor" / "submodule" / "tracked.txt").read_text(
        encoding="utf-8"
    ) == "keep me"
    assert (overlay / "vendor" / "submodule" / ".git").read_text(
        encoding="utf-8"
    ) == "gitdir: ../.git/modules/vendor/submodule\n"


def test_cleanup_removes_temporary_overlay(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with exec_overlay.create_ephemeral_overlay(workspace) as overlay:
        overlay_path = overlay
        assert overlay_path.exists()

    assert not overlay_path.exists()


def test_cleanup_on_exception(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    overlay_path = workspace

    with pytest.raises(RuntimeError), exec_overlay.create_ephemeral_overlay(workspace) as overlay:
        overlay_path = overlay
        raise RuntimeError("boom")

    assert not overlay_path.exists()


def test_create_ephemeral_overlay_prunes_stale_dead_owner_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    base = tmp_path / "exec-base"
    stale_dir = base / "stale-overlay"
    stale_ws = stale_dir / "ws"
    stale_ws.mkdir(parents=True)
    (stale_dir / ".ralph-exec-owner.json").write_text(json.dumps({"pid": -1}), encoding="utf-8")
    monkeypatch.setattr(exec_overlay, "_get_private_exec_base", lambda: base)

    with exec_overlay.create_ephemeral_overlay(workspace) as overlay:
        assert overlay.exists()

    assert not stale_dir.exists(), "dead-owner overlay should be pruned before creating a new one"


def test_create_ephemeral_overlay_keeps_live_owner_dir_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    base = tmp_path / "exec-base"
    active_dir = base / "active-overlay"
    active_ws = active_dir / "ws"
    active_ws.mkdir(parents=True)
    (active_dir / ".ralph-exec-owner.json").write_text(
        json.dumps({"pid": os.getpid()}), encoding="utf-8"
    )
    monkeypatch.setattr(exec_overlay, "_get_private_exec_base", lambda: base)

    with exec_overlay.create_ephemeral_overlay(workspace) as overlay:
        assert overlay.exists()
        assert active_dir.exists(), "live-owner overlay must remain available to active exec users"


def test_handles_empty_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with exec_overlay.create_ephemeral_overlay(workspace) as overlay:
        assert overlay.is_dir()
        assert overlay != workspace


def test_includes_regular_repo_git_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git_dir = workspace / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "objects").mkdir(parents=True)
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "config").write_text("[core]\n\trepositoryformatversion = 0\n", encoding="utf-8")
    (git_dir / "refs" / "heads" / "main").write_text("abcdef\n", encoding="utf-8")
    (workspace / "tracked.txt").write_text("hello\n", encoding="utf-8")

    with exec_overlay.create_ephemeral_overlay(workspace) as overlay:
        overlay_git = overlay / ".git"
        assert overlay_git.is_file()
        private_gitdir = Path(overlay_git.read_text(encoding="utf-8").split(":", 1)[1].strip())
        assert private_gitdir.is_dir()
        assert (private_gitdir / "HEAD").read_text(encoding="utf-8") == "ref: refs/heads/main\n"
        refs_main = private_gitdir / "refs" / "heads" / "main"
        assert refs_main.read_text(encoding="utf-8") == "abcdef\n"
        alternates = private_gitdir / "objects" / "info" / "alternates"
        assert alternates.read_text(encoding="utf-8") == f"{git_dir / 'objects'}\n"


def test_worktree_git_file_creates_private_gitdir(tmp_path: Path) -> None:
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

    with exec_overlay.create_ephemeral_overlay(workspace) as overlay:
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


def test_malformed_worktree_git_pointer_falls_back_to_copied_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").write_text("not a gitdir pointer\n", encoding="utf-8")

    with exec_overlay.create_ephemeral_overlay(workspace) as overlay:
        overlay_git = overlay / ".git"
        assert overlay_git.is_file()
        assert overlay_git.read_text(encoding="utf-8") == "not a gitdir pointer\n"


def test_windows_uses_localappdata(tmp_path: Path) -> None:
    local_app = str(tmp_path / "AppData" / "Local")
    result = exec_overlay._compute_exec_base_str("nt", local_app, str(tmp_path / "home"), "/tmp")
    assert result == str(Path(local_app) / "ralph" / "exec")


def test_windows_falls_back_to_home_when_localappdata_absent(tmp_path: Path) -> None:
    home_str = str(tmp_path / "home" / "user")
    result = exec_overlay._compute_exec_base_str("nt", None, home_str, "/tmp")
    assert result == str(Path(home_str) / "ralph" / "exec")


def test_posix_normal_uses_home_cache(tmp_path: Path) -> None:
    fake_home = tmp_path / "home" / "user"
    fake_home.mkdir(parents=True)
    system_tmp = tmp_path / "system-tmp"
    system_tmp.mkdir()
    result = exec_overlay._compute_exec_base_str("posix", None, str(fake_home), str(system_tmp))
    assert result == str(fake_home / ".cache" / "ralph" / "exec")


def test_posix_home_cache_in_temp_uses_var_tmp(tmp_path: Path) -> None:
    fake_home = tmp_path / "user"
    fake_home.mkdir()
    result = exec_overlay._compute_exec_base_str("posix", None, str(fake_home), str(tmp_path))
    assert result == str(Path("/var/tmp") / "ralph" / "exec")


@pytest.mark.timeout_seconds(30)
def test_overlay_base_dir_has_private_permissions() -> None:
    base = exec_overlay._get_private_exec_base()

    assert base.exists(), "overlay base directory should exist"
    if os.name != "nt":
        mode = base.stat().st_mode & 0o777
        assert mode == 0o700, f"Expected 0o700, got 0o{mode:03o}"
    else:
        assert base.is_dir()


@pytest.mark.subprocess_e2e
def test_overlay_cwd_is_not_in_system_temp_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_command(
        sys.executable,
        ["-c", "import os; print(os.getcwd())"],
        workspace,
        5_000,
    )

    assert result.returncode == 0
    overlay_cwd = Path(result.stdout.decode().strip())
    tmp_dir = Path(os.path.realpath(tempfile.gettempdir()))

    assert not overlay_cwd.is_relative_to(tmp_dir), (
        f"Overlay CWD {overlay_cwd!r} is inside system temp {tmp_dir!r}. "
        "create_ephemeral_overlay must place overlays in a private directory so "
        "scanning the system temp dir finds nothing during exec."
    )

@pytest.mark.timeout_seconds(5)
@pytest.mark.subprocess_e2e
def test_overlay_worktree_is_cleaned_up_after_exec(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_command(
        sys.executable,
        ["-c", "import os; print(os.getcwd())"],
        workspace,
        5_000,
    )

    assert result.returncode == 0
    overlay_cwd = Path(result.stdout.decode().strip())
    assert not overlay_cwd.exists(), (
        f"Overlay worktree {overlay_cwd!r} still exists after exec returned. "
        "The reusable sandbox must clear the worktree on context exit so exec "
        "output does not persist on disk between runs."
    )


def test_run_command_passes_overlay_cwd_to_runner(tmp_path: Path) -> None:
    seen_cwd: list[Path] = []
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()

    def fake_runner(
        command: list[str], cwd: Path, timeout_seconds: float | None
    ) -> exec_completed_process._CompletedProcessAdapter:
        del command, timeout_seconds
        seen_cwd.append(cwd)
        return exec_completed_process._CompletedProcessAdapter(stdout=b"", stderr=b"", returncode=0)

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


def test_run_command_uses_real_overlay_by_default(tmp_path: Path) -> None:
    seen_cwd: list[Path] = []
    workspace = MockWorkspaceRoot(tmp_path)

    def fake_runner(
        command: list[str], cwd: Path, timeout_seconds: float | None
    ) -> exec_completed_process._CompletedProcessAdapter:
        del command, timeout_seconds
        seen_cwd.append(cwd)
        return exec_completed_process._CompletedProcessAdapter(stdout=b"", stderr=b"", returncode=0)

    run_command("echo", [], workspace, 1000, deps=ExecRunDeps(runner=fake_runner))

    assert len(seen_cwd) == 1
    assert seen_cwd[0] != tmp_path
    assert not seen_cwd[0].exists()


@pytest.mark.subprocess_e2e
def test_run_command_writes_only_in_overlay(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "created.txt"
    script = "from pathlib import Path; Path('created.txt').write_text('overlay', encoding='utf-8')"

    result = run_command("python", ["-c", script], workspace, 5000)

    assert result.returncode == 0
    assert not target.exists()


@pytest.mark.subprocess_e2e
def test_git_reset_happens_only_in_overlay(tmp_git_repo: Path) -> None:
    readme = tmp_git_repo / "README.md"
    original = readme.read_text(encoding="utf-8")
    readme.write_text("modified in source copy\n", encoding="utf-8")

    result = run_command("git", ["reset", "--hard", "HEAD"], tmp_git_repo, 5000)

    assert result.returncode == 0
    assert readme.read_text(encoding="utf-8") == "modified in source copy\n"
    assert readme.read_text(encoding="utf-8") != original


@pytest.mark.timeout_seconds(30)
@pytest.mark.subprocess_e2e
def test_overlay_keeps_source_workspace_unchanged(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "marker.txt").write_text("source", encoding="utf-8")
    script = "from pathlib import Path; Path('marker.txt').write_text('overlay', encoding='utf-8')"

    result = run_command("python", ["-c", script], workspace, 5000)

    assert result.returncode == 0
    assert (workspace / "marker.txt").read_text(encoding="utf-8") == "source"

@pytest.mark.timeout_seconds(5)
@pytest.mark.subprocess_e2e
def test_overlay_can_run_git_status_without_touching_source(tmp_git_repo: Path) -> None:
    before = (tmp_git_repo / "README.md").read_text(encoding="utf-8")

    result = run_command("git", ["status", "--short"], tmp_git_repo, 5000)

    assert result.returncode == 0
    assert (tmp_git_repo / "README.md").read_text(encoding="utf-8") == before


@pytest.mark.subprocess_e2e
def test_real_subprocess_rewrites_source_workspace_env_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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


@pytest.mark.timeout_seconds(30)
@pytest.mark.subprocess_e2e
def test_background_process_is_killed_after_exec_returns(tmp_path: Path) -> None:
    psutil = pytest.importorskip("psutil")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = (
        "import sys; "
        "p = getattr(__import__('subprocess'), 'Popen')("
        "    [sys.executable, '-c', 'import time; time.sleep(60)'], "
        "    stdout=getattr(__import__('subprocess'), 'DEVNULL'), "
        "    stderr=getattr(__import__('subprocess'), 'DEVNULL')"
        "); "
        "print(p.pid)"
    )

    result = run_command(sys.executable, ["-c", script], workspace, 10_000)
    assert result.returncode == 0
    child_pid = int(result.stdout.decode().strip())

    try:
        _, alive = psutil.wait_procs([psutil.Process(child_pid)], timeout=2.0)
        assert not alive, (
            f"Background process {child_pid} still alive after exec returned; "
            "_cleanup_exec_orphans must kill it."
        )
    except psutil.NoSuchProcess:
        pass

@pytest.mark.timeout_seconds(5)
@pytest.mark.subprocess_e2e
def test_new_session_background_process_is_killed_after_exec_returns(tmp_path: Path) -> None:
    psutil = pytest.importorskip("psutil")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = (
        "import sys, time; "
        "mod = __import__('subprocess'); "
        "p = getattr(mod, 'Popen')("
        "    [sys.executable, '-c', 'import time; time.sleep(60)'], "
        "    start_new_session=True, "
        "    stdout=getattr(mod, 'DEVNULL'), "
        "    stderr=getattr(mod, 'DEVNULL')"
        "); "
        "time.sleep(0.1); "
        "print(p.pid)"
    )

    result = run_command(sys.executable, ["-c", script], workspace, 10_000)
    assert result.returncode == 0
    child_pid = int(result.stdout.decode().strip())

    try:
        _, alive = psutil.wait_procs([psutil.Process(child_pid)], timeout=2.0)
        assert not alive, (
            f"Background process {child_pid} still alive after exec returned; "
            "start_new_session=True means os.killpg alone is insufficient and "
            "communicate_and_cleanup psutil snapshot must handle this escaped case."
        )
    except psutil.NoSuchProcess:
        pass
