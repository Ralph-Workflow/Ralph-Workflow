#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path('/home/mistlight/.openclaw/workspace')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.marketing.positioning import FOUR_QUESTIONS as POSITIONING_QUESTIONS

OUT_DIR = ROOT / 'agents/marketing/logs'
AUDIT_MD = OUT_DIR / 'marketing_workflow_audit_latest.md'
AUDIT_JSON = OUT_DIR / 'marketing_workflow_audit_latest.json'
OUTREACH = ROOT / 'outreach-log.md'
ADOPTION = OUT_DIR / 'adoption_metrics_latest.json'
RETRO = OUT_DIR / 'reddit_post_analysis.json'
PRINCIPLES = ROOT / 'agents/marketing/MARKETING_WORKFLOW_PRINCIPLES.md'
FOUR_QUESTIONS_DOC = ROOT / 'agents/marketing/FOUR_MARKETING_QUESTIONS.md'
SELF_IMPROVEMENT_DOC = ROOT / 'agents/marketing/MARKETING_SELF_IMPROVEMENT.md'
APOLLO_SEQUENCE_STATUS = OUT_DIR / 'apollo_sequence_status_latest.json'
APOLLO_STATUS = OUT_DIR / 'apollo_status.json'
OUTCOME_CAPABILITY_STATUS = OUT_DIR / 'outcome_capability_latest.json'
REDDIT_MONITOR_LATEST = ROOT / 'seo-reports/reddit_monitor_latest.md'
REDDIT_EXECUTION_STATUS = OUT_DIR / 'reddit_execution_status_latest.json'

REAL_REPLACEMENT_ACTION_TYPES = {
    'thenextai_free_listing_submission',
    'toolshelf_free_listing_submission',
    'aigearbase_free_listing_submission',
    'apollo_sequence_launch',
    'curator_email_outreach',
    'publisher_email_outreach',
    'publisher_contact_form_submission',
    'repo_conversion_asset',
    'comparison_conversion_asset',
    'repo_conversion_comparison_asset',
}

SYSTEM_DESIGN_REPAIR_ACTION_TYPES = {
    'distribution_architecture_repair',
    'distribution_architecture_churn_guard_repair',
    'measurement_hold_churn_guard_repair',
    'measurement_hold_release_reschedule_repair',
    'post_hold_release_prompt_guard_repair',
    'measurement_hold_release_delivery_route_repair',
    'apollo_truthfulness_repair',
    'apollo_cloudflare_truthfulness_repair',
    'apollo_runtime_truth_repair',
    'apollo_followup_truth_repair',
}

LIVE_EXTERNAL_STATUSES = {
    'executed',
    'sent',
    'submitted',
    'published',
    'launched',
}

QUEUE_HOUSEKEEPING_ACTION_TYPES = {
    'curator_queue_follow_through',
    'curator_handoff_follow_through',
    'curator_contact_handoff_follow_through',
    'comparison_backlink_follow_through',
}

LOW_SIGNAL_EXECUTION_MARKERS = {
    'record count was 0',
    '0 right after creation',
    '0 records',
    'zero records',
    'needs a second-pass check',
    'needs a second pass check',
    'import path likely needs a second-pass check',
}
LOW_SIGNAL_STATUS_MARKERS = {
    'pending_manual_approval',
    'pending_moderation',
    'awaiting_moderation',
    'under_review',
}
FAMILY_BURST_WINDOW_HOURS = 24
DIRECTORY_SUBMISSION_BURST_THRESHOLD = 4
CURATOR_OUTREACH_BURST_THRESHOLD = 6
PUBLISHER_OUTREACH_BURST_THRESHOLD = 4
PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_WINDOW_HOURS = 48
PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_THRESHOLD = 2


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def reddit_execution_status_path() -> Path:
    if REDDIT_EXECUTION_STATUS.parent == OUT_DIR:
        return REDDIT_EXECUTION_STATUS
    return OUT_DIR / 'reddit_execution_status_latest.json'


def recent_live_action_family_count(now: datetime, *, family: str, hours: int = FAMILY_BURST_WINDOW_HOURS) -> int:
    cutoff = now.timestamp() - (hours * 3600)
    total = 0
    for path in OUT_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        payload = load_json(path)
        result = payload.get('result') or {}
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
        elif family == 'publisher_outreach':
            if 'publisher_outreach' in name or 'publisher_contact' in name:
                total += 1
    return total


def _coerce_action_fields(action: Any, action_type: Any) -> tuple[str, str]:
    if isinstance(action, str):
        normalized_type = action.strip()
        return normalized_type, normalized_type.replace('_', ' ')
    if isinstance(action, dict):
        normalized_type = str(
            action.get('type')
            or action.get('name')
            or action_type
            or ''
        ).strip()
        normalized_title = str(
            action.get('title')
            or action.get('summary')
            or normalized_type.replace('_', ' ')
        ).strip()
        return normalized_type, normalized_title
    normalized_type = str(action_type or '').strip()
    return normalized_type, normalized_type.replace('_', ' ')


