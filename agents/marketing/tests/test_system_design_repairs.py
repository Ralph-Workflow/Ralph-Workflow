from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agents.marketing import apollo_outbound_verifier, apollo_sequence_launcher, apollo_sequence_status, distribution_hunter, marketing_momentum_watchdog, outcome_capability_runner, outcome_execution_board_runner, run
from agents.marketing.distribution_lane_selector import LaneDecision


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
                 patch.object(apollo_outbound_verifier, 'OUTPUT_MD', log_dir / 'apollo_outbound_verification_latest.md'), \
                 patch.object(apollo_outbound_verifier, '_verify_live_sequence_from_apollo', return_value=None):
                payload = apollo_outbound_verifier.build_verification(datetime.fromisoformat('2026-05-25T12:00:00+02:00'))
        self.assertEqual(payload['result']['status'], 'launch_ready_needs_send_confirmation')
        self.assertFalse(payload['result']['outcome_ready'])

    def test_build_status_prefers_verified_live_sequence_over_launch_ready_packet(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            launch = log_dir / 'marketing_2026-05-25_apollo_sequence_launch.json'
            launch.write_text(json.dumps({
                'timestamp': '2026-05-25T11:00:00+02:00',
                'chosen_action': {'type': 'apollo_sequence_launch', 'sequence_name': 'Seq', 'url': 'https://app.apollo.io/#/lists'},
                'result': {
                    'status': 'launch_ready_packet_created',
                    'outcome_ready': False,
                    'record_count': 5,
                    'sequence_name': 'Seq',
                    'final_url': 'https://app.apollo.io/#/lists',
                },
            }), encoding='utf-8')
            outbound = log_dir / 'marketing_2026-05-25_120500_apollo_outbound_verification.json'
            outbound.write_text(json.dumps({
                'timestamp': '2026-05-25T12:05:00+02:00',
                'chosen_action': {'type': 'apollo_outbound_verification'},
                'result': {
                    'status': 'verified_live_sequence',
                    'record_count': 758,
                    'sequence_name': 'Ralph Workflow Seq',
                    'final_url': 'https://app.apollo.io/#/sequences/seq',
                    'evidence': ['live proof'],
                },
            }), encoding='utf-8')
            with patch.object(apollo_sequence_status, 'LOG_DIR', log_dir), \
                 patch.object(apollo_sequence_status, 'STATUS_JSON', log_dir / 'apollo_sequence_status_latest.json'), \
                 patch.object(apollo_sequence_status, 'STATUS_MD', log_dir / 'apollo_sequence_status_latest.md'):
                payload = apollo_sequence_status.build_status(datetime.fromisoformat('2026-05-25T12:10:00+02:00'))
        self.assertEqual(payload['status'], 'measurement_pending_launch_window')
        self.assertTrue(payload['measurement_pending'])
        self.assertEqual(payload['record_count'], 758)
        self.assertEqual(payload['sequence_name'], 'Ralph Workflow Seq')
        self.assertEqual(payload['final_url'], 'https://app.apollo.io/#/sequences/seq')

    def test_build_status_surfaces_runtime_auth_blocker_for_launch_ready_apollo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            live_list = log_dir / 'marketing_2026-05-25_apollo_list_verification.json'
            live_list.write_text(json.dumps({
                'timestamp': '2026-05-25T10:00:00+02:00',
                'chosen_action': {'type': 'apollo_list_verification'},
                'result': {'record_count': 5, 'final_url': 'https://app.apollo.io/#/lists', 'evidence': ['5 visible records']},
            }), encoding='utf-8')
            runtime_status = log_dir / 'apollo_status.json'
            runtime_status.write_text(json.dumps({
                'timestamp': '2026-05-25T11:45:00+02:00',
                'status': 'cloudflare_auth_blocked',
                'cloudflare_blocked': True,
                'final_url': 'https://app.apollo.io/#/home',
                'notes': 'Cloudflare interstitial detected on authenticated surface.',
            }), encoding='utf-8')
            with patch.object(apollo_sequence_status, 'LOG_DIR', log_dir), \
                 patch.object(apollo_sequence_status, 'STATUS_JSON', log_dir / 'apollo_sequence_status_latest.json'), \
                 patch.object(apollo_sequence_status, 'STATUS_MD', log_dir / 'apollo_sequence_status_latest.md'):
                payload = apollo_sequence_status.build_status(datetime.fromisoformat('2026-05-25T12:00:00+02:00'))
        self.assertEqual(payload['status'], 'runtime_auth_blocked')
        self.assertEqual(payload['runtime_blocker_status'], 'cloudflare_auth_blocked')
        self.assertTrue(payload['needs_live_verification'])

    def test_apollo_outbound_verifier_reports_runtime_auth_blocker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            status_path = log_dir / 'apollo_sequence_status_latest.json'
            status_path.write_text(json.dumps({
                'status': 'runtime_auth_blocked',
                'record_count': 5,
                'sequence_name': 'Seq',
                'needs_live_verification': True,
                'runtime_blocker_status': 'cloudflare_auth_blocked',
                'runtime_blocker_summary': 'Apollo runtime is blocked by a Cloudflare auth interstitial.',
            }), encoding='utf-8')
            with patch.object(apollo_outbound_verifier, 'LOG_DIR', log_dir), \
                 patch.object(apollo_outbound_verifier, 'STATUS_PATH', status_path), \
                 patch.object(apollo_outbound_verifier, 'OUTPUT_MD', log_dir / 'apollo_outbound_verification_latest.md'), \
                 patch.object(apollo_outbound_verifier, '_verify_live_sequence_from_apollo', return_value=None):
                payload = apollo_outbound_verifier.build_verification(datetime.fromisoformat('2026-05-25T12:00:00+02:00'))
        self.assertEqual(payload['result']['status'], 'runtime_auth_blocked')
        self.assertEqual(payload['result']['runtime_blocker_status'], 'cloudflare_auth_blocked')
        self.assertFalse(payload['result']['outcome_ready'])

    def test_apollo_outbound_verifier_refreshes_sequence_status_before_audit_and_board(self):
        call_order: list[str] = []

        def _mark(name: str):
            def _inner() -> int:
                call_order.append(name)
                return 0
            return _inner

        with patch('agents.marketing.apollo_sequence_status.main', side_effect=_mark('apollo_sequence_status_latest')) as status_mock, \
             patch('agents.marketing.marketing_workflow_audit.main', side_effect=_mark('marketing_workflow_audit_latest')) as audit_mock, \
             patch('agents.marketing.outcome_execution_board_runner.main', side_effect=_mark('marketing_execution_board_latest')) as board_mock:
            refresh = apollo_outbound_verifier._refresh_dependent_truths()

        self.assertTrue(refresh['ok'])
        self.assertEqual(
            refresh['refreshed'],
            ['apollo_sequence_status_latest', 'marketing_workflow_audit_latest', 'marketing_execution_board_latest'],
        )
        self.assertEqual(
            call_order,
            ['apollo_sequence_status_latest', 'marketing_workflow_audit_latest', 'marketing_execution_board_latest'],
        )
        status_mock.assert_called_once()
        audit_mock.assert_called_once()
        board_mock.assert_called_once()

    def test_apollo_outbound_verifier_main_refreshes_audit_and_execution_board(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            status_path = log_dir / 'apollo_sequence_status_latest.json'
            status_path.write_text(json.dumps({
                'status': 'verified_live_sequence',
                'record_count': 5,
                'sequence_name': 'Seq',
                'final_url': 'https://app.apollo.io/#/sequences/seq',
                'needs_live_verification': False,
                'evidence': ['live proof'],
            }), encoding='utf-8')
            with patch.object(apollo_outbound_verifier, 'LOG_DIR', log_dir), \
                 patch.object(apollo_outbound_verifier, 'STATUS_PATH', status_path), \
                 patch.object(apollo_outbound_verifier, 'OUTPUT_MD', log_dir / 'apollo_outbound_verification_latest.md'), \
                 patch.object(apollo_outbound_verifier, '_refresh_dependent_truths', return_value={'ok': True, 'refreshed': ['apollo_sequence_status_latest', 'marketing_workflow_audit_latest', 'marketing_execution_board_latest'], 'errors': []}) as refresh_mock:
                rc = apollo_outbound_verifier.main()
            self.assertEqual(rc, 0)
            refresh_mock.assert_called_once()
            logs = sorted(log_dir.glob('marketing_*_apollo_outbound_verification.json'))
            self.assertTrue(logs)
            payload = json.loads(logs[-1].read_text(encoding='utf-8'))
            self.assertEqual(payload['post_verification_refresh']['refreshed'], ['apollo_sequence_status_latest', 'marketing_workflow_audit_latest', 'marketing_execution_board_latest'])
            self.assertIn('Refreshed dependent audit/board artifacts', ' '.join(payload['result']['notes']))


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


class OutcomeCapabilityRunnerTests(unittest.TestCase):
    def test_accepts_frozen_apollo_lane_and_does_not_force_comparison_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            log_dir = root / 'logs'
            log_dir.mkdir()
            queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            queue_path.write_text(json.dumps({'targets': [{'status': 'prepared'}]}), encoding='utf-8')
            frozen_decision = LaneDecision(
                lane='apollo_outreach',
                reason='apollo ready',
                reasons=['apollo ready'],
                owned_content_posts_last_36h=0,
                unsubmitted_directory_channels=[],
                shared_findings_used=['apollo_status.json'],
                artifact_path='/tmp/brief.md',
            )
            fake_execution = type('Execution', (), {
                'lane': 'apollo_outreach',
                'action_type': 'apollo_outreach_execution',
                'status': 'prepared',
                'artifact_path': '/tmp/apollo.md',
                'summary': 'Prepared Apollo packet',
                'targets_prepared': ['Example target'],
                'shared_findings_used': ['apollo_status.json'],
                'live_external_action': False,
                'blocking_factors': [],
            })()
            with patch.object(outcome_capability_runner, 'LOG_DIR', log_dir), \
                 patch.object(outcome_capability_runner, 'STATUS_JSON', log_dir / 'outcome_capability_latest.json'), \
                 patch.object(outcome_capability_runner, 'STATUS_MD', log_dir / 'outcome_capability_latest.md'), \
                 patch.object(outcome_capability_runner, 'QUEUE_PATH', queue_path), \
                 patch('agents.marketing.outcome_capability_runner.choose_distribution_lane', return_value=frozen_decision), \
                 patch('agents.marketing.outcome_capability_runner.execute_distribution_lane', return_value=fake_execution):
                payload = outcome_capability_runner.run(datetime.fromisoformat('2026-05-25T23:22:00+02:00'))
        self.assertEqual(payload['selected_lane'], 'apollo_outreach')
        self.assertEqual(payload['selected_action_type'], 'apollo_outreach_execution')

    def test_forces_non_reddit_capability_lane_and_logs_codeberg_linkage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            log_dir = root / 'logs'
            log_dir.mkdir()
            queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            queue_path.write_text(json.dumps({'targets': [{'status': 'prepared'}, {'status': 'prepared'}]}), encoding='utf-8')
            fake_decision = type('Decision', (), {
                'lane': 'measurement_hold',
                'reason': 'hold',
                'reasons': ['hold'],
                'owned_content_posts_last_36h': 0,
                'unsubmitted_directory_channels': [],
                'shared_findings_used': ['x'],
                'artifact_path': '/tmp/brief.md',
                'short_review_window_release_at': None,
                'skip_directory_submissions': True,
                'skip_curator_outreach': True,
            })()
            fake_execution = type('Execution', (), {
                'lane': 'comparison_backlink_outreach',
                'action_type': 'comparison_backlink_outreach_execution',
                'status': 'prepared',
                'artifact_path': '/tmp/out.md',
                'summary': 'Prepared comparison asset',
                'targets_prepared': ['Morph'],
                'shared_findings_used': ['x'],
                'live_external_action': False,
                'blocking_factors': [],
            })()
            with patch.object(outcome_capability_runner, 'LOG_DIR', log_dir), \
                 patch.object(outcome_capability_runner, 'STATUS_JSON', log_dir / 'outcome_capability_latest.json'), \
                 patch.object(outcome_capability_runner, 'STATUS_MD', log_dir / 'outcome_capability_latest.md'), \
                 patch.object(outcome_capability_runner, 'QUEUE_PATH', queue_path), \
                 patch('agents.marketing.outcome_capability_runner.choose_distribution_lane', return_value=fake_decision), \
                 patch('agents.marketing.outcome_capability_runner.execute_distribution_lane', return_value=fake_execution):
                payload = outcome_capability_runner.run(datetime.fromisoformat('2026-05-25T12:00:00+02:00'))
        self.assertEqual(payload['selected_lane'], 'comparison_backlink_outreach')
        self.assertEqual(payload['direct_codeberg_linkage']['cta'], 'https://codeberg.org/RalphWorkflow/Ralph-Workflow')
        self.assertEqual(payload['outcome_capability']['comparison_queue_prepared_count'], 2)


class OutcomeExecutionBoardRunnerTests(unittest.TestCase):
    def test_executes_current_board_lane_and_logs_structural_capability(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            log_dir = root / 'logs'
            drafts_dir = root / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            audit_path = log_dir / 'marketing_workflow_audit_latest.json'
            audit_path.write_text(json.dumps({
                'repair_actions': [
                    {'failure_type': 'outcome_system_underpowered', 'repair_state': 'needs_execution'}
                ]
            }), encoding='utf-8')
            fake_decision = LaneDecision(
                lane='manual_outreach_asset_follow_through',
                reason='A manual asset already exists.',
                reasons=['A manual asset already exists.'],
                owned_content_posts_last_36h=0,
                unsubmitted_directory_channels=[],
                shared_findings_used=['adoption_metrics_latest.json'],
                artifact_path='/tmp/brief.md',
            )
            fake_execution = type('Execution', (), {
                'lane': 'manual_outreach_asset_follow_through',
                'action_type': 'manual_outreach_asset_follow_through',
                'status': 'prepared',
                'artifact_path': '/tmp/manual.md',
                'summary': 'Reused manual asset.',
                'targets_prepared': ['Target A'],
                'shared_findings_used': ['adoption_metrics_latest.json'],
                'live_external_action': False,
                'blocking_factors': [],
            })()
            with patch.object(outcome_execution_board_runner, 'LOG_DIR', log_dir), \
                 patch.object(outcome_execution_board_runner, 'STATUS_JSON', log_dir / 'outcome_execution_board_latest.json'), \
                 patch.object(outcome_execution_board_runner, 'STATUS_MD', log_dir / 'outcome_execution_board_latest.md'), \
                 patch.object(outcome_execution_board_runner, 'AUDIT_JSON', audit_path), \
                 patch('agents.marketing.outcome_execution_board_runner._write_marketing_execution_board', return_value=(drafts_dir / 'marketing_execution_board_latest.md', ['Target A'])), \
                 patch('agents.marketing.outcome_execution_board_runner.choose_distribution_lane', return_value=fake_decision), \
                 patch('agents.marketing.outcome_execution_board_runner.execute_distribution_lane', return_value=fake_execution):
                payload = outcome_execution_board_runner.run(datetime.fromisoformat('2026-05-26T01:40:00+02:00'))
        self.assertEqual(payload['selected_lane'], 'manual_outreach_asset_follow_through')
        self.assertEqual(payload['structural_capability']['name'], 'execution_board_follow_through_runner')
        self.assertEqual(payload['execution_board_targets'], ['Target A'])
        self.assertTrue(payload['repair_needed_at_start'])

    def test_executes_repo_conversion_proof_asset_when_selector_picks_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            log_dir = root / 'logs'
            drafts_dir = root / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            audit_path = log_dir / 'marketing_workflow_audit_latest.json'
            audit_path.write_text(json.dumps({
                'repair_actions': [
                    {'failure_type': 'outcome_system_underpowered', 'repair_state': 'needs_execution'}
                ]
            }), encoding='utf-8')
            fake_decision = LaneDecision(
                lane='repo_conversion_proof_asset',
                reason='Need a Codeberg-first proof asset.',
                reasons=['StackOverflow slot is exhausted; proof asset is the truthful lane.'],
                owned_content_posts_last_36h=0,
                unsubmitted_directory_channels=[],
                shared_findings_used=['adoption_metrics_latest.json'],
                artifact_path='/tmp/brief.md',
            )
            fake_execution = type('Execution', (), {
                'lane': 'repo_conversion_proof_asset',
                'action_type': 'repo_conversion_proof_asset',
                'status': 'executed',
                'artifact_path': '/tmp/proof.md',
                'summary': 'Prepared repo conversion proof asset.',
                'targets_prepared': ['Codeberg-first proof asset'],
                'shared_findings_used': ['adoption_metrics_latest.json'],
                'live_external_action': False,
                'blocking_factors': [],
            })()
            with patch.object(outcome_execution_board_runner, 'LOG_DIR', log_dir), \
                 patch.object(outcome_execution_board_runner, 'STATUS_JSON', log_dir / 'outcome_execution_board_latest.json'), \
                 patch.object(outcome_execution_board_runner, 'STATUS_MD', log_dir / 'outcome_execution_board_latest.md'), \
                 patch.object(outcome_execution_board_runner, 'AUDIT_JSON', audit_path), \
                 patch('agents.marketing.outcome_execution_board_runner._write_marketing_execution_board', return_value=(drafts_dir / 'marketing_execution_board_latest.md', [])), \
                 patch('agents.marketing.outcome_execution_board_runner.choose_distribution_lane', return_value=fake_decision), \
                 patch('agents.marketing.outcome_execution_board_runner.execute_distribution_lane', return_value=fake_execution) as exec_mock:
                payload = outcome_execution_board_runner.run(datetime.fromisoformat('2026-05-26T06:17:00+02:00'))
        exec_mock.assert_called_once()
        self.assertEqual(payload['selected_lane'], 'repo_conversion_proof_asset')
        self.assertEqual(payload['selected_action_type'], 'repo_conversion_proof_asset')
        self.assertEqual(payload['artifact_path'], '/tmp/proof.md')
        self.assertEqual(payload['measurement_window'], 'Review repo-visit and conversion movement within 7 days.')

    def test_executes_distribution_architecture_repair_when_selector_picks_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            log_dir = root / 'logs'
            drafts_dir = root / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            audit_path = log_dir / 'marketing_workflow_audit_latest.json'
            audit_path.write_text(json.dumps({
                'repair_actions': [
                    {'failure_type': 'outcome_system_underpowered', 'repair_state': 'needs_execution'}
                ]
            }), encoding='utf-8')
            fake_decision = LaneDecision(
                lane='distribution_architecture_repair',
                reason='Board is still empty after the blocker cleared.',
                reasons=['Run a concrete architecture repair instead of a fake board refresh.'],
                owned_content_posts_last_36h=0,
                unsubmitted_directory_channels=[],
                shared_findings_used=['marketing_execution_board_latest.md'],
                artifact_path='/tmp/brief.md',
            )
            fake_execution = type('Execution', (), {
                'lane': 'distribution_architecture_repair',
                'action_type': 'distribution_architecture_repair',
                'status': 'executed',
                'artifact_path': '/tmp/repair.md',
                'summary': 'Ran a concrete distribution architecture repair.',
                'targets_prepared': [],
                'shared_findings_used': ['marketing_execution_board_latest.md'],
                'live_external_action': False,
                'blocking_factors': [],
            })()
            with patch.object(outcome_execution_board_runner, 'LOG_DIR', log_dir), \
                 patch.object(outcome_execution_board_runner, 'STATUS_JSON', log_dir / 'outcome_execution_board_latest.json'), \
                 patch.object(outcome_execution_board_runner, 'STATUS_MD', log_dir / 'outcome_execution_board_latest.md'), \
                 patch.object(outcome_execution_board_runner, 'AUDIT_JSON', audit_path), \
                 patch('agents.marketing.outcome_execution_board_runner._write_marketing_execution_board', return_value=(drafts_dir / 'marketing_execution_board_latest.md', [])), \
                 patch('agents.marketing.outcome_execution_board_runner.choose_distribution_lane', return_value=fake_decision), \
                 patch('agents.marketing.outcome_execution_board_runner.execute_distribution_lane', return_value=fake_execution) as exec_mock:
                payload = outcome_execution_board_runner.run(datetime.fromisoformat('2026-05-26T08:58:00+02:00'))
        exec_mock.assert_called_once()
        self.assertEqual(payload['selected_lane'], 'distribution_architecture_repair')
        self.assertEqual(payload['selected_action_type'], 'distribution_architecture_repair')
        self.assertEqual(payload['artifact_path'], '/tmp/repair.md')
        self.assertEqual(
            payload['measurement_window'],
            'Verify the next runner produces a truthful lane or a changed blocker/fingerprint state.',
        )

    def test_sync_from_execution_updates_latest_status_without_rerunning_selector(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            log_dir = root / 'logs'
            drafts_dir = root / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            board_path = drafts_dir / 'marketing_execution_board_latest.md'
            board_path.write_text('# board\n', encoding='utf-8')
            audit = {
                'repair_actions': [
                    {'failure_type': 'outcome_system_underpowered', 'repair_state': 'needs_execution'}
                ]
            }
            decision = LaneDecision(
                lane='distribution_architecture_guard_pause',
                reason='Hold window is still active.',
                reasons=['Short-window congestion has not cleared yet.'],
                owned_content_posts_last_36h=0,
                unsubmitted_directory_channels=[],
                shared_findings_used=['adoption_metrics_latest.json'],
                artifact_path='/tmp/brief.md',
            )
            execution = type('Execution', (), {
                'lane': 'distribution_architecture_guard_pause',
                'action_type': 'distribution_architecture_guard_pause',
                'status': 'skipped_repair',
                'artifact_path': '/tmp/guard.md',
                'summary': 'Reused current guard pause truth.',
                'targets_prepared': [],
                'shared_findings_used': ['adoption_metrics_latest.json'],
                'live_external_action': False,
                'blocking_factors': [],
            })()
            with patch.object(outcome_execution_board_runner, 'LOG_DIR', log_dir), \
                 patch.object(outcome_execution_board_runner, 'STATUS_JSON', log_dir / 'outcome_execution_board_latest.json'), \
                 patch.object(outcome_execution_board_runner, 'STATUS_MD', log_dir / 'outcome_execution_board_latest.md'):
                payload = outcome_execution_board_runner.sync_from_execution(
                    now=datetime.fromisoformat('2026-05-26T07:50:00+02:00'),
                    audit=audit,
                    decision=decision,
                    board_path=board_path,
                    board_targets=['Target A'],
                    execution=execution,
                )
        self.assertEqual(payload['selected_lane'], 'distribution_architecture_guard_pause')
        self.assertEqual(payload['selected_action_type'], 'distribution_architecture_guard_pause')
        self.assertEqual(payload['artifact_path'], '/tmp/guard.md')
        self.assertEqual(payload['execution_board_targets'], ['Target A'])
        self.assertEqual(
            payload['measurement_window'],
            'Verify the next runner produces a truthful lane or a changed blocker/fingerprint state.',
        )


class SystemDesignRepairAcknowledgementTests(unittest.TestCase):
    def test_distribution_architecture_repair_advances_system_design_repair(self):
        audit = {
            'repair_actions': [
                {
                    'failure_type': 'execution_ceiling_repetition',
                    'repair_kind': 'system_design',
                    'repair_state': 'needs_execution',
                }
            ],
            'repair_window_status': 'needs_repair',
            'measurement_pending_reasons': ['primary_repo_flat'],
        }
        execution = type('Execution', (), {
            'action_type': 'distribution_architecture_repair',
            'live_external_action': False,
        })()

        changed = run._advance_audit_repairs_for_execution(
            audit=audit,
            execution=execution,
            now=datetime.fromisoformat('2026-05-26T03:17:35+02:00'),
        )

        self.assertTrue(changed)
        self.assertEqual(audit['repair_actions'][0]['repair_state'], 'pending_measurement')
        self.assertEqual(audit['repair_window_status'], 'measurement_pending')

    def test_watchdog_treats_structural_repair_as_present_system_repair(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_dir = root / 'logs'
            status_dir.mkdir()
            seo_dir = root / 'seo-reports'
            seo_dir.mkdir()
            report_path = seo_dir / 'reddit_monitor_latest.md'
            report_path.write_text('Degraded coverage. Partial visibility only. Fail closed.', encoding='utf-8')
            adoption_path = status_dir / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({
                'evaluation': {
                    'failing_signals': ['primary_repo_flat']
                },
                'recent_window': {
                    'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}
                }
            }), encoding='utf-8')
            audit_path = status_dir / 'marketing_workflow_audit_latest.json'
            audit_path.write_text(json.dumps({
                'repair_window_status': 'measurement_pending',
                'measurement_pending_reasons': ['primary_repo_flat'],
                'failing_tactics': ['execution_ceiling_repetition'],
                'repair_actions': [],
                'latest_executed_action': {
                    'type': 'distribution_architecture_repair',
                    'ok': True,
                    'live_external_action': False,
                },
            }), encoding='utf-8')
            apollo_status_path = status_dir / 'apollo_status.json'
            apollo_status_path.write_text(json.dumps({'status': 'cloudflare_auth_blocked', 'cloudflare_blocked': True}), encoding='utf-8')
            reddit_exec_path = status_dir / 'reddit_execution_status_latest.json'
            reddit_exec_path.write_text(json.dumps({'status': 'blocked_pending_praw_credentials'}), encoding='utf-8')
            runner_path = status_dir / 'marketing_loop_runner_latest.json'
            runner_path.write_text(json.dumps({'status': 'ok'}), encoding='utf-8')

            with patch.object(marketing_momentum_watchdog, 'SEO', seo_dir), \
                 patch.object(marketing_momentum_watchdog, 'STATUS_DIR', status_dir), \
                 patch.object(marketing_momentum_watchdog, 'STATUS_PATH', status_dir / 'marketing_momentum_watchdog.json'), \
                 patch.object(marketing_momentum_watchdog, 'ADOPTION_PATH', adoption_path), \
                 patch.object(marketing_momentum_watchdog, 'AUDIT_PATH', audit_path), \
                 patch.object(marketing_momentum_watchdog, 'APOLLO_STATUS_PATH', apollo_status_path), \
                 patch.object(marketing_momentum_watchdog, 'RUNNER_PATH', runner_path), \
                 patch.object(marketing_momentum_watchdog, 'REDDIT_EXECUTION_STATUS_PATH', reddit_exec_path), \
                 patch.object(marketing_momentum_watchdog, 'newest_post_time', return_value=None), \
                 patch.object(marketing_momentum_watchdog, 'newest_healthy_report_time', return_value=(report_path, 1.0)), \
                 patch.object(marketing_momentum_watchdog, 'latest_reddit_monitor_runtime', return_value={'status': 'cooldown_skip', 'age_hours': 1.0}), \
                 patch.object(marketing_momentum_watchdog, 'latest_reddit_execution_status', return_value={'status': 'blocked_pending_praw_credentials', 'age_hours': 1.0}), \
                 patch.object(marketing_momentum_watchdog.marketing_run, '_latest_measurement_hold_window', return_value=None), \
                 patch.object(marketing_momentum_watchdog, 'subprocess') as subprocess_mock, \
                 patch.object(marketing_momentum_watchdog, 'datetime') as datetime_mock:
                subprocess_mock.run.return_value = None
                fixed_now = datetime.fromisoformat('2026-05-26T03:20:00+02:00')
                datetime_mock.now.return_value = fixed_now
                datetime_mock.fromtimestamp.side_effect = datetime.fromtimestamp
                payload_path = status_dir / 'marketing_momentum_watchdog.json'
                marketing_momentum_watchdog.main()
                payload = json.loads(payload_path.read_text(encoding='utf-8'))

        self.assertNotIn('outcome_system_repair_missing', payload['actions'])


if __name__ == '__main__':
    unittest.main()
