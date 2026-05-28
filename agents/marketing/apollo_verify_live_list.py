#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents/marketing/logs'
PROFILE_DIR = ROOT / '.apollo-playwright'
LIST_NAME = 'Ralph Workflow — curator follow-up 2026-05-22'
LISTS_URL = 'https://app.apollo.io/#/lists?sortByField=updated_at&sortAscending=false&groupBy[]=labelModality'
SUMMARY_PATH = LOG_DIR / 'apollo_live_list_latest.md'


def _parse_count(page_text: str, list_name: str) -> int | None:
    lines = [line.strip() for line in page_text.splitlines()]
    for idx, line in enumerate(lines):
        if line != list_name:
            continue
        for probe in lines[idx + 1: idx + 8]:
            if probe.isdigit():
                return int(probe)
    return None


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    log_path = LOG_DIR / f"marketing_{now.strftime('%Y-%m-%d')}_apollo_list_verification.json"

    payload: dict = {
        'timestamp': now.isoformat(),
        'run_type': 'marketing-live-execution',
        'chosen_action': {
            'type': 'apollo_list_verification',
            'channel': 'apollo_outreach',
            'title': 'Apollo list verification',
            'list_name': LIST_NAME,
        },
        'why_this_action': {
            'summary': 'Verified whether the live Apollo curator follow-up list is actually usable, so the loop stops treating stale zero-record evidence as truth.',
            'shared_findings_used': [
                'adoption_metrics_latest.json: Codeberg movement is the primary success gate',
                'curator_outreach_queue_latest.json: existing curator follow-up contacts already exist',
                'apollo_status.json: Apollo login is healthy and available for execution',
                'marketing_2026-05-22_apollo_curator_followup_list.json: prior list creation evidence needed a second-pass count check',
            ],
        },
        'result': {
            'status': 'verification_failed',
            'ok': False,
            'live_external_action': True,
            'outcome_ready': False,
            'final_url': LISTS_URL,
            'evidence': [],
            'notes': [],
        },
    }

    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                headless=True,
                executable_path='/usr/bin/chromium',
                viewport={'width': 1400, 'height': 1200},
                locale='en-US',
                timezone_id='Europe/Berlin',
                args=['--disable-blink-features=AutomationControlled', '--no-first-run'],
            )
            try:
                page = context.new_page()
                page.goto(LISTS_URL, wait_until='domcontentloaded', timeout=60000)
                page.wait_for_timeout(8000)
                body = page.locator('body').inner_text(timeout=10000)
                count = _parse_count(body, LIST_NAME)
                final_url = page.url
                payload['result']['final_url'] = final_url
                if LIST_NAME not in body:
                    payload['result']['notes'].append('Apollo lists page loaded, but the target list name was not visible.')
                elif count is None:
                    payload['result']['notes'].append('Apollo lists page loaded, but the visible record count could not be parsed.')
                elif count <= 0:
                    payload['result']['status'] = 'verified_zero_records'
                    payload['result']['ok'] = True
                    payload['result']['outcome_ready'] = False
                    payload['result']['evidence'].append(f"Apollo UI shows list '{LIST_NAME}' with 0 visible records.")
                    payload['result']['notes'].append('Do not count Apollo as shipped until the imported list is non-zero or a live sequence is launched.')
                else:
                    payload['result']['status'] = 'verified_nonzero_records'
                    payload['result']['ok'] = True
                    payload['result']['outcome_ready'] = True
                    payload['result']['record_count'] = count
                    payload['result']['evidence'].append(f"Apollo UI shows list '{LIST_NAME}' with {count} visible records.")
                    payload['result']['evidence'].append('The curator follow-up asset is now usable for sequence launch with a Codeberg-primary CTA.')
            finally:
                context.close()
    except PlaywrightTimeoutError as exc:
        payload['result']['notes'].append(f'Playwright timeout while verifying Apollo list: {exc}')
    except Exception as exc:
        payload['result']['notes'].append(str(exc))

    log_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')

    result = payload['result']
    lines = [
        '# Apollo Live List Verification',
        '',
        f"- Timestamp: `{payload['timestamp']}`",
        f"- List: `{LIST_NAME}`",
        f"- Status: `{result.get('status')}`",
        f"- Final URL: `{result.get('final_url')}`",
        f"- Record count: `{result.get('record_count', 'unknown')}`",
        f"- Outcome ready: `{result.get('outcome_ready')}`",
    ]
    for item in result.get('evidence', []):
        lines.append(f'- Evidence: {item}')
    for item in result.get('notes', []):
        lines.append(f'- Note: {item}')
    SUMMARY_PATH.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    print(json.dumps(payload, indent=2))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
