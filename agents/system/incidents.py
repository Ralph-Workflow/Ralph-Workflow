#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents/system/logs'
INCIDENTS_PATH = LOG_DIR / 'open_incidents_latest.json'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def incident_key(issue: dict) -> str:
    return f"{issue.get('name', '')}::{issue.get('category', '')}"


def load_incidents() -> dict:
    try:
        payload = json.loads(INCIDENTS_PATH.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return {'updated_at': None, 'incidents': {}}
    if not isinstance(payload, dict):
        return {'updated_at': None, 'incidents': {}}
    payload.setdefault('incidents', {})
    return payload


def save_incidents(payload: dict) -> None:
    payload['updated_at'] = _now()
    INCIDENTS_PATH.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')


def classify_owner(name: str) -> str | None:
    lower = name.lower()
    if 'docs' in lower:
        return 'docs'
    if 'marketing' in lower:
        return 'marketing'
    if 'seo' in lower or 'site' in lower or 'backlink' in lower or 'search' in lower:
        return 'site'
    if 'architecture' in lower:
        return 'architecture'
    if 'health' in lower:
        return 'health'
    return None


def upsert_incidents(current_issues: list[dict]) -> dict:
    store = load_incidents()
    incidents = store.setdefault('incidents', {})
    seen_keys: set[str] = set()

    for issue in current_issues:
        if issue.get('category') == 'escalation_required':
            continue
        key = incident_key(issue)
        seen_keys.add(key)
        item = incidents.get(key) or {
            'key': key,
            'name': issue.get('name'),
            'category': issue.get('category'),
            'first_seen': _now(),
            'repeat_count': 0,
            'owner_domain': classify_owner(str(issue.get('name') or '')),
            'escalation_level': 'none',
            'owner_actions': [],
            'status': 'open',
            'blocked_by': [],
        }
        item['last_seen'] = _now()
        item['repeat_count'] = int(item.get('repeat_count', 0)) + 1
        item['last_error'] = issue.get('last_error', '')
        item['likely_cause'] = issue.get('likely_cause', '')
        item['path'] = issue.get('path')
        if item.get('blocked_by'):
            item['status'] = 'blocked_external'
        elif item.get('status') != 'resolved':
            item['status'] = 'open'

        if item['repeat_count'] >= 2 and item.get('escalation_level') == 'none':
            item['escalation_level'] = 'owner'
        if item['repeat_count'] >= 5 and item.get('escalation_level') in {'none', 'owner'}:
            item['escalation_level'] = 'critical'

        incidents[key] = item

    for key, item in incidents.items():
        if key not in seen_keys and item.get('status') == 'open':
            item['status'] = 'resolved'
            item['closed_at'] = _now()

    save_incidents(store)
    return store


def issue_signature(issue: dict) -> str:
    return '::'.join(
        [
            str(issue.get('name') or ''),
            str(issue.get('category') or ''),
            str(issue.get('last_error') or ''),
            str(issue.get('likely_cause') or ''),
        ]
    )


def owner_action_recent(issue: dict, *, action_type: str, cooldown_minutes: int) -> tuple[bool, dict | None]:
    store = load_incidents()
    incidents = store.setdefault('incidents', {})
    key = issue.get('incident_key') or incident_key(issue)
    item = incidents.get(key)
    if not item:
        return False, None
    current_signature = issue_signature(issue)
    for action in reversed(item.get('owner_actions', []) or []):
        if action.get('action_type') != action_type:
            continue
        if action.get('issue_signature') != current_signature:
            continue
        try:
            action_ts = datetime.fromisoformat(str(action.get('at')))
        except (TypeError, ValueError):
            continue
        elapsed_minutes = (datetime.now(timezone.utc) - action_ts).total_seconds() / 60.0
        if elapsed_minutes < cooldown_minutes:
            enriched = dict(action)
            enriched['elapsed_minutes'] = elapsed_minutes
            return True, enriched
        return False, dict(action)
    return False, None


def record_owner_action(issue: dict, *, action_type: str, ok: bool, detail: str, outcome: str | None = None, blocked_by: list[str] | None = None) -> None:
    store = load_incidents()
    incidents = store.setdefault('incidents', {})
    key = issue.get('incident_key') or incident_key(issue)
    item = incidents.get(key)
    if not item:
        return
    actions = item.setdefault('owner_actions', [])
    actions.append({
        'at': _now(),
        'action_type': action_type,
        'ok': ok,
        'detail': detail,
        'outcome': outcome or ('resolved' if ok else 'no_progress'),
        'issue_signature': issue_signature(issue),
    })
    if blocked_by is not None:
        item['blocked_by'] = blocked_by
        if blocked_by:
            item['status'] = 'blocked_external'
        elif item.get('status') == 'blocked_external':
            item['status'] = 'open'
    if outcome == 'resolved':
        item['status'] = 'resolved'
        item['closed_at'] = _now()
    elif outcome == 'no_progress' and not blocked_by:
        item['status'] = 'open'
    incidents[key] = item
    save_incidents(store)


def incident_escalations(current_issues: list[dict]) -> list[dict]:
    store = upsert_incidents(current_issues)
    escalations: list[dict] = []
    incidents = store.get('incidents', {}) or {}
    for issue in current_issues:
        if issue.get('category') == 'escalation_required':
            continue
        key = incident_key(issue)
        item = incidents.get(key) or {}
        level = item.get('escalation_level')
        if level in {'owner', 'critical'}:
            escalations.append({
                'job_id': issue.get('job_id', '__artifacts__'),
                'name': f"{issue.get('name')}_escalation",
                'category': 'escalation_required',
                'last_error': issue.get('last_error', ''),
                'likely_cause': f"Incident escalation level={level}: {issue.get('name')}:{issue.get('category')} repeat_count={item.get('repeat_count')}",
                'repeats_in_recent_history': item.get('repeat_count', 0),
                'owner_domain': item.get('owner_domain'),
                'incident_key': key,
                'escalation_level': level,
                'blocked_by': item.get('blocked_by', []),
            })
    return escalations
