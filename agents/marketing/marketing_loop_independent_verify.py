#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents/marketing/logs'
RUNNER = LOG_DIR / 'marketing_loop_runner_latest.json'
MOMENTUM = LOG_DIR / 'marketing_momentum_watchdog.json'
AUDIT = LOG_DIR / 'marketing_workflow_audit_latest.json'
OUT = LOG_DIR / 'marketing_loop_independent_verification.json'
MAX_COHERENCE_SKEW_SECONDS = 300
EXECUTION_BOARD = ROOT / 'drafts/marketing_execution_board_latest.md'
OUTCOME_EXECUTION_BOARD_STATUS = LOG_DIR / 'outcome_execution_board_latest.json'
DISTRIBUTION_LANE_STATUS = LOG_DIR / 'distribution_lane_latest.json'
ALLOWED_WATCH_ACTIONS = {
    'reddit_channel_blocked',
    'reddit_monitor_degraded',
    'apollo_channel_blocked',
    'apollo_monitor_stale',
    'primary_repo_adoption_flat',
    'measurement_hold_active',
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def runner_is_coherent(runner_path: Path, peer_paths: list[Path]) -> tuple[bool, str | None]:
    runner_mtime = runner_path.stat().st_mtime
    newest_peer = max(path.stat().st_mtime for path in peer_paths)
    if runner_mtime + MAX_COHERENCE_SKEW_SECONDS < newest_peer:
        newest_name = max(peer_paths, key=lambda path: path.stat().st_mtime).name
        return False, newest_name
    return True, None


def shipped_system_redesign(audit: dict) -> bool:
    latest = audit.get('latest_executed_action') or {}
    if latest.get('outcome_ready') is False:
        return False
    return bool(latest.get('ok')) and bool(latest.get('live_external_action'))


def execution_board_freshness() -> tuple[bool, str | None]:
    if not EXECUTION_BOARD.exists():
        return False, 'missing'
    peer_paths = [path for path in (RUNNER, MOMENTUM, AUDIT, DISTRIBUTION_LANE_STATUS) if path.exists()]
    if peer_paths:
        newest_peer = max(path.stat().st_mtime for path in peer_paths)
        if EXECUTION_BOARD.stat().st_mtime + MAX_COHERENCE_SKEW_SECONDS < newest_peer:
            newest_name = max(peer_paths, key=lambda path: path.stat().st_mtime).name
            return False, f'stale_mtime_vs_{newest_name}'
    try:
        text = EXECUTION_BOARD.read_text(encoding='utf-8')
    except OSError:
        return False, 'unreadable'
    match = re.search(r'^Generated:\s*(.+)$', text, re.MULTILINE)
    if not match:
        return False, 'missing_generated_timestamp'
    try:
        generated_at = datetime.fromisoformat(match.group(1).strip())
    except ValueError:
        return False, 'invalid_generated_timestamp'
    if generated_at.tzinfo is not None:
        generated_at = generated_at.astimezone().replace(tzinfo=None)
    if peer_paths:
        newest_peer_dt = max(datetime.fromtimestamp(path.stat().st_mtime) for path in peer_paths)
        if generated_at.timestamp() + MAX_COHERENCE_SKEW_SECONDS < newest_peer_dt.timestamp():
            newest_name = max(peer_paths, key=lambda path: path.stat().st_mtime).name
            return False, f'stale_generated_timestamp_vs_{newest_name}'
    return True, None


def execution_board_is_fresh() -> bool:
    return execution_board_freshness()[0]


def outcome_execution_board_status_is_fresh() -> tuple[bool, str | None]:
    if not OUTCOME_EXECUTION_BOARD_STATUS.exists():
        return False, 'missing'
    peer_paths = [path for path in (EXECUTION_BOARD, DISTRIBUTION_LANE_STATUS, RUNNER, MOMENTUM, AUDIT) if path.exists()]
    if peer_paths:
        newest_peer = max(path.stat().st_mtime for path in peer_paths)
        if OUTCOME_EXECUTION_BOARD_STATUS.stat().st_mtime + MAX_COHERENCE_SKEW_SECONDS < newest_peer:
            newest_name = max(peer_paths, key=lambda path: path.stat().st_mtime).name
            return False, f'stale_mtime_vs_{newest_name}'
    try:
        payload = load_json(OUTCOME_EXECUTION_BOARD_STATUS)
    except Exception:
        return False, 'invalid_json'
    timestamp = payload.get('timestamp')
    if not timestamp:
        return False, 'missing_timestamp'
    try:
        generated_at = datetime.fromisoformat(str(timestamp))
    except ValueError:
        return False, 'invalid_timestamp'
    if generated_at.tzinfo is not None:
        generated_at = generated_at.astimezone().replace(tzinfo=None)
    if peer_paths:
        newest_peer = max(datetime.fromtimestamp(path.stat().st_mtime) for path in peer_paths)
        if generated_at.timestamp() + MAX_COHERENCE_SKEW_SECONDS < newest_peer.timestamp():
            newest_name = max(peer_paths, key=lambda path: path.stat().st_mtime).name
            return False, f'stale_timestamp_vs_{newest_name}'
    return True, None


def distribution_lane_status_is_fresh() -> tuple[bool, str | None]:
    if not DISTRIBUTION_LANE_STATUS.exists():
        return False, 'missing'
    peer_paths = [path for path in (EXECUTION_BOARD, OUTCOME_EXECUTION_BOARD_STATUS, RUNNER, MOMENTUM, AUDIT) if path.exists()]
    if peer_paths:
        newest_peer = max(path.stat().st_mtime for path in peer_paths)
        if DISTRIBUTION_LANE_STATUS.stat().st_mtime + MAX_COHERENCE_SKEW_SECONDS < newest_peer:
            newest_name = max(peer_paths, key=lambda path: path.stat().st_mtime).name
            return False, f'stale_mtime_vs_{newest_name}'
    try:
        payload = load_json(DISTRIBUTION_LANE_STATUS)
    except Exception:
        return False, 'invalid_json'
    artifact_path = str(payload.get('artifact_path') or '').strip()
    if not artifact_path:
        return False, 'missing_artifact_path'
    artifact = Path(artifact_path)
    if not artifact.exists():
        return False, 'missing_artifact'
    try:
        text = artifact.read_text(encoding='utf-8')
    except OSError:
        return False, 'artifact_unreadable'
    match = re.search(r'^Generated:\s*(.+)$', text, re.MULTILINE)
    if not match:
        return False, 'missing_generated_timestamp'
    try:
        generated_at = datetime.fromisoformat(match.group(1).strip())
    except ValueError:
        return False, 'invalid_generated_timestamp'
    if generated_at.tzinfo is not None:
        generated_at = generated_at.astimezone().replace(tzinfo=None)
    if peer_paths:
        newest_peer_dt = max(datetime.fromtimestamp(path.stat().st_mtime) for path in peer_paths)
        if generated_at.timestamp() + MAX_COHERENCE_SKEW_SECONDS < newest_peer_dt.timestamp():
            newest_name = max(peer_paths, key=lambda path: path.stat().st_mtime).name
            return False, f'stale_generated_timestamp_vs_{newest_name}'
    return True, None


def watch_state_is_certifiable(momentum: dict, audit: dict) -> tuple[bool, list[str], list[str]]:
    blockers: list[str] = []
    watchpoints: list[str] = []
    watch_actions = set(momentum.get('watch_actions', []) or [])
    actions = set(momentum.get('actions', []) or [])

    if actions:
        blockers.append('Momentum watchdog still reports actionable failures: ' + ', '.join(sorted(actions)) + '.')

    unexpected = sorted(watch_actions - ALLOWED_WATCH_ACTIONS)
    if unexpected:
        blockers.append('Momentum watchdog reported unexpected watch actions: ' + ', '.join(unexpected) + '.')

    apollo = momentum.get('apollo', {}) or {}
    healthy_report_age_hours = momentum.get('latest_healthy_report_age_hours')
    repair_window_status = audit.get('repair_window_status')
    pending_reasons = set(audit.get('measurement_pending_reasons', []) or [])
    execution_board_fresh, execution_board_reason = execution_board_freshness()
    outcome_board_status_fresh, outcome_board_status_reason = outcome_execution_board_status_is_fresh()
    distribution_lane_fresh, distribution_lane_reason = distribution_lane_status_is_fresh()

    if 'reddit_monitor_degraded' in watch_actions:
        if healthy_report_age_hours is None or healthy_report_age_hours > 3:
            blockers.append('Reddit monitor is degraded without a fresh healthy fallback report inside the grace window.')
        else:
            watchpoints.append('reddit monitoring is degraded, but a fresh healthy fallback report still exists inside the grace window')

    if 'primary_repo_adoption_flat' in watch_actions:
        if repair_window_status != 'measurement_pending' or 'primary_repo_flat' not in pending_reasons:
            if 'measurement_hold_active' in watch_actions and execution_board_fresh:
                blockers.append('Primary repo adoption is flat and the current primary-repo repair is still awaiting real execution from the consolidated execution board.')
                watchpoints.append('primary repo follow-through is consolidated into a fresh execution board during the active hold window')
            else:
                blockers.append('Primary repo adoption is flat without a live measurement-pending repair window.')
        else:
            blockers.append('Primary repo adoption remains measurement-pending after shipped repairs; do not issue a healthy certification artifact yet.')
            watchpoints.append('primary repo adoption remains measurement-pending after shipped repairs')

    if 'measurement_hold_active' in watch_actions:
        if not execution_board_fresh:
            blockers.append(f'Measurement hold is active, but the consolidated execution board is missing or stale ({execution_board_reason}).')
        elif not outcome_board_status_fresh:
            blockers.append(f'Measurement hold is active, but outcome_execution_board_latest.json is stale ({outcome_board_status_reason}).')
        elif not distribution_lane_fresh:
            blockers.append(f'Measurement hold is active, but distribution_lane_latest.json is stale ({distribution_lane_reason}).')
        else:
            watchpoints.append('measurement hold is active, and the current manual/external follow-through queue is consolidated in fresh execution-board artifacts')

    if 'reddit_channel_blocked' in watch_actions:
        if not shipped_system_redesign(audit):
            blockers.append('Reddit is blocked, but no shipped replacement distribution/system redesign is recorded yet.')
        else:
            watchpoints.append('reddit is blocked from this environment, but a replacement distribution execution has already shipped')

    if 'apollo_channel_blocked' in watch_actions:
        if apollo.get('status') not in {'ato_email_verification_required', 'cloudflare_auth_blocked'}:
            blockers.append(f"Apollo blocked watchpoint has an unexpected status: {apollo.get('status')}")
        elif apollo.get('age_hours') is None or apollo.get('age_hours') > 12:
            blockers.append('Apollo blocked watchpoint is stale.')
        else:
            watchpoints.append(f"Apollo outbound is externally blocked ({apollo.get('status')}) with fresh telemetry")

    if 'apollo_monitor_stale' in watch_actions and 'apollo_channel_blocked' not in watch_actions:
        blockers.append('Apollo telemetry is stale without an explicit blocked-channel handoff.')

    return not blockers, blockers, watchpoints


def main() -> int:
    evidence: list[dict] = []
    blockers: list[str] = []
    watchpoints: list[str] = []

    missing = [str(path) for path in (RUNNER, MOMENTUM, AUDIT) if not path.exists()]
    if missing:
        blockers.append('Missing required marketing artifacts: ' + ', '.join(missing))
    runner = load_json(RUNNER) if RUNNER.exists() else {}
    momentum = load_json(MOMENTUM) if MOMENTUM.exists() else {}
    audit = load_json(AUDIT) if AUDIT.exists() else {}

    coherent, newer_peer = runner_is_coherent(RUNNER, [MOMENTUM, AUDIT]) if RUNNER.exists() and MOMENTUM.exists() and AUDIT.exists() else (True, None)
    if not coherent:
        blockers.append(f'Marketing runner bundle is stale relative to {newer_peer}; rerun the full bundle before independent certification.')

    runner_ok = bool(runner.get('ok'))
    if not runner_ok:
        blockers.append('Marketing runner bundle is not healthy.')
    evidence.append({
        'source': str(RUNNER),
        'summary': f"runner ok={runner.get('ok')} generated_at={runner.get('generated_at')}"
    })

    momentum_status = momentum.get('status')
    momentum_actions = momentum.get('actions', []) or []
    momentum_watch_actions = momentum.get('watch_actions', []) or []
    if momentum_status in {'watch'}:
        certifiable_watch, watch_blockers, watchpoint_notes = watch_state_is_certifiable(momentum, audit)
        blockers.extend(watch_blockers)
        watchpoints.extend(watchpoint_notes)
        if not certifiable_watch and not watch_blockers:
            blockers.append('Momentum watchdog entered watch state without a certifiable explanation.')
    elif momentum_status not in {'ok', 'healthy'}:
        blockers.append(f"Momentum watchdog status is {momentum_status} with actions: {', '.join(momentum_actions) if momentum_actions else 'none'}.")
    evidence.append({
        'source': str(MOMENTUM),
        'summary': f"momentum status={momentum_status}; actions={momentum_actions}; watch_actions={momentum_watch_actions}"
    })

    failing_tactics = audit.get('failing_tactics', []) or []
    repair_window_status = audit.get('repair_window_status')
    measurement_pending_reasons = audit.get('measurement_pending_reasons', []) or []
    if repair_window_status == 'needs_repair':
        blockers.append('Workflow audit still reports needs_repair.')
    if 'primary_repo_flat_window' in failing_tactics and 'primary_repo_flat' not in measurement_pending_reasons:
        blockers.append('Workflow audit shows the primary repo adoption goal is still flat in the current window without a live measurement-pending repair state.')
    if 'primary_repo_flat_window' in failing_tactics:
        has_system_design_repair = any((action or {}).get('repair_kind') == 'system_design' for action in (audit.get('repair_actions', []) or []))
        if not has_system_design_repair and not shipped_system_redesign(audit):
            blockers.append('Workflow audit treats flat primary-repo outcomes as tactical-only; missing system-design repair action tied to marketing outcomes.')
    if failing_tactics and not measurement_pending_reasons:
        blockers.append('Workflow audit still has failing tactics without a live measurement-pending repair window.')
    evidence.append({
        'source': str(AUDIT),
        'summary': (
            f"repair_window_status={repair_window_status}; failing_tactics={failing_tactics}; "
            f"measurement_pending_reasons={measurement_pending_reasons}"
        )
    })

    execution_board_fresh, execution_board_reason = execution_board_freshness()
    evidence.append({
        'source': str(EXECUTION_BOARD),
        'summary': f"fresh={execution_board_fresh}; reason={execution_board_reason or 'ok'}"
    })
    if not execution_board_fresh:
        blockers.append(f'marketing_execution_board_latest.md is missing or stale ({execution_board_reason}).')

    outcome_status_fresh, outcome_status_reason = outcome_execution_board_status_is_fresh()
    evidence.append({
        'source': str(OUTCOME_EXECUTION_BOARD_STATUS),
        'summary': f"fresh={outcome_status_fresh}; reason={outcome_status_reason or 'ok'}"
    })
    if not outcome_status_fresh:
        blockers.append(f'outcome_execution_board_latest.json is missing or stale ({outcome_status_reason}).')

    distribution_lane_fresh, distribution_lane_reason = distribution_lane_status_is_fresh()
    evidence.append({
        'source': str(DISTRIBUTION_LANE_STATUS),
        'summary': f"fresh={distribution_lane_fresh}; reason={distribution_lane_reason or 'ok'}"
    })
    if not distribution_lane_fresh:
        blockers.append(f'distribution_lane_latest.json is missing or stale ({distribution_lane_reason}).')

    verdict = 'pass' if not blockers else 'fail'
    if verdict == 'pass':
        if watchpoints:
            summary = 'Independent verifier found the marketing owner loop healthy enough to certify while keeping active watchpoints open.'
        else:
            summary = 'Independent verifier found the marketing owner loop healthy across runner, momentum, and workflow audit surfaces.'
    else:
        summary = 'Independent verifier fails closed because live marketing evidence is still not healthy enough to issue a pass artifact.'
    payload = {
        'checked_at': datetime.now().astimezone().isoformat(),
        'verdict': verdict,
        'summary': summary,
        'evidence': evidence,
        'watchpoints': watchpoints,
        'blockers': blockers,
    }
    OUT.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(payload, indent=2))
    return 0 if verdict == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
