#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents/marketing/logs'
OUTREACH_LOG = ROOT / 'outreach-log.md'
PRIMARY_HEADING = '## Notes'


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _coerce_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _primary_url(data: dict[str, Any]) -> str:
    payload = _coerce_dict(data.get('submitted_payload'))
    return (
        data.get('primary_url')
        or payload.get('website_url')
        or payload.get('url')
        or payload.get('toolUrl')
        or ''
    )


def _submit_url(data: dict[str, Any]) -> str:
    channel = _coerce_dict(data.get('channel'))
    return channel.get('submit_url') or data.get('submit_url') or data.get('url') or ''


def _channel_name(data: dict[str, Any]) -> str:
    channel = _coerce_dict(data.get('channel'))
    return channel.get('name') or data.get('target') or data.get('action') or 'Unknown submission'


def _is_successful_submission(data: dict[str, Any]) -> bool:
    result = _coerce_dict(data.get('result'))
    response = _coerce_dict(result.get('response'))
    verification = _coerce_dict(data.get('verification'))
    return bool(
        data.get('live_external_action')
        or data.get('ok')
        or response.get('success')
        or verification.get('post_status_code') in {200, 201}
        or result.get('http_code') in {200, 201}
    )


def _review_by(data: dict[str, Any], timestamp: datetime) -> str:
    measurement = _coerce_dict(data.get('measurement_window'))
    for key in ('backlink_review_at', 'listing_review_at', 'review_by'):
        value = measurement.get(key) or data.get(key)
        if isinstance(value, str) and value:
            try:
                return datetime.fromisoformat(value.replace('Z', '+00:00')).date().isoformat()
            except ValueError:
                continue
    return (timestamp.date() + timedelta(days=14)).isoformat()


def _build_entry(data: dict[str, Any], artifact: Path) -> tuple[str, str]:
    timestamp_raw = data.get('timestamp') or data.get('timestamp_utc') or artifact.stem
    try:
        timestamp = datetime.fromisoformat(str(timestamp_raw).replace('Z', '+00:00'))
    except ValueError:
        timestamp = datetime.utcnow()
    date_heading = f"## {timestamp.date().isoformat()}"
    channel_name = _channel_name(data)
    submit_url = _submit_url(data)
    primary_url = _primary_url(data)
    review_by = _review_by(data, timestamp)
    lines = [f"- **{channel_name}** — directory submission sent"]
    if submit_url:
        lines.append(f"  - Submit URL: {submit_url}")
    if primary_url:
        lines.append(f"  - Primary URL: {primary_url}")
    lines.append(f"  - Log: `agents/marketing/logs/{artifact.name}`")
    lines.append(f"  - Review by: {review_by}")
    return date_heading, '\n'.join(lines)


def sync_submission_artifacts(outreach_path: Path = OUTREACH_LOG, logs_dir: Path = LOG_DIR) -> list[str]:
    text = outreach_path.read_text(encoding='utf-8') if outreach_path.exists() else '# Outreach Log\n\n'
    added: list[str] = []
    for artifact in sorted(logs_dir.glob('marketing_*_submission*.json')):
        data = _load_json(artifact)
        if not data or not _is_successful_submission(data):
            continue
        channel_name = _channel_name(data)
        submit_url = _submit_url(data)
        marker = f'`agents/marketing/logs/{artifact.name}`'
        lower = text.lower()
        if marker.lower() in lower:
            continue
        if channel_name and channel_name.lower() in lower and submit_url and submit_url.lower() in lower:
            continue
        date_heading, entry = _build_entry(data, artifact)
        if date_heading in text:
            insert_at = text.find(date_heading) + len(date_heading)
            next_heading = text.find('\n## ', insert_at)
            if next_heading == -1:
                next_heading = text.find('\n' + PRIMARY_HEADING, insert_at)
            if next_heading == -1:
                next_heading = len(text)
            section = text[insert_at:next_heading]
            section = section.rstrip() + '\n' + entry + '\n\n'
            text = text[:insert_at] + section + text[next_heading:]
        else:
            notes_idx = text.find('\n' + PRIMARY_HEADING)
            block = f'\n{date_heading}\n\n{entry}\n'
            if notes_idx == -1:
                text = text.rstrip() + '\n' + block + '\n'
            else:
                text = text[:notes_idx].rstrip() + '\n' + block + '\n' + text[notes_idx:]
        added.append(artifact.name)
    outreach_path.write_text(text.rstrip() + '\n', encoding='utf-8')
    return added


def main() -> int:
    added = sync_submission_artifacts()
    print(json.dumps({'added': added, 'count': len(added)}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
