#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_ROOT="$(git rev-parse --show-toplevel)"

REMOTE_SERVER=$("$SCRIPT_DIR/probe.sh") || {
  echo "[remote-build] no build server reachable, running locally" >&2
  exec "$@"
}
export REMOTE_SERVER

_HASH_INPUT="${LOCAL_ROOT}$(hostname)${REMOTE_SERVER}"
if command -v sha256sum &>/dev/null; then
  _HASH=$(printf '%s' "$_HASH_INPUT" | sha256sum | cut -c1-16)
else
  _HASH=$(printf '%s' "$_HASH_INPUT" | shasum -a 256 | cut -c1-16)
fi
export REMOTE_ROOT="${REMOTE_ROOT:-/tmp/rw-${_HASH}}"

echo "[remote-build] syncing to ${REMOTE_SERVER}:${REMOTE_ROOT}..." >&2
"$SCRIPT_DIR/sync.sh"
ssh "$REMOTE_SERVER" "cd $REMOTE_ROOT && \
  git rev-parse --git-dir >/dev/null 2>&1 || \
  (git init -q && git config user.email build@remote && git config user.name Build); \
  git add -A -q 2>/dev/null; \
  git commit -q --allow-empty -m sync 2>/dev/null || true" \
  >/dev/null 2>&1
echo "[remote-build] running: $*" >&2
ssh -t "$REMOTE_SERVER" "cd $REMOTE_ROOT && $*"
