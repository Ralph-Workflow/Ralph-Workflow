"""Tests for ralph/mcp/tools/exec_sandbox.py — lock-free round-robin sandbox pool."""

from __future__ import annotations

import json
import os
import threading
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools import exec_sandbox
from ralph.mcp.tools._exec_execution_error import ExecutionError

if TYPE_CHECKING:
    from pathlib import Path

_THREAD_JOIN_TIMEOUT_S = 3.0
_ENTER_WAIT_TIMEOUT_S = 3.0


# ---------------------------------------------------------------------------
# Round-robin slot selection (plan requirement a)
# ---------------------------------------------------------------------------


def test_round_robin_cycles_bounded_slots_across_sequential_acquires(
    tmp_path: Path,
) -> None:
    """With max_slots=3, four sequential acquires cycle slots 1→2→3→1."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_slots=3,
    )
    seen: list[Path] = []
    for _ in range(4):
        with manager.acquire(workspace) as sandbox:
            seen.append(sandbox)

    # Slots cycle 0001→0002→0003→0001
    assert seen[0] == seen[3], "Fourth acquire must reuse slot from first acquire"
    assert seen[1] != seen[0], "Second acquire must use a different slot"
    assert seen[2] != seen[1], "Third acquire must use a different slot"
    assert seen[2] != seen[0], "Third acquire must be yet another different slot"

    # No filesystem lock files must be created anywhere under the cache
    base = tmp_path / "exec-base"
    for path in base.rglob("*"):
        assert ".ralph-sandbox.lock" not in path.name, "No .ralph-sandbox.lock must be created"
        assert ".ralph-sandbox-pool.lock" not in path.name, "No pool lock must be created"
        assert ".ralph-exec-base.lock" not in path.name, "No base lock must be created"


# ---------------------------------------------------------------------------
# Concurrent acquires — no busy error, bounded physical dirs (plan req b)
# ---------------------------------------------------------------------------


@pytest.mark.timeout_seconds(4)
def test_concurrent_acquires_no_busy_error_with_capped_physical_slots(
    tmp_path: Path,
) -> None:
    """Three concurrent acquires with max_slots=2: no busy error, ≤2 physical slot dirs."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_slots=2,
    )
    all_entered = [threading.Event(), threading.Event(), threading.Event()]
    release_all = threading.Event()
    errors: list[BaseException] = []

    def acquire_slot(index: int) -> None:
        try:
            with manager.acquire(workspace):
                all_entered[index].set()
                assert release_all.wait(timeout=_ENTER_WAIT_TIMEOUT_S)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=acquire_slot, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    try:
        for ev in all_entered:
            assert ev.wait(timeout=_ENTER_WAIT_TIMEOUT_S), "All three must enter without busy error"
    finally:
        release_all.set()
        for t in threads:
            t.join(timeout=_THREAD_JOIN_TIMEOUT_S)

    assert not errors, f"Unexpected errors: {errors}"

    # Physical slot directories must be bounded by max_slots=2
    pool_root = tmp_path / "exec-base" / exec_sandbox._workspace_key(workspace)
    slot_dirs = [d for d in pool_root.iterdir() if d.name.startswith("slot-")]
    assert len(slot_dirs) <= 2, f"Expected ≤2 physical slot dirs, got {len(slot_dirs)}"


# ---------------------------------------------------------------------------
# Legacy lock files treated as inert garbage (plan requirement c)
# ---------------------------------------------------------------------------


