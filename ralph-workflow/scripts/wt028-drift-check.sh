#!/usr/bin/env bash

set -euo pipefail

set -o monitor

RALPH_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RALPH_ROOT"

DRIFT_PATTERNS="NARROW_THRESHOLD|MEDIUM_THRESHOLD|ctx\.mode[[:space:]]*[!=]=[[:space:]]*['\"](compact|medium|wide)['\"]|RALPH_FORCE_NARROW|force_mode[[:space:]]*=|DISPLAY_MODE"
ALLOWLIST_PATTERNS='tests/test_display_context\.py|tests/unit/display/test_display_context\.py|tests/unit/display/test_mode\.py|tests/unit/display/test_context_resize_display_context_refreshed\.py|tests/unit/display/test_parallel_display_t22\.py|tests/test_no_anti_drift_regression\.py'
HISTORICAL_ALLOWLIST_PATTERNS='ralph/display/status_bar\.py|ralph/display/__init__\.py|ralph/display/mode\.py|ralph/display/_mode_adaptive_limits\.py|ralph/display/context\.py|docs/sphinx/.*\.rst|docs/sphinx/.*\.md'

GREP_DIR="$(mktemp -d -t wt028_drift.XXXXXX)"
chmod 700 "$GREP_DIR"
cleanup() {
    if [ -n "${WATCHDOG_PID:-}" ]; then
        kill -KILL -"$WATCHDOG_PID" 2>/dev/null || true
        kill -KILL "$WATCHDOG_PID" 2>/dev/null || true
    fi
    if [ -n "${SCAN_PGID:-}" ]; then
        kill -KILL -"$SCAN_PGID" 2>/dev/null || true
    fi
    if [ -n "${SCAN_PID:-}" ]; then
        kill -KILL "$SCAN_PID" 2>/dev/null || true
    fi
    rm -rf "$GREP_DIR"
}
trap cleanup EXIT

