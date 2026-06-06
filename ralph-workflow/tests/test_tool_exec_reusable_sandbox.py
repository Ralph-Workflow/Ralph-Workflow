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
    os.utime(stale_slot / ".ralph-sandbox-ready", (expired_at, expired_at))

    with manager.acquire(workspace) as sandbox:
        assert sandbox.exists()

    assert not stale_slot.exists()


def test_cleanup_base_enforces_total_byte_budget_across_idle_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_total_bytes=250,
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
        ready_path = slot_root / ".ralph-sandbox-ready"
        ready_path.write_text('{"ready": true}', encoding="utf-8")
        pid, started_at = exec_sandbox._current_process_identity()
        payload: dict[str, int | float] = {"pid": pid}
        if started_at is not None:
            payload["started_at"] = started_at
        owner_path = slot_root / ".ralph-exec-owner.json"
        owner_path.write_text(json.dumps(payload), encoding="utf-8")
        (ws_root / "payload.bin").write_bytes(b"x" * size)
        os.utime(slot_root, (mtime, mtime))
        os.utime(ws_root, (mtime, mtime))
        os.utime(ready_path, (mtime, mtime))
        os.utime(owner_path, (mtime, mtime))
        return slot_root

    now = tmp_path.stat().st_mtime
    oldest = seed_idle_slot(workspace_a, 1, 140, now - 2.0)
    newest = seed_idle_slot(workspace_b, 1, 140, now - 1.0)

    summary = manager.cleanup_base()

    assert summary.remaining_bytes <= 250
    assert not oldest.exists()
    assert newest.exists()


def test_acquire_recovers_over_capacity_base_wide_reclaimable_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_total_bytes=50,
        max_pool_bytes=1_024,
        max_idle_slot_age_s=3600.0,
        max_idle_pool_age_s=3600.0,
    )
    monkeypatch.setattr(manager, "_path_size_bytes_via_du", manager._path_size_bytes)

    workspace_key = exec_sandbox._workspace_key(workspace)
    sibling_workspace = tmp_path / "sibling-workspace"
    sibling_key = exec_sandbox._workspace_key(sibling_workspace)

    base = manager._base_dir
    base.mkdir(parents=True, exist_ok=True)

    # Seed 1: root-level non-lock file — ignored by normal cleanup
    root_file = base / "root_legacy.bin"
    root_file.write_bytes(b"x" * 35)

    # Seed 2: root-level orphan dir — too young for normal pool expiry
    orphan_dir = base / "orphan_dir"
    orphan_dir.mkdir()
    (orphan_dir / "data.bin").write_bytes(b"x" * 35)

    # Seed 3: sibling pool with non-slot garbage — young, not expired
    sibling_pool = base / sibling_key
    sibling_pool.mkdir()
    sibling_garbage = sibling_pool / "garbage.bin"
    sibling_garbage.write_bytes(b"x" * 35)

    # Seed 4: current workspace pool with non-slot legacy garbage
    current_pool = base / workspace_key
    current_pool.mkdir()
    current_legacy = current_pool / "legacy.bin"
    current_legacy.write_bytes(b"x" * 35)

    # Total = 35 * 4 = 140 bytes > hard cap (50 * 2 = 100)
    # Normal cleanup cannot remove these: no slot dirs, pools too young to expire

    with manager.acquire(workspace) as sandbox:
        assert sandbox.exists()

    assert not root_file.exists(), "root-level non-lock file must be removed by recovery"
    assert not orphan_dir.exists(), "root-level orphan directory must be removed by recovery"
    assert not sibling_garbage.exists(), "sibling pool garbage must be removed by recovery"
    assert not current_legacy.exists(), "current pool legacy garbage must be removed by recovery"


