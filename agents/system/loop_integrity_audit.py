#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

REGISTRY = Path('/home/mistlight/.openclaw/workspace/agents/system/self_improvement_loops.json')
LOG_DIR = Path('/home/mistlight/.openclaw/workspace/agents/system/logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)
JSON_LOG = LOG_DIR / 'loop_integrity_latest.json'
MD_LOG = LOG_DIR / 'loop_integrity_latest.md'


def sh(*args: str) -> tuple[int, str, str]:
    p = subprocess.run(args, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def live_cron_jobs() -> list[dict]:
    code, out, err = sh('openclaw', 'cron', 'list', '--json')
    if code != 0:
        raise RuntimeError((out or err).strip() or 'failed to inspect live cron jobs')
    return json.loads(out or '{}').get('jobs', []) or []


def read_crontab() -> str:
    code, out, err = sh('bash', '-lc', 'crontab -l 2>/dev/null || true')
    return out if code == 0 else ''


def ensure_crontab_lines(lines: list[str]) -> list[str]:
    current = read_crontab()
    added = []
    missing = [line for line in lines if line not in current]
    if not missing:
        return added
    script = "(crontab -l 2>/dev/null; printf '\n'"
    for line in missing:
        script += f"; printf '%s\\n' \"{line}\""
    script += ") | awk '!seen[$0]++' | crontab -"
    code, out, err = sh('bash', '-lc', script)
    if code == 0:
        added.extend(missing)
    else:
        raise RuntimeError((out or err).strip() or 'failed to update crontab')
    return added


def path_age_minutes(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 60.0


def run_py(path: str) -> tuple[int, str]:
    p = subprocess.run(['python3', path], capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def load_registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding='utf-8'))


def loop_status(loop: dict) -> dict:
    out = {
        'name': loop['name'],
        'repairs': [],
        'errors': [],
        'status': 'ok',
    }
    required_paths = [
        loop['checkerScript'], loop['runnerScript'], loop['verifierScript'],
        loop['runnerArtifact'], loop['verifierArtifact'],
    ]
    for raw in required_paths:
        p = Path(raw)
        if not p.exists():
            out['errors'].append(f'missing required path: {raw}')

    loop_kind = loop.get('kind', 'crontab-watchdog')
    if loop_kind == 'gateway-cron':
        try:
            jobs = live_cron_jobs()
        except Exception as e:
            out['errors'].append(f'failed to inspect live cron jobs: {e}')
        else:
            owner_job = next((job for job in jobs if job.get('id') == loop.get('ownerCronJob')), None)
            if not owner_job:
                out['errors'].append(f"missing live owner cron job: {loop.get('ownerCronJob')}")
            else:
                if owner_job.get('name') != loop['name']:
                    out['errors'].append(
                        f"owner cron job name mismatch: expected {loop['name']}, got {owner_job.get('name')}"
                    )
                if not owner_job.get('enabled', True):
                    out['errors'].append(f"owner cron job disabled: {owner_job.get('id')}")
                out['ownerCronStatus'] = owner_job.get('status')
    else:
        try:
            added = ensure_crontab_lines([loop['runnerCrontabLine'], loop['verifierCrontabLine']])
            for line in added:
                out['repairs'].append(f'restored crontab line: {line}')
        except Exception as e:
            out['errors'].append(f'failed to ensure crontab lines: {e}')

    if out['errors']:
        out['status'] = 'error'
        return out

    max_age = loop.get('maxArtifactAgeMinutes', 120)
    runner_art = Path(loop['runnerArtifact'])
    verifier_art = Path(loop['verifierArtifact'])

    if path_age_minutes(runner_art) > max_age:
        code, text = run_py(loop['runnerScript'])
        out['repairs'].append('runner artifact was stale; executed runner')
        out['runnerOutput'] = text
        if code != 0:
            out['errors'].append('runner failed while repairing stale state')

    if path_age_minutes(verifier_art) > max_age:
        code, text = run_py(loop['verifierScript'])
        out['repairs'].append('verifier artifact was stale; executed verifier')
        out['verifierOutput'] = text
        if code != 0:
            out['errors'].append('verifier failed while repairing stale state')

    checker_code, checker_out = run_py(loop['checkerScript'])
    out['checkerResult'] = checker_out
    if checker_code != 0:
        out['repairs'].append('checker failed; executed runner for remediation')
        code, text = run_py(loop['runnerScript'])
        out['runnerOutput'] = text
        if code != 0:
            out['errors'].append('runner failed after checker failure')
        out['repairs'].append('executed verifier after remediation')
        vcode, vtext = run_py(loop['verifierScript'])
        out['verifierOutput'] = vtext
        if vcode != 0:
            out['errors'].append('verifier failed after remediation')
        checker_code, checker_out = run_py(loop['checkerScript'])
        out['checkerResultAfterRepair'] = checker_out
        if checker_code != 0:
            out['errors'].append('checker still failing after remediation/verifier pass')

    verifier_text = verifier_art.read_text(encoding='utf-8') if verifier_art.exists() else ''
    if loop['requiresVerifierPassPhrase'] not in verifier_text:
        if loop.get('allowVerifierContractExternalized') and str(out.get('checkerResult') or '').startswith('AGENT_ARCHITECTURE_OK'):
            out['verifierContractExternalized'] = True
        else:
            out['errors'].append('verifier artifact missing required pass phrase')

    out['status'] = 'ok' if not out['errors'] else 'error'
    return out


def write_logs(results: list[dict]) -> None:
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    payload = {'timestamp': now, 'results': results}
    JSON_LOG.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    lines = ['# Self-Improvement Loop Integrity Audit', '', f'Timestamp: {now}', '']
    for r in results:
        lines.append(f"## {r['name']}")
        lines.append(f"- Status: {r['status']}")
        if r.get('repairs'):
            lines.append('- Repairs:')
            for item in r['repairs']:
                lines.append(f'  - {item}')
        if r.get('errors'):
            lines.append('- Errors:')
            for item in r['errors']:
                lines.append(f'  - {item}')
        if r.get('checkerResult'):
            lines.append(f"- Checker: `{r['checkerResult'].splitlines()[0]}`")
        if r.get('checkerResultAfterRepair'):
            lines.append(f"- Checker after repair: `{r['checkerResultAfterRepair'].splitlines()[0]}`")
        lines.append('')
    MD_LOG.write_text('\n'.join(lines), encoding='utf-8')


def main() -> int:
    registry = load_registry()
    results = [loop_status(loop) for loop in registry.get('loops', [])]
    write_logs(results)
    return 0 if all(r['status'] == 'ok' for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
