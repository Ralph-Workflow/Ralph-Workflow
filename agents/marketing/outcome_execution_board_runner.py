#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from dataclasses import is_dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path('/home/mistlight/.openclaw/workspace')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.marketing import distribution_lane_selector, marketing_workflow_audit
from agents.marketing.distribution_lane_selector import LaneDecision, choose_distribution_lane
from agents.marketing.distribution_lane_executor import execute_distribution_lane, _write_marketing_execution_board

LOG_DIR = ROOT / 'agents/marketing/logs'
STATUS_JSON = LOG_DIR / 'outcome_execution_board_latest.json'
STATUS_MD = LOG_DIR / 'outcome_execution_board_latest.md'
AUDIT_JSON = LOG_DIR / 'marketing_workflow_audit_latest.json'
POST_HOLD_REENTRY_CONTRACT = ROOT / 'drafts/post_hold_distribution_reentry_latest.md'
LATEST_EXECUTION_BOARD = ROOT / 'drafts/marketing_execution_board_latest.md'
CODEBERG_PRIMARY = 'https://codeberg.org/RalphWorkflow/Ralph-Workflow'
CURATOR_QUEUE_JSON = LOG_DIR / 'curator_outreach_queue_latest.json'

BOARD_LANES = {
    'primary_repo_flat_contact_handoff_packet',
    'manual_outreach_asset_follow_through',
    'distribution_confirmation_follow_through',
    'directory_confirmation',
    'apollo_launch_handoff_packet',
    'stackoverflow_answer_handoff_packet',
    'curator_contact_handoff_packet',
    'curator_handoff_packet',
    'curator_due_followup',
    'comparison_backlink_outreach',
    'apollo_outreach',
    'repo_conversion_proof_asset',
    'reddit_execution_check',
}

ARCHITECTURE_REPAIR_LANES = {
    'distribution_architecture_repair',
    'distribution_architecture_guard_follow_through',
    'distribution_architecture_guard_pause',
}

EXECUTABLE_LANES = BOARD_LANES | ARCHITECTURE_REPAIR_LANES
ACTIVE_REPAIR_WINDOW_STATUSES = {
    'needs_repair',
    'measurement_pending',
}

DISTRIBUTION_ARCHITECTURE_REUSE_ACTION_TYPES = {
    'distribution_architecture_repair',
    'distribution_architecture_churn_guard_repair',
    'distribution_architecture_guard_follow_through',
    'distribution_architecture_guard_pause',
}

DISTRIBUTION_ARCHITECTURE_REUSE_ACTION_TYPE_MAP = {
    'distribution_architecture_repair': {
        'distribution_architecture_repair',
        'distribution_architecture_churn_guard_repair',
    },
    'distribution_architecture_guard_follow_through': {'distribution_architecture_guard_follow_through'},
    'distribution_architecture_guard_pause': {'distribution_architecture_guard_pause'},
}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _recent_marketing_log_payloads() -> list[tuple[Path, dict[str, Any], datetime]]:
    payloads: list[tuple[Path, dict[str, Any], datetime]] = []
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        if not payload:
            continue
        timestamp = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if timestamp is None:
            try:
                timestamp = datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                continue
        payloads.append((path, payload, timestamp))
    payloads.sort(key=lambda item: item[2], reverse=True)
    return payloads