def test_acquire_recovers_over_capacity_preserving_live_slot_when_garbage_is_reclaimable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_total_bytes=50,
        max_pool_bytes=1_024,
        max_slots=4,
        max_idle_slot_age_s=3600.0,
        max_idle_pool_age_s=3600.0,
    )
    monkeypatch.setattr(manager, "_path_size_bytes_via_du", manager._path_size_bytes)

    workspace_key = exec_sandbox._workspace_key(workspace)
    base = manager._base_dir
    pool_root = base / workspace_key
    slot_1 = manager._slot_root(pool_root, workspace_key, 1)
    slot_1.mkdir(parents=True)

    pid, started_at = exec_sandbox._current_process_identity()
    live_payload: dict[str, int | float] = {"pid": pid}
    if started_at is not None:
        live_payload["started_at"] = started_at
    lock_path = manager._lock_path(slot_1)
    lock_path.write_text(json.dumps(live_payload), encoding="utf-8")
    live_payload_file = slot_1 / "live_payload.bin"
    live_payload_file.write_bytes(b"x" * 20)

    # Reclaimable root garbage pushes total over hard cap: 20 + 90 = 110 > 100
    root_garbage = base / "reclaimable.bin"
    root_garbage.write_bytes(b"x" * 90)

    try:
        with manager.acquire(workspace) as sandbox:
            assert sandbox.exists()
            assert lock_path.exists(), "live slot lock must be preserved during recovery"
            assert live_payload_file.exists(), "live slot payload must be preserved during recovery"
            assert not root_garbage.exists(), "reclaimable garbage must be removed by recovery"
    finally:
        lock_path.unlink(missing_ok=True)


def test_acquire_reports_unrecoverable_over_capacity_without_deleting_live_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_total_bytes=50,
        max_pool_bytes=1_024,
        max_idle_slot_age_s=3600.0,
        max_idle_pool_age_s=3600.0,
    )
    monkeypatch.setattr(manager, "_path_size_bytes_via_du", manager._path_size_bytes)

    workspace_key = exec_sandbox._workspace_key(workspace)
    base = manager._base_dir
    pool_root = base / workspace_key
    slot_1 = manager._slot_root(pool_root, workspace_key, 1)
    slot_1.mkdir(parents=True)

    pid, started_at = exec_sandbox._current_process_identity()
    live_payload: dict[str, int | float] = {"pid": pid}
    if started_at is not None:
        live_payload["started_at"] = started_at
    lock_path = manager._lock_path(slot_1)
    lock_path.write_text(json.dumps(live_payload), encoding="utf-8")
    live_large_file = slot_1 / "live_large.bin"
    live_large_file.write_bytes(b"x" * 110)  # 110 > hard cap 100, cannot be reclaimed

    try:
        with pytest.raises(exec_sandbox.ExecutionError, match="automatic reset"), manager.acquire(
            workspace
        ):
            pass

        assert lock_path.exists(), "live lock must be preserved after failed recovery"
        assert live_large_file.exists(), "live payload must be preserved after failed recovery"
    finally:
        lock_path.unlink(missing_ok=True)


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


def test_cleanup_worktree_preserves_worktree(tmp_path: Path) -> None:
    worktree = tmp_path / "ws"
    worktree.mkdir(parents=True)
    (worktree / "file.txt").write_text("content", encoding="utf-8")

    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    manager._cleanup_worktree(worktree)

    assert worktree.exists()
    assert (worktree / "file.txt").exists()


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


def test_workspace_size_bytes_delegates_to_compute_workspace_size_bytes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("hello")
    pycache = workspace / "__pycache__"
    pycache.mkdir()
    (pycache / "cached.pyc").write_text("ignored_content")
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    result = manager._workspace_size_bytes(workspace)
    expected = exec_sandbox._compute_workspace_size_bytes(workspace)
    assert result == expected
    assert result == 5


def test_path_size_bytes_excludes_lock_files(tmp_path: Path) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    slot = tmp_path / "slot"
    slot.mkdir()
    (slot / "data.bin").write_bytes(b"abc")
    (slot / exec_sandbox._LOCK_FILE).write_bytes(b"xxxxxxxxxx")
    (slot / exec_sandbox._POOL_LOCK_FILE).write_bytes(b"yyyyyy")
    assert manager._path_size_bytes(slot) == 3


def test_path_size_bytes_counts_nested_files(tmp_path: Path) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    root = tmp_path / "root"
    root.mkdir()
    sub = root / "sub"
    sub.mkdir()
    (root / "a.txt").write_bytes(b"aa")
    (sub / "b.txt").write_bytes(b"bbb")
    assert manager._path_size_bytes(root) == 5