def test_legacy_lock_files_treated_as_inert_garbage(tmp_path: Path) -> None:
    """Pre-existing legacy lock-like files never block acquire or raise busy error."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    base = tmp_path / "exec-base"
    base.mkdir(parents=True, exist_ok=True)
    workspace_key = exec_sandbox._workspace_key(workspace)
    pool_root = base / workspace_key
    pool_root.mkdir(parents=True, exist_ok=True)

    # Seed legacy lock-like files that the old lock-based system would have created
    (base / ".ralph-exec-base.lock").write_text("{}", encoding="utf-8")
    (pool_root / ".ralph-sandbox-pool.lock").write_text("{}", encoding="utf-8")
    slot_dir = pool_root / f"slot-{workspace_key}-0001"
    slot_dir.mkdir(parents=True, exist_ok=True)
    (slot_dir / ".ralph-sandbox.lock").write_text("{}", encoding="utf-8")

    # Must acquire without raising any error
    with manager.acquire(workspace) as sandbox:
        assert sandbox.exists()


# ---------------------------------------------------------------------------
# Under-budget: garbage remains (plan requirement d)
# ---------------------------------------------------------------------------


def test_under_budget_garbage_not_cleaned(tmp_path: Path) -> None:
    """Under budget, garbage at root/current-workspace/sibling-workspace is left intact."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Set a very large budget so tiny test files are always under budget
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_total_bytes=100_000_000,
    )

    # First acquire to discover the pool path
    with manager.acquire(workspace) as _:
        pass

    base = tmp_path / "exec-base"
    workspace_key = exec_sandbox._workspace_key(workspace)
    sibling_key = "aabbccdd11223344"

    # Seed garbage at three locations
    root_garbage = base / "root_legacy.bin"
    root_garbage.write_bytes(b"x" * 16)

    current_pool = base / workspace_key
    current_garbage = current_pool / "current_legacy.bin"
    current_garbage.write_bytes(b"x" * 16)

    sibling_pool = base / sibling_key
    sibling_pool.mkdir(parents=True, exist_ok=True)
    sibling_garbage = sibling_pool / "sibling_legacy.bin"
    sibling_garbage.write_bytes(b"x" * 16)

    # Second acquire (under budget) must leave all garbage intact
    with manager.acquire(workspace) as sandbox:
        assert sandbox.exists()
        assert root_garbage.exists(), "Root-level garbage must remain when under budget"
        assert current_garbage.exists(), "Current-workspace garbage must remain when under budget"
        assert sibling_garbage.exists(), "Sibling-workspace garbage must remain when under budget"


# ---------------------------------------------------------------------------
# Over-budget: garbage removed, active slot preserved (plan requirement f)
# ---------------------------------------------------------------------------


def test_over_budget_garbage_removed_active_slot_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Over budget: reclaimable garbage removed, currently-yielded active slot preserved."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Tiny budget: any seeded files will exceed it
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_total_bytes=50,
    )
    # Use pure-Python size counter so tiny budget is exact
    monkeypatch.setattr(manager, "_path_size_bytes_via_du", manager._path_size_bytes)

    base = tmp_path / "exec-base"
    base.mkdir(parents=True, exist_ok=True)
    workspace_key = exec_sandbox._workspace_key(workspace)
    sibling_key = "aabbccdd11223344"

    # Seed reclaimable garbage at root, current pool, and sibling pool
    root_garbage = base / "root_reclaimable.bin"
    root_garbage.write_bytes(b"x" * 30)
    current_pool = base / workspace_key
    current_pool.mkdir(parents=True, exist_ok=True)
    current_garbage = current_pool / "current_reclaimable.bin"
    current_garbage.write_bytes(b"x" * 30)
    sibling_pool = base / sibling_key
    sibling_pool.mkdir(parents=True, exist_ok=True)
    sibling_garbage = sibling_pool / "sibling_reclaimable.bin"
    sibling_garbage.write_bytes(b"x" * 30)
    # Total = 90 bytes > 50 byte budget

    garbage_paths = [root_garbage, current_garbage, sibling_garbage]

    with manager.acquire(workspace) as sandbox:
        assert sandbox.exists(), "Acquire must succeed even when over budget"
        # At least one garbage path must be removed
        remaining = [p for p in garbage_paths if p.exists()]
        removed = [p for p in garbage_paths if not p.exists()]
        assert removed, "At least some reclaimable garbage must be removed"
        # The sandbox itself (active slot) must be preserved
        assert sandbox.exists(), "Active slot worktree must be preserved during recovery"
        del remaining


# ---------------------------------------------------------------------------
# Unrecoverable over-capacity (plan step 3)
# ---------------------------------------------------------------------------


