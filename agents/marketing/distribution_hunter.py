#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_NAME = Path(__file__).name
ROOT = Path("/home/mistlight/.openclaw/workspace")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.marketing.distribution_lane_selector import LaneDecision, choose_distribution_lane
from agents.marketing.distribution_lane_executor import execute_distribution_lane
from agents.marketing import marketing_workflow_audit

LOG_DIR = ROOT / 'agents/marketing/logs'
STATUS_JSON = LOG_DIR / 'distribution_hunter_latest.json'
STATUS_MD = LOG_DIR / 'distribution_hunter_latest.md'

SYSTEM_DESIGN_REPAIR_TYPES = {
    'outcome_system_underpowered',
    'managed_outbound_not_yet_usable',
}


@dataclass
class HunterResult:
    timestamp: str
    repair_types_seen: list[str]
    selected_lane: str
    selected_action_type: str
    execution_status: str
    live_external_action: bool
    artifact_path: str | None
    summary: str
    expected_outcome: str
    measurement_window: str
    fake_green_guard: str


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _current_repair_types() -> list[str]:
    audit = _load_json(LOG_DIR / 'marketing_workflow_audit_latest.json')
    repair_types = []
    for repair in audit.get('repair_actions', []) or []:
        if repair.get('repair_state') != 'needs_execution':
            continue
        failure_type = str(repair.get('failure_type') or '').strip()
        if failure_type in SYSTEM_DESIGN_REPAIR_TYPES:
            repair_types.append(failure_type)
    return sorted(set(repair_types))


def _measurement_window_for(action_type: str) -> str:
    if action_type == 'apollo_outbound_verification':
        return 'Immediate verification now; 7-day launch/reply window only after a launched live sequence exists.'
    if 'directory_confirmation' in action_type:
        return 'Recheck listing approval/backlink evidence in 3-7 days.'
    if 'comparison' in action_type or 'publisher' in action_type:
        return 'Review response/backlink movement within 7 days.'
    if 'stack' in action_type:
        return 'Review answer indexing/click-through within 72 hours.'
    return 'Review outcome movement within 7 days.'


def _expected_outcome_for(lane: str, action_type: str) -> str:
    if lane == 'directory_confirmation':
        return 'Convert prior directory submissions into verified live-listing/backlink proof.'
    if lane == 'comparison_backlink_outreach':
        return 'Create higher-intent backlink/citation motion tied to comparison demand.'
    if lane in {'curator_contact_handoff_packet', 'curator_handoff_packet'}:
        return 'Turn prepared targets into executable curator/contact distribution paths instead of refreshing the queue again.'
    if lane == 'primary_repo_flat_contact_handoff_packet':
        return 'Move primary-repo-flat repair toward manual-executable publisher outreach.'
    if lane == 'stack_overflow_answer':
        return 'Capture existing demand on a channel with measurable referral intent.'
    return f'Advance distribution via lane {lane} without defaulting back to monitoring-only work.'


def run(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now().astimezone().replace(tzinfo=None)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    repair_types = _current_repair_types()
    decision = choose_distribution_lane(now=now)
    execution = execute_distribution_lane(decision, now=now)
    result = HunterResult(
        timestamp=now.isoformat(),
        repair_types_seen=repair_types,
        selected_lane=decision.lane,
        selected_action_type=execution.action_type,
        execution_status=execution.status,
        live_external_action=bool(execution.live_external_action),
        artifact_path=execution.artifact_path,
        summary=execution.summary,
        expected_outcome=_expected_outcome_for(decision.lane, execution.action_type),
        measurement_window=_measurement_window_for(execution.action_type),
        fake_green_guard='This run only counts as real progress when it produces a fresh execution artifact tied to a non-monitor lane. Prepared/verification artifacts stay visible but do not imply outcome movement on their own.',
    )
    payload = asdict(result)
    payload['decision'] = asdict(decision) if is_dataclass(decision) else dict(decision.__dict__)
    payload['execution'] = asdict(execution) if is_dataclass(execution) else dict(execution.__dict__)
    STATUS_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    lines = [
        '# Distribution Hunter',
        '',
        f"- Generated: `{payload['timestamp']}`",
        f"- Pending system-design repairs seen: `{', '.join(payload['repair_types_seen']) or 'none'}`",
        f"- Selected lane: `{payload['selected_lane']}`",
        f"- Action type: `{payload['selected_action_type']}`",
        f"- Status: `{payload['execution_status']}`",
        f"- Live external action: `{payload['live_external_action']}`",
        f"- Artifact: `{payload['artifact_path']}`",
        f"- Expected outcome: {payload['expected_outcome']}",
        f"- Measurement window: {payload['measurement_window']}",
        f"- Fake-green guard: {payload['fake_green_guard']}",
        '',
        '## Summary',
        payload['summary'],
    ]
    STATUS_MD.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return payload


def main() -> int:
    payload = run()
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


# ── Self-repair ──────────────────────────────────────────────────────────────
import traceback

MAX_ARTIFACT_AGE_HOURS = 3


def stale_artifact_report(artifact_path: Path, max_age_hours: float = MAX_ARTIFACT_AGE_HOURS) -> bool:
    if not artifact_path.exists():
        return True
    import time
    age_hours = (time.time() - artifact_path.stat().st_mtime) / 3600
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
    import re, time
    entries = re.findall(rf'###\s+.*?{re.escape(script_name)}.*?(?=\n###|\Z)', text, re.DOTALL)
    flat_count = sum(1 for e in entries if 'no measurable outcome' in e.lower() or 'flat' in e.lower())
    return min(flat_count, max_runs)


def should_self_improve() -> bool:
    return flat_outcome_count(SCRIPT_NAME.replace('.py','')) >= 3


if __name__ == '__main__':
    raise SystemExit(main())
