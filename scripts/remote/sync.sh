#!/usr/bin/env bash
# Rsync the local working tree to the selected build server.
#
# REMOTE_SERVER and REMOTE_ROOT should be set by run.sh.
# If called standalone, computes the same hash so the path is consistent.
set -euo pipefail
LOCAL_ROOT="$(git rev-parse --show-toplevel)"

# Determine which server to sync to.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "${REMOTE_SERVER:-}" ]]; then
  REMOTE_SERVER=$("$SCRIPT_DIR/probe.sh") || {
    echo "[remote-build] no build server reachable" >&2
    exit 1
  }
  export REMOTE_SERVER
fi

# If REMOTE_ROOT is not set (sync.sh called directly), compute from hash.
if [[ -z "${REMOTE_ROOT:-}" ]]; then
  _HASH_INPUT="${LOCAL_ROOT}$(hostname)${REMOTE_SERVER}"
  if command -v sha256sum &>/dev/null; then
    _HASH=$(printf '%s' "$_HASH_INPUT" | sha256sum | cut -c1-16)
  else
    _HASH=$(printf '%s' "$_HASH_INPUT" | shasum -a 256 | cut -c1-16)
  fi
  REMOTE_ROOT="/tmp/rw-${_HASH}"
fi

# --delete removes files on the remote that have been deleted locally, keeping the trees in sync.
# --exclude=.git/ is explicit because .git/ is not in .gitignore (git never tracks itself).
# --filter=':- .gitignore' handles all other exclusions using the same per-directory semantics as git.
# REMOTE_ROOT is in /tmp/ so no manual cleanup is needed.
rsync -az --delete \
  --exclude=.git/ \
  --filter=':- .gitignore' \
  "$LOCAL_ROOT/" \
  "${REMOTE_SERVER:?REMOTE_SERVER not set}:$REMOTE_ROOT/"
