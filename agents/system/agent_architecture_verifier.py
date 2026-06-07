#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
OUT = ROOT / 'agents/system/logs/agent_architecture_verifier_latest.md'
INDEPENDENT = ROOT / 'agents/system/logs/agent_architecture_independent_verification.json'
ARCHITECTURE_JSON = ROOT / 'agents/system/logs/agent_architecture_latest.json'
ARCHITECTURE_MD = ROOT / 'agents/system/logs/agent_architecture_latest.md'
LOOP_INTEGRITY = ROOT / 'agents/system/logs/loop_integrity_latest.json'
HEALTH_MONITOR = ROOT / 'agents/system/logs/health_monitor_latest.json'
DOCS_VERIFIER = ROOT / 'agents/docs_quality/ralph_verifier_latest.md'
MARKET_INTELLIGENCE = ROOT / 'agents/marketing/logs/market_intelligence_latest.json'
MARKET_INTELLIGENCE_CONSUMPTION = ROOT / 'agents/marketing/logs/market_intelligence_consumption_latest.json'
MAX_AGE_MIN = 480
MAX_EVIDENCE_SKEW_SECONDS = 30

# Freshness coherence should only track relatively stable architecture/runtime evidence.
# Health-monitor outputs are still checked for green status by the independent verifier,
# but using the 15-minute monitor artifact as a freshness peer would make architecture
# signoff self-expire continuously even when architecture state has not changed.
FRESHNESS_PEERS = (
    ARCHITECTURE_JSON,
    ARCHITECTURE_MD,
    LOOP_INTEGRITY,
    DOCS_VERIFIER,
    MARKET_INTELLIGENCE,
    MARKET_INTELLIGENCE_CONSUMPTION,
)


