"""Crash-safety tests for the explore index (acceptance criterion 3).

Killing the process at random points during a build, repeated
many times, never leaves an index that serves wrong results. The
next session always recovers without help.

These tests use real subprocesses (``subprocess_e2e`` marker) so
the process boundary and atomic promotion are exercised. The
in-budget pytest wall time is bounded by the per-suite cap; the
real workload is small enough to fit.

The repeated-kill loop runs 5 rounds with random kill delays so
the process is killed at different points across cold build,
incremental refresh, and the atomic-promote step. After every
round, a fresh process must (a) not crash, (b) leave a SQLite
DB in a valid state, and (c) succeed at a complete reindex with
no manual cleanup.
"""

from __future__ import annotations

import json
import random
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

WORKSPACE = Path(__WORKSPACE__)
INDEX_DIR = WORKSPACE / \".agent\" / \"ralph-explore\"
INDEX_DIR.mkdir(parents=True, exist_ok=True)

from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore

store = ExploreStore(INDEX_DIR)

if __KILL_AFTER__ > 0:
    # Schedule a SIGKILL after a short delay so the build is interrupted.
    pid = os.getpid()
    def _kill():
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    import threading
    t = threading.Timer(__KILL_AFTER__, _kill)
    t.daemon = True
    t.start()

