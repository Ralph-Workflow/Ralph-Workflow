#!/usr/bin/env bash
# Main entry point: probe + sync + remote execution with local fallback.
# Accepts any cargo/xtask command as positional arguments.
#
# Remote directory: /tmp/rw-<sha256_16(pwd+hostname)>
# Lives in /tmp so the OS cleans it up automatically; no manual deletion needed.
# The hash makes the path unique per (local repo path, local machine) pair,
# so multiple machines or repos never collide on the same build server directory.
#
# Note: rw-build-server runs Debian Linux (x86_64). cargo xtask verify produces
# no binary artifacts that need copy-back, so the macOS/Linux difference is
# transparent for the verification use case.
#
# Note: SSH non-interactive sessions on Debian may have a stripped PATH.
# Ensure ~/.bashrc on rw-build-server sources ~/.cargo/env and adds
# ~/.bun/bin for non-interactive sessions (inside the `case $-` block).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_ROOT="$(git rev-parse --show-toplevel)"

# Compute a stable, unique remote directory based on SHA-256 of local path + hostname.
_HASH_INPUT="${LOCAL_ROOT}$(hostname)"
if command -v sha256sum &>/dev/null; then
  _HASH=$(printf '%s' "$_HASH_INPUT" | sha256sum | cut -c1-16)
else
  _HASH=$(printf '%s' "$_HASH_INPUT" | shasum -a 256 | cut -c1-16)
fi
export REMOTE_ROOT="${REMOTE_ROOT:-/tmp/rw-${_HASH}}"

if "$SCRIPT_DIR/probe.sh"; then
  echo "[remote-build] syncing to rw-build-server:${REMOTE_ROOT}..." >&2
  "$SCRIPT_DIR/sync.sh"
  # Initialize minimal git repo on remote (mirrors remote.rs ensure_remote_git_repo).
  # Tests using libgit2/git rev-parse need a valid repo; rsync excludes .git/.
  ssh rw-build-server "cd $REMOTE_ROOT && \
    git rev-parse --git-dir >/dev/null 2>&1 || \
    (git init -q && git config user.email build@remote && git config user.name Build); \
    git add -A -q 2>/dev/null; \
    git commit -q --allow-empty -m sync 2>/dev/null || true" \
    >/dev/null 2>&1
  echo "[remote-build] running: $*" >&2
  # -t allocates a pseudo-TTY to stream output in real time.
  ssh -t rw-build-server "cd $REMOTE_ROOT && $*"
else
  echo "[remote-build] rw-build-server unreachable, running locally" >&2
  exec "$@"
fi
