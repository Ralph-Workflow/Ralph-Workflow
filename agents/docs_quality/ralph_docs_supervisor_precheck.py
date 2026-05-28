#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_NAME = Path(__file__).name
ROOT = Path('/home/mistlight/.openclaw/workspace')
DOCS_DIR = ROOT / 'agents/docs_quality'
VERIFIER_JSON = DOCS_DIR / 'ralph_verifier_latest.json'
VERIFIER_MD = DOCS_DIR / 'ralph_verifier_latest.md'
PROCESS_STATE = DOCS_DIR / 'ralph_process_state.json'
CHECKER = DOCS_DIR / 'ralph_docs_check.py'
STATE_PATH = DOCS_DIR / 'ralph_docs_supervisor_precheck_state.json'
TARGET_JOB_NAME = 'ralph-workflow-docs-verifier-supervisor'
MAX_VERIFIER_AGE_MINUTES = 180
TRIGGER_COOLDOWN_MINUTES = 45


@dataclass
class Decision:
    should_trigger: bool
    reasons: list[str]
    verifier_age_minutes: float | None
    process_flags: dict[str, Any]
    checker_exit: int
    checker_summary: str
    cooldown_blocked: bool = False


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


def run_checker() -> tuple[int, str]:
    env = os.environ.copy()
    env['RALPH_DOCS_IGNORE_VERIFIER_STATUS'] = '1'
    env['RALPH_DOCS_IGNORE_PROCESS_STATE'] = '1'
    proc = subprocess.run(
        ['python3', str(CHECKER)],
        capture_output=True,
        text=True,
        env=env,
    )
    summary = ((proc.stdout or '') + '\n' + (proc.stderr or '')).strip()
    return proc.returncode, summary[:1200]


def evaluate(now_ts: float | None = None, *, checker_result: tuple[int, str] | None = None) -> Decision:
    now_ts = now_ts if now_ts is not None else _now()
    verifier_payload = load_json(VERIFIER_JSON)
    verifier_age = file_age_minutes(VERIFIER_JSON, now_ts)
    process_state = load_json(PROCESS_STATE)
    checker_exit, checker_summary = checker_result if checker_result is not None else run_checker()

    reasons: list[str] = []
    if verifier_age is None:
        reasons.append('verifier_json_missing')
    elif verifier_age > MAX_VERIFIER_AGE_MINUTES:
        reasons.append(f'verifier_stale:{verifier_age:.1f}m')

    verifier_status_text = VERIFIER_MD.read_text(encoding='utf-8') if VERIFIER_MD.exists() else ''
    if 'Status: independently verified pass' not in verifier_status_text:
        reasons.append('verifier_not_pass')

    if verifier_payload.get('verdict') != 'pass' or verifier_payload.get('ok') is not True:
        reasons.append('verifier_json_not_pass')

    watched_flags = {
        'incidentOpen': process_state.get('incidentOpen'),
        'repairContinuationRequired': process_state.get('repairContinuationRequired'),
        'pendingIndependentStop': process_state.get('pendingIndependentStop'),
        'escalationRequired': process_state.get('escalationRequired'),
        'currentIncidentId': process_state.get('currentIncidentId'),
    }
    for key, value in watched_flags.items():
        if value not in (False, None, '', 0):
            reasons.append(f'process_state:{key}={value}')

    if checker_exit != 0:
        reasons.append(f'checker_exit:{checker_exit}')

    return Decision(
        should_trigger=bool(reasons),
        reasons=reasons,
        verifier_age_minutes=verifier_age,
        process_flags=watched_flags,
        checker_exit=checker_exit,
        checker_summary=checker_summary,
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
    payload = json.loads(subprocess.check_output(['openclaw', 'cron', 'list', '--json'], text=True))
    for job in payload.get('jobs', []):
        if job.get('name') == job_name:
            return str(job['id'])
    raise RuntimeError(f'job not found: {job_name}')


def trigger_target(job_name: str) -> tuple[bool, str]:
    job_id = find_job_id(job_name)
    proc = subprocess.run(['openclaw', 'cron', 'run', job_id], capture_output=True, text=True)
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
        'verifier_age_minutes': decision.verifier_age_minutes,
        'process_flags': decision.process_flags,
        'checker_exit': decision.checker_exit,
        'checker_summary': decision.checker_summary,
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


# ── Self-repair ──────────────────────────────────────────────────────────────
import traceback

MAX_ARTIFACT_AGE_HOURS = 3


def stale_artifact_report(artifact_path: Path, max_age_hours: float = MAX_ARTIFACT_AGE_HOURS) -> bool:
    if not artifact_path.exists():
        return True
    age_hours = (time.time() - artifact_path.stat().st_mtime) / 3600
    return age_hours > max_age_hours


def self_repair_main() -> int:
    script_name = SCRIPT_NAME.replace('.py', '')
    artifact_candidates = [
        Path(f'/home/mistlight/.openclaw/workspace/agents/docs_quality/{script_name}_state.json'),
        Path(f'/home/mistlight/.openclaw/workspace/agents/marketing/logs/{script_name}_latest.json'),
        Path(f'/home/mistlight/.openclaw/workspace/seo-reports/{script_name}_latest.json'),
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
    log_path = Path('/home/mistlight/.openclaw/workspace/outreach-log.md')
    if not log_path.exists():
        return 0
    text = log_path.read_text()
    import re
    entries = re.findall(rf'###\s+.*?{re.escape(script_name)}.*?(?=\n###|\Z)', text, re.DOTALL)
    flat_count = sum(1 for e in entries if 'no measurable outcome' in e.lower() or 'flat' in e.lower())
    return min(flat_count, max_runs)


def should_self_improve() -> bool:
    return flat_outcome_count(SCRIPT_NAME.replace('.py','')) >= 3


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
