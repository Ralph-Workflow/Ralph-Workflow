#!/bin/bash
# ralph_sync_detector.sh — Detect stuck-work conditions in Ralph Workflow sync
# Writes machine-readable JSON status to agents/marketing/logs/
# Does NOT perform any destructive recovery.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
EDITABLE_REPO="/home/mistlight/.openclaw/workspace/repos/Ralph-Workflow/github-mirror"
SYNC_SCRIPT="$SCRIPT_DIR/sync_to_github.sh"
OUTPUT="$LOG_DIR/ralph_sync_status.json"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

mkdir -p "$LOG_DIR"

# --- Condition checks ---

destructive_sync_layout=false
if grep -q 'git reset.*--hard.*origin/main' "$SYNC_SCRIPT" 2>/dev/null; then
    if grep -q 'WORK_DIR' "$SYNC_SCRIPT" 2>/dev/null || \
       grep -q 'MIRROR_DIR.*repos/Ralph-Workflow' "$SYNC_SCRIPT" 2>/dev/null; then
        destructive_sync_layout=true
    fi
fi
if grep -q 'rm -rf.*\$WORK_DIR' "$SYNC_SCRIPT" 2>/dev/null; then
    destructive_sync_layout=true
fi

editable_repo_missing=true
if [ -d "$EDITABLE_REPO/.git" ]; then
    editable_repo_missing=false
fi

local_unpushed_commits=false
dirty_worktree=false
if [ "$editable_repo_missing" = false ]; then
    pushd "$EDITABLE_REPO" >/dev/null
    unpushed=$(git log --oneline "@{upstream}..HEAD" 2>/dev/null | wc -l)
    if [ "$unpushed" -gt 0 ] 2>/dev/null; then
        local_unpushed_commits=true
    fi
    if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
        dirty_worktree=true
    fi
    popd >/dev/null
fi

verification_blocked=false
if [ ! -f "$EDITABLE_REPO/run_verify.sh" ] && [ ! -f "$EDITABLE_REPO/run_tests.sh" ]; then
    verification_blocked=true
fi

verification_tool_missing=false
if [ ! -f "$EDITABLE_REPO/run_verify.sh" ]; then
    verification_tool_missing=true
fi

# --- Determine auto_recovery_action (non-destructive recommendation only) ---
action="none"
if [ "$destructive_sync_layout" = true ]; then
    action="Update sync_to_github.sh to use a dedicated sync clone path (e.g., /tmp/ralph-github-sync) instead of operating on the editable checkout"
elif [ "$editable_repo_missing" = true ]; then
    action="Clone editable repo: git clone git@codeberg.org:RalphWorkflow/Ralph-Workflow.git $EDITABLE_REPO"
elif [ "$local_unpushed_commits" = true ] && [ "$dirty_worktree" = true ]; then
    action="Review and commit dirty worktree changes, then push unpushed commits in $EDITABLE_REPO before next sync"
elif [ "$local_unpushed_commits" = true ]; then
    action="Push unpushed commits: git push origin main (from $EDITABLE_REPO)"
elif [ "$dirty_worktree" = true ]; then
    action="Commit or stash dirty worktree changes in $EDITABLE_REPO"
elif [ "$verification_blocked" = true ]; then
    action="Install verification tool (run_verify.sh or run_tests.sh) in $EDITABLE_REPO"
fi

# --- Write JSON ---
cat > "$OUTPUT" << JSONEOF
{
  "timestamp": "$TIMESTAMP",
  "script": "$SYNC_SCRIPT",
  "editable_repo": "$EDITABLE_REPO",
  "conditions": {
    "destructive_sync_layout": $destructive_sync_layout,
    "editable_repo_missing": $editable_repo_missing,
    "local_unpushed_commits": $local_unpushed_commits,
    "dirty_worktree": $dirty_worktree,
    "verification_blocked": $verification_blocked,
    "verification_tool_missing": $verification_tool_missing
  },
  "auto_recovery_action": "$action"
}
JSONEOF

echo "Written status to $OUTPUT"
