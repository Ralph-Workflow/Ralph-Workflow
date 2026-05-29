#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents/marketing/logs'
AUDIT_JSON = LOG_DIR / 'marketing_workflow_audit_latest.json'
STATE_PATH = LOG_DIR / 'marketing_workflow_audit_precheck_state.json'
TARGET_JOB_NAME = 'marketing-workflow-audit'
MAX_AUDIT_AGE_MINUTES = 360
TRIGGER_COOLDOWN_MINUTES = 180
DEPENDENCIES = [
    LOG_DIR / 'adoption_metrics_latest.json',
    LOG_DIR / 'reddit_post_analysis.json',
    LOG_DIR / 'market_intelligence_latest.json',
    LOG_DIR / 'reddit_execution_status_latest.json',
    LOG_DIR / 'apollo_status.json',
    LOG_DIR / 'marketing_momentum_watchdog.json',
    ROOT / 'seo-reports/reddit_monitor_latest.md',
    ROOT / 'outreach-log.md',
]
ACTIVE_REPAIR_WINDOW_STATUSES = {'needs_repair', 'measurement_hold'}


@dataclass
class Decision:
    should_trigger: bool
    reasons: list[str]
    audit_age_minutes: float | None
    audit_status: str
    repair_window_status: str
    newest_dependency: str | None
    newest_dependency_age_minutes: float | None


def _now() -> float:
    return time.time()


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def file_age_minutes(path: Path, now_ts: float) -> float | None:
    try:
        return max(0.0, (now_ts - path.stat().st_mtime) / 60.0)
    except FileNotFoundError:
        return None


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def newest_dependency(target_mtime: float | None, now_ts: float) -> tuple[str | None, float | None, list[str]]:
    reasons: list[str] = []
    newest_path: str | None = None
    newest_age: float | None = None
    newest_mtime = float('-inf')
    if target_mtime is None:
        target_mtime = 0.0
    for dep in DEPENDENCIES:
        if not dep.exists():
            continue
        dep_mtime = dep.stat().st_mtime
        if dep_mtime > target_mtime + 1:
            age = max(0.0, (now_ts - dep_mtime) / 60.0)
            shown = display_path(dep)
            reasons.append(f'dependency_newer:{shown}')
            if dep_mtime > newest_mtime:
                newest_mtime = dep_mtime
                newest_path = shown
                newest_age = age
    return newest_path, newest_age, reasons


def evaluate(now_ts: float | None = None) -> Decision:
    now_ts = now_ts if now_ts is not None else _now()
    audit = load_json(AUDIT_JSON)
    audit_age = file_age_minutes(AUDIT_JSON, now_ts)
    audit_status = str(audit.get('status') or '')
    repair_window_status = str(audit.get('repair_window_status') or '')
    reasons: list[str] = []

    target_mtime = AUDIT_JSON.stat().st_mtime if AUDIT_JSON.exists() else None
    if audit_age is None:
        reasons.append('audit_missing')
    elif audit_age > MAX_AUDIT_AGE_MINUTES:
        reasons.append(f'audit_stale:{audit_age:.1f}m')

    if repair_window_status in ACTIVE_REPAIR_WINDOW_STATUSES:
        reasons.append(f'repair_window:{repair_window_status}')

    if audit_status and audit_status not in {'ok', 'watch'}:
        reasons.append(f'audit_status:{audit_status}')

    newest_path, newest_age, dep_reasons = newest_dependency(target_mtime, now_ts)
    reasons.extend(dep_reasons)

    return Decision(
        should_trigger=bool(reasons),
        reasons=reasons,
        audit_age_minutes=audit_age,
        audit_status=audit_status,
        repair_window_status=repair_window_status,
        newest_dependency=newest_path,
        newest_dependency_age_minutes=newest_age,
    )


def load_state() -> dict[str, Any]:
    return load_json(STATE_PATH)


def save_state(payload: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')


def cooldown_allows_trigger(decision: Decision, now_ts: float | None = None) -> tuple[bool, dict[str, Any]]:
    now_ts = now_ts if now_ts is not None else _now()
    state = load_state()
    last_triggered_at = float(state.get('last_triggered_at') or 0)
    last_signature = str(state.get('last_reason_signature') or '')
    signature = '|'.join(decision.reasons)
    elapsed_minutes = (now_ts - last_triggered_at) / 60.0 if last_triggered_at else None
    if last_signature == signature and elapsed_minutes is not None and elapsed_minutes < TRIGGER_COOLDOWN_MINUTES:
        return False, {
            'last_triggered_at': last_triggered_at,
            'last_reason_signature': last_signature,
            'elapsed_minutes': elapsed_minutes,
        }
    return True, {
        'last_triggered_at': last_triggered_at,
        'last_reason_signature': last_signature,
        'elapsed_minutes': elapsed_minutes,
    }


def find_job_id(job_name: str) -> str:
    payload = json.loads(subprocess.check_output(['/home/mistlight/.bun/bin/openclaw', 'cron', 'list', '--json'], text=True))
    for job in payload.get('jobs', []):
        if job.get('name') == job_name:
            return str(job['id'])
    raise RuntimeError(f'job not found: {job_name}')


def trigger_target(job_name: str) -> tuple[bool, str]:
    job_id = find_job_id(job_name)
    proc = subprocess.run(['/home/mistlight/.bun/bin/openclaw', 'cron', 'run', job_id], capture_output=True, text=True)
    detail = ((proc.stdout or '') + '\n' + (proc.stderr or '')).strip()
    ok = proc.returncode == 0 or 'already-running' in detail or '"ok": true' in detail
    return ok, detail[:1200]


def main(argv: list[str]) -> int:
    dry_run = '--dry-run' in argv
    now_ts = _now()
    decision = evaluate(now_ts)
    payload: dict[str, Any] = {
        'checked_at': now_ts,
        'target_job': TARGET_JOB_NAME,
        'should_trigger': decision.should_trigger,
        'reasons': decision.reasons,
        'audit_age_minutes': decision.audit_age_minutes,
        'audit_status': decision.audit_status,
        'repair_window_status': decision.repair_window_status,
        'newest_dependency': decision.newest_dependency,
        'newest_dependency_age_minutes': decision.newest_dependency_age_minutes,
        'dry_run': dry_run,
    }
    if not decision.should_trigger:
        payload['status'] = 'skip_healthy'
        print(json.dumps(payload, indent=2))
        return 0

    allowed, cooldown = cooldown_allows_trigger(decision, now_ts)
    payload['cooldown'] = cooldown
    if not allowed:
        payload['status'] = 'skip_cooldown'
        payload['cooldown_blocked'] = True
        print(json.dumps(payload, indent=2))
        return 0

    if dry_run:
        payload['status'] = 'would_trigger'
        print(json.dumps(payload, indent=2))
        return 0

    ok, detail = trigger_target(TARGET_JOB_NAME)
    payload['status'] = 'triggered' if ok else 'trigger_failed'
    payload['trigger_ok'] = ok
    payload['trigger_detail'] = detail
    if ok:
        save_state({
            'last_triggered_at': now_ts,
            'last_reason_signature': '|'.join(decision.reasons),
            'last_target_job': TARGET_JOB_NAME,
        })
    print(json.dumps(payload, indent=2))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
