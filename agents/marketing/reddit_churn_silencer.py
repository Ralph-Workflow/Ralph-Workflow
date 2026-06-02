#!/usr/bin/env python3
"""
Silence the Reddit suspension health-check churn.

There were 2 monitor passes today (09:25 and 13:30) with zero recovery.
Running a health-check more than once per day while suspended is wasteful
— DDG isn't going to suddenly unblock between passes on the same day.

This writes a marker that reduces the health-check cadence to ONCE per 24h
while suspended. Normal 2x/day resumes only when suspension lifts.
"""

from pathlib import Path
import json
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent.parent
CHURN_MARKER = ROOT / 'agents' / 'marketing' / 'logs' / 'reddit_churn_silenced.json'
SUSPENSION_MARKER = ROOT / 'agents' / 'marketing' / 'logs' / 'reddit_monitor_suspension.json'

def count_todays_passes() -> int:
    """Count Reddit monitor passes for today."""
    monitor_dir = ROOT / 'seo-reports'
    today = datetime.now().strftime('%Y-%m-%d')
    count = len(list(monitor_dir.glob(f'reddit_monitor_{today}_*.md')))
    return count

def main():
    now = datetime.now(timezone.utc)
    passes_today = count_todays_passes()
    
    if not SUSPENSION_MARKER.exists():
        print("[Reddit Silence] Suspension not active. No churn to silence.")
        return 0
    
    churn = {
        "written_at": now.isoformat(),
        "active": True,
        "reason": f"Reddit suspension active, {passes_today} health-check passes today with zero recovery. Reducing to 1x/day while suspended.",
        "passes_today": passes_today,
        "recommended_cadence": "once_per_24h_while_suspended",
        "normal_cadence": "twice_daily_0800_2000",
        "re_enable_on": "Suspension marker deleted OR actual Reddit recovery detected",
        "rule": "When this marker exists, reddit_monitor.py should skip any pass within 24h of the last pass."
    }
    
    CHURN_MARKER.write_text(json.dumps(churn, indent=2))
    
    print(f"[Reddit Silence] {passes_today} passes today during active suspension.")
    print(f"[Reddit Silence] Cadence reduced to 1x/24h while churn marker active.")
    print(f"[Reddit Silence] Marker: {CHURN_MARKER}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
