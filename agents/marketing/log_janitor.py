#!/usr/bin/env python3
"""Log janitor — prevents marketing log inflation from measurement-hold artifacts.

Created 2026-05-30 as part of marketing-workflow-audit structural repair.
The marketing loop generates 50+ JSON log files per day, many of which are
measurement-hold, guard-pause, or verification artifacts with zero outcome
movement. After 14 days they serve only as storage debt.

Behavior (runs weekly on Sunday):
- Archives logs older than 14 days to logs/archive/YYYY-MM/
- Measures hold-action ratio: if >60% of recent logs are measurement_hold
  variants, writes a structural-alert artifact
- Generates counts.json summary for dashboard use
"""

from __future__ import annotations

import json
import os
import shutil
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
LOG_DIR = ROOT / "agents/marketing/logs"
ARCHIVE_BASE = LOG_DIR / "archive"
SUMMARY_PATH = LOG_DIR / "log_counts_summary.json"
STRUCTURAL_ALERT = ROOT / "drafts/log_inflation_alert_latest.md"

HOLD_ACTION_PATTERNS = [
    "measurement_hold",
    "guard_pause",
    "distribution_architecture_guard_pause",
    "measurement_hold_follow_through",
    "distribution_confirmation_follow_through",
    "measurement_hold_execution",
    "distribution_architecture_guard_follow_through",
]

ARCHIVE_AGE_DAYS = 14
HOLD_RATIO_ALERT_THRESHOLD = 0.60


def is_hold_action(path: Path) -> bool:
    """Check if a log file represents a measurement-hold or guard-pause action."""
    try:
        data = json.loads(path.read_text())
        action_type = (
            data.get("chosen_action", {}).get("type", "")
            or data.get("type", "")
            or data.get("selected_lane", "")
        )
        return any(pattern in action_type.lower() for pattern in HOLD_ACTION_PATTERNS)
    except Exception:
        name = path.name.lower()
        return any(pattern in name for pattern in HOLD_ACTION_PATTERNS)


def count_recent_logs(days: int) -> dict:
    """Count log files by type in the recent window."""
    cutoff = datetime.now() - timedelta(days=days)
    counts = Counter()
    hold_count = 0
    total = 0
    recent_files: list[Path] = []

    for f in sorted(LOG_DIR.glob("*.json")):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
        except Exception:
            continue
        if mtime < cutoff:
            continue
        recent_files.append(f)
        total += 1
        if is_hold_action(f):
            hold_count += 1

        # Categorize
        name = f.name.lower()
        if "measurement_hold" in name:
            counts["measurement_hold"] += 1
        elif "guard_pause" in name or "guard_follow" in name:
            counts["guard_pause"] += 1
        elif "distribution_confirmation" in name:
            counts["confirmation_follow_through"] += 1
        elif "outcome_capability" in name:
            counts["outcome_capability"] += 1
        elif "distribution_hunter" in name:
            counts["distribution_hunter"] += 1
        elif "execution_board" in name:
            counts["execution_board"] += 1
        elif "adoption" in name:
            counts["adoption"] += 1
        elif "market_intelligence" in name:
            counts["market_intelligence"] += 1
        elif "pypi" in name:
            counts["pypi"] += 1
        elif "publisher" in name:
            counts["publisher"] += 1
        elif "telegraph" in name or "posting" in name:
            counts["telegraph"] += 1
        elif "reddit" in name:
            counts["reddit"] += 1
        else:
            counts["other"] += 1

    hold_ratio = hold_count / total if total > 0 else 0
    return {
        "window_days": days,
        "total_logs": total,
        "hold_actions": hold_count,
        "hold_ratio": round(hold_ratio, 3),
        "by_category": dict(counts.most_common()),
    }


