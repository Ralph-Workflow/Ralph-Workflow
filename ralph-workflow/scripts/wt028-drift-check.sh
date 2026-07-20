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

# Scan independent roots concurrently. A watchdog bounds the gate even if grep
# stalls; one recursive grep per root avoids serial directory-walk latency.
GREP_TIMEOUT_SECONDS=2
PIDS=()
ROOTS=(ralph tests docs)
for ROOT in "${ROOTS[@]}"; do
    grep -rln -E "$DRIFT_PATTERNS" "$ROOT/" \
        --include='*.py' --include='*.rst' --include='*.md' \
        --exclude='__pycache__' --exclude='*.pyc' >"$GREP_DIR/$ROOT.out" 2>"$GREP_DIR/$ROOT.err" &
    PIDS+=("$!")
done
(
    sleep "$GREP_TIMEOUT_SECONDS"
    : >"$GREP_DIR/timed_out"
    for PID in "${PIDS[@]}"; do
        kill "$PID" 2>/dev/null || true
    done
) &
WATCHDOG_PID="$!"
GREP_RCS=()
for PID in "${PIDS[@]}"; do
    set +e
    wait "$PID"
    GREP_RCS+=("$?")
    set -e
done
kill "$WATCHDOG_PID" 2>/dev/null || true
wait "$WATCHDOG_PID" 2>/dev/null || true

if [ -e "$GREP_DIR/timed_out" ]; then
    echo "FAIL: drift scan exceeded ${GREP_TIMEOUT_SECONDS}s and was stopped" >&2
    echo "Fix the slow scan; do not raise the gate timeout. Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Bounded." >&2
    exit 124
fi

GREP_RC=0
for ROOT_INDEX in "${!ROOTS[@]}"; do
    ROOT_RC="${GREP_RCS[$ROOT_INDEX]}"
    if [ "$ROOT_RC" -ne 0 ] && [ "$ROOT_RC" -ne 1 ]; then
        GREP_RC=2
    fi
done
DRIFT_HITS="$(cat "$GREP_DIR"/*.out)"

if [ "$GREP_RC" -eq 2 ]; then
    echo "FAIL: bad path or permission in upstream grep" >&2
    cat "$GREP_DIR"/*.err >&2
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
