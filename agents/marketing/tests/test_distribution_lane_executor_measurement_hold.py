import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agents.marketing import distribution_lane_selector
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

            persisted = json.loads(queue_path.read_text(encoding='utf-8'))

        self.assertEqual(rows[0]['status'], 'sent_via_form')
        self.assertEqual(rows[1]['status'], 'waiting_review')
        self.assertEqual(persisted['targets'][0]['status'], 'sent_via_form')
        self.assertEqual(persisted['targets'][1]['status'], 'waiting_review')

    def test_curator_queue_rows_infer_email_status_from_recipient_only_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            log_dir.mkdir()
            queue_path = log_dir / 'curator_outreach_queue_latest.json'
            queue_path.write_text(json.dumps({
                'targets': [
                    {
                        'target': 'SitePoint — AI Coding Tools Comparison 2026',
                        'url': 'https://www.sitepoint.com/ai-coding-tools-comparison-2026/',
                        'status': 'waiting_review',
                    },
                ],
            }), encoding='utf-8')
            (log_dir / 'marketing_2026-05-24_sitepoint_publisher_outreach.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T10:46:12+02:00',
                    'action_type': 'publisher_email_outreach',
                    'status': 'executed',
                    'ok': True,
                    'live_external_action': True,
                    'target': 'SitePoint — AI Coding Tools Comparison 2026',
                    'recipient': 'support@sitepoint.com',
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', queue_path):
                rows = distribution_lane_executor._load_curator_queue_rows()

        self.assertEqual(rows[0]['status'], 'sent_via_email_fallback')
        self.assertEqual(rows[0]['last_contact_path'], 'email:support@sitepoint.com')

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

    def test_curator_handoff_packet_includes_live_listing_proof(self):
        now = datetime(2026, 5, 24, 10, 58, 0)
        queue_rows = [
            {
                'target': 'AI Resources',
                'status': 'prepared',
                'priority': 'HIGH',
                'url': 'https://airesources.dev/category/agents/',
                'review_due_date': '2026-06-07',
                'artifact_path': '/tmp/ai-resources.md',
                'action': 'Submit PR or request inclusion with a Codeberg-primary entry',
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            drafts_dir.mkdir()
            log_dir.mkdir()
            adoption_path = log_dir / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')
            backlink_path = log_dir / 'backlink_status_latest.json'
            backlink_path.write_text(json.dumps({
                'directories': {
                    'SaaSHub': {'listing_live': True, 'listing_url': 'https://saashub.com/ralph-workflow'},
                    'ToolWise': {'listing_live': True, 'listing_url': 'https://toolwise.ai/tools/ralph-workflow', 'status_note': 'Existing ToolWise listing already live and pointing to the primary Codeberg repo.'},
                }
            }), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'BACKLINK_STATUS_LATEST_PATH', backlink_path):
                artifact, _prepared = distribution_lane_executor._write_curator_handoff_packet(now, queue_rows)

            text = artifact.read_text(encoding='utf-8')

        self.assertIn('Live third-party proof to reuse', text)
        self.assertIn('https://saashub.com/ralph-workflow', text)
        self.assertIn('https://toolwise.ai/tools/ralph-workflow', text)

    def test_measurement_hold_refreshes_stale_handoff_packets_when_live_listing_proof_is_missing(self):
        now = datetime(2026, 5, 24, 10, 59, 0)
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
            adoption_path = log_dir / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')
            backlink_path = log_dir / 'backlink_status_latest.json'
            backlink_path.write_text(json.dumps({
                'directories': {
                    'SaaSHub': {'listing_live': True, 'listing_url': 'https://saashub.com/ralph-workflow'},
                    'ToolWise': {'listing_live': True, 'listing_url': 'https://toolwise.ai/tools/ralph-workflow'},
                }
            }), encoding='utf-8')
            (log_dir / 'marketing_2026-05-24_measurement_hold_execution.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T10:30:00',
                    'chosen_action': {'type': 'measurement_hold_execution'},
                    'why_this_action': {'summary': 'Existing short review window hold.'},
                    'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
                }),
                encoding='utf-8',
            )
            (log_dir / 'curator_outreach_queue_latest.json').write_text(json.dumps({
                'targets': [
                    {
                        'target': 'AI Resources',
                        'status': 'prepared',
                        'priority': 'HIGH',
                        'url': 'https://airesources.dev/category/agents/',
                        'review_due_date': '2026-06-07',
                        'artifact_path': '/tmp/ai-resources.md',
                        'action': 'Submit PR or request inclusion with a Codeberg-primary entry',
                    }
                ]
            }), encoding='utf-8')
            (log_dir / 'comparison_backlink_queue_latest.json').write_text(json.dumps({
                'targets': [
                    {
                        'slug': 'aider',
                        'name': 'Aider',
                        'status': 'prepared',
                        'comparison_path': '/tmp/aider.md',
                        'review_due_date': '2026-06-05',
                        'artifact_path': '/tmp/aider-outreach.md',
                    }
                ]
            }), encoding='utf-8')
            (drafts_dir / 'curator_handoff_packet_latest.md').write_text(
                '# Ralph Workflow Curator Execution Handoff Packet\n\n### 1. AI Resources\n',
                encoding='utf-8',
            )
            (drafts_dir / 'comparison_backlink_handoff_packet_latest.md').write_text(
                '# Ralph Workflow Comparison Backlink Execution Handoff Packet\n\n### 1. Aider\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'BACKLINK_STATUS_LATEST_PATH', backlink_path), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', log_dir / 'comparison_backlink_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=None), \
                 patch.object(distribution_lane_executor.subprocess, 'run') as mock_run:
                mock_run.return_value.returncode = 1
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            curator_text = (drafts_dir / 'curator_handoff_packet_latest.md').read_text(encoding='utf-8')
            comparison_text = (drafts_dir / 'comparison_backlink_handoff_packet_latest.md').read_text(encoding='utf-8')
            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')

        self.assertEqual(execution.action_type, 'measurement_hold_follow_through')
        self.assertIn('Refreshed stale manual execution packets', execution.summary)
        self.assertIn('https://saashub.com/ralph-workflow', curator_text)
        self.assertIn('https://toolwise.ai/tools/ralph-workflow', comparison_text)
        self.assertIn('curator handoff packet', artifact_text)
        self.assertIn('comparison handoff packet', artifact_text)

    def test_execution_board_skips_already_delivered_curator_manual_contact_packet(self):
        now = datetime(2026, 5, 24, 10, 40, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            (log_dir / 'curator_outreach_queue_latest.json').write_text(
                json.dumps({
                    'targets': [
                        {
                            'target': 'vivy-yi/awesome-agent-orchestration',
                            'status': 'email_invalid_manual_handoff_remaining',
                            'priority': 'HIGH',
                        }
                    ]
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'curator_contact_handoff_packet_latest.md').write_text('# packet\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_curator_contact_handoff_packet_execution.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-23T21:46:39.701664',
                    'chosen_action': {'type': 'curator_contact_handoff_packet_execution'},
                    'why_this_action': {'targets_prepared': ['vivy-yi/awesome-agent-orchestration']},
                    'result': {'status': 'prepared', 'ok': True},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', log_dir / 'comparison_backlink_queue_latest.json'):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertNotIn('Curator manual-contact packet', board_text)

    def test_execution_board_surfaces_scheduled_stackoverflow_run(self):
        now = datetime(2026, 5, 24, 10, 40, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            (log_dir / 'stackoverflow_answer_lane_latest.json').write_text(
                json.dumps({
                    'cooldown_active': True,
                    'next_retry_at': '2026-05-24T11:24:37.256862',
                    'top_questions': [
                        {
                            'title': 'How should I structure autonomous AI agent workflows for production reliability in a TypeScript/Next.js fintech platform?',
                            'url': 'https://stackoverflow.com/questions/79942291/how-should-i-structure-autonomous-ai-agent-workflows-for-production-reliability',
                        }
                    ],
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'stackoverflow_answer_handoff_packet_latest.md').write_text('# packet\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-24_stackoverflow_quota_guard_repair.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T10:37:07+02:00',
                    'type': 'stack_overflow_lane_repair',
                    'review_window': {'scheduled_run_at': '2026-05-24T11:30:00+02:00'},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'STACKOVERFLOW_LATEST_PATH', log_dir / 'stackoverflow_answer_lane_latest.json'), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', log_dir / 'comparison_backlink_queue_latest.json'):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertIn('Scheduled for 2026-05-24T11:30:00+02:00', board_text)
        self.assertNotIn('After 2026-05-24T11:24:37.256862', board_text)

    def test_execution_board_hides_exhausted_stackoverflow_packet(self):
        now = datetime(2026, 5, 24, 11, 50, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            (log_dir / 'stackoverflow_answer_lane_latest.json').write_text(
                json.dumps({
                    'generated_at': '2026-05-24T11:48:19+02:00',
                    'cooldown_active': False,
                    'drafts_created': 0,
                    'reused_existing_draft': {
                        'question_title': 'How should I structure autonomous AI agent workflows for production reliability in a TypeScript/Next.js fintech platform?',
                        'question_url': 'https://stackoverflow.com/questions/79942291/how-should-i-structure-autonomous-ai-agent-workflows-for-production-reliability',
                    },
                    'top_questions': [
                        {
                            'title': 'How should I structure autonomous AI agent workflows for production reliability in a TypeScript/Next.js fintech platform?',
                            'url': 'https://stackoverflow.com/questions/79942291/how-should-i-structure-autonomous-ai-agent-workflows-for-production-reliability',
                        }
                    ],
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'stackoverflow_answer_handoff_packet_latest.md').write_text('# packet\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-24_stackoverflow_post_cooldown_cron.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T08:48:19+02:00',
                    'type': 'stackoverflow_post_cooldown_cron',
                    'status': 'scheduled',
                    'scheduled_run_at': '2026-05-24T11:30:00+02:00',
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'STACKOVERFLOW_LATEST_PATH', log_dir / 'stackoverflow_answer_lane_latest.json'), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', log_dir / 'comparison_backlink_queue_latest.json'), \
                 patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', log_dir / 'stackoverflow_answer_lane_latest.json'):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertNotIn('### 1. StackOverflow demand-capture packet', board_text)
        self.assertIn('StackOverflow demand-capture packet is exhausted for this review window', board_text)

    def test_stackoverflow_execution_counts_reused_existing_draft_as_prepared(self):
        now = datetime(2026, 5, 24, 11, 25, 0)
        decision = LaneDecision(
            lane='stackoverflow_answer',
            reason='Use StackOverflow demand capture.',
            reasons=['cooldown cleared'],
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
            latest_path = log_dir / 'stackoverflow_answer_lane_latest.json'
            latest_path.write_text(json.dumps({
                'generated_at': '2026-05-24T11:24:56.176949',
                'drafts_created': 0,
                'drafts': [],
                'reused_existing_draft': {
                    'question_title': 'How should I structure autonomous AI agent workflows for production reliability in a TypeScript/Next.js fintech platform?',
                    'question_url': 'https://stackoverflow.com/questions/79942291/how-should-i-structure-autonomous-ai-agent-workflows-for-production-reliability',
                    'draft_file': '/tmp/so_answer.md',
                    'packet_file': '/tmp/stackoverflow_answer_handoff_packet_latest.md',
                },
                'top_questions': [
                    {
                        'title': 'How should I structure autonomous AI agent workflows for production reliability in a TypeScript/Next.js fintech platform?',
                        'url': 'https://stackoverflow.com/questions/79942291/how-should-i-structure-autonomous-ai-agent-workflows-for-production-reliability',
                    }
                ],
            }), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'STACKOVERFLOW_LATEST_PATH', latest_path), \
                 patch.object(distribution_lane_executor.stackoverflow_answer_lane, 'main', return_value=0):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')

        self.assertEqual(execution.status, 'prepared')
        self.assertIn('manual-ready', execution.summary.lower())
        self.assertIn('How should I structure autonomous AI agent workflows for production reliability in a TypeScript/Next.js fintech platform?', execution.targets_prepared)
        self.assertEqual(execution.blocking_factors, [])
        self.assertIn('Reused manual-ready draft', artifact_text)


if __name__ == '__main__':
    unittest.main()