def test_path_size_bytes_returns_zero_for_missing_path(tmp_path: Path) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    assert manager._path_size_bytes(tmp_path / "nonexistent") == 0


def test_path_size_bytes_handles_scandir_oserror_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_bytes(b"x")
    original_scandir = exec_sandbox.os.scandir

    def failing_scandir(path: object) -> object:
        if str(path) == str(root):
            raise OSError("simulated")
        return original_scandir(path)

    monkeypatch.setattr(exec_sandbox.os, "scandir", failing_scandir)
    assert manager._path_size_bytes(root) == 0


def test_acquire_skips_post_release_cleanup_when_cleanup_was_recent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    cleanup_call_count = 0
    original_cleanup = manager.cleanup_base

    def counting_cleanup() -> exec_sandbox.ExecCacheCleanupSummary:
        nonlocal cleanup_call_count
        cleanup_call_count += 1
        return original_cleanup()

    monkeypatch.setattr(manager, "cleanup_base", counting_cleanup)

    with manager.acquire(workspace):
        pass
    assert cleanup_call_count == 2  # first acquire: pre+post

    cleanup_call_count = 0
    with manager.acquire(workspace):
        pass
    assert cleanup_call_count == 0  # rapid second: both pre and post throttled


def test_acquire_runs_post_release_cleanup_after_cooldown_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    cleanup_call_count = 0
    original_cleanup = manager.cleanup_base

    def counting_cleanup() -> exec_sandbox.ExecCacheCleanupSummary:
        nonlocal cleanup_call_count
        cleanup_call_count += 1
        return original_cleanup()

    monkeypatch.setattr(manager, "cleanup_base", counting_cleanup)

    with manager.acquire(workspace):
        pass
    assert cleanup_call_count == 2

    manager._last_cleanup_monotonic = 0.0  # simulate cooldown expired
    manager._last_pre_acquire_cleanup_monotonic = 0.0
    manager._last_cleanup_summary = None

    cleanup_call_count = 0
    with manager.acquire(workspace):
        pass
    assert cleanup_call_count == 2  # both pre and post run


def test_cleanup_base_calls_du_once_per_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    du_call_count = 0
    original_du = manager._path_size_bytes_via_du

    def counting_du(path: Path) -> int:
        nonlocal du_call_count
        du_call_count += 1
        return original_du(path)

    monkeypatch.setattr(manager, "_path_size_bytes_via_du", counting_du)
    manager.cleanup_base()
    assert du_call_count == 1


def test_workspace_size_bytes_caches_result_within_ttl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "file.txt").write_bytes(b"hello")
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    scan_count = 0
    original_compute = exec_sandbox._compute_workspace_size_bytes

    def counting_compute(root: Path) -> int:
        nonlocal scan_count
        scan_count += 1
        return original_compute(root)

    monkeypatch.setattr(exec_sandbox, "_compute_workspace_size_bytes", counting_compute)
    manager._workspace_size_bytes(workspace)
    manager._workspace_size_bytes(workspace)
    assert scan_count == 1


def test_workspace_size_bytes_cache_does_not_apply_for_different_workspaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"
    ws1.mkdir()
    ws2.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    scan_count = 0
    original_compute = exec_sandbox._compute_workspace_size_bytes

    def counting_compute(root: Path) -> int:
        nonlocal scan_count
        scan_count += 1
        return original_compute(root)

    monkeypatch.setattr(exec_sandbox, "_compute_workspace_size_bytes", counting_compute)
    manager._workspace_size_bytes(ws1)
    manager._workspace_size_bytes(ws2)
    assert scan_count == 2


def test_workspace_size_bytes_cache_expires_after_ttl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    scan_count = 0
    original_compute = exec_sandbox._compute_workspace_size_bytes

    def counting_compute(root: Path) -> int:
        nonlocal scan_count
        scan_count += 1
        return original_compute(root)

    monkeypatch.setattr(exec_sandbox, "_compute_workspace_size_bytes", counting_compute)
    manager._workspace_size_bytes(workspace)
    key = str(workspace)
    cached_size, _ = manager._workspace_size_cache[key]
    manager._workspace_size_cache[key] = (cached_size, 0.0)  # backdate to expire
    manager._workspace_size_bytes(workspace)
    assert scan_count == 2