def normalize_marketing_action(payload: dict, path: Path) -> dict:
    if not payload:
        return {}
    payload_action_type = str(
        payload.get('action_type')
        or payload.get('type')
        or payload.get('action')
        or ''
    ).strip()
    chosen_action = payload.get('chosen_action')
    if chosen_action:
        normalized = dict(payload)
        if isinstance(chosen_action, str):
            normalized['chosen_action'] = {
                'type': str(payload.get('type') or '').strip(),
                'channel': str(payload.get('channel_family') or payload.get('primary_goal') or 'internal_conversion').strip(),
                'title': str(payload.get('title') or chosen_action).strip(),
                'url': '',
                'summary': chosen_action.strip(),
            }
        elif not isinstance(chosen_action, dict):
            return {}
        result = dict(payload.get('result') or {})
        result.setdefault('ok', bool(payload.get('ok')))
        result.setdefault('status', str(payload.get('status') or ''))
        result.setdefault('live_external_action', bool(payload.get('live_external_action', False)))
        if 'outcome_ready' in payload and 'outcome_ready' not in result:
            result['outcome_ready'] = bool(payload.get('outcome_ready'))
        normalized['result'] = result
        normalized['_path'] = str(path)
        return normalized

    distribution_execution = payload.get('distribution_execution') or {}
    distribution_lane = payload.get('distribution_lane') or {}
    if distribution_execution and distribution_lane:
        return {
            'chosen_action': {
                'type': distribution_execution.get('action_type', ''),
                'channel': distribution_lane.get('lane', ''),
                'title': f"Distribution lane execution: {distribution_lane.get('lane', '')}",
                'url': distribution_execution.get('artifact_path'),
            },
            'result': {
                'ok': distribution_execution.get('status') in LIVE_EXTERNAL_STATUSES or distribution_execution.get('status') in {'prepared', 'executed', 'skipped_repair'},
                'status': distribution_execution.get('status', ''),
                'live_external_action': bool(distribution_execution.get('live_external_action', False)),
                'blocking_factors': distribution_execution.get('blocking_factors', []) or [],
                'notes': [distribution_execution.get('summary', '')] if distribution_execution.get('summary') else [],
            },
            '_path': str(path),
        }

    action = payload.get('action')
    action_type = payload.get('type') or payload.get('action_type')
    normalized_action_type, normalized_action_title = _coerce_action_fields(action, action_type)
    raw_channel = payload.get('channel')
    channel = raw_channel if isinstance(raw_channel, dict) else {}
    response = channel.get('response') or {}
    submitted_payload = payload.get('submitted_payload') or {}
    effective_channel = (
        channel.get('name')
        or channel.get('submit_page')
        or (raw_channel if isinstance(raw_channel, str) else '')
        or 'distribution'
    )
    effective_url = (
        submitted_payload.get('website_url')
        or submitted_payload.get('url')
        or channel.get('submit_page')
        or payload.get('submit_url')
        or payload.get('confirmation_url')
    )
    payload_status = str(payload.get('status', '')).lower()
    executed = (
        'executed' in payload_action_type.lower()
        or payload_status in LIVE_EXTERNAL_STATUSES
        or normalized_action_type in {'curator_email_outreach', 'publisher_email_outreach', 'publisher_contact_form_submission'}
    )
    response_ok = (
        int(response.get('http_status', 0) or 0) == 200
        or bool(payload.get('ok'))
        or payload_status in LIVE_EXTERNAL_STATUSES
    )
    inferred_live_external = bool(
        payload.get('live_external_action')
        or payload_status in {'sent', 'submitted', 'published', 'launched'}
        or submitted_payload
        or payload.get('submit_url')
        or payload.get('confirmation_url')
        or channel.get('submit_page')
        or normalized_action_type == 'curator_email_outreach'
    )
    if normalized_action_type and executed and response_ok:
        return {
            'chosen_action': {
                'type': normalized_action_type,
                'channel': effective_channel,
                'title': normalized_action_title,
                'url': effective_url,
            },
            'result': {
                'ok': True,
                'status': payload_status or 'executed',
                'live_external_action': inferred_live_external,
            },
            '_path': str(path),
        }
    return {}


def has_measurement_pending_marker(outreach_text: str) -> bool:
    lowered = outreach_text.lower()
    return (
        'no further same-run repairs are available' in lowered
        or 'what remains open (not a repair failure — a measurement window problem)' in lowered
        or 'what remains open (not a repair failure - a measurement window problem)' in lowered
    )


def build_repair_action(*, measurement_pending: bool, target_tactic: str, failure_type: str, action: str, kill_condition: str, success_metric: str, priority: int, repair_kind: str = 'tactic', previous_repair: dict | None = None) -> dict:
    repair_state = 'pending_measurement' if measurement_pending else 'needs_execution'
    preserved_pending = bool(previous_repair and previous_repair.get('repair_state') == 'pending_measurement')
    if preserved_pending:
        repair_state = 'pending_measurement'

    payload = {
        'target_tactic': target_tactic,
        'failure_type': failure_type,
        'repair_kind': repair_kind,
        'action': action,
        'kill_condition': kill_condition,
        'success_metric': success_metric,
        'priority': priority,
        'repair_state': repair_state,
    }
    if preserved_pending and previous_repair.get('repair_acknowledged_at'):
        payload['repair_acknowledged_at'] = previous_repair['repair_acknowledged_at']
    return payload


