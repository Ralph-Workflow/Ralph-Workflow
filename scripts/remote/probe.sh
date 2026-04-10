#!/usr/bin/env bash
# Probe rw-build-server and rw-build-server-2; print the name of the selected
# server (lower 1-minute load average) to stdout and export it as REMOTE_SERVER.
# Exits 0 if any server is reachable, non-zero if both are unreachable.
#
# ConnectTimeout=5 caps the fallback latency to at most 5 seconds per server.
# Probes run sequentially here; parallel probing is handled by the Rust xtask
# implementation.  BatchMode=yes prevents interactive password prompts.
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
