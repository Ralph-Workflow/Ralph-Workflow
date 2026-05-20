#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
REPORT_DIR = ROOT / "seo-reports"
STATE_PATH = ROOT / "agents/marketing/logs/reddit_autopost_state.json"
AUTOpOST = ROOT / "agents/marketing/reddit_autopost.py"
NEXT_WINDOW_PACKET = ROOT / "agents/marketing/reddit_next_window_packet.py"

TERMINAL_STATUSES = {"posted", "already_consumed", "no_unused_opportunity", "already_logged", "locked", "stale_only_skip"}
RETRYABLE_STATUSES = {"cooldown_skip", "fresh_opportunity_rate_limited"}


def should_skip_report(report: Path, state: dict) -> bool:
    if state.get("last_report") != str(report):
        return False
    status = state.get("last_attempt_status")
    if status in RETRYABLE_STATUSES:
        return False
    return status in TERMINAL_STATUSES


def latest_report() -> Path | None:
    reports = sorted(REPORT_DIR.glob("reddit_monitor_*.md"))
    return reports[-1] if reports else None


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def report_timestamp(report: Path) -> datetime | None:
    stem = report.stem
    prefix = "reddit_monitor_"
    if not stem.startswith(prefix):
        return None
    try:
        return datetime.strptime(stem[len(prefix):], "%Y-%m-%d_%H%M")
    except ValueError:
        return None


def report_is_fresh(report: Path, now: datetime) -> bool:
    ts = report_timestamp(report)
    if ts is None:
        return False
    return now - ts <= timedelta(hours=8)


def main() -> int:
    now = datetime.now()
    report = latest_report()
    if report is None:
        print(json.dumps({"ok": True, "status": "no_report"}, indent=2))
        return 0

    if not report_is_fresh(report, now):
        print(json.dumps({"ok": True, "status": "stale_report", "report": str(report)}, indent=2))
        return 0

    state = load_state()
    if should_skip_report(report, state):
        print(json.dumps({
            "ok": True,
            "status": "already_handled",
            "report": str(report),
            "last_attempt_status": state.get("last_attempt_status"),
            "last_detail": state.get("last_detail"),
        }, indent=2))
        return 0

    result = subprocess.run([sys.executable, str(AUTOpOST)], capture_output=True, text=True)
    stdout = result.stdout.strip()
    payload = None
    try:
        payload = json.loads(stdout) if stdout else None
    except Exception:
        payload = None

    packet_payload = None
    packet_status = (payload or {}).get("status")
    if packet_status in RETRYABLE_STATUSES:
        packet_result = subprocess.run([sys.executable, str(NEXT_WINDOW_PACKET)], capture_output=True, text=True)
        packet_stdout = packet_result.stdout.strip()
        if packet_stdout:
            try:
                packet_payload = json.loads(packet_stdout)
            except Exception:
                packet_payload = {
                    "ok": packet_result.returncode == 0,
                    "status": "packet_generation_unparsed",
                    "stdout": packet_stdout,
                    "stderr": packet_result.stderr.strip(),
                }
        else:
            packet_payload = {
                "ok": packet_result.returncode == 0,
                "status": "packet_generation_failed" if packet_result.returncode else "packet_generated_empty",
                "stderr": packet_result.stderr.strip(),
            }

    print(json.dumps({
        "ok": result.returncode == 0,
        "status": "autopost_attempted",
        "report": str(report),
        "autopost": payload,
        "next_window_packet": packet_payload,
        "stderr": result.stderr.strip(),
    }, indent=2))
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
