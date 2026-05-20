#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path('/home/mistlight/.openclaw/workspace')
TOOLS_PATH = ROOT / 'TOOLS.md'
PROFILE_DIR = ROOT / '.apollo-playwright'
LOG_DIR = ROOT / 'agents/marketing/logs'
STATUS_PATH = LOG_DIR / 'apollo_status.json'
SUMMARY_PATH = LOG_DIR / 'apollo_status_latest.md'
OUTREACH_LOG = ROOT / 'outreach-log.md'
LOGIN_URL = 'https://app.apollo.io/#/login'
AUTH_ENDPOINT = 'https://app.apollo.io/api/v1/auth/login'
APOLLO_REAL_BROWSER_USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36'


def read_apollo_credentials() -> tuple[str, str]:
    text = TOOLS_PATH.read_text(encoding='utf-8')
    match = re.search(r'^### Apollo\.io\n(?P<section>.*?)(?=^### |\Z)', text, re.M | re.S)
    if not match:
        raise RuntimeError('Apollo.io section not found in TOOLS.md')

    section = match.group('section')
    username_match = (
        re.search(r'Login username:\*\*\s*`([^`]+)`', section)
        or re.search(r'Username:\*\*\s*`([^`]+)`', section)
    )
    password_match = re.search(r'Password:\*\*\s*`([^`]+)`', section)
    if not username_match or not password_match:
        raise RuntimeError('Apollo.io credentials are incomplete in TOOLS.md')
    return username_match.group(1), password_match.group(1)


def append_outreach_note(previous_status: str, current_status: str) -> None:
    existing = OUTREACH_LOG.read_text(encoding='utf-8') if OUTREACH_LOG.exists() else '# Outreach Log\n'
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    block = (
        '\n### Apollo monitor\n'
        f'- **When:** {stamp}\n'
        f'- **Note:** Apollo status changed from `{previous_status}` to `{current_status}`.\n'
    )
    OUTREACH_LOG.write_text(existing.rstrip() + '\n' + block, encoding='utf-8')


