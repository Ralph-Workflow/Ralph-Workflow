#!/usr/bin/env bash
# explore_inotify_probe.sh
#
# Linux-only receipt for the explore-linux-inotify-probe control in
# docs/ralph-workflow-policy/policy-portfolio.toml. Rerun this on a
# Linux host whenever the explore watcher implementation changes,
# capture the dated stdout/stderr, and attach the receipt to the
# change. Default-suite coverage is intentionally absent; the
# supported developer hosts are macOS and Windows where /proc is
# unavailable, so the assertion belongs in a one-off platform
# receipt, not in the 60s budget.

set -euo pipefail

if [ "$(uname -s)" != "Linux" ]; then
    printf 'explore_inotify_probe: Linux-only receipt (host=%s); skipping\n' "$(uname -s)" >&2
    exit 0
fi

if [ ! -d /proc/self/fd ]; then
    printf 'explore_inotify_probe: /proc/self/fd not available; skipping\n' >&2
    exit 0
fi

WORKSPACE=$(mktemp -d -t explore-inotify-XXXXXX)
trap 'rm -rf "$WORKSPACE"' EXIT

INDEX_DIR="$WORKSPACE/.agent/ralph-explore"
mkdir -p "$INDEX_DIR"

printf 'host=%s kernel=%s date=%s\n' "$(uname -s)" "$(uname -r)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

uv run --locked --project . python - <<PY
import os
import sys
from pathlib import Path

WORKSPACE = Path("$WORKSPACE")
INDEX_DIR = WORKSPACE / ".agent" / "ralph-explore"
INDEX_DIR.mkdir(parents=True, exist_ok=True)

from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
from ralph.mcp.explore.handlers import ExploreIndex
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files
from ralph.workspace.fs import FsWorkspace

# Seed at least one file so the workload is non-trivial.
(WORKSPACE / "sample.py").write_text("def sample():\n    return 1\n")

store = ExploreStore(INDEX_DIR)
try:
    reindex(store, WORKSPACE, options=ReindexOptions(timeout_ms=5_000))
finally:
    store.close()

store2 = ExploreStore(INDEX_DIR)
handle = build_sqlite_index_handle(store2)
session = type(
    "S",
    (),
    {
        "explore_index": ExploreIndex(
            workspace_root=WORKSPACE,
            index_root=INDEX_DIR,
            store=store2,
            generation=1,
        )
        if handle is None
        else handle,
        "check_capability": lambda self, c: {"status": "approved", "capability": c},
        "check_edit_area": lambda self, p: {"status": "approved", "path": p},
    },
)()
ws_obj = FsWorkspace(WORKSPACE)
for _ in range(5):
    handle_grep_files(
        session,
        ws_obj,
        {
            "pattern": "def",
            "path": ".",
            "regex": False,
            "case_sensitive": False,
            "use_index": "auto",
        },
    )
store2.close()

# Read /proc/self/fdinfo and tally inotify watches.
fd_dir = Path("/proc/self/fd")
total = 0
for entry in fd_dir.iterdir():
    try:
        target = entry.readlink().as_posix()
    except OSError:
        continue
    if "inotify" not in target:
        continue
    fdinfo = Path("/proc/self/fdinfo") / entry.name
    try:
        text = fdinfo.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        continue
    for line in text.splitlines():
        if line.startswith("inotify wd:"):
            total += 1

print(f"inotify_watches={total}")
sys.stdout.flush()
if total != 0:
    print(f"FAIL: explore substrate holds {total} inotify watches", file=sys.stderr)
    sys.exit(1)
print("PASS: zero inotify watches held by explore substrate after the workload")
PY
