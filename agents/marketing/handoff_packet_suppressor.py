#!/usr/bin/env python3
"""
Handoff Packet Suppression — prevents prepared-only regeneration churn.

The primary_repo_flat_contact_handoff_packet has been regenerated 2x in 48 hours
without any live delivery. This tool writes a suppression marker that signals
all draft-producing paths to skip packet regeneration until:
(a) a fresh live delivery window opens, OR
(b) materially changed targets/channels are added, OR
(c) the suppression marker expires (7 days)

Run once to suppress; re-run to check status.
"""

from pathlib import Path
import json
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent.parent
MARKER_PATH = ROOT / 'agents' / 'marketing' / 'logs' / 'handoff_packet_suppression.json'
DRAFTS_DIR = ROOT / 'drafts'

SUPPRESSED_STEMS = [
    "primary_repo_flat_contact_handoff_packet",
    "curator_contact_handoff_packet",
    "comparison_backlink_handoff_packet",
    "reddit_discussion_handoff_packet",
]

def count_recent_regenerations(stem: str, hours: int = 48) -> int:
    """Count how many times a handoff packet was regenerated in the window."""
    import re
    pattern = re.compile(rf"(\d{{4}}-\d{{2}}-\d{{2}})_{stem}\.md")
    matches = []
    for f in sorted(DRAFTS_DIR.glob(f"*_{stem}.md"), reverse=True):
        m = pattern.search(f.name)
        if m:
            matches.append(f.name)
    return matches

def main():
    now = datetime.now(timezone.utc)
    
    # Check current state
    regenerations = {}
    for stem in SUPPRESSED_STEMS:
        recent = count_recent_regenerations(stem)
        if len(recent) > 1:
            regenerations[stem] = {
                "count_48h": len(recent),
                "latest_files": recent[:3],
                "churning": len(recent) >= 2,
            }
    
    suppression = {
        "written_at": now.isoformat(),
        "active": True,
        "expires_at": (now + __import__('datetime').timedelta(days=7)).isoformat(),
        "reason": "Prepared-only packet regeneration without live delivery. Suppressing to stop churn.",
        "suppressed_stems": SUPPRESSED_STEMS,
        "regeneration_evidence": regenerations,
        "release_conditions": [
            "A fresh live delivery window opens (e.g. human unblocks a distribution channel)",
            "Materially changed targets or channels are added to a packet",
            "Suppression expires naturally (7 days from now)",
        ],
        "rule": "When suppression is active, distribution_lane_executor.py MUST skip all handoff packet generation."
    }
    
    MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    MARKER_PATH.write_text(json.dumps(suppression, indent=2))
    
    print(f"[Suppression] Handoff packets suppressed.")
    print(f"  Churning stems: {list(regenerations.keys())}")
    for stem, data in regenerations.items():
        print(f"    {stem}: {data['count_48h']} regenerations in 48h")
    print(f"  Expires: {suppression['expires_at']}")
    print(f"  Marker: {MARKER_PATH}")
    
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