def write_markdown_summary(payload: dict) -> None:
    lines = [
        '# Apollo Status',
        '',
        f"- Timestamp: `{payload['timestamp']}`",
        f"- Status: `{payload['status']}`",
        f"- Final URL: `{payload['final_url']}`",
        f"- Login attempted: `{payload['login_attempted']}`",
        f"- Cloudflare/auth blocked: `{payload['cloudflare_blocked']}`",
        f"- Auth endpoint status codes: `{payload['auth_endpoint_status_codes']}`",
        f"- Notes: {payload['notes'] or 'none'}",
    ]
    SUMMARY_PATH.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def monitor() -> dict:
    username, password = read_apollo_credentials()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    auth_status_codes: list[int] = []
    notes: list[str] = []
    cloudflare_blocked = False
    login_attempted = False
    final_url = LOGIN_URL
    status = 'unknown'
    email_verification_required = False

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            executable_path='/usr/bin/chromium',
            viewport={'width': 1366, 'height': 768},
            locale='en-US',
            timezone_id='Europe/Berlin',
            user_agent=APOLLO_REAL_BROWSER_USER_AGENT,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-first-run',
            ],
        )
        try:
            page = context.new_page()

            def on_response(response) -> None:
                nonlocal cloudflare_blocked
                url = response.url
                resource_type = response.request.resource_type
                if url.startswith(AUTH_ENDPOINT):
                    auth_status_codes.append(response.status)
                    if response.status == 403:
                        cloudflare_blocked = True
                        notes.append('Apollo auth endpoint returned 403.')
                try:
                    body = response.text()
                except Exception:
                    return
                if 'Just a moment' in body and (url.startswith(AUTH_ENDPOINT) or resource_type in {'document', 'xhr', 'fetch'}):
                    cloudflare_blocked = True
                    if url.startswith(AUTH_ENDPOINT):
                        notes.append('Apollo auth endpoint returned Cloudflare interstitial content.')
                    else:
                        notes.append(f'Cloudflare interstitial detected in response body from {url}.')

            page.on('response', on_response)
            page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=60000)
            page.wait_for_timeout(4000)
            final_url = page.url

            body_text = ''
            try:
                body_text = page.locator('body').inner_text(timeout=5000)
            except Exception:
                body_text = page.content()
            if 'Just a moment' in body_text:
                cloudflare_blocked = True
                notes.append('Apollo login page shows a Cloudflare/auth interstitial.')

            email_selectors = [
                'input[type="email"]',
                'input[name="email"]',
                'input[placeholder*="email" i]',
            ]
            password_selectors = [
                'input[type="password"]',
                'input[name="password"]',
                'input[placeholder*="password" i]',
            ]

            email = None
            password_input = None
            for selector in email_selectors:
                locator = page.locator(selector).first
                if locator.count():
                    email = locator
                    break
            for selector in password_selectors:
                locator = page.locator(selector).first
                if locator.count():
                    password_input = locator
                    break

            if email and password_input and not cloudflare_blocked:
                email.fill(username)
                password_input.fill(password)
                login_attempted = True

                form = password_input.locator('xpath=ancestor::form[1]')
                submit_candidates = [
                    form.locator('button[type="submit"]').first,
                    form.locator('input[type="submit"]').first,
                    form.get_by_role('button', name=re.compile(r'log\s*in|sign\s*in', re.I)).first,
                    page.locator('button[type="submit"]').first,
                    page.locator('input[type="submit"]').first,
                ]
                clicked = False
                for candidate in submit_candidates:
                    try:
                        if candidate.count():
                            candidate.click(timeout=5000)
                            clicked = True
                            break
                    except Exception:
                        continue
                if not clicked:
                    raise RuntimeError('Apollo login submit control was not found')

                try:
                    page.wait_for_load_state('networkidle', timeout=20000)
                except PlaywrightTimeoutError:
                    notes.append('Timed out waiting for Apollo login network to go idle.')
                page.wait_for_timeout(5000)
                final_url = page.url
            elif not cloudflare_blocked:
                notes.append('Apollo login form fields were not available for automation.')

            try:
                body_after = page.locator('body').inner_text(timeout=5000)
            except Exception:
                body_after = ''
            body_after_lower = body_after.lower()
            if 'Just a moment' in body_after:
                cloudflare_blocked = True
                notes.append('Apollo post-login page still shows Cloudflare/auth interstitial.')
            if '/ato/verify-email' in final_url or (
                '6-digit verification code' in body_after_lower and 'sent to' in body_after_lower and 'email' in body_after_lower
            ):
                email_verification_required = True
                cloudflare_blocked = False
                notes = [
                    note for note in notes
                    if 'Cloudflare' not in note and 'auth endpoint returned 403' not in note
                ]
                notes.append(
                    'Cloudflare is cleared on the real-browser path, but Apollo still requires mailbox/email-code verification for this device.'
                )

            if email_verification_required:
                status = 'ato_email_verification_required'
            elif cloudflare_blocked:
                status = 'cloudflare_auth_blocked'
            elif login_attempted and urlparse(final_url).netloc == 'app.apollo.io' and '/login' not in final_url and '#/login' not in final_url:
                status = 'login_succeeded'
            elif login_attempted:
                if final_url != LOGIN_URL and urlparse(final_url).netloc != 'app.apollo.io':
                    notes.append(f'Apollo login redirected to a non-Apollo auth surface: {final_url}.')
                status = 'still_on_login_page'
            else:
                status = 'login_not_attempted'

            if status == 'still_on_login_page' and not notes:
                notes.append('Apollo remained on the login page after credential submission.')
            if status == 'login_succeeded' and not notes:
                notes.append('Apollo login appears to have completed successfully.')
        finally:
            context.close()

    return {
        'timestamp': datetime.now().astimezone().isoformat(),
        'status': status,
        'final_url': final_url,
        'login_attempted': login_attempted,
        'cloudflare_blocked': cloudflare_blocked,
        'notes': ' '.join(dict.fromkeys(notes)),
        'auth_endpoint_status_codes': auth_status_codes,
    }


def main() -> int:
    previous = None
    try:
        if STATUS_PATH.exists():
            previous = json.loads(STATUS_PATH.read_text(encoding='utf-8'))

        payload = monitor()
        STATUS_PATH.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        write_markdown_summary(payload)

        previous_status = (previous or {}).get('status')
        current_status = payload.get('status')
        if previous_status and previous_status != current_status:
            append_outreach_note(previous_status, current_status)

        print(json.dumps({'ok': True, **payload}, indent=2))
        return 0
    except Exception as exc:
        error_payload = {
            'timestamp': datetime.now().astimezone().isoformat(),
            'status': 'script_failure',
            'final_url': LOGIN_URL,
            'login_attempted': False,
            'cloudflare_blocked': False,
            'notes': str(exc),
            'auth_endpoint_status_codes': [],
        }
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            STATUS_PATH.write_text(json.dumps(error_payload, indent=2), encoding='utf-8')
            write_markdown_summary(error_payload)
        except Exception:
            pass
        print(json.dumps({'ok': False, 'error': str(exc)}, indent=2))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
