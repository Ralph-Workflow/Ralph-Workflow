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

# DA-001 (wt-028-display): the watchdog must terminate the entire
# scan + untracked-walk process trees inside the 2 s gate without
# the bash interpreter hanging on orphaned busy-loop ``git`` children
# at script exit. ``set -o monitor`` enables job control so each
# backgrounded subshell becomes its own process-group leader; the
# watchdog then SIGKILLs the *process group* (not just the bash
# subshell PID), which reaps every orphan the script forked.
# Without job control, the subshell PID and the busy-loop ``git``
# child share the parent bash's process group, so a SIGKILL on the
# subshell leaves the ``git`` orphaned and busy-looping. Bash then
# waits for those orphans on exit, holding the script past the
# 3-second test ceiling even though the FAIL message has already
# been printed.
set -o monitor

# Find the ralph-workflow root regardless of cwd.
RALPH_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RALPH_ROOT"

# The git-grep ERE equivalent of the Python regex. ``\s`` is
# non-standard in POSIX ERE so we spell out the POSIX character class.
# The single / double-quote alternation is embedded inside the
# outer double-quoted bash string so both ``'`` and ``"`` appear
# literally in the regex -- git grep's POSIX ERE does not interpret
# ``\x27`` / ``\x22`` byte escapes as the corresponding quote, so
# bracket-class tricks (``[\x27\x22]``) silently fail to match. The
# inner ``\"`` is escaped to land in the regex as a literal ``"``.
DRIFT_PATTERNS="NARROW_THRESHOLD|MEDIUM_THRESHOLD|ctx\.mode[[:space:]]*[!=]=[[:space:]]*['\"](compact|medium|wide)['\"]|RALPH_FORCE_NARROW|force_mode[[:space:]]*=|DISPLAY_MODE"
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

# The matcher uses ``git grep -lIE --threads 8`` (POSIX ERE via git's
# built-in regex engine). ``-I`` skips binary files so a stray .pyc
# does not produce a match; ``-E`` uses POSIX extended regex. ``-l``
# lists matching paths only (one per line), which is the same shape
# the downstream allowlist filter expects. ``--threads 8`` parallelises
# the per-file scan so a cold-cache scan of ~2,836 candidate files
# (22 MB total) completes inside the 2 s bound; single-threaded
# git grep runs in ~3.7 s on this tree, which blows the bound.
#
# Why ``git grep -lIE --threads 8`` instead of a Python harness
# (DA-001 / DA-007): git grep uses ``mmap(2)`` + parallel internal
# workers + a compiled C regex, which is dramatically faster than a
# Python open + read loop on cold cache. Measured on this tree
# (~2,836 candidate files / ~22 MB): the Python harness took
# 2.0-5.1 s and frequently blew the 2 s bound on cold cache;
# ``git grep -lIE --threads 8`` completes in ~0.5-0.9 s cold-cache.
# The prior comment that ruled out ``git grep -lE`` was measured on
# a smaller tree without ``--threads``; the threading is the speed
# win on cold cache.
#
# Why not ``grep -lE`` (BSD grep 2.6.0 on macOS): BSD grep re-scans
# the corpus roughly once per alternation branch, and the two
# ``[[:space:]]`` branches fall off its fast literal path. A
# compiled C regex in one pass does the same work in roughly 0.3-0.4 s.
#
# Why not a thread / process pool here: git grep already parallelises
# internally on SSDs without thrashing slow-volume disks.
#
# Why ``--no-index`` is intentionally absent here: the drift
# invariant is for code that ships in the tree, which is the
# tracked-file set. Untracked files (tmp/, scratch dirs) are out of
# scope for the architectural invariant because they never reach CI.
# If a future regression needs the untracked-file coverage,
# ``--no-index`` adds the working-tree scan at the cost of ~0.5 s;
# the watchdog still bounds the total runtime.
#
# Bytes rather than decoded text: ``git grep -E`` already matches
# bytes. The only semantic difference from a Python ``re`` call is
# that ``[[:space:]]`` matches ASCII whitespace instead of also
# matching exotic Unicode spaces. That is inert for the invariant
# this gate protects: the two ``[[:space:]]`` branches guard
# ``ctx.mode <op> <mode>`` and ``force_mode =``, which are Python
# token separators, and CPython's tokenizer rejects non-ASCII
# whitespace between tokens -- so no reachable .py drift can hide
# in the gap.
GREP_TIMEOUT_SECONDS=2
# Scan the tracked set with ``git grep -lIE`` (uses the git index --
# ~0.5-0.9 s cold cache on this tree) and the untracked set with
# ``git grep --no-index`` on the small untracked file list. The
# untracked list is completed before it is scanned so a slow walk
# cannot be mistaken for an empty list. This keeps the budget inside
# 2 s while still catching synthetic probe files dropped by the
# drift-check self-tests (e.g. ``ralph/_drift_probe_*.py``).
# DA-001 / DA-007 (wt-028-display): the previous Python harness
# also scanned both tracked and untracked files; the ``--no-index``
# half keeps that contract without falling back to the slow
# working-tree-wide scan the early ``git grep -lIE --no-index
# -- ralph tests docs`` shape would force.
TRACKED_OUT="$GREP_DIR/scan_tracked.out"
UNTRACKED_FILES="$GREP_DIR/untracked.list"
UNTRACKED_OUT="$GREP_DIR/scan_untracked.out"
: >"$GREP_DIR/scan.err"
(
    # Enumerate tracked files in the working tree. The call lives
    # inside the backgrounded subshell so a git failure (rc != 0)
    # is captured by ``wait`` rather than tripping ``set -e`` and
    # aborting the script with the raw exit code (e.g. 127 from a
    # stub git on PATH). The drift gate then maps any non-trivial
    # git failure to the script's canonical rc=2 error envelope.
    git ls-files -z -- ralph tests docs \
        | tr '\0' '\n' \
        > "$TRACKED_OUT"
    git grep -lIE --threads 8 "$DRIFT_PATTERNS" -- ralph tests docs \
        2>>"$GREP_DIR/scan.err" \
        > "$TRACKED_OUT"
    # Finish untracked enumeration before deciding there are no probes.
    # A slow walk is bounded by this scan's watchdog rather than silently skipped.
    git ls-files --others --exclude-standard -z -- ralph tests docs > "$UNTRACKED_FILES"
    if [ -s "$UNTRACKED_FILES" ]; then
        # --no-index tells git grep to ignore the index and search
        # the working tree, so it picks up files the index scan
        # above did not see. Read NUL-delimited paths directly so
        # whitespace remains part of the filename on Bash 3 and later.
        while IFS= read -r -d '' untracked_path; do
            untracked_rc=0
            git grep -lIE --no-index --threads 8 "$DRIFT_PATTERNS" \
                -- "$untracked_path" \
                2>>"$GREP_DIR/scan.err" \
                >> "$TRACKED_OUT" || untracked_rc=$?
            if [ "$untracked_rc" -gt 1 ]; then
                exit "$untracked_rc"
            fi
        done < "$UNTRACKED_FILES"
    fi
    sort -u "$TRACKED_OUT" > "$GREP_DIR/scan.out"
) &
SCAN_PID="$!"
SCAN_PGID="$(ps -o pgid= -p "$SCAN_PID" | tr -d ' ')"
(
    sleep "$GREP_TIMEOUT_SECONDS"
    : >"$GREP_DIR/timed_out"
    # SIGKILL the scan subshell so a
    # busy-loop ``git`` child (the DA-001 stall injection in
    # ``tests/display/test_single_mode_anti_drift.py::test_drift_check_times_out_when_search_stalls``)
    # cannot block the watchdog on a non-responsive child. A regular
    # SIGTERM lets a busy loop keep the subshell alive, which then
    # hangs ``wait "$SCAN_PID"`` past the watchdog bound; SIGKILL
    # terminates bash immediately and ``wait`` unblocks with the
    # kill status. The child ``git`` processes are reparented to
    # init when bash dies; the kernel reaps them on its own. The
    # trailing ``|| true`` swallows ESRCH when the subshell already
    # exited. The scan group owns both file walks, so no child can
    # survive to make bash wait at script exit.
    #
    # We kill the *process group* (``kill -KILL -$PGID``), not the
    # bash subshell PID alone. With ``set -o monitor`` the subshells
    # are process-group leaders; killing the group reaps every
    # orphan ``git`` child the script forked, which is what lets
    # bash exit promptly instead of waiting on the orphaned busy
    # loops at script teardown.
    kill -KILL -"$SCAN_PGID" 2>/dev/null || true
    # The leading PID kill is kept as a belt-and-braces fallback in
    # case the PGID lookup raced and the PGID points at a stale
    # group that has already been reaped. The negative-PID kill
    # always fires first so the trailing ``kill $PID`` is a no-op
    # in the normal case.
    kill -KILL "$SCAN_PID" 2>/dev/null || true
) &
WATCHDOG_PID="$!"
set +e
wait "$SCAN_PID"
GREP_RC="$?"
set -e
kill -9 "$WATCHDOG_PID" 2>/dev/null || true
wait "$WATCHDOG_PID" 2>/dev/null || true