GREP_TIMEOUT_SECONDS="${GREP_TIMEOUT_SECONDS:-2}"
TRACKED_OUT="$GREP_DIR/scan_tracked.out"
UNTRACKED_FILES="$GREP_DIR/untracked.list"
UNTRACKED_OUT="$GREP_DIR/scan_untracked.out"
: >"$GREP_DIR/scan.err"
(
    set +e
    tracked_files_rc=0
    git ls-files -z -- ralph tests docs >/dev/null 2>>"$GREP_DIR/scan.err" || tracked_files_rc=$?
    if [ "$tracked_files_rc" -ne 0 ]; then
        exit "$tracked_files_rc"
    fi
    CANDIDATE_LIST="$GREP_DIR/candidates.list"
    tracked_scan_rc=0
    git grep -lzIF --threads 8 \
        -e NARROW_THRESHOLD \
        -e MEDIUM_THRESHOLD \
        -e ctx.mode \
        -e RALPH_FORCE_NARROW \
        -e force_mode \
        -e DISPLAY_MODE \
        -- ralph tests docs \
        > "$CANDIDATE_LIST" 2>>"$GREP_DIR/scan.err" || tracked_scan_rc=$?
    if [ "$tracked_scan_rc" -gt 1 ]; then
        exit "$tracked_scan_rc"
    fi
    : > "$TRACKED_OUT"
    if [ -s "$CANDIDATE_LIST" ]; then
        candidates=()
        while IFS= read -r -d '' candidate; do
            candidates[${#candidates[@]}]="$candidate"
            if [ "${#candidates[@]}" -ge 50 ]; then
                scan_rc=0
                git grep -lIE --threads 8 "$DRIFT_PATTERNS" -- "${candidates[@]}" \
                    >> "$TRACKED_OUT" 2>>"$GREP_DIR/scan.err" || scan_rc=$?
                if [ "$scan_rc" -gt 1 ]; then
                    exit "$scan_rc"
                fi
                candidates=()
            fi
        done < "$CANDIDATE_LIST"
        if [ "${#candidates[@]}" -gt 0 ]; then
            scan_rc=0
            git grep -lIE --threads 8 "$DRIFT_PATTERNS" -- "${candidates[@]}" \
                >> "$TRACKED_OUT" 2>>"$GREP_DIR/scan.err" || scan_rc=$?
            if [ "$scan_rc" -gt 1 ]; then
                exit "$scan_rc"
            fi
        fi
    fi
    untracked_files_rc=0
    git ls-files --others --exclude-standard -z -- ralph tests docs > "$UNTRACKED_FILES" \
        || untracked_files_rc=$?
    if [ "$untracked_files_rc" -ne 0 ]; then
        exit "$untracked_files_rc"
    fi
    if [ -s "$UNTRACKED_FILES" ]; then
        untracked=()
        while IFS= read -r -d '' untracked_path; do
            untracked[${#untracked[@]}]="$untracked_path"
            if [ "${#untracked[@]}" -ge 50 ]; then
                untracked_rc=0
                git grep -lIE --no-index --threads 8 "$DRIFT_PATTERNS" \
                    -- "${untracked[@]}" \
                    >> "$TRACKED_OUT" 2>>"$GREP_DIR/scan.err" || untracked_rc=$?
                if [ "$untracked_rc" -gt 1 ]; then
                    exit "$untracked_rc"
                fi
                untracked=()
            fi
        done < "$UNTRACKED_FILES"
        if [ "${#untracked[@]}" -gt 0 ]; then
            untracked_rc=0
            git grep -lIE --no-index --threads 8 "$DRIFT_PATTERNS" \
                -- "${untracked[@]}" \
                >> "$TRACKED_OUT" 2>>"$GREP_DIR/scan.err" || untracked_rc=$?
            if [ "$untracked_rc" -gt 1 ]; then
                exit "$untracked_rc"
            fi
        fi
    fi
    sort -u "$TRACKED_OUT" > "$GREP_DIR/scan.out"
    sort_rc=$?
    if [ "$sort_rc" -ne 0 ]; then
        exit "$sort_rc"
    fi
    exit 0
) &
SCAN_PID="$!"
SCAN_PGID="$(ps -o pgid= -p "$SCAN_PID" 2>/dev/null | tr -d ' ' || true)"
(
    sleep "$GREP_TIMEOUT_SECONDS"
    : >"$GREP_DIR/timed_out"
    kill -KILL -"$SCAN_PGID" 2>/dev/null || true
    kill -KILL "$SCAN_PID" 2>/dev/null || true
) < /dev/null > /dev/null 2>&1 &
WATCHDOG_PID="$!"
set +e
wait "$SCAN_PID"
GREP_RC="$?"
set -e
disown "$WATCHDOG_PID" 2>/dev/null || true
kill -KILL -"$WATCHDOG_PID" 2>/dev/null || true
kill -KILL "$WATCHDOG_PID" 2>/dev/null || true
wait "$WATCHDOG_PID" 2>/dev/null || true

if [ -e "$GREP_DIR/timed_out" ]; then
    echo "FAIL: drift scan exceeded ${GREP_TIMEOUT_SECONDS}s and was stopped" >&2
    echo "Fix the slow scan; do not raise the gate timeout. Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Bounded." >&2
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

DRIFT_HITS=""
if [ -s "$GREP_DIR/scan.out" ]; then
    DRIFT_HITS="$(cat "$GREP_DIR/scan.out")"
fi

FILTERED="$(echo "$DRIFT_HITS" | grep -v -E "$ALLOWLIST_PATTERNS" | grep -v -E "$HISTORICAL_ALLOWLIST_PATTERNS" || true)"

if [ -n "$FILTERED" ]; then
    echo "FAIL: drift detected in the consolidated single-mode invariant" >&2
    echo "$FILTERED" >&2
    echo "" >&2
    echo "Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Default requirements (fail-closed)." >&2
    exit 1
fi

COMMAND_OUTPUT_HITS="$(git grep -nE '^[[:space:]]*(print\(|typer\.echo\(|sys\.stdout\.write\(|[[:alnum:]_\.]+\.console\.print\()' -- ralph/cli ralph/config ralph/pipeline \
    | grep -vE '^ralph/cli/commands/smoke\.py:[0-9]+:[[:space:]]*sys\.stdout\.write\(f"EXIT_CODE=\{exit_code\}\\n"\)$' || true)"
if [ -n "$COMMAND_OUTPUT_HITS" ]; then
    echo "FAIL: private command display path detected" >&2
    echo "$COMMAND_OUTPUT_HITS" >&2
    echo "Route operator-facing output through display.emit_*; smoke EXIT_CODE= is the only machine-contract exception." >&2
    exit 1
fi

echo "PASS: drift check clean (no retired display modes or private command output paths)"
exit 0