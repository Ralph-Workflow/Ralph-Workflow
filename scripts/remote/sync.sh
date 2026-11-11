#!/usr/bin/env bash
set -euo pipefail
LOCAL_ROOT="$(git rev-parse --show-toplevel)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "${REMOTE_SERVER:-}" ]]; then
  REMOTE_SERVER=$("$SCRIPT_DIR/probe.sh") || {
    echo "[remote-build] no build server reachable" >&2
    exit 1
  }
  export REMOTE_SERVER
fi

if [[ -z "${REMOTE_ROOT:-}" ]]; then
  _HASH_INPUT="${LOCAL_ROOT}$(hostname)${REMOTE_SERVER}"
  if command -v sha256sum &>/dev/null; then
    _HASH=$(printf '%s' "$_HASH_INPUT" | sha256sum | cut -c1-16)
  else
    _HASH=$(printf '%s' "$_HASH_INPUT" | shasum -a 256 | cut -c1-16)
  fi
  REMOTE_ROOT="/tmp/rw-${_HASH}"
fi

rsync -az --delete \
  --exclude=.git/ \
  --filter=':- .gitignore' \
  "$LOCAL_ROOT/" \
  "${REMOTE_SERVER:?REMOTE_SERVER not set}:$REMOTE_ROOT/"
