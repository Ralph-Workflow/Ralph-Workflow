#!/usr/bin/env python3
"""Material-change gate for marketing crons.

Catastrophic churn death spiral: the marketing-active-loop cron can fire 50+
times/day producing identical guard-pause results. Each run reads 10 context
files, computes the same fingerprint, hits the same blocked lanes, and exits.
This is a structural failure that wastes compute and produces zero outcomes.

Fix: a fast pre-check that hashes the execution environment (earlier log
fingerprints + lane state) and skips the run if nothing has materially
changed since the last successful gate check.

Design:
- Gate state stored in agents/marketing/logs/material_change_gate.json
- On each run: compute aggregate fingerprint of the latest logs
- If fingerprint == last successful run's fingerprint → SKIP (exit 2)
- If fingerprint changed → allow run (exit 0)
- Cooldown: always allow at least one run in any 4-hour window

Exit codes:
  0 → material change detected, proceed with full run
  1 → gate error (fail-open: proceed anyway)
  2 → no material change, skip run
  3 → within cooldown grace period, skip but don't update fingerprint
"""

from __future__ import annotations

import json, hashlib, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace")))
LOG_DIR = WORKSPACE / "agents" / "marketing" / "logs"
GATE_FILE = LOG_DIR / "material_change_gate.json"

# Minimum seconds between runs (4 hours = 14400s)
# Under normal conditions this means max ~6 marketing runs/day instead of 48+
MIN_RUN_INTERVAL_S = 4 * 3600

# Files that count as "material change" when their content changes
MATERIAL_FILES = [
    "distribution_lane_latest.json",
    "market_intelligence_latest.json",
    "marketing_workflow_audit_latest.json",
    "backlink_status_latest.json",
]


def compute_fingerprint() -> str:
    """Hash the latest marketing log files to detect material changes."""
    hasher = hashlib.sha256()
    for fname in sorted(MATERIAL_FILES):
        fp = LOG_DIR / fname
        if fp.exists():
            hasher.update(fname.encode())
            hasher.update(fp.read_bytes())
    return hasher.hexdigest()


def load_gate() -> dict:
    if GATE_FILE.exists():
        try:
            return json.loads(GATE_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return {"last_fingerprint": None, "last_run_ts": None, "total_skips": 0, "total_runs": 0}


def save_gate(gate: dict) -> None:
    GATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    GATE_FILE.write_text(json.dumps(gate, indent=2, default=str) + "\n")


def should_run() -> tuple[bool, str]:
    """Returns (should_run, reason)."""
    gate = load_gate()
    now = datetime.now(timezone.utc)
    current_fp = compute_fingerprint()

    gate["total_checks"] = gate.get("total_checks", 0) + 1
    gate["last_check_ts"] = now.isoformat()

    # If we've never run successfully, allow it
    if gate["last_fingerprint"] is None:
        gate["last_fingerprint"] = current_fp
        gate["last_run_ts"] = now.isoformat()
        gate["total_runs"] = gate.get("total_runs", 0) + 1
        save_gate(gate)
        return True, "first_run"

    # Cooldown grace: always allow at least one run in any MIN_RUN_INTERVAL_S window
    if gate["last_run_ts"]:
        try:
            last_run = datetime.fromisoformat(gate["last_run_ts"])
            elapsed = (now - last_run).total_seconds()
            if elapsed >= MIN_RUN_INTERVAL_S:
                # Enough time passed — always allow regardless of fingerprint
                gate["last_fingerprint"] = current_fp
                gate["last_run_ts"] = now.isoformat()
                gate["total_runs"] = gate.get("total_runs", 0) + 1
                save_gate(gate)
                return True, f"cooldown_elapsed ({elapsed:.0f}s)"
        except (ValueError, TypeError):
            pass

    # Fingerprint check: has anything materially changed?
    if current_fp == gate["last_fingerprint"]:
        gate["total_skips"] = gate.get("total_skips", 0) + 1
        save_gate(gate)
        return False, f"fingerprint_unchanged (skips: {gate['total_skips']})"

    # Material change detected
    gate["last_fingerprint"] = current_fp
    gate["last_run_ts"] = now.isoformat()
    gate["total_runs"] = gate.get("total_runs", 0) + 1
    save_gate(gate)
    return True, "material_change"


if __name__ == "__main__":
    ok, reason = should_run()
    gate = load_gate()
    stats = f"runs={gate.get('total_runs',0)} skips={gate.get('total_skips',0)} checks={gate.get('total_checks',0)}"
    if ok:
        print(f"✅ PROCEED: {reason} ({stats})")
        sys.exit(0)
    else:
        print(f"⏭️ SKIP: {reason} ({stats})")
        sys.exit(2)
