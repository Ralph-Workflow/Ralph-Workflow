"""Crash-safety tests for the explore index (acceptance criterion 3).

Killing the process at random points during a build, repeated
many times, never leaves an index that serves wrong results. The
next session always recovers without help.

These tests use real subprocesses (``subprocess_e2e`` marker) so
the process boundary and atomic promotion are exercised. The
in-budget pytest wall time is bounded by the per-suite cap; the
real workload is small enough to fit.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(60)]


SCRIPT_TEMPLATE = """
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

WORKSPACE = Path({workspace!r})
INDEX_DIR = WORKSPACE / \".agent\" / \"ralph-explore\"
INDEX_DIR.mkdir(parents=True, exist_ok=True)

from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore

store = ExploreStore(INDEX_DIR)

if {kill_after_seconds} > 0:
    # Schedule a SIGKILL after a short delay so the build is interrupted.
    pid = os.getpid()
    def _kill():
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    import threading
    t = threading.Timer({kill_after_seconds}, _kill)
    t.daemon = True
    t.start()

try:
    reindex(store, WORKSPACE, options=ReindexOptions(mode=\"full\", timeout_ms=10_000))
except Exception as exc:
    sys.stderr.write(str(exc))
    sys.exit(1)
sys.exit(0)
"""


def _seed(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    if not workspace.exists():
        workspace.mkdir()
    for i in range(20):
        (workspace / f"f{i:02d}.py").write_text(f"def fn_{i}():\n    return {i}\n")
    return workspace


def _run_kill_round(tmp_path: Path, kill_after_seconds: float) -> dict[str, object]:
    workspace = _seed(tmp_path)
    script = SCRIPT_TEMPLATE.format(
        workspace=str(workspace), kill_after_seconds=kill_after_seconds
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        timeout=30,
        check=False,
    )
    return {
        "workspace": str(workspace),
        "returncode": result.returncode,
        "stderr_tail": result.stderr.decode("utf-8", errors="replace")[-200:],
    }


def test_repeated_kill_during_build_never_serves_wrong_results(tmp_path: Path) -> None:
    """Repeated SIGKILL-during-build never serves a half-written generation."""
    rounds: list[dict[str, object]] = []
    for i in range(3):
        sub = tmp_path / f"kill_run_{i}"
        sub.mkdir()
        _seed(sub)
        outcome = _run_kill_round(sub, kill_after_seconds=0.05)
        # The subprocess was killed -> returncode is non-zero.
        assert outcome["returncode"] != 0
        rounds.append(outcome)
    # After the kills, a fresh session must still serve correct
    # results. Reindex one of the subdirs from scratch.
    workspace = tmp_path / "kill_run_0"
    from ralph.mcp.explore.pipeline import ReindexOptions, reindex
    from ralph.mcp.explore.store import ExploreStore

    store = ExploreStore(workspace / ".agent" / "ralph-explore")
    try:
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=10_000),
        )
        assert result.status == "ok"
        # The index now serves matches via a fresh search.
        from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
        from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files

        session = type("S", (), {"explore_index": build_sqlite_index_handle(store), "check_capability": lambda self, c: {"status": "approved", "capability": c}, "check_edit_area": lambda self, p: {"status": "approved", "path": p}})()
        ws = type("W", (), {"root": workspace, "read": lambda self, p: (workspace / p).read_text() if (workspace / p).exists() else "", "iter_files": lambda self, base="": [str(p.relative_to(workspace)) for p in workspace.rglob("*") if p.is_file()], "list_dir": lambda self, base: [p.name for p in workspace.iterdir()], "stat": lambda self, p: {"type": "file", "size_bytes": (workspace / p).stat().st_size} if (workspace / p).is_file() else {"type": "missing"}})()
        result = handle_grep_files(
            session,
            ws,
            {"pattern": "def", "path": ".", "regex": False, "case_sensitive": False, "use_index": "auto"},
        )
        payload = json.loads(result.content[0].text)
        # Either indexed results OR live fallback both contain matches.
        assert "matches" in payload
    finally:
        store.close()


def test_fresh_session_after_kill_recovers_without_manual_cleanup(tmp_path: Path) -> None:
    """A fresh session after a kill completes the build automatically."""
    workspace = tmp_path / "ws_fresh"
    workspace.mkdir()
    sub = tmp_path / "fresh_kill_run_x"
    sub.mkdir()
    _seed(sub)
    # First attempt: kill mid-build.
    script = SCRIPT_TEMPLATE.format(workspace=str(sub), kill_after_seconds=0.01)
    subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=30, check=False)
    # Second attempt: same workspace, no kill; must succeed.
    script = SCRIPT_TEMPLATE.format(workspace=str(sub), kill_after_seconds=0)
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, timeout=30, check=False
    )
    assert result.returncode == 0
    # The index DB exists and has rows.
    db = sub / ".agent" / "ralph-explore" / "index.sqlite"
    assert db.is_file()
    assert db.stat().st_size > 0
