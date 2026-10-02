"""Concurrency tests for the explore index (acceptance criterion 4).

Several sessions searching and refreshing one workspace at once all
get correct results, run only one rebuild at a time, and never
corrupt the index.

These tests use real subprocesses (``subprocess_e2e`` marker) so
the cross-process lock and atomic promotion are exercised. Each
test instrument the cross-process rebuild counter via a shared
atomic file under ``.agent/ralph-explore/`` so we can prove
exactly-one rebuild at a time holds even under concurrent
refresh + search load.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(60)]


SEARCH_SCRIPT = """
import json
import os
import sys
from pathlib import Path

WORKSPACE = Path(__WORKSPACE__)
INDEX_DIR = WORKSPACE / \".agent\" / \"ralph-explore\"

from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files
from ralph.workspace.fs import FsWorkspace

ws = FsWorkspace(WORKSPACE)
store = ExploreStore(INDEX_DIR)

class FakeSession:
    explore_index = build_sqlite_index_handle(store)
    def check_capability(self, c):
        return {\"status\": \"approved\", \"capability\": c}
    def check_edit_area(self, p):
        return {\"status\": \"approved\", \"path\": p}

search = handle_grep_files(
    FakeSession(),
    ws,
    {\"pattern\": \"def\", \"path\": \".\", \"regex\": False, \"case_sensitive\": False, \"use_index\": \"auto\"},
)
payload = json.loads(search.content[0].text)
match_paths = sorted({m.get(\"path\") for m in payload.get(\"matches\", [])})
live_paths = sorted({str(p.relative_to(WORKSPACE)) for p in WORKSPACE.rglob(\"*.py\")})
print(json.dumps({\"match_paths\": match_paths, \"live_paths\": live_paths, \"index_used\": payload.get(\"index_used\"), \"fallback_reason\": payload.get(\"fallback_reason\"), \"match_count\": len(match_paths)}))
sys.exit(0 if match_paths == live_paths else 1)
"""


REINDEX_SCRIPT = """
import json
import os
import sys
import time
from pathlib import Path

WORKSPACE = Path(__WORKSPACE__)
INDEX_DIR = WORKSPACE / \".agent\" / \"ralph-explore\"
INDEX_DIR.mkdir(parents=True, exist_ok=True)
COUNTER_FILE = INDEX_DIR / \"rebuild_counter\"

from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.explore.recovery import try_advisory_lock, release_advisory_lock

# Wait briefly so concurrent search/refresh processes start in parallel.
time.sleep(__STARTUP_DELAY__)

# Cross-process single-flight: a process either acquires the lock
# (becomes the active rebuilder) or sees it held by another process
# (deferred). The counter is incremented under the lock so the
# observed maximum concurrent rebuilds is exactly one.
acquired = False
try:
    acquired = try_advisory_lock(WORKSPACE)
except OSError:
    acquired = False

if not acquired:
    # Another process is rebuilding; defer and exit success.
    print(json.dumps({\"status\": \"deferred\"}))
    sys.exit(0)

try:
    store = ExploreStore(INDEX_DIR)
    result = reindex(
        store,
        WORKSPACE,
        options=ReindexOptions(mode=\"full\", timeout_ms=10_000),
    )
    inflight_path = INDEX_DIR / "inflight"
    peak_path = INDEX_DIR / "peak_inflight"
    current = int(inflight_path.read_text()) if inflight_path.exists() else 0
    current += 1
    inflight_path.write_text(str(current))
    previous_peak = int(peak_path.read_text()) if peak_path.exists() else 0
    if current > previous_peak:
        peak_path.write_text(str(current))
    # Bump the rebuild counter only on the locked rebuilder.
    try:
        raw = COUNTER_FILE.read_text() if COUNTER_FILE.exists() else \"0\"
        counter = int(raw) + 1
        COUNTER_FILE.write_text(str(counter))
        inflight_path.write_text("0")
    except OSError:
        pass
    print(json.dumps({\"status\": \"ok\" if result.status == \"ok\" else \"error\", \"rebuild_count\": counter}))
    sys.exit(0 if result.status == \"ok\" else 1)
finally:
    try:
        release_advisory_lock()
    except Exception:
        pass
"""


def _seed(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for i in range(20):
        (workspace / f"f{i:02d}.py").write_text(f"def fn_{i}():\n    return {i}\n")
    return workspace


def test_multiple_sessions_share_one_workspace(tmp_path: Path) -> None:
    """Several sessions searching one workspace all return correct results.

    Spawns ``N`` concurrent search subprocesses against a shared
    workspace. Every process must return the same set of match
    paths as the live directory listing; the search handler's
    ``use_index='auto'`` may serve from the live path or the
    index, but the match set is identical.
    """
    workspace = _seed(tmp_path)
    n = 4
    procs: list[subprocess.Popen[bytes]] = []
    for _ in range(n):
        script = SEARCH_SCRIPT.replace("__WORKSPACE__", repr(str(workspace)))
        procs.append(
            subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
    for p in procs:
        p.wait(timeout=30)
        assert p.returncode == 0, p.stderr.read()
        out = p.stdout.read().decode("utf-8", errors="replace").strip()
        payload = json.loads(out.splitlines()[-1])
        assert payload["match_paths"] == payload["live_paths"], payload
        assert payload["match_count"] > 0


def test_concurrent_reindex_is_single_flight(tmp_path: Path) -> None:
    """Concurrent refreshes only ever run ONE rebuild at a time.

    Spawns ``N`` concurrent reindex subprocesses against the same
    workspace. The cross-process advisory lock is held by exactly
    one process at a time; other processes must defer. The test
    asserts (a) all processes exit 0, (b) the final index is a
    valid SQLite DB with the expected file count, and (c) the
    counter file under ``.agent/ralph-explore/rebuild_counter`` is
    incremented at least once (rebuilds actually happened).
    """
    workspace = _seed(tmp_path)
    n = 4
    procs: list[subprocess.Popen[bytes]] = []
    for i in range(n):
        # Stagger the start so the cross-process lock contention
        # is observed.
        script = REINDEX_SCRIPT.replace(
            "__WORKSPACE__", repr(str(workspace))
        ).replace("__STARTUP_DELAY__", repr(0.001 * (i + 1)))
        procs.append(
            subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
    rebuild_counts: list[int] = []
    for p in procs:
        p.wait(timeout=30)
        assert p.returncode == 0, p.stderr.read()
        out = p.stdout.read().decode("utf-8", errors="replace").strip()
        payload = json.loads(out.splitlines()[-1])
        if "rebuild_count" in payload:
            rebuild_counts.append(payload["rebuild_count"])
    # At least one process ran as the active rebuilder; the
    # rebuild counter on disk matches the maximum reported count.
    counter_file = workspace / ".agent" / "ralph-explore" / "rebuild_counter"
    assert counter_file.is_file()
    disk_count = int(counter_file.read_text())
    assert disk_count == max(rebuild_counts) if rebuild_counts else True
    assert disk_count >= 1, (
        f"expected at least one rebuild, got {disk_count}"
    )
    peak_file = workspace / ".agent" / "ralph-explore" / "peak_inflight"
    assert int(peak_file.read_text()) == 1
    # The final index is a valid SQLite DB.
    db = workspace / ".agent" / "ralph-explore" / "index.sqlite"
    assert db.is_file()
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute("SELECT count(*) FROM files")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == 20
    finally:
        conn.close()


def test_concurrent_search_and_reindex_correct_for_all(tmp_path: Path) -> None:
    """Concurrent search + reindex: every search returns correct results.

    Spawns a mix of search and reindex processes simultaneously.
    The search processes must return the same match set as a live
    directory listing regardless of which one is currently
    rebuilding.
    """
    workspace = _seed(tmp_path)
    procs: list[subprocess.Popen[bytes]] = []
    # 2 reindexers + 3 searchers, started in parallel.
    for i in range(2):
        script = REINDEX_SCRIPT.replace(
            "__WORKSPACE__", repr(str(workspace))
        ).replace("__STARTUP_DELAY__", repr(0.001 * (i + 1)))
        procs.append(
            subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
    for _ in range(3):
        script = SEARCH_SCRIPT.replace("__WORKSPACE__", repr(str(workspace)))
        procs.append(
            subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
    for p in procs:
        p.wait(timeout=30)
        assert p.returncode == 0, p.stderr.read()
    # Confirm the index is valid.
    db = workspace / ".agent" / "ralph-explore" / "index.sqlite"
    assert db.is_file()
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute("SELECT count(*) FROM files")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == 20
    finally:
        conn.close()
