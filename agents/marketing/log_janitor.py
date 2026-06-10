#!/usr/bin/env python3
"""Log janitor — prevents marketing log inflation.

Reconstructed 2026-06-10 after the cleanup commit (7d285cb9) deleted the
original alongside the rest of the distribution-lane machinery. The crontab
still calls this on Sunday 04:00, so an absent file means silent weekly cron
failure (which the fleet monitor sees as "OK" only because the log file
itself is not being touched). Keep this minimal: archive old JSON logs,
write a counts summary, no structural-alert doc theater.

Behavior (runs weekly on Sunday):
- Move logs/*.json older than 14 days into logs/archive/YYYY-MM/
- Write logs/log_counts_summary.json with the recent 7-day breakdown
- Print a short report to stdout (captured by log_janitor_cron.log)

Exits 0 always. Idempotent (skips files already in archive).
"""
from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
LOG_DIR = ROOT / "agents/marketing/logs"
ARCHIVE_BASE = LOG_DIR / "archive"
SUMMARY_PATH = LOG_DIR / "log_counts_summary.json"

ARCHIVE_AGE_DAYS = 14
SUMMARY_WINDOW_DAYS = 7

# Hold/noop categories that count as "non-outcome" logs.
HOLD_PATTERNS = (
    "measurement_hold",
    "guard_pause",
    "guard_follow",
    "confirmation_follow_through",
    "outcome_capability",
)


def categorize(name: str) -> str:
    n = name.lower()
    for pat in HOLD_PATTERNS:
        if pat in n:
            return pat
    if "execution_board" in n:
        return "execution_board"
    if "adoption" in n:
        return "adoption"
    if "market_intelligence" in n:
        return "market_intelligence"
    if "pypi" in n:
        return "pypi"
    if "publisher" in n:
        return "publisher"
    if "telegraph" in n or "posting" in n:
        return "telegraph"
    if "reddit" in n:
        return "reddit"
    if "distribution" in n:
        return "distribution"
    return "other"


def archive_old_logs() -> int:
    cutoff = datetime.now() - timedelta(days=ARCHIVE_AGE_DAYS)
    moved = 0
    for f in LOG_DIR.glob("*.json"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
        except OSError:
            continue
        if mtime >= cutoff:
            continue
        month_key = mtime.strftime("%Y-%m")
        dest_dir = ARCHIVE_BASE / month_key
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f.name
        if dest.exists():
            # already archived
            continue
        try:
            shutil.move(str(f), str(dest))
            moved += 1
        except OSError as e:
            print(f"  Failed to move {f.name}: {e}")
    return moved


def count_recent(days: int) -> dict:
    cutoff = datetime.now() - timedelta(days=days)
    counts: Counter[str] = Counter()
    total = 0
    hold = 0
    for f in LOG_DIR.glob("*.json"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
        except OSError:
            continue
        if mtime < cutoff:
            continue
        total += 1
        cat = categorize(f.name)
        counts[cat] += 1
        if any(pat in cat for pat in HOLD_PATTERNS):
            hold += 1
    return {
        "window_days": days,
        "total_logs": total,
        "hold_actions": hold,
        "hold_ratio": round(hold / total, 3) if total else 0.0,
        "by_category": dict(counts.most_common()),
    }


def main() -> int:
    now = datetime.now()
    print(f"[{now.isoformat()}] Log Janitor — running...")

    all_json = list(LOG_DIR.glob("*.json"))
    print(f"  Total JSON log files: {len(all_json)}")

    moved = archive_old_logs()
    print(f"  Archived (>{ARCHIVE_AGE_DAYS}d old): {moved}")

    recent = count_recent(SUMMARY_WINDOW_DAYS)
    print(f"  {SUMMARY_WINDOW_DAYS}-day log summary: {recent['total_logs']} total, "
          f"{recent['hold_actions']} hold ({recent['hold_ratio']:.1%})")
    for cat, count in sorted(recent["by_category"].items()):
        print(f"    {cat}: {count}")

    summary = {
        "run_at": now.isoformat(),
        "total_files_before_archive": len(all_json),
        "archived_this_run": moved,
        "recent_7d": recent,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2))
    print(f"  Done. Summary: {SUMMARY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
