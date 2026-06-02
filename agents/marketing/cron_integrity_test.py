#!/usr/bin/env python3
"""
Crontab Integrity Check — catches crontab wipes, silent job loss, and drift from golden copy.

CRITICAL: If the marketing crontab is silently wiped or truncated, the entire autonomous
system goes blind with zero autonomous distribution. This script verifies the live crontab
against the golden reference copy and alerts on drift.

Planned cron: 15 8 * * * (before watchdog at 08:45)
"""

from __future__ import annotations
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent.parent
assert (ROOT / 'agents' / 'marketing').is_dir(), f"Bad ROOT: {ROOT}"
LOG_PATH = ROOT / 'agents' / 'marketing' / 'logs' / 'cron_integrity_latest.json'
GOLDEN_CRONTAB = ROOT / 'agents' / 'marketing' / 'crontab.txt'
MIN_MARKETING_JOBS = 8  # below this, system is degraded

def _live_crontab_lines() -> list[str]:
    result = subprocess.run(
        ['crontab', '-l'],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        return []
    return [l.strip() for l in result.stdout.split('\n') if l.strip() and not l.strip().startswith('#')]

def _golden_job_count() -> int:
    if not GOLDEN_CRONTAB.exists():
        return -1
    lines = [l.strip() for l in GOLDEN_CRONTAB.read_text().split('\n')
             if l.strip() and not l.strip().startswith('#')]
    return len(lines)

def _marketing_jobs_in(crontab_lines: list[str]) -> list[str]:
    """Extract marketing-specific cron lines (excluding infra like git-sync)."""
    return [l for l in crontab_lines
            if 'marketing' in l and 'stale_artifact_watchdog' not in l]

def main() -> int:
    now = datetime.now(timezone.utc)
    lines = _live_crontab_lines()
    golden_count = _golden_job_count()
    marketing_count = len(_marketing_jobs_in(lines))
    total_lines = len(lines)
    
    issues = []
    
    if total_lines < MIN_MARKETING_JOBS:
        issues.append(f"CATASTROPHIC: Only {total_lines} cron lines found (min {MIN_MARKETING_JOBS}). Crontab may be partially wiped.")
    
    if marketing_count < 6:
        issues.append(f"DEGRADED: Only {marketing_count} marketing cron jobs (expected >= 6).")
    
    if golden_count > 0 and total_lines < golden_count * 0.7:
        issues.append(f"MISMATCH: Live crontab has {total_lines} jobs vs golden {golden_count}.")
    
    result = {
        "generated_at": now.isoformat(),
        "live_total_lines": total_lines,
        "marketing_job_count": marketing_count,
        "golden_job_count": golden_count,
        "min_marketing_jobs": MIN_MARKETING_JOBS,
        "issues": issues,
        "ok": len(issues) == 0,
        "crontab_snapshot": lines,
    }
    
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(result, indent=2))
    
    if issues:
        print(f"[CRON INTEGRITY] FAIL — {len(issues)} issues:")
        for i in issues:
            print(f"  {i}")
        # Write a canary file that watchdog can detect
        canary = LOG_PATH.parent / 'cron_integrity_alert.json'
        canary.write_text(json.dumps({"alert": True, "at": now.isoformat(), "issues": issues}, indent=2))
        return 1
    
    print(f"[CRON INTEGRITY] OK — {total_lines} cron lines, {marketing_count} marketing jobs, golden={golden_count}")
    # Clear any stale canary
    canary = LOG_PATH.parent / 'cron_integrity_alert.json'
    if canary.exists():
        canary.unlink()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
