import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agents.marketing import distribution_lane_selector


class DistributionLaneSelectorRepairPauseTests(unittest.TestCase):
    def test_primary_repo_flat_contact_path_counts_as_manual_executable(self):
        self.assertTrue(distribution_lane_selector._publisher_target_has_manual_executable_channel([
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
            (log_dir / 'marketing_2026-05-24_curator_handoff_packet_execution.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T08:40:00',
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
                                {'type': 'website', 'value': 'https://ctxt.dev/about'},
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


if __name__ == '__main__':
    unittest.main()
