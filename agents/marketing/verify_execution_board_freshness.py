#!/usr/bin/env python3
"""Verify execution board freshness — guard against fake-green board refreshes.

The board was claimed as "refreshed" 3+ times on June 4 while still containing
May 25 content. This script checks that the board's Generated timestamp is
within EXPECTED_MAX_AGE_HOURS of now.

Usage: python3 verify_execution_board_freshness.py
Exit code: 0 if fresh, 1 if stale.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BOARD = Path("/home/mistlight/.openclaw/workspace/drafts/marketing_execution_board_latest.md")
EXPECTED_MAX_AGE_HOURS = 6  # Board must have been generated within last 6 hours

def check_board_freshness() -> bool:
    if not BOARD.exists():
        print(f"STALE: Board file {BOARD} does not exist")
        return False

    content = BOARD.read_text()
    match = re.search(r"Generated:\s*([^\n]+)", content)
    if not match:
        print(f"STALE: Board has no Generated timestamp")
        return False

    gen_str = match.group(1).strip()
    # Try ISO format: 2026-06-04T19:10:00Z
    try:
        gen_time = datetime.fromisoformat(gen_str.replace("Z", "+00:00"))
    except ValueError:
        # Try with parenthetical: 2026-06-04T19:10:00Z (21:10 CEST)
        clean = gen_str.split("(")[0].strip()
        try:
            gen_time = datetime.fromisoformat(clean.replace("Z", "+00:00"))
        except ValueError:
            print(f"STALE: Cannot parse Generated timestamp: {gen_str}")
            return False

    now = datetime.now(timezone.utc)
    age = now - gen_time
    max_age = timedelta(hours=EXPECTED_MAX_AGE_HOURS)

    if age > max_age:
        hours = age.total_seconds() / 3600
        print(f"STALE: Board generated {hours:.1f}h ago (max {EXPECTED_MAX_AGE_HOURS}h). Generated: {gen_str}")
        return False

    file_mtime = datetime.fromtimestamp(BOARD.stat().st_mtime, tz=timezone.utc)
    # File modification should be close to generation time
    content_claimed_gen = gen_str

    print(f"FRESH: Board generated {gen_str} ({age.total_seconds()/3600:.1f}h ago) ✓")
    return True

if __name__ == "__main__":
    fresh = check_board_freshness()
    sys.exit(0 if fresh else 1)
