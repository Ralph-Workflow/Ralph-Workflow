#!/usr/bin/env python3
"""
Apollo Sequence Tactic — TERMINATED (autonomous kill, 2026-06-01)

Reason: Measurement window due today. The Apollo sequence was never launched.
Status has been 'not_launched' across every measurement sample since May 25.
Cloudflare auth blocks automation. No human unblock action taken in 7+ days.

This file replaces the active apollo_sequence_status.json with a terminal marker.
All Apollo-related cron jobs, scripts, and regeneration paths should treat
this as a dead tactic until a human explicitly re-enables it.

Kill date: 2026-06-01 13:50 CEST
Kill reason: 7-day window expired, zero launches, Cloudflare permanently blocked
Re-enable: Human must delete this terminal marker + set apollo_cookie
"""

from pathlib import Path
import json
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent.parent
STATUS_PATH = ROOT / 'agents' / 'marketing' / 'logs' / 'apollo_sequence_status_latest.json'
TERMINAL_PATH = ROOT / 'agents' / 'marketing' / 'logs' / 'apollo_tactic_terminated.json'

def main():
    now = datetime.now(timezone.utc)
    
    terminal = {
        "status": "terminated",
        "kill_date": now.isoformat(),
        "kill_reason": "Measurement window expired (June 1). Zero launches since May 25. Cloudflare permablock.",
        "prior_status": "not_launched",
        "re_enable_condition": "Human manually deletes this file + provides apollo_cookie authentication.",
        "affected_scripts": [
            "apollo_sequence_launcher.py",
            "apollo_sequence_status.py",
            "apollo_outbound_verifier.py",
            "apollo_monitor.py",
            "apollo_browserless_fix.py",
            "apollo_verify_live_list.py"
        ],
        "note": "Cron jobs for Apollo have already been removed from crontab. All 6 scripts are now dead code until re-enabled."
    }
    
    TERMINAL_PATH.write_text(json.dumps(terminal, indent=2))
    
    # Update the status file to reflect termination
    prior = {}
    if STATUS_PATH.exists():
        prior = json.loads(STATUS_PATH.read_text())
    
    STATUS_PATH.write_text(json.dumps({
        **prior,
        "status": "terminated",
        "killed_at": now.isoformat(),
        "kill_reason": "7-day window expired, zero launches, Cloudflare permablock",
    }, indent=2))
    
    print(f"[Apollo Kill] Tactic terminated. Terminal marker: {TERMINAL_PATH}")
    print(f"[Apollo Kill] Status updated: {STATUS_PATH}")
    print(f"[Apollo Kill] Re-enable: delete {TERMINAL_PATH} + provide apollo_cookie")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
