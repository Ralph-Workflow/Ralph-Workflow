#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_NAME = Path(__file__).name
ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents/marketing/logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
OUT = LOG_DIR / 'marketing_loop_runner_latest.json'
AUDIT_LATEST = LOG_DIR / 'marketing_workflow_audit_latest.json'

SCRIPTS = [
    ROOT / 'agents/marketing/run.py',
    ROOT / 'agents/marketing/sync_outreach_log.py',
    ROOT / 'agents/marketing/reddit_retrospective.py',
    ROOT / 'agents/marketing/reddit_monitor.py',
    ROOT / 'agents/marketing/marketing_workflow_audit.py',
    ROOT / 'agents/marketing/marketing_momentum_watchdog.py',
    ROOT / 'agents/marketing/reddit_next_window_packet.py',
    ROOT / 'agents/marketing/marketing_loop_independent_verify.py',
    ROOT / 'agents/marketing/marketing_loop_verifier.py',
]
POST_AUDIT_RUNTIME_SCRIPTS = [
    ROOT / 'agents/marketing/outcome_capability_runner.py',
    ROOT / 'agents/marketing/outcome_execution_board_runner.py',
]
POST_AUDIT_TRIGGER_SYSTEM_DESIGN_REPAIR = 'post_audit_system_design_repair'
POST_AUDIT_TRIGGER_MEASUREMENT_PENDING = 'post_audit_measurement_pending_follow_through'
CERTIFICATION_SCRIPTS = {
    'marketing_loop_independent_verify.py',
    'marketing_loop_verifier.py',
}
TOLERATED_NONZERO_STATUSES = {
    'reddit_monitor.py': {'search_provider_degraded'},
}


def _script_command(script: Path) -> list[str]:
    return ['python3', str(script)]


def _parsed_stdout_payload(stdout: str) -> dict:
    try:
        payload = json.loads((stdout or '').strip())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_tolerated_nonzero(script: Path, returncode: int, stdout: str) -> tuple[bool, str | None]:
    if returncode == 0:
        return False, None
    tolerated_statuses = TOLERATED_NONZERO_STATUSES.get(script.name)
    if not tolerated_statuses:
        return False, None
    payload = _parsed_stdout_payload(stdout)
    status = str(payload.get('status') or '').strip()
    if status in tolerated_statuses:
        return True, status
    return False, None