if [ -e "$GREP_DIR/timed_out" ]; then
    echo "FAIL: drift scan exceeded ${GREP_TIMEOUT_SECONDS}s and was stopped" >&2
    echo "Fix the slow scan; do not raise the gate timeout. Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Bounded." >&2
    # Detach any remaining background jobs so bash does not block
    # in its implicit wait-for-jobs at script exit. The watchdog
    # already SIGKILLed the scan subshell; any stray ``git``
    # orphans are reparented to init and reaped by the kernel,
    # but bash's job table still tracks them, and the implicit
    # exit wait would otherwise hold the script open past the
    # test's 3-second timeout.
    disown -a 2>/dev/null || true
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

if [ "$GREP_RC" -eq 2 ]; then
    echo "FAIL: bad path or permission in upstream grep" >&2
    cat "$GREP_DIR/scan.err" >&2
    echo "" >&2
    echo "Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Default requirements (fail-closed)." >&2
    exit 2
fi

# ``set -e`` exits the script on a failed ``cat`` of a non-existent
# scan.out, so read the file with a guarded empty fallback. A
# non-existent scan.out means the scan produced no output (no drift).
DRIFT_HITS=""
if [ -s "$GREP_DIR/scan.out" ]; then
    DRIFT_HITS="$(cat "$GREP_DIR/scan.out")"
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

# Command output must use the shared display. Match executable-looking lines only;
# comments and docstrings may explain the retired direct-output paths. The smoke
# EXIT_CODE line is a documented machine contract and the sole allowed exception.
COMMAND_OUTPUT_HITS="$(git grep -nE '^[[:space:]]*(print\(|typer\.echo\(|sys\.stdout\.write\(|[[:alnum:]_\.]+\.console\.print\()' -- ralph/cli ralph/config ralph/pipeline \
    | grep -v '^ralph/cli/commands/smoke.py:513:' || true)"
if [ -n "$COMMAND_OUTPUT_HITS" ]; then
    echo "FAIL: private command display path detected" >&2
    echo "$COMMAND_OUTPUT_HITS" >&2
    echo "Route operator-facing output through display.emit_*; smoke EXIT_CODE= is the only machine-contract exception." >&2
    exit 1
fi

echo "PASS: drift check clean (no retired display modes or private command output paths)"
exit 0