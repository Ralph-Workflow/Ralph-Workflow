#!/bin/bash
# sync_to_github.sh — Pure mirror. Codeberg -> GitHub. Nothing else.
# Run via cron every 30 minutes.
#
# NO divergence. NO mirror notice. NO SEO tricks.
# GitHub is EXACTLY Codeberg, always.
#
# If this script ever creates a divergent commit, it's a bug.

set -euo pipefail

LOG_DIR="/home/mistlight/.openclaw/workspace/agents/marketing/logs"
SYNC_LOCK="/tmp/ralph_github_sync.lock"
SYNC_DIR="/tmp/ralph-github-sync"
CODEBERG_REMOTE="git@codeberg.org:RalphWorkflow/Ralph-Workflow.git"
GITHUB_REMOTE="git@github.com:Ralph-Workflow/Ralph-Workflow.git"

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_DIR/sync_github.log"
}

# Prevent concurrent runs
if [ -f "$SYNC_LOCK" ]; then
    log "Sync already running, skipping"
    exit 0
fi
trap 'rm -f "$SYNC_LOCK"' EXIT
touch "$SYNC_LOCK"

log "Starting mirror sync"

if [ -d "$SYNC_DIR/.git" ]; then
    cd "$SYNC_DIR"
    git remote set-url origin "$CODEBERG_REMOTE"
    git remote set-url github "$GITHUB_REMOTE" 2>/dev/null || git remote add github "$GITHUB_REMOTE"
    git fetch --quiet origin --prune
    git fetch --quiet github --prune
    git reset --quiet --hard origin/main
else
    rm -rf "$SYNC_DIR"
    mkdir -p "$SYNC_DIR"
    git clone --quiet "$CODEBERG_REMOTE" "$SYNC_DIR"
    cd "$SYNC_DIR"
    git remote add github "$GITHUB_REMOTE"
    git fetch --quiet github --prune
fi

CODEBERG_HEAD=$(git rev-parse origin/main)
GITHUB_HEAD=$(git rev-parse github/main 2>/dev/null || echo "none")

if [ "$CODEBERG_HEAD" = "$GITHUB_HEAD" ]; then
    log "In sync (HEAD=$CODEBERG_HEAD)"
    exit 0
fi

AHEAD=$(git rev-list --count github/main..origin/main 2>/dev/null || echo "0")
BEHIND=$(git rev-list --count origin/main..github/main 2>/dev/null || echo "0")

if [ "$BEHIND" -gt 0 ]; then
    log "GitHub is ${BEHIND} commits AHEAD of Codeberg — that's a divergence bug. Force-pushing Codeberg state."
    git push --quiet --force github main
    git push --quiet --force github --tags
    log "Divergence fixed. GitHub reset to Codeberg HEAD=$CODEBERG_HEAD"
elif [ "$AHEAD" -gt 0 ]; then
    log "Pushing ${AHEAD} commits to GitHub"
    git push --quiet github main
    git push --quiet github --tags
    log "Sync complete (HEAD=$CODEBERG_HEAD)"
fi