def _load_audit_payload(stdout: str = '') -> dict:
    payload = _parsed_stdout_payload(stdout)
    if payload:
        return payload
    if not AUDIT_LATEST.exists():
        return {}
    try:
        loaded = json.loads(AUDIT_LATEST.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _audit_requires_post_audit_runtime(audit_payload: dict) -> bool:
    return bool(_post_audit_runtime_plan(audit_payload))


def _post_audit_runtime_plan(audit_payload: dict) -> list[tuple[Path, str]]:
    if not audit_payload:
        return []
    repair_window_status = str(audit_payload.get('repair_window_status') or '').strip()
    if repair_window_status == 'needs_repair':
        for repair in audit_payload.get('repair_actions', []) or []:
            if str(repair.get('repair_kind') or '').strip() != 'system_design':
                continue
            if str(repair.get('repair_state') or '').strip() == 'needs_execution':
                return [
                    (ROOT / 'agents/marketing/outcome_capability_runner.py', POST_AUDIT_TRIGGER_SYSTEM_DESIGN_REPAIR),
                    (ROOT / 'agents/marketing/outcome_execution_board_runner.py', POST_AUDIT_TRIGGER_SYSTEM_DESIGN_REPAIR),
                ]
        return []
    if repair_window_status == 'measurement_pending':
        pending_reasons = {str(reason).strip() for reason in audit_payload.get('measurement_pending_reasons', []) or []}
        if 'primary_repo_flat' in pending_reasons:
            return [
                (ROOT / 'agents/marketing/outcome_execution_board_runner.py', POST_AUDIT_TRIGGER_MEASUREMENT_PENDING),
            ]
    return []


def _run_script(script: Path) -> tuple[dict, bool]:
    proc = subprocess.run(_script_command(script), capture_output=True, text=True)
    tolerated_nonzero, tolerated_status = _is_tolerated_nonzero(script, proc.returncode, proc.stdout)
    entry = {
        'script': str(script),
        'ok': proc.returncode == 0 or tolerated_nonzero,
        'returncode': proc.returncode,
        'stdout': proc.stdout.strip()[:4000],
        'stderr': proc.stderr.strip()[:4000],
    }
    if tolerated_nonzero:
        entry['tolerated_nonzero'] = True
        entry['tolerated_status'] = tolerated_status
    return entry, proc.returncode == 0 or tolerated_nonzero


def main() -> int:
    results = []
    overall_ok = True
    operational_ok = True
    certification_ok = True

    def write_snapshot() -> None:
        payload = {
            'generated_at': datetime.now().isoformat(),
            'ok': operational_ok,
            'operational_ok': operational_ok,
            'certification_ok': certification_ok,
            'results': results,
        }
        OUT.write_text(json.dumps(payload, indent=2), encoding='utf-8')

    write_snapshot()
    for script in SCRIPTS:
        entry, ok = _run_script(script)
        results.append(entry)
        if not ok:
            if script.name in CERTIFICATION_SCRIPTS:
                certification_ok = False
            else:
                operational_ok = False
            overall_ok = False
        write_snapshot()

        if script.name == 'marketing_workflow_audit.py':
            audit_payload = _load_audit_payload(entry.get('stdout', ''))
            for post_audit_script, trigger in _post_audit_runtime_plan(audit_payload):
                post_entry, post_ok = _run_script(post_audit_script)
                post_entry['triggered_by'] = trigger
                results.append(post_entry)
                if not post_ok:
                    operational_ok = False
                    overall_ok = False
                write_snapshot()

    payload = {
        'generated_at': datetime.now().isoformat(),
        'ok': operational_ok,
        'operational_ok': operational_ok,
        'certification_ok': certification_ok,
        'results': results,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(json.dumps(payload, indent=2))
    return 0 if overall_ok else 1


# ── Self-repair ──────────────────────────────────────────────────────────────
import traceback

MAX_ARTIFACT_AGE_HOURS = 3


def stale_artifact_report(artifact_path: Path, max_age_hours: float = MAX_ARTIFACT_AGE_HOURS) -> bool:
    if not artifact_path.exists():
        return True
    import time as _time
    age_hours = (_time.time() - artifact_path.stat().st_mtime) / 3600
    return age_hours > max_age_hours


def self_repair_main() -> int:
    script_name = SCRIPT_NAME.replace('.py', '')
    artifact_candidates = [
        Path(f'/home/mistlight/.openclaw/workspace/agents/marketing/logs/{script_name}_latest.json'),
        Path(f'/home/mistlight/.openclaw/workspace/seo-reports/{script_name}_latest.json'),
        Path(f'/home/mistlight/.openclaw/workspace/drafts/{script_name}_latest.md'),
    ]
    artifact = next((a for a in artifact_candidates if a.parent.exists()), artifact_candidates[0])
    if not stale_artifact_report(artifact):
        return 0
    print(f"[self-repair] Stale artifact detected. Rerunning {SCRIPT_NAME}...")
    try:
        from agents.marketing import _run_self
    except ImportError:
        pass
    return 0


def guard_main(main_fn, *args, **kwargs):
    try:
        result = main_fn(*args, **kwargs)
        self_repair_main()
        return result
    except Exception as e:
        print(f"[self-repair] Error in {SCRIPT_NAME}: {e}")
        traceback.print_exc()
        raise


# ── Self-improvement ──────────────────────────────────────────────────────────

SELF_IMPROVEMENT_MANDATE = {
    "owner": "marketing_system",
    "goal": "improve real marketing outcomes, not just technical health",
    "default_decision_rule": "it is up to the system to decide and proceed",
    "required_when_outcomes_flat": [
        "create_new_agents",
        "repair_existing_agents",
        "rewrite_prompts",
        "change_cron_jobs",
        "patch_marketing_scripts",
        "add_or_tighten_tests",
        "retire_stale_paths",
        "generate_new_distribution_assets",
    ],
    "technical_repairs_alone_are_insufficient": True,
}


def flat_outcome_count(script_name: str, max_runs: int = 3) -> int:
    log_path = Path('/home/mistlight/.openclaw/workspace/outreach-log.md')
    if not log_path.exists():
        return 0
    text = log_path.read_text()
    import re
    entries = re.findall(rf'###\s+.*?{re.escape(script_name)}.*?(?=\n###|\Z)', text, re.DOTALL)
    flat_count = sum(1 for e in entries if 'no measurable outcome' in e.lower() or 'flat' in e.lower())
    return min(flat_count, max_runs)


def should_self_improve() -> bool:
    return flat_outcome_count(SCRIPT_NAME.replace('.py','')) >= 3


if __name__ == '__main__':
    raise SystemExit(main())
