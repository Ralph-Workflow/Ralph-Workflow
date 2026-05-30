from __future__ import annotations

import json
import os
import threading
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from ralph.mcp.tools import exec_sandbox

_THREAD_JOIN_TIMEOUT_S = 2.0
_LOCK_HOLD_TIME_S = 0.2
_ENTER_WAIT_TIMEOUT_S = 2.0


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


@pytest.mark.timeout_seconds(2)
def test_same_workspace_concurrent_acquire_uses_distinct_pool_slots(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base", lock_timeout_s=0.01)
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
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base", lock_timeout_s=0.01)
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


@pytest.mark.timeout_seconds(2)
def test_same_workspace_three_concurrent_acquires_get_three_distinct_slots(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base", lock_timeout_s=0.01)
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


@pytest.mark.timeout_seconds(5)
def test_same_workspace_concurrent_growth_persists_high_water_target_size(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base", lock_timeout_s=0.01)
    enter_timeout_s = 5.0
    entered = [threading.Event(), threading.Event(), threading.Event()]
    release_all = threading.Event()
    errors: list[BaseException] = []

    def acquire_slot(index: int) -> None:
        try:
            with manager.acquire(workspace):
                entered[index].set()
                assert release_all.wait(timeout=enter_timeout_s)
        except BaseException as exc:  # pragma: no cover - captured for assertion clarity
            errors.append(exc)

    threads = [threading.Thread(target=acquire_slot, args=(idx,)) for idx in range(3)]
    for thread in threads:
        thread.start()
    try:
        for event in entered:
            assert event.wait(timeout=enter_timeout_s)
    finally:
        release_all.set()
        for thread in threads:
            thread.join(timeout=_THREAD_JOIN_TIMEOUT_S)

    assert not errors
    pool_root = manager._base_dir / exec_sandbox._workspace_key(workspace)
    state = json.loads((pool_root / ".ralph-sandbox-pool.json").read_text(encoding="utf-8"))
    assert state["target_slots"] >= 3


def test_same_workspace_concurrency_is_bounded_by_max_slots(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base", lock_timeout_s=0.01, max_slots=2
    )
    pool_root = manager._base_dir / exec_sandbox._workspace_key(workspace)
    pool_root.mkdir(parents=True, exist_ok=True)
    pid, started_at = exec_sandbox._current_process_identity()
    payload: dict[str, int | float] = {"pid": pid}
    if started_at is not None:
        payload["started_at"] = started_at
    for slot_index in range(1, 3):
        slot_root = manager._slot_root(
            pool_root, exec_sandbox._workspace_key(workspace), slot_index
        )
        slot_root.mkdir(parents=True, exist_ok=True)
        manager._lock_path(slot_root).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(exec_sandbox.ExecSandboxBusyError), manager.acquire(workspace):
        pass

    assert len(list(pool_root.glob("slot-*"))) == 2


def test_acquire_prunes_stale_workspace_pool_for_other_workspace(tmp_path: Path) -> None:
    active_workspace = tmp_path / "active-workspace"
    active_workspace.mkdir()
    stale_workspace = tmp_path / "deleted-workspace"
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    stale_workspace_key = exec_sandbox._workspace_key(stale_workspace)
    stale_pool_root = manager._base_dir / stale_workspace_key
    stale_slot = manager._slot_root(stale_pool_root, stale_workspace_key, 1)
    stale_slot.mkdir(parents=True)
    (stale_slot / ".ralph-exec-owner.json").write_text(
        json.dumps({"pid": -1}), encoding="utf-8"
    )

    with manager.acquire(active_workspace) as sandbox:
        assert sandbox.exists()

    assert not stale_pool_root.exists()


def test_acquire_keeps_other_workspace_pool_with_live_pool_lock(tmp_path: Path) -> None:
    active_workspace = tmp_path / "active-workspace"
    active_workspace.mkdir()
    locked_workspace = tmp_path / "locked-workspace"
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    locked_workspace_key = exec_sandbox._workspace_key(locked_workspace)
    locked_pool_root = manager._base_dir / locked_workspace_key
    locked_slot = manager._slot_root(locked_pool_root, locked_workspace_key, 1)
    locked_slot.mkdir(parents=True)
    (locked_slot / ".ralph-exec-owner.json").write_text(
        json.dumps({"pid": -1}), encoding="utf-8"
    )
    pid, started_at = exec_sandbox._current_process_identity()
    payload: dict[str, int | float] = {"pid": pid}
    if started_at is not None:
        payload["started_at"] = started_at
    manager._pool_lock_path(locked_pool_root).write_text(
        json.dumps(payload), encoding="utf-8"
    )

    with manager.acquire(active_workspace) as sandbox:
        assert sandbox.exists()

    assert locked_pool_root.exists()


def test_acquire_keeps_empty_other_workspace_pool_with_live_pool_lock(
    tmp_path: Path,
) -> None:
    active_workspace = tmp_path / "active-workspace"
    active_workspace.mkdir()
    locked_workspace = tmp_path / "locked-workspace"
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    locked_workspace_key = exec_sandbox._workspace_key(locked_workspace)
    locked_pool_root = manager._base_dir / locked_workspace_key
    locked_pool_root.mkdir(parents=True)
    pid, started_at = exec_sandbox._current_process_identity()
    payload: dict[str, int | float] = {"pid": pid}
    if started_at is not None:
        payload["started_at"] = started_at
    manager._pool_lock_path(locked_pool_root).write_text(
        json.dumps(payload), encoding="utf-8"
    )

    with manager.acquire(active_workspace) as sandbox:
        assert sandbox.exists()

    assert locked_pool_root.exists()


def test_acquire_keeps_other_workspace_pool_when_pool_lock_cannot_be_acquired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active_workspace = tmp_path / "active-workspace"
    active_workspace.mkdir()
    locked_workspace = tmp_path / "locked-workspace"
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    locked_workspace_key = exec_sandbox._workspace_key(locked_workspace)
    locked_pool_root = manager._base_dir / locked_workspace_key
    locked_pool_root.mkdir(parents=True)

    original_try_acquire_named_lock = manager._try_acquire_named_lock

    def fake_try_acquire_named_lock(lock_path: Path) -> bool:
        if lock_path == manager._pool_lock_path(locked_pool_root):
            return False
        return original_try_acquire_named_lock(lock_path)

    monkeypatch.setattr(manager, "_try_acquire_named_lock", fake_try_acquire_named_lock)

    with manager.acquire(active_workspace) as sandbox:
        assert sandbox.exists()

    assert locked_pool_root.exists()


def test_reusable_sandbox_prunes_stale_dead_owner_slot_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    workspace_key = exec_sandbox._workspace_key(workspace)
    pool_root = manager._base_dir / workspace_key
    stale_slot = manager._slot_root(pool_root, workspace_key, 2)
    stale_slot.mkdir(parents=True)
    (stale_slot / ".ralph-exec-owner.json").write_text(json.dumps({"pid": -1}), encoding="utf-8")

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
    manager._pool_lock_path(pool_root).write_text(json.dumps({"pid": -1}), encoding="utf-8")

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


def test_reusable_sandbox_reclaims_lock_when_process_identity_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    slot_root = tmp_path / "exec-base" / "slot"
    slot_root.mkdir(parents=True)
    lock_path = manager._lock_path(slot_root)
    lock_path.write_text(
        json.dumps({"pid": 123, "started_at": 10.0}), encoding="utf-8"
    )
    monkeypatch.setattr(
        exec_sandbox,
        "_process_identity_matches",
        lambda pid, started_at: pid == 123 and started_at == 20.0,
    )

    assert manager._reclaim_stale_lock(lock_path) is True
    assert not lock_path.exists()


def test_reusable_sandbox_keeps_lock_when_process_identity_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    slot_root = tmp_path / "exec-base" / "slot"
    slot_root.mkdir(parents=True)
    lock_path = manager._lock_path(slot_root)
    lock_path.write_text(
        json.dumps({"pid": 123, "started_at": 10.0}), encoding="utf-8"
    )
    monkeypatch.setattr(
        exec_sandbox,
        "_process_identity_matches",
        lambda pid, started_at: pid == 123 and started_at == 10.0,
    )

    assert manager._reclaim_stale_lock(lock_path) is False
    assert lock_path.exists()


def test_same_workspace_pool_shrinks_idle_extra_slots(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base", lock_timeout_s=0.01)
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


def test_same_workspace_acquire_does_not_fail_while_idle_slot_prune_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base", lock_timeout_s=0.01
    )
    pool_root = manager._base_dir / exec_sandbox._workspace_key(workspace)
    pool_root.mkdir(parents=True, exist_ok=True)
    (pool_root / ".ralph-sandbox-pool.json").write_text(
        json.dumps({"target_slots": 1, "average_slots": 1.25, "low_usage_streak": 2}),
        encoding="utf-8",
    )
    slot_two = manager._slot_root(pool_root, exec_sandbox._workspace_key(workspace), 2)
    slot_two.mkdir(parents=True)
    (slot_two / "payload.txt").write_text("stale", encoding="utf-8")

    prune_started = threading.Event()
    release_prune = threading.Event()
    shrink_finished = threading.Event()
    errors: list[BaseException] = []
    original_rmtree = exec_sandbox.shutil.rmtree
    workspace_key = exec_sandbox._workspace_key(workspace)
    shrink_thread_name = "shrink-idle-slots"

    def blocking_rmtree(path: Path | str, ignore_errors: bool = False) -> None:
        if threading.current_thread().name == shrink_thread_name:
            prune_started.set()
            assert release_prune.wait(timeout=_ENTER_WAIT_TIMEOUT_S)
        original_rmtree(path, ignore_errors=ignore_errors)

    monkeypatch.setattr(exec_sandbox.shutil, "rmtree", blocking_rmtree)

    def shrink_idle_slots() -> None:
        try:
            manager._shrink_idle_slots(pool_root, workspace_key)
        except BaseException as exc:  # pragma: no cover - captured for assertion clarity
            errors.append(exc)
        finally:
            shrink_finished.set()

    thread = threading.Thread(target=shrink_idle_slots, name=shrink_thread_name)
    thread.start()
    assert prune_started.wait(timeout=_ENTER_WAIT_TIMEOUT_S)

    try:
        with manager.acquire(workspace) as sandbox:
            assert sandbox.exists()
    finally:
        release_prune.set()
        thread.join(timeout=_THREAD_JOIN_TIMEOUT_S)

    assert shrink_finished.is_set()
    assert not errors


def test_reusable_sandbox_self_heals_oversized_pool_state(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base", lock_timeout_s=0.01, max_slots=3
    )
    workspace_key = exec_sandbox._workspace_key(workspace)
    pool_root = manager._base_dir / workspace_key
    pool_root.mkdir(parents=True, exist_ok=True)
    (pool_root / ".ralph-sandbox-pool.json").write_text(
        json.dumps({"target_slots": 99, "average_slots": 99.0, "low_usage_streak": 0}),
        encoding="utf-8",
    )
    for slot_index in range(1, 7):
        slot_root = manager._slot_root(pool_root, workspace_key, slot_index)
        slot_root.mkdir(parents=True)

    with manager.acquire(workspace) as sandbox:
        assert sandbox.exists()

    state = json.loads((pool_root / ".ralph-sandbox-pool.json").read_text(encoding="utf-8"))
    assert state["target_slots"] == 3
    assert len(list(pool_root.glob("slot-*"))) <= 3


def test_reusable_sandbox_bounds_idle_workspace_pools_across_workspaces(
    tmp_path: Path,
) -> None:
    workspaces = [tmp_path / f"workspace-{index}" for index in range(1, 4)]
    for workspace in workspaces:
        workspace.mkdir()

    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        lock_timeout_s=0.01,
        max_workspace_pools=2,
    )

    for workspace in workspaces:
        with manager.acquire(workspace) as sandbox:
            assert sandbox.exists()

    pool_dirs = [
        child
        for child in manager._base_dir.iterdir()
        if child.is_dir() and not child.name.startswith(exec_sandbox._TRASH_PREFIX)
    ]
    assert len(pool_dirs) <= 2
    retained_keys = {pool_dir.name for pool_dir in pool_dirs}
    assert exec_sandbox._workspace_key(workspaces[-1]) in retained_keys


def test_cleanup_base_prunes_expired_current_workspace_idle_slot(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_idle_slot_age_s=1.0,
    )
    workspace_key = exec_sandbox._workspace_key(workspace)
    pool_root = manager._base_dir / workspace_key
    stale_slot = manager._slot_root(pool_root, workspace_key, 2)
    stale_slot_ws = stale_slot / "ws"
    stale_slot_ws.mkdir(parents=True)
    (stale_slot / ".ralph-sandbox-ready").write_text('{"ready": true}', encoding="utf-8")
    pid, started_at = exec_sandbox._current_process_identity()
    payload: dict[str, int | float] = {"pid": pid}
    if started_at is not None:
        payload["started_at"] = started_at
    (stale_slot / ".ralph-exec-owner.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    (stale_slot_ws / "payload.bin").write_bytes(b"stale")
    expired_at = 1.0
    os.utime(stale_slot, (expired_at, expired_at))
    os.utime(stale_slot_ws, (expired_at, expired_at))

    with manager.acquire(workspace) as sandbox:
        assert sandbox.exists()

    assert not stale_slot.exists()


def test_cleanup_base_enforces_total_byte_budget_across_idle_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_total_bytes=100,
        max_pool_bytes=1_024,
        max_idle_slot_age_s=60.0,
        max_idle_pool_age_s=60.0,
    )
    # Use the pure-Python size counter (file bytes only) for the tight budget
    # test; du -sk reports directory block overhead that would skew the micro-budget.
    monkeypatch.setattr(manager, "_path_size_bytes_via_du", manager._path_size_bytes)
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()

    def seed_idle_slot(workspace: Path, slot_index: int, size: int, mtime: float) -> Path:
        workspace_key = exec_sandbox._workspace_key(workspace)
        pool_root = manager._base_dir / workspace_key
        slot_root = manager._slot_root(pool_root, workspace_key, slot_index)
        ws_root = slot_root / "ws"
        ws_root.mkdir(parents=True)
        (slot_root / ".ralph-sandbox-ready").write_text('{"ready": true}', encoding="utf-8")
        pid, started_at = exec_sandbox._current_process_identity()
        payload: dict[str, int | float] = {"pid": pid}
        if started_at is not None:
            payload["started_at"] = started_at
        (slot_root / ".ralph-exec-owner.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        (ws_root / "payload.bin").write_bytes(b"x" * size)
        os.utime(slot_root, (mtime, mtime))
        os.utime(ws_root, (mtime, mtime))
        return slot_root

    now = tmp_path.stat().st_mtime
    oldest = seed_idle_slot(workspace_a, 1, 32, now - 2.0)
    newest = seed_idle_slot(workspace_b, 1, 32, now - 1.0)

    summary = manager.cleanup_base()

    assert summary.remaining_bytes <= 100
    assert not oldest.exists()
    assert newest.exists()


def test_reusable_sandbox_rejects_workspace_copy_over_hard_byte_cap(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "large.bin").write_bytes(b"0123456789")
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_workspace_bytes=4,
    )

    with pytest.raises(OSError, match="sandbox workspace exceeds safety limit"), manager.acquire(
        workspace
    ):
        pass

    assert not any(manager._base_dir.rglob("slot-*"))


def test_same_workspace_acquire_does_not_fail_while_stale_slot_cleanup_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base", lock_timeout_s=0.01
    )
    workspace_key = exec_sandbox._workspace_key(workspace)
    pool_root = manager._base_dir / workspace_key
    stale_slot = manager._slot_root(pool_root, workspace_key, 2)
    stale_slot.mkdir(parents=True)
    (stale_slot / ".ralph-exec-owner.json").write_text(
        json.dumps({"pid": -1}), encoding="utf-8"
    )
    (stale_slot / "payload.txt").write_text("stale", encoding="utf-8")

    cleanup_started = threading.Event()
    release_cleanup = threading.Event()
    background_finished = threading.Event()
    errors: list[BaseException] = []
    original_rmtree = exec_sandbox.shutil.rmtree
    cleanup_thread_name = "stale-slot-cleanup"

    def blocking_rmtree(path: Path | str, ignore_errors: bool = False) -> None:
        if threading.current_thread().name == cleanup_thread_name:
            cleanup_started.set()
            assert release_cleanup.wait(timeout=_ENTER_WAIT_TIMEOUT_S)
        original_rmtree(path, ignore_errors=ignore_errors)

    monkeypatch.setattr(exec_sandbox.shutil, "rmtree", blocking_rmtree)

    def acquire_in_background() -> None:
        try:
            with manager.acquire(workspace):
                pass
        except BaseException as exc:  # pragma: no cover - captured for assertion clarity
            errors.append(exc)
        finally:
            background_finished.set()

    thread = threading.Thread(target=acquire_in_background, name=cleanup_thread_name)
    thread.start()
    assert cleanup_started.wait(timeout=_ENTER_WAIT_TIMEOUT_S)

    try:
        with manager.acquire(workspace) as sandbox:
            assert sandbox.exists()
    finally:
        release_cleanup.set()
        thread.join(timeout=_THREAD_JOIN_TIMEOUT_S)

    assert background_finished.is_set()
    assert not errors


def test_different_workspaces_can_acquire_independently(tmp_path: Path) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base", lock_timeout_s=0.01)

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
        def _fast_reset(self, workspace_root: Path, sandbox_root: Path, worktree: Path) -> bool:
            del workspace_root, sandbox_root, worktree
            calls.append("fast")
            return True

        def _full_reset(self, workspace_root: Path, sandbox_root: Path, worktree: Path) -> None:
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


def test_cleanup_deletes_collectible_slots_when_path_size_bytes_is_slow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Collectible slots must be deleted even when _path_size_bytes is slow or broken.

    Regression: _path_size_bytes walks the full directory tree and can hang for
    minutes on 50 GB+ cache directories.  This test simulates that by making
    _path_size_bytes raise on the collectible slot — the slot should still be
    deleted because the byte-accounting is best-effort, not a prerequisite.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_idle_slot_age_s=60.0,
        max_idle_pool_age_s=60.0,
    )
    workspace_key = exec_sandbox._workspace_key(workspace)
    pool_root = manager._base_dir / workspace_key

    # Create two collectible stale slots (no ready sentinel, dead owner PID)
    slot_one = manager._slot_root(pool_root, workspace_key, 2)
    slot_two = manager._slot_root(pool_root, workspace_key, 3)
    for slot_root in (slot_one, slot_two):
        ws_dir = slot_root / "ws"
        ws_dir.mkdir(parents=True)
        (ws_dir / "payload.bin").write_bytes(b"stale-data")
        (slot_root / ".ralph-exec-owner.json").write_text(
            json.dumps({"pid": -1}), encoding="utf-8"
        )

    # Simulate _path_size_bytes being slow/broken on the stale slot:
    # it must NOT block deletion.
    original_path_size_bytes = manager._path_size_bytes
    called_on_paths: list[Path] = []

    def broken_path_size_bytes(path: Path) -> int:
        called_on_paths.append(path)
        # Raise only when called on one of the collectible slot dirs
        if path in (slot_one, slot_two):
            raise OSError("simulated slow/hung _path_size_bytes")
        return original_path_size_bytes(path)

    monkeypatch.setattr(manager, "_path_size_bytes", broken_path_size_bytes)

    with manager.acquire(workspace):
        pass

    # Both stale slots must be gone even though _path_size_bytes raised.
    assert not slot_one.exists(), f"{slot_one} should have been deleted"
    assert not slot_two.exists(), f"{slot_two} should have been deleted"


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


