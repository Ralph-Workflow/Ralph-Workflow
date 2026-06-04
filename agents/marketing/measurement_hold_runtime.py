from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_NAME = Path(__file__).name
from typing import Any


STRUCTURAL_REPLACEMENT_ACTION_TYPES = {
    'apollo_outreach_execution',
}

MEASUREMENT_HOLD_ACTION_TYPES = {
    'measurement_hold_execution',
    'measurement_hold_follow_through',
    'measurement_hold_churn_guard_repair',
}
MEASUREMENT_HOLD_COOLDOWN_MINUTES = 60
# 2026-06-04: Added a hard maximum cap to prevent indefinite hold deadlocks.
# When all distribution lanes are structurally blocked (Reddit suspended, DDG/Brave
# dead, Apollo Cloudflare-blocked, GitHub auth unavailable), explicit hold-until
# sources could chain holds for days with zero marketing activity. The hard cap
# forces a break into owned_content or blocker escalation after this duration.
# Measured from hold_started_at, not from the resolved hold_until.
MEASUREMENT_HOLD_HARD_MAX_HOURS = 24
# Hold-exhaustion circuit breaker: if consecutive measurement_hold actions in this
# window hit the threshold with zero live external actions, break out of the hold.
HOLD_EXHAUSTION_CONSECUTIVE_THRESHOLD = 2
# 2026-06-01: Reduced from 3→2. At 3, the system was deadlocking too long
# while all lanes were structurally blocked. At 2, circuit-breaks faster
# into owned_content or blocker escalation.
HOLD_EXHAUSTION_WINDOW_HOURS = 24
LIVE_EXTERNAL_STATUSES = {
    'executed',
    'sent',
    'submitted',
    'published',
    'launched',
}


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


def recent_marketing_log_payloads(log_dir: Path) -> list[tuple[Path, dict[str, Any], datetime]]:
    payloads: list[tuple[Path, dict[str, Any], datetime]] = []
    for path in log_dir.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            continue
        timestamp = parse_iso_date(payload.get('timestamp') or payload.get('timestamp_utc'))
        if timestamp is None:
            timestamp = datetime.fromtimestamp(path.stat().st_mtime)
        payloads.append((path, payload, timestamp))
    payloads.sort(key=lambda item: item[2], reverse=True)
    return payloads


def _payload_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _payload_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ''


def _payload_nested_text(payload: dict[str, Any], *keys: str) -> str:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return ''
        value = value.get(key)
    return value.strip() if isinstance(value, str) else ''


def _payload_action_type(payload: dict[str, Any]) -> str:
    chosen_action = _payload_dict(payload, 'chosen_action')
    return str(
        chosen_action.get('type')
        or payload.get('action_type')
        or payload.get('action')
        or ''
    ).strip()


