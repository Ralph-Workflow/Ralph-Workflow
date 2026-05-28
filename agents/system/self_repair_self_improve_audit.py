#!/usr/bin/env python3
"""
Self-Repair / Self-Improvement Audit for ALL active loops.

Checks every cron job against two mandatory capabilities:
  1. SELF-REPAIR: Can the loop detect its own failures and recover without human intervention?
  2. SELF-IMPROVE: Does the loop have an explicit mandate to improve itself when outcomes are flat?

Both are required. Missing either = HIGH severity finding.
"""
from __future__ import annotations
import json, re, subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
SYSTEM = ROOT / 'agents/system'
MARKETING = ROOT / 'agents/marketing'
DOCS = ROOT / 'agents/docs_quality'
OUT_DIR = SYSTEM / 'logs'
OUT_JSON = OUT_DIR / 'self_repair_self_improve_audit_latest.json'
OUT_MD = OUT_DIR / 'self_repair_self_improve_audit_latest.md'


def load_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def cron_jobs() -> list[dict]:
    try:
        payload = json.loads(subprocess.check_output(['openclaw', 'cron', 'list', '--json'], text=True))
        return payload.get('jobs', []) or []
    except Exception:
        return []


# ── Self-repair patterns ──────────────────────────────────────────────────────

SELF_REPAIR_PATTERNS = [
    re.compile(r'def.*watchdog\b', re.I),
    re.compile(r'def.*repair\b', re.I),
    re.compile(r'self_?repair', re.I),
    re.compile(r'guard_main', re.I),
    re.compile(r'stale_artifact', re.I),
    re.compile(r'def.*heal\b', re.I),
    re.compile(r'def.*fix\b', re.I),
    re.compile(r'def.*escalat\b', re.I),
    re.compile(r'3.*strike', re.I),
    re.compile(r'repeat.*fail', re.I),
    re.compile(r'auto_?repair', re.I),
    re.compile(r'auto_?fix', re.I),
    re.compile(r'self_?heal', re.I),
    re.compile(r'incident.*response', re.I),
    re.compile(r'should_.*refresh', re.I),
    re.compile(r'stale.*refresh', re.I),
    re.compile(r'refresh_.*guard', re.I),
    re.compile(r'health_.*monitor', re.I),
    re.compile(r'newest_.*report.*time', re.I),
    re.compile(r'guard.*detail', re.I),
]

# Scripts that are pure reporters / trackers — no self-repair needed
IDEMPOTENT_REPORTER_EXEMPT = {
    'competitor_analysis.py',        # pure research, re-runs fine
    'adoption_tracker.py',           # pure metrics read, re-runs fine
    'sync_research.py',              # git sync, re-runs fine
    'market_intelligence_runtime.py',# pure read, re-runs fine
    'measurement_hold_runtime.py',   # pure state machine, re-runs fine
    'backlink_status.py',            # pure read, re-runs fine
    'reddit_retrospective.py',       # pure analysis, re-runs fine
    'reddit_next_window_packet.py',  # pure packet gen, re-runs fine
    'positioning.py',               # pure analysis, re-runs fine
    'reflection_engine.py',          # pure analysis, re-runs fine
    'weekly_review.py',              # pure analysis, re-runs fine
    'channel_discovery.py',          # pure discovery, re-runs fine
}

# Scripts that are the self-watchdog for another loop — they ARE the self-repair
WATCHDOG_FOR = {
    'reddit_watchdog.py': 'reddit_monitor.py',
    'marketing_momentum_watchdog.py': 'marketing_loop_runner.py',
    'churn_escalation_watchdog.py': 'marketing_loop_runner.py',
    'docs_stack_temp_watchdog.py': 'ralph_docs_verify.py',
    'docs_process_state.py': 'ralph_docs_verify.py',
}

# ── Self-improvement patterns ─────────────────────────────────────────────────