def _distribution_architecture_execution_from_payload(
    *,
    path: Path,
    payload: dict[str, Any],
    timestamp: datetime,
    lane: str,
    action_types: set[str],
    current_fingerprint: str,
    expected_reason: str,
) -> dict[str, Any] | None:
    chosen_action = payload.get('chosen_action') if isinstance(payload.get('chosen_action'), dict) else {}
    verification = payload.get('verification') if isinstance(payload.get('verification'), dict) else {}
    result = payload.get('result') if isinstance(payload.get('result'), dict) else {}
    why_this_action = payload.get('why_this_action') if isinstance(payload.get('why_this_action'), dict) else {}
    selected_lane = str(payload.get('selected_lane') or chosen_action.get('channel') or '').strip()
    selected_action_type = str(payload.get('selected_action_type') or chosen_action.get('type') or '').strip()
    execution = payload.get('execution') if isinstance(payload.get('execution'), dict) else {}
    execution_action_type = str(execution.get('action_type') or '').strip()
    action_type = execution_action_type or selected_action_type
    if action_type not in action_types:
        return None
    if selected_lane and selected_lane != lane:
        return None

    fingerprint = str(
        payload.get('execution_board_fingerprint')
        or verification.get('execution_board_fingerprint')
        or ''
    ).strip()
    if fingerprint and fingerprint != current_fingerprint:
        return None

    reason_hint = str(
        verification.get('guard_reason')
        or why_this_action.get('summary')
        or payload.get('reason')
        or payload.get('summary')
        or ''
    ).strip()
    artifact_path = str(payload.get('artifact_path') or chosen_action.get('draft') or '').strip()
    summary = str(payload.get('summary') or '').strip()
    if execution:
        artifact_path = str(execution.get('artifact_path') or artifact_path).strip()
        summary = str(execution.get('summary') or summary).strip()
    artifact_path = str(result.get('artifact_path') or artifact_path).strip()
    summary = str(result.get('summary') or summary).strip()
    if expected_reason and not fingerprint and expected_reason != reason_hint:
        return None

    return {
        'timestamp': timestamp,
        'log_path': str(path),
        'artifact_path': artifact_path,
        'action_type': action_type,
        'status': str((result.get('status') or execution.get('status') if execution else None) or payload.get('status') or 'executed').strip(),
        'summary': summary,
        'targets_prepared': list(result.get('targets_prepared') or (execution.get('targets_prepared') if execution else None) or payload.get('execution_board_targets') or why_this_action.get('targets_prepared') or []),
        'shared_findings_used': list(why_this_action.get('shared_findings_used') or payload.get('shared_findings_used') or []),
        'live_external_action': bool(result.get('live_external_action') if result.get('live_external_action') is not None else ((execution.get('live_external_action') if execution else None) or False)),
        'blocking_factors': list(result.get('blocking_factors') or (execution.get('blocking_factors') if execution else None) or []),
    }


def _latest_distribution_architecture_execution(lane: str, expected_reason: str = '') -> dict[str, Any] | None:
    action_types = DISTRIBUTION_ARCHITECTURE_REUSE_ACTION_TYPE_MAP.get(lane)
    if not action_types:
        return None

    current_fingerprint = distribution_lane_selector._execution_board_fingerprint()
    latest_match: dict[str, Any] | None = None
    for path, payload, timestamp in _recent_marketing_log_payloads():
        candidate = _distribution_architecture_execution_from_payload(
            path=path,
            payload=payload,
            timestamp=timestamp,
            lane=lane,
            action_types=action_types,
            current_fingerprint=current_fingerprint,
            expected_reason=expected_reason,
        )
        if candidate is not None:
            latest_match = candidate
            break

    if STATUS_JSON.exists():
        payload = _load_json(STATUS_JSON)
        timestamp = _parse_dt(payload.get('timestamp'))
        if timestamp is not None:
            candidate = _distribution_architecture_execution_from_payload(
                path=STATUS_JSON,
                payload=payload,
                timestamp=timestamp,
                lane=lane,
                action_types=action_types,
                current_fingerprint=current_fingerprint,
                expected_reason=expected_reason,
            )
            if candidate is not None and (latest_match is None or candidate['timestamp'] >= latest_match['timestamp']):
                latest_match = candidate

    return latest_match


def _distribution_architecture_execution_is_stale(
    recent_execution: dict[str, Any] | None,
    *,
    lane: str = '',
    now: datetime | None = None,
    short_review_window_release_at: str | None = None,
) -> bool:
    if not recent_execution:
        return False
    artifact_path = str(recent_execution.get('artifact_path') or '').strip()
    if artifact_path and not Path(artifact_path).exists():
        return True
    log_path = str(recent_execution.get('log_path') or '').strip()
    if log_path and not Path(log_path).exists():
        return True

    execution_timestamp = recent_execution.get('timestamp')
    release_at = _parse_dt(short_review_window_release_at)
    if lane == 'distribution_architecture_repair' and now is not None and release_at is not None:
        if not isinstance(execution_timestamp, datetime):
            return True
        artifact_timestamp = execution_timestamp
        if artifact_path:
            try:
                artifact_timestamp = datetime.fromtimestamp(Path(artifact_path).stat().st_mtime)
            except OSError:
                return True
        if artifact_timestamp < release_at <= now:
            return True

    if lane in {'distribution_architecture_guard_follow_through', 'distribution_architecture_guard_pause'} and now is not None:
        if release_at is not None and release_at <= now:
            return True
        short_window_started_at = (
            release_at - timedelta(hours=distribution_lane_selector.SHORT_REVIEW_WINDOW_HOURS)
            if release_at is not None
            else now - timedelta(hours=distribution_lane_selector.SHORT_REVIEW_WINDOW_HOURS)
        )
        if not isinstance(execution_timestamp, datetime) or execution_timestamp < short_window_started_at:
            return True
    return False