def test_unrecoverable_over_capacity_raises_execution_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When only active-slot bytes keep usage above cap, ExecutionError is raised."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_total_bytes=50,
    )
    monkeypatch.setattr(manager, "_path_size_bytes_via_du", manager._path_size_bytes)

    base = tmp_path / "exec-base"
    base.mkdir(parents=True, exist_ok=True)

    # Practical approach: make the size reporter always return huge bytes so recovery
    # cannot bring usage under the tiny budget, simulating unremovable active-slot data.
    def fake_size(path: Path) -> int:
        return 200

    monkeypatch.setattr(manager, "_path_size_bytes_via_du", fake_size)

    with (
        pytest.raises(ExecutionError) as exc_info,
        manager.acquire(workspace),
    ):
        pass

    msg = str(exc_info.value)
    # Must describe automatic reset attempt
    assert "automatic" in msg.lower() or "reset" in msg.lower(), (
        "Error must describe automatic reset"
    )
    # Must have structured fields
    err = exc_info.value
    assert isinstance(err, ExecutionError)
    assert err.current_bytes is not None
    assert err.cap_bytes is not None
    assert err.remaining_bytes is not None


# ---------------------------------------------------------------------------
# Over-capacity suppressed when live/active slots explain the overage
# ---------------------------------------------------------------------------


def test_no_error_when_live_external_slot_explains_overage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No error when over-budget bytes belong to a slot owned by a still-alive process."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    base = tmp_path / "exec-base"
    base.mkdir(parents=True, exist_ok=True)
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=base,
        max_total_bytes=50,
    )

    workspace_key = exec_sandbox._workspace_key(workspace)
    pool_dir = base / workspace_key
    pool_dir.mkdir(parents=True, exist_ok=True)

    # Create a slot owned by the current (live) process
    live_slot = pool_dir / f"slot-{workspace_key}-0099"
    live_slot.mkdir()
    (live_slot / ".ralph-exec-owner.json").write_text(
        json.dumps({"pid": os.getpid()})
    )

    # du always reports over-budget so recovery cannot free space
    monkeypatch.setattr(manager, "_path_size_bytes_via_du", lambda path: 200)

    # acquire must succeed: live slot explains the overage
    with manager.acquire(workspace):
        pass


def test_no_error_when_active_in_process_slot_explains_overage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No error when over-budget bytes belong to a currently-active in-process slot."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_total_bytes=50,
    )

    # Disable TTL so every acquire triggers a fresh capacity check.
    monkeypatch.setattr(exec_sandbox, "_CAPACITY_CHECK_INTERVAL_S", 0.0)

    # First call (first acquire's capacity check): under budget so acquire succeeds.
    # Subsequent calls: over budget — simulates growth from the active slot.
    call_count = [0]

    def sized(path: object) -> int:
        call_count[0] += 1
        return 0 if call_count[0] == 1 else 200

    monkeypatch.setattr(manager, "_path_size_bytes_via_du", sized)

    slot1_ready = threading.Event()
    slot1_release = threading.Event()
    first_error: list[Exception | None] = [None]

    def hold_slot1() -> None:
        try:
            with manager.acquire(workspace):
                slot1_ready.set()
                slot1_release.wait(timeout=_ENTER_WAIT_TIMEOUT_S)
        except Exception as exc:
            first_error[0] = exc
            slot1_ready.set()

    t = threading.Thread(target=hold_slot1, daemon=True)
    t.start()
    assert slot1_ready.wait(timeout=_ENTER_WAIT_TIMEOUT_S), "First slot not acquired in time"
    assert first_error[0] is None, f"First acquire failed: {first_error[0]}"

    # Second acquire: active_now contains the first slot; no error expected
    with manager.acquire(workspace):
        pass

    slot1_release.set()
    t.join(timeout=_THREAD_JOIN_TIMEOUT_S)


# ---------------------------------------------------------------------------
# Over-capacity error raised when live slot does not explain the overage
# ---------------------------------------------------------------------------


