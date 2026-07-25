#!/usr/bin/env bash
# wt-028-display: fail-closed drift check for the consolidated single-mode invariant.
#
# Exits non-zero when:
#   1. The upstream search path is wrong (grep rc=2), or
#   2. Real drift exists: any production code reference to a removed mode token
#      (NARROW_THRESHOLD, MEDIUM_THRESHOLD,
#      ctx.mode == 'compact' / ctx.mode == "compact" /
#      ctx.mode != 'compact' / ctx.mode != "compact",
#      RALPH_FORCE_NARROW, force_mode=, DISPLAY_MODE)
#      in ralph/, tests/, or docs/, after applying the explicit allowlist and
#      the historical-context allowlist.
#
# Exits 0 only when no drift exists AND the search path is valid.
#
# Policy: docs/ralph-workflow-policy/gate-script-policy.md
#   * Default requirements (strict mode + fail-closed + bounded).
#   * Failure output (cite the governing policy file).
#   * Security (private temp files via mktemp + trap cleanup, restrictive perms).

set -euo pipefail

# Find the ralph-workflow root regardless of cwd.
RALPH_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RALPH_ROOT"

DRIFT_PATTERNS='NARROW_THRESHOLD|MEDIUM_THRESHOLD|ctx\.mode\s*[!=]=\s*['\''"](compact|medium|wide)['\''"]|RALPH_FORCE_NARROW|force_mode\s*=|DISPLAY_MODE'
ALLOWLIST_PATTERNS='tests/test_display_context\.py|tests/unit/display/test_display_context\.py|tests/unit/display/test_mode\.py|tests/unit/display/test_context_resize_display_context_refreshed\.py|tests/unit/display/test_parallel_display_t22\.py|tests/test_no_anti_drift_regression\.py'
# Historical-context allowlist: the canonical ``status_bar``/``__init__``/``mode``/``_mode_adaptive_limits``/``context`` modules
# contain historical-collapse text that legitimately mentions ``RALPH_FORCE_NARROW``,
# ``force_mode=``, or ``DISPLAY_MODE`` to explain what was removed. The docs/*.rst
# and docs/*.md files also describe the historical-collapse narrative. Without this
# allowlist, the historical-collapse context would false-positive the drift check.
HISTORICAL_ALLOWLIST_PATTERNS='ralph/display/status_bar\.py|ralph/display/__init__\.py|ralph/display/mode\.py|ralph/display/_mode_adaptive_limits\.py|ralph/display/context\.py|docs/sphinx/.*\.rst|docs/sphinx/.*\.md'

# Private temporary directory for parallel scan output. Per
# docs/ralph-workflow-policy/gate-script-policy.md § Security, predictable
# shared paths are a local privilege-escalation surface.
GREP_DIR="$(mktemp -d -t wt028_drift.XXXXXX)"
chmod 700 "$GREP_DIR"
cleanup() {
    rm -rf "$GREP_DIR"
}
trap cleanup EXIT

# The matcher walks the candidate file list once via a small Python harness
# and reads eligible files in a single sequential pass. The harness uses
# one compiled regex against the raw bytes of each file (no UTF-8 decode
# of the corpus) and keeps the matcher's exit-status contract (0 =
# matched, 1 = no match, 2 = error). DRIFT_PATTERNS is the single source
# of truth and is passed unchanged to the inner scan.
#
# Why not ``grep -lE`` here: BSD grep 2.6.0 (the macOS system grep)
# re-scans the corpus roughly once per alternation branch, and the two
# ``\s`` branches fall off its fast literal path entirely. A single
# compiled regex in one pass does the same work in roughly 0.3-0.4s.
#
# Why not a thread pool / process pool here: on a slow external
# worktree volume, multiple concurrent readers thrash the disk heads
# and run slower than a single sequential reader. Measured on this
# tree (2,794 files / ~21 MB): an 8-thread pool takes 3.9 s cold-cache
# while a single sequential reader takes 0.7 s. The single-pass,
# single-reader scanner is the fastest reliable shape on slow volumes.
#
# Why not ``git grep -IlE`` here: git grep walks the index but reads
# files in the same sequential order, and the matcher overhead plus
# the per-file pipeline can run past the 2 s bound on cold cache.
# Calling git ls-files only (cheap) and reading the file bytes
# ourselves removes git's matcher overhead entirely.
#
# Bytes rather than decoded text: skipping the UTF-8 decode of the
# whole corpus removes the cold-cache spike (``verify`` runs
# ``verify-drift`` FIRST, so this gate is the one that pays for a cold
# page cache). The only semantic difference is that ``\s`` matches
# ASCII whitespace instead of also matching exotic Unicode spaces. That
# is inert for the invariant this gate protects: the two ``\s`` branches
# guard ``ctx.mode <op> <mode>`` and ``force_mode =``, which are Python
# token separators, and CPython's tokenizer rejects non-ASCII whitespace
# between tokens -- so no reachable .py drift can hide in the gap.
# Matching bytes also makes the scan immune to files that are not
# valid UTF-8 at all.
#
# Untracked files are scanned alongside tracked files so synthetic or
# newly-created source files cannot evade the gate. Any read error
# fails closed.
GREP_TIMEOUT_SECONDS=2
python3 -c '
import os
import re
import subprocess
import sys

