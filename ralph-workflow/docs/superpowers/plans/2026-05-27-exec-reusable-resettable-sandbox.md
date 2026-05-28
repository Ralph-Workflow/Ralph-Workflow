# Exec Reusable Resettable Sandbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-run exec overlays with a reusable, resettable, concurrency-safe sandbox pool per workspace while preserving existing exec isolation guarantees, enabling same-workspace parallel exec, and improving hot-path performance.

**Architecture:** Introduce a workspace-hash keyed reusable sandbox-pool manager that owns slot leasing, owner metadata, reset validation, adaptive pool sizing, and rebuild fallback. Keep observable exec behavior unchanged by routing `run_command()` through the pool manager, preserving env rewriting, git isolation, strict reset-before-run semantics, and process cleanup. Optimize in layers: first get pooled resettable reuse correct, then add validated fast-reset shortcuts only when tests prove semantic equivalence.

**Tech Stack:** Python 3.12+, pytest, existing `ralph.mcp.tools.exec` / `exec_overlay` code, `Path`/`shutil` filesystem APIs, existing process-manager cleanup path.

---

## File Structure

- **Create:** `ralph/mcp/tools/exec_sandbox.py`
  - Own reusable sandbox-pool lifecycle: workspace keying, slot leasing, owner metadata, reset orchestration, adaptive sizing persistence, rebuild fallback, and reusable context manager.
- **Modify:** `ralph/mcp/tools/exec.py`
  - Replace direct one-shot overlay creation with reusable sandbox acquisition.
- **Modify:** `ralph/mcp/tools/exec_overlay.py`
  - Retain the portable mirror/build primitives and git-isolation helpers, but refactor them into reusable reset helpers instead of only one-shot tempdir behavior.
- **Create:** `tests/test_tool_exec_reusable_sandbox.py`
  - Black-box unit tests for reuse, reset, fallback, slot-pool behavior, and concurrency via injected seams.
- **Modify:** `tests/test_tool_exec_ephemeral_overlay.py`
  - Preserve current guarantees and adapt expectations where a stable reusable sandbox path replaces per-run tempdir assumptions.
- **Modify:** `tests/test_tool_exec_run_command.py`
  - Assert `run_command()` now reuses the sandbox path without changing visible semantics.
- **Modify:** `tests/test_tool_exec_process_cleanup.py`
  - Ensure process cleanup still runs when using reusable sandboxes.

---

### Task 1: Add failing reuse and clean-reset tests

**Files:**
- Create: `tests/test_tool_exec_reusable_sandbox.py`
- Modify: `tests/test_tool_exec_run_command.py`
- Test: `tests/test_tool_exec_reusable_sandbox.py`, `tests/test_tool_exec_run_command.py`

- [ ] **Step 1: Write the failing reusable-sandbox tests**

```python
from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from ralph.mcp.tools import exec_sandbox


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
```

Append this to `tests/test_tool_exec_run_command.py`:

```python
def test_run_command_reuses_stable_sandbox_path(tmp_path: Path) -> None:
    seen_cwds: list[Path] = []

    def fake_runner(
        command: list[str], cwd: Path, timeout_seconds: float | None
    ) -> _CompletedProcessAdapter:
        del command, timeout_seconds
        seen_cwds.append(cwd)
        (cwd / "dirty.txt").write_text("dirty", encoding="utf-8")
        return _CompletedProcessAdapter(stdout=b"", stderr=b"", returncode=0)

    workspace = MockWorkspaceRoot(tmp_path)

    run_command("echo", [], workspace, 1000, deps=ExecRunDeps(runner=fake_runner))
    run_command("echo", [], workspace, 1000, deps=ExecRunDeps(runner=fake_runner))

    assert len(seen_cwds) == 2
    assert seen_cwds[0] == seen_cwds[1]
    assert not (seen_cwds[1] / "dirty.txt").exists()
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run:
`pytest tests/test_tool_exec_reusable_sandbox.py tests/test_tool_exec_run_command.py -q`

Expected:
- import failure for `ralph.mcp.tools.exec_sandbox`, or
- assertion failures because sandbox paths are still one-shot and not reusable.

- [ ] **Step 3: Add a minimal sandbox manager scaffold**

Create `ralph/mcp/tools/exec_sandbox.py`:

```python
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class ExecSandboxManager:
    def __init__(self, *, base_dir: Path) -> None:
        self._base_dir = base_dir

    @contextmanager
    def acquire(self, workspace_root: Path) -> Iterator[Path]:
        sandbox_root = self._base_dir / workspace_root.name
        sandbox_root.mkdir(parents=True, exist_ok=True)
        worktree = sandbox_root / "ws"
        worktree.mkdir(parents=True, exist_ok=True)
        yield worktree