def archive_old_logs(dry_run: bool = False) -> int:
    """Move logs older than ARCHIVE_AGE_DAYS to the archive."""
    cutoff = datetime.now() - timedelta(days=ARCHIVE_AGE_DAYS)
    archived = 0

    for f in LOG_DIR.glob("*.json"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
        except Exception:
            continue
        if mtime >= cutoff:
            continue

        # Determine archive month folder
        month_key = mtime.strftime("%Y-%m")
        archive_dir = ARCHIVE_BASE / month_key
        if not dry_run:
            # Skip files already in archive
            pass
        archive_dir.mkdir(parents=True, exist_ok=True)

        dest = archive_dir / f.name
        if not dry_run:
            try:
                shutil.move(str(f), str(dest))
            except Exception as e:
                print(f"  Failed to move {f.name}: {e}")
                continue
        archived += 1

    return archived


def write_alert(recent: dict, total_files: int) -> None:
    """Write structural alert if hold ratio exceeds threshold."""
    ratio = recent["hold_ratio"]
    if ratio < HOLD_RATIO_ALERT_THRESHOLD:
        # Remove alert if ratio has improved
        if STRUCTURAL_ALERT.exists():
            STRUCTURAL_ALERT.unlink()
        return

    content = f"""# Marketing Log Inflation Alert
Generated: {datetime.now().isoformat()}

⚠️ **Measurement-hold dominance detected**

Over the past {recent['window_days']} days, {recent['hold_actions']} of {recent['total_logs']} marketing
log files ({ratio:.1%}) were measurement-hold, guard-pause, or confirmation-follow-through
artifacts — actions that produce no outcome movement.

**Category breakdown:**
"""
    for cat, count in recent["by_category"].items():
        content += f"- {cat}: {count}\n"

    content += f"""
**Total log files in logs/:** {total_files}

**Why this matters:**
The measurement-hold mechanism was designed as a short-window safety valve
to prevent bundling multiple external actions into one measurement period.
When it becomes the dominant log output, the system is burning cycles on
hold/noop artifacts instead of creating distribution or conversion.

**Previously applied repairs:**
- Social preview card deployed (2026-05-30)
- llms.txt protocol deployed (2026-05-30)
- Doorway-page consolidation (2026-05-30)
- log_janitor.py (this script, 2026-05-30)
- PyPI auto-unblocker (2026-05-30)
- Publisher discovery reduced to weekly (2026-05-30)
- Apollo verifier left at weekly (Cloudflare block persistent)

**Remaining human-gated blockers:**
- PYPI_TOKEN — v0.8.8 built, unpublished, 1,299 downloads/mo see old README
- gh auth login — 8 comparison PRs + 5 Discussion drafts undeliverable
- Apollo Cloudflare solve — managed outbound blocked
- SMTP credentials — publisher email outreach undeliverable

**System cannot clear this alert on its own.**
The hold ratio will stay high as long as all executable autonomous lanes
are saturated and the remaining blockers are human-gated.
"""
    STRUCTURAL_ALERT.parent.mkdir(parents=True, exist_ok=True)
    STRUCTURAL_ALERT.write_text(content)


def run(dry_run: bool = False) -> None:
    now = datetime.now()
    print(f"[{now.isoformat()}] Log Janitor — running...")

    # Count all files before archiving
    all_json_files = list(LOG_DIR.glob("*.json"))
    total_files = len(all_json_files)
    print(f"  Total JSON log files: {total_files}")

    # Archive old files
    archived = archive_old_logs(dry_run=dry_run)
    print(f"  Archived (>{ARCHIVE_AGE_DAYS}d old): {archived}")

    # Count recent log types
    recent = count_recent_logs(days=7)
    print(f"  7-day log summary: {recent['total_logs']} total, "
          f"{recent['hold_actions']} hold ({recent['hold_ratio']:.1%})")
    for cat, count in sorted(recent["by_category"].items()):
        print(f"    {cat}: {count}")

    # Save summary
    summary = {
        "run_at": now.isoformat(),
        "total_files_before_archive": total_files,
        "archived_this_run": archived,
        "recent_7d": recent,
        "hold_ratio_alert_active": recent["hold_ratio"] >= HOLD_RATIO_ALERT_THRESHOLD,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2))

    # Write structural alert if needed
    write_alert(recent, total_files)

    print(f"  Done. Summary: {SUMMARY_PATH}")


if __name__ == "__main__":
    run()
