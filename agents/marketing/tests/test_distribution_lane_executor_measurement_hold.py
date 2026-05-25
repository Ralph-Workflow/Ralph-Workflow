import json
import tempfile
import unittest
from types import SimpleNamespace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agents.marketing import distribution_lane_selector
from agents.marketing.distribution_lane_selector import LaneDecision
from agents.marketing import distribution_lane_executor


class DistributionLaneExecutorMeasurementHoldTests(unittest.TestCase):
    def test_execution_board_blocks_primary_repo_flat_packet_for_contact_page_only_target(self):
        now = datetime(2026, 5, 24, 15, 10, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            (log_dir / 'primary_repo_flat_contact_discovery_latest.json').write_text(
                json.dumps({
                    'targets': [
                        {
                            'target': 'ctxt.dev / Signum',
                            'channels': [
                                {'type': 'website', 'value': 'https://ctxt.dev/contact', 'label': 'common contact/about path'},
                                {'type': 'telegram', 'value': 'https://t.me/ctxtdev', 'label': 'Telegram'},
                            ],
                        }
                    ]
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md').write_text('# publisher packet\n\n### 1. AXME Code\n', encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertNotIn('### 1. Primary-repo-flat publisher contact packet', board_text)
        self.assertIn('Remaining publisher-contact discovery is not runtime-sendable here: ctxt.dev / Signum.', board_text)

    def test_execution_board_hides_stale_primary_repo_flat_packet_until_refreshed(self):
        now = datetime(2026, 5, 25, 0, 20, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            (log_dir / 'primary_repo_flat_contact_discovery_latest.json').write_text(
                json.dumps({
                    'targets': [
                        {
                            'target': 'ToolChase',
                            'channels': [{'type': 'email', 'value': 'hello@toolchase.com'}],
                        }
                    ]
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md').write_text(
                '# stale packet\n\n### 1. AXME Code\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertNotIn('### 1. Primary-repo-flat publisher contact packet', board_text)
        self.assertIn('canonical handoff packet is stale', board_text)

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

    def test_measurement_hold_follow_through_schedules_post_hold_rerun(self):
        now = datetime(2026, 5, 24, 21, 57, 0)
        decision = LaneDecision(
            lane='measurement_hold',
            reason='Hold for truthful re-entry after the short review window clears.',
            reasons=['multiple fresh external actions already overlap'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='',
            short_review_window_release_at='2026-05-25T02:05:05',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            (log_dir / 'distribution_lane_latest.json').write_text(
                json.dumps({'short_review_window_release_at': '2026-05-25T02:05:05'}),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-24_measurement_hold_execution.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T21:37:00',
                    'chosen_action': {'type': 'measurement_hold_execution'},
                    'why_this_action': {'summary': 'Existing short review window hold.'},
                    'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=None), \
                 patch.object(distribution_lane_executor.subprocess, 'run') as mock_run:
                state = {'added': False}

                def fake_run(command, *args, **kwargs):
                    if command[:3] == ['openclaw', 'cron', 'list']:
                        jobs = [] if not state['added'] else [{
                            'id': 'cron-123',
                            'name': 'marketing-measurement-hold-release',
                            'enabled': True,
                            'schedule': {'kind': 'at', 'at': '2026-05-25T02:05:05'},
                        }]
                        return SimpleNamespace(returncode=0, stdout=json.dumps({'jobs': jobs}), stderr='')
                    if command[:3] == ['openclaw', 'cron', 'add']:
                        state['added'] = True
                        return SimpleNamespace(returncode=0, stdout=json.dumps({'job': {'id': 'cron-123', 'name': 'marketing-measurement-hold-release'}}), stderr='')
                    if command[:3] == ['openclaw', 'cron', 'show']:
                        return SimpleNamespace(returncode=1, stdout='{}', stderr='')
                    return SimpleNamespace(returncode=1, stdout='{}', stderr='')

                mock_run.side_effect = fake_run
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            board_text = board_path.read_text(encoding='utf-8')
            cron_log = json.loads((log_dir / 'marketing_2026-05-24_215700_measurement_hold_release_cron.json').read_text(encoding='utf-8'))
            reentry_contract = (drafts_dir / 'post_hold_distribution_reentry_latest.md').read_text(encoding='utf-8')

        self.assertEqual(execution.action_type, 'measurement_hold_follow_through')
        self.assertIn('Post-hold marketer rerun scheduled', artifact_text)
        self.assertIn('Post-hold re-entry contract', artifact_text)
        self.assertIn('2026-05-25T02:05:05', artifact_text)
        self.assertIn('cron-123', artifact_text)
        self.assertIn('Short review-window congestion clears at: 2026-05-25T02:05:05', board_text)
        self.assertIn('Post-hold marketer rerun scheduled: 2026-05-25T02:05:05', board_text)
        self.assertIn('distribution_architecture_repair instead of another measurement_hold', reentry_contract)
        self.assertEqual(cron_log['type'], 'measurement_hold_release_cron')
        self.assertEqual(cron_log['cron_job']['id'], 'cron-123')
        self.assertEqual(
            cron_log['verification']['reentry_contract_path'],
            str(drafts_dir / 'post_hold_distribution_reentry_latest.md'),
        )

    def test_repeated_measurement_hold_with_existing_repairs_escalates_to_churn_guard(self):
        now = datetime(2026, 5, 25, 0, 43, 0)
        decision = LaneDecision(
            lane='measurement_hold',
            reason='Hold while overlapping review windows clear.',
            reasons=['fresh external actions already shipped'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='',
            short_review_window_release_at='2026-05-25T02:05:05',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            active_hold = {
                'hold_started_at': datetime(2026, 5, 24, 22, 4, 30),
                'hold_until': datetime(2026, 5, 25, 2, 5, 5),
                'source_log': str(log_dir / 'marketing_2026-05-24_220430_measurement_hold_execution.json'),
            }
            repeated_logs = [
                ('marketing_2026-05-24_220430_measurement_hold_execution.json', '2026-05-24T22:04:30.752497', 'measurement_hold_execution'),
                ('marketing_2026-05-24_220547_measurement_hold_follow_through.json', '2026-05-24T22:05:47.263709', 'measurement_hold_follow_through'),
                ('marketing_2026-05-24_221832_measurement_hold_follow_through.json', '2026-05-24T22:18:32.431437', 'measurement_hold_follow_through'),
                ('marketing_2026-05-24_234934_active_loop_prompt_repair.json', '2026-05-24T23:49:34.370467', 'active_loop_prompt_repair'),
                ('marketing_2026-05-24_235759_post_hold_reentry_contract_repair.json', '2026-05-24T23:57:59.977354+02:00', 'post_hold_reentry_contract_repair'),
                ('marketing_2026-05-25_000001_measurement_hold_release_cron.json', '2026-05-25T00:00:01+02:00', 'measurement_hold_release_cron'),
            ]
            for filename, timestamp, action_type in repeated_logs:
                (log_dir / filename).write_text(
                    json.dumps({
                        'timestamp': timestamp,
                        'chosen_action': {'type': action_type},
                        'review_window': {'scheduled_run_at': '2026-05-25T02:05:05'} if action_type == 'measurement_hold_release_cron' else {},
                        'result': {'status': 'executed', 'ok': True, 'live_external_action': False},
                    }),
                    encoding='utf-8',
                )

            (log_dir / 'distribution_lane_latest.json').write_text(
                json.dumps({'short_review_window_release_at': '2026-05-25T02:05:05'}),
                encoding='utf-8',
            )
            (log_dir / 'apollo_sequence_status_latest.json').write_text(json.dumps({}), encoding='utf-8')
            (log_dir / 'curator_outreach_queue_latest.json').write_text(json.dumps({'targets': []}), encoding='utf-8')
            (log_dir / 'comparison_backlink_queue_latest.json').write_text(json.dumps({'targets': []}), encoding='utf-8')
            (log_dir / 'primary_repo_flat_contact_discovery_latest.json').write_text(json.dumps({'targets': []}), encoding='utf-8')
            (log_dir / 'curator_contact_discovery_latest.json').write_text(json.dumps({'targets': []}), encoding='utf-8')
            (drafts_dir / 'stackoverflow_answer_handoff_packet_latest.md').write_text('# packet\n', encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', log_dir / 'comparison_backlink_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'), \
                 patch.object(distribution_lane_executor, 'CURATOR_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'curator_contact_discovery_latest.json'), \
                 patch.object(distribution_lane_executor, 'latest_measurement_hold_window', return_value=active_hold), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=None), \
                 patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True), \
                 patch.object(distribution_lane_executor.subprocess, 'run') as mock_run:
                mock_run.return_value.returncode = 1
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')

        self.assertEqual(execution.action_type, 'measurement_hold_churn_guard_repair')
        self.assertEqual(execution.status, 'executed')
        self.assertIn('Repeat-hold churn guard now active', artifact_text)
        self.assertIn('prompt and post-hold re-entry repairs are already in place', artifact_text)
        self.assertIn('suppress duplicate follow-through', execution.summary)

    def test_measurement_hold_churn_guard_uses_global_repairs_and_escalates_on_third_event(self):
        now = datetime(2026, 5, 25, 3, 11, 0)
        decision = LaneDecision(
            lane='measurement_hold',
            reason='Hold for truthful re-entry after the short review window clears.',
            reasons=['multiple fresh external actions already overlap'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='',
            short_review_window_release_at='2026-05-25T07:20:16',
        )
        active_hold = {
            'hold_started_at': datetime(2026, 5, 25, 1, 47, 40, 177303),
            'hold_until': datetime(2026, 5, 25, 7, 20, 16),
            'source_log': '/tmp/hold.json',
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            repeated_logs = [
                ('marketing_2026-05-25_014740_measurement_hold_execution.json', '2026-05-25T01:47:40.177303', 'measurement_hold_execution'),
                ('marketing_2026-05-25_023723_measurement_hold_follow_through.json', '2026-05-25T02:37:23.000000', 'measurement_hold_follow_through'),
                ('marketing_2026-05-24_234934_active_loop_prompt_repair.json', '2026-05-24T23:49:34.370467', 'active_loop_prompt_repair'),
                ('marketing_2026-05-24_235759_post_hold_reentry_contract_repair.json', '2026-05-24T23:57:59.977354+02:00', 'post_hold_reentry_contract_repair'),
                ('marketing_2026-05-25_023723_measurement_hold_release_cron.json', '2026-05-25T02:37:23.000000', 'measurement_hold_release_cron'),
            ]
            for filename, timestamp, action_type in repeated_logs:
                (log_dir / filename).write_text(
                    json.dumps({
                        'timestamp': timestamp,
                        'chosen_action': {'type': action_type},
                        'review_window': {'scheduled_run_at': '2026-05-25T07:20:16'} if action_type == 'measurement_hold_release_cron' else {},
                        'result': {'status': 'executed', 'ok': True, 'live_external_action': False},
                    }),
                    encoding='utf-8',
                )

            (log_dir / 'distribution_lane_latest.json').write_text(
                json.dumps({'short_review_window_release_at': '2026-05-25T07:20:16'}),
                encoding='utf-8',
            )
            (log_dir / 'apollo_sequence_status_latest.json').write_text(json.dumps({}), encoding='utf-8')
            (log_dir / 'curator_outreach_queue_latest.json').write_text(json.dumps({'targets': []}), encoding='utf-8')
            (log_dir / 'comparison_backlink_queue_latest.json').write_text(json.dumps({'targets': []}), encoding='utf-8')
            (log_dir / 'primary_repo_flat_contact_discovery_latest.json').write_text(json.dumps({'targets': []}), encoding='utf-8')
            (log_dir / 'curator_contact_discovery_latest.json').write_text(json.dumps({'targets': []}), encoding='utf-8')
            (drafts_dir / 'stackoverflow_answer_handoff_packet_latest.md').write_text('# packet\n', encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', log_dir / 'comparison_backlink_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'), \
                 patch.object(distribution_lane_executor, 'CURATOR_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'curator_contact_discovery_latest.json'), \
                 patch.object(distribution_lane_executor, 'latest_measurement_hold_window', return_value=active_hold), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=None), \
                 patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True), \
                 patch.object(distribution_lane_executor.subprocess, 'run') as mock_run:
                mock_run.return_value.returncode = 1
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')

        self.assertEqual(execution.action_type, 'measurement_hold_churn_guard_repair')
        self.assertIn('hold event #3', artifact_text)
        self.assertIn('already in place for this hold cycle', artifact_text)
        self.assertIn('third hold-window event', execution.summary)

    def test_measurement_hold_execution_schedules_post_hold_rerun(self):
        now = datetime(2026, 5, 24, 21, 57, 0)
        decision = LaneDecision(
            lane='measurement_hold',
            reason='Hold for truthful re-entry after the short review window clears.',
            reasons=['multiple fresh external actions already overlap'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='',
            short_review_window_release_at='2026-05-25T02:05:05',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            (log_dir / 'distribution_lane_latest.json').write_text(
                json.dumps({'short_review_window_release_at': '2026-05-25T02:05:05'}),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=None), \
                 patch.object(distribution_lane_executor.subprocess, 'run') as mock_run:
                state = {'added': False}

                def fake_run(command, *args, **kwargs):
                    if command[:3] == ['openclaw', 'cron', 'list']:
                        jobs = [] if not state['added'] else [{
                            'id': 'cron-first-hold',
                            'name': 'marketing-measurement-hold-release',
                            'enabled': True,
                            'schedule': {'kind': 'at', 'at': '2026-05-25T02:05:05'},
                        }]
                        return SimpleNamespace(returncode=0, stdout=json.dumps({'jobs': jobs}), stderr='')
                    if command[:3] == ['openclaw', 'cron', 'add']:
                        state['added'] = True
                        return SimpleNamespace(returncode=0, stdout=json.dumps({'job': {'id': 'cron-first-hold', 'name': 'marketing-measurement-hold-release'}}), stderr='')
                    if command[:3] == ['openclaw', 'cron', 'show']:
                        return SimpleNamespace(returncode=1, stdout='{}', stderr='')
                    return SimpleNamespace(returncode=1, stdout='{}', stderr='')

                mock_run.side_effect = fake_run
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            board_text = board_path.read_text(encoding='utf-8')
            cron_log = json.loads((log_dir / 'marketing_2026-05-24_215700_measurement_hold_release_cron.json').read_text(encoding='utf-8'))
            reentry_contract = (drafts_dir / 'post_hold_distribution_reentry_latest.md').read_text(encoding='utf-8')

        self.assertEqual(execution.action_type, 'measurement_hold_execution')
        self.assertIn('Post-hold marketer rerun scheduled', artifact_text)
        self.assertIn('Post-hold re-entry contract', artifact_text)
        self.assertIn('cron-first-hold', artifact_text)
        self.assertIn('Short review-window congestion clears at: 2026-05-25T02:05:05', artifact_text)
        self.assertIn('Post-hold marketer rerun scheduled: 2026-05-25T02:05:05', board_text)
        self.assertIn('Treat another idle hold as a process failure.', reentry_contract)
        self.assertEqual(cron_log['cron_job']['id'], 'cron-first-hold')

    def test_measurement_hold_scheduler_removes_stale_live_release_job_before_adding_new_one(self):
        now = datetime(2026, 5, 25, 2, 37, 23)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor.subprocess, 'run') as mock_run:
                mock_run.side_effect = [
                    SimpleNamespace(returncode=0, stdout=json.dumps({'jobs': [
                        {
                            'id': 'stale-cron',
                            'name': 'marketing-measurement-hold-release',
                            'enabled': True,
                            'schedule': {'kind': 'at', 'at': '2026-05-25T04:05:05'},
                        }
                    ]}), stderr=''),
                    SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True}), stderr=''),
                    SimpleNamespace(returncode=0, stdout=json.dumps({'jobs': []}), stderr=''),
                    SimpleNamespace(returncode=0, stdout=json.dumps({'job': {'id': 'fresh-cron', 'name': 'marketing-measurement-hold-release'}}), stderr=''),
                ]

                schedule = distribution_lane_executor._schedule_measurement_hold_release_run(
                    now=now,
                    release_at='2026-05-25T07:20:16',
                    shared_findings_used=['adoption_metrics_latest.json'],
                    reentry_contract_path=str(drafts_dir / 'post_hold_distribution_reentry_latest.md'),
                )

            cron_log = json.loads((log_dir / 'marketing_2026-05-25_023723_measurement_hold_release_cron.json').read_text(encoding='utf-8'))

        self.assertEqual(schedule['status'], 'scheduled')
        self.assertEqual(schedule['job_id'], 'fresh-cron')
        self.assertEqual(schedule['removed_stale_jobs'][0]['job_id'], 'stale-cron')
        self.assertEqual(cron_log['cleanup']['removed_stale_jobs'][0]['job_id'], 'stale-cron')

    def test_measurement_hold_follow_through_does_not_resurface_stackoverflow_packet_when_post_cooldown_run_is_already_scheduled(self):
        now = datetime(2026, 5, 24, 5, 20, 0)
        decision = LaneDecision(
            lane='measurement_hold',
            reason='Hold for follow-through.',
            reasons=['scheduled StackOverflow retry already exists'],
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
            (log_dir / 'marketing_2026-05-24_measurement_hold_execution.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T04:51:00',
                    'chosen_action': {'type': 'measurement_hold_execution'},
                    'why_this_action': {'summary': 'Existing short review window hold.'},
                    'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
                }),
                encoding='utf-8',
            )
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
            (log_dir / 'marketing_2026-05-24_stackoverflow_post_cooldown_cron.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T08:47:43+02:00',
                    'type': 'stack_overflow_demand_capture_cron',
                    'status': 'scheduled',
                    'verification': {'scheduled_run_at': '2026-05-24T11:30:00+02:00'},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'STACKOVERFLOW_LATEST_PATH', log_dir / 'stackoverflow_answer_lane_latest.json'), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', log_dir / 'comparison_backlink_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=None), \
                 patch.object(distribution_lane_executor.subprocess, 'run') as mock_run:
                mock_run.return_value.returncode = 1
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')

        self.assertEqual(execution.action_type, 'measurement_hold_follow_through')
        self.assertNotIn('StackOverflow handoff asset', execution.summary)
        self.assertIn('StackOverflow demand-capture follow-through already scheduled', artifact_text)
        self.assertIn('2026-05-24T11:30:00+02:00', artifact_text)
        self.assertNotIn('Best human-executable demand-capture asset still waiting', artifact_text)


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
        self.assertNotIn('## Execute these first', artifact_text)
        self.assertNotIn('Ready-to-send email draft', artifact_text)
        self.assertNotIn('Short contact-form version', artifact_text)

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

    def test_primary_repo_flat_packet_marks_active_review_window_as_reference_only(self):
        now = datetime(2026, 5, 24, 19, 20, 0)
        findings = [
            {
                'target': 'ToolChase',
                'article_url': 'https://toolchase.com/blog/best-ai-coding-tools-2026/',
                'root_url': 'https://toolchase.com/',
                'hook': 'AI coding tools comparison page already covering Claude Code, Codex, Cursor, and Aider',
                'reason': 'Direct comparison/discovery audience already evaluating AI coding tools and adjacent workflow choices.',
                'outreach_subject': 'Ralph Workflow for your next AI coding tools comparison refresh',
                'recommended_next_step': 'email/contact send path is now identified',
                'channels': [
                    {'type': 'email', 'value': 'hello@toolchase.com'},
                    {'type': 'website', 'value': 'https://toolchase.com/contact', 'label': 'contact page'},
                ],
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            drafts_dir.mkdir()
            log_dir.mkdir()
            (drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md').write_text(
                '# Ralph Workflow Primary-Repo-Flat Publisher Contact Packet\n\n'
                '## Live third-party proof to reuse\n'
                '- SaaSHub — https://saashub.com/ralph-workflow\n\n'
                '### 1. ToolChase\n',
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-24_primary_repo_flat_delivery.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T18:18:26',
                    'action_type': 'primary_repo_flat_contact_manual_delivery',
                    'chosen_action': {
                        'packet': str(drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'),
                    },
                    'measurement_window': {
                        'review_at': '2026-05-31T18:18:26',
                    },
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, '_latest_research_signals', return_value=[]), \
                 patch.object(distribution_lane_executor, '_append_live_listing_proof', return_value=None):
                artifact, _prepared = distribution_lane_executor._write_primary_repo_flat_contact_handoff_packet(now, findings)

            text = artifact.read_text(encoding='utf-8')

        self.assertIn('This packet was already delivered in the current review window.', text)
        self.assertIn('## Reference targets already covered in the active review window', text)
        self.assertIn('Another manual delivery right now would be fake progress', text)

    def test_comparison_packet_marks_active_review_window_as_reference_only(self):
        now = datetime(2026, 5, 24, 19, 21, 0)
        queue_rows = [
            {
                'slug': 'aider',
                'name': 'Aider',
                'status': 'prepared',
                'comparison_path': '/tmp/aider.md',
                'review_due_date': '2026-06-05',
                'artifact_path': '/tmp/packet.md',
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            drafts_dir.mkdir()
            log_dir.mkdir()
            (log_dir / 'marketing_2026-05-24_comparison_delivery.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T10:57:00',
                    'action_type': 'comparison_backlink_manual_delivery',
                    'measurement_window': {
                        'review_at': '2026-05-31T10:57:00',
                    },
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, '_append_live_listing_proof', return_value=None):
                artifact, _prepared = distribution_lane_executor._write_comparison_handoff_packet(now, queue_rows)

            text = artifact.read_text(encoding='utf-8')

        self.assertIn('This packet was already manually delivered in the current review window.', text)
        self.assertIn('## Reference targets already covered in the active review window', text)
        self.assertIn('Another manual delivery right now would be fake progress', text)

    def test_curator_handoff_packet_marks_active_pause_as_reference_only(self):
        now = datetime(2026, 5, 24, 19, 22, 0)
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

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, '_append_live_listing_proof', return_value=None), \
                 patch.object(distribution_lane_executor.distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, True)):
                artifact, _prepared = distribution_lane_executor._write_curator_handoff_packet(now, queue_rows)

            text = artifact.read_text(encoding='utf-8')

        self.assertIn('Same-family curator outreach is paused in the active repair window.', text)
        self.assertIn('## Reference targets currently paused by the active repair window', text)
        self.assertIn('Another curator delivery right now would be fake progress', text)

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

    def test_measurement_hold_rewrites_execution_board_after_primary_repo_flat_packet_refresh(self):
        now = datetime(2026, 5, 25, 2, 51, 0)
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

            (log_dir / 'adoption_metrics_latest.json').write_text(
                json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}),
                encoding='utf-8',
            )
            (log_dir / 'backlink_status_latest.json').write_text(
                json.dumps({
                    'directories': {
                        'SaaSHub': {'listing_live': True, 'listing_url': 'https://saashub.com/ralph-workflow'},
                        'ToolWise': {'listing_live': True, 'listing_url': 'https://toolwise.ai/tools/ralph-workflow'},
                    }
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-25_measurement_hold_execution.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T02:30:00',
                    'chosen_action': {'type': 'measurement_hold_execution'},
                    'why_this_action': {'summary': 'Existing short review window hold.'},
                    'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
                }),
                encoding='utf-8',
            )
            (log_dir / 'distribution_lane_latest.json').write_text(
                json.dumps({'short_review_window_release_at': '2026-05-25T07:20:16'}),
                encoding='utf-8',
            )
            (log_dir / 'curator_outreach_queue_latest.json').write_text(json.dumps({'targets': []}), encoding='utf-8')
            (log_dir / 'comparison_backlink_queue_latest.json').write_text(json.dumps({'targets': []}), encoding='utf-8')
            (log_dir / 'primary_repo_flat_contact_discovery_latest.json').write_text(
                json.dumps({
                    'targets': [
                        {
                            'target': 'Beam',
                            'article_url': 'https://getbeam.dev/blog/ai-coding-agents-comparison-2026.html',
                            'root_url': 'https://getbeam.dev/',
                            'hook': 'Claude Code vs Cursor vs Codex comparison for terminal-first builders',
                            'reason': 'Highly adjacent audience already thinking about agent autonomy, workflow fit, and terminal-native execution.',
                            'outreach_subject': 'Ralph Workflow as a workflow-system reference for your coding agents comparison',
                            'channels': [
                                {'type': 'email', 'value': 'frank@nextuptechnologies.co', 'label': 'email'}
                            ],
                            'recommended_next_step': 'email/contact send path is now identified',
                        }
                    ]
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md').write_text(
                '# Ralph Workflow Primary-Repo-Flat Publisher Contact Packet\n\n### 1. Beam\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', log_dir / 'adoption_metrics_latest.json'), \
                 patch.object(distribution_lane_executor, 'BACKLINK_STATUS_LATEST_PATH', log_dir / 'backlink_status_latest.json'), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', log_dir / 'comparison_backlink_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=None), \
                 patch.object(distribution_lane_executor.subprocess, 'run') as mock_run:
                mock_run.return_value.returncode = 1
                distribution_lane_executor.execute_distribution_lane(decision, now)

            board_text = (drafts_dir / 'marketing_execution_board_latest.md').read_text(encoding='utf-8')

        self.assertIn('A refreshed primary-repo-flat publisher packet now exists for the new target set, but the short review window is still active', board_text)
        self.assertNotIn('Primary-repo-flat publisher discovery has changed, but the canonical handoff packet is stale', board_text)

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

        self.assertNotIn('### 1. Curator manual-contact packet', board_text)

    def test_execution_board_hides_primary_repo_flat_packet_after_manual_delivery_in_active_review_window(self):
        now = datetime(2026, 5, 24, 14, 21, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            (log_dir / 'primary_repo_flat_contact_discovery_latest.json').write_text(
                json.dumps({
                    'targets': [
                        {
                            'target': 'AXME Code',
                            'channels': [{'type': 'email', 'value': 'contact@axme.ai'}],
                        }
                    ]
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md').write_text('# publisher packet\n\n### 1. AXME Code\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-24_primary_repo_flat_contact_manual_delivery.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T07:33:00+02:00',
                    'chosen_action': {
                        'type': 'primary_repo_flat_contact_manual_delivery',
                        'packet': str(drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'),
                    },
                    'measurement_window': {'review_at': '2026-05-31T07:33:00+02:00'},
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertNotIn('### 1. Primary-repo-flat publisher contact packet', board_text)
        self.assertIn('Primary-repo-flat publisher contact packet was already manually delivered in the current review window', board_text)

    def test_execution_board_hides_comparison_packet_after_manual_delivery_in_active_review_window(self):
        now = datetime(2026, 5, 24, 14, 21, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            (log_dir / 'comparison_backlink_queue_latest.json').write_text(
                json.dumps({
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
            (drafts_dir / 'comparison_backlink_handoff_packet_latest.md').write_text(
                '# Ralph Workflow Comparison Backlink Execution Handoff Packet\n\n### 1. Aider\n',
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-24_comparison_backlink_manual_delivery.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T06:06:00+02:00',
                    'chosen_action': {'type': 'comparison_backlink_manual_delivery'},
                    'measurement_window': {'review_at': '2026-05-31T06:06:00+02:00'},
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', log_dir / 'comparison_backlink_queue_latest.json'):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertNotIn('### 1. Comparison backlink packet', board_text)
        self.assertIn('Comparison backlink packet was already manually delivered in the current review window', board_text)

    def test_execution_board_hides_curator_handoff_while_faster_packets_are_still_waiting(self):
        now = datetime(2026, 5, 24, 10, 40, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            (log_dir / 'primary_repo_flat_contact_discovery_latest.json').write_text(
                json.dumps({
                    'generated_at': '2026-05-24T10:30:00+02:00',
                    'targets': [
                        {
                            'target': 'PrimeDev.Tools',
                            'channels': [{'type': 'email', 'value': 'hello@primedev.tools'}],
                        }
                    ],
                }),
                encoding='utf-8',
            )
            (log_dir / 'curator_outreach_queue_latest.json').write_text(
                json.dumps({
                    'targets': [
                        {
                            'target': 'AI Resources',
                            'status': 'prepared',
                            'priority': 'HIGH',
                        }
                    ]
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md').write_text('# publisher packet\n\n### 1. PrimeDev.Tools\n', encoding='utf-8')
            (drafts_dir / 'curator_handoff_packet_latest.md').write_text('# curator packet\n', encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', log_dir / 'comparison_backlink_queue_latest.json'):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertIn('Primary-repo-flat publisher contact packet', board_text)
        self.assertNotIn('Curator handoff packet', board_text)

    def test_execution_board_hides_curator_handoff_during_same_family_outreach_hold(self):
        now = datetime(2026, 5, 24, 14, 11, 30)

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
                            'target': 'AI Resources',
                            'status': 'prepared',
                            'priority': 'HIGH',
                        }
                    ]
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'curator_handoff_packet_latest.md').write_text('# curator packet\n', encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', log_dir / 'comparison_backlink_queue_latest.json'), \
                 patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, True)):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertIn('Same-family curator outreach is paused', board_text)
        self.assertNotIn('### 1. Curator handoff packet', board_text)

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
            (log_dir / 'marketing_2026-05-24_stackoverflow_post_cooldown_cron.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T10:37:07+02:00',
                    'type': 'stack_overflow_demand_capture_cron',
                    'status': 'scheduled',
                    'verification': {'scheduled_run_at': '2026-05-24T11:30:00+02:00'},
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

    def test_execution_board_hides_stale_stackoverflow_packet_when_latest_run_has_no_manual_ready_asset(self):
        now = datetime(2026, 5, 24, 17, 47, 16)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            (log_dir / 'apollo_sequence_status_latest.json').write_text(
                json.dumps({'next_review_at': '2026-05-30T00:14:49.075391+02:00', 'launch_review_at': '2026-05-30T00:14:49.075391+02:00'}),
                encoding='utf-8',
            )
            (log_dir / 'stackoverflow_answer_lane_latest.json').write_text(
                json.dumps({
                    'generated_at': '2026-05-24T15:59:44.626446',
                    'status': 'ok',
                    'cooldown_active': False,
                    'drafts_created': 0,
                    'manual_follow_through': False,
                    'reused_existing_draft': None,
                    'top_questions': [
                        {
                            'title': 'How can I get more useful results from ai coding agents',
                            'url': 'https://stackoverflow.com/questions/79913508/how-can-i-get-more-useful-results-from-ai-coding-agents',
                        }
                    ],
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'stackoverflow_answer_handoff_packet_latest.md').write_text(
                '**Question:** How should I structure autonomous AI agent workflows for production reliability?\n**URL:** https://stackoverflow.com/questions/79942291/how-should-i-structure-autonomous-ai-agent-workflows-for-production-reliability\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'STACKOVERFLOW_LATEST_PATH', log_dir / 'stackoverflow_answer_lane_latest.json'), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', log_dir / 'comparison_backlink_queue_latest.json'), \
                 patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', log_dir / 'stackoverflow_answer_lane_latest.json'), \
                 patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=False), \
                 patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(True, True)):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertNotIn('StackOverflow demand-capture packet', board_text)
        self.assertIn('No do-now handoff packet is currently truthful in this review window.', board_text)

    def test_execution_board_explains_why_no_do_now_packet_is_truthful(self):
        now = datetime(2026, 5, 24, 14, 21, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            (log_dir / 'primary_repo_flat_contact_discovery_latest.json').write_text(
                json.dumps({
                    'targets': [
                        {
                            'target': 'ctxt.dev / Signum',
                            'channels': [{'type': 'telegram', 'value': 'https://t.me/ctxtdev'}],
                        },
                        {
                            'target': 'AXME Code',
                            'channels': [{'type': 'email', 'value': 'contact@axme.ai'}],
                        },
                    ]
                }),
                encoding='utf-8',
            )
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
            (log_dir / 'curator_outreach_queue_latest.json').write_text(
                json.dumps({
                    'targets': [
                        {
                            'target': 'vivy-yi/awesome-agent-orchestration',
                            'status': 'email_invalid_manual_handoff_remaining',
                            'priority': 'HIGH',
                        },
                        {
                            'target': 'AI Resources',
                            'status': 'prepared',
                            'priority': 'HIGH',
                        },
                    ]
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'curator_contact_handoff_packet_latest.md').write_text('# packet\n', encoding='utf-8')
            (drafts_dir / 'curator_handoff_packet_latest.md').write_text('# curator packet\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-24_curator_contact_handoff_packet_execution.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T09:00:00',
                    'chosen_action': {'type': 'curator_contact_handoff_packet_execution'},
                    'why_this_action': {'targets_prepared': ['vivy-yi/awesome-agent-orchestration']},
                    'measurement_window': {'review_at': '2026-05-31T09:00:00'},
                    'result': {'status': 'prepared', 'ok': True},
                }),
                encoding='utf-8',
            )
            (log_dir / 'comparison_backlink_queue_latest.json').write_text(
                json.dumps({
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
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'comparison_backlink_handoff_packet_latest.md').write_text('# comparison packet\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-24_comparison_backlink_manual_delivery.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T06:06:00+02:00',
                    'chosen_action': {'type': 'comparison_backlink_manual_delivery'},
                    'measurement_window': {'review_at': '2026-05-31T06:06:00+02:00'},
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )
            (log_dir / 'stackoverflow_answer_lane_latest.json').write_text(
                json.dumps({
                    'generated_at': '2026-05-24T12:47:02',
                    'cooldown_active': False,
                    'drafts_created': 0,
                    'reused_existing_draft': {
                        'question_title': 'How should I structure autonomous AI agent workflows for production reliability?',
                        'question_url': 'https://stackoverflow.com/questions/79942291/example',
                    },
                    'top_questions': [
                        {
                            'title': 'How should I structure autonomous AI agent workflows for production reliability?',
                            'url': 'https://stackoverflow.com/questions/79942291/example',
                        }
                    ],
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'stackoverflow_answer_handoff_packet_latest.md').write_text('# stackoverflow packet\n', encoding='utf-8')
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
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', log_dir / 'comparison_backlink_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'STACKOVERFLOW_LATEST_PATH', log_dir / 'stackoverflow_answer_lane_latest.json'), \
                 patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, True)), \
                 patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', log_dir / 'stackoverflow_answer_lane_latest.json'):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertIn('No do-now handoff packet is currently truthful in this review window.', board_text)
        self.assertIn('Remaining publisher-contact discovery is not runtime-sendable here: ctxt.dev / Signum.', board_text)
        self.assertIn('Fresh publisher outreach already shipped in the current review window for: AXME Code.', board_text)
        self.assertIn('Curator manual-contact packet already exists but was already delivered in the current review window', board_text)
        self.assertIn('Curator handoff packet exists, but same-family curator outreach is paused', board_text)
        self.assertIn('Comparison backlink packet exists, but it was already manually delivered in the current review window.', board_text)
        self.assertIn('StackOverflow handoff packet exists, but the post-cooldown slot already burned without a fresh placement-ready outcome.', board_text)

    def test_execution_board_surfaces_existing_manual_outreach_asset(self):
        now = datetime(2026, 5, 25, 5, 20, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            asset_path = drafts_dir / '2026-05-24_ctxtdev_publisher_outreach_ready.md'
            asset_path.write_text('# ctxt.dev / Signum publisher outreach — ready to send\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-24_ctxtdev_channel_ready_outreach.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T08:20:00+02:00',
                    'chosen_action': {
                        'type': 'ctxtdev_channel_ready_outreach_asset',
                        'channel': 'manual_contact_asset',
                        'title': 'Create a single-target channel-ready outreach draft for ctxt.dev / Signum',
                        'artifact': str(asset_path),
                    },
                    'why_this_action': {
                        'summary': 'A verified Telegram-first manual outreach asset already exists for the strongest untouched publisher target.'
                    },
                    'measurement_window': {
                        'review_at': '2026-05-31T08:20:00+02:00'
                    },
                    'result': {'status': 'executed', 'ok': True, 'live_external_action': False},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', log_dir / 'comparison_backlink_queue_latest.json'), \
                 patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(True, True)):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertIn('### 1. Manual publisher outreach asset', board_text)
        self.assertIn(str(asset_path), board_text)
        self.assertIn('ctxt.dev / Signum', board_text)
        self.assertNotIn('No do-now handoff packet is currently truthful in this review window.', board_text)

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

    def test_distribution_architecture_guard_follow_through_suppresses_duplicate_repair(self):
        now = datetime(2026, 5, 25, 4, 32, 0)
        decision = LaneDecision(
            lane='distribution_architecture_guard_follow_through',
            reason='An active churn guard already covers this same empty-board fingerprint.',
            reasons=['A third-strike distribution-architecture churn guard is already active for this same execution-board fingerprint.'],
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
            board_path = drafts_dir / 'marketing_execution_board_latest.md'
            board_path.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
            fingerprint = distribution_lane_selector.hashlib.sha1(board_path.read_text(encoding='utf-8').encode('utf-8')).hexdigest()
            for filename, timestamp, action_type in [
                ('marketing_2026-05-25_013107_distribution_architecture_repair.json', '2026-05-25T01:31:07', 'distribution_architecture_repair'),
                ('marketing_2026-05-25_020752_distribution_architecture_repair.json', '2026-05-25T02:07:52', 'distribution_architecture_repair'),
                ('marketing_2026-05-25_042100_distribution_architecture_churn_guard_repair.json', '2026-05-25T04:21:00', 'distribution_architecture_churn_guard_repair'),
            ]:
                (log_dir / filename).write_text(
                    json.dumps({
                        'timestamp': timestamp,
                        'chosen_action': {'type': action_type},
                        'verification': {'execution_board_fingerprint': fingerprint},
                        'result': {'status': 'executed', 'ok': True},
                    }),
                    encoding='utf-8',
                )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, '_write_marketing_execution_board', return_value=(board_path, [])), \
                 patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', board_path), \
                 patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 7, 20, 16)), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=None):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')

        self.assertEqual(execution.action_type, 'distribution_architecture_guard_follow_through')
        self.assertIn('suppressed another duplicate structural repair', artifact_text.lower())
        self.assertIn('third-strike churn guard', artifact_text.lower())

    def test_distribution_architecture_repair_executes_instead_of_idle_hold(self):
        now = datetime(2026, 5, 25, 9, 0, 0)
        decision = LaneDecision(
            lane='distribution_architecture_repair',
            reason='The short review window already cleared, but every truthful external/manual lane is still blocked, exhausted, or already delivered; repair the lane architecture instead of logging another idle measurement hold.',
            reasons=['Codeberg is still flat.'],
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
            (log_dir / 'distribution_lane_latest.json').write_text(json.dumps({}), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=None), \
                 patch.object(distribution_lane_executor, '_write_marketing_execution_board', return_value=(drafts_dir / 'board.md', ['ctxt.dev / Signum'])), \
                 patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None), \
                 patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={'third_strike': False, 'repeat_count': 0, 'execution_board_fingerprint': ''}):
                (drafts_dir / 'board.md').write_text('# board\n', encoding='utf-8')
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')

        self.assertEqual(execution.action_type, 'distribution_architecture_repair')
        self.assertEqual(execution.status, 'executed')
        self.assertIn('structural repair', artifact_text.lower())
        self.assertIn('ctxt.dev / Signum', artifact_text)


    def test_distribution_architecture_repair_third_strike_installs_churn_guard(self):
        now = datetime(2026, 5, 25, 4, 21, 0)
        decision = LaneDecision(
            lane='distribution_architecture_repair',
            reason='The same empty-board distribution-architecture failure already repeated twice in this short-review window; escalate the third event into a churn-guard repair instead of another plain architecture note.',
            reasons=['2 prior distribution-architecture repair run(s) already hit this same empty-board window.'],
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
            board_path = drafts_dir / 'marketing_execution_board_latest.md'
            board_path.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
            for idx, timestamp in enumerate(('2026-05-25T01:31:07', '2026-05-25T02:07:52'), start=1):
                (log_dir / f'marketing_2026-05-25_0{idx}0000_distribution_architecture_repair.json').write_text(
                    json.dumps({
                        'timestamp': timestamp,
                        'chosen_action': {'type': 'distribution_architecture_repair'},
                        'result': {'status': 'executed', 'ok': True},
                    }),
                    encoding='utf-8',
                )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, '_write_marketing_execution_board', return_value=(board_path, [])), \
                 patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', board_path), \
                 patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 7, 20, 16)), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=None):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            guard_text = (drafts_dir / 'distribution_architecture_guard_latest.md').read_text(encoding='utf-8')

        self.assertEqual(execution.action_type, 'distribution_architecture_churn_guard_repair')
        self.assertIn('third strike', artifact_text.lower())
        self.assertIn('execution-board fingerprint', artifact_text.lower())
        self.assertIn('suppress another plain distribution_architecture_repair', guard_text.lower())


if __name__ == '__main__':
    unittest.main()
