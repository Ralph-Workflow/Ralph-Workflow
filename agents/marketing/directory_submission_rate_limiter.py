#!/usr/bin/env python3
"""
Directory submission rate limiter — enforces max 3 new submissions per rolling 7-day window.

A submission only counts if it returned HTTP 200/success with a provider-accepted response.
Prepared-only packets, internal drafts, and failed POSTs do NOT count against the cap.

Usage: python3 directory_submission_rate_limiter.py          # check current state
       python3 directory_submission_rate_limiter.py --allow   # returns 0 if room, 1 if capped
       python3 directory_submission_rate_limiter.py --record URL TYPE  # record a new submission
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
LEDGER = ROOT / "agents/marketing/logs/directory_submission_ledger.json"
MAX_SUBMISSIONS = 3
WINDOW_DAYS = 7


def _load_ledger() -> list[dict]:
    if LEDGER.exists():
        try:
            with open(LEDGER) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_ledger(ledger: list[dict]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "w") as f:
        json.dump(ledger, f, indent=2, default=str)


def _window_start() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)


def count_recent(ledger: list[dict] | None = None) -> int:
    """Count submissions within the rolling 7-day window."""
    if ledger is None:
        ledger = _load_ledger()
    cutoff = _window_start()
    count = 0
    for entry in ledger:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts >= cutoff:
                count += 1
        except (KeyError, ValueError):
            continue
    return count


def is_capped(ledger: list[dict] | None = None) -> bool:
    """Returns True if the rolling window is at or above the cap."""
    return count_recent(ledger) >= MAX_SUBMISSIONS


def record_submission(url: str, submission_type: str) -> None:
    """Record a new verified submission."""
    ledger = _load_ledger()
    ledger.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "url": url,
        "type": submission_type,
    })
    _save_ledger(ledger)


def main() -> int:
    if "--record" in sys.argv:
        try:
            idx = sys.argv.index("--record")
            url = sys.argv[idx + 1]
            sub_type = sys.argv[idx + 2] if len(sys.argv) > idx + 2 else "unknown"
        except (IndexError, ValueError):
            print("Usage: directory_submission_rate_limiter.py --record URL TYPE", file=sys.stderr)
            return 2
        record_submission(url, sub_type)
        recent = count_recent()
        print(f"Recorded: {url} ({sub_type})")
        print(f"Rolling window count: {recent}/{MAX_SUBMISSIONS}")
        return 0

    if "--allow" in sys.argv:
        capped = is_capped()
        recent = count_recent()
        print(f"Rolling window: {recent}/{MAX_SUBMISSIONS} — {'CAPPED' if capped else 'ALLOWED'}")
        return 1 if capped else 0

    # Default: status check
    ledger = _load_ledger()
    recent = count_recent(ledger)
    capped = recent >= MAX_SUBMISSIONS
    cutoff = _window_start()
    window_submissions = [e for e in ledger if datetime.fromisoformat(e["timestamp"]) >= cutoff]
    print(json.dumps({
        "rolling_window_days": WINDOW_DAYS,
        "submission_cap": MAX_SUBMISSIONS,
        "recent_count": recent,
        "capped": capped,
        "window_submissions": window_submissions,
        "remaining": max(0, MAX_SUBMISSIONS - recent),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
