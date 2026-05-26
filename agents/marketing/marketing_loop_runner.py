#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents/marketing/logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
OUT = LOG_DIR / 'marketing_loop_runner_latest.json'
AUDIT_LATEST = LOG_DIR / 'marketing_workflow_audit_latest.json'

SCRIPTS = [
    ROOT / 'agents/marketing/run.py',
    ROOT / 'agents/marketing/sync_outreach_log.py',
    ROOT / 'agents/marketing/reddit_retrospective.py',
    ROOT / 'agents/marketing/reddit_monitor.py',
    ROOT / 'agents/marketing/marketing_workflow_audit.py',
    ROOT / 'agents/marketing/marketing_momentum_watchdog.py',
    ROOT / 'agents/marketing/reddit_next_window_packet.py',
    ROOT / 'agents/marketing/marketing_loop_independent_verify.py',
    ROOT / 'agents/marketing/marketing_loop_verifier.py',
]
POST_AUDIT_RUNTIME_SCRIPTS = [
    ROOT / 'agents/marketing/outcome_capability_runner.py',
    ROOT / 'agents/marketing/outcome_execution_board_runner.py',
]
CERTIFICATION_SCRIPTS = {
    'marketing_loop_independent_verify.py',
    'marketing_loop_verifier.py',
}
TOLERATED_NONZERO_STATUSES = {
    'reddit_monitor.py': {'search_provider_degraded'},
}


def _script_command(script: Path) -> list[str]:
    return ['python3', str(script)]


def _parsed_stdout_payload(stdout: str) -> dict:
    try:
        payload = json.loads((stdout or '').strip())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_tolerated_nonzero(script: Path, returncode: int, stdout: str) -> tuple[bool, str | None]:
    if returncode == 0:
        return False, None
    tolerated_statuses = TOLERATED_NONZERO_STATUSES.get(script.name)
    if not tolerated_statuses:
        return False, None
    payload = _parsed_stdout_payload(stdout)
    status = str(payload.get('status') or '').strip()
    if status in tolerated_statuses:
        return True, status
    return False, None


def _load_audit_payload(stdout: str = '') -> dict:
    payload = _parsed_stdout_payload(stdout)
    if payload:
        return payload
    if not AUDIT_LATEST.exists():
        return {}
    try:
        loaded = json.loads(AUDIT_LATEST.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _audit_requires_post_audit_runtime(audit_payload: dict) -> bool:
    if not audit_payload:
        return False
    if str(audit_payload.get('repair_window_status') or '').strip() != 'needs_repair':
        return False
    for repair in audit_payload.get('repair_actions', []) or []:
        if str(repair.get('repair_kind') or '').strip() != 'system_design':
            continue
        if str(repair.get('repair_state') or '').strip() == 'needs_execution':
            return True
    return False


def _run_script(script: Path) -> tuple[dict, bool]:
    proc = subprocess.run(_script_command(script), capture_output=True, text=True)
    tolerated_nonzero, tolerated_status = _is_tolerated_nonzero(script, proc.returncode, proc.stdout)
    entry = {
        'script': str(script),
        'ok': proc.returncode == 0 or tolerated_nonzero,
        'returncode': proc.returncode,
        'stdout': proc.stdout.strip()[:4000],
        'stderr': proc.stderr.strip()[:4000],
    }
    if tolerated_nonzero:
        entry['tolerated_nonzero'] = True
        entry['tolerated_status'] = tolerated_status
    return entry, proc.returncode == 0 or tolerated_nonzero


def main() -> int:
    results = []
    overall_ok = True
    operational_ok = True
    certification_ok = True

    def write_snapshot() -> None:
        payload = {
            'generated_at': datetime.now().isoformat(),
            'ok': operational_ok,
            'operational_ok': operational_ok,
            'certification_ok': certification_ok,
            'results': results,
        }
        OUT.write_text(json.dumps(payload, indent=2), encoding='utf-8')

    write_snapshot()
    for script in SCRIPTS:
        entry, ok = _run_script(script)
        results.append(entry)
        if not ok:
            if script.name in CERTIFICATION_SCRIPTS:
                certification_ok = False
            else:
                operational_ok = False
            overall_ok = False
        write_snapshot()

        if script.name == 'marketing_workflow_audit.py':
            audit_payload = _load_audit_payload(entry.get('stdout', ''))
            if _audit_requires_post_audit_runtime(audit_payload):
                for post_audit_script in POST_AUDIT_RUNTIME_SCRIPTS:
                    post_entry, post_ok = _run_script(post_audit_script)
                    post_entry['triggered_by'] = 'post_audit_system_design_repair'
                    results.append(post_entry)
                    if not post_ok:
                        operational_ok = False
                        overall_ok = False
                    write_snapshot()

    payload = {
        'generated_at': datetime.now().isoformat(),
        'ok': operational_ok,
        'operational_ok': operational_ok,
        'certification_ok': certification_ok,
        'results': results,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps(payload, indent=2))
    return 0 if overall_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
