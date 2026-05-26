#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents/marketing/logs'
STATUS_JSON = LOG_DIR / 'apollo_sequence_status_latest.json'
STATUS_MD = LOG_DIR / 'apollo_sequence_status_latest.md'
LAUNCH_REVIEW_DAYS = 7
REPO_VISIT_REVIEW_DAYS = 14
ADOPTION_REVIEW_DAYS = 30
LIVE_LIST_REVIEW_MAX_AGE_DAYS = 3
RUNTIME_STATUS_MAX_AGE_HOURS = 12
LIVE_SEND_STATUSES = {
    'verified_live_sequence',
    'launched_live_sequence',
    'sending_live_sequence',
    'sent',
    'launched',
}
LAUNCH_READY_ONLY_MARKERS = {
    'launch-ready',
    'launch ready',
    'if human/browser automation launches the emails',
    'sequence-ready',
    'usable for sequence launch',
    'needs send confirmation',
}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _chosen_action_dict(payload: dict) -> dict:
    chosen_action = payload.get('chosen_action')
    return chosen_action if isinstance(chosen_action, dict) else {}


def _latest_launch_log() -> tuple[Path | None, dict]:
    candidates = sorted(LOG_DIR.glob('marketing_*_apollo_sequence_launch.json'), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        payload = _load_json(path)
        if _chosen_action_dict(payload).get('type') == 'apollo_sequence_launch':
            return path, payload
    return None, {}


def _latest_live_list_verification() -> tuple[Path | None, dict[str, Any]]:
    candidates = sorted(LOG_DIR.glob('marketing_*_apollo_list_verification.json'), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        payload = _load_json(path)
        if _chosen_action_dict(payload).get('type') == 'apollo_list_verification':
            return path, payload
    return None, {}


def _latest_outbound_verification() -> tuple[Path | None, dict[str, Any]]:
    candidates = sorted(LOG_DIR.glob('marketing_*_apollo_outbound_verification.json'), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        payload = _load_json(path)
        if _chosen_action_dict(payload).get('type') == 'apollo_outbound_verification':
            return path, payload
    return None, {}


def _verification_age_ok(timestamp: str | None, now: datetime) -> bool:
    if not timestamp:
        return False
    try:
        dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return now - dt <= timedelta(days=LIVE_LIST_REVIEW_MAX_AGE_DAYS)


def _result_text(result: dict[str, Any]) -> str:
    chunks: list[str] = []
    for key in ('status', 'notes', 'evidence', 'blocking_factors'):
        value = result.get(key)
        if isinstance(value, list):
            chunks.extend(str(item) for item in value)
        elif value:
            chunks.append(str(value))
    return ' '.join(chunks).lower()


def _runtime_blocker(now: datetime) -> dict[str, Any]:
    runtime_status_path = LOG_DIR / 'apollo_status.json'
    payload = _load_json(runtime_status_path)
    if not payload or not runtime_status_path.exists():
        return {}
    age_hours = (now - datetime.fromtimestamp(runtime_status_path.stat().st_mtime).astimezone()).total_seconds() / 3600
    if age_hours > RUNTIME_STATUS_MAX_AGE_HOURS:
        return {}
    status = str(payload.get('status') or '').strip().lower()
    notes = str(payload.get('notes') or '').strip()
    if status not in {'cloudflare_auth_blocked', 'ato_email_verification_required'} and not payload.get('cloudflare_blocked'):
        return {}
    summary = 'Apollo runtime is blocked by a Cloudflare auth interstitial.'
    if status == 'ato_email_verification_required':
        summary = 'Apollo runtime is blocked on mailbox/email-code verification for this device.'
    elif payload.get('cloudflare_blocked') and status != 'cloudflare_auth_blocked':
        summary = 'Apollo runtime is blocked by a Cloudflare/captcha auth gate.'
    return {
        'status': status or 'runtime_auth_blocked',
        'summary': summary,
        'notes': notes,
        'timestamp': payload.get('timestamp'),
        'final_url': payload.get('final_url'),
        'path': str(runtime_status_path),
    }


def _blocked_runtime_payload(now: datetime, *, summary: str, launch_path: Path | None, live_list_path: Path | None, outbound_path: Path | None, record_count: int, sequence_name: Any = None, final_url: Any = None, verification_timestamp: Any = None, evidence: list[Any] | None = None, launch_timestamp: str | None = None, next_review_at: str | None = None, launch_review_at: str | None = None, repo_visit_review_at: str | None = None, adoption_review_at: str | None = None) -> dict[str, Any] | None:
    blocker = _runtime_blocker(now)
    if not blocker or record_count <= 0:
        return None
    return {
        'generated_at': now.isoformat(),
        'status': 'runtime_auth_blocked',
        'measurement_pending': False,
        'summary': f"{summary} {blocker['summary']}",
        'launch_log': str(launch_path) if launch_path else None,
        'live_list_log': str(live_list_path) if live_list_path else None,
        'outbound_verification_log': str(outbound_path) if outbound_path else None,
        'record_count': record_count,
        'sequence_name': sequence_name,
        'final_url': final_url,
        'verification_timestamp': verification_timestamp,
        'evidence': list(evidence or []),
        'needs_live_verification': True,
        'runtime_blocker_status': blocker.get('status'),
        'runtime_blocker_summary': blocker.get('summary'),
        'runtime_blocker_notes': blocker.get('notes'),
        'runtime_blocker_timestamp': blocker.get('timestamp'),
        'runtime_status_log': blocker.get('path'),
        'runtime_status_url': blocker.get('final_url'),
        'launch_timestamp': launch_timestamp,
        'next_review_at': next_review_at,
        'launch_review_at': launch_review_at,
        'repo_visit_review_at': repo_visit_review_at,
        'adoption_review_at': adoption_review_at,
    }


def _launch_log_proves_live_send(result: dict[str, Any]) -> bool:
    status = str(result.get('status') or '').strip().lower()
    if status in LIVE_SEND_STATUSES:
        return True
    if not result.get('outcome_ready'):
        return False
    text = _result_text(result)
    return not any(marker in text for marker in LAUNCH_READY_ONLY_MARKERS)


def build_status(now: datetime | None = None) -> dict:
    now = now or datetime.now().astimezone()
    launch_path, launch_payload = _latest_launch_log()
    live_list_path, live_list_payload = _latest_live_list_verification()
    outbound_path, outbound_payload = _latest_outbound_verification()

    if not launch_payload:
        live_list_result = live_list_payload.get('result') or {}
        outbound_result = outbound_payload.get('result') or {}
        record_count = int(live_list_result.get('record_count') or 0)
        latest_outbound_status = str(outbound_result.get('status') or '').strip()
        live_list_timestamp = live_list_payload.get('timestamp') if live_list_payload else None
        live_list_fresh = _verification_age_ok(live_list_timestamp, now)

        if latest_outbound_status == 'verified_live_sequence':
            return {
                'generated_at': now.isoformat(),
                'status': 'verified_live_sequence',
                'measurement_pending': True,
                'summary': 'Apollo outbound verification confirms a live sending sequence is visible.',
                'launch_log': None,
                'live_list_log': str(live_list_path) if live_list_path else None,
                'outbound_verification_log': str(outbound_path) if outbound_path else None,
                'record_count': int(outbound_result.get('record_count') or record_count),
                'sequence_name': outbound_result.get('sequence_name') or live_list_result.get('sequence_name'),
                'final_url': outbound_result.get('final_url') or live_list_result.get('final_url'),
                'verification_timestamp': outbound_payload.get('timestamp'),
                'evidence': list(outbound_result.get('evidence') or []),
                'needs_live_verification': False,
            }

        if record_count > 0:
            blocked_payload = _blocked_runtime_payload(
                now,
                summary='Apollo list is verified non-zero, but live send confirmation cannot be checked from the current runtime.',
                launch_path=None,
                live_list_path=live_list_path,
                outbound_path=outbound_path,
                record_count=record_count,
                sequence_name=live_list_result.get('sequence_name'),
                final_url=live_list_result.get('final_url'),
                verification_timestamp=live_list_timestamp,
                evidence=list(live_list_result.get('evidence') or []),
            )
            if blocked_payload:
                return blocked_payload
        if record_count > 0 and live_list_fresh:
            return {
                'generated_at': now.isoformat(),
                'status': 'launch_ready_unverified_send',
                'measurement_pending': False,
                'summary': 'Apollo list is verified non-zero, but no live sequence-send evidence exists yet.',
                'launch_log': None,
                'live_list_log': str(live_list_path) if live_list_path else None,
                'outbound_verification_log': str(outbound_path) if outbound_path else None,
                'record_count': record_count,
                'sequence_name': live_list_result.get('sequence_name'),
                'final_url': live_list_result.get('final_url'),
                'verification_timestamp': live_list_timestamp,
                'evidence': list(live_list_result.get('evidence') or []),
                'needs_live_verification': True,
            }

        return {
            'generated_at': now.isoformat(),
            'status': 'not_launched',
            'measurement_pending': False,
            'summary': 'No Apollo sequence launch log exists yet.',
            'launch_log': None,
            'live_list_log': str(live_list_path) if live_list_path else None,
            'outbound_verification_log': str(outbound_path) if outbound_path else None,
            'record_count': record_count,
            'verification_timestamp': live_list_timestamp,
            'needs_live_verification': bool(record_count > 0),
        }

    result = launch_payload.get('result') or {}
    timestamp = launch_payload.get('timestamp')
    launch_at = datetime.fromisoformat(timestamp.replace('Z', '+00:00')) if timestamp else now
    if launch_at.tzinfo is None:
        launch_at = launch_at.astimezone()
    record_count = int(result.get('record_count') or 0)

    live_list_path, live_list_payload = _latest_live_list_verification()
    live_list_result = live_list_payload.get('result') or {}
    outbound_path, outbound_payload = _latest_outbound_verification()
    outbound_result = outbound_payload.get('result') or {}
    outbound_status = str(outbound_result.get('status') or '').strip().lower()
    live_send_verified = outbound_status == 'verified_live_sequence' or _launch_log_proves_live_send(result)

    if not live_send_verified:
        live_list_timestamp = live_list_payload.get('timestamp') if live_list_payload else None
        live_list_fresh = _verification_age_ok(live_list_timestamp, now)
        launch_ready_count = record_count or int(live_list_result.get('record_count') or 0)
        if launch_ready_count > 0:
            blocked_payload = _blocked_runtime_payload(
                now,
                summary='Apollo has a non-zero verified list, but live sequence-send evidence cannot be confirmed from the current runtime.',
                launch_path=launch_path,
                live_list_path=live_list_path,
                outbound_path=outbound_path,
                record_count=launch_ready_count,
                sequence_name=result.get('sequence_name') or _chosen_action_dict(launch_payload).get('sequence_name') or live_list_result.get('sequence_name'),
                final_url=result.get('final_url') or _chosen_action_dict(launch_payload).get('url') or live_list_result.get('final_url'),
                verification_timestamp=outbound_payload.get('timestamp') or live_list_timestamp,
                evidence=list(outbound_result.get('evidence') or live_list_result.get('evidence') or result.get('evidence') or []),
                launch_timestamp=launch_at.isoformat(),
                next_review_at=launch_at.isoformat(),
                launch_review_at=(launch_at + timedelta(days=LAUNCH_REVIEW_DAYS)).isoformat(),
                repo_visit_review_at=(launch_at + timedelta(days=REPO_VISIT_REVIEW_DAYS)).isoformat(),
                adoption_review_at=(launch_at + timedelta(days=ADOPTION_REVIEW_DAYS)).isoformat(),
            )
            if blocked_payload:
                return blocked_payload
        if launch_ready_count > 0 and live_list_fresh:
            return {
                'generated_at': now.isoformat(),
                'status': 'launch_ready_unverified_send',
                'measurement_pending': False,
                'summary': 'Apollo has a non-zero verified list, but no live sequence-send evidence exists yet.',
                'launch_log': str(launch_path) if launch_path else None,
                'live_list_log': str(live_list_path) if live_list_path else None,
                'outbound_verification_log': str(outbound_path) if outbound_path else None,
                'launch_timestamp': launch_at.isoformat(),
                'record_count': launch_ready_count,
                'sequence_name': result.get('sequence_name') or _chosen_action_dict(launch_payload).get('sequence_name') or live_list_result.get('sequence_name'),
                'final_url': result.get('final_url') or _chosen_action_dict(launch_payload).get('url') or live_list_result.get('final_url'),
                'verification_timestamp': outbound_payload.get('timestamp') or live_list_timestamp,
                'evidence': list(outbound_result.get('evidence') or live_list_result.get('evidence') or result.get('evidence') or []),
                'needs_live_verification': True,
            }

    launch_review_at = launch_at + timedelta(days=LAUNCH_REVIEW_DAYS)
    repo_review_at = launch_at + timedelta(days=REPO_VISIT_REVIEW_DAYS)
    adoption_review_at = launch_at + timedelta(days=ADOPTION_REVIEW_DAYS)

    if not result.get('outcome_ready') or record_count <= 0:
        status = 'not_outcome_ready'
        measurement_pending = False
        next_review_at = launch_at
        summary = 'Apollo launch exists, but the sequence is not yet outcome-ready.'
    elif now < launch_review_at:
        status = 'measurement_pending_launch_window'
        measurement_pending = True
        next_review_at = launch_review_at
        summary = 'Apollo launch is live and in the 7-day launch/reply measurement window.'
    elif now < repo_review_at:
        status = 'measurement_pending_repo_visit_window'
        measurement_pending = True
        next_review_at = repo_review_at
        summary = 'Apollo launch is still inside the 14-day qualified repo-visit window.'
    elif now < adoption_review_at:
        status = 'measurement_pending_codeberg_window'
        measurement_pending = True
        next_review_at = adoption_review_at
        summary = 'Apollo launch is still inside the 30-day Codeberg adoption window.'
    else:
        status = 'review_due'
        measurement_pending = False
        next_review_at = adoption_review_at
        summary = 'Apollo launch has cleared its measurement windows and now needs a replacement/iteration decision.'

    return {
        'generated_at': now.isoformat(),
        'status': status,
        'measurement_pending': measurement_pending,
        'summary': summary,
        'launch_log': str(launch_path) if launch_path else None,
        'live_list_log': str(_latest_live_list_verification()[0]) if _latest_live_list_verification()[0] else None,
        'outbound_verification_log': str(outbound_path) if outbound_path else None,
        'launch_timestamp': launch_at.isoformat(),
        'record_count': record_count,
        'sequence_name': result.get('sequence_name') or _chosen_action_dict(launch_payload).get('sequence_name'),
        'final_url': result.get('final_url') or _chosen_action_dict(launch_payload).get('url'),
        'launch_review_at': launch_review_at.isoformat(),
        'repo_visit_review_at': repo_review_at.isoformat(),
        'adoption_review_at': adoption_review_at.isoformat(),
        'next_review_at': next_review_at.isoformat(),
        'verification_timestamp': outbound_payload.get('timestamp'),
        'evidence': list(outbound_result.get('evidence') or result.get('evidence') or []),
        'needs_live_verification': False,
    }


def write_status(payload: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    lines = [
        '# Apollo Sequence Status',
        '',
        f"- Generated: `{payload.get('generated_at')}`",
        f"- Status: `{payload.get('status')}`",
        f"- Measurement pending: `{payload.get('measurement_pending')}`",
        f"- Summary: {payload.get('summary')}",
        f"- Launch log: `{payload.get('launch_log')}`",
        f"- Launch timestamp: `{payload.get('launch_timestamp')}`",
        f"- Record count: `{payload.get('record_count')}`",
        f"- Live list log: `{payload.get('live_list_log')}`",
        f"- Outbound verification log: `{payload.get('outbound_verification_log')}`",
        f"- Needs live verification: `{payload.get('needs_live_verification')}`",
        f"- Runtime blocker status: `{payload.get('runtime_blocker_status')}`",
        f"- Runtime blocker summary: `{payload.get('runtime_blocker_summary')}`",
        f"- Runtime blocker notes: `{payload.get('runtime_blocker_notes')}`",
        f"- Runtime status log: `{payload.get('runtime_status_log')}`",
        f"- Next review at: `{payload.get('next_review_at')}`",
        f"- 7-day review: `{payload.get('launch_review_at')}`",
        f"- 14-day review: `{payload.get('repo_visit_review_at')}`",
        f"- 30-day review: `{payload.get('adoption_review_at')}`",
    ]
    STATUS_MD.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    payload = build_status()
    write_status(payload)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