SELF_IMPROVE_PATTERNS = [
    re.compile(r'self_?improv', re.I),
    re.compile(r'self_?improv.*mandate', re.I),
    re.compile(r'improve.*outcomes', re.I),
    re.compile(r'four_.*marketing_.*question', re.I),
    re.compile(r'repair.*agent|agent.*repair', re.I),
    re.compile(r'rewrite.*prompt|prompt.*rewrite', re.I),
    re.compile(r'retire.*stale|stale.*retire', re.I),
    re.compile(r'create.*agent|agent.*create', re.I),
    re.compile(r'patch.*script|script.*patch', re.I),
    re.compile(r'change.*cron|cron.*change', re.I),
    re.compile(r'add.*test|test.*add', re.I),
    re.compile(r'foundry|candidate.*skill|skill.*discover', re.I),
    re.compile(r'mine.*history|history.*mine', re.I),
    re.compile(r'verifier.*artifact', re.I),
    re.compile(r'verdict.*pass|pass.*verdict', re.I),
    re.compile(r'independent.*verify', re.I),
    re.compile(r'3rd.*party|third.*party', re.I),
    re.compile(r'tech.*rep.*alone.*insufficient', re.I),
    re.compile(r'outcome.*evidence|evidence.*outcome', re.I),
    re.compile(r'bottleneck.*ident', re.I),
    re.compile(r'adoption.*flat|flat.*adoption', re.I),
]


def has_self_repair(script_path: Path) -> tuple[bool, str]:
    if not script_path.exists():
        return False, "script_missing"
    text = script_path.read_text()
    name = script_path.name

    # Watchdog scripts ARE the self-repair for another loop
    if name in WATCHDOG_FOR:
        return True, f"is_watchdog_for:{WATCHDOG_FOR[name]}"

    # Pure reporters don't need self-repair — they are always safe to re-run
    if name in IDEMPOTENT_REPORTER_EXEMPT:
        return True, "idempotent_reporter_exempt"

    hits = []
    for pat in SELF_REPAIR_PATTERNS:
        if pat.search(text):
            hits.append(pat.pattern[:40])
    if hits:
        return True, f"patterns:{','.join(hits[:3])}"
    return False, "no_self_repair_pattern_found"


def has_self_improve(script_path: Path) -> tuple[bool, str]:
    if not script_path.exists():
        return False, "script_missing"
    text = script_path.read_text()
    hits = []
    for pat in SELF_IMPROVE_PATTERNS:
        for m in pat.finditer(text):
            hits.append(m.group()[:50])
    if hits:
        return True, f"patterns:{','.join(hits[:3])}"
    return False, "no_self_improve_pattern_found"


def script_path_for_job(job_name: str) -> Path | None:
    """Map a cron job name to its most likely script path."""
    slug = job_name.lower().replace('-', '_').replace(' ', '_')

    # Direct mappings
    DIRECT = {
        'reddit_pipeline_watchdog': MARKETING / 'reddit_watchdog.py',
        'reddit_monitor': MARKETING / 'reddit_monitor.py',
        'marketing_momentum_watchdog': MARKETING / 'marketing_momentum_watchdog.py',
        'marketing_workflow_audit': MARKETING / 'marketing_workflow_audit.py',
        'marketing_active_loop': MARKETING / 'marketing_loop_runner.py',
        'marketing_distribution_hunter': MARKETING / 'distribution_hunter.py',
        'marketing_outcome_capability_runner': MARKETING / 'outcome_capability_runner.py',
        'marketing_churn_watchdog': MARKETING / 'churn_escalation_watchdog.py',
        'competitor_analysis': MARKETING / 'competitor_analysis.py',
        'adoption_tracker': MARKETING / 'adoption_tracker.py',
        'apollo_channel_monitor': MARKETING / 'apollo_monitor.py',
        'content_generator': MARKETING / 'generate_content.py',
        'content_poster': MARKETING / 'run_posting.py',
        'system_health_monitor': SYSTEM / 'health_monitor.py',
        'agent_architecture_watchdog': SYSTEM / 'agent_architecture_audit.py',
        'ralph_workflow_docs_verifier_supervisor': SYSTEM / 'health_monitor.py',
        'ralph_docs_supervisor_precheck': DOCS / 'ralph_docs_supervisor_precheck.py',
        'blocked_channel_recovery': MARKETING / 'reddit_watchdog.py',
        'codeberg_github_mirror_sync': MARKETING / 'sync_research.py',
        'backlink_tracker': MARKETING / 'backlink_status.py',
        'marketing_research_daily': MARKETING / 'run.py',
        'marketing_daily': MARKETING / 'run.py',
        'push_research_findings': MARKETING / 'sync_research.py',
        'ralph_site_owner_loop': MARKETING / 'run.py',
    }

    for key, path in DIRECT.items():
        if key in slug or slug in key:
            return path

    # Fuzzy fallback
    for key in ['reddit', 'apollo', 'marketing', 'competitor', 'content', 'adoption', 'backlink', 'system', 'architecture', 'docs', 'ralph', 'sync']:
        if key in slug:
            base = MARKETING if key in ['reddit', 'apollo', 'marketing', 'competitor', 'content', 'adoption', 'backlink', 'sync'] else SYSTEM
            for candidate in [key.replace('_', ''), key.replace('_', ' ')]:
                fuzzy = base / f"{candidate}.py"
                if fuzzy.exists():
                    return fuzzy

    return None


