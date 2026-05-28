#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT = Path('/home/mistlight/.openclaw/workspace')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
LOG_DIR = ROOT / 'agents/marketing/logs'
STATUS_PATH = LOG_DIR / 'apollo_sequence_status_latest.json'
OUTPUT_MD = LOG_DIR / 'apollo_outbound_verification_latest.md'
PROFILE_DIR = ROOT / '.apollo-playwright'
SEQUENCES_URL = 'https://app.apollo.io/#/sequences?sortAscending=false&sortByField=lastUsedAt&page=1'
CODEBERG_PRIMARY = 'https://codeberg.org/RalphWorkflow/Ralph-Workflow'
GITHUB_MIRROR = 'https://github.com/Ralph-Workflow/Ralph-Workflow'
TARGET_SEQUENCE_PATTERN = re.compile(r'ralph workflow', re.I)


def _refresh_dependent_truths() -> dict:
    refreshed: list[str] = []
    errors: list[str] = []
    try:
        from agents.marketing import apollo_sequence_status
        apollo_sequence_status.main()
        refreshed.append('apollo_sequence_status_latest')
    except Exception as exc:
        errors.append(f'apollo_sequence_status_latest: {exc}')
    try:
        from agents.marketing import marketing_workflow_audit
        marketing_workflow_audit.main()
        refreshed.append('marketing_workflow_audit_latest')
    except Exception as exc:
        errors.append(f'marketing_workflow_audit_latest: {exc}')
    try:
        from agents.marketing import outcome_execution_board_runner
        outcome_execution_board_runner.main()
        refreshed.append('marketing_execution_board_latest')
    except Exception as exc:
        errors.append(f'marketing_execution_board_latest: {exc}')
    return {
        'ok': not errors,
        'refreshed': refreshed,
        'errors': errors,
    }


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _parse_json_response(response) -> dict:
    try:
        return json.loads(response.text())
    except Exception:
        return {}


def _verify_live_sequence_from_apollo(now: datetime) -> dict | None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
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
            with page.expect_response(lambda r: '/api/v1/emailer_campaigns/search' in r.url, timeout=60000) as search_info:
                page.goto(SEQUENCES_URL, wait_until='domcontentloaded', timeout=60000)
                page.wait_for_timeout(12000)
            search_payload = _parse_json_response(search_info.value)
            campaigns = search_payload.get('emailer_campaigns') or []
            target = None
            for campaign in campaigns:
                name = str(campaign.get('name') or '')
                if not TARGET_SEQUENCE_PATTERN.search(name):
                    continue
                if not bool(campaign.get('active')):
                    continue
                target = campaign
                break
            if target is None:
                return None

            campaign_id = str(target.get('id') or '').strip()
            detail_url = f'https://app.apollo.io/#/sequences/{campaign_id}'
            with page.expect_response(lambda r: f'/api/v1/emailer_campaigns/{campaign_id}?' in r.url, timeout=60000) as detail_info:
                page.goto(detail_url, wait_until='domcontentloaded', timeout=60000)
                page.wait_for_timeout(12000)
            detail_payload = _parse_json_response(detail_info.value)
            campaign = detail_payload.get('emailer_campaign') or target
            templates = detail_payload.get('emailer_templates') or []
            template_text = '\n'.join(
                str(template.get('body_text') or '') + '\n' + str(template.get('body_html') or '')
                for template in templates
            )
            codeberg_present = CODEBERG_PRIMARY in template_text
            github_present = GITHUB_MIRROR in template_text
            codeberg_primary_order_ok = False
            if codeberg_present and github_present:
                codeberg_primary_order_ok = template_text.index(CODEBERG_PRIMARY) < template_text.index(GITHUB_MIRROR)

            contact_statuses = campaign.get('contact_statuses') or {}
            active_contacts = int(contact_statuses.get('active') or 0)
            queued_not_sent = int(contact_statuses.get('not_sent') or 0)
            delivered = int(campaign.get('unique_delivered') or 0)
            clicked = int(campaign.get('unique_clicked') or 0)
            replied = int(campaign.get('unique_replied') or 0)
            spam_blocked = int(campaign.get('unique_spam_blocked') or 0)
            live_signal = active_contacts > 0 or queued_not_sent > 0 or delivered > 0
            if not (live_signal and codeberg_present):
                return None

            evidence = [
                f"Apollo sequence '{campaign.get('name')}' is active with {active_contacts} active contacts, {queued_not_sent} not yet sent, and {delivered} delivered.",
                f"Sequence detail contains the Codeberg-primary CTA: {CODEBERG_PRIMARY}",
            ]
            if github_present:
                evidence.append(f"Sequence detail also contains the GitHub mirror secondary CTA: {GITHUB_MIRROR}")
            if codeberg_present and github_present:
                order_note = 'Codeberg appears before GitHub in the outbound copy.' if codeberg_primary_order_ok else 'GitHub appears before Codeberg in the outbound copy.'
                evidence.append(order_note)
            if clicked or replied or spam_blocked:
                evidence.append(
                    f"Observed downstream activity on the live sequence: {clicked} clicks, {replied} replies, {spam_blocked} spam-blocked."
                )

            return {
                'status': 'verified_live_sequence',
                'ok': True,
                'live_external_action': True,
                'outcome_ready': True,
                'record_count': active_contacts + queued_not_sent,
                'sequence_name': campaign.get('name'),
                'final_url': detail_url,
                'needs_live_verification': False,
                'last_used_at': campaign.get('last_used_at'),
                'active_contacts': active_contacts,
                'not_sent_contacts': queued_not_sent,
                'delivered_contacts': delivered,
                'clicked_contacts': clicked,
                'replied_contacts': replied,
                'spam_blocked_contacts': spam_blocked,
                'codeberg_primary_present': codeberg_present,
                'github_mirror_present': github_present,
                'codeberg_primary_order_ok': codeberg_primary_order_ok,
                'evidence': evidence,
                'notes': ['Apollo live sequence state was verified directly from the authenticated sequence detail UI/API.'],
            }
        finally:
            context.close()


