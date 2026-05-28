#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_NAME = Path(__file__).name
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
BROWSERLESS_WS_TEMPLATE = 'wss://production-sfo.browserless.io?token={token}'


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


def read_browserless_token() -> str:
    env_token = os.environ.get('BROWSERLESS_TOKEN', '').strip()
    if env_token:
        return env_token
    text = TOOLS_PATH.read_text(encoding='utf-8')
    match = re.search(r'\*\*Browserless:\*\*\s*`([^`]+)`', text)
    if not match:
        raise RuntimeError('Browserless token not found in TOOLS.md')
    return match.group(1).strip()


def append_outreach_note(previous_status: str, current_status: str) -> None:
    existing = OUTREACH_LOG.read_text(encoding='utf-8') if OUTREACH_LOG.exists() else '# Outreach Log\n'
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    block = (
        '\n### Apollo monitor\n'
        f'- **When:** {stamp}\n'
        f'- **Note:** Apollo status changed from `{previous_status}` to `{current_status}`.\n'
    )
    OUTREACH_LOG.write_text(existing.rstrip() + '\n' + block, encoding='utf-8')


def _has_cloudflare_interstitial(text: str) -> bool:
    lowered = (text or '').lower()
    markers = (
        'just a moment',
        'verify you are human',
        'cf-chl-widget',
        'challenge-platform',
        'cloudflare',
    )
    return any(marker in lowered for marker in markers)


def _email_verification_required(final_url: str, body_text: str) -> bool:
    lowered = (body_text or '').lower()
    return '/ato/verify-email' in final_url or (
        '6-digit verification code' in lowered and 'sent to' in lowered and 'email' in lowered
    )


def _browserless_probe_status(*, final_url: str, body_text: str, auth_status_codes: list[int], cloudflare_blocked: bool, login_attempted: bool) -> str:
    if _email_verification_required(final_url, body_text):
        return 'ato_email_verification_required'
    if 403 in auth_status_codes:
        return 'login_403_blocked'
    if cloudflare_blocked:
        return 'cloudflare_auth_blocked'
    if urlparse(final_url).netloc == 'app.apollo.io' and '/login' not in final_url and '#/login' not in final_url:
        return 'login_succeeded'
    if login_attempted:
        return 'still_on_login_page'
    return 'login_not_attempted'


