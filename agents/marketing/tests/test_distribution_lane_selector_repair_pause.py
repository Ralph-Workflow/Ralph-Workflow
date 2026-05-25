import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agents.marketing import distribution_lane_selector


class DistributionLaneSelectorRepairPauseTests(unittest.TestCase):
    def test_primary_repo_flat_requires_verified_form_like_path_for_manual_execution(self):
        self.assertTrue(distribution_lane_selector._publisher_target_has_manual_executable_channel([
            {'type': 'website', 'value': 'https://example.com/contact?via=tally.so', 'label': 'contact form'},
        ]))
        self.assertFalse(distribution_lane_selector._publisher_target_has_manual_executable_channel([
            {'type': 'website', 'value': 'https://ctxt.dev/contact', 'label': 'common contact/about path'},
        ]))
        self.assertFalse(distribution_lane_selector._publisher_target_has_manual_executable_channel([
            {'type': 'website', 'value': 'https://ctxt.dev/', 'label': 'website'},
        ]))

    def test_recent_live_external_action_count_includes_top_level_flag(self):
        now = datetime(2026, 5, 24, 10, 0, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            log_dir.mkdir()
            (log_dir / 'marketing_2026-05-24_hidstech_publisher_outreach.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T09:59:38',
                    'action_type': 'publisher_email_outreach',
                    'status': 'executed',
                    'ok': True,
                    'live_external_action': True,
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-24_aiagents_directory_submission.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T05:43:30',
                    'action_type': 'aiagents_directory_submission',
                    'status': 'executed',
                    'ok': True,
                    'result': {'live_external_action': True},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir):
                total = distribution_lane_selector._recent_live_external_action_count(now, hours=6)

        self.assertEqual(total, 2)

    def test_recent_live_external_action_count_dedupes_legacy_and_canonical_email_logs(self):
        now = datetime(2026, 5, 24, 20, 20, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            log_dir.mkdir()
            (log_dir / 'marketing_2026-05-24_181741_tembo_publisher_email.json').write_text(
                json.dumps({
                    'timestamp_utc': '2026-05-24T18:17:41.650272+00:00',
                    'action': 'curator_email_outreach',
                    'status': 'sent',
                    'channel': {
                        'recipient': 'hello@tembo.io',
                    },
                    'subject': 'Ralph Workflow as an open-source workflow reference for your agentic coding guide',
                    'body_file': '/tmp/tembo.md',
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-24_tembo_publisher_outreach.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T20:17:41+02:00',
                    'action_type': 'publisher_email_outreach',
                    'status': 'executed',
                    'ok': True,
                    'live_external_action': True,
                    'target': 'Tembo',
                    'recipient': 'hello@tembo.io',
                    'subject': 'Ralph Workflow as an open-source workflow reference for your agentic coding guide',
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir):
                total = distribution_lane_selector._recent_live_external_action_count(now, hours=6)
                release_at = distribution_lane_selector._recent_live_external_window_release_at(now, hours=6)

        self.assertEqual(total, 1)
        self.assertEqual(release_at, datetime(2026, 5, 25, 2, 17, 41))

    def test_normalized_curator_queue_rows_hide_stale_prepared_targets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            log_dir.mkdir()
            queue_path = log_dir / 'curator_outreach_queue_latest.json'
            queue_path.write_text(json.dumps({
                'targets': [
                    {'target': 'AI Dev Setup', 'status': 'prepared'},
                    {'target': 'AI for Code', 'status': 'prepared'},
                    {'target': 'AI Resources', 'status': 'prepared'},
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
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', queue_path):
                names = distribution_lane_selector._prepared_curator_target_names(datetime(2026, 5, 24, 8, 48, 0))

        self.assertEqual(names, ['AI Resources'])

    def test_prepared_curator_targets_waiting_for_handoff_ignores_current_packet(self):
        now = datetime(2026, 5, 24, 8, 48, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            queue_path = log_dir / 'curator_outreach_queue_latest.json'
            queue_path.write_text(json.dumps({
                'targets': [
                    {'target': 'AI Resources', 'status': 'prepared', 'priority': 'HIGH'},
                    {'target': 'AgentOps Weekly', 'status': 'prepared', 'priority': 'MEDIUM'},
                ],
            }), encoding='utf-8')
            handoff_path = drafts_dir / 'curator_handoff_packet_latest.md'
            handoff_path.write_text(
                '# Curator packet\n\n### 1. AI Resources\n\n### 2. AgentOps Weekly\n',
                encoding='utf-8',
            )
            os.utime(handoff_path, (datetime(2026, 5, 24, 8, 46, 0).timestamp(), datetime(2026, 5, 24, 8, 46, 0).timestamp()))
            (log_dir / 'marketing_2026-05-24_curator_handoff_packet_execution.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T08:47:00',
                    'chosen_action': {'type': 'curator_handoff_packet_execution'},
                    'result': {'status': 'prepared', 'ok': True},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', queue_path), \
                 patch.object(distribution_lane_selector, 'CURATOR_HANDOFF_LATEST_PATH', handoff_path):
                waiting = distribution_lane_selector._prepared_curator_targets_waiting_for_handoff(now)
                delivered = distribution_lane_selector._curator_handoff_packet_current(
                    now,
                    ['AI Resources', 'AgentOps Weekly'],
                )

        self.assertEqual(waiting, 0)
        self.assertTrue(delivered)

    def test_primary_repo_flat_contact_packet_is_not_current_when_delivery_log_predates_refresh(self):
        now = datetime(2026, 5, 25, 2, 6, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            handoff_path = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            (log_dir / 'marketing_2026-05-24_primary_repo_flat_contact_handoff_packet_execution.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T06:44:20',
                    'chosen_action': {'type': 'primary_repo_flat_contact_handoff_packet_execution'},
                    'result': {'status': 'prepared', 'ok': True},
                }),
                encoding='utf-8',
            )
            handoff_path.write_text(
                '# Ralph Workflow Primary-Repo-Flat Publisher Contact Packet\n\n### 1. ToolChase\n\n### 2. Beam\n',
                encoding='utf-8',
            )
            os.utime(handoff_path, (datetime(2026, 5, 25, 0, 54, 48).timestamp(), datetime(2026, 5, 25, 0, 54, 48).timestamp()))

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir):
                current = distribution_lane_selector._primary_repo_flat_contact_handoff_packet_current(
                    now,
                    ['ToolChase', 'Beam'],
                )

        self.assertFalse(current)

    def test_stackoverflow_post_cooldown_surface_marks_empty_latest_run_as_exhausted(self):
        now = datetime(2026, 5, 24, 17, 54, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            latest_path = log_dir / 'stackoverflow_answer_lane_latest.json'
            latest_path.write_text(json.dumps({
                'generated_at': '2026-05-24T15:59:44.626446',
                'cooldown_active': False,
                'drafts_created': 0,
                'drafts': [],
                'reused_existing_draft': None,
                'top_questions': [
                    {
                        'title': 'How can I get more useful results from ai coding agents',
                        'url': 'https://stackoverflow.com/questions/79913508/how-can-i-get-more-useful-results-from-ai-coding-agents',
                    }
                ],
                'exhausted_question_urls': [
                    'https://stackoverflow.com/questions/79942291/how-should-i-structure-autonomous-ai-agent-workflows-for-production-reliability',
                ],
            }), encoding='utf-8')
            (log_dir / 'marketing_2026-05-24_stackoverflow_post_cooldown_cron.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T08:47:43+02:00',
                    'status': 'scheduled',
                    'ok': True,
                    'verification': {'scheduled_run_at': '2026-05-24T11:30:00+02:00'},
                    'chosen_action': {'type': 'stackoverflow_post_cooldown_cron'},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', latest_path):
                exhausted = distribution_lane_selector._stack_overflow_post_cooldown_surface_exhausted(now)

        self.assertTrue(exhausted)

    def test_stackoverflow_measurement_pending_ignores_stale_draft_after_exhausted_rerun(self):
        now = datetime(2026, 5, 24, 17, 54, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            stackoverflow_dir = drafts_dir / 'stackoverflow'
            log_dir.mkdir()
            drafts_dir.mkdir()
            stackoverflow_dir.mkdir()

            latest_path = log_dir / 'stackoverflow_answer_lane_latest.json'
            latest_path.write_text(json.dumps({
                'generated_at': '2026-05-24T15:59:44.626446',
                'cooldown_active': False,
                'drafts_created': 0,
                'drafts': [],
                'reused_existing_draft': None,
                'top_questions': [
                    {
                        'title': 'How can I get more useful results from ai coding agents',
                        'url': 'https://stackoverflow.com/questions/79913508/how-can-i-get-more-useful-results-from-ai-coding-agents',
                    }
                ],
                'exhausted_question_urls': [
                    'https://stackoverflow.com/questions/79942291/how-should-i-structure-autonomous-ai-agent-workflows-for-production-reliability',
                ],
            }), encoding='utf-8')
            (stackoverflow_dir / 'so_answer_2026-05-23_existing.md').write_text('# stale answer still on disk\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-24_stackoverflow_post_cooldown_cron.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T08:47:43+02:00',
                    'status': 'scheduled',
                    'ok': True,
                    'verification': {'scheduled_run_at': '2026-05-24T11:30:00+02:00'},
                    'chosen_action': {'type': 'stackoverflow_post_cooldown_cron'},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', latest_path):
                pending = distribution_lane_selector._stack_overflow_measurement_pending(now)

        self.assertFalse(pending)

    def test_pauses_curator_outreach_when_same_family_repair_window_is_active(self):
        now = datetime(2026, 5, 24, 1, 54, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "needs_repair",
            "repair_actions": [
                {
                    "failure_type": "primary_repo_flat",
                    "repair_state": "needs_execution",
                },
                {
                    "failure_type": "same_family_outreach_overlap",
                    "repair_state": "pending_measurement",
                },
                {
                    "failure_type": "same_family_distribution_overlap",
                    "repair_state": "pending_measurement",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=8),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=15),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=4),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[16, 46]),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', side_effect=lambda *args, **kwargs: kwargs.get('action_types') == distribution_lane_selector.RECENT_PROOF_ASSET_ACTION_TYPES),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=True),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'directory_confirmation')
        self.assertIn('active repair window says to hold another same-family curator-contact burst', '\n'.join(decision.reasons).lower())

    def test_skips_repeat_directory_confirmation_inside_short_review_window(self):
        now = datetime(2026, 5, 24, 4, 24, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "needs_repair",
            "repair_actions": [
                {
                    "failure_type": "primary_repo_flat",
                    "repair_state": "needs_execution",
                },
                {
                    "failure_type": "same_family_outreach_overlap",
                    "repair_state": "pending_measurement",
                },
                {
                    "failure_type": "same_family_distribution_overlap",
                    "repair_state": "pending_measurement",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')

            def recent_action(*args, **kwargs):
                action_types = kwargs.get('action_types')
                if action_types == distribution_lane_selector.RECENT_PROOF_ASSET_ACTION_TYPES:
                    return True
                if action_types == distribution_lane_selector.RECENT_DIRECTORY_CONFIRMATION_ACTION_TYPES:
                    return True
                return False

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=8),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=15),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=4),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[19, 42]),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', side_effect=recent_action),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_reset')
        self.assertIn('directory-confirmation refresh already shipped', '\n'.join(decision.reasons).lower())

    def test_holds_when_multiple_recent_external_actions_already_shipped(self):
        now = datetime(2026, 5, 24, 4, 51, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "needs_repair",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "needs_execution"},
                {"failure_type": "same_family_outreach_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=8),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=3),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=['AI Coding Stack']),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=18),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=4),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[19, 48]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=2),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 24, 10, 51, 0)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(True, '2026-05-24T10:49:56')),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', side_effect=lambda *args, **kwargs: kwargs.get('action_types') == distribution_lane_selector.RECENT_PROOF_ASSET_ACTION_TYPES or kwargs.get('action_types') == distribution_lane_selector.RECENT_RESET_ACTION_TYPES or kwargs.get('action_types') == distribution_lane_selector.RECENT_DIRECTORY_CONFIRMATION_ACTION_TYPES),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'measurement_hold')
        self.assertIn('live external marketing action(s) already shipped', '\n'.join(decision.reasons).lower())
        self.assertIn('stackoverflow discovery is in an active post-429 cooldown', '\n'.join(decision.reasons).lower())
        self.assertEqual(decision.short_review_window_release_at, '2026-05-24T10:51:00')

    def test_active_short_window_with_empty_board_and_spent_reentry_repairs_escalates_to_distribution_architecture_repair(self):
        now = datetime(2026, 5, 25, 0, 55, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_outreach_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-24_234934_active_loop_prompt_repair.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T23:49:34',
                    'chosen_action': {'type': 'active_loop_prompt_repair'},
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-24_235759_post_hold_reentry_contract_repair.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T23:57:59',
                    'chosen_action': {'type': 'post_hold_reentry_contract_repair'},
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=['vivy-yi/awesome-agent-orchestration']),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=['vivy-yi/awesome-agent-orchestration']),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[6, 7]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=4),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 2, 5, 5)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 8.0}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('still active', decision.reason.lower())
        self.assertIsNone(decision.short_review_window_release_at)

    def test_active_long_hold_reuses_historical_reentry_repairs_after_third_strike_guard(self):
        now = datetime(2026, 5, 25, 3, 57, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_outreach_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            latest_json.write_text(json.dumps({'short_review_window_release_at': '2026-05-25T07:20:16'}), encoding='utf-8')
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-25_014740_measurement_hold_execution.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T01:47:40.177303',
                    'chosen_action': {'type': 'measurement_hold_execution'},
                    'result': {'status': 'prepared', 'ok': True, 'live_external_action': False},
                    'review_window': {'scheduled_run_at': '2026-05-25T07:20:16'},
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-24_234934_active_loop_prompt_repair.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T23:49:34',
                    'chosen_action': {'type': 'active_loop_prompt_repair'},
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-24_235759_post_hold_reentry_contract_repair.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T23:57:59',
                    'chosen_action': {'type': 'post_hold_reentry_contract_repair'},
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-25_031100_measurement_hold_third_strike_guard_repair.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T03:11:00+02:00',
                    'chosen_action': {'type': 'measurement_hold_third_strike_guard_repair'},
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=True),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[6, 7]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=5),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 7, 20, 16)),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': True}),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('still active', decision.reason.lower())
        self.assertIsNone(decision.short_review_window_release_at)

    def test_cleared_short_window_escalates_to_distribution_architecture_repair(self):
        now = datetime(2026, 5, 25, 9, 0, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_outreach_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=8),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=18),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[0, 0]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=False),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 8.0}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('short review window already cleared', decision.reason.lower())
        self.assertIsNone(decision.short_review_window_release_at)

    def test_cleared_short_window_with_empty_execution_board_escalates_to_distribution_architecture_repair(self):
        now = datetime(2026, 5, 25, 2, 6, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_outreach_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=['vivy-yi/awesome-agent-orchestration']),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=['vivy-yi/awesome-agent-orchestration']),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[6, 7]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=4),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 2, 5, 5)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 8.0}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('short review window already cleared', decision.reason.lower())
        self.assertIsNone(decision.short_review_window_release_at)

    def test_empty_execution_board_new_phrase_escalates_to_distribution_architecture_repair(self):
        now = datetime(2026, 5, 25, 6, 1, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_outreach_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '- No truthful do-now packet remains on this board right now.\n',
                encoding='utf-8',
            )
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=True),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[6, 7]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=5),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 7, 20, 16)),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'repairs_seen': set(distribution_lane_selector.MEASUREMENT_HOLD_REENTRY_REPAIR_ACTION_TYPES), 'reentry_repairs_complete': True}),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 8.0}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('both post-hold rerun repairs were already used', decision.reason.lower())
        self.assertIsNone(decision.short_review_window_release_at)

    def test_existing_distribution_architecture_guard_suppresses_duplicate_repair_selection(self):
        now = datetime(2026, 5, 25, 4, 32, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_outreach_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            for filename, timestamp, action_type in [
                ('marketing_2026-05-25_013107_distribution_architecture_repair.json', '2026-05-25T01:31:07', 'distribution_architecture_repair'),
                ('marketing_2026-05-25_020752_distribution_architecture_repair.json', '2026-05-25T02:07:52', 'distribution_architecture_repair'),
                ('marketing_2026-05-25_042100_distribution_architecture_churn_guard_repair.json', '2026-05-25T04:21:00', 'distribution_architecture_churn_guard_repair'),
            ]:
                (log_dir / filename).write_text(
                    json.dumps({
                        'timestamp': timestamp,
                        'chosen_action': {'type': action_type},
                        'verification': {'execution_board_fingerprint': distribution_lane_selector.hashlib.sha1(execution_board.read_text(encoding='utf-8').encode('utf-8')).hexdigest()},
                        'result': {'status': 'executed', 'ok': True},
                    }),
                    encoding='utf-8',
                )

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[6, 7]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=5),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 7, 20, 16)),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': True}),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_guard_follow_through')
        self.assertIn('active third-strike churn guard', decision.reason.lower())

    def test_cleared_short_window_prefers_primary_repo_flat_publisher_packet_when_targets_are_ready(self):
        now = datetime(2026, 5, 25, 2, 6, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_outreach_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '### 1. Primary-repo-flat publisher contact packet\n'
                '- When: Do now\n'
                '- Targets: ToolChase, Beam\n',
                encoding='utf-8',
            )
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json', 'primary_repo_flat_contact_discovery_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['ToolChase', 'Beam']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[6, 7]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=4),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 2, 5, 5)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 8.0}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        self.assertIn('short review window already cleared', decision.reason.lower())
        self.assertIsNone(decision.short_review_window_release_at)


    def test_skip_curator_outreach_does_not_hide_ready_primary_repo_flat_publisher_packet(self):
        now = datetime(2026, 5, 25, 2, 20, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_outreach_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '### 1. Primary-repo-flat publisher contact packet\n'
                '- When: Do now\n'
                '- Targets: ToolChase, Beam\n',
                encoding='utf-8',
            )
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json', 'primary_repo_flat_contact_discovery_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=['vivy-yi/awesome-agent-orchestration']),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=['vivy-yi/awesome-agent-orchestration']),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['ToolChase', 'Beam']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[6, 7]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=3),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 2, 17, 41)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=False),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 8.0}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        self.assertNotEqual(decision.lane, 'curator_contact_handoff_packet')

    def test_curator_measurement_saturation_does_not_hide_ready_primary_repo_flat_publisher_packet(self):
        now = datetime(2026, 5, 25, 7, 21, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '### 1. Primary-repo-flat publisher contact packet\n'
                '- When: Do now\n'
                '- Targets: Beam\n',
                encoding='utf-8',
            )
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json', 'primary_repo_flat_contact_discovery_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=['vivy-yi/awesome-agent-orchestration']),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=['vivy-yi/awesome-agent-orchestration']),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=['ToolChase']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['ToolChase', 'Beam']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[6, 7]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=False),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 8.0}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        self.assertNotEqual(decision.lane, 'curator_contact_handoff_packet')

    def test_stackoverflow_exhaustion_prefers_primary_repo_flat_publisher_packet_over_hold(self):
        now = datetime(2026, 5, 25, 7, 21, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            execution_board.write_text('# board\n', encoding='utf-8')
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json', 'primary_repo_flat_contact_discovery_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': False}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=['vivy-yi/awesome-agent-orchestration']),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=['vivy-yi/awesome-agent-orchestration']),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=['ToolChase']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['ToolChase', 'Beam']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[6, 7]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=2),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 8, 24, 28)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=False),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 8.0}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        self.assertIn('stackoverflow recovery lane is exhausted', decision.reason.lower())

    def test_holds_primary_repo_flat_publisher_packet_when_all_targets_were_contacted_recently(self):
        now = datetime(2026, 5, 24, 5, 55, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "needs_repair",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "needs_execution"},
                {"failure_type": "same_family_outreach_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            primary_repo_flat_contact_discovery = log_dir / 'primary_repo_flat_contact_discovery_latest.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            primary_repo_flat_contact_discovery.write_text(
                json.dumps({
                    'targets': [
                        {'target': 'AXME Code', 'channels': [{'type': 'email', 'value': 'contact@axme.ai'}]},
                        {'target': 'WyeWorks', 'channels': [{'type': 'email', 'value': 'hello@wyeworks.com'}]},
                    ]
                }),
                encoding='utf-8',
            )
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

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', primary_repo_flat_contact_discovery),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=7),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=19),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=4),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[20, 38]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=5),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(True, '2026-05-24T11:24:37.256862')),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', side_effect=lambda *args, **kwargs: kwargs.get('action_types') == distribution_lane_selector.RECENT_PROOF_ASSET_ACTION_TYPES),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertNotEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        joined = '\n'.join(decision.reasons).lower()
        self.assertIn('fresh publisher outreach already shipped', joined)
        self.assertIn('all currently discovered publisher-contact targets already have fresh outreach', joined)

    def test_primary_repo_flat_contact_form_submission_counts_as_recent_publisher_outreach(self):
        now = datetime(2026, 5, 25, 2, 30, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_outreach_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            primary_repo_flat_contact_discovery = log_dir / 'primary_repo_flat_contact_discovery_latest.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            primary_repo_flat_contact_discovery.write_text(
                json.dumps({
                    'targets': [
                        {'target': 'ToolChase', 'channels': [{'type': 'email', 'value': 'hello@toolchase.com'}]},
                    ]
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-25_toolchase_contact_form_submission.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T02:24:28+02:00',
                    'action_type': 'publisher_contact_form_submission',
                    'target': 'ToolChase',
                    'status': 'executed',
                    'ok': True,
                }),
                encoding='utf-8',
            )

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', primary_repo_flat_contact_discovery),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=7),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=19),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=4),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[20, 38]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=5),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=False),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', side_effect=lambda *args, **kwargs: kwargs.get('action_types') == distribution_lane_selector.RECENT_PROOF_ASSET_ACTION_TYPES),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertNotEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        joined = '\n'.join(decision.reasons).lower()
        self.assertIn('fresh publisher outreach already shipped', joined)
        self.assertIn('toolchase', joined)
        self.assertIn('all currently discovered publisher-contact targets already have fresh outreach', joined)

    def test_primary_repo_flat_non_executable_targets_do_not_keep_lane_actionable(self):
        now = datetime(2026, 5, 24, 6, 15, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "needs_repair",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "needs_execution"},
                {"failure_type": "same_family_outreach_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            primary_repo_flat_contact_discovery = log_dir / 'primary_repo_flat_contact_discovery_latest.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            primary_repo_flat_contact_discovery.write_text(
                json.dumps({
                    'targets': [
                        {
                            'target': 'ctxt.dev / Signum',
                            'channels': [
                                {'type': 'website', 'value': 'https://ctxt.dev/'},
                                {'type': 'telegram', 'value': 'https://t.me/ctxtdev'},
                            ],
                        },
                    ]
                }),
                encoding='utf-8',
            )

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', primary_repo_flat_contact_discovery),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=7),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=19),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=4),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[20, 38]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=5),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(True, '2026-05-24T11:24:37.256862')),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', side_effect=lambda *args, **kwargs: kwargs.get('action_types') == distribution_lane_selector.RECENT_PROOF_ASSET_ACTION_TYPES),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertNotEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        joined = '\n'.join(decision.reasons).lower()
        self.assertNotIn('package that codeberg-first outreach instead of ending at measurement hold', joined)
        self.assertIn('non-runtime-executable channels', joined)
        self.assertIn('ctxt.dev / signum'.lower(), joined)

    def test_primary_repo_flat_current_packet_reason_enforces_follow_through_instead_of_repackaging(self):
        now = datetime(2026, 5, 24, 20, 18, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_outreach_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            primary_repo_flat_contact_discovery = log_dir / 'primary_repo_flat_contact_discovery_latest.json'
            primary_repo_flat_contact_handoff = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            primary_repo_flat_contact_discovery.write_text(
                json.dumps({
                    'targets': [
                        {
                            'target': 'ctxt.dev / Signum',
                            'channels': [
                                {'type': 'website', 'value': 'https://ctxt.dev/contact', 'label': 'common contact/about path'},
                            ],
                        },
                    ]
                }),
                encoding='utf-8',
            )
            primary_repo_flat_contact_handoff.write_text(
                '# packet\n\n### 1. ctxt.dev / Signum\n',
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-24_primary_repo_flat_contact_handoff_packet_execution.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T18:18:26',
                    'chosen_action': {'type': 'primary_repo_flat_contact_handoff_packet_execution'},
                    'result': {'status': 'prepared', 'ok': True},
                }),
                encoding='utf-8',
            )

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', primary_repo_flat_contact_discovery),
                    patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_HANDOFF_LATEST_PATH', primary_repo_flat_contact_handoff),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[7, 9]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=4),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 2, 5, 5)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=False),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        joined = '\n'.join(decision.reasons).lower()
        self.assertIn('non-runtime-executable channels (ctxt.dev / signum)', joined)
        self.assertNotIn('enforce follow-through instead of pretending a fresh packet is needed', joined)
        self.assertNotIn('package that codeberg-first outreach instead of ending at measurement hold', joined)
        self.assertNotIn('all currently discovered publisher-contact targets already have fresh outreach', joined)


    def test_stackoverflow_measurement_pending_counts_reused_existing_draft(self):
        now = datetime(2026, 5, 24, 11, 25, 0)
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
                },
            }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', latest_path):
                pending = distribution_lane_selector._stack_overflow_measurement_pending(now)

        self.assertTrue(pending)


    def test_repeated_distribution_architecture_repairs_trigger_third_strike_reason(self):
        now = datetime(2026, 5, 25, 4, 21, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_outreach_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            execution_board.write_text(
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
            (log_dir / 'marketing_2026-05-24_234934_active_loop_prompt_repair.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T23:49:34',
                    'chosen_action': {'type': 'active_loop_prompt_repair'},
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-24_235759_post_hold_reentry_contract_repair.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T23:57:59',
                    'chosen_action': {'type': 'post_hold_reentry_contract_repair'},
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-25_031100_measurement_hold_third_strike_guard_repair.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T03:11:00+02:00',
                    'chosen_action': {'type': 'measurement_hold_third_strike_guard_repair'},
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=True),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[6, 7]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=5),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 7, 20, 16)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('third event', decision.reason.lower())
        self.assertIn('2 prior distribution-architecture repair run(s)', '\n'.join(decision.reasons))

    def test_execution_board_empty_check_ignores_stale_empty_line_when_manual_asset_exists(self):
        now = datetime(2026, 5, 25, 5, 20, 0)

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
                    'measurement_window': {
                        'review_at': '2026-05-31T08:20:00+02:00'
                    },
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', board_path):
                empty = distribution_lane_selector._execution_board_has_no_truthful_do_now_packet(now)

        self.assertFalse(empty)


    def test_delivered_manual_outreach_asset_no_longer_counts_as_waiting_execution(self):
        now = datetime(2026, 5, 25, 5, 49, 0)

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
                    'measurement_window': {
                        'review_at': '2026-05-31T08:20:00+02:00'
                    },
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-25_manual_outreach_asset_follow_through_delivery.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T05:39:00+02:00',
                    'chosen_action': {
                        'type': 'manual_outreach_asset_follow_through',
                        'channel': 'current_chat_manual_handoff',
                        'title': 'Delivered ctxt.dev / Signum manual outreach asset to current chat',
                        'draft': str(asset_path),
                    },
                    'result': {
                        'status': 'delivered_to_current_chat',
                        'ok': True,
                        'manual_handoff_required': True,
                        'next_review_at': '2026-05-31T00:00:00+02:00',
                    },
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', board_path):
                assets = distribution_lane_selector._manual_outreach_assets_waiting_for_execution(now)
                empty = distribution_lane_selector._execution_board_has_no_truthful_do_now_packet(now)

        self.assertEqual(assets, [])
        self.assertTrue(empty)

    def test_existing_manual_outreach_asset_beats_measurement_hold(self):
        now = datetime(2026, 5, 25, 5, 20, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "needs_repair",
            "repair_actions": [
                {"failure_type": "same_family_outreach_overlap", "repair_state": "needs_execution"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            audit_path = log_dir / 'audit.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
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
                    'measurement_window': {
                        'review_at': '2026-05-31T08:20:00+02:00'
                    },
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=True),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[2, 6]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=5),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 7, 20, 16)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(True, True)),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'manual_outreach_asset_follow_through')
        self.assertIn('channel-ready manual publisher outreach asset already exists', decision.reason)


if __name__ == '__main__':
    unittest.main()
