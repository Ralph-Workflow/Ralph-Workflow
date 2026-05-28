#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_REPAIR_PASSES = 2
REPEAT_FAILURE_THRESHOLD = 2

ROOT = Path('/home/mistlight/.openclaw/workspace')
DQ = ROOT / 'agents' / 'docs_quality'
SCRIPT = DQ / 'docs_stack_temp_watchdog.py'
STATUS_JSON = DQ / 'docs_stack_temp_watchdog_status.json'
STATUS_MD = DQ / 'docs_stack_temp_watchdog_status.md'

CHECKER = DQ / 'ralph_docs_check.py'
EDITORIAL = DQ / 'ralph_docs_editorial_audit.py'
AGENTIC = DQ / 'ralph_docs_agentic_review.py'
RUNNER = DQ / 'ralph_docs_runner.py'
VERIFY = DQ / 'ralph_docs_verify.py'
AGENTIC_JSON = DQ / 'ralph_agentic_latest.json'
VERIFIER_MD = DQ / 'ralph_verifier_latest.md'
PARALLEL_SIGNOFF_JSON = DQ / 'docs_stack_parallel_signoff.json'
TIMEOUT = 180
VERIFY_TIMEOUT = 180
RUNNER_LOCK_EXIT = 75
VERIFY_LOCK_EXIT = 75
STATE_JSON = DQ / 'docs_stack_temp_watchdog_runtime.json'
WATCHDOG_SELF_ENV = 'RALPH_DOCS_WATCHDOG_CHILD'
WATCHDOG_MODE_ENV = 'RALPH_DOCS_WATCHDOG_MODE'
WATCHDOG_SKIP_AGENTIC_ENV = 'RALPH_DOCS_WATCHDOG_SKIP_AGENTIC'
DOC_STATE_PATHS = [
    Path('/home/mistlight/RalphWithReviewer/README.md'),
    Path('/home/mistlight/RalphWithReviewer/ralph-workflow/README.md'),
    ROOT / 'repos' / 'Ralph-Workflow' / 'github-mirror' / 'README.md',
    ROOT / 'repos' / 'Ralph-Workflow' / 'github-mirror' / 'START_HERE.md',
    ROOT / 'repos' / 'Ralph-Workflow' / 'github-mirror' / 'docs',
    ROOT / 'repos' / 'Ralph-Workflow' / 'github-mirror' / 'ralph-workflow' / 'docs' / 'sphinx',
]
ARTIFACT_STATE_PATHS = [
    STATUS_JSON,
    STATUS_MD,
    AGENTIC_JSON,
    VERIFIER_MD,
    STATE_JSON,
]


