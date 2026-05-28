from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

from ralph.mcp.tools import exec_sandbox

if TYPE_CHECKING:
    from pathlib import Path

_THREAD_JOIN_TIMEOUT_S = 2.0
_LOCK_HOLD_TIME_S = 0.05
_ENTER_WAIT_TIMEOUT_S = 1.0


def test_reusable_sandbox_reuses_stable_path_for_same_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    with manager.acquire(workspace) as sandbox1:
        first = sandbox1
        (sandbox1 / "mutated.txt").write_text("dirty", encoding="utf-8")

    with manager.acquire(workspace) as sandbox2:
        second = sandbox2
        assert second == first
        assert not (sandbox2 / "mutated.txt").exists()


def test_reusable_sandbox_rebuilds_when_validation_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    with manager.acquire(workspace) as sandbox:
        sentinel = sandbox.parent / ".ralph-sandbox-ready"

    sentinel.unlink()

    with manager.acquire(workspace) as sandbox2:
        assert (sandbox2.parent / ".ralph-sandbox-ready").exists()


def test_reusable_sandbox_repopulates_from_workspace_on_each_acquire(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "tracked.txt").write_text("source", encoding="utf-8")
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    with manager.acquire(workspace) as sandbox:
        assert (sandbox / "tracked.txt").read_text(encoding="utf-8") == "source"
        (sandbox / "tracked.txt").write_text("dirty", encoding="utf-8")

    with manager.acquire(workspace) as sandbox2:
        assert (sandbox2 / "tracked.txt").read_text(encoding="utf-8") == "source"


def test_same_workspace_concurrent_acquire_uses_distinct_pool_slots(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base", lock_timeout_s=0.01
    )
    first_entered = threading.Event()
    second_entered = threading.Event()
    release_first = threading.Event()
    seen_paths: list[Path] = []
    errors: list[BaseException] = []
    seen_lock = threading.Lock()

    def hold_lock(first: bool) -> None:
        try:
            with manager.acquire(workspace) as sandbox:
                with seen_lock:
                    seen_paths.append(sandbox)
                if first:
                    first_entered.set()
                    assert release_first.wait(timeout=_ENTER_WAIT_TIMEOUT_S)
                else:
                    second_entered.set()
        except BaseException as exc:  # pragma: no cover - captured for assertion clarity
            errors.append(exc)

    first = threading.Thread(target=hold_lock, args=(True,))
    second_started = threading.Event()

    def run_second() -> None:
        second_started.set()
        hold_lock(False)

    second = threading.Thread(target=run_second)

    first.start()
    try:
        assert first_entered.wait(timeout=_ENTER_WAIT_TIMEOUT_S)
        second.start()
        assert second_started.wait(timeout=_ENTER_WAIT_TIMEOUT_S)
        assert second_entered.wait(timeout=_LOCK_HOLD_TIME_S)
        release_first.set()
    finally:
        first.join(timeout=_THREAD_JOIN_TIMEOUT_S)
        second.join(timeout=_THREAD_JOIN_TIMEOUT_S)

    assert not errors
    assert second_entered.is_set()
    assert len(seen_paths) == 2
    assert seen_paths[0] != seen_paths[1]


def test_same_workspace_pool_persists_learned_target_size(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base", lock_timeout_s=0.01
    )
    release_first = threading.Event()
    first_entered = threading.Event()
    second_entered = threading.Event()
    errors: list[BaseException] = []

    def acquire_slot(first: bool) -> None:
        try:
            with manager.acquire(workspace):
                if first:
                    first_entered.set()
                    assert release_first.wait(timeout=_ENTER_WAIT_TIMEOUT_S)
                else:
                    second_entered.set()
        except BaseException as exc:  # pragma: no cover - captured for assertion clarity
            errors.append(exc)

    first = threading.Thread(target=acquire_slot, args=(True,))
    second = threading.Thread(target=acquire_slot, args=(False,))
    first.start()
    assert first_entered.wait(timeout=_ENTER_WAIT_TIMEOUT_S)
    second.start()
    assert second_entered.wait(timeout=_ENTER_WAIT_TIMEOUT_S)
    release_first.set()
    first.join(timeout=_THREAD_JOIN_TIMEOUT_S)
    second.join(timeout=_THREAD_JOIN_TIMEOUT_S)

    assert not errors
    pool_root = manager._base_dir / exec_sandbox._workspace_key(workspace)
    state = json.loads((pool_root / ".ralph-sandbox-pool.json").read_text(encoding="utf-8"))
    assert state["target_slots"] >= 2


def test_same_workspace_three_concurrent_acquires_get_three_distinct_slots(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base", lock_timeout_s=0.01
    )
    entered = [threading.Event(), threading.Event(), threading.Event()]
    release_all = threading.Event()
    seen_paths: list[Path] = []
    errors: list[BaseException] = []
    seen_lock = threading.Lock()

    def acquire_slot(index: int) -> None:
        try:
            with manager.acquire(workspace) as sandbox:
                with seen_lock:
                    seen_paths.append(sandbox)
                entered[index].set()
                assert release_all.wait(timeout=_ENTER_WAIT_TIMEOUT_S)
        except BaseException as exc:  # pragma: no cover - captured for assertion clarity
            errors.append(exc)

    threads = [threading.Thread(target=acquire_slot, args=(idx,)) for idx in range(3)]
    for thread in threads:
        thread.start()
    try:
        for event in entered:
            assert event.wait(timeout=_ENTER_WAIT_TIMEOUT_S)
    finally:
        release_all.set()
        for thread in threads:
            thread.join(timeout=_THREAD_JOIN_TIMEOUT_S)

    assert not errors
    assert len(seen_paths) == 3
    assert len(set(seen_paths)) == 3


