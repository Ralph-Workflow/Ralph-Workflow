#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
REGISTRY = ROOT / 'agents/system/self_improvement_loops.json'
ARTIFACT = ROOT / 'agents/system/logs/agent_architecture_latest.json'


def main() -> int:
    before = ARTIFACT.stat().st_mtime if ARTIFACT.exists() else 0.0
    registry = json.loads(REGISTRY.read_text(encoding='utf-8'))
    loop = next(item for item in registry['loops'] if item['name'] == 'agent-architecture-watchdog')
    job_id = loop['ownerCronJob']
    jobs = json.loads(subprocess.check_output(['openclaw', 'cron', 'list', '--json'], text=True)).get('jobs', [])
    job = next((item for item in jobs if item.get('id') == job_id), {})
    payload = job.get('payload', {}) or {}
    state = job.get('state', {}) or {}
    timeout_seconds = int(payload.get('timeoutSeconds') or 0)
    recent_duration_seconds = int((state.get('lastDurationMs') or 0) / 1000)
    adaptive_floor = recent_duration_seconds + max(300, recent_duration_seconds)
    if timeout_seconds > 60:
        wait_seconds = min(max(600, adaptive_floor), max(600, timeout_seconds - 30), 1800)
    else:
        wait_seconds = max(600, adaptive_floor)
    proc = subprocess.run(['openclaw', 'cron', 'run', job_id], capture_output=True, text=True)
    if proc.returncode != 0:
        print((proc.stdout or proc.stderr).strip())
        return 1
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if ARTIFACT.exists() and ARTIFACT.stat().st_mtime > before:
            print(json.dumps({'ok': True, 'job_id': job_id, 'artifact': str(ARTIFACT)}, indent=2))
            return 0
        time.sleep(3)
    print(json.dumps({'ok': False, 'job_id': job_id, 'detail': 'artifact did not refresh before timeout'}, indent=2))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
