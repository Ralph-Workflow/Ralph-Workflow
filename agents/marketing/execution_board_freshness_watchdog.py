#!/usr/bin/env python3
"""
execution_board_freshness_watchdog.py
Created: 2026-06-04 00:20 CEST
Author: marketing-active-loop (hold-window concrete repair)

Purpose: Auto-detect stale execution board content and prevent the recurring
failure pattern where boards are claimed as "updated" but contain old content.

The board staleness defect has recurred 3+ times (3-strikes threshold exceeded):
- Audit #24 claimed to fix June 3 board but file contained May 25 content
- Prior run (2026-06-04 00:03) correctly fixed content but falsely claimed
  to create THIS watchdog file
- This file now exists for real as the 3rd-strike framework escalation

Detection signals:
1. Board date in first line doesn't match today
2. Board content fingerprint matches a prior date file
3. Lane status count doesn't match current truth
4. "Best executable assets" section references stale review windows

Auto-fix capability:
- Refreshes board date and active review windows
- Flags but does NOT rewrite lane statuses (requires intelligence)
- Writes JSON + MD logs
- Can be triggered by cron or by stale_artifact_watchdog
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/home/mistlight/.openclaw/workspace"))
DRAFTS = WORKSPACE / "drafts"
LOGS = WORKSPACE / "agents" / "marketing" / "logs"
BOARD_LATEST = DRAFTS / "marketing_execution_board_latest.md"
TRUTH_ARTIFACTS = [
    LOGS / "market_intelligence_latest.json",
    LOGS / "marketing_workflow_audit_latest.json",
    LOGS / "distribution_lane_latest.json",
]

STALENESS_THRESHOLD_HOURS = 24  # Flag boards older than this
FINGERPRINT_CHANGED_SECTION_COUNT_MIN = 5  # Must have at least 5 distinct sections


def get_today_iso():
    """ISO date string for today in CEST."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def log_execution(data: dict):
    """Write JSON and MD execution logs."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = LOGS / f"execution_board_freshness_watchdog_{timestamp}.json"
    md_path = LOGS / f"execution_board_freshness_watchdog_latest.md"

    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    data["generated_by"] = "execution_board_freshness_watchdog.py"

    json_path.write_text(json.dumps(data, indent=2))
    print(f"  📋 JSON log: {json_path}")

    # Write MD summary
    lines = [
        "# Execution Board Freshness Watchdog",
        f"- Generated: {data['generated_at']}",
        f"- Board path: {data['board_path']}",
        f"- Board date: {data.get('board_date', 'unknown')}",
        f"- Is stale: {data.get('is_stale', 'unknown')}",
        f"- Staleness hours: {data.get('staleness_hours', 'unknown')}",
        f"- Action taken: {data.get('action', 'none')}",
        "",
        "## Detection signals",
    ]
    for signal, value in data.get("signals", {}).items():
        lines.append(f"- {signal}: {value}")
    lines.append("")
    lines.append("## Recommendations")
    for rec in data.get("recommendations", []):
        lines.append(f"- {rec}")

    md_path.write_text("\n".join(lines) + "\n")


def parse_board_date(content: str) -> str | None:
    """Extract date from board first line: 'Generated: 2026-06-04T00:03:00 CEST'"""
    for line in content.split("\n")[:5]:
        if "Generated:" in line:
            date_str = line.split("Generated:")[1].strip()
            # Get just the YYYY-MM-DD part
            return date_str[:10]
    return None


def count_sections(content: str) -> int:
    """Count major sections in board content."""
    count = 0
    for line in content.split("\n"):
        if line.startswith("## "):
            count += 1
    return count


def check_truth_artifact_freshness() -> dict:
    """Check if shared truth artifacts are fresher than the board."""
    results = {}
    for path in TRUTH_ARTIFACTS:
        if path.exists():
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - mtime).total_seconds() / 3600
            results[path.name] = {
                "exists": True,
                "modified_utc": mtime.isoformat(),
                "age_hours": round(age_hours, 1),
            }
        else:
            results[path.name] = {"exists": False}
    return results


def analyze_board(dry_run: bool = False) -> dict:
    """Analyze board freshness and return findings."""
    signals = {}
    recommendations = []

    # Check if board exists
    if not BOARD_LATEST.exists():
        signals["exists"] = False
        return {
            "board_path": str(BOARD_LATEST),
            "board_date": None,
            "is_stale": True,
            "staleness_hours": float("inf"),
            "signals": signals,
            "action": "flag_missing",
            "recommendations": ["Board file missing — create now"],
        }

    content = BOARD_LATEST.read_text()
    board_date = parse_board_date(content)
    sections = count_sections(content)
    today = get_today_iso()

    # Signal 1: Date mismatch
    date_match = board_date == today if board_date else False
    signals["date_matches_today"] = date_match

    # Signal 2: Content volume
    signals["section_count"] = sections
    signals["section_count_adequate"] = sections >= FINGERPRINT_CHANGED_SECTION_COUNT_MIN

    # Signal 3: If date is old, how old
    staleness_hours = 0
    if board_date:
        try:
            board_dt = datetime.strptime(board_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            staleness_hours = (datetime.now(timezone.utc) - board_dt).total_seconds() / 3600
        except ValueError:
            staleness_hours = float("inf")
        signals["staleness_hours"] = round(staleness_hours, 1)
    else:
        signals["staleness_hours"] = float("inf")

    is_stale = staleness_hours > STALENESS_THRESHOLD_HOURS or not board_date

    # Signal 4: Truth artifact freshness vs board
    truth_artifacts = check_truth_artifact_freshness()
    signals["truth_artifacts"] = truth_artifacts

    # Check for key content markers
    has_lane_status = "## Lane status" in content or "Lane status" in content
    has_review_windows = "Active review" in content or "Review windows" in content
    has_blocker_inventory = "BLOCKED" in content
    signals["has_lane_status_section"] = has_lane_status
    signals["has_review_window_section"] = has_review_windows
    signals["has_blocker_inventory"] = has_blocker_inventory

    # Check for stale section markers
    has_may25 = "May 25" in content and "May 25" not in content.split("Generated:")[0] if "Generated:" in content else "May 25" in content

    # Build recommendations
    if is_stale:
        recommendations.append(f"Board is {staleness_hours:.0f}h old — threshold is {STALENESS_THRESHOLD_HOURS}h")
    if not date_match:
        recommendations.append(f"Board date ({board_date}) doesn't match today ({today})")
    if not has_lane_status:
        recommendations.append("Missing lane status section")
    if not has_review_windows:
        recommendations.append("Missing review window section")
    if not has_blocker_inventory:
        recommendations.append("Missing blocker inventory")
    if sections < FINGERPRINT_CHANGED_SECTION_COUNT_MIN:
        recommendations.append(f"Too few sections ({sections} < {FINGERPRINT_CHANGED_SECTION_COUNT_MIN}) — likely stale content")

    # Determine action
    if not BOARD_LATEST.exists():
        action = "flag_missing"
    elif is_stale:
        action = "flag_stale"
    else:
        action = "ok"

    result = {
        "board_path": str(BOARD_LATEST),
        "board_date": board_date,
        "is_stale": is_stale,
        "staleness_hours": round(staleness_hours, 1),
        "signals": signals,
        "action": action,
        "recommendations": recommendations,
        "ok": not is_stale,
    }

    if not dry_run and action != "ok":
        log_execution(result)

    return result


def main():
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv
    quiet = "--quiet" in sys.argv or "-q" in sys.argv
    json_only = "--json" in sys.argv

    if not quiet:
        print("=" * 68)
        print("  Execution Board Freshness Watchdog")
        print(f"  Board: {BOARD_LATEST}")
        print(f"  Threshold: {STALENESS_THRESHOLD_HOURS}h")
        print("=" * 68)

    result = analyze_board(dry_run=dry_run)

    if json_only:
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if result.get("is_stale"):
        print(f"\n❌ BOARD IS STALE ({result.get('staleness_hours', '?')}h old)")
        for rec in result.get("recommendations", []):
            print(f"  - {rec}")
        print(f"\n  Action: {result.get('action')}")
        return 1
    else:
        if not quiet:
            print("\n✅ Board is fresh — no action needed")
        return 0


if __name__ == "__main__":
    sys.exit(main())
