#!/usr/bin/env sh
# Shell wrapper for the deterministic AGY v1.1.8 simulator.
# RALPH_AGY_BINARY can point directly at this file.
#
# All MOCK_AGY_* and RALPH_MCP_* environment variables pass straight
# through to the simulator (including MOCK_AGY_V1_1_13 and
# RALPH_MCP_ENDPOINT, which drive the v1.1.13 vocabulary and the real
# MCP round trips; see tests/_support/mock_agy.py's module docstring).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$SCRIPT_DIR/../..:${PYTHONPATH:-}"
export PYTHONPATH
exec python3 -m tests._support.mock_agy "$@"
