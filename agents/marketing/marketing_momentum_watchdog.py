#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.marketing.channel_discovery import ACTIVE_CHANNEL_NAMES, RETIRED_CHANNELS
SEO = ROOT / 'seo-reports'
LOG_JSONL = ROOT / 'agents/marketing/logs/reddit_posts.jsonl'
RETRO = ROOT / 'agents/marketing/reddit_retrospective.py'
SITE_FETCH = 'https://ralphworkflow.com'
STATUS_DIR = ROOT / 'agents/marketing/logs'
STATUS_PATH = STATUS_DIR / 'marketing_momentum_watchdog.json'
ADOPTION_PATH = STATUS_DIR / 'adoption_metrics_latest.json'
AUDIT_PATH = STATUS_DIR / 'marketing_workflow_audit_latest.json'
APOLLO_STATUS_PATH = STATUS_DIR / 'apollo_status.json'


def newest_report() -> Path | None:
    reports = sorted(SEO.glob('reddit_monitor_*.md'))
    return reports[-1] if reports else None


def newest_post_time() -> datetime | None:
    if not LOG_JSONL.exists():
        return None
    last = None
    for line in LOG_JSONL.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            ts = row.get('timestamp')
            if ts:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    dt = dt.astimezone()
                last = dt if last is None or dt > last else last
        except Exception:
            continue
    return last


def append_note(text: str) -> None:
    path = ROOT / 'outreach-log.md'
    existing = path.read_text(encoding='utf-8') if path.exists() else '# Outreach Log\n'
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    block = f"\n### Marketing momentum watchdog\n- **When:** {stamp}\n- **Note:** {text}\n"
    path.write_text(existing.rstrip() + '\n' + block, encoding='utf-8')


def main() -> int:
    now = datetime.now().astimezone()
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(RETRO)], capture_output=True, text=True)

    report = newest_report()
    post_ts = newest_post_time()
    report_age_hours = None
    post_age_hours = None
    actions = []
    apollo = {
        'status': 'missing',
        'age_hours': None,
        'cloudflare_blocked': False,
    }

    if report:
        report_age_hours = round((now - datetime.fromtimestamp(report.stat().st_mtime, tz=now.tzinfo)).total_seconds() / 3600, 2)
    if post_ts:
        post_age_hours = round((now - post_ts.astimezone(now.tzinfo)).total_seconds() / 3600, 2)

    stale_report = report is None or (now - datetime.fromtimestamp(report.stat().st_mtime, tz=now.tzinfo)) > timedelta(hours=6)
    stale_post = post_ts is None or (now - post_ts.astimezone(now.tzinfo)) > timedelta(hours=8)

    if stale_report:
        actions.append('reddit_monitor_stale')
    if stale_post:
        actions.append('no_recent_reddit_post')

    if APOLLO_STATUS_PATH.exists():
        try:
            apollo_data = json.loads(APOLLO_STATUS_PATH.read_text(encoding='utf-8'))
            apollo['status'] = apollo_data.get('status', 'unknown')
            apollo['cloudflare_blocked'] = bool(apollo_data.get('cloudflare_blocked'))
            apollo_mtime = datetime.fromtimestamp(APOLLO_STATUS_PATH.stat().st_mtime, tz=now.tzinfo)
            apollo['age_hours'] = round((now - apollo_mtime).total_seconds() / 3600, 2)
        except (json.JSONDecodeError, OSError):
            apollo['status'] = 'unreadable'
    if apollo['age_hours'] is None or apollo['age_hours'] > 12:
        actions.append('apollo_monitor_stale')
    if apollo['status'] in {'cloudflare_auth_blocked', 'ato_email_verification_required'}:
        actions.append('apollo_channel_blocked')

    # Check repo adoption flatness as a momentum stall signal
    adoption_flat = False
    if ADOPTION_PATH.exists():
        try:
            adoption = json.loads(ADOPTION_PATH.read_text(encoding='utf-8'))
            eval_data = adoption.get('evaluation', {})
            if 'primary_repo_flat' in eval_data.get('failing_signals', []):
                adoption_flat = True
                actions.append('primary_repo_adoption_flat')
        except (json.JSONDecodeError, OSError):
            pass

    # Check audit for repair actions that need execution
    pending_repairs = []
    blocked_distribution_channels = []
    if AUDIT_PATH.exists():
        try:
            audit = json.loads(AUDIT_PATH.read_text(encoding='utf-8'))
            if audit.get('has_failing_tactics'):
                for ra in audit.get('repair_actions', []):
                    pending_repairs.append(ra['failure_type'])
        except (json.JSONDecodeError, OSError):
            pass

    if pending_repairs and 'pending_repairs_detected' not in actions:
        actions.append('pending_repairs_detected')

    # Check whether supposedly actionable non-Reddit channels are actually blocked by auth/captcha.
    channel_log = ROOT / 'agents/marketing/logs/channel_discovery.json'
    if channel_log.exists():
        try:
            channel_data = json.loads(channel_log.read_text(encoding='utf-8'))
            for entry in channel_data.get('results', []):
                name = entry.get('name')
                if not name or name in RETIRED_CHANNELS or name not in ACTIVE_CHANNEL_NAMES:
                    continue
                status = entry.get('status')
                note = (entry.get('note') or '').lower()
                difficulty = entry.get('difficulty')
                if difficulty in {'easy', 'medium'} and (
                    'captcha' in note or 'hcaptcha' in note or 'recaptcha' in note or 'turnstile' in note
                    or status in {'login_required', 'broken_submit_surface', 'noop_submit_surface'}
                ):
                    blocked_distribution_channels.append(name)
        except (json.JSONDecodeError, OSError):
            pass

    if blocked_distribution_channels:
        actions.append('channel_access_mismatch')

    summary = {
        'generated_at': now.isoformat(),
        'latest_report': str(report) if report else None,
        'report_age_hours': report_age_hours,
        'latest_post_age_hours': post_age_hours,
        'adoption_flat': adoption_flat,
        'pending_repairs': pending_repairs,
        'blocked_distribution_channels': blocked_distribution_channels,
        'apollo': apollo,
        'actions': actions,
        'status': 'healthy' if not actions else 'needs_attention',
    }
    STATUS_PATH.write_text(json.dumps(summary, indent=2), encoding='utf-8')

    if actions:
        extra = ''
        if adoption_flat:
            extra = ' Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated.'
        if pending_repairs:
            extra += f' Pending repairs: {", ".join(pending_repairs)}.'
        if blocked_distribution_channels:
            extra += f' Distribution channels need replacement or human-auth handoff: {", ".join(blocked_distribution_channels)}.'
        if 'apollo_monitor_stale' in actions:
            age_text = 'missing' if apollo['age_hours'] is None else f"{apollo['age_hours']}h old"
            extra += f' Apollo monitoring is stale ({age_text}); treat Apollo as a managed outbound channel with missing fresh telemetry until the monitor runs again.'
        if 'apollo_channel_blocked' in actions:
            if apollo['status'] == 'ato_email_verification_required':
                extra += ' Cloudflare is cleared but Apollo still requires mailbox verification for this device.'
            else:
                extra += ' Cloudflare/auth protection blocks login.'
        append_note('Momentum check found: ' + ', '.join(actions) + '.' + extra)
        print(json.dumps({'ok': False, **summary}, indent=2))
        return 1

    print(json.dumps({'ok': True, **summary}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
