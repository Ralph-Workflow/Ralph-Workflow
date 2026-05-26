#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.marketing import run as marketing_run
from agents.marketing.channel_discovery import ACTIVE_CHANNEL_NAMES, RETIRED_CHANNELS
from agents.marketing.reddit_monitor import report_is_healthy_for_reuse
SEO = ROOT / 'seo-reports'
CURRENT_DISTRIBUTION_CHANNELS = {
    'toolwise',
    'aitoolsindex',
    'saashub',
    'alternativeto',
    'producthunt',
    'theresanaiforthat',
}
MANUAL_OR_MANAGED_CHANNELS = {
    'saashub',
    'alternativeto',
    'producthunt',
    'theresanaiforthat',
    'toolshelf',  # captcha/turnstile blocked from this environment — requires human-auth to unblock
}
LOG_JSONL = ROOT / 'agents/marketing/logs/reddit_posts.jsonl'
RETRO = ROOT / 'agents/marketing/reddit_retrospective.py'
SITE_FETCH = 'https://ralphworkflow.com'
STATUS_DIR = ROOT / 'agents/marketing/logs'
STATUS_PATH = STATUS_DIR / 'marketing_momentum_watchdog.json'
ADOPTION_PATH = STATUS_DIR / 'adoption_metrics_latest.json'
AUDIT_PATH = STATUS_DIR / 'marketing_workflow_audit_latest.json'
APOLLO_STATUS_PATH = STATUS_DIR / 'apollo_status.json'
RUNNER_PATH = STATUS_DIR / 'marketing_loop_runner_latest.json'
REDDIT_EXECUTION_STATUS_PATH = STATUS_DIR / 'reddit_execution_status_latest.json'
STRUCTURAL_REPLACEMENT_ACTION_TYPES = {
    'apollo_outreach_execution',
}

SYSTEM_DESIGN_REPAIR_ACTION_TYPES = {
    'distribution_architecture_repair',
    'distribution_architecture_churn_guard_repair',
    'measurement_hold_churn_guard_repair',
    'measurement_hold_release_reschedule_repair',
    'post_hold_release_prompt_guard_repair',
    'measurement_hold_release_delivery_route_repair',
    'apollo_truthfulness_repair',
    'apollo_cloudflare_truthfulness_repair',
    'apollo_runtime_truth_repair',
    'apollo_followup_truth_repair',
}


def newest_report() -> Path | None:
    latest_alias = SEO / 'reddit_monitor_latest.md'
    if latest_alias.exists():
        return latest_alias

    reports = []
    for report in SEO.glob('reddit_monitor_*.md'):
        stem = report.stem
        try:
            datetime.strptime(stem[len('reddit_monitor_'):], '%Y-%m-%d_%H%M')
        except ValueError:
            continue
        reports.append(report)
    reports.sort()
    return reports[-1] if reports else None


def report_signal(report: Path | None) -> str:
    if report is None or not report.exists():
        return 'missing'
    try:
        text = report.read_text(encoding='utf-8')
    except OSError:
        return 'unreadable'
    text_l = text.lower()
    diagnostics: dict[str, int] = {}
    if '**Search diagnostics:**' in text:
        try:
            diagnostics_line = text.split('**Search diagnostics:**', 1)[1].splitlines()[0].strip()
        except Exception:
            diagnostics_line = ''
        for part in diagnostics_line.split(','):
            key, _, value = part.strip().partition('=')
            if key and value.isdigit():
                diagnostics[key] = int(value)
    shortlist_count = None
    shortlist_match = re.search(r'\*\*Shortlisted:\*\*\s*(\d+)', text)
    if shortlist_match:
        shortlist_count = int(shortlist_match.group(1))

    partial_reddit_blocking = (
        diagnostics.get('reddit_ip_blocked', 0) > 0
        and (diagnostics.get('ok', 0) > 0 or (shortlist_count or 0) > 0)
    )
    if partial_reddit_blocking:
        return 'degraded'
    if any(marker in text_l for marker in [
        'reddit is ip-blocked',
        'reddit ip-blocked',
        'reddit_ip_blocked',
        '403 confirmed',
        'ip-blocked',
        'ip blocked',
    ]) and not partial_reddit_blocking:
        return 'blocked'
    if 'degraded coverage' in text_l and any(marker in text_l for marker in [
        'partial visibility',
        'challenge-heavy',
        'provider still challenge-heavy',
        'telemetry-limited',
        'fails closed on posting',
    ]):
        return 'blocked'
    if 'no reliable coverage yet' in text_l:
        return 'degraded'
    if shortlist_match and int(shortlist_match.group(1)) == 0:
        return 'no_opportunities'
    if shortlist_match:
        return 'opportunities_found'
    return 'unknown'


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


