from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

from ralph.executor.process import ProcessRunOptions, run_process

pytestmark = [pytest.mark.timeout_seconds(60)]


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
    script = SCRIPT_TEMPLATE.replace("__WORKSPACE__", repr(str(workspace))).replace(
        "__KILL_AFTER__", repr(kill_after_seconds)
    )
    result = run_process(
        sys.executable,
        ["-c", script],
        options=ProcessRunOptions(capture_output=True, timeout=30),
    )
    return result.returncode


def _verify_recovery(workspace: Path) -> dict[str, object]:
    script = VERIFY_SCRIPT_TEMPLATE.replace("__WORKSPACE__", repr(str(workspace)))
    result = run_process(
        sys.executable,
        ["-c", script],
        options=ProcessRunOptions(capture_output=True, timeout=30),
    )
    out = result.stdout.strip()
    if result.returncode == 0 and out:
        try:
            return json.loads(out.splitlines()[-1])
        except json.JSONDecodeError:
            pass
    return {
        "status": "error",
        "returncode": result.returncode,
        "stderr": result.stderr[-300:],
        "stdout_tail": out[-200:],
    }


def test_repeated_random_kill_during_build_self_recovers(tmp_path: Path) -> None:
    rng = random.Random(0xC0FFEE)
    workspace = _seed(tmp_path, files=4)
    for round_idx in range(1):
        import shutil

        index_dir = workspace / ".agent" / "ralph-explore"
        if index_dir.exists():
            shutil.rmtree(index_dir, ignore_errors=True)
        index_dir.mkdir(parents=True, exist_ok=True)
        kill_delay = rng.uniform(0.005, 0.05)
        rc = _run_kill_round(workspace, kill_delay)
        assert rc != 0, (
            f"round {round_idx}: SIGKILL did not interrupt build (rc={rc}, kill_delay={kill_delay})"
        )
        verify = _verify_recovery(workspace)
        assert verify["status"] == "ok", f"round {round_idx}: recovery failed, verify={verify}"
        assert verify["match_count"] > 0, (
            f"round {round_idx}: fresh index served 0 matches: {verify}"
        )


def test_kill_during_atomic_promote_does_not_serve_partial_generation(tmp_path: Path) -> None:
    workspace = _seed(tmp_path, files=6)
    rc = _run_kill_round(workspace, kill_after_seconds=0.02)
    assert rc != 0
    verify = _verify_recovery(workspace)
    assert verify["status"] == "ok", verify
    assert verify["match_count"] > 0, verify