def test_same_workspace_concurrent_growth_persists_high_water_target_size(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base", lock_timeout_s=0.01
    )
    entered = [threading.Event(), threading.Event(), threading.Event()]
    release_all = threading.Event()
    errors: list[BaseException] = []

    def acquire_slot(index: int) -> None:
        try:
            with manager.acquire(workspace):
                entered[index].set()
                assert release_all.wait(timeout=_ENTER_WAIT_TIMEOUT_S)
        except BaseException as exc:  # pragma: no cover - captured for assertion clarity
            errors.append(exc)

    threads = [threading.Thread(target=acquire_slot, args=(idx,)) for idx in range(3)]
    for thread in threads:
        thread.start()
    try:
        for event in entered:
            assert event.wait(timeout=_ENTER_WAIT_TIMEOUT_S)
    finally:
        release_all.set()
        for thread in threads:
            thread.join(timeout=_THREAD_JOIN_TIMEOUT_S)

    assert not errors
    pool_root = manager._base_dir / exec_sandbox._workspace_key(workspace)
    state = json.loads((pool_root / ".ralph-sandbox-pool.json").read_text(encoding="utf-8"))
    assert state["target_slots"] >= 3


def test_reusable_sandbox_prunes_stale_dead_owner_slot_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    workspace_key = exec_sandbox._workspace_key(workspace)
    pool_root = manager._base_dir / workspace_key
    stale_slot = manager._slot_root(pool_root, workspace_key, 2)
    stale_slot.mkdir(parents=True)
    (stale_slot / ".ralph-exec-owner.json").write_text(
        json.dumps({"pid": -1}), encoding="utf-8"
    )

    with manager.acquire(workspace) as sandbox:
        assert sandbox.exists()

    assert not stale_slot.exists()


def test_reusable_sandbox_reclaims_stale_pool_lock(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    workspace_key = exec_sandbox._workspace_key(workspace)
    pool_root = manager._base_dir / workspace_key
    pool_root.mkdir(parents=True, exist_ok=True)
    manager._pool_lock_path(pool_root).write_text(
        json.dumps({"pid": -1}), encoding="utf-8"
    )

    with manager.acquire(workspace) as sandbox:
        assert sandbox.exists()


def test_reusable_sandbox_reclaims_ownerless_stale_locked_slot(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    workspace_key = exec_sandbox._workspace_key(workspace)
    pool_root = manager._base_dir / workspace_key
    stale_slot = manager._slot_root(pool_root, workspace_key, 1)
    stale_slot.mkdir(parents=True)
    manager._lock_path(stale_slot).write_text(json.dumps({"pid": -1}), encoding="utf-8")

    with manager.acquire(workspace) as sandbox:
        assert sandbox == stale_slot / "ws"


def test_same_workspace_pool_shrinks_idle_extra_slots(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base", lock_timeout_s=0.01
    )
    pool_root = manager._base_dir / exec_sandbox._workspace_key(workspace)
    pool_root.mkdir(parents=True, exist_ok=True)
    (pool_root / ".ralph-sandbox-pool.json").write_text(
        json.dumps({"target_slots": 2, "average_slots": 2.0}), encoding="utf-8"
    )
    slot_two = manager._slot_root(pool_root, exec_sandbox._workspace_key(workspace), 2)
    slot_two.mkdir(parents=True)

    with manager.acquire(workspace):
        pass
    with manager.acquire(workspace):
        pass

    state = json.loads((pool_root / ".ralph-sandbox-pool.json").read_text(encoding="utf-8"))
    assert state["target_slots"] == 1
    assert not slot_two.exists()


def test_different_workspaces_can_acquire_independently(tmp_path: Path) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base", lock_timeout_s=0.01
    )

    with (
        manager.acquire(workspace_a) as sandbox_a,
        manager.acquire(workspace_b) as sandbox_b,
    ):
        assert sandbox_a != sandbox_b


def test_reusable_sandbox_uses_fast_reset_when_previous_state_is_valid(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls: list[str] = []

    class RecordingManager(exec_sandbox.ExecSandboxManager):
        def _fast_reset(
            self, workspace_root: Path, sandbox_root: Path, worktree: Path
        ) -> bool:
            del workspace_root, sandbox_root, worktree
            calls.append("fast")
            return True

        def _full_reset(
            self, workspace_root: Path, sandbox_root: Path, worktree: Path
        ) -> None:
            calls.append("full")
            super()._full_reset(workspace_root, sandbox_root, worktree)

    manager = RecordingManager(base_dir=tmp_path / "exec-base")
    with manager.acquire(workspace):
        pass
    with manager.acquire(workspace):
        pass

    assert calls == ["full", "fast"]


def test_reusable_sandbox_preserves_git_isolation_for_regular_repo(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git_dir = workspace / ".git"
    git_dir.mkdir()
    (git_dir / "objects").mkdir()
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "refs" / "heads" / "main").write_text("abcdef\n", encoding="utf-8")

    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    with manager.acquire(workspace) as sandbox:
        overlay_git = sandbox / ".git"
        assert overlay_git.is_file()
