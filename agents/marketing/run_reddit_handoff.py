#!/usr/bin/env python3
"""Run the reddit handoff bridge — generate ready-to-paste Reddit replies from latest monitor.
Thin wrapper that can be called directly from cron or run.py.
"""
import subprocess
import sys
from pathlib import Path

BRIDGE_PATH = Path(__file__).resolve().parent / "reddit_handoff_bridge.py"

result = subprocess.run(
    [sys.executable, str(BRIDGE_PATH)],
    capture_output=True, text=True, timeout=60,
)
print(result.stdout, flush=True)
if result.stderr:
    print(result.stderr, flush=True, file=sys.stderr)
sys.exit(result.returncode)
