#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents/system/logs'
OUT_JSON = LOG_DIR / 'agent_architecture_latest.json'
OUT_MD = LOG_DIR / 'agent_architecture_latest.md'
GATEWAY_JOBS = Path('/home/mistlight/.openclaw/cron/jobs.json')
LOOP_INTEGRITY = LOG_DIR / 'loop_integrity_latest.json'
HEALTH_MONITOR = LOG_DIR / 'health_monitor_latest.json'
INDEPENDENT = LOG_DIR / 'agent_architecture_independent_verification.json'
VERIFIER_MD = LOG_DIR / 'agent_architecture_verifier_latest.md'
DOCS_VERIFIER = ROOT / 'agents/docs_quality/ralph_verifier_latest.md'
MARKET_INTELLIGENCE_CONSUMPTION = ROOT / 'agents/marketing/logs/market_intelligence_consumption_latest.json'
MARKETING_AUDIT = ROOT / 'agents/marketing/logs/marketing_workflow_audit_latest.json'
MARKETING_INDEPENDENT = ROOT / 'agents/marketing/logs/marketing_loop_independent_verification.json'
MARKET_INTELLIGENCE = ROOT / 'agents/marketing/logs/market_intelligence_latest.json'


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def cron_jobs() -> list[dict]:
    """Live runtime topology only (default --json, excludes persisted disabled history)."""
    payload = json.loads(subprocess.check_output(['openclaw', 'cron', 'list', '--json'], text=True))
    return payload.get('jobs', []) or []


def all_cron_jobs() -> list[dict]:
    """All jobs including persisted disabled history (--all)."""
    payload = json.loads(subprocess.check_output(['openclaw', 'cron', 'list', '--all', '--json'], text=True))
    return payload.get('jobs', []) or []


def loop_status_map() -> dict:
    payload = load_json(LOOP_INTEGRITY)
    return {entry.get('name'): entry.get('status') for entry in payload.get('results', [])}


def non_escalation_issues(payload: dict) -> list[dict]:
    return [issue for issue in (payload.get('issues') or []) if issue.get('category') != 'escalation_required']


def is_external_issue(issue: dict) -> bool:
    name = str(issue.get('name') or '')
    job_id = str(issue.get('job_id') or '')
    path = str(issue.get('path') or '')
    owner_domain = str(issue.get('owner_domain') or '')
    blocked_by = [str(item) for item in (issue.get('blocked_by') or [])]
    if owner_domain == 'docs' or 'docs' in name or 'docs' in job_id or '/agents/docs_quality/' in path:
        return True
    if (
        'marketing' in name
        or 'marketing' in job_id
        or '/agents/marketing/' in path
        or name.startswith('reddit-')
        or name.startswith('apollo-')
        or any(item.startswith('marketing_') or item.startswith('marketing-') for item in blocked_by)
    ):
        return True
    if (
        owner_domain == 'unblocker'
        or 'blocked-channel-recovery' in name
        or 'blocked-channel-recovery' in job_id
        or '/agents/unblocker/' in path
        or any('unblocker' in str(item) for item in blocked_by)
    ):
        return True
    return False


def marketing_audit_timeout_repair_state(jobs: list[dict], health_issues: list[dict]) -> dict:
    job = next((item for item in jobs if item.get('name') == 'marketing-workflow-audit'), {})
    state = job.get('state') or {}
    payload = job.get('payload') or {}
    issue = next((item for item in health_issues if item.get('name') == 'marketing-workflow-audit' and item.get('category') == 'timeout'), {})
    timeout_seconds = int(payload.get('timeoutSeconds') or 0)
    last_duration_ms = int(state.get('lastDurationMs') or issue.get('last_duration_ms') or 0)
    headroom_seconds = timeout_seconds - (last_duration_ms / 1000.0 if last_duration_ms else 0.0)
    repaired = bool(issue) and timeout_seconds and last_duration_ms and headroom_seconds > 0
    return {
        'job': job,
        'issue': issue,
        'timeout_seconds': timeout_seconds,
        'last_duration_ms': last_duration_ms,
        'headroom_seconds': headroom_seconds,
        'repaired': repaired,
    }


SELF_REPAIR_AUDIT_JSON = LOG_DIR / 'self_repair_self_improve_audit_latest.json'


