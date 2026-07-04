# FS Churn Reduction (fseventsd CPU) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the filesystem-event volume Ralph Workflow generates during long runs so macOS `fseventsd` stops burning a full core, by batching per-line writes, closing write-handle churn, pruning unbounded `.agent` accumulation, and consolidating machine-only bookkeeping into a per-workspace SQLite database.

**Architecture:** Three layers of fix. (1) Hot-path batching: `RawOverflowLog` gets a persistent buffered file handle with a time-based flush instead of `mkdir`+`open`+`close` per agent output line; loguru file sinks get block buffering. (2) Retention: a run-start sweep deletes accumulated `completion_seen_*.json`, stale receipt dirs, and `agent_retry_*` scratch. (3) Consolidation: receipts and completion sentinels move from one-file-per-event under `.agent/` into a single WAL-mode SQLite DB (`.agent/state.db`), eliminating file-create events entirely for those paths. Agent-facing artifacts (PLAN.md, prompts, artifact JSON, spill files) intentionally stay as plain files — agents read them with workspace file tools.

**Tech Stack:** Python ≥3.12, stdlib `sqlite3` (WAL mode), loguru, pytest.

## Diagnosis (evidence, 2026-07-03)

Measured on a live host with 6 concurrent `ralph` runs on an external APFS volume:

- `fseventsd` at 102% CPU, 8 GB RSS, 1383 CPU-minutes since Jun 24; the volume's `.fseventsd` journal held **29,121 files / 1.0 GB** (healthy is a few MB).
- Spotlight indexing is **enabled** on the volume (`mdutil -s`), so `mds_stores` re-scans every churned path (627 CPU-minutes).
- Ralph-owned event sources, per instance:
  - `RawOverflowLog.append()` (`ralph/display/raw_overflow.py:47`) does `mkdir` + `open("ab")` + `write` + `close` **per agent stdout line**. Live files reach 10 MB+ (≈50–100k open/close cycles per unit). Four construction sites all funnel through it (`subprocess_executor.py:99`, `_process_reader.py:225`, `_pty_line_reader.py:1061`, `parallel_display.py:1244`).
  - loguru file sinks default to `buffering=1` — one write syscall per record per sink (main log + optional JSONL + per-worker sinks).
  - Unbounded accumulation under `.agent/`: ~170 `completion_seen_<uuid>.json` (50 B each), 176 `receipts/<run_id>/` dirs, dozens of `tmp/agent_retry_*.md`, exec spills — never swept.
  - Per-phase file create/delete: prompt materialization, drain clearing, checkpoint `.tmp`+rename.
- Workspace churn Ralph *drives* but doesn't own: verify loops make the project-under-test write `log/test.log` at ~80–120 KB/s, create Active-Storage blobs under `tmp/storage/`, git objects, and full docs rebuilds (hundreds of HTML files per cycle).

Checkpoint saves are event-driven and infrequent — **not** a churn driver. `.ralph/run.json` is written by Ralph-Workflow-Pro, not the engine.

## Immediate operator mitigations (no code — do these first)

These address the volume-level pathology today; Task 7 documents them permanently:

1. Disable Spotlight on the workspace volume: `sudo mdutil -i off "/Volumes/Crucial X9"` (biggest consumer-side win; the volume is a scratch/work disk, not searched via Spotlight).
2. Reset the bloated event journal: with runs stopped, `sudo rm -rf "/Volumes/Crucial X9/.fseventsd"` then remount (or reboot). fseventsd recreates a fresh journal.
3. Optional, aggressive: `sudo touch "/Volumes/Crucial X9/.fseventsd/no_log"` disables on-disk event journaling for that volume (live FSEvents subscribers still work; Time Machine and Spotlight on that volume degrade to full rescans — acceptable for a scratch volume).
4. In the projects under test, keep test logs bounded (e.g. Rails `config.logger` with rotation, or truncate `log/test.log` in the verify script).

## Global Constraints

- Python `>=3.12` (`pyproject.toml`); stdlib `sqlite3` only — no new dependencies.
- Run tests with `make test-unit` (pytest) from `ralph-workflow/`; lint with `make lint`.
- The idle watchdog corroborates liveness from `RawOverflowLog.size_bytes` (in-memory counter) with `LOG_GROWTH_SECONDS = 30.0` (`ralph/timeout_defaults.py:250`). The buffered flush interval MUST stay well below 30 s (default 5 s) and `size_bytes` MUST keep updating per `append()` (not per flush).
- `.agent/state.db*` (db/-wal/-shm) must be classified as engine cache by `ralph/agents/invoke/_workspace_change_classifier.py` `CACHE_FILENAME_GLOBS`, like `completion_seen_*.json` is today.
- Receipts/sentinels keep their HMAC anti-forgery semantics (`completion_receipts.py` docstring): the broker-owned secret never reaches the agent; verification must fail closed.
- `ralph/testing/audit_agent_internal_paths.py` guards internal-path references — update its allowlist when paths change.

---

### Task 1: Persistent buffered handle in `RawOverflowLog`

The hot-path fix: one open file handle per unit, 64 KB userspace buffer, time-based flush.

**Files:**
- Modify: `ralph/display/raw_overflow.py`
- Test: `tests/test_raw_overflow.py`

**Interfaces:**
- Produces: `RawOverflowLog(workspace_root, unit_id, *, max_bytes=..., flush_interval_seconds=5.0, now=time.monotonic)`, new methods `flush() -> None` and `close() -> None`. `append()`/`size_bytes`/`disable()`/`relative_reference()` signatures unchanged. Task 2 wires `close()` into the four owner sites.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_raw_overflow.py`:

```python
def test_append_keeps_handle_open_and_buffers(tmp_path: Path) -> None:
    """Writes are buffered; flush() makes them visible on disk."""
    log = RawOverflowLog(tmp_path, "unit-1", flush_interval_seconds=3600.0)
    log.append("buffered line")
    # size_bytes must track appends immediately (watchdog liveness contract)
    assert log.size_bytes == len("buffered line\n".encode())
    log.flush()
    assert "buffered line\n" in log.path.read_text(encoding="utf-8")
    log.close()