```

- [ ] **Step 4: Re-run the focused tests and confirm they still fail for the right reason**

Run:
`pytest tests/test_tool_exec_reusable_sandbox.py tests/test_tool_exec_run_command.py -q`

Expected:
- tests fail because reset/reuse semantics are incomplete, not because the module is missing.

---

### Task 2: Implement reusable sandbox lifecycle and route exec through it

**Files:**
- Modify: `ralph/mcp/tools/exec_sandbox.py`
- Modify: `ralph/mcp/tools/exec.py`
- Test: `tests/test_tool_exec_reusable_sandbox.py`, `tests/test_tool_exec_run_command.py`

- [ ] **Step 1: Write the next failing lifecycle tests for source mirroring and reset validation**

Append to `tests/test_tool_exec_reusable_sandbox.py`:

```python
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
```

- [ ] **Step 2: Run the lifecycle tests to verify they fail**

Run:
`pytest tests/test_tool_exec_reusable_sandbox.py::test_reusable_sandbox_repopulates_from_workspace_on_each_acquire -q`

Expected:
`FAIL` because the scaffolded sandbox manager does not mirror or reset source contents.

- [ ] **Step 3: Implement minimal reusable reset behavior and wire `run_command()` to it**

Update `ralph/mcp/tools/exec_sandbox.py`:

```python
from __future__ import annotations

import hashlib
import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ralph.mcp.tools.exec_overlay import (
    _ensure_git_isolation,
    _mirror_workspace,
    _prune_stale_exec_dirs,
    _write_overlay_owner_metadata,
)

_READY_FILE = ".ralph-sandbox-ready"


def _workspace_key(workspace_root: Path) -> str:
    digest = hashlib.sha256(str(workspace_root.resolve()).encode("utf-8")).hexdigest()
    return digest[:16]


class ExecSandboxManager:
    def __init__(self, *, base_dir: Path) -> None:
        self._base_dir = base_dir

    @contextmanager
    def acquire(self, workspace_root: Path) -> Iterator[Path]:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        _prune_stale_exec_dirs(self._base_dir)
        sandbox_root = self._base_dir / _workspace_key(workspace_root)
        worktree = sandbox_root / "ws"
        self._reset(workspace_root, sandbox_root, worktree)
        yield worktree

    def _reset(self, workspace_root: Path, sandbox_root: Path, worktree: Path) -> None:
        if sandbox_root.exists():
            shutil.rmtree(sandbox_root)
        sandbox_root.mkdir(parents=True, exist_ok=True)
        _write_overlay_owner_metadata(sandbox_root)
        _mirror_workspace(workspace_root, worktree)
        _ensure_git_isolation(workspace_root, worktree, sandbox_root)
        (sandbox_root / _READY_FILE).write_text(json.dumps({"ready": True}), encoding="utf-8")
```

Update `ralph/mcp/tools/exec.py` near `run_command()`:

```python
from ralph.mcp.tools.exec_sandbox import ExecSandboxManager
from ralph.mcp.tools.exec_overlay import _get_private_exec_base


_SANDBOX_MANAGER = ExecSandboxManager(base_dir=_get_private_exec_base())
```

And inside `run_command()` replace the overlay factory line with:

```python
overlay_factory = resolved_deps.overlay_factory or _SANDBOX_MANAGER.acquire
```

- [ ] **Step 4: Re-run the focused tests and confirm they pass**

Run:
`pytest tests/test_tool_exec_reusable_sandbox.py tests/test_tool_exec_run_command.py -q`

Expected:
`PASS`

---

### Task 3: Add same-workspace sandbox-pool concurrency with failing tests first

**Files:**
- Modify: `tests/test_tool_exec_reusable_sandbox.py`
- Modify: `ralph/mcp/tools/exec_sandbox.py`
- Test: `tests/test_tool_exec_reusable_sandbox.py`

- [ ] **Step 1: Write the failing lock/concurrency tests**

Append to `tests/test_tool_exec_reusable_sandbox.py`:

```python
import threading
import time