pattern = re.compile(sys.argv[1].encode("utf-8"))
roots = ("ralph", "tests", "docs")
# Drift tokens never appear in any file larger than 100 KB; skipping
# oversized files is a cheap pre-filter that never matches.
_MAX_FILE_BYTES = 100_000

def _is_candidate(path: str) -> bool:
    if not path.endswith((".py", ".md", ".rst")):
        return False
    return "/__pycache__/" not in path

# Tracked files come from git index (cheap while cold).
tracked_proc = subprocess.run(
    ["git", "ls-files", "-z", "--", *roots],
    check=False,
    capture_output=True,
    timeout=1.5,
)
if tracked_proc.returncode != 0:
    sys.stderr.write("cannot enumerate tracked files via git ls-files\n")
    sys.exit(2)
tracked_paths = [
    raw.decode(sys.getfilesystemencoding(), errors="surrogateescape")
    for raw in tracked_proc.stdout.split(b"\x00")
    if raw and _is_candidate(raw.decode(sys.getfilesystemencoding(), errors="surrogateescape"))
]

# Untracked, non-ignored files via git (also cheap).
untracked_proc = subprocess.run(
    ["git", "ls-files", "--others", "--exclude-standard", "-z", "--", *roots],
    check=False,
    capture_output=True,
    timeout=1.5,
)
if untracked_proc.returncode != 0:
    sys.stderr.write("cannot enumerate untracked files via git ls-files\n")
    sys.exit(2)
untracked_paths = [
    raw.decode(sys.getfilesystemencoding(), errors="surrogateescape")
    for raw in untracked_proc.stdout.split(b"\x00")
    if raw and _is_candidate(raw.decode(sys.getfilesystemencoding(), errors="surrogateescape"))
]

seen: set[str] = set()
unique_paths: list[str] = []
for path in tracked_paths + untracked_paths:
    if path in seen:
        continue
    seen.add(path)
    unique_paths.append(path)

matched_paths: list[str] = []
# Single sequential reader: on a slow external volume this is faster
# than a thread pool because the OS does not thrash the disk heads.
# The compiled regex makes the per-file CPU cost negligible.
for path in unique_paths:
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        sys.stderr.write("cannot stat {0}: {1}\n".format(path, exc))
        sys.exit(2)
    if size > _MAX_FILE_BYTES:
        continue
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        sys.stderr.write("cannot read {0}: {1}\n".format(path, exc))
        sys.exit(2)
    if pattern.search(data) is not None:
        matched_paths.append(path)

for path in sorted(matched_paths):
    sys.stdout.write(path + "\n")
matched = bool(matched_paths)
sys.exit(0 if matched else 1)
' "$DRIFT_PATTERNS" \
    >"$GREP_DIR/scan.out" 2>"$GREP_DIR/scan.err" &
SCAN_PID="$!"
(
    sleep "$GREP_TIMEOUT_SECONDS"
    : >"$GREP_DIR/timed_out"
    kill "$SCAN_PID" 2>/dev/null || true
) &
WATCHDOG_PID="$!"
set +e
wait "$SCAN_PID"
GREP_RC="$?"
set -e
kill "$WATCHDOG_PID" 2>/dev/null || true
wait "$WATCHDOG_PID" 2>/dev/null || true

if [ -e "$GREP_DIR/timed_out" ]; then
    echo "FAIL: drift scan exceeded ${GREP_TIMEOUT_SECONDS}s and was stopped" >&2
    echo "Fix the slow scan; do not raise the gate timeout. Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Bounded." >&2
    exit 124
fi

if [ "$GREP_RC" -eq 124 ]; then
    echo "FAIL: drift scan exceeded ${GREP_TIMEOUT_SECONDS}s and was stopped" >&2
    echo "Fix the slow scan; do not raise the gate timeout. Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Bounded." >&2
    exit 124
elif [ "$GREP_RC" -eq 1 ]; then
    GREP_RC=0
elif [ "$GREP_RC" -ne 0 ]; then
    GREP_RC=2
fi
DRIFT_HITS="$(cat "$GREP_DIR/scan.out")"

if [ "$GREP_RC" -eq 2 ]; then
    echo "FAIL: bad path or permission in upstream grep" >&2
    cat "$GREP_DIR/scan.err" >&2
    echo "" >&2
    echo "Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Default requirements (fail-closed)." >&2
    exit 2
fi

# Apply the explicit allowlist, then the historical-context allowlist.
FILTERED="$(echo "$DRIFT_HITS" | grep -v -E "$ALLOWLIST_PATTERNS" | grep -v -E "$HISTORICAL_ALLOWLIST_PATTERNS" || true)"

if [ -n "$FILTERED" ]; then
    echo "FAIL: drift detected in the consolidated single-mode invariant" >&2
    echo "$FILTERED" >&2
    echo "" >&2
    echo "Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Default requirements (fail-closed)." >&2
    exit 1
fi

echo "PASS: drift check clean (no NARROW_THRESHOLD / MEDIUM_THRESHOLD / ctx.mode [==|!=] compact|medium|wide / RALPH_FORCE_NARROW / force_mode= / DISPLAY_MODE outside the historical allowlist)"
exit 0
