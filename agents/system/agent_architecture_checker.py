#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
JSON_ART = ROOT / 'agents/system/logs/agent_architecture_latest.json'
MD_ART = ROOT / 'agents/system/logs/agent_architecture_latest.md'
MAX_AGE_MIN = 480


def age_minutes(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 60.0


def main() -> int:
    for path in [JSON_ART, MD_ART]:
        if not path.exists():
            print(f'AGENT_ARCHITECTURE_FAIL: missing artifact: {path}')
            return 1
        if age_minutes(path) > MAX_AGE_MIN:
            print(f'AGENT_ARCHITECTURE_FAIL: stale artifact: {path}')
            return 1

    payload = json.loads(JSON_ART.read_text(encoding='utf-8'))
    required_top = ['schema_version', 'executive_verdict', 'findings', 'ordered_fix_plan']
    missing = [key for key in required_top if key not in payload]
    if missing:
        print('AGENT_ARCHITECTURE_FAIL: missing keys: ' + ', '.join(missing))
        return 1

    verdict = payload.get('executive_verdict', {})
    if not verdict.get('primary_failure_mode') or not verdict.get('most_urgent_fix'):
        print('AGENT_ARCHITECTURE_FAIL: incomplete executive verdict')
        return 1

    print('AGENT_ARCHITECTURE_OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
