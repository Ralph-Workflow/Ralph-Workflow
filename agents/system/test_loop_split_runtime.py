#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class IncidentCooldownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        self.incidents = load_module('incidents_test', ROOT / 'agents/system/incidents.py')
        self.incidents.INCIDENTS_PATH = self.tmp / 'open_incidents_latest.json'

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_owner_action_recent_for_same_signature(self) -> None:
        issue = {
            'name': 'marketing_independent_verification',
            'category': 'review_followup_required',
            'last_error': 'still red',
            'likely_cause': 'same blocker',
        }
        self.incidents.upsert_incidents([issue])
        self.incidents.record_owner_action(
            issue,
            action_type='immediate_review_followup_owner_action',
            ok=True,
            detail='ran once',
            outcome='no_progress',
        )
        recent, action = self.incidents.owner_action_recent(
            issue,
            action_type='immediate_review_followup_owner_action',
            cooldown_minutes=240,
        )
        self.assertTrue(recent)
        self.assertIsNotNone(action)
        self.assertEqual(action['issue_signature'], self.incidents.issue_signature(issue))

    def test_owner_action_not_recent_for_different_signature(self) -> None:
        issue = {
            'name': 'marketing_independent_verification',
            'category': 'review_followup_required',
            'last_error': 'old red',
            'likely_cause': 'old blocker',
        }
        self.incidents.upsert_incidents([issue])
        self.incidents.record_owner_action(
            issue,
            action_type='immediate_review_followup_owner_action',
            ok=True,
            detail='ran once',
            outcome='no_progress',
        )
        changed = dict(issue)
        changed['last_error'] = 'new red'
        recent, _ = self.incidents.owner_action_recent(
            changed,
            action_type='immediate_review_followup_owner_action',
            cooldown_minutes=240,
        )
        self.assertFalse(recent)

    def test_owner_action_not_recent_after_cooldown(self) -> None:
        issue = {
            'name': 'marketing_independent_verification',
            'category': 'review_followup_required',
            'last_error': 'still red',
            'likely_cause': 'same blocker',
        }
        old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        payload = {
            'updated_at': old_time,
            'incidents': {
                self.incidents.incident_key(issue): {
                    'key': self.incidents.incident_key(issue),
                    'name': issue['name'],
                    'category': issue['category'],
                    'first_seen': old_time,
                    'repeat_count': 3,
                    'owner_domain': 'marketing',
                    'escalation_level': 'owner',
                    'owner_actions': [
                        {
                            'at': old_time,
                            'action_type': 'immediate_review_followup_owner_action',
                            'ok': True,
                            'detail': 'ran once',
                            'outcome': 'no_progress',
                            'issue_signature': self.incidents.issue_signature(issue),
                        }
                    ],
                    'status': 'open',
                    'blocked_by': [],
                }
            },
        }
        self.incidents.INCIDENTS_PATH.write_text(json.dumps(payload), encoding='utf-8')
        recent, _ = self.incidents.owner_action_recent(
            issue,
            action_type='immediate_review_followup_owner_action',
            cooldown_minutes=240,
        )
        self.assertFalse(recent)


class DocsPrecheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        self.mod = load_module('docs_precheck_test', ROOT / 'agents/docs_quality/ralph_docs_supervisor_precheck.py')
        self.mod.VERIFIER_JSON = self.tmp / 'verifier.json'
        self.mod.VERIFIER_MD = self.tmp / 'verifier.md'
        self.mod.PROCESS_STATE = self.tmp / 'process_state.json'
        self.mod.STATE_PATH = self.tmp / 'state.json'

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def write_healthy(self) -> None:
        self.mod.VERIFIER_JSON.write_text(json.dumps({'verdict': 'pass', 'ok': True}), encoding='utf-8')
        self.mod.VERIFIER_MD.write_text('Status: independently verified pass\n', encoding='utf-8')
        self.mod.PROCESS_STATE.write_text(json.dumps({
            'incidentOpen': False,
            'repairContinuationRequired': False,
            'pendingIndependentStop': False,
            'escalationRequired': False,
            'currentIncidentId': None,
        }), encoding='utf-8')

    def test_healthy_state_skips_trigger(self) -> None:
        self.write_healthy()
        decision = self.mod.evaluate(checker_result=(0, 'ok'))
        self.assertFalse(decision.should_trigger)
        self.assertEqual(decision.reasons, [])

    def test_checker_failure_triggers(self) -> None:
        self.write_healthy()
        decision = self.mod.evaluate(checker_result=(1, 'broken'))
        self.assertTrue(decision.should_trigger)
        self.assertIn('checker_exit:1', decision.reasons)

    def test_process_state_open_triggers(self) -> None:
        self.write_healthy()
        self.mod.PROCESS_STATE.write_text(json.dumps({'incidentOpen': True}), encoding='utf-8')
        decision = self.mod.evaluate(checker_result=(0, 'ok'))
        self.assertTrue(decision.should_trigger)
        self.assertTrue(any(r.startswith('process_state:incidentOpen') for r in decision.reasons))

    def test_cooldown_blocks_repeated_same_reason(self) -> None:
        self.write_healthy()
        now_ts = 1_700_000_000.0
        decision = self.mod.evaluate(now_ts=now_ts, checker_result=(1, 'broken'))
        self.mod.save_state({'last_triggered_at': now_ts - 60, 'last_reason_signature': '|'.join(decision.reasons)})
        allowed, meta = self.mod.cooldown_allows_trigger(decision, now_ts)
        self.assertFalse(allowed)
        self.assertLess(meta['elapsed_minutes'], self.mod.TRIGGER_COOLDOWN_MINUTES)


class MarketingAuditPrecheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        self.mod = load_module('marketing_audit_precheck_test', ROOT / 'agents/marketing/marketing_workflow_audit_precheck.py')
        self.mod.AUDIT_JSON = self.tmp / 'audit.json'
        self.mod.STATE_PATH = self.tmp / 'state.json'
        self.dep1 = self.tmp / 'dep1.json'
        self.dep2 = self.tmp / 'dep2.md'
        self.mod.DEPENDENCIES = [self.dep1, self.dep2]

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_healthy_state_skips(self) -> None:
        self.mod.AUDIT_JSON.write_text(json.dumps({'status': 'ok', 'repair_window_status': 'clear'}), encoding='utf-8')
        self.dep1.write_text('{}', encoding='utf-8')
        self.dep2.write_text('ok', encoding='utf-8')
        audit_mtime = self.mod.AUDIT_JSON.stat().st_mtime
        self.dep1.touch()
        self.dep2.touch()
        os_times = (audit_mtime - 10, audit_mtime - 10)
        import os
        os.utime(self.dep1, os_times)
        os.utime(self.dep2, os_times)
        decision = self.mod.evaluate()
        self.assertFalse(decision.should_trigger)

    def test_dependency_newer_triggers(self) -> None:
        import os

        self.mod.AUDIT_JSON.write_text(json.dumps({'status': 'ok', 'repair_window_status': 'clear'}), encoding='utf-8')
        audit_mtime = self.mod.AUDIT_JSON.stat().st_mtime
        self.dep1.write_text('{}', encoding='utf-8')
        os.utime(self.dep1, (audit_mtime + 5, audit_mtime + 5))
        decision = self.mod.evaluate(now_ts=audit_mtime + 10)
        self.assertTrue(decision.should_trigger)
        self.assertTrue(any(r.startswith('dependency_newer:') for r in decision.reasons))

    def test_repair_window_triggers(self) -> None:
        self.mod.AUDIT_JSON.write_text(json.dumps({'status': 'watch', 'repair_window_status': 'needs_repair'}), encoding='utf-8')
        decision = self.mod.evaluate()
        self.assertTrue(decision.should_trigger)
        self.assertIn('repair_window:needs_repair', decision.reasons)

    def test_cooldown_blocks_repeated_same_reason(self) -> None:
        self.mod.AUDIT_JSON.write_text(json.dumps({'status': 'watch', 'repair_window_status': 'needs_repair'}), encoding='utf-8')
        now_ts = 1_700_000_000.0
        decision = self.mod.evaluate(now_ts)
        self.mod.save_state({'last_triggered_at': now_ts - 60, 'last_reason_signature': '|'.join(decision.reasons)})
        allowed, meta = self.mod.cooldown_allows_trigger(decision, now_ts)
        self.assertFalse(allowed)
        self.assertLess(meta['elapsed_minutes'], self.mod.TRIGGER_COOLDOWN_MINUTES)


if __name__ == '__main__':
    unittest.main()
