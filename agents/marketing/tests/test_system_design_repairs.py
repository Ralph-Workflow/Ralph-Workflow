from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agents.marketing import apollo_outbound_verifier, apollo_sequence_launcher, apollo_sequence_status, distribution_hunter, run


class TelegraphViewsTests(unittest.TestCase):
    def test_enrich_posts_with_views_uses_telegraph_api_for_telegraph_posts(self):
        posts = [
            {'platform': 'telegraph', 'url': 'https://telegra.ph/Test-Post-05-25'},
            {'platform': 'write.as', 'url': 'https://write.as/test'},
        ]
        with patch.object(run, 'fetch_telegraph_views', return_value=42) as tele_mock, \
             patch.object(run, 'fetch_writeas_views', return_value=7) as wa_mock:
            enriched = run.enrich_posts_with_views(posts)
        self.assertEqual(enriched[0]['views'], 42)
        self.assertEqual(enriched[1]['views'], 7)
        tele_mock.assert_called_once()
        wa_mock.assert_called_once()


class ApolloSequenceStatusTests(unittest.TestCase):
    def test_build_status_reports_launch_ready_unverified_send(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            live_list = log_dir / 'marketing_2026-05-25_apollo_list_verification.json'
            live_list.write_text(json.dumps({
                'timestamp': '2026-05-25T10:00:00+02:00',
                'chosen_action': {'type': 'apollo_list_verification'},
                'result': {'record_count': 5, 'final_url': 'https://app.apollo.io/#/lists', 'evidence': ['5 visible records']},
            }), encoding='utf-8')
            with patch.object(apollo_sequence_status, 'LOG_DIR', log_dir), \
                 patch.object(apollo_sequence_status, 'STATUS_JSON', log_dir / 'apollo_sequence_status_latest.json'), \
                 patch.object(apollo_sequence_status, 'STATUS_MD', log_dir / 'apollo_sequence_status_latest.md'):
                payload = apollo_sequence_status.build_status(datetime.fromisoformat('2026-05-25T12:00:00+02:00'))
        self.assertEqual(payload['status'], 'launch_ready_unverified_send')
        self.assertTrue(payload['needs_live_verification'])
        self.assertEqual(payload['record_count'], 5)

    def test_build_status_downgrades_legacy_launch_ready_log_without_live_send(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            live_list = log_dir / 'marketing_2026-05-25_apollo_list_verification.json'
            live_list.write_text(json.dumps({
                'timestamp': '2026-05-25T10:00:00+02:00',
                'chosen_action': {'type': 'apollo_list_verification'},
                'result': {'record_count': 5, 'final_url': 'https://app.apollo.io/#/lists', 'evidence': ['5 visible records']},
            }), encoding='utf-8')
            launch = log_dir / 'marketing_2026-05-25_apollo_sequence_launch.json'
            launch.write_text(json.dumps({
                'timestamp': '2026-05-25T11:00:00+02:00',
                'chosen_action': {'type': 'apollo_sequence_launch', 'sequence_name': 'Seq'},
                'result': {
                    'status': 'executed',
                    'outcome_ready': True,
                    'record_count': 5,
                    'sequence_name': 'Seq',
                    'notes': ['If human/browser automation launches the emails, keep this sequence name unchanged.'],
                },
            }), encoding='utf-8')
            with patch.object(apollo_sequence_status, 'LOG_DIR', log_dir), \
                 patch.object(apollo_sequence_status, 'STATUS_JSON', log_dir / 'apollo_sequence_status_latest.json'), \
                 patch.object(apollo_sequence_status, 'STATUS_MD', log_dir / 'apollo_sequence_status_latest.md'):
                payload = apollo_sequence_status.build_status(datetime.fromisoformat('2026-05-25T12:00:00+02:00'))
        self.assertEqual(payload['status'], 'launch_ready_unverified_send')
        self.assertFalse(payload['measurement_pending'])
        self.assertTrue(payload['needs_live_verification'])

    def test_apollo_outbound_verifier_separates_launch_ready_from_live_send(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            status_path = log_dir / 'apollo_sequence_status_latest.json'
            status_path.write_text(json.dumps({
                'status': 'launch_ready_unverified_send',
                'record_count': 5,
                'sequence_name': 'Seq',
                'needs_live_verification': True,
            }), encoding='utf-8')
            with patch.object(apollo_outbound_verifier, 'LOG_DIR', log_dir), \
                 patch.object(apollo_outbound_verifier, 'STATUS_PATH', status_path), \
                 patch.object(apollo_outbound_verifier, 'OUTPUT_MD', log_dir / 'apollo_outbound_verification_latest.md'):
                payload = apollo_outbound_verifier.build_verification(datetime.fromisoformat('2026-05-25T12:00:00+02:00'))
        self.assertEqual(payload['result']['status'], 'launch_ready_needs_send_confirmation')
        self.assertFalse(payload['result']['outcome_ready'])


class ApolloSequenceLauncherTests(unittest.TestCase):
    def test_launcher_logs_launch_ready_not_live_send(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            verification_path = log_dir / 'marketing_2026-05-25_apollo_list_verification.json'
            verification_path.write_text(json.dumps({
                'result': {
                    'record_count': 5,
                    'outcome_ready': True,
                    'final_url': 'https://app.apollo.io/#/lists/example',
                }
            }), encoding='utf-8')
            outreach_path = tmp / 'outreach-log.md'
            outreach_path.write_text('# Outreach Log\n\n', encoding='utf-8')

            with patch.object(apollo_sequence_launcher, 'LOG_DIR', log_dir), \
                 patch.object(apollo_sequence_launcher, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(apollo_sequence_launcher, 'OUTREACH_LOG', outreach_path):
                rc = apollo_sequence_launcher.main()

            self.assertEqual(rc, 0)
            launch_log = log_dir / f"marketing_{datetime.now().astimezone().strftime('%Y-%m-%d')}_apollo_sequence_launch.json"
            payload = json.loads(launch_log.read_text(encoding='utf-8'))
            self.assertEqual(payload['result']['status'], 'launch_ready_packet_created')
            self.assertFalse(payload['result']['live_external_action'])
            self.assertFalse(payload['result']['outcome_ready'])


class DistributionHunterTests(unittest.TestCase):
    def test_distribution_hunter_writes_latest_status(self):
        fake_decision = type('Decision', (), {
            'lane': 'directory_confirmation',
            'reason': 'Need proof',
            'reasons': ['Need proof'],
            'owned_content_posts_last_36h': 0,
            'unsubmitted_directory_channels': [],
            'shared_findings_used': ['x'],
            'artifact_path': '/tmp/brief.md',
            'short_review_window_release_at': None,
            'skip_directory_submissions': False,
            'skip_curator_outreach': False,
        })()
        fake_execution = type('Execution', (), {
            'lane': 'directory_confirmation',
            'action_type': 'directory_confirmation_execution',
            'status': 'executed',
            'artifact_path': '/tmp/execution.json',
            'summary': 'Refreshed proof',
            'targets_prepared': [],
            'shared_findings_used': ['x'],
            'live_external_action': False,
            'blocking_factors': [],
        })()
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            audit_path = log_dir / 'marketing_workflow_audit_latest.json'
            status_json = log_dir / 'distribution_hunter_latest.json'
            audit_path.write_text(json.dumps({'repair_actions': [{'failure_type': 'outcome_system_underpowered', 'repair_state': 'needs_execution'}]}), encoding='utf-8')
            with patch.object(distribution_hunter, 'LOG_DIR', log_dir), \
                 patch.object(distribution_hunter, 'STATUS_JSON', status_json), \
                 patch.object(distribution_hunter, 'STATUS_MD', log_dir / 'distribution_hunter_latest.md'), \
                 patch('agents.marketing.distribution_hunter.choose_distribution_lane', return_value=fake_decision), \
                 patch('agents.marketing.distribution_hunter.execute_distribution_lane', return_value=fake_execution):
                payload = distribution_hunter.run(datetime.fromisoformat('2026-05-25T12:00:00+02:00'))
                self.assertTrue(status_json.exists())
        self.assertEqual(payload['selected_lane'], 'directory_confirmation')
        self.assertEqual(payload['selected_action_type'], 'directory_confirmation_execution')


if __name__ == '__main__':
    unittest.main()