def newest_healthy_report_time(now: datetime) -> tuple[Path | None, float | None]:
    latest_healthy = SEO / 'reddit_monitor_latest_healthy.md'
    candidates: list[Path] = []
    if latest_healthy.exists():
        candidates.append(latest_healthy)
    candidates.extend(sorted(SEO.glob('reddit_monitor_*.md'), reverse=True))
    for report in candidates:
        try:
            text = report.read_text(encoding='utf-8')
        except OSError:
            continue
        if not report_is_healthy_for_reuse(text):
            continue
        age_hours = round((now - datetime.fromtimestamp(report.stat().st_mtime, tz=now.tzinfo)).total_seconds() / 3600, 2)
        return report, age_hours
    return None, None


def append_note(text: str) -> None:
    path = ROOT / 'outreach-log.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding='utf-8') if path.exists() else '# Outreach Log\n'
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    block = f"\n### Marketing momentum watchdog\n- **When:** {stamp}\n- **Note:** {text}\n"
    path.write_text(existing.rstrip() + '\n' + block, encoding='utf-8')


def latest_reddit_monitor_runtime(now: datetime) -> dict[str, object]:
    if not RUNNER_PATH.exists():
        return {'status': None, 'age_hours': None}
    try:
        payload = json.loads(RUNNER_PATH.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {'status': None, 'age_hours': None}

    generated_at = payload.get('generated_at')
    generated_dt = None
    if isinstance(generated_at, str):
        try:
            generated_dt = datetime.fromisoformat(generated_at)
            if generated_dt.tzinfo is None:
                generated_dt = generated_dt.astimezone()
        except ValueError:
            generated_dt = None

    for entry in payload.get('results', []) or []:
        script = str(entry.get('script') or '')
        if not script.endswith('reddit_monitor.py'):
            continue
        stdout = entry.get('stdout') or ''
        try:
            monitor_payload = json.loads(stdout)
        except json.JSONDecodeError:
            monitor_payload = {}
        age_hours = None
        if generated_dt is not None:
            age_hours = round((now - generated_dt.astimezone(now.tzinfo)).total_seconds() / 3600, 2)
        return {
            'status': monitor_payload.get('status'),
            'age_hours': age_hours,
        }
    return {'status': None, 'age_hours': None}


def latest_reddit_execution_status(now: datetime) -> dict[str, object]:
    if not REDDIT_EXECUTION_STATUS_PATH.exists():
        return {'status': None, 'age_hours': None, 'blocking_reason': None}
    try:
        payload = json.loads(REDDIT_EXECUTION_STATUS_PATH.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {'status': None, 'age_hours': None, 'blocking_reason': None}

    generated_dt = None
    for key in ('generated_at', 'timestamp'):
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        try:
            generated_dt = datetime.fromisoformat(value)
            if generated_dt.tzinfo is None:
                generated_dt = generated_dt.astimezone()
            break
        except ValueError:
            continue

    age_hours = None
    if generated_dt is not None:
        age_hours = round((now - generated_dt.astimezone(now.tzinfo)).total_seconds() / 3600, 2)

    return {
        'status': payload.get('status'),
        'age_hours': age_hours,
        'blocking_reason': payload.get('blocking_reason') or payload.get('detail'),
    }


def main() -> int:
    now = datetime.now().astimezone()
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(RETRO)], capture_output=True, text=True)

    report = newest_report()
    post_ts = newest_post_time()
    report_age_hours = None
    post_age_hours = None
    actions: list[str] = []
    watch_actions: list[str] = []
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
    monitor_signal = report_signal(report)
    healthy_report, healthy_report_age_hours = newest_healthy_report_time(now)
    reddit_monitor_runtime = latest_reddit_monitor_runtime(now)
    reddit_execution_status = latest_reddit_execution_status(now)
    measurement_hold_window = marketing_run._latest_measurement_hold_window(now)
    if measurement_hold_window is not None:
        try:
            hold_source = Path(measurement_hold_window.get('source_log') or '').resolve()
            root_resolved = ROOT.resolve()
            if root_resolved not in hold_source.parents:
                measurement_hold_window = None
        except OSError:
            measurement_hold_window = None
    recent_runtime_skip = (
        reddit_monitor_runtime.get('status') in {'cooldown_skip'}
        and reddit_monitor_runtime.get('age_hours') is not None
        and float(reddit_monitor_runtime['age_hours']) <= 2
    )
    active_measurement_hold = measurement_hold_window is not None
    execution_blocked = (
        reddit_execution_status.get('status') in {'network_security_blocked', 'execution_blocked', 'not_logged_in'}
        and reddit_execution_status.get('age_hours') is not None
        and float(reddit_execution_status['age_hours']) <= 12
    )
    execution_ready = (
        reddit_execution_status.get('status') == 'browser_session_ready'
        and reddit_execution_status.get('age_hours') is not None
        and float(reddit_execution_status['age_hours']) <= 12
    )
    if execution_blocked:
        monitor_signal = 'blocked'

    if stale_report and not recent_runtime_skip and not active_measurement_hold and not execution_blocked:
        actions.append('reddit_monitor_stale')
    elif monitor_signal == 'blocked':
        # If Reddit is blocked but a replacement non-Reddit distribution execution has already
        # shipped, keep this as a watchpoint instead of failing the whole momentum loop again.
        watch_actions.append('reddit_channel_blocked')
    elif monitor_signal == 'degraded':
        if healthy_report_age_hours is not None and healthy_report_age_hours <= 3:
            watch_actions.append('reddit_monitor_degraded')
        else:
            actions.append('reddit_monitor_degraded')

    latest_executed_action = {}
    recent_shipped_non_reddit_execution = False

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
        watch_actions.append('apollo_monitor_stale')
    if apollo['status'] in {'cloudflare_auth_blocked', 'ato_email_verification_required'}:
        watch_actions.append('apollo_channel_blocked')

    # Check repo adoption flatness as a momentum stall signal
    adoption_flat = False
    if ADOPTION_PATH.exists():
        try:
            adoption = json.loads(ADOPTION_PATH.read_text(encoding='utf-8'))
            eval_data = adoption.get('evaluation', {})
            if 'primary_repo_flat' in eval_data.get('failing_signals', []):
                adoption_flat = True
        except (json.JSONDecodeError, OSError):
            pass

    # Check audit for repair actions that need execution
    pending_repairs = []
    blocked_distribution_channels = []
    repair_window_status = None
    measurement_pending_reasons = []
    failing_tactics = []
    repair_actions = []
    if AUDIT_PATH.exists():
        try:
            audit = json.loads(AUDIT_PATH.read_text(encoding='utf-8'))
            repair_window_status = audit.get('repair_window_status')
            measurement_pending_reasons = audit.get('measurement_pending_reasons', []) or []
            failing_tactics = audit.get('failing_tactics', []) or []
            repair_actions = audit.get('repair_actions', []) or []
            latest_executed_action = audit.get('latest_executed_action') or {}
            if audit.get('has_failing_tactics') and repair_window_status != 'measurement_pending':
                for ra in repair_actions:
                    pending_repairs.append(ra['failure_type'])
        except (json.JSONDecodeError, OSError):
            pass

    live_repair_actions = [
        action for action in repair_actions
        if action.get('repair_state') in {'pending_measurement', 'needs_execution'}
    ]
    live_system_design_repairs = [
        action for action in live_repair_actions
        if action.get('repair_kind') == 'system_design'
    ]
    latest_action_type = latest_executed_action.get('type') or ''
    explicit_outcome_ready = latest_executed_action.get('outcome_ready')
    if explicit_outcome_ready is None:
        recent_action_outcome_ready = bool(latest_executed_action.get('ok')) and (
            bool(latest_executed_action.get('live_external_action'))
            or latest_action_type in STRUCTURAL_REPLACEMENT_ACTION_TYPES
        )
    else:
        recent_action_outcome_ready = bool(explicit_outcome_ready)
    recent_shipped_non_reddit_execution = bool(latest_executed_action.get('ok')) and recent_action_outcome_ready and (
        bool(latest_executed_action.get('live_external_action'))
        or latest_action_type in STRUCTURAL_REPLACEMENT_ACTION_TYPES
    ) and 'reddit' not in latest_action_type
    recent_structural_system_repair = bool(latest_executed_action.get('ok')) and latest_action_type in SYSTEM_DESIGN_REPAIR_ACTION_TYPES
    if stale_post and monitor_signal not in {'no_opportunities', 'blocked'} and not recent_shipped_non_reddit_execution and not recent_runtime_skip:
        actions.append('no_recent_reddit_post')
    shipped_replacement_execution = recent_shipped_non_reddit_execution

    # Once a non-Reddit replacement path or live system-design repair is already in motion,
    # degraded Reddit telemetry should not fail the whole momentum loop again in the same run.
    if (
        'reddit_monitor_degraded' in actions
        and (
            shipped_replacement_execution
            or recent_structural_system_repair
            or bool(live_system_design_repairs)
        )
    ):
        actions.remove('reddit_monitor_degraded')
        if 'reddit_monitor_degraded' not in watch_actions:
            watch_actions.append('reddit_monitor_degraded')

    # A blocked Reddit lane is a genuine signal, but once a replacement distribution path has
    # already shipped it becomes a managed watchpoint rather than a same-run momentum failure.
    if 'reddit_channel_blocked' in watch_actions and not shipped_replacement_execution:
        watch_actions.remove('reddit_channel_blocked')
        actions.append('reddit_channel_blocked')

    if adoption_flat:
        if not live_system_design_repairs and not shipped_replacement_execution and not recent_structural_system_repair and not active_measurement_hold:
            actions.append('outcome_system_repair_missing')
        if (
            (repair_window_status == 'measurement_pending' and 'primary_repo_flat' in measurement_pending_reasons)
            or active_measurement_hold
        ):
            watch_actions.append('primary_repo_adoption_flat')
        else:
            actions.append('primary_repo_adoption_flat')

    if failing_tactics and repair_window_status == 'measurement_pending' and not live_repair_actions and not shipped_replacement_execution and not active_measurement_hold:
        actions.append('measurement_pending_without_repairs')

    if pending_repairs and 'pending_repairs_detected' not in actions and not active_measurement_hold:
        actions.append('pending_repairs_detected')
    elif active_measurement_hold:
        watch_actions.append('measurement_hold_active')

    # Check whether supposedly actionable non-Reddit channels are actually blocked by auth/captcha.
    channel_log = ROOT / 'agents/marketing/logs/channel_discovery.json'
    if channel_log.exists():
        try:
            channel_data = json.loads(channel_log.read_text(encoding='utf-8'))
            for entry in channel_data.get('results', []):
                name = entry.get('name')
                if not name or name in RETIRED_CHANNELS or name not in ACTIVE_CHANNEL_NAMES:
                    continue
                if name not in CURRENT_DISTRIBUTION_CHANNELS or name in MANUAL_OR_MANAGED_CHANNELS:
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

    status = 'healthy'
    if actions:
        status = 'needs_attention'
    elif watch_actions:
        status = 'watch'

    summary = {
        'generated_at': now.isoformat(),
        'latest_report': str(report) if report else None,
        'report_age_hours': report_age_hours,
        'latest_healthy_report': str(healthy_report) if healthy_report else None,
        'latest_healthy_report_age_hours': healthy_report_age_hours,
        'latest_post_age_hours': post_age_hours,
        'reddit_monitor_runtime': reddit_monitor_runtime,
        'reddit_execution_status': reddit_execution_status,
        'adoption_flat': adoption_flat,
        'pending_repairs': pending_repairs,
        'blocked_distribution_channels': blocked_distribution_channels,
        'apollo': apollo,
        'measurement_hold': {
            'active': active_measurement_hold,
            'hold_started_at': measurement_hold_window['hold_started_at'].isoformat() if active_measurement_hold else None,
            'hold_until': measurement_hold_window['hold_until'].isoformat() if active_measurement_hold else None,
            'source_log': measurement_hold_window['source_log'] if active_measurement_hold else None,
        },
        'actions': actions,
        'watch_actions': watch_actions,
        'status': status,
    }
    STATUS_PATH.write_text(json.dumps(summary, indent=2), encoding='utf-8')

    if actions:
        extra = ''
        if adoption_flat:
            extra = ' Codeberg adoption is flat — current tactics are failing and must be replaced, not repeated.'
        if 'reddit_channel_blocked' in actions:
            extra += ' Reddit is confirmed IP-blocked / 403 from this environment, so this is a dead distribution channel right now, not a watch-level telemetry wobble.'
        if 'reddit_monitor_degraded' in actions:
            extra += ' Reddit monitoring has degraded provider coverage, so lack of recent posting is not being treated as proof of a missed opportunity.'
        if pending_repairs:
            extra += f' Pending repairs: {", ".join(pending_repairs)}.'
        elif measurement_pending_reasons:
            extra += f' Repairs are already live; waiting on measurement for: {", ".join(measurement_pending_reasons)}.'
        if 'measurement_pending_without_repairs' in actions:
            extra += f' Failing tactics are still present with no live repair actions: {", ".join(failing_tactics)}.'
        if 'outcome_system_repair_missing' in actions:
            extra += ' Primary-repo adoption is flat without a live system-design repair; technical/tactical repairs alone are not acceptable.'
        if blocked_distribution_channels:
            extra += f' Distribution channels need replacement or human-auth handoff: {", ".join(blocked_distribution_channels)}.'
        if 'apollo_monitor_stale' in watch_actions:
            age_text = 'missing' if apollo['age_hours'] is None else f"{apollo['age_hours']}h old"
            extra += f' Apollo monitoring is stale ({age_text}); treat Apollo as a managed outbound channel with missing fresh telemetry until the monitor runs again.'
        if 'apollo_channel_blocked' in watch_actions:
            if apollo['status'] == 'ato_email_verification_required':
                extra += ' Cloudflare is cleared but Apollo still requires mailbox verification for this device.'
            else:
                extra += ' Cloudflare/auth protection blocks login.'
        if 'reddit_monitor_degraded' in watch_actions:
            extra += ' Reddit monitor provider coverage is degraded right now, but a fresh healthy search report still exists inside the grace window, so this is being tracked without failing the whole loop.'
        if 'primary_repo_adoption_flat' in watch_actions:
            extra += ' Primary repo adoption is still flat, but repairs are already live and this remains a measurement-window watchpoint rather than a same-run repair failure.'
        if 'measurement_hold_active' in watch_actions and active_measurement_hold:
            extra += f" Active measurement hold remains in force until {measurement_hold_window['hold_until'].isoformat()}, so this run is intentionally suppressing new reset churn."
        append_note('Momentum check found: ' + ', '.join(actions) + '.' + extra)
        print(json.dumps({'ok': False, **summary}, indent=2))
        return 1

    if watch_actions:
        extra = []
        if 'primary_repo_adoption_flat' in watch_actions:
            extra.append('primary repo adoption is still flat against the stated marketing goal')
        if 'apollo_channel_blocked' in watch_actions:
            extra.append('Apollo outbound remains blocked')
        if 'reddit_channel_blocked' in watch_actions:
            extra.append('Reddit is blocked from this environment, but a replacement distribution path has already shipped')
        if 'reddit_monitor_degraded' in watch_actions:
            extra.append('Reddit monitoring coverage is degraded')
        if 'apollo_monitor_stale' in watch_actions:
            extra.append('Apollo telemetry is stale')
        if 'measurement_hold_active' in watch_actions and active_measurement_hold:
            extra.append(f"measurement hold is active until {measurement_hold_window['hold_until'].isoformat()}")
        append_note('Momentum watch state: ' + '; '.join(extra) + '.')
        print(json.dumps({'ok': True, **summary}, indent=2))
        return 0

    print(json.dumps({'ok': True, **summary}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