def age_minutes(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 60.0


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def non_self_referential_health_issues(payload: dict) -> list[dict]:
    issues = payload.get('issues') or []
    filtered: list[dict] = []
    for issue in issues:
        if issue.get('category') == 'escalation_required':
            continue
        if (
            issue.get('job_id') == '__artifacts__'
            and issue.get('name') in {
                'agent_architecture_verifier',
                'agent_architecture_verifier_runtime',
                'agent_architecture_json',
            }
        ):
            continue
        filtered.append(issue)
    return filtered


def is_marketing_owned_issue(issue: dict) -> bool:
    name = str(issue.get('name') or '')
    job_id = str(issue.get('job_id') or '')
    path = str(issue.get('path') or '')
    owner_domain = str(issue.get('owner_domain') or '')
    blocked_by = [str(item) for item in (issue.get('blocked_by') or [])]
    marketing_names = {'competitor-analysis', 'content-poster', 'market-intelligence-refresh'}
    return (
        owner_domain == 'site'
        or 'Push research findings to git repo' in name
        or 'marketing' in name
        or name in marketing_names
        or 'marketing' in job_id
        or '/agents/marketing/' in path
        or name.startswith('reddit-')
        or name.startswith('apollo-')
        or name.startswith('seo-')
        or any(item.startswith('marketing_') or item.startswith('marketing-') for item in blocked_by)
    )


def is_docs_owned_issue(issue: dict) -> bool:
    name = str(issue.get('name') or '')
    job_id = str(issue.get('job_id') or '')
    path = str(issue.get('path') or '')
    owner_domain = str(issue.get('owner_domain') or '')
    return (
        owner_domain == 'docs'
        or 'docs' in name
        or 'docs' in job_id
        or '/agents/docs_quality/' in path
    )


def is_unblocker_owned_issue(issue: dict) -> bool:
    name = str(issue.get('name') or '')
    job_id = str(issue.get('job_id') or '')
    path = str(issue.get('path') or '')
    owner_domain = str(issue.get('owner_domain') or '')
    blocked_by = [str(item) for item in (issue.get('blocked_by') or [])]
    return (
        owner_domain == 'unblocker'
        or 'blocked-channel-recovery' in name
        or 'blocked-channel-recovery' in job_id
        or '/agents/unblocker/' in path
        or any('unblocker' in str(item) for item in blocked_by)
    )


def is_external_owned_issue(issue: dict) -> bool:
    return is_marketing_owned_issue(issue) or is_docs_owned_issue(issue) or is_unblocker_owned_issue(issue)


def architecture_runtime_health_blockers(payload: dict) -> tuple[list[dict], list[dict]]:
    blockers: list[dict] = []
    external_only: list[dict] = []
    external_marketing_categories = {
        'loop_verification_fail',
        'escalation_required',
        'review_followup_required',
    }
    for issue in non_self_referential_health_issues(payload):
        name = str(issue.get('name') or '')
        category = str(issue.get('category') or '')
        blocked_by = issue.get('blocked_by') or []
        if is_external_owned_issue(issue) and category in external_marketing_categories:
            external_only.append(issue)
            continue
        if name.startswith('marketing_independent_verification_blockers_'):
            external_only.append(issue)
            continue
        if is_external_owned_issue(issue):
            external_only.append(issue)
            continue
        if blocked_by and all(str(item).startswith('marketing_') or str(item) == 'marketing_independent_verification' for item in blocked_by):
            external_only.append(issue)
            continue
        blockers.append(issue)
    return blockers, external_only


def independent_verification_is_fresh_against_runtime() -> tuple[bool, str | None]:
    peers = [path for path in FRESHNESS_PEERS if path.exists()]
    if not INDEPENDENT.exists() or not peers:
        return True, None
    newest_peer = max(peers, key=lambda path: path.stat().st_mtime)
    if INDEPENDENT.stat().st_mtime + MAX_EVIDENCE_SKEW_SECONDS < newest_peer.stat().st_mtime:
        return False, newest_peer.name
    return True, None


def sync_architecture_latest(payload: dict, errors: list[str], checked_at: str | None, summary: str | None) -> None:
    if not ARCHITECTURE_JSON.exists() or not ARCHITECTURE_MD.exists():
        return

    try:
        architecture = load_json(ARCHITECTURE_JSON)
    except Exception:
        return

    independent = architecture.setdefault('independent_verification', {})
    independent['status'] = 'performed'
    independent['artifacts'] = [str(INDEPENDENT), str(OUT)]
    independent['summary'] = summary
    independent['checked_at'] = checked_at
    independent['verdict'] = payload.get('verdict') if payload else None

    runtime = architecture.setdefault('runtime_assertions', {})
    runtime['independent_architecture_verdict'] = payload.get('verdict') if payload else None
    runtime['architecture_verifier_checked_at'] = checked_at
    runtime['architecture_verifier_status'] = 'pass' if not errors else 'fail'

    pending_item = 'Fresh architecture independent verification against the refreshed live topology.'
    needs = architecture.get('what_still_needs_independent_verification') or []
    if not errors:
        architecture['what_still_needs_independent_verification'] = [item for item in needs if item != pending_item]
    elif pending_item not in needs:
        architecture['what_still_needs_independent_verification'] = [pending_item, *needs]

    ARCHITECTURE_JSON.write_text(json.dumps(architecture, indent=2) + '\n', encoding='utf-8')

    md = ARCHITECTURE_MD.read_text(encoding='utf-8')

    lines = md.splitlines()
    out_lines: list[str] = []
    skip_prefixes = (
        '- Summary:',
        '- Previous artifact verdict:',
        '- Previous artifact checked at:',
    )
    in_independent_section = False
    summary_inserted = False
    verifier_verdict = payload.get('verdict') if payload else 'fail'
    for line in lines:
        if line.startswith('- Verifier status:'):
            out_lines.append('- Verifier status: performed')
            continue
        if line.startswith('- Verifier verdict:'):
            out_lines.append(f'- Verifier verdict: {verifier_verdict}')
            continue
        if line == '## Independent verification':
            in_independent_section = True
            summary_inserted = False
            out_lines.append(line)
            continue
        if in_independent_section and line.startswith('## '):
            if not summary_inserted and summary:
                out_lines.append(f'- Summary: {summary}')
            in_independent_section = False
        if in_independent_section and line.startswith(skip_prefixes):
            continue
        if in_independent_section and line.startswith('- Performed:'):
            out_lines.append('- Performed: yes')
            continue
        if in_independent_section and line.startswith('- Verdict:'):
            out_lines.append(f'- Verdict: {verifier_verdict}')
            if not summary_inserted and summary:
                out_lines.append(f'- Summary: {summary}')
                summary_inserted = True
            continue
        out_lines.append(line)
    ARCHITECTURE_MD.write_text('\n'.join(out_lines) + '\n', encoding='utf-8')


def invalidate_architecture_latest(errors: list[str], checked_at: str | None) -> None:
    sync_architecture_latest({}, errors, checked_at, 'Independent verification found architecture blockers that prevent a healthy verifier pass.')


def clear_architecture_invalidation(checked_at: str | None) -> None:
    payload = load_json(INDEPENDENT) if INDEPENDENT.exists() else {}
    sync_architecture_latest(payload, [], checked_at, payload.get('summary'))


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
    if payload and verdict not in {'pass', 'qualified_pass'}:
        errors.append(f"independent verifier did not pass (verdict={payload.get('verdict')!r})")

    coherent, newer_peer = independent_verification_is_fresh_against_runtime()
    if payload and not coherent:
        errors.append(
            'independent verification artifact predates newer runtime evidence '
            f'({newer_peer}); rerun independent verification after the latest architecture/runtime refresh'
        )

    if HEALTH_MONITOR.exists():
        if age_minutes(HEALTH_MONITOR) > MAX_AGE_MIN:
            errors.append(f'stale health monitor artifact: {HEALTH_MONITOR}')
        else:
            try:
                health_payload = load_json(HEALTH_MONITOR)
            except json.JSONDecodeError as exc:
                errors.append(f'invalid health monitor artifact: {exc}')
            else:
                health_issues, external_watchpoints = architecture_runtime_health_blockers(health_payload)
                if health_issues:
                    errors.append(
                        'health monitor reports non-architecture live issues: '
                        + ', '.join(f"{issue.get('name')}:{issue.get('category')}" for issue in health_issues)
                    )
    else:
        errors.append(f'missing health monitor artifact: {HEALTH_MONITOR}')

    checked_at = payload.get('checked_at') if payload else None
    status_text = 'independently verified pass' if not errors else 'independently verified fail'
    summary = payload.get('summary') if payload else None
    lines = [
        '# Agent Architecture Independent Verification',
        '',
        f'- Checked: {datetime.now().isoformat()}',
        f'- Status: {status_text}',
        f'- Independent artifact: `{INDEPENDENT}`',
    ]
    if checked_at:
        lines.append(f'- Independent check time: {checked_at}')
    if summary:
        lines.append(f'- Summary: {summary}')
    if payload and payload.get('verdict') == 'qualified_pass':
        external_blockers = payload.get('external_blockers') or []
        lines.append(f"- Qualified external blockers: {', '.join(external_blockers) if external_blockers else 'none'}")
    lines.extend(['', '## Verification result'])
    if errors:
        lines.extend(['', *[f'- {error}' for error in errors]])
    else:
        lines.extend(['', '- Independent verification artifact is present, fresh, and passed.'])

    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    if errors:
        invalidate_architecture_latest(errors, checked_at)
    else:
        clear_architecture_invalidation(checked_at)
    print(json.dumps({'ok': not errors, 'errors': errors, 'artifact': str(INDEPENDENT), 'checked_at': checked_at}, indent=2))
    return 0 if not errors else 1


if __name__ == '__main__':
    raise SystemExit(main())
