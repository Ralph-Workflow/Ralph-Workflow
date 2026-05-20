#!/bin/bash
# sync_to_github.sh — Push Codeberg repo to GitHub mirror
# Run via cron every 30 minutes
#
# Uses a dedicated sync clone in /tmp/ralph-github-sync so it never
# touches the editable checkout at repos/Ralph-Workflow/github-mirror.

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

log "Starting Codeberg -> GitHub sync"

# Use a dedicated sync-only clone — never touch the editable checkout
if [ -d "$SYNC_DIR/.git" ]; then
    log "Updating existing sync clone"
    cd "$SYNC_DIR"
    git remote set-url origin "$CODEBERG_REMOTE"
    git remote set-url github "$GITHUB_REMOTE" 2>/dev/null || git remote add github "$GITHUB_REMOTE"
    git fetch --quiet origin
    git fetch --quiet github
    git reset --quiet --hard origin/main
else
    log "Creating dedicated sync clone in $SYNC_DIR"
    rm -rf "$SYNC_DIR"
    mkdir -p "$SYNC_DIR"
    git clone --quiet "$CODEBERG_REMOTE" "$SYNC_DIR"
    cd "$SYNC_DIR"
    git remote add github "$GITHUB_REMOTE"
    git fetch --quiet github
fi

# Compare Codeberg (origin/main) vs GitHub (github/main)
CODEBERG_HEAD=$(git rev-parse origin/main)
GITHUB_HEAD=$(git rev-parse github/main)

if [ "$CODEBERG_HEAD" = "$GITHUB_HEAD" ]; then
    log "Already up to date (HEAD=$CODEBERG_HEAD)"
    exit 0
fi

AHEAD=$(git log --oneline github/main..origin/main 2>/dev/null | wc -l | tr -d ' ')
BEHIND=$(git log --oneline origin/main..github/main 2>/dev/null | wc -l | tr -d ' ')

log "Codeberg is ${AHEAD} commits ahead of GitHub, GitHub is ${BEHIND} commits ahead"

if [ "$AHEAD" -gt 0 ] && [ "$BEHIND" -eq 0 ]; then
    log "Pushing $AHEAD new commits to GitHub (HEAD=$CODEBERG_HEAD)"
    git push --quiet github main
    log "Sync complete"
elif [ "$BEHIND" -gt 0 ] && [ "$AHEAD" -eq 0 ]; then
    log "WARNING: GitHub is ${BEHIND} commits ahead of Codeberg — force-pushing Codeberg state"
    git push --quiet --force github main
    log "Force sync complete"
else
    log "WARNING: Histories diverged. Codeberg ${AHEAD} ahead, GitHub ${BEHIND} ahead — force-pushing Codeberg"
    git push --quiet --force github main
    log "Force sync complete"
fi
