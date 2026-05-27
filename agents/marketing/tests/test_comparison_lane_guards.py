import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agents.marketing import distribution_lane_executor
from agents.marketing import distribution_lane_selector


class ComparisonLaneGuardTests(unittest.TestCase):
    def test_selector_uses_distribution_reset_when_comparison_lane_is_manual_only(self):
        now = datetime(2026, 5, 27, 2, 22, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({
                'evaluation': {'failing_signals': ['primary_repo_flat']},
                'recent_window': {
                    'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                },
            }), encoding='utf-8')
            audit_path = log_dir / 'marketing_workflow_audit_latest.json'
            audit_path.write_text(json.dumps({
                'repair_window_status': 'active',
                'repair_actions': [
                    {'failure_type': 'same_family_outreach_overlap', 'repair_state': 'needs_execution'},
                ],
            }), encoding='utf-8')
            market_path = log_dir / 'market_intelligence_latest.json'
            market_path.write_text(json.dumps({
                'comparison_pages': [
                    {'slug': 'hermes-agent', 'name': 'Hermes Agent'},
                    {'slug': 'openhands', 'name': 'OpenHands'},
                ],
            }), encoding='utf-8')
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['shared finding']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': False, 'reddit_blocked': False, 'partial_visibility_only': False}),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': False, 'status': 'idle', 'record_count': 0}),
                    patch.object(distribution_lane_selector, '_apollo_followup_due', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(0, 2)),
                    patch.object(distribution_lane_selector, '_comparison_backlink_lane_manual_only_blocked', return_value=True),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=2),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, True)),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_execution_board_short_review_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=False),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={}),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={'repeat_count': 0, 'guard_installed': False}),
                ]:
                    stack.enter_context(patcher)

                decision = distribution_lane_selector.choose_distribution_lane(
                    now,
                    write_action_log=False,
                    persist_latest_artifacts=False,
                )

        self.assertEqual(decision.lane, 'distribution_reset')
        self.assertTrue(decision.skip_curator_outreach)

    def test_executor_marks_comparison_follow_through_as_skipped_and_reuses_current_handoff_packet(self):
        now = datetime(2026, 5, 27, 2, 22, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='comparison_backlink_outreach',
            reason='Ship a fresh comparison lane.',
            reasons=['comparison lane selected'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['market_intelligence_latest.json'],
            artifact_path='/tmp/brief.md',
        )
        market_intelligence = {
            'comparison_pages': [
                {'slug': 'hermes-agent', 'name': 'Hermes Agent', 'comparison_path': '/compare/hermes-agent'},
            ],
            'competitors': {
                'hermes-agent': {'name': 'Hermes Agent', 'positioning': 'Self-improving agent'},
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            comparison_queue = log_dir / 'comparison_backlink_queue_latest.json'
            comparison_queue.write_text(json.dumps({'targets': [
                {
                    'slug': 'hermes-agent',
                    'name': 'Hermes Agent',
                    'status': 'prepared',
                    'comparison_path': '/compare/hermes-agent',
                    'artifact_path': '/tmp/hermes-agent.md',
                    'review_due_date': '2026-06-05',
                },
            ]}), encoding='utf-8')
            (log_dir / 'adoption_metrics_latest.json').write_text(json.dumps({
                'recent_window': {
                    'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                },
            }), encoding='utf-8')
            (log_dir / 'marketing_2026-05-27_comparison_backlink_manual_delivery.json').write_text(json.dumps({
                'timestamp': '2026-05-27T01:00:00',
                'chosen_action': {'type': 'comparison_backlink_manual_delivery'},
                'measurement_window': {'review_at': '2026-06-03T01:00:00'},
            }), encoding='utf-8')
            handoff_latest = drafts_dir / 'comparison_backlink_handoff_packet_latest.md'
            handoff_latest.write_text(
                '\n'.join([
                    '# Ralph Workflow Comparison Backlink Execution Handoff Packet',
                    '## Execute these first',
                    '### 1. Hermes Agent',
                    '- Ready file: /tmp/hermes-agent.md',
                ]) + '\n',
                encoding='utf-8',
            )
            handoff_before = handoff_latest.read_text(encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', log_dir / 'adoption_metrics_latest.json'), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=market_intelligence), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.action_type, 'comparison_backlink_follow_through')
            self.assertEqual(execution.status, 'skipped_repair')
            self.assertIn('already-current comparison handoff packet', execution.summary)
            self.assertEqual(handoff_latest.read_text(encoding='utf-8'), handoff_before)


if __name__ == '__main__':
    unittest.main()
