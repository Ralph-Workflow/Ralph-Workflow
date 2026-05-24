import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agents.marketing.distribution_lane_selector import LaneDecision
from agents.marketing import distribution_lane_executor


class DistributionLaneExecutorMeasurementHoldTests(unittest.TestCase):
    def test_active_measurement_hold_becomes_follow_through_not_new_hold(self):
        now = datetime(2026, 5, 24, 5, 20, 0)
        decision = LaneDecision(
            lane='measurement_hold',
            reason='Hold for follow-through.',
            reasons=['fresh external actions already shipped'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            existing_hold = {
                'timestamp': '2026-05-24T04:51:00',
                'chosen_action': {'type': 'measurement_hold_execution'},
                'why_this_action': {'summary': 'Existing short review window hold.'},
                'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
            }
            (log_dir / 'marketing_2026-05-24_measurement_hold_execution.json').write_text(
                json.dumps(existing_hold), encoding='utf-8'
            )

            stackoverflow_latest = {
                'cooldown_active': True,
                'next_retry_at': '2026-05-24T11:24:37.256862',
                'top_questions': [
                    {
                        'title': 'How should I structure autonomous AI agent workflows for production reliability in a TypeScript/Next.js fintech platform?',
                        'url': 'https://stackoverflow.com/questions/79942291/how-should-i-structure-autonomous-ai-agent-workflows-for-production-reliability',
                    }
                ],
            }
            (log_dir / 'stackoverflow_answer_lane_latest.json').write_text(json.dumps(stackoverflow_latest), encoding='utf-8')
            (drafts_dir / 'stackoverflow_answer_handoff_packet_latest.md').write_text('# packet\n', encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'STACKOVERFLOW_LATEST_PATH', log_dir / 'stackoverflow_answer_lane_latest.json'), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=None), \
                 patch.object(distribution_lane_executor.subprocess, 'run') as mock_run:
                mock_run.return_value.returncode = 1
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')

        self.assertEqual(execution.action_type, 'measurement_hold_follow_through')
        self.assertEqual(execution.status, 'executed')
        self.assertIn('active measurement-hold cooldown', execution.summary.lower())
        self.assertIn('StackOverflow handoff asset', execution.summary)
        self.assertEqual(
            execution.targets_prepared,
            ['How should I structure autonomous AI agent workflows for production reliability in a TypeScript/Next.js fintech platform?'],
        )
        self.assertIn('Best human-executable demand-capture asset still waiting', artifact_text)
        self.assertIn('stackoverflow_answer_handoff_packet_latest.md', artifact_text)


if __name__ == '__main__':
    unittest.main()