def test_error_when_live_slot_does_not_explain_overage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ExecutionError raised when a live slot exists but its bytes do not cover the overage."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    base = tmp_path / "exec-base"
    base.mkdir(parents=True, exist_ok=True)
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=base,
        max_total_bytes=50,
    )

    workspace_key = exec_sandbox._workspace_key(workspace)
    pool_dir = base / workspace_key
    pool_dir.mkdir(parents=True, exist_ok=True)

    # Create a slot owned by the current (live) process — tiny content
    live_slot = pool_dir / f"slot-{workspace_key}-0099"
    live_slot.mkdir()
    (live_slot / ".ralph-exec-owner.json").write_text(
        json.dumps({"pid": os.getpid()}), encoding="utf-8"
    )

    # du: return a small value for the live slot, but a large value for the base
    # so the live-slot footprint cannot explain the full overage.
    def size_by_path(path: Path) -> int:
        if path == live_slot or path.is_relative_to(live_slot):
            return 5  # Tiny: cannot explain 200-50=150 byte overage
        return 200

    monkeypatch.setattr(manager, "_path_size_bytes_via_du", size_by_path)

    # Error must still be raised because 5 bytes < 150 byte overage
    with (
        pytest.raises(ExecutionError),
        manager.acquire(workspace),
    ):
        pass


# ---------------------------------------------------------------------------
# Over-capacity error raised even after partial removal
# ---------------------------------------------------------------------------


def test_over_capacity_error_raised_even_when_paths_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ExecutionError is raised even when some paths were removed but budget stays exceeded."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_total_bytes=50,
    )

    base = tmp_path / "exec-base"
    base.mkdir()

    # Seed a real file that will be staged and removed during recovery
    garbage = base / "garbage.bin"
    garbage.write_bytes(b"x" * 10)

    # But size reporter always returns high (simulates OS accounting delay / active slots)
    monkeypatch.setattr(manager, "_path_size_bytes_via_du", lambda path: 200)

    with (
        pytest.raises(ExecutionError) as exc_info,
        manager.acquire(workspace),
    ):
        pass

    err = exc_info.value
    assert isinstance(err, ExecutionError)
    assert err.current_bytes is not None
    assert err.cap_bytes is not None
    assert err.removed_paths is not None
    assert err.removed_bytes is not None
    assert err.remaining_bytes is not None
    msg = str(err)
    assert "automatic" in msg.lower() or "reset" in msg.lower(), msg


# ---------------------------------------------------------------------------
# Basic reset behavior (kept from pre-existing tests)
# ---------------------------------------------------------------------------


