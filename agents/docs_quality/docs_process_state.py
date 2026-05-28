#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path('/home/mistlight/.openclaw/workspace')
STATE_PATH = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_process_state.json'
SIGNOFF_PATH = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_independent_stop_approval.json'
DOC_STATE_PATHS = [
    Path('/home/mistlight/RalphWithReviewer/README.md'),
    Path('/home/mistlight/RalphWithReviewer/ralph-workflow/README.md'),
    WORKSPACE / 'repos' / 'Ralph-Workflow' / 'github-mirror' / 'README.md',
    WORKSPACE / 'repos' / 'Ralph-Workflow' / 'github-mirror' / 'START_HERE.md',
    WORKSPACE / 'repos' / 'Ralph-Workflow' / 'github-mirror' / 'docs',
    WORKSPACE / 'repos' / 'Ralph-Workflow' / 'github-mirror' / 'ralph-workflow' / 'docs' / 'sphinx',
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_now_stamp() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def docs_state_fingerprint() -> str:
    sha = hashlib.sha256()
    for root in DOC_STATE_PATHS:
        files = sorted(p for p in root.rglob('*') if p.is_file()) if root.is_dir() else [root]
        for path in files:
            sha.update(str(path).encode('utf-8'))
            try:
                sha.update(path.read_bytes())
            except FileNotFoundError:
                sha.update(b'__missing__')
    return sha.hexdigest()[:16]


def default_state() -> dict:
    return {
        'version': 1,
        'incidentOpen': False,
        'repairContinuationRequired': False,
        'pendingIndependentStop': False,
        'escalationRequired': False,
        'currentIncidentId': None,
        'consecutiveVerifierFailures': 0,
        'totalVerifierFailures': 0,
        'lastFailureAtUtc': None,
        'lastFailureReason': None,
        'lastFailureDocsStateFingerprint': None,
        'lastHealthyAtUtc': None,
        'lastHealthyDocsStateFingerprint': None,
        'lastIndependentStopApprovedAtUtc': None,
        'lastIndependentStopApprovedFingerprint': None,
        'lastIndependentStopApprovedBy': None,
    }


def load_state() -> dict:
    try:
        raw = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    except Exception:
        raw = {}
    state = default_state()
    state.update(raw if isinstance(raw, dict) else {})
    return state


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')


def record_failure(state: dict, *, reason: str, docs_fingerprint: str) -> dict:
    next_state = dict(state)
    if not next_state.get('currentIncidentId'):
        next_state['currentIncidentId'] = f'ralph-docs-incident-{utc_now_stamp()}'
    next_state['incidentOpen'] = True
    next_state['repairContinuationRequired'] = True
    next_state['pendingIndependentStop'] = False
    next_state['consecutiveVerifierFailures'] = int(next_state.get('consecutiveVerifierFailures') or 0) + 1
    next_state['totalVerifierFailures'] = int(next_state.get('totalVerifierFailures') or 0) + 1
    next_state['lastFailureAtUtc'] = utc_now_iso()
    next_state['lastFailureReason'] = reason
    next_state['lastFailureDocsStateFingerprint'] = docs_fingerprint
    next_state['escalationRequired'] = next_state['consecutiveVerifierFailures'] >= 3
    return next_state


def clear_incident(state: dict, *, docs_fingerprint: str, signoff: dict) -> dict:
    next_state = dict(state)
    next_state['incidentOpen'] = False
    next_state['repairContinuationRequired'] = False
    next_state['pendingIndependentStop'] = False
    next_state['escalationRequired'] = False
    next_state['currentIncidentId'] = None
    next_state['consecutiveVerifierFailures'] = 0
    next_state['lastHealthyAtUtc'] = utc_now_iso()
    next_state['lastHealthyDocsStateFingerprint'] = docs_fingerprint
    next_state['lastIndependentStopApprovedAtUtc'] = signoff.get('checkedAtUtc') or utc_now_iso()
    next_state['lastIndependentStopApprovedFingerprint'] = docs_fingerprint
    next_state['lastIndependentStopApprovedBy'] = signoff.get('signedBy') or signoff.get('signer')
    return next_state


def load_signoff() -> dict | None:
    try:
        payload = json.loads(SIGNOFF_PATH.read_text(encoding='utf-8'))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def independent_stop_approved(state: dict, *, docs_fingerprint: str) -> tuple[bool, str, dict | None]:
    signoff = load_signoff()
    if not signoff:
        return False, f'missing signoff artifact at {SIGNOFF_PATH}', None
    approved = signoff.get('approvedToStopRepair')
    if approved is None:
        approved = signoff.get('approvedToDeactivate')
    if approved is not True:
        return False, 'signoff artifact does not approve stopping repair', signoff
    if signoff.get('docsStateFingerprint') != docs_fingerprint:
        return False, 'signoff fingerprint does not match current docs state', signoff
    incident_id = state.get('currentIncidentId')
    if incident_id and signoff.get('incidentId') not in (None, incident_id):
        return False, 'signoff incident id does not match the active repair incident', signoff
    if signoff.get('repeatFailureCleared') is not True:
        return False, 'signoff does not confirm repeat-failure clearance', signoff
    return True, 'independent stop approval valid', signoff
