import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.marketing import outcome_execution_board_runner, run
from agents.marketing.distribution_lane_selector import LaneDecision


class RunRepairModeTests(unittest.TestCase):
    def test_write_measurement_hold_skip_log_reuses_matching_log_in_same_window(self):
        hold_started_at = datetime(2026, 5, 26, 19, 55, 0)
        hold_until = datetime(2026, 5, 26, 20, 55, 18)
        now = datetime(2026, 5, 26, 20, 20, 0)
        hold_window = {
            'hold_started_at': hold_started_at,
            'hold_until': hold_until,
            'source_log': '/tmp/measurement_hold.json',
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            existing = log_dir / 'marketing_2026-05-26_200011_measurement_hold_skip.json'
            existing.write_text(json.dumps({
                'timestamp': '2026-05-26T20:00:11',
                'chosen_action': {
                    'type': 'measurement_hold_cooldown_skip',
                },
                'why_this_action': {
                    'hold_until': hold_until.isoformat(),
                    'source_log': '/tmp/measurement_hold.json',
                },
            }), encoding='utf-8')

            with patch.object(run, 'LOG_DIR', log_dir):
                path = run._write_measurement_hold_skip_log(now, hold_window)

            self.assertEqual(path, existing)
            self.assertEqual(len(list(log_dir.glob('*measurement_hold_skip.json'))), 1)

    def test_write_measurement_hold_skip_log_writes_new_when_hold_truth_changes(self):
        hold_started_at = datetime(2026, 5, 26, 19, 55, 0)
        now = datetime(2026, 5, 26, 20, 20, 0)
        hold_window = {
            'hold_started_at': hold_started_at,
            'hold_until': datetime(2026, 5, 26, 20, 55, 18),
            'source_log': '/tmp/measurement_hold.json',
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            existing = log_dir / 'marketing_2026-05-26_200011_measurement_hold_skip.json'
            existing.write_text(json.dumps({
                'timestamp': '2026-05-26T20:00:11',
                'chosen_action': {
                    'type': 'measurement_hold_cooldown_skip',
                },
                'why_this_action': {
                    'hold_until': '2026-05-26T20:30:00',
                    'source_log': '/tmp/measurement_hold.json',
                },
            }), encoding='utf-8')

            with patch.object(run, 'LOG_DIR', log_dir):
                path = run._write_measurement_hold_skip_log(now, hold_window)

            self.assertNotEqual(path, existing)
            payload = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(payload['why_this_action']['hold_until'], '2026-05-26T20:55:18')
            self.assertEqual(len(list(log_dir.glob('*measurement_hold_skip.json'))), 2)

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
                 patch.object(run, 'LOG_DIR', tmp), \
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

    def test_refresh_primary_repo_flat_contact_discovery_for_empty_board_forces_refresh_after_repeated_prepared_only_packet_churn(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            discovery_path = tmp / 'primary_repo_flat_contact_discovery_latest.json'
            discovery_path.write_text(
                json.dumps({'generated_at': '2026-05-26T11:00:00+00:00', 'targets': []}),
                encoding='utf-8',
            )
            script_path = tmp / 'primary_repo_flat_contact_discovery.py'
            script_path.write_text('print("ok")\n', encoding='utf-8')
            for name, timestamp, action_type in [
                ('marketing_2026-05-26_090000_primary_repo_flat_contact_handoff_packet_execution.json', '2026-05-26T09:00:00+00:00', 'primary_repo_flat_contact_handoff_packet_execution'),
                ('marketing_2026-05-26_100000_primary_repo_flat_contact_handoff_follow_through.json', '2026-05-26T10:00:00+00:00', 'primary_repo_flat_contact_handoff_follow_through'),
            ]:
                (tmp / name).write_text(
                    json.dumps({
                        'timestamp': timestamp,
                        'chosen_action': {'type': action_type},
                        'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
                    }),
                    encoding='utf-8',
                )

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
            payload = json.loads(log_path.read_text(encoding='utf-8'))
            self.assertEqual(payload['why_this_action']['refresh_trigger'], 'prepared_only_packet_repeat')
            self.assertEqual(payload['why_this_action']['prepared_only_repeat_count'], 2)
            self.assertEqual(payload['chosen_action']['type'], 'primary_repo_flat_contact_discovery_staleness_repair')
            self.assertIn('discovery_artifact_fingerprint', payload['verification'])

    def test_refresh_primary_repo_flat_contact_discovery_for_empty_board_suppresses_duplicate_repeat_refresh_while_artifact_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            discovery_payload = {'generated_at': '2026-05-26T11:00:00+00:00', 'targets': []}
            discovery_path = tmp / 'primary_repo_flat_contact_discovery_latest.json'
            discovery_path.write_text(json.dumps(discovery_payload), encoding='utf-8')
            script_path = tmp / 'primary_repo_flat_contact_discovery.py'
            script_path.write_text('print("ok")\n', encoding='utf-8')
            discovery_fingerprint = run._stable_json_fingerprint(discovery_payload)
            empty_fingerprint = run._stable_json_fingerprint([])
            for name, timestamp, action_type in [
                ('marketing_2026-05-26_090000_primary_repo_flat_contact_handoff_packet_execution.json', '2026-05-26T09:00:00+00:00', 'primary_repo_flat_contact_handoff_packet_execution'),
                ('marketing_2026-05-26_100000_primary_repo_flat_contact_handoff_follow_through.json', '2026-05-26T10:00:00+00:00', 'primary_repo_flat_contact_handoff_follow_through'),
            ]:
                (tmp / name).write_text(
                    json.dumps({
                        'timestamp': timestamp,
                        'chosen_action': {'type': action_type},
                        'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
                    }),
                    encoding='utf-8',
                )
            (tmp / 'marketing_2026-05-26_120000_primary_repo_flat_contact_discovery_staleness_repair.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-26T12:00:00+00:00',
                    'chosen_action': {'type': 'primary_repo_flat_contact_discovery_staleness_repair'},
                    'why_this_action': {
                        'refresh_trigger': 'prepared_only_packet_repeat',
                        'stale_generated_at': '2026-05-26T11:00:00+00:00',
                        'board_targets_before': [],
                        'board_targets_after': [],
                    },
                    'verification': {
                        'discovery_artifact_fingerprint': discovery_fingerprint,
                        'board_targets_before_fingerprint': empty_fingerprint,
                        'board_targets_after_fingerprint': empty_fingerprint,
                    },
                    'result': {'status': 'executed', 'ok': True, 'live_external_action': False},
                }),
                encoding='utf-8',
            )

            with patch.object(run, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path), \
                 patch.object(run, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_SCRIPT_PATH', script_path), \
                 patch.object(run, 'LOG_DIR', tmp), \
                 patch.object(run.subprocess, 'run') as refresh_run:
                board_path, board_targets, log_path = run._refresh_primary_repo_flat_contact_discovery_for_empty_board(
                    now=datetime(2026, 5, 26, 12, 9, 0),
                    execution_board_path=tmp / 'old_board.md',
                    execution_board_targets=[],
                )

            refresh_run.assert_not_called()
            self.assertEqual(board_path, tmp / 'old_board.md')
            self.assertEqual(board_targets, [])
            self.assertIsNone(log_path)

    def test_refresh_primary_repo_flat_contact_discovery_for_empty_board_suppresses_duplicate_repeat_refresh_after_empty_result_even_when_discovery_changed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            discovery_payload = {
                'generated_at': '2026-05-26T12:05:00+00:00',
                'targets': [{'target': 'ctxt.dev / Signum'}],
            }
            discovery_path = tmp / 'primary_repo_flat_contact_discovery_latest.json'
            discovery_path.write_text(json.dumps(discovery_payload), encoding='utf-8')
            script_path = tmp / 'primary_repo_flat_contact_discovery.py'
            script_path.write_text('print("ok")\n', encoding='utf-8')
            empty_fingerprint = run._stable_json_fingerprint([])
            for name, timestamp, action_type in [
                ('marketing_2026-05-26_090000_primary_repo_flat_contact_handoff_packet_execution.json', '2026-05-26T09:00:00+00:00', 'primary_repo_flat_contact_handoff_packet_execution'),
                ('marketing_2026-05-26_100000_primary_repo_flat_contact_handoff_follow_through.json', '2026-05-26T10:00:00+00:00', 'primary_repo_flat_contact_handoff_follow_through'),
            ]:
                (tmp / name).write_text(
                    json.dumps({
                        'timestamp': timestamp,
                        'chosen_action': {'type': action_type},
                        'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
                    }),
                    encoding='utf-8',
                )
            (tmp / 'marketing_2026-05-26_120000_primary_repo_flat_contact_discovery_staleness_repair.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-26T12:00:00+00:00',
                    'chosen_action': {'type': 'primary_repo_flat_contact_discovery_staleness_repair'},
                    'why_this_action': {
                        'refresh_trigger': 'prepared_only_packet_repeat',
                        'stale_generated_at': '2026-05-26T11:00:00+00:00',
                        'board_targets_before': [],
                        'board_targets_after': [],
                    },
                    'verification': {
                        'discovery_artifact_fingerprint': 'older-fingerprint',
                        'board_targets_before_fingerprint': empty_fingerprint,
                        'board_targets_after_fingerprint': empty_fingerprint,
                    },
                    'result': {'status': 'executed', 'ok': True, 'live_external_action': False},
                }),
                encoding='utf-8',
            )

            with patch.object(run, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path), \
                 patch.object(run, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_SCRIPT_PATH', script_path), \
                 patch.object(run, 'LOG_DIR', tmp), \
                 patch.object(run.subprocess, 'run') as refresh_run:
                board_path, board_targets, log_path = run._refresh_primary_repo_flat_contact_discovery_for_empty_board(
                    now=datetime(2026, 5, 26, 12, 9, 0),
                    execution_board_path=tmp / 'old_board.md',
                    execution_board_targets=[],
                )

            refresh_run.assert_not_called()
            self.assertEqual(board_path, tmp / 'old_board.md')
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

    def test_latest_measurement_hold_window_does_not_rebase_to_follow_through_or_churn_guard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                execution_payload = {
                    'timestamp': '2026-05-28T03:43:43.128124',
                    'chosen_action': {'type': 'measurement_hold_execution'},
                    'why_this_action': {'summary': 'measurement hold is active'},
                    'review_window': {'scheduled_run_at': '2026-05-28T09:12:15'},
                    'result': {'status': 'executed', 'ok': True, 'live_external_action': False},
                }
                follow_through_payload = {
                    'timestamp': '2026-05-28T05:12:01.551250',
                    'chosen_action': {'type': 'measurement_hold_follow_through'},
                    'why_this_action': {'summary': 'follow-through only'},
                    'review_window': {'scheduled_run_at': '2026-05-28T09:12:15'},
                    'result': {'status': 'executed', 'ok': True, 'live_external_action': False},
                }
                churn_guard_payload = {
                    'timestamp': '2026-05-28T05:14:06.038761',
                    'chosen_action': {'type': 'measurement_hold_churn_guard_repair'},
                    'why_this_action': {'summary': 'guard'},
                    'result': {'status': 'executed', 'ok': True, 'live_external_action': False},
                }
                (run.LOG_DIR / 'marketing_2026-05-28_measurement_hold_execution.json').write_text(json.dumps(execution_payload), encoding='utf-8')
                (run.LOG_DIR / 'marketing_2026-05-28_051201_measurement_hold_follow_through.json').write_text(json.dumps(follow_through_payload), encoding='utf-8')
                (run.LOG_DIR / 'marketing_2026-05-28_051406_measurement_hold_churn_guard_repair.json').write_text(json.dumps(churn_guard_payload), encoding='utf-8')
                (run.LOG_DIR / 'distribution_lane_latest.json').write_text(
                    json.dumps({'short_review_window_release_at': '2026-05-28T09:12:15'}),
                    encoding='utf-8',
                )

                hold = run._latest_measurement_hold_window(datetime(2026, 5, 28, 5, 20, 0))

                self.assertIsNotNone(hold)
                self.assertEqual(hold['hold_started_at'], datetime(2026, 5, 28, 3, 43, 43, 128124))
                self.assertEqual(hold['hold_until'], datetime(2026, 5, 28, 9, 12, 15))
                self.assertEqual(hold['source_log'], str(run.LOG_DIR / 'marketing_2026-05-28_measurement_hold_execution.json'))
            finally:
                run.LOG_DIR = original_log_dir

    def test_latest_measurement_hold_window_respects_latest_live_external_boundary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                old_execution_payload = {
                    'timestamp': '2026-05-28T03:43:43.128124',
                    'chosen_action': {'type': 'measurement_hold_execution'},
                    'review_window': {'scheduled_run_at': '2026-05-28T09:12:15'},
                    'result': {'status': 'executed', 'ok': True, 'live_external_action': False},
                }
                live_payload = {
                    'timestamp': '2026-05-28T04:40:00',
                    'chosen_action': {'type': 'publisher_email_outreach'},
                    'result': {'status': 'executed', 'ok': True, 'live_external_action': True},
                }
                follow_through_payload = {
                    'timestamp': '2026-05-28T05:12:01.551250',
                    'chosen_action': {'type': 'measurement_hold_follow_through'},
                    'why_this_action': {'summary': 'follow-through only'},
                    'review_window': {'scheduled_run_at': '2026-05-28T09:12:15'},
                    'result': {'status': 'executed', 'ok': True, 'live_external_action': False},
                }
                churn_guard_payload = {
                    'timestamp': '2026-05-28T04:56:32.391216',
                    'chosen_action': {'type': 'measurement_hold_churn_guard_repair'},
                    'why_this_action': {'summary': 'guard'},
                    'result': {'status': 'executed', 'ok': True, 'live_external_action': False},
                }
                (run.LOG_DIR / 'marketing_2026-05-28_034343_measurement_hold_execution.json').write_text(json.dumps(old_execution_payload), encoding='utf-8')
                (run.LOG_DIR / 'marketing_2026-05-28_publisher_email_outreach.json').write_text(json.dumps(live_payload), encoding='utf-8')
                (run.LOG_DIR / 'marketing_2026-05-28_045632_measurement_hold_churn_guard_repair.json').write_text(json.dumps(churn_guard_payload), encoding='utf-8')
                (run.LOG_DIR / 'marketing_2026-05-28_051201_measurement_hold_follow_through.json').write_text(json.dumps(follow_through_payload), encoding='utf-8')
                (run.LOG_DIR / 'distribution_lane_latest.json').write_text(
                    json.dumps({'short_review_window_release_at': '2026-05-28T09:12:15'}),
                    encoding='utf-8',
                )

                hold = run._latest_measurement_hold_window(datetime(2026, 5, 28, 5, 20, 0))

                self.assertIsNotNone(hold)
                self.assertEqual(hold['hold_started_at'], datetime(2026, 5, 28, 5, 12, 1, 551250))
                self.assertEqual(hold['source_log'], str(run.LOG_DIR / 'marketing_2026-05-28_051201_measurement_hold_follow_through.json'))
            finally:
                run.LOG_DIR = original_log_dir

    def test_latest_measurement_hold_window_does_not_match_old_holds_via_current_distribution_lane_release(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                old_hold_payload = {
                    'timestamp': '2026-05-27T11:18:13.123092',
                    'chosen_action': {'type': 'measurement_hold_execution'},
                    'result': {'status': 'executed', 'ok': True, 'live_external_action': False},
                }
                current_guard_payload = {
                    'timestamp': '2026-05-28T04:56:32.391216',
                    'chosen_action': {'type': 'measurement_hold_churn_guard_repair'},
                    'why_this_action': {'summary': 'guard'},
                    'review_window': {'scheduled_run_at': '2026-05-28T09:12:15'},
                    'result': {'status': 'executed', 'ok': True, 'live_external_action': False},
                }
                (run.LOG_DIR / 'marketing_2026-05-27_111813_measurement_hold_execution.json').write_text(json.dumps(old_hold_payload), encoding='utf-8')
                (run.LOG_DIR / 'marketing_2026-05-28_045632_measurement_hold_churn_guard_repair.json').write_text(json.dumps(current_guard_payload), encoding='utf-8')
                (run.LOG_DIR / 'distribution_lane_latest.json').write_text(
                    json.dumps({'short_review_window_release_at': '2026-05-28T09:12:15'}),
                    encoding='utf-8',
                )

                hold = run._latest_measurement_hold_window(datetime(2026, 5, 28, 5, 20, 0))

                self.assertIsNotNone(hold)
                self.assertEqual(hold['hold_started_at'], datetime(2026, 5, 28, 4, 56, 32, 391216))
                self.assertEqual(hold['source_log'], str(run.LOG_DIR / 'marketing_2026-05-28_045632_measurement_hold_churn_guard_repair.json'))
            finally:
                run.LOG_DIR = original_log_dir

    def test_measurement_hold_follow_through_stale_check_ignores_same_run_alias_refreshes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            original_drafts_dir = run.DRAFTS_DIR
            run.LOG_DIR = Path(tmpdir) / 'logs'
            run.DRAFTS_DIR = Path(tmpdir) / 'drafts'
            run.LOG_DIR.mkdir(parents=True, exist_ok=True)
            run.DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
            try:
                follow_through_timestamp = datetime(2026, 5, 28, 5, 27, 12)
                tracked_artifact = run.LOG_DIR / 'primary_repo_flat_contact_discovery_latest.json'
                tracked_artifact.write_text('{}', encoding='utf-8')
                same_run_mtime = follow_through_timestamp.timestamp() + 2
                os.utime(tracked_artifact, (same_run_mtime, same_run_mtime))

                self.assertFalse(
                    run._measurement_hold_follow_through_is_stale({'timestamp': follow_through_timestamp})
                )

                later_mtime = follow_through_timestamp.timestamp() + 10
                os.utime(tracked_artifact, (later_mtime, later_mtime))

                self.assertTrue(
                    run._measurement_hold_follow_through_is_stale({'timestamp': follow_through_timestamp})
                )
            finally:
                run.LOG_DIR = original_log_dir
                run.DRAFTS_DIR = original_drafts_dir

    def test_measurement_hold_follow_through_stale_check_ignores_same_content_resync_when_fingerprint_matches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            original_drafts_dir = run.DRAFTS_DIR
            run.LOG_DIR = Path(tmpdir) / 'logs'
            run.DRAFTS_DIR = Path(tmpdir) / 'drafts'
            run.LOG_DIR.mkdir(parents=True, exist_ok=True)
            run.DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
            try:
                follow_through_timestamp = datetime(2026, 5, 28, 5, 27, 12)
                tracked_artifact = run.LOG_DIR / 'primary_repo_flat_contact_discovery_latest.json'
                tracked_artifact.write_text('{"targets": []}\n', encoding='utf-8')
                fingerprint = run._artifact_content_fingerprint(tracked_artifact)

                later_mtime = follow_through_timestamp.timestamp() + 10
                os.utime(tracked_artifact, (later_mtime, later_mtime))

                self.assertFalse(
                    run._measurement_hold_follow_through_is_stale({
                        'timestamp': follow_through_timestamp,
                        'truth_artifact_fingerprints': {str(tracked_artifact): fingerprint},
                    })
                )

                tracked_artifact.write_text('{"targets": ["new-target"]}\n', encoding='utf-8')
                changed_mtime = follow_through_timestamp.timestamp() + 20
                os.utime(tracked_artifact, (changed_mtime, changed_mtime))

                self.assertTrue(
                    run._measurement_hold_follow_through_is_stale({
                        'timestamp': follow_through_timestamp,
                        'truth_artifact_fingerprints': {str(tracked_artifact): fingerprint},
                    })
                )
            finally:
                run.LOG_DIR = original_log_dir
                run.DRAFTS_DIR = original_drafts_dir

    def test_write_distribution_execution_log_persists_measurement_hold_truth_fingerprints(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            original_drafts_dir = run.DRAFTS_DIR
            run.LOG_DIR = Path(tmpdir) / 'logs'
            run.DRAFTS_DIR = Path(tmpdir) / 'drafts'
            run.LOG_DIR.mkdir(parents=True, exist_ok=True)
            run.DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
            try:
                tracked_artifact = run.LOG_DIR / 'primary_repo_flat_contact_discovery_latest.json'
                tracked_artifact.write_text('{"targets": []}\n', encoding='utf-8')
                lane = SimpleNamespace(lane='measurement_hold', short_review_window_release_at='2026-05-28T09:12:15')
                execution = SimpleNamespace(
                    action_type='measurement_hold_churn_guard_repair',
                    artifact_path='/tmp/hold.md',
                    status='executed',
                    summary='guard',
                    targets_prepared=[],
                    shared_findings_used=[],
                    live_external_action=False,
                    blocking_factors=[],
                )

                log_path = run._write_distribution_execution_log(
                    distribution_lane=lane,
                    execution=execution,
                    now=datetime(2026, 5, 28, 5, 32, 0),
                )
                payload = json.loads(log_path.read_text(encoding='utf-8'))

                self.assertEqual(
                    payload['verification']['truth_artifact_fingerprints'][str(tracked_artifact)],
                    run._artifact_content_fingerprint(tracked_artifact),
                )
                self.assertEqual(payload['review_window']['scheduled_run_at'], '2026-05-28T09:12:15')
            finally:
                run.LOG_DIR = original_log_dir
                run.DRAFTS_DIR = original_drafts_dir

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

    def test_main_collapses_non_truthful_packet_lane_back_to_measurement_hold_during_active_hold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                hold_window = {
                    'hold_started_at': datetime(2026, 5, 27, 8, 0, 0),
                    'hold_until': datetime(2026, 5, 27, 14, 26, 29),
                    'source_log': '/tmp/marketing_2026-05-27_measurement_hold_execution.json',
                    'reason': 'Existing hold still active.',
                }
                stale_packet_decision = LaneDecision(
                    lane='apollo_launch_handoff_packet',
                    reason='Apollo packet still available.',
                    reasons=['prepared asset exists'],
                    owned_content_posts_last_36h=0,
                    unsubmitted_directory_channels=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    artifact_path='/tmp/apollo.md',
                    short_review_window_release_at='2026-05-27T14:26:29',
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
                     patch.object(run, '_write_marketing_execution_board', return_value=(Path('/tmp/board.md'), [])), \
                     patch.object(run, 'choose_distribution_lane', return_value=stale_packet_decision), \
                     patch.object(run.distribution_lane_selector, '_execution_board_has_no_truthful_do_now_packet', return_value=True), \
                     patch.object(run, 'execute_distribution_lane', return_value=execution) as execute_mock:
                    rc = run.main()

                self.assertEqual(rc, 0)
                executed_decision = execute_mock.call_args.args[0]
                self.assertEqual(executed_decision.lane, 'measurement_hold')
                self.assertIn('empty execution board truth', executed_decision.reason.lower())
            finally:
                run.LOG_DIR = original_log_dir

    def test_main_keeps_architecture_repair_lane_during_active_hold_even_with_empty_board(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                hold_window = {
                    'hold_started_at': datetime(2026, 5, 27, 8, 0, 0),
                    'hold_until': datetime(2026, 5, 27, 14, 26, 29),
                    'source_log': '/tmp/marketing_2026-05-27_measurement_hold_execution.json',
                    'reason': 'Existing hold still active.',
                }
                repair_decision = LaneDecision(
                    lane='distribution_architecture_repair',
                    reason='Selector found a concrete repair.',
                    reasons=['repair the loop instead of churning'],
                    owned_content_posts_last_36h=0,
                    unsubmitted_directory_channels=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    artifact_path='/tmp/repair.md',
                    short_review_window_release_at='2026-05-27T14:26:29',
                )
                execution = SimpleNamespace(
                    action_type='distribution_architecture_repair',
                    status='executed',
                    artifact_path='/tmp/repair.md',
                    summary='Repaired hold-window execution truth.',
                    targets_prepared=[],
                    live_external_action=False,
                    blocking_factors=[],
                )

                with patch.object(run, '_latest_measurement_hold_window', return_value=hold_window), \
                     patch.object(run, '_write_marketing_execution_board', return_value=(Path('/tmp/board.md'), [])), \
                     patch.object(run, 'choose_distribution_lane', return_value=repair_decision), \
                     patch.object(run.distribution_lane_selector, '_execution_board_has_no_truthful_do_now_packet', return_value=True), \
                     patch.object(run, 'execute_distribution_lane', return_value=execution) as execute_mock:
                    rc = run.main()

                self.assertEqual(rc, 0)
                executed_decision = execute_mock.call_args.args[0]
                self.assertEqual(executed_decision.lane, 'distribution_architecture_repair')
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
                synced_decision = sync_mock.call_args.kwargs['decision']
                self.assertEqual(synced_decision.lane, 'measurement_hold')
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

    def test_distribution_architecture_guard_execution_ignores_latest_alias_mtime_churn(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            artifact = tmp / 'repair.md'
            artifact.write_text('# repair\n', encoding='utf-8')
            log = tmp / 'repair.json'
            log.write_text('{}', encoding='utf-8')
            board = tmp / 'marketing_execution_board_latest.md'
            board.write_text('# newer board\n', encoding='utf-8')
            audit = tmp / 'marketing_workflow_audit_latest.json'
            audit.write_text('{}\n', encoding='utf-8')
            adoption = tmp / 'adoption_metrics_latest.json'
            adoption.write_text('{}\n', encoding='utf-8')

            original_drafts_dir = run.DRAFTS_DIR
            original_log_dir = run.LOG_DIR
            run.DRAFTS_DIR = tmp
            run.LOG_DIR = tmp
            try:
                self.assertFalse(run._distribution_architecture_guard_execution_is_stale({
                    'timestamp': datetime(2026, 5, 26, 19, 21, 43),
                    'artifact_path': str(artifact),
                    'log_path': str(log),
                }))
            finally:
                run.DRAFTS_DIR = original_drafts_dir
                run.LOG_DIR = original_log_dir

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

    def test_latest_distribution_architecture_repair_falls_back_to_outcome_runner_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            original_status_json = outcome_execution_board_runner.STATUS_JSON
            run.LOG_DIR = Path(tmpdir)
            try:
                status_path = run.LOG_DIR / 'outcome_execution_board_latest.json'
                status_path.write_text(json.dumps({
                    'timestamp': '2026-05-26T15:26:35',
                    'selected_lane': 'distribution_architecture_repair',
                    'selected_action_type': 'distribution_architecture_churn_guard_repair',
                    'execution_board_fingerprint': 'abc123',
                    'execution': {
                        'action_type': 'distribution_architecture_churn_guard_repair',
                        'status': 'executed',
                        'targets_prepared': [],
                        'live_external_action': False,
                        'blocking_factors': [],
                    },
                    'artifact_path': '/tmp/existing_distribution_architecture_repair.md',
                    'summary': 'Escalated the repeated empty-board architecture failure into a third-strike churn guard tied to the current review window.',
                    'shared_findings_used': ['adoption_metrics_latest.json'],
                }), encoding='utf-8')

                with patch.object(outcome_execution_board_runner, 'STATUS_JSON', status_path), \
                     patch.object(run.distribution_lane_selector, '_execution_board_fingerprint', return_value='abc123'):
                    found = run._latest_distribution_architecture_execution(
                        'distribution_architecture_repair',
                        expected_reason='Repair empty-board churn.',
                    )

                self.assertIsNotNone(found)
                self.assertEqual(found['log_path'], str(status_path))
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

    def test_latest_lane_to_persist_keeps_measurement_hold_truth(self):
        selected = LaneDecision(
            lane='measurement_hold',
            reason='Hold truth is current.',
            reasons=['no truthful do-now packet exists in the review window'],
            owned_content_posts_last_36h=1,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/current-hold.md',
            short_review_window_release_at='2026-05-28T02:54:06',
        )
        refreshed = LaneDecision(
            lane='distribution_architecture_guard_pause',
            reason='pause duplicate guard churn until the fingerprint changes',
            reasons=[],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=[],
            artifact_path='/tmp/stale-guard.md',
            short_review_window_release_at='2026-05-28T02:54:06',
        )
        execution = SimpleNamespace(action_type='measurement_hold_follow_through')

        latest = run._latest_lane_to_persist_after_execution(selected, refreshed, execution)

        self.assertEqual(latest.lane, 'measurement_hold')
        self.assertEqual(latest.short_review_window_release_at, '2026-05-28T02:54:06')
        self.assertEqual(latest.artifact_path, '/tmp/current-hold.md')

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

    def test_latest_measurement_hold_follow_through_reuses_churn_guard_log_in_same_hold_window(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                prior_log = run.LOG_DIR / 'marketing_2026-05-28_045632_measurement_hold_churn_guard_repair.json'
                prior_log.write_text(json.dumps({
                    'timestamp': '2026-05-28T04:56:32',
                    'chosen_action': {
                        'type': 'measurement_hold_churn_guard_repair',
                        'draft': '/tmp/existing_hold_guard.md',
                    },
                    'result': {
                        'status': 'executed',
                        'summary': 'Reused churn guard truth.',
                        'targets_prepared': [],
                        'live_external_action': False,
                        'blocking_factors': [],
                    },
                }), encoding='utf-8')

                recent = run._latest_measurement_hold_follow_through({
                    'hold_started_at': datetime(2026, 5, 28, 3, 43, 43),
                })

                self.assertIsNotNone(recent)
                self.assertEqual(recent['action_type'], 'measurement_hold_churn_guard_repair')
                self.assertEqual(recent['artifact_path'], '/tmp/existing_hold_guard.md')
                self.assertEqual(recent['log_path'], str(prior_log))
            finally:
                run.LOG_DIR = original_log_dir

    def test_main_reuses_existing_measurement_hold_churn_guard_in_same_hold_window(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            original_drafts_dir = run.DRAFTS_DIR
            run.LOG_DIR = Path(tmpdir)
            run.DRAFTS_DIR = Path(tmpdir) / 'drafts'
            run.DRAFTS_DIR.mkdir()
            try:
                prior_log = run.LOG_DIR / 'marketing_2026-05-28_045632_measurement_hold_churn_guard_repair.json'
                prior_log.write_text(json.dumps({
                    'timestamp': '2026-05-28T04:56:32',
                    'chosen_action': {
                        'type': 'measurement_hold_churn_guard_repair',
                        'draft': '/tmp/existing_hold_guard.md',
                    },
                    'result': {
                        'status': 'executed',
                        'summary': 'Existing churn guard truth.',
                        'targets_prepared': [],
                        'live_external_action': False,
                        'blocking_factors': [],
                    },
                }), encoding='utf-8')

                hold_window = {
                    'hold_started_at': datetime(2026, 5, 28, 3, 43, 43),
                    'hold_until': datetime(2026, 5, 28, 9, 12, 15),
                    'source_log': '/tmp/hold.json',
                    'reason': 'active review window',
                }
                distribution_lane = LaneDecision(
                    lane='measurement_hold',
                    reason='Hold until short-window blockers clear.',
                    reasons=['active review window'],
                    owned_content_posts_last_36h=0,
                    unsubmitted_directory_channels=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    artifact_path='/tmp/current-hold.md',
                    short_review_window_release_at='2026-05-28T09:12:15',
                )
                distribution_execution = SimpleNamespace(
                    lane='measurement_hold',
                    action_type='measurement_hold_churn_guard_repair',
                    status='executed',
                    artifact_path='/tmp/should-not-run.md',
                    summary='should not run',
                    targets_prepared=[],
                    shared_findings_used=[],
                    live_external_action=False,
                    blocking_factors=[],
                )

                with patch.object(run, '_latest_measurement_hold_window', return_value=hold_window), \
                     patch.object(run, 'run_seo_daily', return_value={}), \
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
                     patch.object(run, '_write_marketing_execution_board', return_value=(run.DRAFTS_DIR / 'board.md', [])), \
                     patch.object(run, '_refresh_primary_repo_flat_contact_discovery_for_empty_board', return_value=(run.DRAFTS_DIR / 'board.md', [], None)), \
                     patch.object(run, 'choose_distribution_lane', return_value=distribution_lane), \
                     patch.object(run, '_measurement_hold_follow_through_is_stale', return_value=False), \
                     patch.object(run, 'execute_distribution_lane', return_value=distribution_execution) as execute_mock, \
                     patch.object(run, '_write_distribution_execution_log', return_value=run.LOG_DIR / 'distribution_execution.json'), \
                     patch.object(run, '_refresh_distribution_lane_after_execution', return_value=distribution_lane):
                    rc = run.main()

                self.assertEqual(rc, 0)
                execute_mock.assert_not_called()
                daily_log = run.LOG_DIR / f"marketing_{datetime.now().strftime('%Y-%m-%d')}.json"
                payload = json.loads(daily_log.read_text(encoding='utf-8'))
                self.assertTrue(payload['distribution_execution']['reused_existing_follow_through'])
                self.assertEqual(payload['distribution_execution']['action_type'], 'measurement_hold_churn_guard_repair')
                self.assertEqual(payload['distribution_execution']['artifact_path'], '/tmp/existing_hold_guard.md')
                self.assertEqual(payload['distribution_execution_log'], str(prior_log))
            finally:
                run.LOG_DIR = original_log_dir
                run.DRAFTS_DIR = original_drafts_dir

    def test_guard_follow_through_reuse_is_stale_when_it_predates_current_short_window(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_path = tmp / 'guard-follow-through.json'
            log_path.write_text('{}', encoding='utf-8')
            artifact_path = tmp / 'guard-follow-through.md'
            artifact_path.write_text('guard', encoding='utf-8')

            stale = run._distribution_architecture_guard_execution_is_stale(
                {
                    'timestamp': datetime(2026, 5, 25, 7, 55, 41),
                    'log_path': str(log_path),
                    'artifact_path': str(artifact_path),
                },
                lane='distribution_architecture_guard_follow_through',
                now=datetime(2026, 5, 26, 22, 37, 0),
                short_review_window_release_at='2026-05-26T22:47:35',
            )

        self.assertTrue(stale)

    def test_guard_pause_reuse_is_stale_after_short_window_release_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_path = tmp / 'guard-pause.json'
            log_path.write_text('{}', encoding='utf-8')
            artifact_path = tmp / 'guard-pause.md'
            artifact_path.write_text('guard', encoding='utf-8')

            stale = run._distribution_architecture_guard_execution_is_stale(
                {
                    'timestamp': datetime(2026, 5, 27, 21, 57, 54),
                    'log_path': str(log_path),
                    'artifact_path': str(artifact_path),
                },
                lane='distribution_architecture_guard_pause',
                now=datetime(2026, 5, 28, 3, 16, 0),
                short_review_window_release_at='2026-05-28T03:03:00',
            )

        self.assertTrue(stale)

    def test_latest_distribution_architecture_guard_execution_reuses_same_window_reason_match_when_fingerprint_drifted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                artifact_path = run.LOG_DIR / 'existing_guard_follow_through.md'
                artifact_path.write_text('guard\n', encoding='utf-8')
                prior_log = run.LOG_DIR / 'marketing_2026-05-26_213700_distribution_architecture_guard_follow_through.json'
                prior_log.write_text(json.dumps({
                    'timestamp': '2026-05-26T21:37:00',
                    'chosen_action': {
                        'type': 'distribution_architecture_guard_follow_through',
                        'draft': str(artifact_path),
                    },
                    'why_this_action': {
                        'summary': 'The same empty-board distribution-architecture failure is already under an active third-strike churn guard for this review window; suppress another identical repair and reuse the guard until the board fingerprint or blocker set materially changes.',
                        'shared_findings_used': ['adoption_metrics_latest.json'],
                    },
                    'result': {
                        'status': 'executed',
                        'summary': 'Reused the active churn guard for the current short review window.',
                        'targets_prepared': [],
                        'live_external_action': False,
                        'blocking_factors': [],
                    },
                    'verification': {
                        'execution_board_fingerprint': 'older-fingerprint',
                    },
                }), encoding='utf-8')

                with patch.object(run.distribution_lane_selector, '_execution_board_fingerprint', return_value='newer-fingerprint'), \
                     patch.object(run.outcome_execution_board_runner, 'STATUS_JSON', run.LOG_DIR / 'missing_outcome_status.json'):
                    found = run._latest_distribution_architecture_guard_execution(
                        'distribution_architecture_guard_follow_through',
                        expected_reason='The same empty-board distribution-architecture failure is already under an active third-strike churn guard for this review window; suppress another identical repair and reuse the guard until the board fingerprint or blocker set materially changes.',
                        now=datetime(2026, 5, 26, 22, 37, 0),
                        short_review_window_release_at='2026-05-26T22:47:35',
                    )

                self.assertIsNotNone(found)
                self.assertEqual(found['log_path'], str(prior_log))
            finally:
                run.LOG_DIR = original_log_dir

    def test_main_reuses_existing_distribution_architecture_repair_when_truth_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            original_drafts_dir = run.DRAFTS_DIR
            run.LOG_DIR = Path(tmpdir)
            run.DRAFTS_DIR = Path(tmpdir) / 'drafts'
            run.DRAFTS_DIR.mkdir()
            try:
                prior_artifact = run.LOG_DIR / 'existing_distribution_architecture_repair.md'
                prior_artifact.write_text('repair\n', encoding='utf-8')
                prior_log = run.LOG_DIR / 'marketing_2026-05-26_094455_distribution_architecture_repair.json'
                prior_log.write_text(json.dumps({
                    'timestamp': '2026-05-26T09:44:55',
                    'chosen_action': {
                        'type': 'distribution_architecture_churn_guard_repair',
                        'channel': 'distribution_architecture_repair',
                        'draft': str(prior_artifact),
                    },
                    'why_this_action': {
                        'summary': 'Repair empty-board churn.',
                        'shared_findings_used': ['adoption_metrics_latest.json'],
                    },
                    'result': {
                        'status': 'executed',
                        'summary': 'Existing architecture repair is still current.',
                        'targets_prepared': [],
                        'live_external_action': False,
                        'blocking_factors': [],
                    },
                    'verification': {
                        'execution_board_fingerprint': 'abc123',
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
                     patch.object(run.distribution_lane_selector, '_execution_board_fingerprint', return_value='abc123'), \
                     patch.object(run.outcome_execution_board_runner, 'STATUS_JSON', run.LOG_DIR / 'missing_outcome_status.json'), \
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
                self.assertEqual(payload['distribution_execution']['artifact_path'], str(prior_artifact))
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

    def test_main_keeps_distribution_architecture_repair_truth_when_refresh_regresses_to_owned_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            original_drafts_dir = run.DRAFTS_DIR
            run.LOG_DIR = Path(tmpdir)
            run.DRAFTS_DIR = Path(tmpdir) / 'drafts'
            run.DRAFTS_DIR.mkdir()
            try:
                decision = LaneDecision(
                    lane='distribution_architecture_repair',
                    reason='selected lane during active short hold',
                    reasons=['execution board is empty during the active review window'],
                    owned_content_posts_last_36h=0,
                    unsubmitted_directory_channels=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    artifact_path='/tmp/selected.md',
                    short_review_window_release_at='2026-05-26T20:55:18',
                )
                refreshed = LaneDecision(
                    lane='owned_content',
                    reason='fallback drift',
                    reasons=['stale fallback'],
                    owned_content_posts_last_36h=1,
                    unsubmitted_directory_channels=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    artifact_path='/tmp/refreshed.md',
                )
                execution = SimpleNamespace(
                    lane='distribution_architecture_repair',
                    action_type='distribution_architecture_churn_guard_repair',
                    status='executed',
                    artifact_path='/tmp/post.md',
                    summary='Installed churn guard for the active review window.',
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
                     patch.object(run, '_write_marketing_execution_board', return_value=(run.DRAFTS_DIR / 'marketing_execution_board_latest.md', [])), \
                     patch.object(run, 'choose_distribution_lane', side_effect=[decision, refreshed]), \
                     patch.object(run, 'execute_distribution_lane', return_value=execution), \
                     patch.object(run.distribution_lane_selector, 'persist_latest_lane_decision') as persist_mock, \
                     patch.object(run.outcome_execution_board_runner, 'sync_from_execution'):
                    rc = run.main()

                self.assertEqual(rc, 0)
                persisted_lane = persist_mock.call_args.args[0]
                self.assertEqual(persisted_lane.lane, 'distribution_architecture_repair')
                self.assertEqual(persisted_lane.short_review_window_release_at, '2026-05-26T20:55:18')
                daily_log = run.LOG_DIR / f"marketing_{datetime.now().strftime('%Y-%m-%d')}.json"
                payload = json.loads(daily_log.read_text(encoding='utf-8'))
                self.assertEqual(payload['post_execution_distribution_lane']['lane'], 'distribution_architecture_repair')
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

    def test_latest_distribution_lane_alias_is_stale_when_latest_truth_regressed(self):
        now = datetime(2026, 5, 27, 3, 21, 0)
        decision = LaneDecision(
            lane='distribution_architecture_repair',
            reason='fresh repair lane',
            reasons=['board is still empty after the hold cleared'],
            owned_content_posts_last_36h=1,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/stale.md',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            drafts_dir.mkdir()
            log_dir.mkdir()
            (drafts_dir / '2026-05-27_distribution_action_brief.md').write_text(
                '# Ralph Workflow Distribution Action Brief\nGenerated: 2026-05-27T02:57:08\nChosen lane: **distribution_architecture_repair**\n',
                encoding='utf-8',
            )
            (log_dir / 'distribution_lane_latest.json').write_text(json.dumps({
                'lane': 'reddit_execution_check',
                'reason': 'stale reddit lane',
                'reasons': ['old state'],
                'artifact_path': str(drafts_dir / '2026-05-25_distribution_action_brief.md'),
            }), encoding='utf-8')
            (log_dir / 'distribution_lane_latest.md').write_text(
                '# Ralph Workflow Distribution Action Brief\nGenerated: 2026-05-25T18:53:00\nChosen lane: **reddit_execution_check**\n',
                encoding='utf-8',
            )

            with patch.object(run.distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(run.distribution_lane_selector, 'LATEST_JSON', log_dir / 'distribution_lane_latest.json'), \
                 patch.object(run.distribution_lane_selector, 'LATEST_MD', log_dir / 'distribution_lane_latest.md'):
                self.assertTrue(run._latest_distribution_lane_alias_is_stale(decision, now))

    def test_refresh_latest_distribution_lane_alias_if_stale_rewrites_current_truth(self):
        now = datetime(2026, 5, 27, 3, 21, 0)
        decision = LaneDecision(
            lane='distribution_architecture_repair',
            reason='fresh repair lane',
            reasons=['board is still empty after the hold cleared'],
            owned_content_posts_last_36h=1,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/stale.md',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            drafts_dir.mkdir()
            log_dir.mkdir()
            (drafts_dir / '2026-05-27_distribution_action_brief.md').write_text('stale placeholder\n', encoding='utf-8')
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            latest_json.write_text(json.dumps({
                'lane': 'reddit_execution_check',
                'reason': 'stale reddit lane',
                'reasons': ['old state'],
                'artifact_path': str(drafts_dir / '2026-05-25_distribution_action_brief.md'),
            }), encoding='utf-8')
            latest_md.write_text(
                '# Ralph Workflow Distribution Action Brief\nGenerated: 2026-05-25T18:53:00\nChosen lane: **reddit_execution_check**\n',
                encoding='utf-8',
            )

            with patch.object(run.distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(run.distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(run.distribution_lane_selector, 'LATEST_MD', latest_md):
                refreshed = run._refresh_latest_distribution_lane_alias_if_stale(decision, now)

            payload = json.loads(latest_json.read_text(encoding='utf-8'))
            self.assertEqual(refreshed.lane, 'distribution_architecture_repair')
            self.assertEqual(payload['lane'], 'distribution_architecture_repair')
            self.assertEqual(Path(payload['artifact_path']).name, '2026-05-27_distribution_action_brief.md')
            latest_text = latest_md.read_text(encoding='utf-8')
            self.assertIn('Generated: 2026-05-27T03:21:00', latest_text)
            self.assertIn('Chosen lane: **distribution_architecture_repair**', latest_text)

    def test_refresh_latest_truth_snapshot_if_stale_rewrites_outcome_and_lane_aliases(self):
        now = datetime(2026, 5, 28, 4, 18, 0)
        decision = LaneDecision(
            lane='measurement_hold',
            reason='Hold truth is current.',
            reasons=['no truthful do-now packet exists in the review window'],
            owned_content_posts_last_36h=1,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/stale.md',
            short_review_window_release_at='2026-05-28T09:12:15',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            drafts_dir.mkdir()
            log_dir.mkdir()
            board_path = drafts_dir / '2026-05-28_marketing_execution_board.md'
            board_path.write_text('# current board\n', encoding='utf-8')

            (log_dir / 'distribution_lane_latest.json').write_text(json.dumps({
                'lane': 'distribution_architecture_guard_pause',
                'reason': 'stale guard pause',
                'reasons': [],
                'artifact_path': str(drafts_dir / '2026-05-25_distribution_action_brief.md'),
                'short_review_window_release_at': '2026-05-28T09:12:15',
            }), encoding='utf-8')
            (log_dir / 'distribution_lane_latest.md').write_text(
                '# Ralph Workflow Distribution Action Brief\nGenerated: 2026-05-25T18:53:00\nChosen lane: **distribution_architecture_guard_pause**\n',
                encoding='utf-8',
            )
            (log_dir / 'outcome_execution_board_latest.json').write_text(json.dumps({
                'timestamp': '2026-05-25T18:53:00',
                'selected_lane': 'distribution_architecture_guard_pause',
                'execution_board_path': str(drafts_dir / '2026-05-25_marketing_execution_board.md'),
                'short_review_window_release_at': '2026-05-28T09:12:15',
            }), encoding='utf-8')
            (log_dir / 'outcome_execution_board_latest.md').write_text(
                '# Outcome Execution Board Runner\n\n- Generated: `2026-05-25T18:53:00`\n- Selected lane: `distribution_architecture_guard_pause`\n',
                encoding='utf-8',
            )

            with patch.object(run.distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(run.distribution_lane_selector, 'LATEST_JSON', log_dir / 'distribution_lane_latest.json'), \
                 patch.object(run.distribution_lane_selector, 'LATEST_MD', log_dir / 'distribution_lane_latest.md'), \
                 patch.object(outcome_execution_board_runner.distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(outcome_execution_board_runner.distribution_lane_selector, 'LATEST_JSON', log_dir / 'distribution_lane_latest.json'), \
                 patch.object(outcome_execution_board_runner.distribution_lane_selector, 'LATEST_MD', log_dir / 'distribution_lane_latest.md'), \
                 patch.object(outcome_execution_board_runner, 'STATUS_JSON', log_dir / 'outcome_execution_board_latest.json'), \
                 patch.object(outcome_execution_board_runner, 'STATUS_MD', log_dir / 'outcome_execution_board_latest.md'), \
                 patch.object(outcome_execution_board_runner, 'LOG_DIR', log_dir), \
                 patch.object(outcome_execution_board_runner, 'LATEST_EXECUTION_BOARD', drafts_dir / 'marketing_execution_board_latest.md'), \
                 patch.object(outcome_execution_board_runner.distribution_lane_selector, '_execution_board_fingerprint', return_value='fresh123'):
                refreshed = run._refresh_latest_truth_snapshot_if_stale(
                    decision,
                    now,
                    audit={},
                    board_path=board_path,
                    board_targets=[],
                )

            lane_payload = json.loads((log_dir / 'distribution_lane_latest.json').read_text(encoding='utf-8'))
            outcome_payload = json.loads((log_dir / 'outcome_execution_board_latest.json').read_text(encoding='utf-8'))
            self.assertEqual(refreshed.lane, 'measurement_hold')
            self.assertEqual(lane_payload['lane'], 'measurement_hold')
            self.assertEqual(Path(lane_payload['artifact_path']).name, '2026-05-28_distribution_action_brief.md')
            self.assertEqual(outcome_payload['selected_lane'], 'measurement_hold')
            self.assertEqual(outcome_payload['execution_board_path'], str(board_path))
            self.assertEqual(outcome_payload['execution_board_fingerprint'], 'fresh123')

    def test_write_distribution_execution_log_records_short_review_window_release_for_measurement_hold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            run.LOG_DIR = Path(tmpdir)
            try:
                (run.LOG_DIR / 'distribution_lane_latest.json').write_text(
                    json.dumps({'short_review_window_release_at': '2026-05-25T02:05:05'}),
                    encoding='utf-8',
                )
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

    def test_write_distribution_execution_log_prefers_active_hold_until_over_stale_short_window_release(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = run.LOG_DIR
            try:
                run.LOG_DIR = Path(tmpdir)
                lane = LaneDecision(
                    lane='measurement_hold',
                    reason='hold',
                    reasons=[],
                    owned_content_posts_last_36h=0,
                    unsubmitted_directory_channels=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    artifact_path='',
                    short_review_window_release_at='2026-05-28T03:03:00',
                )
                execution = SimpleNamespace(
                    action_type='measurement_hold_follow_through',
                    status='executed',
                    artifact_path='/tmp/hold.md',
                    summary='Hold follow-through.',
                    targets_prepared=[],
                    shared_findings_used=['adoption_metrics_latest.json'],
                    live_external_action=False,
                    blocking_factors=[],
                )

                with patch.object(run, 'shared_latest_measurement_hold_window', return_value={
                    'hold_until': datetime(2026, 5, 28, 3, 34, 26),
                }):
                    log_path = run._write_distribution_execution_log(
                        distribution_lane=lane,
                        execution=execution,
                        now=datetime(2026, 5, 28, 2, 54, 49),
                    )
                payload = json.loads(log_path.read_text(encoding='utf-8'))

                self.assertEqual(payload['review_window']['scheduled_run_at'], '2026-05-28T03:34:26')
                self.assertEqual(payload['why_this_action']['hold_until'], '2026-05-28T03:34:26')
            finally:
                run.LOG_DIR = original_log_dir


if __name__ == '__main__':
    unittest.main()