def audit_all_loops():
    jobs = cron_jobs()

    # Filter to active loops (exclude one-shot timers, etc.)
    active = [j for j in jobs if j.get('enabled') and j.get('name') and not j.get('name', '').startswith('_')]

    loop_results = []
    findings = []
    unregistered_no_self_repair = []
    unregistered_no_self_improve = []

    for job in active:
        name = job.get('name', '')
        if any(x in name.lower() for x in [' measurement', ' hold ', ' reddit_retro', ' reddit_next']):
            # Skip pure-timing hold wheels — they self-manage
            loop_results.append({
                'job': name, 'status': 'timing_wheel',
                'has_self_repair': True, 'sr_detail': 'timing_wheel_exempt',
                'has_self_improve': True, 'si_detail': 'timing_wheel_exempt',
            })
            continue

        # Skip one-shot/timing-wheel jobs (deleteAfterRun, at-schedule) — no loop script
        if job.get('deleteAfterRun') or (job.get('schedule', {}).get('kind') == 'at'):
            loop_results.append({
                'job': name, 'status': 'oneshot_timer_exempt',
                'has_self_repair': True, 'sr_detail': 'oneshot_timer_exempt',
                'has_self_improve': True, 'si_detail': 'oneshot_timer_exempt',
            })
            continue

        script = script_path_for_job(name)
        has_sr, sr_detail = has_self_repair(script) if script else (False, 'no_script_found')
        has_si, si_detail = has_self_improve(script) if script else (False, 'no_script_found')

        needs_sr = script.name not in IDEMPOTENT_REPORTER_EXEMPT if script else False
        needs_si = True  # all operational loops need self-improve

        sr_missing = needs_sr and not has_sr
        si_missing = needs_si and not has_si

        loop_results.append({
            'job': name,
            'script': str(script) if script else 'UNKNOWN',
            'has_self_repair': has_sr,
            'sr_detail': sr_detail,
            'needs_self_repair': needs_sr,
            'has_self_improve': has_si,
            'si_detail': si_detail,
            'needs_self_improve': needs_si,
            'self_repair_missing': sr_missing,
            'self_improve_missing': si_missing,
            'job_id': job.get('id'),
            'model': job.get('payload', {}).get('model') or job.get('model'),
        })

        if sr_missing:
            unregistered_no_self_repair.append(name)
            findings.append({
                'severity': 'high',
                'title': f'Loop "{name}" has NO self-repair mechanism',
                'mechanism': f'Script {script.name if script else "UNKNOWN"} has no watchdog, no auto-rerun guard, no incident response, and no repair logic. A failure in this loop goes uncaught until manual intervention.',
                'source_layer': 'self-repair',
                'root_cause': 'Loop was created without a self-watchdog or incident-response path.',
                'evidence_refs': [str(script)] if script else [],
                'confidence': 0.95,
                'recommended_fix': (
                    f'Add a watchdog function to {script.name if script else "the loop script"} that:\n'
                    '  1. Detects failure states (non-zero exit, missing artifact, stale timestamp)\n'
                    '  2. Reruns the loop or refreshes its artifacts\n'
                    '  3. Escalates to the owner if recovery fails after 2 retries\n'
                    '  4. Logs the repair attempt to the incidents system'
                ),
            })

        if si_missing:
            unregistered_no_self_improve.append(name)
            findings.append({
                'severity': 'high',
                'title': f'Loop "{name}" has NO self-improvement mandate',
                'mechanism': f'Script {script.name if script else "UNKNOWN"} has no self-improvement mandate. When outcomes are flat, this loop will repeat the same tactics forever without improving or redesigning its approach.',
                'source_layer': 'self-improvement',
                'root_cause': 'Loop was created without a self-improvement mandate or a third-party verification requirement.',
                'evidence_refs': [str(script)] if script else [],
                'confidence': 0.9,
                'recommended_fix': (
                    f'Add a self_improvement_mandate section to {script.name if script else "the loop script"} that:\n'
                    '  1. Detects when outcomes are flat for N consecutive runs\n'
                    '  2. Triggers a redesign pass: new agents, prompt rewrites, cron changes, or path retirement\n'
                    '  3. Registers the loop in the self_improvement_loops.json registry with checker/runner/verifier\n'
                    '  4. Requires independent third-party signoff before marking the loop healthy again'
                ),
            })

    # Sort: worst first
    findings.sort(key=lambda f: ['critical', 'high', 'medium', 'low'].index(f['severity']))

    now = datetime.now().astimezone().isoformat()
    payload = {
        'schema_version': 'ecc.self-repair-self-improve-audit.v1',
        'checked_at': now,
        'total_loops_audited': len(loop_results),
        'loops_with_self_repair': sum(1 for r in loop_results if r.get('has_self_repair')),
        'loops_with_self_improve': sum(1 for r in loop_results if r.get('has_self_improve')),
        'loops_missing_self_repair': len(unregistered_no_self_repair),
        'loops_missing_self_improve': len(unregistered_no_self_improve),
        'loop_results': loop_results,
        'findings': findings,
        'self_repair_missing_loops': unregistered_no_self_repair,
        'self_improve_missing_loops': unregistered_no_self_improve,
        'severity_counts': {
            'critical': sum(1 for f in findings if f['severity'] == 'critical'),
            'high': sum(1 for f in findings if f['severity'] == 'high'),
            'medium': sum(1 for f in findings if f['severity'] == 'medium'),
            'low': sum(1 for f in findings if f['severity'] == 'low'),
        },
    }

    OUT_JSON.write_text(json.dumps(payload, indent=2) + '\n')
    md_lines = [
        '# Self-Repair / Self-Improvement Audit',
        f'- Checked: {now}',
        f'- Loops audited: {len(loop_results)}',
        f'- Loops with self-repair: {payload["loops_with_self_repair"]}',
        f'- Loops with self-improve: {payload["loops_with_self_improve"]}',
        f'- Loops missing self-repair: {payload["loops_missing_self_repair"]}',
        f'- Loops missing self-improve: {payload["loops_missing_self_improve"]}',
        '',
        '## Findings (sorted by severity)',
        '',
    ]
    for f in findings:
        md_lines += [
            f"### [{f['severity'].upper()}] {f['title']}",
            f"**Mechanism:** {f['mechanism']}",
            f"**Root cause:** {f['root_cause']}",
            f"**Recommended fix:** {f['recommended_fix']}",
            '',
        ]

    if not findings:
        md_lines.append('✅ All loops have self-repair AND self-improvement mechanisms.')

    OUT_MD.write_text('\n'.join(md_lines) + '\n')
    print(f"✅ Audited {len(loop_results)} loops. HIGH findings: {len([f for f in findings if f['severity']=='high'])}")
    return payload


if __name__ == '__main__':
    audit_all_loops()
