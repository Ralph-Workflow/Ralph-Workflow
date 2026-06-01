#!/usr/bin/env python3
"""
Backlink stagnation watchdog — detects stalled directory submissions and
escalates when submissions have been in "pending editorial review" state
for >7 days without a live listing.

Triggered weekly (Sunday) by cron. Auto-finds new directories to submit
to when existing ones stagnate.

Output: agents/marketing/logs/backlink_stagnation_latest.json

Repair policy:
- Pending >7 days → flag as STALE, mark for re-submission or replacement
- Pending >14 days → mark as DEAD, trigger new-directory discovery
- All submissions dead from a batch → escalate to evaluator for fresh outreach
"""

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "agents" / "marketing" / "logs"
OUT_PATH = LOG_DIR / "backlink_stagnation_latest.json"
ESCALATION_PATH = LOG_DIR / "backlink_stagnation_escalation.json"
BACKLINK_STATUS_PATH = LOG_DIR / "backlink_status_latest.json"

STALE_DAYS = 7
DEAD_DAYS = 14
MAX_DIRECTORIES_TO_DISCOVER = 5

# Known AI tool directories for fresh discovery when stagnation hits
SEED_DIRECTORIES = [
    {
        "name": "AITopTools",
        "submit_url": "https://aitoptools.com/submit/",
        "listing_pattern": "https://aitoptools.com/tool/ralph-workflow/",
    },
    {
        "name": "FutureTools",
        "submit_url": "https://www.futuretools.io/submit",
        "listing_pattern": "https://www.futuretools.io/tools/ralph-workflow",
    },
    {
        "name": "Futurepedia",
        "submit_url": "https://www.futurepedia.io/submit-tool",
        "listing_pattern": "https://www.futurepedia.io/tool/ralph-workflow",
    },
    {
        "name": "There's an AI for That",
        "submit_url": "https://theresanaiforthat.com/submit/",
        "listing_pattern": "https://theresanaiforthat.com/ai/ralph-workflow/",
    },
    {
        "name": "TopAI.tools",
        "submit_url": "https://topai.tools/submit",
        "listing_pattern": "https://topai.tools/tool/ralph-workflow",
    },
    {
        "name": "AI Parabellum",
        "submit_url": "https://aiparabellum.com/submit/",
        "listing_pattern": "https://aiparabellum.com/tool/ralph-workflow/",
    },
    {
        "name": "Aixploria",
        "submit_url": "https://www.aixploria.com/en/submit-your-ai/",
        "listing_pattern": "https://www.aixploria.com/en/ralph-workflow/",
    },
    {
        "name": "Insidr.ai",
        "submit_url": "https://www.insidr.ai/submit-tool/",
        "listing_pattern": "https://www.insidr.ai/tools/ralph-workflow/",
    },
    {
        "name": "AI Tool Guru",
        "submit_url": "https://aitoolguru.com/submit",
        "listing_pattern": "https://aitoolguru.com/tool/ralph-workflow",
    },
    {
        "name": "DevHunt",
        "submit_url": "https://devhunt.org/submit",
        "listing_pattern": "https://devhunt.org/tools/ralph-workflow",
    },
]


def _parse_date(date_str: str) -> Optional[datetime]:
    """Parse ISO-format datetime string."""
    try:
        return datetime.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None


