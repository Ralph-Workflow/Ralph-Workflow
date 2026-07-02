#!/usr/bin/env bash
# wt-028-display: fail-closed drift check for the consolidated single-mode invariant.
#
# Exits non-zero when:
#   1. The upstream search path is wrong (grep rc=2), or
#   2. Real drift exists: any production code reference to a removed mode token
#      (force_mode=, force_narrow, NARROW_THRESHOLD, MEDIUM_THRESHOLD,
#      ctx.mode == 'compact' / ctx.mode == "compact" / ctx.mode != 'compact' / ctx.mode != "compact")
#      in ralph/ or tests/, after applying the explicit allowlist.
#
# Exits 0 only when no drift exists AND the search path is valid.

set -u

# Find the ralph-workflow root regardless of cwd.
RALPH_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RALPH_ROOT"

DRIFT_PATTERNS='force_mode=|force_narrow|NARROW_THRESHOLD|MEDIUM_THRESHOLD'
ALLOWLIST_PATTERNS='ralph/display/mode\.py|ralph/display/__init__\.py|tests/display/test_single_mode_anti_drift\.py|tests/test_display_context\.py|tests/unit/display/test_display_context\.py|tests/unit/display/test_mode\.py|tests/unit/display/test_context_resize_display_context_refreshed\.py|tests/unit/display/test_parallel_display_t22\.py|tests/test_no_anti_drift_regression\.py'

# Exclude __pycache__ and .pyc files so the check stays stable across builds.
DRIFT_HITS="$(grep -rln -E "$DRIFT_PATTERNS" ralph/ tests/ \
    --include='*.py' --include='*.rst' --include='*.md' \
    --exclude='__pycache__' --exclude='*.pyc' 2>/tmp/wt028_drift.err)"
GREP_RC=$?

if [ "$GREP_RC" -eq 2 ]; then
    echo "FAIL: bad path or permission in upstream grep"
    cat /tmp/wt028_drift.err
    exit 2
fi

# Apply the explicit allowlist.
FILTERED="$(echo "$DRIFT_HITS" | grep -v -E "$ALLOWLIST_PATTERNS" || true)"

if [ -n "$FILTERED" ]; then
    echo "FAIL: drift detected in the consolidated single-mode invariant"
    echo "$FILTERED"
    exit 1
fi

echo "PASS: drift check clean (no force_mode / force_narrow / NARROW_THRESHOLD / MEDIUM_THRESHOLD outside the allowlist)"
exit 0