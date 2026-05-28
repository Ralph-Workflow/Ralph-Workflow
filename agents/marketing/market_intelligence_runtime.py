from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents/marketing/logs'
MARKET_INTELLIGENCE_FILE = LOG_DIR / 'market_intelligence_latest.json'
CONSUMPTION_FILE = LOG_DIR / 'market_intelligence_consumption_latest.json'

RUNTIME_PROVEN_CONSUMERS = {
    'agents/marketing/run.py',
    'agents/marketing/reddit_monitor.py',
}

PROMPT_GUIDED_CONSUMERS = {
    'agent-architecture-watchdog',
    'ralph-site-owner-loop',
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_consumption_payload() -> dict[str, Any]:
    if not CONSUMPTION_FILE.exists():
        return {
            'schema_version': 'market-intelligence-consumption.v1',
            'updated_at': None,
            'shared_artifact': str(MARKET_INTELLIGENCE_FILE),
            'runtime_proven_consumers': sorted(RUNTIME_PROVEN_CONSUMERS),
            'prompt_guided_consumers': sorted(PROMPT_GUIDED_CONSUMERS),
            'producer': None,
            'consumers': {},
        }
    try:
        data = json.loads(CONSUMPTION_FILE.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        data = {}
    data.setdefault('schema_version', 'market-intelligence-consumption.v1')
    data['shared_artifact'] = str(MARKET_INTELLIGENCE_FILE)
    data['runtime_proven_consumers'] = sorted(RUNTIME_PROVEN_CONSUMERS)
    data['prompt_guided_consumers'] = sorted(PROMPT_GUIDED_CONSUMERS)
    data.setdefault('producer', None)
    data.setdefault('consumers', {})
    data.setdefault('updated_at', None)
    return data


def _write_consumption_payload(payload: dict[str, Any]) -> None:
    payload['updated_at'] = _now()
    CONSUMPTION_FILE.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')


def record_market_intelligence_production(producer: str, payload: dict[str, Any] | None = None) -> None:
    state = _read_consumption_payload()
    artifact_generated_at = None
    if payload:
        artifact_generated_at = payload.get('generated_at') or payload.get('timestamp')
    state['producer'] = {
        'name': producer,
        'recorded_at': _now(),
        'artifact_generated_at': artifact_generated_at,
    }
    _write_consumption_payload(state)


def load_market_intelligence(consumer: str, *, required: bool = False) -> dict[str, Any] | None:
    state = _read_consumption_payload()
    detail: dict[str, Any] = {
        'consumer': consumer,
        'recorded_at': _now(),
        'required': required,
    }

    if not MARKET_INTELLIGENCE_FILE.exists():
        detail['status'] = 'missing'
        state['consumers'][consumer] = detail
        _write_consumption_payload(state)
        return None

    try:
        payload = json.loads(MARKET_INTELLIGENCE_FILE.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError) as exc:
        detail['status'] = 'unreadable'
        detail['error'] = str(exc)
        state['consumers'][consumer] = detail
        _write_consumption_payload(state)
        return None

    detail['status'] = 'loaded'
    detail['artifact_generated_at'] = payload.get('generated_at') or payload.get('timestamp')
    detail['summary_report'] = payload.get('summary_report')
    detail['competitor_count'] = len(payload.get('competitors', {}))
    detail['comparison_page_count'] = len(payload.get('comparison_pages', []))
    state['consumers'][consumer] = detail
    _write_consumption_payload(state)
    return payload


def record_market_intelligence_skip(consumer: str, reason: str) -> None:
    state = _read_consumption_payload()
    state['consumers'][consumer] = {
        'consumer': consumer,
        'recorded_at': _now(),
        'status': 'skipped',
        'reason': reason,
    }
    _write_consumption_payload(state)
