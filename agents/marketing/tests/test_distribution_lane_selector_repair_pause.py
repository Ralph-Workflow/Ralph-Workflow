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
    @staticmethod
    def _board_fingerprint(execution_board: Path) -> str:
        return distribution_lane_selector.hashlib.sha1(
            distribution_lane_selector._normalized_execution_board_text(
                execution_board.read_text(encoding='utf-8')
            ).encode('utf-8')
        ).hexdigest()

    def test_distribution_architecture_repair_state_ignores_lane_decision_logs_for_guard_pause_counts(self):
        now = datetime(2026, 5, 26, 20, 2, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            fingerprint = 'abc123'
            artifact_path = '/tmp/guard-pause.md'
            (log_dir / 'marketing_2026-05-26_200200_distribution_lane_switch.json').write_text(
                json.dumps({
                    'timestamp': now.isoformat(),
                    'run_type': 'marketing-distribution-lane',
                    'chosen_action': {
                        'type': 'distribution_lane_switch',
                        'channel': 'distribution_architecture_guard_pause',
                    },
                    'verification': {'execution_board_fingerprint': fingerprint},
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-26_200201_distribution_execution.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-26T20:02:01',
                    'chosen_action': {
                        'type': 'distribution_architecture_guard_pause',
                        'draft': artifact_path,
                    },
                    'verification': {'execution_board_fingerprint': fingerprint},
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-26_200159_summary.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-26T20:01:59',
                    'distribution_execution': {
                        'action_type': 'distribution_architecture_guard_pause',
                        'artifact_path': artifact_path,
                    },
                    'verification': {'execution_board_fingerprint': fingerprint},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, '_execution_board_fingerprint', return_value=fingerprint):
                state = distribution_lane_selector._distribution_architecture_repair_state(
                    now,
                    release_at=datetime(2026, 5, 26, 20, 55, 18),
                )

        self.assertEqual(state['guard_pause_count'], 1)
        self.assertEqual(state['cumulative_guard_pause_count'], 1)

    def test_distribution_architecture_repair_state_requires_matching_fingerprint_for_legacy_guard_logs(self):
        now = datetime(2026, 5, 26, 21, 37, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            current_fingerprint = 'current-board'
            (log_dir / 'marketing_2026-05-25_072249_distribution_architecture_guard_follow_through.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T07:22:49',
                    'chosen_action': {'type': 'distribution_architecture_guard_follow_through'},
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-25_103900_distribution_architecture_guard_pause.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T10:39:00',
                    'chosen_action': {'type': 'distribution_architecture_guard_pause'},
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-26_distribution_architecture_repair_execution.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-26T21:26:55',
                    'chosen_action': {'type': 'distribution_architecture_churn_guard_repair'},
                    'verification': {'execution_board_fingerprint': current_fingerprint},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, '_execution_board_fingerprint', return_value=current_fingerprint):
                state = distribution_lane_selector._distribution_architecture_repair_state(
                    now,
                    release_at=datetime(2026, 5, 26, 22, 47, 35),
                )

        self.assertTrue(state['guard_installed'])
        self.assertEqual(state['repeat_count'], 1)
        self.assertEqual(state['guard_follow_through_count'], 0)
        self.assertEqual(state['guard_pause_count'], 0)
        self.assertEqual(state['cumulative_guard_pause_count'], 0)

    def test_distribution_architecture_repair_state_reuses_same_window_guard_follow_through_despite_fingerprint_drift(self):
        now = datetime(2026, 5, 26, 21, 56, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            current_fingerprint = 'current-board'
            (log_dir / 'marketing_2026-05-26_distribution_architecture_churn_guard_repair.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-26T21:26:55',
                    'chosen_action': {'type': 'distribution_architecture_churn_guard_repair'},
                    'verification': {'execution_board_fingerprint': current_fingerprint},
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-26_distribution_architecture_guard_follow_through_execution.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-26T21:37:00',
                    'chosen_action': {'type': 'distribution_architecture_guard_follow_through'},
                    'why_this_action': {
                        'summary': 'Guard follow-through already ran in this short window.',
                    },
                    'verification': {'execution_board_fingerprint': 'prior-board'},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, '_execution_board_fingerprint', return_value=current_fingerprint):
                state = distribution_lane_selector._distribution_architecture_repair_state(
                    now,
                    release_at=datetime(2026, 5, 26, 22, 47, 35),
                )

        self.assertTrue(state['guard_installed'])
        self.assertEqual(state['guard_follow_through_count'], 1)
        self.assertEqual(state['current_guard_follow_through_count'], 1)
        self.assertEqual(state['recent_guard_follow_through_count'], 1)

    def test_apollo_status_blocked_ignores_ancillary_cloudflare_notes_when_login_succeeded(self):
        payload = {
            'status': 'login_succeeded',
            'cloudflare_blocked': False,
            'notes': 'Cloudflare interstitial detected in response body from https://app.apollo.io/. Background Cloudflare challenges were seen on ancillary Apollo requests, but the authenticated UI remained usable.',
            'browserless_probe_status': None,
        }
        self.assertFalse(distribution_lane_selector._apollo_status_blocked(payload))

    def test_load_recent_monitor_summary_clears_reddit_blocked_when_browser_session_is_ready(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            latest_md = tmp / 'reddit_monitor_latest.md'
            latest_md.write_text(
                'reddit is ip-blocked\n'
                '**Search diagnostics:** reddit_ip_blocked=1, ok=0\n'
                '**Shortlisted:** 2\n',
                encoding='utf-8',
            )
            log_dir = tmp / 'logs'
            log_dir.mkdir()
            (log_dir / 'reddit_execution_status_latest.json').write_text(json.dumps({
                'generated_at': datetime.now().astimezone().isoformat(),
                'status': 'browser_session_ready',
            }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'REDDIT_EXECUTION_STATUS_PATH', log_dir / 'reddit_execution_status_latest.json'):
                summary = distribution_lane_selector._load_recent_monitor_summary()

        self.assertFalse(summary['reddit_blocked'])
        self.assertEqual(summary['execution_status'], 'browser_session_ready')

    def test_reddit_autopost_cooldown_active_only_while_next_safe_post_is_in_future(self):
        now = datetime(2026, 5, 26, 18, 16, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            state_path = tmp / 'reddit_autopost_state.json'
            state_path.write_text(json.dumps({
                'last_attempt_status': 'cooldown_skip',
                'next_safe_post_at': '2026-05-26T20:56:18',
            }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'REDDIT_AUTOPOST_STATE_PATH', state_path):
                self.assertTrue(distribution_lane_selector._reddit_autopost_cooldown_active(now))
                self.assertFalse(distribution_lane_selector._reddit_autopost_cooldown_active(datetime(2026, 5, 26, 20, 56, 18)))

    def _choose_lane_with_empty_board_reddit_ready(self, now: datetime, *, autopost_state: dict, report_text: str | None = None) -> str:
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {"repair_window_status": "inactive", "repair_actions": []}

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
            reddit_execution_status = log_dir / 'reddit_execution_status_latest.json'
            reddit_autopost_state = log_dir / 'reddit_autopost_state.json'
            reddit_monitor_latest = tmp / 'reddit_monitor_latest.md'

            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
            reddit_execution_status.write_text(json.dumps({
                'generated_at': now.isoformat(),
                'status': 'browser_session_ready',
            }), encoding='utf-8')
            reddit_autopost_state.write_text(json.dumps(autopost_state), encoding='utf-8')
            reddit_monitor_latest.write_text(
                report_text
                or '## Today’s bottom line\n- **Yes**, I found **0** credible discussion opportunities.\n',
                encoding='utf-8',
            )

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, 'REDDIT_EXECUTION_STATUS_PATH', reddit_execution_status),
                    patch.object(distribution_lane_selector, 'REDDIT_AUTOPOST_STATE_PATH', reddit_autopost_state),
                    patch.object(distribution_lane_selector, 'REDDIT_MONITOR_LATEST', reddit_monitor_latest),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[{'id': 'a'}, {'id': 'b'}]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': False, 'reddit_blocked': False, 'partial_visibility_only': False}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': False}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=0),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_curator_contact_packet_already_delivered', return_value=False),
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_recent_curator_queue_contact_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_pending_confirmation_actions', return_value=[]),
                    patch.object(distribution_lane_selector, '_pending_confirmation_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_recent_prep_count', return_value=0),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(0, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[0, 0]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=False),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 0, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_due_curator_followup_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_execution_board_has_no_truthful_do_now_packet', return_value=True),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        return decision.lane

    def test_reddit_cooldown_blocks_reddit_execution_check_override(self):
        lane = self._choose_lane_with_empty_board_reddit_ready(
            datetime(2026, 5, 26, 18, 16, 0),
            autopost_state={
                'last_attempt_status': 'cooldown_skip',
                'next_safe_post_at': '2026-05-26T20:56:18',
            },
        )

        self.assertEqual(lane, 'curator_outreach')

    def test_report_guard_blocks_reddit_execution_check_override(self):
        lane = self._choose_lane_with_empty_board_reddit_ready(
            datetime(2026, 5, 26, 20, 58, 0),
            autopost_state={
                'last_attempt_status': 'report_guard_skip',
                'last_detail': 'report_coverage_unhealthy; report_partial_coverage; mention_fit_below_medium',
            },
            report_text=(
                '## Today’s bottom line\n'
                '- **Important telemetry note**: some Reddit queries were blocked (**reddit_ip_blocked=4**), but other queries still returned usable results (**ok=4**). Treat this as partial coverage, not a total Reddit outage.\n\n'
                '### 1) Example thread\n'
                '- URL: https://www.reddit.com/r/AI_Agents/comments/example/\n'
                '- Community: `r/AI_Agents`\n'
                '- Freshness: during this pass\n'
                '- Direct reply fit: **high**\n'
                '- Mention fit: **medium-low**\n'
                '- Best RalphWorkflow angle: **content-family match: production_failure**\n'
            ),
        )

        self.assertEqual(lane, 'curator_outreach')

    def test_prepared_only_primary_repo_flat_repeat_prefers_truthful_measurement_hold(self):
        now = datetime(2026, 5, 26, 18, 16, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {"repair_window_status": "inactive", "repair_actions": []}

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
            reddit_execution_status = log_dir / 'reddit_execution_status_latest.json'

            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
            reddit_execution_status.write_text(json.dumps({
                'generated_at': now.isoformat(),
                'status': 'cooldown_skip',
            }), encoding='utf-8')

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, 'REDDIT_EXECUTION_STATUS_PATH', reddit_execution_status),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[{'id': 'a'}, {'id': 'b'}]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': False}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=0),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_curator_contact_packet_already_delivered', return_value=False),
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_recent_curator_queue_contact_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_pending_confirmation_actions', return_value=[]),
                    patch.object(distribution_lane_selector, '_pending_confirmation_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['ToolWise']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_recent_prep_count', return_value=distribution_lane_selector.PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_THRESHOLD),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_prepared_only_family_repeat_count', return_value=distribution_lane_selector.PRIMARY_REPO_FLAT_PACKET_PREP_REPEAT_THRESHOLD),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(0, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[0, 0]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=distribution_lane_selector.SHORT_REVIEW_WINDOW_EXTERNAL_ACTION_THRESHOLD),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 26, 20, 0, 0)),
                    patch.object(distribution_lane_selector, '_execution_board_short_review_release_at', return_value=datetime(2026, 5, 26, 20, 0, 0)),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_publisher_outreach_paused_by_repair_window', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=False),
                    patch.object(
                        distribution_lane_selector,
                        '_recent_executed_action_type',
                        side_effect=lambda _now, action_types=None, hours=24: bool(
                            action_types == distribution_lane_selector.RECENT_PROOF_ASSET_ACTION_TYPES
                        ),
                    ),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 0, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_directory_secondary_surface_repair_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_directory_secondary_surface_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_directory_secondary_surface_followup_window', return_value={}),
                    patch.object(distribution_lane_selector, '_directory_secondary_surface_followup_active', return_value=False),
                    patch.object(distribution_lane_selector, '_due_curator_followup_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_execution_board_has_no_truthful_do_now_packet', return_value=True),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={'repeat_count': 0, 'third_strike': False, 'guard_installed': False, 'guard_follow_through_count': 0, 'guard_pause_count': 0, 'cumulative_guard_pause_count': 0}),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': False}),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'measurement_hold')
        self.assertIn('prepared-only packet churn', decision.reason)

    def test_primary_repo_flat_accepts_only_truthful_manual_contact_routes(self):
        self.assertTrue(distribution_lane_selector._publisher_target_has_manual_executable_channel([
            {'type': 'website', 'value': 'https://example.com/contact?via=tally.so', 'label': 'contact form'},
        ]))
        self.assertTrue(distribution_lane_selector._publisher_target_has_manual_executable_channel([
            {'type': 'telegram', 'value': 'https://t.me/ctxtdev', 'label': 'Telegram'},
        ]))
        self.assertTrue(distribution_lane_selector._publisher_target_has_manual_executable_channel([
            {'type': 'website', 'value': 'https://ctxt.dev/contact', 'label': 'contact page'},
        ]))
        self.assertFalse(distribution_lane_selector._publisher_target_has_manual_executable_channel([
            {'type': 'website', 'value': 'https://ctxt.dev/', 'label': 'website'},
        ]))
        self.assertTrue(distribution_lane_selector._publisher_target_has_manual_executable_channel([
            {'type': 'website', 'value': 'https://aisaying.net', 'label': 'feedback form'},
        ]))

    def test_primary_repo_flat_waiting_targets_treat_github_issue_only_as_non_executable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            discovery_path = tmp / 'primary_repo_flat_contact_discovery_latest.json'
            discovery_path.write_text(
                json.dumps({
                    'targets': [
                        {
                            'target': 'TLDL',
                            'recommended_next_step': 'GitHub issue/PR path is now identified',
                            'channels': [
                                {'type': 'github_issue', 'value': 'https://github.com/shenli/tldl/issues/new', 'label': 'GitHub issue'},
                            ],
                        },
                        {
                            'target': 'ctxt.dev / Signum',
                            'recommended_next_step': 'Telegram consulting contact path is explicitly confirmed',
                            'channels': [
                                {'type': 'telegram', 'value': 'https://t.me/ctxtdev', 'label': 'Telegram'},
                                {'type': 'github_issue', 'value': 'https://github.com/heurema/signum/issues/new', 'label': 'GitHub issue'},
                            ],
                        },
                    ],
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', tmp), \
                 patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path):
                self.assertEqual(
                    distribution_lane_selector._primary_repo_flat_contact_targets_waiting_for_execution(),
                    [],
                )
                self.assertEqual(
                    distribution_lane_selector._primary_repo_flat_non_executable_targets_waiting_for_execution(),
                    ['TLDL', 'ctxt.dev / Signum'],
                )

    def test_primary_repo_flat_manual_review_targets_require_truthful_manual_executable_route(self):
        now = datetime(2026, 5, 27, 20, 5, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            discovery_path = tmp / 'primary_repo_flat_contact_discovery_latest.json'
            discovery_path.write_text(
                json.dumps({
                    'targets': [
                        {
                            'target': 'ComputingForGeeks',
                            'channels': [
                                {'type': 'website', 'value': 'https://computingforgeeks.com/about', 'label': 'about page'},
                                {'type': 'github_issue', 'value': 'https://github.com/nicepkg/oh-my-openagent/issues/new', 'label': 'GitHub issue'},
                                {'type': 'x', 'value': 'https://twitter.com/jj_mutai', 'label': 'X/Twitter'},
                            ],
                        },
                        {
                            'target': 'ctxt.dev / Signum',
                            'channels': [
                                {'type': 'telegram', 'value': 'https://t.me/ctxtdev', 'label': 'Telegram'},
                            ],
                        },
                    ],
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', tmp), \
                 patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path), \
                 patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=set()), \
                 patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value=set()):
                self.assertEqual(
                    distribution_lane_selector._primary_repo_flat_manual_review_targets_waiting_for_execution(now),
                    ['ctxt.dev / Signum'],
                )

    def test_primary_repo_flat_manual_review_targets_accept_contact_page_route(self):
        now = datetime(2026, 5, 27, 20, 5, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            discovery_path = tmp / 'primary_repo_flat_contact_discovery_latest.json'
            discovery_path.write_text(
                json.dumps({
                    'targets': [
                        {
                            'target': 'ComputingForGeeks',
                            'channels': [
                                {'type': 'website', 'value': 'https://computingforgeeks.com/contact', 'label': 'contact page'},
                                {'type': 'x', 'value': 'https://twitter.com/jj_mutai', 'label': 'X/Twitter'},
                            ],
                        },
                    ],
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', tmp), \
                 patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path), \
                 patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=set()), \
                 patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value=set()):
                self.assertEqual(
                    distribution_lane_selector._primary_repo_flat_manual_review_targets_waiting_for_execution(now),
                    ['ComputingForGeeks'],
                )

    def test_primary_repo_flat_manual_review_asset_current_requires_matching_targets(self):
        now = datetime(2026, 5, 27, 20, 5, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            drafts_dir.mkdir()
            asset_path = drafts_dir / 'primary_repo_flat_manual_review_asset_latest.md'
            asset_path.write_text('# empty asset\n', encoding='utf-8')

            with patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir):
                self.assertFalse(
                    distribution_lane_selector._primary_repo_flat_manual_review_asset_current(now, ['ComputingForGeeks'])
                )

            asset_path.write_text(
                '# manual asset\n\n### 1. ComputingForGeeks\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir):
                self.assertTrue(
                    distribution_lane_selector._primary_repo_flat_manual_review_asset_current(now, ['ComputingForGeeks'])
                )

    def test_handoff_packet_allow_superset_matches_executor_truth(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            packet = Path(tmpdir) / 'primary_repo_flat_contact_handoff_packet_latest.md'
            packet.write_text(
                '# packet\n\n'
                '- ToolWise — https://toolwise.ai/tools/ralph-workflow\n\n'
                '### 1. TIMEWELL\n\n'
                '### 2. Codivox\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, '_live_listing_proof_rows', return_value=[]):
                self.assertTrue(distribution_lane_selector._handoff_packet_is_current(
                    packet,
                    ['TIMEWELL', 'Codivox'],
                    require_live_listing_proof=True,
                    allow_superset=True,
                ))
                self.assertFalse(distribution_lane_selector._handoff_packet_is_current(
                    packet,
                    ['TIMEWELL', 'Codivox', 'Toolradar'],
                    require_live_listing_proof=True,
                    allow_superset=True,
                ))

    def test_recent_curator_queue_contact_targets_include_recent_email_fallbacks(self):
        now = datetime(2026, 5, 25, 9, 10, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            queue_path = tmp / 'curator_outreach_queue_latest.json'
            queue_path.write_text(
                json.dumps({
                    'targets': [
                        {
                            'target': 'TIMEWELL benchmark — AI Coding Tools Compared [Latest 2026]',
                            'status': 'sent_via_email_fallback',
                            'last_contact_at': '2026-05-24T11:03:16.790735+00:00',
                        },
                        {
                            'target': 'Old Target',
                            'status': 'sent_via_email_fallback',
                            'last_contact_at': '2026-05-10T11:03:16.790735+00:00',
                        },
                    ],
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, '_curator_queue_path', return_value=queue_path):
                targets = distribution_lane_selector._recent_curator_queue_contact_targets(now, days=7)

        self.assertIn('TIMEWELL', targets)
        self.assertNotIn('Old Target', targets)

    def test_manual_outreach_assets_waiting_prefers_current_primary_repo_flat_packet(self):
        now = datetime(2026, 5, 25, 17, 8, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            discovery_path = log_dir / 'primary_repo_flat_contact_discovery_latest.json'
            discovery_path.write_text(
                json.dumps({
                    'targets': [
                        {
                            'target': 'TIMEWELL',
                            'channels': [{'type': 'email', 'value': 'timewell@timewell.jp'}],
                        },
                        {
                            'target': 'Toolradar',
                            'channels': [{'type': 'email', 'value': 'editorial@toolradar.com'}],
                        },
                        {
                            'target': 'Morph',
                            'channels': [{'type': 'email', 'value': 'info@morphllm.com'}],
                        },
                    ],
                }),
                encoding='utf-8',
            )
            packet_path = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            packet_path.write_text(
                '# packet\n\n'
                '- ToolWise — https://toolwise.ai/tools/ralph-workflow\n\n'
                '### 1. TIMEWELL\n\n'
                '### 2. Toolradar\n\n'
                '### 3. Morph\n',
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-25_primary_repo_flat_contact_handoff_packet_execution.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T15:04:46+02:00',
                    'chosen_action': {'type': 'primary_repo_flat_contact_handoff_packet_execution'},
                    'result': {'status': 'prepared', 'ok': True},
                }),
                encoding='utf-8',
            )
            legacy_asset = drafts_dir / 'reddit_discussion_handoff_packet_latest.md'
            legacy_asset.write_text('# reddit packet\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-25_reddit_discussion_channel_ready_outreach_asset.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T14:08:00+02:00',
                    'type': 'reddit_discussion_channel_ready_outreach_asset',
                    'chosen_action': {
                        'channel': 'manual_contact_asset',
                        'artifact': str(legacy_asset),
                        'title': 'Prepare Reddit discussion handoff packet',
                    },
                    'measurement_window': {'review_at': '2026-06-01T14:08:00+02:00'},
                    'result': {'status': 'executed', 'artifact': str(legacy_asset)},
                }),
                encoding='utf-8',
            )

            with ExitStack() as stack:
                stack.enter_context(patch.object(distribution_lane_selector, 'LOG_DIR', log_dir))
                stack.enter_context(patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir))
                stack.enter_context(patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path))
                assets = distribution_lane_selector._manual_outreach_assets_waiting_for_execution(now)

        self.assertEqual(assets[0]['artifact_path'], str(packet_path))
        self.assertEqual(assets[0]['targets'], ['TIMEWELL', 'Toolradar', 'Morph'])

    def test_primary_repo_flat_manual_asset_is_hidden_when_post_hold_only_and_repeat_blocked(self):
        now = datetime(2026, 5, 26, 15, 7, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-26T20:55:18\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
            packet_path = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            packet_path.write_text('# packet\n', encoding='utf-8')

            with ExitStack() as stack:
                stack.enter_context(patch.object(distribution_lane_selector, 'LOG_DIR', log_dir))
                stack.enter_context(patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir))
                stack.enter_context(patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board))
                stack.enter_context(patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]))
                stack.enter_context(patch.object(distribution_lane_selector, '_recent_curator_queue_contact_targets', return_value=set()))
                stack.enter_context(patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value=set()))
                stack.enter_context(patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['TLDL']))
                stack.enter_context(patch.object(distribution_lane_selector, '_handoff_packet_is_current', return_value=True))
                stack.enter_context(patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False))
                stack.enter_context(patch.object(distribution_lane_selector, '_primary_repo_flat_recent_prep_count', return_value=2))
                assets = distribution_lane_selector._manual_outreach_assets_waiting_for_execution(now)

        self.assertEqual(assets, [])

    def test_primary_repo_flat_manual_asset_reappears_after_post_hold_release_even_if_repeat_blocked(self):
        now = datetime(2026, 5, 26, 21, 7, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-26T20:55:18\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
            packet_path = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            packet_path.write_text('# packet\n', encoding='utf-8')

            with ExitStack() as stack:
                stack.enter_context(patch.object(distribution_lane_selector, 'LOG_DIR', log_dir))
                stack.enter_context(patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir))
                stack.enter_context(patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board))
                stack.enter_context(patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]))
                stack.enter_context(patch.object(distribution_lane_selector, '_recent_curator_queue_contact_targets', return_value=set()))
                stack.enter_context(patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value=set()))
                stack.enter_context(patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['TLDL']))
                stack.enter_context(patch.object(distribution_lane_selector, '_handoff_packet_is_current', return_value=True))
                stack.enter_context(patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False))
                stack.enter_context(patch.object(distribution_lane_selector, '_primary_repo_flat_recent_prep_count', return_value=2))
                assets = distribution_lane_selector._manual_outreach_assets_waiting_for_execution(now)

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]['artifact_path'], str(packet_path))

    def test_choose_distribution_lane_prefers_primary_repo_flat_followthrough_over_generic_manual_asset(self):
        now = datetime(2026, 5, 25, 17, 27, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_outreach_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_publisher_overlap", "repair_state": "pending_measurement"},
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
            primary_packet = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            reddit_packet = drafts_dir / 'reddit_discussion_handoff_packet_latest.md'
            primary_packet.write_text('# primary packet\n', encoding='utf-8')
            reddit_packet.write_text('# reddit packet\n', encoding='utf-8')
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
                    patch.object(distribution_lane_selector, '_curator_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_curator_contact_packet_already_delivered', return_value=False),
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_recent_curator_queue_contact_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['TIMEWELL', 'Toolradar', 'Morph']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 2]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=distribution_lane_selector.SHORT_REVIEW_WINDOW_EXTERNAL_ACTION_THRESHOLD),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, True)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(
                        distribution_lane_selector,
                        '_recent_executed_action_type',
                        side_effect=lambda _now, action_types=None, hours=24: bool(
                            action_types and 'repo_conversion_proof_asset' in action_types
                        ),
                    ),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_due_curator_followup_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[
                        {
                            'target': 'TIMEWELL, Toolradar, Morph',
                            'targets': ['TIMEWELL', 'Toolradar', 'Morph'],
                            'artifact_path': str(primary_packet),
                            'title': 'Primary-repo-flat publisher contact packet',
                        },
                        {
                            'target': 'reddit discussion handoff packet latest',
                            'artifact_path': str(reddit_packet),
                            'title': 'Prepare Reddit discussion handoff packet',
                        },
                    ]),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        self.assertIn('codeberg-first publisher contact packet already exists', decision.reason.lower())

    def test_choose_distribution_lane_re_repair_when_release_window_moves_after_guard_pause(self):
        now = datetime(2026, 5, 27, 15, 27, 19)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "same_family_publisher_overlap", "repair_state": "pending_measurement"},
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

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[{'id': 'a'}, {'id': 'b'}]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json', 'marketing_execution_board_latest.md']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=True),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-01T23:11:13+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_curator_contact_packet_already_delivered', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_recent_curator_queue_contact_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_recent_prep_count', return_value=0),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 1]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=distribution_lane_selector.SHORT_REVIEW_WINDOW_EXTERNAL_ACTION_THRESHOLD),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 27, 18, 35, 8)),
                    patch.object(distribution_lane_selector, '_execution_board_short_review_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_publisher_outreach_paused_by_repair_window', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=False),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 3, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_directory_secondary_surface_repair_targets', return_value=['ToolHunt']),
                    patch.object(distribution_lane_selector, '_directory_secondary_surface_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_directory_secondary_surface_followup_window', return_value={'review_at': datetime(2026, 5, 31, 0, 0, 0)}),
                    patch.object(distribution_lane_selector, '_directory_secondary_surface_followup_active', return_value=True),
                    patch.object(distribution_lane_selector, '_due_curator_followup_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_execution_board_has_no_truthful_do_now_packet', return_value=True),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={
                        'repeat_count': 1,
                        'third_strike': True,
                        'guard_installed': True,
                        'guard_follow_through_count': 0,
                        'guard_pause_count': 1,
                        'cumulative_guard_pause_count': 1,
                        'latest_matching_at': datetime(2026, 5, 27, 15, 24, 1),
                        'latest_matching_release_at': datetime(2026, 5, 27, 15, 26, 31),
                        'latest_guard_pause_release_at': datetime(2026, 5, 27, 15, 26, 31),
                        'earliest_guard_pause_at': datetime(2026, 5, 27, 15, 13, 45),
                    }),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': True}),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('release window changed', decision.reason.lower())

    def test_choose_distribution_lane_refreshes_board_when_rolling_short_window_extends_past_post_hold_release(self):
        now = datetime(2026, 5, 26, 20, 56, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_publisher_overlap", "repair_state": "pending_measurement"},
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
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[{'id': 'a'}]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json', 'marketing_execution_board_latest.md']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=True),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-02T07:23:34+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_curator_contact_packet_already_delivered', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_recent_curator_queue_contact_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['TIMEWELL']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_recent_prep_count', return_value=0),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 1]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=2),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 26, 22, 47, 35)),
                    patch.object(distribution_lane_selector, '_execution_board_short_review_release_at', return_value=datetime(2026, 5, 26, 20, 55, 18)),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, True)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=False),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 3, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_directory_secondary_surface_repair_targets', return_value=['ToolHunt']),
                    patch.object(distribution_lane_selector, '_directory_secondary_surface_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_directory_secondary_surface_followup_window', return_value={'review_at': datetime(2026, 5, 31, 0, 0, 0)}),
                    patch.object(distribution_lane_selector, '_directory_secondary_surface_followup_active', return_value=True),
                    patch.object(distribution_lane_selector, '_due_curator_followup_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={'repeat_count': 0, 'third_strike': False, 'guard_installed': False, 'guard_follow_through_count': 0, 'guard_pause_count': 0, 'cumulative_guard_pause_count': 0}),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': True}),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('extended the short review window', decision.reason)

    def test_choose_distribution_lane_escalates_repeated_primary_repo_flat_prepared_only_packet_to_distribution_architecture_repair(self):
        now = datetime(2026, 5, 26, 14, 12, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_publisher_overlap", "repair_state": "pending_measurement"},
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
            handoff_path = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            handoff_path.write_text(
                '# Ralph Workflow Primary-Repo-Flat Publisher Contact Packet\n\n'
                '## Execute these first\n'
                '### 1. TLDL\n'
                '- URL: https://www.tldl.io/resources/ai-coding-tools-2026\n',
                encoding='utf-8',
            )
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            for filename, timestamp in [
                ('marketing_2026-05-26_033641_primary_repo_flat_contact_handoff_packet_execution.json', '2026-05-26T03:36:41'),
                ('marketing_2026-05-26_141021_primary_repo_flat_contact_handoff_packet_execution.json', '2026-05-26T14:10:21'),
            ]:
                (log_dir / filename).write_text(
                    json.dumps({
                        'timestamp': timestamp,
                        'chosen_action': {'type': 'primary_repo_flat_contact_handoff_packet_execution'},
                        'why_this_action': {'targets_prepared': ['TLDL']},
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
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json', 'primary_repo_flat_contact_discovery_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-02T07:23:34+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_curator_contact_packet_already_delivered', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 1]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=1),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=False),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_due_curator_followup_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[
                        {
                            'target': 'TLDL',
                            'targets': ['TLDL'],
                            'artifact_path': str(handoff_path),
                            'title': 'Primary-repo-flat publisher contact packet',
                        },
                    ]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['TLDL']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'repo_conversion_proof_asset')
        self.assertIn('proof asset', decision.reason.lower())

    def test_choose_distribution_lane_escalates_primary_repo_flat_packet_family_churn_even_when_targets_drift(self):
        now = datetime(2026, 5, 26, 14, 12, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_publisher_overlap", "repair_state": "pending_measurement"},
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
            handoff_path = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            handoff_path.write_text(
                '# Ralph Workflow Primary-Repo-Flat Publisher Contact Packet\n\n'
                '## Execute these first\n'
                '### 1. TLDL\n'
                '- URL: https://www.tldl.io/resources/ai-coding-tools-2026\n',
                encoding='utf-8',
            )
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            for filename, timestamp, targets in [
                ('marketing_2026-05-26_033921_primary_repo_flat_contact_handoff_packet_execution.json', '2026-05-26T03:39:21', ['AI Saying']),
                ('marketing_2026-05-26_141021_primary_repo_flat_contact_handoff_packet_execution.json', '2026-05-26T14:10:21', ['TLDL']),
            ]:
                (log_dir / filename).write_text(
                    json.dumps({
                        'timestamp': timestamp,
                        'chosen_action': {'type': 'primary_repo_flat_contact_handoff_packet_execution'},
                        'why_this_action': {'targets_prepared': targets},
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
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json', 'primary_repo_flat_contact_discovery_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-02T07:23:34+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_curator_contact_packet_already_delivered', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 1]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=1),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=False),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_due_curator_followup_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[
                        {
                            'target': 'TLDL',
                            'targets': ['TLDL'],
                            'artifact_path': str(handoff_path),
                            'title': 'Primary-repo-flat publisher contact packet',
                        },
                    ]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['TLDL']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'repo_conversion_proof_asset')
        self.assertIn('proof asset', decision.reason.lower())

    def test_manual_outreach_asset_delivery_recognizes_current_chat_manual_delivery_logs(self):
        now = datetime(2026, 5, 25, 15, 11, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            artifact = drafts_dir / 'reddit_discussion_handoff_packet_latest.md'
            artifact.write_text('# packet\n', encoding='utf-8')
            delivered_source_mtime = datetime(2026, 5, 25, 14, 20, 0)
            os.utime(artifact, (delivered_source_mtime.timestamp(), delivered_source_mtime.timestamp()))
            (log_dir / 'marketing_2026-05-25_reddit_discussion_manual_delivery.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T14:24:00+02:00',
                    'type': 'reddit_discussion_manual_delivery',
                    'chosen_action': {
                        'channel': 'current_chat',
                        'packet': str(artifact),
                    },
                    'measurement_window': {
                        'review_at': '2026-06-01T14:24:00+02:00',
                    },
                    'result': {
                        'status': 'executed',
                        'artifact_reused': str(artifact),
                    },
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir):
                self.assertTrue(distribution_lane_selector._manual_outreach_asset_delivery_still_active(
                    artifact_path=str(artifact),
                    now=now,
                ))
                self.assertEqual(distribution_lane_selector._manual_outreach_assets_waiting_for_execution(now), [])

    def test_manual_outreach_asset_delivery_ignores_same_path_packet_refreshed_after_delivery(self):
        now = datetime(2026, 5, 25, 16, 40, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            artifact = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            artifact.write_text('# refreshed packet\n\n### 1. Toolradar\n', encoding='utf-8')
            refreshed_at = datetime(2026, 5, 25, 15, 4, 46)
            os.utime(artifact, (refreshed_at.timestamp(), refreshed_at.timestamp()))
            (log_dir / 'marketing_2026-05-25_primary_repo_flat_contact_manual_delivery_refresh.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T06:50:32+02:00',
                    'chosen_action': {
                        'type': 'primary_repo_flat_contact_manual_delivery_refresh',
                        'packet': str(artifact),
                    },
                    'measurement_window': {'review_at': '2026-06-01T06:50:32+02:00'},
                    'result': {
                        'status': 'executed',
                        'artifact': str(artifact),
                    },
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir):
                self.assertFalse(distribution_lane_selector._manual_outreach_asset_delivery_still_active(
                    artifact_path=str(artifact),
                    now=now,
                ))

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

    def test_recent_live_external_action_count_dedupes_legacy_email_when_subject_only_exists_inside_channel(self):
        now = datetime(2026, 5, 25, 9, 24, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            log_dir.mkdir()
            (log_dir / 'marketing_2026-05-25_070703_nxcode_publisher_email.json').write_text(
                json.dumps({
                    'timestamp_utc': '2026-05-25T07:07:03.084429+00:00',
                    'action': 'curator_email_outreach',
                    'status': 'sent',
                    'channel': {
                        'recipient': 'support@nxcode.io',
                        'subject': 'Ralph Workflow as a workflow-system addition to your AI coding tools comparison',
                    },
                    'body_file': '/tmp/nxcode.md',
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-25_nxcode_publisher_outreach.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T09:07:03+02:00',
                    'action_type': 'publisher_email_outreach',
                    'status': 'executed',
                    'ok': True,
                    'live_external_action': True,
                    'target': 'NxCode',
                    'recipient': 'support@nxcode.io',
                    'subject': 'Ralph Workflow as a workflow-system addition to your AI coding tools comparison',
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir):
                total = distribution_lane_selector._recent_live_external_action_count(now, hours=6)
                release_at = distribution_lane_selector._recent_live_external_window_release_at(now, hours=6)

        self.assertEqual(total, 1)
        self.assertEqual(release_at, datetime(2026, 5, 25, 15, 7, 3))

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

    def test_primary_repo_flat_handoff_packet_current_accepts_refreshed_matching_packet_when_recent_prep_log_is_still_valid(self):
        now = datetime(2026, 5, 25, 17, 4, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            handoff_path = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            (log_dir / 'marketing_2026-05-25_primary_repo_flat_contact_handoff_packet_execution.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T11:14:27.780471',
                    'chosen_action': {'type': 'primary_repo_flat_contact_handoff_packet_execution'},
                    'why_this_action': {'targets_prepared': ['TIMEWELL', 'Toolradar']},
                    'result': {'status': 'prepared', 'ok': True},
                }),
                encoding='utf-8',
            )
            handoff_path.write_text(
                '# Ralph Workflow Primary-Repo-Flat Publisher Contact Packet\n\n### 1. TIMEWELL\n\n### 2. Toolradar\n\n### 3. Morph\n',
                encoding='utf-8',
            )
            refreshed_at = datetime(2026, 5, 25, 15, 5, 9)
            os.utime(handoff_path, (refreshed_at.timestamp(), refreshed_at.timestamp()))

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir):
                current = distribution_lane_selector._primary_repo_flat_contact_handoff_packet_current(
                    now,
                    ['TIMEWELL', 'Toolradar', 'Morph'],
                )

        self.assertTrue(current)

    def test_choose_distribution_lane_refreshes_stale_primary_repo_flat_packet_during_short_hold(self):
        now = datetime(2026, 5, 25, 7, 23, 0)
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
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=True),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=12),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_curator_contact_packet_already_delivered', return_value=False),
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=['ToolChase']),
                    patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['ToolChase', 'NxCode', 'TIMEWELL']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=['ctxt.dev / Signum']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 5]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=4),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 8, 24, 28)),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, True)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=False),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_due_curator_followup_targets', return_value=[]),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        self.assertIn('stale', decision.reason.lower())
        self.assertIn('truthful asset', decision.reason.lower())

    def test_choose_distribution_lane_escalates_guarded_empty_board_to_concrete_repair(self):
        now = datetime(2026, 5, 25, 8, 29, 0)
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
            board_path = drafts_dir / 'marketing_execution_board_latest.md'
            board_path.write_text(
                '# board\n\n- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
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
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', board_path),
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
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=12),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_curator_contact_packet_already_delivered', return_value=False),
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=['ToolChase']),
                    patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=['ctxt.dev / Signum']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 5]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=2),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 8, 58, 32)),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, True)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=False),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_due_curator_followup_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_execution_board_has_no_truthful_do_now_packet', return_value=True),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': True}),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={
                        'repeat_count': 4,
                        'third_strike': True,
                        'guard_installed': True,
                        'guard_follow_through_count': 8,
                        'guard_pause_count': 1,
                        'execution_board_fingerprint': 'same-board',
                    }),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('instead of logging another guard pause', decision.reason.lower())
        self.assertTrue(any('guard follow-through run' in reason.lower() for reason in decision.reasons))
        self.assertTrue(any('guard pause run' in reason.lower() for reason in decision.reasons))

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

    def test_choose_distribution_lane_prefers_repo_proof_asset_after_exhausted_stackoverflow_slot(self):
        now = datetime(2026, 5, 26, 6, 17, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_publisher_overlap", "repair_state": "pending_measurement"},
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
                    patch.object(distribution_lane_selector, '_curator_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_curator_contact_packet_already_delivered', return_value=False),
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_recent_curator_queue_contact_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=['ctxt.dev / Signum']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 2]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=2),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 26, 8, 57, 0)),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, True)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=False),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_due_curator_followup_targets', return_value=[]),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'repo_conversion_proof_asset')
        self.assertIn('proof asset', decision.reason.lower())
        self.assertIn('stackoverflow slot already burned', decision.reason.lower())

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
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=True),
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
        self.assertEqual(decision.short_review_window_release_at, '2026-05-25T02:05:05')

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
        self.assertEqual(decision.short_review_window_release_at, '2026-05-25T07:20:16')

    def test_idle_measurement_hold_with_empty_execution_board_escalates_to_distribution_architecture_repair(self):
        now = datetime(2026, 5, 25, 9, 24, 0)
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
            board_path = drafts_dir / 'marketing_execution_board_latest.md'
            board_path.write_text(
                '# board\n\n- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
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
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', board_path),
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
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 5]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=1),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=False),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 8.0}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('no active short review window', decision.reason.lower())
        self.assertIsNone(decision.short_review_window_release_at)

    def test_idle_measurement_hold_escalates_to_distribution_architecture_repair_even_when_board_is_stale(self):
        now = datetime(2026, 5, 25, 9, 24, 0)
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
            board_path = drafts_dir / 'marketing_execution_board_latest.md'
            board_path.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '### 1. Old packet\n'
                '- When: Do now\n'
                '- Packet: /tmp/stale-packet.md\n',
                encoding='utf-8',
            )
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
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', board_path),
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
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 5]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=1),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 8.0}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('no active short review window', decision.reason.lower())

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
            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
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
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=True),
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
                    patch.object(distribution_lane_selector, '_execution_board_has_no_truthful_do_now_packet', return_value=True),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('no active short review window anymore', decision.reason.lower())
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
        self.assertIn('no active short review window anymore', decision.reason.lower())
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
        self.assertEqual(decision.short_review_window_release_at, '2026-05-25T07:20:16')

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
                        'verification': {'execution_board_fingerprint': self._board_fingerprint(execution_board)},
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

    def test_choose_distribution_lane_repairs_owned_content_drift_when_primary_repo_packet_is_post_hold_only(self):
        now = datetime(2026, 5, 26, 15, 7, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
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
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-26T20:55:18\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
            primary_packet = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            primary_packet.write_text('# primary packet\n', encoding='utf-8')
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
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[{'id': 'recent-owned-post'}]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=True),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-02T07:23:34.700335+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_curator_contact_packet_already_delivered', return_value=False),
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_recent_curator_queue_contact_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_pending_confirmation_actions', return_value=[]),
                    patch.object(distribution_lane_selector, '_pending_confirmation_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['TLDL']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=['TLDL']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_recent_prep_count', return_value=2),
                    patch.object(distribution_lane_selector, '_handoff_packet_is_current', return_value=True),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(0, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 1]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=1),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 26, 20, 55, 18)),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
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
        self.assertIn('repair the lane architecture', decision.reason.lower())

    def test_active_release_window_with_empty_board_and_saturated_owned_content_repairs_architecture(self):
        now = datetime(2026, 5, 27, 16, 34, 22)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "current_bottleneck": "conversion_to_free_use",
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_publisher_overlap", "repair_state": "pending_measurement"},
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
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-27T18:35:08\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
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
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[{'id': 'post-1'}, {'id': 'post-2'}]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': False, 'reddit_blocked': True, 'partial_visibility_only': False}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=True),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-01T23:11:13.732870+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_curator_contact_packet_already_delivered', return_value=False),
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=['SitePoint']),
                    patch.object(distribution_lane_selector, '_recent_curator_queue_contact_targets', return_value={'AI Coding Stack'}),
                    patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value={'Codivox'}),
                    patch.object(distribution_lane_selector, '_pending_confirmation_actions', return_value=[]),
                    patch.object(distribution_lane_selector, '_pending_confirmation_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['SitePoint']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=['SitePoint']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_recent_prep_count', return_value=5),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_prepared_only_family_repeat_count', return_value=5),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_comparison_backlink_lane_manual_only_blocked', return_value=True),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[0, 0]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=1),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 27, 18, 35, 8)),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_publisher_outreach_paused_by_repair_window', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 3, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={'repeat_count': 0, 'guard_installed': False, 'third_strike': False, 'guard_follow_through_count': 0, 'guard_pause_count': 0}),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('execution board is already empty for this active review window', decision.reason.lower())

    def test_owned_content_empty_board_inside_active_short_window_reuses_guard_instead_of_repair_churn(self):
        now = datetime(2026, 5, 26, 8, 13, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
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

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[{'id': 'recent-owned-post'}]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-02T07:23:34.700335+02:00'}),
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
                    patch.object(distribution_lane_selector, '_recent_curator_queue_contact_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_pending_confirmation_actions', return_value=[]),
                    patch.object(distribution_lane_selector, '_pending_confirmation_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(0, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 1]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=6),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 26, 8, 57, 0)),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_due_curator_followup_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_execution_board_has_no_truthful_do_now_packet', return_value=True),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={
                        'execution_board_fingerprint': 'fingerprint',
                        'repeat_count': 4,
                        'latest_matching_at': datetime(2026, 5, 26, 7, 24, 0),
                        'third_strike': True,
                        'guard_installed': True,
                        'guard_logs': ['guard.json'],
                        'guard_follow_through_count': 0,
                        'guard_follow_through_logs': [],
                        'latest_guard_follow_through_at': None,
                        'guard_pause_count': 9,
                        'guard_pause_logs': ['pause.json'],
                        'latest_guard_pause_at': datetime(2026, 5, 26, 6, 5, 54),
                        'earliest_guard_pause_at': datetime(2026, 5, 25, 9, 31, 0),
                    }),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_guard_pause')
        self.assertIn('newer concrete repair already ran', decision.reason.lower())

    def test_conversion_bottleneck_empty_board_inside_active_short_window_repairs_even_without_primary_flat_flag(self):
        now = datetime(2026, 5, 27, 13, 37, 0)
        adoption = {"evaluation": {"failing_signals": []}}
        audit = {
            "current_bottleneck": "conversion_to_free_use",
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "same_family_publisher_overlap", "repair_state": "pending_measurement"},
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
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[{'id': 'recent-owned-post'}]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-01T23:11:13.732870+02:00'}),
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
                    patch.object(distribution_lane_selector, '_recent_curator_queue_contact_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_pending_confirmation_actions', return_value=[]),
                    patch.object(distribution_lane_selector, '_pending_confirmation_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[0, 0]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=5),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 27, 14, 26, 29)),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 3, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_due_curator_followup_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_execution_board_has_no_truthful_do_now_packet', return_value=True),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('repair the lane architecture', decision.reason.lower())

    def test_guarded_empty_board_escalates_to_repair_after_short_window_clears_without_follow_through(self):
        now = datetime(2026, 5, 26, 23, 10, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
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

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[{'id': 'recent-owned-post'}]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=False),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-02T07:23:34.700335+02:00'}),
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
                    patch.object(distribution_lane_selector, '_recent_curator_queue_contact_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_pending_confirmation_actions', return_value=[]),
                    patch.object(distribution_lane_selector, '_pending_confirmation_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(0, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 1]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=2),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 26, 22, 47, 35)),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_due_curator_followup_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_execution_board_has_no_truthful_do_now_packet', return_value=True),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={
                        'execution_board_fingerprint': 'fingerprint',
                        'repeat_count': 4,
                        'latest_matching_at': datetime(2026, 5, 26, 22, 24, 0),
                        'third_strike': True,
                        'guard_installed': True,
                        'guard_logs': ['guard.json'],
                        'guard_follow_through_count': 0,
                        'guard_follow_through_logs': [],
                        'latest_guard_follow_through_at': None,
                        'guard_pause_count': 1,
                        'guard_pause_logs': ['pause.json'],
                        'latest_guard_pause_at': datetime(2026, 5, 26, 22, 5, 54),
                        'earliest_guard_pause_at': datetime(2026, 5, 26, 22, 5, 54),
                    }),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('short review window already cleared', decision.reason.lower())

    def test_active_short_window_with_guard_follow_through_pauses_duplicate_guard_churn(self):
        now = datetime(2026, 5, 25, 8, 0, 0)
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
            fingerprint = self._board_fingerprint(execution_board)
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            for filename, timestamp, action_type in [
                ('marketing_2026-05-25_013107_distribution_architecture_repair.json', '2026-05-25T01:31:07', 'distribution_architecture_repair'),
                ('marketing_2026-05-25_020752_distribution_architecture_repair.json', '2026-05-25T02:07:52', 'distribution_architecture_repair'),
                ('marketing_2026-05-25_042100_distribution_architecture_churn_guard_repair.json', '2026-05-25T04:21:00', 'distribution_architecture_churn_guard_repair'),
                ('marketing_2026-05-25_072249_distribution_architecture_guard_follow_through.json', '2026-05-25T07:22:49', 'distribution_architecture_guard_follow_through'),
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
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[6, 7]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=distribution_lane_selector.SHORT_REVIEW_WINDOW_EXTERNAL_ACTION_THRESHOLD),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 15, 7, 3)),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': False}),
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

        self.assertEqual(decision.lane, 'distribution_architecture_guard_pause')
        self.assertIn('pause duplicate guard churn', decision.reason.lower())
        self.assertEqual(decision.short_review_window_release_at, '2026-05-25T15:07:03')

    def test_active_short_window_with_prior_guard_pause_escalates_to_distribution_architecture_repair(self):
        now = datetime(2026, 5, 25, 8, 0, 0)
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
            fingerprint = self._board_fingerprint(execution_board)
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            for filename, timestamp, action_type in [
                ('marketing_2026-05-25_013107_distribution_architecture_repair.json', '2026-05-25T01:31:07', 'distribution_architecture_repair'),
                ('marketing_2026-05-25_020752_distribution_architecture_repair.json', '2026-05-25T02:07:52', 'distribution_architecture_repair'),
                ('marketing_2026-05-25_042100_distribution_architecture_churn_guard_repair.json', '2026-05-25T04:21:00', 'distribution_architecture_churn_guard_repair'),
                ('marketing_2026-05-25_072249_distribution_architecture_guard_follow_through.json', '2026-05-25T07:22:49', 'distribution_architecture_guard_follow_through'),
                ('marketing_2026-05-25_073500_distribution_architecture_guard_pause.json', '2026-05-25T07:35:00', 'distribution_architecture_guard_pause'),
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
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[6, 7]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=1),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 15, 7, 3)),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': False}),
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
        self.assertIn('instead of logging another guard pause', decision.reason.lower())
        self.assertEqual(decision.short_review_window_release_at, '2026-05-25T15:07:03')

    def test_distribution_architecture_repair_state_does_not_import_stale_guard_follow_through(self):
        now = datetime(2026, 5, 26, 20, 35, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-26T20:55:18\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
            fingerprint = self._board_fingerprint(execution_board)
            (log_dir / 'marketing_2026-05-26_192143_distribution_architecture_churn_guard_repair.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-26T19:21:43',
                    'chosen_action': {'type': 'distribution_architecture_churn_guard_repair'},
                    'verification': {'execution_board_fingerprint': fingerprint},
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-26.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-26T20:18:31',
                    'distribution_execution': {'action_type': 'distribution_architecture_guard_pause'},
                    'verification': {'execution_board_fingerprint': fingerprint},
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-25_072249_distribution_architecture_guard_follow_through.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T07:22:49',
                    'chosen_action': {'type': 'distribution_architecture_guard_follow_through'},
                    'verification': {'execution_board_fingerprint': fingerprint},
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board):
                state = distribution_lane_selector._distribution_architecture_repair_state(
                    now,
                    release_at=datetime(2026, 5, 26, 20, 55, 18),
                )

        self.assertEqual(state['guard_follow_through_count'], 0)

    def test_active_short_window_with_prior_guard_pause_and_newer_repair_reuses_guard_pause(self):
        now = datetime(2026, 5, 25, 15, 37, 0)
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
            fingerprint = self._board_fingerprint(execution_board)
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            for filename, timestamp, action_type in [
                ('marketing_2026-05-25_042100_distribution_architecture_churn_guard_repair.json', '2026-05-25T04:21:00', 'distribution_architecture_churn_guard_repair'),
                ('marketing_2026-05-25_072249_distribution_architecture_guard_follow_through.json', '2026-05-25T07:22:49', 'distribution_architecture_guard_follow_through'),
                ('marketing_2026-05-25_123831_distribution_architecture_guard_pause.json', '2026-05-25T12:38:31', 'distribution_architecture_guard_pause'),
                ('marketing_2026-05-25_124500_distribution_architecture_churn_guard_repair.json', '2026-05-25T12:45:00', 'distribution_architecture_churn_guard_repair'),
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
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[6, 7]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=2),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 15, 57, 14)),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': False}),
                    patch.object(distribution_lane_selector, '_execution_board_has_no_truthful_do_now_packet', return_value=True),
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

        self.assertEqual(decision.lane, 'distribution_architecture_guard_pause')
        self.assertIn('already logged both a guard pause and a concrete repair', decision.reason.lower())
        self.assertEqual(decision.short_review_window_release_at, '2026-05-25T15:57:14')

    def test_active_short_window_with_repeated_guard_pauses_escalates_to_distribution_architecture_repair(self):
        now = datetime(2026, 5, 25, 19, 49, 0)
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
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-25T23:07:41\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
            fingerprint = self._board_fingerprint(execution_board)
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            for filename, timestamp, action_type in [
                ('marketing_2026-05-25_042100_distribution_architecture_churn_guard_repair.json', '2026-05-25T04:21:00', 'distribution_architecture_churn_guard_repair'),
                ('marketing_2026-05-25_072249_distribution_architecture_guard_follow_through.json', '2026-05-25T07:22:49', 'distribution_architecture_guard_follow_through'),
                ('marketing_2026-05-25_114500_distribution_architecture_churn_guard_repair.json', '2026-05-25T11:45:00', 'distribution_architecture_churn_guard_repair'),
                ('marketing_2026-05-25_123831_distribution_architecture_guard_pause.json', '2026-05-25T12:38:31', 'distribution_architecture_guard_pause'),
                ('marketing_2026-05-25_194900_distribution_architecture_guard_pause.json', '2026-05-25T19:49:00', 'distribution_architecture_guard_pause'),
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
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[6, 7]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=2),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 23, 7, 41)),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': False}),
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
        self.assertIn('guard-pause path repeatedly again', decision.reason.lower())
        self.assertEqual(decision.short_review_window_release_at, '2026-05-25T23:07:41')

    def test_active_short_window_with_three_guard_pauses_escalates_to_distribution_architecture_repair(self):
        now = datetime(2026, 5, 25, 20, 10, 0)
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
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-25T23:07:41\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
            fingerprint = self._board_fingerprint(execution_board)
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            for filename, timestamp, action_type in [
                ('marketing_2026-05-25_042100_distribution_architecture_churn_guard_repair.json', '2026-05-25T04:21:00', 'distribution_architecture_churn_guard_repair'),
                ('marketing_2026-05-25_072249_distribution_architecture_guard_follow_through.json', '2026-05-25T07:22:49', 'distribution_architecture_guard_follow_through'),
                ('marketing_2026-05-25_114500_distribution_architecture_churn_guard_repair.json', '2026-05-25T11:45:00', 'distribution_architecture_churn_guard_repair'),
                ('marketing_2026-05-25_123831_distribution_architecture_guard_pause.json', '2026-05-25T12:38:31', 'distribution_architecture_guard_pause'),
                ('marketing_2026-05-25_194900_distribution_architecture_guard_pause.json', '2026-05-25T19:49:00', 'distribution_architecture_guard_pause'),
                ('marketing_2026-05-25_195900_distribution_architecture_guard_pause.json', '2026-05-25T19:59:00', 'distribution_architecture_guard_pause'),
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
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[6, 7]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=2),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 23, 7, 41)),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': False}),
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
        self.assertIn('guard-pause path repeatedly again', decision.reason.lower())
        self.assertEqual(decision.short_review_window_release_at, '2026-05-25T23:07:41')

    def test_execution_board_cleared_short_window_overrides_newer_guard_pause_reuse(self):
        now = datetime(2026, 5, 25, 16, 2, 0)
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
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-25T15:57:14\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
            fingerprint = self._board_fingerprint(execution_board)
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            for filename, timestamp, action_type in [
                ('marketing_2026-05-25_042100_distribution_architecture_churn_guard_repair.json', '2026-05-25T04:21:00', 'distribution_architecture_churn_guard_repair'),
                ('marketing_2026-05-25_072249_distribution_architecture_guard_follow_through.json', '2026-05-25T07:22:49', 'distribution_architecture_guard_follow_through'),
                ('marketing_2026-05-25_123831_distribution_architecture_guard_pause.json', '2026-05-25T12:38:31', 'distribution_architecture_guard_pause'),
                ('marketing_2026-05-25_124500_distribution_architecture_churn_guard_repair.json', '2026-05-25T12:45:00', 'distribution_architecture_churn_guard_repair'),
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
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=1),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 17, 3, 35)),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': False}),
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
        self.assertIn('instead of logging another guard pause', decision.reason.lower())
        self.assertEqual(decision.short_review_window_release_at, '2026-05-25T17:03:35')

    def test_guarded_empty_board_pauses_after_guard_follow_through_already_logged(self):
        now = datetime(2026, 5, 25, 8, 0, 0)
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
            fingerprint = self._board_fingerprint(execution_board)
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            for filename, timestamp, action_type in [
                ('marketing_2026-05-25_013107_distribution_architecture_repair.json', '2026-05-25T01:31:07', 'distribution_architecture_repair'),
                ('marketing_2026-05-25_020752_distribution_architecture_repair.json', '2026-05-25T02:07:52', 'distribution_architecture_repair'),
                ('marketing_2026-05-25_042100_distribution_architecture_churn_guard_repair.json', '2026-05-25T04:21:00', 'distribution_architecture_churn_guard_repair'),
                ('marketing_2026-05-25_072249_distribution_architecture_guard_follow_through.json', '2026-05-25T07:22:49', 'distribution_architecture_guard_follow_through'),
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
        self.assertIn('concrete distribution-architecture repair now', decision.reason.lower())

    def test_execution_board_fingerprint_ignores_generated_timestamp_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            board = Path(tmpdir) / 'marketing_execution_board_latest.md'
            board.write_text(
                '# Ralph Workflow Marketing Execution Board\n'
                'Generated: 2026-05-26T11:34:56\n\n'
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-26T12:30:22\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, '_execution_board_latest_path', return_value=board):
                first = distribution_lane_selector._execution_board_fingerprint()
                board.write_text(
                    '# Ralph Workflow Marketing Execution Board\n'
                    'Generated: 2026-05-26T11:58:08\n\n'
                    '## Active review windows\n'
                    '- Short review-window congestion clears at: 2026-05-26T12:30:22\n\n'
                    '## Best executable assets still waiting\n'
                    '- No do-now handoff packet is currently truthful in this review window.\n',
                    encoding='utf-8',
                )
                second = distribution_lane_selector._execution_board_fingerprint()
                board.write_text(
                    '# Ralph Workflow Marketing Execution Board\n'
                    'Generated: 2026-05-26T11:58:08\n\n'
                    '## Active review windows\n'
                    '- Short review-window congestion clears at: 2026-05-26T13:00:00\n\n'
                    '## Best executable assets still waiting\n'
                    '- No do-now handoff packet is currently truthful in this review window.\n',
                    encoding='utf-8',
                )
                third = distribution_lane_selector._execution_board_fingerprint()

        self.assertEqual(first, second)
        self.assertNotEqual(second, third)

    def test_guard_follow_through_state_survives_beyond_24h_when_board_truth_is_unchanged(self):
        now = datetime(2026, 5, 26, 12, 6, 0)
        board_text = (
            '# Ralph Workflow Marketing Execution Board\n'
            'Generated: 2026-05-26T11:58:08\n\n'
            '## Active review windows\n'
            '- Short review-window congestion clears at: 2026-05-26T12:30:22\n\n'
            '## Best executable assets still waiting\n'
            '- No do-now handoff packet is currently truthful in this review window.\n'
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            execution_board.write_text(board_text, encoding='utf-8')
            fingerprint = distribution_lane_selector.hashlib.sha1(
                distribution_lane_selector._normalized_execution_board_text(board_text).encode('utf-8')
            ).hexdigest()

            for filename, timestamp, action_type in [
                ('marketing_2026-05-25_075541_distribution_architecture_guard_follow_through.json', '2026-05-25T07:55:41', 'distribution_architecture_guard_follow_through'),
                ('marketing_2026-05-26_094455_distribution_architecture_churn_guard_repair.json', '2026-05-26T09:44:55', 'distribution_architecture_churn_guard_repair'),
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

            with ExitStack() as stack:
                stack.enter_context(patch.object(distribution_lane_selector, 'LOG_DIR', log_dir))
                stack.enter_context(patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir))
                stack.enter_context(patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board))
                state = distribution_lane_selector._distribution_architecture_repair_state(
                    now,
                    release_at=datetime(2026, 5, 26, 12, 30, 22),
                )

        self.assertEqual(state['guard_follow_through_count'], 1)

    def test_active_short_window_with_empty_board_does_not_surface_primary_repo_flat_packet_just_because_stackoverflow_is_exhausted(self):
        now = datetime(2026, 5, 25, 6, 42, 0)
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
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['ctxt.dev / Signum', 'NxCode', 'TIMEWELL']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=['ctxt.dev / Signum', 'NxCode', 'TIMEWELL']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
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
        self.assertNotEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        self.assertIn('execution board is already empty', decision.reason.lower())
        self.assertEqual(decision.short_review_window_release_at, '2026-05-25T07:20:16')

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

    def test_active_short_window_prefers_primary_repo_flat_packet_refresh_when_live_outbound_is_saturated(self):
        now = datetime(2026, 5, 25, 5, 54, 0)
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
                '- No do-now handoff packet is currently truthful in this review window.\n',
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
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['NxCode', 'TIMEWELL']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
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
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={'repeat_count': 4, 'guard_installed': True, 'third_strike': True}),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        self.assertIn('short review window is still active', decision.reason.lower())

    def test_active_publisher_overlap_pause_blocks_primary_repo_flat_packet_refresh(self):
        now = datetime(2026, 5, 25, 5, 54, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_publisher_overlap", "repair_state": "pending_measurement"},
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
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['NxCode', 'TIMEWELL']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
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
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={'repeat_count': 4, 'guard_installed': True, 'third_strike': True}),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertNotEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        self.assertIn('publisher-contact burst', ' '.join(decision.reasons).lower())


    def test_active_short_window_still_allows_primary_repo_flat_packet_when_curator_contact_packet_was_already_delivered(self):
        now = datetime(2026, 5, 25, 5, 54, 0)
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
                '- No do-now handoff packet is currently truthful in this review window.\n',
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
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=['vivy-yi/awesome-agent-orchestration']),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_curator_contact_packet_already_delivered', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['Toolradar', 'Codivox']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
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
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={'repeat_count': 4, 'guard_installed': True, 'third_strike': True}),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        self.assertIn('short review window is still active', decision.reason.lower())

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

    def test_stackoverflow_exhaustion_does_not_force_primary_repo_flat_publisher_packet_during_active_short_window(self):
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

        self.assertNotEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        self.assertNotIn('stackoverflow recovery lane is exhausted', decision.reason.lower())

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
                                {'type': 'x', 'value': 'https://x.com/ctxtdev'},
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
                                {'type': 'website', 'value': 'https://ctxt.dev/about', 'label': 'about page'},
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


    def test_primary_repo_flat_manual_delivery_refresh_blocks_packet_reselection(self):
        now = datetime(2026, 5, 25, 8, 19, 0)
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
                                {'type': 'website', 'value': 'https://ctxt.dev/work-with-me', 'label': 'contact page'},
                            ],
                        },
                        {
                            'target': 'NxCode',
                            'channels': [
                                {'type': 'email', 'value': 'support@nxcode.io', 'label': 'email'},
                            ],
                        },
                        {
                            'target': 'TIMEWELL',
                            'channels': [
                                {'type': 'email', 'value': 'timewell@timewell.jp', 'label': 'email'},
                            ],
                        },
                    ]
                }),
                encoding='utf-8',
            )
            primary_repo_flat_contact_handoff.write_text(
                '# publisher packet\n\n- ToolWise — https://toolwise.ai/tools/ralph-workflow\n\n### 1. ctxt.dev / Signum\n\n### 2. NxCode\n\n### 3. TIMEWELL\n',
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-25_primary_repo_flat_contact_manual_delivery_refresh.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T06:50:32+02:00',
                    'chosen_action': {
                        'type': 'primary_repo_flat_contact_manual_delivery_refresh',
                        'packet': str(primary_repo_flat_contact_handoff),
                    },
                    'measurement_window': {'review_at': '2026-06-01T06:50:32+02:00'},
                    'result': {
                        'status': 'executed',
                        'ok': True,
                        'artifact': str(primary_repo_flat_contact_handoff),
                    },
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
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=True),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[7, 9]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=4),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 8, 24, 28)),
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
                    patch.object(distribution_lane_selector, '_publisher_outreach_paused_by_repair_window', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertNotEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        joined = '\n'.join(decision.reasons).lower()
        self.assertIn('already manually delivered in the current review window', joined)
        self.assertIn('already has a live review window', joined)

    def test_active_manual_outreach_delivery_targets_are_excluded_from_primary_repo_flat_packet_reselection(self):
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
            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            discovery_path = log_dir / 'primary_repo_flat_contact_discovery_latest.json'
            handoff_path = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            manual_asset_path = drafts_dir / '2026-05-24_ctxtdev_publisher_outreach_ready.md'

            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
            discovery_path.write_text(json.dumps({
                'targets': [
                    {'target': 'ctxt.dev / Signum', 'channels': [{'type': 'telegram', 'value': 'https://t.me/ctxtdev', 'label': 'Telegram'}]},
                    {'target': 'ToolChase', 'channels': [{'type': 'email', 'value': 'hello@toolchase.com', 'label': 'email'}]},
                    {'target': 'NxCode', 'channels': [{'type': 'email', 'value': 'support@nxcode.io', 'label': 'email'}]},
                    {'target': 'TIMEWELL', 'channels': [{'type': 'email', 'value': 'timewell@timewell.jp', 'label': 'email'}]},
                ],
            }), encoding='utf-8')
            handoff_path.write_text(
                '# Ralph Workflow Primary-Repo-Flat Publisher Contact Packet\n\n'
                '### 1. NxCode\n\n'
                '### 2. TIMEWELL\n',
                encoding='utf-8',
            )
            manual_asset_path.write_text('# ctxt.dev / Signum publisher outreach — ready to send\n', encoding='utf-8')
            os.utime(handoff_path, (datetime(2026, 5, 25, 8, 10, 29).timestamp(), datetime(2026, 5, 25, 8, 10, 29).timestamp()))
            (log_dir / 'marketing_2026-05-25_manual_outreach_asset_follow_through_delivery.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T05:39:00+02:00',
                    'chosen_action': {
                        'type': 'manual_outreach_asset_follow_through',
                        'channel': 'current_chat_manual_handoff',
                        'draft': str(manual_asset_path),
                    },
                    'why_this_action': {'targets_prepared': ['ctxt.dev / Signum']},
                    'result': {
                        'status': 'delivered_to_current_chat',
                        'ok': True,
                        'next_review_at': '2026-05-31T00:00:00+02:00',
                    },
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-25_primary_repo_flat_contact_manual_delivery_refresh.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T06:50:32+02:00',
                    'chosen_action': {
                        'type': 'primary_repo_flat_contact_manual_delivery_refresh',
                        'packet': str(handoff_path),
                    },
                    'measurement_window': {'review_at': '2026-06-01T06:50:32+02:00'},
                    'result': {
                        'status': 'executed',
                        'ok': True,
                        'artifact': str(handoff_path),
                    },
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-25_toolchase_publisher_outreach.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T07:10:00+02:00',
                    'target': 'ToolChase',
                    'chosen_action': {'type': 'publisher_email_outreach'},
                    'result': {'status': 'executed', 'ok': True, 'live_external_action': True},
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
                    patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path),
                    patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_HANDOFF_LATEST_PATH', handoff_path),
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
                    patch.object(distribution_lane_selector, '_curator_contact_packet_already_delivered', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 5]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=1),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)
                active_targets = distribution_lane_selector._active_manual_outreach_delivery_targets(now)

        self.assertEqual(active_targets, {'ctxt.dev / Signum'})
        self.assertNotEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        joined = '\n'.join(decision.reasons)
        self.assertIn('active manual publisher handoff already covers (ctxt.dev / Signum)', joined)
        self.assertIn('already has a live review window', joined)


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

    def test_current_chat_final_reply_manual_delivery_counts_as_already_delivered(self):
        now = datetime(2026, 5, 27, 4, 6, 0)

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
            asset_path = drafts_dir / '2026-05-27_primary_repo_flat_manual_review_asset.md'
            asset_path.write_text('# TLDL publisher outreach — ready to send\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-27_tldl_channel_ready_outreach.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-27T03:39:30+02:00',
                    'chosen_action': {
                        'type': 'publisher_manual_review_channel_ready_outreach_asset',
                        'channel': 'manual_contact_asset',
                        'title': 'Create a Codeberg-first manual publisher outreach asset for TLDL',
                        'artifact': str(asset_path),
                    },
                    'measurement_window': {
                        'review_at': '2026-06-03T03:39:30+02:00'
                    },
                    'result': {'status': 'executed', 'ok': True},
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-27_034150_manual_publisher_review_asset_delivery.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-27T03:41:50+02:00',
                    'chosen_action': {
                        'type': 'manual_publisher_review_asset_delivery',
                        'channel': 'current_chat_final_reply',
                        'title': 'Deliver the current Codeberg-first manual publisher outreach asset for TLDL',
                        'packet': str(asset_path),
                    },
                    'result': {
                        'status': 'delivered',
                        'ok': True,
                        'outcome_ready': True,
                        'delivery_surface': 'current_chat_final_reply',
                        'packet_path': str(asset_path),
                    },
                    'measurement_window': {
                        'review_at': '2026-06-03T03:41:50+02:00'
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

    def test_active_manual_outreach_delivery_targets_uses_measurement_window_review_at(self):
        now = datetime(2026, 5, 27, 5, 24, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            log_dir.mkdir()

            (log_dir / 'marketing_2026-05-27_manual_asset_delivery.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-27T03:41:50+02:00',
                    'chosen_action': {
                        'type': 'manual_publisher_review_asset_delivery',
                        'channel': 'current_chat_final_reply',
                        'target': 'TLDL',
                    },
                    'why_this_action': {
                        'targets_prepared': ['TLDL', 'ComputingForGeeks'],
                    },
                    'result': {
                        'status': 'delivered',
                        'ok': True,
                        'targets_prepared': ['TLDL', 'ComputingForGeeks'],
                    },
                    'measurement_window': {
                        'review_at': '2026-06-03T03:41:50+02:00'
                    },
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir):
                targets = distribution_lane_selector._active_manual_outreach_delivery_targets(now)

        self.assertEqual(targets, {'TLDL', 'ComputingForGeeks'})

    def test_active_manual_outreach_delivery_targets_backfills_targets_from_matching_prepared_asset_log(self):
        now = datetime(2026, 5, 27, 5, 24, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            asset_path = drafts_dir / '2026-05-27_primary_repo_flat_manual_review_asset.md'
            asset_path.write_text('# shared manual review asset\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-27_prepared_manual_asset.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-27T03:39:30+02:00',
                    'chosen_action': {
                        'type': 'publisher_manual_review_channel_ready_outreach_asset',
                        'channel': 'distribution_architecture_repair',
                        'draft': str(asset_path),
                    },
                    'result': {
                        'status': 'prepared',
                        'ok': True,
                        'targets_prepared': ['TLDL', 'ComputingForGeeks'],
                    },
                    'measurement_window': {
                        'review_at': '2026-06-03T03:39:30+02:00'
                    },
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-27_manual_asset_delivery.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-27T03:41:50+02:00',
                    'chosen_action': {
                        'type': 'manual_publisher_review_asset_delivery',
                        'channel': 'current_chat_final_reply',
                        'target': 'TLDL',
                        'packet': str(asset_path),
                    },
                    'result': {
                        'status': 'delivered',
                        'ok': True,
                        'packet_path': str(asset_path),
                    },
                    'measurement_window': {
                        'review_at': '2026-06-03T03:41:50+02:00'
                    },
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir):
                targets = distribution_lane_selector._active_manual_outreach_delivery_targets(now)

        self.assertEqual(targets, {'TLDL', 'ComputingForGeeks'})

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

    def test_owned_content_fallback_yields_to_truthful_manual_follow_through_asset(self):
        now = datetime(2026, 5, 27, 19, 12, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_publisher_overlap", "repair_state": "pending_measurement"},
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
            manual_asset = drafts_dir / 'primary_repo_flat_manual_review_asset_latest.md'
            discovery_path = log_dir / 'primary_repo_flat_contact_discovery_latest.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '### 1. Primary-repo-flat manual follow-through asset\n'
                '- Targets: ComputingForGeeks\n',
                encoding='utf-8',
            )
            manual_asset.write_text('# manual asset\n', encoding='utf-8')
            discovery_path.write_text(json.dumps({
                'targets': [
                    {
                        'target': 'ComputingForGeeks',
                        'channels': [
                            {'type': 'website', 'label': 'Work with me', 'value': 'https://computingforgeeks.com/work-with-me/'},
                        ],
                    }
                ]
            }), encoding='utf-8')

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[{'title': 'recent post'}]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=True),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-01T23:11:13+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=True),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_manual_review_asset_current', return_value=True),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 2]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=1),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=False),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 3, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(True, True)),
                    patch.object(distribution_lane_selector, '_publisher_outreach_paused_by_repair_window', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'manual_outreach_asset_follow_through')
        self.assertIn('channel-ready manual publisher outreach asset already exists', decision.reason)

    def test_owned_content_noop_is_downgraded_to_measurement_hold_when_no_publishable_guide_remains(self):
        now = datetime(2026, 5, 27, 23, 42, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption_metrics_latest.json'
            audit_path = log_dir / 'marketing_workflow_audit_latest.json'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            execution_board = drafts_dir / 'marketing_execution_board_latest.md'

            adoption_path.write_text(json.dumps({"evaluation": {"failing_signals": ["primary_repo_flat"]}}), encoding='utf-8')
            audit_path.write_text(json.dumps({
                "repair_window_status": "measurement_pending",
                "repair_actions": [{"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"}],
            }), encoding='utf-8')
            latest_json.write_text('{}', encoding='utf-8')
            latest_md.write_text('', encoding='utf-8')
            execution_board.write_text('# empty\n', encoding='utf-8')

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[{'title': 'recent post'}]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=True),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-01T23:11:13+02:00'}),
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
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 2]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=1),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 3, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(True, True)),
                    patch.object(distribution_lane_selector, '_owned_content_publication_available', return_value=False),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertNotEqual(decision.lane, 'owned_content')

    def test_repeated_prepared_only_manual_followthrough_suppresses_same_asset(self):
        now = datetime(2026, 5, 27, 23, 42, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            asset_path = drafts_dir / 'primary_repo_flat_manual_review_asset_latest.md'
            asset_path.write_text('# manual asset\n- ComputingForGeeks\n', encoding='utf-8')
            discovery_path = log_dir / 'primary_repo_flat_contact_discovery_latest.json'
            discovery_path.write_text(json.dumps({
                'targets': [
                    {
                        'target': 'ComputingForGeeks',
                        'channels': [
                            {'type': 'website', 'label': 'Contact', 'value': 'https://computingforgeeks.com/contact'},
                        ],
                    }
                ]
            }), encoding='utf-8')
            for stamp in ('2026-05-27T22:10:00+02:00', '2026-05-27T23:27:26+02:00'):
                safe_stamp = stamp.replace(':', '').replace('+0200', '').replace('+02:00', '')
                (log_dir / f'marketing_{safe_stamp}_manual_followthrough.json').write_text(
                    json.dumps({
                        'timestamp': stamp,
                        'chosen_action': {
                            'type': 'manual_outreach_asset_follow_through',
                            'channel': 'manual_outreach_asset_follow_through',
                            'draft': str(drafts_dir / 'manual_followthrough.md'),
                        },
                        'why_this_action': {
                            'targets_prepared': ['ComputingForGeeks'],
                        },
                        'result': {
                            'status': 'prepared',
                            'ok': True,
                            'live_external_action': False,
                        },
                    }),
                    encoding='utf-8',
                )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path), \
                 patch.object(distribution_lane_selector, '_primary_repo_flat_manual_review_targets_waiting_for_execution', return_value=['ComputingForGeeks']), \
                 patch.object(distribution_lane_selector, '_primary_repo_flat_manual_review_asset_current', return_value=True):
                self.assertTrue(
                    distribution_lane_selector._primary_repo_flat_manual_review_asset_suppressed(
                        now,
                        primary_repo_flat_targets=[],
                        manual_review_targets=['ComputingForGeeks'],
                    )
                )
                assets = distribution_lane_selector._manual_outreach_assets_waiting_for_execution(now)

        self.assertEqual(assets, [])

    def test_prepared_only_packet_churn_does_not_hide_distinct_manual_review_asset(self):
        now = datetime(2026, 5, 27, 23, 40, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            discovery_path = log_dir / 'primary_repo_flat_contact_discovery_latest.json'
            asset_path = drafts_dir / 'primary_repo_flat_manual_review_asset_latest.md'
            asset_path.write_text('# shared manual review asset\n- ComputingForGeeks\n', encoding='utf-8')
            discovery_path.write_text(json.dumps({
                'targets': [
                    {
                        'target': 'ComputingForGeeks',
                        'channels': [
                            {'type': 'website', 'label': 'Contact', 'value': 'https://computingforgeeks.com/contact'},
                        ],
                    }
                ]
            }), encoding='utf-8')

            for stamp in ('2026-05-27T20:10:00+02:00', '2026-05-27T21:27:26+02:00'):
                safe_stamp = stamp.replace(':', '').replace('+0200', '').replace('+02:00', '')
                (log_dir / f'marketing_{safe_stamp}_packet_prep.json').write_text(
                    json.dumps({
                        'timestamp': stamp,
                        'chosen_action': {
                            'type': 'primary_repo_flat_contact_handoff_packet_execution',
                            'draft': str(drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'),
                        },
                        'result': {
                            'status': 'prepared',
                            'ok': True,
                            'live_external_action': False,
                        },
                    }),
                    encoding='utf-8',
                )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path), \
                 patch.object(distribution_lane_selector, '_primary_repo_flat_manual_review_targets_waiting_for_execution', return_value=['ComputingForGeeks']), \
                 patch.object(distribution_lane_selector, '_primary_repo_flat_manual_review_asset_current', return_value=True):
                self.assertFalse(
                    distribution_lane_selector._primary_repo_flat_manual_review_asset_suppressed(
                        now,
                        primary_repo_flat_targets=[],
                        manual_review_targets=['ComputingForGeeks'],
                    )
                )
                assets = distribution_lane_selector._manual_outreach_assets_waiting_for_execution(now)

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]['targets'], ['ComputingForGeeks'])
        self.assertIn('manual follow-through asset', assets[0]['title'].lower())

    def test_repeated_prepared_only_manual_followthrough_yields_to_directory_confirmation(self):
        now = datetime(2026, 5, 27, 23, 42, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_publisher_overlap", "repair_state": "pending_measurement"},
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
            manual_asset = drafts_dir / 'primary_repo_flat_manual_review_asset_latest.md'
            discovery_path = log_dir / 'primary_repo_flat_contact_discovery_latest.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '### 1. Directory secondary-surface repair\n'
                '- SaaSHub alternatives page still routes repo intent to GitHub only.\n',
                encoding='utf-8',
            )
            manual_asset.write_text('# manual asset\n- ComputingForGeeks\n', encoding='utf-8')
            discovery_path.write_text(json.dumps({
                'targets': [
                    {
                        'target': 'ComputingForGeeks',
                        'channels': [
                            {'type': 'website', 'label': 'Contact', 'value': 'https://computingforgeeks.com/contact'},
                        ],
                    }
                ]
            }), encoding='utf-8')
            for idx, stamp in enumerate(('2026-05-27T22:10:00+02:00', '2026-05-27T23:27:26+02:00'), start=1):
                (log_dir / f'marketing_2026-05-27_repeat_{idx}.json').write_text(
                    json.dumps({
                        'timestamp': stamp,
                        'chosen_action': {
                            'type': 'manual_outreach_asset_follow_through',
                            'channel': 'manual_outreach_asset_follow_through',
                            'draft': str(drafts_dir / 'manual_followthrough.md'),
                        },
                        'why_this_action': {
                            'targets_prepared': ['ComputingForGeeks'],
                        },
                        'result': {
                            'status': 'prepared',
                            'ok': True,
                            'live_external_action': False,
                        },
                    }),
                    encoding='utf-8',
                )

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[{'title': 'recent post'}]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=True),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-01T23:11:13+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=True),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_manual_review_asset_current', return_value=True),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 2]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=3),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 28, 1, 20, 16)),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=False),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 3, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=True),
                    patch.object(distribution_lane_selector, '_directory_secondary_surface_repair_targets', return_value=['SaaSHub alternatives']),
                    patch.object(distribution_lane_selector, '_directory_secondary_surface_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_directory_secondary_surface_followup_window', return_value={}),
                    patch.object(distribution_lane_selector, '_directory_secondary_surface_followup_active', return_value=False),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(True, True)),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'directory_confirmation')

    def test_same_family_publisher_overlap_blocks_manual_review_followthrough_reselection(self):
        now = datetime(2026, 5, 28, 0, 42, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "same_family_publisher_overlap", "repair_state": "pending_measurement"},
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
            manual_asset = drafts_dir / 'primary_repo_flat_manual_review_asset_latest.md'
            discovery_path = log_dir / 'primary_repo_flat_contact_discovery_latest.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '### 1. Primary-repo-flat manual follow-through asset\n'
                '- When: Do now\n'
                '- Targets: ComputingForGeeks\n',
                encoding='utf-8',
            )
            manual_asset.write_text('# manual asset\n- ComputingForGeeks\n', encoding='utf-8')
            discovery_path.write_text(json.dumps({
                'targets': [
                    {
                        'target': 'ComputingForGeeks',
                        'channels': [
                            {'type': 'website', 'label': 'Contact', 'value': 'https://computingforgeeks.com/contact'},
                        ],
                    }
                ]
            }), encoding='utf-8')

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board),
                    patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', audit_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_recent_owned_content_posts', return_value=[{'title': 'recent post'}]),
                    patch.object(distribution_lane_selector, '_working_directory_channels', return_value=[]),
                    patch.object(distribution_lane_selector, '_already_attempted_channel_names', return_value=set()),
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=True),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-01T23:11:13+02:00'}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=5),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=0),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=25),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=True),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_manual_contact_queue_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_curator_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_manual_review_asset_current', return_value=True),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 2]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=1),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 3, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(True, True)),
                    patch.object(distribution_lane_selector, '_publisher_outreach_paused_by_repair_window', return_value=True),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'measurement_hold')
        self.assertTrue(any(
            'manual-only primary-repo-flat publisher follow-through asset exists' in reason.lower()
            for reason in decision.reasons
        ))
        self.assertFalse(any(
            'execution board explicitly surfaces the manual publisher follow-through asset as do-now' in reason
            for reason in decision.reasons
        ))

    def test_reddit_discussion_packet_does_not_count_as_manual_publisher_follow_through(self):
        now = datetime(2026, 5, 25, 17, 27, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_outreach_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_publisher_overlap", "repair_state": "pending_measurement"},
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
            reddit_asset = drafts_dir / 'reddit_discussion_handoff_packet_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            audit_path.write_text(json.dumps(audit), encoding='utf-8')
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
                encoding='utf-8',
            )
            reddit_asset.write_text('# Reddit packet\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-25_reddit_discussion_channel_ready_outreach_asset.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T14:08:00+02:00',
                    'type': 'reddit_discussion_channel_ready_outreach_asset',
                    'chosen_action': {
                        'channel': 'manual_contact_asset',
                        'artifact': str(reddit_asset),
                        'title': 'Prepare Reddit discussion handoff packet',
                    },
                    'measurement_window': {'review_at': '2026-06-01T14:08:00+02:00'},
                    'result': {'status': 'executed', 'artifact': str(reddit_asset)},
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
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
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
        self.assertIn('repair the lane architecture', decision.reason.lower())

    def test_manual_outreach_asset_is_not_misclassified_as_reddit_from_summary_only(self):
        now = datetime(2026, 5, 25, 17, 44, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            artifact = drafts_dir / '2026-05-23_vivy_yi_curator_email.txt'
            artifact.write_text('manual curator follow-through\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_vivy_manual_contact_asset.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-23T21:46:39+02:00',
                    'chosen_action': {
                        'type': 'curator_contact_channel_ready_outreach_asset',
                        'channel': 'manual_contact_asset',
                        'artifact': str(artifact),
                        'title': 'vivy-yi curator email fallback attempt',
                    },
                    'why_this_action': {
                        'summary': 'Used the highest-priority unsent curator target after Reddit and Apollo were already constrained by current workflow rules.',
                        'targets_prepared': ['vivy-yi/awesome-agent-orchestration'],
                    },
                    'measurement_window': {
                        'review_at': '2026-06-01T21:46:39+02:00',
                    },
                    'result': {
                        'status': 'prepared',
                        'artifact': str(artifact),
                    },
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir):
                assets = distribution_lane_selector._manual_outreach_assets_waiting_for_execution(now)

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]['artifact_path'], str(artifact))

    def test_post_hold_only_primary_repo_flat_packet_yields_guard_pause_instead_of_fake_do_now(self):
        now = datetime(2026, 5, 25, 5, 54, 0)
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
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-25T07:20:16\n\n'
                '## Best executable assets still waiting\n'
                '### 1. Primary-repo-flat publisher contact packet\n'
                '- When: After short-window congestion clears (2026-05-25T07:20:16)\n'
                '- Packet: /tmp/primary_repo_flat_contact_handoff_packet_latest.md\n'
                '- A refreshed primary-repo-flat publisher packet now exists for the current waiting target set, but the short review window is still active; hold manual delivery until that congestion clears.\n',
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
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['ToolChase', 'NxCode']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=True),
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
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(True, True)),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={'repeat_count': 3, 'guard_installed': True, 'third_strike': True, 'guard_follow_through_count': 1, 'guard_pause_count': 0}),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_guard_pause')
        self.assertIn('pause duplicate guard churn', decision.reason.lower())
        self.assertIn('post-hold only', ' '.join(decision.reasons).lower())
        self.assertEqual(decision.short_review_window_release_at, '2026-05-25T07:20:16')

    def test_active_short_window_keeps_guard_pause_when_concrete_repair_already_ran_after_pause_started(self):
        now = datetime(2026, 5, 25, 6, 55, 0)
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
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-25T07:20:16\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
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
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
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
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(True, True)),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={
                        'repeat_count': 5,
                        'guard_installed': True,
                        'third_strike': True,
                        'guard_follow_through_count': 7,
                        'guard_pause_count': 19,
                        'cumulative_guard_pause_count': 19,
                        'latest_matching_at': datetime(2026, 5, 25, 6, 50, 0),
                        'earliest_guard_pause_at': datetime(2026, 5, 25, 6, 0, 0),
                    }),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_guard_pause')
        self.assertIn('pause duplicate guard churn', decision.reason.lower())
        self.assertNotIn('escalate into one concrete distribution-architecture repair', decision.reason.lower())

    def test_active_release_window_without_congestion_keeps_guard_pause_when_concrete_repair_already_ran_after_pause_started(self):
        now = datetime(2026, 5, 25, 6, 55, 0)
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
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-25T07:20:16\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
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
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[6, 7]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=1),
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
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(True, True)),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={
                        'repeat_count': 5,
                        'guard_installed': True,
                        'third_strike': True,
                        'guard_follow_through_count': 0,
                        'guard_pause_count': 2,
                        'cumulative_guard_pause_count': 2,
                        'latest_matching_at': datetime(2026, 5, 25, 6, 50, 0),
                        'earliest_guard_pause_at': datetime(2026, 5, 25, 6, 0, 0),
                        'latest_matching_release_at': None,
                        'latest_guard_pause_release_at': None,
                    }),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_guard_pause')
        self.assertIn('pause duplicate guard churn', decision.reason.lower())
        self.assertNotIn('short review window already cleared', decision.reason.lower())
        self.assertEqual(decision.short_review_window_release_at, '2026-05-25T07:20:16')

    def test_cleared_short_window_escalates_to_repair_after_newer_repair_for_same_fingerprint(self):
        now = datetime(2026, 5, 26, 23, 37, 0)
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
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-02T07:23:34+02:00'}),
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
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 5]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=5),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 26, 22, 47, 35)),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': True}),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 3, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={
                        'repeat_count': 3,
                        'guard_installed': True,
                        'third_strike': True,
                        'guard_follow_through_count': 0,
                        'current_guard_follow_through_count': 0,
                        'recent_guard_follow_through_count': 0,
                        'guard_pause_count': 1,
                        'cumulative_guard_pause_count': 1,
                        'latest_matching_at': datetime(2026, 5, 26, 23, 33, 49),
                        'earliest_guard_pause_at': datetime(2026, 5, 26, 23, 1, 54),
                    }),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('newer concrete repair ran', decision.reason.lower())
        self.assertIn('short review window already cleared', decision.reason.lower())

    def test_cleared_short_window_pauses_duplicate_repair_when_release_and_fingerprint_are_unchanged(self):
        now = datetime(2026, 5, 27, 21, 28, 0)
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
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-27T18:35:08\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
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
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-01T23:11:13+02:00'}),
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
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 5]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=5),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 27, 18, 35, 8)),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': True}),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 3, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={
                        'repeat_count': 6,
                        'guard_installed': True,
                        'third_strike': True,
                        'guard_follow_through_count': 0,
                        'current_guard_follow_through_count': 0,
                        'recent_guard_follow_through_count': 0,
                        'guard_pause_count': 2,
                        'cumulative_guard_pause_count': 2,
                        'latest_matching_at': datetime(2026, 5, 27, 21, 23, 14),
                        'earliest_guard_pause_at': datetime(2026, 5, 27, 20, 54, 6),
                        'latest_matching_release_at': datetime(2026, 5, 27, 18, 35, 8),
                        'latest_guard_pause_release_at': datetime(2026, 5, 27, 18, 35, 8),
                    }),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_guard_pause')
        self.assertIn('pause duplicate guard churn', decision.reason.lower())
        self.assertIn('short review window already cleared', decision.reason.lower())

    def test_no_active_short_window_escalates_to_repair_without_release_metadata(self):
        now = datetime(2026, 5, 27, 21, 28, 0)
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
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-01T23:11:13+02:00'}),
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
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 5]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': True}),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 3, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={
                        'repeat_count': 5,
                        'guard_installed': True,
                        'third_strike': True,
                        'guard_follow_through_count': 0,
                        'current_guard_follow_through_count': 0,
                        'recent_guard_follow_through_count': 0,
                        'guard_pause_count': 2,
                        'cumulative_guard_pause_count': 2,
                        'latest_matching_at': datetime(2026, 5, 27, 21, 23, 14),
                        'earliest_guard_pause_at': datetime(2026, 5, 27, 20, 54, 6),
                        'latest_matching_release_at': None,
                        'latest_guard_pause_release_at': None,
                    }),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('no active short review window remains', decision.reason.lower())
        self.assertIn('instead of logging another guard pause', decision.reason.lower())

    def test_cleared_short_window_escalates_to_repair_after_repeated_repairs_without_guard_follow_through(self):
        now = datetime(2026, 5, 26, 23, 37, 0)
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
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-27T18:35:08\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n',
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
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-01T23:11:13+02:00'}),
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
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 5]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=5),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 27, 18, 35, 8)),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': True}),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 3, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={
                        'repeat_count': 6,
                        'guard_installed': True,
                        'third_strike': True,
                        'guard_follow_through_count': 0,
                        'current_guard_follow_through_count': 0,
                        'recent_guard_follow_through_count': 0,
                        'guard_pause_count': 2,
                        'cumulative_guard_pause_count': 2,
                        'latest_matching_at': datetime(2026, 5, 27, 21, 23, 14),
                        'earliest_guard_pause_at': datetime(2026, 5, 27, 20, 54, 6),
                        'latest_matching_release_at': datetime(2026, 5, 27, 18, 35, 8),
                        'latest_guard_pause_release_at': datetime(2026, 5, 27, 18, 35, 8),
                    }),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_guard_pause')
        self.assertIn('pause duplicate guard churn', decision.reason.lower())
        self.assertIn('short review window already cleared', decision.reason.lower())

    def test_cleared_short_window_escalates_to_repair_after_repeated_repairs_without_guard_follow_through(self):
        now = datetime(2026, 5, 26, 23, 37, 0)
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
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-02T07:23:34+02:00'}),
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
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 5]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=5),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 26, 22, 47, 35)),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': True}),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 3, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={
                        'repeat_count': 2,
                        'guard_installed': True,
                        'third_strike': True,
                        'guard_follow_through_count': 0,
                        'current_guard_follow_through_count': 0,
                        'recent_guard_follow_through_count': 0,
                        'guard_pause_count': 0,
                        'cumulative_guard_pause_count': 0,
                        'latest_matching_at': datetime(2026, 5, 26, 23, 33, 49),
                        'earliest_guard_pause_at': None,
                    }),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('repeated concrete repairs', decision.reason.lower())
        self.assertIn('short review window already cleared', decision.reason.lower())

    def test_post_hold_release_reuses_current_primary_repo_flat_packet_even_if_prep_repeat_threshold_was_hit(self):
        now = datetime(2026, 5, 25, 7, 25, 0)
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
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-25T07:20:16\n\n'
                '## Best executable assets still waiting\n'
                '### 1. Primary-repo-flat publisher contact packet\n'
                '- When: After short-window congestion clears (2026-05-25T07:20:16)\n'
                '- Packet: /tmp/primary_repo_flat_contact_handoff_packet_latest.md\n'
                '- A refreshed primary-repo-flat publisher packet now exists for the current waiting target set, but the short review window is still active; hold manual delivery until that congestion clears.\n',
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
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['ToolChase', 'NxCode']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_recent_prep_count', return_value=2),
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
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(True, True)),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={'repeat_count': 3, 'guard_installed': True, 'third_strike': True, 'guard_follow_through_count': 1, 'guard_pause_count': 0}),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        self.assertIn('current codeberg-first publisher contact packet already exists', decision.reason.lower())

    def test_execution_board_post_hold_only_packet_counts_as_no_truthful_do_now(self):
        now = datetime(2026, 5, 25, 5, 54, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            drafts_dir.mkdir()

            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-25T07:20:16\n\n'
                '## Best executable assets still waiting\n'
                '### 1. Primary-repo-flat publisher contact packet\n'
                '- When: After short-window congestion clears (2026-05-25T07:20:16)\n'
                '- Packet: /tmp/primary_repo_flat_contact_handoff_packet_latest.md\n'
                '- A refreshed primary-repo-flat publisher packet now exists for the current waiting target set, but the short review window is still active; hold manual delivery until that congestion clears.\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board), \
                 patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[]), \
                 patch.object(distribution_lane_selector, '_pending_confirmation_actions', return_value=[]):
                result = distribution_lane_selector._execution_board_has_no_truthful_do_now_packet(now)

        self.assertTrue(result)

    def test_execution_board_post_hold_only_packet_stays_non_truthful_even_if_manual_asset_exists(self):
        now = datetime(2026, 5, 25, 5, 54, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            drafts_dir.mkdir()

            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-25T07:20:16\n\n'
                '## Best executable assets still waiting\n'
                '### 1. Primary-repo-flat publisher contact packet\n'
                '- When: After short-window congestion clears (2026-05-25T07:20:16)\n'
                '- Packet: /tmp/primary_repo_flat_contact_handoff_packet_latest.md\n'
                '- A refreshed primary-repo-flat publisher packet now exists for the current waiting target set, but the short review window is still active; hold manual delivery until that congestion clears.\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board), \
                 patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[
                     {
                         'target': 'AI Saying',
                         'targets': ['AI Saying'],
                         'artifact_path': '/tmp/primary_repo_flat_contact_handoff_packet_latest.md',
                         'title': 'Primary-repo-flat publisher contact packet',
                     },
                 ]), \
                 patch.object(distribution_lane_selector, '_pending_confirmation_actions', return_value=[]):
                result = distribution_lane_selector._execution_board_has_no_truthful_do_now_packet(now)

        self.assertTrue(result)

    def test_execution_board_explicit_empty_marker_counts_as_empty_without_live_waiting_assets(self):
        now = datetime(2026, 5, 27, 6, 10, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            drafts_dir.mkdir()

            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Active review windows\n'
                '- Directory secondary-surface repair already shipped in the current review window; do not requeue it until the documented follow-up date or the live target set changes.\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n'
                '- Directory secondary-surface repair already shipped in the current review window; wait for the follow-up date or a target-set change before resurfacing it.\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board), \
                 patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[]), \
                 patch.object(distribution_lane_selector, '_pending_confirmation_actions', return_value=[]):
                result = distribution_lane_selector._execution_board_has_no_truthful_do_now_packet(now)

        self.assertTrue(result)

    def test_execution_board_explicit_empty_marker_overrides_stale_manual_asset_discovery(self):
        now = datetime(2026, 5, 27, 23, 58, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            drafts_dir.mkdir()

            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n'
                '- The current primary-repo-flat publisher contact packet was already prepared 5 time(s) in the last 48 hours without a live delivery window; do not resurface it as a do-now asset until the target set or delivery state materially changes.\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board), \
                 patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[{
                     'target': 'SitePoint',
                     'targets': ['SitePoint'],
                     'artifact_path': '/tmp/primary_repo_flat_contact_handoff_packet_latest.md',
                     'title': 'Primary-repo-flat publisher contact packet',
                 }]), \
                 patch.object(distribution_lane_selector, '_pending_confirmation_actions', return_value=[]):
                result = distribution_lane_selector._execution_board_has_no_truthful_do_now_packet(now)

        self.assertTrue(result)

    def test_execution_board_waiting_blocks_parses_plain_bullet_waiting_section(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            drafts_dir.mkdir()

            execution_board = drafts_dir / 'marketing_execution_board_latest.md'
            execution_board.write_text(
                '# Ralph Workflow Marketing Execution Board\n\n'
                '## Best executable assets still waiting\n'
                '- No do-now handoff packet is currently truthful in this review window.\n'
                '- Directory secondary-surface repair already shipped in the current review window; wait for the follow-up date or a target-set change before resurfacing it.\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', execution_board):
                blocks = distribution_lane_selector._execution_board_waiting_blocks()

        self.assertEqual(
            blocks,
            [
                ['- No do-now handoff packet is currently truthful in this review window.'],
                ['- Directory secondary-surface repair already shipped in the current review window; wait for the follow-up date or a target-set change before resurfacing it.'],
            ],
        )

    def test_choose_distribution_lane_repairs_empty_post_hold_board_even_without_runtime_release_timestamp(self):
        now = datetime(2026, 5, 26, 2, 37, 0)
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
                '## Active review windows\n'
                '- Short review-window congestion clears at: 2026-05-26T08:57:00\n\n'
                '## Best executable assets still waiting\n'
                '### 1. Primary-repo-flat publisher contact packet\n'
                '- When: After short-window congestion clears (2026-05-26T08:57:00)\n'
                '- Packet: /tmp/primary_repo_flat_contact_handoff_packet_latest.md\n'
                '- A refreshed primary-repo-flat publisher packet now exists for the current waiting target set, but the short review window is still active; hold manual delivery until that congestion clears.\n',
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
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_recent_curator_queue_contact_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value=set()),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['AI Saying']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[2, 3]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=2),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
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
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(True, True)),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={'repeat_count': 0, 'guard_installed': False, 'third_strike': False, 'guard_follow_through_count': 0, 'guard_pause_count': 0}),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_repair')
        self.assertIn('execution board is still empty', decision.reason.lower())

    def test_current_primary_repo_flat_packet_beats_measurement_hold_even_when_publisher_burst_pause_is_active(self):
        now = datetime(2026, 5, 25, 17, 4, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_publisher_overlap", "repair_state": "pending_measurement"},
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
                '- When: Do now\n',
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
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': False, 'reddit_blocked': True, 'partial_visibility_only': False}),
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
                    patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['TIMEWELL', 'Toolradar', 'Morph']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 2]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=1),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': True}),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 2, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={'repeat_count': 1, 'guard_installed': False, 'third_strike': False}),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        self.assertIn('blocks another net-new publisher burst', decision.reason.lower())

    def test_current_primary_followthrough_packet_beats_prepared_only_repeat_repair(self):
        now = datetime(2026, 5, 27, 7, 44, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_outreach_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_distribution_overlap", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_publisher_overlap", "repair_state": "pending_measurement"},
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
                '- Packet: /tmp/primary_repo_flat_contact_handoff_packet_latest.md\n'
                '- Targets: Requesty, SOTAAZ, SitePoint\n'
                '- Why this matters: A current Codeberg-first publisher contact packet already exists for fresh primary-repo-flat targets.\n',
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
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': False, 'reddit_blocked': True, 'partial_visibility_only': False}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=True),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-02T07:23:34+02:00'}),
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
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['Requesty', 'SOTAAZ', 'SitePoint']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_recent_prep_count', return_value=7),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 2]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=1),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': True}),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 3, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={'repeat_count': 1, 'guard_installed': False, 'third_strike': False}),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        self.assertIn('current codeberg-first publisher contact packet already exists', decision.reason.lower())

    def test_primary_repo_flat_manual_review_asset_counts_as_followthrough(self):
        now = datetime(2026, 5, 27, 7, 44, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
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
            execution_board.write_text('# Ralph Workflow Marketing Execution Board\n', encoding='utf-8')

            manual_asset = drafts_dir / '2026-05-27_primary_repo_flat_manual_review_asset.md'
            manual_asset.write_text('TLDL follow-through asset', encoding='utf-8')

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
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': False, 'reddit_blocked': True, 'partial_visibility_only': False}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=True),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-02T07:23:34+02:00'}),
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
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=['TLDL']),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_recent_prep_count', return_value=7),
                    patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[{'target': 'TLDL', 'targets': ['TLDL'], 'artifact_path': str(manual_asset), 'title': 'Distribution lane execution: distribution_architecture_repair'}]),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 2]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=1),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': True}),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=False),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 3, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={'repeat_count': 1, 'guard_installed': False, 'third_strike': False}),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'primary_repo_flat_contact_handoff_packet')
        self.assertIn('truthful follow-through surface', decision.reason.lower())

    def test_guard_pause_wins_when_publisher_overlap_hold_is_active_and_release_window_only_shifted(self):
        now = datetime(2026, 5, 27, 17, 57, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        audit = {
            "repair_window_status": "measurement_pending",
            "repair_actions": [
                {"failure_type": "primary_repo_flat", "repair_state": "pending_measurement"},
                {"failure_type": "same_family_publisher_overlap", "repair_state": "pending_measurement"},
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
            execution_board.write_text('# Ralph Workflow Marketing Execution Board\n\nNo truthful do-now lane.\n', encoding='utf-8')
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
                    patch.object(distribution_lane_selector, '_shared_findings', return_value=['adoption_metrics_latest.json', 'market_intelligence_latest.json']),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': False, 'reddit_blocked': True, 'partial_visibility_only': False}),
                    patch.object(distribution_lane_selector, '_hn_ceiling_repeated', return_value=True),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True, 'next_review_at': '2026-06-01T23:11:13+02:00'}),
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
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_non_executable_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_contact_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False),
                    patch.object(distribution_lane_selector, '_primary_repo_flat_recent_prep_count', return_value=0),
                    patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_pending_confirmation_actions', return_value=[]),
                    patch.object(distribution_lane_selector, '_pending_confirmation_handoff_packet_current', return_value=False),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[1, 2]),
                    patch.object(distribution_lane_selector, '_recent_live_external_action_count', return_value=1),
                    patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 27, 18, 35, 8)),
                    patch.object(distribution_lane_selector, '_execution_board_short_review_release_at', return_value=datetime(2026, 5, 27, 17, 32, 44)),
                    patch.object(distribution_lane_selector, '_short_review_window_reentry_repairs_state', return_value={'reentry_repairs_complete': True}),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_rate_limit_cooldown_active', return_value=(False, None)),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_manual_delivery_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_run_current', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=True),
                    patch.object(distribution_lane_selector, '_backlink_status_snapshot', return_value={'payload': {'summary': {}}, 'live_listings': 3, 'age_hours': 0.1}),
                    patch.object(distribution_lane_selector, '_directory_confirmation_due', return_value=False),
                    patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)),
                    patch.object(distribution_lane_selector, '_publisher_outreach_paused_by_repair_window', return_value=True),
                    patch.object(distribution_lane_selector, '_execution_board_has_no_truthful_do_now_packet', return_value=True),
                    patch.object(distribution_lane_selector, '_should_enforce_empty_board_architecture_repair', return_value=True),
                    patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={
                        'repeat_count': 4,
                        'guard_installed': True,
                        'third_strike': True,
                        'guard_follow_through_count': 1,
                        'guard_pause_count': 1,
                        'cumulative_guard_pause_count': 1,
                        'latest_matching_at': datetime(2026, 5, 27, 17, 0, 0),
                        'earliest_guard_pause_at': datetime(2026, 5, 27, 16, 0, 0),
                        'latest_matching_release_at': datetime(2026, 5, 27, 17, 20, 0),
                        'latest_guard_pause_release_at': datetime(2026, 5, 27, 17, 20, 0),
                    }),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

        self.assertEqual(decision.lane, 'distribution_architecture_guard_pause')
        self.assertIn('publisher-overlap repair window', decision.reason.lower())


if __name__ == '__main__':
    unittest.main()