def test_time_based_flush(tmp_path: Path) -> None:
    fake_time = [0.0]
    log = RawOverflowLog(
        tmp_path, "unit-1", flush_interval_seconds=5.0, now=lambda: fake_time[0]
    )
    log.append("first")
    fake_time[0] = 6.0
    log.append("second")  # crosses the interval -> flush
    content = log.path.read_text(encoding="utf-8")
    assert "first\n" in content
    assert "second\n" in content
    log.close()


def test_close_flushes_and_reopen_appends(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1", flush_interval_seconds=3600.0)
    log.append("before close")
    log.close()
    assert "before close\n" in log.path.read_text(encoding="utf-8")
    log.append("after close")  # reopens in append mode
    log.close()
    content = log.path.read_text(encoding="utf-8")
    assert "before close\n" in content
    assert "after close\n" in content


def test_close_is_idempotent(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("x")
    log.close()
    log.close()  # no raise
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests/test_raw_overflow.py -v`
Expected: new tests FAIL (`TypeError: unexpected keyword argument 'flush_interval_seconds'`); existing tests PASS.

- [ ] **Step 3: Rewrite `RawOverflowLog` with a persistent handle**

Replace the class body in `ralph/display/raw_overflow.py` (keep module docstring, `_sanitize_unit_id`, `DEFAULT_MAX_OVERFLOW_FILE_BYTES`):

```python
"""Per-unit raw NDJSON overflow log writer."""

from __future__ import annotations

import re
import threading
import time
from typing import TYPE_CHECKING, BinaryIO, Callable

if TYPE_CHECKING:
    from pathlib import Path

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")
DEFAULT_MAX_OVERFLOW_FILE_BYTES = 50 * 1024 * 1024
#: Userspace buffer for the persistent handle. Amortizes write syscalls
#: (and the fsevents they generate) across many appended lines.
_BUFFER_BYTES = 64 * 1024
#: Default seconds between forced flushes. MUST stay well below
#: ralph.timeout_defaults.LOG_GROWTH_SECONDS (30.0): operators tail this
#: file and the on-disk copy must never look wedged while the unit is live.
DEFAULT_FLUSH_INTERVAL_SECONDS = 5.0


def _sanitize_unit_id(unit_id: str) -> str:
    return _SAFE_CHARS.sub("_", unit_id)


class RawOverflowLog:
    """Append-mode raw log for a single work unit.

    Thread-safe. Holds one buffered file handle open for the unit's
    lifetime instead of opening/closing per line (the per-line pattern
    generated an fsevent storm on long runs). Silently no-ops on
    filesystem errors so the display path never crashes due to a
    read-only workspace.
    """

    def __init__(
        self,
        workspace_root: Path,
        unit_id: str,
        *,
        max_bytes: int = DEFAULT_MAX_OVERFLOW_FILE_BYTES,
        flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        safe_id = _sanitize_unit_id(unit_id)
        self.path = workspace_root / ".agent" / "raw" / f"{safe_id}.log"
        self._lock = threading.Lock()
        self._first_write = True
        self._disabled = False
        self._max_bytes = max(max_bytes, 0)
        self._bytes_written = 0
        self._flush_interval = max(flush_interval_seconds, 0.0)
        self._now = now
        self._fh: BinaryIO | None = None
        self._last_flush = now()

    def disable(self) -> None:
        """Permanently disable this log so future appends are no-ops."""
        with self._lock:
            self._close_locked()
            self._disabled = True

    def append(self, line: str) -> bool:
        """Write *line* to the overflow log.

        Returns True when the line was written. Returns False when the log is
        disabled, the byte cap has been reached, or an I/O error occurs.
        """
        with self._lock:
            if self._disabled:
                return False
            try:
                text = line.rstrip("\n") + "\n"
                encoded = text.encode("utf-8")
                if self._bytes_written + len(encoded) > self._max_bytes:
                    self._close_locked()
                    self._disabled = True
                    return False
                if self._fh is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    mode = "wb" if self._first_write else "ab"
                    self._fh = self.path.open(mode, buffering=_BUFFER_BYTES)
                    self._first_write = False
                self._fh.write(encoded)
                self._bytes_written += len(encoded)
                if self._now() - self._last_flush >= self._flush_interval:
                    self._fh.flush()
                    self._last_flush = self._now()
                return True
            except (OSError, PermissionError):
                self._close_locked()
                self._disabled = True
                return False

    def flush(self) -> None:
        """Force buffered bytes to disk. Never raises."""
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.flush()
                    self._last_flush = self._now()
                except (OSError, PermissionError):
                    self._close_locked()
                    self._disabled = True

    def close(self) -> None:
        """Flush and release the file handle. Idempotent; appends may reopen."""
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except (OSError, PermissionError):
                pass
            self._fh = None

    def relative_reference(self, workspace_root: Path) -> str:
        """Return POSIX path relative to *workspace_root*, or absolute on error."""
        try:
            return self.path.relative_to(workspace_root).as_posix()
        except ValueError:
            return self.path.as_posix()

    @property
    def size_bytes(self) -> int:
        """Bytes appended so far (buffered bytes included).

        The idle watchdog's log-growth corroborator reads this to prove the
        unit is alive; it must advance on every append, not only on flush.
        Returns 0 before the first write. Never raises.
        """
        if self._first_write:
            return 0
        return self._bytes_written

    @property
    def is_disabled(self) -> bool:
        """True when the log has been permanently disabled (byte cap reached or I/O error)."""
        return self._disabled


__all__ = [
    "DEFAULT_FLUSH_INTERVAL_SECONDS",
    "DEFAULT_MAX_OVERFLOW_FILE_BYTES",
    "RawOverflowLog",
]
```

Note: the old `size_bytes` did a `stat()` probe and returned 0 when the file vanished; the probe existed only to notice external deletion. The in-memory counter is now authoritative (buffered bytes must count — see contract above). Check `git grep -n "size_bytes" ralph tests` and update any test asserting the stat-probe behavior.

- [ ] **Step 4: Update existing tests that read the file right after `append()`**

In `tests/test_raw_overflow.py`, the pre-existing tests (`test_append_writes_lines`, `test_first_write_truncates_previous_content`, `test_unit_id_sanitization`, `test_relative_reference`, and any other test reading `log.path`) must call `log.flush()` (or `log.close()`) before reading `log.path`. In `test_first_write_truncates_previous_content` specifically, call `log1.close()` BEFORE constructing `log2`: with persistent handles, a still-open `log1` holds buffered bytes against the same inode `log2` truncates, and a late GC flush from `log1` would race the assertion. Example:

```python
def test_append_writes_lines(tmp_path: Path) -> None:
    log = RawOverflowLog(tmp_path, "unit-1")
    log.append("line one")
    log.append("line two")
    log.flush()
    content = log.path.read_text(encoding="utf-8")
    assert "line one\n" in content
    assert "line two\n" in content
```

- [ ] **Step 5: Run the full raw-overflow test file**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests/test_raw_overflow.py -v`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add ralph/display/raw_overflow.py tests/test_raw_overflow.py
git commit -m "perf(display): persistent buffered handle in RawOverflowLog"
```

---

### Task 2: Close raw overflow handles at unit/reader teardown

Without explicit close, buffered tails only reach disk at GC/exit.

**Files:**
- Modify: `ralph/agents/subprocess_executor.py` (`drop_unit`, ~line 104)
- Modify: `ralph/agents/invoke/_process_reader.py` (owner of `self._raw_overflow`, constructed ~line 225)
- Modify: `ralph/agents/invoke/_pty_line_reader.py` (owner of `self._raw_overflow`, constructed ~line 1061)
- Modify: `ralph/display/parallel_display.py` (owner of `self._overflow_logs`, constructed ~line 1244)
- Test: `tests/test_raw_overflow.py` (executor-level test), existing suites `tests/display/test_parallel_display_drop_unit.py`

**Interfaces:**
- Consumes: `RawOverflowLog.close()` from Task 1.
- Produces: no new public API; teardown behavior only.

- [ ] **Step 1: Write the failing test for `drop_unit` closing the log**

Append to `tests/test_raw_overflow.py`:

```python
def test_executor_drop_unit_closes_raw_log(tmp_path: Path) -> None:
    from ralph.agents.subprocess_executor import SubprocessAgentExecutor

    executor = SubprocessAgentExecutor.__new__(SubprocessAgentExecutor)
    executor._raw_logs = {}
    executor._raw_overflow_root = tmp_path
    executor._cwd = tmp_path
    log = executor._get_raw_log("unit-x")
    log.append("pending line")
    executor.drop_unit("unit-x")
    # close() during drop must have flushed the buffered tail
    assert "pending line\n" in (
        tmp_path / ".agent" / "raw" / "unit-x.log"
    ).read_text(encoding="utf-8")
```

(If `SubprocessAgentExecutor.__new__` needs more attributes, set only the ones `_get_raw_log`/`drop_unit` touch — currently `_raw_logs`, `_raw_overflow_root`, `_cwd`.)

- [ ] **Step 2: Run it to verify it fails**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests/test_raw_overflow.py::test_executor_drop_unit_closes_raw_log -v`
Expected: FAIL — file missing or missing the buffered tail.

- [ ] **Step 3: Wire `close()` into the four owners**

`ralph/agents/subprocess_executor.py` — in `drop_unit`:

```python
    def drop_unit(self, unit_id: str) -> None:
        """Release per-unit state so long parallel sessions don't accumulate state across waves."""
        raw_log = self._raw_logs.pop(unit_id, None)
        if raw_log is not None:
            raw_log.close()
```

`ralph/agents/invoke/_process_reader.py` and `ralph/agents/invoke/_pty_line_reader.py`: find each reader's teardown (the `finally:` block of the read/run method that resets per-invocation state — grep `self._raw_overflow` for the owning class, then locate that method's `finally`). Add:

```python
        self._raw_overflow.close()
```

`ralph/display/parallel_display.py`: in the unit-drop path that removes entries from `self._overflow_logs` (see `tests/display/test_parallel_display_drop_unit.py` for the entry point), close before discarding:

```python
        overflow = self._overflow_logs.pop(unit_id, None)
        if overflow is not None:
            overflow.close()
```

If the display has no drop path for `_overflow_logs` today, add the close in the same method that drops other per-unit display state.

- [ ] **Step 4: Run the executor test and neighbors**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests/test_raw_overflow.py tests/display/test_parallel_display_drop_unit.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Run the agents + display unit suites to catch regressions**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests/agents tests/display -x -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ralph/agents/subprocess_executor.py ralph/agents/invoke/_process_reader.py ralph/agents/invoke/_pty_line_reader.py ralph/display/parallel_display.py tests/test_raw_overflow.py
git commit -m "perf(agents): close raw overflow handles at unit teardown"
```

---

### Task 3: Run-start retention sweep for `.agent` accumulation

Deletes accumulated machine-only bookkeeping from prior runs: `completion_seen_*.json` at `.agent/` root, `receipts/<run_id>/` dirs, `tmp/agent_retry_*.md` / `tmp/agent_retry_context_*.md`.

**Files:**
- Create: `ralph/workspace/agent_dir_retention.py`
- Modify: `ralph/config/bootstrap.py` (call the sweep where the workspace `.agent` scaffolding is prepared at run start)
- Test: `tests/unit/test_agent_dir_retention.py`

**Interfaces:**
- Produces: `sweep_agent_dir(workspace_root: Path, *, keep_run_id: str | None, max_age_seconds: float = 7 * 24 * 3600.0, now: Callable[[], float] = time.time) -> int` — returns count of entries removed. Never raises.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_agent_dir_retention.py`:

```python
"""Tests for the run-start .agent retention sweep."""

from __future__ import annotations

import os
from pathlib import Path

from ralph.workspace.agent_dir_retention import sweep_agent_dir

_WEEK = 7 * 24 * 3600.0


def _make_aged(path: Path, age_seconds: float, now: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    stamp = now - age_seconds
    os.utime(path, (stamp, stamp))


def test_removes_old_completion_sentinels_keeps_current(tmp_path: Path) -> None:
    now = 1_000_000_000.0
    agent = tmp_path / ".agent"
    _make_aged(agent / "completion_seen_old.json", _WEEK + 10, now)
    _make_aged(agent / "completion_seen_current.json", _WEEK + 10, now)
    _make_aged(agent / "completion_seen_fresh.json", 60.0, now)

    removed = sweep_agent_dir(tmp_path, keep_run_id="current", now=lambda: now)

    assert not (agent / "completion_seen_old.json").exists()
    assert (agent / "completion_seen_current.json").exists()  # current run kept
    assert (agent / "completion_seen_fresh.json").exists()  # too young
    assert removed == 1


def test_removes_old_receipt_dirs(tmp_path: Path) -> None:
    now = 1_000_000_000.0
    _make_aged(tmp_path / ".agent" / "receipts" / "old-run" / "plan.json", _WEEK + 10, now)
    _make_aged(tmp_path / ".agent" / "receipts" / "current" / "plan.json", _WEEK + 10, now)

    sweep_agent_dir(tmp_path, keep_run_id="current", now=lambda: now)

    assert not (tmp_path / ".agent" / "receipts" / "old-run").exists()
    assert (tmp_path / ".agent" / "receipts" / "current" / "plan.json").exists()


def test_removes_old_agent_retry_scratch(tmp_path: Path) -> None:
    now = 1_000_000_000.0
    _make_aged(tmp_path / ".agent" / "tmp" / "agent_retry_abc.md", _WEEK + 10, now)
    _make_aged(tmp_path / ".agent" / "tmp" / "agent_retry_context_abc.md", _WEEK + 10, now)
    _make_aged(tmp_path / ".agent" / "tmp" / "development_prompt.md", _WEEK + 10, now)

    sweep_agent_dir(tmp_path, keep_run_id=None, now=lambda: now)

    assert not (tmp_path / ".agent" / "tmp" / "agent_retry_abc.md").exists()
    assert not (tmp_path / ".agent" / "tmp" / "agent_retry_context_abc.md").exists()
    # non-matching files untouched
    assert (tmp_path / ".agent" / "tmp" / "development_prompt.md").exists()


def test_missing_agent_dir_is_noop(tmp_path: Path) -> None:
    assert sweep_agent_dir(tmp_path, keep_run_id=None) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests/unit/test_agent_dir_retention.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the sweep**

Create `ralph/workspace/agent_dir_retention.py`:

```python
"""Run-start retention sweep for machine-only ``.agent`` bookkeeping.

Long-lived workspaces accumulate one ``completion_seen_<run_id>.json``
per agent session, one ``receipts/<run_id>/`` directory per run, and
``agent_retry_*`` scratch per retry — hundreds of files over multi-day
runs. Nothing reads them after their run ends. The sweep deletes
entries older than ``max_age_seconds`` (default 7 days), always keeping
the current run's entries regardless of age.

Everything here is best-effort: a failed unlink is skipped, never raised,
so a permission quirk cannot break run startup.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Callable

DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600.0

_SCRATCH_GLOBS: tuple[str, ...] = (
    "agent_retry_*.md",
    "agent_retry_context_*.md",
)


def _older_than(path: Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def sweep_agent_dir(
    workspace_root: Path,
    *,
    keep_run_id: str | None,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    now: Callable[[], float] = time.time,
) -> int:
    """Delete aged machine-only bookkeeping under ``<workspace>/.agent``.

    Args:
        workspace_root: Workspace root containing ``.agent``.
        keep_run_id: Current run id whose sentinel/receipts are always kept.
        max_age_seconds: Entries younger than this are kept.
        now: Clock injection for tests.

    Returns:
        Number of filesystem entries removed.
    """
    agent_dir = workspace_root / ".agent"
    if not agent_dir.is_dir():
        return 0
    cutoff = now() - max_age_seconds
    removed = 0

    keep_sentinel = (
        f"completion_seen_{keep_run_id}.json" if keep_run_id is not None else None
    )
    for sentinel in agent_dir.glob("completion_seen_*.json"):
        if sentinel.name == keep_sentinel or not _older_than(sentinel, cutoff):
            continue
        try:
            sentinel.unlink()
            removed += 1
        except OSError:
            continue

    receipts_dir = agent_dir / "receipts"
    if receipts_dir.is_dir():
        for run_dir in receipts_dir.iterdir():
            if not run_dir.is_dir() or run_dir.name == keep_run_id:
                continue
            if not _older_than(run_dir, cutoff):
                continue
            try:
                shutil.rmtree(run_dir)
                removed += 1
            except OSError:
                continue

    tmp_dir = agent_dir / "tmp"
    if tmp_dir.is_dir():
        for pattern in _SCRATCH_GLOBS:
            for scratch in tmp_dir.glob(pattern):
                if not _older_than(scratch, cutoff):
                    continue
                try:
                    scratch.unlink()
                    removed += 1
                except OSError:
                    continue

    return removed


__all__ = ["DEFAULT_MAX_AGE_SECONDS", "sweep_agent_dir"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests/unit/test_agent_dir_retention.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Hook the sweep into run start**

In `ralph/config/bootstrap.py`, locate the function that prepares the workspace `.agent` scaffolding / gitignore entries at run start (the one calling `_atomic_append_text(exclude_path, ...)` around line 556 is in the right area; pick the single entry point that runs once per `ralph` invocation with the workspace root and run id in scope). Add:

```python
from ralph.workspace.agent_dir_retention import sweep_agent_dir

    removed = sweep_agent_dir(workspace_root, keep_run_id=run_id)
    if removed:
        logger.debug("Retention sweep removed {} stale .agent entries", removed)
```

If the run id is not in scope at that point, pass `keep_run_id=None` — age alone protects the active run because its files are always younger than 7 days... EXCEPT for runs that themselves last longer than the window, so prefer the call site where `run_id` exists. Verify by running a dry `ralph --help`-level smoke and checking no sweep happens without a workspace.

- [ ] **Step 6: Run bootstrap/config test suites**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests/config tests/unit -x -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ralph/workspace/agent_dir_retention.py ralph/config/bootstrap.py tests/unit/test_agent_dir_retention.py
git commit -m "perf(workspace): run-start retention sweep for .agent bookkeeping"
```

---

### Task 4: Block-buffer the high-volume loguru sinks

Per-worker sinks and the structured JSONL sink go from `buffering=1` (loguru default, one syscall per record) to 8 KB block buffering. The operator-facing `ralph.log` stays line-buffered so `tail -f` remains live.

**Files:**
- Modify: `ralph/logging_worker_sink.py:39`
- Modify: `ralph/logging.py:154` (structured sink `logger.add`)
- Test: `tests/unit/test_logging_buffering.py` (create)

**Interfaces:**
- Consumes: loguru `logger.add(path, ..., buffering=N)` (kwargs forwarded to `open()`).
- Produces: no API change; `logger.complete()` / `logger.remove(sink_id)` flushes as before (loguru closes the file on remove).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_logging_buffering.py`:

```python
"""Buffered file sinks still deliver records after sink removal (flush-on-close)."""

from __future__ import annotations

from pathlib import Path

from ralph.logging_worker_sink import bind_worker_sink, remove_worker_sink
from loguru import logger


def test_worker_sink_flushes_on_remove(tmp_path: Path) -> None:
    handle = bind_worker_sink("unit-9", tmp_path, run_id="run-1")
    logger.bind(unit_id="unit-9").info("hello worker")
    remove_worker_sink(handle)  # closes the file -> flush
    assert "hello worker" in handle.log_path.read_text(encoding="utf-8")


def test_worker_sink_uses_block_buffering(tmp_path: Path) -> None:
    handle = bind_worker_sink("unit-8", tmp_path, run_id="run-1")
    try:
        logger.bind(unit_id="unit-8").info("small record")
        # A single small record must NOT hit disk immediately under
        # block buffering (this is the syscall-batching property).
        assert handle.log_path.stat().st_size == 0
    finally:
        remove_worker_sink(handle)
```

- [ ] **Step 2: Run to verify the buffering assertion fails**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests/unit/test_logging_buffering.py -v`
Expected: `test_worker_sink_uses_block_buffering` FAILS (record visible immediately under line buffering); the flush-on-remove test passes.

- [ ] **Step 3: Add `buffering=8192` to the two sinks**

`ralph/logging_worker_sink.py:39`:

```python
    sink_id = logger.add(
        log_path,
        filter=worker_filter,
        format="{time} {level} {message}",
        buffering=8192,
    )
```

`ralph/logging.py` structured sink (`logger.add(structured_log_path, ...)`):

```python
        logger.add(
            structured_log_path,
            level=level,
            serialize=True,
            backtrace=True,
            diagnose=False,
            rotation=config.rotation,
            buffering=8192,
        )
```

Leave the `text_log_path` handler unchanged (line-buffered, operators tail it). Trade-off accepted: on SIGKILL up to 8 KB of worker/JSONL tail is lost; on clean exit and `logger.remove()` everything flushes.

- [ ] **Step 4: Run tests**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests/unit/test_logging_buffering.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add ralph/logging_worker_sink.py ralph/logging.py tests/unit/test_logging_buffering.py
git commit -m "perf(logging): block-buffer worker and structured sinks"
```

---

### Task 5: `RunStateDB` — per-workspace SQLite for machine-only bookkeeping

The embedded-database decision: **yes, narrowly.** One WAL-mode SQLite file at `.agent/state.db` replaces one-file-per-event bookkeeping (receipts in Task 6, completion sentinels in Task 7). It must NOT absorb agent-facing files (PLAN.md, prompts, artifact JSON, spill files) — agents read those with plain file tools; that contract stays.

Why SQLite fits here: receipts/sentinels are cross-process (MCP server writes, engine reads) — WAL handles that on local APFS; writes become row upserts to one inode instead of `mkdir`+create per event; accumulation becomes `DELETE WHERE`, not directory sweeps; atomicity without the `.tmp`+rename dance.

**Files:**
- Create: `ralph/mcp/artifacts/state_db.py`
- Test: `tests/unit/test_state_db.py`

**Interfaces:**
- Produces (consumed by Tasks 6–7):

```python
class RunStateDB:
    def __init__(self, workspace_root: Path) -> None: ...   # opens/creates .agent/state.db
    def upsert_receipt(self, run_id: str, artifact_type: str, hmac_hex: str | None) -> None: ...
    def get_receipt_hmac(self, run_id: str, artifact_type: str) -> str | None | _Missing: ...
    def delete_receipt(self, run_id: str, artifact_type: str) -> None: ...
    def clear_run_receipts(self, run_id: str) -> None: ...
    def upsert_completion_sentinel(self, run_id: str, hmac_hex: str | None) -> None: ...
    def get_completion_sentinel_hmac(self, run_id: str) -> str | None | _Missing: ...
    def delete_completion_sentinel(self, run_id: str) -> None: ...
    def close(self) -> None: ...
```

`MISSING` (of private type `_Missing`) is a module-level sentinel distinguishing "row absent" from "row present with NULL hmac".

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_state_db.py`:

```python
"""Tests for the per-workspace SQLite bookkeeping store."""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.artifacts.state_db import MISSING, RunStateDB


def test_receipt_roundtrip(tmp_path: Path) -> None:
    db = RunStateDB(tmp_path)
    assert db.get_receipt_hmac("run-1", "plan") is MISSING
    db.upsert_receipt("run-1", "plan", "abc123")
    assert db.get_receipt_hmac("run-1", "plan") == "abc123"
    db.delete_receipt("run-1", "plan")
    assert db.get_receipt_hmac("run-1", "plan") is MISSING
    db.close()


def test_receipt_null_hmac_distinct_from_missing(tmp_path: Path) -> None:
    db = RunStateDB(tmp_path)
    db.upsert_receipt("run-1", "plan", None)
    assert db.get_receipt_hmac("run-1", "plan") is None
    db.close()


def test_clear_run_receipts_scoped_to_run(tmp_path: Path) -> None:
    db = RunStateDB(tmp_path)
    db.upsert_receipt("run-1", "plan", "a")
    db.upsert_receipt("run-1", "issues", "b")
    db.upsert_receipt("run-2", "plan", "c")
    db.clear_run_receipts("run-1")
    assert db.get_receipt_hmac("run-1", "plan") is MISSING
    assert db.get_receipt_hmac("run-1", "issues") is MISSING
    assert db.get_receipt_hmac("run-2", "plan") == "c"
    db.close()


def test_completion_sentinel_roundtrip(tmp_path: Path) -> None:
    db = RunStateDB(tmp_path)
    assert db.get_completion_sentinel_hmac("run-1") is MISSING
    db.upsert_completion_sentinel("run-1", "sig")
    assert db.get_completion_sentinel_hmac("run-1") == "sig"
    db.delete_completion_sentinel("run-1")
    assert db.get_completion_sentinel_hmac("run-1") is MISSING
    db.close()


def test_cross_connection_visibility(tmp_path: Path) -> None:
    """Simulates MCP-server-writes / engine-reads across processes."""
    writer = RunStateDB(tmp_path)
    reader = RunStateDB(tmp_path)
    writer.upsert_receipt("run-1", "plan", "sig")
    assert reader.get_receipt_hmac("run-1", "plan") == "sig"
    writer.close()
    reader.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests/unit/test_state_db.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `RunStateDB`**

Create `ralph/mcp/artifacts/state_db.py`:

```python
"""Per-workspace SQLite store for machine-only run bookkeeping.

Replaces one-file-per-event bookkeeping under ``.agent/`` (receipts,
completion sentinels) with a single WAL-mode database at
``.agent/state.db``. Motivation: on long multi-instance runs the
per-event file creates were a measurable share of the macOS fseventsd
event storm, and the files accumulated without bound.

Scope rule: ONLY machine-only state belongs here. Anything an agent or
a human reads through workspace file tools (PLAN.md, prompts, artifact
JSON, exec spills) stays a plain file.

Concurrency: the MCP server process writes while the engine process
reads. WAL mode plus a busy timeout covers that on a local filesystem.
Every public method opens no extra connections; one connection per
RunStateDB instance, serialized by SQLite itself.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Final


class _Missing:
    """Sentinel type distinguishing 'row absent' from 'hmac is NULL'."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "<MISSING>"


MISSING: Final = _Missing()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    run_id        TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    hmac          TEXT,
    created_at    REAL NOT NULL DEFAULT (unixepoch('subsec')),
    PRIMARY KEY (run_id, artifact_type)
);
CREATE TABLE IF NOT EXISTS completion_sentinels (
    run_id     TEXT PRIMARY KEY,
    hmac       TEXT,
    created_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
);
"""

DB_RELPATH = ".agent/state.db"


class RunStateDB:
    """Handle to the workspace bookkeeping database (create-on-open)."""

    def __init__(self, workspace_root: Path) -> None:
        db_path = workspace_root / DB_RELPATH
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, timeout=5.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- receipts ---------------------------------------------------------

    def upsert_receipt(self, run_id: str, artifact_type: str, hmac_hex: str | None) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO receipts (run_id, artifact_type, hmac) VALUES (?, ?, ?) "
                "ON CONFLICT(run_id, artifact_type) DO UPDATE SET hmac=excluded.hmac",
                (run_id, artifact_type, hmac_hex),
            )

    def get_receipt_hmac(self, run_id: str, artifact_type: str) -> str | None | _Missing:
        row = self._conn.execute(
            "SELECT hmac FROM receipts WHERE run_id = ? AND artifact_type = ?",
            (run_id, artifact_type),
        ).fetchone()
        if row is None:
            return MISSING
        return row[0]

    def delete_receipt(self, run_id: str, artifact_type: str) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM receipts WHERE run_id = ? AND artifact_type = ?",
                (run_id, artifact_type),
            )

    def clear_run_receipts(self, run_id: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM receipts WHERE run_id = ?", (run_id,))

    # -- completion sentinels ---------------------------------------------

    def upsert_completion_sentinel(self, run_id: str, hmac_hex: str | None) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO completion_sentinels (run_id, hmac) VALUES (?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET hmac=excluded.hmac",
                (run_id, hmac_hex),
            )

    def get_completion_sentinel_hmac(self, run_id: str) -> str | None | _Missing:
        row = self._conn.execute(
            "SELECT hmac FROM completion_sentinels WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return MISSING
        return row[0]

    def delete_completion_sentinel(self, run_id: str) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM completion_sentinels WHERE run_id = ?", (run_id,)
            )

    def close(self) -> None:
        self._conn.close()


__all__ = ["DB_RELPATH", "MISSING", "RunStateDB"]
```

- [ ] **Step 4: Run tests**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests/unit/test_state_db.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Classify `state.db*` as engine cache for the workspace-change classifier**

In `ralph/agents/invoke/_workspace_change_classifier.py:87` extend:

```python
CACHE_FILENAME_GLOBS: tuple[str, ...] = (
    "completion_seen_*.json",
    "state.db",
    "state.db-wal",
    "state.db-shm",
)
```

Also add `.agent/state.db` to the internal-path allowlist in `ralph/testing/audit_agent_internal_paths.py` (same list that has `.agent/tmp/mcp-server.log`, line ~565).

- [ ] **Step 6: Run classifier + audit suites**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests/agents -k "classifier" -q && .venv/bin/python -m pytest tests -k "audit_agent_internal" -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ralph/mcp/artifacts/state_db.py ralph/agents/invoke/_workspace_change_classifier.py ralph/testing/audit_agent_internal_paths.py tests/unit/test_state_db.py
git commit -m "feat(artifacts): WAL SQLite RunStateDB for machine-only bookkeeping"
```

---

### Task 6: Back receipts with `RunStateDB` (public API unchanged)

Swap the storage inside `ralph/mcp/artifacts/completion_receipts.py`; every caller keeps working because `write_artifact_receipt` / `artifact_receipt_present` / `delete_artifact_receipt` / `clear_run_receipts` keep their signatures. Legacy file receipts remain readable for one release (fallback read), so an in-flight run surviving an upgrade still passes its completion gate.

**Files:**
- Modify: `ralph/mcp/artifacts/completion_receipts.py`
- Test: existing receipt tests (find with `git grep -ln "artifact_receipt_present\|write_artifact_receipt" tests/`) plus new fallback test in the same file(s).

**Interfaces:**
- Consumes: `RunStateDB`, `MISSING` from Task 5.
- Produces: unchanged public functions; the `backend: FileBackend` parameter stays accepted (used only for the legacy-read fallback).

- [ ] **Step 1: Add a failing test for DB-backed write + legacy fallback**

In the existing receipts test module, add:

```python
def test_receipt_written_to_db_not_files(tmp_path: Path) -> None:
    write_artifact_receipt(tmp_path, "run-1", "plan", receipt_secret="s3cret")
    # No per-run receipt directory should be created anymore
    assert not (tmp_path / ".agent" / "receipts").exists()
    assert artifact_receipt_present(tmp_path, "run-1", "plan", receipt_secret="s3cret")
    assert not artifact_receipt_present(tmp_path, "run-1", "plan", receipt_secret="wrong")


def test_legacy_file_receipt_still_honored(tmp_path: Path) -> None:
    # Simulate a receipt written by the previous release
    legacy = tmp_path / ".agent" / "receipts" / "run-1" / "plan.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"run_id": "run-1", "artifact_type": "plan"}))
    assert artifact_receipt_present(tmp_path, "run-1", "plan")
```

- [ ] **Step 2: Run to verify the first test fails**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests -k "receipt" -v`
Expected: `test_receipt_written_to_db_not_files` FAILS (files still created).

- [ ] **Step 3: Swap the internals**

In `completion_receipts.py`, keep `_receipt_hmac`, `_receipt_dir`, `_receipt_path` (legacy fallback) and reimplement the four public functions:

```python
from ralph.mcp.artifacts.state_db import MISSING, RunStateDB


def _open_db(workspace_root: Path) -> RunStateDB:
    return RunStateDB(Path(workspace_root))


def write_artifact_receipt(
    workspace_root: Path,
    run_id: str,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    receipt_secret: str | None = None,
) -> None:
    hmac_hex = (
        _receipt_hmac(receipt_secret, run_id, artifact_type)
        if receipt_secret is not None
        else None
    )
    db = _open_db(workspace_root)
    try:
        db.upsert_receipt(run_id, artifact_type, hmac_hex)
    finally:
        db.close()


def artifact_receipt_present(
    workspace_root: Path,
    run_id: str,
    artifact_type: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    receipt_secret: str | None = None,
) -> bool:
    db = _open_db(workspace_root)
    try:
        stored = db.get_receipt_hmac(run_id, artifact_type)
    finally:
        db.close()
    if stored is not MISSING:
        if receipt_secret is None:
            return True
        if not isinstance(stored, str):
            return False
        expected = _receipt_hmac(receipt_secret, run_id, artifact_type)
        return hmac.compare_digest(stored, expected)
    return _legacy_file_receipt_present(
        workspace_root, run_id, artifact_type,
        backend=backend, receipt_secret=receipt_secret,
    )
```

`_legacy_file_receipt_present` is the old file-reading body of `artifact_receipt_present`, renamed. `delete_artifact_receipt` and `clear_run_receipts` call the DB AND the legacy file paths (delete both). Keep per-call open/close — receipts fire a handful of times per phase, and connection-per-call sidesteps cross-thread reuse questions.

- [ ] **Step 4: Run the receipts + MCP suites**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests -k "receipt" -v && .venv/bin/python -m pytest tests/mcp -x -q`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add ralph/mcp/artifacts/completion_receipts.py tests/
git commit -m "feat(artifacts): back receipts with RunStateDB, legacy file fallback"
```

---

### Task 7: Back completion sentinels with `RunStateDB`

Same swap for `.agent/completion_seen_<run_id>.json`. Writers/readers: `ralph/mcp/tools/coordination.py` (`COMPLETION_SENTINEL_RELPATHFMT`, line ~76, MCP server side) and `ralph/agents/invoke/__init__.py:234` (engine side). Preserve HMAC semantics; keep a legacy-file read fallback for one release. After this task, Task 3's sentinel sweep becomes a DB `DELETE` — simplify `sweep_agent_dir` to also call `RunStateDB` cleanup, keeping the file-glob path for legacy files.

**Files:**
- Modify: `ralph/mcp/tools/coordination.py`
- Modify: `ralph/agents/invoke/__init__.py:234` (sentinel read)
- Modify: `ralph/workspace/agent_dir_retention.py` (add `DELETE FROM completion_sentinels/receipts WHERE created_at < cutoff` via `RunStateDB`; add method `prune_older_than(cutoff: float) -> int` to `RunStateDB`)
- Test: the suites covering completion detection (find with `git grep -ln "completion_seen" tests/`)

**Interfaces:**
- Consumes: `RunStateDB.upsert_completion_sentinel` / `get_completion_sentinel_hmac` from Task 5.
- Produces: `RunStateDB.prune_older_than(cutoff: float) -> int` (deletes aged rows from both tables, returns row count).

- [ ] **Step 1: Locate and mirror the existing sentinel tests**

Run: `git grep -ln "completion_seen" tests/` and read the write/read tests. Write one new failing test per behavior: sentinel recorded via coordination tool is visible to the engine-side check without any `completion_seen_*.json` file appearing; HMAC-mismatched row rejected; legacy file still honored.

- [ ] **Step 2: Run new tests, verify failure**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests -k "completion" -q`
Expected: new tests FAIL.

- [ ] **Step 3: Swap writer and reader**

In `coordination.py`, replace the sentinel file write with `RunStateDB(workspace_root).upsert_completion_sentinel(run_id, hmac_hex)` (same open/use/close pattern as Task 6). In `ralph/agents/invoke/__init__.py:234` replace the `sentinel_path` existence/HMAC check with the DB read plus legacy-file fallback. Add `prune_older_than` to `RunStateDB`:

```python
    def prune_older_than(self, cutoff: float) -> int:
        with self._conn:
            a = self._conn.execute(
                "DELETE FROM receipts WHERE created_at < ?", (cutoff,)
            ).rowcount
            b = self._conn.execute(
                "DELETE FROM completion_sentinels WHERE created_at < ?", (cutoff,)
            ).rowcount
        return a + b
```

and call it from `sweep_agent_dir` (wrapped in try/except OSError+sqlite3.Error, best-effort).

- [ ] **Step 4: Run completion + agents + MCP suites**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests -k "completion" -q && .venv/bin/python -m pytest tests/agents tests/mcp -x -q`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add ralph/mcp/tools/coordination.py ralph/agents/invoke/__init__.py ralph/mcp/artifacts/state_db.py ralph/workspace/agent_dir_retention.py tests/
git commit -m "feat(mcp): back completion sentinels with RunStateDB"
```

---

### Task 8: FS-health diagnostic + operator documentation

Make the environmental half visible: `ralph doctor`-style diagnostics warn when Spotlight indexes the workspace volume or the volume's fsevents journal is bloated, and the operator manual documents the mitigations.

**Files:**
- Create: `ralph/diagnostics/fs_health.py`
- Modify: `ralph/diagnostics/__init__.py` (include the check in `run_diagnostics`)
- Modify: `docs/operator-manual.md` (new "External-volume filesystem hygiene" section with the four mitigations from the plan preamble, verbatim commands included)
- Test: `tests/unit/test_fs_health.py`

**Interfaces:**
- Produces: `FsHealth.gather(workspace_root: Path, *, run_command=subprocess.run) -> FsHealth` dataclass with fields `volume_root: str`, `spotlight_indexing_enabled: bool | None`, `fsevents_journal_bytes: int | None`, `warnings: list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_fs_health.py`:

```python
"""Tests for the fs-health diagnostic."""

from __future__ import annotations

from pathlib import Path

from ralph.diagnostics.fs_health import FsHealth, _volume_root


def test_volume_root_for_external_volume() -> None:
    assert _volume_root(Path("/Volumes/Disk X/proj/ws")) == Path("/Volumes/Disk X")


def test_volume_root_for_boot_volume() -> None:
    assert _volume_root(Path("/Users/me/proj")) == Path("/")


def test_warns_on_spotlight_and_fat_journal(tmp_path: Path) -> None:
    def fake_run(cmd, **kwargs):
        class R:
            returncode = 0
            stdout = "/Volumes/X:\n\tIndexing enabled. \n"
        return R()

    journal = tmp_path / ".fseventsd"
    journal.mkdir()
    (journal / "0000000012345678").write_bytes(b"x" * (60 * 1024 * 1024))

    health = FsHealth.gather(tmp_path, run_command=fake_run)
    assert health.spotlight_indexing_enabled is True
    assert health.fsevents_journal_bytes is not None
    assert health.fsevents_journal_bytes > 50 * 1024 * 1024
    assert len(health.warnings) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests/unit/test_fs_health.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the diagnostic**

Create `ralph/diagnostics/fs_health.py`:

```python
"""Filesystem-health diagnostics for the workspace volume (macOS-focused).

Long multi-instance runs on an external volume can drive the macOS
``fseventsd`` daemon to a full core when (a) Spotlight indexes the
churned paths and (b) the volume's ``.fseventsd`` journal bloats.
This check surfaces both so operators apply the documented mitigations
(see docs/operator-manual.md, "External-volume filesystem hygiene").
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_JOURNAL_WARN_BYTES = 50 * 1024 * 1024


def _volume_root(path: Path) -> Path:
    resolved = path.resolve()
    parts = resolved.parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        return Path(parts[0]) / parts[1] / parts[2]
    return Path(parts[0]) if parts else Path("/")


@dataclass
class FsHealth:
    volume_root: str
    spotlight_indexing_enabled: bool | None = None
    fsevents_journal_bytes: int | None = None
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def gather(
        cls,
        workspace_root: Path,
        *,
        run_command=subprocess.run,
    ) -> "FsHealth":
        volume = _volume_root(workspace_root)
        health = cls(volume_root=str(volume))
        if sys.platform != "darwin":
            return health

        try:
            result = run_command(
                ["mdutil", "-s", str(volume)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                health.spotlight_indexing_enabled = "Indexing enabled" in result.stdout
        except (OSError, subprocess.TimeoutExpired):
            health.spotlight_indexing_enabled = None

        journal = volume / ".fseventsd"
        try:
            if journal.is_dir():
                health.fsevents_journal_bytes = sum(
                    f.stat().st_size for f in journal.iterdir() if f.is_file()
                )
        except (OSError, PermissionError):
            health.fsevents_journal_bytes = None

        if health.spotlight_indexing_enabled and str(volume).startswith("/Volumes/"):
            health.warnings.append(
                f"Spotlight indexes {volume}; long runs churn it hard. "
                f"Consider: sudo mdutil -i off '{volume}'"
            )
        if (
            health.fsevents_journal_bytes is not None
            and health.fsevents_journal_bytes > _JOURNAL_WARN_BYTES
        ):
            health.warnings.append(
                f"fsevents journal on {volume} is "
                f"{health.fsevents_journal_bytes // (1024 * 1024)} MB (healthy: a few MB); "
                "see operator manual 'External-volume filesystem hygiene'."
            )
        return health


__all__ = ["FsHealth"]
```

- [ ] **Step 4: Wire into `run_diagnostics` and the docs**

Add an `fs_health: FsHealth` field to `DiagnosticReport` in `ralph/diagnostics/__init__.py`, populate it in `run_diagnostics`, and surface `health.warnings` through the same channel other diagnostics use. Then add the "External-volume filesystem hygiene" section to `docs/operator-manual.md` with the four mitigations from this plan's preamble (copy the commands verbatim, including the `no_log` trade-off sentence).

- [ ] **Step 5: Run tests**

Run: `cd ralph-workflow && .venv/bin/python -m pytest tests/unit/test_fs_health.py -v && .venv/bin/python -m pytest tests -k "diagnostic" -q`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add ralph/diagnostics/fs_health.py ralph/diagnostics/__init__.py docs/operator-manual.md tests/unit/test_fs_health.py
git commit -m "feat(diagnostics): fs-health check for Spotlight and fsevents journal bloat"
```

---

## Explicitly out of scope (and why)

- **Checkpoint `.tmp`+rename → DB:** saves are per phase transition (rare); the atomic-rename file keeps the resume contract inspectable. Not a churn driver.
- **Artifact JSON / PLAN.md / prompts / exec spills → DB:** agent- and human-facing by contract; agents read them with workspace file tools.
- **Project-under-test churn (test.log at ~100 KB/s, tmp/storage blobs, docs rebuilds):** not Ralph's files; addressed as operator guidance in Task 8's docs section.
- **`mcp-server.log`:** already an inherited-fd append (one open per server start) — fine as is.
- **Artifact history archives → DB:** viable later; low write rate (per submission), so deferred until Tasks 5–7 prove out the DB.

## Verification of the actual symptom

After Tasks 1–4 land and the operator mitigations are applied, re-run the measurement that produced the diagnosis: with ≥2 concurrent runs active for 30+ minutes, `ps aux | grep fseventsd` should show single-digit CPU, and `du -sh /Volumes/<vol>/.fseventsd` should stay in the low MB after a journal reset. If fseventsd still spikes, profile again with `sudo fs_usage -w -f filesys | grep -v CACHE_HIT` scoped to the ralph PIDs before assuming more engine work is needed.
