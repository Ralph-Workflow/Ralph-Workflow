#!/usr/bin/env sh
# Shell wrapper for the deterministic AGY v1.0.8 simulator.
# RALPH_AGY_BINARY can point directly at this file.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$SCRIPT_DIR/../..:${PYTHONPATH:-}"
export PYTHONPATH
exec python3 -m tests._support.mock_agy "$@"
