#!/usr/bin/env python3
"""ARCHITECTURALLY RETIRED 2026-05-28 — watches a permanently-blocked channel.
All Reddit activity consolidated into a no-op retirement state.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

if __name__ == '__main__':
    print(json.dumps({'status': 'retired', 'reason': 'Reddit pipeline architecturally retired 2026-05-28'}))
    sys.exit(0)

SCRIPT_NAME = Path(__file__).name
ROOT = Path("/home/mistlight/.openclaw/workspace")
REPORT_DIR = ROOT / "seo-reports"
STATE_PATH = ROOT / "agents/marketing/logs/reddit_autopost_state.json"
MONITOR = ROOT / "agents/marketing/reddit_monitor.py"
AUTOpOST = ROOT / "agents/marketing/reddit_autopost.py"
NEXT_WINDOW_PACKET = ROOT / "agents/marketing/reddit_next_window_packet.py"

TERMINAL_STATUSES = {"posted", "already_consumed", "no_unused_opportunity", "already_logged", "locked", "stale_only_skip"}
RETRYABLE_STATUSES = {"cooldown_skip", "fresh_opportunity_rate_limited"}
REFRESHABLE_GUARD_DETAILS = {
    "report_partial_coverage",
    "report_coverage_unhealthy",
    "mention_fit_below_medium",
}


def _detail_tokens(value) -> set[str]:
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    if isinstance(value, str):
        return {part.strip() for part in value.split(';') if part.strip()}
    return set()


def should_refresh_report(report: Path, state: dict) -> bool:
    if state.get("last_report") != str(report):
        return False
    if state.get("last_attempt_status") != "report_guard_skip":
        return False
    details = _detail_tokens(state.get("last_detail"))
    return bool(details & REFRESHABLE_GUARD_DETAILS)


def should_skip_report(report: Path, state: dict) -> bool:
    if state.get("last_report") != str(report):
        return False
    if should_refresh_report(report, state):
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
    # ── Spidering guard: Reddit is permanently blocked ──
    try:
        from agents.marketing.channel_spidering_guard import guard_check, guard_record
        allowed, reason, remaining = guard_check("reddit")
        if not allowed:
            guard_record("reddit", ok=False, fingerprint="spidering_guard_rejected")
            print(json.dumps({"ok": False, "status": "spidering_blocked", "reason": reason, "live_external_action": False}))
            return 1
    except ImportError:
        pass

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

    refresh_requested = should_refresh_report(report, state)
    if refresh_requested:
        monitor_result = subprocess.run([sys.executable, str(MONITOR)], capture_output=True, text=True)
        monitor_stdout = monitor_result.stdout.strip()
        monitor_payload = None
        try:
            monitor_payload = json.loads(monitor_stdout) if monitor_stdout else None
        except Exception:
            monitor_payload = None

        refreshed_report = latest_report() or report
        refreshed_status = (monitor_payload or {}).get("status")
        if refreshed_status in RETRYABLE_STATUSES:
            packet_result = subprocess.run([sys.executable, str(NEXT_WINDOW_PACKET)], capture_output=True, text=True)
            packet_stdout = packet_result.stdout.strip()
            try:
                packet_payload = json.loads(packet_stdout) if packet_stdout else None
            except Exception:
                packet_payload = {
                    "ok": packet_result.returncode == 0,
                    "status": "packet_generation_unparsed" if packet_stdout else ("packet_generation_failed" if packet_result.returncode else "packet_generated_empty"),
                    "stdout": packet_stdout,
                    "stderr": packet_result.stderr.strip(),
                }
            print(json.dumps({
                "ok": monitor_result.returncode == 0,
                "status": "refresh_blocked_stale_report_preserved",
                "report": str(report),
                "refresh": monitor_payload,
                "next_window_packet": packet_payload,
                "stderr": monitor_result.stderr.strip(),
            }, indent=2))
            return 0 if monitor_result.returncode == 0 else 1

        report = refreshed_report
        if not report_is_fresh(report, now):
            print(json.dumps({
                "ok": monitor_result.returncode == 0,
                "status": "refresh_failed_stale_report",
                "report": str(report),
                "refresh": monitor_payload,
                "stderr": monitor_result.stderr.strip(),
            }, indent=2))
            return 0 if monitor_result.returncode == 0 else 1

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


# ── Self-improvement ──────────────────────────────────────────────────────────

SELF_IMPROVEMENT_MANDATE = {
    "owner": "marketing_system",
    "goal": "improve real marketing outcomes, not just technical health",
    "default_decision_rule": "it is up to the system to decide and proceed",
    "required_when_outcomes_flat": [
        "create_new_agents",
        "repair_existing_agents",
        "rewrite_prompts",
        "change_cron_jobs",
        "patch_marketing_scripts",
        "add_or_tighten_tests",
        "retire_stale_paths",
        "generate_new_distribution_assets",
    ],
    "technical_repairs_alone_are_insufficient": True,
}


def flat_outcome_count(script_name: str, max_runs: int = 3) -> int:
    log_path = Path('/home/mistlight/.openclaw/workspace/outreach-log.md')
    if not log_path.exists():
        return 0
    text = log_path.read_text()
    import re
    entries = re.findall(rf'###\s+.*?{re.escape(script_name)}.*?(?=\n###|\Z)', text, re.DOTALL)
    flat_count = sum(1 for e in entries if 'no measurable outcome' in e.lower() or 'flat' in e.lower())
    return min(flat_count, max_runs)


def should_self_improve() -> bool:
    """Return True if this loop has had flat outcomes for 3+ consecutive runs."""
    return flat_outcome_count(SCRIPT_NAME.replace('.py','')) >= 3


if __name__ == "__main__":
    raise SystemExit(main())
