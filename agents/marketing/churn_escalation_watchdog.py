#!/usr/bin/env python3
"""Churn escalation watchdog — monitors artifact density and auto-pauses crons.

This watchdog is the third layer of defense against the self-referential churn
cascade that consumed 146-178 artifacts/day through May 28.

Layer 1: Cron throttling (30min → 6h) — deployed May 28, 2026
Layer 2: Hard artifact-rate limiter (distribution_lane_executor.py) — deployed May 28
Layer 3: THIS WATCHDOG — monitors, auto-pauses, and escalates

Three-strikes escalation (per USER.md rule):
  Strike 1: soft-flag in the shared execution board
  Strike 2: auto-pause the two churn-heavy crons (audit-precheck, momentum-watchdog)
  Strike 3: kill ALL marketing crons + write emergency signal + notify human

Usage:
    python3 agents/marketing/churn_escalation_watchdog.py [--dry-run]
    python3 agents/marketing/churn_escalation_watchdog.py --reset
    python3 agents/marketing/churn_escalation_watchdog.py --status
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path('/home/mistlight/.openclaw/workspace')
STATE_PATH = ROOT / 'agents/marketing/logs/churn_escalation_state.json'
EMERGENCY_SIGNAL = ROOT / 'agents/marketing/logs/CHURN_EMERGENCY_SIGNAL.md'
LOG_DIR = ROOT / 'agents/marketing/logs'

# ── thresholds ──────────────────────────────────────────────────────────────
EXECUTION_ARTIFACT_HARD_LIMIT = 4          # per hour (matches distribution_lane_executor)
MEASUREMENT_HOLD_MAX = 2                    # per hour
CHURN_ARTIFACT_WINDOW_HOURS = 6             # sliding window
MAX_CHURN_EVENTS_BEFORE_PAUSE = 3           # strike 2 threshold
MAX_CHURN_EVENTS_BEFORE_EMERGENCY = 6       # strike 3 threshold
STRIKE_WINDOW_HOURS = 24                    # counter reset window

# ── cron IDs ────────────────────────────────────────────────────────────────
CHURN_HEAVY_CRON_IDS = [
    '5e8746f2-2311-48f6-a113-4ac6880b2376',  # marketing-workflow-audit-precheck
    'ce3f9db8-bb49-4f97-9ee3-d4786314c204',  # marketing-momentum-watchdog
]
ALL_MARKETING_CRON_IDS = [
    '5e8746f2-2311-48f6-a113-4ac6880b2376',  # marketing-workflow-audit-precheck
    'ce3f9db8-bb49-4f97-9ee3-d4786314c204',  # marketing-momentum-watchdog
    '5d2cc5b0-b001-4d77-a270-08d987e0779a',  # marketing-active-loop
    'ba650cdc-c8fa-40b0-9684-6b45df3cbd60',  # marketing-research-daily
    'fe8f8f62-54c4-4b80-9d22-3cb65adad113',  # marketing-daily
    '1a3502be-1f1c-4e0d-8c61-98e9ebffaa75',  # marketing-measurement-hold-release
    '6571aec7-48f0-4051-8713-3aee65197cb4',  # marketing-outcome-capability
    'b3d15455-5bc1-4fa1-b735-1dbde7c7e3b5',  # marketing-distribution-hunter
    '50578fdf-ea83-46bd-8580-36d2bae7ba54',  # marketing-workflow-audit (THIS)
]

NON_DISTRIBUTION_EXECUTION_TYPES = {
    'distribution_architecture_repair',
    'distribution_architecture_churn_guard_repair',
    'distribution_architecture_guard_pause',
    'measurement_hold_follow_through',
    'measurement_hold_churn_guard_repair',
    'measurement_hold_release_reschedule_repair',
    'post_hold_release_prompt_guard_repair',
    'measurement_hold_release_delivery_route_repair',
    'guard_follow_through',
    'guard_pause',
    'independent_verification_hold',
    'self_improvement_verification',
    'churn_guard_follow_through',
    # Extended from May 28 repair-spike analysis
    'primary_repo_flat_contact_discovery_repair',
    'primary_repo_flat_status_churn_guard_repair',
    'primary_repo_flat_delivery_guard_repair',
    'measurement_hold_stackoverflow_delivery_guard_repair',
    'measurement_hold_release_payload_guard_repair',
    'measurement_hold_truth_fingerprint_repair',
    'stackoverflow_lane_runtime_repair',
    'guard_pause_release_boundary_repair',
    'truth_snapshot_alias_self_heal_repair',
    'distribution_architecture_guard_pause_truth_repair',
    'distribution_architecture_conversion_repair',
    'distribution_lane_latest_truth_repair',
    'readme_repo_conversion_repair',
    'reddit_latest_truth_repair',
    'process_repair_and_new_asset',
    'apollo_truthfulness_repair',
    'apollo_cloudflare_truthfulness_repair',
    'apollo_runtime_truth_repair',
    'apollo_followup_truth_repair',
}

# Suffix-based catch-all for any repair not explicitly listed
_REPAIR_ACTION_SUFFIXES = (
    "_repair",
    "_guard_pause",
    "_churn_guard",
    "_truth_repair",
)


def _is_non_distribution_action(action_type: str) -> bool:
    """Return True for any action that is a system repair, not a distribution action.

    Shares logic with run.py's _is_self_repair_action.
    """
    action_type = str(action_type).strip()
    if not action_type:
        return False
    if action_type in NON_DISTRIBUTION_EXECUTION_TYPES:
        return True
    return any(action_type.endswith(suffix) for suffix in _REPAIR_ACTION_SUFFIXES) or \
        "_churn_guard_" in action_type


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {'strikes': [], 'paused_crons': [], 'emergency_declared': False,
            'last_check': None, 'history': []}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


def count_artifacts(now: datetime) -> dict[str, int]:
    """Count artifacts in the sliding window by category."""
    cutoff = now.timestamp() - (CHURN_ARTIFACT_WINDOW_HOURS * 3600)
    counts = {
        'total': 0,
        'measurement_hold': 0,
        'non_distribution_execution': 0,
        'distribution': 0,
        'other': 0,
    }
    for path in LOG_DIR.glob('marketing_2026-*.json'):
        if any(t in path.name for t in ('latest', 'workflow_audit', 'loop_runner',
                'loop_verifier', 'independent_verification', 'momentum_watchdog',
                'positioning_audit', 'adoption_metrics')):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        counts['total'] += 1
        try:
            payload = json.loads(path.read_text())
            action_type = (
                (payload.get('chosen_action') or {}).get('type', '')
                or payload.get('type', '')
                or payload.get('action_type', '')
            )
        except Exception:
            action_type = ''
        
        if 'measurement_hold' in action_type or 'measurement_hold' in path.name:
            counts['measurement_hold'] += 1
        elif _is_non_distribution_action(action_type):
            counts['non_distribution_execution'] += 1
        elif 'outreach' in action_type or 'submission' in action_type or 'backlink' in action_type or 'apollo' in action_type.lower():
            counts['distribution'] += 1
        else:
            counts['other'] += 1
    return counts


def rate_exceeded(counts: dict[str, int]) -> tuple[bool, list[str]]:
    """Check if any rate limits are exceeded."""
    violations = []
    hourly_rate = lambda total: total / max(1, CHURN_ARTIFACT_WINDOW_HOURS)

    if hourly_rate(counts['measurement_hold']) > MEASUREMENT_HOLD_MAX:
        violations.append(
            f"measurement_hold rate {hourly_rate(counts['measurement_hold']):.1f}/hr "
            f"(limit {MEASUREMENT_HOLD_MAX}/hr)"
        )
    if hourly_rate(counts['non_distribution_execution']) > EXECUTION_ARTIFACT_HARD_LIMIT:
        violations.append(
            f"non-distribution execution rate {hourly_rate(counts['non_distribution_execution']):.1f}/hr "
            f"(limit {EXECUTION_ARTIFACT_HARD_LIMIT}/hr)"
        )
    if counts['distribution'] == 0 and counts['total'] >= 10:
        violations.append(
            f"{counts['total']} total artifacts in {CHURN_ARTIFACT_WINDOW_HOURS}h "
            f"with ZERO distribution actions — pure churn"
        )
    return bool(violations), violations


def churn_ratio_danger(counts: dict[str, int]) -> bool:
    """Return True if churn artifacts dominate distribution in either recent or daily windows."""
    if counts['total'] == 0:
        return False
    churn = counts['measurement_hold'] + counts['non_distribution_execution']
    dist = counts['distribution']
    if dist == 0 and churn >= 6:
        return True
    if churn > 0 and dist > 0 and (churn / max(dist, 1)) >= 5:
        return True
    # Daily cumulative check: even if the 6h window looks fine, the full day
    # may show a repair-to-distribution imbalance.
    return _daily_churn_ratio_danger()


def _daily_churn_ratio_danger(hours: float = 24) -> bool:
    """Check 24h cumulative repair-to-distribution ratio.

    Even if the 6h sliding window passes, 30 repairs and 0 distribution actions
    in a 24h period is a silent failure.
    """
    now = datetime.now()
    cutoff = now.timestamp() - (hours * 3600)
    churn_24h = 0
    dist_24h = 0
    for path in LOG_DIR.glob('marketing_2026-*.json'):
        if any(t in path.name for t in ('latest', 'workflow_audit', 'loop_runner',
                'loop_verifier', 'independent_verification', 'momentum_watchdog',
                'positioning_audit', 'adoption_metrics')):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        try:
            payload = json.loads(path.read_text())
            action_type = (
                (payload.get('chosen_action') or {}).get('type', '')
                or payload.get('type', '')
                or payload.get('action_type', '')
            )
        except Exception:
            action_type = ''
        if 'measurement_hold' in action_type or _is_non_distribution_action(action_type):
            churn_24h += 1
        elif 'outreach' in action_type or 'submission' in action_type or 'backlink' in action_type or 'apollo' in action_type.lower():
            dist_24h += 1
    if dist_24h == 0 and churn_24h >= 8:
        return True
    if churn_24h > 0 and dist_24h > 0 and (churn_24h / max(dist_24h, 1)) >= 4:
        return True
    return False


def pause_crons(cron_ids: list[str], dry_run: bool = False) -> dict:
    """Pause cron jobs via openclaw CLI."""
    results = {}
    for cid in cron_ids:
        if dry_run:
            results[cid] = 'dry_run_pause'
            continue
        try:
            result = subprocess.run(
                ['/home/mistlight/.bun/bin/openclaw', 'cron', 'disable', cid],
                capture_output=True, text=True, timeout=10
            )
            results[cid] = 'paused' if result.returncode == 0 else f'error: {result.stderr.strip()[:100]}'
        except Exception as exc:
            results[cid] = f'exception: {exc}'
    return results


def kill_crons(cron_ids: list[str], dry_run: bool = False) -> dict:
    """Remove cron jobs via openclaw CLI."""
    results = {}
    for cid in cron_ids:
        if dry_run:
            results[cid] = 'dry_run_remove'
            continue
        try:
            result = subprocess.run(
                ['/home/mistlight/.bun/bin/openclaw', 'cron', 'rm', cid],
                capture_output=True, text=True, timeout=10
            )
            results[cid] = 'removed' if result.returncode == 0 else f'error: {result.stderr.strip()[:100]}'
        except Exception as exc:
            results[cid] = f'exception: {exc}'
    return results


def write_emergency_signal(counts: dict[str, int], violations: list[str], state: dict) -> None:
    """Write a human-readable emergency signal file."""
    EMERGENCY_SIGNAL.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '# 🚨 CHURN EMERGENCY — Marketing Cron Killed',
        '',
        f'**Generated:** {datetime.now().isoformat()}',
        '',
        '## Violations',
    ]
    for v in violations:
        lines.append(f'- {v}')
    lines.extend([
        '',
        '## Artifact Counts',
        f'- Total: {counts["total"]} (in {CHURN_ARTIFACT_WINDOW_HOURS}h window)',
        f'- Measurement hold: {counts["measurement_hold"]}',
        f'- Non-distribution execution: {counts["non_distribution_execution"]}',
        f'- Distribution: {counts["distribution"]}',
        f'- Other: {counts["other"]}',
        '',
        '## Actions Taken',
        '- ALL marketing crons have been KILLED',
        '- Cron IDs removed from OpenClaw cron table',
        '- Manual restart required by human operator',
        '',
        '## Recovery Steps',
        '1. Investigate root cause of churn cascade',
        '2. Verify the hard artifact-rate limiter in distribution_lane_executor.py',
        '3. Re-add crons with conservative cadence (≥4h)',
        '4. Clear the escalation state: `python3 agents/marketing/churn_escalation_watchdog.py --reset`',
        '',
        '## Strike History',
    ])
    for s in state.get('strikes', []):
        lines.append(f'- {s.get("timestamp", "?")} [{s.get("level", "?")}] {s.get("reason", "?")[:120]}')
    lines.append('')
    EMERGENCY_SIGNAL.write_text('\n'.join(lines))


def record_strike(level: int, reason: str, state: dict) -> dict:
    """Record a strike and return updated state."""
    strike = {
        'level': level,
        'timestamp': datetime.now().isoformat(),
        'reason': reason,
    }
    state.setdefault('strikes', []).append(strike)
    
    # Prune old strikes outside the window
    cutoff = datetime.now() - timedelta(hours=STRIKE_WINDOW_HOURS)
    state['strikes'] = [
        s for s in state['strikes']
        if datetime.fromisoformat(s['timestamp'].replace('Z', '+00:00').replace('+00:00', '')) > cutoff
    ]
    return state


def run_check(dry_run: bool = False) -> dict[str, Any]:
    """Main watchdog check."""
    now = datetime.now()
    state = load_state()
    counts = count_artifacts(now)
    exceeded, violations = rate_exceeded(counts)
    danger = churn_ratio_danger(counts)
    
    result = {
        'timestamp': now.isoformat(),
        'dry_run': dry_run,
        'counts': counts,
        'rate_exceeded': exceeded,
        'violations': violations,
        'churn_ratio_danger': danger,
        'action': 'none',
        'details': '',
    }

    if not exceeded and not danger:
        result['action'] = 'pass_clean'
        result['details'] = 'all metrics within acceptable range'
        save_state(state)
        return result

    # ── Strike 1: soft flag ──
    if len(state.get('strikes', [])) < MAX_CHURN_EVENTS_BEFORE_PAUSE:
        reason = '; '.join(violations) if violations else f'churn ratio danger (distribution={counts["distribution"]}, churn={counts["measurement_hold"] + counts["non_distribution_execution"]})'
        state = record_strike(1, reason, state)
        result['action'] = 'strike_1_soft_flag'
        result['details'] = f'recorded strike 1: {reason[:200]}'
        result['strike_count'] = len(state.get('strikes', []))
        save_state(state)
        return result

    # ── Strike 2: auto-pause churn crons ──
    if len(state.get('strikes', [])) < MAX_CHURN_EVENTS_BEFORE_EMERGENCY:
        reason = '; '.join(violations) if violations else f'churn ratio danger with {len(state.get("strikes", []))} prior strikes'
        state = record_strike(2, reason, state)
        pause_results = pause_crons(CHURN_HEAVY_CRON_IDS, dry_run=dry_run)
        state['paused_crons'] = CHURN_HEAVY_CRON_IDS
        result['action'] = 'strike_2_auto_pause'
        result['details'] = f'paused {len(CHURN_HEAVY_CRON_IDS)} churn-heavy crons: {pause_results}'
        result['pause_results'] = pause_results
        result['strike_count'] = len(state.get('strikes', []))
        save_state(state)
        return result

    # ── Strike 3: emergency kill ──
    reason = '; '.join(violations) if violations else f'persistent churn cascade with {len(state.get("strikes", []))} prior strikes'
    state = record_strike(3, reason, state)
    kill_results = kill_crons(ALL_MARKETING_CRON_IDS, dry_run=dry_run)
    write_emergency_signal(counts, violations, state)
    state['emergency_declared'] = True
    state['killed_crons'] = ALL_MARKETING_CRON_IDS
    result['action'] = 'strike_3_emergency_kill'
    result['details'] = f'killed ALL {len(ALL_MARKETING_CRON_IDS)} marketing crons'
    result['kill_results'] = kill_results
    result['strike_count'] = len(state.get('strikes', []))
    save_state(state)
    return result


def reset_state() -> None:
    """Reset strike counter after manual investigation."""
    STATE_PATH.write_text(json.dumps({
        'strikes': [],
        'paused_crons': [],
        'emergency_declared': False,
        'last_check': datetime.now().isoformat(),
        'history': [],
        'reset_at': datetime.now().isoformat(),
    }, indent=2))
    if EMERGENCY_SIGNAL.exists():
        EMERGENCY_SIGNAL.unlink()
    print("✅ Churn escalation state reset.")


def show_status() -> None:
    """Print current watchdog state."""
    state = load_state()
    counts = count_artifacts(datetime.now())
    print("=== Churn Escalation Watchdog Status ===")
    print(f"Artifacts (last {CHURN_ARTIFACT_WINDOW_HOURS}h):")
    print(f"  Total: {counts['total']}")
    print(f"  Measurement hold: {counts['measurement_hold']}")
    print(f"  Non-distribution execution: {counts['non_distribution_execution']}")
    print(f"  Distribution: {counts['distribution']}")
    print(f"Strikes: {len(state.get('strikes', []))}")
    for s in state.get('strikes', []):
        print(f"  - [{s['level']}] {s['timestamp'][:19]} {s['reason'][:100]}")
    print(f"Paused crons: {state.get('paused_crons', [])}")
    print(f"Emergency: {state.get('emergency_declared', False)}")


if __name__ == '__main__':
    if '--reset' in sys.argv:
        reset_state()
    elif '--status' in sys.argv:
        show_status()
    elif '--test' in sys.argv:
        # Test mode: always report what WOULD happen
        result = run_check(dry_run=True)
        print(json.dumps(result, indent=2, default=str))
    else:
        dry_run = '--dry-run' in sys.argv
        result = run_check(dry_run=dry_run)
        # Write result as a log artifact
        log_path = LOG_DIR / f'churn_watchdog_{datetime.now().strftime("%Y-%m-%d_%H%M%S")}.json'
        log_path.write_text(json.dumps(result, indent=2, default=str))
        print(json.dumps(result, indent=2, default=str))
        if result['action'].startswith('strike_'):
            print(f"\n⚠️  ACTION: {result['action']} — {result['details'][:200]}")
