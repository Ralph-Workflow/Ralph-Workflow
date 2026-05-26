#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import urllib.parse
from dataclasses import dataclass, is_dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path('/home/mistlight/.openclaw/workspace')
AGENTS_DIR = ROOT / 'agents/marketing'
LOG_DIR = AGENTS_DIR / 'logs'
DRAFTS_DIR = ROOT / 'drafts'
EXECUTION_BOARD_LATEST_PATH = DRAFTS_DIR / 'marketing_execution_board_latest.md'
ADOPTION_PATH = LOG_DIR / 'adoption_metrics_latest.json'
CHANNEL_DISCOVERY_PATH = LOG_DIR / 'channel_discovery.json'
OUTREACH_LOG_PATH = ROOT / 'outreach-log.md'
MARKET_INTELLIGENCE_PATH = LOG_DIR / 'market_intelligence_latest.json'
LATEST_JSON = LOG_DIR / 'distribution_lane_latest.json'
LATEST_MD = LOG_DIR / 'distribution_lane_latest.md'
REDDIT_MONITOR_LATEST = ROOT / 'seo-reports/reddit_monitor_latest.md'
AUDIT_LATEST_JSON = LOG_DIR / 'marketing_workflow_audit_latest.json'
CURATOR_QUEUE_LATEST_PATH = LOG_DIR / 'curator_outreach_queue_latest.json'
COMPARISON_QUEUE_LATEST_PATH = LOG_DIR / 'comparison_backlink_queue_latest.json'
DISTRIBUTION_RESET_QUEUE_LATEST_PATH = LOG_DIR / 'distribution_reset_targets_latest.json'
DISTRIBUTION_RESET_LOG_PATH = LOG_DIR / 'distribution_reset_execution_log.md'
APOLLO_STATUS_PATH = LOG_DIR / 'apollo_status.json'
APOLLO_SEQUENCE_STATUS_PATH = LOG_DIR / 'apollo_sequence_status_latest.json'
REDDIT_EXECUTION_STATUS_PATH = LOG_DIR / 'reddit_execution_status_latest.json'
CURATOR_CONTACT_DISCOVERY_LATEST_PATH = LOG_DIR / 'curator_contact_discovery_latest.json'
CURATOR_HANDOFF_LATEST_PATH = DRAFTS_DIR / 'curator_handoff_packet_latest.md'
CURATOR_CONTACT_HANDOFF_LATEST_PATH = DRAFTS_DIR / 'curator_contact_handoff_packet_latest.md'
PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH = LOG_DIR / 'primary_repo_flat_contact_discovery_latest.json'
PRIMARY_REPO_FLAT_CONTACT_HANDOFF_LATEST_PATH = DRAFTS_DIR / 'primary_repo_flat_contact_handoff_packet_latest.md'
PENDING_CONFIRMATION_HANDOFF_LATEST_PATH = DRAFTS_DIR / 'distribution_confirmation_follow_through_latest.md'
STACKOVERFLOW_LATEST_PATH = LOG_DIR / 'stackoverflow_answer_lane_latest.json'
STACKOVERFLOW_HANDOFF_LATEST_PATH = DRAFTS_DIR / 'stackoverflow_answer_handoff_packet_latest.md'
DIRECTORY_CONFIRMATION_EXECUTION_LATEST_PATH = DRAFTS_DIR / 'directory_confirmation_execution_latest.md'
BACKLINK_STATUS_LATEST_PATH = LOG_DIR / 'backlink_status_latest.json'
LOW_SIGNAL_APOLLO_MARKERS = {
    'record count was 0',
    '0 right after creation',
    '0 records',
    'zero records',
    'needs a second-pass check',
    'needs a second pass check',
    'import path likely needs a second-pass check',
}

LIVE_EXTERNAL_STATUSES = {
    'executed',
    'sent',
    'submitted',
    'published',
    'launched',
}

RECENT_PROOF_ASSET_ACTION_TYPES = {
    'repo_conversion_proof_asset',
    'repo_conversion_proof_asset_reuse',
    'repo_conversion_docs_push',
    'repo_conversion_quickstart_patch',
}

RECENT_RESET_ACTION_TYPES = {
    'distribution_reset_execution',
}

RECENT_DIRECTORY_CONFIRMATION_ACTION_TYPES = {
    'directory_confirmation_execution',
    'saashub_secondary_surface_execution',
    'saashub_secondary_surface_comment_execution',
    'saashub_secondary_surface_comment_confirmation',
    'saashub_repo_routing_execution',
}

MEASUREMENT_HOLD_REENTRY_REPAIR_ACTION_TYPES = {
    'active_loop_prompt_repair',
    'post_hold_reentry_contract_repair',
}

MEASUREMENT_HOLD_GLOBAL_REPAIR_BRIDGE_ACTION_TYPES = {
    'measurement_hold_third_strike_guard_repair',
}

DISTRIBUTION_ARCHITECTURE_GUARD_PAUSE_ESCALATION_THRESHOLD = 2
PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_THRESHOLD = 2
PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_WINDOW_HOURS = 48

DISTRIBUTION_ARCHITECTURE_REPAIR_ACTION_TYPES = {
    'distribution_architecture_repair',
    'distribution_architecture_churn_guard_repair',
}

DISTRIBUTION_ARCHITECTURE_GUARD_FOLLOW_THROUGH_ACTION_TYPES = {
    'distribution_architecture_guard_follow_through',
}

PUBLISHER_CONTACT_ACTION_TYPES = {
    'publisher_email_outreach',
    'publisher_contact_form_submission',
    'publisher_feedback_form_submission',
}

PRIMARY_REPO_FLAT_MANUAL_DELIVERY_ACTION_TYPES = {
    'primary_repo_flat_contact_manual_delivery',
    'primary_repo_flat_contact_manual_delivery_refresh',
}

MANUAL_EXECUTABLE_PUBLISHER_CHANNEL_TYPES = {
    'email',
    'telegram',
}

RUNTIME_SENDABLE_PUBLISHER_CHANNEL_TYPES = {
    'email',
}

CURATOR_MEASUREMENT_WINDOW_STATUSES = {
    'sent',
    'sent_via_email_fallback',
    'sent_via_form',
    'sent_via_github_issue',
    'sent_via_manual_handoff',
    'awaiting_reply',
    'waiting_review',
    'email_invalid_manual_handoff_remaining',
}

MANUAL_CONTACT_HANDOFF_REMAINING_STATUSES = {
    'email_invalid_manual_handoff_remaining',
    'manual_handoff_remaining',
}

CURATOR_MEASUREMENT_WINDOW_SATURATION = 5
DIRECTORY_MEASUREMENT_WINDOW_HOURS = 24
DIRECTORY_SUBMISSION_BURST_THRESHOLD = 4
STACKOVERFLOW_POST_COOLDOWN_GRACE = timedelta(minutes=3)
ACTIVE_REPAIR_WINDOW_STATUSES = {'needs_repair', 'measurement_pending'}
SHORT_REVIEW_WINDOW_HOURS = 6
SHORT_REVIEW_WINDOW_EXTERNAL_ACTION_THRESHOLD = 2
STACKOVERFLOW_POST_COOLDOWN_ACTION_TYPES = {
    'stackoverflow_post_cooldown_cron',
    'stack_overflow_demand_capture_cron',
}

MANUAL_OUTREACH_ASSET_ACTION_SUFFIX = '_channel_ready_outreach_asset'
MANUAL_OUTREACH_DELIVERY_ACTION_SUFFIX = '_manual_delivery'
MANUAL_OUTREACH_DELIVERY_CHANNELS = {
    'current_chat_manual_handoff',
    'current_chat',
}


def _curator_queue_path() -> Path:
    if CURATOR_QUEUE_LATEST_PATH.parent == LOG_DIR:
        return CURATOR_QUEUE_LATEST_PATH
    return LOG_DIR / 'curator_outreach_queue_latest.json'


def _curator_contact_discovery_path() -> Path:
    if CURATOR_CONTACT_DISCOVERY_LATEST_PATH.parent == LOG_DIR:
        return CURATOR_CONTACT_DISCOVERY_LATEST_PATH
    return LOG_DIR / 'curator_contact_discovery_latest.json'


def _curator_handoff_path() -> Path:
    if CURATOR_HANDOFF_LATEST_PATH.parent == DRAFTS_DIR:
        return CURATOR_HANDOFF_LATEST_PATH
    return DRAFTS_DIR / 'curator_handoff_packet_latest.md'


def _curator_contact_handoff_path() -> Path:
    if CURATOR_CONTACT_HANDOFF_LATEST_PATH.parent == DRAFTS_DIR:
        return CURATOR_CONTACT_HANDOFF_LATEST_PATH
    return DRAFTS_DIR / 'curator_contact_handoff_packet_latest.md'


def _primary_repo_flat_contact_discovery_path() -> Path:
    if PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH.parent == LOG_DIR:
        return PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH
    return LOG_DIR / 'primary_repo_flat_contact_discovery_latest.json'


def _primary_repo_flat_contact_handoff_path() -> Path:
    if PRIMARY_REPO_FLAT_CONTACT_HANDOFF_LATEST_PATH.parent == DRAFTS_DIR:
        return PRIMARY_REPO_FLAT_CONTACT_HANDOFF_LATEST_PATH
    return DRAFTS_DIR / 'primary_repo_flat_contact_handoff_packet_latest.md'


def _distribution_reset_queue_path() -> Path:
    if DISTRIBUTION_RESET_QUEUE_LATEST_PATH.parent == LOG_DIR:
        return DISTRIBUTION_RESET_QUEUE_LATEST_PATH
    return LOG_DIR / 'distribution_reset_targets_latest.json'


def _distribution_reset_log_path() -> Path:
    if DISTRIBUTION_RESET_LOG_PATH.parent == LOG_DIR:
        return DISTRIBUTION_RESET_LOG_PATH
    return LOG_DIR / 'distribution_reset_execution_log.md'


def _backlink_status_latest_path() -> Path:
    if BACKLINK_STATUS_LATEST_PATH.parent == LOG_DIR:
        return BACKLINK_STATUS_LATEST_PATH
    return LOG_DIR / 'backlink_status_latest.json'


def _directory_confirmation_execution_latest_path() -> Path:
    if DIRECTORY_CONFIRMATION_EXECUTION_LATEST_PATH.parent == DRAFTS_DIR:
        return DIRECTORY_CONFIRMATION_EXECUTION_LATEST_PATH
    return DRAFTS_DIR / 'directory_confirmation_execution_latest.md'


def _pending_confirmation_handoff_path() -> Path:
    if PENDING_CONFIRMATION_HANDOFF_LATEST_PATH.parent == DRAFTS_DIR:
        return PENDING_CONFIRMATION_HANDOFF_LATEST_PATH
    return DRAFTS_DIR / 'distribution_confirmation_follow_through_latest.md'


def _comparison_queue_path() -> Path:
    if COMPARISON_QUEUE_LATEST_PATH.parent == LOG_DIR:
        return COMPARISON_QUEUE_LATEST_PATH
    return LOG_DIR / 'comparison_backlink_queue_latest.json'


def _apollo_status_path() -> Path:
    if APOLLO_STATUS_PATH.parent == LOG_DIR:
        return APOLLO_STATUS_PATH
    return LOG_DIR / 'apollo_status.json'


def _apollo_sequence_status_path() -> Path:
    if APOLLO_SEQUENCE_STATUS_PATH.parent == LOG_DIR:
        return APOLLO_SEQUENCE_STATUS_PATH
    return LOG_DIR / 'apollo_sequence_status_latest.json'


def _stack_overflow_latest_path() -> Path:
    if STACKOVERFLOW_LATEST_PATH.parent == LOG_DIR:
        return STACKOVERFLOW_LATEST_PATH
    return LOG_DIR / 'stackoverflow_answer_lane_latest.json'


def _audit_latest_json_path() -> Path:
    if AUDIT_LATEST_JSON.parent == LOG_DIR:
        return AUDIT_LATEST_JSON
    return LOG_DIR / 'marketing_workflow_audit_latest.json'


def _root_dir() -> Path:
    if LOG_DIR.name == 'logs' and LOG_DIR.parent.name == 'marketing' and LOG_DIR.parent.parent.name == 'agents':
        return LOG_DIR.parent.parent.parent
    return LOG_DIR.parent if LOG_DIR.name == 'logs' else ROOT


def _reddit_monitor_latest_path() -> Path:
    default_path = ROOT / 'seo-reports' / 'reddit_monitor_latest.md'
    if REDDIT_MONITOR_LATEST != default_path:
        return REDDIT_MONITOR_LATEST
    return _root_dir() / 'seo-reports' / 'reddit_monitor_latest.md'


def _reddit_execution_status_path() -> Path:
    if REDDIT_EXECUTION_STATUS_PATH.parent == LOG_DIR:
        return REDDIT_EXECUTION_STATUS_PATH
    return LOG_DIR / 'reddit_execution_status_latest.json'


def _execution_board_latest_path() -> Path:
    if EXECUTION_BOARD_LATEST_PATH.parent == DRAFTS_DIR:
        return EXECUTION_BOARD_LATEST_PATH
    return DRAFTS_DIR / 'marketing_execution_board_latest.md'


def _manual_outreach_delivery_payload(payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any], str]:
    chosen_action = _chosen_action_dict(payload)
    action_type = _chosen_action_type(payload)
    channel = str(chosen_action.get('channel') or payload.get('channel') or '').strip().lower()
    result = payload.get('result') if isinstance(payload.get('result'), dict) else {}
    is_delivery = (
        action_type == 'manual_outreach_asset_follow_through'
        or action_type.endswith(MANUAL_OUTREACH_DELIVERY_ACTION_SUFFIX)
        or channel in MANUAL_OUTREACH_DELIVERY_CHANNELS
    )
    return is_delivery, action_type, result, channel


def _pending_confirmation_actions(now: datetime, *, days: int = 7) -> list[dict[str, Any]]:
    cutoff = now - timedelta(days=days)
    actions: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        result = payload.get('result') if isinstance(payload.get('result'), dict) else {}
        status = str(payload.get('status') or result.get('status') or '').strip().lower()
        if not (bool(result.get('confirmation_required')) or 'pending_email_confirmation' in status):
            continue
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue
        chosen_action = _chosen_action_dict(payload)
        title = str(
            chosen_action.get('title')
            or payload.get('title')
            or payload.get('target')
            or _chosen_action_type(payload)
            or path.stem
        ).strip()
        url = str(
            chosen_action.get('target_url')
            or payload.get('submit_url')
            or payload.get('url')
            or chosen_action.get('url')
            or ''
        ).strip()
        key = (title.lower(), url.lower(), status)
        if key in seen:
            continue
        seen.add(key)
        actions.append({
            'title': title,
            'url': url,
            'status': status,
            'confirmation_channel': str(result.get('confirmation_channel') or '').strip(),
            'platform_response': str(result.get('platform_response') or '').strip(),
            'path': str(path),
            'timestamp': dt,
        })
    actions.sort(key=lambda item: item['timestamp'], reverse=True)
    return actions


def _pending_confirmation_handoff_packet_current(now: datetime, expected_targets: list[str]) -> bool:
    handoff_path = _pending_confirmation_handoff_path()
    if not _handoff_packet_is_current(handoff_path, expected_targets, allow_superset=True):
        return False
    age = now - datetime.fromtimestamp(handoff_path.stat().st_mtime)
    return age <= timedelta(days=7)


def _manual_outreach_asset_delivery_still_active(*, artifact_path: str, now: datetime, respect_artifact_refresh: bool = True) -> bool:
    artifact = str(artifact_path or '').strip()
    if not artifact:
        return False
    artifact_file = Path(artifact)
    artifact_mtime: datetime | None = None
    if artifact_file.exists():
        artifact_mtime = datetime.fromtimestamp(artifact_file.stat().st_mtime)
    cutoff = now - timedelta(days=14)
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        is_delivery, _action_type, result, _channel = _manual_outreach_delivery_payload(payload)
        status = str(payload.get('status') or result.get('status') or '').strip().lower()
        if not is_delivery:
            continue
        chosen_action = _chosen_action_dict(payload)
        delivered_artifact = str(
            chosen_action.get('artifact')
            or chosen_action.get('draft')
            or chosen_action.get('packet')
            or result.get('artifact')
            or result.get('artifact_reused')
            or ''
        ).strip()
        if delivered_artifact != artifact:
            continue
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue
        if respect_artifact_refresh and artifact_mtime is not None and artifact_mtime > dt:
            continue
        measurement_window = payload.get('measurement_window') if isinstance(payload.get('measurement_window'), dict) else {}
        next_review_at = _parse_dt(
            str(
                result.get('next_review_at')
                or measurement_window.get('review_at')
                or measurement_window.get('freshness_review_at')
                or ''
            ).strip()
        )
        if next_review_at is not None and next_review_at >= now:
            return True
        if status == 'delivered_to_current_chat' and dt.date() == now.date():
            return True
    return False


def _is_manual_community_discussion_asset(*, action_type: str, title: str, artifact_path: str, summary: str = '') -> bool:
    structured_fields = [
        action_type.strip().lower(),
        title.strip().lower(),
        artifact_path.strip().lower(),
    ]
    if any(
        token in field
        for field in structured_fields
        for token in ('reddit', 'discussion handoff', 'community discussion', 'reddit_discussion')
    ):
        return True

    summary_text = summary.strip().lower()
    return any(token in summary_text for token in (
        'reddit discussion',
        'discussion handoff',
        'community discussion',
        'reddit_discussion',
    ))



