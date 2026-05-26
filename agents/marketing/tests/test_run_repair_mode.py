import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.marketing import run
from agents.marketing.distribution_lane_selector import LaneDecision


class RunRepairModeTests(unittest.TestCase):
    def test_sync_post_hold_release_run_if_needed_skips_non_hold_lane(self):
        decision = LaneDecision(
            lane='manual_outreach_asset_follow_through',
            reason='already actionable',
            reasons=['already actionable'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/manual_packet.md',
        )
        object.__setattr__(decision, 'short_review_window_release_at', '2026-05-26T13:14:38')

        with patch.object(run, '_write_post_hold_reentry_contract') as write_contract, \
             patch.object(run, '_schedule_measurement_hold_release_run') as schedule_release:
            result = run._sync_post_hold_release_run_if_needed(
                now=datetime(2026, 5, 26, 12, 38, 0),
                distribution_lane=decision,
                execution_board_path=Path('/tmp/board.md'),
                shared_findings_used=['adoption_metrics_latest.json'],
            )

        self.assertEqual(result, {})
        write_contract.assert_not_called()
        schedule_release.assert_not_called()

    def test_sync_post_hold_release_run_if_needed_reschedules_architecture_lane(self):
        decision = LaneDecision(
            lane='distribution_architecture_repair',
            reason='repair now',
            reasons=['repair now'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/distribution_action_brief.md',
        )
        object.__setattr__(decision, 'short_review_window_release_at', '2026-05-26T13:14:38')

        with patch.object(run, '_write_post_hold_reentry_contract', return_value=Path('/tmp/reentry.md')) as write_contract, \
             patch.object(run, '_schedule_measurement_hold_release_run', return_value={'status': 'scheduled', 'scheduled_run_at': '2026-05-26T13:14:38'}) as schedule_release:
            result = run._sync_post_hold_release_run_if_needed(
                now=datetime(2026, 5, 26, 12, 38, 0),
                distribution_lane=decision,
                execution_board_path=Path('/tmp/board.md'),
                shared_findings_used=['adoption_metrics_latest.json'],
            )

        self.assertEqual(result['status'], 'scheduled')
        write_contract.assert_called_once()
        schedule_release.assert_called_once()
        self.assertEqual(schedule_release.call_args.kwargs['release_at'], '2026-05-26T13:14:38')

    def test_refresh_primary_repo_flat_contact_discovery_for_empty_board_refreshes_stale_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            discovery_path = tmp / 'primary_repo_flat_contact_discovery_latest.json'
            discovery_path.write_text(
                json.dumps({'generated_at': '2026-05-26T03:00:00+00:00', 'targets': []}),
                encoding='utf-8',
            )
            script_path = tmp / 'primary_repo_flat_contact_discovery.py'
            script_path.write_text('print("ok")\n', encoding='utf-8')

            with patch.object(run, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path), \
                 patch.object(run, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_SCRIPT_PATH', script_path), \
                 patch.object(run, 'LOG_DIR', tmp), \
                 patch.object(run, '_write_marketing_execution_board', return_value=(tmp / 'board.md', ['fresh publisher target'])), \
                 patch.object(run.subprocess, 'run', return_value=subprocess.CompletedProcess(args=['python'], returncode=0, stdout='refreshed', stderr='')) as refresh_run:
                board_path, board_targets, log_path = run._refresh_primary_repo_flat_contact_discovery_for_empty_board(
                    now=datetime(2026, 5, 26, 12, 9, 0),
                    execution_board_path=tmp / 'old_board.md',
                    execution_board_targets=[],
                )

            refresh_run.assert_called_once()
            self.assertEqual(board_path, tmp / 'board.md')
            self.assertEqual(board_targets, ['fresh publisher target'])
            self.assertIsNotNone(log_path)
            self.assertTrue(log_path.exists())
            payload = json.loads(log_path.read_text(encoding='utf-8'))
            self.assertEqual(payload['type'], 'primary_repo_flat_contact_discovery_staleness_repair')
            self.assertEqual(payload['result']['board_target_count_after'], 1)

    def test_refresh_primary_repo_flat_contact_discovery_for_empty_board_skips_fresh_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            discovery_path = tmp / 'primary_repo_flat_contact_discovery_latest.json'
            discovery_path.write_text(
                json.dumps({'generated_at': '2026-05-26T11:00:00+00:00', 'targets': []}),
                encoding='utf-8',
            )

            with patch.object(run, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path), \
                 patch.object(run.subprocess, 'run') as refresh_run:
                board_path, board_targets, log_path = run._refresh_primary_repo_flat_contact_discovery_for_empty_board(
                    now=datetime(2026, 5, 26, 12, 9, 0),
                    execution_board_path=tmp / 'board.md',
                    execution_board_targets=[],
                )

            refresh_run.assert_not_called()
            self.assertEqual(board_path, tmp / 'board.md')
            self.assertEqual(board_targets, [])
            self.assertIsNone(log_path)

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

    def test_same_family_publisher_pause_advances_when_other_lane_runs(self):
        audit = {
            'repair_window_status': 'needs_repair',
            'measurement_pending_reasons': [],
            'repair_actions': [
                {
                    'failure_type': 'same_family_publisher_overlap',
                    'repair_kind': 'tactic',
                    'repair_state': 'needs_execution',
                },
            ],
        }
        execution = SimpleNamespace(action_type='measurement_hold_execution', live_external_action=False)

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

    def test_apply_repair_mode_overrides_skips_blocked_comparison_redirect(self):
        decision = LaneDecision(
            lane='owned_content',
            reason='base reason',
            reasons=['base reason'],
            owned_content_posts_last_36h=2,
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

        with patch.object(run.distribution_lane_selector, '_comparison_backlink_lane_manual_only_blocked', return_value=True), \
             patch.object(run, '_execution_board_surfaces_repo_proof_asset', return_value=False):
            updated = run._apply_repair_mode_overrides(
                decision,
                pending_repairs,
                now=datetime(2026, 5, 26, 1, 50, 0),
            )

        self.assertEqual(updated.lane, 'measurement_hold')
        self.assertIn('GitHub auth is blocked', updated.reason)

    def test_apply_repair_mode_overrides_prefers_repo_proof_asset_when_board_surfaces_it(self):
        decision = LaneDecision(
            lane='owned_content',
            reason='base reason',
            reasons=['base reason'],
            owned_content_posts_last_36h=2,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/distribution_action_brief.md',
        )
        pending_repairs = [
            {
                'failure_type': 'primary_repo_flat',
                'repair_kind': 'tactic',
                'repair_state': 'pending_measurement',
                'action': 'replace stale distribution with conversion-oriented work',
            },
        ]

        with patch.object(run.distribution_lane_selector, '_comparison_backlink_lane_manual_only_blocked', return_value=True), \
             patch.object(run, '_execution_board_surfaces_repo_proof_asset', return_value=True):
            updated = run._apply_repair_mode_overrides(
                decision,
                pending_repairs,
                now=datetime(2026, 5, 26, 6, 34, 0),
            )

        self.assertEqual(updated.lane, 'repo_conversion_proof_asset')
        self.assertIn('repo-first proof asset', updated.reason)

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

    def test_latest_measurement_hold_window_prefers_latest_release_over_stale_cron_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                hold_payload = {
                    'timestamp': '2026-05-25T14:23:10.404832',
                    'chosen_action': {'type': 'measurement_hold_execution'},
                    'why_this_action': {'summary': 'measurement hold is active'},
                    'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
                }
                stale_release_payload = {
                    'timestamp': '2026-05-25T14:23:10.404900',
                    'chosen_action': {'type': 'measurement_hold_release_cron'},
                    'review_window': {'scheduled_run_at': '2026-05-25T15:07:03'},
                    'result': {'status': 'scheduled', 'ok': True, 'live_external_action': False},
                }
                (run.LOG_DIR / 'marketing_2026-05-25_measurement_hold_execution.json').write_text(json.dumps(hold_payload), encoding='utf-8')
                (run.LOG_DIR / 'marketing_2026-05-25_measurement_hold_release_cron.json').write_text(json.dumps(stale_release_payload), encoding='utf-8')
                (run.LOG_DIR / 'distribution_lane_latest.json').write_text(
                    json.dumps({'short_review_window_release_at': '2026-05-25T23:07:41'}),
                    encoding='utf-8',
                )

                hold = run._latest_measurement_hold_window(datetime(2026, 5, 25, 20, 30, 0))

                self.assertIsNotNone(hold)
                self.assertEqual(hold['hold_until'], datetime(2026, 5, 25, 23, 7, 41))
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
                daily_log = run.LOG_DIR / f"marketing_{datetime.now().strftime('%Y-%m-%d')}.json"
                payload = json.loads(daily_log.read_text(encoding='utf-8'))
                self.assertEqual(payload['marketing_status'], 'measurement_hold')
                self.assertEqual(payload['distribution_execution']['action_type'], 'measurement_hold_follow_through')
                self.assertEqual(payload['distribution_execution']['targets_prepared'], ['Example target'])
                self.assertFalse(payload['distribution_execution']['reused_existing_follow_through'])
                self.assertIn('distribution_execution_log', payload)
            finally:
                run.LOG_DIR = original_log_dir

    def test_main_refreshes_execution_board_during_active_hold(self):
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
                     patch.object(run, '_write_marketing_execution_board', return_value=(Path('/tmp/board.md'), ['board target'])) as board_mock, \
                     patch.object(run, 'choose_distribution_lane', return_value=decision), \
                     patch.object(run, 'execute_distribution_lane', return_value=execution), \
                     patch.object(run.outcome_execution_board_runner, 'sync_from_execution') as sync_mock:
                    rc = run.main()

                self.assertEqual(rc, 0)
                board_mock.assert_called_once()
                sync_mock.assert_called_once()
                self.assertEqual(sync_mock.call_args.kwargs['board_path'], Path('/tmp/board.md'))
                self.assertEqual(sync_mock.call_args.kwargs['board_targets'], ['board target'])
                self.assertEqual(sync_mock.call_args.kwargs['decision'], decision)
                self.assertEqual(sync_mock.call_args.kwargs['execution'], execution)
            finally:
                run.LOG_DIR = original_log_dir

    def test_main_reuses_existing_follow_through_during_same_active_hold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            original_drafts_dir = run.DRAFTS_DIR
            run.LOG_DIR = Path(tmpdir)
            run.DRAFTS_DIR = Path(tmpdir) / 'drafts'
            run.DRAFTS_DIR.mkdir()
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

                decision = LaneDecision(
                    lane='measurement_hold',
                    reason='Hold for follow-through.',
                    reasons=['existing hold still active'],
                    owned_content_posts_last_36h=0,
                    unsubmitted_directory_channels=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    artifact_path='',
                )

                with patch.object(run, '_latest_measurement_hold_window', return_value=hold_window), \
                     patch.object(run, 'choose_distribution_lane', return_value=decision) as choose_mock, \
                     patch.object(run, 'execute_distribution_lane') as execute_mock, \
                     patch.object(run.distribution_lane_selector, 'persist_latest_lane_decision') as persist_mock:
                    rc = run.main()

                self.assertEqual(rc, 0)
                choose_mock.assert_called_once()
                self.assertEqual(choose_mock.call_args.kwargs.get('persist_latest_artifacts'), False)
                execute_mock.assert_not_called()
                persist_mock.assert_any_call(decision, unittest.mock.ANY, write_action_log=False)
                self.assertGreaterEqual(persist_mock.call_count, 1)
                daily_log = run.LOG_DIR / f"marketing_{datetime.now().strftime('%Y-%m-%d')}.json"
                payload = json.loads(daily_log.read_text(encoding='utf-8'))
                self.assertTrue(payload['distribution_execution']['reused_existing_follow_through'])
                self.assertEqual(payload['distribution_execution']['artifact_path'], '/tmp/existing_hold.md')
                self.assertEqual(payload['distribution_execution']['targets_prepared'], ['Existing target'])
                self.assertEqual(payload['distribution_execution_log'], str(prior_log))
            finally:
                run.LOG_DIR = original_log_dir
                run.DRAFTS_DIR = original_drafts_dir

    def test_main_refreshes_follow_through_when_truth_artifact_changed_after_prior_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            original_drafts_dir = run.DRAFTS_DIR
            run.LOG_DIR = Path(tmpdir)
            run.DRAFTS_DIR = Path(tmpdir) / 'drafts'
            run.DRAFTS_DIR.mkdir()
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
                changed_packet = run.DRAFTS_DIR / 'primary_repo_flat_contact_handoff_packet_latest.md'
                changed_packet.write_text('# refreshed packet\n', encoding='utf-8')
                changed_mtime = datetime(2026, 5, 24, 7, 5, 0).timestamp()
                os.utime(changed_packet, (changed_mtime, changed_mtime))

                decision = LaneDecision(
                    lane='measurement_hold',
                    reason='Hold for follow-through.',
                    reasons=['fresh external actions already shipped'],
                    owned_content_posts_last_36h=0,
                    unsubmitted_directory_channels=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    artifact_path='',
                )
                refreshed = LaneDecision(
                    lane='distribution_architecture_repair',
                    reason='board changed after execution',
                    reasons=['refresh latest truth'],
                    owned_content_posts_last_36h=0,
                    unsubmitted_directory_channels=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    artifact_path='/tmp/next.md',
                )
                execution = SimpleNamespace(
                    action_type='measurement_hold_follow_through',
                    status='executed',
                    artifact_path='/tmp/refreshed_hold.md',
                    summary='Refreshed hold follow-through after packet drift.',
                    targets_prepared=['Refreshed target'],
                    live_external_action=False,
                    blocking_factors=[],
                )

                with patch.object(run, '_latest_measurement_hold_window', return_value=hold_window), \
                     patch.object(run, 'choose_distribution_lane', side_effect=[decision, refreshed]) as choose_mock, \
                     patch.object(run, 'execute_distribution_lane', return_value=execution) as execute_mock, \
                     patch.object(run.distribution_lane_selector, 'persist_latest_lane_decision') as persist_mock:
                    rc = run.main()

                self.assertEqual(rc, 0)
                self.assertEqual(choose_mock.call_count, 2)
                persist_mock.assert_any_call(refreshed, unittest.mock.ANY, write_action_log=False)
                self.assertGreaterEqual(persist_mock.call_count, 1)
                self.assertEqual(choose_mock.call_args_list[1].kwargs.get('write_action_log'), False)
                self.assertEqual(choose_mock.call_args_list[1].kwargs.get('persist_latest_artifacts'), False)
                execute_mock.assert_called_once()
                daily_log = run.LOG_DIR / f"marketing_{datetime.now().strftime('%Y-%m-%d')}.json"
                payload = json.loads(daily_log.read_text(encoding='utf-8'))
                self.assertFalse(payload['distribution_execution']['reused_existing_follow_through'])
                self.assertEqual(payload['distribution_execution']['artifact_path'], '/tmp/refreshed_hold.md')
                self.assertEqual(payload['distribution_execution']['targets_prepared'], ['Refreshed target'])
                self.assertNotEqual(payload['distribution_execution_log'], str(prior_log))
                self.assertEqual(payload['post_execution_distribution_lane']['lane'], 'distribution_architecture_repair')
            finally:
                run.LOG_DIR = original_log_dir
                run.DRAFTS_DIR = original_drafts_dir

    def test_latest_distribution_architecture_guard_execution_accepts_legacy_reason_match_without_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                prior_log = run.LOG_DIR / 'marketing_2026-05-25_093100_distribution_architecture_guard_pause.json'
                prior_log.write_text(json.dumps({
                    'timestamp': '2026-05-25T09:31:00',
                    'chosen_action': {
                        'type': 'distribution_architecture_guard_pause',
                        'channel': 'distribution_architecture_guard_pause',
                        'draft': '/tmp/existing_guard_pause.md',
                    },
                    'why_this_action': {
                        'summary': 'Pause duplicate guard churn.',
                        'shared_findings_used': ['adoption_metrics_latest.json'],
                    },
                    'result': {
                        'status': 'skipped_repair',
                        'summary': 'Paused duplicate guard churn.',
                        'targets_prepared': [],
                        'live_external_action': False,
                        'blocking_factors': [],
                    },
                }), encoding='utf-8')

                with patch.object(run.distribution_lane_selector, '_execution_board_fingerprint', return_value='abc123'):
                    found = run._latest_distribution_architecture_guard_execution(
                        'distribution_architecture_guard_pause',
                        expected_reason='Pause duplicate guard churn.',
                    )

                self.assertIsNotNone(found)
                self.assertEqual(found['log_path'], str(prior_log))
            finally:
                run.LOG_DIR = original_log_dir

    def test_distribution_architecture_guard_execution_stale_only_when_artifact_or_log_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            artifact = tmp / 'guard.md'
            artifact.write_text('# guard\n', encoding='utf-8')
            log = tmp / 'guard.json'
            log.write_text('{}', encoding='utf-8')

            self.assertFalse(run._distribution_architecture_guard_execution_is_stale({
                'artifact_path': str(artifact),
                'log_path': str(log),
            }))
            self.assertTrue(run._distribution_architecture_guard_execution_is_stale({
                'artifact_path': str(tmp / 'missing.md'),
                'log_path': str(log),
            }))

    def test_latest_distribution_architecture_repair_accepts_current_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                prior_log = run.LOG_DIR / 'marketing_2026-05-26_094455_distribution_architecture_repair.json'
                prior_log.write_text(json.dumps({
                    'timestamp': '2026-05-26T09:44:55',
                    'chosen_action': {
                        'type': 'distribution_architecture_churn_guard_repair',
                        'channel': 'distribution_architecture_repair',
                        'draft': '/tmp/existing_distribution_architecture_repair.md',
                    },
                    'why_this_action': {
                        'summary': 'Repair empty-board churn.',
                        'shared_findings_used': ['adoption_metrics_latest.json'],
                    },
                    'result': {
                        'status': 'executed',
                        'summary': 'Escalated the repeated empty-board architecture failure into a third-strike churn guard tied to the current review window.',
                        'targets_prepared': [],
                        'live_external_action': False,
                        'blocking_factors': [],
                    },
                    'verification': {
                        'execution_board_fingerprint': 'abc123',
                    },
                }), encoding='utf-8')

                with patch.object(run.distribution_lane_selector, '_execution_board_fingerprint', return_value='abc123'):
                    found = run._latest_distribution_architecture_execution(
                        'distribution_architecture_repair',
                        expected_reason='Repair empty-board churn.',
                    )

                self.assertIsNotNone(found)
                self.assertEqual(found['log_path'], str(prior_log))
                self.assertEqual(found['action_type'], 'distribution_architecture_churn_guard_repair')
            finally:
                run.LOG_DIR = original_log_dir

    def test_latest_lane_to_persist_keeps_active_distribution_architecture_repair_context(self):
        selected = LaneDecision(
            lane='distribution_architecture_repair',
            reason='Repair empty-board churn.',
            reasons=['empty board still unchanged'],
            owned_content_posts_last_36h=1,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/current.md',
            short_review_window_release_at='2026-05-26T12:30:22',
        )
        refreshed = LaneDecision(
            lane='distribution_architecture_guard_pause',
            reason='pause duplicate guard churn until the fingerprint changes',
            reasons=[],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=[],
            artifact_path='/tmp/stale.md',
            short_review_window_release_at=None,
        )
        execution = SimpleNamespace(action_type='distribution_architecture_churn_guard_repair')

        latest = run._latest_lane_to_persist_after_execution(selected, refreshed, execution)

        self.assertEqual(latest.lane, 'distribution_architecture_repair')
        self.assertEqual(latest.short_review_window_release_at, '2026-05-26T12:30:22')
        self.assertEqual(latest.artifact_path, '/tmp/current.md')

    def test_main_reuses_existing_distribution_architecture_guard_pause_when_truth_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            original_drafts_dir = run.DRAFTS_DIR
            run.LOG_DIR = Path(tmpdir)
            run.DRAFTS_DIR = Path(tmpdir) / 'drafts'
            run.DRAFTS_DIR.mkdir()
            try:
                prior_log = run.LOG_DIR / 'marketing_2026-05-25_093100_distribution_architecture_guard_pause.json'
                prior_log.write_text(json.dumps({
                    'timestamp': '2026-05-25T09:31:00',
                    'chosen_action': {
                        'type': 'distribution_architecture_guard_pause',
                        'channel': 'distribution_architecture_guard_pause',
                        'draft': '/tmp/existing_guard_pause.md',
                    },
                    'why_this_action': {
                        'shared_findings_used': ['adoption_metrics_latest.json'],
                    },
                    'result': {
                        'status': 'skipped_repair',
                        'summary': 'Paused duplicate guard churn.',
                        'targets_prepared': [],
                        'live_external_action': False,
                        'blocking_factors': [],
                    },
                }), encoding='utf-8')

                decision = LaneDecision(
                    lane='distribution_architecture_guard_pause',
                    reason='Pause duplicate guard churn.',
                    reasons=['guard already acknowledged'],
                    owned_content_posts_last_36h=0,
                    unsubmitted_directory_channels=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    artifact_path='/tmp/current.md',
                )

                with patch.object(run, '_latest_measurement_hold_window', return_value=None), \
                     patch.object(run, 'run_seo_daily', return_value={'priority_actions': [], 'ranks': {}, 'backlinks': {'count_approx': 0}}), \
                     patch.object(run, 'load_seo_trends', return_value=[]), \
                     patch.object(run, 'compute_trends', return_value={}), \
                     patch.object(run, 'recent_successful_posts', return_value=[]), \
                     patch.object(run, 'load_posted_records', return_value=[]), \
                     patch.object(run, 'enrich_posts_with_views', return_value=[]), \
                     patch.object(run, 'summarize_content_performance', return_value={}), \
                     patch.object(run, 'competitor_report_is_stale', return_value=False), \
                     patch.object(run, 'load_shared_market_intelligence', return_value=None), \
                     patch.object(run, 'load_adoption_data', return_value={}), \
                     patch.object(run, 'write_seo_insights', return_value=run.LOG_DIR / 'seo-insights.json'), \
                     patch.object(run, '_latest_distribution_architecture_execution', return_value={
                         'timestamp': datetime(2026, 5, 25, 9, 31, 0),
                         'log_path': str(prior_log),
                         'action_type': 'distribution_architecture_guard_pause',
                         'artifact_path': '/tmp/existing_guard_pause.md',
                         'status': 'skipped_repair',
                         'summary': 'Paused duplicate guard churn.',
                         'targets_prepared': [],
                         'shared_findings_used': ['adoption_metrics_latest.json'],
                         'live_external_action': False,
                         'blocking_factors': [],
                     }), \
                     patch.object(run, '_distribution_architecture_guard_execution_is_stale', return_value=False), \
                     patch.object(run, 'choose_distribution_lane', return_value=decision) as choose_mock, \
                     patch.object(run, 'execute_distribution_lane') as execute_mock:
                    rc = run.main()

                self.assertEqual(rc, 0)
                execute_mock.assert_not_called()
                choose_mock.assert_called_once()
                daily_log = run.LOG_DIR / f"marketing_{datetime.now().strftime('%Y-%m-%d')}.json"
                payload = json.loads(daily_log.read_text(encoding='utf-8'))
                self.assertTrue(payload['reused_existing_distribution_execution'])
                self.assertEqual(payload['distribution_execution']['artifact_path'], '/tmp/existing_guard_pause.md')
                self.assertEqual(payload['distribution_execution_log'], str(prior_log))
            finally:
                run.LOG_DIR = original_log_dir
                run.DRAFTS_DIR = original_drafts_dir

    def test_main_reuses_existing_distribution_architecture_repair_when_truth_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            original_drafts_dir = run.DRAFTS_DIR
            run.LOG_DIR = Path(tmpdir)
            run.DRAFTS_DIR = Path(tmpdir) / 'drafts'
            run.DRAFTS_DIR.mkdir()
            try:
                prior_log = run.LOG_DIR / 'marketing_2026-05-26_094455_distribution_architecture_repair.json'
                prior_log.write_text(json.dumps({
                    'timestamp': '2026-05-26T09:44:55',
                    'chosen_action': {
                        'type': 'distribution_architecture_churn_guard_repair',
                        'channel': 'distribution_architecture_repair',
                        'draft': '/tmp/existing_distribution_architecture_repair.md',
                    },
                    'why_this_action': {
                        'shared_findings_used': ['adoption_metrics_latest.json'],
                    },
                    'result': {
                        'status': 'executed',
                        'summary': 'Existing architecture repair is still current.',
                        'targets_prepared': [],
                        'live_external_action': False,
                        'blocking_factors': [],
                    },
                }), encoding='utf-8')

                decision = LaneDecision(
                    lane='distribution_architecture_repair',
                    reason='Repair empty-board churn.',
                    reasons=['empty board still unchanged'],
                    owned_content_posts_last_36h=0,
                    unsubmitted_directory_channels=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    artifact_path='/tmp/current.md',
                )

                with patch.object(run, '_latest_measurement_hold_window', return_value=None), \
                     patch.object(run, 'run_seo_daily', return_value={'priority_actions': [], 'ranks': {}, 'backlinks': {'count_approx': 0}}), \
                     patch.object(run, 'load_seo_trends', return_value=[]), \
                     patch.object(run, 'compute_trends', return_value={}), \
                     patch.object(run, 'recent_successful_posts', return_value=[]), \
                     patch.object(run, 'load_posted_records', return_value=[]), \
                     patch.object(run, 'enrich_posts_with_views', return_value=[]), \
                     patch.object(run, 'summarize_content_performance', return_value={}), \
                     patch.object(run, 'competitor_report_is_stale', return_value=False), \
                     patch.object(run, 'load_shared_market_intelligence', return_value=None), \
                     patch.object(run, 'load_adoption_data', return_value={}), \
                     patch.object(run, 'write_seo_insights', return_value=run.LOG_DIR / 'seo-insights.json'), \
                     patch.object(run, '_latest_distribution_architecture_execution', return_value={
                         'timestamp': datetime(2026, 5, 26, 9, 44, 55),
                         'log_path': str(prior_log),
                         'action_type': 'distribution_architecture_churn_guard_repair',
                         'artifact_path': '/tmp/existing_distribution_architecture_repair.md',
                         'status': 'executed',
                         'summary': 'Existing architecture repair is still current.',
                         'targets_prepared': [],
                         'shared_findings_used': ['adoption_metrics_latest.json'],
                         'live_external_action': False,
                         'blocking_factors': [],
                     }), \
                     patch.object(run, '_distribution_architecture_guard_execution_is_stale', return_value=False), \
                     patch.object(run, 'choose_distribution_lane', return_value=decision) as choose_mock, \
                     patch.object(run, 'execute_distribution_lane') as execute_mock:
                    rc = run.main()

                self.assertEqual(rc, 0)
                execute_mock.assert_not_called()
                choose_mock.assert_called_once()
                daily_log = run.LOG_DIR / f"marketing_{datetime.now().strftime('%Y-%m-%d')}.json"
                payload = json.loads(daily_log.read_text(encoding='utf-8'))
                self.assertTrue(payload['reused_existing_distribution_execution'])
                self.assertEqual(payload['distribution_execution']['action_type'], 'distribution_architecture_churn_guard_repair')
                self.assertEqual(payload['distribution_execution']['artifact_path'], '/tmp/existing_distribution_architecture_repair.md')
                self.assertEqual(payload['distribution_execution_log'], str(prior_log))
            finally:
                run.LOG_DIR = original_log_dir
                run.DRAFTS_DIR = original_drafts_dir

    def test_main_does_not_reuse_distribution_architecture_repair_when_board_truth_is_newer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            original_drafts_dir = run.DRAFTS_DIR
            run.LOG_DIR = Path(tmpdir)
            run.DRAFTS_DIR = Path(tmpdir) / 'drafts'
            run.DRAFTS_DIR.mkdir()
            try:
                prior_log = run.LOG_DIR / 'marketing_prior_distribution_architecture_repair.json'
                prior_log.write_text(json.dumps({'ok': True}), encoding='utf-8')
                prior_timestamp = datetime.now() - timedelta(minutes=30)
                board_latest = run.DRAFTS_DIR / 'marketing_execution_board_latest.md'
                board_latest.write_text('# newer board truth\n', encoding='utf-8')
                (run.LOG_DIR / 'distribution_lane_latest.json').write_text('{}', encoding='utf-8')
                (run.LOG_DIR / 'marketing_workflow_audit_latest.json').write_text('{}', encoding='utf-8')
                (run.LOG_DIR / 'adoption_metrics_latest.json').write_text('{}', encoding='utf-8')

                decision = LaneDecision(
                    lane='distribution_architecture_repair',
                    reason='Repair empty-board churn after the hold cleared.',
                    reasons=['execution board stayed empty after blocker release'],
                    owned_content_posts_last_36h=0,
                    unsubmitted_directory_channels=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    artifact_path='/tmp/current.md',
                )
                execution = SimpleNamespace(
                    lane='distribution_architecture_repair',
                    action_type='distribution_architecture_churn_guard_repair',
                    status='executed',
                    artifact_path='/tmp/new-repair.md',
                    summary='Ran a fresh architecture repair.',
                    targets_prepared=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    live_external_action=False,
                    blocking_factors=[],
                )

                with patch.object(run, '_latest_measurement_hold_window', return_value=None), \
                     patch.object(run, 'run_seo_daily', return_value={'priority_actions': [], 'ranks': {}, 'backlinks': {'count_approx': 0}}), \
                     patch.object(run, 'load_seo_trends', return_value=[]), \
                     patch.object(run, 'compute_trends', return_value={}), \
                     patch.object(run, 'recent_successful_posts', return_value=[]), \
                     patch.object(run, 'load_posted_records', return_value=[]), \
                     patch.object(run, 'enrich_posts_with_views', return_value=[]), \
                     patch.object(run, 'summarize_content_performance', return_value={}), \
                     patch.object(run, 'competitor_report_is_stale', return_value=False), \
                     patch.object(run, 'load_shared_market_intelligence', return_value=None), \
                     patch.object(run, 'load_adoption_data', return_value={}), \
                     patch.object(run, 'write_seo_insights', return_value=run.LOG_DIR / 'seo-insights.json'), \
                     patch.object(run, '_latest_distribution_architecture_execution', return_value={
                         'timestamp': prior_timestamp,
                         'log_path': str(prior_log),
                         'action_type': 'distribution_architecture_churn_guard_repair',
                         'artifact_path': '/tmp/existing_distribution_architecture_repair.md',
                         'status': 'executed',
                         'summary': 'Existing architecture repair is still current.',
                         'targets_prepared': [],
                         'shared_findings_used': ['adoption_metrics_latest.json'],
                         'live_external_action': False,
                         'blocking_factors': [],
                     }), \
                     patch.object(run, 'choose_distribution_lane', return_value=decision), \
                     patch.object(run, 'execute_distribution_lane', return_value=execution) as execute_mock:
                    rc = run.main()

                self.assertEqual(rc, 0)
                execute_mock.assert_called_once()
                daily_log = run.LOG_DIR / f"marketing_{datetime.now().strftime('%Y-%m-%d')}.json"
                payload = json.loads(daily_log.read_text(encoding='utf-8'))
                self.assertFalse(payload['reused_existing_distribution_execution'])
                self.assertEqual(payload['distribution_execution']['artifact_path'], '/tmp/new-repair.md')
            finally:
                run.LOG_DIR = original_log_dir
                run.DRAFTS_DIR = original_drafts_dir

    def test_main_persists_post_execution_distribution_lane_truth(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            original_drafts_dir = run.DRAFTS_DIR
            run.LOG_DIR = Path(tmpdir)
            run.DRAFTS_DIR = Path(tmpdir) / 'drafts'
            run.DRAFTS_DIR.mkdir()
            try:
                decision = LaneDecision(
                    lane='owned_content',
                    reason='selector picked owned content',
                    reasons=['initial state'],
                    owned_content_posts_last_36h=0,
                    unsubmitted_directory_channels=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    artifact_path='/tmp/initial.md',
                )
                refreshed = LaneDecision(
                    lane='measurement_hold',
                    reason='short review window is still active',
                    reasons=['refresh truth after execution'],
                    owned_content_posts_last_36h=0,
                    unsubmitted_directory_channels=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    artifact_path='/tmp/refreshed.md',
                )
                execution = SimpleNamespace(
                    lane='owned_content',
                    action_type='owned_content_publication',
                    status='published',
                    artifact_path='/tmp/post.md',
                    summary='Published one owned-content asset.',
                    targets_prepared=['post'],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    live_external_action=True,
                    blocking_factors=[],
                )

                with patch.object(run, '_latest_measurement_hold_window', return_value=None), \
                     patch.object(run, 'run_seo_daily', return_value={'priority_actions': [], 'ranks': {}, 'backlinks': {'count_approx': 0}}), \
                     patch.object(run, 'load_seo_trends', return_value=[]), \
                     patch.object(run, 'compute_trends', return_value={}), \
                     patch.object(run, 'recent_successful_posts', return_value=[]), \
                     patch.object(run, 'load_posted_records', return_value=[]), \
                     patch.object(run, 'enrich_posts_with_views', return_value=[]), \
                     patch.object(run, 'summarize_content_performance', return_value={}), \
                     patch.object(run, 'competitor_report_is_stale', return_value=False), \
                     patch.object(run, 'load_shared_market_intelligence', return_value=None), \
                     patch.object(run, 'load_adoption_data', return_value={}), \
                     patch.object(run, 'write_seo_insights', return_value=run.LOG_DIR / 'seo-insights.json'), \
                     patch.object(run, '_write_marketing_execution_board', return_value=(run.DRAFTS_DIR / 'marketing_execution_board_latest.md', [])), \
                     patch.object(run, 'choose_distribution_lane', side_effect=[decision, refreshed]) as choose_mock, \
                     patch.object(run, 'execute_distribution_lane', return_value=execution), \
                     patch.object(run.distribution_lane_selector, 'persist_latest_lane_decision') as persist_mock, \
                     patch.object(run.outcome_execution_board_runner, 'sync_from_execution'):
                    rc = run.main()

                self.assertEqual(rc, 0)
                self.assertEqual(choose_mock.call_args_list[0].kwargs.get('persist_latest_artifacts'), False)
                persist_mock.assert_called_once_with(refreshed, unittest.mock.ANY, write_action_log=False)
            finally:
                run.LOG_DIR = original_log_dir
                run.DRAFTS_DIR = original_drafts_dir

    def test_refresh_distribution_lane_after_execution_skips_duplicate_action_log(self):
        initial = LaneDecision(
            lane='distribution_architecture_repair',
            reason='initial',
            reasons=['initial'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/initial.md',
        )
        with patch.object(run, 'choose_distribution_lane', return_value=initial) as choose_mock:
            refreshed = run._refresh_distribution_lane_after_execution(datetime(2026, 5, 25, 9, 13, 0), [])

        self.assertEqual(refreshed.lane, 'distribution_architecture_repair')
        self.assertEqual(choose_mock.call_args.kwargs.get('write_action_log'), False)
        self.assertEqual(choose_mock.call_args.kwargs.get('persist_latest_artifacts'), False)

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
