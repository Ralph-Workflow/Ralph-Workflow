#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
OUT = ROOT / 'agents/marketing/logs/marketing_loop_verifier_latest.md'
INDEPENDENT = ROOT / 'agents/marketing/logs/marketing_loop_independent_verification.json'
RUNNER = ROOT / 'agents/marketing/logs/marketing_loop_runner_latest.json'
MOMENTUM = ROOT / 'agents/marketing/logs/marketing_momentum_watchdog.json'
AUDIT = ROOT / 'agents/marketing/logs/marketing_workflow_audit_latest.json'
MAX_AGE_MIN = 240
MAX_EVIDENCE_SKEW_SECONDS = 30


def age_minutes(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 60.0


def independent_verification_is_fresh_against_runtime() -> tuple[bool, str | None]:
    peers = [path for path in (RUNNER, MOMENTUM, AUDIT) if path.exists()]
    if not INDEPENDENT.exists() or not peers:
        return True, None
    newest_peer = max(peers, key=lambda path: path.stat().st_mtime)
    if INDEPENDENT.stat().st_mtime + MAX_EVIDENCE_SKEW_SECONDS < newest_peer.stat().st_mtime:
        return False, newest_peer.name
    return True, None


def main() -> int:
    errors: list[str] = []
    payload: dict = {}

    if not INDEPENDENT.exists():
        errors.append(f'missing independent verification artifact: {INDEPENDENT}')
    else:
        if age_minutes(INDEPENDENT) > MAX_AGE_MIN:
            errors.append(f'stale independent verification artifact: {INDEPENDENT}')
        try:
            payload = json.loads(INDEPENDENT.read_text(encoding='utf-8'))
        except json.JSONDecodeError as exc:
            errors.append(f'invalid independent verification artifact: {exc}')

    verdict = str(payload.get('verdict', '')).lower() if payload else ''
    if payload and verdict != 'pass':
        errors.append(f"independent verifier did not pass (verdict={payload.get('verdict')!r})")

    coherent, newer_peer = independent_verification_is_fresh_against_runtime()
    if payload and not coherent:
        errors.append(
            'independent verification artifact predates newer live runtime evidence '
            f'({newer_peer}); rerun the independent verifier after the latest repair/runtime refresh'
        )

    checked_at = payload.get('checked_at') if payload else None
    status_text = 'independently verified pass' if not errors else 'independently verified fail'
    summary = payload.get('summary') if payload else None
    lines = [
        '# Marketing Loop Independent Verification',
        '',
        f'- Checked: {datetime.now().isoformat()}',
        f'- Status: {status_text}',
        f'- Independent artifact: `{INDEPENDENT}`',
    ]
    if checked_at:
        lines.append(f'- Independent check time: {checked_at}')
    if summary:
        lines.append(f'- Summary: {summary}')
    lines.extend(['', '## Verification result'])
    if errors:
        lines.extend(['', *[f'- {error}' for error in errors]])
    else:
        lines.extend(['', '- Independent verification artifact is present, fresh, and passed.'])

    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'ok': not errors, 'errors': errors, 'artifact': str(INDEPENDENT), 'checked_at': checked_at}, indent=2))
    return 0 if not errors else 1


if __name__ == '__main__':
    raise SystemExit(main())
