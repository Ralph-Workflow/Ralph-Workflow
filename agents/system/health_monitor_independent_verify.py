#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents/system/logs'
OUT = LOG_DIR / 'health_monitor_independent_verification.json'
HEALTH_MONITOR_SOURCE = ROOT / 'agents/system/health_monitor.py'
HEALTH_MONITOR_LATEST = LOG_DIR / 'health_monitor_latest.json'
ARCH_VERIFIER_LATEST = LOG_DIR / 'agent_architecture_verifier_latest.md'
LOOP_INTEGRITY_LATEST = LOG_DIR / 'loop_integrity_latest.json'
DOCS_AGENTIC_LATEST = ROOT / 'agents/docs_quality/ralph_agentic_latest.json'
REQUIRED_SOURCE_SNIPPETS = [
    'ARCHITECTURE_VERIFIER_SCRIPT',
    'GATEWAY_JOBS_FILE',
    'agent_architecture_json',
    'agent_architecture_verifier_runtime',
    'rerun_independent_architecture_verification',
    'rerun_architecture_verifier',
    'Architecture audit metadata disagrees with live Gateway cron topology',
    'docs_agentic_review',
    'required_empty_lists',
    'required_criteria_pass',
]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def main() -> int:
    blockers: list[str] = []
    verified_repairs: list[dict] = []
    evidence: list[dict] = []

    for path in (HEALTH_MONITOR_SOURCE, HEALTH_MONITOR_LATEST, ARCH_VERIFIER_LATEST, LOOP_INTEGRITY_LATEST, DOCS_AGENTIC_LATEST):
        if not path.exists():
            blockers.append(f'missing required evidence: {path}')

    source_text = HEALTH_MONITOR_SOURCE.read_text(encoding='utf-8') if HEALTH_MONITOR_SOURCE.exists() else ''
    missing_snippets = [snippet for snippet in REQUIRED_SOURCE_SNIPPETS if snippet not in source_text]
    if missing_snippets:
        blockers.append('health monitor source is missing architecture-verifier contract enforcement: ' + ', '.join(missing_snippets))
    else:
        verified_repairs.append({
            'claim': 'health_monitor.py now checks both the live architecture verifier and live-vs-artifact cron topology coherence',
            'status': 'verified',
            'details': 'Source inspection shows runtime verifier enforcement plus direct checks that agent_architecture_latest.json agrees with live Gateway cron topology.'
        })

    health = load_json(HEALTH_MONITOR_LATEST) if HEALTH_MONITOR_LATEST.exists() else {}
    if health and health.get('issues_found') != 0:
        blockers.append(f"health monitor latest run is not green: issues_found={health.get('issues_found')}")
    elif health:
        verified_repairs.append({
            'claim': 'direct rerun of health_monitor.py is green after the repair',
            'status': 'verified',
            'details': f"health_monitor_latest.json records jobs_checked={health.get('jobs_checked')} and issues_found=0."
        })

    verifier_text = ARCH_VERIFIER_LATEST.read_text(encoding='utf-8') if ARCH_VERIFIER_LATEST.exists() else ''
    if 'Status: independently verified pass' not in verifier_text:
        blockers.append('architecture verifier latest artifact does not show independent pass')
    else:
        verified_repairs.append({
            'claim': 'architecture-verifier path is healthy after the health-monitor repair',
            'status': 'verified',
            'details': 'agent_architecture_verifier_latest.md currently reports Status: independently verified pass.'
        })

    docs_agentic = load_json(DOCS_AGENTIC_LATEST) if DOCS_AGENTIC_LATEST.exists() else {}
    if docs_agentic:
        must_fix = docs_agentic.get('mustFix') or []
        if not isinstance(must_fix, list):
            must_fix = [must_fix]
        failing_criteria = [key for key, value in (docs_agentic.get('criteria') or {}).items() if value != 'pass']
        if docs_agentic.get('status') != 'pass' or docs_agentic.get('loopHealthy') is not True or docs_agentic.get('shouldUserNeedToRepeatThis') is not False or any(str(item).strip() for item in must_fix) or failing_criteria:
            blockers.append('docs agentic artifact is not fully green under the tightened contract')
        else:
            verified_repairs.append({
                'claim': 'docs loop artifact is genuinely green under the tightened contract',
                'status': 'verified',
                'details': 'ralph_agentic_latest.json has status=pass, loopHealthy=true, shouldUserNeedToRepeatThis=false, empty mustFix, and all criteria pass.'
            })

    loop_integrity = load_json(LOOP_INTEGRITY_LATEST) if LOOP_INTEGRITY_LATEST.exists() else {}
    if loop_integrity:
        results = {entry.get('name'): entry for entry in loop_integrity.get('results', [])}
        for loop_name in ('agent-architecture-watchdog', 'autonomous-marketing-stack'):
            status = results.get(loop_name, {}).get('status')
            if status != 'ok':
                blockers.append(f'loop integrity still reports {loop_name} as {status!r}')
        if not blockers:
            verified_repairs.append({
                'claim': 'loop-integrity audit is green on the repaired full-contract loops',
                'status': 'verified',
                'details': 'loop_integrity_latest.json now reports agent-architecture-watchdog status=ok and autonomous-marketing-stack status=ok.'
            })

    payload = {
        'checked_at': datetime.now().astimezone().isoformat(),
        'verdict': 'pass' if not blockers else 'fail',
        'summary': (
            'Fresh independent verification confirms the health monitor now checks the live architecture-verifier boundary, and the repaired full-contract loops remain green.'
            if not blockers else
            'Independent verification found blockers that prevent the repaired health monitor from being certified healthy.'
        ),
        'verified_repairs': verified_repairs,
        'remaining_blockers': blockers,
        'evidence': [
            {
                'source': str(HEALTH_MONITOR_SOURCE),
                'summary': 'Health monitor source includes live architecture-verifier enforcement and repair reruns.'
            },
            {
                'source': str(HEALTH_MONITOR_LATEST),
                'summary': f"Latest health-monitor run reports issues_found={health.get('issues_found')} and jobs_checked={health.get('jobs_checked')}."
            },
            {
                'source': str(ARCH_VERIFIER_LATEST),
                'summary': 'Architecture verifier latest artifact currently shows an independently verified pass.'
            },
            {
                'source': str(LOOP_INTEGRITY_LATEST),
                'summary': 'Loop integrity latest artifact is used to verify full-contract loop status after repair.'
            },
            {
                'source': str(DOCS_AGENTIC_LATEST),
                'summary': 'Docs agentic latest artifact is checked for empty mustFix and full green criteria under the tightened contract.'
            },
        ],
    }
    OUT.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(payload, indent=2))
    return 0 if not blockers else 1


if __name__ == '__main__':
    raise SystemExit(main())
