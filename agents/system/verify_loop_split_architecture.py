#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
DOCS_PRECHECK = ROOT / 'agents/docs_quality/ralph_docs_supervisor_precheck.py'
MARKETING_PRECHECK = ROOT / 'agents/marketing/marketing_workflow_audit_precheck.py'
TEST_SCRIPT = ROOT / 'agents/system/test_loop_split_runtime.py'
EXPECTED = {
    'docs-precheck': {
        'name': 'ralph-docs-supervisor-precheck',
        'model': 'minimax/MiniMax-M3',
    },
    'docs-gpt-worker': {
        'name': 'ralph-workflow-docs-verifier-supervisor',
        'model': 'openrouter/deepseek/deepseek-v4-pro',
        'expr': '6 */12 * * *',
    },
    'marketing-audit': {
        'name': 'marketing-workflow-audit',
        'model': 'openrouter/deepseek/deepseek-v4-pro',
        'expr': '20 8 * * *',
    },
}


def load_jobs() -> dict:
    return json.loads(subprocess.check_output(['openclaw', 'cron', 'list', '--json'], text=True))


def find_job(jobs: dict, name: str) -> dict:
    for job in jobs.get('jobs', []):
        if job.get('name') == name:
            return job
    raise AssertionError(f'missing cron job: {name}')


def run_cmd(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def main() -> int:
    proofs: list[dict] = []
    jobs = load_jobs()
    for spec in EXPECTED.values():
        job = find_job(jobs, spec['name'])
        payload = job.get('payload', {}) or {}
        assert payload.get('model') == spec['model'], f"{spec['name']} model mismatch: {payload.get('model')}"
        if 'expr' in spec:
            assert job.get('schedule', {}).get('expr') == spec['expr'], f"{spec['name']} expr mismatch: {job.get('schedule', {}).get('expr')}"
        proofs.append({
            'job': spec['name'],
            'model': payload.get('model'),
            'expr': job.get('schedule', {}).get('expr'),
        })

    for script in [DOCS_PRECHECK]:
        code, out, err = run_cmd(['python3', str(script), '--dry-run'])
        assert code == 0, f'{script.name} dry-run failed: {(out or err).strip()}'
        payload = json.loads((out or '{}').strip())
        assert payload.get('status') in {'skip_healthy', 'skip_cooldown', 'would_trigger'}, f'{script.name} unexpected status: {payload.get("status")}'
        proofs.append({'script': script.name, 'status': payload.get('status'), 'reasons': payload.get('reasons', [])[:5]})

    code, out, err = run_cmd(['python3', str(TEST_SCRIPT)])
    assert code == 0, f'test suite failed: {(out or err).strip()}'
    proofs.append({'tests': 'agents/system/test_loop_split_runtime.py', 'result': 'pass'})

    result = {'ok': True, 'proofs': proofs}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
