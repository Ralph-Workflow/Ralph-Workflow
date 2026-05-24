import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agents.marketing.distribution_lane_selector import LaneDecision
from agents.marketing import distribution_lane_executor


class DistributionLaneExecutorMeasurementHoldTests(unittest.TestCase):
    def test_curator_queue_rows_normalize_recent_live_actions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            log_dir.mkdir()
            queue_path = log_dir / 'curator_outreach_queue_latest.json'
            queue_path.write_text(json.dumps({
                'targets': [
                    {
                        'target': 'AI Dev Setup',
                        'url': 'https://aidevsetup.com/',
                        'status': 'prepared',
                    },
                    {
                        'target': 'AI for Code',
                        'url': 'https://aiforcode.io/',
                        'status': 'prepared',
                    },
                ],
            }), encoding='utf-8')
            (log_dir / 'marketing_2026-05-24_ai_dev_setup_contact_submission.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T04:44:21+02:00',
                    'target': 'AI Dev Setup',
                    'channel': 'high_intent_contact_form',
                    'status': 'executed',
                    'ok': True,
                    'live_external_action': True,
                    'submit_url': 'https://aidevsetup.com/contact',
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-23_aiforcode_submission.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-23T20:36:08+02:00',
                    'target': 'AI for Code',
                    'channel': 'directory_submission',
                    'status': 'executed',
                    'ok': True,
                    'live_external_action': True,
                    'submit_url': 'https://aiforcode.io/',
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', queue_path):
                rows = distribution_lane_executor._load_curator_queue_rows()

        self.assertEqual(rows[0]['status'], 'sent_via_form')
        self.assertEqual(rows[1]['status'], 'waiting_review')

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
        self.assertIn(
            'How should I structure autonomous AI agent workflows for production reliability in a TypeScript/Next.js fintech platform?',
            execution.targets_prepared,
        )
        self.assertIn('Best human-executable demand-capture asset still waiting', artifact_text)
        self.assertIn('stackoverflow_answer_handoff_packet_latest.md', artifact_text)


    def test_primary_repo_flat_packet_skips_recently_contacted_publishers(self):
        now = datetime(2026, 5, 24, 5, 55, 0)
        decision = LaneDecision(
            lane='primary_repo_flat_contact_handoff_packet',
            reason='Fresh primary-repo-flat publisher targets now have verified public contact paths.',
            reasons=['publisher contacts discovered'],
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

            discovery = {
                'targets': [
                    {'target': 'AXME Code', 'channels': [{'type': 'email', 'value': 'contact@axme.ai'}]},
                    {'target': 'WyeWorks', 'channels': [{'type': 'email', 'value': 'hello@wyeworks.com'}]},
                ]
            }
            (log_dir / 'primary_repo_flat_contact_discovery_latest.json').write_text(json.dumps(discovery), encoding='utf-8')
            (log_dir / 'marketing_2026-05-24_axme_publisher_outreach.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T05:54:00',
                    'action_type': 'publisher_email_outreach',
                    'target': 'AXME Code',
                    'status': 'executed',
                    'ok': True,
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-24_wyeworks_publisher_outreach.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T05:53:00',
                    'action_type': 'publisher_email_outreach',
                    'target': 'WyeWorks',
                    'status': 'executed',
                    'ok': True,
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md').write_text('# packet\n', encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=None), \
                 patch.object(distribution_lane_executor.subprocess, 'run') as mock_run:
                mock_run.return_value.returncode = 1
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')

        self.assertEqual(execution.action_type, 'primary_repo_flat_contact_handoff_follow_through')
        self.assertIn('already received fresh outreach', execution.summary.lower())
        self.assertNotIn('AXME Code', artifact_text)
        self.assertNotIn('WyeWorks', artifact_text)

    def test_primary_repo_flat_packet_treats_non_executable_channels_as_follow_through(self):
        now = datetime(2026, 5, 24, 6, 5, 0)
        decision = LaneDecision(
            lane='primary_repo_flat_contact_handoff_packet',
            reason='Fresh primary-repo-flat publisher targets now have verified public contact paths.',
            reasons=['publisher contacts discovered'],
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

            discovery = {
                'targets': [
                    {
                        'target': 'ctxt.dev / Signum',
                        'channels': [
                            {'type': 'website', 'value': 'https://ctxt.dev/about'},
                            {'type': 'telegram', 'value': 'https://t.me/ctxtdev'},
                        ],
                    },
                ]
            }
            (log_dir / 'primary_repo_flat_contact_discovery_latest.json').write_text(json.dumps(discovery), encoding='utf-8')
            (drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md').write_text('# packet\n', encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=None), \
                 patch.object(distribution_lane_executor.subprocess, 'run') as mock_run:
                mock_run.return_value.returncode = 1
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')

        self.assertEqual(execution.action_type, 'primary_repo_flat_contact_handoff_follow_through')
        self.assertIn('non-runtime-executable channels', execution.summary.lower())
        self.assertIn('ctxt.dev / Signum', artifact_text)
        self.assertNotIn('## Execute these first', artifact_text)

    def test_measurement_hold_refresh_skips_recently_contacted_publishers(self):
        now = datetime(2026, 5, 24, 8, 33, 0)
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
                'timestamp': '2026-05-24T08:00:00',
                'chosen_action': {'type': 'measurement_hold_execution'},
                'why_this_action': {'summary': 'Existing short review window hold.'},
                'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
            }
            (log_dir / 'marketing_2026-05-24_measurement_hold_execution.json').write_text(
                json.dumps(existing_hold), encoding='utf-8'
            )
            discovery = {
                'targets': [
                    {'target': 'AXME Code', 'channels': [{'type': 'email', 'value': 'contact@axme.ai'}]},
                    {'target': 'WyeWorks', 'channels': [{'type': 'email', 'value': 'hello@wyeworks.com'}]},
                ]
            }
            (log_dir / 'primary_repo_flat_contact_discovery_latest.json').write_text(json.dumps(discovery), encoding='utf-8')
            (log_dir / 'marketing_2026-05-24_axme_publisher_outreach.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T08:10:00',
                    'action_type': 'publisher_email_outreach',
                    'target': 'AXME Code',
                    'status': 'executed',
                    'ok': True,
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-24_wyeworks_publisher_outreach.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T08:11:00',
                    'action_type': 'publisher_email_outreach',
                    'target': 'WyeWorks',
                    'status': 'executed',
                    'ok': True,
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md').write_text('# stale packet\n- AXME Code\n- WyeWorks\n', encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=None), \
                 patch.object(distribution_lane_executor.subprocess, 'run') as mock_run:
                mock_run.return_value.returncode = 1
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            packet_text = (drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md').read_text(encoding='utf-8')

        self.assertEqual(execution.action_type, 'measurement_hold_follow_through')
        self.assertIn('Recently contacted executable targets already inside the active review window', packet_text)
        self.assertNotIn('### 1. AXME Code', packet_text)
        self.assertNotIn('### 2. WyeWorks', packet_text)


if __name__ == '__main__':
    unittest.main()
