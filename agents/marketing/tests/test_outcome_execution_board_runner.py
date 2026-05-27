import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.marketing import outcome_execution_board_runner
from agents.marketing.distribution_lane_selector import LaneDecision


class OutcomeExecutionBoardRunnerTests(unittest.TestCase):
    def test_distribution_architecture_execution_from_run_log_payload_is_reusable(self):
        payload = {
            'timestamp': '2026-05-26T19:24:52.714478',
            'run_type': 'marketing-distribution-execution',
            'chosen_action': {
                'type': 'distribution_architecture_churn_guard_repair',
                'channel': 'distribution_architecture_repair',
                'draft': '/tmp/execution.md',
            },
            'why_this_action': {
                'summary': 'same fingerprint guard reason',
                'shared_findings_used': ['adoption_metrics_latest.json'],
                'targets_prepared': [],
            },
            'result': {
                'status': 'executed',
                'summary': 'guard already installed',
                'targets_prepared': [],
                'live_external_action': False,
                'blocking_factors': [],
            },
            'verification': {
                'execution_board_fingerprint': 'abc123',
                'guard_reason': 'same fingerprint guard reason',
            },
        }

        now = datetime(2026, 5, 26, 19, 24, 52)
        reusable = outcome_execution_board_runner._distribution_architecture_execution_from_payload(
            path=Path('/tmp/fake.json'),
            payload=payload,
            timestamp=now,
            lane='distribution_architecture_repair',
            action_types={'distribution_architecture_churn_guard_repair'},
            current_fingerprint='abc123',
            expected_reason='same fingerprint guard reason',
        )

        self.assertIsNotNone(reusable)
        self.assertEqual(reusable['action_type'], 'distribution_architecture_churn_guard_repair')
        self.assertEqual(reusable['artifact_path'], '/tmp/execution.md')
        self.assertEqual(reusable['summary'], 'guard already installed')
        self.assertEqual(reusable['timestamp'], now)

    def test_sync_from_execution_persists_latest_lane_and_preserves_short_hold_release(self):
        now = datetime(2026, 5, 26, 11, 10, 6)
        decision = LaneDecision(
            lane='distribution_architecture_repair',
            reason='selected lane during active short hold',
            reasons=['execution board is empty during the active review window'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/selected.md',
            short_review_window_release_at='2026-05-26T12:30:22',
        )
        refreshed = LaneDecision(
            lane='distribution_architecture_guard_pause',
            reason='post-execution latest lane should pause duplicate churn',
            reasons=['guard follow-through already logged'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/refreshed.md',
            short_review_window_release_at=None,
        )
        execution = SimpleNamespace(
            lane='distribution_architecture_repair',
            action_type='distribution_architecture_churn_guard_repair',
            status='executed',
            artifact_path='/tmp/execution.md',
            summary='installed churn guard for the active review window',
            targets_prepared=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            live_external_action=False,
            blocking_factors=[],
        )

        with patch.object(outcome_execution_board_runner, 'choose_distribution_lane', return_value=refreshed) as choose_mock, \
             patch.object(outcome_execution_board_runner.distribution_lane_selector, 'persist_latest_lane_decision') as persist_mock, \
             patch.object(outcome_execution_board_runner, '_write_status'):
            payload = outcome_execution_board_runner.sync_from_execution(
                now=now,
                audit={},
                decision=decision,
                board_path=Path('/tmp/board.md'),
                board_targets=[],
                execution=execution,
            )

        choose_mock.assert_called_once_with(now=now, write_action_log=False, persist_latest_artifacts=False)
        persisted_lane = persist_mock.call_args.args[0]
        self.assertEqual(persisted_lane.lane, 'distribution_architecture_guard_pause')
        self.assertEqual(persisted_lane.short_review_window_release_at, '2026-05-26T12:30:22')
        self.assertEqual(payload['selected_lane'], 'distribution_architecture_repair')
        self.assertEqual(payload['selected_action_type'], 'distribution_architecture_churn_guard_repair')

    def test_sync_from_execution_refreshes_execution_board_after_latest_lane_persist(self):
        now = datetime(2026, 5, 27, 6, 26, 47)
        decision = LaneDecision(
            lane='measurement_hold',
            reason='hold truth is current',
            reasons=['no truthful do-now packet exists in the review window'],
            owned_content_posts_last_36h=1,
            unsubmitted_directory_channels=[],
            shared_findings_used=['distribution_lane_latest.json'],
            artifact_path='/tmp/selected.md',
            short_review_window_release_at=None,
        )
        refreshed = LaneDecision(
            lane='measurement_hold',
            reason='hold truth is current',
            reasons=['no truthful do-now packet exists in the review window'],
            owned_content_posts_last_36h=1,
            unsubmitted_directory_channels=[],
            shared_findings_used=['distribution_lane_latest.json'],
            artifact_path='/tmp/refreshed.md',
            short_review_window_release_at=None,
        )
        execution = SimpleNamespace(
            lane='measurement_hold',
            action_type='measurement_hold_follow_through',
            status='executed',
            artifact_path='/tmp/execution.md',
            summary='reused current measurement hold truth',
            targets_prepared=['old-target'],
            shared_findings_used=['distribution_lane_latest.json'],
            live_external_action=False,
            blocking_factors=[],
        )

        with patch.object(outcome_execution_board_runner, 'choose_distribution_lane', return_value=refreshed), \
             patch.object(outcome_execution_board_runner.distribution_lane_selector, 'persist_latest_lane_decision'), \
             patch.object(outcome_execution_board_runner, '_write_marketing_execution_board', return_value=(Path('/tmp/refreshed-board.md'), ['fresh-target'])), \
             patch.object(outcome_execution_board_runner, '_write_status'):
            payload = outcome_execution_board_runner.sync_from_execution(
                now=now,
                audit={},
                decision=decision,
                board_path=Path('/tmp/stale-board.md'),
                board_targets=['stale-target'],
                execution=execution,
            )

        self.assertEqual(payload['execution_board_path'], '/tmp/refreshed-board.md')
        self.assertEqual(payload['execution_board_targets'], ['fresh-target'])

    def test_sync_from_execution_resyncs_latest_execution_board_alias(self):
        now = datetime(2026, 5, 27, 14, 47, 0)
        decision = LaneDecision(
            lane='measurement_hold',
            reason='hold truth is current',
            reasons=['no truthful do-now packet exists in the review window'],
            owned_content_posts_last_36h=1,
            unsubmitted_directory_channels=[],
            shared_findings_used=['distribution_lane_latest.json'],
            artifact_path='/tmp/selected.md',
            short_review_window_release_at=None,
        )
        refreshed = LaneDecision(
            lane='measurement_hold',
            reason='hold truth is current',
            reasons=['no truthful do-now packet exists in the review window'],
            owned_content_posts_last_36h=1,
            unsubmitted_directory_channels=[],
            shared_findings_used=['distribution_lane_latest.json'],
            artifact_path='/tmp/refreshed.md',
            short_review_window_release_at=None,
        )
        execution = SimpleNamespace(
            lane='measurement_hold',
            action_type='measurement_hold_follow_through',
            status='executed',
            artifact_path='/tmp/execution.md',
            summary='reused current measurement hold truth',
            targets_prepared=[],
            shared_findings_used=['distribution_lane_latest.json'],
            live_external_action=False,
            blocking_factors=[],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            drafts_dir = Path(tmpdir)
            dated_board = drafts_dir / '2026-05-27_marketing_execution_board.md'
            dated_board.write_text('# Ralph Workflow Marketing Execution Board\nGenerated: 2026-05-27T14:47:00\n', encoding='utf-8')
            stale_latest = drafts_dir / 'marketing_execution_board_latest.md'
            stale_latest.write_text('# Ralph Workflow Marketing Execution Board\nGenerated: 2026-05-26T23:16:00\n', encoding='utf-8')

            with patch.object(outcome_execution_board_runner, 'LATEST_EXECUTION_BOARD', stale_latest), \
                 patch.object(outcome_execution_board_runner, 'choose_distribution_lane', return_value=refreshed), \
                 patch.object(outcome_execution_board_runner.distribution_lane_selector, 'persist_latest_lane_decision'), \
                 patch.object(outcome_execution_board_runner, '_write_marketing_execution_board', return_value=(dated_board, [])), \
                 patch.object(outcome_execution_board_runner, '_write_status'):
                outcome_execution_board_runner.sync_from_execution(
                    now=now,
                    audit={},
                    decision=decision,
                    board_path=stale_latest,
                    board_targets=[],
                    execution=execution,
                )

            self.assertEqual(stale_latest.read_text(encoding='utf-8'), dated_board.read_text(encoding='utf-8'))

    def test_sync_from_execution_keeps_distribution_architecture_repair_when_refresh_regresses_to_owned_content(self):
        now = datetime(2026, 5, 26, 15, 14, 24)
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
            artifact_path='/tmp/execution.md',
            summary='installed churn guard for the active review window',
            targets_prepared=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            live_external_action=False,
            blocking_factors=[],
        )

        with patch.object(outcome_execution_board_runner, 'choose_distribution_lane', return_value=refreshed), \
             patch.object(outcome_execution_board_runner.distribution_lane_selector, 'persist_latest_lane_decision') as persist_mock, \
             patch.object(outcome_execution_board_runner, '_write_status'):
            outcome_execution_board_runner.sync_from_execution(
                now=now,
                audit={},
                decision=decision,
                board_path=Path('/tmp/board.md'),
                board_targets=[],
                execution=execution,
            )

        persisted_lane = persist_mock.call_args.args[0]
        self.assertEqual(persisted_lane.lane, 'distribution_architecture_repair')
        self.assertEqual(persisted_lane.short_review_window_release_at, '2026-05-26T20:55:18')

    def test_run_persists_latest_lane_after_standalone_execution_board_action(self):
        now = datetime(2026, 5, 26, 11, 10, 6)
        decision = LaneDecision(
            lane='distribution_architecture_repair',
            reason='selected lane during active short hold',
            reasons=['execution board is empty during the active review window'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/selected.md',
            short_review_window_release_at='2026-05-26T12:30:22',
        )
        refreshed = LaneDecision(
            lane='distribution_architecture_guard_pause',
            reason='post-execution latest lane should pause duplicate churn',
            reasons=['guard follow-through already logged'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/refreshed.md',
            short_review_window_release_at=None,
        )
        execution = SimpleNamespace(
            lane='distribution_architecture_repair',
            action_type='distribution_architecture_churn_guard_repair',
            status='executed',
            artifact_path='/tmp/execution.md',
            summary='installed churn guard for the active review window',
            targets_prepared=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            live_external_action=False,
            blocking_factors=[],
        )

        with patch.object(outcome_execution_board_runner, '_load_json', return_value={}), \
             patch.object(outcome_execution_board_runner, '_write_marketing_execution_board', return_value=(Path('/tmp/board.md'), [])), \
             patch.object(outcome_execution_board_runner, 'choose_distribution_lane', side_effect=[decision, refreshed]), \
             patch.object(outcome_execution_board_runner, '_latest_distribution_architecture_execution', return_value=None), \
             patch.object(outcome_execution_board_runner, 'execute_distribution_lane', return_value=execution), \
             patch.object(outcome_execution_board_runner.distribution_lane_selector, 'persist_latest_lane_decision') as persist_mock, \
             patch.object(outcome_execution_board_runner, '_write_status'):
            payload = outcome_execution_board_runner.run(now)

        persisted_lane = persist_mock.call_args.args[0]
        self.assertEqual(persisted_lane.lane, 'distribution_architecture_guard_pause')
        self.assertEqual(persisted_lane.short_review_window_release_at, '2026-05-26T12:30:22')
        self.assertEqual(payload['selected_lane'], 'distribution_architecture_repair')
        self.assertEqual(payload['selected_action_type'], 'distribution_architecture_churn_guard_repair')

    def test_persist_latest_lane_after_guard_pause_reuses_current_decision(self):
        now = datetime(2026, 5, 27, 15, 13, 45)
        decision = LaneDecision(
            lane='distribution_architecture_guard_pause',
            reason='pause duplicate guard churn',
            reasons=['board is still empty in the review window'],
            owned_content_posts_last_36h=2,
            unsubmitted_directory_channels=[],
            shared_findings_used=['marketing_execution_board_latest.md'],
            artifact_path='/tmp/guard-pause.md',
            short_review_window_release_at='2026-05-27T15:23:42',
        )
        execution = SimpleNamespace(
            lane='distribution_architecture_guard_pause',
            action_type='distribution_architecture_guard_pause',
            status='skipped_repair',
            artifact_path='/tmp/guard-pause.md',
            summary='Paused duplicate guard churn for the current execution-board fingerprint.',
            targets_prepared=[],
            shared_findings_used=['marketing_execution_board_latest.md'],
            live_external_action=False,
            blocking_factors=[],
        )

        with patch.object(outcome_execution_board_runner, 'choose_distribution_lane') as choose_mock, \
             patch.object(outcome_execution_board_runner.distribution_lane_selector, 'persist_latest_lane_decision') as persist_mock:
            latest = outcome_execution_board_runner._persist_latest_lane_after_execution(now, decision, execution)

        choose_mock.assert_not_called()
        persisted_lane = persist_mock.call_args.args[0]
        self.assertEqual(persisted_lane.lane, 'distribution_architecture_guard_pause')
        self.assertEqual(persisted_lane.short_review_window_release_at, '2026-05-27T15:23:42')
        self.assertEqual(latest.lane, 'distribution_architecture_guard_pause')

    def test_build_payload_marks_architecture_repair_as_no_do_now_lane(self):
        now = datetime(2026, 5, 27, 14, 13, 8)
        decision = LaneDecision(
            lane='distribution_architecture_guard_pause',
            reason='pause duplicate guard churn',
            reasons=['board is still empty in the review window'],
            owned_content_posts_last_36h=2,
            unsubmitted_directory_channels=[],
            shared_findings_used=['marketing_execution_board_latest.md'],
            artifact_path='/tmp/guard-pause.md',
            short_review_window_release_at='2026-05-27T14:43:06',
        )
        execution = SimpleNamespace(
            lane='distribution_architecture_guard_pause',
            action_type='distribution_architecture_guard_pause',
            status='skipped_repair',
            artifact_path='/tmp/guard-pause.md',
            summary='Paused duplicate guard churn for the current execution-board fingerprint.',
            targets_prepared=[],
            shared_findings_used=['marketing_execution_board_latest.md'],
            live_external_action=False,
            blocking_factors=[],
        )

        with patch.object(outcome_execution_board_runner.distribution_lane_selector, '_execution_board_fingerprint', return_value='abc123'):
            payload = outcome_execution_board_runner._build_payload(
                now=now,
                audit={},
                decision=decision,
                board_path=Path('/tmp/board.md'),
                board_targets=[],
                execution=execution,
            )

        self.assertFalse(payload['do_now_lane_available'])
        self.assertEqual(payload['selected_lane'], 'distribution_architecture_guard_pause')
        self.assertEqual(payload['executed_lane'], 'distribution_architecture_guard_pause')

    def test_build_payload_marks_board_lane_as_do_now_lane(self):
        now = datetime(2026, 5, 27, 14, 13, 8)
        decision = LaneDecision(
            lane='comparison_backlink_outreach',
            reason='prepared comparison queue is the strongest lane',
            reasons=['queue is ready for follow-through'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['market_intelligence_latest.json'],
            artifact_path='/tmp/comparison.md',
        )
        execution = SimpleNamespace(
            lane='comparison_backlink_outreach',
            action_type='comparison_backlink_follow_through',
            status='executed',
            artifact_path='/tmp/comparison.md',
            summary='Prepared comparison backlink follow-through.',
            targets_prepared=['aider'],
            shared_findings_used=['market_intelligence_latest.json'],
            live_external_action=False,
            blocking_factors=[],
        )

        with patch.object(outcome_execution_board_runner.distribution_lane_selector, '_execution_board_fingerprint', return_value='abc123'):
            payload = outcome_execution_board_runner._build_payload(
                now=now,
                audit={},
                decision=decision,
                board_path=Path('/tmp/board.md'),
                board_targets=['aider'],
                execution=execution,
            )

        self.assertTrue(payload['do_now_lane_available'])
        self.assertEqual(payload['selected_lane'], 'comparison_backlink_outreach')

    def test_sync_from_execution_keeps_distribution_architecture_repair_after_release_when_refresh_stays_repair(self):
        now = datetime(2026, 5, 27, 2, 53, 0)
        decision = LaneDecision(
            lane='distribution_architecture_repair',
            reason='empty board is still stuck after the hold cleared',
            reasons=['post-hold empty board must repair again'],
            owned_content_posts_last_36h=1,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/selected.md',
            short_review_window_release_at='2026-05-26T22:47:35',
        )
        refreshed = LaneDecision(
            lane='distribution_architecture_repair',
            reason='board is still empty after the hold cleared',
            reasons=['still needs concrete repair'],
            owned_content_posts_last_36h=1,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/refreshed.md',
            short_review_window_release_at='2026-05-26T22:47:35',
        )
        execution = SimpleNamespace(
            lane='distribution_architecture_repair',
            action_type='distribution_architecture_churn_guard_repair',
            status='executed',
            artifact_path='/tmp/execution.md',
            summary='repaired the post-hold empty board again',
            targets_prepared=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            live_external_action=False,
            blocking_factors=[],
        )

        with patch.object(outcome_execution_board_runner, 'choose_distribution_lane', return_value=refreshed), \
             patch.object(outcome_execution_board_runner.distribution_lane_selector, 'persist_latest_lane_decision') as persist_mock, \
             patch.object(outcome_execution_board_runner, '_write_status'):
            outcome_execution_board_runner.sync_from_execution(
                now=now,
                audit={},
                decision=decision,
                board_path=Path('/tmp/board.md'),
                board_targets=[],
                execution=execution,
            )

        persisted_lane = persist_mock.call_args.args[0]
        self.assertEqual(persisted_lane.lane, 'distribution_architecture_repair')
        self.assertEqual(persisted_lane.reason, 'board is still empty after the hold cleared')
        self.assertEqual(persisted_lane.short_review_window_release_at, '2026-05-26T22:47:35')

    def test_run_reuses_existing_distribution_architecture_execution(self):
        now = datetime(2026, 5, 26, 19, 32, 0)
        decision = LaneDecision(
            lane='distribution_architecture_repair',
            reason='same empty-board fingerprint still active',
            reasons=['execution board still has no truthful do-now lane'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/selected.md',
            short_review_window_release_at='2026-05-26T20:55:18',
        )
        refreshed = LaneDecision(
            lane='distribution_architecture_repair',
            reason='same empty-board fingerprint still active',
            reasons=['execution board still has no truthful do-now lane'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/refreshed.md',
            short_review_window_release_at='2026-05-26T20:55:18',
        )
        reused_execution = {
            'action_type': 'distribution_architecture_churn_guard_repair',
            'status': 'executed',
            'artifact_path': '/tmp/existing.md',
            'summary': 'already repaired this fingerprint in the current review window',
            'targets_prepared': [],
            'shared_findings_used': ['adoption_metrics_latest.json'],
            'live_external_action': False,
            'blocking_factors': [],
            'log_path': '/tmp/existing.json',
        }

        with patch.object(outcome_execution_board_runner, '_load_json', return_value={}), \
             patch.object(outcome_execution_board_runner, '_write_marketing_execution_board', return_value=(Path('/tmp/board.md'), [])), \
             patch.object(outcome_execution_board_runner, 'choose_distribution_lane', side_effect=[decision, refreshed]), \
             patch.object(outcome_execution_board_runner, '_latest_distribution_architecture_execution', return_value=reused_execution), \
             patch.object(outcome_execution_board_runner, '_distribution_architecture_execution_is_stale', return_value=False), \
             patch.object(outcome_execution_board_runner, 'execute_distribution_lane') as execute_mock, \
             patch.object(outcome_execution_board_runner.distribution_lane_selector, 'persist_latest_lane_decision'), \
             patch.object(outcome_execution_board_runner, '_write_status'):
            payload = outcome_execution_board_runner.run(now)

        execute_mock.assert_not_called()
        self.assertEqual(payload['selected_action_type'], 'distribution_architecture_churn_guard_repair')
        self.assertEqual(payload['artifact_path'], '/tmp/existing.md')
        self.assertEqual(payload['summary'], 'already repaired this fingerprint in the current review window')

    def test_run_uses_contract_release_for_repair_reuse_staleness(self):
        now = datetime(2026, 5, 27, 2, 40, 18)
        decision = LaneDecision(
            lane='distribution_architecture_repair',
            reason='same empty-board fingerprint still active',
            reasons=['execution board still has no truthful do-now lane'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/selected.md',
            short_review_window_release_at=None,
        )
        refreshed = LaneDecision(
            lane='distribution_architecture_repair',
            reason='same empty-board fingerprint still active',
            reasons=['execution board still has no truthful do-now lane'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/refreshed.md',
            short_review_window_release_at=None,
        )
        reused_execution = {
            'action_type': 'distribution_architecture_repair',
            'status': 'executed',
            'artifact_path': '/tmp/existing.md',
            'summary': 'stale pre-release repair',
            'targets_prepared': [],
            'shared_findings_used': ['adoption_metrics_latest.json'],
            'live_external_action': False,
            'blocking_factors': [],
            'log_path': '/tmp/existing.json',
        }
        execution = SimpleNamespace(
            lane='distribution_architecture_repair',
            action_type='distribution_architecture_repair',
            status='executed',
            artifact_path='/tmp/new.md',
            summary='fresh repair executed',
            targets_prepared=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            live_external_action=False,
            blocking_factors=[],
        )

        with patch.object(outcome_execution_board_runner, '_load_json', return_value={}), \
             patch.object(outcome_execution_board_runner, '_write_marketing_execution_board', return_value=(Path('/tmp/board.md'), [])), \
             patch.object(outcome_execution_board_runner, 'choose_distribution_lane', side_effect=[decision, refreshed]), \
             patch.object(outcome_execution_board_runner, '_latest_distribution_architecture_execution', return_value=reused_execution), \
             patch.object(outcome_execution_board_runner, '_contract_short_window_release_at', return_value='2026-05-26T22:47:35'), \
             patch.object(outcome_execution_board_runner, '_distribution_architecture_execution_is_stale', return_value=True) as stale_mock, \
             patch.object(outcome_execution_board_runner, 'execute_distribution_lane', return_value=execution) as execute_mock, \
             patch.object(outcome_execution_board_runner.distribution_lane_selector, 'persist_latest_lane_decision'), \
             patch.object(outcome_execution_board_runner, '_write_status'):
            payload = outcome_execution_board_runner.run(now)

        stale_mock.assert_called_once_with(
            reused_execution,
            lane='distribution_architecture_repair',
            now=now,
            short_review_window_release_at='2026-05-26T22:47:35',
        )
        execute_mock.assert_called_once_with(decision, now=now)
        self.assertEqual(payload['artifact_path'], '/tmp/new.md')
        self.assertEqual(payload['summary'], 'fresh repair executed')

    def test_sync_from_execution_promotes_post_release_same_fingerprint_repair_to_guard_pause(self):
        now = datetime(2026, 5, 26, 23, 16, 0)
        decision = LaneDecision(
            lane='distribution_architecture_repair',
            reason='short window already cleared and no truthful do-now lane exists',
            reasons=['execution board still has no truthful do-now lane'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/selected.md',
            short_review_window_release_at='2026-05-26T22:47:35',
        )
        refreshed = LaneDecision(
            lane='distribution_architecture_repair',
            reason='same empty-board fingerprint still active',
            reasons=['execution board still has no truthful do-now lane'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/refreshed.md',
            short_review_window_release_at='2026-05-26T22:47:35',
        )
        execution = SimpleNamespace(
            lane='distribution_architecture_repair',
            action_type='distribution_architecture_churn_guard_repair',
            status='executed',
            artifact_path='/tmp/execution.md',
            summary='already repaired this fingerprint in the cleared post-hold slot',
            targets_prepared=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            live_external_action=False,
            blocking_factors=[],
        )

        with patch.object(outcome_execution_board_runner, 'choose_distribution_lane', return_value=refreshed), \
             patch.object(outcome_execution_board_runner.distribution_lane_selector, 'persist_latest_lane_decision') as persist_mock, \
             patch.object(outcome_execution_board_runner, '_write_status'):
            outcome_execution_board_runner.sync_from_execution(
                now=now,
                audit={},
                decision=decision,
                board_path=Path('/tmp/board.md'),
                board_targets=[],
                execution=execution,
            )

        persisted_lane = persist_mock.call_args.args[0]
        self.assertEqual(persisted_lane.lane, 'distribution_architecture_guard_pause')
        self.assertEqual(persisted_lane.short_review_window_release_at, '2026-05-26T22:47:35')
        self.assertIn('pause duplicate same-fingerprint', persisted_lane.reason.lower())

    def test_sync_from_execution_uses_contract_release_when_decision_release_missing(self):
        now = datetime(2026, 5, 26, 23, 16, 0)
        decision = LaneDecision(
            lane='distribution_architecture_repair',
            reason='short window already cleared and no truthful do-now lane exists',
            reasons=['execution board still has no truthful do-now lane'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/selected.md',
            short_review_window_release_at=None,
        )
        refreshed = LaneDecision(
            lane='distribution_architecture_repair',
            reason='same empty-board fingerprint still active',
            reasons=['execution board still has no truthful do-now lane'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/refreshed.md',
            short_review_window_release_at=None,
        )
        execution = SimpleNamespace(
            lane='distribution_architecture_repair',
            action_type='distribution_architecture_churn_guard_repair',
            status='executed',
            artifact_path='/tmp/execution.md',
            summary='already repaired this fingerprint in the cleared post-hold slot',
            targets_prepared=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            live_external_action=False,
            blocking_factors=[],
        )

        with patch.object(outcome_execution_board_runner, 'choose_distribution_lane', return_value=refreshed), \
             patch.object(outcome_execution_board_runner, '_contract_short_window_release_at', return_value='2026-05-26T22:47:35'), \
             patch.object(outcome_execution_board_runner.distribution_lane_selector, 'persist_latest_lane_decision') as persist_mock, \
             patch.object(outcome_execution_board_runner, '_write_status'):
            outcome_execution_board_runner.sync_from_execution(
                now=now,
                audit={},
                decision=decision,
                board_path=Path('/tmp/board.md'),
                board_targets=[],
                execution=execution,
            )

        persisted_lane = persist_mock.call_args.args[0]
        self.assertEqual(persisted_lane.lane, 'distribution_architecture_guard_pause')
        self.assertEqual(persisted_lane.short_review_window_release_at, '2026-05-26T22:47:35')

    def test_guard_follow_through_reuse_is_stale_when_it_predates_current_short_window(self):
        with patch.object(outcome_execution_board_runner, 'LOG_DIR', Path('/tmp')):
            stale = outcome_execution_board_runner._distribution_architecture_execution_is_stale(
                {
                    'timestamp': datetime(2026, 5, 25, 7, 55, 41),
                    'artifact_path': '',
                    'log_path': '',
                },
                lane='distribution_architecture_guard_follow_through',
                now=datetime(2026, 5, 26, 22, 37, 0),
                short_review_window_release_at='2026-05-26T22:47:35',
            )

        self.assertTrue(stale)

    def test_distribution_architecture_repair_reuse_is_stale_after_hold_release_clears(self):
        with patch.object(outcome_execution_board_runner, 'LOG_DIR', Path('/tmp')):
            stale = outcome_execution_board_runner._distribution_architecture_execution_is_stale(
                {
                    'timestamp': datetime(2026, 5, 26, 3, 17, 35),
                    'artifact_path': '',
                    'log_path': '',
                },
                lane='distribution_architecture_repair',
                now=datetime(2026, 5, 27, 2, 37, 26),
                short_review_window_release_at='2026-05-26T22:47:35',
            )

        self.assertTrue(stale)

    def test_distribution_architecture_repair_reuse_is_stale_when_wrapper_is_fresh_but_artifact_predates_release(self):
        with tempfile.NamedTemporaryFile() as tmp:
            artifact_path = Path(tmp.name)
            artifact_timestamp = datetime(2026, 5, 26, 3, 17, 35).timestamp()
            os.utime(artifact_path, (artifact_timestamp, artifact_timestamp))

            with patch.object(outcome_execution_board_runner, 'LOG_DIR', Path('/tmp')):
                stale = outcome_execution_board_runner._distribution_architecture_execution_is_stale(
                    {
                        'timestamp': datetime(2026, 5, 27, 2, 39, 20),
                        'artifact_path': str(artifact_path),
                        'log_path': '',
                    },
                    lane='distribution_architecture_repair',
                    now=datetime(2026, 5, 27, 2, 39, 20),
                    short_review_window_release_at='2026-05-26T22:47:35',
                )

        self.assertTrue(stale)

    def test_sync_from_execution_records_execution_board_fingerprint_for_reuse(self):
        now = datetime(2026, 5, 26, 11, 10, 6)
        decision = LaneDecision(
            lane='distribution_architecture_repair',
            reason='selected lane during active short hold',
            reasons=['execution board is empty during the active review window'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/selected.md',
            short_review_window_release_at='2026-05-26T12:30:22',
        )
        execution = SimpleNamespace(
            lane='distribution_architecture_repair',
            action_type='distribution_architecture_churn_guard_repair',
            status='executed',
            artifact_path='/tmp/execution.md',
            summary='installed churn guard for the active review window',
            targets_prepared=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            live_external_action=False,
            blocking_factors=[],
        )

        with patch.object(outcome_execution_board_runner, 'choose_distribution_lane', return_value=decision), \
             patch.object(outcome_execution_board_runner.distribution_lane_selector, '_execution_board_fingerprint', return_value='abc123'), \
             patch.object(outcome_execution_board_runner.distribution_lane_selector, 'persist_latest_lane_decision'), \
             patch.object(outcome_execution_board_runner, '_write_status'):
            payload = outcome_execution_board_runner.sync_from_execution(
                now=now,
                audit={},
                decision=decision,
                board_path=Path('/tmp/board.md'),
                board_targets=[],
                execution=execution,
            )

        self.assertEqual(payload['execution_board_fingerprint'], 'abc123')
        self.assertEqual(payload['short_review_window_release_at'], '2026-05-26T12:30:22')

    def test_sync_latest_truth_snapshot_refreshes_status_without_execution(self):
        now = datetime(2026, 5, 27, 12, 40, 0)
        decision = LaneDecision(
            lane='owned_content',
            reason='no stronger autonomous lane detected',
            reasons=['hold window still has no truthful do-now packet'],
            owned_content_posts_last_36h=2,
            unsubmitted_directory_channels=[],
            shared_findings_used=['distribution_lane_latest.json'],
            artifact_path='/tmp/owned-content.md',
            short_review_window_release_at='2026-05-27T14:26:29',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            status_json = tmp / 'outcome_execution_board_latest.json'
            status_md = tmp / 'outcome_execution_board_latest.md'
            board_path = tmp / 'marketing_execution_board_latest.md'
            board_path.write_text('# board\n', encoding='utf-8')

            with patch.object(outcome_execution_board_runner, 'STATUS_JSON', status_json), \
                 patch.object(outcome_execution_board_runner, 'STATUS_MD', status_md), \
                 patch.object(outcome_execution_board_runner, 'LOG_DIR', tmp), \
                 patch.object(outcome_execution_board_runner, 'AUDIT_JSON', tmp / 'audit.json'), \
                 patch.object(outcome_execution_board_runner, 'choose_distribution_lane', return_value=decision), \
                 patch.object(outcome_execution_board_runner.distribution_lane_selector, 'persist_latest_lane_decision') as persist_mock, \
                 patch.object(outcome_execution_board_runner.distribution_lane_selector, '_execution_board_fingerprint', return_value='fresh123'):
                payload = outcome_execution_board_runner.sync_latest_truth_snapshot(
                    now=now,
                    audit={},
                    board_path=board_path,
                    board_targets=['no-do-now-packet'],
                )

            self.assertEqual(payload['timestamp'], now.isoformat())
            self.assertEqual(payload['selected_lane'], 'owned_content')
            self.assertEqual(payload['selected_action_type'], 'truth_snapshot_only')
            self.assertIsNone(payload['executed_lane'])
            self.assertFalse(payload['do_now_lane_available'])
            self.assertEqual(payload['execution_board_targets'], ['no-do-now-packet'])
            persisted_lane = persist_mock.call_args.args[0]
            self.assertEqual(persisted_lane.lane, 'owned_content')
            written = json.loads(status_json.read_text(encoding='utf-8'))
            self.assertEqual(written['timestamp'], now.isoformat())
            self.assertEqual(written['selected_lane'], 'owned_content')
            self.assertEqual(written['selected_action_type'], 'truth_snapshot_only')
            self.assertIsNone(written['executed_lane'])
            self.assertFalse(written['do_now_lane_available'])
            self.assertEqual(written['execution_board_fingerprint'], 'fresh123')
            self.assertIn(now.isoformat(), status_md.read_text(encoding='utf-8'))

    def test_sync_latest_truth_snapshot_resyncs_latest_execution_board_alias(self):
        now = datetime(2026, 5, 27, 21, 15, 0)
        decision = LaneDecision(
            lane='owned_content',
            reason='no stronger autonomous lane detected',
            reasons=['hold window still has no truthful do-now packet'],
            owned_content_posts_last_36h=2,
            unsubmitted_directory_channels=[],
            shared_findings_used=['distribution_lane_latest.json'],
            artifact_path='/tmp/owned-content.md',
            short_review_window_release_at='2026-05-27T18:35:08',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            status_json = tmp / 'outcome_execution_board_latest.json'
            status_md = tmp / 'outcome_execution_board_latest.md'
            dated_board = tmp / '2026-05-27_marketing_execution_board.md'
            dated_board.write_text('# dated board\n', encoding='utf-8')
            stale_latest = tmp / 'marketing_execution_board_latest.md'
            stale_latest.write_text('# stale board\n', encoding='utf-8')

            with patch.object(outcome_execution_board_runner, 'STATUS_JSON', status_json), \
                 patch.object(outcome_execution_board_runner, 'STATUS_MD', status_md), \
                 patch.object(outcome_execution_board_runner, 'LOG_DIR', tmp), \
                 patch.object(outcome_execution_board_runner, 'AUDIT_JSON', tmp / 'audit.json'), \
                 patch.object(outcome_execution_board_runner, 'LATEST_EXECUTION_BOARD', stale_latest), \
                 patch.object(outcome_execution_board_runner, 'choose_distribution_lane', return_value=decision), \
                 patch.object(outcome_execution_board_runner.distribution_lane_selector, 'persist_latest_lane_decision'), \
                 patch.object(outcome_execution_board_runner.distribution_lane_selector, '_execution_board_fingerprint', return_value='fresh123'):
                outcome_execution_board_runner.sync_latest_truth_snapshot(
                    now=now,
                    audit={},
                    board_path=dated_board,
                    board_targets=['no-do-now-packet'],
                )

            self.assertEqual(stale_latest.read_text(encoding='utf-8'), dated_board.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
