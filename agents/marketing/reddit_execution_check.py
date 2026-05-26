#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents/marketing/logs'
STATUS_PATH = LOG_DIR / 'reddit_execution_status_latest.json'
SUMMARY_PATH = LOG_DIR / 'reddit_execution_status_latest.md'

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.marketing.reddit_post import expected_username, get_cdp_http_url


def _raw_probe() -> dict:
    urls = [
        'https://www.reddit.com/login/',
        'https://old.reddit.com/login',
        'https://www.reddit.com/api/me.json',
    ]
    results = []
    for url in urls:
        try:
            response = requests.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
            results.append({'url': url, 'status_code': response.status_code})
        except Exception as exc:
            results.append({'url': url, 'error': str(exc)})
    return {'urls': results}


def _browser_probe() -> dict:
    expected = expected_username()
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(get_cdp_http_url())
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            try:
                page.goto('https://www.reddit.com/api/me.json', timeout=30000)
                page.wait_for_timeout(2000)
                payload = json.loads(page.locator('body').inner_text(timeout=5000))
            finally:
                page.close()
        finally:
            browser.close()
    username = ((payload.get('data') or {}).get('name') or '').strip()
    return {
        'username': username,
        'expected_username': expected,
        'matches_expected': bool(expected and username and username.lower() == expected.lower()),
        'has_session': bool(username),
    }


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    raw_probe = _raw_probe()
    browser_probe = _browser_probe()

    if browser_probe['has_session'] and browser_probe['matches_expected']:
        status = 'browser_session_ready'
        blocking_reason = None
        notes = (
            'Direct requests from this server IP are still blocked by Reddit, '
            'but the live Chromium session is authenticated and usable.'
        )
    elif browser_probe['has_session']:
        status = 'wrong_account'
        blocking_reason = (
            f"Live Chromium session is logged into u/{browser_probe['username']}, "
            f"expected u/{browser_probe['expected_username']}."
        )
        notes = 'Reddit browser lane exists, but it is on the wrong account.'
    else:
        status = 'not_logged_in'
        blocking_reason = 'No authenticated Reddit browser session was available through the live Chromium attach path.'
        notes = 'Direct IP checks remain blocked and the browser lane was not authenticated.'

    payload = {
        'generated_at': datetime.now().astimezone().isoformat(),
        'status': status,
        'ok': status == 'browser_session_ready',
        'blocking_reason': blocking_reason,
        'notes': notes,
        'raw_request_probe': raw_probe,
        'browser_probe': browser_probe,
    }
    STATUS_PATH.write_text(json.dumps(payload, indent=2), encoding='utf-8')

    raw_status_summary = ', '.join(
        f"{item['url']} -> {item.get('status_code', 'ERR')}"
        for item in raw_probe['urls']
    )

    lines = [
        '# Reddit Execution Status',
        '',
        f"- Generated at: `{payload['generated_at']}`",
        f"- Status: `{status}`",
        f"- Browser username: `{browser_probe['username'] or 'unknown'}`",
        f"- Expected username: `{browser_probe['expected_username'] or 'unknown'}`",
        f"- Raw request statuses: `{raw_status_summary}`",
        f"- Notes: {notes}",
    ]
    if blocking_reason:
        lines.append(f'- Blocking reason: {blocking_reason}')
    SUMMARY_PATH.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    print(json.dumps(payload, indent=2))
    return 0 if payload['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
