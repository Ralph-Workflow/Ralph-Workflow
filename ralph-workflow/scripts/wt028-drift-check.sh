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

# Private temporary directory for parallel grep output. Per
# docs/ralph-workflow-policy/gate-script-policy.md § Security, predictable
# shared paths are a local privilege-escalation surface.
GREP_DIR="$(mktemp -d -t wt028_drift.XXXXXX)"
chmod 700 "$GREP_DIR"
cleanup() {
    rm -rf "$GREP_DIR"
}
trap cleanup EXIT

# Git enumerates tracked and non-ignored untracked files before ONE
# single-pass matcher process scans the relevant source suffixes. This avoids
# a cold metadata walk across thousands of files on external worktree volumes.
#
# Why not ``grep -lE`` here: BSD grep 2.6.0 (the macOS system grep)
# re-scans the corpus roughly once per alternation branch, and the two
# ``\s`` branches fall off its fast literal path entirely. Measured on
# this tree (2,719 files / 21.5 MB): a single literal costs ~0.37s, the
# full six-branch DRIFT_PATTERNS costs ~3.6s -- which blew the 2s bound
# below as the tree grew. One compiled regex in a single pass does the
# same work in ~0.3s, restoring real headroom rather than shaving the
# margin. Do NOT raise GREP_TIMEOUT_SECONDS to accommodate a slow scan;
# see docs/ralph-workflow-policy/gate-script-policy.md § Bounded.
#
# The pipeline stays under the watchdog below, so the bounded and fail-closed
# rc contract is unchanged, and the matcher keeps grep's exit
# statuses (0 = matched, 1 = no match, 2 = error). DRIFT_PATTERNS remains
# the single source of truth: it is passed through verbatim as argv and
# never re-spelled in a second dialect.
#
# The match runs on bytes rather than decoded text. This skips a UTF-8
# decode of the whole corpus, which is what removed the cold-cache spike
# (``verify`` runs ``verify-drift`` FIRST, so this gate is the one that
# pays for a cold page cache). The only semantic difference is that
# ``\s`` matches ASCII whitespace instead of also matching exotic Unicode
# spaces. That is inert for the invariant this gate protects: the two
# ``\s`` branches guard ``ctx.mode <op> <mode>`` and ``force_mode =``,
# which are Python token separators, and CPython's tokenizer rejects
# non-ASCII whitespace between tokens -- so no reachable .py drift can
# hide in the gap. Matching bytes also makes the scan immune to files
# that are not valid UTF-8 at all.
#
# The corpus is ~2,700 small files totalling ~21 MB, so the scan is
# dominated by per-file open/read latency, not by bytes and not by regex
# work: measured on this tree a serial pass costs ~5.9s (a plain ``cat``
# of the same file list costs ~3.4s even warm) while the whole compiled
# regex over the same bytes costs ~0.2s. The reads are therefore issued
# from a fixed set of worker threads -- ``read()`` releases the GIL, so
# the threads overlap the storage round-trips that are the actual cost,
# and the identical pass drops to ~0.25s. The worker count is fixed
# rather than derived from ``os.cpu_count()`` because the resource being
# saturated is I/O concurrency (outstanding requests to a high-latency
# external volume), not CPU. Do NOT raise GREP_TIMEOUT_SECONDS to
# accommodate a slow scan; see
# docs/ralph-workflow-policy/gate-script-policy.md § Bounded.
#
# Why hand-rolled threads instead of ``concurrent.futures``: this gate is
# the FIRST step ``make verify`` runs, so it pays cold-import cost for
# everything it touches, and importing ``concurrent.futures`` pulls in
# ``queue``/``weakref``/``_base`` for roughly 8x the import cost of plain
# ``threading``. Each worker takes a stride of the path list
# (``paths[start::READ_WORKERS]``), so there is no shared queue, no lock,
# and no work-distribution overhead; results are written to distinct
# preallocated slots, which is safe under CPython.
#
# The emitted path list stays in ``find`` order because ``results`` is
# indexed by position, so the downstream allowlist filtering is
# unchanged. Fail-closed in two places: a read error fails the whole scan
# with rc=2 (reported once the workers join, because a worker cannot exit
# the process out from under its peers), and a slot still holding its
# ``None`` sentinel -- which can only happen if a worker died before
# scanning its stride -- also fails with rc=2 rather than being read as
# "no drift here".
GREP_TIMEOUT_SECONDS=2
python3 -c '
import re
import subprocess
import sys
import threading

READ_WORKERS = 64

pattern = re.compile(sys.argv[1].encode("utf-8"))
try:
    listing = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z", "--", "ralph", "tests", "docs"],
        check=False,
        stdout=subprocess.PIPE,
        timeout=1.5,
    )
except subprocess.TimeoutExpired:
    sys.exit(124)
if listing.returncode != 0:
    sys.exit(2)
paths = [
    raw.decode(sys.getfilesystemencoding(), errors="surrogateescape")
    for raw in listing.stdout.split(b"\0")
    if raw.endswith((b".py", b".rst", b".md")) and b"/__pycache__/" not in raw
]
results = [None] * len(paths)


def scan(start):
    for index in range(start, len(paths), READ_WORKERS):
        path = paths[index]
        try:
            with open(path, "rb") as handle:
                blob = handle.read()
        except OSError as exc:
            results[index] = "cannot read {0}: {1}".format(path, exc)
            continue
        results[index] = pattern.search(blob) is not None


workers = [
    threading.Thread(target=scan, args=(start,))
    for start in range(min(READ_WORKERS, len(paths)))
]
for worker in workers:
    worker.start()
for worker in workers:
    worker.join()

unscanned = [
    paths[index] for index, result in enumerate(results) if result is None
]
errors = [result for result in results if isinstance(result, str)]
if errors or unscanned:
    for error in errors:
        sys.stderr.write(error + "\n")
    for path in unscanned:
        sys.stderr.write("scan worker died before reading {0}\n".format(path))
    sys.exit(2)

matched = False
for path, hit in zip(paths, results):
    if hit is True:
        sys.stdout.write(path + "\n")
        matched = True
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