def _load_backlink_status() -> dict:
    if BACKLINK_STATUS_PATH.exists():
        try:
            return json.loads(BACKLINK_STATUS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _check_url_live(url: str) -> tuple[bool, Optional[str]]:
    """Check if a URL returns 200 and contains product markers."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "RalphWorkflow-BacklinkWatchdog/1.0",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            body = resp.read().decode("utf-8", errors="replace").lower()
            if "ralph" in body or "ralph-workflow" in body:
                return True, "product markers found"
            return True, "page live but no product markers"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)[:100]


def _find_new_directory_candidates() -> list[dict]:
    """Return pre-vetted new directories to submit to when stagnation occurs."""
    now = datetime.now(timezone.utc)
    candidates = []
    for d in SEED_DIRECTORIES:
        listing_url = d["listing_pattern"]
        live, note = _check_url_live(listing_url)
        if not live:
            candidates.append(
                {
                    "name": d["name"],
                    "submit_url": d["submit_url"],
                    "listing_url": listing_url,
                    "status": "unsubmitted",
                    "listing_live": False,
                }
            )
        else:
            candidates.append(
                {
                    "name": d["name"],
                    "submit_url": d["submit_url"],
                    "listing_url": listing_url,
                    "status": "already_listed",
                    "listing_live": True,
                }
            )
        time.sleep(0.5)  # rate-limit
    return candidates


def _classify_submissions(directories: dict, generated_at: str) -> dict:
    """Classify each directory submission by staleness."""
    now = datetime.now(timezone.utc)
    generated = _parse_date(generated_at) or now
    age_hours = (now - generated).total_seconds() / 3600

    classifications = {"live": [], "stale": [], "dead": [], "pending_normal": []}

    for name, data in directories.items():
        listing_live = data.get("listing_live", False)
        status_note = data.get("status_note", "")

        if listing_live:
            classifications["live"].append({**data, "name": name})
            continue

        # Determine age based on status_note date hints
        # Most notes follow pattern "Submitted 2026-05-23"
        submitted_match = re.search(r"(\d{4}-\d{2}-\d{2})", status_note)
        if not submitted_match:
            submitted_match = re.search(r"(\d{4}-\d{2}-\d{2})", str(data.get("check_results", [])))
        if submitted_match:
            submitted_date = _parse_date(submitted_match.group(1))
            if submitted_date:
                days_pending = (now.date() - submitted_date.date()).days
            else:
                days_pending = 0
        else:
            days_pending = 0

        entry = {**data, "name": name, "days_pending": days_pending}

        if days_pending >= DEAD_DAYS:
            classifications["dead"].append(entry)
        elif days_pending >= STALE_DAYS:
            classifications["stale"].append(entry)
        else:
            classifications["pending_normal"].append(entry)

    return classifications


def main():
    now = datetime.now(timezone.utc)
    now_str = now.isoformat()

    status = _load_backlink_status()
    directories = status.get("directories", {})
    generated_at = status.get("generated_at", now_str)

    classifications = _classify_submissions(directories, generated_at)

    live_count = len(classifications["live"])
    stale_count = len(classifications["stale"])
    dead_count = len(classifications["dead"])
    pending_count = len(classifications["pending_normal"])
    total = live_count + stale_count + dead_count + pending_count

    # Escalation logic
    should_escalate = stale_count + dead_count > 0
    fresh_candidates = []
    if should_escalate:
        fresh_candidates = _find_new_directory_candidates()

    output = {
        "generated_at": now_str,
        "source": "backlink_stagnation_watchdog.py",
        "backlink_status_at": generated_at,
        "summary": {
            "total_directories": total,
            "live": live_count,
            "stale_gt_7_days": stale_count,
            "dead_gt_14_days": dead_count,
            "pending_normal": pending_count,
            "should_escalate": should_escalate,
        },
        "stale_submissions": classifications["stale"],
        "dead_submissions": classifications["dead"],
        "live_listings": [
            {"name": d["name"], "url": d.get("listing_url", "")}
            for d in classifications["live"]
        ],
        "fresh_directory_candidates": fresh_candidates[:MAX_DIRECTORIES_TO_DISCOVER],
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(output, indent=2) + "\n")

    if should_escalate:
        escalation = {
            "escalated_at": now_str,
            "reason": f"{stale_count} stale + {dead_count} dead submissions out of {total} total directories",
            "stale_submissions": [
                {"name": s["name"], "days_pending": s["days_pending"]}
                for s in classifications["stale"]
            ],
            "dead_submissions": [
                {"name": s["name"], "days_pending": s["days_pending"]}
                for s in classifications["dead"]
            ],
            "recommended_action": "Post fresh batch of directory submissions from fresh_directory_candidates",
            "fresh_candidates_count": len(fresh_candidates),
            "fresh_unsubmitted": [
                c
                for c in fresh_candidates
                if c["status"] == "unsubmitted"
            ],
        }
        ESCALATION_PATH.write_text(json.dumps(escalation, indent=2) + "\n")
        print(
            f"ESCALATION: {stale_count} stale + {dead_count} dead. "
            f"{len([c for c in fresh_candidates if c['status'] == 'unsubmitted'])} "
            f"fresh candidates ready."
        )
    else:
        if ESCALATION_PATH.exists():
            ESCALATION_PATH.unlink()
        print(f"OK: {live_count} live, {pending_count} pending, 0 stale/dead")

    return 0


if __name__ == "__main__":
    sys.exit(main())