def build_verification(now: datetime | None = None) -> dict:
    now = now or datetime.now().astimezone()
    status_payload = _load_json(STATUS_PATH)
    status = str(status_payload.get('status') or '').strip()
    record_count = int(status_payload.get('record_count') or 0)
    needs_live_verification = bool(status_payload.get('needs_live_verification'))

    live_result = None
    try:
        live_result = _verify_live_sequence_from_apollo(now)
    except PlaywrightTimeoutError as exc:
        live_result = {
            'status': 'verification_timeout',
            'ok': False,
            'live_external_action': False,
            'outcome_ready': False,
            'record_count': record_count,
            'sequence_name': status_payload.get('sequence_name'),
            'final_url': status_payload.get('final_url'),
            'needs_live_verification': needs_live_verification,
            'evidence': [],
            'notes': [f'Playwright timeout while verifying Apollo live sequence: {exc}'],
        }
    except Exception as exc:
        live_result = {
            'status': 'verification_failed',
            'ok': False,
            'live_external_action': False,
            'outcome_ready': False,
            'record_count': record_count,
            'sequence_name': status_payload.get('sequence_name'),
            'final_url': status_payload.get('final_url'),
            'needs_live_verification': needs_live_verification,
            'evidence': [],
            'notes': [str(exc)],
        }

    if live_result and live_result.get('status') == 'verified_live_sequence':
        verification_result = live_result
    elif status == 'verified_live_sequence':
        verification_result = {
            'status': 'verified_live_sequence',
            'ok': True,
            'live_external_action': True,
            'outcome_ready': True,
            'record_count': record_count,
            'sequence_name': status_payload.get('sequence_name'),
            'final_url': status_payload.get('final_url'),
            'needs_live_verification': False,
            'runtime_blocker_status': status_payload.get('runtime_blocker_status'),
            'evidence': list(status_payload.get('evidence') or []),
            'notes': ['Apollo already has explicit live sequence evidence.'],
        }
    elif status == 'launch_ready_unverified_send' and record_count > 0:
        verification_result = {
            'status': 'launch_ready_needs_send_confirmation',
            'ok': True,
            'live_external_action': False,
            'outcome_ready': False,
            'record_count': record_count,
            'sequence_name': status_payload.get('sequence_name'),
            'final_url': status_payload.get('final_url'),
            'needs_live_verification': needs_live_verification,
            'runtime_blocker_status': status_payload.get('runtime_blocker_status'),
            'evidence': list(status_payload.get('evidence') or []),
            'notes': ['Apollo has a non-zero verified list, but no evidence yet that the sequence is actively sending.'],
        }
    elif status == 'runtime_auth_blocked' and record_count > 0:
        verification_result = {
            'status': 'runtime_auth_blocked',
            'ok': False,
            'live_external_action': False,
            'outcome_ready': False,
            'record_count': record_count,
            'sequence_name': status_payload.get('sequence_name'),
            'final_url': status_payload.get('final_url'),
            'needs_live_verification': needs_live_verification,
            'runtime_blocker_status': status_payload.get('runtime_blocker_status'),
            'evidence': list(status_payload.get('evidence') or []),
            'notes': [str(status_payload.get('runtime_blocker_summary') or 'Apollo has a non-zero verified list, but the current runtime is auth-blocked before send confirmation can be checked.')],
        }
    elif status == 'not_outcome_ready':
        verification_result = {
            'status': 'not_outcome_ready',
            'ok': False,
            'live_external_action': False,
            'outcome_ready': False,
            'record_count': record_count,
            'sequence_name': status_payload.get('sequence_name'),
            'final_url': status_payload.get('final_url'),
            'needs_live_verification': needs_live_verification,
            'runtime_blocker_status': status_payload.get('runtime_blocker_status'),
            'evidence': list(status_payload.get('evidence') or []),
            'notes': ['Apollo launch evidence exists, but the asset is still not usable.'],
        }
    else:
        verification_result = live_result or {
            'status': 'no_usable_apollo_asset',
            'ok': False,
            'live_external_action': False,
            'outcome_ready': False,
            'record_count': record_count,
            'sequence_name': status_payload.get('sequence_name'),
            'final_url': status_payload.get('final_url'),
            'needs_live_verification': needs_live_verification,
            'runtime_blocker_status': status_payload.get('runtime_blocker_status'),
            'evidence': [],
            'notes': ['No usable Apollo outbound asset is visible yet.'],
        }

    payload = {
        'timestamp': now.isoformat(),
        'run_type': 'marketing-verification',
        'chosen_action': {
            'type': 'apollo_outbound_verification',
            'channel': 'apollo_outreach',
            'title': 'Apollo outbound verification',
        },
        'why_this_action': {
            'summary': 'Verify real live Apollo sequence state from the authenticated UI/API so managed outbound cannot stay stuck on launch-ready packet truth.',
            'shared_findings_used': [
                'apollo_sequence_status_latest.json: canonical Apollo state snapshot',
                'marketing_workflow_audit_latest.json: managed outbound must prove usability before counting as progress',
                'Apollo authenticated sequence detail UI/API: live Ralph Workflow sequence content and delivery stats',
            ],
        },
        'result': verification_result,
    }
    return payload


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    payload = build_verification()
    log_path = LOG_DIR / f"marketing_{datetime.now().astimezone().strftime('%Y-%m-%d_%H%M%S')}_apollo_outbound_verification.json"
    log_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    refresh = _refresh_dependent_truths()
    payload['post_verification_refresh'] = refresh
    result = payload['result']
    if refresh.get('refreshed'):
        result.setdefault('notes', []).append(
            'Refreshed dependent audit/board artifacts: ' + ', '.join(refresh['refreshed']) + '.'
        )
    if refresh.get('errors'):
        result.setdefault('notes', []).append(
            'Dependent artifact refresh errors: ' + '; '.join(refresh['errors']) + '.'
        )
    log_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    lines = [
        '# Apollo Outbound Verification',
        '',
        f"- Timestamp: `{payload['timestamp']}`",
        f"- Status: `{result.get('status')}`",
        f"- Outcome ready: `{result.get('outcome_ready')}`",
        f"- Live external action: `{result.get('live_external_action')}`",
        f"- Record count: `{result.get('record_count')}`",
        f"- Sequence name: `{result.get('sequence_name')}`",
        f"- Needs live verification: `{result.get('needs_live_verification')}`",
        f"- Runtime blocker status: `{result.get('runtime_blocker_status')}`",
        f"- Final URL: `{result.get('final_url')}`",
    ]
    for note in result.get('notes', []):
        lines.append(f'- Note: {note}')
    if refresh.get('refreshed'):
        lines.append(f"- Refreshed artifacts: {', '.join(refresh['refreshed'])}")
    if refresh.get('errors'):
        lines.append(f"- Refresh errors: {'; '.join(refresh['errors'])}")
    for item in result.get('evidence', []):
        lines.append(f'- Evidence: {item}')
    OUTPUT_MD.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