def run_py(path: Path, *, timeout: int = TIMEOUT, watchdog_mode: bool = False, skip_agentic: bool = False) -> dict:
    env = os.environ.copy()
    env[WATCHDOG_SELF_ENV] = '1'
    if watchdog_mode:
        env[WATCHDOG_MODE_ENV] = '1'
    if skip_agentic:
        env[WATCHDOG_SKIP_AGENTIC_ENV] = '1'
    try:
        proc = subprocess.run(
            ['python3', str(path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return {
            'path': str(path),
            'exit': proc.returncode,
            'output': (proc.stdout + proc.stderr).strip(),
        }
    except subprocess.TimeoutExpired as exc:
        combined = ((exc.stdout or '') + (exc.stderr or '')).strip()
        if combined:
            combined += '\n\n'
        combined += f'TIMEOUT: {path} exceeded {timeout}s'
        return {'path': str(path), 'exit': 124, 'output': combined}


def load_agentic() -> dict:
    try:
        return json.loads(AGENTIC_JSON.read_text(encoding='utf-8'))
    except Exception as exc:
        return {
            'status': 'fail',
            'summary': f'Could not load agentic JSON: {exc}',
            'loopHealthy': False,
            'shouldUserNeedToRepeatThis': True,
        }


def _fingerprint_paths(paths: list[Path]) -> str:
    sha = hashlib.sha256()
    for root in paths:
        files = sorted(p for p in root.rglob('*') if p.is_file()) if root.is_dir() else [root]
        for path in files:
            sha.update(str(path).encode('utf-8'))
            try:
                sha.update(path.read_bytes())
            except FileNotFoundError:
                sha.update(b'__missing__')
    return sha.hexdigest()[:16]


def docs_state_fingerprint() -> str:
    return _fingerprint_paths(DOC_STATE_PATHS)


def artifact_state_fingerprint() -> str:
    return _fingerprint_paths(ARTIFACT_STATE_PATHS)


def load_parallel_signoff() -> dict:
    try:
        return json.loads(PARALLEL_SIGNOFF_JSON.read_text(encoding='utf-8'))
    except Exception as exc:
        return {
            'approvedToDeactivate': False,
            'error': f'Could not load parallel signoff JSON: {exc}',
        }


def parallel_signoff_valid(current_fp: str) -> tuple[bool, str]:
    signoff = load_parallel_signoff()
    if signoff.get('approvedToDeactivate') is not True:
        return False, 'parallel signoff did not approve deactivation'
    if signoff.get('docsStateFingerprint') != current_fp:
        return False, 'parallel signoff fingerprint does not match current docs state'
    if signoff.get('verifierPassed') is not True:
        return False, 'parallel signoff does not confirm verifier pass'
    if signoff.get('agenticPassed') is not True:
        return False, 'parallel signoff does not confirm agentic pass'
    if signoff.get('repeatFailureCleared') is not True:
        return False, 'parallel signoff does not confirm repeat-failure conditions cleared'
    return True, 'parallel signoff valid'


def has_lock_skip(run: dict) -> bool:
    output = (run.get('output') or '').lower()
    return 'already holds the global lock' in output or 'skip:' in output and 'global lock' in output


def verifier_passed() -> bool:
    try:
        text = VERIFIER_MD.read_text(encoding='utf-8').lower()
    except FileNotFoundError:
        return False
    return 'status: independently verified pass' in text


def evaluate_health(agentic: dict, runs: list[dict], current_fp: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    by_name = {Path(item['path']).name: item for item in runs}
    exits = {name: item['exit'] for name, item in by_name.items()}
    verifier_run = by_name.get('ralph_docs_verify.py')
    runner_run = by_name.get('ralph_docs_runner.py')
    verifier_exit = exits.get('ralph_docs_verify.py')
    verifier_green = verifier_passed()
    for name in ['ralph_docs_check.py', 'ralph_docs_editorial_audit.py']:
        if exits.get(name) != 0:
            reasons.append(f'{name} failed')
    if verifier_exit not in (None, 0):
        verifier_lock_ok = verifier_run and verifier_exit == VERIFY_LOCK_EXIT and verifier_green and has_lock_skip(verifier_run)
        if not verifier_lock_ok:
            reasons.append('verifier failed')
    runner_lock_only = runner_run and exits.get('ralph_docs_runner.py') == RUNNER_LOCK_EXIT and has_lock_skip(runner_run)
    if agentic.get('status') != 'pass':
        reasons.append('agentic status is not pass')
    if agentic.get('loopHealthy') is not True:
        reasons.append('agentic loopHealthy is not true')
    if agentic.get('shouldUserNeedToRepeatThis') is not False:
        reasons.append('agentic still says the user should need to repeat this')
    if not verifier_green:
        reasons.append('verifier artifact is not green')
    signoff_ok, signoff_reason = parallel_signoff_valid(current_fp)
    if not signoff_ok and not runner_lock_only:
        reasons.append(signoff_reason)
    return (len(reasons) == 0, reasons)


def load_runtime_state() -> dict:
    try:
        return json.loads(STATE_JSON.read_text(encoding='utf-8'))
    except Exception:
        return {
            'consecutiveFailures': 0,
            'lastDocsStateFingerprint': None,
            'lastHealthyAtUtc': None,
            'lastEscalationAtUtc': None,
            'lastEscalationReason': None,
        }


def save_runtime_state(state: dict) -> None:
    STATE_JSON.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')


def repeat_failure_detected(state: dict, current_fp: str, reasons: list[str]) -> tuple[bool, str]:
    if state.get('consecutiveFailures', 0) >= REPEAT_FAILURE_THRESHOLD:
        return True, f"consecutive failures reached {state.get('consecutiveFailures', 0)}"
    if state.get('lastDocsStateFingerprint') == current_fp and reasons:
        return True, 'same docs-state fingerprint is still failing'
    return False, ''


def concise_run_summary(runs: list[dict]) -> list[dict]:
    summary = []
    for item in runs:
        output = (item.get('output') or '').strip()
        snippet = output.splitlines()[:6]
        summary.append({
            'path': item['path'],
            'exit': item['exit'],
            'summary': '\n'.join(snippet),
        })
    return summary


def aggressive_repair_passes() -> list[dict]:
    runs: list[dict] = []
    for _ in range(MAX_REPAIR_PASSES):
        runner = run_py(RUNNER, timeout=TIMEOUT)
        runs.append(runner)
        if runner['exit'] == RUNNER_LOCK_EXIT and has_lock_skip(runner):
            break
        verify_run = run_py(VERIFY, timeout=VERIFY_TIMEOUT)
        runs.append(verify_run)
        agentic = load_agentic()
        current_fp = docs_state_fingerprint()
        healthy, _ = evaluate_health(agentic, runs, current_fp)
        if healthy:
            break
        if verify_run['exit'] not in (0, VERIFY_LOCK_EXIT):
            break
    return runs


def write_status(payload: dict) -> None:
    STATUS_JSON.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    lines = [
        '# Temporary Docs Stack Watchdog Status',
        '',
        f"Checked at: {payload['checkedAtUtc']}",
        f"Healthy: {'yes' if payload['healthy'] else 'no'}",
        '',
        '## Reasons',
        '',
    ]
    if payload['reasons']:
        lines.extend([f"- {reason}" for reason in payload['reasons']])
    else:
        lines.append('- none')
    lines.extend(['', '## Script runs', ''])
    for run in payload['runs']:
        lines.append(f"- `{Path(run['path']).name}` exit={run['exit']}")
        if run.get('summary'):
            lines.extend(['', '```', run['summary'], '```'])
    STATUS_MD.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main(argv: list[str]) -> int:
    self_delete = '--self-delete' in argv
    state = load_runtime_state()
    if os.environ.get(WATCHDOG_SELF_ENV) == '1':
        payload = {
            'checkedAtUtc': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
            'healthy': False,
            'docsStateFingerprint': docs_state_fingerprint(),
            'artifactStateFingerprint': artifact_state_fingerprint(),
            'pid': os.getpid(),
            'reasons': ['watchdog recursion guard tripped'],
            'parallelSignoff': load_parallel_signoff(),
            'agentic': {'status': 'fail', 'loopHealthy': False, 'shouldUserNeedToRepeatThis': True, 'summary': 'watchdog recursion guard tripped'},
            'runtimeState': load_runtime_state(),
            'runs': [],
        }
        write_status(payload)
        print('DOCS_STACK_STILL_BROKEN')
        print('- watchdog recursion guard tripped')
        return 1
    runs = [
        run_py(CHECKER),
        run_py(EDITORIAL),
        run_py(AGENTIC, timeout=TIMEOUT, watchdog_mode=True, skip_agentic=True),
    ]
    if any(item['exit'] != 0 for item in runs):
        runs.extend(aggressive_repair_passes())
    verify_run = run_py(VERIFY, timeout=VERIFY_TIMEOUT, watchdog_mode=True, skip_agentic=True)
    runs.append(verify_run)
    agentic = load_agentic()
    current_fp = docs_state_fingerprint()
    healthy, reasons = evaluate_health(agentic, runs, current_fp)
    escalation, escalation_reason = repeat_failure_detected(state, current_fp, reasons)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    if healthy:
        state['consecutiveFailures'] = 0
        state['lastHealthyAtUtc'] = now
    else:
        state['consecutiveFailures'] = int(state.get('consecutiveFailures', 0)) + 1
    state['lastDocsStateFingerprint'] = current_fp
    if escalation:
        state['lastEscalationAtUtc'] = now
        state['lastEscalationReason'] = escalation_reason
        reasons = reasons + [f'REPEAT_FAILURE_ESCALATION_REQUIRED: {escalation_reason}', 'AUTO-ESCALATION RULE: use parallel subagents / all-hands repair before considering deactivation']
    payload = {
        'checkedAtUtc': now,
        'healthy': healthy,
        'docsStateFingerprint': current_fp,
        'artifactStateFingerprint': artifact_state_fingerprint(),
        'pid': os.getpid(),
        'reasons': reasons,
        'parallelSignoff': load_parallel_signoff(),
        'agentic': {
            'status': agentic.get('status'),
            'loopHealthy': agentic.get('loopHealthy'),
            'shouldUserNeedToRepeatThis': agentic.get('shouldUserNeedToRepeatThis'),
            'summary': agentic.get('summary'),
        },
        'runtimeState': state,
        'runs': concise_run_summary(runs),
    }
    save_runtime_state(state)
    write_status(payload)
    if healthy:
        print('DOCS_STACK_FIXED')
        if self_delete and SCRIPT.exists():
            SCRIPT.unlink()
            print('SELF_DELETED')
        return 0
    print('DOCS_STACK_STILL_BROKEN')
    for reason in reasons:
        print(f'- {reason}')
    return 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
