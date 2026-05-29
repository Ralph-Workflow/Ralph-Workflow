from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from ralph.mcp.tools import exec_sandbox


def test_round_robin_cycles_through_slots(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "f.txt").write_text("content", encoding="utf-8")
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base", max_slots=3
    )

    with manager.acquire(workspace) as s0:
        assert s0.name == "ws"
        assert s0.parent.name == "s0"
        assert (s0 / "f.txt").read_text(encoding="utf-8") == "content"

    with manager.acquire(workspace) as s1:
        assert s1.parent.name == "s1"

    with manager.acquire(workspace) as s2:
        assert s2.parent.name == "s2"

    with manager.acquire(workspace) as s3:
        assert s3.parent.name == "s0"


def test_rsync_cleans_up_mutations_from_previous_exec(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    with manager.acquire(workspace) as sandbox:
        (sandbox / "dirty.txt").write_text("stale", encoding="utf-8")

    with manager.acquire(workspace) as same_slot:
        assert not (same_slot / "dirty.txt").exists()


def test_repopulates_from_workspace_on_each_acquire(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "tracked.txt").write_text("v1", encoding="utf-8")
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    with manager.acquire(workspace) as sandbox:
        assert (sandbox / "tracked.txt").read_text(encoding="utf-8") == "v1"

    (workspace / "tracked.txt").write_text("v2", encoding="utf-8")

    with manager.acquire(workspace) as sandbox:
        assert (sandbox / "tracked.txt").read_text(encoding="utf-8") == "v2"


def test_workspace_files_persist_across_rounds(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("keeper", encoding="utf-8")
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base", max_slots=3
    )

    with manager.acquire(workspace) as first:
        pass

    with manager.acquire(workspace) as _:
        pass
    with manager.acquire(workspace) as _:
        pass

    with manager.acquire(workspace) as same_slot:
        assert same_slot == first
        assert (same_slot / "keep.txt").read_text(encoding="utf-8") == "keeper"


def test_different_workspaces_use_different_pools(tmp_path: Path) -> None:
    ws_a = tmp_path / "a"
    ws_b = tmp_path / "b"
    ws_a.mkdir()
    ws_b.mkdir()
    (ws_a / "a.txt").write_text("a", encoding="utf-8")
    (ws_b / "b.txt").write_text("b", encoding="utf-8")
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    with manager.acquire(ws_a) as sa:
        assert (sa / "a.txt").exists()
        assert not (sa / "b.txt").exists()

    with manager.acquire(ws_b) as sb:
        assert (sb / "b.txt").exists()
        assert not (sb / "a.txt").exists()
        assert sb.parent.parent != sa.parent.parent


def test_concurrent_acquires_get_different_slots(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    results: list[Path] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def acquire_and_record() -> None:
        with manager.acquire(workspace) as sandbox:
            with lock:
                results.append(sandbox)
            barrier.wait(timeout=5.0)

    t1 = threading.Thread(target=acquire_and_record)
    t2 = threading.Thread(target=acquire_and_record)
    t1.start()
    t2.start()
    t1.join(timeout=2)
    t2.join(timeout=2)

    assert len(results) == 2
    assert results[0] != results[1]


def test_git_isolation_preserved(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    (workspace / ".git" / "HEAD").write_text(
        "ref: refs/heads/main\n", encoding="utf-8"
    )
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    with manager.acquire(workspace) as sandbox:
        overlay_git = sandbox / ".git"
        assert overlay_git.is_file()
        gitdir_text = overlay_git.read_text(encoding="utf-8")
        assert gitdir_text.startswith("gitdir:")


def test_cleanup_base_is_throttled_on_rapid_calls(tmp_path: Path) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    call_count = 0
    original = manager._cleanup_base_locked

    def counting_cleanup() -> exec_sandbox.ExecCacheCleanupSummary:
        nonlocal call_count
        call_count += 1
        return original()

    t0 = 1000.0
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(manager, "_cleanup_base_locked", counting_cleanup)
    monkeypatch.setattr(exec_sandbox.time, "monotonic", lambda: t0)

    first = manager.cleanup_base()
    assert call_count == 1
    assert first.removed_paths >= 0

    t0 += 1.0
    manager.cleanup_base()
    assert call_count == 1

    t0 += 60.0
    manager.cleanup_base()
    assert call_count == 2

    monkeypatch.undo()


def test_cleanup_base_runs_when_not_throttled(tmp_path: Path) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    first = manager.cleanup_base()
    assert first.removed_paths >= 0
    assert first.remaining_bytes >= 0


def test_compute_workspace_size_bytes_returns_total_file_sizes(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "a.txt").write_text("hello")
    (workspace / "b.txt").write_text("world!")
    sub = workspace / "sub"
    sub.mkdir()
    (sub / "c.txt").write_text("x")
    assert exec_sandbox._compute_workspace_size_bytes(workspace) == 12


def test_compute_workspace_size_bytes_excludes_generated_dirs(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "src.txt").write_text("abc")
    pycache = workspace / "__pycache__"
    pycache.mkdir()
    (pycache / "cached.pyc").write_text("ignored")
    node_modules = workspace / "node_modules"
    node_modules.mkdir()
    (node_modules / "big.js").write_text("xxxxxxxx")
    assert exec_sandbox._compute_workspace_size_bytes(workspace) == 3


def test_compute_workspace_size_bytes_handles_empty_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    assert exec_sandbox._compute_workspace_size_bytes(workspace) == 0


def test_manager_uses_cached_workspace_size_when_provided(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        workspace_size_bytes=42,
    )
    assert manager._workspace_size_bytes(workspace) == 42


def test_manager_falls_back_to_compute_when_no_cache(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "f.txt").write_text("hi")
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
    )
    assert manager._workspace_size_bytes(workspace) == 2


def test_path_size_bytes_via_du_falls_back_to_rglob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    test_dir = tmp_path / "sized"
    test_dir.mkdir()
    (test_dir / "a.txt").write_text("hello", encoding="utf-8")
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    monkeypatch.setattr(exec_sandbox.shutil, "which", lambda _cmd: None)
    size = manager._path_size_bytes_via_du(test_dir)
    assert size == 5


def test_path_size_bytes_via_du_uses_du_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    test_dir = tmp_path / "sized"
    test_dir.mkdir()
    (test_dir / "f.txt").write_text("x", encoding="utf-8")
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    monkeypatch.setattr(exec_sandbox.shutil, "which", lambda _cmd: "/usr/bin/du")

    def fake_run(*args: object, **kwargs: object) -> object:
        del args, kwargs

        class Result:
            returncode = 0
            stdout = "42\ttest-dir\n"

        return Result()

    monkeypatch.setattr(exec_sandbox.subprocess, "run", fake_run)
    size = manager._path_size_bytes_via_du(test_dir)
    assert size == 42 * 1024


def test_cleanup_base_removes_trash_dirs(tmp_path: Path) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    trash = manager._base_dir / ".ralph-exec-trash-test"
    trash.mkdir(parents=True)
    (trash / "junk.txt").write_text("junk", encoding="utf-8")
    assert trash.exists()

    manager.cleanup_base()
    assert not trash.exists()


def test_delete_expired_pool_does_not_walk_tree_before_rmtree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    pool_root = manager._base_dir / "dead-pool"
    pool_root.mkdir(parents=True)
    (pool_root / "junk.txt").write_text("junk", encoding="utf-8")

    path_size_called = False

    def tracking_path_size(path: Path) -> int:
        nonlocal path_size_called
        path_size_called = True
        return 0

    monkeypatch.setattr(manager, "_path_size_bytes", tracking_path_size)
    manager._delete_expired_pool_locked(pool_root, time.time() + 100_000)
    assert not path_size_called


def test_cleanup_per_pool_failure_does_not_block_other_pools(tmp_path: Path) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    pool_a = manager._base_dir / "pool-a"
    pool_b = manager._base_dir / "pool-b"
    pool_a.mkdir(parents=True)
    pool_b.mkdir(parents=True)
    (pool_a / "a.txt").write_text("a", encoding="utf-8")
    (pool_b / "b.txt").write_text("b", encoding="utf-8")

    original_iterdir = Path.iterdir

    def failing_iterdir(self: Path) -> object:
        if self == pool_a:
            raise OSError("simulated failure")
        return original_iterdir(self)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(Path, "iterdir", failing_iterdir)
    try:
        manager.cleanup_base()
    finally:
        monkeypatch.undo()
