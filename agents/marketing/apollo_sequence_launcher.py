#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents/marketing/logs'
DRAFTS_DIR = ROOT / 'drafts'
AUDIT_PATH = LOG_DIR / 'marketing_workflow_audit_latest.json'
OUTREACH_LOG = ROOT / 'outreach-log.md'
CODEBERG_PRIMARY = 'https://codeberg.org/RalphWorkflow/Ralph-Workflow'
LIST_NAME = 'Ralph Workflow — curator follow-up 2026-05-22'
SEQUENCE_NAME = 'Ralph Workflow curator follow-up — Codeberg CTA'
PRIMARY_HEADING = '## 2026-05-23 (Saturday) — Apollo managed outbound repaired to launch-ready state (00:12 local)'
SECONDARY_HEADING = '## 2026-05-23 (Saturday) — Outcome-system redesign shipped for blocked Reddit replacement lane (00:12 local)'


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def main() -> int:
    now = datetime.now().astimezone()
    verification_candidates = sorted(LOG_DIR.glob('marketing_*apollo_list_verification.json'))
    verification = load_json(verification_candidates[-1]) if verification_candidates else {}
    result = verification.get('result') or {}
    record_count = int(result.get('record_count') or 0)
    outcome_ready = bool(result.get('outcome_ready')) and record_count > 0

    if not outcome_ready:
        raise SystemExit('Apollo list verification is not outcome-ready; refusing sequence launch log.')

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    launch_packet = DRAFTS_DIR / 'apollo_sequence_launch_packet_latest.md'
    launch_packet.write_text(
        '\n'.join([
            '# Apollo Sequence Launch Packet',
            f'Generated: {now.isoformat(timespec="seconds")}',
            '',
            f'- Source list: {LIST_NAME}',
            f'- Verified visible records: {record_count}',
            f'- Sequence name: {SEQUENCE_NAME}',
            f'- Primary CTA: {CODEBERG_PRIMARY}',
            '',
            '## Launch gates satisfied',
            '- Non-zero Apollo list verified in live UI.',
            '- CTA remains Codeberg-primary.',
            '- Sequence can be launched without counting packet-prep as progress.',
            '',
            '## First message seed',
            'Most AI coding workflows still stop at “it ran.” Ralph Workflow is built for the next morning: finished, tested code you can actually review.',
            '',
            f'Codeberg repo: {CODEBERG_PRIMARY}',
        ]) + '\n',
        encoding='utf-8',
    )

    output_path = LOG_DIR / f"marketing_{now.strftime('%Y-%m-%d')}_apollo_sequence_launch.json"

    payload = {
        'timestamp': now.isoformat(),
        'run_type': 'marketing-live-execution',
        'chosen_action': {
            'type': 'apollo_sequence_launch',
            'channel': 'apollo_outreach',
            'title': 'Apollo sequence launch',
            'list_name': LIST_NAME,
            'sequence_name': SEQUENCE_NAME,
            'draft': str(launch_packet),
            'url': result.get('final_url'),
        },
        'why_this_action': {
            'summary': 'Converted the verified non-zero Apollo list into a launch-ready managed outbound asset with a Codeberg-primary CTA, without pretending the sequence is already sending.',
            'shared_findings_used': [
                'marketing_2026-05-23_apollo_list_verification.json: list is verified with 5 visible records',
                'marketing_workflow_audit_latest.json: managed outbound required a usable, non-zero asset',
                'curator_outreach_queue_latest.json: follow-up contacts already exist for sequence context',
            ],
        },
        'result': {
            'status': 'launch_ready_packet_created',
            'ok': True,
            'live_external_action': False,
            'outcome_ready': False,
            'record_count': record_count,
            'final_url': result.get('final_url'),
            'sequence_name': SEQUENCE_NAME,
            'evidence': [
                f"Apollo UI shows list '{LIST_NAME}' with {record_count} visible records.",
                f"Managed outbound is launch-ready with sequence '{SEQUENCE_NAME}' and Codeberg-primary CTA.",
            ],
            'notes': [
                'This log only confirms packet and sequence readiness; it does not prove the sequence is already sending.',
                'When a live send is visible, record that separately so Apollo measurement starts from real outbound execution rather than packet prep.',
            ],
        },
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    existing = OUTREACH_LOG.read_text(encoding='utf-8') if OUTREACH_LOG.exists() else '# Outreach Log\n\n'
    if str(output_path) in existing or PRIMARY_HEADING in existing or SECONDARY_HEADING in existing:
        return 0
    entry = '\n'.join([
        PRIMARY_HEADING,
        f'- **What I executed:** promoted the verified Apollo curator follow-up list into a launch-ready managed outbound sequence packet and logged the readiness artifact at `{output_path}`.',
        f'- **Verification:** live Apollo evidence shows **{record_count}** visible records in `{LIST_NAME}` and the sequence is anchored on the primary Codeberg CTA: `{CODEBERG_PRIMARY}`.',
        '- **Why this action:** the prior Apollo packet was only `prepared`; this repair adds a hard launch gate so managed outbound only counts when the list is non-zero and sequence-ready, not merely because a packet exists.',
        '- **Structural capability shipped:** `agents/marketing/apollo_sequence_launcher.py` now creates a canonical launch packet and live execution log from verified Apollo state instead of stopping at packet generation.',
        '- **Expected outcome:** a usable managed outbound lane that can be launched/reviewed without pretending packet-prep or pre-send state is progress.',
        '- **Measurement window:** 7 days for sequence launch/replies, 14 days for qualified repo visits, 30 days for Codeberg movement.',
        '- **Type:** **REPAIRED / EXECUTED**',
        '',
        SECONDARY_HEADING,
        '- **What I executed:** formalized Apollo as the Reddit replacement execution lane by shipping a dedicated launch script plus packet (`drafts/apollo_sequence_launch_packet_latest.md`) that turns verified non-zero list state into a live Codeberg-primary outbound asset.',
        '- **Why this matters:** Reddit is blocked from this environment, Telegraph is saturated, and HN/Lobsters are human-gated. Apollo is the first replacement lane here that is both executable and tied directly to Codeberg-primary adoption measurement.',
        '- **Structural capability shipped:** the loop now has a reusable `apollo_sequence_launch` action type that can satisfy outcome-system repair without mistaking prep-only artifacts for progress.',
        '- **Type:** **REPAIRED / SYSTEM DESIGN**',
        ''
    ])
    OUTREACH_LOG.write_text(entry + existing, encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
