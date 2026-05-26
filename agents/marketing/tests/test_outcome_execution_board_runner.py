import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.marketing import outcome_execution_board_runner
from agents.marketing.distribution_lane_selector import LaneDecision


class OutcomeExecutionBoardRunnerTests(unittest.TestCase):
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
             patch.object(outcome_execution_board_runner, 'execute_distribution_lane', return_value=execution), \
             patch.object(outcome_execution_board_runner.distribution_lane_selector, 'persist_latest_lane_decision') as persist_mock, \
             patch.object(outcome_execution_board_runner, '_write_status'):
            payload = outcome_execution_board_runner.run(now)

        persisted_lane = persist_mock.call_args.args[0]
        self.assertEqual(persisted_lane.lane, 'distribution_architecture_guard_pause')
        self.assertEqual(persisted_lane.short_review_window_release_at, '2026-05-26T12:30:22')
        self.assertEqual(payload['selected_lane'], 'distribution_architecture_repair')
        self.assertEqual(payload['selected_action_type'], 'distribution_architecture_churn_guard_repair')


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


if __name__ == '__main__':
    unittest.main()