def test_cleanup_per_pool_failure_does_not_block_other_pools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure cleaning one pool must not prevent cleanup of other pools."""
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()

    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_idle_slot_age_s=60.0,
        max_idle_pool_age_s=60.0,
    )

    # Pool A: one collectible stale slot (will be made to fail)
    key_a = exec_sandbox._workspace_key(workspace_a)
    pool_a = manager._base_dir / key_a
    slot_a = manager._slot_root(pool_a, key_a, 2)
    slot_a.mkdir(parents=True)
    (slot_a / "ws").mkdir()
    (slot_a / ".ralph-exec-owner.json").write_text(
        json.dumps({"pid": -1}), encoding="utf-8"
    )

    # Pool B: two collectible stale slots (should be cleaned up)
    key_b = exec_sandbox._workspace_key(workspace_b)
    pool_b = manager._base_dir / key_b
    slot_b1 = manager._slot_root(pool_b, key_b, 2)
    slot_b2 = manager._slot_root(pool_b, key_b, 3)
    for slot_root in (slot_b1, slot_b2):
        slot_root.mkdir(parents=True)
        (slot_root / "ws").mkdir()
        (slot_root / ".ralph-exec-owner.json").write_text(
            json.dumps({"pid": -1}), encoding="utf-8"
        )

    # Make pool A's cleanup raise an unexpected error (e.g. OSError)
    original_prune = manager._prune_expired_and_broken_slots_locked

    def failing_prune(pool_root: Path, current_time: float) -> tuple:
        if pool_root == pool_a:
            raise OSError("simulated pool failure")
        return original_prune(pool_root, current_time)

    monkeypatch.setattr(
        manager, "_prune_expired_and_broken_slots_locked", failing_prune
    )

    manager.cleanup_base()

    # Pool B's slots should have been cleaned despite pool A's failure.
    assert not slot_b1.exists(), f"{slot_b1} in unaffected pool should be deleted"
    assert not slot_b2.exists(), f"{slot_b2} in unaffected pool should be deleted"

    # Pool A's slot may or may not be deleted depending on locking; the key
    # assertion is that pool B was processed and cleaned.


def test_cleanup_worktree_survives_rmtree_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_cleanup_worktree must not crash when rmtree fails with OSError."""
    worktree = tmp_path / "ws"
    worktree.mkdir(parents=True)
    (worktree / "file.txt").write_text("content", encoding="utf-8")

    # Make rmtree fail with PermissionError (a subclass of OSError)
    def failing_rmtree(path: str, ignore_errors: bool = False) -> None:
        del ignore_errors
        if str(path).endswith("/ws"):
            raise PermissionError("simulated permission error")
        exec_sandbox.shutil.rmtree(path)

    monkeypatch.setattr(exec_sandbox.shutil, "rmtree", failing_rmtree)

    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    # Must not raise — the suppressed exception should be caught.
    manager._cleanup_worktree(worktree)


