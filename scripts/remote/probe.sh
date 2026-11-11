#!/usr/bin/env bash
set -euo pipefail

SERVERS=("rw-build-server" "rw-build-server-2")
declare -A LOADS

for server in "${SERVERS[@]}"; do
  load=$(ssh -o ConnectTimeout=5 -o BatchMode=yes "$server" \
    "awk '{print \$1}' /proc/loadavg" 2>/dev/null) || true
  if [[ -n "$load" ]]; then
    LOADS["$server"]="$load"
  fi
done

if [[ ${#LOADS[@]} -eq 0 ]]; then
  echo "[remote-build] no build server reachable" >&2
  exit 1
fi

best=""
best_load=""
for server in "${!LOADS[@]}"; do
  load="${LOADS[$server]}"
  if [[ -z "$best" ]] || awk "BEGIN { exit !($load < $best_load) }"; then
    best="$server"
    best_load="$load"
  fi
done

export REMOTE_SERVER="$best"
echo "$best"