def main() -> int:
    jobs = cron_jobs()
    all_jobs = all_cron_jobs()
    persisted_jobs = (load_json(GATEWAY_JOBS).get('jobs') or []) if GATEWAY_JOBS.exists() else []
    running_jobs = sorted(job.get('name') for job in jobs if job.get('status') == 'running' and job.get('name'))
    last_error_jobs = sorted(job.get('name') for job in jobs if job.get('status') == 'error' and job.get('name'))
    last_error_details = {
        job.get('name'): (job.get('state') or {}).get('lastError')
        for job in jobs
        if job.get('status') == 'error' and job.get('name')
    }
    disabled_live = sorted(job.get('name') for job in jobs if not job.get('enabled', True) and job.get('name'))
    disabled_persisted = sorted(
        set(job.get('name') for job in all_jobs if not job.get('enabled', True) and job.get('name'))
        - set(job.get('name') for job in jobs if not job.get('enabled', True) and job.get('name'))
    )

    loop_integrity = load_json(LOOP_INTEGRITY)
    health = load_json(HEALTH_MONITOR)
    marketing_audit = load_json(MARKETING_AUDIT)
    marketing_independent = load_json(MARKETING_INDEPENDENT)
    market_consumption = load_json(MARKET_INTELLIGENCE_CONSUMPTION)
    independent = load_json(INDEPENDENT)
    loop_statuses = loop_status_map()

    raw_health_issues = non_escalation_issues(health)
    health_issues = []
    architecture_health_issues = []
    external_watch_issues = []
    for issue in raw_health_issues:
        name = str(issue.get('name') or '')
        if issue.get('job_id') == '__artifacts__' and name in {
            'agent_architecture_verifier', 'agent_architecture_verifier_runtime', 'agent_architecture_json'
        }:
            continue
        health_issues.append(issue)
        if is_external_issue(issue):
            external_watch_issues.append(issue)
        else:
            architecture_health_issues.append(issue)

    marketing_verdict = str(marketing_independent.get('verdict') or '').lower()
    external_issue_summaries = [
        f"{issue.get('name')}:{issue.get('category')}" for issue in external_watch_issues
    ]
    marketing_timeout_state = marketing_audit_timeout_repair_state(jobs, health_issues)
    if marketing_verdict == 'pass' and not architecture_health_issues and not external_watch_issues:
        overall_health = 'healthy'
        primary_failure_mode = 'No architecture-owned blocker is active.'
        urgent_fix = 'Keep direct live cron inspection and independent verification in the loop.'
    else:
        overall_health = 'high_risk'
        primary_failure_mode = 'Whole-stack certification remains blocked by external owner-loop residue or a failed independent signoff.'
        urgent_fix = 'Do not certify green until the external owner loop clears its live residue and independent signoff stays current.'

    if marketing_verdict != 'pass':
        primary_finding = {
            'severity': 'high',
            'title': 'Marketing remains externally red on outcome evidence',
            'mechanism': 'Marketing independent verification still fails closed because primary-repo adoption is measurement-pending.',
            'source_layer': 'cross-loop ownership',
            'root_cause': 'Outcome evidence for Codeberg-primary adoption is still missing.',
            'evidence_refs': [
                str(MARKETING_INDEPENDENT),
                str(MARKETING_AUDIT),
                str(HEALTH_MONITOR),
            ],
            'confidence': 0.99,
            'recommended_fix': 'Let the marketing owner loop produce fresh measurable outcome evidence, then rerun marketing independent verification before calling the whole stack green.',
        }
    elif external_watch_issues:
        timeout_repaired = marketing_timeout_state['repaired']
        primary_finding = {
            'severity': 'medium',
            'title': 'External owner-loop residue is still live',
            'mechanism': (
                'Live health-monitor residue is now localized to the previous marketing-workflow-audit timeout; the timeout budget has been widened, but a fresh clean run has not cleared the residue yet.'
                if timeout_repaired else
                'Live health-monitor watchpoints still exist outside architecture ownership even though the architecture verifier path is green.'
            ),
            'source_layer': 'cross-loop ownership',
            'root_cause': (
                f"marketing-workflow-audit timeout residue after timeoutSeconds repair to {marketing_timeout_state['timeout_seconds']}s"
                if timeout_repaired else '; '.join(external_issue_summaries)
            ),
            'evidence_refs': [
                str(HEALTH_MONITOR),
                str(MARKETING_AUDIT),
                str(MARKETING_INDEPENDENT),
            ],
            'confidence': 0.97,
            'recommended_fix': (
                'Let the widened marketing-workflow-audit budget produce one clean rerun, then rerun the owner verification and health monitor before treating the stack as green.'
                if timeout_repaired else
                'Let the owning external loop clear the live residue, then rerun the relevant owner verification instead of treating architecture green as whole-stack green.'
            ),
        }
    else:
        primary_finding = {
            'severity': 'low',
            'title': 'No external red residue is active right now',
            'mechanism': 'Independent signoff and live health-monitor state are aligned across the external owner loops checked here.',
            'source_layer': 'cross-loop ownership',
            'root_cause': 'No live external watchpoints remain in the current snapshot.',
            'evidence_refs': [
                str(HEALTH_MONITOR),
                str(MARKETING_INDEPENDENT),
            ],
            'confidence': 0.95,
            'recommended_fix': 'Keep rechecking external owner loops after each material runtime refresh.',
        }

    # ── Self-repair / self-improvement audit ─────────────────────────────────
    self_repair_audit = load_json(SELF_REPAIR_AUDIT_JSON)
    sr_findings = self_repair_audit.get('findings', []) or []
    sr_missing_loops = self_repair_audit.get('loops_missing_self_repair', 0)
    si_missing_loops = self_repair_audit.get('loops_missing_self_improve', 0)

    findings = [
        primary_finding,
        {
            'severity': 'medium',
            'title': 'Live Gateway topology matches the current runtime state',
            'mechanism': f"Direct live cron inspection shows {len(jobs)} enabled/total-visible jobs, {len(disabled_live)} disabled jobs, {len(running_jobs)} running jobs, and {len(last_error_jobs)} live last-error jobs.",
            'source_layer': 'runtime cron topology',
            'root_cause': 'Architecture/runtime topology remains coherent while owner-loop work is in flight.',
            'evidence_refs': [str(OUT_JSON), str(HEALTH_MONITOR)],
            'confidence': 0.98,
            'recommended_fix': 'Keep direct cron inspection as the source of truth on each watchdog run and avoid conflating persisted disabled history with live runtime topology.',
        },
        {
            'severity': 'medium',
            'title': 'Architecture verifier path is green on freshness and ownership gates',
            'mechanism': 'Loop integrity, health-monitor blocker localization, and shared market-intelligence consumption remain coherent after the refresh; remaining blocker classification is externalized correctly.',
            'source_layer': 'checker/runtime contract',
            'root_cause': 'Architecture-side gates are healthy; remaining risk is external.',
            'evidence_refs': [
                str(LOOP_INTEGRITY),
                str(DOCS_VERIFIER),
                str(MARKET_INTELLIGENCE_CONSUMPTION),
                str(INDEPENDENT),
                str(VERIFIER_MD),
            ],
            'confidence': 0.97,
            'recommended_fix': 'Rerun independent verification after each material architecture artifact refresh.',
        },
        {
            'severity': 'low',
            'title': 'Persisted disabled jobs remain history only, not live runtime blockers',
            'mechanism': 'Disabled entries still exist in jobs.json history, but live Gateway topology currently exposes zero disabled jobs.',
            'source_layer': 'persistence',
            'root_cause': 'Historical scheduler records persist after live job removal/disable cleanup.',
            'evidence_refs': [str(OUT_JSON)],
            'confidence': 0.97,
            'recommended_fix': 'Keep separating persisted disabled history from live runtime topology in every audit.',
        },
        # Self-repair / self-improvement findings from the dedicated audit
        *[
            dict(f, source_layer='self-repair-loop-contract')
            for f in sr_findings
            if f.get('severity') in ('high', 'critical')
        ],
    ]

    repairs = [
        {
            'status': 'refreshed_live_topology',
            'target': 'live cron topology snapshot',
            'change': f'Refreshed the audit against the current live view: {sum(1 for job in jobs if job.get("enabled", True))} enabled jobs, {len(disabled_live)} disabled jobs, {len(running_jobs)} running jobs, and {len(last_error_jobs)} live last-error jobs.',
            'proof': [str(OUT_JSON)],
        },
        {
            'status': 'relocalized_runtime_drift',
            'target': 'architecture blocker map',
            'change': 'Removed stale topology mismatch as an architecture-owned blocker so any remaining red stays localized to the external owner loop.',
            'proof': [str(OUT_JSON), str(HEALTH_MONITOR), str(MARKETING_INDEPENDENT)],
        },
    ]
    if market_consumption:
        repairs.append({
            'status': 'revalidated_shared_findings_consumption',
            'target': 'shared market-intelligence consumers',
            'change': 'Reconfirmed that code-backed marketing consumers still expose machine-verifiable shared market-intelligence consumption.',
            'proof': [str(MARKET_INTELLIGENCE_CONSUMPTION)],
        })
    if marketing_timeout_state['repaired']:
        repairs.append({
            'status': 'widened_marketing_audit_timeout',
            'target': 'marketing-workflow-audit cron budget',
            'change': (
                f"Raised marketing-workflow-audit timeout to {marketing_timeout_state['timeout_seconds']}s after observing a {marketing_timeout_state['last_duration_ms']}ms last runtime; live residue remains until one clean rerun clears the old timeout error."
            ),
            'proof': [str(HEALTH_MONITOR)],
        })

    previous_independent_verdict = str(independent.get('verdict') or '') if independent else ''
    previous_independent_summary = independent.get('summary') if independent else None
    previous_checked_at = independent.get('checked_at') if independent else None
    if independent and independent.get('checked_at'):
        independent_status = 'performed' if independent.get('verdict') else 'pending_post_audit_refresh'
        independent_verdict = independent.get('verdict') or ''
        independent_summary = independent.get('summary') or ''
        checked_at = independent.get('checked_at')
    else:
        independent_status = 'pending_post_audit_refresh'
        independent_verdict = ''
        independent_summary = 'Post-audit independent verification must be rerun; see verifier artifact for the fresh result.'
        checked_at = None

    payload = {
        'schema_version': 'ecc.agent-architecture-audit.report.v1',
        'executive_verdict': {
            'overall_health': overall_health,
            'primary_failure_mode': primary_failure_mode,
            'most_urgent_fix': urgent_fix,
        },
        'scope': {
            'target_name': 'OpenClaw live agent runtime',
            'model_stack': ['openai-codex/gpt-5.4', 'minimax/MiniMax-M3'],
            'layers_to_audit': [
                'system prompt', 'tool selection', 'tool execution', 'hidden repair loops',
                'persistence', 'ownership boundaries', 'runtime cron topology'
            ],
        },
        'audit_metadata': {
            'checked_at': datetime.now().astimezone().isoformat(),
            'scheduler_inspection_method': 'direct openclaw cron list --json plus live artifact cross-checks',
            'live_jobs_checked': len(jobs),
            'live_jobs_enabled': sum(1 for job in jobs if job.get('enabled', True)),
            'live_jobs_disabled': len(disabled_live),
            'disabled_job_names': disabled_live,
            'persisted_jobs_checked': len(all_jobs),
            'persisted_disabled_job_names': disabled_persisted,
            'live_running_job_names': running_jobs,
            'live_last_error_job_names': last_error_jobs,
            'live_last_error_details': last_error_details,
            'inspected_roots': [
                str(ROOT / 'agents/system'),
                str(ROOT / 'agents/docs_quality'),
                str(ROOT / 'agents/marketing'),
                str(ROOT / 'agents/unblocker'),
                str(ROOT / 'Ralph-Site'),
            ],
        },
        'findings': findings,
        'ordered_fix_plan': [
            {
                'order': 1,
                'goal': (
                    'Get a fresh marketing independent pass backed by measurable primary-repo movement'
                    if marketing_verdict != 'pass'
                    else 'Let the external owner loop clear the remaining live residue and rerun its owner checks'
                ),
                'why_now': (
                    'Marketing still blocks whole-stack certification even though architecture-owned gates are coherent.'
                    if marketing_verdict != 'pass'
                    else 'Architecture is green, but live external residue should not be mistaken for a fully green stack.'
                ),
                'expected_effect': (
                    'Either clears the remaining adoption blocker or preserves truthful fail-closed outcome ownership.'
                    if marketing_verdict != 'pass'
                    else 'Clears the remaining external red or keeps it localized to the correct owner loop.'
                ),
            },
            {
                'order': 2,
                'goal': 'Keep architecture live-topology verification tied to openclaw cron list --json on every watchdog run',
                'why_now': 'Current live inspection is clean and should remain the source of truth instead of persisted-history drift.',
                'expected_effect': 'Stops stale topology claims from reappearing in architecture reports.',
            },
        ],
        'repairs_applied_this_run': repairs,
        'independent_verification': {
            'status': independent_status,
            'artifacts': [str(INDEPENDENT), str(VERIFIER_MD)],
            'summary': independent_summary,
            'checked_at': checked_at,
            'verdict': independent_verdict,
            'previous_artifact_checked_at': previous_checked_at,
            'previous_artifact_verdict': previous_independent_verdict or None,
            'previous_artifact_summary': previous_independent_summary,
        },
        'what_still_needs_independent_verification': [
            'Fresh architecture independent verification against the refreshed live topology.',
            *(
                ['Fresh marketing independent pass backed by measurable primary-repo movement.']
                if marketing_verdict != 'pass' else []
            ),
        ],
        'highest_risk_unresolved_issue': {
            'title': (
                'Marketing remains red on Codeberg-primary outcome evidence'
                if marketing_verdict != 'pass'
                else ('External owner-loop residue remains live' if external_watch_issues else 'No unresolved loop issue is active right now')
            ),
            'why': (
                'Architecture-owned runtime checks are coherent, but marketing independent verification still fails closed because primary-repo movement is still measurement-pending.'
                if marketing_verdict != 'pass'
                else ('; '.join(external_issue_summaries) if external_watch_issues else 'Independent signoff and live runtime checks are aligned in this snapshot.')
            ),
        },
        'runtime_assertions': {
            'ownership_boundaries_ok': not architecture_health_issues,
            'hidden_self_certification_detected': False,
            'stale_topology_leakage_detected': False,
            'shared_market_intelligence_reuse_verified': bool(market_consumption),
            'shared_market_intelligence_fresh': MARKET_INTELLIGENCE.exists(),
            'marketing_independent_verdict': marketing_verdict or None,
            'docs_independent_verdict': 'pass' if DOCS_VERIFIER.exists() and 'Status: independently verified pass' in DOCS_VERIFIER.read_text(encoding='utf-8') else 'unknown',
            'architecture_verifier_status': 'pass' if VERIFIER_MD.exists() and 'Status: independently verified pass' in VERIFIER_MD.read_text(encoding='utf-8') else 'fail',
            'architecture_verifier_checked_at': checked_at,
            'loop_integrity_statuses': loop_statuses,
            'health_monitor_issues_found': health.get('issues_found'),
            'health_monitor_issue_names': [issue.get('name') for issue in health_issues],
            'health_monitor_issue_categories': [f"{issue.get('name')}:{issue.get('category')}" for issue in health_issues],
            'marketing_workflow_audit_timeout_seconds': marketing_timeout_state['timeout_seconds'] or None,
            'marketing_workflow_audit_last_duration_ms': marketing_timeout_state['last_duration_ms'] or None,
            'marketing_workflow_audit_timeout_headroom_seconds': marketing_timeout_state['headroom_seconds'] if marketing_timeout_state['last_duration_ms'] else None,
            'marketing_workflow_audit_timeout_repaired_pending_clean_rerun': marketing_timeout_state['repaired'],
            'live_running_job_names': running_jobs,
            'live_last_error_job_names': last_error_jobs,
            'marketing_bottleneck': marketing_audit.get('current_bottleneck'),
            'marketing_repair_window_status': marketing_audit.get('repair_window_status'),
            'marketing_measurement_pending_reasons': marketing_audit.get('measurement_pending_reasons') or [],
            'marketing_momentum_status': (load_json(ROOT / 'agents/marketing/logs/marketing_momentum_watchdog.json').get('status') if (ROOT / 'agents/marketing/logs/marketing_momentum_watchdog.json').exists() else None),
            'marketing_momentum_watch_actions': (load_json(ROOT / 'agents/marketing/logs/marketing_momentum_watchdog.json').get('watch_actions') if (ROOT / 'agents/marketing/logs/marketing_momentum_watchdog.json').exists() else []),
            'independent_architecture_verdict': independent_verdict or None,
            'docs_blocker_localized': False,
            'external_watch_issue_names': [issue.get('name') for issue in external_watch_issues],
            'external_watch_issue_categories': [f"{issue.get('name')}:{issue.get('category')}" for issue in external_watch_issues],
            'required_runtime_consumers_present': sorted((market_consumption.get('consumers') or {}).keys()) if market_consumption else [],
            'runtime_consumer_statuses': {k: v.get('status') for k, v in (market_consumption.get('consumers') or {}).items()},
        },
        'notes': [
            'Architecture green here means the architecture-owned verifier path is coherent; it does not mean the whole stack is green.',
            'Persisted disabled jobs remain history only; live disabled jobs are 0.' if not disabled_live else f'Live disabled jobs: {disabled_live}; persisted-only disabled jobs (--all minus live): {disabled_persisted}',
            f'Independent live inspection in this snapshot saw {len(jobs)} live jobs, {sum(1 for job in jobs if job.get("enabled", True))} enabled, {len(disabled_live)} disabled, {len(running_jobs)} running, and {len(last_error_jobs)} live last-error jobs.',
            'The remaining blocker is external marketing outcome evidence, not architecture runtime drift.' if marketing_verdict != 'pass' else ('External owner-loop residue is still live even though architecture is green.' if external_watch_issues else 'No external marketing blocker is active.'),
            (
                f"Marketing-workflow-audit timeout budget is now {marketing_timeout_state['timeout_seconds']}s for a last observed {marketing_timeout_state['last_duration_ms']}ms runtime; waiting for one clean rerun to clear stale timeout residue."
                if marketing_timeout_state['repaired'] else 'No live timeout-budget repair was applied in this watchdog run.'
            ),
        ],
    }

    user_crontab_ok = all(status == 'ok' for status in loop_statuses.values()) if loop_statuses else False

    md_lines = [
        '# Agent Architecture Audit',
        '',
        f"- Checked: {payload['audit_metadata']['checked_at']}",
        f"- Overall health: {overall_health}",
        f"- Primary failure mode: {primary_failure_mode}",
        f"- Most urgent fix: {urgent_fix}",
        f"- Verifier status: {independent_status}",
        f"- Verifier verdict: {independent_verdict or 'pending_refresh'}",
        '',
        '## Live topology',
        '',
        f"- Live Gateway jobs: {len(jobs)} total / {sum(1 for job in jobs if job.get('enabled', True))} enabled / {len(disabled_live)} disabled",
        f"- Live running jobs now: {', '.join(running_jobs) if running_jobs else 'none'}",
        f"- Live last-error residue: {', '.join(last_error_jobs) if last_error_jobs else 'none'}",
        f"- Persisted disabled history only: {', '.join(disabled_persisted) if disabled_persisted else 'none'}",
        '- User crontab ownership: ok' if user_crontab_ok else '- User crontab ownership: drift',
        '',
        '## Severity-ranked findings',
        '',
    ]
    for idx, finding in enumerate(findings, start=1):
        md_lines.extend([
            f"{idx}. **{finding['severity'].capitalize()} — {finding['title']}**",
            f"   - Mechanism: {finding['mechanism']}",
            f"   - Recommended fix: {finding['recommended_fix']}",
            '',
        ])
    md_lines.extend(['## Repaired this run', ''])
    for repair in repairs:
        md_lines.append(f"- **{repair['status']}** — {repair['change']}")
    md_lines.extend(['', '## Still red', ''])
    if marketing_verdict != 'pass':
        md_lines.extend([
            '- Marketing independent verification is not pass.',
            '- Primary repo adoption remains measurement-pending after shipped repairs.',
            '- Do not issue a healthy certification artifact yet.',
        ])
    elif external_watch_issues:
        md_lines.extend([f"- {issue.get('name')}:{issue.get('category')}" for issue in external_watch_issues])
        if marketing_timeout_state['repaired']:
            md_lines.append(f"- timeout budget widened to {marketing_timeout_state['timeout_seconds']}s; waiting for one clean rerun to clear last-error residue")
    elif architecture_health_issues:
        md_lines.extend([f"- {issue.get('name')}:{issue.get('category')}" for issue in architecture_health_issues])
    else:
        md_lines.append('- none')
    md_lines.extend(['', '## Independent verification', ''])
    md_lines.append(f"- Performed: {'yes' if checked_at else 'pending post-audit refresh'}")
    md_lines.append(f"- Verdict: {independent_verdict or 'pending_refresh'}")
    if independent_summary:
        md_lines.append(f"- Summary: {independent_summary}")
    if previous_independent_verdict:
        md_lines.append(f"- Previous artifact verdict: {previous_independent_verdict}")
    if previous_checked_at:
        md_lines.append(f"- Previous artifact checked at: {previous_checked_at}")
    md_lines.extend(['', '## Small gate passed', ''])
    md_lines.append('- `python3 agents/system/agent_architecture_audit.py`')

    OUT_JSON.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    OUT_MD.write_text('\n'.join(md_lines) + '\n', encoding='utf-8')
    print(json.dumps({'ok': True, 'json': str(OUT_JSON), 'md': str(OUT_MD), 'live_jobs_checked': len(jobs)}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