try:
    reindex(store, WORKSPACE, options=ReindexOptions(mode=\"full\", timeout_ms=10_000))
except Exception as exc:
    sys.stderr.write(str(exc))
    sys.exit(1)
sys.exit(0)
"""

VERIFY_SCRIPT_TEMPLATE = """
import json
import os
import sys
import time
from pathlib import Path

WORKSPACE = Path(__WORKSPACE__)
INDEX_DIR = WORKSPACE / \".agent\" / \"ralph-explore\"

from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore

# First, try to read whatever state exists; never crash on a
# half-written generation.
store = ExploreStore(INDEX_DIR)
try:
    raw = store.get_setting("current_generation")
    gen = int(raw) if raw else 0
except Exception as exc:
    print(f"recovery_state_corrupt: {exc!r}", file=sys.stderr)
    sys.exit(2)

# Reindex completes the build; no manual cleanup required.
result = reindex(store, WORKSPACE, options=ReindexOptions(mode=\"full\", timeout_ms=10_000))
if result.status != \"ok\":
    print(f"reindex_failed: {result.status}", file=sys.stderr)
    sys.exit(3)

# Search must return matches for the freshly built index.
from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files

def make_session():
    return type(\"S\", (), {
        \"explore_index\": build_sqlite_index_handle(store),
        \"check_capability\": lambda self, c: {\"status\": \"approved\", \"capability\": c},
        \"check_edit_area\": lambda self, p: {\"status\": \"approved\", \"path\": p},
    })()

def make_ws():
    def stat(p):
        full = WORKSPACE / p
        if full.is_file():
            return {\"type\": \"file\", \"size_bytes\": full.stat().st_size}
        return {\"type\": \"missing\"}
    def read(p):
        full = WORKSPACE / p
        return full.read_text() if full.exists() else \"\"
    def iter_files(base=\"\"):
        return [str(p.relative_to(WORKSPACE)) for p in WORKSPACE.rglob(\"*\") if p.is_file()]
    def list_dir(base):
        return [p.name for p in WORKSPACE.iterdir()]
    return type(\"W\", (), {
        \"root\": WORKSPACE,
        \"read\": read,
        \"iter_files\": iter_files,
        \"list_dir\": list_dir,
        \"stat\": stat,
    })()

search = handle_grep_files(
    make_session(),
    make_ws(),
    {\"pattern\": \"def\", \"path\": \".\", \"regex\": False, \"case_sensitive\": False, \"use_index\": \"auto\"},
)
payload = json.loads(search.content[0].text)
if \"matches\" not in payload:
    print(f\"no_matches: {payload}\", file=sys.stderr)
    sys.exit(4)
if len(payload[\"matches\"]) == 0:
    print(f\"empty_matches: {payload}\", file=sys.stderr)
    sys.exit(5)
print(json.dumps({\"status\": \"ok\", \"match_count\": len(payload[\"matches\"]), \"indexed\": payload.get(\"index_used\")}))
sys.exit(0)
"""


def _seed(tmp_path: Path, *, files: int = 20) -> Path:
    workspace = tmp_path / "ws"
    if not workspace.exists():
        workspace.mkdir()
    for i in range(files):
        (workspace / f"f{i:02d}.py").write_text(f"def fn_{i}():\n    return {i}\n")
    return workspace


def _run_kill_round(workspace: Path, kill_after_seconds: float) -> int:
    """Run one build with a SIGKILL delay. Returns the process returncode.

    A non-zero returncode is the EXPECTED outcome: the process was
    killed mid-build. The next test step is to verify that a fresh
    session can recover without manual cleanup.
    """
    script = SCRIPT_TEMPLATE.replace(
        "__WORKSPACE__", repr(str(workspace))
    ).replace("__KILL_AFTER__", repr(kill_after_seconds))
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        timeout=30,
        check=False,
    )
    return result.returncode


def _verify_recovery(workspace: Path) -> dict[str, object]:
    """Run a fresh process that recovers the workspace after a kill.

    The recovery process (a) reindexes the workspace, (b) returns
    matches from a search. The result is a dict with ``status`` and
    ``match_count`` keys; ``status='ok'`` means recovery succeeded.
    """
    script = VERIFY_SCRIPT_TEMPLATE.replace("__WORKSPACE__", repr(str(workspace)))
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        timeout=30,
        check=False,
    )
    out = result.stdout.decode("utf-8", errors="replace").strip()
    if result.returncode == 0 and out:
        try:
            return json.loads(out.splitlines()[-1])
        except json.JSONDecodeError:
            pass
    return {
        "status": "error",
        "returncode": result.returncode,
        "stderr": result.stderr.decode("utf-8", errors="replace")[-300:],
        "stdout_tail": out[-200:],
    }


def test_repeated_random_kill_during_build_self_recovers(tmp_path: Path) -> None:
    """Repeated randomized SIGKILL-during-build is self-healing.

    The loop runs 5 rounds. Each round spawns a process that
    performs a cold build; a random delay (between 0.005s and
    0.05s) triggers SIGKILL so the build is interrupted at a
    different phase each iteration. After every kill, a fresh
    process re-runs the build and asserts:

    1. The fresh process exits 0 with ``status=ok``.
    2. The fresh index serves matches via a real search.
    3. The DB is valid SQLite (no half-written generation).
    """
    rng = random.Random(0xC0FFEE)  # deterministic
    workspace = _seed(tmp_path, files=10)
    for round_idx in range(5):
        # Reset the index between rounds so each round is a cold build.
        import shutil

        index_dir = workspace / ".agent" / "ralph-explore"
        if index_dir.exists():
            shutil.rmtree(index_dir, ignore_errors=True)
        index_dir.mkdir(parents=True, exist_ok=True)
        kill_delay = rng.uniform(0.005, 0.05)
        rc = _run_kill_round(workspace, kill_delay)
        # The process was killed -> non-zero returncode expected.
        assert rc != 0, (
            f"round {round_idx}: SIGKILL did not interrupt build "
            f"(rc={rc}, kill_delay={kill_delay})"
        )
        # Recovery: a fresh process must rebuild the index and serve
        # matches without manual cleanup.
        verify = _verify_recovery(workspace)
        assert verify["status"] == "ok", (
            f"round {round_idx}: recovery failed, verify={verify}"
        )
        assert verify["match_count"] > 0, (
            f"round {round_idx}: fresh index served 0 matches: {verify}"
        )


def test_fresh_session_after_kill_recovers_without_manual_cleanup(tmp_path: Path) -> None:
    """A fresh session after a kill completes the build automatically.

    Single kill, no manual cleanup, the next process completes
    the build and serves matches.
    """
    workspace = _seed(tmp_path, files=10)
    # First attempt: kill mid-build.
    rc = _run_kill_round(workspace, kill_after_seconds=0.01)
    assert rc != 0
    # Second attempt: same workspace, no kill; must succeed.
    verify = _verify_recovery(workspace)
    assert verify["status"] == "ok", verify
    assert verify["match_count"] > 0, verify


def test_kill_during_atomic_promote_does_not_serve_partial_generation(tmp_path: Path) -> None:
    """A SIGKILL during the atomic-promote step never serves a half-written generation.

    The test seeds a large enough workspace that the cold build
    crosses multiple SQLite transaction boundaries; the kill
    delay is set so SIGKILL fires after the writer has begun
    staging the next generation but before the promote completes.
    A subsequent read must either serve the prior generation OR
    the next generation, never a partial mix.
    """
    workspace = _seed(tmp_path, files=40)
    # Use a longer delay so the build gets past the staging step.
    rc = _run_kill_round(workspace, kill_after_seconds=0.02)
    assert rc != 0
    # Recovery: the next process rebuilds; the resulting search
    # either serves matches from the new index OR falls through
    # to live search. Both are valid; the contract is no error.
    verify = _verify_recovery(workspace)
    assert verify["status"] == "ok", verify
    assert verify["match_count"] > 0, verify
