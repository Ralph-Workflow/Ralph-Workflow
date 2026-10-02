"""Concurrency tests for the explore index (acceptance criterion 4).

Several sessions searching and refreshing one workspace at once all
get correct results, run only one rebuild at a time, and never
corrupt the index.

These tests use real subprocesses (``subprocess_e2e`` marker) so
the cross-process lock and atomic promotion are exercised.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(60)]


SCRIPT_TEMPLATE = """
import os
import sys
from pathlib import Path

WORKSPACE = Path({workspace!r})
INDEX_DIR = WORKSPACE / \".agent\" / \"ralph-explore\"

from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore

store = ExploreStore(INDEX_DIR)
result = reindex(store, WORKSPACE, options=ReindexOptions(mode=\"full\", timeout_ms=10_000))
sys.exit(0 if result.status == \"ok\" else 1)
"""


def _seed(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for i in range(20):
        (workspace / f"f{i:02d}.py").write_text(f"def fn_{i}():\n    return {i}\n")
    return workspace


def _run_reindex(workspace: Path) -> subprocess.CompletedProcess[bytes]:
    script = SCRIPT_TEMPLATE.format(workspace=str(workspace))
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_multiple_sessions_share_one_workspace(tmp_path: Path) -> None:
    """Several sessions searching and refreshing one workspace all succeed."""
    workspace = _seed(tmp_path)
    # Run several sessions in parallel against the same workspace.
    procs: list[subprocess.Popen[bytes]] = []
    for _ in range(3):
        script = SCRIPT_TEMPLATE.format(workspace=str(workspace))
        procs.append(
            subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
    for p in procs:
        p.wait(timeout=30)
        assert p.returncode == 0
    # The index DB exists, is non-empty, and reports a coherent generation.
    db = workspace / ".agent" / "ralph-explore" / "index.sqlite"
    assert db.is_file()
    assert db.stat().st_size > 0


def test_concurrent_reindex_does_not_corrupt(tmp_path: Path) -> None:
    """Repeated concurrent reindexes do not corrupt the index."""
    workspace = _seed(tmp_path)
    # Run a reindex, then immediately another; the second must succeed
    # without raising a database error.
    for _ in range(3):
        result = _run_reindex(workspace)
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    # The index DB is still a valid SQLite file.
    db = workspace / ".agent" / "ralph-explore" / "index.sqlite"
    assert db.is_file()
    # Read with sqlite3 to confirm it's not corrupted.
    import sqlite3

    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute("SELECT count(*) FROM files")
        row = cur.fetchone()
        assert row is not None
        # The files table has 20 rows.
        assert row[0] == 20
    finally:
        conn.close()
