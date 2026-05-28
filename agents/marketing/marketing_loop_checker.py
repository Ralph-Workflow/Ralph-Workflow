#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
AUDIT = ROOT / 'agents/marketing/logs/marketing_workflow_audit_latest.json'
MOMENTUM = ROOT / 'agents/marketing/logs/marketing_momentum_watchdog.json'
PACKET = ROOT / 'drafts/reddit_next_window_packets_latest.md'
AUTOpOST = ROOT / 'agents/marketing/reddit_autopost.py'
NEXT_PACKET = ROOT / 'agents/marketing/reddit_next_window_packet.py'
RUNNER = ROOT / 'agents/marketing/logs/marketing_loop_runner_latest.json'
MAX_AGE_MIN = 240
MAX_COHERENCE_SKEW_SECONDS = 300
ALLOWED_WATCH_ACTIONS = {
    'reddit_channel_blocked',
    'reddit_monitor_degraded',
    'apollo_channel_blocked',
    'apollo_monitor_stale',
    'primary_repo_adoption_flat',
    'measurement_hold_active',
}


def age_minutes(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 60.0


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def latest_reddit_packet_is_required(runner: dict, momentum: dict) -> bool:
    watch_actions = set(momentum.get('watch_actions', []) or [])
    if 'reddit_channel_blocked' in watch_actions:
        return False
    for result in runner.get('results', []) or []:
        if 'reddit_next_window_packet.py' not in str(result.get('script') or ''):
            continue
        stdout = str(result.get('stdout') or '').strip()
        if not stdout:
            break
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            break
        if payload.get('status') in {'channel_blocked_skip', 'report_guard_skip'}:
            return False
        break
    return True


def runner_is_coherent(runner_path: Path, peer_paths: list[Path]) -> tuple[bool, str | None]:
    runner_mtime = runner_path.stat().st_mtime
    newest_peer = max(path.stat().st_mtime for path in peer_paths)
    if runner_mtime + MAX_COHERENCE_SKEW_SECONDS < newest_peer:
        newest_name = max(peer_paths, key=lambda path: path.stat().st_mtime).name
        return False, newest_name
    return True, None


def repetitive_outreach_runtime_fixed() -> bool:
    auto = AUTOpOST.read_text(encoding='utf-8')
    packet = NEXT_PACKET.read_text(encoding='utf-8')
    required_auto = [
        'SITE_LANGUAGE_TERMS',
        'candidate_policy_issues',
        'one_paragraph_candidates',
        'review tax',
    ]
    required_packet = [
        'candidate_policy_issues(candidate, opp)',
        'validate_marketing_copy(candidate)',
    ]
    return all(token in auto for token in required_auto) and all(token in packet for token in required_packet)


def shipped_system_redesign(audit: dict) -> bool:
    latest = audit.get('latest_executed_action') or {}
    if latest.get('outcome_ready') is False:
        return False
    return bool(latest.get('ok')) and bool(latest.get('live_external_action'))


def watch_state_is_certifiable(momentum: dict, audit: dict) -> tuple[bool, str | None]:
    watch_actions = set(momentum.get('watch_actions', []) or [])
    actions = set(momentum.get('actions', []) or [])
    if actions:
        return False, 'momentum watchdog still reports actionable failures'
    unexpected = sorted(watch_actions - ALLOWED_WATCH_ACTIONS)
    if unexpected:
        return False, 'unexpected watch actions: ' + ', '.join(unexpected)

    apollo = momentum.get('apollo', {}) or {}
    healthy_report_age_hours = momentum.get('latest_healthy_report_age_hours')
    repair_window_status = audit.get('repair_window_status')
    pending_reasons = set(audit.get('measurement_pending_reasons', []) or [])

    if 'reddit_monitor_degraded' in watch_actions:
        if healthy_report_age_hours is None or healthy_report_age_hours > 3:
            return False, 'reddit monitor degraded without a fresh healthy fallback report'

    if 'primary_repo_adoption_flat' in watch_actions:
        if repair_window_status != 'measurement_pending' or 'primary_repo_flat' not in pending_reasons:
            return False, 'primary repo adoption flat is no longer covered by a measurement-pending repair window'
        return False, 'primary repo adoption remains flat inside a measurement-pending repair window; do not certify health yet'

    if 'reddit_channel_blocked' in watch_actions:
        if not shipped_system_redesign(audit):
            return False, 'reddit is blocked, but no shipped replacement distribution execution is recorded yet'

    if 'apollo_channel_blocked' in watch_actions:
        if apollo.get('status') not in {'ato_email_verification_required', 'cloudflare_auth_blocked'}:
            return False, f"apollo blocked watchpoint has an unexpected status: {apollo.get('status')}"
        if apollo.get('age_hours') is None or apollo.get('age_hours') > 12:
            return False, 'apollo blocked watchpoint is stale'

    if 'apollo_monitor_stale' in watch_actions and 'apollo_channel_blocked' not in watch_actions:
        return False, 'apollo telemetry is stale without an explicit blocked-channel handoff'

    return True, None


def main() -> int:
    missing = [str(p) for p in [AUDIT, MOMENTUM, RUNNER] if not p.exists()]
    if missing:
        print('MARKETING_LOOP_FAIL: missing artifacts: ' + ', '.join(missing))
        return 1

    stale = [str(p) for p in [AUDIT, MOMENTUM, RUNNER] if age_minutes(p) > MAX_AGE_MIN]
    if stale:
        print('MARKETING_LOOP_FAIL: stale artifacts: ' + ', '.join(stale))
        return 1

    audit = load_json(AUDIT)
    momentum = load_json(MOMENTUM)
    runner = load_json(RUNNER)
    packet_required = latest_reddit_packet_is_required(runner, momentum)

    if packet_required and not PACKET.exists():
        print(f'MARKETING_LOOP_FAIL: missing artifacts: {PACKET}')
        return 1
    if packet_required and age_minutes(PACKET) > MAX_AGE_MIN:
        print(f'MARKETING_LOOP_FAIL: stale artifacts: {PACKET}')
        return 1

    coherent, newer_peer = runner_is_coherent(RUNNER, [AUDIT, MOMENTUM])
    if not coherent:
        print(f'MARKETING_LOOP_FAIL: runner artifact is stale relative to {newer_peer}; rerun the full runner bundle before certifying health')
        return 1

    if not runner.get('ok', False):
        print('MARKETING_LOOP_FAIL: runner bundle still reports failure')
        return 1

    momentum_status = momentum.get('status')
    watch_actions = set(momentum.get('watch_actions', []) or [])
    if momentum_status in {'watch'}:
        certifiable_watch, watch_error = watch_state_is_certifiable(momentum, audit)
        if not certifiable_watch:
            print(f'MARKETING_LOOP_FAIL: {watch_error}')
            return 1
    elif momentum_status not in {'ok', 'healthy'}:
        print(f"MARKETING_LOOP_FAIL: momentum watchdog status is {momentum_status}")
        return 1

    if audit.get('repair_window_status') == 'needs_repair':
        print('MARKETING_LOOP_FAIL: audit still reports needs_repair')
        return 1

    failing = set(audit.get('failing_tactics', []) or [])
    pending_reasons = set(audit.get('measurement_pending_reasons', []) or [])
    actions = set(momentum.get('actions', []) or [])

    if 'pending_repairs_detected' in actions:
        print('MARKETING_LOOP_FAIL: momentum watchdog still reports pending repairs')
        return 1

    if 'outcome_system_repair_missing' in actions:
        print('MARKETING_LOOP_FAIL: primary repo adoption is flat but the loop has not produced a live system-design repair aimed at marketing outcomes')
        return 1

    if 'primary_repo_flat_window' in failing and 'primary_repo_flat' not in pending_reasons:
        print('MARKETING_LOOP_FAIL: primary repo adoption is still flat across the current measurement window without a live measurement-pending repair state')
        return 1

    if 'primary_repo_flat_window' in failing:
        has_system_design_repair = any((action or {}).get('repair_kind') == 'system_design' for action in (audit.get('repair_actions', []) or []))
        if not has_system_design_repair and not shipped_system_redesign(audit):
            print('MARKETING_LOOP_FAIL: outcome flatness is being treated as a tactical problem only; missing system-design repair action')
            return 1

    if 'reddit_style_repetition' in failing:
        if 'repetitive_outreach' not in pending_reasons:
            print('MARKETING_LOOP_FAIL: repetitive outreach still failing without measurement-pending repair state')
            return 1
        if not repetitive_outreach_runtime_fixed():
            print('MARKETING_LOOP_FAIL: repetitive outreach marked measurement-pending but runtime enforcement is missing')
            return 1

    print('MARKETING_LOOP_OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
