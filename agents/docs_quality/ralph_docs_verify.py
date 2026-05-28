#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from docs_process_state import (
    clear_incident,
    docs_state_fingerprint,
    independent_stop_approved,
    load_state,
    record_failure,
    save_state,
)

WORKSPACE = Path('/home/mistlight/.openclaw/workspace')
CHECKER = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_docs_check.py'
EDITORIAL = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_docs_editorial_audit.py'
AGENTIC = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_docs_agentic_review.py'
RUNNER = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_docs_runner.py'
EDITORIAL_REPORT = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_editorial_latest.md'
AGENTIC_REPORT = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_agentic_latest.md'
RUNNER_STATUS = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_latest.md'
VERIFIER_STATUS = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_verifier_latest.md'
VERIFIER_JSON = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_verifier_latest.json'
VERIFIER_HISTORY = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_verifier_history.jsonl'


def run_py(path: Path) -> tuple[int, str]:
    proc = subprocess.run(['python3', str(path)], capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def combined_check() -> tuple[int, str]:
    c_code, c_out = run_py(CHECKER)
    e_code, e_out = run_py(EDITORIAL)
    a_code, a_out = run_py(AGENTIC)
    code = 0 if c_code == 0 and e_code == 0 and a_code == 0 else 1
    out = f'CHECKER\n{c_out}\n\nEDITORIAL\n{e_out}\n\nAGENTIC\n{a_out}'
    return code, out


def write_status(status: str, pre: str, post: str, remediation_forced: bool, process_state: dict, stop_reason: str) -> None:
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    body = f'''# Ralph Docs Independent Verifier Status\n\nStatus: {status}\n\nTimestamp:\n- {now}\n\n## Pre-verification results\n```\n{pre}\n```\n\n## Remediation forced by verifier\n- {'yes' if remediation_forced else 'no'}\n\n## Final verification results\n```\n{post}\n```\n\n## Evidence artifacts\n- runner status: `{RUNNER_STATUS}`\n- editorial audit: `{EDITORIAL_REPORT}`\n- agentic review: `{AGENTIC_REPORT}`\n- verifier status: `{VERIFIER_STATUS}`\n- verifier json: `{VERIFIER_JSON}`\n'''
    VERIFIER_STATUS.write_text(body, encoding='utf-8')
    RUNNER_STATUS.write_text(
        f'''# Ralph Docs Watchdog Status\n\nStatus: {status}\n\nTimestamp:\n- {now}\n\n## Current verifier authority\n- This file was refreshed by `ralph_docs_verify.py` so the live watchdog artifact matches the latest verified state.\n- Stop reason: {stop_reason}\n\n## Process incident state\n- incident: `{process_state.get('currentIncidentId') or 'none'}`\n- incidentOpen: `{process_state.get('incidentOpen')}`\n- repairContinuationRequired: `{process_state.get('repairContinuationRequired')}`\n- pendingIndependentStop: `{process_state.get('pendingIndependentStop')}`\n- consecutiveVerifierFailures: `{process_state.get('consecutiveVerifierFailures')}`\n- escalationRequired: `{process_state.get('escalationRequired')}`\n\n## Live evidence artifacts\n- editorial audit: `{EDITORIAL_REPORT}`\n- agentic review: `{AGENTIC_REPORT}`\n- verifier status: `{VERIFIER_STATUS}`\n- verifier json: `{VERIFIER_JSON}`\n\n## Final verification results\n```\n{post}\n```\n''',
        encoding='utf-8',
    )


def write_json_and_history(payload: dict) -> None:
    VERIFIER_JSON.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    with VERIFIER_HISTORY.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(payload) + '\n')


def main() -> int:
    pre_code, pre_out = combined_check()
    remediation_forced = False
    final_code, final_out = pre_code, pre_out

    if pre_code != 0:
        remediation_forced = True
        run_py(RUNNER)
        final_code, final_out = combined_check()

    docs_fingerprint = docs_state_fingerprint()
    state = load_state()
    stop_reason = 'initial verification already passed' if final_code == 0 else 'verification checks failed'
    independent_stop_required = bool(state.get('incidentOpen')) or bool(state.get('repairContinuationRequired')) or bool(state.get('pendingIndependentStop'))
    independent_stop_present = False

    if final_code == 0:
        if independent_stop_required:
            approved, approval_reason, signoff = independent_stop_approved(state, docs_fingerprint=docs_fingerprint)
            if approved:
                state = clear_incident(state, docs_fingerprint=docs_fingerprint, signoff=signoff or {})
                stop_reason = f"independent stop approval cleared incident {signoff.get('incidentId') or 'unknown'}"
                independent_stop_present = True
            else:
                final_code = 1
                state = record_failure(
                    state,
                    reason=f'candidate pass reached but independent stop approval is still missing: {approval_reason}',
                    docs_fingerprint=docs_fingerprint,
                )
                state['pendingIndependentStop'] = True
                stop_reason = f'candidate pass reached but independent stop approval is still missing: {approval_reason}'
        else:
            healthy_state = dict(state)
            healthy_state['incidentOpen'] = False
            healthy_state['repairContinuationRequired'] = False
            healthy_state['pendingIndependentStop'] = False
            healthy_state['escalationRequired'] = False
            healthy_state['currentIncidentId'] = None
            healthy_state['consecutiveVerifierFailures'] = 0
            healthy_state['lastHealthyAtUtc'] = datetime.now(timezone.utc).isoformat()
            healthy_state['lastHealthyDocsStateFingerprint'] = docs_fingerprint
            state = healthy_state
    else:
        state = record_failure(state, reason='verification checks failed', docs_fingerprint=docs_fingerprint)
        stop_reason = 'verification checks failed'

    save_state(state)
    status = 'independently verified pass' if final_code == 0 else 'independent verifier failed signoff'
    checked_at = datetime.now(timezone.utc).isoformat()
    payload = {
        'checked_at': checked_at,
        'verdict': 'pass' if final_code == 0 else 'fail',
        'status': status,
        'ok': final_code == 0,
        'remediation_forced': remediation_forced,
        'remediation_passes_attempted': 1 if remediation_forced else 0,
        'passes': [],
        'stop_reason': stop_reason,
        'final_code': final_code,
        'repair_system_broken': final_code != 0 and remediation_forced,
        'independent_stop_required': independent_stop_required,
        'independent_stop_present': independent_stop_present,
        'process_state': state,
    }
    write_status(status, pre_out, final_out, remediation_forced, state, stop_reason)
    write_json_and_history(payload)
    print(status)
    print(final_out)
    return final_code


if __name__ == '__main__':
    raise SystemExit(main())
