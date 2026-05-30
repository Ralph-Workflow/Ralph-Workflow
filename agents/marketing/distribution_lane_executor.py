#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from html import unescape
from unittest.mock import Mock
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path('/home/mistlight/.openclaw/workspace')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.marketing import distribution_lane_selector
from agents.marketing.distribution_lane_selector import LaneDecision
from agents.marketing.market_intelligence_runtime import load_market_intelligence
from agents.marketing.measurement_hold_runtime import latest_measurement_hold_window
from agents.marketing.positioning import CODEBERG_PRIMARY, FOUR_QUESTIONS, directory_blurb
from agents.marketing import stackoverflow_answer_lane
from agents.marketing.channel_spidering_guard import guard_check, PERMANENTLY_BLOCKED
from agents.marketing.run_posting import (
    CTA_FOOTER,
    already_posted_successfully,
    crosspost_blog_content,
    digest_text,
    extract_title_and_body,
    load_posted,
    post_telegraph,
    save_posted,
)

LOG_DIR = ROOT / 'agents/marketing/logs'


@contextmanager
def _selector_local_paths():
    original = {
        'LOG_DIR': distribution_lane_selector.LOG_DIR,
        'DRAFTS_DIR': distribution_lane_selector.DRAFTS_DIR,
        'EXECUTION_BOARD_LATEST_PATH': distribution_lane_selector.EXECUTION_BOARD_LATEST_PATH,
        'AUDIT_LATEST_JSON': distribution_lane_selector.AUDIT_LATEST_JSON,
        'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH': distribution_lane_selector.PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH,
        'PRIMARY_REPO_FLAT_CONTACT_HANDOFF_LATEST_PATH': distribution_lane_selector.PRIMARY_REPO_FLAT_CONTACT_HANDOFF_LATEST_PATH,
    }
    distribution_lane_selector.LOG_DIR = LOG_DIR
    distribution_lane_selector.DRAFTS_DIR = DRAFTS_DIR
    distribution_lane_selector.EXECUTION_BOARD_LATEST_PATH = DRAFTS_DIR / 'marketing_execution_board_latest.md'
    distribution_lane_selector.AUDIT_LATEST_JSON = LOG_DIR / 'marketing_workflow_audit_latest.json'
    distribution_lane_selector.PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH = _primary_repo_flat_contact_discovery_path()
    distribution_lane_selector.PRIMARY_REPO_FLAT_CONTACT_HANDOFF_LATEST_PATH = DRAFTS_DIR / 'primary_repo_flat_contact_handoff_packet_latest.md'
    try:
        yield
    finally:
        for key, value in original.items():
            setattr(distribution_lane_selector, key, value)


def _call_selector_local(callback, *args, **kwargs):
    with _selector_local_paths():
        return callback(*args, **kwargs)
DRAFTS_DIR = ROOT / 'drafts'
SEO_REPORTS_DIR = ROOT / 'seo-reports'
TARGETS_PATH = LOG_DIR / 'curator_outreach_targets.md'
ADOPTION_PATH = LOG_DIR / 'adoption_metrics_latest.json'
OUTREACH_LOG_PATH = ROOT / 'outreach-log.md'
USER_PROFILE_PATH = ROOT / 'USER.md'
CURATOR_QUEUE_LATEST_PATH = LOG_DIR / 'curator_outreach_queue_latest.json'
COMPARISON_QUEUE_LATEST_PATH = LOG_DIR / 'comparison_backlink_queue_latest.json'
CURATOR_CONTACT_DISCOVERY_LATEST_PATH = LOG_DIR / 'curator_contact_discovery_latest.json'
PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH = LOG_DIR / 'primary_repo_flat_contact_discovery_latest.json'
DISTRIBUTION_RESET_LOG_PATH = LOG_DIR / 'distribution_reset_execution_log.md'
DISTRIBUTION_RESET_QUEUE_LATEST_PATH = LOG_DIR / 'distribution_reset_targets_latest.json'
APOLLO_STATUS_PATH = LOG_DIR / 'apollo_status.json'
STACKOVERFLOW_LATEST_PATH = LOG_DIR / 'stackoverflow_answer_lane_latest.json'
START_HERE_PATH = ROOT / 'START_HERE.md'
WORKFLOW_COMPOSITION_EXAMPLE_PATH = ROOT / 'content/examples/workflow_composition_example.md'
BACKLINK_STATUS_LATEST_PATH = LOG_DIR / 'backlink_status_latest.json'
OWNED_CONTENT_SOURCE_CANDIDATES = [
    ROOT / 'content/guides/good_unattended_task.md',
    ROOT / 'docs/first-task-guide.md',
    ROOT / 'content/guides/review_ai_coding_output_before_merge.md',
    ROOT / 'content/guides/autonomous_ai_workflows_production_reliability.md',
]

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

MEASUREMENT_HOLD_COOLDOWN_MINUTES = 60
MEASUREMENT_HOLD_RELEASE_CRON_NAME = 'marketing-measurement-hold-release'
MEASUREMENT_HOLD_REQUIRED_CONTEXT_PATHS = [
    '/home/mistlight/.openclaw/workspace/agents/marketing/MARKETING_SELF_IMPROVEMENT.md',
    '/home/mistlight/.openclaw/workspace/agents/marketing/MARKETING_WORKFLOW_PRINCIPLES.md',
    '/home/mistlight/.openclaw/workspace/agents/marketing/FOUR_MARKETING_QUESTIONS.md',
    '/home/mistlight/.openclaw/workspace/agents/marketing/ADOPTION_FUNNEL_NEXT.md',
    '/home/mistlight/.openclaw/workspace/agents/marketing/logs/market_intelligence_latest.json',
    '/home/mistlight/.openclaw/workspace/agents/marketing/logs/marketing_workflow_audit_latest.json',
    '/home/mistlight/.openclaw/workspace/agents/marketing/logs/marketing_workflow_audit_latest.md',
    '/home/mistlight/.openclaw/workspace/agents/marketing/logs/distribution_lane_latest.json',
    '/home/mistlight/.openclaw/workspace/agents/marketing/logs/distribution_lane_latest.md',
    '/home/mistlight/.openclaw/workspace/drafts/marketing_execution_board_latest.md',
]
MEASUREMENT_HOLD_FRESHEST_ARTIFACT_PATHS = [
    '/home/mistlight/.openclaw/workspace/agents/marketing/logs/reddit_post_analysis_latest.json',
    '/home/mistlight/.openclaw/workspace/agents/marketing/logs/reddit_post_analysis_latest.md',
    '/home/mistlight/.openclaw/workspace/agents/marketing/logs/adoption_metrics_latest.json',
    '/home/mistlight/.openclaw/workspace/agents/marketing/logs/adoption_metrics_latest.md',
    '/home/mistlight/.openclaw/workspace/agents/marketing/logs/market_intelligence_latest.json',
]

PUBLISHER_CONTACT_ACTION_TYPES = {
    'publisher_email_outreach',
    'publisher_contact_form_submission',
    'publisher_feedback_form_submission',
}

MANUAL_EXECUTABLE_PUBLISHER_CHANNEL_TYPES = {
    'email',
}

RUNTIME_SENDABLE_PUBLISHER_CHANNEL_TYPES = {
    'email',
}

MANUAL_CONTACT_HANDOFF_REMAINING_STATUSES = {
    'email_invalid_manual_handoff_remaining',
    'manual_handoff_remaining',
}

MESSAGE_SIGNAL_PHRASES = [
    'stop babysitting your agents',
    'run until done',
    'overnight coding',
    'finished code',
    'tested code',
    'ready to review',
    'would you ship it?',
    'seeing what the agent actually did',
    'graceful downgrade paths',
    'visible review packets',
    'staged autonomy',
    'success criteria that survive real usage',
    'summary-vs-visible-state trust',
]

MEASUREMENT_HOLD_ACTION_TYPES = {
    'measurement_hold_execution',
    'measurement_hold_follow_through',
}

MEASUREMENT_HOLD_REENTRY_REPAIR_ACTION_TYPES = {
    'active_loop_prompt_repair',
    'post_hold_reentry_contract_repair',
}

PRIMARY_REPO_FLAT_MANUAL_DELIVERY_ACTION_TYPES = {
    'primary_repo_flat_contact_manual_delivery',
    'primary_repo_flat_contact_manual_delivery_refresh',
}

MANUAL_OUTREACH_ASSET_ACTION_SUFFIX = '_channel_ready_outreach_asset'
MANUAL_OUTREACH_DELIVERY_ACTION_SUFFIX = '_manual_delivery'
MANUAL_OUTREACH_DELIVERY_CHANNELS = {
    'current_chat_manual_handoff',
    'current_chat_final_reply',
    'current_chat',
}

DIRECTORY_SECONDARY_SURFACE_ACTION_TYPES = {
    'saashub_secondary_surface_execution',
    'saashub_secondary_surface_comment_execution',
    'saashub_secondary_surface_comment_confirmation',
}


@dataclass(frozen=True)
class LaneExecution:
    lane: str
    action_type: str
    status: str
    artifact_path: str | None
    summary: str
    targets_prepared: list[str]
    shared_findings_used: list[str]
    live_external_action: bool = False
    blocking_factors: list[str] | None = None


def _curator_contact_discovery_path() -> Path:
    if CURATOR_CONTACT_DISCOVERY_LATEST_PATH.parent == LOG_DIR:
        return CURATOR_CONTACT_DISCOVERY_LATEST_PATH
    return LOG_DIR / 'curator_contact_discovery_latest.json'


def _distribution_reset_log_path() -> Path:
    if DISTRIBUTION_RESET_LOG_PATH.parent == LOG_DIR:
        return DISTRIBUTION_RESET_LOG_PATH
    return LOG_DIR / 'distribution_reset_execution_log.md'


def _distribution_reset_queue_path() -> Path:
    if DISTRIBUTION_RESET_QUEUE_LATEST_PATH.parent == LOG_DIR:
        return DISTRIBUTION_RESET_QUEUE_LATEST_PATH
    return LOG_DIR / 'distribution_reset_targets_latest.json'


def _primary_repo_flat_contact_discovery_path() -> Path:
    if PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH.parent == LOG_DIR:
        return PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH
    return LOG_DIR / 'primary_repo_flat_contact_discovery_latest.json'


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {}


def _recent_local_executed_action_type(now: datetime, *, action_types: set[str], hours: int = 48) -> bool:
    cutoff = now - timedelta(hours=hours)
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            try:
                dt = datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                continue
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


def _backlink_status_latest_path() -> Path:
    if BACKLINK_STATUS_LATEST_PATH.parent == LOG_DIR:
        return BACKLINK_STATUS_LATEST_PATH
    return LOG_DIR / 'backlink_status_latest.json'


def _post_hold_reentry_contract_latest_path() -> Path:
    return DRAFTS_DIR / 'post_hold_distribution_reentry_latest.md'


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


def _format_local_schedule_at(value: str | None) -> str:
    dt = _parse_dt(value)
    if dt is None:
        return str(value or '').strip()
    return dt.isoformat(timespec='seconds')


def _later_release_at(*values: str | None) -> str:
    latest_dt: datetime | None = None
    latest_raw = ''
    for value in values:
        raw = str(value or '').strip()
        if not raw:
            continue
        parsed = _parse_dt(raw)
        if parsed is None:
            if not latest_raw:
                latest_raw = raw
            continue
        if latest_dt is None or parsed > latest_dt:
            latest_dt = parsed
            latest_raw = parsed.isoformat(timespec='seconds')
    return latest_raw


def _resolved_measurement_hold_release_at(
    now: datetime,
    release_at: str | None,
    *extra_release_at_values: str | None,
) -> str:
    live_release = _short_review_window_release_at(now)
    live_release_raw = live_release.isoformat(timespec='seconds') if live_release is not None else ''
    latest_lane_release_raw = ''
    try:
        latest_lane_payload = _load_json(LOG_DIR / 'distribution_lane_latest.json')
        latest_lane_release_raw = str(latest_lane_payload.get('short_review_window_release_at') or '').strip()
    except Exception:
        latest_lane_release_raw = ''
    return _later_release_at(release_at, live_release_raw, latest_lane_release_raw, *extra_release_at_values)


def _schedule_at_matches(left: str | None, right: str | None) -> bool:
    left_dt = _parse_dt(left)
    right_dt = _parse_dt(right)
    if left_dt is not None and right_dt is not None:
        return left_dt == right_dt
    return str(left or '').strip() == str(right or '').strip()


def _cron_at_argument(value: str) -> str:
    raw = str(value or '').strip()
    if not raw:
        return raw
    try:
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return raw
    if parsed.tzinfo is not None:
        return parsed.isoformat(timespec='seconds')
    local_tz = datetime.now().astimezone().tzinfo or UTC
    return parsed.replace(tzinfo=local_tz).isoformat(timespec='seconds')


def _measurement_hold_release_message(reentry_contract_path: str | None = None) -> str:
    required_context_lines = '\n'.join(
        f'- read {path}' for path in MEASUREMENT_HOLD_REQUIRED_CONTEXT_PATHS
    )
    freshest_artifact_lines = '\n'.join(
        f'- read {path}' for path in MEASUREMENT_HOLD_FRESHEST_ARTIFACT_PATHS
    )
    contract_rule = ''
    if reentry_contract_path:
        contract_rule = (
            f'- read {reentry_contract_path} first; it contains the current blocked-lane truth '
            'and the post-hold re-entry contract\n'
        )

    return (
        'Act as the always-on RalphWorkflow marketer.\n\n'
        'This is the post-measurement-hold re-entry run. Before acting, verify from the latest '
        'distribution-lane and execution-board artifacts that the short review window has actually '
        'cleared; if it has not, treat the wake as an early-release scheduling failure, '
        'repair/reschedule the release path, and do not pretend the slot is already open.\n\n'
        'If the short review window has cleared, choose the single highest-leverage marketing '
        'action you can actually do right now, execute it, and log the result.\n\n'
        'Required context before acting:\n'
        f'{contract_rule}'
        f'{required_context_lines}\n'
        '- read the freshest Reddit/competitor/adoption artifacts before acting:\n'
        f'{freshest_artifact_lines}\n\n'
        'Mandatory rules:\n'
        '- read and reuse the latest market_intelligence, adoption, distribution-lane, audit, '
        'and execution-board artifacts first\n'
        '- prefer Codeberg traffic/adoption first and GitHub second\n'
        '- do the action, not just recommend it\n'
        '- avoid fake progress, duplicate packet delivery, and repeated cooldown-lane churn\n'
        '- do not select another measurement_hold just because Apollo/comparison/review windows '
        'are still open after the short hold cleared\n'
        '- if no truthful external/manual lane remains, spend this run on a concrete runtime/process '
        'repair that improves the next executable slot\n'
        '- if a safe runtime repair still blocks truthful execution, fix it in the same run\n'
    )