def test_same_workspace_concurrent_acquire_fails_deterministically(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base", lock_timeout_s=0.01)
    entered = threading.Event()

    def hold_lock() -> None:
        with manager.acquire(workspace):
            entered.set()
            time.sleep(0.05)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    entered.wait(timeout=1)

    with pytest.raises(exec_sandbox.ExecSandboxBusyError):
        with manager.acquire(workspace):
            pass

    thread.join(timeout=1)


def test_different_workspaces_can_acquire_independently(tmp_path: Path) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    manager = exec_sandbox.ExecSandboxManager(base_dir=tmp_path / "exec-base", lock_timeout_s=0.01)

    with manager.acquire(workspace_a) as sandbox_a, manager.acquire(workspace_b) as sandbox_b:
        assert sandbox_a != sandbox_b
```

- [ ] **Step 2: Run the concurrency tests to verify they fail**

Run:
`pytest tests/test_tool_exec_reusable_sandbox.py::test_same_workspace_concurrent_acquire_fails_deterministically tests/test_tool_exec_reusable_sandbox.py::test_different_workspaces_can_acquire_independently -q`

Expected:
`FAIL` because the sandbox manager has no lock or busy error yet.

- [ ] **Step 3: Implement minimal lockfile-based exclusivity**

Update `ralph/mcp/tools/exec_sandbox.py`:

```python
import os
import time


class ExecSandboxBusyError(RuntimeError):
    """Raised when a reusable sandbox is already in use."""


class ExecSandboxManager:
    def __init__(self, *, base_dir: Path, lock_timeout_s: float = 0.1) -> None:
        self._base_dir = base_dir
        self._lock_timeout_s = lock_timeout_s

    def _lock_path(self, sandbox_root: Path) -> Path:
        return sandbox_root / ".ralph-sandbox.lock"

    def _acquire_lock(self, sandbox_root: Path) -> None:
        sandbox_root.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path(sandbox_root)
        deadline = time.monotonic() + self._lock_timeout_s
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise ExecSandboxBusyError(f"sandbox busy: {sandbox_root}")
                time.sleep(0.005)

    def _release_lock(self, sandbox_root: Path) -> None:
        self._lock_path(sandbox_root).unlink(missing_ok=True)
```

And wrap `acquire()`:

```python
    @contextmanager
    def acquire(self, workspace_root: Path) -> Iterator[Path]:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        _prune_stale_exec_dirs(self._base_dir)
        sandbox_root = self._base_dir / _workspace_key(workspace_root)
        self._acquire_lock(sandbox_root)
        try:
            worktree = sandbox_root / "ws"
            self._reset(workspace_root, sandbox_root, worktree)
            yield worktree
        finally:
            self._release_lock(sandbox_root)
```

- [ ] **Step 4: Re-run the concurrency tests and confirm they pass**

Run:
`pytest tests/test_tool_exec_reusable_sandbox.py::test_same_workspace_concurrent_acquire_fails_deterministically tests/test_tool_exec_reusable_sandbox.py::test_different_workspaces_can_acquire_independently -q`

Expected:
`PASS`

---

### Task 4: Preserve orphan cleanup and env/git semantics under reusable sandboxes

**Files:**
- Modify: `tests/test_tool_exec_process_cleanup.py`
- Modify: `tests/test_tool_exec_ephemeral_overlay.py`
- Modify: `ralph/mcp/tools/exec_sandbox.py`
- Test: `tests/test_tool_exec_process_cleanup.py`, `tests/test_tool_exec_ephemeral_overlay.py`

- [ ] **Step 1: Add failing regression tests for reusable sandbox semantics**

Append to `tests/test_tool_exec_ephemeral_overlay.py`:

```python
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
```

Append to `tests/test_tool_exec_process_cleanup.py`:

```python
def test_reusable_sandbox_does_not_skip_process_cleanup(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_command("echo", ["ok"], workspace, 1000)

    assert result.returncode == 0
```

- [ ] **Step 2: Run the regression tests to confirm any breakage before refactor**

Run:
`pytest tests/test_tool_exec_ephemeral_overlay.py tests/test_tool_exec_process_cleanup.py -q`

Expected:
Either `PASS` already, or narrowly failing assertions that reveal reusable-sandbox regressions.

- [ ] **Step 3: Tighten sandbox validation and fallback behavior**

Update `ralph/mcp/tools/exec_sandbox.py`:

```python
    def _ready_path(self, sandbox_root: Path) -> Path:
        return sandbox_root / _READY_FILE

    def _is_ready(self, sandbox_root: Path) -> bool:
        return self._ready_path(sandbox_root).is_file()

    def _reset(self, workspace_root: Path, sandbox_root: Path, worktree: Path) -> None:
        rebuild = not self._is_ready(sandbox_root)
        if rebuild and sandbox_root.exists():
            shutil.rmtree(sandbox_root)
            sandbox_root.mkdir(parents=True, exist_ok=True)
        elif worktree.exists():
            shutil.rmtree(worktree)
        _write_overlay_owner_metadata(sandbox_root)
        _mirror_workspace(workspace_root, worktree)
        _ensure_git_isolation(workspace_root, worktree, sandbox_root)
        self._ready_path(sandbox_root).write_text(json.dumps({"ready": True}), encoding="utf-8")
```

- [ ] **Step 4: Run the related exec suite and confirm all semantics hold**

Run:
`pytest tests/test_tool_exec_reusable_sandbox.py tests/test_tool_exec_ephemeral_overlay.py tests/test_tool_exec_run_command.py tests/test_tool_exec_process_cleanup.py -q`

Expected:
`PASS`

---

### Task 5: Add validated fast-reset seam and prove hot-path optimization safely

**Files:**
- Modify: `ralph/mcp/tools/exec_sandbox.py`
- Modify: `tests/test_tool_exec_reusable_sandbox.py`
- Test: `tests/test_tool_exec_reusable_sandbox.py`

- [ ] **Step 1: Add the failing test for the validated fast-reset seam**

Append to `tests/test_tool_exec_reusable_sandbox.py`:

```python
def test_reusable_sandbox_uses_fast_reset_when_previous_state_is_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
`pytest tests/test_tool_exec_reusable_sandbox.py::test_reusable_sandbox_uses_fast_reset_when_previous_state_is_valid -q`

Expected:
`FAIL` because the manager does not yet separate fast-reset and full-reset paths.

- [ ] **Step 3: Refactor to explicit `_fast_reset()` / `_full_reset()` seams with safe fallback**

Update `ralph/mcp/tools/exec_sandbox.py`:

```python
    def _full_reset(self, workspace_root: Path, sandbox_root: Path, worktree: Path) -> None:
        if sandbox_root.exists():
            shutil.rmtree(sandbox_root)
        sandbox_root.mkdir(parents=True, exist_ok=True)
        _write_overlay_owner_metadata(sandbox_root)
        _mirror_workspace(workspace_root, worktree)
        _ensure_git_isolation(workspace_root, worktree, sandbox_root)
        self._ready_path(sandbox_root).write_text(json.dumps({"ready": True}), encoding="utf-8")

    def _fast_reset(self, workspace_root: Path, sandbox_root: Path, worktree: Path) -> bool:
        if not self._is_ready(sandbox_root):
            return False
        if worktree.exists():
            shutil.rmtree(worktree)
        _write_overlay_owner_metadata(sandbox_root)
        _mirror_workspace(workspace_root, worktree)
        _ensure_git_isolation(workspace_root, worktree, sandbox_root)
        return True

    def _reset(self, workspace_root: Path, sandbox_root: Path, worktree: Path) -> None:
        if self._fast_reset(workspace_root, sandbox_root, worktree):
            return
        self._full_reset(workspace_root, sandbox_root, worktree)
```

- [ ] **Step 4: Run the fast-reset test and the broader reusable suite**

Run:
`pytest tests/test_tool_exec_reusable_sandbox.py tests/test_tool_exec_run_command.py -q`

Expected:
`PASS`

---

### Task 6: Full verification

**Files:**
- Modify: `ralph/mcp/tools/exec.py`
- Modify: `ralph/mcp/tools/exec_overlay.py`
- Modify: `ralph/mcp/tools/exec_sandbox.py`
- Modify: `tests/test_tool_exec_reusable_sandbox.py`
- Modify: `tests/test_tool_exec_ephemeral_overlay.py`
- Modify: `tests/test_tool_exec_run_command.py`
- Modify: `tests/test_tool_exec_process_cleanup.py`

- [ ] **Step 1: Run the focused exec suite**

Run:
`pytest tests/test_tool_exec_reusable_sandbox.py tests/test_tool_exec_ephemeral_overlay.py tests/test_tool_exec_run_command.py tests/test_tool_exec_process_cleanup.py -q`

Expected:
`PASS`

- [ ] **Step 2: Run repository verification**

Run:
`make verify`

Expected:
`PASS` with no lint, type, or test failures.

- [ ] **Step 3: Inspect the final changed surface**

Run:
`git diff -- ralph/mcp/tools/exec.py ralph/mcp/tools/exec_overlay.py ralph/mcp/tools/exec_sandbox.py tests/test_tool_exec_reusable_sandbox.py tests/test_tool_exec_ephemeral_overlay.py tests/test_tool_exec_run_command.py tests/test_tool_exec_process_cleanup.py`

Expected:
- reusable sandbox logic is isolated to the exec sandbox path
- no public exec API drift
- tests prove reuse, reset, concurrency, fallback, and performance seam behavior

---

## Spec Coverage Check

- **Reusable per-workspace sandbox pool:** Tasks 1-2
- **Portable full reset contract:** Tasks 2 and 4
- **Same-workspace concurrency safety and pool growth:** Task 3
- **Cross-workspace concurrency:** Task 3
- **Git isolation preserved:** Tasks 2 and 4
- **Hot-path optimization with validated fast-reset seam:** Task 5
- **Strict TDD:** every task begins with failing tests and explicit red/green commands
- **Full verification:** Task 6

## Placeholder Scan

- No TODO/TBD placeholders remain.
- Every step includes exact file paths and concrete commands.
- New types and method names (`ExecSandboxManager`, `ExecSandboxBusyError`, `_fast_reset`, `_full_reset`) are introduced before later tasks depend on them.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-27-exec-reusable-resettable-sandbox.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