def test_acquire_throttles_pre_acquire_cleanup_when_recent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    cleanup_call_count = 0
    original_cleanup = manager.cleanup_base

    def counting_cleanup() -> exec_sandbox.ExecCacheCleanupSummary:
        nonlocal cleanup_call_count
        cleanup_call_count += 1
        return original_cleanup()

    monkeypatch.setattr(manager, "cleanup_base", counting_cleanup)
    with manager.acquire(workspace):
        pass
    first_run_count = cleanup_call_count
    cleanup_call_count = 0
    with manager.acquire(workspace):
        pass
    assert cleanup_call_count == 0
    assert first_run_count >= 1


def test_acquire_pre_acquire_cleanup_runs_after_cooldown_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    cleanup_call_count = 0
    original_cleanup = manager.cleanup_base

    def counting_cleanup() -> exec_sandbox.ExecCacheCleanupSummary:
        nonlocal cleanup_call_count
        cleanup_call_count += 1
        return original_cleanup()

    monkeypatch.setattr(manager, "cleanup_base", counting_cleanup)
    with manager.acquire(workspace):
        pass
    manager._last_pre_acquire_cleanup_monotonic = 0.0
    manager._last_cleanup_summary = None
    cleanup_call_count = 0
    with manager.acquire(workspace):
        pass
    assert cleanup_call_count >= 1


def test_fast_reset_does_not_pre_delete_worktree_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "tracked.txt").write_text("original", encoding="utf-8")
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    sandbox_root: Path
    with manager.acquire(workspace) as worktree:
        sandbox_root = worktree.parent

    (sandbox_root / "ws").mkdir(exist_ok=True)
    (sandbox_root / "ws" / "dirty.txt").write_text("dirty", encoding="utf-8")
    inode_before = (sandbox_root / "ws").stat().st_ino

    manager._reset(workspace, sandbox_root, sandbox_root / "ws")

    assert (sandbox_root / "ws").stat().st_ino == inode_before
    assert not (sandbox_root / "ws" / "dirty.txt").exists()
    assert (sandbox_root / "ws" / "tracked.txt").read_text(encoding="utf-8") == "original"


def test_pool_state_cache_survives_file_deletion_after_save(tmp_path: Path) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    pool_root = manager._base_dir / "pool-a"
    pool_root.mkdir(parents=True)

    manager._save_pool_state(
        pool_root, {"target_slots": 3, "average_slots": 2.5, "low_usage_streak": 0}
    )
    manager._pool_state_path(pool_root).unlink()
    state = manager._load_pool_state(pool_root)

    assert state["target_slots"] == 3


def test_pool_state_cache_is_keyed_by_pool_root(tmp_path: Path) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    pool1 = manager._base_dir / "pool1"
    pool2 = manager._base_dir / "pool2"
    pool1.mkdir(parents=True)
    pool2.mkdir(parents=True)

    manager._save_pool_state(
        pool1, {"target_slots": 5, "average_slots": 5.0, "low_usage_streak": 0}
    )
    manager._save_pool_state(
        pool2, {"target_slots": 2, "average_slots": 2.0, "low_usage_streak": 0}
    )
    manager._pool_state_path(pool1).unlink()
    manager._pool_state_path(pool2).unlink()

    state1 = manager._load_pool_state(pool1)
    state2 = manager._load_pool_state(pool2)

    assert state1["target_slots"] == 5
    assert state2["target_slots"] == 2


def test_pool_state_cache_loads_from_file_when_not_in_cache(tmp_path: Path) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    pool_root = manager._base_dir / "pool-b"
    pool_root.mkdir(parents=True)

    manager._pool_state_path(pool_root).write_text(
        json.dumps({"target_slots": 4, "average_slots": 3.0, "low_usage_streak": 0}),
        encoding="utf-8",
    )
    state = manager._load_pool_state(pool_root)

    assert state["target_slots"] == 4