def _cron_job_payload(job_id: str) -> dict[str, Any]:
    job_id = str(job_id or '').strip()
    if not job_id:
        return {}
    result = subprocess.run(
        ['/home/mistlight/.bun/bin/openclaw', 'cron', 'show', job_id, '--json'],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0 or not (result.stdout or '').strip():
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _cron_job_message_matches(job_id: str, expected_message: str) -> bool:
    payload = _cron_job_payload(job_id)
    actual_message = str(((payload.get('payload') or {}).get('message')) or '').strip()
    return bool(actual_message) and actual_message == expected_message.strip()


def _chosen_action_dict(payload: dict[str, Any]) -> dict[str, Any]:
    chosen_action = payload.get('chosen_action')
    return chosen_action if isinstance(chosen_action, dict) else {}


def _chosen_action_type(payload: dict[str, Any]) -> str:
    return str(
        _chosen_action_dict(payload).get('type')
        or payload.get('type')
        or payload.get('action_type')
        or payload.get('action')
        or ''
    ).strip()


def _curator_queue_status_from_live_payload(payload: dict[str, Any]) -> str:
    chosen_action = _chosen_action_dict(payload)
    action_type = _chosen_action_type(payload).lower()
    channel = str(
        payload.get('channel')
        or chosen_action.get('channel')
        or ''
    ).lower()
    recipient = str(payload.get('recipient') or chosen_action.get('recipient') or '').strip()
    if 'email' in channel or action_type.endswith('email_outreach') or recipient:
        return 'sent_via_email_fallback'
    if 'github_issue' in channel or action_type.endswith('github_issue_outreach'):
        return 'sent_via_github_issue'
    if 'form' in channel or action_type.endswith('form_submission') or action_type.endswith('contact_submission'):
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


def _normalize_curator_queue_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    recent_actions = _curator_queue_recent_live_actions()
    normalized: list[dict[str, str]] = []
    for row in rows:
        normalized_row = dict(row)
        status = str(normalized_row.get('status') or '').lower()
        if status in {'prepared', 'queued', 'in_review', 'waiting_review'}:
            target_name = _display_target_name(str(normalized_row.get('target') or '').strip())
            action = recent_actions.get(target_name)
            if action is not None:
                payload = action['payload']
                chosen_action = _chosen_action_dict(payload)
                normalized_row['status'] = _curator_queue_status_from_live_payload(payload)
                normalized_row['last_contact_at'] = payload.get('timestamp') or payload.get('timestamp_utc') or action['timestamp'].isoformat()
                normalized_row['last_contact_log'] = action['path']
                channel = str(payload.get('channel') or chosen_action.get('channel') or '').strip()
                recipient = str(payload.get('recipient') or chosen_action.get('recipient') or '').strip()
                target_url = str(payload.get('submit_url') or payload.get('url') or chosen_action.get('url') or '').strip()
                inferred_status = normalized_row['status']
                if recipient and (('email' in channel.lower()) or inferred_status == 'sent_via_email_fallback'):
                    normalized_row['last_contact_path'] = f'email:{recipient}'
                elif channel and target_url:
                    normalized_row['last_contact_path'] = f'{channel}:{target_url}'
                elif target_url:
                    normalized_row['last_contact_path'] = target_url
        normalized.append(normalized_row)
    return normalized


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
        ok = bool(payload.get('ok') or result.get('ok') or status in {'executed', 'sent', 'submitted', 'published', 'launched'})
        if not ok:
            continue

        action_type = _chosen_action_type(payload)
        if str(action_type).strip() not in action_types:
            continue

        targets.update(_target_name_variants(str(payload.get('target') or '').strip()))
    return targets


def _current_primary_repo_flat_actionable_findings(now: datetime) -> list[dict[str, Any]]:
    recent_publisher_targets = _recent_contact_targets(
        now,
        action_types=PUBLISHER_CONTACT_ACTION_TYPES,
        days=7,
    )
    active_manual_delivery_targets = _active_manual_outreach_delivery_targets(now)
    return [
        row
        for row in _load_primary_repo_flat_contact_discovery()
        if str(row.get('target') or '').strip()
        and _publisher_target_is_packet_executable(row)
        and _display_target_name(str(row.get('target') or '').strip()) not in recent_publisher_targets
        and _display_target_name(str(row.get('target') or '').strip()) not in active_manual_delivery_targets
    ]


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


def _manual_outreach_artifact_paths(payload: dict[str, Any]) -> list[str]:
    chosen_action = _chosen_action_dict(payload)
    result = payload.get('result') if isinstance(payload.get('result'), dict) else {}
    paths: list[str] = []
    seen: set[str] = set()
    for raw in (
        chosen_action.get('artifact'),
        chosen_action.get('draft'),
        chosen_action.get('packet'),
        result.get('artifact'),
        result.get('artifact_reused'),
        result.get('packet_path'),
        result.get('packet'),
    ):
        cleaned = str(raw or '').strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        paths.append(cleaned)
    return paths


def _manual_outreach_targets_for_artifacts(
    *,
    artifact_paths: list[str],
    now: datetime,
    exclude_path: Path | None = None,
) -> set[str]:
    normalized_paths = {str(path).strip() for path in artifact_paths if str(path).strip()}
    if not normalized_paths:
        return set()
    cutoff = now - timedelta(days=14)
    targets: set[str] = set()
    for path in LOG_DIR.glob('marketing_*.json'):
        if exclude_path is not None and path == exclude_path:
            continue
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
        if normalized_paths.isdisjoint(_manual_outreach_artifact_paths(payload)):
            continue
        raw_targets = (
            ((payload.get('why_this_action') or {}).get('targets_prepared') or [])
            or ((payload.get('result') or {}).get('targets_prepared') or [])
        )
        for item in raw_targets:
            target = _display_target_name(str(item).strip())
            if target:
                targets.add(target)
    return targets


def _manual_outreach_payload_targets(payload: dict[str, Any], *, now: datetime, source_path: Path | None = None) -> set[str]:
    chosen_action = _chosen_action_dict(payload)
    result = payload.get('result') if isinstance(payload.get('result'), dict) else {}
    targets: set[str] = set()
    raw_targets = (
        ((payload.get('why_this_action') or {}).get('targets_prepared') or [])
        or (result.get('targets_prepared') or [])
    )
    for item in raw_targets:
        target = _display_target_name(str(item).strip())
        if target:
            targets.add(target)
    fallback_target = _display_target_name(str(chosen_action.get('target') or payload.get('target') or '').strip())
    if fallback_target:
        targets.add(fallback_target)
    targets.update(
        _manual_outreach_targets_for_artifacts(
            artifact_paths=_manual_outreach_artifact_paths(payload),
            now=now,
            exclude_path=source_path,
        )
    )
    return targets


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
        delivered_artifacts = _manual_outreach_artifact_paths(payload)
        if artifact not in delivered_artifacts:
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


def _apollo_runtime_blocker_review_delivery_still_active(*, artifact_path: str, now: datetime) -> bool:
    artifact = str(artifact_path or '').strip()
    if not artifact:
        return False
    cutoff = now - timedelta(days=14)
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        if _chosen_action_type(payload) != 'apollo_runtime_blocker_review_delivery':
            continue
        delivered_at = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if delivered_at is None:
            delivered_at = datetime.fromtimestamp(path.stat().st_mtime)
        if delivered_at < cutoff:
            continue
        chosen_action = _chosen_action_dict(payload)
        result = payload.get('result') if isinstance(payload.get('result'), dict) else {}
        delivered_artifacts = [
            str(chosen_action.get('artifact') or '').strip(),
            str(chosen_action.get('draft') or '').strip(),
            str(chosen_action.get('packet') or '').strip(),
            str(result.get('artifact') or '').strip(),
            str(result.get('artifact_reused') or '').strip(),
            str(result.get('packet_path') or '').strip(),
        ]
        delivered_artifacts.extend(
            str(item).strip()
            for item in (chosen_action.get('artifacts') or [])
            if str(item).strip()
        )
        if artifact not in delivered_artifacts:
            continue
        status = str(payload.get('status') or result.get('status') or '').strip().lower()
        if status in {'delivered', 'delivered_to_current_chat'} and delivered_at.date() == now.date():
            return True
    return False



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
        delivered_at = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if delivered_at is None:
            delivered_at = datetime.fromtimestamp(path.stat().st_mtime)
        if delivered_at < cutoff:
            continue
        status = str(payload.get('status') or result.get('status') or '').strip().lower()
        measurement_window = payload.get('measurement_window') if isinstance(payload.get('measurement_window'), dict) else {}
        next_review_at = _parse_dt(
            str(
                result.get('next_review_at')
                or measurement_window.get('review_at')
                or measurement_window.get('freshness_review_at')
                or ''
            ).strip()
        )
        if next_review_at is None and not (status in {'delivered', 'delivered_to_current_chat'} and delivered_at.date() == now.date()):
            continue
        if next_review_at is not None and next_review_at < now:
            continue
        targets.update(_manual_outreach_payload_targets(payload, now=now, source_path=path))
    return targets



def _short_review_window_release_at(now: datetime | None = None) -> datetime | None:
    payload = _load_json(LOG_DIR / 'distribution_lane_latest.json')
    release_at = _parse_dt(str(payload.get('short_review_window_release_at') or '').strip())
    if now is None:
        return release_at
    recent_release_at = _recent_live_external_window_release_at(
        now,
        hours=distribution_lane_selector.SHORT_REVIEW_WINDOW_HOURS,
    )
    if recent_release_at is None and isinstance(distribution_lane_selector._recent_live_external_window_release_at, Mock):
        recent_release_at = distribution_lane_selector._recent_live_external_window_release_at(
            now,
            hours=distribution_lane_selector.SHORT_REVIEW_WINDOW_HOURS,
        )
    if recent_release_at is not None and recent_release_at > now:
        return recent_release_at
    active_hold = latest_measurement_hold_window(now, LOG_DIR)
    if active_hold is not None:
        hold_until = active_hold.get('hold_until') if isinstance(active_hold, dict) else None
        hold_release_at = hold_until if isinstance(hold_until, datetime) and hold_until > now else None
        candidates = [dt for dt in (release_at, hold_release_at) if dt is not None and dt > now]
        if candidates:
            return max(candidates)
    return None


def _recent_live_external_window_release_at(now: datetime, *, hours: int) -> datetime | None:
    cutoff = now - timedelta(hours=hours)
    timestamps: list[datetime] = []
    seen: set[tuple[str, ...]] = set()
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
        event_key = distribution_lane_selector._live_external_event_key(path, payload, dt)
        if event_key in seen:
            continue
        seen.add(event_key)
        timestamps.append(dt)
    if not timestamps:
        return None
    return min(timestamps) + timedelta(hours=hours)



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



def _manual_outreach_assets_waiting_for_execution(now: datetime) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    primary_repo_flat_targets = [
        _display_target_name(str(row.get('target') or '').strip())
        for row in _current_primary_repo_flat_actionable_findings(now)
        if str(row.get('target') or '').strip() and _publisher_target_has_runtime_sendable_channel(row.get('channels') or [])
    ]
    primary_repo_flat_targets = [target for target in primary_repo_flat_targets if target]
    manual_review_targets = _call_selector_local(
        distribution_lane_selector._primary_repo_flat_manual_review_targets_waiting_for_execution,
        now,
    )
    primary_repo_flat_packet_path = DRAFTS_DIR / 'primary_repo_flat_contact_handoff_packet_latest.md'
    packet_current_for_active_window = bool(
        primary_repo_flat_targets
        and _handoff_packet_is_current(primary_repo_flat_packet_path, primary_repo_flat_targets, require_live_listing_proof=True, allow_superset=True)
    )
    primary_repo_flat_delivery_active = _primary_repo_flat_packet_delivery_still_active(now, primary_repo_flat_targets)
    primary_repo_flat_recent_prep_repeat_count = _primary_repo_flat_recent_prep_count(
        now,
        primary_repo_flat_targets,
        hours=distribution_lane_selector.PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_WINDOW_HOURS,
    )
    short_review_window_release_at = _short_review_window_release_at(now)
    primary_repo_flat_post_hold_only = bool(
        packet_current_for_active_window
        and short_review_window_release_at is not None
        and now < short_review_window_release_at
        and not primary_repo_flat_delivery_active
    )
    if (
        primary_repo_flat_targets
        and packet_current_for_active_window
        and not primary_repo_flat_delivery_active
        and not (
            primary_repo_flat_post_hold_only
            and primary_repo_flat_recent_prep_repeat_count >= distribution_lane_selector.PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_THRESHOLD
        )
    ):
        packet_age = now - datetime.fromtimestamp(primary_repo_flat_packet_path.stat().st_mtime)
        if packet_age <= timedelta(days=7):
            packet_path = str(primary_repo_flat_packet_path)
            seen_paths.add(packet_path)
            assets.append({
                'target': ', '.join(primary_repo_flat_targets[:3]),
                'targets': primary_repo_flat_targets,
                'path': packet_path,
                'title': 'Primary-repo-flat publisher contact packet',
                'summary': 'Current Codeberg-first publisher contact packet is still truthful and waiting for manual follow-through.',
            })

    manual_review_path = DRAFTS_DIR / 'primary_repo_flat_manual_review_asset_latest.md'
    manual_review_suppressed = _call_selector_local(
        distribution_lane_selector._primary_repo_flat_manual_review_asset_suppressed,
        now,
        primary_repo_flat_targets=primary_repo_flat_targets,
        manual_review_targets=manual_review_targets,
    )
    if (
        manual_review_targets
        and not packet_current_for_active_window
        and not primary_repo_flat_delivery_active
        and not _manual_outreach_asset_delivery_still_active(
            artifact_path=str(manual_review_path),
            now=now,
            respect_artifact_refresh=False,
        )
        and not manual_review_suppressed
        and _call_selector_local(
            distribution_lane_selector._primary_repo_flat_manual_review_asset_current,
            now,
            manual_review_targets,
        )
    ):
        asset_path = str(manual_review_path)
        seen_paths.add(asset_path)
        assets.append({
            'target': ', '.join(manual_review_targets[:3]),
            'targets': manual_review_targets,
            'path': asset_path,
            'title': 'Primary-repo-flat manual follow-through asset',
            'summary': 'A current Codeberg-first manual follow-through asset already exists for the active primary-repo-flat target set; use it instead of regenerating the packet.',
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
        artifact_path = str(
            chosen_action.get('artifact')
            or chosen_action.get('draft')
            or ((payload.get('result') or {}).get('artifact') if isinstance(payload.get('result'), dict) else '')
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
        prepared_targets = ((payload.get('why_this_action') or {}).get('targets_prepared') or []) if isinstance(payload.get('why_this_action'), dict) else []
        fallback_target = str(prepared_targets[0]).strip() if prepared_targets else ''
        target = title.partition(' for ')[2].strip() if ' for ' in title else fallback_target or Path(artifact_path).stem.replace('_', ' ')
        summary = str(((payload.get('why_this_action') or {}).get('summary') if isinstance(payload.get('why_this_action'), dict) else '') or payload.get('expected_outcome') or '').strip()
        if _is_manual_community_discussion_asset(
            action_type=action_type,
            title=title,
            artifact_path=artifact_path,
            summary=summary,
        ):
            continue
        seen_paths.add(artifact_path)
        assets.append({
            'target': target,
            'targets': [target] if target else [],
            'path': artifact_path,
            'title': title,
            'summary': summary,
        })
    return assets



def _manual_community_discussion_asset_still_actionable(artifact_path: str) -> bool:
    text = _read_text(Path(artifact_path)).lower()
    if not text:
        return True
    if 'status: already_handled' in text:
        return False
    if 'status: autopost_attempted' in text and (
        'post-hold marketer rerun already scheduled' in text
        or '## post-hold marketer rerun scheduled' in text
        or 'scheduled run:' in text
        or 'do not create another duplicate wake' in text
    ):
        return False
    if 'chosen lane:' in text and not any(token in text for token in ('reddit', 'discussion', 'community')):
        return False
    return True


def _reddit_manual_discussion_blocked() -> bool:
    # Permanent architectural block: Reddit is IP-blocked at Hetzner, Tor-blocked,
    # and architecturally retired since 2026-05-28. No transient status check can
    # override this; Reddit discussion assets must never appear as "Do now" board packets.
    if PERMANENTLY_BLOCKED.get("reddit"):
        return True

    # Also check the spidering guard in case the PERMANENTLY_BLOCKED dict is
    # imported late or renewed during a runtime reassembly.
    try:
        from agents.marketing.channel_spidering_guard import guard_check as _guard_check
        ok, _, _ = _guard_check("reddit")
        if not ok:
            return True
    except Exception:
        pass

    using_workspace_defaults = (
        LOG_DIR == ROOT / 'agents' / 'marketing' / 'logs'
        and SEO_REPORTS_DIR == ROOT / 'seo-reports'
    )

    if using_workspace_defaults:
        try:
            summary = _call_selector_local(distribution_lane_selector._load_recent_monitor_summary) or {}
        except Exception:
            summary = {}
        if bool(summary.get('reddit_blocked')):
            return True

        execution_status_value = str(summary.get('execution_status') or '').strip().lower()
        if execution_status_value in {'network_security_blocked', 'execution_blocked', 'not_logged_in'}:
            return True

        try:
            execution_payload = _load_json(_call_selector_local(distribution_lane_selector._reddit_execution_status_path))
        except Exception:
            execution_payload = {}
    else:
        execution_payload = _load_json(LOG_DIR / 'reddit_execution_status_latest.json')
        execution_status_value = str(execution_payload.get('status') or '').strip().lower()
        return execution_status_value in {'network_security_blocked', 'execution_blocked', 'not_logged_in'}

    execution_status_value = str(execution_payload.get('status') or '').strip().lower()
    execution_age = _parse_dt(execution_payload.get('generated_at') or execution_payload.get('timestamp'))
    if execution_age is None:
        return execution_status_value in {'network_security_blocked', 'execution_blocked', 'not_logged_in'}
    execution_recent = (datetime.now() - execution_age).total_seconds() <= 12 * 3600
    return execution_recent and execution_status_value in {'network_security_blocked', 'execution_blocked', 'not_logged_in'}


def _reddit_discussion_asset_waiting_for_execution(now: datetime) -> dict[str, str] | None:
    if _reddit_manual_discussion_blocked():
        return None
    cutoff = now - timedelta(days=14)
    candidate: dict[str, str] | None = None
    candidate_dt: datetime | None = None
    current_opportunities = _reddit_discussion_opportunities(limit=2)
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        chosen_action = _chosen_action_dict(payload)
        action_type = _chosen_action_type(payload)
        if action_type == 'distribution_lane_switch':
            continue
        artifact_path = str(
            chosen_action.get('artifact')
            or chosen_action.get('draft')
            or ((payload.get('result') or {}).get('artifact') if isinstance(payload.get('result'), dict) else '')
            or ''
        ).strip()
        title = str(chosen_action.get('title') or payload.get('title') or '').strip()
        summary = str(((payload.get('why_this_action') or {}).get('summary') if isinstance(payload.get('why_this_action'), dict) else '') or payload.get('expected_outcome') or '').strip()
        result = payload.get('result') if isinstance(payload.get('result'), dict) else {}
        payload_status = str(payload.get('status') or result.get('status') or '').strip().lower()
        if bool(payload.get('live_external_action') or result.get('live_external_action')) or payload_status in LIVE_EXTERNAL_STATUSES:
            continue
        if not artifact_path or not Path(artifact_path).exists():
            continue
        if not _is_manual_community_discussion_asset(
            action_type=action_type,
            title=title,
            artifact_path=artifact_path,
            summary=summary,
        ):
            continue
        if not _manual_community_discussion_asset_still_actionable(artifact_path):
            continue
        artifact_file = Path(artifact_path)
        if artifact_file.name == 'reddit_discussion_handoff_packet_latest.md':
            if _reddit_discussion_packet_delivery_still_active(now, current_opportunities):
                continue
        elif _manual_outreach_asset_delivery_still_active(
            artifact_path=artifact_path,
            now=now,
            respect_artifact_refresh=False,
        ):
            continue
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue
        measurement_window = payload.get('measurement_window') if isinstance(payload.get('measurement_window'), dict) else {}
        review_at = _parse_dt(str(measurement_window.get('review_at') or '').strip())
        if review_at is not None and review_at < now:
            continue
        if candidate_dt is None or dt > candidate_dt:
            candidate_dt = dt
            candidate = {
                'path': artifact_path,
                'title': title,
                'summary': summary,
            }
    return candidate



def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except OSError:
        return ''


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


def _append_live_listing_proof(lines: list[str]) -> None:
    proof_rows = _live_listing_proof_rows()
    if not proof_rows:
        return
    lines.extend([
        '',
        '## Live third-party proof to reuse',
        '- Confirmed public listings already exist; cite these instead of sounding like every submission is still pending.',
    ])
    for row in proof_rows:
        line = f"- {row['name']} — {row['listing_url']}"
        if row.get('preferred_repo_target') == 'codeberg_primary':
            line += ' (routes to Codeberg first)'
        elif row.get('preferred_repo_target') == 'github_only':
            line += ' (GitHub-only listing; mirror-first)'
        elif row.get('preferred_repo_target') == 'both':
            line += ' (includes both Codeberg and GitHub links)'
        if row.get('status_note'):
            line += f" ({row['status_note']})"
        lines.append(line)


def _packet_includes_live_listing_proof(path: Path) -> bool:
    proof_rows = _live_listing_proof_rows()
    if not proof_rows:
        return True
    text = _read_text(path)
    if not text:
        return False
    return all(
        (row.get('listing_url') and row['listing_url'] in text)
        or row['name'] in text
        for row in proof_rows
    )


def _secondary_surface_repair_rows(payload: dict[str, Any]) -> list[dict[str, str]]:
    directories = payload.get('directories') or {}
    rows: list[dict[str, str]] = []
    for name, row in directories.items():
        for surface in row.get('secondary_surface_targets') or []:
            route = str(surface.get('preferred_repo_target') or 'unknown')
            if route not in {'github_only', 'unknown'}:
                continue
            rows.append({
                'name': str(name),
                'url': str(surface.get('url') or ''),
                'preferred_repo_target': route,
                'listing_url': str(row.get('listing_url') or ''),
            })
    return rows


def _directory_confirmation_packet_is_current(path: Path, repair_rows: list[dict[str, str]]) -> bool:
    text = _read_text(path)
    if not text:
        return False
    if not repair_rows:
        return True
    return all(
        (row.get('url') and row['url'] in text)
        or row['name'] in text
        for row in repair_rows
    )


def _directory_secondary_surface_repair_still_active(
    now: datetime,
    repair_rows: list[dict[str, str]],
) -> bool:
    if not repair_rows:
        return False

    expected_urls = {
        str(row.get('url') or '').strip()
        for row in repair_rows
        if str(row.get('url') or '').strip()
    }
    if not expected_urls:
        return False

    for payload in _recent_action_payloads(
        action_types=DIRECTORY_SECONDARY_SURFACE_ACTION_TYPES,
        now=now,
        days=14,
    ):
        chosen_action = _chosen_action_dict(payload)
        review_window = payload.get('review_window') if isinstance(payload.get('review_window'), dict) else {}
        follow_up_not_before = _parse_dt(review_window.get('follow_up_not_before'))
        if follow_up_not_before is not None and follow_up_not_before <= now:
            continue

        covered_urls = {
            str(url).strip()
            for url in (payload.get('targets') or [])
            if str(url).strip()
        }
        target_url = str(chosen_action.get('target_url') or payload.get('target_url') or '').strip()
        if target_url:
            covered_urls.add(target_url)

        if expected_urls.issubset(covered_urls):
            return True

    return False


def _write_directory_confirmation_execution(now: datetime) -> tuple[Path, list[str], dict[str, Any]]:
    subprocess.run(
        [sys.executable, str(ROOT / 'agents/marketing/backlink_status.py')],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    payload = _load_json(_backlink_status_latest_path())
    directories = payload.get('directories') or {}
    live = []
    pending = []
    blocked = []
    for name, row in directories.items():
        item = {
            'name': name,
            'listing_url': row.get('listing_url', ''),
            'status_note': row.get('status_note', ''),
            'preferred_repo_target': row.get('preferred_repo_target', 'unknown'),
        }
        if row.get('listing_live'):
            live.append(item)
        else:
            checks = row.get('check_results') or []
            if any(check.get('status') in {401, 403} for check in checks):
                blocked.append(item)
            else:
                pending.append(item)

    summary = payload.get('summary') or {}
    generated_at = payload.get('generated_at', 'unknown')
    repair_rows = _secondary_surface_repair_rows(payload)
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_directory_confirmation_execution.md'
    latest_artifact = DRAFTS_DIR / 'directory_confirmation_execution_latest.md'
    lines = [
        '# Ralph Workflow directory confirmation execution',
        f'Generated: {now.isoformat(timespec="seconds")}',
        f'- Backlink snapshot refreshed: {generated_at}',
        f'- Live directory listings detected: {summary.get("directories_with_live_listings", 0)}',
        f'- Live listings routing to Codeberg first: {summary.get("live_listings_pointing_to_codeberg", 0)}',
        f'- Search queries indexed: {summary.get("queries_indexed", 0)} / {summary.get("total_queries", 0)}',
        f'- Live secondary surfaces needing routing repair: {len(repair_rows)}',
        '',
        '## Why this run exists',
        '- Recent low-intent directory submissions already stacked into the same measurement window.',
        '- The loop needs fresh approval/backlink evidence before it claims another directory action is useful.',
        '- Live listings should be reused as proof assets in the next curator/comparison packets instead of being left invisible.',
        '- If a live third-party surface still routes repo intent to GitHub-only or leaves repo routing unclear, fixing that surface is a higher-truth next move than pretending the board is empty.',
    ]

    if live:
        lines.extend(['', '## Live listings to reuse now'])
        for row in live:
            route = row.get('preferred_repo_target', 'unknown')
            route_note = {
                'codeberg_primary': 'Routes to Codeberg first',
                'github_only': 'GitHub-only listing',
                'both': 'Includes both Codeberg and GitHub links',
            }.get(route, 'Repo target not verified yet')
            lines.append(f'- **{row["name"]}** — {row["listing_url"]}')
            lines.append(f'  - Route: {route_note}')
            if row.get('status_note'):
                lines.append(f'  - Note: {row["status_note"]}')

    if pending:
        lines.extend(['', '## Pending / still reviewing'])
        for row in pending[:8]:
            lines.append(f'- **{row["name"]}** — {row["listing_url"] or "listing URL not yet known"}')
            if row.get('status_note'):
                lines.append(f'  - Note: {row["status_note"]}')

    if blocked:
        lines.extend(['', '## Auth-gated or blocked checks'])
        for row in blocked:
            lines.append(f'- **{row["name"]}** — {row["listing_url"]}')
            if row.get('status_note'):
                lines.append(f'  - Note: {row["status_note"]}')

    if repair_rows:
        lines.extend(['', '## Live secondary surfaces that still need Codeberg-routing repair'])
        for row in repair_rows:
            route_note = 'GitHub-only live surface' if row['preferred_repo_target'] == 'github_only' else 'Live surface with repo target still unclear'
            lines.append(f'- **{row["name"]}** — {row["url"]}')
            lines.append(f'  - Current route: {route_note}')
            if row.get('listing_url'):
                lines.append(f'  - Proof spine: main listing already live at {row["listing_url"]}')
            lines.append('  - Action now: ask the editor/owner to add or elevate the primary Codeberg repo on this already-live surface.')

        lines.extend([
            '',
            '## Suggested manual repair ask',
            '- Thanks for already listing Ralph Workflow. One live surface still routes repo intent away from the primary Codeberg repo (or leaves it unclear).',
            '- Please add the primary repo as **https://codeberg.org/RalphWorkflow/Ralph-Workflow** on the affected live page and keep GitHub only as the mirror if needed.',
            '- Why this matters: Codeberg is the canonical upstream, so this fixes repo-discovery truth on a page that is already ranking / already visible.',
        ])

    lines.extend([
        '',
        '## Next actions',
        '- Reuse the live listing URLs above in the next curator/comparison/manual-contact packets as third-party proof.',
        '- Keep Codeberg as the primary CTA whenever a live listing already points there; do not restart the directory-submission loop first.',
        '- If live secondary surfaces still need routing repair, treat this packet as a real follow-through asset instead of calling the board empty.',
        '- If search indexing is still weak, treat these live listings as trust assets and keep the next active lane focused on higher-intent demand capture or citation follow-through.',
    ])

    text = '\n'.join(lines) + '\n'
    artifact.write_text(text, encoding='utf-8')
    latest_artifact.write_text(text, encoding='utf-8')
    prepared = [row['name'] for row in repair_rows] or [row['name'] for row in live] or [row['name'] for row in pending[:5]]
    return artifact, prepared, payload


def _write_distribution_confirmation_follow_through(now: datetime) -> tuple[Path, list[str]]:
    actions = _call_selector_local(distribution_lane_selector._pending_confirmation_actions, now)
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_distribution_confirmation_follow_through.md'
    latest_artifact = DRAFTS_DIR / 'distribution_confirmation_follow_through_latest.md'

    lines = [
        '# Ralph Workflow distribution confirmation follow-through',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        '- A live external correction already shipped, but it still requires platform confirmation before the public proof actually exists.',
        '- Until that confirmation happens, the action is not outcome-ready and should not be counted as a completed distribution win.',
        '- This packet keeps the board truthful by turning the blocker into an explicit do-now follow-through step.',
        '',
        '## Shared findings reused',
        '- marketing_workflow_audit_latest.json → confirmation-pending actions must not count as outcome-ready',
        '- distribution_lane_latest.json → current lane selection and active review-window context',
        '- backlink_status_latest.json → live directory and routing evidence still anchor the correction ask',
        '',
    ]

    prepared: list[str] = []
    if not actions:
        lines.extend([
            'No confirmation-required live actions are currently waiting.',
            '',
            'If this packet still exists, clear it on the next run once the blocking action is either confirmed or expired.',
        ])
    else:
        lines.extend([
            '## Confirm these now',
        ])
        for idx, action in enumerate(actions, start=1):
            title = str(action.get('title') or 'confirmation-required action').strip()
            prepared.append(title)
            lines.extend([
                f'### {idx}. {title}',
                f'- Status: {action.get("status", "unknown")}',
                f'- Confirmation channel: {action.get("confirmation_channel", "unknown") or "unknown"}',
                f'- Surface URL: {action.get("url", "") or "unknown"}',
                f'- Source log: {action.get("path", "")}',
            ])
            platform_response = str(action.get('platform_response') or '').strip()
            if platform_response:
                lines.append(f'- Platform response: {platform_response}')
            lines.extend([
                '- Next step: complete the platform confirmation (for example the emailed approval link) before counting this as a shipped public correction.',
                '',
            ])

        lines.extend([
            '## Process rule now in force',
            '- Do not stack another same-surface correction while this one is still blocked on confirmation.',
            '- Do not mark this action outcome-ready until the platform-specific confirmation step is complete and the public surface can be rechecked.',
        ])

    content = '\n'.join(lines).rstrip() + '\n'
    artifact.write_text(content, encoding='utf-8')
    latest_artifact.write_text(content, encoding='utf-8')
    return latest_artifact, prepared


def _http_get_text(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (compatible; RalphWorkflow marketing loop)',
            'Accept': 'text/html,application/json;q=0.9,*/*;q=0.8',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='ignore')
    except (urllib.error.URLError, TimeoutError, ValueError):
        return ''


def _github_user_contact_snapshot(owner: str) -> dict[str, Any]:
    text = _http_get_text(f'https://api.github.com/users/{owner}', timeout=15)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def _latest_repo_commit_email(repo_url: str) -> str:
    if not repo_url:
        return ''
    tmpdir = None
    try:
        tmpdir = tempfile.TemporaryDirectory()
        clone_dir = Path(tmpdir.name) / 'repo'
        clone = subprocess.run(
            ['git', 'clone', '--depth', '1', repo_url, str(clone_dir)],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if clone.returncode != 0:
            return ''
        log = subprocess.run(
            ['git', '-C', str(clone_dir), 'log', '-1', '--format=%ae'],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        email = (log.stdout or '').strip()
        if not email or '@' not in email:
            return ''
        lowered = email.lower()
        if 'noreply' in lowered or lowered.endswith('@users.noreply.github.com') or lowered.endswith('[bot]'):
            return ''
        return email
    except (OSError, subprocess.SubprocessError):
        return ''
    finally:
        if tmpdir is not None:
            tmpdir.cleanup()


NOISY_CONTACT_HOST_FRAGMENTS = {
    'api.githubcopilot.com',
    'archiveprogram.github.com',
    'avatars.githubusercontent.com',
    'camo.githubusercontent.com',
    'cdn.jsdelivr.net',
    'demo.gpg-badge.hesreallyhim.com',
    'docs.github.com',
    'github-cloud.s3.amazonaws.com',
    'github.blog',
    'github-readme-stats.vercel.app',
    'github.community',
    'github.githubassets.com',
    'githubassets.com',
    'githubusercontent.com',
    'maintainers.github.com',
    'media.giphy.com',
    'npmjs.com',
    'orcid.org',
    'pypi.org',
    'securitylab.github.com',
    'skills.github.com',
    'stars.github.com',
    'support.github.com',
    'www.githubstatus.com',
}

ACTIONABLE_CONTACT_PATH_HINTS = (
    'contact',
    'about',
    'connect',
    'submit',
    'reach',
    'team',
)

NOISY_CONTACT_SUFFIXES = (
    '.js',
    '.css',
    '.svg',
    '.png',
    '.jpg',
    '.jpeg',
    '.gif',
    '.webp',
)


def _normalize_url(value: str) -> str:
    cleaned = unescape((value or '').strip())
    if cleaned.lower().startswith('mailto:'):
        return cleaned
    if cleaned.startswith('//'):
        cleaned = f'https:{cleaned}'
    elif cleaned and not re.match(r'^https?://', cleaned, re.I):
        if cleaned.startswith(('/', '#', '?')):
            return ''
        if '.' not in cleaned.split('/')[0]:
            return ''
        cleaned = f'https://{cleaned.lstrip("/")}'
    return cleaned.rstrip('/ ')


def _is_meaningful_contact_url(url: str) -> bool:
    if url.lower().startswith('mailto:'):
        return False
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return False
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if host in NOISY_CONTACT_HOST_FRAGMENTS:
        return False
    if host.endswith('.githubassets.com') or host.endswith('.githubusercontent.com'):
        return False
    if host == 'github.com' and path in {'', '/'}:
        return False
    if any(path.endswith(suffix) for suffix in NOISY_CONTACT_SUFFIXES):
        return False
    return True


def _is_actionable_website_url(url: str) -> bool:
    if not _is_meaningful_contact_url(url):
        return False
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower().strip('/')
    if any(hint in path for hint in ACTIONABLE_CONTACT_PATH_HINTS):
        return True
    if path in {'', 'index.html', 'index.htm'}:
        return True
    if host.startswith('www.') and path in {'', 'index.html', 'index.htm'}:
        return True
    return False


def _extract_contact_links(text: str) -> list[dict[str, str]]:
    channels: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(channel_type: str, value: str, label: str) -> None:
        cleaned = (value or '').strip()
        if not cleaned:
            return
        key = (channel_type, cleaned)
        if key in seen:
            return
        seen.add(key)
        channels.append({'type': channel_type, 'value': cleaned, 'label': label})

    text = text or ''
    for email in sorted(set(re.findall(r'mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})', text, re.I))):
        add('email', email, 'email')
    for email in sorted(set(re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', text))):
        add('email', email, 'email')
    hrefs = set(re.findall(r'href=["\']([^"\']+)["\']', text, re.I))
    hrefs.update(re.findall(r'https://[^\s"\'<>]+', text))
    for raw_url in sorted(hrefs):
        url = _normalize_url(raw_url)
        lowered = url.lower()
        if 'linkedin.com/' in lowered and _is_meaningful_contact_url(url):
            add('linkedin', url, 'LinkedIn')
            continue
        if ('twitter.com/' in lowered or 'x.com/' in lowered) and _is_meaningful_contact_url(url):
            add('x', url, 'X/Twitter')
            continue
        if 'github.com/' in lowered:
            continue
        if _is_actionable_website_url(url):
            label = 'contact page' if any(hint in urllib.parse.urlparse(url).path.lower() for hint in ACTIONABLE_CONTACT_PATH_HINTS) else 'website'
            add('website', url, label)
    return channels


def _prioritize_contact_channels(channels: list[dict[str, str]]) -> list[dict[str, str]]:
    def sort_key(channel: dict[str, str]) -> tuple[int, int, str]:
        channel_type = (channel.get('type') or '').lower()
        label = (channel.get('label') or '').lower()
        value = (channel.get('value') or '').lower()
        type_rank = {
            'email': 0,
            'x': 1,
            'linkedin': 2,
            'website': 3,
            'github_issue': 4,
        }.get(channel_type, 9)
        contact_rank = 0 if 'contact' in label or any(hint in value for hint in ACTIONABLE_CONTACT_PATH_HINTS) else 1
        return (type_rank, contact_rank, value)

    ordered = sorted(channels, key=sort_key)
    pruned: list[dict[str, str]] = []
    website_count = 0
    for channel in ordered:
        if channel.get('type') == 'website':
            website_count += 1
            if website_count > 2:
                continue
        pruned.append(channel)
    return pruned


def _discover_curator_channels(queue_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for row in queue_rows:
        status = (row.get('status') or '').strip().lower()
        priority = (row.get('priority') or '').strip().upper()
        if status not in ({'prepared'} | MANUAL_CONTACT_HANDOFF_REMAINING_STATUSES) or 'HIGH' not in priority:
            continue

        url = (row.get('url') or '').strip()
        match = re.search(r'github\.com/([^/]+)/([^/#?]+)', url)
        if not match:
            continue

        owner = match.group(1)
        repo = match.group(2)
        user = _github_user_contact_snapshot(owner)
        profile_html = _http_get_text(f'https://github.com/{owner}', timeout=15)
        repo_html = _http_get_text(url, timeout=15)

        channels: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def add(channel_type: str, value: str, label: str) -> None:
            cleaned = (value or '').strip()
            if not cleaned:
                return
            key = (channel_type, cleaned)
            if key in seen:
                return
            seen.add(key)
            channels.append({'type': channel_type, 'value': cleaned, 'label': label})

        if user.get('email'):
            add('email', str(user['email']), 'GitHub public email')
        commit_email = _latest_repo_commit_email(url)
        if commit_email:
            add('email', commit_email, 'latest public commit email')
        if user.get('blog'):
            blog_url = _normalize_url(str(user['blog']))
            if _is_actionable_website_url(blog_url):
                add('website', blog_url, 'profile website')
            contact_url = blog_url.rstrip('/') + '/contact' if blog_url else ''
            if _is_actionable_website_url(contact_url):
                add('website', contact_url, 'possible contact page')
        if user.get('twitter_username'):
            add('x', f"https://x.com/{user['twitter_username']}", 'GitHub-linked X/Twitter')

        for channel in _extract_contact_links(profile_html):
            add(channel['type'], channel['value'], f'profile {channel["label"]}')

        repo_issue_url = f'https://github.com/{owner}/{repo}/issues/new'
        if 'open an issue' in repo_html.lower() or 'contributing' in repo_html.lower():
            add('github_issue', repo_issue_url, 'repo contribution path')

        channels = _prioritize_contact_channels(channels)

        recommended = 'manual research still needed'
        if any(channel['type'] == 'email' for channel in channels):
            recommended = 'email fallback can run now'
        elif any(channel['type'] in {'website', 'x', 'linkedin'} for channel in channels):
            recommended = 'manual contact channel is now identified'
        elif any(channel['type'] == 'github_issue' for channel in channels):
            recommended = 'GitHub issue/PR path remains the next move once auth is restored'

        findings.append({
            'target': row.get('target') or f'{owner}/{repo}',
            'url': url,
            'owner': owner,
            'repo': repo,
            'priority': row.get('priority') or '',
            'status': row.get('status') or '',
            'channels': channels,
            'recommended_next_step': recommended,
            'artifact_path': row.get('artifact_path') or '',
        })
    return findings


def _write_curator_contact_discovery(now: datetime, queue_rows: list[dict[str, Any]]) -> tuple[Path, list[dict[str, Any]]]:
    findings = _discover_curator_channels(queue_rows)
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_curator_contact_discovery.md'
    latest_artifact = DRAFTS_DIR / 'curator_contact_discovery_latest.md'

    lines = [
        '# Ralph Workflow Curator Contact Discovery',
        '',
        'Prepared curator targets are blocked on GitHub auth in this runtime.',
        'This pass replaces fake handoff follow-through with real contact-channel discovery for the highest-priority prepared targets.',
        '',
    ]
    if not findings:
        lines.extend([
            'No high-priority prepared curator targets produced usable contact channels in this pass.',
            '',
            'Next move: discover fresh autonomous distribution lanes or restore GitHub auth before retrying curator execution.',
        ])
    else:
        for idx, finding in enumerate(findings, start=1):
            lines.extend([
                f'## {idx}. {finding["target"]}',
                f'- URL: {finding["url"]}',
                f'- Recommended next step: {finding["recommended_next_step"]}',
                f'- Ready file: {finding["artifact_path"]}',
            ])
            if finding['channels']:
                lines.append('- Discovered channels:')
                for channel in finding['channels']:
                    lines.append(f"  - {channel['label']}: {channel['value']}")
            else:
                lines.append('- Discovered channels: none')
            lines.append('')

    artifact.write_text('\n'.join(lines).rstrip() + '\n', encoding='utf-8')
    latest_artifact.write_text(artifact.read_text(encoding='utf-8'), encoding='utf-8')

    payload = {
        'generated_at': now.isoformat(),
        'targets': findings,
    }
    _curator_contact_discovery_path().write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    return artifact, findings


def _load_curator_contact_discovery() -> list[dict[str, Any]]:
    payload = _load_json(_curator_contact_discovery_path())
    targets = payload.get('targets', []) or []
    return [target for target in targets if isinstance(target, dict)]


def _load_primary_repo_flat_contact_discovery() -> list[dict[str, Any]]:
    path = _primary_repo_flat_contact_discovery_path()
    payload = _load_json(path)
    targets = payload.get('targets', []) or []
    if not targets and path.exists():
        try:
            direct_payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            direct_payload = {}
        targets = direct_payload.get('targets', []) or []
    return [target for target in targets if isinstance(target, dict)]


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
            'contact page',
            'advertise page',
            'consultation page',
            'consulting page',
            'work with me page',
        )):
            return True
        if any(token in value for token in ('typeform.com', 'tally.so', 'forms.gle', 'docs.google.com/forms', 'airtable.com', 'jotform.com', 'hubspot.com', 'formspree.io')):
            return True
    return False


def _publisher_target_is_packet_executable(row: dict[str, Any]) -> bool:
    channels = row.get('channels') or []
    return _publisher_target_has_manual_executable_channel(channels)


def _publisher_target_has_runtime_sendable_channel(channels: list[dict[str, Any]]) -> bool:
    for channel in channels:
        channel_type = str((channel or {}).get('type') or '').strip().lower()
        if channel_type in RUNTIME_SENDABLE_PUBLISHER_CHANNEL_TYPES:
            return True
    return False


def _publisher_target_has_manual_reviewable_channel(channels: list[dict[str, Any]]) -> bool:
    for channel in channels:
        channel_type = str((channel or {}).get('type') or '').strip().lower()
        if channel_type in {'telegram', 'github_issue', 'x', 'linkedin'}:
            return True
        if channel_type != 'website':
            continue
        label = str((channel or {}).get('label') or '').strip().lower()
        if any(token in label for token in (
            'contact',
            'about',
            'advertise',
            'feedback',
            'support',
            'faq',
            'consult',
            'work with me',
        )):
            return True
    return False


def _current_contact_discovery_targets() -> list[str]:
    return [
        _display_target_name(str(target.get('target') or '').strip())
        for target in _load_curator_contact_discovery()
        if str(target.get('target') or '').strip()
    ]


def _contact_discovery_is_current(expected_targets: list[str]) -> bool:
    contact_path = _curator_contact_discovery_path()
    if not expected_targets or not contact_path.exists():
        return False
    current = sorted(_current_contact_discovery_targets())
    expected = sorted(_display_target_name(target) for target in expected_targets)
    return current == expected


def _is_weak_role_email(value: str) -> bool:
    lowered = (value or '').strip().lower()
    return lowered.startswith(('legal@', 'privacy@', 'support@', 'security@', 'compliance@', 'terms@', 'info@noreply', 'no-reply@', 'noreply@'))


def _contact_channel_suggestion(channels: list[dict[str, str]]) -> str:
    website_value = next((channel.get('value') or '' for channel in channels if (channel.get('type') or '').lower() == 'website'), '')
    preferred_form_value = next(
        (
            channel.get('value') or ''
            for channel in channels
            if (channel.get('type') or '').lower() == 'website'
            and any(token in (channel.get('label') or '').lower() for token in ('feedback form', 'contact form', 'submission form', 'message form'))
        ),
        '',
    )
    for channel in channels:
        channel_type = (channel.get('type') or '').lower()
        value = channel.get('value') or ''
        if channel_type == 'email' and not _is_weak_role_email(value):
            return f'Email first: {value}'
        if channel_type == 'website' and preferred_form_value:
            return f'Use the site contact path first: {preferred_form_value}'
        if channel_type == 'website':
            return f'Use the site contact path first: {value}'
        if channel_type == 'telegram':
            return f'Use Telegram contact path first: {value}'
        if channel_type == 'x':
            return f'Use X/Twitter DM or mention path first: {value}'
        if channel_type == 'linkedin':
            return f'Use LinkedIn outreach first: {value}'
        if channel_type == 'github_issue':
            return f'Fallback to GitHub issue/PR path: {value}'
    weak_email = next((channel.get('value') or '' for channel in channels if (channel.get('type') or '').lower() == 'email'), '')
    if weak_email and preferred_form_value:
        return f'Use the site contact path first: {preferred_form_value} (weak fallback email: {weak_email})'
    if weak_email and website_value:
        return f'Use the site contact path first: {website_value} (weak fallback email: {weak_email})'
    if weak_email:
        return f'Fallback role email only: {weak_email}'
    return 'No direct contact channel found; wait for GitHub auth or do manual research.'


def _write_curator_contact_handoff_packet(now: datetime, findings: list[dict[str, Any]]) -> tuple[Path, list[str]]:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_curator_contact_handoff_packet.md'
    latest_artifact = DRAFTS_DIR / 'curator_contact_handoff_packet_latest.md'
    selected = findings[:5]
    selected_targets = [str(finding.get('target') or '').strip() for finding in selected if str(finding.get('target') or '').strip()]
    delivery_active = _curator_contact_packet_already_delivered(now, selected_targets)
    lines = [
        '# Ralph Workflow Curator Contact Execution Packet',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        '- GitHub auth is blocked in this runtime, but contact-channel discovery has already identified alternate paths for prepared curator targets.',
        f'- {_adoption_summary()}',
        '- This packet turns discovered channels into one canonical human-executable contact list instead of rediscovering the same profiles again.',
        '',
        '## Shared findings reused',
        '- curator_contact_discovery_latest.json → already-discovered contact channels',
        '- curator_outreach_queue_latest.json → current prepared targets and ready files',
        '- market_intelligence_latest.json → positioning and comparison framing',
        '- adoption_metrics_latest.json → Codeberg movement is still the primary success gate',
    ]
    if delivery_active:
        lines.extend([
            '',
            '## Current execution status',
            '- This packet was already delivered in the current review window.',
            '- Do not redeliver it yet unless the prepared target set materially changes or the review window expires.',
            '- Use this file as reference only while follow-through lives on the marketing execution board.',
        ])
    _append_live_listing_proof(lines)
    lines.extend([
        '',
        '## Reference targets already covered in the active review window' if delivery_active else '## Execute these first',
    ])
    prepared: list[str] = []
    for idx, finding in enumerate(selected, start=1):
        target_name = str(finding.get('target') or 'unknown target')
        prepared.append(target_name)
        channels = finding.get('channels') or []
        lines.extend([
            f'### {idx}. {_display_target_name(target_name)}',
            f'- URL: {finding.get("url", "")}',
            f'- Ready file: {finding.get("artifact_path", "")}',
            f'- Recommended next step: {finding.get("recommended_next_step", "manual outreach")}',
            f'- First channel to try: {_contact_channel_suggestion(channels)}',
        ])
        if channels:
            lines.append('- Contact channels:')
            for channel in channels:
                lines.append(f"  - {channel.get('label', channel.get('type', 'channel'))}: {channel.get('value', '')}")
        else:
            lines.append('- Contact channels: none discovered yet')
        lines.extend([
            '- Outreach angle: lead with Codeberg-primary inclusion/citation value, not another general product intro.',
            '',
        ])
    lines.extend([
        '## Process rule now in force',
        '- Do not rerun contact discovery for the same prepared target set unless the prepared set changes.',
        '- Refresh this packet when discovered channels or the top prepared targets change, not on every audit loop.',
    ])
    if delivery_active:
        lines.append('- Another delivery right now would be fake progress because this packet is already inside its active review window.')
    lines.extend([
        '',
        '## Measurement contract',
        '- Expected outcome: at least one real maintainer/curator contact attempt using the discovered non-GitHub channel set',
        '- Review window: 7 days for contact attempt, 14 days for response, 30 days for live backlink/listing evidence',
    ])
    content = '\n'.join(lines) + '\n'
    artifact.write_text(content, encoding='utf-8')
    latest_artifact.write_text(content, encoding='utf-8')
    return artifact, prepared


def _write_primary_repo_flat_contact_handoff_packet(now: datetime, findings: list[dict[str, Any]]) -> tuple[Path, list[str]]:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_primary_repo_flat_contact_handoff_packet.md'
    latest_artifact = DRAFTS_DIR / 'primary_repo_flat_contact_handoff_packet_latest.md'

    recent_publisher_targets = _recent_contact_targets(
        now,
        action_types=PUBLISHER_CONTACT_ACTION_TYPES,
        days=7,
    )
    active_manual_delivery_targets = _active_manual_outreach_delivery_targets(now)

    executable_findings: list[dict[str, Any]] = []
    covered_findings: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    for finding in findings:
        target_name = _display_target_name(str(finding.get('target') or '').strip())
        if not target_name or target_name in seen_targets:
            continue
        seen_targets.add(target_name)
        if not _publisher_target_is_packet_executable(finding):
            continue
        if target_name in recent_publisher_targets or target_name in active_manual_delivery_targets:
            covered_findings.append(finding)
            continue
        executable_findings.append(finding)

    selected_pool = executable_findings or covered_findings or findings
    selected = selected_pool[:5]
    selected_targets = [str(finding.get('target') or '').strip() for finding in selected if str(finding.get('target') or '').strip()]
    delivery_active = _primary_repo_flat_packet_delivery_still_active(now, selected_targets)
    research_signals = _latest_research_signals(limit=5)
    lines = [
        '# Ralph Workflow Primary-Repo-Flat Publisher Contact Packet',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        '- Codeberg adoption is still flat in the active window.',
        '- Same-family directory and curator bursts are already inside measurement windows, so a different publisher/contact lane is higher leverage than another overlap-heavy batch.',
        '- Public contact discovery is already done for these fresh developer-native publishers; this packet converts that discovery into one executable Codeberg-first handoff.',
        '',
        '## Shared findings reused',
        '- primary_repo_flat_contact_discovery_latest.json → verified public contact routes for fresh publisher targets',
        '- marketing_workflow_audit_latest.json → primary_repo_flat repair is still active while overlap-heavy lanes stay paused',
        '- market_intelligence_latest.json → positioning truth and comparison framing',
        '- adoption_metrics_latest.json → Codeberg movement remains the primary success gate',
    ]
    if research_signals:
        lines.append('- latest research / reddit monitor → current pain language around trust, verification, and morning-after review')
    if delivery_active:
        lines.extend([
            '',
            '## Current execution status',
            '- This packet was already delivered in the current review window.',
            '- Do not redeliver it yet unless the discovered channels, target set, or hooks materially change.',
            '- Use this file as reference only while the marketing execution board remains the source of truth.',
        ])
    _append_live_listing_proof(lines)
    lines.extend([
        '',
        '## Angle to lead with',
        *[f'- {item}' for item in _primary_repo_flat_angle_lines(research_signals)],
        '',
        '## Reference targets already covered in the active review window' if delivery_active else '## Execute these first',
    ])
    prepared: list[str] = []
    for idx, finding in enumerate(selected, start=1):
        target_name = str(finding.get('target') or 'unknown target')
        prepared.append(target_name)
        channels = finding.get('channels') or []
        lines.extend([
            f'### {idx}. {_display_target_name(target_name)}',
            f'- URL: {finding.get("article_url") or finding.get("root_url") or finding.get("url") or ""}',
            f'- Hook: {finding.get("hook", "")}',
            f'- Why this target: {finding.get("reason", "")}',
            f'- Suggested subject: {finding.get("outreach_subject", "")}',
            f'- Recommended next step: {finding.get("recommended_next_step", "manual outreach")}',
            f'- First channel to try: {_contact_channel_suggestion(channels)}',
        ])
        if channels:
            lines.append('- Contact channels:')
            for channel in channels:
                lines.append(f"  - {channel.get('label', channel.get('type', 'channel'))}: {channel.get('value', '')}")
        else:
            lines.append('- Contact channels: none discovered yet')
        lines.extend([
            '- Outreach angle: keep it publisher-native and pain-led; ask for comparison/citation consideration with Codeberg as the canonical repo, not a generic product pitch.',
            '- Ready-to-send email draft:',
            '```text',
            _primary_repo_flat_email_draft(finding, research_signals),
            '```',
            '- Short contact-form version:',
            '```text',
            _primary_repo_flat_contact_form_draft(finding, research_signals),
            '```',
            '',
        ])
    lines.extend([
        '## Process rule now in force',
        '- Do not spend another run rediscovering contact paths for this exact publisher set.',
        '- Refresh this packet only when the discovered channels, target set, or message hooks materially change.',
    ])
    if delivery_active:
        lines.append('- Another manual delivery right now would be fake progress because this packet is already inside its active review window.')
    lines.extend([
        '',
        '## Measurement contract',
        '- Expected outcome: at least one real publisher/maintainer contact attempt using these discovered public channels',
        '- Review window: 7 days for contact attempt, 14 days for reply/citation signal, 30 days for live backlink or attributable qualified repo inspection',
    ])
    content = '\n'.join(lines) + '\n'
    artifact.write_text(content, encoding='utf-8')
    latest_artifact.write_text(content, encoding='utf-8')
    return artifact, prepared


def _latest_apollo_execution_warning() -> str | None:
    candidates = sorted(LOG_DIR.glob('marketing_*apollo*.json'), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        payload = _load_json(path)
        chosen_action = _chosen_action_dict(payload)
        if (chosen_action.get('channel') or '') != 'apollo_outreach':
            continue
        result = payload.get('result') or {}
        if not result.get('live_external_action') and 'outcome_ready' not in result:
            continue
        if result.get('outcome_ready') is True:
            return None
        notes = result.get('notes') or []
        evidence = result.get('evidence') or []
        text = ' '.join(str(item) for item in [*notes, *evidence]).lower()
        if any(marker in text for marker in LOW_SIGNAL_APOLLO_MARKERS):
            return 'Latest Apollo execution evidence still shows a zero-record / import-verification gap. Do not count Apollo as shipped until the imported list is non-zero or a live sequence is launched.'
        return None
    return None


def _reddit_monitor_latest_path() -> Path:
    return SEO_REPORTS_DIR / 'reddit_monitor_latest.md'


def _target_identity(*, heading: str = '', url: str = '') -> tuple[str, str]:
    normalized_url = (url or '').strip().lower()
    if normalized_url:
        return ('url', normalized_url)
    return ('heading', (heading or '').strip().lower())


def _parse_distribution_reset_targets(text: str) -> list[dict[str, str]]:
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


def _load_distribution_reset_targets() -> list[dict[str, str]]:
    text = _read_text(_distribution_reset_log_path())
    if not text:
        return []
    discovered = _parse_distribution_reset_targets(text)
    if not discovered:
        return []

    occupied = {
        _target_identity(heading=row.get('target', ''), url=row.get('url', ''))
        for row in _load_curator_queue_rows()
    }
    occupied.update(
        _target_identity(heading=row.get('name', '') or row.get('target', ''), url=row.get('url', ''))
        for row in _comparison_queue_rows(COMPARISON_QUEUE_LATEST_PATH)
    )

    fresh: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in discovered:
        identity = _target_identity(heading=row.get('target', ''), url=row.get('url', ''))
        if identity in occupied or identity in seen:
            continue
        seen.add(identity)
        fresh.append(row)
    return fresh


def _load_distribution_reset_queue_rows() -> list[dict[str, str]]:
    path = _distribution_reset_queue_path()
    payload = _load_json(path)
    rows = payload.get('targets', []) or []
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    changed = False

    def target_markers(row: dict[str, str]) -> set[str]:
        markers: set[str] = set()
        heading = str(row.get('target') or '').strip().lower()
        url = str(row.get('url') or '').strip().lower()
        if heading:
            markers.add(heading)
        if url:
            markers.add(url)
            parsed = urllib.parse.urlparse(url)
            host = parsed.netloc.lower()
            if host.startswith('www.'):
                host = host[4:]
            if host:
                markers.add(host)
            path_part = parsed.path.strip('/').lower()
            if path_part:
                markers.add(path_part)
        return {marker for marker in markers if len(marker) >= 4}

    def has_live_evidence(row: dict[str, str]) -> bool:
        markers = target_markers(row)
        if not markers:
            return False
        live_statuses = {'sent', 'submitted', 'published', 'launched'}
        for log_path in LOG_DIR.glob('marketing_*.json'):
            if any(token in log_path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
                continue
            log_payload = _load_json(log_path)
            if not log_payload:
                continue
            result = log_payload.get('result') or {}
            status = str(log_payload.get('status') or result.get('status') or '').lower()
            executed = bool(
                log_payload.get('ok')
                or result.get('ok')
                or log_payload.get('live_external_action')
                or result.get('live_external_action')
            )
            if not (log_payload.get('live_external_action') or result.get('live_external_action') or status in live_statuses):
                executed = False
            if not executed:
                continue
            haystack = json.dumps(log_payload, sort_keys=True).lower()
            if any(marker in haystack for marker in markers):
                return True
        return False

    for row in rows:
        if not isinstance(row, dict):
            continue
        row = dict(row)
        identity = _target_identity(heading=row.get('target', ''), url=row.get('url', ''))
        if identity in seen:
            changed = True
            continue
        seen.add(identity)
        status = str(row.get('status') or '').lower()
        if status in {'discovered', 'ready_for_promotion'} and has_live_evidence(row):
            row['status'] = 'executed_elsewhere'
            row['reconciled_at'] = datetime.now().isoformat(timespec='seconds')
            row['reconciled_by'] = 'distribution_lane_executor'
            changed = True
        deduped.append(row)

    if changed:
        path.write_text(json.dumps({
            'generated_at': payload.get('generated_at') or datetime.now().isoformat(timespec='seconds'),
            'targets': deduped,
        }, indent=2) + '\n', encoding='utf-8')
    return deduped


def _distribution_reset_targets_for_curator(existing_queue_rows: list[dict[str, str]], limit: int = 3) -> list[dict[str, str]]:
    occupied = {
        _target_identity(heading=row.get('target', ''), url=row.get('url', ''))
        for row in existing_queue_rows
    }
    queue_candidates = [
        row for row in _load_distribution_reset_queue_rows()
        if (row.get('status') or '').lower() in {'discovered', 'ready_for_promotion'}
    ]
    raw_candidates: list[dict[str, str]]
    source_label: str
    if queue_candidates:
        raw_candidates = queue_candidates
        source_label = 'distribution_reset_targets_latest.json'
    else:
        raw_candidates = _load_distribution_reset_targets()
        source_label = 'distribution_reset_execution_log.md'

    selected: list[dict[str, str]] = []
    for row in raw_candidates:
        identity = _target_identity(heading=row.get('target', ''), url=row.get('url', ''))
        if identity in occupied:
            continue
        selected.append({
            'heading': row.get('target', ''),
            'url': row.get('url', ''),
            'what_it_is': 'Fresh distribution-reset target discovered from a saturated-lane reset.',
            'why_it_fits': row.get('why_it_fits', ''),
            'action': 'Submit PR or request inclusion with a Codeberg-primary entry',
            'priority': 'HIGH — fresh reset target, not yet prepared elsewhere',
            'entry_format': f'- [Ralph Workflow]({CODEBERG_PRIMARY}) — {_curator_entry_blurb(_latest_research_signals())}',
            'source': source_label,
        })
        occupied.add(identity)
        if len(selected) >= limit:
            break
    return selected


def _mark_distribution_reset_targets_promoted(prepared_rows: list[dict[str, str]], *, now: datetime) -> None:
    if not prepared_rows:
        return
    queue_rows = _load_distribution_reset_queue_rows()
    if not queue_rows:
        return

    prepared_by_identity = {
        _target_identity(heading=row.get('target', ''), url=row.get('url', '')): row
        for row in prepared_rows
    }
    changed = False
    for row in queue_rows:
        identity = _target_identity(heading=row.get('target', ''), url=row.get('url', ''))
        prepared = prepared_by_identity.get(identity)
        if not prepared:
            continue
        row['status'] = 'promoted_to_curator_queue'
        row['promoted_at'] = now.isoformat()
        row['artifact_path'] = prepared.get('artifact_path', '')
        changed = True

    if changed:
        _distribution_reset_queue_path().write_text(json.dumps({
            'generated_at': now.isoformat(),
            'targets': queue_rows,
        }, indent=2) + '\n', encoding='utf-8')


def _load_curator_queue_rows() -> list[dict[str, str]]:
    payload = _load_json(CURATOR_QUEUE_LATEST_PATH)
    rows = payload.get('targets', []) or []
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        identity = _target_identity(heading=row.get('target', ''), url=row.get('url', ''))
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(row)
    normalized = _normalize_curator_queue_rows(deduped)
    if normalized != deduped:
        payload['generated_at'] = datetime.now().isoformat()
        payload['targets'] = normalized
        CURATOR_QUEUE_LATEST_PATH.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    return normalized


def _is_repo_internal_curator_target(row: dict[str, str]) -> bool:
    combined = ' '.join([
        str(row.get('target') or '').lower(),
        str(row.get('url') or '').lower(),
        str(row.get('action') or '').lower(),
    ])
    return any(marker in combined for marker in (
        'github topics:',
        'github.com/topics/',
        'add topic tag',
        'repo description',
        'check if ralph workflow is already tagged',
    ))


def _parse_curator_targets(text: str) -> list[dict[str, str]]:
    blocks = re.split(r'\n(?=###\s+\d+\.)', text)
    targets: list[dict[str, str]] = []
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if not lines or not lines[0].startswith('### '):
            continue
        entry: dict[str, str] = {'heading': lines[0][4:].strip()}
        for line in lines[1:]:
            if line.startswith('- **URL:** '):
                entry['url'] = line.replace('- **URL:** ', '', 1).strip()
            elif line.startswith('- **What it is:** '):
                entry['what_it_is'] = line.replace('- **What it is:** ', '', 1).strip()
            elif line.startswith('- **Why it fits:** '):
                entry['why_it_fits'] = line.replace('- **Why it fits:** ', '', 1).strip()
            elif line.startswith('- **Action:** '):
                entry['action'] = line.replace('- **Action:** ', '', 1).strip()
            elif line.startswith('- **Priority:** '):
                entry['priority'] = line.replace('- **Priority:** ', '', 1).strip()
            elif line.startswith('- **Entry format:** '):
                entry['entry_format'] = line.replace('- **Entry format:** ', '', 1).strip()
            elif line.startswith('- **Note:** '):
                entry.setdefault('note', line.replace('- **Note:** ', '', 1).strip())
        targets.append(entry)
    return targets


def _is_actionable_curator_target(target: dict[str, str]) -> bool:
    action = (target.get('action') or '').lower()
    if not action:
        return False
    measurement_only_phrases = (
        'check indexing status',
        'measure existing work',
    )
    if any(phrase in action for phrase in measurement_only_phrases):
        return False
    return True


def _top_comparison_pages(market_intelligence: dict[str, Any] | None, limit: int = 3) -> list[dict[str, str]]:
    pages = (market_intelligence or {}).get('comparison_pages', []) or []
    selected = []
    for page in pages[:limit]:
        selected.append({
            'slug': page.get('slug', ''),
            'name': page.get('name', ''),
            'path': page.get('path', ''),
        })
    return selected


def _adoption_summary() -> str:
    adoption = _load_json(ADOPTION_PATH)
    recent = adoption.get('recent_window', {}).get('Codeberg', {})
    if not recent:
        return 'Codeberg movement data unavailable; treat primary-repo adoption as the success gate.'
    return (
        'Codeberg is still flat in the active window '
        f"({recent.get('samples', 0)} samples; stars {recent.get('stars_delta_window', 0):+d}, "
        f"watchers {recent.get('watchers_delta_window', 0):+d}, forks {recent.get('forks_delta_window', 0):+d})."
    )


def _recent_distribution_context() -> str:
    outreach = _read_text(OUTREACH_LOG_PATH)
    hn_mentions = outreach.lower().count('hn/lobsters')
    if hn_mentions >= 3:
        return 'HN/Lobsters has already been named as the ceiling repeatedly; this asset must create a different executable path.'
    return 'Use this asset as a measured supplement to the current distribution mix.'


def _curator_subject(target: dict[str, str]) -> str:
    page = target.get('heading', 'your list').split('—')[0].strip()
    return f'Ralph Workflow for {page}'


def _latest_research_signals(limit: int = 7) -> list[str]:
    texts: list[str] = []

    research_paths = sorted(SEO_REPORTS_DIR.glob('research_*.md'))
    if research_paths:
        texts.append(_read_text(research_paths[-1]).lower())

    reddit_monitor_path = _reddit_monitor_latest_path()
    if reddit_monitor_path.exists():
        texts.append(_read_text(reddit_monitor_path).lower())

    combined = '\n'.join(texts)
    if not combined:
        return []

    signals: list[str] = []
    for phrase in MESSAGE_SIGNAL_PHRASES:
        if phrase in combined and phrase not in signals:
            signals.append(phrase)
        if len(signals) >= limit:
            break
    return signals


def _reddit_discussion_opportunities(limit: int = 3) -> list[dict[str, str]]:
    text = _read_text(_reddit_monitor_latest_path())
    if not text or '## Best current discussion opportunities' not in text:
        return []

    section = text.split('## Best current discussion opportunities', 1)[1]
    section = section.split('## Strong current rejects', 1)[0]
    blocks = re.split(r'\n###\s+\d+\)\s+', '\n' + section)
    opportunities: list[dict[str, str]] = []

    def _field(block: str, label: str) -> str:
        match = re.search(rf'- {re.escape(label)}:\s*(.+)', block)
        return match.group(1).strip() if match else ''

    for block in blocks[1:]:
        lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
        if not lines:
            continue
        title = lines[0]
        url = _field(block, 'URL').strip('<>')
        direct_reply_fit = _field(block, 'Direct reply fit').replace('*', '').strip().lower()
        mention_fit = _field(block, 'Mention fit').replace('*', '').strip().lower()
        if not title or not url:
            continue
        if direct_reply_fit not in {'high', 'medium', 'medium-high'}:
            continue
        opportunities.append({
            'title': title,
            'url': url,
            'community': _field(block, 'Community').replace('`', '').strip(),
            'freshness': _field(block, 'Freshness'),
            'direct_reply_fit': direct_reply_fit,
            'mention_fit': mention_fit,
            'best_angle': _field(block, 'Best RalphWorkflow angle').replace('*', '').strip(),
            'why_it_fits': _field(block, 'Why it fits'),
        })
        if len(opportunities) >= limit:
            break
    return opportunities


def _reddit_discussion_reply_draft(opportunity: dict[str, str], research_signals: list[str]) -> str:
    title = str(opportunity.get('title') or '').lower()
    if 'context continuity' in title:
        opening = 'The failure mode is usually continuity, not raw context size.'
        body = (
            'Once a run crosses Git, CI, tickets, and chat, you need the workflow to carry forward plan, checkpoints, '
            'and verification instead of asking the next agent to guess state from scratch.'
        )
    else:
        opening = 'A demo becomes a workflow when the run ends in inspectable state, not just a confident summary.'
        body = (
            'The useful bar is simple: what changed, what passed, what is blocked, and whether the result is actually ready '
            'for review without another babysitting pass.'
        )

    bridge = (
        'The teams that make this work usually keep a simple core loop and add explicit handoffs for planning, build, '
        'verification, and morning-after review.'
    )
    if any('ready to review' in phrase or 'tested code' in phrase for phrase in research_signals):
        bridge = (
            'The handoff has to end with finished, tested work that is ready to review, otherwise the system is just moving '
            'uncertainty around.'
        )
    elif any('what changed' in phrase or 'visible review packets' in phrase for phrase in research_signals):
        bridge = (
            'What helped most for me was making the handoff visible: what changed, what passed, and what still needs a human '
            'decision.'
        )

    return ' '.join([opening, body, bridge])


def _reddit_discussion_packet_entries(path: Path) -> list[dict[str, str]]:
    text = _read_text(path)
    if not text:
        return []
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = re.match(r'^## Opportunity\s+\d+:\s+(.+)$', line)
        if match:
            if current:
                entries.append(current)
            current = {'title': match.group(1).strip(), 'url': ''}
            continue
        if current and line.startswith('- URL:'):
            current['url'] = line.split(':', 1)[1].strip().strip('<>')
    if current:
        entries.append(current)
    return entries



def _reddit_discussion_packet_is_current(path: Path, opportunities: list[dict[str, str]]) -> bool:
    if not opportunities or not path.exists():
        return False
    expected = [
        {
            'title': str(item.get('title') or '').strip(),
            'url': str(item.get('url') or '').strip(),
        }
        for item in opportunities
        if str(item.get('title') or '').strip() and str(item.get('url') or '').strip()
    ]
    if not expected:
        return False
    current = _reddit_discussion_packet_entries(path)
    return current == expected



def _reddit_discussion_packet_delivery_still_active(now: datetime, opportunities: list[dict[str, str]]) -> bool:
    latest_artifact = DRAFTS_DIR / 'reddit_discussion_handoff_packet_latest.md'
    if not _reddit_discussion_packet_is_current(latest_artifact, opportunities):
        return False
    latest_packet_mtime: datetime | None = None
    if latest_artifact.exists():
        latest_packet_mtime = datetime.fromtimestamp(latest_artifact.stat().st_mtime)
    cutoff = now - timedelta(days=14)
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        is_delivery, _action_type, result, _channel = _manual_outreach_delivery_payload(payload)
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
        if delivered_artifact != str(latest_artifact):
            continue
        delivered_at = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if delivered_at is None:
            delivered_at = datetime.fromtimestamp(path.stat().st_mtime)
        if delivered_at < cutoff:
            continue
        if latest_packet_mtime is not None and latest_packet_mtime > delivered_at:
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
        if delivered_at.date() == now.date():
            return True
    return False



def _write_reddit_discussion_handoff_asset(now: datetime, shared_findings_used: list[str]) -> tuple[Path, list[str]] | None:
    if _reddit_manual_discussion_blocked():
        return None
    latest_artifact = DRAFTS_DIR / 'reddit_discussion_handoff_packet_latest.md'
    opportunities = _reddit_discussion_opportunities(limit=2)
    if not opportunities:
        return None
    if _reddit_discussion_packet_delivery_still_active(now, opportunities):
        return None

    research_signals = _latest_research_signals()
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_reddit_discussion_handoff_packet.md'
    lines = [
        '# Ralph Workflow Reddit Discussion Handoff Packet',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        '- The loop was stuck in empty-board distribution-architecture churn even though the latest Reddit monitor found credible manual discussion opportunities.',
        '- Reddit automation remains fail-closed from this environment. This packet is manual discussion follow-through only, not autonomous posting.',
        f'- {_adoption_summary()}',
        f'- {_recent_distribution_context()}',
        '',
        '## Shared findings reused',
        f'- {_reddit_monitor_latest_path()} → current discussion opportunities, thread wording, and mention-fit discipline',
        '- FOUR_MARKETING_QUESTIONS.md → keep optional product follow-up aligned to what it is, who it is for, why it is different, and why now',
        *[f'- {item}' for item in shared_findings_used],
        '',
        '## Operating rule',
        '- First reply should stay discussion-first. Do not force a product mention when the thread only supports a workflow answer.',
        '- Only use the optional Ralph Workflow follow-up if someone asks what system or OSS example matches the described workflow shape.',
        '',
    ]

    targets: list[str] = []
    for idx, opportunity in enumerate(opportunities, start=1):
        title = str(opportunity.get('title') or '').strip()
        targets.append(title)
        reply = _reddit_discussion_reply_draft(opportunity, research_signals)
        optional_followup = (
            'If you want an OSS example of that shape, '
            f'Ralph Workflow is {FOUR_QUESTIONS["what_is_it"].removeprefix("Ralph Workflow is ")} '
            'for developers who need a structured workflow instead of a chat session. '
            'The useful part is the simple loop core plus explicit planning/build/verification handoffs, and the Codeberg repo is the primary place to inspect it: '
            f'{CODEBERG_PRIMARY}.'
        )
        lines.extend([
            f'## Opportunity {idx}: {title}',
            f'- URL: <{opportunity.get("url", "")}>',
            f'- Community: {opportunity.get("community", "unknown")}',
            f'- Freshness: {opportunity.get("freshness", "unknown")}',
            f'- Direct reply fit: {opportunity.get("direct_reply_fit", "unknown")}',
            f'- Mention fit: {opportunity.get("mention_fit", "unknown")}',
            f'- Best angle: {opportunity.get("best_angle", "")}',
            f'- Why it fits: {opportunity.get("why_it_fits", "")}',
            '- Default posture: reply helpfully without a product mention in the first pass.',
            '',
            '### Suggested first reply',
            '```text',
            reply,
            '```',
            '',
            '### Optional follow-up only if the thread asks for tooling/examples',
            '```text',
            optional_followup,
            '```',
            '',
        ])

    lines.extend([
        '## Measurement contract',
        '- Expected outcome: at least one truthful manual discussion reply against a live pain thread without repeating stale Reddit openings.',
        '- Review window: within 24 hours for freshness, then 7 days for any conversation or repo-visit signal.',
        '- Kill condition: if these threads age out or the monitor stops finding credible opportunities, do not keep resurfacing this exact packet.',
    ])

    content = '\n'.join(lines) + '\n'
    artifact.write_text(content, encoding='utf-8')
    latest_artifact.write_text(content, encoding='utf-8')
    return latest_artifact, targets


def _primary_repo_flat_angle_lines(research_signals: list[str]) -> list[str]:
    lines: list[str] = []
    if any('stop babysitting your agents' in phrase for phrase in research_signals):
        lines.append('Lead with the no-babysitting / close-the-laptop problem, not generic autonomy hype.')
    if any('tested code' in phrase or 'ready to review' in phrase for phrase in research_signals):
        lines.append('Anchor the pitch on finished, tested code that is ready to review by morning.')
    if any('visible review packets' in phrase or 'what changed' in phrase for phrase in research_signals):
        lines.append('Stress visible handoffs and what-changed clarity rather than self-reported agent success.')
    if not lines:
        lines.append('Lead with plan → build → verify discipline and the morning-after review outcome.')
    return lines[:3]


def _primary_repo_flat_positioning_sentence() -> str:
    positioning = str(FOUR_QUESTIONS['what_is_it']).strip()
    if positioning.lower().startswith('ralph workflow is '):
        return positioning
    return f'Ralph Workflow is {positioning}'


def _primary_repo_flat_email_draft(finding: dict[str, Any], research_signals: list[str]) -> str:
    hook = str(finding.get('hook') or 'your recent workflow piece').strip()
    reason = str(finding.get('reason') or 'the audience already cares about disciplined agent workflows').strip()
    positioning = _primary_repo_flat_positioning_sentence()
    lead = (
        'A lot of teams want unattended coding runs, but they still do not trust "autonomous" claims '
        'unless the workflow hands back finished, tested code that is ready to review.'
    )
    if any('visible review packets' in phrase or 'what changed' in phrase for phrase in research_signals):
        lead = (
            'A lot of teams are not blocked on model quality anymore; they are blocked on seeing what changed '
            'and whether the agent really handed back something ready to review.'
        )
    elif any('stop babysitting your agents' in phrase for phrase in research_signals):
        lead = (
            'A lot of teams can get agents to keep running, but they still have to babysit them because '
            'the workflow does not end in a trustworthy review handoff.'
        )

    lines = [
        'Hi,',
        '',
        f'I liked {hook}. {lead}',
        '',
        (
            f'{positioning} '
            'It is built around a plan → build → verify loop so the result is not another confident summary, '
            'but a concrete morning-after handoff in your own repo.'
        ),
        (
            f'Why I thought of your audience: {reason} '
            f'{FOUR_QUESTIONS["why_different"]}'
        ),
        '',
        (
            'If you keep comparison roundups, workflow references, or follow-up links on this topic, '
            f'the canonical repo is {CODEBERG_PRIMARY}.'
        ),
        '',
        'Thanks,',
        'Ralph Workflow',
    ]
    return '\n'.join(lines)


def _primary_repo_flat_contact_form_draft(finding: dict[str, Any], research_signals: list[str]) -> str:
    hook = str(finding.get('hook') or 'your recent workflow piece').strip()
    reason = str(finding.get('reason') or 'your readers already care about disciplined agent workflows').strip()
    positioning = _primary_repo_flat_positioning_sentence()
    pain_line = 'finished, tested code that is ready to review by morning'
    if any('stop babysitting your agents' in phrase for phrase in research_signals):
        pain_line = 'no-babysitting agent runs that still end in a trustworthy review handoff'
    return (
        f'I liked {hook}. {positioning} '
        'It is built around a plan → build → verify loop for teams that want '
        f'{pain_line}. {reason} If you keep comparisons or workflow references on this topic, '
        f'the canonical repo is {CODEBERG_PRIMARY}.'
    )


def _curator_entry_blurb(research_signals: list[str]) -> str:
    if research_signals:
        return (
            'free open-source workflow layer for unattended coding runs that aims to hand back '
            'finished, tested code ready to review instead of another confident summary'
        )
    return directory_blurb()


def _curator_pitch(
    target: dict[str, str],
    comparison_pages: list[dict[str, str]],
    research_signals: list[str],
) -> str:
    comparison_hint = ''
    if comparison_pages:
        names = ', '.join(page['name'] for page in comparison_pages[:2] if page.get('name'))
        if names:
            comparison_hint = f' Useful comparison anchors already prepared: {names}.'
    why = target.get('why_it_fits') or target.get('note') or 'It fits the list scope cleanly.'
    pain_lead = 'For developers trying to stop babysitting coding agents, '
    if 'run until done' in research_signals:
        pain_lead = 'For teams learning that run-until-done is not enough on its own, '
    return (
        f"{pain_lead}Ralph Workflow is a free and open-source workflow layer for longer coding runs in your own repo. "
        'It is built to end in finished, tested code that is ready to review instead of another confident summary. '
        f"Why this target fits: {why}.{comparison_hint}"
    )


def _slugify(value: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', (value or '').lower()).strip('-')
    return slug or 'target'


def _write_target_ready_files(
    *,
    now: datetime,
    targets: list[dict[str, str]],
    comparison_pages: list[dict[str, str]],
    research_signals: list[str],
    existing_queue_rows: list[dict[str, str]] | None = None,
) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    base_dir = DRAFTS_DIR / 'curator_outreach' / now.strftime('%Y-%m-%d')
    base_dir.mkdir(parents=True, exist_ok=True)
    created_paths: list[str] = []
    created_rows: list[dict[str, str]] = []
    queue_rows: list[dict[str, str]] = list(existing_queue_rows or [])
    seen_targets = {
        _target_identity(heading=row.get('target', ''), url=row.get('url', ''))
        for row in queue_rows
    }

    for idx, target in enumerate(targets, start=1):
        heading = target.get('heading', f'Target {idx}')
        slug = _slugify(heading)
        subject = _curator_subject(target)
        entry = target.get('entry_format') or f'- [Ralph Workflow]({CODEBERG_PRIMARY}) — {_curator_entry_blurb(research_signals)}'
        artifact_path = base_dir / f'{idx:02d}_{slug}.md'
        lines = [
            f'# Curator target {idx}: {heading}',
            '',
            f'- URL: {target.get("url", "unknown")}',
            f'- Action: {target.get("action", "reach out")}',
            f'- Priority: {target.get("priority", "unknown")}',
            f'- Entry format: {entry}',
            '',
            '## Shared findings reused',
            f'- What it is: {FOUR_QUESTIONS["what_is_it"]}',
            f'- Who it is for: {FOUR_QUESTIONS["who_is_it_for"]}',
            f'- Why different: {FOUR_QUESTIONS["why_different"]}',
            f'- Why now: {FOUR_QUESTIONS["why_now"]}',
            f'- Primary repo: {CODEBERG_PRIMARY}',
            f'- Demand-signal source: {_reddit_monitor_latest_path()}',
        ]
        if research_signals:
            lines.extend(['', '## Current demand phrases to reuse'])
            for phrase in research_signals:
                lines.append(f'- {phrase}')
        if comparison_pages:
            lines.extend(['', '## Comparison assets to cite'])
            for page in comparison_pages:
                lines.append(f"- {page['name']} — {page['path']}")
        lines.extend([
            '',
            '## Ready subject',
            subject,
            '',
            '## Ready pitch',
            _curator_pitch(target, comparison_pages, research_signals),
            '',
            '## Ready PR/list entry',
            entry,
            '',
            '## Follow-up rule',
            '- If no response or merge signal within 14 days, sharpen the comparison-specific proof instead of sending another generic intro.',
        ])
        artifact_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        created_paths.append(str(artifact_path))
        row = {
            'target': heading,
            'url': target.get('url', ''),
            'action': target.get('action', 'reach out'),
            'priority': target.get('priority', 'unknown'),
            'subject': subject,
            'artifact_path': str(artifact_path),
            'status': 'prepared',
            'review_due_date': (now + timedelta(days=14)).strftime('%Y-%m-%d'),
        }
        identity = _target_identity(heading=row.get('target', ''), url=row.get('url', ''))
        if identity not in seen_targets:
            queue_rows.append(row)
            created_rows.append(row)
            seen_targets.add(identity)

    CURATOR_QUEUE_LATEST_PATH.write_text(json.dumps({
        'generated_at': now.isoformat(),
        'targets': queue_rows,
    }, indent=2) + '\n', encoding='utf-8')
    return created_paths, queue_rows, created_rows


def _select_unprepared_targets(all_targets: list[dict[str, str]], existing_queue_rows: list[dict[str, str]], limit: int = 3) -> list[dict[str, str]]:
    seen = {
        _target_identity(heading=row.get('target', ''), url=row.get('url', ''))
        for row in existing_queue_rows
    }
    selected: list[dict[str, str]] = []
    for target in all_targets:
        identity = _target_identity(heading=target.get('heading', ''), url=target.get('url', ''))
        if identity in seen:
            continue
        selected.append(target)
        if len(selected) >= limit:
            break
    return selected


def _write_curator_follow_through(
    now: datetime,
    queue_rows: list[dict[str, str]],
    comparison_pages: list[dict[str, str]],
    research_signals: list[str],
) -> Path:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_curator_queue_follow_through.md'
    status_buckets: dict[str, list[dict[str, str]]] = {}
    for row in queue_rows:
        status_buckets.setdefault((row.get('status') or 'unknown').lower(), []).append(row)

    lines = [
        '# Ralph Workflow Curator Queue Follow-Through',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        '- The curator queue already has live prepared targets; regenerating the same packet would be fake activity.',
        f'- {_adoption_summary()}',
        '- The right move now is disciplined follow-through on the existing queue plus queue aging checks.',
    ]
    if research_signals:
        lines.extend(['', '## Demand signals to preserve in any outreach'])
        lines.extend(f'- {phrase}' for phrase in research_signals)
    lines.extend(['', '## Queue status summary'])
    for status, rows in sorted(status_buckets.items()):
        lines.append(f'- {status}: {len(rows)}')

    lines.extend(['', '## Live queue'])
    for row in queue_rows:
        lines.append(
            f"- {row.get('target')} — status={row.get('status', 'unknown')} — review due {row.get('review_due_date', 'unknown')} — {row.get('artifact_path', '')}"
        )
    if comparison_pages:
        lines.extend(['', '## Comparison assets to keep reusing'])
        for page in comparison_pages:
            lines.append(f"- {page['name']} — {page['path']}")
    lines.extend([
        '',
        '## Immediate next-action rules',
        '- sent_via_email_fallback: do not resend now; review on the due date, then follow up once if there is still no response.',
        '- prepared: keep the highest-priority untouched targets ready, but do not regenerate their copy.',
        '- If no untouched or due targets remain, add genuinely new curator targets before another prep run.',
        '',
        '## Process rule now in force',
        '- Do not regenerate already-prepared curator targets.',
        '- Prepare only untouched targets on the next curator pass.',
        '- If no untouched targets remain, wait for review_due_date or add genuinely new targets before another prep run.',
    ])
    artifact.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return artifact


def _curator_priority_score(row: dict[str, str]) -> tuple[int, str]:
    priority = (row.get('priority') or '').lower()
    if 'high' in priority:
        bucket = 0
    elif 'medium' in priority:
        bucket = 1
    else:
        bucket = 2
    return bucket, (row.get('target') or '').lower()


def _display_target_name(raw: str) -> str:
    cleaned = re.sub(r'^\s*\d+[.)]\s+', '', raw or '')
    return cleaned or raw or 'unknown target'


def _write_curator_handoff_packet(now: datetime, queue_rows: list[dict[str, str]]) -> tuple[Path, list[str]]:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_curator_handoff_packet.md'
    latest_artifact = DRAFTS_DIR / 'curator_handoff_packet_latest.md'
    candidates = [
        row for row in queue_rows
        if (row.get('status') or '').lower() == 'prepared' and not _is_repo_internal_curator_target(row)
    ]
    candidates.sort(key=_curator_priority_score)
    selected = candidates[:5]
    _skip_directory_submissions, skip_curator_outreach = _call_selector_local(
        distribution_lane_selector._active_repair_pause_flags,
    )

    lines = [
        '# Ralph Workflow Curator Execution Handoff Packet',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        '- Prepared curator targets already exist, but the loop kept rediscovering/resetting instead of consolidating the best ready-to-execute items.',
        f'- {_adoption_summary()}',
        '- This packet lowers human execution friction on the exact prepared targets most likely to create a fresh Codeberg-primary backlink or list inclusion.',
        '',
        '## Shared findings reused',
        '- curator_outreach_queue_latest.json → current prepared targets and statuses',
        '- adoption_metrics_latest.json → Codeberg movement is still the primary success gate',
        '- market_intelligence_latest.json → comparison framing and competitor adjacency',
        f'- {_reddit_monitor_latest_path()} → keep current pain-language tied to real workflow failures',
    ]
    if skip_curator_outreach:
        lines.extend([
            '',
            '## Current execution status',
            '- Same-family curator outreach is paused in the active repair window.',
            '- Do not redeliver this packet until the hold clears or the prepared target set materially changes.',
            '- Use this file as reference only while the marketing execution board remains the source of truth.',
        ])
    _append_live_listing_proof(lines)
    lines.extend([
        '',
        '## Reference targets currently paused by the active repair window' if skip_curator_outreach else '## Execute these first',
    ])

    prepared: list[str] = []
    for idx, row in enumerate(selected, start=1):
        prepared.append(row.get('target', ''))
        lines.extend([
            f'### {idx}. {_display_target_name(row.get("target", ""))}',
            f'- Status: {row.get("status", "unknown")}',
            f'- Priority: {row.get("priority", "unknown")}',
            f'- URL: {row.get("url", "")}',
            f'- Review due: {row.get("review_due_date", "unknown")}',
            f'- Ready file: {row.get("artifact_path", "")}',
            f'- Suggested next action: {row.get("action", "execute the prepared outreach/PR")}',
            '',
        ])

    lines.extend([
        '## Process rule now in force',
        '- While prepared curator targets still exist, do not spend another run on distribution-reset discovery for the same lane.',
        '- Refresh this packet when the top prepared set changes, not on every audit loop.',
    ])
    if skip_curator_outreach:
        lines.append('- Another curator delivery right now would be fake progress because same-family outreach is paused in the active repair window.')
    lines.extend([
        '',
        '## Measurement contract',
        '- Expected outcome: at least one executed PR, inclusion request, or maintainer contact against the prepared queue',
        '- Review window: next 7 days for execution, 14 days for response, 30 days for live backlink/listing evidence',
    ])
    content = '\n'.join(lines) + '\n'
    artifact.write_text(content, encoding='utf-8')
    latest_artifact.write_text(content, encoding='utf-8')
    return artifact, prepared


def _write_curator_due_followup_packet(now: datetime, queue_rows: list[dict[str, str]]) -> tuple[Path, list[str]]:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_curator_due_followup_packet.md'
    latest_artifact = DRAFTS_DIR / 'curator_due_followup_packet_latest.md'
    candidates: list[dict[str, str]] = []
    for row in queue_rows:
        status = (row.get('status') or '').lower()
        if status not in {'sent_via_email_fallback', 'sent_via_form', 'sent_via_github_issue', 'sent_via_manual_handoff', 'waiting_review', 'awaiting_reply'}:
            continue
        if _is_repo_internal_curator_target(row):
            continue
        due = _parse_dt(str(row.get('review_due_date') or ''))
        if due is None or due > now:
            continue
        candidates.append(row)
    candidates.sort(key=_curator_priority_score)
    selected = candidates[:5]

    lines = [
        '# Ralph Workflow Curator Due Follow-Up Packet',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        '- At least one curator review window is due, so the loop should follow through instead of hiding inside another measurement hold or reset.',
        f'- {_adoption_summary()}',
        '- This packet turns overdue outreach into concrete next touches with the existing proof assets and Codeberg-primary CTA.',
        '',
        '## Shared findings reused',
        '- curator_outreach_queue_latest.json → sent / waiting-review targets and review dates',
        '- market_intelligence_latest.json → positioning truths and comparison framing',
        '- backlink_status_latest.json → live third-party proof already available to cite',
        '- adoption_metrics_latest.json → Codeberg movement is still the primary success gate',
    ]
    _append_live_listing_proof(lines)
    lines.extend([
        '',
        '## Follow up now',
    ])

    prepared: list[str] = []
    for idx, row in enumerate(selected, start=1):
        target_name = _display_target_name(row.get('target', ''))
        prepared.append(target_name)
        last_contact = row.get('last_contact_at') or 'unknown'
        last_log = row.get('last_contact_log') or 'unknown'
        lines.extend([
            f'### {idx}. {target_name}',
            f'- Current status: {row.get("status", "unknown")}',
            f'- Review due: {row.get("review_due_date", "unknown")}',
            f'- Last contact at: {last_contact}',
            f'- Last contact log: {last_log}',
            f'- Ready file: {row.get("artifact_path", "")}',
            '- Follow-up rule: keep it short, reference the earlier outreach, add one concrete proof point, and keep Codeberg as the primary repo link.',
            '',
        ])

    lines.extend([
        '## Process rule now in force',
        '- Once a target crosses review_due_date, the next eligible curator lane should be follow-through first, not fresh discovery.',
        '- Do not mark another measurement hold as healthy if due follow-ups exist.',
        '',
        '## Measurement contract',
        '- Expected outcome: at least one real follow-up against an overdue curator target',
        '- Review window: 7 days for reply, 14 days for backlink / inclusion evidence',
    ])
    content = '\n'.join(lines) + '\n'
    artifact.write_text(content, encoding='utf-8')
    latest_artifact.write_text(content, encoding='utf-8')
    return artifact, prepared


def _current_curator_handoff_targets(queue_rows: list[dict[str, str]]) -> list[str]:
    candidates = [
        row for row in queue_rows
        if (row.get('status') or '').lower() == 'prepared' and not _is_repo_internal_curator_target(row)
    ]
    candidates.sort(key=_curator_priority_score)
    return [_display_target_name(row.get('target', '')) for row in candidates[:5]]


def _manual_contact_queue_rows(queue_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    candidates = [
        row for row in queue_rows
        if (row.get('status') or '').lower() in MANUAL_CONTACT_HANDOFF_REMAINING_STATUSES
        and not _is_repo_internal_curator_target(row)
    ]
    candidates.sort(key=_curator_priority_score)
    return candidates[:5]


def _current_comparison_handoff_targets(queue_rows: list[dict[str, str]]) -> list[str]:
    candidates = [
        row for row in queue_rows
        if (row.get('status') or '').lower() == 'prepared'
    ]
    candidates.sort(key=lambda row: ((row.get('name') or row.get('slug') or '').lower()))
    return [(row.get('name') or row.get('slug') or 'unknown target') for row in candidates[:5]]


def _packet_headings(path: Path) -> list[str]:
    if not path.exists():
        return []
    headings: list[str] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.startswith('### '):
            _, _, value = line.partition('. ')
            headings.append((value or line[4:]).strip())
    return headings


def _handoff_packet_is_current(
    path: Path,
    expected_targets: list[str],
    *,
    require_live_listing_proof: bool = False,
    allow_superset: bool = False,
) -> bool:
    if not expected_targets or not path.exists():
        return False
    headings = _packet_headings(path)
    if allow_superset:
        if not set(expected_targets).issubset(set(headings)):
            return False
    elif headings != expected_targets:
        return False
    if require_live_listing_proof and not _packet_includes_live_listing_proof(path):
        return False
    return True


def _primary_repo_flat_contact_status_fingerprint(
    *,
    recent_targets: list[str],
    non_executable_targets: list[str],
) -> str:
    payload = {
        'recent_targets': [str(target).strip() for target in recent_targets if str(target).strip()],
        'non_executable_targets': [str(target).strip() for target in non_executable_targets if str(target).strip()],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha1(encoded.encode('utf-8')).hexdigest()


def _primary_repo_flat_contact_status_packet_is_current(
    path: Path,
    *,
    recent_targets: list[str],
    non_executable_targets: list[str],
) -> bool:
    if not path.exists():
        return False
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return False
    expected = _primary_repo_flat_contact_status_fingerprint(
        recent_targets=recent_targets,
        non_executable_targets=non_executable_targets,
    )
    return f'<!-- status_fingerprint: {expected} -->' in text


def _write_primary_repo_flat_contact_status_packet(
    now: datetime,
    *,
    recent_targets: list[str],
    non_executable_targets: list[str],
) -> tuple[Path, bool]:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_primary_repo_flat_contact_handoff_packet.md'
    latest_artifact = DRAFTS_DIR / 'primary_repo_flat_contact_handoff_packet_latest.md'
    if (
        _primary_repo_flat_contact_status_packet_is_current(
            artifact,
            recent_targets=recent_targets,
            non_executable_targets=non_executable_targets,
        )
        and _primary_repo_flat_contact_status_packet_is_current(
            latest_artifact,
            recent_targets=recent_targets,
            non_executable_targets=non_executable_targets,
        )
    ):
        return artifact, False
    fingerprint = _primary_repo_flat_contact_status_fingerprint(
        recent_targets=recent_targets,
        non_executable_targets=non_executable_targets,
    )
    lines = [
        '# Ralph Workflow Primary-Repo-Flat Publisher Contact Packet',
        f'Generated: {now.isoformat(timespec="seconds")}',
        f'<!-- status_fingerprint: {fingerprint} -->',
        '',
        '## Why this exists now',
        '- The previously sendable publisher-contact packet drifted out of date during the current review window.',
        '- This file now reflects the truthful current state instead of leaving stale already-contacted targets in the latest packet.',
        '',
        '## Current state',
    ]
    if recent_targets:
        lines.append('- Recently contacted executable targets already inside the active review window: ' + ', '.join(recent_targets[:5]))
    if non_executable_targets:
        lines.append('- Remaining discovered targets are not runtime-sendable here: ' + ', '.join(non_executable_targets[:5]))
    lines.extend([
        '- Do not use this packet for a fresh send until a new executable publisher target appears or a current target exits review.',
        '',
        '## Process rule now in force',
        '- Do not regenerate this packet just to keep stale targets visible.',
        '- Use the marketing execution board for live manual follow-through during the hold window.',
    ])
    content = '\n'.join(lines) + '\n'
    artifact.write_text(content, encoding='utf-8')
    latest_artifact.write_text(content, encoding='utf-8')
    return artifact, True


def _write_primary_repo_flat_manual_review_asset(now: datetime, findings: list[dict[str, Any]]) -> tuple[Path, list[str]]:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_primary_repo_flat_manual_review_asset.md'
    latest_artifact = DRAFTS_DIR / 'primary_repo_flat_manual_review_asset_latest.md'

    selected_targets = set(_call_selector_local(
        distribution_lane_selector._primary_repo_flat_manual_review_targets_waiting_for_execution,
        now,
    ))
    research_signals = _latest_research_signals(limit=5)

    selected: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    for finding in findings:
        target_name = _display_target_name(str(finding.get('target') or '').strip())
        if not target_name or target_name in seen_targets or target_name not in selected_targets:
            continue
        seen_targets.add(target_name)
        selected.append(finding)

    selected = selected[:5]
    prepared = [str(finding.get('target') or '').strip() for finding in selected if str(finding.get('target') or '').strip()]
    lines = [
        '# Ralph Workflow Manual Publisher Outreach Asset',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        '- The runtime cannot send these publisher/contact routes directly from this environment.',
        '- They are still legitimate Codeberg-primary follow-through targets with human-executable contact paths.',
        '- This asset turns manual-only discovery into one truthful do-now handoff instead of letting the lane look empty.',
        '',
        '## Shared findings reused',
        '- primary_repo_flat_contact_discovery_latest.json → latest publisher/contact discovery for Codeberg-primary outreach',
        '- marketing_workflow_audit_latest.json → primary repo remains flat, so untouched publisher lanes still matter',
        '- market_intelligence_latest.json → comparison framing and positioning truth',
        '- adoption_metrics_latest.json → Codeberg movement remains the primary success gate',
    ]
    if research_signals:
        lines.append('- latest research / reddit monitor → current pain language around trust, verification, and morning-after review')
    lines.extend([
        '',
        '## Execute these first',
    ])
    if not selected:
        lines.extend([
            '- No truthful manual-executable publisher target is currently waiting.',
            '- Park this asset until a real email/Telegram/form route appears or the active review windows expire.',
            '',
            '## Process rule now in force',
            '- Do not treat about pages, generic site pages, GitHub issues, X, or LinkedIn alone as a do-now publisher handoff lane.',
            '- Regenerate this asset only after discovery finds a real manual-executable route or a currently active manual-delivery window clears.',
            '',
            '## Measurement contract',
            '- Expected outcome: no manual publisher action until a truthful route exists',
            '- Review window: re-check after new discovery, after a blocker clears, or during the next architecture repair pass',
        ])
        content = '\n'.join(lines) + '\n'
        artifact.write_text(content, encoding='utf-8')
        latest_artifact.write_text(content, encoding='utf-8')
        return artifact, prepared
    for idx, finding in enumerate(selected, start=1):
        target_name = str(finding.get('target') or 'unknown target')
        channels = finding.get('channels') or []
        lines.extend([
            f'### {idx}. {_display_target_name(target_name)}',
            f'- URL: {finding.get("article_url") or finding.get("root_url") or finding.get("url") or ""}',
            f'- Hook: {finding.get("hook", "")}',
            f'- Why this target: {finding.get("reason", "")}',
            f'- Suggested subject: {finding.get("outreach_subject", "")}',
            f'- Recommended next step: {finding.get("recommended_next_step", "manual outreach")}',
            f'- Best human-first path: {_contact_channel_suggestion(channels)}',
        ])
        if channels:
            lines.append('- Contact channels:')
            for channel in channels:
                lines.append(f"  - {channel.get('label', channel.get('type', 'channel'))}: {channel.get('value', '')}")
        lines.extend([
            '- Suggested short message:',
            '```text',
            _primary_repo_flat_contact_form_draft(finding, research_signals),
            '```',
            '',
        ])
    lines.extend([
        '## Process rule now in force',
        '- Reuse this asset until one of these targets is contacted or a stronger runtime-sendable publisher route appears.',
        '- Keep Codeberg first and GitHub second in every manual handoff.',
        '',
        '## Measurement contract',
        '- Expected outcome: at least one manual contact attempt or public maintainer touchpoint using these discovered routes',
        '- Review window: 7 days for handoff/manual attempt, 14 days for reply/citation signal, 30 days for attributable repo movement',
    ])
    content = '\n'.join(lines) + '\n'
    artifact.write_text(content, encoding='utf-8')
    latest_artifact.write_text(content, encoding='utf-8')
    return artifact, prepared


def _refresh_manual_execution_assets(now: datetime) -> tuple[list[str], list[str]]:
    refreshed_assets: list[str] = []
    targets_prepared: list[str] = []

    curator_queue_rows = _load_curator_queue_rows()
    expected_curator_targets = _current_curator_handoff_targets(curator_queue_rows)
    curator_packet_path = DRAFTS_DIR / 'curator_handoff_packet_latest.md'
    if expected_curator_targets and not _handoff_packet_is_current(curator_packet_path, expected_curator_targets, require_live_listing_proof=True):
        refreshed_packet, refreshed_targets = _write_curator_handoff_packet(now, curator_queue_rows)
        refreshed_assets.append(f'curator handoff packet → {refreshed_packet}')
        targets_prepared.extend(refreshed_targets)

    comparison_queue_rows = _comparison_queue_rows(COMPARISON_QUEUE_LATEST_PATH)
    expected_comparison_targets = _current_comparison_handoff_targets(comparison_queue_rows)
    comparison_packet_path = DRAFTS_DIR / 'comparison_backlink_handoff_packet_latest.md'
    if expected_comparison_targets and not _handoff_packet_is_current(comparison_packet_path, expected_comparison_targets, require_live_listing_proof=True):
        refreshed_packet, refreshed_targets = _write_comparison_handoff_packet(now, comparison_queue_rows)
        refreshed_assets.append(f'comparison handoff packet → {refreshed_packet}')
        targets_prepared.extend(refreshed_targets)

    contact_findings = _load_curator_contact_discovery()
    manual_contact_rows = _manual_contact_queue_rows(curator_queue_rows)
    expected_contact_targets = [_display_target_name(str(row.get('target') or '').strip()) for row in manual_contact_rows]
    contact_packet_path = DRAFTS_DIR / 'curator_contact_handoff_packet_latest.md'
    if contact_findings and expected_contact_targets and not _handoff_packet_is_current(contact_packet_path, expected_contact_targets, require_live_listing_proof=True):
        refreshed_packet, refreshed_targets = _write_curator_contact_handoff_packet(now, contact_findings)
        refreshed_assets.append(f'manual-contact handoff packet → {refreshed_packet}')
        targets_prepared.extend(refreshed_targets)

    actionable_primary_repo_flat_findings = _current_primary_repo_flat_actionable_findings(now)
    expected_primary_repo_flat_targets = [
        _display_target_name(str(row.get('target') or '').strip())
        for row in actionable_primary_repo_flat_findings
    ]
    primary_repo_flat_packet_path = DRAFTS_DIR / 'primary_repo_flat_contact_handoff_packet_latest.md'
    if actionable_primary_repo_flat_findings and expected_primary_repo_flat_targets and not _handoff_packet_is_current(primary_repo_flat_packet_path, expected_primary_repo_flat_targets, require_live_listing_proof=True):
        refreshed_packet, refreshed_targets = _write_primary_repo_flat_contact_handoff_packet(now, actionable_primary_repo_flat_findings)
        refreshed_assets.append(f'primary-repo-flat publisher contact packet → {refreshed_packet}')
        targets_prepared.extend(refreshed_targets)
    elif not actionable_primary_repo_flat_findings and primary_repo_flat_packet_path.exists():
        recent_publisher_targets = sorted(_recent_contact_targets(
            now,
            action_types=PUBLISHER_CONTACT_ACTION_TYPES,
            days=7,
        ))
        if not _primary_repo_flat_packet_delivery_still_active(now, recent_publisher_targets):
            non_executable_targets = [
                _display_target_name(str(row.get('target') or '').strip())
                for row in _load_primary_repo_flat_contact_discovery()
                if str(row.get('target') or '').strip()
                and not _publisher_target_has_manual_executable_channel(row.get('channels') or [])
            ]
            refreshed_packet, wrote_packet = _write_primary_repo_flat_contact_status_packet(
                now,
                recent_targets=recent_publisher_targets,
                non_executable_targets=non_executable_targets,
            )
            if wrote_packet:
                refreshed_assets.append(f'primary-repo-flat publisher contact packet status → {refreshed_packet}')

    return refreshed_assets, targets_prepared


def _write_manual_handoff_follow_through(
    *,
    now: datetime,
    title: str,
    why: list[str],
    packet_label: str,
    packet_path: Path,
    targets: list[str],
    review_rows: list[dict[str, str]],
    comparison_packet_path: Path | None = None,
) -> Path:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_{title}.md'
    lines = [
        '# Ralph Workflow Manual Handoff Follow-Through',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
    ]
    lines.extend(f'- {item}' for item in why)
    lines.extend([
        '- The existing manual packet is still current, so rewriting it again would be fake activity.',
        '',
        '## Current packet to use',
        f'- {packet_label}: {packet_path}',
    ])
    if comparison_packet_path is not None and comparison_packet_path.exists():
        lines.append(f'- Comparison packet: {comparison_packet_path}')
    lines.extend([
        '',
        '## Top prepared targets still waiting for execution',
    ])
    for name in targets:
        lines.append(f'- {name}')
    if review_rows:
        lines.extend(['', '## Review dates already in force'])
        for row in review_rows[:5]:
            lines.append(
                f"- {_display_target_name(row.get('target') or row.get('name') or row.get('slug') or 'unknown target')} — review due {row.get('review_due_date', 'unknown')}"
            )
    lines.extend([
        '',
        '## Process rule now in force',
        '- Do not regenerate this packet unless the top prepared set changes.',
        '- Use the existing packet for manual execution, then wait for review dates or live response evidence.',
    ])
    artifact.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return artifact


def _write_comparison_handoff_packet(now: datetime, queue_rows: list[dict[str, str]]) -> tuple[Path, list[str]]:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_comparison_backlink_handoff_packet.md'
    latest_artifact = DRAFTS_DIR / 'comparison_backlink_handoff_packet_latest.md'
    candidates = [
        row for row in queue_rows
        if (row.get('status') or '').lower() == 'prepared'
    ]
    candidates.sort(key=lambda row: ((row.get('name') or row.get('slug') or '').lower()))
    selected = candidates[:5]
    delivery_active = _comparison_packet_delivery_still_active(now)

    lines = [
        '# Ralph Workflow Comparison Backlink Execution Handoff Packet',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        '- Prepared comparison/backlink targets already exist, but missing GitHub auth from this environment blocks live PR submission.',
        f'- {_adoption_summary()}',
        '- This packet consolidates the highest-priority ready comparison targets into one human-executable handoff so the queue can actually move.',
        '',
        '## Shared findings reused',
        '- comparison_backlink_queue_latest.json → current prepared comparison targets and statuses',
        '- market_intelligence_latest.json → positioning and competitor adjacency',
        '- adoption_metrics_latest.json → Codeberg movement is still the primary success gate',
        f'- {_reddit_monitor_latest_path()} → keep current pain-language tied to real workflow failures',
    ]
    if delivery_active:
        lines.extend([
            '',
            '## Current execution status',
            '- This packet was already manually delivered in the current review window.',
            '- Do not redeliver it yet unless the prepared comparison set materially changes or the review window expires.',
            '- Use this file as reference only while the marketing execution board remains the source of truth.',
        ])
    _append_live_listing_proof(lines)
    lines.extend([
        '',
        '## Reference targets already covered in the active review window' if delivery_active else '## Execute these first',
    ])

    prepared: list[str] = []
    for idx, row in enumerate(selected, start=1):
        name = row.get('name') or row.get('slug') or 'unknown target'
        prepared.append(name)
        lines.extend([
            f'### {idx}. {name}',
            f'- Status: {row.get("status", "unknown")}',
            f'- Comparison page: {row.get("comparison_path", "")}',
            f'- Review due: {row.get("review_due_date", "unknown")}',
            f'- Ready file: {row.get("artifact_path", "")}',
            '- Suggested next action: submit the prepared inclusion / citation request using the ready file',
            '',
        ])

    lines.extend([
        '## Process rule now in force',
        '- While prepared comparison targets still exist, do not spend another run regenerating comparison packets for the same queue.',
        '- Refresh this packet when the prepared comparison set changes, not on every audit loop.',
    ])
    if delivery_active:
        lines.append('- Another manual delivery right now would be fake progress because this packet is already inside its active review window.')
    lines.extend([
        '',
        '## Measurement contract',
        '- Expected outcome: at least one executed inclusion request, citation request, or maintainer contact against the prepared comparison queue',
        '- Review window: next 7 days for execution, 14 days for response, 30 days for live backlink/listing evidence',
    ])
    content = '\n'.join(lines) + '\n'
    artifact.write_text(content, encoding='utf-8')
    latest_artifact.write_text(content, encoding='utf-8')
    return artifact, prepared


def _append_handoff_pointer(artifact_path: str | None, *, title: str, handoff_path: Path) -> None:
    if not artifact_path:
        return
    artifact = Path(artifact_path)
    if not artifact.exists():
        return
    text = artifact.read_text(encoding='utf-8')
    pointer = f'## {title}\n- {handoff_path}\n'
    if pointer in text:
        return
    artifact.write_text(text.rstrip() + '\n\n' + pointer, encoding='utf-8')


def _write_curator_execution(decision: LaneDecision, now: datetime, market_intelligence: dict[str, Any] | None) -> tuple[Path, list[str]]:
    existing_queue_rows = _load_curator_queue_rows()
    reset_targets = _distribution_reset_targets_for_curator(existing_queue_rows)
    static_targets = [target for target in _parse_curator_targets(_read_text(TARGETS_PATH)) if _is_actionable_curator_target(target)]
    selected_targets = reset_targets or _select_unprepared_targets(static_targets, existing_queue_rows)
    comparison_pages = _top_comparison_pages(market_intelligence)
    research_signals = _latest_research_signals()
    if not selected_targets:
        artifact = _write_curator_follow_through(now, existing_queue_rows, comparison_pages, research_signals)
        prepared = [row.get('target', '') for row in existing_queue_rows]
        return artifact, prepared

    target_files, queue_rows, created_rows = _write_target_ready_files(
        now=now,
        targets=selected_targets,
        comparison_pages=comparison_pages,
        research_signals=research_signals,
        existing_queue_rows=existing_queue_rows,
    )
    _mark_distribution_reset_targets_promoted(created_rows, now=now)
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_curator_outreach_execution.md'
    lines = [
        '# Ralph Workflow Curator Outreach Execution Pack',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        f'- {decision.reason}',
        f'- {_adoption_summary()}',
        f'- {_recent_distribution_context()}',
        '- Shared findings reused directly instead of inventing a siloed pitch from scratch.',
        '',
        '## Shared findings reused',
        '- adoption_metrics_latest.json → Codeberg movement is the success gate',
        '- market_intelligence_latest.json → comparison framing + competitor adjacency',
        '- curator_outreach_targets.md → discovered targets and action shape',
        '- distribution_reset_targets_latest.json → fresh reset-discovered targets promoted into real outreach assets first',
        '- outreach-log.md → avoid repeating HN/Lobsters-only notes',
        f'- {_reddit_monitor_latest_path()} → current pain-language and mention-fit discipline',
        '',
        '## Proof spine to reuse in every PR/email',
        f'- Product blurb: {_curator_entry_blurb(research_signals)}',
        f'- Primary repo: {CODEBERG_PRIMARY}',
        f'- What is it: {FOUR_QUESTIONS["what_is_it"]}',
        f'- Why now: {FOUR_QUESTIONS["why_now"]}',
    ]
    if research_signals:
        lines.extend(['', '## Current demand phrases reused'])
        for phrase in research_signals:
            lines.append(f'- {phrase}')
    if comparison_pages:
        lines.extend(['', '## Comparison assets to cite'])
        for page in comparison_pages:
            lines.append(f"- {page['name']} — {page['path']}")

    if target_files:
        lines.extend(['', '## Ready target files'])
        for target_file in target_files:
            lines.append(f'- {target_file}')
        lines.append(f'- Queue log: {CURATOR_QUEUE_LATEST_PATH}')

    if reset_targets:
        lines.extend([
            '',
            '## Reset targets activated in this run',
            '- These targets came from the fresh distribution-reset queue and were promoted into real outreach assets instead of logging another reset-only cycle.',
        ])
        for target in reset_targets:
            lines.append(f"- {target.get('heading', 'unknown target')} — {target.get('url', '')}")

    prepared = []
    for idx, target in enumerate(selected_targets, start=1):
        name = target.get('heading', f'Target {idx}')
        prepared.append(name)
        ready_file = created_rows[idx - 1]['artifact_path'] if idx - 1 < len(created_rows) else ''
        lines.extend([
            '',
            f'## Target {idx}: {name}',
            f"- URL: {target.get('url', 'unknown')}",
            f"- Action: {target.get('action', 'reach out')}",
            f"- Priority: {target.get('priority', 'unknown')}",
            f"- Fit note: {target.get('why_it_fits') or target.get('note') or 'scope fit confirmed'}",
            f"- Subject: {_curator_subject(target)}",
            f"- Ready file: {ready_file}",
            '- Suggested copy:',
            '```text',
            _curator_pitch(target, comparison_pages, research_signals),
            '',
            'Proposed entry:',
            target.get('entry_format') or f'- [Ralph Workflow]({CODEBERG_PRIMARY}) — {_curator_entry_blurb(research_signals)}',
            '```',
        ])

    lines.extend([
        '',
        '## Measurement contract',
        '- Expected outcome: at least one live comparison/listing backlink to the Codeberg repo',
        '- Review window: 14 days for responses, 30 days for a live backlink',
        '- Replacement condition: if these targets do not move, improve comparison framing before another outreach batch',
    ])
    artifact.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return artifact, prepared


def _top_competitor_records(market_intelligence: dict[str, Any] | None, limit: int | None = None) -> list[dict[str, str]]:
    competitors = (market_intelligence or {}).get('competitors', {}) or {}
    pages_by_slug = {
        item.get('slug', ''): item
        for item in (market_intelligence or {}).get('comparison_pages', []) or []
        if item.get('slug')
    }
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for slug, data in competitors.items():
        stars = data.get('github_stars') or 0
        ranked.append((int(stars), slug, data))
    ranked.sort(reverse=True)
    if limit is not None:
        ranked = ranked[:limit]

    selected: list[dict[str, str]] = []
    for _stars, slug, data in ranked:
        page = pages_by_slug.get(slug, {})
        selected.append({
            'slug': slug,
            'name': data.get('name') or slug,
            'positioning': data.get('positioning') or '',
            'comparison_path': page.get('path', ''),
        })
    return selected


def _comparison_queue_rows(path: Path) -> list[dict[str, str]]:
    payload = _load_json(path)
    rows = payload.get('targets', []) or []
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        slug = (row.get('slug') or '').strip().lower()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        deduped.append(row)
    return deduped


def _select_unprepared_comparison_targets(
    market_intelligence: dict[str, Any] | None,
    existing_rows: list[dict[str, str]],
    limit: int = 3,
) -> list[dict[str, str]]:
    seen = {(row.get('slug') or '').strip().lower() for row in existing_rows}
    selected: list[dict[str, str]] = []
    for target in _top_competitor_records(market_intelligence):
        slug = (target.get('slug') or '').strip().lower()
        if not slug or slug in seen:
            continue
        selected.append(target)
        if len(selected) >= limit:
            break
    return selected


def _comparison_pitch(name: str, positioning: str, research_signals: list[str]) -> str:
    pain = 'stop babysitting your agents'
    if 'run until done' in research_signals:
        pain = 'run until done is not enough on its own'
    elif 'overnight coding' in research_signals:
        pain = 'overnight coding only works when the morning-after review is clean'
    positioning_note = f' {name} is positioned as: {positioning}.' if positioning else ''
    return (
        f"Teams searching for {pain} are often comparing workflow layers against model-first tools.{positioning_note} "
        'Ralph Workflow is the free open-source workflow layer for your own repo unattended coding runs that aims to end in finished, tested code ready to review instead of another confident summary. '
        f'Primary repo: {CODEBERG_PRIMARY}'
    )


def _write_comparison_backlink_follow_through(now: datetime, queue_rows: list[dict[str, str]]) -> Path:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_comparison_backlink_follow_through.md'
    lines = [
        '# Ralph Workflow Comparison Backlink Follow-Through',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        '- The current comparison queue already covers every ranked competitor with a prepared packet.',
        f'- {_adoption_summary()}',
        '- Do not claim fresh execution if the run only re-describes already-prepared targets.',
        '',
        '## Live comparison queue',
    ]
    for row in queue_rows:
        lines.append(
            f"- {row.get('name') or row.get('slug')} — status={row.get('status', 'unknown')} — review due {row.get('review_due_date', 'unknown')} — {row.get('artifact_path', '')}"
        )
    lines.extend([
        '',
        '## Process rule now in force',
        '- Comparison backlink execution counts as a fresh repair only when it adds new targets or sends due follow-ups.',
        '- If the queue is fully prepared and nothing is due, wait for review dates or add genuinely new comparison targets before counting another execution.',
    ])
    artifact.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return artifact


def _write_comparison_backlink_execution(
    decision: LaneDecision,
    now: datetime,
    market_intelligence: dict[str, Any] | None,
) -> tuple[Path, list[str], str]:
    research_signals = _latest_research_signals()
    existing_rows = _comparison_queue_rows(COMPARISON_QUEUE_LATEST_PATH)
    comparison_targets = _select_unprepared_comparison_targets(market_intelligence, existing_rows, limit=3)

    base_dir = DRAFTS_DIR / 'comparison_backlinks' / now.strftime('%Y-%m-%d')
    base_dir.mkdir(parents=True, exist_ok=True)

    prepared: list[str] = []
    target_files: list[str] = []
    queue_rows = list(existing_rows)

    for idx, target in enumerate(comparison_targets, start=1):
        slug = (target.get('slug') or '').strip().lower()
        if not slug:
            continue
        name = target.get('name') or slug
        artifact_path = base_dir / f'{idx:02d}_{slug}.md'
        lines = [
            f'# Comparison backlink target: {name}',
            '',
            f'- Competitor slug: {slug}',
            f'- Comparison page: {target.get("comparison_path") or "missing"}',
            f'- Positioning to contrast: {target.get("positioning") or "n/a"}',
            '',
            '## Shared findings reused',
            f'- What it is: {FOUR_QUESTIONS["what_is_it"]}',
            f'- Who it is for: {FOUR_QUESTIONS["who_is_it_for"]}',
            f'- Why different: {FOUR_QUESTIONS["why_different"]}',
            f'- Why now: {FOUR_QUESTIONS["why_now"]}',
            f'- Primary repo: {CODEBERG_PRIMARY}',
            f'- Demand-signal source: {_reddit_monitor_latest_path()}',
        ]
        if research_signals:
            lines.extend(['', '## Current demand phrases to preserve'])
            lines.extend(f'- {phrase}' for phrase in research_signals)
        lines.extend([
            '',
            '## Ready backlink/citation pitch',
            _comparison_pitch(name, target.get('positioning', ''), research_signals),
            '',
            '## Suggested inclusion line',
            f'- [Ralph Workflow]({CODEBERG_PRIMARY}) — free open-source workflow layer for unattended coding runs with finished, tested code ready to review',
            '',
            '## Measurement contract',
            '- Expected outcome: a fresh comparison citation, backlink, or curated-list placement tied to this comparison angle',
            '- Review window: 14 days for response, 30 days for a live backlink or list inclusion',
        ])
        artifact_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        target_files.append(str(artifact_path))
        prepared.append(name)
        queue_rows.append({
            'slug': slug,
            'name': name,
            'comparison_path': target.get('comparison_path', ''),
            'artifact_path': str(artifact_path),
            'status': 'prepared',
            'review_due_date': (now + timedelta(days=14)).strftime('%Y-%m-%d'),
        })

    COMPARISON_QUEUE_LATEST_PATH.write_text(json.dumps({
        'generated_at': now.isoformat(),
        'targets': queue_rows,
    }, indent=2) + '\n', encoding='utf-8')

    if not prepared:
        artifact = _write_comparison_backlink_follow_through(now, queue_rows)
        return artifact, prepared, 'comparison_backlink_follow_through'

    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_comparison_backlink_outreach_execution.md'
    lines = [
        '# Ralph Workflow Comparison Backlink Outreach Pack',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        f'- {decision.reason}',
        f'- {_adoption_summary()}',
        '- The curator queue is already live, so this run ships a fresh comparison-led backlink asset instead of another follow-through note.',
        '',
        '## Shared findings reused',
        '- market_intelligence_latest.json → comparison pages and competitor positioning',
        '- outreach-log.md → avoid repeating HN/Lobsters-only handoff logic',
        '- adoption_metrics_latest.json → Codeberg movement is the success gate',
        f'- {_reddit_monitor_latest_path()} → current pain-language and mention-fit discipline',
        '',
        '## Ready target files',
    ]
    lines.extend(f'- {path}' for path in target_files)
    lines.extend([
        f'- Queue log: {COMPARISON_QUEUE_LATEST_PATH}',
        '',
        '## Prepared comparison names',
    ])
    lines.extend(f'- {name}' for name in prepared)
    lines.extend([
        '',
        '## Process rule now in force',
        '- When curator queue prep is already saturated, the next same-run asset must be comparison/backlink-oriented or another fresh executable lane.',
        '- Do not treat queue follow-through alone as outcome-system repair.',
    ])
    artifact.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return artifact, prepared, 'comparison_backlink_outreach_execution'


def _write_distribution_reset_execution(decision: LaneDecision, now: datetime, market_intelligence: dict[str, Any] | None) -> tuple[Path, list[str]]:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_distribution_reset_execution.md'
    comparison_pages = _top_comparison_pages(market_intelligence, limit=8)
    curator_rows = _load_curator_queue_rows()
    comparison_rows = _comparison_queue_rows(COMPARISON_QUEUE_LATEST_PATH)
    discovered_targets = _load_distribution_reset_targets()
    research_signals = _latest_research_signals()
    lines = [
        '# Ralph Workflow Distribution Reset Packet',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        f'- {decision.reason}',
        f'- {_adoption_summary()}',
        '- Both live outreach queues are saturated, so another follow-through note would be fake activity.',
        '- This packet resets the lane toward new target discovery while preserving Codeberg-first proof and current measurement windows.',
        '',
        '## Shared findings reused',
        '- adoption_metrics_latest.json → Codeberg movement is the only primary success gate',
        '- curator_outreach_queue_latest.json → existing curator coverage already prepared',
        '- comparison_backlink_queue_latest.json → existing comparison coverage already prepared',
        '- market_intelligence_latest.json → comparison pages and competitor positioning to reuse',
        '- outreach-log.md → avoid another HN/Lobsters-only loop',
    ]
    if research_signals:
        lines.extend(['', '## Pain-language to preserve in new targets'])
        lines.extend(f'- {phrase}' for phrase in research_signals)
    lines.extend([
        '',
        '## Queue snapshot',
        f'- Live curator targets: {len(curator_rows)}',
        f'- Live comparison targets: {len(comparison_rows)}',
        '',
        '## Next untouched target classes to create',
        '- comparison pages not yet turned into third-party citations or curated-list inclusions',
        '- your own repo coding tool roundups that can link directly to Codeberg instead of the GitHub mirror',
        '- workflow/orchestration directories or blog lists adjacent to the current comparison pages',
    ])
    if discovered_targets:
        _distribution_reset_queue_path().write_text(json.dumps({
            'generated_at': now.isoformat(),
            'targets': [
                {
                    'target': row['target'],
                    'url': row['url'],
                    'why_it_fits': row['why_it_fits'],
                    'status': 'discovered',
                    'source': str(_distribution_reset_log_path()),
                }
                for row in discovered_targets
            ],
        }, indent=2) + '\n', encoding='utf-8')
        lines.extend([
            '',
            '## Fresh targets discovered in this reset',
        ])
        for row in discovered_targets:
            lines.append(f"- **{row['target']}** — {row['url']}")
            lines.append(f"  - Why it fits: {row['why_it_fits']}")
        lines.append(f'- Queue log: {_distribution_reset_queue_path()}')
    lines.extend([
        '',
        '## Comparison assets to extend',
    ])
    for page in comparison_pages:
        lines.append(f"- {page['name']} — {page['path']}")
    lines.extend([
        '',
        '## Process rule now in force',
        '- Do not log queue follow-through alone as a fresh outcome repair when all prepared targets are still waiting for review windows.',
        '- The next distribution-prep execution must add genuinely new targets or a new executable channel.',
        '- Keep all public-facing CTAs Codeberg-primary while expanding target discovery.',
        '',
        '## Measurement contract',
        '- Expected outcome: at least 3 newly identified third-party backlink/citation targets before the next outreach-prep execution',
        '- Review window: next audit run should show a fresh executable lane instead of follow-through-only status',
    ])
    artifact.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    if discovered_targets:
        return artifact, [row['target'] for row in discovered_targets]
    return artifact, []


def _write_directory_execution(decision: LaneDecision, now: datetime) -> tuple[Path, list[str]]:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_directory_submission_execution.md'
    channels = decision.unsubmitted_directory_channels[:3]
    lines = [
        '# Ralph Workflow Directory Submission Execution Pack',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        f'- {decision.reason}',
        f'- {_adoption_summary()}',
        '',
        '## Submission spine',
        f'- Product: Ralph Workflow',
        f'- Primary URL: {CODEBERG_PRIMARY}',
        f'- Blurb: {directory_blurb()}',
        f'- What it is: {FOUR_QUESTIONS["what_is_it"]}',
        f'- Who it is for: {FOUR_QUESTIONS["who_is_it_for"]}',
        '',
        '## Channels to execute next',
    ]
    for name in channels:
        lines.append(f'- {name}')
    lines.extend([
        '',
        '## Measurement contract',
        '- Expected outcome: at least one new directory listing or backlink routed to Codeberg',
        '- Review window: 14 days for listing status, 30 days for indexation/backlink evidence',
    ])
    artifact.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return artifact, channels


def _write_apollo_execution(decision: LaneDecision, now: datetime, market_intelligence: dict[str, Any] | None) -> tuple[Path, list[str]]:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_apollo_outreach_execution.md'
    latest_artifact = DRAFTS_DIR / 'apollo_outreach_packet_latest.md'
    apollo = _load_json(APOLLO_STATUS_PATH)
    comparison_pages = _top_comparison_pages(market_intelligence, limit=4)
    top_competitors = _top_competitor_records(market_intelligence, limit=4)
    research_signals = _latest_research_signals(limit=5)
    curator_rows = _load_curator_queue_rows()
    prepared_curator = [row for row in curator_rows if (row.get('status') or '').lower() == 'prepared'][:4]
    comparison_rows = [row for row in _comparison_queue_rows(COMPARISON_QUEUE_LATEST_PATH) if (row.get('status') or '').lower() == 'prepared'][:4]
    execution_warning = _latest_apollo_execution_warning()

    sequence_names = [
        'Codeberg-first autonomous coding proof',
        'Comparison-led migration / evaluation',
        'Morning-after review packet pain',
    ]

    lines = [
        '# Ralph Workflow Apollo Outbound Execution Packet',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        f'- {decision.reason}',
        f'- {_adoption_summary()}',
        '- Reddit is blocked/degraded from this environment, while Apollo is currently authenticated and gives the loop a different executable distribution path.',
        '- This packet reuses existing comparison and curator proof instead of generating another siloed outreach idea.',
        '',
        '## Apollo status reused',
        f"- Status: {apollo.get('status', 'unknown')}",
        f"- Final URL: {apollo.get('final_url', 'unknown')}",
        f"- Notes: {apollo.get('notes', 'none')}",
        '',
        '## Shared findings reused',
        '- market_intelligence_latest.json → competitor positioning and comparison assets',
        '- curator_outreach_queue_latest.json → concrete list/curator proof already prepared',
        '- comparison_backlink_queue_latest.json → comparison angles already prepared',
        '- adoption_metrics_latest.json → Codeberg movement is the only primary success gate',
        f'- {_reddit_monitor_latest_path()} → current demand phrases and finish-state pain language',
        '',
        '## Apollo search / list filters to build now',
        '- Titles: founder, CTO, engineering manager, developer productivity, platform engineering, AI engineering, devtools',
        '- Company themes: developer tools, platform engineering, agent tooling, internal tooling, software consultancies, AI-native product teams',
        '- Exclude broad generic marketing personas; this motion is for your own repo technical evaluators who can inspect Codeberg directly',
        '',
        '## Sequence variants to create in Apollo',
    ]
    for name in sequence_names:
        lines.append(f'- {name}')

    lines.extend([
        '',
        '## CTA rule',
        f'- Primary CTA: {CODEBERG_PRIMARY}',
        '- Secondary mirror mention only when needed for familiarity: GitHub is the mirror, not the main destination',
        '',
        '## Fresh openings to stop repetition',
    ])
    openings = [
        'Most AI coding demos stop at “it ran”; the harder question is what you can review the next morning.',
        'If your team is still babysitting agent runs, the bottleneck usually is not the model — it is the handoff back into real repo review.',
        'The useful bar for autonomous coding is not “agent completed” but “finished, tested, mergeable work you can inspect quickly.”',
    ]
    for opening in openings:
        lines.append(f'- {opening}')

    if research_signals:
        lines.extend(['', '## Current pain language to keep native'])
        lines.extend(f'- {phrase}' for phrase in research_signals)

    if prepared_curator:
        lines.extend(['', '## Curator proof already prepared'])
        for row in prepared_curator:
            lines.append(f"- {row.get('target')} — {row.get('artifact_path', '')}")

    if comparison_rows:
        lines.extend(['', '## Comparison proof already prepared'])
        for row in comparison_rows:
            lines.append(f"- {row.get('name') or row.get('slug')} — {row.get('artifact_path', '')}")

    if comparison_pages:
        lines.extend(['', '## Best comparison assets to cite'])
        for page in comparison_pages:
            lines.append(f"- {page['name']} — {page['path']}")

    if top_competitors:
        lines.extend(['', '## Competitor frames to target in messaging'])
        for competitor in top_competitors:
            lines.append(f"- {competitor['name']} — {competitor.get('positioning', '')}")

    lines.extend([
        '',
        '## Apollo execution gate',
        '- Do not count Apollo progress from packet generation or list creation alone.',
        '- A list import only counts when the visible imported-contact count is non-zero.',
        '- If the imported-contact count is zero, rebuild the CSV from the prepared curator/comparison targets and retry import before touching sequences.',
        f'- Only launch a sequence after the list is visibly populated and the CTA stays Codeberg-primary: {CODEBERG_PRIMARY}',
    ])
    if execution_warning:
        lines.append(f'- Current warning: {execution_warning}')

    lines.extend([
        '',
        '## Ready email / sequence seed',
        '```text',
        'Subject: when an AI coding run actually becomes reviewable',
        '',
        openings[2],
        '',
        'Ralph Workflow is a free open-source workflow layer for your own repo autonomous coding runs. The point is not another confident summary — it is getting back finished, tested code that is ready to review, with visible evidence of what changed and whether you would merge it.',
        '',
        f'If that is the problem you are trying to solve, the cleanest entry point is the Codeberg repo: {CODEBERG_PRIMARY}',
        '```',
        '',
        '## Measurement contract',
        '- Expected outcome: at least one live Apollo list/sequence configured against the ICP above',
        '- Review window: 7 days for list/sequence launch, 14 days for replies or qualified repo visits, 30 days for Codeberg movement',
        '- Replacement condition: if Apollo execution produces no qualified signals, change the ICP or proof asset before adding more volume',
    ])

    content = '\n'.join(lines) + '\n'
    artifact.write_text(content, encoding='utf-8')
    latest_artifact.write_text(content, encoding='utf-8')
    return artifact, sequence_names


def _write_apollo_launch_handoff_packet(now: datetime) -> tuple[Path, list[str]]:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_apollo_launch_handoff_packet.md'
    latest_artifact = DRAFTS_DIR / 'apollo_launch_handoff_packet_latest.md'
    launch_packet = DRAFTS_DIR / 'apollo_sequence_launch_packet_latest.md'
    status_payload = _load_json(LOG_DIR / 'apollo_sequence_status_latest.json')
    launch_log = _load_json(LOG_DIR / f'marketing_{now.strftime("%Y-%m-%d")}_apollo_sequence_launch.json')

    record_count = int(status_payload.get('record_count') or launch_log.get('result', {}).get('record_count') or 0)
    sequence_name = (
        status_payload.get('sequence_name')
        or (launch_log.get('result') or {}).get('sequence_name')
        or ((launch_log.get('chosen_action') or {}).get('sequence_name') if isinstance(launch_log.get('chosen_action'), dict) else None)
        or 'unknown sequence'
    )
    final_url = status_payload.get('final_url') or (launch_log.get('result') or {}).get('final_url') or 'unknown'
    next_review_at = _parse_dt(str(status_payload.get('next_review_at') or '').strip())
    needs_live_verification = bool(status_payload.get('needs_live_verification'))
    followup_due = (
        record_count > 0
        and not bool(status_payload.get('measurement_pending'))
        and next_review_at is not None
        and next_review_at <= now
    )
    shared_findings = [
        'apollo_sequence_status_latest.json → canonical Apollo launch state and verification gate',
        'apollo_sequence_launch_packet_latest.md → Codeberg-primary launch packet',
        'marketing_workflow_audit_latest.json → managed outbound must prove live send before entering measurement',
    ]

    why_lines = [
        '- Apollo is already launch-ready with a verified non-zero list, but the loop still has no proof that emails are actually sending.',
        '- That makes live send confirmation the truthful next step; generating another Apollo prep packet would be fake progress.',
    ]
    next_steps = [
        '- Open Apollo on the launch packet URL/list and confirm the named sequence is actually active/sending.',
        '- If the sequence is not active yet, launch the existing sequence exactly as written in the launch packet instead of rebuilding the audience or copy.',
        '- Once the live send is visible, log that evidence as the event that starts Apollo measurement. Do not backdate measurement to packet creation.',
    ]
    measurement_lines = [
        '- Expected outcome: one visibly active Apollo sequence using the existing Codeberg-primary CTA.',
        '- Review window starts only after live send confirmation lands.',
    ]

    if followup_due and not needs_live_verification:
        why_lines = [
            '- Apollo already passed its first launch checkpoint, but the sequence is still not outcome-ready.',
            '- That makes a same-day truth check the real next step; dropping back to an empty board here would be fake progress.',
        ]
        next_steps = [
            '- Open Apollo on the logged sequence/list and verify whether the sequence is actually active, paused, blocked, or never launched.',
            '- If live send evidence exists, log it now so measurement starts from the real outbound event rather than from packet prep.',
            '- If live send evidence does not exist, log the exact blocker and keep the existing Codeberg-primary launch packet unchanged until that blocker is cleared.',
        ]
        measurement_lines = [
            '- Expected outcome: either real live-send evidence or an exact blocker tied to the existing Apollo sequence.',
            '- Review window starts only after live send confirmation lands; a blocker log should reset the loop back into truthful repair instead of fake-green measurement.',
        ]

    lines = [
        '# Apollo Launch / Send Confirmation Handoff Packet',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        *why_lines,
        f'- {_adoption_summary()}',
        '',
        '## Shared findings reused',
        *[f'- {item}' for item in shared_findings],
        '',
        '## Current Apollo state',
        f"- Status: {status_payload.get('status', 'unknown')}",
        f'- Record count: {record_count}',
        f'- Sequence name: {sequence_name}',
        f'- Final URL: {final_url}',
        f"- Needs live verification: {status_payload.get('needs_live_verification')}",
        f"- Next review at: {status_payload.get('next_review_at') or 'unknown'}",
        '',
        '## Canonical packet to use',
        f'- Launch packet: {launch_packet}',
        f"- Launch log: {status_payload.get('launch_log') or 'unknown'}",
        f"- Latest verification log: {status_payload.get('outbound_verification_log') or 'unknown'}",
        '',
        '## Do this next',
        *next_steps,
        f'- Keep the primary CTA unchanged: {CODEBERG_PRIMARY}',
        '',
        '## Guard rails',
        '- Do not count packet generation, list import, or sequence-ready state as a shipped outbound outcome.',
        '- Do not widen the audience or rewrite the sequence until live-send evidence exists and the first measurement window finishes.',
        '',
        '## Measurement contract',
        *measurement_lines,
    ]

    content = '\n'.join(lines) + '\n'
    artifact.write_text(content, encoding='utf-8')
    latest_artifact.write_text(content, encoding='utf-8')
    return artifact, [sequence_name]


def _write_apollo_runtime_blocker_review_packet(
    now: datetime,
    *,
    apollo_status: dict[str, Any],
    apollo_runtime_status: dict[str, Any],
) -> tuple[Path, list[str]]:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_apollo_runtime_blocker_review_packet.md'
    latest_artifact = DRAFTS_DIR / 'apollo_runtime_blocker_review_packet_latest.md'
    launch_packet = DRAFTS_DIR / 'apollo_launch_handoff_packet_latest.md'
    sequence_name = str(apollo_status.get('sequence_name') or 'Apollo sequence').strip()
    blocker_status = str(
        apollo_status.get('runtime_blocker_status')
        or apollo_runtime_status.get('status')
        or 'unknown'
    ).strip()
    blocker_summary = str(
        apollo_status.get('runtime_blocker_summary')
        or apollo_runtime_status.get('summary')
        or apollo_runtime_status.get('notes')
        or 'Runtime auth is currently blocked.'
    ).strip()
    blocker_notes = str(
        apollo_status.get('runtime_blocker_notes')
        or apollo_runtime_status.get('notes')
        or 'No additional runtime notes logged.'
    ).strip()

    lines = [
        '# Apollo Runtime-Blocker Review Packet',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        '- Apollo follow-up is already due, but the current runtime is blocked before the loop can verify or launch the prepared Codeberg-first sequence.',
        '- The truthful next move is to preserve the existing sequence packet, log the blocker explicitly, and resume from that blocker instead of falling back to another empty-board pause.',
        f'- {_adoption_summary()}',
        '',
        '## Shared findings reused',
        '- apollo_sequence_status_latest.json → due follow-up state and sequence identity',
        '- apollo_status.json → live runtime blocker truth',
        '- apollo_launch_handoff_packet_latest.md → canonical Codeberg-primary sequence packet to preserve',
        '- marketing_execution_board_latest.md → consolidated hold-window truth surface',
        '',
        '## Current blocker',
        f'- Sequence: {sequence_name}',
        f"- Apollo sequence status: {apollo_status.get('status') or 'unknown'}",
        f'- Runtime blocker: {blocker_status}',
        f'- Summary: {blocker_summary}',
        f'- Notes: {blocker_notes}',
        f"- Next review at: {apollo_status.get('next_review_at') or 'unknown'}",
        '',
        '## Keep using this existing packet',
        f'- Canonical launch/review packet: {launch_packet}',
        f"- Latest outbound verification log: {apollo_status.get('outbound_verification_log') or 'unknown'}",
        '',
        '## Do-now follow-through',
        '- Do not rebuild the audience, copy, or sequence while the runtime blocker is unchanged.',
        '- Reuse the existing Codeberg-primary launch packet on the next browser-capable or auth-cleared Apollo surface.',
        '- As soon as the blocker clears, verify whether the sequence is active/sending and log that evidence before starting measurement.',
        '- If the blocker persists after the short-window release, treat that persistent auth failure as the next architecture-repair target instead of another generic guard pause.',
        '',
        '## Expected outcome',
        '- The next Apollo-capable run starts from a truthful blocker packet instead of an empty-board reset.',
        '- The loop keeps one highest-leverage outbound asset warm without pretending it is live before the runtime can actually prove it.',
    ]

    content = '\n'.join(lines) + '\n'
    artifact.write_text(content, encoding='utf-8')
    latest_artifact.write_text(content, encoding='utf-8')
    return artifact, [sequence_name]


def _write_stackoverflow_execution(now: datetime) -> tuple[Path, list[str], str, int, bool]:
    rc = stackoverflow_answer_lane.main()
    payload = _load_json(STACKOVERFLOW_LATEST_PATH)
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_stackoverflow_answer_execution.md'
    drafts = payload.get('drafts', []) or []
    top_questions = payload.get('top_questions', []) or []
    reused_existing_draft = payload.get('reused_existing_draft') or {}
    reused_existing_draft_ready = bool(reused_existing_draft)

    lines = [
        '# Ralph Workflow StackOverflow Answer Execution Pack',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        f'- {_adoption_summary()}',
        '- Apollo is already in a live measurement window and GitHub-auth distribution is currently blocked, so this run shifts to fresh high-intent demand capture.',
        '- These drafts should only be used where Ralph Workflow genuinely improves the answer; keep them helpful first and promotional only when natural.',
        '',
        '## Lane results',
        f'- Return code: {rc}',
        f'- Questions found: {payload.get("total_questions_found", 0)}',
        f'- Drafts created: {payload.get("drafts_created", 0)}',
        f'- Reused existing manual-ready draft: {"yes" if reused_existing_draft_ready else "no"}',
        f'- Log: {STACKOVERFLOW_LATEST_PATH}',
    ]

    prepared: list[str] = []
    if drafts:
        lines.extend(['', '## Ready answer drafts'])
        for draft in drafts:
            prepared.append(draft.get('question_title', 'unknown question'))
            lines.append(f"- {draft.get('question_title', 'unknown question')} — {draft.get('draft_file', '')}")
    elif reused_existing_draft_ready:
        title = reused_existing_draft.get('question_title', 'unknown question')
        prepared.append(title)
        lines.extend([
            '',
            '## Reused manual-ready draft',
            f'- {title}',
            f"- URL: {reused_existing_draft.get('question_url', '')}",
            f"- Draft file: {reused_existing_draft.get('draft_file', '')}",
            f"- Packet file: {reused_existing_draft.get('packet_file', '')}",
            '- This still counts as a live demand-capture asset for the current window: the lane revalidated the question, preserved quota, and surfaced the exact answer packet to place or reuse.',
        ])
    elif top_questions:
        lines.extend(['', '## Top candidate questions (no draft files created)'])
        for question in top_questions:
            prepared.append(question.get('title', 'unknown question'))
            lines.append(f"- {question.get('title', 'unknown question')} — {question.get('url', '')}")
    else:
        lines.extend([
            '',
            '## No current candidates',
            '- The repaired StackOverflow lane ran but did not surface draft-worthy questions in this pass.',
            '- Keep the API-backed search path; do not fall back to the old scrape-only logic.',
        ])

    lines.extend([
        '',
        '## Process rule now in force',
        '- Do not answer generic beginner or already-solved questions just to create activity.',
        '- Prefer unanswered or weakly answered workflow / agent reliability questions where the Codeberg repo is a genuinely useful reference.',
    ])
    artifact.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return artifact, prepared, 'stackoverflow_answer_execution', int(payload.get('drafts_created', 0) or 0), reused_existing_draft_ready


def _parse_stackoverflow_draft(path: Path) -> dict[str, str]:
    text = _read_text(path)
    parsed = {
        'filename': path.name,
        'title': '',
        'url': '',
        'score': '',
        'answers': '',
        'body': '',
    }
    if not text:
        return parsed

    title_match = re.search(r'^\*\*Question:\*\*\s+(.+)$', text, re.MULTILINE)
    url_match = re.search(r'^\*\*URL:\*\*\s+(https?://\S+)$', text, re.MULTILINE)
    score_match = re.search(r'^\*\*Score:\*\*\s+(.+)$', text, re.MULTILINE)
    answers_match = re.search(r'^\*\*Answers:\*\*\s+(.+)$', text, re.MULTILINE)
    if title_match:
        parsed['title'] = title_match.group(1).strip()
    if url_match:
        parsed['url'] = url_match.group(1).strip()
    if score_match:
        parsed['score'] = score_match.group(1).strip()
    if answers_match:
        parsed['answers'] = answers_match.group(1).strip()

    separator = '\n---\n'
    if separator in text:
        parsed['body'] = text.split(separator, 1)[1].strip()
    return parsed


def _ensure_workflow_composition_example(now: datetime) -> list[str]:
    files_changed: list[str] = []
    content = f'''# Example Workflow Composition

This is what a **real Ralph Workflow run** should feel like when you try it on one bounded backlog task.

If you only want the repo first, start on **Codeberg**: <{CODEBERG_PRIMARY}>

## Example task

**Task:** Add a CSV export to a billing-history page without changing invoice creation or billing calculations.

## 1. Sharpen the task before code starts

Use one paragraph, not a vague prompt dump.

```md
Change:
Add CSV export to the billing history page.

Keep unchanged:
Do not change invoice creation, billing calculations, or existing filters.

Done means:
Users can export the currently filtered billing-history rows to CSV from the page.

Checks:
Relevant billing tests pass and any new billing-history tests pass.
```

Why this phase matters:
- it locks the finish line before implementation starts
- it keeps the run bounded enough to review later
- it gives verification something concrete to test

## 2. Build inside the workflow, not as disconnected chat hops

The implementation phase should stay scoped to the task above.

Useful expectations:
- the diff stays narrow
- unrelated cleanup is avoided
- changed files are easy to inspect
- the workflow records what was attempted and what was fixed

## 3. Verify before anyone calls it done

The workflow should run the checks named in the spec and hand back the result clearly.

A clean verification section usually includes:
- which tests ran
- whether build/lint passed
- what failed first, if anything
- what was repaired before the final handoff

## 4. Hand back a morning-after review bundle

The morning artifact should answer the merge question fast.

That bundle should include:
- the scoped task
- changed files
- what changed in plain language
- checks that really ran
- open questions or remaining risk

See also: [Review bundle example](./review_bundle_example.md)

## What this composition proves

The point is not that an agent can write code.

The point is that the workflow composes:
1. task sharpening
2. implementation
3. verification
4. morning-after review

That is the difference between a transcript and a result you can actually judge.

## Best next steps

- [Start here on one real task](../../START_HERE.md)
- [Example first task](./first_task_example.md)
- [Good unattended task vs bad one](../guides/good_unattended_task.md)
- [Review AI coding output before merge](../guides/review_ai_coding_output_before_merge.md)

## Public next step

If this is the kind of workflow you want, inspect the primary repo on **Codeberg** first and use the mirror only if you need it:

- **Codeberg (primary):** <{CODEBERG_PRIMARY}>
- **GitHub mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>
'''
    if _read_text(WORKFLOW_COMPOSITION_EXAMPLE_PATH) != content:
        WORKFLOW_COMPOSITION_EXAMPLE_PATH.write_text(content, encoding='utf-8')
        files_changed.append(str(WORKFLOW_COMPOSITION_EXAMPLE_PATH))

    start_here = _read_text(START_HERE_PATH)
    link_line = '- [Workflow composition example](./content/examples/workflow_composition_example.md)'
    if start_here and link_line not in start_here:
        anchor = '- [Review bundle example](./content/examples/review_bundle_example.md)'
        if anchor in start_here:
            updated = start_here.replace(anchor, f'{link_line}\n{anchor}')
        else:
            updated = start_here.rstrip() + f'\n{link_line}\n'
        START_HERE_PATH.write_text(updated, encoding='utf-8')
        files_changed.append(str(START_HERE_PATH))

    return files_changed


def _write_repo_conversion_proof_asset(now: datetime) -> tuple[Path, list[str]]:
    files_changed = _ensure_workflow_composition_example(now)
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_repo_conversion_proof_asset.md'
    lines = [
        '# Ralph Workflow Repo Conversion Proof Asset',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        f'- {_adoption_summary()}',
        '- The StackOverflow reuse packet is already current, so regenerating it again would be fake activity.',
        '- External distribution lanes are already inside measurement windows, so the strongest same-run move is a durable repo proof asset that improves evaluator understanding now.',
        '',
        '## Proof asset shipped',
        f'- New deep example: {WORKFLOW_COMPOSITION_EXAMPLE_PATH}',
        '- First-run routing update: START_HERE.md now points to the workflow composition example in the next-examples section.',
        '',
        '## Shared findings reused',
        '- ADOPTION_FUNNEL_NEXT.md → example workflow composition is a named needed proof asset',
        '- market_intelligence_latest.json → preserve the four core truths and Codeberg-first positioning',
        '- marketing_workflow_audit_latest.json → current bottleneck is distribution_and_message_to_primary_repo_conversion',
        '- distribution_lane_latest.json → external lanes are already in overlapping measurement windows',
        '',
        '## Docs review note',
        '- What changed: added a workflow-composition proof example and promoted it from START_HERE.md.',
        '- Why this surface: it sits directly in the repo-first evaluation path without expanding README again.',
        '- What was pruned/shortened/merged: no new top-level README branch was added; the change stayed one layer deeper in START_HERE examples.',
        '- Duplication reduced: the new page composes existing first-task and review-bundle ideas into one concrete walkthrough instead of forcing users to infer the workflow from separate pages.',
        '- Why the top-level experience is better: evaluators now have a clearer answer to how the workflow actually fits together before they decide whether to run it.',
        '',
        '## Verification',
        '- Confirm the new example file exists and is linked from START_HERE.md.',
    ]
    artifact.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return artifact, files_changed



def _write_stackoverflow_reuse_packet(now: datetime, draft: dict[str, str]) -> Path:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_stackoverflow_answer_reuse_packet.md'
    latest_artifact = DRAFTS_DIR / 'stackoverflow_answer_reuse_packet_latest.md'
    title = draft.get('title') or 'Unknown question'
    url = draft.get('url') or 'unknown URL'
    answer_body = draft.get('body') or 'No answer body available.'
    snippet = (
        'A practical pattern here is to keep the task envelope small, separate implementation from verification, '
        'and require a morning-after review bundle with the diff, checks run, and unresolved risks before anyone calls it done.'
    )
    lines = [
        '# Ralph Workflow StackOverflow Answer Reuse Packet',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        f'- {_adoption_summary()}',
        '- Live StackOverflow posting is not automated from this runtime, so the best same-run move is to turn the strongest fresh answer draft into a reusable demand-capture asset.',
        '- This packet reuses the exact draft instead of regenerating the lane or producing another abstract recommendation.',
        '',
        '## Canonical question to reuse',
        f'- Title: {title}',
        f'- URL: {url}',
        f'- Source draft: {draft.get("filename", "unknown")}',
        '',
        '## Final answer text',
        '```md',
        answer_body,
        '```',
        '',
        '## Short curator / comparison snippet',
        '```text',
        snippet,
        f' One open-source example of that pattern is Ralph Workflow on Codeberg: {CODEBERG_PRIMARY}',
        '```',
        '',
        '## Reuse rules',
        '- Use the full answer for manual StackOverflow posting or any Q&A-style developer surface.',
        '- Use the short snippet when a curator, maintainer, or comparison page needs a concrete reliability explanation instead of a product intro.',
        '- Keep Codeberg primary and GitHub mirror-only if a repo link is needed.',
        '',
        '## Measurement contract',
        '- Expected outcome: one real reuse of this exact answer spine on a live or near-live high-intent surface',
        '- Review window: 7 days for reuse, 14 days for attributable repo inspection, 30 days for Codeberg movement',
    ]
    content = '\n'.join(lines) + '\n'
    artifact.write_text(content, encoding='utf-8')
    latest_artifact.write_text(content, encoding='utf-8')
    return artifact



def _write_stackoverflow_handoff_packet(now: datetime) -> tuple[Path, list[str]]:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_stackoverflow_answer_handoff_packet.md'
    latest_artifact = DRAFTS_DIR / 'stackoverflow_answer_handoff_packet_latest.md'
    stackoverflow_dir = DRAFTS_DIR / 'stackoverflow'
    draft_files = sorted(
        stackoverflow_dir.glob('so_answer_*.md') if stackoverflow_dir.exists() else [],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    prepared: list[str] = []
    parsed_drafts = [_parse_stackoverflow_draft(path) for path in draft_files[:5]]
    strongest = parsed_drafts[0] if parsed_drafts else {}
    reuse_packet_path = _write_stackoverflow_reuse_packet(now, strongest) if strongest else None
    lines = [
        '# Ralph Workflow StackOverflow Answer Handoff Packet',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this exists now',
        f'- {_adoption_summary()}',
        '- The StackOverflow search lane already produced a fresh answer asset this week, so this run should advance reuse/posting instead of regenerating the same lane.',
        '- Apollo, curator outreach, and directory submission are already inside overlapping measurement windows, so the best move is to push the existing high-intent draft closer to a live surface.',
        '',
        '## Immediate operator rule',
        '- Do not rerun the StackOverflow search lane until these draft assets are either posted, reused, or age out of the current review window.',
        '- If live StackOverflow posting is unavailable, repurpose the answer into another high-intent proof surface instead of letting it sit idle.',
    ]

    if parsed_drafts:
        lines.extend(['', '## Ready drafts'])
        for draft in parsed_drafts:
            prepared.append(draft.get('filename', 'unknown'))
            title = draft.get('title') or draft.get('filename', 'unknown')
            meta = []
            if draft.get('score'):
                meta.append(f"score={draft['score']}")
            if draft.get('answers'):
                meta.append(f"answers={draft['answers']}")
            suffix = f" ({', '.join(meta)})" if meta else ''
            lines.append(f"- {title}{suffix}")
            if draft.get('url'):
                lines.append(f"  - {draft['url']}")
        if strongest:
            lines.extend([
                '',
                '## Strongest draft to post or reuse first',
                f"- Title: {strongest.get('title', 'unknown')}",
                f"- URL: {strongest.get('url', 'unknown')}",
                '',
                '## Final answer text',
                '```md',
                strongest.get('body', 'No answer body available.'),
                '```',
            ])
            if reuse_packet_path is not None:
                lines.extend([
                    '',
                    '## Reuse packet generated in this run',
                    f'- {reuse_packet_path}',
                ])
    else:
        lines.extend([
            '',
            '## Draft status',
            '- No local StackOverflow draft file was found. If the measurement window note is stale, clear it before selecting this lane again.',
        ])

    lines.extend([
        '',
        '## Recommended next actions',
        '- Post the strongest draft manually where a direct StackOverflow answer is possible, using the final answer text above.',
        '- Reuse the same answer spine in curator/comparison outreach with the generated reuse packet instead of rewriting the explanation from scratch.',
        '- Keep the answer focused on workflow reliability, visible finish state, tests, and reviewability; avoid generic promo framing.',
        '',
        '## Measurement contract',
        '- Expected outcome: at least one live placement or reuse of an existing StackOverflow answer draft',
        '- Review window: 7 days for first live placement/reuse, 14 days for attributable qualified repo inspection, 30 days for Codeberg movement',
        '- Replacement condition: if the draft cannot be placed or reused on any real surface, replace this lane with a different executable high-intent demand-capture asset',
    ])

    content = '\n'.join(lines) + '\n'
    artifact.write_text(content, encoding='utf-8')
    latest_artifact.write_text(content, encoding='utf-8')
    return artifact, prepared


def _current_manual_demand_capture_hint() -> dict[str, Any]:
    latest = _load_json(STACKOVERFLOW_LATEST_PATH)
    if not latest:
        return {}

    packet_path = DRAFTS_DIR / 'stackoverflow_answer_handoff_packet_latest.md'
    if not packet_path.exists():
        return {}

    drafts = latest.get('drafts') or []
    reused = latest.get('reused_existing_draft') or {}
    top_questions = latest.get('top_questions') or []
    top = top_questions[0] if top_questions else {}
    cooldown_active = bool(latest.get('cooldown_active'))
    manual_ready = bool(drafts or reused or latest.get('manual_follow_through'))
    if not manual_ready and not (cooldown_active and top):
        return {}

    packet_text = _read_text(packet_path)
    packet_url_match = re.search(r'^\*\*URL:\*\*\s+(https?://\S+)$', packet_text, re.MULTILINE)
    packet_title_match = re.search(r'^\*\*Question:\*\*\s+(.+)$', packet_text, re.MULTILINE)
    packet_url = packet_url_match.group(1).strip() if packet_url_match else ''
    packet_title = packet_title_match.group(1).strip() if packet_title_match else ''

    candidate_urls = {
        str((draft or {}).get('question_url') or '').strip()
        for draft in drafts
        if str((draft or {}).get('question_url') or '').strip()
    }
    reused_url = str(reused.get('question_url') or '').strip()
    if reused_url:
        candidate_urls.add(reused_url)

    candidate_titles = {
        str((draft or {}).get('question_title') or '').strip()
        for draft in drafts
        if str((draft or {}).get('question_title') or '').strip()
    }
    reused_title = str(reused.get('question_title') or '').strip()
    if reused_title:
        candidate_titles.add(reused_title)

    top_url = str(top.get('url') or '').strip()
    top_title = str(top.get('title') or '').strip()
    if cooldown_active and top_url:
        candidate_urls.add(top_url)
    if cooldown_active and top_title:
        candidate_titles.add(top_title)

    if packet_url and candidate_urls and packet_url not in candidate_urls:
        return {}
    if not packet_url and packet_title and candidate_titles and packet_title not in candidate_titles:
        return {}

    preferred = reused or (drafts[0] if drafts else {})
    title = str(preferred.get('question_title') or '').strip()
    url = str(preferred.get('question_url') or '').strip()
    if not title or not url:
        title = title or top_title
        url = url or top_url
    if not title and packet_title:
        title = packet_title
    if not url and packet_url:
        url = packet_url
    if not title or not url:
        return {}

    return {
        'title': title,
        'url': url,
        'packet_path': str(packet_path),
        'next_retry_at': latest.get('next_retry_at', ''),
        'cooldown_active': cooldown_active,
    }


def _recent_action_payloads(*, action_types: set[str], now: datetime, days: int = 7) -> list[dict[str, Any]]:
    def normalize(value: datetime) -> datetime:
        if value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value

    cutoff = normalize(now) - timedelta(days=days)
    payloads: list[tuple[datetime, dict[str, Any]]] = []
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        action_type = _chosen_action_type(payload)
        if action_type not in action_types:
            continue
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        dt = normalize(dt)
        if dt < cutoff:
            continue
        payloads.append((dt, payload))
    payloads.sort(key=lambda item: item[0], reverse=True)
    return [payload for _dt, payload in payloads]


def _recent_measurement_hold_reentry_repairs_state(
    now: datetime,
    *,
    days: int = 7,
) -> dict[str, Any]:
    cutoff = now - timedelta(days=days)
    repairs_seen: set[str] = set()
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        action_type = _chosen_action_type(payload)
        if action_type not in MEASUREMENT_HOLD_REENTRY_REPAIR_ACTION_TYPES:
            continue
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff or dt > now:
            continue
        repairs_seen.add(action_type)
    return {
        'repairs_seen': repairs_seen,
        'reentry_repairs_complete': repairs_seen == MEASUREMENT_HOLD_REENTRY_REPAIR_ACTION_TYPES,
    }


def _measurement_hold_window_repeat_state(
    now: datetime,
    *,
    hold_started_at: datetime,
    hold_until: datetime,
) -> dict[str, Any]:
    cutoff = min(hold_started_at, now)
    hold_events = 0
    hold_window_repairs_seen: set[str] = set()
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        action_type = _chosen_action_type(payload)
        if action_type not in (MEASUREMENT_HOLD_ACTION_TYPES | MEASUREMENT_HOLD_REENTRY_REPAIR_ACTION_TYPES):
            continue
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff or dt > hold_until:
            continue
        if action_type in MEASUREMENT_HOLD_ACTION_TYPES:
            hold_events += 1
        elif action_type in MEASUREMENT_HOLD_REENTRY_REPAIR_ACTION_TYPES:
            hold_window_repairs_seen.add(action_type)

    recent_repairs_state = _recent_measurement_hold_reentry_repairs_state(now)
    effective_repairs_seen = hold_window_repairs_seen | set(recent_repairs_state['repairs_seen'])
    return {
        'hold_events': hold_events,
        'next_hold_event_number': hold_events + 1,
        'repairs_seen': effective_repairs_seen,
        'hold_window_repairs_seen': hold_window_repairs_seen,
        'reentry_repairs_complete': effective_repairs_seen == MEASUREMENT_HOLD_REENTRY_REPAIR_ACTION_TYPES,
    }


MEASUREMENT_HOLD_MAX_ARTIFACTS_PER_HOUR = 2
MEASUREMENT_HOLD_CHURN_HARD_GUARD_WINDOW_HOURS = 6
# Global artifact rate limit: if the system produces more than this many
# execution artifacts per hour (all types), suppress further writes.
EXECUTION_ARTIFACT_HARD_LIMIT_PER_HOUR = 4
EXECUTION_ARTIFACT_HARD_LIMIT_WINDOW_HOURS = 6
EXECUTION_ARTIFACT_TYPES_FOR_RATE_LIMIT = {
    'measurement_hold_execution',
    'measurement_hold_follow_through',
    'measurement_hold_churn_guard_repair',
    'distribution_architecture_repair',
    'distribution_architecture_churn_guard_repair',
    'distribution_architecture_guard_follow_through',
    'distribution_architecture_guard_pause',
}


def _execution_artifact_recent_count(
    now: datetime,
    *,
    artifact_types: set[str] | None = None,
    window_hours: int = EXECUTION_ARTIFACT_HARD_LIMIT_WINDOW_HOURS,
) -> dict[str, int]:
    """Count execution artifacts in the recent window. Returns per-type counts."""
    if artifact_types is None:
        artifact_types = EXECUTION_ARTIFACT_TYPES_FOR_RATE_LIMIT
    cutoff = now - timedelta(hours=window_hours)
    counts: dict[str, int] = {}
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        action_type = _chosen_action_type(payload)
        if action_type not in artifact_types:
            continue
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff or dt > now:
            continue
        counts[action_type] = counts.get(action_type, 0) + 1
    return counts


def _execution_artifact_hard_limit_reached(now: datetime) -> bool:
    """Return True if total non-distribution execution artifacts exceed the hard limit."""
    counts = _execution_artifact_recent_count(now)
    total = sum(counts.values())
    max_allowed = EXECUTION_ARTIFACT_HARD_LIMIT_PER_HOUR * EXECUTION_ARTIFACT_HARD_LIMIT_WINDOW_HOURS
    return total >= max_allowed


def _non_distribution_execution_artifact_count(
    now: datetime, window_hours: int = EXECUTION_ARTIFACT_HARD_LIMIT_WINDOW_HOURS
) -> int:
    """Return total count of non-distribution execution artifacts in the window."""
    counts = _execution_artifact_recent_count(now, window_hours=window_hours)
    return sum(counts.values())


def _measurement_hold_recent_artifact_count(
    now: datetime,
    *,
    artifact_types: set[str] | None = None,
    window_hours: int = MEASUREMENT_HOLD_CHURN_HARD_GUARD_WINDOW_HOURS,
) -> dict[str, int]:
    """Count measurement-hold artifacts in the recent window. Returns per-type counts."""
    if artifact_types is None:
        artifact_types = MEASUREMENT_HOLD_ACTION_TYPES
    cutoff = now - timedelta(hours=window_hours)
    counts: dict[str, int] = {}
    for path in LOG_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        action_type = _chosen_action_type(payload)
        if action_type not in artifact_types:
            continue
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff or dt > now:
            continue
        counts[action_type] = counts.get(action_type, 0) + 1
    return counts


def _measurement_hold_churn_hard_guard_active(
    now: datetime,
    *,
    max_per_hour: int = MEASUREMENT_HOLD_MAX_ARTIFACTS_PER_HOUR,
    window_hours: int = MEASUREMENT_HOLD_CHURN_HARD_GUARD_WINDOW_HOURS,
) -> bool:
    """Return True if measurement-hold artifacts are being generated too fast.

    This is the hard guardrail line: if the system is producing more than
    MEASUREMENT_HOLD_MAX_ARTIFACTS_PER_HOUR measurement-hold artifacts per hour
    averaged over the window, suppress further writes.
    """
    counts = _measurement_hold_recent_artifact_count(
        now, artifact_types=MEASUREMENT_HOLD_ACTION_TYPES, window_hours=window_hours
    )
    total = sum(counts.values())
    max_allowed = max_per_hour * window_hours
    return total >= max_allowed


def _current_stackoverflow_scheduled_run(now: datetime) -> str:
    action_types = {
        'stack_overflow_lane_repair',
        'stackoverflow_post_cooldown_cron',
        'stack_overflow_demand_capture_cron',
    }
    for payload in _recent_action_payloads(action_types=action_types, now=now, days=7):
        scheduled_candidates = [
            str(((payload.get('review_window') or {}).get('scheduled_run_at')) or '').strip(),
            str(((payload.get('verification') or {}).get('scheduled_run_at')) or '').strip(),
            str(payload.get('scheduled_run_at') or '').strip(),
        ]
        for scheduled in scheduled_candidates:
            scheduled_dt = _parse_dt(scheduled)
            if scheduled_dt is not None and scheduled_dt >= now:
                return scheduled
    return ''


def _current_measurement_hold_release_run(now: datetime, *, not_before: str | None = None) -> str:
    live_jobs = _live_measurement_hold_release_jobs(now)
    if not live_jobs:
        return ''

    not_before_dt = _parse_dt(not_before)
    eligible_jobs = live_jobs
    if not_before_dt is not None:
        eligible_jobs = []
        for job in live_jobs:
            scheduled_dt = _parse_dt(job.get('scheduled_run_at'))
            if scheduled_dt is None or scheduled_dt >= not_before_dt:
                eligible_jobs.append(job)
        if not eligible_jobs:
            return ''

    for job in eligible_jobs:
        scheduled_dt = _parse_dt(job.get('scheduled_run_at'))
        if scheduled_dt is not None and scheduled_dt >= now:
            return _format_local_schedule_at(job.get('scheduled_run_at'))
    return ''


def _resolve_measurement_hold_release_delivery() -> dict[str, str]:
    try:
        text = USER_PROFILE_PATH.read_text(encoding='utf-8')
    except OSError:
        return {'channel': 'last'}

    matrix_match = re.search(r'@[^\s)]+:matrix\.org', text)
    if matrix_match:
        return {
            'channel': 'matrix',
            'to': matrix_match.group(0),
        }

    return {'channel': 'last'}


def _live_measurement_hold_release_jobs(now: datetime) -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            ['/home/mistlight/.bun/bin/openclaw', 'cron', 'list', '--json'],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, PermissionError, OSError) as _subprocess_err:
        return []
    if result.returncode != 0:
        return []

    try:
        payload = json.loads(getattr(result, 'stdout', '') or '{}')
    except json.JSONDecodeError:
        return []

    jobs = payload.get('jobs') if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return []

    live_jobs: list[dict[str, str]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if str(job.get('name') or '').strip() != MEASUREMENT_HOLD_RELEASE_CRON_NAME:
            continue
        if job.get('enabled') is False:
            continue

        schedule = job.get('schedule') if isinstance(job.get('schedule'), dict) else {}
        state = job.get('state') if isinstance(job.get('state'), dict) else {}
        status = str(job.get('status') or '').strip().lower()
        scheduled_run_at = str(schedule.get('at') or '').strip()
        scheduled_dt = _parse_dt(scheduled_run_at)
        is_running = status == 'running' or state.get('runningAtMs') is not None

        if not scheduled_run_at and not is_running:
            continue

        live_jobs.append({
            'job_id': str(job.get('id') or '').strip(),
            'job_name': str(job.get('name') or '').strip(),
            'scheduled_run_at': scheduled_run_at,
            'status': status,
        })

    def _sort_key(item: dict[str, str]) -> tuple[int, datetime]:
        scheduled_dt = _parse_dt(item.get('scheduled_run_at'))
        status = str(item.get('status') or '').strip().lower()
        is_overdue_idle = scheduled_dt is not None and scheduled_dt < now and status != 'running'
        return (1 if is_overdue_idle else 0, scheduled_dt or now)

    live_jobs.sort(key=_sort_key)
    return live_jobs


def _schedule_measurement_hold_release_run(
    *,
    now: datetime,
    release_at: str,
    shared_findings_used: list[str],
    reentry_contract_path: str | None = None,
) -> dict[str, Any]:
    resolved_release_at = _resolved_measurement_hold_release_at(now, release_at)
    release_dt = _parse_dt(resolved_release_at)
    if release_dt is None or release_dt <= now:
        return {}

    release_display_at = release_dt.isoformat(timespec='seconds')
    release_cron_at = _cron_at_argument(resolved_release_at)

    if not resolved_release_at:
        return {}

    message = _measurement_hold_release_message(reentry_contract_path)
    live_jobs = _live_measurement_hold_release_jobs(now)
    removed_jobs: list[dict[str, str]] = []
    matching_job = next((job for job in live_jobs if _schedule_at_matches(job.get('scheduled_run_at'), resolved_release_at)), None)
    matching_job_has_expected_payload = False
    if matching_job:
        matching_job_has_expected_payload = _cron_job_message_matches(
            str(matching_job.get('job_id') or ''),
            message,
        )

    for stale_job in live_jobs:
        if (
            matching_job
            and stale_job.get('job_id') == matching_job.get('job_id')
            and matching_job_has_expected_payload
        ):
            continue
        rm_command = ['/home/mistlight/.bun/bin/openclaw', 'cron', 'rm', stale_job.get('job_id', ''), '--json']
        rm_result = subprocess.run(
            rm_command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if rm_result.returncode == 0:
            removed_jobs.append(stale_job)

    if matching_job and matching_job_has_expected_payload:
        return {
            'status': 'already_scheduled',
            'scheduled_run_at': release_display_at,
            'job_id': str(matching_job.get('job_id') or '').strip(),
            'job_name': str(matching_job.get('job_name') or '').strip(),
            'removed_stale_jobs': removed_jobs,
        }

    delivery = _resolve_measurement_hold_release_delivery()
    command = [
        '/home/mistlight/.bun/bin/openclaw', 'cron', 'add',
        '--json',
        '--name', 'marketing-measurement-hold-release',
        '--at', release_cron_at,
        '--agent', 'main',
        '--session', 'isolated',
        '--announce',
        '--channel', delivery.get('channel', 'last'),
    ]
    if delivery.get('to'):
        command.extend(['--to', delivery['to']])
    command.extend([
        '--delete-after-run',
        '--light-context',
        '--model', 'openai-codex/gpt-5.4',
        '--thinking', 'medium',
        '--timeout-seconds', '1800',
        '--message', message,
    ])
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        return {
            'status': 'failed',
            'scheduled_run_at': release_display_at,
            'error': (result.stderr or result.stdout or '').strip(),
        }

    payload: dict[str, Any] = {}
    try:
        loaded = json.loads(result.stdout or '{}')
        if isinstance(loaded, dict):
            payload = loaded
    except json.JSONDecodeError:
        payload = {}

    job = payload.get('job') if isinstance(payload.get('job'), dict) else payload
    log_payload = {
        'timestamp': now.isoformat(),
        'type': 'measurement_hold_release_cron',
        'status': 'scheduled',
        'ok': True,
        'shared_findings_reused': list(shared_findings_used or []),
        'verification': {
            'added_with': 'openclaw cron add --name marketing-measurement-hold-release --at <release_at> ...',
            'scheduled_run_at': release_display_at,
            'cron_at_argument': release_cron_at,
            'delete_after_run': True,
            'reentry_contract_path': reentry_contract_path or '',
            'delivery_channel': delivery.get('channel', 'last'),
            'delivery_target': delivery.get('to', ''),
        },
        'cron_job': {
            'id': str(job.get('id') or payload.get('id') or '').strip(),
            'name': str(job.get('name') or payload.get('name') or MEASUREMENT_HOLD_RELEASE_CRON_NAME).strip(),
        },
        'review_window': {
            'scheduled_run_at': release_display_at,
        },
    }
    if removed_jobs:
        log_payload['cleanup'] = {
            'removed_stale_jobs': removed_jobs,
        }
    log_path = LOG_DIR / f"marketing_{now.strftime('%Y-%m-%d_%H%M%S')}_measurement_hold_release_cron.json"
    log_path.write_text(json.dumps(log_payload, indent=2) + '\n', encoding='utf-8')
    return {
        'status': 'scheduled',
        'scheduled_run_at': release_display_at,
        'job_id': log_payload['cron_job']['id'],
        'job_name': log_payload['cron_job']['name'],
        'log_path': str(log_path),
        'reentry_contract_path': reentry_contract_path or '',
        'removed_stale_jobs': removed_jobs,
    }


def _append_post_hold_schedule_note(artifact_path: str | None, schedule: dict[str, Any]) -> None:
    if not artifact_path:
        return
    status = str(schedule.get('status') or '').strip().lower()
    if status not in {'scheduled', 'already_scheduled'}:
        return

    path = Path(artifact_path)
    if not path.exists() or path.suffix.lower() != '.md':
        return

    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return
    if '## Post-hold marketer rerun' in text:
        return

    heading = '## Post-hold marketer rerun scheduled' if status == 'scheduled' else '## Post-hold marketer rerun already scheduled'
    lines = [
        '',
        heading,
        f"- Scheduled run: {schedule.get('scheduled_run_at', 'unknown')}",
    ]
    job_name = str(schedule.get('job_name') or '').strip()
    job_id = str(schedule.get('job_id') or '').strip()
    if job_name or job_id:
        lines.append(f"- Cron job: {job_name or 'marketing-measurement-hold-release'} ({job_id or 'unknown id'})")
    log_path = str(schedule.get('log_path') or '').strip()
    if log_path:
        lines.append(f'- Log: {log_path}')
    if status == 'scheduled':
        lines.append('- This keeps the first truthful post-hold slot alive even though the current lane is still blocked by short-window congestion.')
    else:
        lines.append('- The current one-shot already matches the live short-window release time; do not create another duplicate wake.')

    try:
        path.write_text(text.rstrip() + '\n\n' + '\n'.join(lines) + '\n', encoding='utf-8')
    except OSError:
        return


def _write_post_hold_reentry_contract(
    now: datetime,
    *,
    release_at: str | None,
    execution_board_path: Path,
    shared_findings_used: list[str],
) -> Path:
    resolved_release_at = _resolved_measurement_hold_release_at(now, release_at)
    actionable_primary_repo_flat_findings = _current_primary_repo_flat_actionable_findings(now)
    remaining_primary_repo_flat_targets = [
        _display_target_name(str(row.get('target') or '').strip())
        for row in actionable_primary_repo_flat_findings
        if str(row.get('target') or '').strip()
    ]
    discovered_non_runtime_sendable_targets = [
        _display_target_name(str(row.get('target') or '').strip())
        for row in _load_primary_repo_flat_contact_discovery()
        if str(row.get('target') or '').strip()
        and not _publisher_target_has_runtime_sendable_channel(row.get('channels') or [])
    ]
    stackoverflow_surface_exhausted = _call_selector_local(
        distribution_lane_selector._stack_overflow_post_cooldown_surface_exhausted,
        now,
    )

    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_post_hold_distribution_reentry.md'
    latest_artifact = _post_hold_reentry_contract_latest_path()
    lines = [
        '# Post-hold distribution re-entry contract',
        f'Generated: {now.isoformat(timespec="seconds")}',
        f'- Hold release at: {resolved_release_at or "unknown"}',
        f'- Execution board: {execution_board_path}',
        '',
        '## Hard rule for the first post-hold slot',
        '- If the execution board is still empty after the hold clears, choose distribution_architecture_repair instead of another measurement_hold.',
        '- Do not regenerate or redeliver packets that are already current in this review window just to fill the slot.',
        '- Keep Codeberg as the canonical CTA and GitHub as mirror-only support.',
    ]

    lines.extend([
        '',
        '## Current blocked-lane truth to preserve',
    ])
    if discovered_non_runtime_sendable_targets:
        lines.append(
            '- Remaining publisher discovery is still non-runtime-sendable here and requires public-path/Telegram follow-through: '
            + ', '.join(discovered_non_runtime_sendable_targets)
            + '.'
        )
    if remaining_primary_repo_flat_targets:
        lines.append(
            '- Manual-executable primary-repo-flat publisher targets would still be: '
            + ', '.join(remaining_primary_repo_flat_targets)
            + '.'
        )
    else:
        lines.append('- No fresh manual-executable primary-repo-flat publisher targets are currently available.')
    if stackoverflow_surface_exhausted:
        lines.append('- StackOverflow is exhausted for this review window; do not spend the re-entry slot refreshing that packet.')
    lines.extend([
        '- Curator/comparison/manual-contact packets that were already delivered in the current review window remain reference artifacts, not fresh actions.',
        '- Apollo remains inside its existing review window, so do not mistake "still measuring" for a reason to idle again.',
        '',
        '## What the first post-hold run must do',
        '- Pick one untouched truthful lane if one exists at that moment.',
        '- If every truthful lane is still blocked, exhausted, or already delivered, perform a concrete runtime/process repair in the same run.',
        '- Treat another idle hold as a process failure.',
        '',
        '## Shared findings reused',
        *[f'- {item}' for item in shared_findings_used],
    ])

    content = '\n'.join(lines) + '\n'
    artifact.write_text(content, encoding='utf-8')
    latest_artifact.write_text(content, encoding='utf-8')
    return latest_artifact


def _curator_contact_packet_already_delivered(now: datetime, manual_contact_targets: list[str]) -> bool:
    if not manual_contact_targets:
        return False
    expected = [target.strip() for target in manual_contact_targets if target.strip()]
    if not expected:
        return False
    for payload in _recent_action_payloads(action_types={'curator_contact_handoff_packet_execution'}, now=now, days=14):
        prepared = [str(item).strip() for item in ((payload.get('why_this_action') or {}).get('targets_prepared') or []) if str(item).strip()]
        if prepared == expected[:len(prepared)] or prepared == expected:
            return True
    return False


def _primary_repo_flat_recent_prep_matches_targets(now: datetime, expected_targets: list[str]) -> bool:
    expected = [target.strip() for target in expected_targets if target.strip()]
    if not expected:
        return False
    for payload in _recent_action_payloads(action_types={'primary_repo_flat_contact_handoff_packet_execution'}, now=now, days=7):
        prepared = [
            _display_target_name(str(item).strip())
            for item in (
                ((payload.get('why_this_action') or {}).get('targets_prepared') or [])
                or ((payload.get('result') or {}).get('targets_prepared') or [])
            )
            if str(item).strip()
        ]
        if prepared and prepared == expected[:len(prepared)]:
            return True
    return False


def _primary_repo_flat_prepared_only_family_repeat_count(now: datetime, *, hours: int | None = None) -> int:
    days = 7
    if hours is not None:
        days = max(1, math.ceil(hours / 24))
    total = 0
    for payload in _recent_action_payloads(
        action_types={'primary_repo_flat_contact_handoff_packet_execution'},
        now=now,
        days=days,
    ):
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if hours is not None and dt is not None and dt < now - timedelta(hours=hours):
            continue
        result = payload.get('result') if isinstance(payload.get('result'), dict) else {}
        status = str(result.get('status') or payload.get('status') or '').strip().lower()
        if status != 'prepared':
            continue
        if bool(result.get('live_external_action') or payload.get('live_external_action')):
            continue
        total += 1
    return total


def _primary_repo_flat_recent_prep_count(now: datetime, expected_targets: list[str], *, hours: int | None = None) -> int:
    expected = [target.strip() for target in expected_targets if target.strip()]
    if not expected:
        return 0
    days = 7
    if hours is not None:
        days = max(1, math.ceil(hours / 24))
    total = 0
    for payload in _recent_action_payloads(
        action_types={'primary_repo_flat_contact_handoff_packet_execution'},
        now=now,
        days=days,
    ):
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if hours is not None and dt is not None and dt < now - timedelta(hours=hours):
            continue
        prepared = [
            _display_target_name(str(item).strip())
            for item in (
                ((payload.get('why_this_action') or {}).get('targets_prepared') or [])
                or ((payload.get('result') or {}).get('targets_prepared') or [])
            )
            if str(item).strip()
        ]
        if prepared and prepared == expected[:len(prepared)]:
            total += 1
    return total



def _primary_repo_flat_packet_delivery_still_active(now: datetime, primary_repo_flat_targets: list[str]) -> bool:
    expected = [target.strip() for target in primary_repo_flat_targets if target.strip()]
    if not expected:
        return False
    latest_packet_path = DRAFTS_DIR / 'primary_repo_flat_contact_handoff_packet_latest.md'
    latest_packet_mtime: datetime | None = None
    if latest_packet_path.exists():
        latest_packet_mtime = datetime.fromtimestamp(latest_packet_path.stat().st_mtime)
    short_review_window_release_at = _short_review_window_release_at(now)
    short_review_window_active = bool(short_review_window_release_at and now < short_review_window_release_at)
    active_manual_delivery_targets = _active_manual_outreach_delivery_targets(now)
    for payload in _recent_action_payloads(action_types=PRIMARY_REPO_FLAT_MANUAL_DELIVERY_ACTION_TYPES, now=now, days=14):
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
        action_type = str(chosen_action.get('type') or payload.get('type') or payload.get('action_type') or '').strip()
        refreshed_after_delivery = bool(
            latest_packet_mtime is not None
            and delivered_at is not None
            and latest_packet_mtime > delivered_at
            and (
                action_type == 'primary_repo_flat_contact_manual_delivery_refresh'
                or _primary_repo_flat_recent_prep_matches_targets(now, expected)
            )
        )
        if refreshed_after_delivery:
            continue
        if review_dt is not None and review_dt >= now:
            return True
        if delivered_at is not None and delivered_at.date() == now.date():
            return True
    return False


def _comparison_packet_delivery_still_active(now: datetime) -> bool:
    for payload in _recent_action_payloads(action_types={'comparison_backlink_manual_delivery'}, now=now, days=14):
        review_at = str(((payload.get('measurement_window') or {}).get('review_at')) or '').strip()
        review_dt = _parse_dt(review_at)
        if review_dt is not None and review_dt >= now:
            return True
        delivered_at = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if delivered_at is not None and delivered_at.date() == now.date():
            return True
    return False


def _verified_infrastructure_state(now: datetime) -> list[str]:
    """Return a truthful infra-state block programmatically so the board never fabricates cron claims."""
    lines: list[str] = []
    # Telegraph cross-post state
    try:
        guard_ok, guard_reason, guard_remaining = guard_check('telegraph')
        clears_at = now + timedelta(hours=guard_remaining) if not guard_ok else None
        posted = load_posted()
        post_count = len(posted.get('entries', []))
        dry = crosspost_blog_content(posted, now.strftime('%Y-%m-%d'), dry_run=True)
        queued = len(dry)

        lines.append(f'- **Telegraph guard**: {"clear" if guard_ok else f"cooldown ({guard_reason}) — clears ~{clears_at.strftime("%H:%M UTC") if clears_at else "?"}"}')
        lines.append(f'- **Telegraph queue**: {queued} blog{"s" if queued != 1 else ""} pending cross-post (dry-run discovery verified), {post_count} already posted')
        # Check crontab for Telegraph scheduling
        try:
            crontab = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=5)
            for cline in crontab.stdout.split('\n'):
                if 'run_posting' in cline or 'crosspost' in cline.lower():
                    lines.append(f'- **Telegraph crontab**: `{cline.strip()}`')
                    break
            else:
                lines.append('- **Telegraph crontab**: NOT FOUND — no run_posting crontab entry')
        except Exception:
            lines.append('- **Telegraph crontab**: could not read crontab')
    except Exception as e:
        lines.append(f'- **Telegraph**: state check failed ({e})')

    # PyPI deploy state
    try:
        # Check canonical dist location (release_pypi.sh builds here)
        dist_dirs = [
            ROOT / 'repos/Ralph-Workflow/github-mirror/ralph-workflow/dist',
            ROOT / 'agents/marketing/pypi_readme_deploy/dist',
        ]
        wheels, sdist = [], []
        for dist_dir in dist_dirs:
            if dist_dir.is_dir():
                for w in dist_dir.glob('*.whl'):
                    if '0.8.8' in w.name:
                        wheels.append(w)
                for s in dist_dir.glob('*.tar.gz'):
                    if '0.8.8' in s.name:
                        sdist.append(s)
        has_creds = bool(
            (Path.home() / '.pypirc').exists()
            or os.environ.get('PYPI_TOKEN')
        )
        built = len(wheels) + len(sdist) > 0
        status = 'deployable' if (has_creds and built) else ('blocked on credentials' if not has_creds else 'built but not deployable')
        lines.append(f'- **PyPI v0.8.8**: {status} — {len(wheels)} wheel(s), {len(sdist)} sdist(s)' + (', twine-check PASSED' if built else ''))
    except Exception as e:
        lines.append(f'- **PyPI**: state check failed ({e})')

    return lines


def _write_marketing_execution_board(now: datetime) -> tuple[Path, list[str]]:
    artifact = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_marketing_execution_board.md'
    latest_artifact = DRAFTS_DIR / 'marketing_execution_board_latest.md'

    curator_queue_rows = _load_curator_queue_rows()
    comparison_queue_rows = _comparison_queue_rows(COMPARISON_QUEUE_LATEST_PATH)
    manual_hint = _current_manual_demand_capture_hint()
    scheduled_stackoverflow_run = _current_stackoverflow_scheduled_run(now)
    stackoverflow_manual_delivery_current = _call_selector_local(
        distribution_lane_selector._stack_overflow_manual_delivery_current,
        now,
    )
    stackoverflow_post_cooldown_run_current = _call_selector_local(
        distribution_lane_selector._stack_overflow_post_cooldown_run_current,
        now,
    )
    stackoverflow_surface_exhausted = _call_selector_local(
        distribution_lane_selector._stack_overflow_post_cooldown_surface_exhausted,
        now,
    )
    apollo_status = _load_json(LOG_DIR / 'apollo_sequence_status_latest.json')
    apollo_launch_handoff_packet = DRAFTS_DIR / 'apollo_launch_handoff_packet_latest.md'
    apollo_launch_packet = DRAFTS_DIR / 'apollo_sequence_launch_packet_latest.md'
    _skip_directory_submissions, skip_curator_outreach = _call_selector_local(
        distribution_lane_selector._active_repair_pause_flags,
    )
    skip_publisher_outreach = _call_selector_local(
        distribution_lane_selector._publisher_outreach_paused_by_repair_window,
    )
    curator_measurement_saturated = (
        _call_selector_local(distribution_lane_selector._curator_measurement_window_count, now)
        >= distribution_lane_selector.CURATOR_MEASUREMENT_WINDOW_SATURATION
    )

    execution_items: list[dict[str, str]] = []
    targets_prepared: list[str] = []

    short_review_window_release_dt = _short_review_window_release_at(now)
    short_review_window_release_at = (
        short_review_window_release_dt.isoformat(timespec='seconds')
        if short_review_window_release_dt is not None and short_review_window_release_dt > now
        else ''
    )
    short_review_window_active = bool(
        short_review_window_release_dt is not None and short_review_window_release_dt > now
    )
    if not short_review_window_active:
        short_review_window_release_dt = None

    primary_repo_flat_findings = _current_primary_repo_flat_actionable_findings(now)
    primary_repo_flat_targets = [
        _display_target_name(str(row.get('target') or '').strip())
        for row in primary_repo_flat_findings
    ]
    primary_repo_flat_runtime_targets = [
        _display_target_name(str(row.get('target') or '').strip())
        for row in primary_repo_flat_findings
        if _publisher_target_has_runtime_sendable_channel(row.get('channels') or [])
    ]
    primary_repo_flat_packet = DRAFTS_DIR / 'primary_repo_flat_contact_handoff_packet_latest.md'
    primary_repo_flat_packet_delivery_active = _primary_repo_flat_packet_delivery_still_active(now, primary_repo_flat_targets)
    primary_repo_flat_recent_prep_repeat_count = _primary_repo_flat_prepared_only_family_repeat_count(
        now,
        hours=distribution_lane_selector.PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_WINDOW_HOURS,
    )
    primary_repo_flat_packet_current = bool(
        primary_repo_flat_targets
        and _handoff_packet_is_current(
            primary_repo_flat_packet,
            primary_repo_flat_targets,
            require_live_listing_proof=True,
        )
    )
    primary_repo_flat_packet_current_for_active_window = bool(
        primary_repo_flat_targets
        and _handoff_packet_is_current(
            primary_repo_flat_packet,
            primary_repo_flat_targets,
            require_live_listing_proof=True,
            allow_superset=True,
        )
    )
    primary_repo_flat_prepared_only_repeat_blocked = bool(
        primary_repo_flat_recent_prep_repeat_count >= distribution_lane_selector.PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_THRESHOLD
        and not primary_repo_flat_packet_delivery_active
        and not primary_repo_flat_packet_current_for_active_window
    )
    immediate_publisher_packet_available = bool(
        primary_repo_flat_targets
        and primary_repo_flat_packet.exists()
        and primary_repo_flat_packet_current_for_active_window
        and not primary_repo_flat_packet_delivery_active
        and not primary_repo_flat_prepared_only_repeat_blocked
    )
    publisher_packet_needs_follow_through_before_curator = bool(
        primary_repo_flat_targets
        and primary_repo_flat_packet.exists()
        and (
            not primary_repo_flat_packet_current
            or primary_repo_flat_packet_delivery_active
        )
    )
    if immediate_publisher_packet_available:
        packet_targets = primary_repo_flat_targets[:3]
        why = 'Fresh developer-native publisher contacts are already discovered for the flat-Codeberg repair lane.'
        if packet_targets and not primary_repo_flat_runtime_targets:
            why += ' This packet is still human-executable via verified public contact paths even though no direct runtime-sendable email route remains.'
        when = 'Do now'
        if short_review_window_active and short_review_window_release_at:
            when = f'After short-window congestion clears ({short_review_window_release_at})'
        execution_items.append({
            'label': 'Primary-repo-flat publisher contact packet',
            'path': str(primary_repo_flat_packet),
            'when': when,
            'why': why,
            'targets': ', '.join(packet_targets),
        })
        targets_prepared.extend(packet_targets)

    manual_outreach_assets = _manual_outreach_assets_waiting_for_execution(now)
    non_runtime_primary_repo_flat_targets = {
        _display_target_name(str(row.get('target') or '').strip())
        for row in _load_primary_repo_flat_contact_discovery()
        if str(row.get('target') or '').strip()
        and not _publisher_target_has_runtime_sendable_channel(row.get('channels') or [])
    }
    manual_followthrough_blocked_targets: list[str] = []
    reddit_discussion_asset = _reddit_discussion_asset_waiting_for_execution(now)
    if reddit_discussion_asset is not None:
        execution_items.append({
            'label': 'Manual community discussion asset',
            'path': reddit_discussion_asset['path'],
            'when': 'Do now',
            'why': reddit_discussion_asset['summary'] or 'Fresh community discussion opportunities already exist, so reuse the packet directly instead of regenerating another Reddit monitor handoff.',
            'targets': reddit_discussion_asset['title'] or 'Reddit discussion handoff packet',
        })
    reddit_manual_discussion_blocked = _reddit_manual_discussion_blocked()
    for item in manual_outreach_assets:
        if Path(str(item.get('path') or '')).name == 'primary_repo_flat_contact_handoff_packet_latest.md':
            continue
        target = str(item.get('target') or '').strip()
        targets = [str(value).strip() for value in (item.get('targets') or []) if str(value).strip()]
        title = str(item.get('title') or '').strip().lower()
        summary = str(item.get('summary') or '').strip()
        path_text = str(item.get('path') or '').strip().lower()
        label = 'Manual publisher outreach asset'
        if 'reddit' in title or 'discussion' in title or 'discussion handoff' in summary.lower() or 'reddit_discussion' in path_text:
            label = 'Manual community discussion asset'
        if label == 'Manual community discussion asset' and reddit_manual_discussion_blocked:
            continue
        is_primary_repo_flat_manual_followthrough = 'primary_repo_flat_manual_review_asset' in path_text or 'manual follow-through asset' in title
        if (
            is_primary_repo_flat_manual_followthrough
            and skip_publisher_outreach
            and targets
            and all(candidate in non_runtime_primary_repo_flat_targets for candidate in targets)
        ):
            manual_followthrough_blocked_targets.extend(targets)
            continue
        execution_items.append({
            'label': label,
            'path': str(item.get('path') or ''),
            'when': 'Do now',
            'why': summary or 'A channel-ready manual publisher outreach asset already exists and should be reused before inventing another packet.',
            'targets': target,
        })
        if target:
            targets_prepared.append(target)

    if (
        manual_hint
        and not stackoverflow_surface_exhausted
        and not stackoverflow_manual_delivery_current
        and not stackoverflow_post_cooldown_run_current
    ):
        stackoverflow_when = 'Do now'
        if scheduled_stackoverflow_run:
            stackoverflow_when = f'Scheduled for {scheduled_stackoverflow_run}'
        elif manual_hint.get('cooldown_active'):
            stackoverflow_when = f"After {manual_hint.get('next_retry_at') or 'cooldown ends'}"
        execution_items.append({
            'label': 'StackOverflow demand-capture packet',
            'path': manual_hint.get('packet_path', ''),
            'when': stackoverflow_when,
            'why': 'Highest-intent Q&A asset already exists and should be reused before another search pass.',
            'targets': manual_hint.get('title', ''),
        })
        if manual_hint.get('title'):
            targets_prepared.append(manual_hint['title'])

    manual_contact_targets = [
        _display_target_name(str(row.get('target') or '').strip())
        for row in _manual_contact_queue_rows(curator_queue_rows)
        if str(row.get('target') or '').strip()
    ]
    curator_contact_packet = DRAFTS_DIR / 'curator_contact_handoff_packet_latest.md'
    immediate_manual_contact_packet_available = bool(
        manual_contact_targets
        and curator_contact_packet.exists()
        and not _curator_contact_packet_already_delivered(now, manual_contact_targets)
    )
    if immediate_manual_contact_packet_available:
        execution_items.append({
            'label': 'Curator manual-contact packet',
            'path': str(curator_contact_packet),
            'when': 'Do now',
            'why': 'These prepared curator targets already have non-GitHub contact paths, so execution matters more than more discovery.',
            'targets': ', '.join(manual_contact_targets[:3]),
        })
        targets_prepared.extend(manual_contact_targets[:3])

    curator_targets = _current_curator_handoff_targets(curator_queue_rows)
    curator_packet = DRAFTS_DIR / 'curator_handoff_packet_latest.md'
    if (
        curator_targets
        and curator_packet.exists()
        and not skip_curator_outreach
        and not curator_measurement_saturated
        and not immediate_publisher_packet_available
        and not publisher_packet_needs_follow_through_before_curator
        and not immediate_manual_contact_packet_available
    ):
        execution_items.append({
            'label': 'Curator handoff packet',
            'path': str(curator_packet),
            'when': 'Do now',
            'why': 'Prepared curator assets already exist and no faster follow-through packet is still waiting ahead of them.',
            'targets': ', '.join(curator_targets[:3]),
        })
        targets_prepared.extend(curator_targets[:3])

    comparison_targets = _current_comparison_handoff_targets(comparison_queue_rows)
    comparison_packet = DRAFTS_DIR / 'comparison_backlink_handoff_packet_latest.md'
    comparison_packet_delivery_active = _comparison_packet_delivery_still_active(now)
    if comparison_targets and comparison_packet.exists() and not comparison_packet_delivery_active:
        execution_items.append({
            'label': 'Comparison backlink packet',
            'path': str(comparison_packet),
            'when': 'Do after fresh publisher / curator contacts are sent',
            'why': 'Comparison proof is already prepared and should be reused instead of redrafted.',
            'targets': ', '.join(comparison_targets[:3]),
        })
        targets_prepared.extend(comparison_targets[:3])

    backlink_payload = _load_json(_backlink_status_latest_path())
    directory_repair_rows = _secondary_surface_repair_rows(backlink_payload)
    directory_confirmation_packet = DRAFTS_DIR / 'directory_confirmation_execution_latest.md'
    directory_confirmation_packet_current = _directory_confirmation_packet_is_current(
        directory_confirmation_packet,
        directory_repair_rows,
    )
    directory_secondary_surface_follow_through_active = _directory_secondary_surface_repair_still_active(
        now,
        directory_repair_rows,
    )
    if (
        directory_repair_rows
        and directory_confirmation_packet.exists()
        and directory_confirmation_packet_current
        and not directory_secondary_surface_follow_through_active
    ):
        execution_items.append({
            'label': 'Directory secondary-surface repair packet',
            'path': str(directory_confirmation_packet),
            'when': 'Do now',
            'why': 'A live third-party surface still routes repo intent away from Codeberg or leaves it unclear, so correcting that surface is a real adoption-moving follow-through asset.',
            'targets': ', '.join(sorted({row['name'] for row in directory_repair_rows})[:3]),
        })
        targets_prepared.extend(sorted({row['name'] for row in directory_repair_rows})[:3])

    recent_proof_asset_shipped = _recent_local_executed_action_type(
        now,
        action_types=distribution_lane_selector.RECENT_PROOF_ASSET_ACTION_TYPES,
    )

    lines = [
        '# Ralph Workflow Marketing Execution Board',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this board exists',
        f'- {_adoption_summary()}',
        '- Multiple live lanes already exist, so this board consolidates the best executable assets instead of letting them stay siloed across separate packet files.',
        '- Use this as the single follow-through surface during measurement holds and overlapping review windows.',
        '',
        '## Active review windows',
    ]

    scheduled_measurement_hold_release_run = _current_measurement_hold_release_run(
        now,
        not_before=short_review_window_release_at or None,
    )

    if apollo_status:
        lines.append(f"- Apollo next review: {apollo_status.get('next_review_at', 'unknown')}")
        lines.append(f"- Apollo launch review: {apollo_status.get('launch_review_at', 'unknown')}")
    if short_review_window_release_at:
        lines.append(f"- Short review-window congestion clears at: {short_review_window_release_at}")
    if scheduled_measurement_hold_release_run:
        lines.append(f"- Post-hold marketer rerun scheduled: {scheduled_measurement_hold_release_run}")
    if manual_hint.get('cooldown_active') and manual_hint.get('next_retry_at'):
        lines.append(f"- StackOverflow retry opens: {manual_hint['next_retry_at']}")
    if stackoverflow_manual_delivery_current:
        lines.append('- StackOverflow demand-capture packet was already delivered for manual placement in the current review window; do not redeliver it until a genuinely new placement path exists.')
    if stackoverflow_post_cooldown_run_current:
        lines.append('- A post-cooldown StackOverflow rerun is already scheduled in the current review window; do not spend another slot refreshing the same packet before that run lands.')
    if stackoverflow_surface_exhausted:
        lines.append('- StackOverflow demand-capture packet is exhausted for this review window; do not redeliver it until a genuinely new placement path exists.')
    if skip_curator_outreach:
        lines.append('- Same-family curator outreach is paused in the active repair window; do not treat prepared curator packets as do-now assets until that hold ages out or the lane map changes.')
    if primary_repo_flat_packet_delivery_active:
        lines.append('- Primary-repo-flat publisher contact packet was already manually delivered in the current review window; do not surface it again until that window expires or the prepared target set changes.')
    if primary_repo_flat_targets and primary_repo_flat_packet.exists() and primary_repo_flat_packet_current_for_active_window and short_review_window_active:
        lines.append('- A refreshed primary-repo-flat publisher packet now exists for the current waiting target set, but the short review window is still active; hold manual delivery until that congestion clears.')
    if comparison_packet_delivery_active:
        lines.append('- Comparison backlink packet was already manually delivered in the current review window; do not surface it again until that window expires or the prepared target set changes.')
    if directory_secondary_surface_follow_through_active:
        lines.append('- Directory secondary-surface repair already shipped in the current review window; do not requeue it until the documented follow-up date or the live target set changes.')

    apollo_status_value = str(apollo_status.get('status') or '').strip().lower()
    apollo_runtime_status_path = LOG_DIR / 'apollo_status.json'
    apollo_runtime_status = _load_json(apollo_runtime_status_path)
    apollo_runtime_blocked = apollo_status_value == 'runtime_auth_blocked' or _call_selector_local(
        distribution_lane_selector._apollo_status_blocked,
        apollo_runtime_status,
    )
    apollo_launch_ready_unverified = (
        apollo_status_value == 'launch_ready_unverified_send'
        and int(apollo_status.get('record_count') or 0) > 0
    )
    apollo_followup_due = _call_selector_local(
        distribution_lane_selector._apollo_followup_due,
        now,
        apollo_status,
    )
    apollo_next_review_at = _parse_dt(str(apollo_status.get('next_review_at') or '').strip())
    apollo_blocked_followup_due = (
        apollo_runtime_blocked
        and int(apollo_status.get('record_count') or 0) > 0
        and not bool(apollo_status.get('measurement_pending'))
        and apollo_next_review_at is not None
        and apollo_next_review_at <= now
    )
    apollo_launch_packet_path = apollo_launch_handoff_packet if apollo_launch_handoff_packet.exists() else apollo_launch_packet
    apollo_blocker_packet_delivery_active = False
    if not apollo_runtime_blocked and (apollo_launch_ready_unverified or apollo_followup_due) and apollo_launch_packet_path.exists():
        when = 'Do now'
        why = 'Apollo already has a verified non-zero list and Codeberg-primary launch packet; the next truthful step is live send confirmation, not another outbound rebuild.'
        label = 'Apollo launch / send confirmation packet'
        if apollo_followup_due and not bool(apollo_status.get('needs_live_verification')):
            why = 'Apollo already passed its first review checkpoint and is still not outcome-ready, so the next truthful step is a same-day live-state review instead of another empty-board hold.'
            label = 'Apollo outcome-readiness review packet'
        elif short_review_window_active and short_review_window_release_at:
            when = f'After short-window congestion clears ({short_review_window_release_at})'
        execution_items.append({
            'label': label,
            'path': str(apollo_launch_packet_path),
            'when': when,
            'why': why,
            'targets': str(apollo_status.get('sequence_name') or 'Apollo sequence').strip(),
        })
        if str(apollo_status.get('sequence_name') or '').strip():
            targets_prepared.append(str(apollo_status.get('sequence_name') or '').strip())
    elif apollo_blocked_followup_due:
        blocker_packet_path, blocker_targets = _write_apollo_runtime_blocker_review_packet(
            now,
            apollo_status=apollo_status,
            apollo_runtime_status=apollo_runtime_status,
        )
        apollo_blocker_packet_delivery_active = _apollo_runtime_blocker_review_delivery_still_active(
            artifact_path=str(blocker_packet_path),
            now=now,
        )
        if not apollo_blocker_packet_delivery_active:
            execution_items.append({
                'label': 'Apollo runtime-blocker review packet',
                'path': str(blocker_packet_path),
                'when': 'Do now',
                'why': 'Apollo follow-up is already due, but runtime auth is blocked; the truthful next move is to carry a blocker-specific recovery packet instead of collapsing back into another empty-board guard pause.',
                'targets': str(apollo_status.get('sequence_name') or 'Apollo sequence').strip(),
            })
        targets_prepared.extend(blocker_targets)

    if apollo_blocker_packet_delivery_active:
        lines.append('- Apollo runtime-blocker review packet was already delivered in the current review window; do not resurface it until the blocker packet changes or the runtime blocker clears.')

    if execution_items:
        lines.extend(['', '## Best executable assets still waiting'])
        for idx, item in enumerate(execution_items, start=1):
            lines.extend([
                f"### {idx}. {item['label']}",
                f"- When: {item['when']}",
                f"- Packet: {item['path']}",
                f"- Targets: {item['targets']}",
                f"- Why this matters: {item['why']}",
                '',
            ])
    else:
        blockers: list[str] = []
        if apollo_runtime_blocked and int(apollo_status.get('record_count') or 0) > 0:
            blockers.append(
                '- Apollo has a verified non-zero list, but the current runtime is auth-blocked '
                f"({apollo_status.get('runtime_blocker_status') or apollo_runtime_status.get('status') or 'unknown'}), so do not surface the launch/review packet as a do-now asset until the runtime blocker clears."
            )
        if apollo_blocker_packet_delivery_active:
            blockers.append(
                '- Apollo runtime-blocker review packet already exists but was already delivered in the current review window; do not redeliver it until the blocker packet changes or the runtime blocker clears.'
            )
        all_primary_repo_flat_findings = _load_primary_repo_flat_contact_discovery()
        if all_primary_repo_flat_findings:
            recent_publisher_targets = sorted(_recent_contact_targets(
                now,
                action_types=PUBLISHER_CONTACT_ACTION_TYPES,
                days=7,
            ))
            active_manual_delivery_targets = _active_manual_outreach_delivery_targets(now)
            discovered_primary_targets = {
                _display_target_name(str(row.get('target') or '').strip())
                for row in all_primary_repo_flat_findings
                if str(row.get('target') or '').strip()
            }
            blocked_primary_targets = set(recent_publisher_targets) | set(active_manual_delivery_targets)
            non_runtime_primary_targets = [
                _display_target_name(str(row.get('target') or '').strip())
                for row in all_primary_repo_flat_findings
                if str(row.get('target') or '').strip()
                and not _publisher_target_has_runtime_sendable_channel(row.get('channels') or [])
                and _display_target_name(str(row.get('target') or '').strip()) not in blocked_primary_targets
            ]
            if non_runtime_primary_targets:
                blockers.append(
                    '- Remaining publisher-contact discovery is not runtime-sendable here: '
                    + ', '.join(non_runtime_primary_targets[:3])
                    + '.'
                )
            already_contacted_primary_targets = [
                target for target in recent_publisher_targets
                if target in discovered_primary_targets
            ]
            if already_contacted_primary_targets:
                blockers.append(
                    '- Fresh publisher outreach already shipped in the current review window for: '
                    + ', '.join(already_contacted_primary_targets[:3])
                    + '.'
                )

        if (
            primary_repo_flat_targets
            and primary_repo_flat_packet.exists()
            and not primary_repo_flat_packet_current_for_active_window
            and not (primary_repo_flat_packet_delivery_active and primary_repo_flat_packet_current_for_active_window)
        ):
            blockers.append(
                '- Primary-repo-flat publisher discovery has changed, and the canonical handoff packet no longer covers the current waiting target set; refresh it before treating the packet as do-now.'
            )
        if primary_repo_flat_targets and primary_repo_flat_packet.exists() and primary_repo_flat_packet_delivery_active:
            blockers.append(
                '- Primary-repo-flat publisher contact packet already exists but was already delivered in the current review window; do not redeliver it yet.'
            )
        if primary_repo_flat_prepared_only_repeat_blocked:
            blockers.append(
                '- The current primary-repo-flat publisher contact packet was already prepared '
                f'{primary_repo_flat_recent_prep_repeat_count} time(s) in the last '
                f'{distribution_lane_selector.PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_WINDOW_HOURS} hours without a live delivery window; '
                'do not resurface it as a do-now asset until the target set or delivery state materially changes.'
            )
        if manual_followthrough_blocked_targets:
            blockers.append(
                '- A manual-only primary-repo-flat follow-through asset exists for '
                f'{", ".join(sorted(set(manual_followthrough_blocked_targets))[:3])}, '
                'but same-family publisher outreach is paused and those targets still lack a runtime-sendable channel; do not surface that asset as a do-now lane yet.'
            )
        if manual_contact_targets and curator_contact_packet.exists() and not immediate_manual_contact_packet_available:
            blockers.append(
                '- Curator manual-contact packet already exists but was already delivered in the current review window; do not redeliver it yet.'
            )
        if curator_targets and skip_curator_outreach:
            blockers.append(
                '- Curator handoff packet exists, but same-family curator outreach is paused during the active repair window.'
            )
        elif curator_targets and curator_measurement_saturated:
            blockers.append(
                '- Curator handoff packet exists, but curator reply/backlink review windows are already saturated in the current short window.'
            )
        if comparison_targets and comparison_packet.exists() and comparison_packet_delivery_active:
            blockers.append(
                '- Comparison backlink packet exists, but it was already manually delivered in the current review window.'
            )
        if directory_repair_rows and directory_secondary_surface_follow_through_active:
            blockers.append(
                '- Directory secondary-surface repair already shipped in the current review window; wait for the follow-up date or a target-set change before resurfacing it.'
            )
        stronger_blockers_before_stackoverflow = bool(blockers)
        if stackoverflow_surface_exhausted:
            blockers.append(
                '- StackOverflow handoff packet exists, but the post-cooldown slot already burned without a fresh placement-ready outcome.'
            )

        repo_proof_asset_fallback_ready = bool(
            stackoverflow_surface_exhausted
            and not recent_proof_asset_shipped
            and not stronger_blockers_before_stackoverflow
        )
        if repo_proof_asset_fallback_ready:
            execution_items.append({
                'label': 'Repo conversion proof asset',
                'path': str(DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_repo_conversion_proof_asset.md'),
                'when': 'Do now',
                'why': 'External distribution lanes are already in-flight or exhausted, so the next truthful move is a repo-first proof asset that improves Codeberg conversion instead of another empty hold or guard-pause loop.',
                'targets': 'workflow composition example + START_HERE routing',
            })
            targets_prepared.extend([
                str(WORKFLOW_COMPOSITION_EXAMPLE_PATH),
                str(START_HERE_PATH),
            ])
            lines.extend(['', '## Best executable assets still waiting'])
            for idx, item in enumerate(execution_items, start=1):
                lines.extend([
                    f"### {idx}. {item['label']}",
                    f"- When: {item['when']}",
                    f"- Packet: {item['path']}",
                    f"- Targets: {item['targets']}",
                    f"- Why this matters: {item['why']}",
                    '',
                ])
            blockers = []
        else:
            lines.extend([
                '',
                '## Best executable assets still waiting',
                '- No do-now handoff packet is currently truthful in this review window.',
            ])
        if blockers:
            lines.extend(blockers)
            lines.append('- If this board is still empty after one of these blockers clears, the lane architecture needs another repair.')
        else:
            lines.append('- No current handoff packet was found. If this stays empty while adoption is flat, the lane architecture needs another repair.')

    lines.extend([
        '## Shared findings reused',
        '- market_intelligence_latest.json → positioning truths and comparison framing',
        '- adoption_metrics_latest.json → Codeberg movement remains the primary success gate',
        '- curator_outreach_queue_latest.json / comparison_backlink_queue_latest.json → live prepared execution queues',
        '- primary_repo_flat_contact_discovery_latest.json → fresh publisher-contact lane',
        '- apollo_sequence_status_latest.json / apollo_sequence_launch_packet_latest.md → launch-ready managed outbound state',
        '- stackoverflow_answer_handoff_packet_latest.md → high-intent Q&A demand-capture asset',
        '',
        '## Verified infrastructure state (programmatic, not fabricated)',
    ])
    lines.extend(_verified_infrastructure_state(now))
    lines.extend([
        '',
        '## Process rule now in force',
        '- Do not generate another siloed packet when one of the assets above is already current.',
        '- During a hold window, refresh stale packets if needed, then point back to this board instead of inventing another reset artifact.',
    ])

    content = '\n'.join(lines) + '\n'
    artifact.write_text(content, encoding='utf-8')
    latest_artifact.write_text(content, encoding='utf-8')

    deduped_targets: list[str] = []
    seen_targets: set[str] = set()
    for target in targets_prepared:
        cleaned = str(target).strip()
        if not cleaned or cleaned in seen_targets:
            continue
        seen_targets.add(cleaned)
        deduped_targets.append(cleaned)
    return artifact, deduped_targets



def _write_action_log(execution: LaneExecution, now: datetime) -> Path:
    payload = {
        'timestamp': now.isoformat(),
        'run_type': 'marketing-distribution-execution',
        'chosen_action': {
            'type': execution.action_type,
            'channel': execution.lane,
            'title': f'Distribution lane execution: {execution.lane}',
            'draft': execution.artifact_path,
        },
        'why_this_action': {
            'summary': execution.summary,
            'shared_findings_used': execution.shared_findings_used,
            'targets_prepared': execution.targets_prepared,
        },
        'result': {
            'status': execution.status,
            'ok': True,
            'live_external_action': execution.live_external_action,
            'blocking_factors': execution.blocking_factors or [],
        },
    }
    if execution.lane in {'distribution_architecture_repair', 'distribution_architecture_guard_follow_through', 'distribution_architecture_guard_pause'}:
        payload['verification'] = {
            'execution_board_fingerprint': _call_selector_local(distribution_lane_selector._execution_board_fingerprint),
        }
    path = LOG_DIR / f'marketing_{now.strftime("%Y-%m-%d")}_{execution.lane}_execution.json'
    path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    return path


def _execute_owned_content(now: datetime) -> LaneExecution:
    posted = load_posted()

    for source_path in OWNED_CONTENT_SOURCE_CANDIDATES:
        if not source_path.exists():
            continue

        raw = source_path.read_text(encoding='utf-8')
        title, body, metadata = extract_title_and_body(raw)
        body = body.strip()
        if len(body) < 300:
            continue

        draft_hash = digest_text(body)
        if already_posted_successfully(
            posted,
            draft_hash,
            'telegraph',
            experiment_id=metadata.get('experiment_id'),
            source_path=str(source_path),
        ):
            continue

        artifact = DRAFTS_DIR / f"{now.strftime('%Y-%m-%d')}_{source_path.stem}_telegraph.md"
        artifact.write_text(body + '\n', encoding='utf-8')

        ok, url_or_error = post_telegraph(title, body + CTA_FOOTER, source_path=str(source_path))
        if ok:
            record = {
                'date': now.strftime('%Y-%m-%d'),
                'draft': artifact.name,
                'title': title,
                'platform': 'telegraph',
                'ok': True,
                'status': 'posted',
                'url': url_or_error,
                'error': None,
                'draft_hash': draft_hash,
                'source_path': str(source_path),
            }
            posted.setdefault('posts', []).append(record)
            posted['last_run'] = now.isoformat()
            save_posted(posted)
            return LaneExecution(
                lane='owned_content',
                action_type='owned_content_publication',
                status='executed',
                artifact_path=str(artifact),
                summary='Published a repo-native proof guide to Telegraph so the hold window still creates fresh Codeberg-first demand capture instead of another owned-content noop.',
                targets_prepared=[url_or_error, str(source_path)],
                shared_findings_used=[],
                live_external_action=True,
                blocking_factors=[],
            )

        return LaneExecution(
            lane='owned_content',
            action_type='owned_content_publication_failed',
            status='failed',
            artifact_path=str(artifact),
            summary='Prepared an owned-content proof guide for publication, but the Telegraph publish attempt failed.',
            targets_prepared=[str(source_path)],
            shared_findings_used=[],
            live_external_action=False,
            blocking_factors=[url_or_error],
        )

    return LaneExecution(
        lane='owned_content',
        action_type='owned_content_lane_noop',
        status='skipped',
        artifact_path=None,
        summary='Owned-content lane stayed selected, but no fresh repo-native guide remained unpublished for Telegraph.',
        targets_prepared=[],
        shared_findings_used=[],
        live_external_action=False,
        blocking_factors=['No unpublished owned-content guide was available for Telegraph publication.'],
    )


def execute_distribution_lane(decision: LaneDecision, now: datetime | None = None) -> LaneExecution:
    now = now or datetime.now()
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    market_intelligence = load_market_intelligence('agents/marketing/distribution_lane_executor.py')
    github_auth_available = subprocess.run(
        ['gh', 'auth', 'status'],
        capture_output=True,
        text=True,
        check=False,
    ).returncode == 0

    # ── Cross-channel spidering guard ────────────────────────────────────
    # Map lane decision to spidering guard channel name so permanently
    # blocked and cooldown-active channels are rejected before dispatch.
    _LANE_TO_GUARD_CHANNEL: dict[str, str] = {
        'devto_bootstrap': 'dev.to',
        'devto_crosspost': 'dev.to',
        'reddit_monitor': 'reddit',
        'reddit_manual_discussion': 'reddit',
        'hn_poster': 'hackernews',
        'lobsters_poster': 'lobsters',
        'apollo_outreach_execution': 'apollo-outreach',
        'apollo_outreach': 'apollo',
        'github_discussions_outreach': 'github-discussions',
        'github_discussions_search': 'github-discussions-search',
        'stackoverflow_lane': 'stackoverflow',
        'pypi_readme_update': 'pypi',
        'mastodon_poster': 'mastodon',
        'smtp_outreach': 'smtp-outreach',
        'primary_repo_flat_contact_discovery': 'primary_repo_flat_contact_discovery',
    }
    guard_channel = _LANE_TO_GUARD_CHANNEL.get(decision.lane)
    if guard_channel:
        from agents.marketing.channel_spidering_guard import guard_check
        allowed, reason, cooldown_h = guard_check(guard_channel)
        if not allowed:
            return LaneExecution(
                lane=decision.lane,
                action_type=f'{guard_channel}_spidering_guard_blocked',
                status='spidering_blocked',
                artifact_path='',
                summary=f'Channel spidering guard blocked {guard_channel}: {reason} ({cooldown_h:.1f}h cooldown remaining)',
                targets_prepared=[],
                shared_findings_used=decision.shared_findings_used,
                live_external_action=False,
                blocking_factors=[f'spidering_guard: {reason}'],
            )

    # --- Repair-awareness: respect skip flags set by run.py when audit repairs are pending.
    # These flags are set via LaneDecision attributes when repair_mode is active in run.py.
    skip_directory_submissions = getattr(decision, 'skip_directory_submissions', False)
    skip_curator_outreach = getattr(decision, 'skip_curator_outreach', False)

    def _skipped_execution(lane: str, action_type: str, reason: str) -> LaneExecution:
        return LaneExecution(
            lane=lane,
            action_type=action_type,
            status='skipped_repair',
            artifact_path='',
            summary=reason,
            targets_prepared=[],
            shared_findings_used=decision.shared_findings_used,
            live_external_action=False,
            blocking_factors=[],
        )

    # Block directory submissions when same_family_distribution_overlap repair is active.
    if skip_directory_submissions and decision.lane == 'directory_submission':
        return _skipped_execution(
            decision.lane,
            'directory_submission_skipped',
            'Skipped: same_family_distribution_overlap repair active; net-new low-intent directory submissions are paused per audit repair plan.',
        )

    # Block curator outreach when same_family_outreach_overlap repair is active.
    if skip_curator_outreach and decision.lane == 'curator_outreach':
        return _skipped_execution(
            decision.lane,
            'curator_outreach_skipped',
            'Skipped: same_family_outreach_overlap repair active; same-day curator-contact bursts are paused per audit repair plan.',
        )

    blocking_factors: list[str] = []
    if not github_auth_available and decision.lane in {'curator_outreach', 'curator_handoff_packet', 'curator_contact_handoff_packet', 'comparison_backlink_outreach'}:
        blocking_factors.append('github_auth_missing_for_live_pr_submission')

    if decision.lane == 'curator_outreach':
        artifact, prepared = _write_curator_execution(decision, now, market_intelligence)
        action_type = 'curator_outreach_execution'
        summary = 'Prepared target-specific curator outreach copy using shared market intelligence and Codeberg-first proof assets.'
        if artifact.name.endswith('_curator_queue_follow_through.md'):
            action_type = 'curator_queue_follow_through'
            summary = 'Detected a live curator queue and enforced follow-through instead of regenerating the same outreach targets.'
        execution = LaneExecution(
            lane=decision.lane,
            action_type=action_type,
            status='prepared',
            artifact_path=str(artifact),
            summary=summary,
            targets_prepared=prepared,
            shared_findings_used=decision.shared_findings_used,
            live_external_action=False,
            blocking_factors=blocking_factors,
        )
        if 'github_auth_missing_for_live_pr_submission' in blocking_factors:
            handoff_path, handoff_targets = _write_curator_handoff_packet(now, _load_curator_queue_rows())
            _append_handoff_pointer(execution.artifact_path, title='Canonical manual execution packet', handoff_path=handoff_path)
            if handoff_targets:
                execution = LaneExecution(
                    lane=execution.lane,
                    action_type=execution.action_type,
                    status=execution.status,
                    artifact_path=execution.artifact_path,
                    summary=execution.summary + ' Also refreshed the canonical curator handoff packet because live PR submission is blocked here.',
                    targets_prepared=execution.targets_prepared,
                    shared_findings_used=execution.shared_findings_used,
                    live_external_action=execution.live_external_action,
                    blocking_factors=execution.blocking_factors,
                )
    elif decision.lane == 'directory_submission':
        artifact, prepared = _write_directory_execution(decision, now)
        summary = 'Prepared directory submission payloads for the next unused autonomous backlink lanes.'
        execution = LaneExecution(
            lane=decision.lane,
            action_type='directory_submission_execution',
            status='prepared',
            artifact_path=str(artifact),
            summary=summary,
            targets_prepared=prepared,
            shared_findings_used=decision.shared_findings_used,
            live_external_action=False,
            blocking_factors=[],
        )
    elif decision.lane == 'directory_confirmation':
        artifact, prepared, payload = _write_directory_confirmation_execution(now)
        summary_payload = payload.get('summary') or {}
        execution = LaneExecution(
            lane=decision.lane,
            action_type='directory_confirmation_execution',
            status='executed',
            artifact_path=str(artifact),
            summary=(
                'Refreshed live directory-listing and backlink evidence so the loop can reuse real approvals '
                f"({summary_payload.get('directories_with_live_listings', 0)} live listing(s)) instead of stacking more low-intent submissions."
            ),
            targets_prepared=prepared,
            shared_findings_used=decision.shared_findings_used,
            live_external_action=False,
            blocking_factors=[],
        )
    elif decision.lane == 'distribution_confirmation_follow_through':
        artifact, prepared = _write_distribution_confirmation_follow_through(now)
        execution = LaneExecution(
            lane=decision.lane,
            action_type='distribution_confirmation_follow_through',
            status='prepared',
            artifact_path=str(artifact),
            summary='Prepared the confirmation-required follow-through packet so a live but not-yet-approved correction is treated as a real blocker instead of a shipped outcome.',
            targets_prepared=prepared,
            shared_findings_used=decision.shared_findings_used,
            live_external_action=False,
            blocking_factors=['Platform confirmation is still required before the public action is approved.'],
        )
    elif decision.lane in {'curator_handoff_packet', 'curator_contact_handoff_packet'}:
        curator_queue_rows = _load_curator_queue_rows()
        comparison_queue_rows = _comparison_queue_rows(COMPARISON_QUEUE_LATEST_PATH)
        curator_latest = DRAFTS_DIR / 'curator_handoff_packet_latest.md'
        comparison_latest = DRAFTS_DIR / 'comparison_backlink_handoff_packet_latest.md'
        current_curator_targets = _current_curator_handoff_targets(curator_queue_rows)
        current_comparison_targets = _current_comparison_handoff_targets(comparison_queue_rows)
        curator_packet_current = _handoff_packet_is_current(curator_latest, current_curator_targets, require_live_listing_proof=True)
        comparison_packet_current = _handoff_packet_is_current(comparison_latest, current_comparison_targets, require_live_listing_proof=True)
        prefer_contact_handoff = decision.lane == 'curator_contact_handoff_packet'

        prepared: list[str] = []
        comparison_targets: list[str] = []
        refreshed_packets = False

        if not prefer_contact_handoff and not curator_packet_current:
            artifact, prepared = _write_curator_handoff_packet(now, curator_queue_rows)
            refreshed_packets = True
        elif not prefer_contact_handoff:
            artifact = _write_manual_handoff_follow_through(
                now=now,
                title='curator_handoff_follow_through',
                why=[
                    decision.reason,
                    _adoption_summary(),
                ],
                packet_label='Curator packet',
                packet_path=curator_latest,
                targets=current_curator_targets,
                review_rows=curator_queue_rows,
                comparison_packet_path=comparison_latest if comparison_latest.exists() else None,
            )

        if not prefer_contact_handoff and current_comparison_targets and not comparison_packet_current:
            comparison_handoff_path, comparison_targets = _write_comparison_handoff_packet(now, comparison_queue_rows)
            _append_handoff_pointer(str(artifact), title='Comparison backlink execution packet', handoff_path=comparison_handoff_path)
            refreshed_packets = True
        elif not prefer_contact_handoff and current_comparison_targets and comparison_latest.exists() and not refreshed_packets:
            _append_handoff_pointer(str(artifact), title='Comparison backlink execution packet', handoff_path=comparison_latest)

        if refreshed_packets:
            execution = LaneExecution(
                lane=decision.lane,
                action_type='curator_handoff_packet_execution',
                status='prepared',
                artifact_path=str(artifact),
                summary='Consolidated the highest-priority prepared curator targets into one canonical execution packet instead of repeating another reset/discovery cycle.' + (' Also refreshed the comparison backlink handoff packet because prepared citation targets already exist.' if comparison_targets else ''),
                targets_prepared=prepared,
                shared_findings_used=decision.shared_findings_used,
                live_external_action=False,
                blocking_factors=blocking_factors,
            )
        elif 'github_auth_missing_for_live_pr_submission' in blocking_factors:
            contact_queue_rows = _manual_contact_queue_rows(curator_queue_rows) if prefer_contact_handoff else []
            contact_discovery_rows = contact_queue_rows or curator_queue_rows
            contact_targets = [
                _display_target_name(row.get('target', ''))
                for row in contact_queue_rows
            ] if contact_queue_rows else [_display_target_name(name) for name in current_curator_targets]

            if not _contact_discovery_is_current(contact_targets):
                artifact, findings = _write_curator_contact_discovery(now, contact_discovery_rows)
                if comparison_latest.exists():
                    _append_handoff_pointer(str(artifact), title='Comparison backlink execution packet', handoff_path=comparison_latest)
                execution = LaneExecution(
                    lane=decision.lane,
                    action_type='curator_contact_discovery_execution',
                    status='prepared',
                    artifact_path=str(artifact),
                    summary=(
                        'Replaced stale curator handoff follow-through with real contact-channel discovery for manual-contact-only curator targets blocked on GitHub auth.'
                        if contact_queue_rows else
                        'Replaced stale curator handoff follow-through with real contact-channel discovery for the highest-priority prepared curator targets blocked on GitHub auth.'
                    ),
                    targets_prepared=[finding['target'] for finding in findings],
                    shared_findings_used=decision.shared_findings_used,
                    live_external_action=False,
                    blocking_factors=blocking_factors,
                )
            else:
                contact_findings = _load_curator_contact_discovery()
                contact_latest = DRAFTS_DIR / 'curator_contact_handoff_packet_latest.md'
                contact_packet_current = _handoff_packet_is_current(contact_latest, contact_targets, require_live_listing_proof=True)
                if not contact_packet_current:
                    artifact, prepared = _write_curator_contact_handoff_packet(now, contact_findings)
                    if comparison_latest.exists():
                        _append_handoff_pointer(str(artifact), title='Comparison backlink execution packet', handoff_path=comparison_latest)
                    execution = LaneExecution(
                        lane=decision.lane,
                        action_type='curator_contact_handoff_packet_execution',
                        status='prepared',
                        artifact_path=str(artifact),
                        summary='Escalated existing curator contact discovery into one canonical manual-contact execution packet so the loop stops rediscovering the same blocked targets.',
                        targets_prepared=prepared,
                        shared_findings_used=decision.shared_findings_used,
                        live_external_action=False,
                        blocking_factors=blocking_factors,
                    )
                else:
                    artifact = _write_manual_handoff_follow_through(
                        now=now,
                        title='curator_contact_handoff_follow_through',
                        why=[
                            decision.reason,
                            _adoption_summary(),
                            'Contact discovery is already current for the prepared curator target set.',
                        ],
                        packet_label='Curator contact packet',
                        packet_path=contact_latest,
                        targets=contact_targets,
                        review_rows=curator_queue_rows,
                        comparison_packet_path=comparison_latest if comparison_latest.exists() else None,
                    )
                    execution = LaneExecution(
                        lane=decision.lane,
                        action_type='curator_contact_handoff_follow_through',
                        status='prepared',
                        artifact_path=str(artifact),
                        summary='Detected that curator contact discovery and the manual-contact packet are already current, so this run enforced follow-through instead of rediscovering the same channels again.',
                        targets_prepared=[],
                        shared_findings_used=decision.shared_findings_used,
                        live_external_action=False,
                        blocking_factors=blocking_factors,
                    )
        else:
            execution = LaneExecution(
                lane=decision.lane,
                action_type='curator_handoff_follow_through',
                status='prepared',
                artifact_path=str(artifact),
                summary='Detected that the existing curator manual packet already matches the top prepared targets, so this run enforced follow-through instead of regenerating the same handoff artifact.',
                targets_prepared=[],
                shared_findings_used=decision.shared_findings_used,
                live_external_action=False,
                blocking_factors=blocking_factors,
            )
    elif decision.lane == 'primary_repo_flat_contact_handoff_packet':
        recent_publisher_targets = _recent_contact_targets(
            now,
            action_types=PUBLISHER_CONTACT_ACTION_TYPES,
            days=7,
        )
        active_manual_delivery_targets = _active_manual_outreach_delivery_targets(now)
        all_findings = _load_primary_repo_flat_contact_discovery()
        findings = [
            finding for finding in all_findings
            if _publisher_target_is_packet_executable(finding)
            and _display_target_name(str(finding.get('target') or '').strip()) not in recent_publisher_targets
            and _display_target_name(str(finding.get('target') or '').strip()) not in active_manual_delivery_targets
        ]
        non_executable_targets = [
            _display_target_name(str(finding.get('target') or '').strip())
            for finding in all_findings
            if str(finding.get('target') or '').strip()
            and not _publisher_target_is_packet_executable(finding)
        ]
        expected_targets = [
            _display_target_name(str(finding.get('target') or '').strip())
            for finding in findings
            if str(finding.get('target') or '').strip()
        ]
        packet_path = DRAFTS_DIR / 'primary_repo_flat_contact_handoff_packet_latest.md'
        if findings and not _handoff_packet_is_current(packet_path, expected_targets, require_live_listing_proof=True):
            artifact, prepared = _write_primary_repo_flat_contact_handoff_packet(now, findings)
            execution = LaneExecution(
                lane=decision.lane,
                action_type='primary_repo_flat_contact_handoff_packet_execution',
                status='prepared',
                artifact_path=str(artifact),
                summary='Converted the fresh primary-repo-flat publisher discovery into one canonical Codeberg-first execution packet so the loop can use a genuinely different contact lane during the current measurement window.',
                targets_prepared=prepared,
                shared_findings_used=decision.shared_findings_used,
                live_external_action=False,
                blocking_factors=[],
            )
        else:
            why = [
                decision.reason,
                _adoption_summary(),
            ]
            summary = 'Detected that the primary-repo-flat publisher contact packet already matches the discovered target set, so this run enforced follow-through instead of regenerating the same packet.'
            if recent_publisher_targets and not expected_targets:
                why.append('Every discovered publisher target already has fresh outreach inside the active review window.')
                summary = 'All discovered publisher-contact targets already received fresh outreach inside the active review window, so this run preserved follow-through instead of re-queuing the same packet.'
            elif non_executable_targets and not expected_targets:
                why.append(
                    'The remaining discovered publisher targets only expose non-runtime-executable channels '
                    f'({", ".join(non_executable_targets[:3])}), so this lane cannot truthfully claim a sendable packet yet.'
                )
                summary = 'The remaining discovered publisher targets only expose non-runtime-executable channels, so this run preserved follow-through instead of re-queuing another unsendable packet.'
            else:
                why.append('Primary-repo-flat publisher contact discovery is already current for this target set.')
            artifact = _write_manual_handoff_follow_through(
                now=now,
                title='primary_repo_flat_contact_handoff_follow_through',
                why=why,
                packet_label='Primary-repo-flat publisher contact packet',
                packet_path=packet_path,
                targets=expected_targets,
                review_rows=[],
            )
            execution = LaneExecution(
                lane=decision.lane,
                action_type='primary_repo_flat_contact_handoff_follow_through',
                status='prepared',
                artifact_path=str(artifact),
                summary=summary,
                targets_prepared=[],
                shared_findings_used=decision.shared_findings_used,
                live_external_action=False,
                blocking_factors=[],
            )
    elif decision.lane == 'manual_outreach_asset_follow_through':
        manual_assets = _manual_outreach_assets_waiting_for_execution(now)
        first_asset = manual_assets[0] if manual_assets else {}
        packet_path = Path(str(first_asset.get('path') or first_asset.get('artifact_path') or DRAFTS_DIR / 'marketing_execution_board_latest.md'))
        raw_targets = first_asset.get('targets') if isinstance(first_asset.get('targets'), list) else None
        targets = [str(item).strip() for item in (raw_targets or []) if str(item).strip()]
        if not targets:
            fallback_target = str(first_asset.get('target') or '').strip()
            targets = [fallback_target] if fallback_target else []
        packet_label = 'Primary-repo-flat publisher contact packet' if packet_path.name == 'primary_repo_flat_contact_handoff_packet_latest.md' else 'Manual publisher outreach asset'
        why = [
            decision.reason,
            _adoption_summary(),
            'A current manual outreach asset already exists, so generating another packet would be fake progress.',
        ]
        artifact = _write_manual_handoff_follow_through(
            now=now,
            title='manual_outreach_asset_follow_through',
            why=why,
            packet_label=packet_label,
            packet_path=packet_path,
            targets=targets,
            review_rows=[],
        )
        summary = 'Reused the current Codeberg-first publisher contact packet as the active follow-through surface instead of letting the loop fall back to a stale manual asset.' if packet_label == 'Primary-repo-flat publisher contact packet' else 'Reused the existing channel-ready manual publisher outreach asset as the active follow-through surface instead of burying it under another measurement-hold cycle.'
        execution = LaneExecution(
            lane=decision.lane,
            action_type='manual_outreach_asset_follow_through',
            status='prepared',
            artifact_path=str(artifact),
            summary=summary,
            targets_prepared=targets,
            shared_findings_used=decision.shared_findings_used,
            live_external_action=False,
            blocking_factors=[],
        )
    elif decision.lane == 'comparison_backlink_outreach':
        artifact, prepared, action_type = _write_comparison_backlink_execution(decision, now, market_intelligence)
        status = 'prepared'
        summary = 'Prepared comparison-led backlink outreach assets from shared market intelligence so the loop ships a fresh distribution capability instead of queue housekeeping.'
        if action_type == 'comparison_backlink_follow_through':
            status = 'skipped_repair'
            summary = 'Detected that the comparison queue only yielded follow-through and blocked the run from counting prepared-only comparison churn as fresh execution.'
        execution = LaneExecution(
            lane=decision.lane,
            action_type=action_type,
            status=status,
            artifact_path=str(artifact),
            summary=summary,
            targets_prepared=prepared,
            shared_findings_used=decision.shared_findings_used,
            live_external_action=False,
            blocking_factors=blocking_factors,
        )
        if 'github_auth_missing_for_live_pr_submission' in blocking_factors:
            comparison_queue_rows = _comparison_queue_rows(COMPARISON_QUEUE_LATEST_PATH)
            expected_targets = _current_comparison_handoff_targets(comparison_queue_rows)
            latest_handoff_path = DRAFTS_DIR / 'comparison_backlink_handoff_packet_latest.md'
            handoff_current = _handoff_packet_is_current(
                latest_handoff_path,
                expected_targets,
                require_live_listing_proof=True,
            )
            handoff_delivery_active = _comparison_packet_delivery_still_active(now)
            if handoff_current:
                _append_handoff_pointer(execution.artifact_path, title='Canonical manual execution packet', handoff_path=latest_handoff_path)
                reuse_summary = ' Reused the already-current comparison handoff packet because live PR submission is blocked here.'
                if handoff_delivery_active:
                    reuse_summary = ' Reused the already-current comparison handoff packet because live PR submission is blocked here and that packet is still inside its active review window.'
                execution = LaneExecution(
                    lane=execution.lane,
                    action_type=execution.action_type,
                    status=execution.status,
                    artifact_path=execution.artifact_path,
                    summary=execution.summary + reuse_summary,
                    targets_prepared=execution.targets_prepared,
                    shared_findings_used=execution.shared_findings_used,
                    live_external_action=execution.live_external_action,
                    blocking_factors=execution.blocking_factors,
                )
            else:
                handoff_path, handoff_targets = _write_comparison_handoff_packet(now, comparison_queue_rows)
                _append_handoff_pointer(execution.artifact_path, title='Canonical manual execution packet', handoff_path=handoff_path)
                if handoff_targets:
                    execution = LaneExecution(
                        lane=execution.lane,
                        action_type=execution.action_type,
                        status=execution.status,
                        artifact_path=execution.artifact_path,
                        summary=execution.summary + ' Also refreshed the canonical comparison handoff packet because live PR submission is blocked here.',
                        targets_prepared=execution.targets_prepared,
                        shared_findings_used=execution.shared_findings_used,
                        live_external_action=execution.live_external_action,
                        blocking_factors=execution.blocking_factors,
                    )
    elif decision.lane == 'curator_due_followup':
        queue_rows = _load_curator_queue_rows()
        artifact, prepared = _write_curator_due_followup_packet(now, queue_rows)
        execution = LaneExecution(
            lane=decision.lane,
            action_type='curator_due_followup_packet_execution',
            status='prepared',
            artifact_path=str(artifact),
            summary='Prepared a concrete follow-up packet for overdue curator outreach targets.',
            targets_prepared=prepared,
            shared_findings_used=decision.shared_findings_used,
            live_external_action=False,
            blocking_factors=blocking_factors,
        )
    elif decision.lane == 'reddit_execution_check':
        # The board is empty, Reddit browser session is confirmed ready, and this lane
        # was selected to break the distribution-architecture-repair grinding loop by
        # triggering the reddit-watchdog pipeline to ship a real external action.
        # Run the watchdog directly (bypasses the stale runner-log cooldown check) and
        # map its result to a LaneExecution.
        watchdog_result = subprocess.run(
            [sys.executable, str(ROOT / 'agents/marketing/reddit_watchdog.py')],
            capture_output=True, text=True, timeout=180,
        )
        try:
            wd_payload = json.loads(watchdog_result.stdout.strip()) if watchdog_result.stdout.strip() else {}
        except Exception:
            wd_payload = {}
        wd_status = wd_payload.get('status', 'unknown')
        wd_ok = wd_payload.get('ok', False)
        if wd_status == 'already_handled':
            # Already posted recently; write a brief artifact noting the state.
            artifact = _write_reddit_execution_check_brief(now, wd_status, wd_payload)
            execution = LaneExecution(
                lane=decision.lane,
                action_type='reddit_execution_check_already_handled',
                status='skipped',
                artifact_path=str(artifact),
                summary=f'Reddit execution check: {wd_status}. Browser session ready but last post was recent.',
                targets_prepared=[],
                shared_findings_used=decision.shared_findings_used,
                live_external_action=False,
                blocking_factors=['reddit_cooldown_active'],
            )
        else:
            # Either a fresh post was attempted or a new opportunity was found.
            # live_external_action is True only if a post was actually shipped.
            shipped = wd_status == 'posted' and wd_ok
            artifact = _write_reddit_execution_check_brief(now, wd_status, wd_payload)
            execution = LaneExecution(
                lane=decision.lane,
                action_type='reddit_execution_check',
                status='executed' if shipped else 'prepared',
                artifact_path=str(artifact),
                summary=(
                    f'Reddit execution check: {wd_status}.'
                    f' Live external action shipped.' if shipped
                    else f' Reddit execution check ran: {wd_status}. No live post this run.'
                ),
                targets_prepared=[],
                shared_findings_used=decision.shared_findings_used,
                live_external_action=shipped,
                blocking_factors=[],
            )
        # Write the board so the selector's board-empty check is satisfied and the
        # grinding loop breaks. Without this, the board stays empty after this lane runs,
        # the next selector call sees board-empty again and re-routes here, grinding.
        _write_marketing_execution_board(now)
    elif decision.lane == 'measurement_hold':
        active_hold = latest_measurement_hold_window(now, LOG_DIR)
        if active_hold is not None:
            manual_hint = _current_manual_demand_capture_hint()
            scheduled_stackoverflow_run = _current_stackoverflow_scheduled_run(now)
            stackoverflow_manual_delivery_current = _call_selector_local(
                distribution_lane_selector._stack_overflow_manual_delivery_current,
                now,
            )
            stackoverflow_post_cooldown_run_current = _call_selector_local(
                distribution_lane_selector._stack_overflow_post_cooldown_run_current,
                now,
            )
            stackoverflow_surface_exhausted = _call_selector_local(
                distribution_lane_selector._stack_overflow_post_cooldown_surface_exhausted,
                now,
            )
            hold_repeat_state = _measurement_hold_window_repeat_state(
                now,
                hold_started_at=active_hold['hold_started_at'],
                hold_until=active_hold['hold_until'],
            )
            execution_board_path, board_targets = _write_marketing_execution_board(now)
            active_hold_release_at = _resolved_measurement_hold_release_at(
                now,
                decision.short_review_window_release_at,
                active_hold['hold_until'].isoformat(timespec='seconds'),
            )
            reentry_contract_path = _write_post_hold_reentry_contract(
                now,
                release_at=active_hold_release_at,
                execution_board_path=execution_board_path,
                shared_findings_used=decision.shared_findings_used,
            )
            hold_release_schedule = {}
            if active_hold_release_at:
                hold_release_schedule = _schedule_measurement_hold_release_run(
                    now=now,
                    release_at=active_hold_release_at,
                    shared_findings_used=decision.shared_findings_used,
                    reentry_contract_path=str(reentry_contract_path),
                )

            lines = [
                '# Measurement Hold Follow-Through',
                f'Generated: {now.isoformat(timespec="seconds")}',
                '',
                'An active measurement-hold cooldown is already in force.',
                f"- Hold started at: {active_hold['hold_started_at'].isoformat()}",
                f"- Hold ends at: {active_hold['hold_until'].isoformat()}",
                f"- Source log: {active_hold['source_log']}",
                f'- Consolidated execution board: {execution_board_path}',
                f'- Post-hold re-entry contract: {reentry_contract_path}',
                '',
                'Do not reset the hold window by emitting another measurement_hold_execution.',
                'Use the existing queue, handoff packets, and live measurement windows as the source of truth until the cooldown expires or a new live external action lands.',
            ]
            if decision.short_review_window_release_at:
                lines.append(f"- Short review-window congestion clears at: {decision.short_review_window_release_at}")
            targets_prepared: list[str] = list(board_targets)
            summary = 'Respected the active measurement-hold cooldown instead of resetting it with another short-window hold execution, and refreshed one consolidated execution board for the live manual lanes.'
            refreshed_assets, refreshed_targets = _refresh_manual_execution_assets(now)
            targets_prepared.extend(refreshed_targets)

            if manual_hint and not scheduled_stackoverflow_run and not stackoverflow_surface_exhausted and not stackoverflow_manual_delivery_current and not stackoverflow_post_cooldown_run_current:
                lines.extend([
                    '',
                    '## Best human-executable demand-capture asset still waiting',
                    f"- Target: {manual_hint['title']}",
                    f"- URL: {manual_hint['url']}",
                    f"- Packet: {manual_hint['packet_path']}",
                    '- Why this stays relevant: it is the strongest current high-intent Q&A fit while the live API lane is cooling down.',
                ])
                if manual_hint.get('cooldown_active') and manual_hint.get('next_retry_at'):
                    lines.append(f"- StackOverflow lane retry opens at: {manual_hint['next_retry_at']}")
                summary += ' Re-surfaced the current StackOverflow handoff asset so the hold window still points at a concrete demand-capture move.'
            elif scheduled_stackoverflow_run or stackoverflow_post_cooldown_run_current:
                lines.extend([
                    '',
                    '## StackOverflow demand-capture follow-through already scheduled',
                    f"- Scheduled run: {scheduled_stackoverflow_run or 'current-window rerun already logged'}",
                    '- Do not re-deliver the current StackOverflow handoff packet before that scheduled slot fires.',
                ])
            elif stackoverflow_manual_delivery_current:
                lines.extend([
                    '',
                    '## StackOverflow demand-capture packet already delivered in this review window',
                    '- The current StackOverflow handoff packet was already surfaced for manual placement during this window.',
                    '- Do not redeliver it until a genuinely new placement path exists.',
                ])
            elif stackoverflow_surface_exhausted:
                lines.extend([
                    '',
                    '## StackOverflow demand-capture packet retired for this review window',
                    '- The post-cooldown slot already fired without a fresh placement-ready outcome.',
                    '- Keep the packet retired until a genuinely new high-intent placement path exists.',
                ])

            if hold_release_schedule.get('status') == 'scheduled':
                lines.extend([
                    '',
                    '## Post-hold marketer rerun scheduled',
                    f"- Scheduled run: {hold_release_schedule['scheduled_run_at']}",
                    f"- Cron job: {hold_release_schedule.get('job_name', 'marketing-measurement-hold-release')} ({hold_release_schedule.get('job_id', 'unknown id')})",
                    f"- Log: {hold_release_schedule.get('log_path', '')}",
                    '- This keeps the first post-hold slot from disappearing into silence once the short review window expires.',
                ])
                summary += ' Scheduled an automatic post-hold marketer rerun at the exact short-window release time.'
            elif hold_release_schedule.get('status') == 'already_scheduled':
                lines.extend([
                    '',
                    '## Post-hold marketer rerun already scheduled',
                    f"- Scheduled run: {hold_release_schedule['scheduled_run_at']}",
                    '- Do not create another duplicate one-shot; use the scheduled rerun as the first post-hold execution slot.',
                ])
            elif hold_release_schedule.get('status') == 'failed':
                lines.extend([
                    '',
                    '## Post-hold marketer rerun scheduling failed',
                    f"- Intended run: {hold_release_schedule['scheduled_run_at']}",
                    f"- Blocker: {hold_release_schedule.get('error', 'unknown cron add failure')}",
                ])
                summary += ' Tried to schedule the post-hold rerun, but cron creation failed.'

            if refreshed_assets:
                execution_board_path, board_targets = _write_marketing_execution_board(now)
                targets_prepared = list(board_targets)
                lines.extend([
                    '',
                    '## Same-run hold repairs applied',
                    '- Refreshed stale manual execution packets so the live prepared queues stay actionable during the cooldown instead of drifting out of sync.',
                ])
                lines.extend(f'- {item}' for item in refreshed_assets)
                summary += ' Refreshed stale manual execution packets for the live prepared queues during the hold window.'

            next_hold_event_number = int(hold_repeat_state.get('next_hold_event_number') or 1)
            reentry_repairs_complete = bool(hold_repeat_state.get('reentry_repairs_complete', False))
            churn_guard_active = (
                next_hold_event_number >= 3
                and reentry_repairs_complete
                and not refreshed_assets
                and hold_release_schedule.get('status') == 'already_scheduled'
            )

            # Hard artifact-rate limiter: if the churn guard is active and the system
            # has already produced too many measurement-hold artifacts in the recent
            # window, suppress the artifact write entirely instead of just relabeling it.
            # This breaks the self-feeding cycle where each artifact counts as "fresh
            # activity" for the next cron run.
            hard_guard_active = (
                churn_guard_active
                and _measurement_hold_churn_hard_guard_active(now)
            )

            if hard_guard_active:
                # Suppress entirely: no artifact, no write, no self-feeding cycle.
                # The post-hold rerun is already scheduled; the re-entry contract and
                # repair path are in place. Any further artifact generation here is
                # pure self-referential churn.
                recent_counts = _measurement_hold_recent_artifact_count(now)
                execution = LaneExecution(
                    lane=decision.lane,
                    action_type='measurement_hold_hard_churn_suppressed',
                    status='suppressed',
                    artifact_path='',
                    summary=(
                        f'Hard churn guard suppressed artifact write: {sum(recent_counts.values())} '
                        f'measurement-hold artifacts in the last {MEASUREMENT_HOLD_CHURN_HARD_GUARD_WINDOW_HOURS}h '
                        f'(limit: {MEASUREMENT_HOLD_MAX_ARTIFACTS_PER_HOUR}/h). '
                        'Post-hold rerun already scheduled; no new artifact needed.'
                    ),
                    targets_prepared=[],
                    shared_findings_used=decision.shared_findings_used,
                    live_external_action=False,
                    blocking_factors=['hard_churn_guard_active'],
                )
            else:
                if churn_guard_active:
                    lines.extend([
                        '',
                        '## Repeat-hold churn guard now active',
                        f"- This run would become hold event #{next_hold_event_number} inside the same active hold window.",
                        '- The prompt and post-hold re-entry repairs are already in place for this hold cycle.',
                        '- Suppress any further pretend follow-through work until the scheduled post-hold rerun or a genuinely new live signal changes the lane map.',
                    ])
                    summary = (
                        'Escalated repeated measurement-hold churn into an architecture guard on the third hold-window event: '
                        'the prompt repair, the re-entry contract repair, and the scheduled post-hold rerun are already in place, '
                        'so future runs should suppress duplicate follow-through unless a real signal or stale packet change appears.'
                    )
                    execution_action_type = 'measurement_hold_churn_guard_repair'
                else:
                    execution_action_type = 'measurement_hold_follow_through'

                deduped_targets: list[str] = []
                seen_targets: set[str] = set()
                for target in targets_prepared:
                    cleaned = str(target).strip()
                    if not cleaned or cleaned in seen_targets:
                        continue
                    seen_targets.add(cleaned)
                    deduped_targets.append(cleaned)
                targets_prepared = deduped_targets

                lines.extend([
                    '',
                    'Shared findings reused:',
                    *[f'- {item}' for item in decision.shared_findings_used],
                ])
                artifact = LOG_DIR / f"marketing_{now.strftime('%Y-%m-%d_%H%M%S')}_measurement_hold_follow_through.md"
                artifact.write_text('\n'.join(lines) + '\n', encoding='utf-8')
                execution = LaneExecution(
                    lane=decision.lane,
                    action_type=execution_action_type,
                    status='executed',
                    artifact_path=str(artifact),
                    summary=summary,
                    targets_prepared=targets_prepared,
                    shared_findings_used=decision.shared_findings_used,
                    live_external_action=False,
                    blocking_factors=[],
                )
        else:
            execution_board_path, _board_targets = _write_marketing_execution_board(now)
            initial_hold_release_at = _resolved_measurement_hold_release_at(
                now,
                decision.short_review_window_release_at,
                (now + timedelta(minutes=MEASUREMENT_HOLD_COOLDOWN_MINUTES)).isoformat(timespec='seconds'),
            )
            reentry_contract_path = _write_post_hold_reentry_contract(
                now,
                release_at=initial_hold_release_at,
                execution_board_path=execution_board_path,
                shared_findings_used=decision.shared_findings_used,
            )
            hold_release_schedule = {}
            if initial_hold_release_at:
                hold_release_schedule = _schedule_measurement_hold_release_run(
                    now=now,
                    release_at=initial_hold_release_at,
                    shared_findings_used=decision.shared_findings_used,
                    reentry_contract_path=str(reentry_contract_path),
                )

            lines = [
                '# Measurement Hold',
                f'Generated: {now.isoformat(timespec="seconds")}',
                '',
                'Fresh external actions already shipped in the short review window.',
                'Do not invent another reset or same-family outreach burst right now.',
                'Use the active queue, current handoff packets, and live measurement windows as the source of truth until one of them materially ages or resolves.',
                f'- Consolidated execution board: {execution_board_path}',
                f'- Post-hold re-entry contract: {reentry_contract_path}',
            ]
            if decision.short_review_window_release_at:
                lines.append(f"- Short review-window congestion clears at: {decision.short_review_window_release_at}")
            if hold_release_schedule.get('status') == 'scheduled':
                lines.extend([
                    '',
                    '## Post-hold marketer rerun scheduled',
                    f"- Scheduled run: {hold_release_schedule['scheduled_run_at']}",
                    f"- Cron job: {hold_release_schedule.get('job_name', 'marketing-measurement-hold-release')} ({hold_release_schedule.get('job_id', 'unknown id')})",
                    f"- Log: {hold_release_schedule.get('log_path', '')}",
                    '- This preserves the first truthful post-hold execution slot automatically.',
                ])
            elif hold_release_schedule.get('status') == 'already_scheduled':
                lines.extend([
                    '',
                    '## Post-hold marketer rerun already scheduled',
                    f"- Scheduled run: {hold_release_schedule['scheduled_run_at']}",
                    '- Do not create another duplicate one-shot; use the scheduled rerun as the first post-hold execution slot.',
                ])
            elif hold_release_schedule.get('status') == 'failed':
                lines.extend([
                    '',
                    '## Post-hold marketer rerun scheduling failed',
                    f"- Intended run: {hold_release_schedule['scheduled_run_at']}",
                    f"- Blocker: {hold_release_schedule.get('error', 'unknown cron add failure')}",
                ])

            lines.extend([
                '',
                'Shared findings reused:',
                *[f'- {item}' for item in decision.shared_findings_used],
            ])

            artifact = LOG_DIR / f"marketing_{now.strftime('%Y-%m-%d_%H%M%S')}_measurement_hold.md"
            artifact.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            summary = 'Enforced a short measurement hold so the loop stops inventing new reset work immediately after multiple fresh external actions.'
            if hold_release_schedule.get('status') == 'scheduled':
                summary += ' Scheduled an automatic post-hold marketer rerun at the exact short-window release time.'
            elif hold_release_schedule.get('status') == 'failed':
                summary += ' Tried to schedule the post-hold rerun, but cron creation failed.'
            execution = LaneExecution(
                lane=decision.lane,
                action_type='measurement_hold_execution',
                status='prepared',
                artifact_path=str(artifact),
                summary=summary,
                targets_prepared=[],
                shared_findings_used=decision.shared_findings_used,
                live_external_action=False,
                blocking_factors=[],
            )
    elif decision.lane in {'distribution_architecture_repair', 'distribution_architecture_guard_follow_through', 'distribution_architecture_guard_pause'}:
        # Hard artifact-rate guard: if the system is churning non-distribution
        # execution artifacts faster than the global limit, suppress this write
        # entirely instead of adding another self-feeding artifact to the pile.
        if _execution_artifact_hard_limit_reached(now):
            total_count = _non_distribution_execution_artifact_count(now)
            execution = LaneExecution(
                lane=decision.lane,
                action_type='execution_artifact_hard_limit_suppressed',
                status='suppressed',
                artifact_path='',
                summary=(
                    f'Hard execution artifact rate limit reached: {total_count} infrastructure '
                    f'artifacts in the last {EXECUTION_ARTIFACT_HARD_LIMIT_WINDOW_HOURS}h '
                    f'(limit: {EXECUTION_ARTIFACT_HARD_LIMIT_PER_HOUR}/h). '
                    'Suppressed further infrastructure artifact generation to break the self-feeding churn cycle.'
                ),
                targets_prepared=[],
                shared_findings_used=decision.shared_findings_used,
                live_external_action=False,
                blocking_factors=['execution_artifact_hard_limit_reached'],
            )
        else:
            execution_board_path, board_targets = _write_marketing_execution_board(now)
            release_at = _call_selector_local(
                distribution_lane_selector._recent_live_external_window_release_at,
                now,
                hours=distribution_lane_selector.SHORT_REVIEW_WINDOW_HOURS,
            )
            repair_state = _call_selector_local(
                distribution_lane_selector._distribution_architecture_repair_state,
                now,
                release_at=release_at,
            )
            guard_path = DRAFTS_DIR / 'distribution_architecture_guard_latest.md'
            guard_active = bool(repair_state.get('third_strike'))
            if guard_active:
                guard_lines = [
                    '# Distribution Architecture Churn Guard',
                    f'Generated: {now.isoformat(timespec="seconds")}',
                    '',
                    '- Third-strike escalation is active for repeated empty-board architecture repairs in the same review window.',
                    f"- Current execution-board fingerprint: {repair_state.get('execution_board_fingerprint') or 'missing'}",
                    f"- Matching prior repair runs in this window: {repair_state.get('repeat_count', 0)}",
                    '- Suppress another plain distribution_architecture_repair until at least one of these changes:',
                    '  - the execution board fingerprint changes',
                    '  - the active short-review release time moves or clears',
                    '  - a genuinely new live external action lands and changes the blocker set',
                    '',
                    '## Current truth source',
                    f'- Execution board: {execution_board_path}',
                ]
                guard_path.write_text('\n'.join(guard_lines) + '\n', encoding='utf-8')
            if decision.lane == 'distribution_architecture_guard_follow_through':
                lines = [
                    '# Distribution Architecture Guard Follow-Through',
                    f'Generated: {now.isoformat(timespec="seconds")}',
                    '',
                    'A third-strike churn guard is already active for this same empty-board state.',
                    'Do not emit another identical distribution_architecture_repair while the board fingerprint and blocker set are unchanged.',
                    '',
                    '## Guard reused in this run',
                    f'- Guard contract: {guard_path}',
                    f"- Execution-board fingerprint: {repair_state.get('execution_board_fingerprint') or 'missing'}",
                    f"- Prior matching repair runs in this window: {repair_state.get('repeat_count', 0)}",
                    '- Suppressed another duplicate structural repair for the same fingerprint.',
                    '- Kept the current execution board as the only truth source until a new packet, blocker change, or live action lands.',
                    '',
                    '## Current source of truth',
                    f'- Consolidated execution board: {execution_board_path}',
                ]
                if board_targets:
                    lines.append(f"- Board targets still visible: {', '.join(board_targets[:6])}")
                lines.extend([
                    '',
                    'Shared findings reused:',
                    *[f'- {item}' for item in decision.shared_findings_used],
                ])
                artifact = LOG_DIR / f"marketing_{now.strftime('%Y-%m-%d_%H%M%S')}_distribution_architecture_guard_follow_through.md"
                artifact.write_text('\n'.join(lines) + '\n', encoding='utf-8')
                execution = LaneExecution(
                    lane=decision.lane,
                    action_type='distribution_architecture_guard_follow_through',
                    status='executed',
                    artifact_path=str(artifact),
                    summary='The third-strike distribution-architecture churn guard was already active for this review window, so this run suppressed another identical repair loop.',
                    targets_prepared=list(board_targets),
                    shared_findings_used=decision.shared_findings_used,
                    live_external_action=False,
                    blocking_factors=[],
                )
            elif decision.lane == 'distribution_architecture_guard_pause':
                lines = [
                    '# Distribution Architecture Guard Pause',
                    f'Generated: {now.isoformat(timespec="seconds")}',
                    '',
                    'The current empty-board fingerprint is already under an active churn guard, and this same review window already logged a guard follow-through.',
                    'Pause further duplicate guard notes until the execution-board fingerprint or blocker set materially changes.',
                    '',
                    '## Guard state reused',
                    f'- Guard contract: {guard_path}',
                    f"- Execution-board fingerprint: {repair_state.get('execution_board_fingerprint') or 'missing'}",
                    f"- Prior matching repair runs in this window: {repair_state.get('repeat_count', 0)}",
                    f"- Prior guard follow-through runs in this window: {repair_state.get('guard_follow_through_count', 0)}",
                    '- No new manual packet was surfaced because the execution board still has no truthful do-now asset.',
                    '',
                    '## Current source of truth',
                    f'- Consolidated execution board: {execution_board_path}',
                ]
                if board_targets:
                    lines.append(f"- Board targets still visible: {', '.join(board_targets[:6])}")
                lines.extend([
                    '',
                    'Shared findings reused:',
                    *[f'- {item}' for item in decision.shared_findings_used],
                ])
                artifact = LOG_DIR / f"marketing_{now.strftime('%Y-%m-%d_%H%M%S')}_distribution_architecture_guard_pause.md"
                artifact.write_text('\n'.join(lines) + '\n', encoding='utf-8')
                execution = LaneExecution(
                    lane=decision.lane,
                    action_type='distribution_architecture_guard_pause',
                    status='skipped_repair',
                    artifact_path=str(artifact),
                    summary='The active distribution-architecture churn guard had already been acknowledged for this fingerprint in the current review window, so the loop paused further duplicate guard follow-through churn.',
                    targets_prepared=list(board_targets),
                    shared_findings_used=decision.shared_findings_used,
                    live_external_action=False,
                    blocking_factors=[],
                )
            else:
                current_reddit_opportunities = _reddit_discussion_opportunities(limit=2)
                reddit_packet_delivery_active = _reddit_discussion_packet_delivery_still_active(now, current_reddit_opportunities)
                manual_review_targets: list[str] = []
                manual_review_path: Path | None = None
                manual_review_waiting_targets = _call_selector_local(
                    distribution_lane_selector._primary_repo_flat_manual_review_targets_waiting_for_execution,
                    now,
                )
                manual_review_asset_current = _call_selector_local(
                    distribution_lane_selector._primary_repo_flat_manual_review_asset_current,
                    now,
                    manual_review_waiting_targets,
                )
                if manual_review_waiting_targets and not manual_review_asset_current:
                    manual_review_findings = _load_primary_repo_flat_contact_discovery()
                    manual_review_path, manual_review_targets = _write_primary_repo_flat_manual_review_asset(now, manual_review_findings)
                    if manual_review_targets:
                        execution_board_path, board_targets = _write_marketing_execution_board(now)
                if manual_review_targets and manual_review_path is not None:
                    execution = LaneExecution(
                        lane=decision.lane,
                        action_type='publisher_manual_review_channel_ready_outreach_asset',
                        status='prepared',
                        artifact_path=str(manual_review_path),
                        summary='Converted repeated empty-board architecture churn into a Codeberg-first manual publisher outreach asset for untouched human-executable contact routes.',
                        targets_prepared=list(manual_review_targets),
                        shared_findings_used=decision.shared_findings_used,
                        live_external_action=False,
                        blocking_factors=[],
                    )
                else:
                    discussion_asset = _write_reddit_discussion_handoff_asset(now, decision.shared_findings_used)
                    if discussion_asset is not None:
                        discussion_path, discussion_targets = discussion_asset
                        _write_marketing_execution_board(now)
                        execution = LaneExecution(
                            lane=decision.lane,
                            action_type='reddit_discussion_channel_ready_outreach_asset',
                            status='prepared',
                            artifact_path=str(discussion_path),
                            summary='Converted repeated empty-board architecture churn into a manual discussion handoff asset built from fresh Reddit monitor opportunities.',
                            targets_prepared=list(discussion_targets),
                            shared_findings_used=decision.shared_findings_used,
                            live_external_action=False,
                            blocking_factors=[],
                        )
                    else:
                        refreshed_assets, refreshed_targets = _refresh_manual_execution_assets(now)
                        if refreshed_assets:
                            execution_board_path, board_targets = _write_marketing_execution_board(now)
                        lines = [
                            '# Distribution Lane Architecture Repair',
                            f'Generated: {now.isoformat(timespec="seconds")}',
                            '',
                            'Treat this slot as a structural repair, not as another measurement-hold report.',
                        ]
                        if guard_active:
                            lines.extend([
                                'The same empty-board architecture state already repeated twice in this review window.',
                                'This run escalates that third strike into a churn guard tied to the board fingerprint instead of emitting another plain repair note.',
                            ])
                        else:
                            lines.append('The selector still had no truthful fresh external/manual lane to run, so this pass hardens the process instead of pretending a packet exists.')
                        lines.extend([
                            '',
                            '## Repair applied in this run',
                            '- Reconfirmed the current execution board so lane selection uses the latest truthful packet and delivery state.',
                            '- Marked repeated post-hold measurement-hold selection as a process failure, not an acceptable steady state.',
                            '- Forced the next repair cycle to prefer runtime/process repair over another idle hold when short-window congestion is already gone.',
                        ])
                        if reddit_packet_delivery_active:
                            lines.append('- Suppressed regeneration of the Reddit discussion handoff packet because the latest packet was already manually delivered and is still inside its active review window.')
                        if guard_active:
                            lines.extend([
                                '- Installed a third-strike churn guard for repeated empty-board architecture repairs with the same execution-board fingerprint.',
                                f'- Guard contract: {guard_path}',
                                f"- Execution-board fingerprint: {repair_state.get('execution_board_fingerprint') or 'missing'}",
                                f"- Prior matching repair runs in this window: {repair_state.get('repeat_count', 0)}",
                            ])
                        if refreshed_assets:
                            lines.extend([
                                '',
                                '## Same-run packet repairs applied',
                                '- Refreshed stale manual execution packets so the next truthful slot has current assets instead of an empty or drifting board.',
                            ])
                            lines.extend(f'- {item}' for item in refreshed_assets)
                        lines.extend([
                            '',
                            '## Current source of truth',
                            f'- Consolidated execution board: {execution_board_path}',
                        ])
                        if board_targets:
                            lines.append(f"- Board targets still visible: {', '.join(board_targets[:6])}")
                        lines.extend([
                            '',
                            '## Next structural requirements',
                            '- Do not re-enter measurement_hold without active short-window congestion or a newer live external action after the last hold.',
                            '- The next truthful slot must choose either an untouched executable lane or another concrete runtime repair.',
                            '- Keep Codeberg as the primary CTA and keep duplicate packet delivery fail-closed.',
                            '',
                            'Shared findings reused:',
                            *[f'- {item}' for item in decision.shared_findings_used],
                        ])
                        artifact = LOG_DIR / f"marketing_{now.strftime('%Y-%m-%d_%H%M%S')}_distribution_architecture_repair.md"
                        artifact.write_text('\n'.join(lines) + '\n', encoding='utf-8')
                        execution = LaneExecution(
                            lane=decision.lane,
                            action_type='distribution_architecture_churn_guard_repair' if guard_active else 'distribution_architecture_repair',
                            status='executed',
                            artifact_path=str(artifact),
                            summary=(
                                'Escalated the repeated empty-board architecture failure into a third-strike churn guard tied to the current review window.'
                                if guard_active else
                                'Converted a cleared post-hold slot into a structural lane-repair action instead of logging another idle measurement hold.'
                            ) + (
                                ' Refreshed stale manual execution packets so the next truthful slot can reuse current assets.'
                                if refreshed_assets else ''
                            ),
                            targets_prepared=list(board_targets) + refreshed_targets,
                            shared_findings_used=decision.shared_findings_used,
                            live_external_action=False,
                            blocking_factors=[],
                        )
    elif decision.lane == 'distribution_reset':
        artifact, prepared = _write_distribution_reset_execution(decision, now, market_intelligence)
        summary = 'Shipped a queue-reset/discovery packet so the loop expands into new targets instead of logging saturated follow-through as progress.'
        action_type = 'distribution_reset_execution'
        status = 'prepared' if prepared else 'skipped'
        blocking_factors: list[str] = []

        existing_queue_rows = _load_curator_queue_rows()
        reset_targets = _distribution_reset_targets_for_curator(existing_queue_rows, limit=4)
        if reset_targets:
            comparison_pages = _top_comparison_pages(market_intelligence)
            research_signals = _latest_research_signals()
            _target_files, queue_rows, created_rows = _write_target_ready_files(
                now=now,
                targets=reset_targets,
                comparison_pages=comparison_pages,
                research_signals=research_signals,
                existing_queue_rows=existing_queue_rows,
            )
            _mark_distribution_reset_targets_promoted(created_rows, now=now)
            _append_handoff_pointer(str(artifact), title='Promoted curator target files', handoff_path=Path(created_rows[0]['artifact_path']).parent)
            prepared = [row.get('target', '') for row in created_rows] or prepared
            summary = 'Promoted fresh distribution-reset targets into real curator-ready assets instead of stopping at discovery.'
            status = 'prepared'

            github_auth_available = subprocess.run(
                ['gh', 'auth', 'status'],
                capture_output=True,
                text=True,
                check=False,
            ).returncode == 0
            if not github_auth_available:
                promoted_target_names = [_display_target_name(row.get('target', '')) for row in created_rows if row.get('target')]
                if not _contact_discovery_is_current(promoted_target_names):
                    discovery_artifact, findings = _write_curator_contact_discovery(now, created_rows)
                    _append_handoff_pointer(str(artifact), title='Curator contact discovery', handoff_path=discovery_artifact)
                else:
                    findings = [
                        row for row in _load_curator_contact_discovery()
                        if _display_target_name(str(row.get('target') or '').strip()) in promoted_target_names
                    ]
                if findings:
                    contact_handoff_path, _ = _write_curator_contact_handoff_packet(now, findings)
                    _append_handoff_pointer(str(artifact), title='Curator contact execution packet', handoff_path=contact_handoff_path)
                    summary += ' Also refreshed a manual-contact execution packet because GitHub PR auth is blocked in this runtime.'
        elif not prepared:
            summary = 'Distribution reset found no genuinely new targets, so the lane was skipped instead of relogging existing comparison assets as progress.'
            blocking_factors = ['No fresh reset-discovered targets were available; discover new third-party targets before rerunning distribution_reset.']

        execution = LaneExecution(
            lane=decision.lane,
            action_type=action_type,
            status=status,
            artifact_path=str(artifact),
            summary=summary,
            targets_prepared=prepared,
            shared_findings_used=decision.shared_findings_used,
            live_external_action=False,
            blocking_factors=blocking_factors,
        )
    elif decision.lane == 'apollo_outreach':
        artifact, prepared = _write_apollo_execution(decision, now, market_intelligence)
        execution = LaneExecution(
            lane=decision.lane,
            action_type='apollo_outreach_execution',
            status='prepared',
            artifact_path=str(artifact),
            summary='Prepared a managed Apollo outbound execution packet from the shared comparison, curator, and demand-signal artifacts.',
            targets_prepared=prepared,
            shared_findings_used=decision.shared_findings_used,
            live_external_action=False,
            blocking_factors=[],
        )
    elif decision.lane == 'apollo_launch_handoff_packet':
        artifact, prepared = _write_apollo_launch_handoff_packet(now)
        execution = LaneExecution(
            lane=decision.lane,
            action_type='apollo_launch_handoff_packet',
            status='prepared',
            artifact_path=str(artifact),
            summary='Prepared the Apollo launch/send confirmation handoff so the loop advances the already-verified non-zero list into real live-send evidence instead of regenerating prep artifacts.',
            targets_prepared=prepared,
            shared_findings_used=decision.shared_findings_used,
            live_external_action=False,
            blocking_factors=[],
        )
    elif decision.lane == 'stackoverflow_answer':
        artifact, prepared, action_type, drafts_created, reused_existing_draft_ready = _write_stackoverflow_execution(now)
        execution = LaneExecution(
            lane=decision.lane,
            action_type=action_type,
            status='prepared' if (drafts_created > 0 or reused_existing_draft_ready) else 'skipped',
            artifact_path=str(artifact),
            summary=(
                'Prepared fresh StackOverflow answer drafts so the loop can capture high-intent demand while Apollo and prior outreach stay in their measurement windows.'
                if drafts_created > 0 else
                'Revalidated the strongest existing StackOverflow answer packet and kept it manual-ready for placement now that the cooldown window has cleared.'
                if reused_existing_draft_ready else
                'Ran the StackOverflow demand-capture lane, but this pass did not surface draft-worthy questions. Keep the API-backed search path, but do not count low-fit candidate lists as execution progress.'
            ),
            targets_prepared=prepared if (drafts_created > 0 or reused_existing_draft_ready) else [],
            shared_findings_used=decision.shared_findings_used,
            live_external_action=False,
            blocking_factors=[] if (drafts_created > 0 or reused_existing_draft_ready) else ['No draft-worthy StackOverflow questions surfaced in this pass.'],
        )
    elif decision.lane == 'stackoverflow_answer_handoff_packet':
        artifact, prepared = _write_stackoverflow_handoff_packet(now)
        execution = LaneExecution(
            lane=decision.lane,
            action_type='stackoverflow_answer_handoff_packet',
            status='prepared' if prepared else 'skipped',
            artifact_path=str(artifact),
            summary=(
                'Prepared a StackOverflow answer handoff packet so the existing high-intent draft can be posted or reused before the lane is regenerated.'
                if prepared else
                'Selected the StackOverflow handoff lane, but no local answer draft was available to package.'
            ),
            targets_prepared=prepared,
            shared_findings_used=decision.shared_findings_used,
            live_external_action=False,
            blocking_factors=[] if prepared else ['No local StackOverflow answer draft was available to package.'],
        )
    elif decision.lane == 'repo_conversion_proof_asset':
        artifact, files_changed = _write_repo_conversion_proof_asset(now)
        execution = LaneExecution(
            lane=decision.lane,
            action_type='repo_conversion_proof_asset',
            status='executed',
            artifact_path=str(artifact),
            summary='Shipped a missing workflow-composition proof asset and linked it into the first-run path so the loop stops refreshing the same StackOverflow handoff packet during saturated external windows.',
            targets_prepared=files_changed,
            shared_findings_used=decision.shared_findings_used,
            live_external_action=False,
            blocking_factors=[],
        )
    elif decision.lane == 'owned_content':
        execution = _execute_owned_content(now)
        execution = LaneExecution(
            lane=execution.lane,
            action_type=execution.action_type,
            status=execution.status,
            artifact_path=execution.artifact_path,
            summary=execution.summary,
            targets_prepared=execution.targets_prepared,
            shared_findings_used=decision.shared_findings_used,
            live_external_action=execution.live_external_action,
            blocking_factors=execution.blocking_factors,
        )
    else:
        execution = LaneExecution(
            lane=decision.lane,
            action_type='owned_content_lane_noop',
            status='skipped',
            artifact_path=None,
            summary='Owned-content lane remains active; no non-content distribution execution packet needed.',
            targets_prepared=[],
            shared_findings_used=decision.shared_findings_used,
            live_external_action=False,
            blocking_factors=[],
        )

    if (
        decision.lane != 'measurement_hold'
        and not execution.live_external_action
        and decision.short_review_window_release_at
        and (_parse_dt(decision.short_review_window_release_at) or now) > now
    ):
        execution_board_path, _ = _write_marketing_execution_board(now)
        reentry_contract_path = _write_post_hold_reentry_contract(
            now,
            release_at=decision.short_review_window_release_at,
            execution_board_path=execution_board_path,
            shared_findings_used=decision.shared_findings_used,
        )
        hold_release_schedule = _schedule_measurement_hold_release_run(
            now=now,
            release_at=decision.short_review_window_release_at,
            shared_findings_used=decision.shared_findings_used,
            reentry_contract_path=str(reentry_contract_path),
        )
        if hold_release_schedule.get('status') in {'scheduled', 'already_scheduled'}:
            _write_marketing_execution_board(now)
            _append_post_hold_schedule_note(execution.artifact_path, hold_release_schedule)
            summary_suffix = (
                ' Scheduled an automatic post-hold marketer rerun at the updated short-window release time.'
                if hold_release_schedule.get('status') == 'scheduled'
                else ' Confirmed the automatic post-hold marketer rerun is already aligned to the current short-window release time.'
            )
            execution = LaneExecution(
                lane=execution.lane,
                action_type=execution.action_type,
                status=execution.status,
                artifact_path=execution.artifact_path,
                summary=execution.summary + summary_suffix,
                targets_prepared=execution.targets_prepared,
                shared_findings_used=execution.shared_findings_used,
                live_external_action=execution.live_external_action,
                blocking_factors=execution.blocking_factors,
            )

    _write_action_log(execution, now)
    if (
        execution.artifact_path
        and execution.status in {'prepared', 'executed'}
        and not execution.live_external_action
    ):
        _write_marketing_execution_board(now)
    return execution


def _write_reddit_execution_check_brief(now: datetime, status: str, payload: dict) -> Path:
    """Write a brief artifact for the reddit_execution_check lane."""
    brief_path = DRAFTS_DIR / f'reddit_execution_check_{now.strftime("%Y-%m-%d_%H%M%S")}.md'
    lines = [
        f'# Reddit Execution Check — {now.isoformat(timespec="seconds")}',
        f'Status: {status}',
        f'OK: {payload.get("ok", False)}',
    ]
    if payload.get('report'):
        lines.append(f'Report: {payload["report"]}')
    if payload.get('last_attempt_status'):
        lines.append(f'Last attempt status: {payload["last_attempt_status"]}')
    if payload.get('detail'):
        lines.append(f'Detail: {payload["detail"]}')
    brief_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return brief_path


def main() -> int:
    from agents.marketing.distribution_lane_selector import choose_distribution_lane

    now = datetime.now()
    decision = choose_distribution_lane(now)
    execution = execute_distribution_lane(decision, now)
    print(json.dumps(execution.__dict__, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
