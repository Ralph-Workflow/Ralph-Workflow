"""Subprocess-based R6.2 build measurement helpers.

Extracted from :mod:`ralph.mcp.explore._bench_r6_metrics` so the
hub module stays under the repository file-size limit. The helpers
own the ``python -c`` runner script and the ``subprocess.run`` glue
that isolates each benchmark build in a fresh process so the
reported peak RSS depends on the build alone -- not on prior
activity in the parent process.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Final

# ---------------------------------------------------------------------------
# Subprocess-based R6.2 build measurement
# ---------------------------------------------------------------------------
#
# R6.6 requires the benchmark gate to "run reliably ... without flaky
# results". The previous harness measured peak RSS via
# ``resource.getrusage(resource.RUSAGE_SELF).ru_maxrss``, which is the
# *process-lifetime* resident-set high-water mark. When the gate runs
# in the same pytest process after hundreds of prior explore tests,
# that high-water mark reflects prior memory use, not the build's
# peak -- a direct measurement hazard that failed the gate on
# developer machines once the harness ran late in the suite.
#
# The fix is to isolate the build inside a fresh subprocess: each
# build phase runs in a short-lived child that performs the work and
# prints its own peak RSS plus the build's wall/CPU time. The parent
# parses that value, so the reported peak RSS depends only on the
# build, never on prior activity in the benchmark process.
#
# For refresh metrics the child does a one-shot cold build as a
# warm-up pass so the measured refresh wall/CPU excludes the
# process-startup cost (FTS5 cache, page-cache fill, etc.) that the
# first reindex call in any process pays. The warm-up pass is the
# same shape as ``measure_cold_build``; only the measured phase
# after it is reported to the parent.
#
# The child script is intentionally tiny and string-substituted with
# the caller's parameters; it emits a single JSON line on stdout.

_BUILD_RUNNER_SCRIPT: Final[str] = (
    "import json\n"
    "import resource\n"
    "import shutil\n"
    "import sys\n"
    "import time\n"
    "from pathlib import Path\n"
    "from ralph.mcp.explore.pipeline import ReindexOptions, reindex\n"
    "from ralph.mcp.explore.store import ExploreStore\n"
    "\n"
    "WORKSPACE = Path(__WORKSPACE_PATH__)\n"
    "INDEX_DIR = Path(__INDEX_DIR_PATH__)\n"
    "MEASURED_MODE = __MEASURED_MODE__\n"
    "CHANGE_COUNT = __CHANGE_COUNT__\n"
    "DO_WARMUP = __DO_WARMUP__\n"
    "TIMEOUT_MS = __TIMEOUT_MS__\n"
    "\n"
    "def _peak_rss_kb():\n"
    "    # VmHWM is the primary Linux per-process peak RSS in kB.\n"
    "    # In this fresh child, getrusage is isolated from parent history.\n"
    "    try:\n"
    "        with open('/proc/self/status') as _f:\n"
    "            for _line in _f:\n"
    "                if _line.startswith('VmHWM:'):\n"
    "                    return int(_line.split()[1])\n"
    "    except FileNotFoundError:\n"
    "        pass\n"
    "    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
    "    return int(peak / 1024) if sys.platform == 'darwin' else int(peak)\n"
    "\n"
    "# filesystem-write-ok: transient scratch directory cleanup before benchmark build\n"
    "if MEASURED_MODE == 'full' and INDEX_DIR.exists():\n"
    "    shutil.rmtree(INDEX_DIR, ignore_errors=True)\n"
    "\n"
    "store = ExploreStore(INDEX_DIR)\n"
    "try:\n"
    "    if DO_WARMUP:\n"
    "        # One-shot warm-up pass so the measured phase reflects\n"
    "        # steady-state behavior, not the FTS5/page-cache\n"
    "        # start-up cost of the very first reindex call.\n"
    "        reindex(store, WORKSPACE, options=ReindexOptions(\n"
    "            mode='changed' if MEASURED_MODE == 'changed' else 'full',\n"
    "            timeout_ms=TIMEOUT_MS,\n"
    "        ))\n"
    "    if MEASURED_MODE == 'changed' and CHANGE_COUNT > 0:\n"
    "        py_files = sorted(WORKSPACE.rglob('*.py'))\n"
    "        for f in py_files[:CHANGE_COUNT]:\n"
    "            # filesystem-write-ok: transient scratch workspace dirtying for refresh benchmark\n"
    "            f.write_text(f.read_text() + '\\n')\n"
    "    start_wall = time.monotonic()\n"
    "    start_cpu = time.process_time()\n"
    "    reindex(store, WORKSPACE, options=ReindexOptions(\n"
    "        mode=MEASURED_MODE, timeout_ms=TIMEOUT_MS,\n"
    "    ))\n"
    "    elapsed_cpu = time.process_time() - start_cpu\n"
    "    elapsed_wall = time.monotonic() - start_wall\n"
    "    index_size = store.index_storage_bytes()\n"
    "    files_read = sum(\n"
    "        p.stat().st_size for p in WORKSPACE.rglob('*') if p.is_file()\n"
    "    )\n"
    "    peak_rss = float(_peak_rss_kb()) * 1024.0\n"
    "    print(json.dumps({\n"
    "        'wall': elapsed_wall,\n"
    "        'cpu': elapsed_cpu,\n"
    "        'peak_rss': peak_rss,\n"
    "        'index_size': index_size,\n"
    "        'files_read': files_read,\n"
    "    }))\n"
    "finally:\n"
    "    store.close()\n"
)


def run_build_subprocess(
    *,
    workspace: Path,
    index_dir: Path,
    measured_mode: str,
    change_count: int,
    do_warmup: bool,
    timeout_ms: int = 120_000,
) -> dict[str, float]:
    """Run the build phase in a fresh subprocess and return its metrics.

    The child performs the build and prints ``{wall, cpu, peak_rss,
    index_size, files_read}`` on stdout as a single JSON object. The
    parent parses the last non-empty line, so any incidental logging
    the child emits cannot shadow the JSON payload. The subprocess
    is bounded by ``timeout_ms`` (converted to seconds) plus a small
    grace window; any timeout raises ``subprocess.TimeoutExpired``
    which the caller surfaces as a test failure.

    ``do_warmup`` controls whether the child runs a one-shot full
    reindex before the measured phase. The cold build does not set
    this flag (its warm-up pass IS the measured phase); the changed
    refresh does set it so the steady-state reindex path is measured.
    """
    script = (
        _BUILD_RUNNER_SCRIPT
        .replace("__WORKSPACE_PATH__", repr(str(workspace)))
        .replace("__INDEX_DIR_PATH__", repr(str(index_dir)))
        .replace("__MEASURED_MODE__", repr(measured_mode))
        .replace("__CHANGE_COUNT__", str(change_count))
        .replace("__DO_WARMUP__", "True" if do_warmup else "False")
        .replace("__TIMEOUT_MS__", str(timeout_ms))
    )
    # mcp-timeout-ok: subprocess bounded by timeout; benchmark build is the
    # workload, the deadline is the wall budget.
    # resource-lifecycle-ok: short-lived benchmark subprocess; the parent
    # blocks on .run()'s timeout so the child cannot outlive the call, no
    # fd or process leaks across the harness.
    proc = subprocess.run(  # resource-lifecycle-ok: short-lived benchmark subprocess; parent blocks on .run()'s timeout so the child cannot outlive the call, no fd or process leaks across the harness.  # filesystem-poll-ok: same short-lived subprocess; the parent blocks on the bounded timeout, so this is not a poll loop.
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=(timeout_ms / 1000.0) + 30.0,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"build subprocess failed: {proc.stderr}")
    last_line = proc.stdout.strip().splitlines()[-1]
    payload_obj: object = json.loads(last_line)
    payload: dict[str, float] = payload_obj if isinstance(payload_obj, dict) else {}
    return {
        "wall": float(payload["wall"]),
        "cpu": float(payload["cpu"]),
        "peak_rss": float(payload["peak_rss"]),
        "index_size": float(payload["index_size"]),
        "files_read": float(payload["files_read"]),
    }