def _repair_needs_execution(audit: dict[str, Any]) -> bool:
    for repair in audit.get('repair_actions', []) or []:
        if str(repair.get('failure_type') or '').strip() != 'outcome_system_underpowered':
            continue
        if str(repair.get('repair_state') or '').strip() == 'needs_execution':
            return True
    return False


def _load_active_pending_repairs(audit: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not audit:
        return []
    if audit.get('repair_window_status') not in ACTIVE_REPAIR_WINDOW_STATUSES:
        return []
    return [
        repair for repair in (audit.get('repair_actions', []) or [])
        if repair.get('repair_state') in {'needs_execution', 'pending_measurement'}
    ]


def _apply_repair_mode_overrides(
    decision: Any,
    pending_repairs: list[dict[str, Any]],
) -> Any:
    if not pending_repairs:
        return decision
    skip_directory_submissions = any(
        repair.get('failure_type') == 'same_family_distribution_overlap'
        for repair in pending_repairs
    )
    skip_curator_outreach = any(
        repair.get('failure_type') == 'same_family_outreach_overlap'
        for repair in pending_repairs
    )
    object.__setattr__(decision, 'skip_directory_submissions', skip_directory_submissions)
    object.__setattr__(decision, 'skip_curator_outreach', skip_curator_outreach)
    return decision


def _load_recent_latest_lane_decision(now: datetime) -> Any | None:
    payload = _load_json(distribution_lane_selector.LATEST_JSON)
    if not payload:
        return None
    try:
        modified_at = datetime.fromtimestamp(distribution_lane_selector.LATEST_JSON.stat().st_mtime)
    except OSError:
        return None
    if modified_at.date() != now.date():
        return None
    try:
        return LaneDecision(
            lane=str(payload.get('lane') or '').strip(),
            reason=str(payload.get('reason') or '').strip(),
            reasons=list(payload.get('reasons') or []),
            owned_content_posts_last_36h=int(payload.get('owned_content_posts_last_36h') or 0),
            unsubmitted_directory_channels=list(payload.get('unsubmitted_directory_channels') or []),
            shared_findings_used=list(payload.get('shared_findings_used') or []),
            artifact_path=str(payload.get('artifact_path') or '').strip(),
            short_review_window_release_at=str(payload.get('short_review_window_release_at') or '').strip() or None,
            skip_directory_submissions=bool(payload.get('skip_directory_submissions', False)),
            skip_curator_outreach=bool(payload.get('skip_curator_outreach', False)),
        )
    except Exception:
        return None


def _choose_current_decision(now: datetime, audit: dict[str, Any]) -> Any:
    pending_repairs = _load_active_pending_repairs(audit)
    decision = choose_distribution_lane(
        now=now,
        write_action_log=False,
        persist_latest_artifacts=False,
    )
    decision = _apply_repair_mode_overrides(decision, pending_repairs)
    latest = _load_recent_latest_lane_decision(now)
    if (
        latest is not None
        and latest.lane in ARCHITECTURE_REPAIR_LANES
        and decision.lane == 'measurement_hold'
    ):
        return latest
    return decision


def _contract_short_window_release_at() -> str:
    if not POST_HOLD_REENTRY_CONTRACT.exists():
        return ''
    try:
        text = POST_HOLD_REENTRY_CONTRACT.read_text(encoding='utf-8')
    except OSError:
        return ''
    match = re.search(r'^- Hold release at:\s*(.+)$', text, re.MULTILINE)
    if not match:
        return ''
    release_at = match.group(1).strip()
    return '' if release_at.lower() == 'unknown' else release_at


def _effective_short_window_release_at(decision: Any) -> str:
    release_at = str(getattr(decision, 'short_review_window_release_at', '') or '').strip()
    if release_at:
        return release_at
    return _contract_short_window_release_at()


def _measurement_window_for(lane: str, action_type: str) -> str:
    if lane in {'primary_repo_flat_contact_handoff_packet', 'manual_outreach_asset_follow_through', 'curator_contact_handoff_packet', 'curator_handoff_packet', 'curator_due_followup', 'comparison_backlink_outreach'}:
        return 'Review reply/backlink movement and Codeberg deltas within 7 days.'
    if lane == 'repo_conversion_proof_asset' or 'repo_conversion_proof_asset' in action_type:
        return 'Review repo-visit and conversion movement within 7 days.'
    if lane == 'directory_confirmation' or 'confirmation' in action_type:
        return 'Recheck approval/routing evidence in 3-7 days.'
    if 'apollo' in lane or 'apollo' in action_type:
        return 'Verify live send evidence immediately, then review response/traffic within 7 days.'
    if 'stack' in lane or 'stack' in action_type:
        return 'Review posting/reuse outcome within 72 hours.'
    if lane in ARCHITECTURE_REPAIR_LANES or 'distribution_architecture' in action_type:
        return 'Verify the next runner produces a truthful lane or a changed blocker/fingerprint state.'
    return 'Review Codeberg-linked movement within 7 days.'


def _do_now_lane_available(decision: Any, execution: Any | None) -> bool:
    if execution is None:
        return False
    return str(getattr(decision, 'lane', '') or '').strip() in BOARD_LANES


def _earliest_future_curator_review(now: datetime) -> tuple[datetime, str] | None:
    payload = _load_json(CURATOR_QUEUE_JSON)
    future_dates: list[tuple[datetime, str]] = []
    for row in payload.get('targets', []) or []:
        if not isinstance(row, dict):
            continue
        status = str(row.get('status') or '').strip().lower()
        if status in {'prepared', 'draft'}:
            continue
        review_due = _parse_dt(row.get('review_due_date'))
        target = str(row.get('target') or row.get('name') or '').strip()
        if review_due is None or review_due <= now or not target:
            continue
        future_dates.append((review_due, target))
    if not future_dates:
        return None
    review_due, target = min(future_dates, key=lambda item: item[0])
    return review_due, f'Curator reply/review window matures for {target}.'


def _next_truthful_checkpoint(now: datetime, audit: dict[str, Any], decision: Any) -> dict[str, Any] | None:
    candidates: list[tuple[datetime, str, str]] = []

    short_release_at = _parse_dt(_effective_short_window_release_at(decision))
    if short_release_at is not None and short_release_at > now:
        candidates.append((short_release_at, 'short_review_window_release', 'Current short review window clears.'))

    apollo_status = audit.get('apollo_sequence_status') if isinstance(audit.get('apollo_sequence_status'), dict) else {}
    apollo_next_review_at = _parse_dt(apollo_status.get('next_review_at'))
    if apollo_next_review_at is not None and apollo_next_review_at > now:
        candidates.append((apollo_next_review_at, 'apollo_review_window', 'Apollo launch/reply measurement window reaches its next review checkpoint.'))

    directory_window_getter = getattr(distribution_lane_selector, '_directory_secondary_surface_followup_window', None)
    if callable(directory_window_getter):
        directory_window = directory_window_getter()
        directory_review_at = directory_window.get('review_at') if isinstance(directory_window, dict) else None
        if isinstance(directory_review_at, datetime) and directory_review_at > now:
            candidates.append((directory_review_at, 'directory_secondary_surface_followup', 'Live directory secondary-surface repair reaches its next review checkpoint.'))

    curator_review = _earliest_future_curator_review(now)
    if curator_review is not None:
        review_at, reason = curator_review
        candidates.append((review_at, 'curator_review_due', reason))

    if not candidates:
        return None

    at, source, reason = min(candidates, key=lambda item: item[0])
    return {
        'at': at.isoformat(),
        'source': source,
        'reason': reason,
    }



def _build_payload(
    *,
    now: datetime,
    audit: dict[str, Any],
    decision: Any,
    board_path: Path,
    board_targets: list[str],
    execution: Any | None,
) -> dict[str, Any]:
    selected_lane = decision.lane
    selected_action_type = execution.action_type if execution is not None else 'truth_snapshot_only'
    artifact_path = execution.artifact_path if execution is not None else str(board_path)
    summary = (
        execution.summary if execution is not None else
        'Execution board refreshed, but no truthful do-now lane was available; the board itself is the structural truth source.'
    )
    do_now_lane_available = _do_now_lane_available(decision, execution)

    payload = {
        'timestamp': now.isoformat(),
        'type': 'outcome_execution_board_runner',
        'status': 'executed',
        'repair_needed_at_start': _repair_needs_execution(audit),
        'selected_lane': selected_lane,
        'selected_action_type': selected_action_type,
        'executed_lane': execution.lane if execution is not None else None,
        'do_now_lane_available': do_now_lane_available,
        'artifact_path': artifact_path,
        'execution_board_path': str(board_path),
        'execution_board_targets': board_targets,
        'execution_board_fingerprint': distribution_lane_selector._execution_board_fingerprint(),
        'short_review_window_release_at': _effective_short_window_release_at(decision),
        'codeberg_primary': CODEBERG_PRIMARY,
        'measurement_window': _measurement_window_for(selected_lane, selected_action_type),
        'summary': summary,
        'next_truthful_checkpoint': _next_truthful_checkpoint(now, audit, decision),
        'structural_capability': {
            'name': 'execution_board_follow_through_runner',
            'why_new': 'Creates a dedicated runtime that turns the execution board into the next actionable lane instead of letting system-design repairs stop at packet refreshes or queue housekeeping.',
            'board_lanes': sorted(BOARD_LANES),
            'architecture_repair_lanes': sorted(ARCHITECTURE_REPAIR_LANES),
            'board_target_count': len(board_targets),
        },
        'fake_green_guard': 'This runner only counts if it refreshes the consolidated execution board and either advances one of its executable lanes or explicitly records that no truthful do-now lane exists yet.',
        'shared_findings_used': list(dict.fromkeys(list(getattr(decision, 'shared_findings_used', []) or []) + [
            'marketing_execution_board_latest.md: consolidated do-now assets across all non-Reddit lanes',
            'marketing_workflow_audit_latest.json: outcome_system_underpowered repair source',
        ])),
    }
    if execution is not None:
        payload['execution'] = {
            'lane': execution.lane,
            'action_type': execution.action_type,
            'status': execution.status,
            'targets_prepared': execution.targets_prepared,
            'live_external_action': execution.live_external_action,
            'blocking_factors': execution.blocking_factors or [],
        }
    return payload



def _write_status(payload: dict[str, Any]) -> None:
    STATUS_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    next_checkpoint = payload.get('next_truthful_checkpoint') if isinstance(payload.get('next_truthful_checkpoint'), dict) else None
    lines = [
        '# Outcome Execution Board Runner',
        '',
        f"- Generated: `{payload['timestamp']}`",
        f"- Repair needed at start: `{payload['repair_needed_at_start']}`",
        f"- Execution board: `{payload['execution_board_path']}`",
        f"- Selected lane: `{payload['selected_lane']}`",
        f"- Action type: `{payload['selected_action_type']}`",
        f"- Executed lane: `{payload['executed_lane']}`",
        f"- Truthful do-now lane available: `{payload['do_now_lane_available']}`",
        f"- Artifact: `{payload['artifact_path']}`",
        f"- Codeberg primary CTA: `{CODEBERG_PRIMARY}`",
        f"- Measurement window: {payload['measurement_window']}",
    ]
    if next_checkpoint:
        lines.extend([
            f"- Next truthful checkpoint: `{next_checkpoint['at']}` ({next_checkpoint['source']})",
            f"- Checkpoint reason: {next_checkpoint['reason']}",
        ])
    lines.extend([
        '',
        '## Structural capability added',
        '- Dedicated execution-board runtime that re-checks the consolidated do-now asset list before every system-design follow-through pass.',
        '- Converts the board from a passive markdown artifact into an active runner that can advance current follow-through lanes without waiting for another generic audit/repair cycle.',
        '- Preserves fake-green protection: if the board has no truthful do-now asset, that absence is logged explicitly instead of being masked by queue refreshes.',
        '',
        '## Summary',
        payload['summary'],
    ])
    STATUS_MD.write_text('\n'.join(lines) + '\n', encoding='utf-8')



def _with_short_window_release(decision: Any, short_window_release_at: str) -> Any:
    if is_dataclass(decision):
        return replace(decision, short_review_window_release_at=short_window_release_at)
    return LaneDecision(
        lane=str(getattr(decision, 'lane', '') or ''),
        reason=str(getattr(decision, 'reason', '') or ''),
        reasons=list(getattr(decision, 'reasons', []) or []),
        owned_content_posts_last_36h=int(getattr(decision, 'owned_content_posts_last_36h', 0) or 0),
        unsubmitted_directory_channels=list(getattr(decision, 'unsubmitted_directory_channels', []) or []),
        shared_findings_used=list(getattr(decision, 'shared_findings_used', []) or []),
        artifact_path=str(getattr(decision, 'artifact_path', '') or ''),
        short_review_window_release_at=short_window_release_at,
        skip_directory_submissions=bool(getattr(decision, 'skip_directory_submissions', False)),
        skip_curator_outreach=bool(getattr(decision, 'skip_curator_outreach', False)),
    )


POST_RELEASE_DUPLICATE_REPAIR_PAUSE_WINDOW = timedelta(hours=1)


def _guard_pause_for_duplicate_same_fingerprint_repair(refreshed: Any, short_window_release_at: str) -> Any:
    reason = (
        'Pause duplicate same-fingerprint distribution-architecture repair after the cleared short-window slot '
        'already ran once; wait for a changed fingerprint, blocker set, or fresh truthful lane before rerunning it.'
    )
    reasons = list(getattr(refreshed, 'reasons', []) or [])
    reasons.insert(0, 'Duplicate same-fingerprint post-release repair already executed in the current slot.')
    if is_dataclass(refreshed):
        return replace(
            refreshed,
            lane='distribution_architecture_guard_pause',
            reason=reason,
            reasons=reasons,
            short_review_window_release_at=short_window_release_at,
        )
    return LaneDecision(
        lane='distribution_architecture_guard_pause',
        reason=reason,
        reasons=reasons,
        owned_content_posts_last_36h=int(getattr(refreshed, 'owned_content_posts_last_36h', 0) or 0),
        unsubmitted_directory_channels=list(getattr(refreshed, 'unsubmitted_directory_channels', []) or []),
        shared_findings_used=list(getattr(refreshed, 'shared_findings_used', []) or []),
        artifact_path=str(getattr(refreshed, 'artifact_path', '') or ''),
        short_review_window_release_at=short_window_release_at,
        skip_directory_submissions=bool(getattr(refreshed, 'skip_directory_submissions', False)),
        skip_curator_outreach=bool(getattr(refreshed, 'skip_curator_outreach', False)),
    )


def _persist_latest_lane_after_execution(now: datetime, decision: Any, execution: Any | None) -> Any:
    selected_release = _effective_short_window_release_at(decision)
    execution_action_type = str(getattr(execution, 'action_type', '') or '').strip() if execution is not None else ''

    if execution_action_type in {
        'measurement_hold_execution',
        'measurement_hold_follow_through',
        'distribution_architecture_guard_follow_through',
        'distribution_architecture_guard_pause',
    }:
        latest = decision
        if selected_release and not str(getattr(latest, 'short_review_window_release_at', '') or '').strip():
            latest = _with_short_window_release(latest, selected_release)
        distribution_lane_selector.persist_latest_lane_decision(
            latest,
            now,
            write_action_log=False,
        )
        return latest

    refreshed = choose_distribution_lane(now=now, write_action_log=False, persist_latest_artifacts=False)
    refreshed_release = str(getattr(refreshed, 'short_review_window_release_at', '') or '').strip()
    refreshed_lane = str(getattr(refreshed, 'lane', '') or '').strip()

    latest = refreshed
    if (
        selected_release
        and execution_action_type in DISTRIBUTION_ARCHITECTURE_REUSE_ACTION_TYPES
    ):
        release_at = _parse_dt(selected_release)
        release_recently_cleared = bool(
            release_at is not None
            and now >= release_at
            and now - release_at <= POST_RELEASE_DUPLICATE_REPAIR_PAUSE_WINDOW
        )
        if release_recently_cleared and refreshed_lane == 'distribution_architecture_repair':
            latest = _guard_pause_for_duplicate_same_fingerprint_repair(refreshed, selected_release)
        elif not refreshed_release and refreshed_lane in ARCHITECTURE_REPAIR_LANES:
            latest = _with_short_window_release(refreshed, selected_release)
        elif refreshed_lane == 'owned_content':
            latest = decision

    distribution_lane_selector.persist_latest_lane_decision(
        latest,
        now,
        write_action_log=False,
    )
    return latest


def _sync_latest_execution_board_alias(board_path: Path) -> None:
    try:
        content = board_path.read_text(encoding='utf-8')
    except OSError:
        return
    try:
        LATEST_EXECUTION_BOARD.write_text(content, encoding='utf-8')
    except OSError:
        return



def _persist_latest_lane_and_refresh_board(
    now: datetime,
    decision: Any,
    execution: Any | None,
) -> tuple[Any, Path, list[str]]:
    latest_lane = _persist_latest_lane_after_execution(now, decision, execution)
    board_path, board_targets = _write_marketing_execution_board(now)
    _sync_latest_execution_board_alias(board_path)
    return latest_lane, board_path, board_targets


def sync_from_execution(
    *,
    now: datetime,
    audit: dict[str, Any],
    decision: Any,
    board_path: Path,
    board_targets: list[str],
    execution: Any,
) -> dict[str, Any]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _latest_lane, board_path, board_targets = _persist_latest_lane_and_refresh_board(now, decision, execution)
    payload = _build_payload(
        now=now,
        audit=audit,
        decision=decision,
        board_path=board_path,
        board_targets=board_targets,
        execution=execution,
    )
    _write_status(payload)
    return payload



def sync_latest_truth_snapshot(
    *,
    now: datetime | None = None,
    audit: dict[str, Any] | None = None,
    decision: Any | None = None,
    board_path: Path | None = None,
    board_targets: list[str] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now().astimezone().replace(tzinfo=None)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    current_audit = audit if audit is not None else _load_json(AUDIT_JSON)
    current_decision = decision if decision is not None else _choose_current_decision(now, current_audit)
    distribution_lane_selector.persist_latest_lane_decision(
        current_decision,
        now,
        write_action_log=False,
    )
    current_board_path: Path
    current_board_targets: list[str]
    if board_path is None or board_targets is None:
        current_board_path, current_board_targets = _write_marketing_execution_board(now)
    else:
        current_board_path, current_board_targets = board_path, board_targets
    _sync_latest_execution_board_alias(current_board_path)

    payload = _build_payload(
        now=now,
        audit=current_audit,
        decision=current_decision,
        board_path=current_board_path,
        board_targets=current_board_targets,
        execution=None,
    )
    _write_status(payload)
    return payload



def run(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now().astimezone().replace(tzinfo=None)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    audit = _load_json(AUDIT_JSON)
    board_path, board_targets = _write_marketing_execution_board(now)
    decision = _choose_current_decision(now, audit)

    execution = None
    if decision.lane in EXECUTABLE_LANES:
        reused_execution = None
        if decision.lane in ARCHITECTURE_REPAIR_LANES:
            reused_execution = _latest_distribution_architecture_execution(
                decision.lane,
                expected_reason=decision.reason,
            )
        effective_release_at = _effective_short_window_release_at(decision)
        if reused_execution is not None and not _distribution_architecture_execution_is_stale(
            reused_execution,
            lane=decision.lane,
            now=now,
            short_review_window_release_at=effective_release_at,
        ):
            execution = SimpleNamespace(
                lane=decision.lane,
                action_type=reused_execution.get('action_type', decision.lane),
                status=reused_execution.get('status', 'executed'),
                artifact_path=reused_execution.get('artifact_path', ''),
                summary=reused_execution.get('summary', 'Reused existing distribution-architecture execution.'),
                targets_prepared=list(reused_execution.get('targets_prepared') or []),
                shared_findings_used=list(reused_execution.get('shared_findings_used') or []),
                live_external_action=bool(reused_execution.get('live_external_action', False)),
                blocking_factors=list(reused_execution.get('blocking_factors') or []),
            )
        else:
            execution = execute_distribution_lane(decision, now=now)

    _latest_lane, board_path, board_targets = _persist_latest_lane_and_refresh_board(now, decision, execution)

    payload = _build_payload(
        now=now,
        audit=audit,
        decision=decision,
        board_path=board_path,
        board_targets=board_targets,
        execution=execution,
    )
    _write_status(payload)
    return payload


def main() -> int:
    payload = run()
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