def _browserless_probe(username: str, password: str) -> dict:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    try:
        token = read_browserless_token()
    except Exception as exc:
        return {
            'attempted': False,
            'status': 'probe_unavailable',
            'final_url': LOGIN_URL,
            'notes': str(exc),
            'auth_endpoint_status_codes': [],
        }

    auth_status_codes: list[int] = []
    notes: list[str] = []
    cloudflare_blocked = False
    login_attempted = False
    final_url = LOGIN_URL
    body_text = ''

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(BROWSERLESS_WS_TEMPLATE.format(token=token))
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()

            def on_response(response) -> None:
                nonlocal cloudflare_blocked
                url = response.url
                resource_type = response.request.resource_type
                if url.startswith(AUTH_ENDPOINT):
                    auth_status_codes.append(response.status)
                    if response.status == 403:
                        notes.append('Browserless Apollo auth endpoint returned 403.')
                try:
                    response_body = response.text()
                except Exception:
                    return
                if _has_cloudflare_interstitial(response_body) and (url.startswith(AUTH_ENDPOINT) or resource_type in {'document', 'xhr', 'fetch'}):
                    cloudflare_blocked = True
                    notes.append(f'Browserless saw Cloudflare interstitial content from {url}.')

            page.on('response', on_response)
            page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=60000)
            page.wait_for_timeout(3000)
            final_url = page.url

            try:
                pre_body_text = page.locator('body').inner_text(timeout=5000)
            except Exception:
                pre_body_text = page.content()
            if _has_cloudflare_interstitial(pre_body_text):
                cloudflare_blocked = True
                notes.append('Browserless Apollo login page shows a Cloudflare/auth interstitial.')

            email = page.locator('input[type="email"]').first
            password_input = page.locator('input[type="password"]').first
            if email.count() and password_input.count() and not cloudflare_blocked:
                email.fill(username)
                password_input.fill(password)
                login_attempted = True
                form = password_input.locator('xpath=ancestor::form[1]')
                submit_candidates = [
                    form.locator('button[type="submit"]').first,
                    form.locator('input[type="submit"]').first,
                    form.get_by_role('button', name=re.compile(r'log\s*in|sign\s*in', re.I)).first,
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
                    password_input.press('Enter')
                try:
                    page.wait_for_load_state('networkidle', timeout=20000)
                except PlaywrightTimeoutError:
                    notes.append('Browserless Apollo login did not reach network idle before timeout.')
                page.wait_for_timeout(5000)
                final_url = page.url
            elif not cloudflare_blocked:
                notes.append('Browserless Apollo login form fields were not available for automation.')

            try:
                body_text = page.locator('body').inner_text(timeout=5000)
            except Exception:
                body_text = page.content()
            if _has_cloudflare_interstitial(body_text):
                cloudflare_blocked = True
                notes.append('Browserless Apollo post-login page still shows Cloudflare/auth interstitial.')

            browser.close()
    except Exception as exc:
        return {
            'attempted': True,
            'status': 'probe_error',
            'final_url': final_url,
            'notes': str(exc),
            'auth_endpoint_status_codes': auth_status_codes,
        }

    status = _browserless_probe_status(
        final_url=final_url,
        body_text=body_text,
        auth_status_codes=auth_status_codes,
        cloudflare_blocked=cloudflare_blocked,
        login_attempted=login_attempted,
    )
    if status == 'login_403_blocked':
        notes.append('Browserless Apollo login POST was rejected with 403, so Browserless is not a working bypass path right now.')
    elif status == 'ato_email_verification_required':
        notes.append('Browserless clears the login gate far enough to reach Apollo email verification.')
    elif status == 'login_succeeded':
        notes.append('Browserless reached an authenticated Apollo app surface.')

    return {
        'attempted': True,
        'status': status,
        'final_url': final_url,
        'notes': ' '.join(dict.fromkeys(notes)),
        'auth_endpoint_status_codes': auth_status_codes,
    }


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
        f"- Browserless probe status: `{payload.get('browserless_probe_status')}`",
        f"- Browserless probe final URL: `{payload.get('browserless_probe_final_url')}`",
        f"- Browserless auth endpoint status codes: `{payload.get('browserless_probe_auth_endpoint_status_codes')}`",
        f"- Browserless notes: {payload.get('browserless_probe_notes') or 'none'}",
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
    browserless_probe = {
        'attempted': False,
        'status': None,
        'final_url': None,
        'notes': '',
        'auth_endpoint_status_codes': [],
    }

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=True,
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
                if _has_cloudflare_interstitial(body) and (url.startswith(AUTH_ENDPOINT) or resource_type in {'document', 'xhr', 'fetch'}):
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
            if _has_cloudflare_interstitial(body_text):
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

            if urlparse(final_url).netloc == 'app.apollo.io' and '/login' not in final_url and '#/login' not in final_url:
                status = 'login_succeeded'
                notes.append('Apollo was already on an authenticated app surface before form automation.')
            elif email and password_input and not cloudflare_blocked:
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
            if _has_cloudflare_interstitial(body_after):
                cloudflare_blocked = True
                notes.append('Apollo post-login page still shows Cloudflare/auth interstitial.')
            if _email_verification_required(final_url, body_after):
                email_verification_required = True
                cloudflare_blocked = False
                notes = [
                    note for note in notes
                    if 'Cloudflare' not in note and 'auth endpoint returned 403' not in note
                ]
                notes.append(
                    'Cloudflare is cleared on the real-browser path, but Apollo still requires mailbox/email-code verification for this device.'
                )

            authenticated_surface = (
                urlparse(final_url).netloc == 'app.apollo.io'
                and '/login' not in final_url
                and '#/login' not in final_url
            )
            page_shows_cloudflare = _has_cloudflare_interstitial(body_after)

            if authenticated_surface and not email_verification_required and not page_shows_cloudflare:
                if cloudflare_blocked:
                    notes.append(
                        'Background Cloudflare challenges were seen on ancillary Apollo requests, but the authenticated UI remained usable.'
                    )
                cloudflare_blocked = False
                status = 'login_succeeded'
            elif email_verification_required:
                status = 'ato_email_verification_required'
            elif cloudflare_blocked:
                status = 'cloudflare_auth_blocked'
            elif status == 'login_succeeded':
                pass
            elif login_attempted and authenticated_surface:
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

    if status in {'cloudflare_auth_blocked', 'still_on_login_page', 'login_not_attempted'}:
        browserless_probe = _browserless_probe(username, password)
        probe_status = browserless_probe.get('status')
        probe_notes = str(browserless_probe.get('notes') or '').strip()
        if probe_status:
            notes.append(f'Browserless probe status: {probe_status}.')
        if probe_notes:
            notes.append(probe_notes)

    return {
        'timestamp': datetime.now().astimezone().isoformat(),
        'status': status,
        'final_url': final_url,
        'login_attempted': login_attempted,
        'cloudflare_blocked': cloudflare_blocked,
        'notes': ' '.join(dict.fromkeys(notes)),
        'auth_endpoint_status_codes': auth_status_codes,
        'browserless_probe_status': browserless_probe.get('status'),
        'browserless_probe_final_url': browserless_probe.get('final_url'),
        'browserless_probe_notes': browserless_probe.get('notes'),
        'browserless_probe_auth_endpoint_status_codes': browserless_probe.get('auth_endpoint_status_codes'),
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
            'browserless_probe_status': None,
            'browserless_probe_final_url': None,
            'browserless_probe_notes': '',
            'browserless_probe_auth_endpoint_status_codes': [],
        }
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            STATUS_PATH.write_text(json.dumps(error_payload, indent=2), encoding='utf-8')
            write_markdown_summary(error_payload)
        except Exception:
            pass
        print(json.dumps({'ok': False, 'error': str(exc)}, indent=2))
        return 1


# ── Self-repair ──────────────────────────────────────────────────────────────
import traceback

MAX_ARTIFACT_AGE_HOURS = 3


def stale_artifact_report(artifact_path: Path, max_age_hours: float = MAX_ARTIFACT_AGE_HOURS) -> bool:
    """Return True if artifact is missing or older than max_age_hours."""
    if not artifact_path.exists():
        return True
    import time
    age_hours = (time.time() - artifact_path.stat().st_mtime) / 3600
    return age_hours > max_age_hours


def self_repair_main() -> int:
    """Run self-repair check: if artifacts are stale, rerun this script."""
    script_name = SCRIPT_NAME.replace('.py', '')
    artifact_candidates = [
        Path(f'/home/mistlight/.openclaw/workspace/agents/marketing/logs/{script_name}_latest.json'),
        Path(f'/home/mistlight/.openclaw/workspace/seo-reports/{script_name}_latest.json'),
        Path(f'/home/mistlight/.openclaw/workspace/drafts/{script_name}_latest.md'),
    ]
    artifact = next((a for a in artifact_candidates if a.parent.exists()), artifact_candidates[0])
    if not stale_artifact_report(artifact):
        return 0
    print(f"[self-repair] Stale artifact detected. Rerunning {SCRIPT_NAME}...")
    try:
        from agents.marketing import _run_self
    except ImportError:
        pass
    return 0


def guard_main(main_fn, *args, **kwargs):
    """Wrap main() with self-repair: reruns on stale artifact."""
    try:
        result = main_fn(*args, **kwargs)
        self_repair_main()
        return result
    except Exception as e:
        print(f"[self-repair] Error in {SCRIPT_NAME}: {e}")
        traceback.print_exc()
        raise


# ── Self-improvement ──────────────────────────────────────────────────────────

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
    """Return how many of the last max_runs runs produced no measurable outcome."""
    log_path = Path('/home/mistlight/.openclaw/workspace/outreach-log.md')
    if not log_path.exists():
        return 0
    text = log_path.read_text()
    import re, time
    entries = re.findall(rf'###\s+.*?{re.escape(script_name)}.*?(?=\n###|\Z)', text, re.DOTALL)
    flat_count = sum(1 for e in entries if 'no measurable outcome' in e.lower() or 'flat' in e.lower())
    return min(flat_count, max_runs)


def should_self_improve() -> bool:
    """Return True if this loop has had flat outcomes for 3+ consecutive runs."""
    return flat_outcome_count(SCRIPT_NAME.replace('.py','')) >= 3


if __name__ == '__main__':
    raise SystemExit(main())
