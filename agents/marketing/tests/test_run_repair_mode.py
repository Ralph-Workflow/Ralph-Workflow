import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.marketing import run
from agents.marketing.distribution_lane_selector import LaneDecision


class RunRepairModeTests(unittest.TestCase):
    def test_primary_repo_flat_does_not_advance_on_handoff_only_execution(self):
        audit = {
            'repair_window_status': 'needs_repair',
            'measurement_pending_reasons': ['primary_repo_flat'],
            'repair_actions': [
                {
                    'failure_type': 'primary_repo_flat',
                    'repair_kind': 'tactic',
                    'repair_state': 'needs_execution',
                }
            ],
        }
        execution = SimpleNamespace(action_type='curator_handoff_packet_execution', live_external_action=False)

        changed = run._advance_audit_repairs_for_execution(
            audit=audit,
            execution=execution,
            now=datetime(2026, 5, 23, 21, 0, 0),
        )

        self.assertFalse(changed)
        self.assertEqual(audit['repair_actions'][0]['repair_state'], 'needs_execution')
        self.assertEqual(audit['repair_window_status'], 'needs_repair')

    def test_primary_repo_flat_advances_on_live_external_execution(self):
        audit = {
            'repair_window_status': 'needs_repair',
            'measurement_pending_reasons': ['primary_repo_flat'],
            'repair_actions': [
                {
                    'failure_type': 'primary_repo_flat',
                    'repair_kind': 'tactic',
                    'repair_state': 'needs_execution',
                }
            ],
        }
        execution = SimpleNamespace(action_type='directory_submission_execution', live_external_action=True)

        changed = run._advance_audit_repairs_for_execution(
            audit=audit,
            execution=execution,
            now=datetime(2026, 5, 23, 21, 0, 0),
        )

        self.assertTrue(changed)
        self.assertEqual(audit['repair_actions'][0]['repair_state'], 'pending_measurement')
        self.assertEqual(audit['repair_window_status'], 'measurement_pending')

    def test_same_family_pauses_advance_when_other_lane_runs(self):
        audit = {
            'repair_window_status': 'needs_repair',
            'measurement_pending_reasons': [],
            'repair_actions': [
                {
                    'failure_type': 'same_family_distribution_overlap',
                    'repair_kind': 'tactic',
                    'repair_state': 'needs_execution',
                },
                {
                    'failure_type': 'same_family_outreach_overlap',
                    'repair_kind': 'tactic',
                    'repair_state': 'needs_execution',
                },
            ],
        }
        execution = SimpleNamespace(action_type='stackoverflow_answer_execution', live_external_action=False)

        changed = run._advance_audit_repairs_for_execution(
            audit=audit,
            execution=execution,
            now=datetime(2026, 5, 23, 21, 0, 0),
        )

        self.assertTrue(changed)
        self.assertEqual(
            [repair['repair_state'] for repair in audit['repair_actions']],
            ['pending_measurement', 'pending_measurement'],
        )
        self.assertEqual(audit['repair_window_status'], 'clear')

    def test_same_family_outreach_advances_on_manual_contact_packet_execution(self):
        audit = {
            'repair_window_status': 'needs_repair',
            'measurement_pending_reasons': [],
            'repair_actions': [
                {
                    'failure_type': 'same_family_outreach_overlap',
                    'repair_kind': 'tactic',
                    'repair_state': 'needs_execution',
                },
            ],
        }
        execution = SimpleNamespace(action_type='curator_contact_handoff_packet_execution', live_external_action=False)

        changed = run._advance_audit_repairs_for_execution(
            audit=audit,
            execution=execution,
            now=datetime(2026, 5, 23, 21, 0, 0),
        )

        self.assertTrue(changed)
        self.assertEqual(audit['repair_actions'][0]['repair_state'], 'pending_measurement')
        self.assertEqual(audit['repair_window_status'], 'clear')

    def test_load_active_pending_repairs_keeps_measurement_window_repairs_live(self):
        audit = {
            'repair_window_status': 'measurement_pending',
            'measurement_pending_reasons': ['primary_repo_flat'],
            'repair_actions': [
                {
                    'failure_type': 'primary_repo_flat',
                    'repair_kind': 'tactic',
                    'repair_state': 'pending_measurement',
                },
                {
                    'failure_type': 'same_family_distribution_overlap',
                    'repair_kind': 'tactic',
                    'repair_state': 'pending_measurement',
                },
            ],
        }

        repairs = run._load_active_pending_repairs(audit)

        self.assertEqual(len(repairs), 2)
        self.assertEqual({repair['failure_type'] for repair in repairs}, {'primary_repo_flat', 'same_family_distribution_overlap'})

    def test_apply_repair_mode_overrides_keeps_distribution_reset_choice_during_measurement_window(self):
        decision = LaneDecision(
            lane='distribution_reset',
            reason='base reason',
            reasons=['base reason'],
            owned_content_posts_last_36h=3,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/distribution_action_brief.md',
        )
        pending_repairs = [
            {
                'failure_type': 'primary_repo_flat',
                'repair_kind': 'tactic',
                'repair_state': 'pending_measurement',
                'action': 'keep pushing conversion evidence while measurement is pending',
            },
            {
                'failure_type': 'same_family_outreach_overlap',
                'repair_kind': 'tactic',
                'repair_state': 'pending_measurement',
            },
        ]

        updated = run._apply_repair_mode_overrides(decision, pending_repairs)

        self.assertEqual(updated.lane, 'distribution_reset')
        self.assertTrue(updated.skip_curator_outreach)
        self.assertEqual(updated.reason, 'base reason')

    def test_apply_repair_mode_overrides_keeps_distribution_reset_choice_during_needs_execution_window(self):
        decision = LaneDecision(
            lane='distribution_reset',
            reason='base reason',
            reasons=['base reason'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/distribution_action_brief.md',
        )
        pending_repairs = [
            {
                'failure_type': 'primary_repo_flat',
                'repair_kind': 'tactic',
                'repair_state': 'needs_execution',
                'action': 'replace stale distribution with conversion-oriented work',
            },
        ]

        updated = run._apply_repair_mode_overrides(decision, pending_repairs)

        self.assertEqual(updated.lane, 'distribution_reset')
        self.assertEqual(updated.reason, 'base reason')

    def test_latest_measurement_hold_window_detects_active_cooldown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                payload = {
                    'timestamp': '2026-05-24T04:51:00',
                    'chosen_action': {'type': 'measurement_hold_execution'},
                    'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
                    'why_this_action': {'summary': 'measurement hold is active'},
                }
                (run.LOG_DIR / 'marketing_2026-05-24_measurement_hold_execution.json').write_text(json.dumps(payload), encoding='utf-8')

                hold = run._latest_measurement_hold_window(datetime(2026, 5, 24, 5, 20, 0))

                self.assertIsNotNone(hold)
                self.assertEqual(hold['hold_started_at'], datetime(2026, 5, 24, 4, 51, 0))
                self.assertEqual(hold['hold_until'], datetime(2026, 5, 24, 5, 51, 0))
            finally:
                run.LOG_DIR = original_log_dir

    def test_latest_measurement_hold_window_uses_short_review_window_release_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                payload = {
                    'timestamp': '2026-05-25T01:47:40.177303',
                    'chosen_action': {'type': 'measurement_hold_execution'},
                    'why_this_action': {
                        'summary': 'measurement hold is active',
                        'hold_until': '2026-05-25T02:05:05',
                    },
                    'review_window': {'scheduled_run_at': '2026-05-25T02:05:05'},
                    'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
                }
                (run.LOG_DIR / 'marketing_2026-05-25_measurement_hold_execution.json').write_text(json.dumps(payload), encoding='utf-8')

                hold = run._latest_measurement_hold_window(datetime(2026, 5, 25, 1, 49, 0))

                self.assertIsNotNone(hold)
                self.assertEqual(hold['hold_started_at'], datetime(2026, 5, 25, 1, 47, 40, 177303))
                self.assertEqual(hold['hold_until'], datetime(2026, 5, 25, 2, 5, 5))
            finally:
                run.LOG_DIR = original_log_dir

    def test_latest_measurement_hold_window_falls_back_to_release_cron_schedule(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                hold_payload = {
                    'timestamp': '2026-05-25T01:47:40.177303',
                    'chosen_action': {'type': 'measurement_hold_execution'},
                    'why_this_action': {'summary': 'measurement hold is active'},
                    'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
                }
                release_payload = {
                    'timestamp': '2026-05-25T01:47:41.000000',
                    'chosen_action': {'type': 'measurement_hold_release_cron'},
                    'review_window': {'scheduled_run_at': '2026-05-25T02:05:05'},
                    'result': {'status': 'scheduled', 'ok': True, 'live_external_action': False},
                }
                (run.LOG_DIR / 'marketing_2026-05-25_measurement_hold_execution.json').write_text(json.dumps(hold_payload), encoding='utf-8')
                (run.LOG_DIR / 'marketing_2026-05-25_measurement_hold_release_cron.json').write_text(json.dumps(release_payload), encoding='utf-8')

                hold = run._latest_measurement_hold_window(datetime(2026, 5, 25, 1, 49, 0))

                self.assertIsNotNone(hold)
                self.assertEqual(hold['hold_until'], datetime(2026, 5, 25, 2, 5, 5))
            finally:
                run.LOG_DIR = original_log_dir

    def test_latest_measurement_hold_window_falls_back_to_distribution_lane_release_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                hold_payload = {
                    'timestamp': '2026-05-25T01:47:40.177303',
                    'chosen_action': {'type': 'measurement_hold_execution'},
                    'why_this_action': {'summary': 'measurement hold is active'},
                    'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
                }
                (run.LOG_DIR / 'marketing_2026-05-25_measurement_hold_execution.json').write_text(json.dumps(hold_payload), encoding='utf-8')
                (run.LOG_DIR / 'distribution_lane_latest.json').write_text(
                    json.dumps({'short_review_window_release_at': '2026-05-25T02:05:05'}),
                    encoding='utf-8',
                )

                hold = run._latest_measurement_hold_window(datetime(2026, 5, 25, 1, 49, 0))

                self.assertIsNotNone(hold)
                self.assertEqual(hold['hold_until'], datetime(2026, 5, 25, 2, 5, 5))
            finally:
                run.LOG_DIR = original_log_dir

    def test_latest_measurement_hold_window_clears_after_new_live_external_action(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                hold_payload = {
                    'timestamp': '2026-05-24T04:51:00',
                    'chosen_action': {'type': 'measurement_hold_execution'},
                    'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
                }
                live_payload = {
                    'timestamp': '2026-05-24T05:10:00',
                    'chosen_action': {'type': 'directory_submission_execution'},
                    'result': {'status': 'submitted', 'ok': True, 'live_external_action': True},
                }
                (run.LOG_DIR / 'marketing_2026-05-24_measurement_hold_execution.json').write_text(json.dumps(hold_payload), encoding='utf-8')
                (run.LOG_DIR / 'marketing_2026-05-24_directory_submission_execution.json').write_text(json.dumps(live_payload), encoding='utf-8')

                hold = run._latest_measurement_hold_window(datetime(2026, 5, 24, 5, 20, 0))

                self.assertIsNone(hold)
            finally:
                run.LOG_DIR = original_log_dir

    def test_latest_measurement_hold_window_ignores_internal_repair_logs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                hold_payload = {
                    'timestamp': '2026-05-24T04:51:00',
                    'chosen_action': {'type': 'measurement_hold_execution'},
                    'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
                }
                repair_payload = {
                    'timestamp': '2026-05-24T05:12:49',
                    'chosen_action': {'type': 'measurement_hold_cooldown_repair'},
                    'result': {'status': 'executed', 'ok': True, 'live_external_action': False},
                }
                (run.LOG_DIR / 'marketing_2026-05-24_measurement_hold_execution.json').write_text(json.dumps(hold_payload), encoding='utf-8')
                (run.LOG_DIR / 'marketing_2026-05-24_measurement_hold_cooldown_repair.json').write_text(json.dumps(repair_payload), encoding='utf-8')

                hold = run._latest_measurement_hold_window(datetime(2026, 5, 24, 5, 20, 0))

                self.assertIsNotNone(hold)
                self.assertEqual(hold['hold_started_at'], datetime(2026, 5, 24, 4, 51, 0))
            finally:
                run.LOG_DIR = original_log_dir

    def test_latest_measurement_hold_window_tolerates_string_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                hold_payload = {
                    'timestamp': '2026-05-24T08:00:00',
                    'chosen_action': {'type': 'measurement_hold_execution'},
                    'why_this_action': 'measurement hold is active',
                    'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
                }
                malformed_recent_payload = {
                    'timestamp': '2026-05-24T08:10:00',
                    'chosen_action': 'Create a comparison page',
                    'why_this_action': 'comparison page shipped',
                    'result': {'status': 'executed', 'ok': True, 'live_external_action': False},
                }
                (run.LOG_DIR / 'marketing_2026-05-24_measurement_hold_execution.json').write_text(json.dumps(hold_payload), encoding='utf-8')
                (run.LOG_DIR / 'marketing_2026-05-24_malformed_recent_log.json').write_text(json.dumps(malformed_recent_payload), encoding='utf-8')

                hold = run._latest_measurement_hold_window(datetime(2026, 5, 24, 8, 20, 0))

                self.assertIsNotNone(hold)
                self.assertEqual(hold['reason'], 'measurement hold is active')
            finally:
                run.LOG_DIR = original_log_dir

    def test_main_runs_lightweight_follow_through_during_active_hold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                hold_window = {
                    'hold_started_at': datetime(2026, 5, 24, 6, 52, 32),
                    'hold_until': datetime(2026, 5, 24, 7, 52, 32),
                    'source_log': '/tmp/marketing_2026-05-24_measurement_hold_execution.json',
                    'reason': 'Existing hold still active.',
                }
                decision = LaneDecision(
                    lane='measurement_hold',
                    reason='Hold for follow-through.',
                    reasons=['fresh external actions already shipped'],
                    owned_content_posts_last_36h=0,
                    unsubmitted_directory_channels=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    artifact_path='',
                )
                execution = SimpleNamespace(
                    action_type='measurement_hold_follow_through',
                    status='executed',
                    artifact_path='/tmp/hold.md',
                    summary='Active hold respected and follow-through surfaced.',
                    targets_prepared=['Example target'],
                    live_external_action=False,
                    blocking_factors=[],
                )

                with patch.object(run, '_latest_measurement_hold_window', return_value=hold_window), \
                     patch.object(run, 'choose_distribution_lane', return_value=decision), \
                     patch.object(run, 'execute_distribution_lane', return_value=execution):
                    rc = run.main()

                self.assertEqual(rc, 0)
                daily_log = run.LOG_DIR / 'marketing_2026-05-24.json'
                payload = json.loads(daily_log.read_text(encoding='utf-8'))
                self.assertEqual(payload['marketing_status'], 'measurement_hold')
                self.assertEqual(payload['distribution_execution']['action_type'], 'measurement_hold_follow_through')
                self.assertEqual(payload['distribution_execution']['targets_prepared'], ['Example target'])
                self.assertFalse(payload['distribution_execution']['reused_existing_follow_through'])
                self.assertIn('distribution_execution_log', payload)
            finally:
                run.LOG_DIR = original_log_dir

    def test_main_reuses_existing_follow_through_during_same_active_hold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                hold_window = {
                    'hold_started_at': datetime(2026, 5, 24, 6, 52, 32),
                    'hold_until': datetime(2026, 5, 24, 7, 52, 32),
                    'source_log': '/tmp/marketing_2026-05-24_measurement_hold_execution.json',
                    'reason': 'Existing hold still active.',
                }
                prior_log = run.LOG_DIR / 'marketing_2026-05-24_070000_measurement_hold_follow_through.json'
                prior_log.write_text(json.dumps({
                    'timestamp': '2026-05-24T07:00:00',
                    'chosen_action': {
                        'type': 'measurement_hold_follow_through',
                        'channel': 'measurement_hold',
                        'draft': '/tmp/existing_hold.md',
                    },
                    'result': {
                        'status': 'executed',
                        'summary': 'Active hold respected and follow-through surfaced.',
                        'targets_prepared': ['Existing target'],
                        'live_external_action': False,
                        'blocking_factors': [],
                    },
                }), encoding='utf-8')

                with patch.object(run, '_latest_measurement_hold_window', return_value=hold_window), \
                     patch.object(run, 'choose_distribution_lane') as choose_mock, \
                     patch.object(run, 'execute_distribution_lane') as execute_mock:
                    rc = run.main()

                self.assertEqual(rc, 0)
                choose_mock.assert_not_called()
                execute_mock.assert_not_called()
                daily_log = run.LOG_DIR / 'marketing_2026-05-24.json'
                payload = json.loads(daily_log.read_text(encoding='utf-8'))
                self.assertTrue(payload['distribution_execution']['reused_existing_follow_through'])
                self.assertEqual(payload['distribution_execution']['artifact_path'], '/tmp/existing_hold.md')
                self.assertEqual(payload['distribution_execution']['targets_prepared'], ['Existing target'])
                self.assertEqual(payload['distribution_execution_log'], str(prior_log))
            finally:
                run.LOG_DIR = original_log_dir

    def test_write_distribution_execution_log_records_short_review_window_release_for_measurement_hold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                lane = LaneDecision(
                    lane='measurement_hold',
                    reason='Hold for truthful re-entry after the short review window clears.',
                    reasons=['multiple fresh external actions already overlap'],
                    owned_content_posts_last_36h=0,
                    unsubmitted_directory_channels=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    artifact_path='',
                    short_review_window_release_at='2026-05-25T02:05:05',
                )
                execution = SimpleNamespace(
                    action_type='measurement_hold_execution',
                    status='prepared',
                    artifact_path='/tmp/hold.md',
                    summary='Enforced a short measurement hold.',
                    targets_prepared=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    live_external_action=False,
                    blocking_factors=[],
                )

                log_path = run._write_distribution_execution_log(
                    distribution_lane=lane,
                    execution=execution,
                    now=datetime(2026, 5, 25, 1, 47, 40, 177303),
                )
                payload = json.loads(log_path.read_text(encoding='utf-8'))

                self.assertEqual(payload['review_window']['scheduled_run_at'], '2026-05-25T02:05:05')
                self.assertEqual(payload['why_this_action']['hold_until'], '2026-05-25T02:05:05')
            finally:
                run.LOG_DIR = original_log_dir


if __name__ == '__main__':
    unittest.main()
