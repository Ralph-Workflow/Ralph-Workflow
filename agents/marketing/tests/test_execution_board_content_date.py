#!/usr/bin/env python3
"""
Verify that the marketing execution board's content date matches its filename.

Prevents the 4-strike fake-green pattern where audits claimed to refresh
the board but only renamed the file without updating the content.

Run: python3 agents/marketing/tests/test_execution_board_content_date.py
"""

import os
import re
import sys
from datetime import date
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[3]  # .../openclaw/workspace
BOARD_SYMLINK = WORKSPACE / "drafts" / "marketing_execution_board_latest.md"


def get_board_content_date(board_path: Path) -> str | None:
    """Extract the Generated: date from the board file."""
    if not board_path.exists():
        return None
    content = board_path.read_text()
    m = re.search(r"Generated:\s*(\d{4}-\d{2}-\d{2})", content)
    return m.group(1) if m else None


def get_board_filename_date(board_path: Path) -> str | None:
    """Extract the date from the board filename (e.g., 2026-06-04)."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})_marketing_execution_board", board_path.name)
    return m.group(1) if m else None


def main():
    errors = []

    if not BOARD_SYMLINK.exists():
        print(f"FAIL: Board symlink does not exist: {BOARD_SYMLINK}")
        sys.exit(1)

    # Resolve symlink to actual file
    real_board = BOARD_SYMLINK.resolve()

    # Check 1: Does the content have a Generated: date?
    content_date = get_board_content_date(real_board)
    if content_date is None:
        errors.append(
            f"Board file {real_board.name} has no 'Generated: YYYY-MM-DD' line"
        )

    # Check 2: Does the filename contain a date?
    filename_date = get_board_filename_date(real_board)
    if filename_date is None:
        errors.append(
            f"Board file {real_board.name} has no date in filename"
        )

    # Check 3: Do the dates match?
    if content_date and filename_date and content_date != filename_date:
        errors.append(
            f"Date MISMATCH: content says '{content_date}' but filename says '{filename_date}'"
        )

    # Check 4: Is the content date today? (warn, not fail — board may be intentional snapshot)
    if content_date:
        today = date.today().isoformat()
        if content_date != today:
            print(
                f"WARN: Board content date '{content_date}' is not today '{today}' "
                f"(may be intentional if board is a historical snapshot)"
            )

    if errors:
        print("FAIL: Execution board content date verification failed:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    print(
        f"✅ PASS: Board content date '{content_date}' matches filename date "
        f"'{filename_date}' — file: {real_board.name}"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