def _resolve_hold_until(
    *,
    hold_payload: dict[str, Any],
    hold_started_at: datetime,
    log_dir: Path,
    payloads: list[tuple[Path, dict[str, Any], datetime]],
    include_distribution_lane_latest: bool = True,
) -> datetime:
    default_hold_until = hold_started_at + timedelta(minutes=MEASUREMENT_HOLD_COOLDOWN_MINUTES)
    explicit_candidates: list[datetime] = []

    inline_candidates = (
        _payload_nested_text(hold_payload, 'why_this_action', 'hold_until'),
        _payload_nested_text(hold_payload, 'result', 'hold_until'),
        _payload_nested_text(hold_payload, 'review_window', 'scheduled_run_at'),
    )
    for raw_value in inline_candidates:
        parsed = parse_iso_date(raw_value)
        if parsed is not None and parsed > hold_started_at:
            explicit_candidates.append(parsed)

    latest_release_cron_candidate: datetime | None = None
    latest_release_cron_logged_at: datetime | None = None
    for _path, payload, timestamp in payloads:
        if timestamp <= hold_started_at:
            continue
        if _payload_action_type(payload) != 'measurement_hold_release_cron':
            continue
        scheduled_run_at = parse_iso_date(_payload_nested_text(payload, 'review_window', 'scheduled_run_at'))
        if scheduled_run_at is None or scheduled_run_at <= hold_started_at:
            continue
        if latest_release_cron_logged_at is None or timestamp > latest_release_cron_logged_at:
            latest_release_cron_logged_at = timestamp
            latest_release_cron_candidate = scheduled_run_at
    if latest_release_cron_candidate is not None:
        explicit_candidates.append(latest_release_cron_candidate)

    if include_distribution_lane_latest:
        distribution_lane_latest_path = log_dir / 'distribution_lane_latest.json'
        try:
            distribution_lane_latest = json.loads(distribution_lane_latest_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            distribution_lane_latest = {}
        scheduled_run_at = parse_iso_date(
            str(distribution_lane_latest.get('short_review_window_release_at') or '').strip()
        )
        if scheduled_run_at is not None and scheduled_run_at > hold_started_at:
            explicit_candidates.append(scheduled_run_at)

        outcome_execution_board_status_path = log_dir / 'outcome_execution_board_latest.json'
        try:
            outcome_execution_board_status = json.loads(outcome_execution_board_status_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            outcome_execution_board_status = {}
        next_checkpoint = outcome_execution_board_status.get('next_truthful_checkpoint')
        if isinstance(next_checkpoint, dict):
            checkpoint_at = parse_iso_date(str(next_checkpoint.get('at') or '').strip())
            if checkpoint_at is not None and checkpoint_at > hold_started_at:
                explicit_candidates.append(checkpoint_at)

    resolved = max(explicit_candidates) if explicit_candidates else default_hold_until
    # ── Hard cap: never let a measurement hold exceed the hard max duration ──
    # This prevents the deadlock we saw May 28–June 4: all lanes blocked, hold
    # chaining through explicit candidates (short_review_window_release_at) that
    # never actually cleared because no external action could ship through the
    # blocked lanes.
    hard_max = hold_started_at + timedelta(hours=MEASUREMENT_HOLD_HARD_MAX_HOURS)
    if resolved > hard_max:
        return hard_max
    return resolved


def latest_measurement_hold_window(now: datetime, log_dir: Path) -> dict[str, Any] | None:
    latest_hold: tuple[Path, dict[str, Any], datetime] | None = None
    payloads = recent_marketing_log_payloads(log_dir)

    for path, payload, timestamp in payloads:
        action_type = _payload_action_type(payload)
        if action_type in MEASUREMENT_HOLD_ACTION_TYPES:
            latest_hold = (path, payload, timestamp)
            break

    if latest_hold is None:
        return None

    hold_path, hold_payload, hold_started_at = latest_hold
    latest_hold_timestamp = hold_started_at
    hold_until = _resolve_hold_until(
        hold_payload=hold_payload,
        hold_started_at=hold_started_at,
        log_dir=log_dir,
        payloads=payloads,
    )

    def _is_live_external(payload: dict[str, Any]) -> bool:
        result = _payload_dict(payload, 'result')
        action_type = _payload_action_type(payload)
        return bool(result.get('live_external_action')) or action_type in STRUCTURAL_REPLACEMENT_ACTION_TYPES

    matched_hold_candidates: list[tuple[Path, dict[str, Any], datetime]] = []
    for path, payload, timestamp in payloads:
        action_type = _payload_action_type(payload)
        if action_type not in MEASUREMENT_HOLD_ACTION_TYPES:
            continue
        candidate_hold_until = _resolve_hold_until(
            hold_payload=payload,
            hold_started_at=timestamp,
            log_dir=log_dir,
            payloads=payloads,
            include_distribution_lane_latest=False,
        )
        if candidate_hold_until != hold_until or timestamp > latest_hold_timestamp:
            continue
        has_live_external_between = any(
            timestamp < other_timestamp <= latest_hold_timestamp and _is_live_external(other_payload)
            for _other_path, other_payload, other_timestamp in payloads
        )
        if not has_live_external_between:
            matched_hold_candidates.append((path, payload, timestamp))

    if matched_hold_candidates:
        execution_candidates = [candidate for candidate in matched_hold_candidates if _payload_action_type(candidate[1]) == 'measurement_hold_execution']
        source_candidates = execution_candidates or matched_hold_candidates
        hold_path, hold_payload, hold_started_at = min(source_candidates, key=lambda item: item[2])

    latest_live_external_after_hold: datetime | None = None
    for _path, payload, timestamp in payloads:
        if _is_live_external(payload) and timestamp > hold_started_at:
            latest_live_external_after_hold = timestamp
            break

    comparable_now = now
    if hold_until.tzinfo is None and comparable_now.tzinfo is not None:
        comparable_now = comparable_now.replace(tzinfo=None)
    elif hold_until.tzinfo is not None and comparable_now.tzinfo is None:
        comparable_now = comparable_now.replace(tzinfo=hold_until.tzinfo)

    if comparable_now >= hold_until:
        return None
    if latest_live_external_after_hold is not None:
        return None

    why_this_action = _payload_dict(hold_payload, 'why_this_action')
    return {
        'hold_started_at': hold_started_at,
        'hold_until': hold_until,
        'source_log': str(hold_path),
        'reason': (why_this_action.get('summary') or _payload_text(hold_payload, 'why_this_action') or 'Recent measurement hold still active.'),
    }


# ── Hold-exhaustion circuit-breaker ───────────────────────────────────────────
# When the lane selector has no viable lanes (all distribution channels blocked
# on human-gated credentials) and redirects to measurement_hold 3+ times in a
# 24-hour window with zero live external actions between them, the system is in
# a hold deadlock. The measurement window can't move because the hold prevents
# actions; the hold can't lift because no action changes the measurement.
#
# The circuit-breaker forces a break to the safest autonomous lane (owned_content)
# to ensure at least one real action ships before the next hold check.




def hold_exhausted(now: datetime, log_dir: Path) -> bool:
    """Return True if 3+ consecutive measurement_hold actions in the exhaustion
    window with zero live external actions between them — a hold deadlock."""
    from datetime import timedelta

    payloads = recent_marketing_log_payloads(log_dir)
    cutoff = now - timedelta(hours=HOLD_EXHAUSTION_WINDOW_HOURS)

    hold_count = 0
    for _path, payload, timestamp in payloads:
        if timestamp < cutoff:
            break
        action_type = str(
            payload.get('action_type') or payload.get('action') or ''
        ).strip()
        if 'measurement_hold' in action_type.lower():
            hold_count += 1
            continue
        # Any payload with live_external_action=true resets the cascade
        if payload.get('live_external_action') is True:
            # If we already have 3+ holds before this live action, still exhausted
            # (live action happened AFTER the holds, not BETWEEN them)
            break

    return hold_count >= HOLD_EXHAUSTION_CONSECUTIVE_THRESHOLD


# ── Self-improvement ──────────────────────────────────────────────────────────
# Note: measurement_hold_runtime.py provides a shared runtime library consulted
# by other agents. Self-improvement is indirect: flat outcomes should trigger
# the system to review whether hold windows are appropriate.

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