def previous_repair_map(payload: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    repairs = payload.get('repair_actions', []) or []
    mapped: dict[tuple[str, str], dict[str, Any]] = {}
    for repair in repairs:
        mapped[(repair.get('failure_type', ''), repair.get('target_tactic', ''))] = repair
    return mapped


def recent_prepared_primary_repo_flat_packet_count(now: datetime, *, hours: int = PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_WINDOW_HOURS) -> int:
    cutoff = now.timestamp() - (hours * 3600)
    total = 0
    for path in OUT_DIR.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        payload = load_json(path)
        if ((payload.get('chosen_action') or {}).get('type') or '') != 'primary_repo_flat_contact_handoff_packet_execution':
            continue
        result = payload.get('result') or {}
        if str(result.get('status') or payload.get('status') or '').strip().lower() != 'prepared':
            continue
        if bool(result.get('live_external_action') or payload.get('live_external_action')):
            continue
        total += 1
    return total


def load_reddit_channel_state() -> dict[str, bool]:
    if not REDDIT_MONITOR_LATEST.exists():
        return {'reddit_blocked': False, 'provider_degraded': False}
    text = REDDIT_MONITOR_LATEST.read_text(encoding='utf-8')
    text_l = text.lower()
    diagnostics: dict[str, int] = {}
    if '**Search diagnostics:**' in text:
        try:
            diagnostics_line = text.split('**Search diagnostics:**', 1)[1].splitlines()[0].strip()
        except Exception:
            diagnostics_line = ''
        for part in diagnostics_line.split(','):
            key, _, value = part.strip().partition('=')
            if key and value.isdigit():
                diagnostics[key] = int(value)
    shortlisted = 0
    if '**Shortlisted:**' in text:
        try:
            shortlisted = int(text.split('**Shortlisted:**', 1)[1].splitlines()[0].strip())
        except Exception:
            shortlisted = 0

    partial_reddit_blocking = diagnostics.get('reddit_ip_blocked', 0) > 0 and (diagnostics.get('ok', 0) > 0 or shortlisted > 0)
    reddit_blocked = (
        (
            'reddit is ip-blocked from this server' in text_l
            or 'reddit api calls return http 403' in text_l
            or 'reddit_ip_blocked' in text_l
            or ('partial coverage' in text_l and 'reddit queries were blocked' in text_l)
            or ('partial visibility' in text_l and 'fails closed on posting' in text_l)
            or ('coverage is still partial' in text_l and 'fail closed on posting' in text_l)
            or ('partial visibility only. fail closed.' in text_l)
        )
        and not partial_reddit_blocking
    )
    provider_degraded = (
        'provider_challenge=' in text_l
        or ('search diagnostics:' in text_l and 'provider' in text_l)
        or 'partial coverage' in text_l
        or 'partial visibility' in text_l
    )

    runtime_status_path = reddit_execution_status_path()
    if runtime_status_path.exists():
        runtime = load_json(runtime_status_path)
        runtime_status = str(runtime.get('status') or '').strip().lower()
        runtime_timestamp = runtime.get('generated_at') or runtime.get('timestamp')
        runtime_recent = False
        if runtime_timestamp:
            try:
                runtime_dt = datetime.fromisoformat(str(runtime_timestamp).replace('Z', '+00:00'))
                if runtime_dt.tzinfo is None:
                    runtime_dt = runtime_dt.astimezone()
                runtime_recent = (datetime.now().astimezone() - runtime_dt) <= timedelta(hours=12)
            except ValueError:
                runtime_recent = False
        if runtime_recent and runtime_status == 'browser_session_ready':
            reddit_blocked = False
        elif runtime_recent and runtime_status in {'network_security_blocked', 'execution_blocked', 'not_logged_in'}:
            reddit_blocked = True

    return {
        'reddit_blocked': reddit_blocked,
        'provider_degraded': provider_degraded,
    }


def load_latest_marketing_action(*, prefer_meaningful: bool = True) -> dict:
    candidates = [
        path for path in OUT_DIR.glob('marketing_*.json')
        if '_latest' not in path.name
        and 'workflow_audit' not in path.name
        and 'loop_runner' not in path.name
        and 'loop_verifier' not in path.name
        and 'independent_verification' not in path.name
        and 'momentum_watchdog' not in path.name
        and 'positioning_audit' not in path.name
    ]
    normalized: list[tuple[float, bool, dict]] = []
    for path in candidates:
        payload = normalize_marketing_action(load_json(path), path)
        if payload and payload.get('chosen_action'):
            normalized.append((path.stat().st_mtime, bool((payload.get('result') or {}).get('live_external_action')), payload))

    if not normalized:
        return {}

    if not prefer_meaningful:
        return max(normalized, key=lambda entry: entry[0])[2]

    meaningful_actions = [
        entry for entry in normalized
        if entry[1] or ((entry[2].get('chosen_action') or {}).get('type') in REAL_REPLACEMENT_ACTION_TYPES)
    ]
    if meaningful_actions:
        return max(meaningful_actions, key=lambda entry: entry[0])[2]
    return max(normalized, key=lambda entry: entry[0])[2]


def action_outcome_ready(payload: dict) -> tuple[bool, str | None]:
    if not payload:
        return False, None

    result = payload.get('result') or {}
    status = str(payload.get('status') or result.get('status') or '').strip().lower()
    if bool(result.get('confirmation_required')) or 'pending_email_confirmation' in status:
        return False, 'Live execution is still blocked on email confirmation before the public action is actually approved.'
    if bool(result.get('manual_approval_pending')) or any(marker in status for marker in LOW_SIGNAL_STATUS_MARKERS):
        return False, 'Live execution is still pending manual approval/moderation, so it should not count as visible outcome movement yet.'

    explicit = result.get('outcome_ready')
    if explicit is not None:
        return bool(explicit), result.get('outcome_warning')

    evidence_chunks: list[str] = []
    for key in ('notes', 'evidence', 'blocking_factors'):
        value = result.get(key)
        if isinstance(value, list):
            evidence_chunks.extend(str(item) for item in value)
        elif value:
            evidence_chunks.append(str(value))
    evidence_text = ' '.join(evidence_chunks).lower()
    if any(marker in evidence_text for marker in LOW_SIGNAL_EXECUTION_MARKERS):
        return False, 'Live execution evidence says the outbound asset is not usable yet.'

    chosen_type = ((payload.get('chosen_action') or {}).get('type') or '').strip()
    counts_as_replacement = bool(result.get('live_external_action')) or chosen_type in REAL_REPLACEMENT_ACTION_TYPES
    if bool(result.get('ok')) and counts_as_replacement:
        return True, None
    return False, None


def outcome_capability_shipped(payload: dict, now: datetime, apollo_status: dict | None = None) -> bool:
    if not payload:
        return False
    lane = str(payload.get('selected_lane') or '').strip()
    cta = str(((payload.get('direct_codeberg_linkage') or {}).get('cta') or payload.get('codeberg_primary') or '')).strip()
    timestamp_raw = str(payload.get('timestamp') or '').strip()
    if not lane or not cta.endswith('/Ralph-Workflow'):
        return False
    if lane == 'apollo_outreach':
        apollo_status = apollo_status or {}
        apollo_state = str(apollo_status.get('status') or '').strip().lower()
        if apollo_status.get('cloudflare_blocked') or 'cloudflare' in apollo_state or 'auth_blocked' in apollo_state or 'login_failed' in apollo_state:
            return False
    try:
        timestamp = datetime.fromisoformat(timestamp_raw.replace('Z', '+00:00')) if timestamp_raw else None
    except ValueError:
        timestamp = None
    if timestamp is not None and timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone().replace(tzinfo=None)
    if timestamp is not None and timestamp < now.replace(tzinfo=None) - timedelta(days=7):
        return False
    return str(payload.get('status') or '').strip().lower() == 'executed'


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    adoption = load_json(ADOPTION)
    retro = load_json(RETRO)
    outreach_text = OUTREACH.read_text(encoding='utf-8') if OUTREACH.exists() else ''
    latest_action = load_latest_marketing_action()
    latest_activity = load_latest_marketing_action(prefer_meaningful=False)
    apollo_sequence_status = load_json(APOLLO_SEQUENCE_STATUS)
    apollo_status = load_json(APOLLO_STATUS)
    outcome_capability = load_json(OUTCOME_CAPABILITY_STATUS)
    reddit_channel_state = load_reddit_channel_state()
    previous_audit = load_json(AUDIT_JSON)
    prior_repairs = previous_repair_map(previous_audit)
    now = datetime.now()

    articles = (
        outreach_text.count('**write.as article**')
        + outreach_text.count('Live URL: https://telegra.ph/')
        + outreach_text.count('(telegraph)')
    )
    reddit_posts = len(retro.get('recent_posts', []))
    repeated_openings = retro.get('repeated_openings', [])
    metrics = adoption.get('metrics', [])
    recent_window = adoption.get('recent_window', {})
    adoption_eval = adoption.get('evaluation', {})

    codeberg_window = recent_window.get('Codeberg', {})
    github_window = recent_window.get('GitHub', {})
    codeberg_flat = codeberg_window.get('samples', 0) >= 3 and all(codeberg_window.get(k, 0) == 0 for k in ('stars_delta_window', 'watchers_delta_window', 'forks_delta_window'))
    github_flat = github_window.get('samples', 0) >= 3 and all(github_window.get(k, 0) == 0 for k in ('stars_delta_window', 'watchers_delta_window', 'forks_delta_window'))
    repetitive_reddit = bool(repeated_openings)
    reddit_blocked = bool(reddit_channel_state.get('reddit_blocked'))
    measurement_pending = has_measurement_pending_marker(outreach_text)
    recent_directory_submissions = recent_live_action_family_count(now, family='directory_submission')
    recent_curator_outreach = recent_live_action_family_count(now, family='curator_outreach')
    recent_publisher_outreach = recent_live_action_family_count(now, family='publisher_outreach')
    directory_submission_burst = recent_directory_submissions >= DIRECTORY_SUBMISSION_BURST_THRESHOLD
    curator_outreach_burst = recent_curator_outreach >= CURATOR_OUTREACH_BURST_THRESHOLD
    publisher_outreach_burst = recent_publisher_outreach >= PUBLISHER_OUTREACH_BURST_THRESHOLD
    recent_action = latest_action.get('chosen_action', {}) if latest_action else {}
    recent_action_result = latest_action.get('result', {}) if latest_action else {}
    recent_action_ok = bool(recent_action_result.get('ok'))
    recent_action_type = (latest_action.get('chosen_action', {}) or {}).get('type', '') if latest_action else ''
    recent_action_live_external = bool(recent_action_result.get('live_external_action'))
    recent_action_outcome_ready, recent_action_warning = action_outcome_ready(latest_action)

    latest_activity_action = latest_activity.get('chosen_action', {}) if latest_activity else {}
    latest_activity_result = latest_activity.get('result', {}) if latest_activity else {}
    latest_activity_type = latest_activity_action.get('type', '') if latest_activity else ''
    latest_activity_live_external = bool(latest_activity_result.get('live_external_action'))
    latest_activity_outcome_ready, latest_activity_warning = action_outcome_ready(latest_activity)
    repeated_handoff_mentions = outreach_text.lower().count('hn/lobsters')
    # Circuit breaker: fire when the HN/Lobsters bottleneck is correctly identified but
    # cannot be resolved from this environment (blocked auth), AND the most recent
    # successful action was a peripheral channel (Telegraph, owned content) rather than
    # the bottleneck channel itself. Telegraph-post success should not mask an HN
    # execution ceiling that has persisted for 3+ cycles.
    bottleneck_channel_actions = {'hn_submission', 'hn_lobsters_submission'}
    peripheral_actions = {'owned_content_publication', 'content_distribution', 'telegraph_post'}
    recent_was_bottleneck_action = recent_action_type in bottleneck_channel_actions
    recent_was_peripheral = recent_action_type in peripheral_actions
    latest_activity_ok = bool(latest_activity_result.get('ok'))
    system_redesign_shipped = (
        recent_action_ok and recent_action_outcome_ready and (recent_action_live_external or recent_action_type in REAL_REPLACEMENT_ACTION_TYPES)
    ) or outcome_capability_shipped(outcome_capability, now, apollo_status=apollo_status) or (
        latest_activity_ok and latest_activity_type in SYSTEM_DESIGN_REPAIR_ACTION_TYPES
    )
    queue_housekeeping_only = recent_action_type in QUEUE_HOUSEKEEPING_ACTION_TYPES
    execution_ceiling_repetition = (
        codeberg_flat
        and repeated_handoff_mentions >= 3
        and not recent_was_bottleneck_action
        and (not recent_action_ok or recent_was_peripheral)
    )

    if codeberg_flat:
        bottleneck = 'distribution_and_message_to_primary_repo_conversion'
    else:
        bottleneck = 'conversion_to_free_use'

    reasons = [
        'Owned content and outreach exist, but repo/public adoption signals are still low.',
        'Codeberg is the primary repo, so primary-repo movement matters more than mirror vanity metrics.',
    ]
    if codeberg_flat:
        reasons.append('Codeberg adoption is flat across the recent measurement window, so the active tactics are not earning real adoption movement yet.')
    if github_flat:
        reasons.append('GitHub mirror adoption is also flat, which reinforces that activity is not converting anywhere meaningful yet.')
    if repetitive_reddit and reddit_blocked:
        reasons.append('Historical Reddit repetition is still on record, but Reddit is blocked from this environment, so do not spend this run rewriting a suspended channel.')
    elif repetitive_reddit:
        reasons.append('Reddit body repetition risk is visible, which weakens authenticity and makes the loop less likely to learn from fresh audience response.')
    if directory_submission_burst:
        reasons.append(f'{recent_directory_submissions} directory submissions already shipped in the last {FAMILY_BURST_WINDOW_HOURS} hours; more same-family submissions now would mostly create overlapping approval windows and noisier measurement, not a cleaner adoption signal.')
    if curator_outreach_burst:
        reasons.append(f'{recent_curator_outreach} curator contact attempts already shipped in the last {FAMILY_BURST_WINDOW_HOURS} hours; more same-family outreach should be treated as overlap risk unless a materially different demand-capture lane is chosen.')
    if publisher_outreach_burst:
        reasons.append(f'{recent_publisher_outreach} publisher contact attempts already shipped in the last {FAMILY_BURST_WINDOW_HOURS} hours; another same-family publisher burst now would mostly blur reply measurement instead of creating a clearer Codeberg adoption read.')
    if latest_activity_type and latest_activity_type != recent_action_type:
        reasons.append(
            f"The most recent runtime activity was {latest_activity_type}, but the latest meaningful external/replacement execution remains {recent_action_type or 'none'}; do not confuse follow-through with fresh outcome-bearing distribution."
        )

    if recent_action_ok and recent_action_type and recent_action_live_external and not queue_housekeeping_only:
        if recent_action_outcome_ready:
            reasons.append(f"The active loop did execute a live marketing action recently ({recent_action_type}), so the system is still shipping output even though outcome movement is not visible yet.")
        else:
            reasons.append(f"The active loop did attempt a live marketing action recently ({recent_action_type}), but the execution evidence is still low-signal or unusable, so it should not count as real distribution progress yet.")
    elif latest_activity and latest_activity_type and not latest_activity_live_external:
        reasons.append(f"The most recent marketing artifact ({latest_activity_type}) was preparation/follow-through work, not a live external execution, so it should not be mistaken for outcome movement.")
    if outcome_capability_shipped(outcome_capability, now, apollo_status=apollo_status):
        reasons.append(
            f"A fresh outcome-capability runtime is now logged ({outcome_capability.get('selected_lane', 'unknown lane')}), so the system-design repair should be treated as shipped even though repo adoption has not moved yet."
        )
    if queue_housekeeping_only:
        reasons.append('The most recent marketing action was curator-queue housekeeping, which should not count as a fresh replacement tactic or proof of new distribution progress.')
    if apollo_sequence_status.get('measurement_pending'):
        reasons.append(
            'Apollo managed outbound is already inside an active measurement window '
            f"until {apollo_sequence_status.get('next_review_at', 'the next review checkpoint')}, so the loop should not repackage the same lane before that checkpoint."
        )
    if execution_ceiling_repetition:
        reasons.append('The loop has repeated the same HN/Lobsters bottleneck multiple times without a fresh autonomous replacement action, so the ceiling itself is now a failing tactic.')

    next_moves = [
        'Kill or rewrite any tactic that stays flat across the recent adoption window instead of rewarding it for mere activity.',
        'Treat Codeberg movement as the primary outcome metric; GitHub is secondary mirror evidence only.',
        'Reduce repetitive outreach patterns and keep messaging tied to real workflow pain in a native-sounding voice.',
        'Require each new marketing action to name its expected outcome, measurement window, and replacement condition if it fails.',
        'If the current agent/process design is too weak to improve outcomes, create or repair agents, prompts, cron jobs, scripts, tests, and workflow rules in the same run instead of merely recommending them.',
    ]

    repair_actions: list[dict] = []
    measurement_pending_reasons: list[str] = []
    if measurement_pending and codeberg_flat:
        measurement_pending_reasons.append('primary_repo_flat')
    if codeberg_flat:
        content_distribution_action = (
            'REPLACE stale content distribution repair. Owned content is saturated for now; hold homepage/Telegraph steady and push Codeberg-primary curator/comparison backlinks, directory confirmation, and third-party citations that can move primary-repo adoption without another Telegraph-first cycle.'
            if system_redesign_shipped else
            'REPLACE stale content distribution repair. write.as is permanently blocked; Telegraph is primary. Real gap is (a) homepage title/description SEO tuning, (b) Telegraph posts targeting keyword gaps (unattended coding agent, AI agent orchestration CLI), (c) backlink building via directory submissions and competitor citations.'
        )
        repair_actions.append(build_repair_action(
            measurement_pending=measurement_pending,
            target_tactic='content_distribution',
            failure_type='primary_repo_flat',
            action=content_distribution_action,
            kill_condition='Still no Codeberg delta after 7 days of new approach',
            success_metric='Codeberg stars_delta_window > 0 or watchers_delta_window > 0 within 14 days',
            priority=1,
            repair_kind='tactic',
            previous_repair=prior_repairs.get(('primary_repo_flat', 'content_distribution')),
        ))
        if not system_redesign_shipped:
            repair_actions.append(build_repair_action(
                measurement_pending=False,
                target_tactic='marketing_system_architecture',
                failure_type='outcome_system_underpowered',
                action='REDESIGN the marketing system itself for outcome movement. In the same run, create or repair agents, prompts, cron jobs, scripts, tests, and development workflow so the loop can pursue stronger distribution, conversion, and follow-through paths instead of only technical repairs or repeated monitoring.',
                kill_condition='Another audit still shows flat primary-repo adoption without any new structural marketing capability or replacement execution path',
                success_metric='A new outcome-oriented agent/process/runtime capability is created and logged before the next audit, with a direct link to Codeberg adoption movement',
                priority=1,
                repair_kind='system_design',
                previous_repair=prior_repairs.get(('outcome_system_underpowered', 'marketing_system_architecture')),
            ))
            if recent_action_warning:
                repair_actions.append(build_repair_action(
                    measurement_pending=False,
                    target_tactic='managed_outbound_execution',
                    failure_type='managed_outbound_not_yet_usable',
                    action='REPAIR the managed outbound execution path. A recent Apollo/live-outbound action exists, but the evidence says the asset is not usable yet. In the same run, refresh the execution packet with import/count verification and sequence-launch gates, and do not count Apollo progress until a non-zero list or live sequence exists.',
                    kill_condition='Another audit still counts Apollo/list activity without proof that the outbound asset is usable',
                    success_metric='Latest managed-outbound log proves a non-zero imported list or a launched live sequence tied to the Codeberg-primary CTA',
                    priority=1,
                    repair_kind='system_design',
                    previous_repair=prior_repairs.get(('managed_outbound_not_yet_usable', 'managed_outbound_execution')),
                ))
    if directory_submission_burst:
        repair_actions.append(build_repair_action(
            measurement_pending=False,
            target_tactic='directory_submission_burst',
            failure_type='same_family_distribution_overlap',
            action='PAUSE net-new low-intent directory submissions for now. Let the existing listing approvals mature, then use the next run on higher-intent demand capture or conversion-moving lanes such as StackOverflow answers, manual curator/contact execution packets, or direct comparison-backlink follow-through.',
            kill_condition='Another audit adds more directory submissions before current listing windows have produced approval/backlink evidence or aged past their review checkpoints',
            success_metric='Next execution lane is not another directory submission burst and produces a cleaner measurement path toward Codeberg movement',
            priority=1,
            repair_kind='tactic',
            previous_repair=prior_repairs.get(('same_family_distribution_overlap', 'directory_submission_burst')),
        ))
    if curator_outreach_burst:
        repair_actions.append(build_repair_action(
            measurement_pending=False,
            target_tactic='curator_outreach_burst',
            failure_type='same_family_outreach_overlap',
            action='HOLD another same-day curator-contact burst. Reuse the prepared/manual-contact artifacts already in queue and spend the next active cycle on a different lane that can create clearer demand or cleaner follow-through measurement.',
            kill_condition='Another audit adds more same-family curator outreach before the existing reply/backlink windows have materially aged or produced evidence',
            success_metric='Next execution lane advances a different family or executes an existing manual-contact packet instead of starting another same-day curator burst',
            priority=2,
            repair_kind='tactic',
            previous_repair=prior_repairs.get(('same_family_outreach_overlap', 'curator_outreach_burst')),
        ))
    if publisher_outreach_burst:
        repair_actions.append(build_repair_action(
            measurement_pending=False,
            target_tactic='publisher_outreach_burst',
            failure_type='same_family_publisher_overlap',
            action='HOLD another same-day publisher-contact burst. Let the existing Codeberg-first publisher reply windows breathe, and spend the next active cycle on a different family such as directory confirmation, comparison/backlink reuse, StackOverflow demand capture, or due follow-up review.',
            kill_condition='Another audit adds more same-family publisher outreach before the current reply/review windows have materially aged or produced evidence',
            success_metric='Next execution lane is not another same-day publisher-contact burst and produces a cleaner measurement path toward Codeberg movement',
            priority=2,
            repair_kind='tactic',
            previous_repair=prior_repairs.get(('same_family_publisher_overlap', 'publisher_outreach_burst')),
        ))
    if measurement_pending and github_flat:
        measurement_pending_reasons.append('mirror_repo_flat')
    if github_flat:
        repair_actions.append(build_repair_action(
            measurement_pending=measurement_pending,
            target_tactic='github_mirror_outreach',
            failure_type='mirror_repo_flat',
            action='Ensure all public-facing content links Codeberg as primary and GitHub as mirror. If GitHub mirror remains flat, it is secondary evidence — do not allocate dedicated effort unless Codeberg is moving.',
            kill_condition='N/A (mirror, not primary)',
            success_metric='GitHub mirror shows any adoption delta',
            priority=3,
            repair_kind='tactic',
            previous_repair=prior_repairs.get(('mirror_repo_flat', 'github_mirror_outreach')),
        ))
    if measurement_pending and repetitive_reddit and not reddit_blocked:
        measurement_pending_reasons.append('repetitive_outreach')
    dormant_risks: list[str] = []
    if repetitive_reddit and reddit_blocked:
        dormant_risks.append('reddit_style_repetition_suspended_while_channel_blocked')
    elif repetitive_reddit:
        repair_actions.append(build_repair_action(
            measurement_pending=measurement_pending,
            target_tactic='reddit_post_style',
            failure_type='repetitive_outreach',
            action='REWRITE Reddit outreach template. Current opening has been used repeatedly. Draft 2-3 fresh openings tied to specific subreddit pain points. Do not reuse any opening across different subreddits.',
            kill_condition='Same opening detected again in next audit',
            success_metric='No repeated openings in next audit window',
            priority=2,
            repair_kind='tactic',
            previous_repair=prior_repairs.get(('repetitive_outreach', 'reddit_post_style')),
        ))
    if execution_ceiling_repetition:
        repair_actions.append(build_repair_action(
            measurement_pending=system_redesign_shipped,
            target_tactic='distribution_ceiling',
            failure_type='execution_ceiling_repetition',
            action='STOP repeating the same HN/Lobsters-only handoff. Ship a fresh autonomous replacement asset in this run: either a new conversion-focused Telegraph post tied to a current pain angle, or a curator/directory/backlink outreach packet the loop can hand off cleanly without another generic ceiling note.',
            kill_condition='Another audit repeats the same HN/Lobsters bottleneck without a new shipped asset',
            success_metric='A fresh non-monitor marketing asset is shipped and logged before the next audit',
            priority=1,
            repair_kind='system_design',
            previous_repair=prior_repairs.get(('execution_ceiling_repetition', 'distribution_ceiling')),
        ))

    message_checks = dict(POSITIONING_QUESTIONS)

    failing_tactic_names = [
        name for name, failed in {
            'reddit_style_repetition': repetitive_reddit and not reddit_blocked,
            'primary_repo_flat_window': codeberg_flat,
            'mirror_repo_flat_window': github_flat,
            'same_family_distribution_overlap': directory_submission_burst,
            'same_family_outreach_overlap': curator_outreach_burst,
            'same_family_publisher_overlap': publisher_outreach_burst,
            'execution_ceiling_repetition': execution_ceiling_repetition,
        }.items() if failed
    ]

    for repair in repair_actions:
        if repair.get('repair_state') == 'pending_measurement' and repair.get('failure_type') not in measurement_pending_reasons:
            measurement_pending_reasons.append(repair.get('failure_type'))

    has_needs_execution_repairs = any(repair.get('repair_state') == 'needs_execution' for repair in repair_actions)
    has_pending_measurement_repairs = any(repair.get('repair_state') == 'pending_measurement' for repair in repair_actions)
    repair_window_status = 'needs_repair' if has_needs_execution_repairs else ('measurement_pending' if has_pending_measurement_repairs else 'clear')

    worked: list[str] = []
    not_worked: list[str] = []
    repetitive: list[str] = []
    low_signal: list[str] = []
    should_change_now: list[str] = []
    prepared_primary_repo_flat_packet_repeats = recent_prepared_primary_repo_flat_packet_count(now)

    if recent_action_ok and recent_action_type and recent_action_live_external:
        if codeberg_flat or measurement_pending_reasons:
            low_signal.append(
                f'Recent live external action exists ({recent_action_type}), but flat primary-repo movement means it is still measurement-pending, not proof that the tactic worked.'
            )
        else:
            worked.append(f'Execution path produced a live external action with non-flat outcome context: {recent_action_type}.')
    elif latest_activity_type and latest_activity_result.get('ok'):
        worked.append(f'Internal repair/follow-through is still running reliably: {latest_activity_type}.')

    if codeberg_flat:
        not_worked.append('Primary-repo adoption did not move: Codeberg stars/watchers/forks stayed flat across the recent window.')
    if github_flat:
        not_worked.append('Mirror adoption did not move either: GitHub stayed flat, so activity is not converting on either repo surface.')
    if execution_ceiling_repetition:
        not_worked.append('The loop kept naming the HN/Lobsters handoff bottleneck without shipping a stronger replacement lane.')

    if repeated_openings:
        repetitive.extend(f'Repeated outreach opening: {opening}' for opening in repeated_openings)
    if publisher_outreach_burst:
        repetitive.append(f'{recent_publisher_outreach} publisher contact attempts shipped inside the last {FAMILY_BURST_WINDOW_HOURS} hours, which is overlapping the same family.')
    if curator_outreach_burst:
        repetitive.append(f'{recent_curator_outreach} curator contact attempts shipped inside the last {FAMILY_BURST_WINDOW_HOURS} hours, which is overlapping the same family.')
    if directory_submission_burst:
        repetitive.append(f'{recent_directory_submissions} directory submissions shipped inside the last {FAMILY_BURST_WINDOW_HOURS} hours, which is overlapping the same family.')
    if prepared_primary_repo_flat_packet_repeats >= PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_THRESHOLD:
        repetitive.append(
            f'The primary-repo-flat publisher contact packet was regenerated as prepared-only follow-through '
            f'{prepared_primary_repo_flat_packet_repeats} times inside the last {PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_WINDOW_HOURS} hours.'
        )
        low_signal.append(
            'Prepared-only primary-repo-flat packet refreshes are repeating without entering a live delivery/review window, '
            'so that lane is currently counting packet churn rather than adoption-moving distribution.'
        )
        should_change_now.append(
            'Repair the primary-repo-flat follow-through architecture: stop reselecting prepared-only publisher packets unless they have a fresh live delivery window or materially changed targets/channels.'
        )

    if recent_action_warning:
        low_signal.append(f'{recent_action_type or "latest live action"}: {recent_action_warning}')
    if latest_activity_warning and latest_activity_warning != recent_action_warning:
        low_signal.append(f'{latest_activity_type or "latest marketing activity"}: {latest_activity_warning}')
    if reddit_blocked:
        low_signal.append('Reddit remains blocked/partial from this environment, so that channel cannot produce a trustworthy execution read right now.')

    for repair in sorted(repair_actions, key=lambda row: (row.get('priority', 99), row.get('failure_type', ''))):
        should_change_now.append(repair['action'])

    payload = {
        'generated_at': datetime.now().isoformat(),
        'current_bottleneck': bottleneck,
        'articles_logged': articles,
        'reddit_posts_analyzed': reddit_posts,
        'repeated_openings': repeated_openings,
        'adoption_metrics': metrics,
        'recent_window': recent_window,
        'adoption_evaluation': adoption_eval,
        'failing_tactics': failing_tactic_names,
        'worked': worked,
        'not_worked': not_worked,
        'repetitive': repetitive,
        'low_signal': low_signal,
        'should_change_now': should_change_now,
        'dormant_risks': dormant_risks,
        'repair_actions': repair_actions,
        'repair_window_status': repair_window_status,
        'measurement_pending_reasons': measurement_pending_reasons,
        'has_failing_tactics': bool(failing_tactic_names),
        'reasons': reasons,
        'next_moves': next_moves,
        'self_improvement_mandate': {
            'owner': 'marketing_system',
            'goal': 'improve real marketing outcomes, not just technical health',
            'default_decision_rule': 'it is up to the system to decide and proceed',
            'required_when_outcomes_are_flat': [
                'create_new_agents',
                'repair_existing_agents',
                'rewrite_prompts',
                'change_cron_jobs',
                'patch_marketing_scripts',
                'add_or_tighten_tests',
                'change_development_process',
                'retire_stale_paths',
                'generate_new_distribution_assets'
            ],
            'technical_repairs_alone_are_insufficient': True
        },
        'four_marketing_questions': message_checks,
        'latest_marketing_activity': {
            'path': latest_activity.get('_path'),
            'type': latest_activity_type,
            'title': latest_activity_action.get('title'),
            'status': latest_activity_result.get('status'),
            'ok': bool(latest_activity_result.get('ok')),
            'live_external_action': latest_activity_live_external,
            'outcome_ready': latest_activity_outcome_ready,
            'warning': latest_activity_warning,
            'url': latest_activity_action.get('url'),
        } if latest_activity else None,
        'latest_executed_action': {
            'path': latest_action.get('_path'),
            'type': recent_action_type,
            'title': recent_action.get('title'),
            'status': recent_action_result.get('status'),
            'ok': recent_action_ok,
            'live_external_action': recent_action_live_external,
            'outcome_ready': recent_action_outcome_ready,
            'warning': recent_action_warning,
            'url': recent_action.get('url'),
        } if latest_action else None,
        'apollo_sequence_status': apollo_sequence_status or None,
    }
    AUDIT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')

    lines = [
        '# Marketing Workflow Audit',
        '',
        f'- Generated: {payload["generated_at"]}',
        f'- Current bottleneck: **{bottleneck}**',
        f'- Owned articles logged: **{articles}**',
        f'- Reddit posts analyzed: **{reddit_posts}**',
        '',
        '## Why this is the bottleneck',
    ]
    lines += [f'- {r}' for r in reasons]
    lines += ['', '## What actually worked']
    if payload['worked']:
        lines += [f'- {item}' for item in payload['worked']]
    else:
        lines.append('- No meaningful tactic produced a trustworthy win in this window.')
    lines += ['', '## What did not work']
    if payload['not_worked']:
        lines += [f'- {item}' for item in payload['not_worked']]
    else:
        lines.append('- No clear failure signal detected in this window.')
    lines += ['', '## What is repetitive']
    if payload['repetitive']:
        lines += [f'- {item}' for item in payload['repetitive']]
    else:
        lines.append('- No material repetition signal detected in this window.')
    lines += ['', '## What is low-signal']
    if payload['low_signal']:
        lines += [f'- {item}' for item in payload['low_signal']]
    else:
        lines.append('- No low-signal execution marker detected in this window.')
    lines += ['', '## What should change now']
    if payload['should_change_now']:
        lines += [f'- {item}' for item in payload['should_change_now']]
    else:
        lines.append('- No immediate change queued.')
    lines += ['', '## Observed risks']
    if repeated_openings:
        lines += [f'- Repetition risk in outreach opening: "{x}"' for x in repeated_openings]
    else:
        lines.append('- No exact repeated outreach opening detected in the latest audit inputs.')
    if payload['failing_tactics']:
        lines += [f'- Failing tactic detected: {name}' for name in payload['failing_tactics']]
    for risk in payload.get('dormant_risks', []) or []:
        lines.append(f'- Dormant risk parked for now: {risk}')
    lines += ['', '## Outcome evaluation']
    for platform, summary in recent_window.items():
        lines.append(
            f"- {platform}: samples={summary.get('samples', 0)}, stars {summary.get('stars_delta_window', 0):+d}, watchers {summary.get('watchers_delta_window', 0):+d}, forks {summary.get('forks_delta_window', 0):+d}"
        )
    for finding in adoption_eval.get('findings', []):
        lines.append(f'- {finding}')
    lines += ['', '## Repair actions (execute in this run)']
    for ra in repair_actions:
        state = ra.get('repair_state')
        lines += [
            f'- **{ra["failure_type"]}** ({ra.get("repair_kind", "tactic")}) → {ra["action"]}',
            f'  - Repair state: {state}',
            f'  - Kill condition: {ra["kill_condition"]}',
            f'  - Success metric: {ra["success_metric"]}',
        ]
    if measurement_pending_reasons:
        lines += [
            '- No additional same-run repair actions remain. Existing repairs are live and the loop is now waiting on measurement.',
            f'- Measurement-pending reasons: {", ".join(measurement_pending_reasons)}',
        ]
    elif not repair_actions:
        lines.append('- No repair actions needed.')
    if payload.get('latest_executed_action'):
        action = payload['latest_executed_action']
        lines += ['', '## Latest executed marketing action']
        lines += [
            f"- Type: {action.get('type')}",
            f"- Title: {action.get('title')}",
            f"- Status: {action.get('status')} (ok={action.get('ok')})",
            f"- Outcome-ready: {action.get('outcome_ready')}",
            f"- Source log: {action.get('path')}",
        ]
        if action.get('warning'):
            lines.append(f"- Warning: {action.get('warning')}")
        if action.get('url'):
            lines.append(f"- URL: {action.get('url')}")
    lines += ['', '## Next highest-leverage moves']
    lines += [f'- {m}' for m in next_moves]
    lines += ['', '## Self-improvement mandate']
    lines += [
        '- The marketing system owns outcomes, not just activity.',
        '- Default internal decision rule: it is up to the system to decide and proceed.',
        '- Allowed same-run self-repairs include new agents, prompt rewrites, cron changes, script patches, stronger tests, and process redesign when those improve marketing outcomes.',
    ]
    lines += ['', '## Four marketing questions that messaging must answer']
    lines += [f'- {k}: {v}' for k, v in message_checks.items()]
    lines += ['', '## Principle reference', f'- See `{PRINCIPLES}`', f'- See `{FOUR_QUESTIONS_DOC}`', f'- See `{SELF_IMPROVEMENT_DOC}`']
    AUDIT_MD.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
