#!/usr/bin/env bash
# Rsync the local working tree to rw-build-server.
#
# REMOTE_ROOT should be set by run.sh (hash-based /tmp path).
# If called standalone, computes the same hash so the path is consistent.
set -euo pipefail
LOCAL_ROOT="$(git rev-parse --show-toplevel)"

# If REMOTE_ROOT is not set (sync.sh called directly), compute from hash.
if [[ -z "${REMOTE_ROOT:-}" ]]; then
  _HASH_INPUT="${LOCAL_ROOT}$(hostname)"
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
  "rw-build-server:$REMOTE_ROOT/"