def _manual_outreach_assets_waiting_for_execution(now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now()
    assets: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    recent_publisher_targets = set(_recent_contact_targets(
        now,
        action_types=PUBLISHER_CONTACT_ACTION_TYPES,
        days=7,
    )) | _recent_curator_queue_contact_targets(now, days=7)
    active_manual_targets = _active_manual_outreach_delivery_targets(now)
    primary_repo_flat_targets = [
        target for target in _primary_repo_flat_contact_targets_waiting_for_execution()
        if target not in recent_publisher_targets and target not in active_manual_targets
    ]
    primary_repo_flat_packet_path = _primary_repo_flat_contact_handoff_path()
    primary_repo_flat_packet_current = False
    if primary_repo_flat_targets and primary_repo_flat_packet_path.exists():
        packet_age = now - datetime.fromtimestamp(primary_repo_flat_packet_path.stat().st_mtime)
        primary_repo_flat_packet_current = (
            packet_age <= timedelta(days=7)
            and _handoff_packet_is_current(
                primary_repo_flat_packet_path,
                primary_repo_flat_targets,
                require_live_listing_proof=True,
                allow_superset=True,
            )
        )
    if (
        primary_repo_flat_packet_current
        and not _primary_repo_flat_packet_delivery_still_active(now, primary_repo_flat_targets)
    ):
        packet_path = str(primary_repo_flat_packet_path)
        seen_paths.add(packet_path)
        assets.append({
            'target': ', '.join(primary_repo_flat_targets[:3]),
            'targets': primary_repo_flat_targets,
            'artifact_path': packet_path,
            'title': 'Primary-repo-flat publisher contact packet',
        })

    cutoff = now - timedelta(days=14)
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        chosen_action = _chosen_action_dict(payload)
        action_type = _chosen_action_type(payload)
        channel = str(chosen_action.get('channel') or payload.get('channel') or '').strip().lower()
        if not (
            action_type.endswith(MANUAL_OUTREACH_ASSET_ACTION_SUFFIX)
            or channel == 'manual_contact_asset'
        ):
            continue
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue
        result = payload.get('result') if isinstance(payload.get('result'), dict) else {}
        artifact_path = str(
            chosen_action.get('artifact')
            or chosen_action.get('draft')
            or result.get('artifact')
            or ''
        ).strip()
        if not artifact_path or artifact_path in seen_paths or not Path(artifact_path).exists():
            continue
        if Path(artifact_path).name == 'primary_repo_flat_contact_handoff_packet_latest.md':
            continue
        if _manual_outreach_asset_delivery_still_active(
            artifact_path=artifact_path,
            now=now,
            respect_artifact_refresh=False,
        ):
            continue
        measurement_window = payload.get('measurement_window') if isinstance(payload.get('measurement_window'), dict) else {}
        review_at = _parse_dt(str(measurement_window.get('review_at') or '').strip())
        if review_at is not None and review_at < now:
            continue
        title = str(chosen_action.get('title') or payload.get('title') or '').strip()
        summary = str(((payload.get('why_this_action') or {}).get('summary') if isinstance(payload.get('why_this_action'), dict) else '') or payload.get('expected_outcome') or '').strip()
        if _is_manual_community_discussion_asset(
            action_type=action_type,
            title=title,
            artifact_path=artifact_path,
            summary=summary,
        ):
            continue
        prepared_targets = ((payload.get('why_this_action') or {}).get('targets_prepared') or []) if isinstance(payload.get('why_this_action'), dict) else []
        fallback_target = str(prepared_targets[0]).strip() if prepared_targets else ''
        target = title.partition(' for ')[2].strip() if ' for ' in title else fallback_target or Path(artifact_path).stem.replace('_', ' ')
        seen_paths.add(artifact_path)
        assets.append({
            'target': target,
            'targets': [target] if target else [],
            'artifact_path': artifact_path,
            'title': title,
        })
    return assets


def _active_manual_outreach_delivery_targets(now: datetime) -> set[str]:
    targets: set[str] = set()
    cutoff = now - timedelta(days=14)
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        is_delivery, _action_type, result, _channel = _manual_outreach_delivery_payload(payload)
        if not is_delivery:
            continue
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue
        status = str(payload.get('status') or result.get('status') or '').strip().lower()
        next_review_at = _parse_dt(str(result.get('next_review_at') or '').strip())
        if next_review_at is None and not (status == 'delivered_to_current_chat' and dt.date() == now.date()):
            continue
        if next_review_at is not None and next_review_at < now:
            continue
        chosen_action = _chosen_action_dict(payload)
        raw_targets = ((payload.get('why_this_action') or {}).get('targets_prepared') or [])
        for item in raw_targets:
            target = _display_target_name(str(item).strip())
            if target:
                targets.add(target)
        fallback_target = _display_target_name(str(chosen_action.get('target') or payload.get('target') or '').strip())
        if fallback_target:
            targets.add(fallback_target)
    return targets


def _execution_board_has_no_truthful_do_now_packet(now: datetime | None = None) -> bool:
    path = _execution_board_latest_path()
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return False
    explicit_empty_marker = any(
        marker in text
        for marker in (
            'No do-now handoff packet is currently truthful in this review window.',
            'No truthful do-now packet remains on this board right now.',
            '- None in the current short-window hold.',
        )
    )

    lines = text.splitlines()
    in_waiting_section = False
    current_block: list[str] = []
    blocks: list[list[str]] = []
    for line in lines:
        if line.startswith('## '):
            if 'Best executable assets still waiting' in line:
                in_waiting_section = True
                current_block = []
                continue
            if in_waiting_section:
                break
        if not in_waiting_section:
            continue
        if line.startswith('### '):
            if current_block:
                blocks.append(current_block)
            current_block = [line]
            continue
        if current_block:
            current_block.append(line)
    if current_block:
        blocks.append(current_block)
    def _block_is_post_hold_only(block: list[str]) -> bool:
        joined = '\n'.join(block).lower()
        return (
            'when: after short-window congestion clears' in joined
            or 'hold manual delivery until that congestion clears' in joined
        )

    board_is_post_hold_only = bool(blocks) and all(_block_is_post_hold_only(block) for block in blocks)
    if board_is_post_hold_only:
        return True

    if _manual_outreach_assets_waiting_for_execution(now):
        return False
    if _pending_confirmation_actions(now or datetime.now()):
        return False

    return explicit_empty_marker


def _execution_board_requests_primary_repo_flat_refresh() -> bool:
    path = _execution_board_latest_path()
    try:
        text = path.read_text(encoding='utf-8').lower()
    except OSError:
        return False
    return (
        'primary-repo-flat publisher discovery has changed, but the canonical handoff packet is stale' in text
        or 'refresh it from the latest target set instead of treating the old packet as do-now' in text
    )


def _normalized_execution_board_text(text: str) -> str:
    if not text:
        return ''
    normalized_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('Generated:'):
            continue
        normalized_lines.append(line.rstrip())
    return '\n'.join(normalized_lines).strip()


def _execution_board_fingerprint() -> str:
    path = _execution_board_latest_path()
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return ''
    normalized = _normalized_execution_board_text(text)
    return hashlib.sha1(normalized.encode('utf-8')).hexdigest() if normalized else ''


def _execution_board_short_review_release_at() -> datetime | None:
    path = _execution_board_latest_path()
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return None
    marker = 'Short review-window congestion clears at:'
    for line in text.splitlines():
        if marker not in line:
            continue
        raw = line.split(marker, 1)[1].strip().lstrip('-').strip()
        if not raw:
            return None
        return _parse_dt(raw)
    return None


def _distribution_architecture_repair_state(
    now: datetime,
    *,
    release_at: datetime | None,
) -> dict[str, Any]:
    fingerprint = _execution_board_fingerprint()
    short_window_started_at = (
        release_at - timedelta(hours=SHORT_REVIEW_WINDOW_HOURS)
        if release_at is not None
        else now - timedelta(hours=SHORT_REVIEW_WINDOW_HOURS)
    )
    fallback_started_at = now - timedelta(days=7)

    def _collect(cutoff: datetime) -> dict[str, Any]:
        matching_logs: list[str] = []
        guard_logs: list[str] = []
        guard_follow_through_logs: list[str] = []
        guard_pause_logs: list[str] = []
        latest_matching_at: datetime | None = None
        latest_guard_follow_through_at: datetime | None = None
        latest_guard_pause_at: datetime | None = None
        earliest_guard_pause_at: datetime | None = None
        for path in LOG_DIR.glob('marketing_*.json'):
            if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
                continue
            payload = _load_json(path)
            action_type = _chosen_action_type(payload)
            chosen_action = _chosen_action_dict(payload)
            action_channel = str(chosen_action.get('channel') or payload.get('channel') or '').strip()
            is_guard_pause = action_type == 'distribution_architecture_guard_pause' or action_channel == 'distribution_architecture_guard_pause'
            if action_type not in DISTRIBUTION_ARCHITECTURE_REPAIR_ACTION_TYPES | DISTRIBUTION_ARCHITECTURE_GUARD_FOLLOW_THROUGH_ACTION_TYPES and not is_guard_pause:
                continue
            dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
            if dt is None:
                dt = datetime.fromtimestamp(path.stat().st_mtime)
            if dt < cutoff or dt > now:
                continue
            verification = payload.get('verification') if isinstance(payload.get('verification'), dict) else {}
            logged_fingerprint = str(verification.get('execution_board_fingerprint') or '').strip()
            if fingerprint and logged_fingerprint and logged_fingerprint != fingerprint:
                continue
            if action_type in DISTRIBUTION_ARCHITECTURE_REPAIR_ACTION_TYPES:
                matching_logs.append(str(path))
                if latest_matching_at is None or dt > latest_matching_at:
                    latest_matching_at = dt
            if action_type == 'distribution_architecture_churn_guard_repair':
                guard_logs.append(str(path))
            if action_type == 'distribution_architecture_guard_follow_through':
                guard_follow_through_logs.append(str(path))
                if latest_guard_follow_through_at is None or dt > latest_guard_follow_through_at:
                    latest_guard_follow_through_at = dt
            if is_guard_pause:
                guard_pause_logs.append(str(path))
                if latest_guard_pause_at is None or dt > latest_guard_pause_at:
                    latest_guard_pause_at = dt
                if earliest_guard_pause_at is None or dt < earliest_guard_pause_at:
                    earliest_guard_pause_at = dt
        return {
            'matching_logs': matching_logs,
            'guard_logs': guard_logs,
            'guard_follow_through_logs': guard_follow_through_logs,
            'guard_pause_logs': guard_pause_logs,
            'latest_matching_at': latest_matching_at,
            'latest_guard_follow_through_at': latest_guard_follow_through_at,
            'latest_guard_pause_at': latest_guard_pause_at,
            'earliest_guard_pause_at': earliest_guard_pause_at,
        }

    state = _collect(short_window_started_at)
    if fingerprint:
        fallback_state = _collect(fallback_started_at)
        for key in ('matching_logs', 'guard_logs', 'guard_follow_through_logs', 'guard_pause_logs'):
            if not state[key]:
                state[key] = fallback_state[key]
        for key in ('latest_matching_at', 'latest_guard_follow_through_at', 'latest_guard_pause_at'):
            if state[key] is None:
                state[key] = fallback_state[key]
        if state['earliest_guard_pause_at'] is None:
            state['earliest_guard_pause_at'] = fallback_state['earliest_guard_pause_at']
        elif fallback_state['earliest_guard_pause_at'] is not None:
            state['earliest_guard_pause_at'] = min(state['earliest_guard_pause_at'], fallback_state['earliest_guard_pause_at'])

    matching_logs = state['matching_logs']
    guard_logs = state['guard_logs']
    guard_follow_through_logs = state['guard_follow_through_logs']
    guard_pause_logs = state['guard_pause_logs']
    cumulative_guard_pause_logs = list(dict.fromkeys([
        *guard_pause_logs,
        *fallback_state['guard_pause_logs'],
    ])) if fingerprint else guard_pause_logs

    return {
        'execution_board_fingerprint': fingerprint,
        'repeat_count': len(matching_logs),
        'matching_logs': matching_logs,
        'latest_matching_at': state['latest_matching_at'],
        'third_strike': len(matching_logs) >= 2,
        'guard_installed': bool(guard_logs),
        'guard_logs': guard_logs,
        'guard_follow_through_count': len(guard_follow_through_logs),
        'guard_follow_through_logs': guard_follow_through_logs,
        'latest_guard_follow_through_at': state['latest_guard_follow_through_at'],
        'guard_pause_count': len(guard_pause_logs),
        'guard_pause_logs': guard_pause_logs,
        'cumulative_guard_pause_count': len(cumulative_guard_pause_logs),
        'cumulative_guard_pause_logs': cumulative_guard_pause_logs,
        'latest_guard_pause_at': state['latest_guard_pause_at'],
        'earliest_guard_pause_at': state['earliest_guard_pause_at'],
    }


@dataclass(frozen=True)
class LaneDecision:
    lane: str
    reason: str
    reasons: list[str]
    owned_content_posts_last_36h: int
    unsubmitted_directory_channels: list[str]
    shared_findings_used: list[str]
    artifact_path: str
    short_review_window_release_at: str | None = None
    # Repair-awareness fields (set by run.py when audit repairs are pending).
    skip_directory_submissions: bool = False
    skip_curator_outreach: bool = False


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {}


def _chosen_action_dict(payload: dict[str, Any]) -> dict[str, Any]:
    chosen_action = payload.get('chosen_action')
    return chosen_action if isinstance(chosen_action, dict) else {}


def _chosen_action_type(payload: dict[str, Any]) -> str:
    return str(
        _chosen_action_dict(payload).get('type')
        or payload.get('action_type')
        or payload.get('type')
        or payload.get('action')
        or ''
    ).strip()


def _cron_show_payload(job_id: str) -> dict[str, Any]:
    job_id = str(job_id or '').strip()
    if not job_id:
        return {}
    try:
        result = subprocess.run(
            ['openclaw', 'cron', 'show', job_id, '--json'],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _cron_job_running_from_payload(payload: dict[str, Any]) -> bool:
    cron_job = payload.get('cron_job')
    cron_job_id = ''
    if isinstance(cron_job, dict):
        cron_job_id = str(cron_job.get('id') or '').strip()
    if not cron_job_id:
        cron_job_id = str(payload.get('cron_job_id') or payload.get('job_id') or '').strip()
    if not cron_job_id:
        return False
    cron_payload = _cron_show_payload(cron_job_id)
    status = str(cron_payload.get('status') or '').strip().lower()
    state = cron_payload.get('state')
    if status == 'running':
        return True
    if isinstance(state, dict) and state.get('runningAtMs'):
        return True
    return False


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _short_review_window_reentry_repairs_state(
    now: datetime,
    *,
    release_at: datetime | None,
) -> dict[str, Any]:
    if release_at is None:
        return {
            'repairs_seen': set(),
            'reentry_repairs_complete': False,
        }

    window_started_at = release_at - timedelta(hours=SHORT_REVIEW_WINDOW_HOURS)

    repairs_seen: set[str] = set()
    historical_repairs_seen: set[str] = set()
    bridge_repairs_seen: set[str] = set()
    window_ends_at = min(now, release_at)
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        action_type = _chosen_action_type(payload)
        if action_type not in MEASUREMENT_HOLD_REENTRY_REPAIR_ACTION_TYPES and action_type not in MEASUREMENT_HOLD_GLOBAL_REPAIR_BRIDGE_ACTION_TYPES:
            continue
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt > window_ends_at:
            continue
        if action_type in MEASUREMENT_HOLD_REENTRY_REPAIR_ACTION_TYPES:
            historical_repairs_seen.add(action_type)
            if dt >= window_started_at:
                repairs_seen.add(action_type)
        elif dt >= window_started_at:
            bridge_repairs_seen.add(action_type)

    if repairs_seen != MEASUREMENT_HOLD_REENTRY_REPAIR_ACTION_TYPES and bridge_repairs_seen and historical_repairs_seen == MEASUREMENT_HOLD_REENTRY_REPAIR_ACTION_TYPES:
        repairs_seen = set(MEASUREMENT_HOLD_REENTRY_REPAIR_ACTION_TYPES)

    return {
        'repairs_seen': repairs_seen,
        'reentry_repairs_complete': repairs_seen == MEASUREMENT_HOLD_REENTRY_REPAIR_ACTION_TYPES,
    }


def _curator_queue_status_from_live_payload(payload: dict[str, Any]) -> str:
    chosen_action = _chosen_action_dict(payload)
    channel = str(
        payload.get('channel')
        or chosen_action.get('channel')
        or ''
    ).lower()
    if 'email' in channel:
        return 'sent_via_email_fallback'
    if 'github_issue' in channel:
        return 'sent_via_github_issue'
    if 'form' in channel:
        return 'sent_via_form'
    return 'waiting_review'


def _curator_queue_recent_live_actions(days: int = 30) -> dict[str, dict[str, Any]]:
    cutoff = datetime.now() - timedelta(days=days)
    latest: dict[str, dict[str, Any]] = {}
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        result = payload.get('result') or {}
        status = str(payload.get('status') or result.get('status') or '').lower()
        live_external = bool(payload.get('live_external_action') or result.get('live_external_action') or status in LIVE_EXTERNAL_STATUSES)
        if not live_external:
            continue
        chosen_action = _chosen_action_dict(payload)
        target_name = _display_target_name(
            str(payload.get('target') or chosen_action.get('target') or '').strip()
        )
        if not target_name:
            continue
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue
        existing = latest.get(target_name)
        if existing and existing['timestamp'] >= dt:
            continue
        latest[target_name] = {
            'timestamp': dt,
            'path': str(path),
            'payload': payload,
        }
    return latest


def _normalized_curator_queue_rows() -> list[dict[str, Any]]:
    payload = _load_json(_curator_queue_path())
    rows = payload.get('targets', []) or []
    recent_actions = _curator_queue_recent_live_actions()
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized_row = dict(row)
        status = str(normalized_row.get('status') or '').lower()
        if status in {'prepared', 'queued', 'in_review'}:
            target_name = _display_target_name(str(normalized_row.get('target') or '').strip())
            action = recent_actions.get(target_name)
            if action is not None:
                payload = action['payload']
                normalized_row['status'] = _curator_queue_status_from_live_payload(payload)
                normalized_row['last_contact_at'] = payload.get('timestamp') or payload.get('timestamp_utc') or action['timestamp'].isoformat()
                normalized_row['last_contact_log'] = action['path']
        normalized.append(normalized_row)
    return normalized


def _recent_owned_content_posts(now: datetime, hours: int = 36) -> list[dict[str, Any]]:
    cutoff = now - timedelta(hours=hours)
    posts: list[dict[str, Any]] = []
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        chosen = _chosen_action_dict(payload)
        result = payload.get('result') or {}
        if chosen.get('type') != 'owned_content_publication' or not result.get('ok'):
            continue
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt >= cutoff:
            item = dict(payload)
            item['_path'] = str(path)
            posts.append(item)
    posts.sort(key=lambda item: _parse_dt(item.get('timestamp') or item.get('timestamp_utc')) or datetime.min)
    return posts


def _recent_live_action_family_count(now: datetime, *, hours: int, family: str) -> int:
    cutoff = now - timedelta(hours=hours)
    total = 0
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        result = payload.get('result') or {}
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue

        payload_status = str(payload.get('status') or result.get('status') or '').lower()
        live_external = bool(
            payload.get('live_external_action')
            or result.get('live_external_action')
            or payload_status in LIVE_EXTERNAL_STATUSES
        )
        if not live_external:
            continue

        name = path.name.lower()
        if family == 'directory_submission':
            if 'submission' in name and 'curator' not in name and 'comparison' not in name and 'attempt' not in name:
                total += 1
        elif family == 'curator_outreach':
            if 'curator_email' in name or 'curator_outreach' in name or 'curator_contact' in name:
                total += 1
    return total


def _live_external_event_key(path: Path, payload: dict[str, Any], dt: datetime) -> tuple[str, ...]:
    chosen_action = _chosen_action_dict(payload)
    channel = payload.get('channel')
    if isinstance(channel, dict):
        channel_name = str(channel.get('type') or channel.get('name') or '').strip().lower()
        recipient = str(channel.get('recipient') or payload.get('recipient') or chosen_action.get('recipient') or '').strip().lower()
        subject = str(channel.get('subject') or payload.get('subject') or '').strip().lower()
    else:
        channel_name = str(channel or chosen_action.get('channel') or '').strip().lower()
        recipient = str(payload.get('recipient') or chosen_action.get('recipient') or '').strip().lower()
        subject = str(payload.get('subject') or '').strip().lower()
    target = _display_target_name(str(payload.get('target') or chosen_action.get('target') or '')).lower()
    submit_url = str(payload.get('submit_url') or payload.get('url') or chosen_action.get('url') or '').strip().lower()
    if recipient and subject:
        return (
            dt.replace(second=0, microsecond=0).isoformat(),
            recipient,
            subject,
        )
    if recipient or subject or target or submit_url:
        return (
            dt.replace(second=0, microsecond=0).isoformat(),
            recipient,
            subject,
            target,
            submit_url,
            channel_name,
        )
    return (path.name.lower(),)


def _recent_live_external_actions(now: datetime, *, hours: int) -> list[datetime]:
    cutoff = now - timedelta(hours=hours)
    seen: set[tuple[str, ...]] = set()
    timestamps: list[datetime] = []
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        result = payload.get('result') or {}
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue
        status = str(payload.get('status') or result.get('status') or '').lower()
        if not bool(
            payload.get('live_external_action')
            or result.get('live_external_action')
            or status in {'sent', 'submitted', 'published', 'launched'}
        ):
            continue
        event_key = _live_external_event_key(path, payload, dt)
        if event_key in seen:
            continue
        seen.add(event_key)
        timestamps.append(dt)
    return sorted(timestamps)


def _recent_live_external_action_count(now: datetime, *, hours: int) -> int:
    return len(_recent_live_external_actions(now, hours=hours))


def _recent_live_external_window_release_at(now: datetime, *, hours: int) -> datetime | None:
    timestamps = _recent_live_external_actions(now, hours=hours)
    if not timestamps:
        return None
    earliest = min(timestamps)
    return earliest + timedelta(hours=hours)


def _recent_executed_action_type(now: datetime, *, action_types: set[str], hours: int = 48) -> bool:
    cutoff = now - timedelta(hours=hours)
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue

        result = payload.get('result') or {}
        status = str(payload.get('status') or result.get('status') or '').lower()
        ok = bool(payload.get('ok') or result.get('ok') or status == 'executed')
        if not ok:
            continue

        action_type = _chosen_action_type(payload)
        if str(action_type).strip() in action_types:
            return True
    return False


def _target_name_variants(name: str) -> set[str]:
    cleaned = _display_target_name(str(name or '').strip())
    if not cleaned:
        return set()
    variants = {cleaned}
    for separator in (' — ', ' / '):
        if separator in cleaned:
            head = cleaned.split(separator, 1)[0].strip()
            if head:
                variants.add(head)
    lowered = cleaned.lower()
    for marker in (' benchmark', ' comparison'):
        idx = lowered.find(marker)
        if idx > 0:
            prefix = cleaned[:idx].strip(' -—:/')
            if prefix:
                variants.add(prefix)
    return variants


def _recent_contact_targets(now: datetime, *, action_types: set[str], days: int = 7) -> set[str]:
    cutoff = now - timedelta(days=days)
    targets: set[str] = set()
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue

        result = payload.get('result') or {}
        status = str(payload.get('status') or result.get('status') or '').lower()
        ok = bool(payload.get('ok') or result.get('ok') or status in LIVE_EXTERNAL_STATUSES)
        if not ok:
            continue

        action_type = _chosen_action_type(payload)
        if str(action_type).strip() not in action_types:
            continue

        targets.update(_target_name_variants(str(payload.get('target') or '').strip()))
    return targets


def _recent_curator_queue_contact_targets(now: datetime, *, days: int = 7) -> set[str]:
    cutoff = now - timedelta(days=days)
    payload = _load_json(_curator_queue_path())
    targets: set[str] = set()
    for row in payload.get('targets', []) or []:
        status = str(row.get('status') or '').strip().lower()
        if status not in CURATOR_MEASUREMENT_WINDOW_STATUSES:
            continue
        last_contact_at = _parse_dt(str(row.get('last_contact_at') or '').strip())
        if last_contact_at is None or last_contact_at < cutoff:
            continue
        targets.update(_target_name_variants(str(row.get('target') or '').strip()))
    return targets


def _backlink_status_snapshot(now: datetime) -> dict[str, Any]:
    payload = _load_json(_backlink_status_latest_path())
    generated_at = _parse_dt(payload.get('generated_at'))
    age_hours = None
    if generated_at is not None:
        age_hours = max(0.0, (now - generated_at).total_seconds() / 3600)
    summary = payload.get('summary') or {}
    return {
        'payload': payload,
        'generated_at': generated_at,
        'age_hours': age_hours,
        'live_listings': int(summary.get('directories_with_live_listings', 0) or 0),
        'queries_indexed': int(summary.get('queries_indexed', 0) or 0),
    }


def _live_listing_proof_rows(limit: int = 3) -> list[dict[str, str]]:
    payload = _load_json(_backlink_status_latest_path())
    directories = payload.get('directories') or {}
    rows: list[dict[str, str]] = []
    for name, row in sorted(directories.items()):
        if not row.get('listing_live'):
            continue
        rows.append({
            'name': str(name),
            'listing_url': str(row.get('listing_url') or ''),
            'status_note': str(row.get('status_note') or ''),
            'preferred_repo_target': str(row.get('preferred_repo_target') or 'unknown'),
        })
    return rows[:limit]


def _packet_includes_live_listing_proof(path: Path) -> bool:
    proof_rows = _live_listing_proof_rows()
    if not proof_rows:
        return True
    if not path.exists():
        return False
    text = path.read_text(encoding='utf-8')
    if not text:
        return False
    return all(
        (row.get('listing_url') and row['listing_url'] in text)
        or row['name'] in text
        for row in proof_rows
    )


def _directory_confirmation_due(now: datetime, recent_directory_submissions: int) -> bool:
    if recent_directory_submissions < DIRECTORY_SUBMISSION_BURST_THRESHOLD:
        return False
    if _recent_executed_action_type(now, action_types=RECENT_DIRECTORY_CONFIRMATION_ACTION_TYPES, hours=6):
        return False
    snapshot = _backlink_status_snapshot(now)
    if not snapshot.get('payload'):
        return True
    age_hours = snapshot.get('age_hours')
    if age_hours is None:
        return True
    return age_hours >= 4


def _directory_secondary_surface_repair_targets() -> list[str]:
    payload = _load_json(_backlink_status_latest_path())
    directories = payload.get('directories') or {}
    targets: list[str] = []
    for name, row in directories.items():
        for surface in row.get('secondary_surface_targets') or []:
            route = str(surface.get('preferred_repo_target') or 'unknown')
            if route not in {'github_only', 'unknown'}:
                continue
            url = str(surface.get('url') or '').strip()
            targets.append(url or str(name))
    return targets


def _directory_secondary_surface_packet_current() -> bool:
    path = _directory_confirmation_execution_latest_path()
    if not path.exists():
        return False
    text = path.read_text(encoding='utf-8')
    if not text:
        return False
    targets = _directory_secondary_surface_repair_targets()
    if not targets:
        return False
    return all(target in text for target in targets)


def _directory_secondary_surface_followup_window() -> dict[str, Any]:
    latest: dict[str, Any] = {}
    latest_dt: datetime | None = None
    for path in LOG_DIR.glob('marketing_*saashub*listing*correction*.json'):
        payload = _load_json(path)
        if str(payload.get('action') or '').strip() != 'saashub_live_listing_correction':
            continue
        review_at = _parse_dt(str(payload.get('review_window') or '').strip())
        if review_at is None:
            continue
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if latest_dt is None or dt > latest_dt:
            latest_dt = dt
            latest = {
                'path': str(path),
                'timestamp': dt,
                'review_at': review_at,
            }
    return latest


def _directory_secondary_surface_followup_active(now: datetime) -> bool:
    window = _directory_secondary_surface_followup_window()
    review_at = window.get('review_at')
    return isinstance(review_at, datetime) and review_at >= now


def _is_primary_repo_flat(adoption: dict[str, Any]) -> bool:
    evaluation = adoption.get('evaluation', {})
    return 'primary_repo_flat' in evaluation.get('failing_signals', [])


def _working_directory_channels() -> list[str]:
    payload = _load_json(CHANNEL_DISCOVERY_PATH)
    working = payload.get('working', []) or []
    channels: list[str] = []
    for entry in working:
        name = entry.get('name', '')
        if not name:
            continue
        channels.append(_canonical_channel_name(name))
    return channels


def _normalize_name(value: str) -> str:
    return ''.join(ch for ch in (value or '').lower() if ch.isalnum())


CHANNEL_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    'aitoolsinc': ('aitoolsinc', 'ai tools', 'aitools.inc', 'ai tools aitools.inc'),
    'aitoolsindex': ('aitoolsindex', 'ai tools index'),
    'toolwise': ('toolwise', 'tool wise'),
    'toolshelf': ('toolshelf', 'tool shelf', 'toolshelfdev', 'toolshelfio'),
    'thenextai': ('thenextai', 'the next ai'),
    'openagents': ('openagents', 'openagentspro', 'openagents.pro'),
    'aigearbase': ('aigearbase', 'ai gear base'),
}


def _canonical_channel_name(value: str) -> str:
    normalized = _normalize_name(value)
    for canonical, aliases in CHANNEL_NAME_ALIASES.items():
        if normalized == canonical or any(normalized == _normalize_name(alias) for alias in aliases):
            return canonical
    return normalized


def _channel_names_from_submission_payload(payload: dict[str, Any]) -> set[str]:
    names: set[str] = set()

    def add(value: str | None) -> None:
        if not value:
            return
        canonical = _canonical_channel_name(value)
        if canonical:
            names.add(canonical)

    channel_value = payload.get('channel')
    if isinstance(channel_value, dict):
        add(channel_value.get('name', ''))
    elif isinstance(channel_value, str):
        add(channel_value)

    for key in ('target', 'action'):
        value = payload.get(key)
        if isinstance(value, str):
            add(value)

    for key in ('submit_url', 'target_surface', 'url'):
        raw = payload.get(key)
        if isinstance(raw, str):
            _add_channel_names_from_url(names, raw)

    return names


def _add_channel_names_from_url(names: set[str], raw: str) -> None:
    if not raw:
        return
    try:
        hostname = raw.split('://', 1)[-1].split('/', 1)[0]
    except Exception:
        hostname = ''
    if not hostname:
        return
    canonical = _canonical_channel_name(hostname)
    if canonical:
        names.add(canonical)
    host_parts = [part for part in hostname.lower().split('.') if part and part != 'www']
    if host_parts:
        canonical = _canonical_channel_name(host_parts[0])
        if canonical:
            names.add(canonical)
        if len(host_parts) >= 2:
            collapsed = _canonical_channel_name(''.join(host_parts[:-1]))
            if collapsed:
                names.add(collapsed)


def _already_attempted_channel_names() -> set[str]:
    mentioned: set[str] = set()
    outreach_text = OUTREACH_LOG_PATH.read_text(encoding='utf-8') if OUTREACH_LOG_PATH.exists() else ''
    outreach_normalized = _normalize_name(outreach_text.lower())
    for name, aliases in CHANNEL_NAME_ALIASES.items():
        if any(_normalize_name(alias) in outreach_normalized for alias in aliases):
            mentioned.add(name)

    for line in outreach_text.splitlines():
        if 'http://' not in line and 'https://' not in line:
            continue
        for token in line.replace('(', ' ').replace(')', ' ').split():
            if token.startswith('http://') or token.startswith('https://'):
                _add_channel_names_from_url(mentioned, token.rstrip('.,`>'))

    for path in LOG_DIR.glob('marketing_*submission.json'):
        payload = _load_json(path)
        mentioned.update(_channel_names_from_submission_payload(payload))
    return mentioned


def _apollo_status_blocked(payload: dict[str, Any]) -> bool:
    status = str(payload.get('status') or '').strip().lower()
    if status in {'cloudflare_auth_blocked', 'ato_email_verification_required', 'still_on_login_page', 'login_not_attempted', 'script_failure'}:
        return True
    if payload.get('cloudflare_blocked') and status != 'login_succeeded':
        return True
    browserless_status = str(payload.get('browserless_probe_status') or '').strip().lower()
    notes = str(payload.get('notes') or payload.get('status_notes') or '').strip().lower()
    authenticated_surface_still_usable = (
        status == 'login_succeeded'
        and not payload.get('cloudflare_blocked')
        and (
            'authenticated ui remained usable' in notes
            or 'background cloudflare challenges were seen on ancillary apollo requests' in notes
        )
    )
    if any(marker in notes for marker in ('verify you are human', 'captcha', 'cf challenge', 'cloudflare challenge')):
        if authenticated_surface_still_usable and not any(
            marker in notes for marker in ('verify you are human', 'captcha')
        ):
            return False
        return True
    return browserless_status in {'cloudflare_auth_blocked', 'login_403_blocked'} and status != 'login_succeeded'


def _shared_findings() -> list[str]:
    findings = [
        'adoption_metrics_latest.json: Codeberg movement is the primary success gate',
        'channel_discovery.json: validated easy-submit directory lanes',
        'outreach-log.md: avoid duplicate submission work and repeated HN/Lobsters-only handoff',
    ]
    market = _load_json(MARKET_INTELLIGENCE_PATH)
    if market:
        findings.append('market_intelligence_latest.json: reusable competitor comparisons and positioning truths')
    apollo = _load_json(_apollo_status_path())
    if apollo.get('status') == 'login_succeeded' and not _apollo_status_blocked(apollo):
        findings.append('apollo_status.json: managed outbound is authenticated and available for execution packaging')
    return findings


def _apollo_ready(now: datetime) -> bool:
    apollo_status_path = _apollo_status_path()
    payload = _load_json(apollo_status_path)
    if payload.get('status') != 'login_succeeded' or _apollo_status_blocked(payload):
        return False
    if not apollo_status_path.exists():
        return False
    age_hours = (now - datetime.fromtimestamp(apollo_status_path.stat().st_mtime)).total_seconds() / 3600
    return age_hours <= 12


def _apollo_execution_ready() -> bool:
    candidates = sorted(LOG_DIR.glob('marketing_*apollo*.json'), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        payload = _load_json(path)
        chosen = _chosen_action_dict(payload)
        result = payload.get('result') or {}
        if (chosen.get('channel') or '') != 'apollo_outreach':
            continue
        if not result.get('ok') or not result.get('live_external_action'):
            continue
        evidence_chunks: list[str] = []
        for key in ('notes', 'evidence', 'blocking_factors'):
            value = result.get(key)
            if isinstance(value, list):
                evidence_chunks.extend(str(item) for item in value)
            elif value:
                evidence_chunks.append(str(value))
        evidence_text = ' '.join(evidence_chunks).lower()
        if any(marker in evidence_text for marker in LOW_SIGNAL_APOLLO_MARKERS):
            return False
        if result.get('outcome_ready') is False:
            return False
        return True
    return False


def _apollo_sequence_measurement_status() -> dict[str, Any]:
    return _load_json(_apollo_sequence_status_path())


def _apollo_measurement_pending() -> bool:
    payload = _apollo_sequence_measurement_status()
    return bool(payload.get('measurement_pending'))


def _apollo_followup_due(now: datetime, payload: dict[str, Any] | None = None) -> bool:
    payload = payload or _apollo_sequence_measurement_status()
    if not payload or bool(payload.get('measurement_pending')):
        return False
    if int(payload.get('record_count') or 0) <= 0:
        return False
    status = str(payload.get('status') or '').strip().lower()
    if status not in {'launch_ready_unverified_send', 'not_outcome_ready'}:
        return False
    next_review_at = _parse_dt(str(payload.get('next_review_at') or '').strip())
    return next_review_at is not None and next_review_at <= now


def _is_repo_internal_curator_target(row: dict[str, Any]) -> bool:
    url = str(row.get('url') or '').lower()
    action = str(row.get('action') or '').lower()
    target = str(row.get('target') or '').lower()
    internal_markers = (
        'github.com/topics/',
        'add topic tag',
        'check if ralph workflow is already tagged',
        'repo description',
        'github topics:',
    )
    combined = ' '.join((url, action, target))
    return any(marker in combined for marker in internal_markers)


def _is_active_curator_queue_row(row: dict[str, Any], now: datetime, *, prepared_only: bool = False) -> bool:
    status = (row.get('status') or '').lower()
    allowed_statuses = {'prepared'} if prepared_only else {'prepared', 'queued', 'in_review'}
    if status not in allowed_statuses:
        return False
    if _is_repo_internal_curator_target(row):
        return False
    due = row.get('review_due_date')
    if due:
        try:
            if datetime.fromisoformat(due) < now:
                return False
        except ValueError:
            pass
    return True


def _live_curator_queue_count(now: datetime) -> int:
    count = 0
    for row in _normalized_curator_queue_rows():
        if not _is_active_curator_queue_row(row, now):
            continue
        count += 1
    return count


def _curator_measurement_window_count(now: datetime) -> int:
    count = 0
    for row in _normalized_curator_queue_rows():
        status = (row.get('status') or '').lower()
        if status not in CURATOR_MEASUREMENT_WINDOW_STATUSES:
            continue
        if _is_repo_internal_curator_target(row):
            continue
        due = row.get('review_due_date')
        if due:
            try:
                if datetime.fromisoformat(due) < now:
                    continue
            except ValueError:
                pass
        elif not row.get('last_contact_at'):
            continue
        count += 1
    return count


def _prepared_curator_target_names(now: datetime) -> list[str]:
    candidates: list[dict[str, Any]] = []
    for row in _normalized_curator_queue_rows():
        if not _is_active_curator_queue_row(row, now, prepared_only=True):
            continue
        if str(row.get('target') or '').strip():
            candidates.append(row)
    candidates.sort(key=_curator_priority_score)
    return [_display_target_name(str(row.get('target') or '').strip()) for row in candidates[:5]]


def _curator_handoff_packet_ready(now: datetime, expected_targets: list[str]) -> bool:
    handoff_path = _curator_handoff_path()
    if not _handoff_packet_is_current(handoff_path, expected_targets):
        return False
    age = now - datetime.fromtimestamp(handoff_path.stat().st_mtime)
    return age <= timedelta(days=7)


def _prepared_curator_targets_waiting_for_handoff(now: datetime) -> int:
    expected_targets = _prepared_curator_target_names(now)
    if _curator_handoff_packet_ready(now, expected_targets):
        return 0
    return len(expected_targets)


def _due_curator_followup_targets(now: datetime) -> list[str]:
    due_rows: list[dict[str, Any]] = []
    for row in _normalized_curator_queue_rows():
        status = str(row.get('status') or '').lower()
        if status not in {'sent_via_email_fallback', 'sent_via_form', 'sent_via_github_issue', 'sent_via_manual_handoff', 'waiting_review', 'awaiting_reply'}:
            continue
        if _is_repo_internal_curator_target(row):
            continue
        due = _parse_dt(str(row.get('review_due_date') or ''))
        if due is None or due > now:
            continue
        if str(row.get('target') or '').strip():
            due_rows.append(row)
    due_rows.sort(key=_curator_priority_score)
    return [_display_target_name(str(row.get('target') or '').strip()) for row in due_rows[:5]]


def _curator_priority_score(row: dict[str, Any]) -> tuple[int, str]:
    priority = str(row.get('priority') or '').lower()
    if 'high' in priority:
        bucket = 0
    elif 'medium' in priority:
        bucket = 1
    else:
        bucket = 2
    return bucket, str(row.get('target') or '').lower()


def _display_target_name(value: str) -> str:
    raw = (value or '').strip()
    if not raw:
        return raw
    if raw[0].isdigit() and '. ' in raw:
        return raw.split('. ', 1)[1].strip() or raw
    return raw


def _contact_discovery_current_for_targets(expected_targets: list[str]) -> bool:
    contact_path = _curator_contact_discovery_path()
    if not expected_targets or not contact_path.exists():
        return False
    payload = _load_json(contact_path)
    current = sorted(
        _display_target_name(str(target.get('target') or '').strip())
        for target in payload.get('targets', []) or []
        if str(target.get('target') or '').strip()
    )
    expected = sorted(_display_target_name(target) for target in expected_targets)
    return current == expected


def _contact_discovery_has_targets() -> bool:
    contact_path = _curator_contact_discovery_path()
    if not contact_path.exists():
        return False
    payload = _load_json(contact_path)
    return any(str(target.get('target') or '').strip() for target in payload.get('targets', []) or [])


def _manual_contact_targets_waiting_for_execution() -> list[str]:
    contact_path = _curator_contact_discovery_path()
    if not contact_path.exists():
        return []
    payload = _load_json(contact_path)
    targets: list[str] = []
    for row in payload.get('targets', []) or []:
        status = str(row.get('status') or '').lower()
        if status not in MANUAL_CONTACT_HANDOFF_REMAINING_STATUSES:
            continue
        target_name = _display_target_name(str(row.get('target') or '').strip())
        if target_name:
            targets.append(target_name)
    return targets


def _primary_repo_flat_contact_targets_waiting_for_execution() -> list[str]:
    payload = _load_json(_primary_repo_flat_contact_discovery_path())
    targets: list[str] = []
    for row in payload.get('targets', []) or []:
        target_name = _display_target_name(str(row.get('target') or '').strip())
        if target_name and _publisher_target_is_packet_executable(row):
            targets.append(target_name)
    return targets


def _primary_repo_flat_non_executable_targets_waiting_for_execution() -> list[str]:
    payload = _load_json(_primary_repo_flat_contact_discovery_path())
    targets: list[str] = []
    for row in payload.get('targets', []) or []:
        target_name = _display_target_name(str(row.get('target') or '').strip())
        channels = row.get('channels') or []
        if target_name and channels and not _publisher_target_is_packet_executable(row):
            targets.append(target_name)
    return targets


def _publisher_target_is_packet_executable(row: dict[str, Any]) -> bool:
    channels = row.get('channels') or []
    if _publisher_target_has_packet_executable_channel(channels):
        return True
    recommended = str(row.get('recommended_next_step') or '').strip().lower()
    return (
        'github issue/pr path is now identified' in recommended
        and any(str((channel or {}).get('type') or '').strip().lower() == 'github_issue' for channel in channels)
    )


def _publisher_target_has_packet_executable_channel(channels: list[dict[str, Any]]) -> bool:
    """Match the executor's truthful packet criteria for primary-repo-flat outreach.

    Telegram can still be a legitimate manual route, but it needs a separate
    channel-ready asset instead of being treated as immediately packet-sendable.
    """
    for channel in channels:
        channel_type = str((channel or {}).get('type') or '').strip().lower()
        if channel_type in RUNTIME_SENDABLE_PUBLISHER_CHANNEL_TYPES:
            return True
        if channel_type != 'website':
            continue
        label = str((channel or {}).get('label') or '').strip().lower()
        value = str((channel or {}).get('value') or '').strip().lower()
        if any(token in label for token in (
            'contact form',
            'submission form',
            'submit form',
            'message form',
            'intake form',
            'feedback form',
        )):
            return True
        if any(token in value for token in (
            'typeform.com',
            'tally.so',
            'forms.gle',
            'docs.google.com/forms',
            'airtable.com',
            'jotform.com',
            'hubspot.com',
            'formspree.io',
        )):
            return True
    return False


def _publisher_target_has_manual_executable_channel(channels: list[dict[str, Any]]) -> bool:
    for channel in channels:
        channel_type = str((channel or {}).get('type') or '').strip().lower()
        if channel_type in MANUAL_EXECUTABLE_PUBLISHER_CHANNEL_TYPES:
            return True
        if channel_type != 'website':
            continue
        label = str((channel or {}).get('label') or '').strip().lower()
        value = str((channel or {}).get('value') or '').strip().lower()
        if any(token in label for token in (
            'contact form',
            'submission form',
            'submit form',
            'message form',
            'intake form',
            'feedback form',
        )):
            return True
        if any(token in value for token in (
            'typeform.com',
            'tally.so',
            'forms.gle',
            'docs.google.com/forms',
            'airtable.com',
            'jotform.com',
            'hubspot.com',
            'formspree.io',
        )):
            return True
    return False


def _publisher_target_has_runtime_sendable_channel(channels: list[dict[str, Any]]) -> bool:
    for channel in channels:
        channel_type = str((channel or {}).get('type') or '').strip().lower()
        if channel_type in RUNTIME_SENDABLE_PUBLISHER_CHANNEL_TYPES:
            return True
    return False


def _manual_contact_queue_targets_waiting_for_execution(now: datetime) -> list[str]:
    payload = _load_json(_curator_queue_path())
    targets: list[dict[str, Any]] = []
    for row in payload.get('targets', []) or []:
        status = str(row.get('status') or '').lower()
        if status not in MANUAL_CONTACT_HANDOFF_REMAINING_STATUSES:
            continue
        if _is_repo_internal_curator_target(row):
            continue
        due = row.get('review_due_date')
        if due:
            try:
                if datetime.fromisoformat(due) < now:
                    continue
            except ValueError:
                pass
        target_name = str(row.get('target') or '').strip()
        if target_name:
            targets.append(row)
    targets.sort(key=_curator_priority_score)
    return [_display_target_name(str(row.get('target') or '').strip()) for row in targets]


def _packet_headings(path: Path) -> list[str]:
    if not path.exists():
        return []
    headings: list[str] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.startswith('### '):
            continue
        _, _, value = line.partition('. ')
        headings.append(_display_target_name((value or line[4:]).strip()))
    return headings


def _handoff_packet_is_current(
    path: Path,
    expected_targets: list[str],
    require_live_listing_proof: bool = False,
    allow_superset: bool = False,
) -> bool:
    if not expected_targets or not path.exists():
        return False
    actual = _packet_headings(path)
    expected = [_display_target_name(target) for target in expected_targets]
    if allow_superset:
        if not set(expected).issubset(set(actual)):
            return False
    elif actual != expected:
        return False
    if require_live_listing_proof and not _packet_includes_live_listing_proof(path):
        return False
    return True


def _recent_action_delivery_current(now: datetime, expected_action_type: str, not_before: datetime | None = None) -> bool:
    cutoff = now - timedelta(days=7)
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue
        if not_before is not None and dt < not_before:
            continue

        action_type = _chosen_action_type(payload)
        if str(action_type).strip() != expected_action_type:
            continue

        result = payload.get('result') or {}
        status = str(payload.get('status') or result.get('status') or '').lower()
        if bool(payload.get('ok') or result.get('ok') or status in LIVE_EXTERNAL_STATUSES):
            return True
    return False


def _curator_handoff_packet_current(now: datetime, expected_targets: list[str]) -> bool:
    if not _curator_handoff_packet_ready(now, expected_targets):
        return False
    handoff_path = _curator_handoff_path()
    return _recent_action_delivery_current(now, 'curator_handoff_packet_execution', not_before=datetime.fromtimestamp(handoff_path.stat().st_mtime))


def _curator_contact_handoff_packet_current(now: datetime, expected_targets: list[str]) -> bool:
    handoff_path = _curator_contact_handoff_path()
    if not _handoff_packet_is_current(handoff_path, expected_targets):
        return False
    handoff_mtime = datetime.fromtimestamp(handoff_path.stat().st_mtime)
    age = now - handoff_mtime
    if age > timedelta(days=7):
        return False
    return _recent_action_delivery_current(now, 'curator_contact_handoff_packet_execution', not_before=handoff_mtime)


def _curator_contact_packet_already_delivered(now: datetime, expected_targets: list[str]) -> bool:
    expected = [_display_target_name(str(target).strip()) for target in expected_targets if str(target).strip()]
    if not expected:
        return False
    cutoff = now - timedelta(days=14)
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue
        if _chosen_action_type(payload) != 'curator_contact_handoff_packet_execution':
            continue
        prepared = [
            _display_target_name(str(item).strip())
            for item in ((payload.get('why_this_action') or {}).get('targets_prepared') or [])
            if str(item).strip()
        ]
        if prepared == expected[:len(prepared)] or prepared == expected:
            return True
    return False


def _primary_repo_flat_recent_prep_count(now: datetime, expected_targets: list[str], *, hours: int | None = None) -> int:
    expected = [target.strip() for target in expected_targets if target.strip()]
    if not expected:
        return 0
    cutoff = now - (timedelta(hours=hours) if hours is not None else timedelta(days=7))
    total = 0
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue
        if _chosen_action_type(payload) != 'primary_repo_flat_contact_handoff_packet_execution':
            continue
        prepared = [
            _display_target_name(str(item).strip())
            for item in ((payload.get('why_this_action') or {}).get('targets_prepared') or [])
            if str(item).strip()
        ]
        if prepared and prepared == expected[:len(prepared)]:
            total += 1
    return total


def _primary_repo_flat_recent_prep_matches_targets(now: datetime, expected_targets: list[str]) -> bool:
    return _primary_repo_flat_recent_prep_count(now, expected_targets) > 0


def _primary_repo_flat_contact_handoff_packet_current(now: datetime, expected_targets: list[str]) -> bool:
    handoff_path = _primary_repo_flat_contact_handoff_path()
    if not _handoff_packet_is_current(handoff_path, expected_targets, allow_superset=True):
        return False
    handoff_mtime = datetime.fromtimestamp(handoff_path.stat().st_mtime)
    age = now - handoff_mtime
    if age > timedelta(days=7):
        return False
    return (
        _recent_action_delivery_current(now, 'primary_repo_flat_contact_handoff_packet_execution', not_before=handoff_mtime)
        or _primary_repo_flat_recent_prep_matches_targets(now, expected_targets)
    )


def _primary_repo_flat_packet_delivery_still_active(now: datetime, expected_targets: list[str]) -> bool:
    expected = [target.strip() for target in expected_targets if target.strip()]
    if not expected:
        return False
    latest_packet_path = _primary_repo_flat_contact_handoff_path()
    latest_packet_mtime: datetime | None = None
    if latest_packet_path.exists():
        latest_packet_mtime = datetime.fromtimestamp(latest_packet_path.stat().st_mtime)
    short_review_window_release_at = _recent_live_external_window_release_at(
        now,
        hours=SHORT_REVIEW_WINDOW_HOURS,
    )
    short_review_window_active = bool(short_review_window_release_at and now < short_review_window_release_at)
    active_manual_delivery_targets = _active_manual_outreach_delivery_targets(now)
    cutoff = now - timedelta(days=14)
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue

        action_type = _chosen_action_type(payload)
        if str(action_type).strip() not in PRIMARY_REPO_FLAT_MANUAL_DELIVERY_ACTION_TYPES:
            continue

        chosen_action = payload.get('chosen_action') if isinstance(payload.get('chosen_action'), dict) else {}
        result = payload.get('result') if isinstance(payload.get('result'), dict) else {}
        packet_path = str(
            chosen_action.get('packet')
            or chosen_action.get('draft')
            or result.get('artifact')
            or result.get('packet')
            or ''
        ).strip()
        packet_name = Path(packet_path).name if packet_path else ''
        if packet_name and 'primary_repo_flat_contact_handoff_packet' not in packet_name:
            continue
        if packet_path and not _handoff_packet_is_current(
            Path(packet_path),
            expected,
            require_live_listing_proof=True,
            allow_superset=True,
        ):
            continue

        review_at = str(((payload.get('measurement_window') or {}).get('review_at')) or '').strip()
        review_dt = _parse_dt(review_at)
        delivered_at = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        refreshed_after_delivery = bool(
            action_type == 'primary_repo_flat_contact_manual_delivery_refresh'
            and latest_packet_mtime is not None
            and delivered_at is not None
            and latest_packet_mtime > delivered_at
        )
        if refreshed_after_delivery and (
            _primary_repo_flat_recent_prep_matches_targets(now, expected)
            or (not short_review_window_active and not active_manual_delivery_targets)
        ):
            continue
        if review_dt is not None and review_dt >= now:
            return True
        if delivered_at is not None and delivered_at.date() == now.date():
            return True
    return False


def _comparison_queue_capacity(now: datetime) -> tuple[int, int]:
    queue = _load_json(_comparison_queue_path())
    live = 0
    for row in queue.get('targets', []) or []:
        status = (row.get('status') or '').lower()
        if status not in {'prepared', 'queued', 'in_review'}:
            continue
        due = row.get('review_due_date')
        if due:
            try:
                if datetime.fromisoformat(due) < now:
                    continue
            except ValueError:
                pass
        live += 1
    market = _load_json(MARKET_INTELLIGENCE_PATH)
    total = len(market.get('comparison_pages', []) or [])
    return live, total


def _comparison_backlink_lane_manual_only_blocked(now: datetime, *, github_auth_available: bool | None = None) -> bool:
    if github_auth_available is None:
        github_auth_available = _github_auth_available()
    if github_auth_available:
        return False
    live_comparison_queue, comparison_capacity = _comparison_queue_capacity(now)
    return comparison_capacity > 0 and live_comparison_queue >= comparison_capacity


def _distribution_reset_target_identity(*, target: str = '', url: str = '') -> tuple[str, str]:
    normalized_url = (url or '').strip().lower()
    if normalized_url:
        return ('url', normalized_url)
    return ('target', (target or '').strip().lower())


def _parse_distribution_reset_log_targets() -> list[dict[str, str]]:
    reset_log_path = _distribution_reset_log_path()
    text = reset_log_path.read_text(encoding='utf-8') if reset_log_path.exists() else ''
    pattern = re.compile(
        r'^\d+\.\s+\*\*(?P<target>.+?)\*\*\s*$\n'
        r'\s*URL:\s*(?P<url>https?://\S+)\s*$\n'
        r'\s*Why it fits:\s*(?P<why>.+?)(?=\n(?:\s*\d+\.\s+\*\*|##|###)|\Z)',
        re.MULTILINE | re.DOTALL,
    )
    targets: list[dict[str, str]] = []
    for match in pattern.finditer(text):
        targets.append({
            'target': match.group('target').strip(),
            'url': match.group('url').strip(),
            'why_it_fits': ' '.join(match.group('why').split()),
        })
    return targets


def _distribution_reset_target_markers(row: dict[str, Any]) -> set[str]:
    markers: set[str] = set()
    target = str(row.get('target') or '').strip().lower()
    url = str(row.get('url') or '').strip().lower()
    if target:
        markers.add(target)
    if url:
        markers.add(url)
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        if host.startswith('www.'):
            host = host[4:]
        if host:
            markers.add(host)
        path = parsed.path.strip('/').lower()
        if path:
            markers.add(path)
    return {marker for marker in markers if len(marker) >= 4}


def _distribution_reset_target_has_live_evidence(row: dict[str, Any]) -> bool:
    markers = _distribution_reset_target_markers(row)
    if not markers:
        return False
    live_statuses = {'sent', 'submitted', 'published', 'launched'}

    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        if not payload:
            continue
        result = payload.get('result') or {}
        status = str(payload.get('status') or result.get('status') or '').lower()
        executed = bool(
            payload.get('ok')
            or result.get('ok')
            or payload.get('live_external_action')
            or result.get('live_external_action')
        )
        if not (payload.get('live_external_action') or result.get('live_external_action') or status in live_statuses):
            executed = False
        if not executed:
            continue
        haystack = json.dumps(payload, sort_keys=True).lower()
        if any(marker in haystack for marker in markers):
            return True
    return False


def _reconciled_distribution_reset_queue_rows() -> list[dict[str, Any]]:
    path = _distribution_reset_queue_path()
    payload = _load_json(path)
    rows = payload.get('targets', []) or []
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    changed = False

    for raw_row in rows:
        if not isinstance(raw_row, dict):
            continue
        row = dict(raw_row)
        identity = _distribution_reset_target_identity(target=row.get('target', ''), url=row.get('url', ''))
        if identity in seen:
            changed = True
            continue
        seen.add(identity)

        status = str(row.get('status') or '').lower()
        if status in {'discovered', 'ready_for_promotion'} and _distribution_reset_target_has_live_evidence(row):
            row['status'] = 'executed_elsewhere'
            row['reconciled_at'] = datetime.now().isoformat(timespec='seconds')
            row['reconciled_by'] = 'distribution_lane_selector'
            changed = True
        deduped.append(row)

    if changed:
        path.write_text(json.dumps({
            'generated_at': payload.get('generated_at') or datetime.now().isoformat(timespec='seconds'),
            'targets': deduped,
        }, indent=2) + '\n', encoding='utf-8')
    return deduped


def _distribution_reset_targets_ready() -> int:
    ready = 0
    for row in _reconciled_distribution_reset_queue_rows():
        status = (row.get('status') or '').lower()
        if status in {'discovered', 'ready_for_promotion'}:
            ready += 1
    if ready:
        return ready

    occupied = {
        _distribution_reset_target_identity(target=row.get('target', ''), url=row.get('url', ''))
        for row in (_load_json(_curator_queue_path()).get('targets', []) or [])
        if isinstance(row, dict)
    }
    occupied.update(
        _distribution_reset_target_identity(target=(row.get('name') or row.get('slug') or ''), url=row.get('url', ''))
        for row in (_load_json(_comparison_queue_path()).get('targets', []) or [])
        if isinstance(row, dict)
    )
    fresh = 0
    seen: set[tuple[str, str]] = set()
    for row in _parse_distribution_reset_log_targets():
        identity = _distribution_reset_target_identity(target=row.get('target', ''), url=row.get('url', ''))
        if identity in occupied or identity in seen:
            continue
        seen.add(identity)
        fresh += 1
    return fresh


def _stack_overflow_lane_recently_empty() -> bool:
    payload = _load_json(_stack_overflow_latest_path())
    return payload.get('drafts_created') == 0 if payload else False


def _stack_overflow_rate_limit_cooldown_active(now: datetime) -> tuple[bool, str | None]:
    payload = _load_json(_stack_overflow_latest_path())
    if not payload:
        return False, None
    next_retry_at = _parse_dt(payload.get('next_retry_at'))
    cooldown_active = bool(payload.get('cooldown_active'))
    if cooldown_active and next_retry_at and next_retry_at > now:
        return True, next_retry_at.isoformat()
    return False, next_retry_at.isoformat() if next_retry_at else None


def _stack_overflow_measurement_pending(now: datetime) -> bool:
    if _stack_overflow_post_cooldown_surface_exhausted(now):
        return False

    stackoverflow_latest_path = _stack_overflow_latest_path()
    payload = _load_json(stackoverflow_latest_path)
    if payload:
        drafts_created = int(payload.get('drafts_created', 0) or 0)
        reusable_draft_ready = bool(payload.get('reused_existing_draft'))
        if drafts_created > 0 or payload.get('drafts') or reusable_draft_ready:
            if stackoverflow_latest_path.exists():
                age = now - datetime.fromtimestamp(stackoverflow_latest_path.stat().st_mtime)
                if age <= timedelta(days=7):
                    return True

    stackoverflow_draft_dir = DRAFTS_DIR / 'stackoverflow'
    if not stackoverflow_draft_dir.exists():
        return False
    cutoff = now - timedelta(days=7)
    for draft_file in stackoverflow_draft_dir.glob('so_answer_*.md'):
        modified = datetime.fromtimestamp(draft_file.stat().st_mtime)
        if modified >= cutoff:
            return True
    return False


def _stack_overflow_latest_generated_after(dt: datetime | None) -> bool:
    if dt is None:
        return False
    payload = _load_json(_stack_overflow_latest_path())
    if not payload:
        return False
    generated_at = _parse_dt(payload.get('generated_at'))
    return generated_at is not None and generated_at >= dt


def _stack_overflow_handoff_path() -> Path:
    if STACKOVERFLOW_HANDOFF_LATEST_PATH.parent == DRAFTS_DIR:
        return STACKOVERFLOW_HANDOFF_LATEST_PATH
    return DRAFTS_DIR / 'stackoverflow_answer_handoff_packet_latest.md'


def _stack_overflow_handoff_packet_current(now: datetime) -> bool:
    handoff_path = _stack_overflow_handoff_path()
    if not handoff_path.exists():
        return False
    age = now - datetime.fromtimestamp(handoff_path.stat().st_mtime)
    return age <= timedelta(days=7)


def _stack_overflow_manual_delivery_current(now: datetime) -> bool:
    cutoff = now - timedelta(days=7)
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue

        action_type = _chosen_action_type(payload)
        if str(action_type).strip() != 'stackoverflow_manual_delivery':
            continue

        result = payload.get('result') or {}
        status = str(payload.get('status') or result.get('status') or '').lower()
        if bool(payload.get('ok') or result.get('ok') or status in LIVE_EXTERNAL_STATUSES):
            return True
    return False


def _stack_overflow_post_cooldown_run_current(now: datetime) -> bool:
    cutoff = now - timedelta(days=7)
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue

        action_type = _chosen_action_type(payload)
        if str(action_type).strip() not in STACKOVERFLOW_POST_COOLDOWN_ACTION_TYPES:
            continue

        result = payload.get('result') or {}
        status = str(payload.get('status') or result.get('status') or '').lower()
        if status not in {'scheduled', 'executed', 'ok'} and not bool(payload.get('ok') or result.get('ok')):
            continue

        scheduled_for = _parse_dt(
            payload.get('scheduled_run_at')
            or result.get('scheduled_run_at')
            or (payload.get('verification') or {}).get('scheduled_run_at')
        )
        if _cron_job_running_from_payload(payload) and not _stack_overflow_latest_generated_after(scheduled_for):
            return True
        if scheduled_for is None:
            return True
        if scheduled_for < now - STACKOVERFLOW_POST_COOLDOWN_GRACE:
            continue
        return True
    return False


def _stack_overflow_post_cooldown_surface_exhausted(now: datetime) -> bool:
    scheduled_for: datetime | None = None
    latest_payload: dict[str, Any] | None = None
    cutoff = now - timedelta(days=7)
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue

        action_type = _chosen_action_type(payload)
        if str(action_type).strip() not in STACKOVERFLOW_POST_COOLDOWN_ACTION_TYPES:
            continue

        result = payload.get('result') or {}
        status = str(payload.get('status') or result.get('status') or '').lower()
        if status not in {'scheduled', 'executed', 'ok'} and not bool(payload.get('ok') or result.get('ok')):
            continue

        candidate = _parse_dt(
            payload.get('scheduled_run_at')
            or result.get('scheduled_run_at')
            or (payload.get('verification') or {}).get('scheduled_run_at')
        )
        if candidate is None:
            continue
        if scheduled_for is None or candidate > scheduled_for:
            scheduled_for = candidate
            latest_payload = payload

    if scheduled_for is None or scheduled_for >= now - STACKOVERFLOW_POST_COOLDOWN_GRACE:
        return False
    if latest_payload and _cron_job_running_from_payload(latest_payload) and not _stack_overflow_latest_generated_after(scheduled_for):
        return False

    payload = _load_json(_stack_overflow_latest_path())
    if not payload:
        return False
    generated_at = _parse_dt(payload.get('generated_at'))
    if generated_at is None or generated_at < scheduled_for:
        return False

    drafts_created = int(payload.get('drafts_created', 0) or 0)
    if drafts_created > 0 or payload.get('drafts'):
        return False

    return True


def _load_recent_monitor_summary() -> dict[str, Any]:
    reddit_monitor_latest_path = _reddit_monitor_latest_path()
    if not reddit_monitor_latest_path.exists():
        return {}
    text = reddit_monitor_latest_path.read_text(encoding='utf-8')
    text_l = text.lower()
    diagnostics: dict[str, int] = {}
    for part in (text.split('**Search diagnostics:**', 1)[1].splitlines()[0].strip() if '**Search diagnostics:**' in text else '').split(','):
        key, _, value = part.strip().partition('=')
        if key and value.isdigit():
            diagnostics[key] = int(value)
    shortlisted = 0
    marker = '**Shortlisted:**'
    if marker in text:
        try:
            shortlisted = int(text.split(marker, 1)[1].splitlines()[0].strip())
        except ValueError:
            shortlisted = 0
    partial_visibility = 'partial visibility only' in text_l or 'fail closed' in text_l or 'direct access is still degraded' in text_l
    provider_degraded = (
        'no reliable coverage yet' in text_l
        or partial_visibility
        or diagnostics.get('provider_challenge', 0) >= max(6, diagnostics.get('ok', 0) * 2)
        or diagnostics.get('reddit_direct_access_degraded', 0) > 0
        or diagnostics.get('direct_access_degraded', 0) > 0
    )
    reddit_blocked = any(marker in text_l for marker in ['reddit is ip-blocked', 'reddit ip-blocked', 'reddit_ip_blocked']) or diagnostics.get('reddit_ip_blocked', 0) > 0

    execution_status = _load_json(_reddit_execution_status_path())
    execution_status_value = str(execution_status.get('status') or '').strip().lower()
    execution_age = _parse_dt(execution_status.get('generated_at') or execution_status.get('timestamp'))
    execution_blocked = False
    execution_ready = False
    if execution_age is not None:
        execution_recent = (datetime.now() - execution_age).total_seconds() <= 12 * 3600
        execution_blocked = execution_recent and execution_status_value in {'network_security_blocked', 'execution_blocked', 'not_logged_in'}
        execution_ready = execution_recent and execution_status_value == 'browser_session_ready'
    if execution_blocked:
        reddit_blocked = True
        partial_visibility = True
    elif execution_ready:
        reddit_blocked = False

    return {
        'shortlisted': shortlisted,
        'search_diagnostics': diagnostics,
        'provider_degraded': provider_degraded,
        'reddit_blocked': reddit_blocked,
        'partial_visibility_only': partial_visibility,
        'execution_status': execution_status_value or None,
    }


def _active_repair_pause_flags() -> tuple[bool, bool]:
    audit = _load_json(_audit_latest_json_path())
    if audit.get('repair_window_status') not in ACTIVE_REPAIR_WINDOW_STATUSES:
        return False, False
    pending_repairs = [
        repair for repair in (audit.get('repair_actions', []) or [])
        if repair.get('repair_state') in {'needs_execution', 'pending_measurement'}
    ]
    skip_directory_submissions = any(
        repair.get('failure_type') == 'same_family_distribution_overlap'
        for repair in pending_repairs
    )
    skip_curator_outreach = any(
        repair.get('failure_type') == 'same_family_outreach_overlap'
        for repair in pending_repairs
    )
    return skip_directory_submissions, skip_curator_outreach


def _publisher_outreach_paused_by_repair_window() -> bool:
    audit = _load_json(_audit_latest_json_path())
    if audit.get('repair_window_status') not in ACTIVE_REPAIR_WINDOW_STATUSES:
        return False
    return any(
        repair.get('failure_type') == 'same_family_publisher_overlap'
        and repair.get('repair_state') in {'needs_execution', 'pending_measurement'}
        for repair in (audit.get('repair_actions', []) or [])
    )


def _hn_ceiling_repeated() -> bool:
    audit = _load_json(_audit_latest_json_path())
    failing = set(audit.get('failing_tactics', []) or [])
    if 'execution_ceiling_repetition' in failing:
        return True
    outreach_text = OUTREACH_LOG_PATH.read_text(encoding='utf-8').lower() if OUTREACH_LOG_PATH.exists() else ''
    return outreach_text.count('hn/lobsters') >= 3


def _github_auth_available() -> bool:
    return subprocess.run(
        ['gh', 'auth', 'status'],
        capture_output=True,
        text=True,
        check=False,
    ).returncode == 0


def choose_distribution_lane(
    now: datetime | None = None,
    *,
    write_action_log: bool = True,
    persist_latest_artifacts: bool = True,
) -> LaneDecision:
    now = now or datetime.now()
    adoption = _load_json(ADOPTION_PATH)
    primary_flat = _is_primary_repo_flat(adoption)
    recent_posts = _recent_owned_content_posts(now)
    working_channels = _working_directory_channels()
    attempted_channels = _already_attempted_channel_names()
    unsubmitted_channels = sorted([name for name in working_channels if name not in attempted_channels])
    shared_findings = _shared_findings()
    monitor_summary = _load_recent_monitor_summary()
    reddit_degraded = bool(monitor_summary.get('provider_degraded'))
    reddit_blocked = bool(monitor_summary.get('reddit_blocked'))
    reddit_execution_degraded = reddit_degraded or reddit_blocked or bool(monitor_summary.get('partial_visibility_only'))
    hn_ceiling_repeated = _hn_ceiling_repeated()
    github_auth_available = _github_auth_available()
    apollo_authenticated = _apollo_ready(now)
    apollo_execution_ready = _apollo_execution_ready()
    apollo_measurement_status = _apollo_sequence_measurement_status()
    apollo_measurement_pending = bool(apollo_measurement_status.get('measurement_pending'))
    apollo_sequence_status = str(apollo_measurement_status.get('status') or '').strip().lower()
    apollo_launch_ready_unverified = (
        apollo_sequence_status == 'launch_ready_unverified_send'
        and int(apollo_measurement_status.get('record_count') or 0) > 0
    )
    apollo_followup_due = _apollo_followup_due(now, apollo_measurement_status)
    live_curator_queue = _live_curator_queue_count(now)
    prepared_curator_target_names = _prepared_curator_target_names(now)
    prepared_curator_handoff_targets = _prepared_curator_targets_waiting_for_handoff(now)
    curator_handoff_packet_current = _curator_handoff_packet_current(now, prepared_curator_target_names)
    due_curator_followup_targets = _due_curator_followup_targets(now)
    live_curator_measurement_windows = _curator_measurement_window_count(now)
    contact_discovery_current = _contact_discovery_current_for_targets(prepared_curator_target_names)
    contact_discovery_available = _contact_discovery_has_targets()
    manual_contact_targets = _manual_contact_targets_waiting_for_execution()
    manual_contact_queue_targets = _manual_contact_queue_targets_waiting_for_execution(now)
    current_manual_contact_targets = manual_contact_queue_targets or manual_contact_targets
    curator_contact_handoff_current = _curator_contact_handoff_packet_current(now, current_manual_contact_targets)
    curator_contact_packet_already_delivered = _curator_contact_packet_already_delivered(now, current_manual_contact_targets)
    recent_publisher_contact_targets = set(_recent_contact_targets(
        now,
        action_types=PUBLISHER_CONTACT_ACTION_TYPES,
        days=7,
    )) | _recent_curator_queue_contact_targets(now, days=7)
    manual_outreach_assets = _manual_outreach_assets_waiting_for_execution(now)
    primary_repo_flat_handoff_path = _primary_repo_flat_contact_handoff_path()
    primary_repo_flat_followthrough_asset = next(
        (
            item for item in manual_outreach_assets
            if str(item.get('artifact_path') or '').strip() == str(primary_repo_flat_handoff_path)
            or 'primary-repo-flat' in str(item.get('title') or '').strip().lower()
        ),
        None,
    )
    generic_manual_outreach_assets = [
        item for item in manual_outreach_assets
        if item is not primary_repo_flat_followthrough_asset
    ]
    manual_outreach_asset_targets = [
        str(item.get('target') or '').strip()
        for item in generic_manual_outreach_assets
        if str(item.get('target') or '').strip()
    ]
    active_manual_outreach_delivery_targets = _active_manual_outreach_delivery_targets(now)
    pending_confirmation_actions = _pending_confirmation_actions(now)
    pending_confirmation_targets = [str(item.get('title') or '').strip() for item in pending_confirmation_actions if str(item.get('title') or '').strip()]
    pending_confirmation_handoff_current = _pending_confirmation_handoff_packet_current(now, pending_confirmation_targets)
    all_primary_repo_flat_contact_targets = [
        target for target in _primary_repo_flat_contact_targets_waiting_for_execution()
        if target not in active_manual_outreach_delivery_targets
    ]
    non_executable_primary_repo_flat_contact_targets = [
        target for target in _primary_repo_flat_non_executable_targets_waiting_for_execution()
        if target not in active_manual_outreach_delivery_targets
    ]
    primary_repo_flat_contact_targets = [
        target for target in all_primary_repo_flat_contact_targets
        if target not in recent_publisher_contact_targets
    ]
    runtime_sendable_primary_repo_flat_targets = [
        target for target in primary_repo_flat_contact_targets
        if target not in non_executable_primary_repo_flat_contact_targets
    ]
    primary_repo_flat_contact_handoff_current = _primary_repo_flat_contact_handoff_packet_current(now, primary_repo_flat_contact_targets)
    primary_repo_flat_packet_delivery_active = _primary_repo_flat_packet_delivery_still_active(now, primary_repo_flat_contact_targets)
    primary_repo_flat_recent_prep_repeat_count = _primary_repo_flat_recent_prep_count(
        now,
        primary_repo_flat_contact_targets,
        hours=PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_WINDOW_HOURS,
    )
    live_comparison_queue, comparison_capacity = _comparison_queue_capacity(now)
    reset_targets_ready = _distribution_reset_targets_ready()
    recent_directory_submissions = _recent_live_action_family_count(
        now,
        hours=DIRECTORY_MEASUREMENT_WINDOW_HOURS,
        family='directory_submission',
    )
    recent_curator_outreach = _recent_live_action_family_count(
        now,
        hours=DIRECTORY_MEASUREMENT_WINDOW_HOURS,
        family='curator_outreach',
    )
    recent_live_external_actions = _recent_live_external_action_count(
        now,
        hours=SHORT_REVIEW_WINDOW_HOURS,
    )
    recent_live_external_release_at = _recent_live_external_window_release_at(
        now,
        hours=SHORT_REVIEW_WINDOW_HOURS,
    )
    execution_board_short_review_release_at = _execution_board_short_review_release_at()
    skip_directory_submissions, skip_curator_outreach = _active_repair_pause_flags()
    skip_publisher_outreach = _publisher_outreach_paused_by_repair_window()

    reasons: list[str] = []
    lane = 'owned_content'
    reason = 'No stronger autonomous lane detected.'

    if primary_flat:
        reasons.append('Primary Codeberg adoption is flat in the current measurement window.')
    if recent_posts:
        reasons.append(f'{len(recent_posts)} owned-content posts already shipped in the last 36 hours.')
    if unsubmitted_channels:
        reasons.append(f'Validated easy-submit channels still unused: {", ".join(unsubmitted_channels)}.')
    if recent_directory_submissions:
        reasons.append(f'{recent_directory_submissions} directory submissions already shipped in the last {DIRECTORY_MEASUREMENT_WINDOW_HOURS} hours.')
    if recent_curator_outreach:
        reasons.append(f'{recent_curator_outreach} curator contact attempts already shipped in the last {DIRECTORY_MEASUREMENT_WINDOW_HOURS} hours.')
    if recent_live_external_actions:
        reasons.append(f'{recent_live_external_actions} live external marketing action(s) already shipped in the last {SHORT_REVIEW_WINDOW_HOURS} hours.')
        if recent_live_external_release_at is not None and recent_live_external_actions >= SHORT_REVIEW_WINDOW_EXTERNAL_ACTION_THRESHOLD:
            reasons.append(
                'If no new outcome lands first, this short-window congestion clears at '
                f'{recent_live_external_release_at.isoformat(timespec="seconds")}. '
                'Before then, another live outbound action would mostly blur measurement.'
            )
        if primary_repo_flat_contact_targets and recent_live_external_actions >= SHORT_REVIEW_WINDOW_EXTERNAL_ACTION_THRESHOLD:
            reasons.append('Fresh publisher-contact targets remain, but the short review window already has enough live external actions that another contact packet now would blur measurement more than it helps.')
    if skip_directory_submissions:
        reasons.append('Active repair window says to pause net-new directory submissions until current approval windows mature.')
    if skip_curator_outreach:
        reasons.append('Active repair window says to hold another same-family curator-contact burst and use a different lane.')
    if skip_publisher_outreach:
        reasons.append('Active repair window says to hold another same-day publisher-contact burst until the current reply windows mature or another family advances first.')
    if reddit_degraded:
        reasons.append('Reddit search coverage is degraded, so more monitor passes are lower leverage than third-party distribution prep.')
    if reddit_execution_degraded:
        reasons.append('Reddit execution is fail-closed from this environment right now, so the loop should not treat another Reddit pass as a shippable distribution lane.')
    if hn_ceiling_repeated:
        reasons.append('HN/Lobsters has repeated as a blocked ceiling, so the loop should create a different distribution lane in the same run.')
    if apollo_authenticated and apollo_execution_ready:
        reasons.append('Apollo is authenticated and the runtime has recent proof of a usable live import/sequence step, so managed outbound is a real lane here.')
    elif apollo_authenticated:
        reasons.append('Apollo is authenticated, but the runtime still lacks proof of a usable live import/sequence step, so do not treat packet generation alone as a live lane.')
    if apollo_measurement_pending:
        reasons.append(
            'Apollo already has an active measurement window '
            f"until {apollo_measurement_status.get('next_review_at', 'the next review checkpoint')}, so do not spend this run repackaging the same outbound lane."
        )
    elif apollo_launch_ready_unverified:
        reasons.append(
            'Apollo already has a verified non-zero list and launch-ready packet, but no live send confirmation yet; '
            'the truthful next move is launch/send follow-through, not another generic outbound packet refresh.'
        )
    elif apollo_followup_due:
        reasons.append(
            'Apollo already passed its first launch checkpoint and is still not outcome-ready; '
            'review that live state now instead of dropping back to an empty-board hold.'
        )
    if live_curator_queue:
        reasons.append(f'{live_curator_queue} curator outreach targets are already live in the queue, so the loop should advance or review them instead of regenerating the same packet.')
    if live_curator_measurement_windows:
        reasons.append(f'{live_curator_measurement_windows} curator targets are already inside active reply/backlink review windows, so another same-family outreach batch would mostly create unmeasurable overlap.')
    if prepared_curator_handoff_targets:
        reasons.append(f'{prepared_curator_handoff_targets} prepared curator targets still need a canonical execution handoff packet.')
    elif curator_handoff_packet_current:
        reasons.append('The curator handoff packet is already current for the top prepared targets and was already delivered in this review window, so regenerating it again would be fake progress.')
    elif primary_flat and apollo_measurement_pending and reddit_blocked and not unsubmitted_channels:
        reasons.append('No actionable prepared curator targets remain outside the current measurement windows, so the next lane should create fresh high-intent demand capture rather than another packet refresh.')
    if contact_discovery_current:
        reasons.append('Prepared curator targets already have current non-GitHub contact discovery, so the next packet should advance manual maintainer contact instead of another generic PR handoff.')
    if due_curator_followup_targets:
        reasons.append(f'Curator follow-ups are now due for {len(due_curator_followup_targets)} target(s) ({", ".join(due_curator_followup_targets[:3])}), so the loop should not hide behind another measurement hold when real follow-through is ready.')
    if manual_contact_targets:
        reasons.append(f'Manual-contact-only curator targets are still waiting for execution ({", ".join(manual_contact_targets[:3])}), so the loop should use the existing contact-discovery asset before inventing new reset work.')
    elif manual_contact_queue_targets:
        if curator_contact_packet_already_delivered:
            reasons.append(f'Manual-contact-only curator targets remain in the live queue ({", ".join(manual_contact_queue_targets[:3])}), but the contact handoff packet was already delivered in this review window, so another packet right now would be fake progress.')
        else:
            reasons.append(f'Manual-contact-only curator targets are still waiting in the live queue ({", ".join(manual_contact_queue_targets[:3])}), so the loop should advance contact discovery + execution instead of inventing new reset work.')
    if curator_contact_handoff_current:
        reasons.append('The manual-contact execution packet is already current for the waiting targets and was already delivered in this review window, so selecting it again would be fake progress.')
    if recent_publisher_contact_targets:
        reasons.append(
            'Fresh publisher outreach already shipped in the current 7-day review window '
            f'({", ".join(sorted(recent_publisher_contact_targets)[:3])}), so those targets should not be re-queued immediately.'
        )
    if pending_confirmation_actions:
        reasons.append(
            f'{len(pending_confirmation_actions)} live external action(s) are still blocked on email confirmation '
            f'({", ".join(pending_confirmation_targets[:2])}), so the loop should advance that follow-through before treating them as shipped outcomes.'
        )
        if pending_confirmation_handoff_current:
            reasons.append('A current confirmation follow-through packet already exists for those blocked actions, so the board is not actually empty.')
    if manual_outreach_asset_targets:
        reasons.append(
            'A channel-ready manual publisher outreach asset already exists '
            f'({", ".join(manual_outreach_asset_targets[:3])}), so the loop should reuse that Codeberg-first follow-through surface instead of pretending there is no truthful packet.'
        )
    if active_manual_outreach_delivery_targets:
        reasons.append(
            'An active manual publisher handoff already covers '
            f'({", ".join(sorted(active_manual_outreach_delivery_targets)[:3])}), so those targets should stay out of fresh packet selection until their review window expires.'
        )
    if non_executable_primary_repo_flat_contact_targets:
        reasons.append(
            'Some remaining publisher targets only expose non-runtime-executable channels '
            f'({", ".join(non_executable_primary_repo_flat_contact_targets[:3])}), so they should not keep this lane looking actionable until a sendable path exists.'
        )
    if primary_repo_flat_contact_targets:
        if primary_repo_flat_packet_delivery_active:
            reasons.append(
                'The primary-repo-flat publisher contact packet was already manually delivered in the current review window, '
                'so another packet refresh right now would be fake progress.'
            )
        elif not primary_repo_flat_contact_handoff_current:
            reasons.append(
                'Primary-repo-flat repair already surfaced fresh developer-native publishers with public contact paths '
                f'({", ".join(primary_repo_flat_contact_targets[:3])}), so the loop should package that Codeberg-first outreach instead of ending at measurement hold.'
            )
    elif all_primary_repo_flat_contact_targets:
        reasons.append('All currently discovered publisher-contact targets already have fresh outreach inside their review windows, so another packet refresh right now would be fake progress.')
    if primary_repo_flat_contact_handoff_current:
        reasons.append('The primary-repo-flat publisher contact packet is already current for the remaining untouched target set, so the loop should enforce follow-through instead of pretending a fresh packet is needed.')
    if primary_repo_flat_packet_delivery_active:
        reasons.append('A refreshed primary-repo-flat packet already has a live review window, so the loop should not re-select that same packet until the window expires or the target set materially changes.')
    if primary_repo_flat_recent_prep_repeat_count >= PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_THRESHOLD and not primary_repo_flat_packet_delivery_active:
        reasons.append(
            f'The same primary-repo-flat publisher packet has already been prepared {primary_repo_flat_recent_prep_repeat_count} time(s) in the last '
            f'{PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_WINDOW_HOURS} hours without a live delivery window, so selecting it again would be fake progress.'
        )
    primary_repo_flat_post_hold_only = (
        primary_repo_flat_contact_handoff_current
        and execution_board_short_review_release_at is not None
        and now < execution_board_short_review_release_at
        and not primary_repo_flat_packet_delivery_active
    )
    if primary_repo_flat_post_hold_only:
        reasons.append(
            'The execution board still marks the current primary-repo-flat packet as post-hold only '
            f'until {execution_board_short_review_release_at.isoformat(timespec="seconds")}, so surfacing it as a do-now lane would be fake progress.'
        )
    if not github_auth_available and prepared_curator_handoff_targets and comparison_capacity > 0 and live_comparison_queue >= comparison_capacity:
        reasons.append('GitHub auth is unavailable here, so prepared PR/citation targets need a manual execution handoff before the loop discovers even more targets.')
    if reset_targets_ready:
        reasons.append(f'{reset_targets_ready} fresh distribution-reset targets are waiting to be turned into real outreach assets.')
    curator_queue_saturated = live_curator_queue >= 6 and not unsubmitted_channels
    curator_measurement_saturated = live_curator_measurement_windows >= CURATOR_MEASUREMENT_WINDOW_SATURATION and not unsubmitted_channels
    comparison_queue_saturated = comparison_capacity > 0 and live_comparison_queue >= comparison_capacity
    comparison_lane_manual_only_blocked = _comparison_backlink_lane_manual_only_blocked(
        now,
        github_auth_available=github_auth_available,
    )
    directory_submission_burst = recent_directory_submissions >= DIRECTORY_SUBMISSION_BURST_THRESHOLD
    stackoverflow_measurement_pending = _stack_overflow_measurement_pending(now)
    stackoverflow_rate_limit_cooldown, stackoverflow_next_retry_at = _stack_overflow_rate_limit_cooldown_active(now)
    stackoverflow_handoff_current = _stack_overflow_handoff_packet_current(now)
    stackoverflow_manual_delivery_current = _stack_overflow_manual_delivery_current(now)
    stackoverflow_post_cooldown_run_current = _stack_overflow_post_cooldown_run_current(now)
    stackoverflow_post_cooldown_surface_exhausted = _stack_overflow_post_cooldown_surface_exhausted(now)
    if stackoverflow_post_cooldown_surface_exhausted:
        stackoverflow_measurement_pending = False
        stackoverflow_handoff_current = False
        stackoverflow_post_cooldown_run_current = False
    if comparison_lane_manual_only_blocked:
        reasons.append(
            'The comparison/backlink queue is already fully prepared, but GitHub auth is blocked here, so that lane is manual-only follow-through rather than fresh live outbound work.'
        )
    recent_proof_asset_shipped = _recent_executed_action_type(
        now,
        action_types=RECENT_PROOF_ASSET_ACTION_TYPES,
    )
    recent_reset_shipped = _recent_executed_action_type(
        now,
        action_types=RECENT_RESET_ACTION_TYPES,
        hours=24,
    )
    recent_directory_confirmation_shipped = _recent_executed_action_type(
        now,
        action_types=RECENT_DIRECTORY_CONFIRMATION_ACTION_TYPES,
        hours=6,
    )
    backlink_snapshot = _backlink_status_snapshot(now)
    directory_confirmation_due = _directory_confirmation_due(now, recent_directory_submissions)
    directory_secondary_surface_targets = _directory_secondary_surface_repair_targets()
    directory_secondary_surface_packet_current = _directory_secondary_surface_packet_current()
    directory_secondary_surface_followup_window = _directory_secondary_surface_followup_window()
    directory_secondary_surface_followup_active = _directory_secondary_surface_followup_active(now)
    if curator_queue_saturated:
        reasons.append('The curator queue is already saturated, so another queue-follow-through note would be fake activity unless the loop ships a fresh comparison/backlink asset.')
    if curator_measurement_saturated:
        reasons.append('Curator outreach already has enough live measurement windows open; the next move should create fresh demand capture instead of piling on more curator contact.')
    if comparison_queue_saturated:
        reasons.append('The comparison/backlink queue already covers every prepared comparison page, so another comparison follow-through would also be fake activity.')
    if directory_submission_burst:
        reasons.append('Low-intent directory distribution is already in a same-family burst, so another submission right now would mostly stack overlapping approval windows instead of creating a cleaner adoption read.')
    if backlink_snapshot.get('live_listings'):
        reasons.append(
            f"Backlink status already shows {backlink_snapshot.get('live_listings')} live directory listing(s), so the loop should reuse that evidence instead of acting like every submission is still opaque."
        )
    if directory_confirmation_due:
        reasons.append('The directory-confirmation snapshot is stale relative to the current submission burst, so refresh live listing/backlink evidence before adding more low-intent distribution.')
    if recent_directory_confirmation_shipped:
        reasons.append('A directory-confirmation refresh already shipped in the current short review window, so the next lane should reuse that evidence instead of regenerating the same snapshot.')
    if directory_secondary_surface_targets and directory_secondary_surface_packet_current:
        reasons.append('A current directory secondary-surface repair packet already exists for a live page that still misroutes or obscures Codeberg repo intent, so the loop should reuse that asset instead of calling the board empty.')
    if directory_secondary_surface_followup_active:
        review_at = directory_secondary_surface_followup_window.get('review_at')
        if isinstance(review_at, datetime):
            reasons.append(
                'The live secondary-surface repair already has an active review window '
                f'until {review_at.isoformat(timespec="seconds")}, so selecting directory confirmation again before then would be fake progress.'
            )
    if _stack_overflow_lane_recently_empty():
        reasons.append('The prior StackOverflow draft pass returned zero candidates, so if that lane is chosen it must rely on the repaired API-driven search rather than the old scrape-only path.')
    if stackoverflow_rate_limit_cooldown:
        reasons.append(
            'StackOverflow discovery is in an active post-429 cooldown '
            f"until {stackoverflow_next_retry_at or 'the next retry window'}, so do not spend the next slot re-hitting the API."
        )
    if stackoverflow_measurement_pending:
        reasons.append('A fresh StackOverflow answer draft already exists, so do not rerun the same demand-capture lane until that asset is posted, reused, or ages out of the current review window.')
    if stackoverflow_handoff_current:
        reasons.append('The StackOverflow handoff packet is already current, so regenerating it again would be fake progress.')
    if stackoverflow_manual_delivery_current:
        reasons.append('The StackOverflow packet was already delivered for manual placement in the current review window, so another handoff packet now would be fake progress.')
    if stackoverflow_post_cooldown_run_current:
        reasons.append('A post-cooldown StackOverflow run is already scheduled in the current review window, so another pre-cooldown packet refresh would be fake progress.')
    if stackoverflow_post_cooldown_surface_exhausted:
        reasons.append('The post-cooldown StackOverflow slot already ran after the retry window and still produced no fresh placement-ready outcome, so retire this packet for now and spend the next slot elsewhere.')
    if recent_proof_asset_shipped:
        reasons.append('Repo conversion proof assets already shipped recently, so this run should not loop on another docs-only proof-asset pass.')

    active_short_window_packet_refresh_allowed = (
        recent_live_external_release_at is not None
        and now < recent_live_external_release_at
        and recent_live_external_actions >= SHORT_REVIEW_WINDOW_EXTERNAL_ACTION_THRESHOLD
        and not manual_contact_targets
        and not (
            manual_contact_queue_targets
            and not curator_contact_packet_already_delivered
            and not curator_contact_handoff_current
        )
    )
    primary_repo_flat_refresh_requested = _execution_board_requests_primary_repo_flat_refresh()

    primary_repo_flat_packet_refresh_ready = (
        primary_flat
        and not github_auth_available
        and runtime_sendable_primary_repo_flat_targets
        and not primary_repo_flat_contact_handoff_current
        and primary_repo_flat_followthrough_asset is None
        and not primary_repo_flat_packet_delivery_active
        and (not skip_publisher_outreach or primary_repo_flat_refresh_requested)
        and (
            recent_live_external_release_at is None
            or now >= recent_live_external_release_at
            or skip_curator_outreach
            or active_short_window_packet_refresh_allowed
        )
        and (
            skip_curator_outreach
            or curator_measurement_saturated
            or comparison_queue_saturated
            or stackoverflow_measurement_pending
            or stackoverflow_post_cooldown_surface_exhausted
        )
    )

    if primary_flat and due_curator_followup_targets:
        lane = 'curator_due_followup'
        reason = 'At least one curator outreach review window is now due, so the highest-leverage move is a concrete follow-up packet instead of another reset or measurement hold.'
    elif primary_flat and not github_auth_available and not curator_contact_handoff_current and not curator_contact_packet_already_delivered and not (primary_repo_flat_contact_targets and (skip_curator_outreach or curator_measurement_saturated)) and (
        manual_contact_queue_targets
        or (prepared_curator_handoff_targets and contact_discovery_current)
        or (manual_contact_targets and contact_discovery_available)
    ):
        lane = 'curator_contact_handoff_packet'
        reason = (
            'A manual-contact-only curator target is already waiting in the live queue; '
            'advance contact discovery + the execution packet instead of inventing fresh reset work.'
            if manual_contact_queue_targets else
            'Prepared curator targets already have non-GitHub contact channels; advance the manual-contact execution packet instead of another generic handoff refresh.'
        )
    elif primary_repo_flat_packet_refresh_ready:
        lane = 'primary_repo_flat_contact_handoff_packet'
        reason = (
            'The short review window is still active, but the primary-repo-flat publisher packet is stale while fresh targets already have verified public contact paths; '
            'refresh the Codeberg-first execution packet now so the next slot has a truthful asset.'
            if recent_live_external_release_at is not None and now < recent_live_external_release_at and recent_live_external_actions >= SHORT_REVIEW_WINDOW_EXTERNAL_ACTION_THRESHOLD else
            'The short review window already cleared and fresh primary-repo-flat publisher targets now have verified public contact paths; '
            'turn them into one Codeberg-first execution packet instead of stalling inside measurement hold, even when the remaining path is human-executable rather than runtime-sendable.'
            if recent_live_external_release_at is not None and now >= recent_live_external_release_at else
            'Fresh primary-repo-flat publisher targets now have verified public contact paths; turn them into one Codeberg-first execution packet instead of stalling inside measurement hold, even when the remaining path is human-executable rather than runtime-sendable.'
        )
    elif primary_flat and len(recent_posts) >= 2 and unsubmitted_channels and not directory_submission_burst and not skip_directory_submissions:
        lane = 'directory_submission'
        reason = 'Stop adding Telegraph-first volume; use the next unspent autonomous backlink lane.'
    elif primary_flat and directory_submission_burst and unsubmitted_channels and apollo_measurement_pending and reddit_execution_degraded and not github_auth_available and not stackoverflow_measurement_pending and not stackoverflow_rate_limit_cooldown and not stackoverflow_post_cooldown_surface_exhausted and (curator_measurement_saturated or live_comparison_queue > 0):
        lane = 'stackoverflow_answer'
        reason = 'Recent directory submissions already saturated the lowest-intent lane; use higher-intent StackOverflow demand capture while current approval/reply windows mature.'
    elif primary_flat and skip_directory_submissions and backlink_snapshot.get('live_listings') and (directory_confirmation_due or directory_submission_burst) and not recent_directory_confirmation_shipped and (skip_curator_outreach or comparison_queue_saturated or curator_measurement_saturated):
        lane = 'directory_confirmation'
        reason = 'Directory submissions are paused and live listing proof already exists, so refresh approval/backlink evidence and reuse it in the next higher-intent lane instead of inventing another reset.'
    elif primary_flat and directory_confirmation_due and backlink_snapshot.get('payload') and not recent_directory_confirmation_shipped:
        lane = 'directory_confirmation'
        reason = 'Directory submissions already burst; refresh live listing and backlink evidence so the next move reuses real approvals instead of stacking more low-intent submissions.'
    elif primary_flat and pending_confirmation_actions:
        lane = 'distribution_confirmation_follow_through'
        reason = 'A live directory/surface correction already shipped, but it is still blocked on email confirmation; advance that follow-through instead of pretending the action is already outcome-ready or stacking another lane.'
    elif primary_flat and apollo_authenticated and apollo_launch_ready_unverified and reddit_blocked and not unsubmitted_channels:
        lane = 'apollo_launch_handoff_packet'
        reason = 'Apollo already has a verified non-zero list and canonical launch packet, but no live send proof; surface the launch/send handoff instead of regenerating prep or falling back to another architecture repair.'
    elif primary_flat and apollo_authenticated and apollo_followup_due and reddit_blocked and not unsubmitted_channels:
        lane = 'apollo_launch_handoff_packet'
        reason = 'Apollo already passed its first review checkpoint and is still not outcome-ready; reuse the launch/send handoff as the truthful follow-through surface instead of calling the board empty again.'
    elif primary_flat and apollo_authenticated and apollo_execution_ready and not apollo_measurement_pending and reddit_blocked and not unsubmitted_channels and not github_auth_available and (live_curator_queue > 0 or live_comparison_queue > 0):
        lane = 'apollo_outreach'
        reason = 'Reddit is blocked, GitHub PR auth is blocked here, and Apollo is live; use managed outbound with the already-prepared curator/comparison proof spine instead of another manual handoff packet.'
    elif primary_flat and apollo_authenticated and apollo_execution_ready and not apollo_measurement_pending and reddit_blocked and not unsubmitted_channels and live_curator_queue == 0 and live_comparison_queue == 0:
        lane = 'apollo_outreach'
        reason = 'Reddit is blocked while Apollo is live; switch to a managed outbound execution packet built from the shared comparison and curator proof spine.'
    elif primary_flat and not primary_repo_flat_post_hold_only and (
        (
            primary_repo_flat_contact_handoff_current
            and not primary_repo_flat_packet_delivery_active
            and runtime_sendable_primary_repo_flat_targets
        )
        or primary_repo_flat_followthrough_asset is not None
    ) and (skip_curator_outreach or stackoverflow_post_cooldown_surface_exhausted or recent_live_external_actions >= SHORT_REVIEW_WINDOW_EXTERNAL_ACTION_THRESHOLD):
        lane = 'primary_repo_flat_contact_handoff_packet'
        reason = (
            'The active repair window still blocks another net-new publisher burst, but a current Codeberg-first publisher contact packet already exists for fresh primary-repo-flat targets; '
            'reuse that packet as the truthful follow-through surface instead of stalling behind another measurement hold or churn guard.'
            if skip_publisher_outreach else
            'A current Codeberg-first publisher contact packet already exists for fresh primary-repo-flat targets; '
            'reuse that packet as the truthful follow-through surface instead of stalling behind another measurement hold or churn guard.'
        )
    elif primary_flat and generic_manual_outreach_assets and (skip_curator_outreach or stackoverflow_post_cooldown_surface_exhausted or recent_live_external_actions >= SHORT_REVIEW_WINDOW_EXTERNAL_ACTION_THRESHOLD):
        lane = 'manual_outreach_asset_follow_through'
        reason = (
            'The active repair window still blocks another net-new publisher burst, but a channel-ready manual publisher outreach asset already exists for an untouched Codeberg-primary target; '
            'use that packet as the truthful follow-through surface instead of hiding behind another measurement hold.'
            if skip_publisher_outreach else
            'A channel-ready manual publisher outreach asset already exists for an untouched Codeberg-primary target; '
            'use that packet as the truthful follow-through surface instead of hiding behind another measurement hold.'
        )
    elif primary_flat and directory_secondary_surface_targets and directory_secondary_surface_packet_current and not recent_directory_confirmation_shipped and not directory_secondary_surface_followup_active and (skip_directory_submissions or recent_live_external_actions >= SHORT_REVIEW_WINDOW_EXTERNAL_ACTION_THRESHOLD or stackoverflow_post_cooldown_surface_exhausted):
        lane = 'directory_confirmation'
        reason = 'A current directory secondary-surface repair packet already targets a live page that still misroutes or obscures Codeberg repo intent; reuse that packet as the truthful follow-through surface instead of falling back to measurement hold.'
    elif primary_flat and skip_directory_submissions and skip_curator_outreach and apollo_measurement_pending and reddit_execution_degraded and stackoverflow_measurement_pending and (stackoverflow_handoff_current or stackoverflow_manual_delivery_current or stackoverflow_post_cooldown_run_current) and recent_proof_asset_shipped and recent_live_external_actions >= SHORT_REVIEW_WINDOW_EXTERNAL_ACTION_THRESHOLD:
        lane = 'measurement_hold'
        reason = 'Several fresh external actions already shipped in the short review window, and the other executable lanes are still inside measurement or handoff windows; hold for follow-through instead of inventing another reset.'
    elif primary_flat and reset_targets_ready and skip_curator_outreach:
        lane = 'distribution_reset' if comparison_queue_saturated else 'comparison_backlink_outreach'
        reason = (
            'Curator outreach is paused by the active repair window, so use a different lane instead of promoting '
            'fresh reset targets into another same-family burst.'
        )
    elif primary_flat and reset_targets_ready:
        lane = 'curator_outreach'
        reason = 'Fresh reset targets exist; promote them into real outreach assets before logging another reset or queue-housekeeping cycle.'
    elif primary_flat and curator_queue_saturated and len(recent_posts) >= 2 and not comparison_queue_saturated:
        lane = 'comparison_backlink_outreach'
        reason = 'Curator queue prep is already full; ship a fresh comparison/backlink outreach asset instead of another follow-through note.'
    elif primary_flat and stackoverflow_post_cooldown_surface_exhausted and apollo_measurement_pending and reddit_execution_degraded and not unsubmitted_channels and comparison_queue_saturated and (prepared_curator_handoff_targets == 0 or curator_measurement_saturated) and not recent_proof_asset_shipped and not _execution_board_has_no_truthful_do_now_packet(now):
        lane = 'repo_conversion_proof_asset'
        reason = 'The post-cooldown StackOverflow slot already burned without a fresh placement-ready outcome, and the other external lanes are still in-flight; ship a repo proof asset instead of logging another empty-board hold.'
    elif primary_flat and stackoverflow_post_cooldown_surface_exhausted and apollo_measurement_pending and reddit_execution_degraded and not unsubmitted_channels and comparison_queue_saturated:
        lane = 'measurement_hold'
        reason = 'The post-cooldown StackOverflow slot already burned without a fresh outcome, and the other external lanes are still in-flight; hold for a genuinely different executable window instead of rerunning the same demand-capture search.'
    elif primary_flat and apollo_measurement_pending and reddit_execution_degraded and not unsubmitted_channels and not github_auth_available and not stackoverflow_measurement_pending and not stackoverflow_rate_limit_cooldown and not stackoverflow_post_cooldown_surface_exhausted and comparison_queue_saturated and (prepared_curator_handoff_targets == 0 or curator_measurement_saturated):
        lane = 'stackoverflow_answer'
        reason = 'Apollo is already in-flight, Reddit is fail-closed here, and curator/comparison outreach is already saturated or exhausted; draft high-intent StackOverflow answers instead of refreshing internal-only packets.'
    elif primary_flat and apollo_measurement_pending and reddit_execution_degraded and not unsubmitted_channels and stackoverflow_measurement_pending and (stackoverflow_handoff_current or stackoverflow_manual_delivery_current) and comparison_queue_saturated and (prepared_curator_handoff_targets == 0 or curator_measurement_saturated) and not recent_proof_asset_shipped:
        lane = 'repo_conversion_proof_asset'
        reason = 'The StackOverflow packet is already ready or already handed off for placement, and the external lanes are still in measurement windows; ship a missing repo proof asset instead of refreshing the same packet again.'
    elif primary_flat and apollo_measurement_pending and reddit_execution_degraded and not unsubmitted_channels and stackoverflow_measurement_pending and stackoverflow_handoff_current and comparison_queue_saturated and manual_contact_targets and (contact_discovery_current or contact_discovery_available) and not github_auth_available and not curator_contact_handoff_current and not curator_contact_packet_already_delivered:
        lane = 'curator_contact_handoff_packet'
        reason = 'External lanes are already in-flight, and a manual-contact-only curator target already has a current alternate contact path; advance the manual-contact execution packet instead of inventing fresh reset work.'
    elif primary_flat and recent_reset_shipped and not github_auth_available and prepared_curator_handoff_targets and curator_measurement_saturated:
        lane = 'distribution_reset'
        reason = 'Fresh reset targets already shipped, but same-family curator windows are already saturated; expand untouched target classes instead of refreshing another curator handoff packet.'
    elif primary_flat and recent_reset_shipped and not github_auth_available and prepared_curator_handoff_targets:
        lane = 'curator_handoff_packet'
        reason = 'Fresh reset targets already shipped in this window and were promoted into prepared curator assets; advance one canonical manual execution packet instead of looping back into another reset.'
    elif primary_flat and apollo_measurement_pending and reddit_execution_degraded and not unsubmitted_channels and stackoverflow_measurement_pending and stackoverflow_post_cooldown_run_current and comparison_queue_saturated and (prepared_curator_handoff_targets == 0 or curator_measurement_saturated) and recent_proof_asset_shipped:
        lane = 'measurement_hold'
        reason = 'A post-cooldown StackOverflow run is already queued and the current external lanes are still saturated or in-flight; hold for follow-through instead of refreshing the same packet or inventing a reset.'
    elif primary_flat and apollo_measurement_pending and reddit_execution_degraded and not unsubmitted_channels and stackoverflow_measurement_pending and (stackoverflow_handoff_current or stackoverflow_manual_delivery_current) and comparison_queue_saturated and (prepared_curator_handoff_targets == 0 or curator_measurement_saturated) and recent_proof_asset_shipped:
        lane = 'distribution_reset'
        reason = 'The proof-asset lane already shipped recently and the current external lanes are still saturated or in-flight; create fresh reset targets instead of looping on the same docs and StackOverflow handoff surfaces.'
    elif primary_flat and apollo_measurement_pending and reddit_execution_degraded and not unsubmitted_channels and stackoverflow_measurement_pending and stackoverflow_manual_delivery_current:
        lane = 'distribution_reset' if recent_proof_asset_shipped else 'repo_conversion_proof_asset'
        reason = (
            'The StackOverflow packet was already delivered for manual placement and the current external lanes are still in-flight; create fresh reset targets instead of refreshing the same handoff surface.'
            if recent_proof_asset_shipped else
            'The StackOverflow packet was already delivered for manual placement and the current external lanes are still in-flight; ship a repo proof asset instead of refreshing the same handoff surface.'
        )
    elif primary_flat and apollo_measurement_pending and reddit_execution_degraded and not unsubmitted_channels and stackoverflow_measurement_pending and comparison_queue_saturated and (prepared_curator_handoff_targets == 0 or curator_measurement_saturated) and not stackoverflow_post_cooldown_run_current and not stackoverflow_post_cooldown_surface_exhausted:
        lane = 'stackoverflow_answer_handoff_packet'
        reason = 'A fresh StackOverflow answer draft already exists and the other active lanes are still inside measurement windows; advance a posting/reuse handoff packet instead of regenerating the same demand-capture lane.'
    elif primary_flat and not github_auth_available and curator_queue_saturated and comparison_queue_saturated and prepared_curator_handoff_targets:
        lane = 'curator_handoff_packet'
        reason = 'Prepared outreach targets already exist but GitHub auth is blocked here; refresh the canonical manual execution packet instead of discovering more targets.'
    elif primary_flat and curator_queue_saturated and comparison_queue_saturated:
        lane = 'distribution_reset'
        reason = 'Curator and comparison queues are both saturated; ship a new queue-reset/discovery packet instead of pretending a fresh outreach asset exists.'
    elif primary_flat and prepared_curator_handoff_targets:
        lane = 'curator_handoff_packet'
        reason = 'Prepared curator targets exist but still lack one canonical execution packet; consolidate the best unsent targets instead of resetting the lane again.'
    elif primary_flat and (len(recent_posts) >= 2 or reddit_degraded or hn_ceiling_repeated) and not curator_measurement_saturated and not skip_curator_outreach:
        lane = 'curator_outreach'
        if reddit_degraded or hn_ceiling_repeated:
            reason = 'Monitoring is not the move right now; switch to a Codeberg-primary curator/comparison distribution lane.'
        else:
            reason = 'Owned content is saturated for now; switch to comparison-page and curator distribution prep.'

    if lane == 'directory_submission' and skip_directory_submissions:
        lane = 'directory_confirmation' if directory_confirmation_due and backlink_snapshot.get('payload') else 'distribution_reset'
        reason = (
            'Directory submissions are paused by the active repair window, so refresh approval evidence or reset target selection '
            'instead of stacking another low-intent submission.'
        )
    if lane == 'curator_outreach' and skip_curator_outreach:
        lane = 'distribution_reset' if comparison_queue_saturated else 'comparison_backlink_outreach'
        reason = (
            'Curator outreach is paused by the active repair window, so use a different lane instead of repeating the same-family burst.'
        )
    if lane == 'comparison_backlink_outreach' and comparison_lane_manual_only_blocked:
        lane = 'measurement_hold'
        reason = (
            'GitHub auth is blocked and the comparison/backlink queue is already fully prepared, so another comparison-backlink run would only create manual-only follow-through during the current review window; '
            'hold for a truthful execution slot instead of logging fake progress.'
        )
    if (
        lane == 'primary_repo_flat_contact_handoff_packet'
        and primary_repo_flat_recent_prep_repeat_count >= PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_THRESHOLD
        and not primary_repo_flat_packet_delivery_active
    ):
        lane = 'distribution_architecture_repair'
        reason = (
            'The same primary-repo-flat publisher packet keeps getting regenerated as prepared-only follow-through without entering a live delivery window; '
            'repair the distribution architecture now instead of refreshing that handoff again.'
        )
    short_window_reentry_repairs = _short_review_window_reentry_repairs_state(
        now,
        release_at=recent_live_external_release_at,
    )
    distribution_architecture_repair_state = _distribution_architecture_repair_state(
        now,
        release_at=recent_live_external_release_at,
    )
    empty_board_fallback_lanes = {'measurement_hold', 'owned_content'}
    short_window_active_with_exhausted_reentry_repairs = (
        lane in empty_board_fallback_lanes
        and recent_live_external_release_at is not None
        and now < recent_live_external_release_at
        and _execution_board_has_no_truthful_do_now_packet(now)
        and short_window_reentry_repairs['reentry_repairs_complete']
    )
    short_window_cleared_with_empty_board = (
        lane in empty_board_fallback_lanes
        and recent_live_external_release_at is not None
        and now >= recent_live_external_release_at
        and _execution_board_has_no_truthful_do_now_packet(now)
    )
    execution_board_short_window_cleared_with_empty_board = (
        lane in empty_board_fallback_lanes
        and execution_board_short_review_release_at is not None
        and now >= execution_board_short_review_release_at
        and _execution_board_has_no_truthful_do_now_packet(now)
    )
    no_short_window_idle_empty_board = (
        lane in empty_board_fallback_lanes
        and recent_live_external_release_at is None
        and _execution_board_has_no_truthful_do_now_packet(now)
    )
    guarded_empty_board_inside_active_short_window = (
        lane in empty_board_fallback_lanes
        and recent_live_external_release_at is not None
        and now < recent_live_external_release_at
        and _execution_board_has_no_truthful_do_now_packet(now)
        and distribution_architecture_repair_state.get('guard_installed')
        and bool(distribution_architecture_repair_state.get('guard_follow_through_count'))
    )
    owned_content_empty_board_inside_active_short_window = (
        lane == 'owned_content'
        and recent_live_external_release_at is not None
        and now < recent_live_external_release_at
        and _execution_board_has_no_truthful_do_now_packet(now)
    )

    if (
        lane in empty_board_fallback_lanes
        and recent_live_external_actions < SHORT_REVIEW_WINDOW_EXTERNAL_ACTION_THRESHOLD
        and (skip_directory_submissions or skip_curator_outreach)
    ) or short_window_active_with_exhausted_reentry_repairs or short_window_cleared_with_empty_board or execution_board_short_window_cleared_with_empty_board or no_short_window_idle_empty_board or guarded_empty_board_inside_active_short_window or owned_content_empty_board_inside_active_short_window:
        if distribution_architecture_repair_state['repeat_count']:
            reasons.append(
                f"{distribution_architecture_repair_state['repeat_count']} prior distribution-architecture repair run(s) already hit this same empty-board window."
            )
        if distribution_architecture_repair_state.get('guard_installed'):
            reasons.append('A third-strike distribution-architecture churn guard is already active for this same execution-board fingerprint.')
            if distribution_architecture_repair_state.get('guard_follow_through_count'):
                reasons.append(
                    f"{distribution_architecture_repair_state.get('guard_follow_through_count', 0)} prior guard follow-through run(s) already acknowledged this same fingerprint in the current review window."
                )
                if (
                    recent_live_external_release_at is not None
                    and now < recent_live_external_release_at
                    and not execution_board_short_window_cleared_with_empty_board
                    and not short_window_cleared_with_empty_board
                    and not no_short_window_idle_empty_board
                ):
                    latest_matching_at = distribution_architecture_repair_state.get('latest_matching_at')
                    earliest_guard_pause_at = distribution_architecture_repair_state.get('earliest_guard_pause_at')
                    repair_already_ran_since_guard_pause_started = bool(
                        latest_matching_at is not None
                        and earliest_guard_pause_at is not None
                        and latest_matching_at >= earliest_guard_pause_at
                    )
                    cumulative_guard_pause_count = distribution_architecture_repair_state.get(
                        'cumulative_guard_pause_count',
                        distribution_architecture_repair_state.get('guard_pause_count', 0),
                    )
                    if cumulative_guard_pause_count:
                        reasons.append(
                            f"{cumulative_guard_pause_count} prior guard pause run(s) already reused this same fingerprint in the current review window."
                        )
                        if cumulative_guard_pause_count >= DISTRIBUTION_ARCHITECTURE_GUARD_PAUSE_ESCALATION_THRESHOLD:
                            lane = 'distribution_architecture_repair'
                            reason = (
                                'The same empty-board distribution-architecture failure already hit the guard-pause path repeatedly again in this review window; '
                                'escalate into a concrete distribution-architecture repair now instead of logging another guard pause.'
                            )
                        elif repair_already_ran_since_guard_pause_started:
                            lane = 'distribution_architecture_guard_pause'
                            reason = (
                                'The same empty-board distribution-architecture failure is still under an active third-strike churn guard, '
                                'and this review window already logged both a guard pause and a concrete repair for the current fingerprint; '
                                'pause duplicate guard churn until the board fingerprint, blocker set, or live-action release window materially changes.'
                            )
                        else:
                            lane = 'distribution_architecture_repair'
                            reason = (
                                'The same empty-board distribution-architecture failure is still under an active third-strike churn guard, '
                                'but this review window already reused that pause for the current fingerprint without a newer concrete repair afterward; '
                                'perform one concrete distribution-architecture repair now instead of logging another guard pause.'
                            )
                    else:
                        lane = 'distribution_architecture_guard_pause'
                        reason = (
                            'The same empty-board distribution-architecture failure is still under an active third-strike churn guard, '
                            'and this review window already logged guard follow-through for the current fingerprint; '
                            'pause duplicate guard churn until the board fingerprint, blocker set, or live-action release window materially changes.'
                        )
                else:
                    lane = 'distribution_architecture_repair'
                    if distribution_architecture_repair_state.get('guard_pause_count'):
                        reasons.append(
                            f"{distribution_architecture_repair_state.get('guard_pause_count', 0)} prior guard pause run(s) already reused this same fingerprint in the current review window."
                        )
                    reason = (
                        'The current execution-board fingerprint is still empty even though a third-strike churn guard and guard follow-through already exist for this review window; '
                        'perform a concrete distribution-architecture repair now instead of logging another guard pause.'
                    )
            else:
                lane = 'distribution_architecture_guard_follow_through'
                reason = (
                    'The same empty-board distribution-architecture failure is already under an active third-strike churn guard for this review window; '
                    'suppress another identical repair and reuse the guard until the board fingerprint or blocker set materially changes.'
                )
        else:
            lane = 'distribution_architecture_repair'
            if distribution_architecture_repair_state['third_strike']:
                reason = (
                    'The same empty-board distribution-architecture failure already repeated twice in this short-review window; '
                    'escalate the third event into a churn-guard repair instead of another plain architecture note.'
                )
            elif short_window_active_with_exhausted_reentry_repairs:
                reason = (
                    'The short review window is still active, but the execution board is already empty and both post-hold rerun '
                    'repairs were already used in this window; repair the lane architecture instead of logging another hold.'
                )
            elif execution_board_short_window_cleared_with_empty_board:
                fallback_lane = 'owned_content' if lane == 'owned_content' else 'measurement_hold'
                reason = (
                    'The execution board\'s own short review-window blocker already cleared, but the board is still empty and the selector still '
                    f'fell back to {fallback_lane}; repair the lane architecture instead of logging another stale guard pause.'
                )
            elif no_short_window_idle_empty_board:
                fallback_lane = 'owned_content' if lane == 'owned_content' else 'measurement_hold'
                reason = (
                    'There is no active short review window anymore, but the execution board is still empty and the selector still '
                    f'fell back to {fallback_lane}; repair the lane architecture instead of logging another fake-idle hold.'
                )
            else:
                fallback_lane = 'owned_content' if lane == 'owned_content' else 'measurement_hold'
                reason = (
                    'The short review window already cleared, but every truthful external/manual lane is still blocked, exhausted, '
                    f'or already delivered; repair the lane architecture instead of letting the selector drift into {fallback_lane}.'
                )

    if (
        lane == 'distribution_architecture_guard_pause'
        and recent_live_external_release_at is not None
        and now < recent_live_external_release_at
        and _execution_board_has_no_truthful_do_now_packet(now)
        and distribution_architecture_repair_state.get('guard_installed')
        and distribution_architecture_repair_state.get('guard_follow_through_count')
        and distribution_architecture_repair_state.get(
            'cumulative_guard_pause_count',
            distribution_architecture_repair_state.get('guard_pause_count', 0),
        ) >= DISTRIBUTION_ARCHITECTURE_GUARD_PAUSE_ESCALATION_THRESHOLD
    ):
        lane = 'distribution_architecture_repair'
        reason = (
            'The same empty-board distribution-architecture failure already hit the guard-pause path repeatedly again in this review window; '
            'escalate into a concrete distribution-architecture repair now instead of logging another guard pause.'
        )

    artifact_path = str(write_action_brief(
        lane=lane,
        now=now,
        recent_posts=recent_posts,
        unsubmitted_channels=unsubmitted_channels,
        shared_findings=shared_findings,
        reason=reason,
        reasons=reasons,
        write_latest_md=persist_latest_artifacts,
    ))

    decision = LaneDecision(
        lane=lane,
        reason=reason,
        reasons=reasons,
        owned_content_posts_last_36h=len(recent_posts),
        unsubmitted_directory_channels=unsubmitted_channels,
        shared_findings_used=shared_findings,
        artifact_path=artifact_path,
        short_review_window_release_at=(
            recent_live_external_release_at.isoformat(timespec='seconds')
            if recent_live_external_release_at is not None
            and now < recent_live_external_release_at
            else None
        ),
        skip_directory_submissions=skip_directory_submissions,
        skip_curator_outreach=skip_curator_outreach,
    )
    if persist_latest_artifacts:
        return persist_latest_lane_decision(decision, now, write_action_log=write_action_log)
    if write_action_log:
        write_marketing_action_log(decision, now)
    return decision


def persist_latest_lane_decision(
    decision: LaneDecision,
    now: datetime,
    *,
    write_action_log: bool = False,
) -> LaneDecision:
    artifact_path = str(write_action_brief(
        lane=decision.lane,
        now=now,
        recent_posts=_recent_owned_content_posts(now),
        unsubmitted_channels=list(getattr(decision, 'unsubmitted_directory_channels', []) or []),
        shared_findings=list(getattr(decision, 'shared_findings_used', []) or []),
        reason=decision.reason,
        reasons=list(getattr(decision, 'reasons', []) or []),
        write_latest_md=True,
    ))
    if is_dataclass(decision):
        persisted = replace(decision, artifact_path=artifact_path)
    else:
        persisted = LaneDecision(
            lane=decision.lane,
            reason=decision.reason,
            reasons=list(getattr(decision, 'reasons', []) or []),
            owned_content_posts_last_36h=int(getattr(decision, 'owned_content_posts_last_36h', 0) or 0),
            unsubmitted_directory_channels=list(getattr(decision, 'unsubmitted_directory_channels', []) or []),
            shared_findings_used=list(getattr(decision, 'shared_findings_used', []) or []),
            artifact_path=artifact_path,
            short_review_window_release_at=getattr(decision, 'short_review_window_release_at', None),
            skip_directory_submissions=bool(getattr(decision, 'skip_directory_submissions', False)),
            skip_curator_outreach=bool(getattr(decision, 'skip_curator_outreach', False)),
        )
    LATEST_JSON.write_text(json.dumps(persisted.__dict__, indent=2) + '\n', encoding='utf-8')
    if write_action_log:
        write_marketing_action_log(persisted, now)
    return persisted


def write_action_brief(
    *,
    lane: str,
    now: datetime,
    recent_posts: list[dict[str, Any]],
    unsubmitted_channels: list[str],
    shared_findings: list[str],
    reason: str,
    reasons: list[str],
    write_latest_md: bool = True,
) -> Path:
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    artifact = DRAFTS_DIR / f"{now.strftime('%Y-%m-%d')}_distribution_action_brief.md"

    lines = [
        '# Ralph Workflow Distribution Action Brief',
        f'Generated: {now.isoformat(timespec="seconds")}',
        f'Chosen lane: **{lane}**',
        '',
        '## Why this lane',
        f'- {reason}',
    ]
    lines.extend(f'- {item}' for item in reasons)
    lines.extend(['', '## Shared findings reused'])
    lines.extend(f'- {item}' for item in shared_findings)

    if recent_posts:
        lines.extend(['', '## Recent owned-content already shipped'])
        for post in recent_posts[-3:]:
            chosen = post.get('chosen_action') or {}
            lines.append(f"- {chosen.get('title')} ({chosen.get('channel', 'unknown channel')})")

    if lane == 'directory_submission':
        lines.extend(['', '## Immediate directory submission queue'])
        for name in unsubmitted_channels[:3]:
            lines.append(f'- **{name}** — validated easy-submit channel not yet recorded in outreach history')
        lines.extend([
            '',
            '## Required payload spine',
            '- Product: Ralph Workflow',
            '- Primary URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow',
            '- Positioning: free and open-source composable loop framework and AI orchestrator',
            '- Proof asset: latest comparison or start-here Telegraph piece',
        ])
    elif lane == 'directory_confirmation':
        lines.extend([
            '',
            '## Immediate directory confirmation work',
            '- Re-run `agents/marketing/backlink_status.py` and reuse `backlink_status_latest.json` as the canonical live-listing snapshot',
            '- Treat live listings as proof assets to reuse in curator/comparison packets instead of pretending all submissions are still pending black boxes',
            '- Identify which approved listings already route to Codeberg first and which still need follow-up or evidence capture',
            '- Do not count another net-new directory submission as progress until this confirmation pass is refreshed',
        ])
    elif lane == 'distribution_confirmation_follow_through':
        lines.extend([
            '',
            '## Immediate confirmation follow-through',
            '- Reuse the existing confirmation-required action instead of creating another surface correction or directory submission',
            '- Treat email confirmation as a real blocker: the action is not outcome-ready until the platform approves it',
            '- Keep the target page and Codeberg-first routing ask exactly the same; only complete the pending platform confirmation step',
            '- Do not let the execution board claim there is no truthful do-now packet while a confirmation-required action is still waiting',
        ])
    elif lane == 'curator_outreach':
        lines.extend([
            '',
            '## Immediate curator outreach queue',
            '- Use `drafts/curator_handoff_packet_latest.md` as the canonical execution packet once targets are prepared',
            '- Use `agents/marketing/logs/curator_outreach_targets.md` for the first PR/email targets',
            '- Lead with comparison intent and Codeberg-primary proof, not another general product intro',
            '- If a live curator queue already exists, prepare only untouched targets; do not regenerate the same three targets again',
        ])
    elif lane == 'curator_handoff_packet':
        lines.extend([
            '',
            '## Immediate curator handoff work',
            '- Build one canonical packet from the highest-priority prepared curator targets that have not been sent yet',
            '- Include the comparison/backlink handoff packet too when prepared comparison targets are also waiting',
            '- Reuse the existing target-ready files instead of generating a new queue or another reset note',
            '- Include exact next actions, ready links, and Codeberg-primary wording so a human can execute quickly',
            '- Do not count another discovery/reset cycle as progress while prepared targets still await handoff',
        ])
    elif lane == 'curator_contact_handoff_packet':
        lines.extend([
            '',
            '## Immediate curator contact handoff work',
            '- Reuse `curator_contact_discovery_latest.json` and `curator_contact_handoff_packet_latest.md` instead of rebuilding the generic PR handoff packet',
            '- Advance the prepared targets that already have non-GitHub maintainer contact paths',
            '- Keep the outreach angle Codeberg-primary and comparison-led; do not slip back into a generic product intro',
            '- Refresh contact discovery only if the prepared target set changed',
            '- Do not count another queue reset or packet refresh as progress while manual-contact execution is the real next step',
        ])
    elif lane == 'primary_repo_flat_contact_handoff_packet':
        lines.extend([
            '',
            '## Immediate primary-repo-flat contact handoff work',
            '- Reuse `primary_repo_flat_contact_discovery_latest.json` and `primary_repo_flat_contact_handoff_packet_latest.md` as the canonical publisher-contact asset pair',
            '- Focus on the fresh developer-native publishers discovered in the primary-repo-flat repair, not the saturated same-family curator queue',
            '- Keep each message Codeberg-first, concrete about workflow pain, and comparison/citation oriented rather than generic outbound copy',
            '- Refresh this packet only when the discovered target set or public contact routes materially change',
            '- Treat this as a different executable lane from the blocked GitHub-PR curator packet, not another measurement-hold note',
        ])
    elif lane == 'apollo_launch_handoff_packet':
        lines.extend([
            '',
            '## Immediate Apollo launch/send handoff work',
            '- Reuse `apollo_sequence_status_latest.json` plus `drafts/apollo_sequence_launch_packet_latest.md` as the canonical managed-outbound state and launch packet',
            '- Treat the next action as live send confirmation, not another list/import/prospecting pass',
            '- Keep Codeberg as the primary CTA and preserve the existing sequence/list names unless the packet itself is stale',
            '- Once the send is visibly live, log that as a separate verification event so Apollo enters a real measurement window instead of another prep loop',
            '- Do not regenerate the generic Apollo packet while this launch-ready state is already the truthful next step',
        ])
    elif lane == 'manual_outreach_asset_follow_through':
        lines.extend([
            '',
            '## Immediate manual outreach follow-through',
            '- Reuse the existing single-target manual outreach asset instead of generating another packet',
            '- Treat that asset as the truthful follow-through surface during this hold window',
            '- Keep Codeberg as the primary CTA and do not dilute the target-specific proof angle',
            '- Do not claim the board is empty while a current manual outreach asset still exists',
        ])
    elif lane == 'comparison_backlink_outreach':
        lines.extend([
            '',
            '## Immediate comparison/backlink queue',
            '- Reuse `agents/marketing/logs/market_intelligence_latest.json` as the canonical comparison source of truth',
            '- Use `drafts/comparison_backlink_handoff_packet_latest.md` as the canonical execution packet once targets are prepared',
            '- Build outreach around the strongest existing comparison pages and current pain phrases, not another general product intro',
            '- Prepare only targets that can create a fresh Codeberg-primary backlink or comparison citation',
            '- Do not count curator queue housekeeping as progress for this lane',
        ])
    elif lane == 'curator_due_followup':
        lines.extend([
            '',
            '## Immediate curator due-follow-up work',
            '- Use the existing sent/waiting-review curator queue as the source of truth',
            '- Prepare one concrete follow-up packet for the overdue targets instead of opening a fresh outreach or reset lane',
            '- Keep the message short, publisher-native, and Codeberg-primary',
            '- Once the follow-up packet exists, wait for the next review window instead of looping on queue narration',
        ])
    elif lane == 'stackoverflow_answer_handoff_packet':
        lines.extend([
            '',
            '## Immediate StackOverflow handoff work',
            '- Reuse the existing draft(s) in `drafts/stackoverflow/` instead of rerunning the search lane',
            '- Package the best answer for manual posting or near-term reuse on other high-intent developer surfaces',
            '- Keep the answer vendor-neutral, helpful first, and Codeberg-primary only where it naturally supports the answer',
            '- If live posting is blocked, reuse the draft as a proof asset for comparison pages, outbound follow-ups, or future Q&A surfaces instead of letting it idle',
            '- Do not treat another zero-draft StackOverflow scan as progress while a fresh answer asset already exists',
        ])
    elif lane == 'repo_conversion_proof_asset':
        lines.extend([
            '',
            '## Immediate repo proof-asset work',
            '- Ship one missing proof asset that helps a repo visitor understand how the workflow composes planning, build, verification, and morning-after review',
            '- Reuse the existing first-task, review-bundle, and market-positioning artifacts instead of creating another broad top-level rewrite',
            '- Keep Codeberg as the primary CTA and add the new proof asset to the first-run path only where it reduces evaluator friction',
            '- Do not count another StackOverflow handoff refresh as progress while the current packet is already fresh',
        ])
    elif lane == 'distribution_reset':
        lines.extend([
            '',
            '## Immediate queue-reset work',
            '- Do not count curator or comparison queue follow-through alone as a fresh repair',
            '- Reuse `market_intelligence_latest.json` and current queue logs to define the next untouched target classes',
            '- Add genuinely new third-party citation/backlink targets before the next outreach-prep execution',
            '- Keep Codeberg as the only primary CTA while expanding the target universe',
        ])
    elif lane == 'measurement_hold':
        lines.extend([
            '',
            '## Immediate measurement-hold work',
            '- Do not ship another fresh outreach/reset action in this short review window',
            '- Reuse current live actions, approval windows, and handoff packets as the active queue of truth',
            '- Spend the next slot on follow-through evidence or a genuinely different executable lane only after one of the current windows ages or resolves',
            '- Treat another reset packet right now as fake progress unless a new external constraint changes the lane map',
        ])
    elif lane == 'distribution_architecture_repair':
        lines.extend([
            '',
            '## Immediate lane-architecture repair work',
            '- Do not emit another measurement hold once the short review window has already cleared',
            '- Treat this as a process-repair slot: replace stale lane-selection logic, prompts, or scheduling rules that still point back to idle holds',
            '- Preserve Codeberg as the primary CTA while forcing the next post-hold slot to choose either a truthful untouched lane or a concrete runtime repair',
            '- Use the execution board and shared findings as the truth source for what is actually blocked, exhausted, or already delivered',
        ])
    elif lane == 'distribution_architecture_guard_follow_through':
        lines.extend([
            '',
            '## Immediate lane-architecture guard follow-through work',
            '- Reuse the current execution board as the single source of truth for blocked or already-delivered assets',
            '- Acknowledge the guarded empty-board state once, without regenerating any already-current packet',
            '- Spend the next eligible slot on a materially different executable lane only after the board fingerprint or blocker set changes',
        ])
    elif lane == 'distribution_architecture_guard_pause':
        lines.extend([
            '',
            '## Immediate lane-architecture guard pause work',
            '- Do not emit another duplicate guard follow-through note for the same execution-board fingerprint in this review window',
            '- Preserve the current empty-board truth until a blocker clears or a genuinely new executable asset appears',
            '- When the fingerprint changes, force the next run to choose either a real untouched lane or a fresh architecture repair',
        ])
    elif lane == 'apollo_outreach':
        lines.extend([
            '',
            '## Immediate Apollo managed-outbound work',
            '- Reuse `market_intelligence_latest.json`, `curator_outreach_queue_latest.json`, and `comparison_backlink_queue_latest.json` as the proof spine',
            '- Build one execution packet with ICP filters, sequence copy, and Codeberg-primary CTA instead of another general marketing note',
            '- Keep GitHub framed as the mirror only; Apollo copy should route serious evaluators to Codeberg first',
            '- Use Apollo because Reddit is degraded/blocked from this environment and GitHub PR auth is not the only available path',
        ])
    elif lane == 'stackoverflow_answer':
        lines.extend([
            '',
            '## Immediate StackOverflow answer work',
            '- Use the API-backed StackOverflow answer lane instead of the older scrape-only search path',
            '- Draft answers only for questions where Ralph Workflow genuinely improves the answer; keep them helpful and non-promotional',
            '- Prioritize unanswered or weakly answered agent/workflow reliability questions over generic beginner topics',
            '- Treat drafted answers as fresh demand-capture assets while Apollo and existing outreach stay inside their measurement windows',
        ])
    else:
        lines.extend([
            '',
            '## Owned-content lane remains allowed',
            '- No distribution-lane override triggered yet',
            '- If the next measurement window is still flat, escalate away from Telegraph-first output',
        ])

    text = '\n'.join(lines) + '\n'
    artifact.write_text(text, encoding='utf-8')
    if write_latest_md:
        LATEST_MD.write_text(text, encoding='utf-8')
    return artifact


def write_marketing_action_log(decision: LaneDecision, now: datetime) -> Path:
    payload = {
        'timestamp': now.isoformat(),
        'run_type': 'marketing-distribution-lane',
        'chosen_action': {
            'type': 'distribution_lane_switch' if decision.lane != 'owned_content' else 'owned_content_allowed',
            'channel': decision.lane,
            'title': f'Distribution lane decision: {decision.lane}',
            'draft': decision.artifact_path,
        },
        'why_this_action': {
            'shared_findings_used': decision.shared_findings_used,
            'reasoning': decision.reason,
            'supporting_reasons': decision.reasons,
        },
        'result': {
            'status': 'prepared',
            'ok': True,
        },
    }
    if decision.lane in {'distribution_architecture_repair', 'distribution_architecture_guard_follow_through', 'distribution_architecture_guard_pause'}:
        payload['verification'] = {
            'execution_board_fingerprint': _execution_board_fingerprint(),
        }
    path = LOG_DIR / f"marketing_{now.strftime('%Y-%m-%d')}_{decision.lane}.json"
    path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    return path


def main() -> int:
    decision = choose_distribution_lane()
    print(json.dumps(decision.__dict__, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