def test_reusable_sandbox_always_clean_on_entry(tmp_path: Path) -> None:
    """Sandbox is always clean (dirty files absent) at the start of each acquire."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    with manager.acquire(workspace) as sandbox:
        (sandbox / "mutated.txt").write_text("dirty", encoding="utf-8")

    with manager.acquire(workspace) as sandbox2:
        assert not (sandbox2 / "mutated.txt").exists(), (
            "Dirty file from previous run must be absent after reset"
        )


def test_reusable_sandbox_rebuilds_when_validation_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base", max_slots=1)

    with manager.acquire(workspace) as sandbox:
        sentinel = sandbox.parent / ".ralph-sandbox-ready"

    sentinel.unlink()

    with manager.acquire(workspace) as sandbox2:
        assert (sandbox2.parent / ".ralph-sandbox-ready").exists()


def test_reusable_sandbox_repopulates_from_workspace_on_each_acquire(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "tracked.txt").write_text("source", encoding="utf-8")
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base", max_slots=1)

    with manager.acquire(workspace) as sandbox:
        assert (sandbox / "tracked.txt").read_text(encoding="utf-8") == "source"
        (sandbox / "tracked.txt").write_text("dirty", encoding="utf-8")

    with manager.acquire(workspace) as sandbox2:
        assert (sandbox2 / "tracked.txt").read_text(encoding="utf-8") == "source", (
            "Sandbox must be repopulated from workspace on each acquire"
        )


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

    with (
        pytest.raises(OSError, match="sandbox workspace exceeds safety limit"),
        manager.acquire(workspace),
    ):
        pass

    assert not any(manager._base_dir.rglob("slot-*"))


def test_different_workspaces_can_acquire_independently(tmp_path: Path) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    with (
        manager.acquire(workspace_a) as sandbox_a,
        manager.acquire(workspace_b) as sandbox_b,
    ):
        assert sandbox_a != sandbox_b


# ---------------------------------------------------------------------------
# Over-capacity recovery with reclaimable entries
# ---------------------------------------------------------------------------


def test_acquire_recovers_over_capacity_base_wide_reclaimable_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_total_bytes=50,
    )
    monkeypatch.setattr(manager, "_path_size_bytes_via_du", manager._path_size_bytes)

    workspace_key = exec_sandbox._workspace_key(workspace)
    sibling_key = "aabbccdd11223344"

    base = manager._base_dir
    base.mkdir(parents=True, exist_ok=True)

    # Root-level non-pool file — should be removed
    root_file = base / "root_legacy.bin"
    root_file.write_bytes(b"x" * 35)

    # Root-level orphan dir (not a hex-key pool name) — should be removed
    orphan_dir = base / "orphan_dir"
    orphan_dir.mkdir()
    (orphan_dir / "data.bin").write_bytes(b"x" * 35)

    # Sibling pool — should be removed entirely
    sibling_pool = base / sibling_key
    sibling_pool.mkdir()
    (sibling_pool / "garbage.bin").write_bytes(b"x" * 35)

    # Current workspace pool with an idle slot dir — slot should be removed
    current_pool = base / workspace_key
    current_pool.mkdir()
    idle_slot = current_pool / f"slot-{workspace_key}-0099"
    idle_slot.mkdir()
    (idle_slot / "stale.bin").write_bytes(b"x" * 35)

    # Total = 35 * 4 = 140 bytes > 50 byte budget

    with manager.acquire(workspace) as sandbox:
        assert sandbox.exists()

    assert not root_file.exists(), "Root-level non-pool file must be removed"
    assert not orphan_dir.exists(), "Root-level orphan directory must be removed"
    assert not sibling_pool.exists() or not (sibling_pool / "garbage.bin").exists(), (
        "Sibling pool garbage must be removed"
    )
    assert not idle_slot.exists(), "Idle slot dir in current pool must be removed"


# ---------------------------------------------------------------------------
# cleanup_base() smoke test
# ---------------------------------------------------------------------------


def test_cleanup_base_returns_summary_with_remaining_bytes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")

    with manager.acquire(workspace) as _:
        pass

    summary = manager.cleanup_base()
    assert summary.removed_paths >= 0
    assert summary.remaining_bytes >= 0


def test_cleanup_base_removes_dead_owner_slots(tmp_path: Path) -> None:
    """cleanup_base() removes slot dirs whose owner process is no longer running."""
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base")
    base = tmp_path / "exec-base"
    base.mkdir(parents=True, exist_ok=True)
    pool_root = base / "aabbccdd11223344"
    pool_root.mkdir()
    dead_slot = pool_root / "slot-aabbccdd11223344-0001"
    dead_slot.mkdir()
    (dead_slot / ".ralph-exec-owner.json").write_text('{"pid": -1}', encoding="utf-8")
    (dead_slot / "some_file.txt").write_text("data", encoding="utf-8")

    summary = manager.cleanup_base()
    assert not dead_slot.exists(), "Dead-owner slot must be removed by cleanup_base"
    assert summary.removed_paths >= 1


# ---------------------------------------------------------------------------
# Active slots excluded from recovery
# ---------------------------------------------------------------------------


@pytest.mark.timeout_seconds(4)
def test_active_slot_not_deleted_during_concurrent_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slot currently being held must not be deleted during capacity recovery."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(
        base_dir=tmp_path / "exec-base",
        max_total_bytes=50,
        max_slots=2,
    )
    monkeypatch.setattr(manager, "_path_size_bytes_via_du", manager._path_size_bytes)

    base = tmp_path / "exec-base"
    base.mkdir(parents=True, exist_ok=True)

    # Seed enough data to trigger recovery
    for i in range(3):
        gf = base / f"garbage_{i}.bin"
        gf.write_bytes(b"x" * 25)

    # The active slot should be preserved inside the with-block
    with manager.acquire(workspace) as sandbox:
        assert sandbox.exists(), "Active slot must remain accessible during recovery"
        slot_root = sandbox.parent
        assert slot_root.exists(), "Active slot root must not be deleted during recovery"
