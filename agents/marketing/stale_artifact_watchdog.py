#!/usr/bin/env python3
"""Stale-artifact watchdog — prevents agents opening with outdated intelligence.

Reconstructed 2026-06-10 after the cleanup commit (7d285cb9) deleted the
original (which was tangled with distribution_lane_executor state). Cron
runs this daily at 08:45, before blocker_truth_check (08:50) and run.py
(09:00). Three-strike rule from AGENTS.md: any failure that recurs 3 times
is an escalation, no matter how minor — that's exactly the bug this is here
to prevent (the same agents opening 7-day-old files under today's date).

Minimal scope:
- Scan logs/*_latest.* and drafts/*_latest.* for mtime > 48h (stale for a
  daily-cadence pointer) or > 7d (almost certainly orphaned).
- Scan current-dated artifacts (filename YYYY-MM-DD_*) older than 48h.
- Write a status JSON to logs/stale_artifact_watchdog_latest.json.
- Exit 0 always; the cron wrapper's caller decides what to do with findings.

Does NOT touch the source content (no auto-clean — that would be theater).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
MARKETING = ROOT / "agents/marketing"
LOGS = MARKETING / "logs"
DRAFTS = ROOT / "drafts"
STATE_PATH = LOGS / "stale_artifact_watchdog_latest.json"

STALE_HOURS = 48          # > 48h = "stale for a daily-cadence pointer"
VERY_STALE_HOURS = 168    # > 7d  = "almost certainly orphaned, not just delayed"
MAX_AGE_HOURS = 30 * 24   # ignore anything older (genuine archive)


def _age_h(p: Path) -> float:
    return (datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)).total_seconds() / 3600


def _severity(age_h: float) -> str:
    if age_h >= VERY_STALE_HOURS:
        return "very_stale"
    if age_h >= STALE_HOURS:
        return "stale"
    return "ok"


def _scan_latest_pointers() -> list[dict]:
    """Check every *_latest.* under logs/ and drafts/."""
    findings: list[dict] = []
    for base in (LOGS, DRAFTS):
        if not base.exists():
            continue
        for p in base.glob("*_latest.*"):
            try:
                age = _age_h(p)
            except OSError:
                continue
            if age > MAX_AGE_HOURS or age <= STALE_HOURS:
                continue
            findings.append({
                "kind": "stale_latest_pointer",
                "severity": _severity(age),
                "path": str(p.relative_to(ROOT)),
                "age_h": round(age, 1),
            })
    return findings


def _scan_dated_artifacts() -> list[dict]:
    """Check current-date marketing artifacts (YYYY-MM-DD_*) older than 48h."""
    findings: list[dict] = []
    if not LOGS.exists():
        return findings
    today = datetime.now().strftime("%Y-%m-")
    for p in LOGS.glob(f"{today}*"):
        try:
            age = _age_h(p)
        except OSError:
            continue
        if age <= STALE_HOURS or age > MAX_AGE_HOURS:
            continue
        findings.append({
            "kind": "stale_dated_artifact",
            "severity": _severity(age),
            "path": str(p.relative_to(ROOT)),
            "age_h": round(age, 1),
        })
    return findings


def main() -> int:
    now = datetime.now()
    findings = _scan_latest_pointers() + _scan_dated_artifacts()
    very_stale = sum(1 for f in findings if f["severity"] == "very_stale")
    stale = sum(1 for f in findings if f["severity"] == "stale")

    status = {
        "run_at": now.isoformat(),
        "stale_threshold_h": STALE_HOURS,
        "very_stale_threshold_h": VERY_STALE_HOURS,
        "finding_count": len(findings),
        "stale_count": stale,
        "very_stale_count": very_stale,
        "findings": findings,
        "ok": len(findings) == 0,
    }
    STATE_PATH.write_text(json.dumps(status, indent=2))

    if findings:
        print(f"⚠️ stale-artifact-watchdog: {stale} stale, {very_stale} very_stale "
              f"(threshold {STALE_HOURS}h / {VERY_STALE_HOURS}h)")
        # Show the most concerning first
        for f in sorted(findings, key=lambda x: -x["age_h"])[:5]:
            print(f"  - {f['severity']:11s} {f['kind']}: {f['path']} ({f['age_h']}h)")
        if len(findings) > 5:
            print(f"  ... and {len(findings) - 5} more (see {STATE_PATH})")
    else:
        print(f"stale-artifact-watchdog: all current (threshold {STALE_HOURS}h)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