def test_shrink_idle_slots_skips_pool_lock_when_target_is_minimum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    pool_root = tmp_path / "exec-base" / "pool-x"
    pool_root.mkdir(parents=True)
    manager._pool_state_cache[str(pool_root)] = {
        "target_slots": 1,
        "average_slots": 1.0,
        "low_usage_streak": 5,
    }

    acquire_calls: list[Path] = []
    orig_acquire = manager._acquire_named_lock

    def recording_acquire(lock_path: Path) -> None:
        acquire_calls.append(lock_path)
        orig_acquire(lock_path)

    monkeypatch.setattr(manager, "_acquire_named_lock", recording_acquire)

    manager._shrink_idle_slots(pool_root, "testkey")

    pool_lock_path = manager._pool_lock_path(pool_root)
    assert not any(p == pool_lock_path for p in acquire_calls)


def test_shrink_idle_slots_acquires_lock_when_target_exceeds_minimum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    pool_root = tmp_path / "exec-base" / "pool-y"
    pool_root.mkdir(parents=True)
    manager._pool_state_cache[str(pool_root)] = {
        "target_slots": 3,
        "average_slots": 3.0,
        "low_usage_streak": 0,
    }

    acquire_calls: list[Path] = []
    orig_acquire = manager._acquire_named_lock

    def recording_acquire(lock_path: Path) -> None:
        acquire_calls.append(lock_path)
        orig_acquire(lock_path)

    monkeypatch.setattr(manager, "_acquire_named_lock", recording_acquire)

    manager._shrink_idle_slots(pool_root, "testkey")

    pool_lock_path = manager._pool_lock_path(pool_root)
    assert any(p == pool_lock_path for p in acquire_calls)


def test_fast_reset_clears_sentinel_on_mirror_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    sandbox_root: Path
    with manager.acquire(workspace) as worktree:
        sandbox_root = worktree.parent

    assert (sandbox_root / ".ralph-sandbox-ready").exists()

    def raising_mirror(*args: object, **kwargs: object) -> None:
        raise OSError("simulated")

    monkeypatch.setattr(exec_sandbox, "_mirror_workspace", raising_mirror)

    with pytest.raises(OSError):
        manager._fast_reset(workspace, sandbox_root, sandbox_root / "ws")

    assert not (sandbox_root / ".ralph-sandbox-ready").exists()


def test_slot_last_used_at_returns_ready_sentinel_mtime(tmp_path: Path) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    sentinel = slot_root / ".ralph-sandbox-ready"
    sentinel.write_text('{"ready": true}', encoding="utf-8")
    expected_mtime = sentinel.stat().st_mtime

    result = manager._slot_last_used_at(slot_root)

    assert result == expected_mtime


def test_slot_last_used_at_falls_back_to_slot_root_mtime_when_sentinel_absent(
    tmp_path: Path,
) -> None:
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    slot_root = tmp_path / "slot"
    slot_root.mkdir()
    expected_mtime = slot_root.stat().st_mtime

    result = manager._slot_last_used_at(slot_root)

    assert result == expected_mtime


def test_prune_expired_returns_no_remaining_when_all_slots_collected(
    tmp_path: Path,
) -> None:
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_idle_slot_age_s=0.0,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    key = exec_sandbox._workspace_key(workspace)
    pool_root = manager._base_dir / key
    pool_root.mkdir(parents=True)
    slot = manager._slot_root(pool_root, key, 1)
    slot.mkdir()
    (slot / ".ralph-exec-owner.json").write_text(
        json.dumps({"pid": -1}), encoding="utf-8"
    )

    _, _, _, has_remaining = manager._prune_expired_and_broken_slots_locked(
        pool_root, time.time()
    )

    assert has_remaining is False


def test_prune_expired_returns_has_remaining_when_active_slot_exists(
    tmp_path: Path,
) -> None:
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_idle_slot_age_s=3600.0,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    key = exec_sandbox._workspace_key(workspace)
    pool_root = manager._base_dir / key
    pool_root.mkdir(parents=True)

    with manager.acquire(workspace):
        _, _, _, has_remaining = manager._prune_expired_and_broken_slots_locked(
            pool_root, time.time()
        )
    assert has_remaining is True
