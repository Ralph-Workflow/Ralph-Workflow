#!/usr/bin/env python3
"""
measurement_window_watchdog.py — Daily gate that checks measurement window closures
and surfaces past-due tactics for kill / rewrite / escalate decisions.

Created: 2026-05-28 — repair for measurement-window drift + stale test gates.
The marketing system sets measurement windows on almost every distribution lane
but has no automated review to check whether windows have passed — leaving stale
repairs in measurement_pending forever while the loop grinds on.

This watchdog:
1. Reads all active measurement windows from recent distribution logs
2. Flags any window past its review date with no outcome logged
3. Flags any repair action stuck in measurement_pending without a delta
4. Produces a kill-list and an escalation-list
5. Writes a machine-readable JSON for other scripts to consume
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path('/home/mistlight/.openclaw/workspace')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AGENTS_DIR = ROOT / 'agents/marketing'
LOG_DIR = AGENTS_DIR / 'logs'
DRAFTS_DIR = ROOT / 'drafts'

# ── Paths ────────────────────────────────────────────────────────────────────
WATCHDOG_JSON_PATH = LOG_DIR / 'measurement_window_watchdog_latest.json'
WATCHDOG_MD_PATH = DRAFTS_DIR / 'measurement_window_watchdog_latest.md'
ADOPTION_METRICS_PATH = LOG_DIR / 'adoption_metrics_latest.json'
AUDIT_PATH = LOG_DIR / 'marketing_workflow_audit_latest.json'

# ── Tactic types that are past-due when measurement window has passed ─────────
TACTIC_SLA_DAYS: dict[str, int] = {
    'apollo_outreach_sequence': 7,       # 7 days for Apollo response window
    'apollo_outreach_launch': 30,        # 30 days for full adoption window
    'curator_outreach_execution': 14,    # 14 days for curator response
    'comparison_backlink_outreach': 14,  # 14 days for backlink evidence
    'directory_submission_execution': 7, # 7 days for editorial review
    'directory_confirmation_execution': 7,
    'reddit_comment': 7,
    'content_distribution': 7,
    'repo_conversion_proof_asset': 14,
    'primary_repo_flat_contact_handoff_packet_execution': 14,
    'default': 14,
}


@dataclass
class PastDueTactic:
    """A tactic whose measurement window has passed without evidence of outcome."""
    action_type: str
    lane: str
    launched_at: str
    measurement_window_days: int
    review_due_date: str
    days_past_due: int
    artifact_path: str
    status: str
    recommendation: str  # kill, escalate, rewrite, wait


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {}


def parse_iso_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        try:
            return datetime.strptime(value, '%Y-%m-%d')
        except ValueError:
            return None
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _adoption_has_delta(adoption: dict[str, Any] | None) -> bool:
    """Check whether the primary repo shows measurable adoption delta."""
    if not adoption:
        return False
    recent = adoption.get('recent_window', {}) or {}
    cb = recent.get('Codeberg', {}) or {}
    return any([
        (cb.get('stars_delta_window') or 0) != 0,
        (cb.get('watchers_delta_window') or 0) != 0,
        (cb.get('forks_delta_window') or 0) != 0,
    ])


def _extract_measurement_windows(now: datetime) -> list[dict[str, Any]]:
    """Scan recent distribution logs for active measurement + review windows."""
    windows: list[dict[str, Any]] = []
    cutoff = now - timedelta(days=60)  # Look back 60 days

    for path in sorted(LOG_DIR.glob('marketing_*.json'), reverse=True):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            continue

        ts_str = str(data.get('timestamp', '')).strip()
        ts = parse_iso_date(ts_str)
        if ts is None or ts < cutoff:
            continue

        # Extract review dates
        review_window = data.get('review_window') if isinstance(data.get('review_window'), dict) else {}
        measurement_window = data.get('measurement_window') if isinstance(data.get('measurement_window'), dict) else {}

        review_due = review_window.get('review_due_date') or \
                     review_window.get('scheduled_run_at') or \
                     measurement_window.get('review_due_date') or \
                     data.get('review_due_date') or ''

        if not review_due:
            # Infer from why_this_action.hold_until or similar
            why = data.get('why_this_action') if isinstance(data.get('why_this_action'), dict) else {}
            hold_until = why.get('hold_until') or ''
            if hold_until:
                review_due = hold_until

        if not review_due:
            continue

        review_dt = parse_iso_date(review_due)
        if review_dt is None:
            continue

        action_type = str(data.get('action_type', '') or data.get('execution_action_type', '') or '')
        lane = str(data.get('lane', '') or '')
        status = str(data.get('status', '') or '')
        artifact_path = str(data.get('artifact_path', '') or data.get('handoff_packet_path', '') or '')

        # Check if outcome evidence exists
        has_outcome = status in {'distributed', 'responded', 'published', 'live', 'launched'} or \
                      data.get('live_external_action', False)

        windows.append({
            'action_type': action_type,
            'lane': lane,
            'launched_at': ts_str,
            'review_due_date': review_due,
            'review_dt': review_dt,
            'days_past_due': (now - review_dt).days,
            'artifact_path': artifact_path,
            'status': status,
            'has_outcome': has_outcome,
            'log_path': str(path),
        })

    return windows


def _check_audit_repairs_stuck(now: datetime) -> list[dict[str, Any]]:
    """Check audit repair_actions stuck in measurement_pending without delta."""
    audit = _load_json(AUDIT_PATH)
    if not audit:
        return []

    repairs = audit.get('repair_actions', []) or []
    stuck: list[dict[str, Any]] = []

    for repair in repairs:
        state = repair.get('repair_state', '')
        if state != 'pending_measurement':
            continue

        acknowledged = parse_iso_date(repair.get('repair_acknowledged_at', ''))
        if acknowledged is None:
            continue

        days_in_pending = (now - acknowledged).days
        failure_type = repair.get('failure_type', '')

        # If it's been >7 days in pending_measurement with no adoption delta, it's stuck
        if days_in_pending > 7:
            adoption = _load_json(ADOPTION_METRICS_PATH)
            has_delta = _adoption_has_delta(adoption)

            recommendation = 'wait' if days_in_pending <= 14 else 'escalate'
            if failure_type == 'primary_repo_flat' and not has_delta:
                recommendation = 'escalate'  # Primary signal is still failing

            stuck.append({
                'repair_type': failure_type,
                'repair_action': repair.get('action', ''),
                'acknowledged_at': repair.get('repair_acknowledged_at', ''),
                'days_in_pending': days_in_pending,
                'adoption_has_delta': has_delta,
                'recommendation': recommendation,
            })

    return stuck


def _classify_tactic(tactic: dict[str, Any], now: datetime) -> str:
    """Classify: kill / escalate / rewrite / wait."""
    if tactic['has_outcome']:
        return 'wait'  # Already has outcome evidence

    days_past = tactic['days_past_due']
    action_type = tactic['action_type']

    if days_past <= 0:
        return 'wait'  # Window hasn't closed yet

    sla = TACTIC_SLA_DAYS.get(action_type, TACTIC_SLA_DAYS['default'])
    past_sla = days_past > sla

    if past_sla and action_type in {'content_distribution', 'owned_content'}:
        return 'kill'  # Owned content that hasn't driven adoption past SLA should be killed
    elif past_sla and action_type in {'reddit_comment', 'curator_outreach_execution'}:
        return 'rewrite'  # Outreach past SLA needs fresh angle
    elif past_sla:
        return 'escalate'  # Can't auto-enforce, needs human decision
    elif days_past > 3:
        return 'rewrite'  # 3+ days past due, time for fresh approach
    else:
        return 'wait'


def run(now: datetime | None = None) -> dict[str, Any]:
    if now is None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)

    windows = _extract_measurement_windows(now)

    # De-duplicate: keep most recent instance of each unique action_type+artifact combo
    seen_keys: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for w in windows:
        key = f"{w['action_type']}::{w['artifact_path']}"
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(w)
    windows = deduped

    past_due: list[dict[str, Any]] = []
    kill_list: list[dict[str, Any]] = []
    escalate_list: list[dict[str, Any]] = []
    rewrite_list: list[dict[str, Any]] = []
    wait_list: list[dict[str, Any]] = []

    for w in windows:
        if w['days_past_due'] <= 0:
            continue  # Not past due yet

        classification = _classify_tactic(w, now)
        entry = {
            'action_type': w['action_type'],
            'lane': w['lane'],
            'launched_at': w['launched_at'],
            'review_due_date': w['review_due_date'],
            'days_past_due': w['days_past_due'],
            'artifact_path': w['artifact_path'],
            'recommendation': classification,
            'log_path': w['log_path'],
        }

        if classification == 'kill':
            kill_list.append(entry)
        elif classification == 'escalate':
            escalate_list.append(entry)
        elif classification == 'rewrite':
            rewrite_list.append(entry)
        else:
            wait_list.append(entry)

        past_due.append(entry)

    stuck_repairs = _check_audit_repairs_stuck(now)

    # Check Apollo specifically
    apollo_windows = [w for w in windows if 'apollo' in w['action_type'].lower()]
    apollo_blocked = any(w['days_past_due'] > 14 and not w['has_outcome'] for w in apollo_windows)

    result = {
        'generated_at': now.isoformat(),
        'past_due_count': len(past_due),
        'kill_count': len(kill_list),
        'escalate_count': len(escalate_list),
        'rewrite_count': len(rewrite_list),
        'wait_count': len(wait_list),
        'stuck_repair_count': len(stuck_repairs),
        'kill_list': kill_list,
        'escalate_list': escalate_list,
        'rewrite_list': rewrite_list,
        'wait_list': wait_list,
        'stuck_repairs': stuck_repairs,
        'apollo_blocked': apollo_blocked,
        'total_active_windows': len(windows),
    }

    # Write artifacts
    WATCHDOG_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCHDOG_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCHDOG_JSON_PATH.write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')

    lines = _build_markdown_report(result)
    WATCHDOG_MD_PATH.write_text(lines, encoding='utf-8')

    return result


def _build_markdown_report(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append('# Measurement Window Watchdog Report')
    lines.append(f'Generated: {result["generated_at"]}')
    lines.append('')
    lines.append('## Summary')
    lines.append(f'- Active measurement windows: {result["total_active_windows"]}')
    lines.append(f'- Past-due tactics: {result["past_due_count"]}')
    lines.append(f'  - Kill: {result["kill_count"]}')
    lines.append(f'  - Rewrite: {result["rewrite_count"]}')
    lines.append(f'  - Escalate: {result["escalate_count"]}')
    lines.append(f'  - Wait: {result["wait_count"]}')
    lines.append(f'- Stuck repairs: {result["stuck_repair_count"]}')
    lines.append(f'- Apollo blocked: {result["apollo_blocked"]}')
    lines.append('')

    if result['kill_list']:
        lines.append('## 🔴 Kill: Past-SLA Tactics (no outcome)')
        lines.append('These tactics are past their measurement SLA with zero adoption delta. Kill them.')
        for t in result['kill_list']:
            lines.append(f'- **{t["action_type"]}** ({t["days_past_due"]} days past due) — {t["artifact_path"]}')
        lines.append('')

    if result['escalate_list']:
        lines.append('## 🟡 Escalate: Needs Human Decision')
        for t in result['escalate_list']:
            lines.append(f'- **{t["action_type"]}** ({t["days_past_due"]} days past due) — {t["artifact_path"]}')
        lines.append('')

    if result['rewrite_list']:
        lines.append('## 🟠 Rewrite: Fresh Approach Needed')
        for t in result['rewrite_list']:
            lines.append(f'- **{t["action_type"]}** ({t["days_past_due"]} days past due) — {t["artifact_path"]}')
        lines.append('')

    if result['stuck_repairs']:
        lines.append('## ⚠️ Stuck Repairs (measurement_pending > 7 days)')
        for r in result['stuck_repairs']:
            lines.append(f'- **{r["repair_type"]}** — {r["days_in_pending"]}d in pending, adoption_delta={r["adoption_has_delta"]} → {r["recommendation"]}')
        lines.append('')

    return '\n'.join(lines)


def main() -> None:
    result = run()
    print(json.dumps(result, indent=2, default=str))
    summary = (
        f'{result["past_due_count"]} past-due, '
        f'{result["kill_count"]} kill, '
        f'{result["rewrite_count"]} rewrite, '
        f'{result["escalate_count"]} escalate, '
        f'{result["stuck_repair_count"]} repairs stuck'
    )
    print(f'\n📊 {summary}')
    print(f'   Report: {WATCHDOG_MD_PATH}')


if __name__ == '__main__':
    main()