def test_delete_expired_pool_does_not_walk_tree_before_rmtree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_delete_expired_pool_locked must not call _path_size_bytes before rmtree.

    Same bottleneck pattern as Bug 1 — walking a stale pool's entire tree
    to count bytes is unnecessary when the tree is about to be deleted.
    """
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_idle_pool_age_s=0.0,
    )
    pool_root = manager._base_dir / "dead-pool"
    slot_root = manager._slot_root(pool_root, "dead-pool", 1)
    (slot_root / "ws").mkdir(parents=True)
    (slot_root / "ws" / "payload.bin").write_bytes(b"x" * 1024)
    (slot_root / ".ralph-exec-owner.json").write_text(
        json.dumps({"pid": -1}), encoding="utf-8"
    )

    # _path_size_bytes must NOT be called on pool_root or slot_root
    called_paths: list[Path] = []
    original = manager._path_size_bytes

    def tracking_size_bytes(path: Path) -> int:
        called_paths.append(path)
        return original(path)

    monkeypatch.setattr(manager, "_path_size_bytes", tracking_size_bytes)
    monkeypatch.setattr(manager, "_path_size_bytes_via_du", tracking_size_bytes)

    manager._delete_expired_pool_locked(pool_root, time.time())

    assert not pool_root.exists()
    assert all(
        path != pool_root and not str(path).startswith(str(pool_root))
        for path in called_paths
    ), f"_path_size_bytes was called on pool path: {called_paths}"


def test_cleanup_base_removes_trash_dirs_without_path_size_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trash dirs must be removed without walking their tree for byte accounting."""
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    trash = manager._base_dir / ".ralph-exec-trash-test-123-456"
    trash.mkdir(parents=True)
    (trash / "large_file.bin").write_bytes(b"y" * 8192)

    called_paths: list[Path] = []
    original = manager._path_size_bytes

    def tracking_size_bytes(path: Path) -> int:
        called_paths.append(path)
        return original(path)

    monkeypatch.setattr(manager, "_path_size_bytes", tracking_size_bytes)
    monkeypatch.setattr(manager, "_path_size_bytes_via_du", tracking_size_bytes)

    manager.cleanup_base()

    assert not trash.exists()
    assert all(
        path != trash for path in called_paths
    ), f"_path_size_bytes was called on trash: {called_paths}"


def test_path_size_bytes_via_du_falls_back_to_rglob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_path_size_bytes_via_du falls back to rglob when du is unavailable."""
    monkeypatch.setattr(exec_sandbox.shutil, "which", lambda _cmd: None)
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    test_dir = tmp_path / "test-dir"
    test_dir.mkdir()
    (test_dir / "f").write_bytes(b"hello")
    (test_dir / "g").write_bytes(b"world")

    size = manager._path_size_bytes_via_du(test_dir)

    assert size == 10  # "hello" (5) + "world" (5)


def test_path_size_bytes_via_du_uses_du_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_path_size_bytes_via_du prefers du -sk when available."""
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    test_dir = tmp_path / "test-dir"
    test_dir.mkdir()
    (test_dir / "f").write_bytes(b"hello")

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
