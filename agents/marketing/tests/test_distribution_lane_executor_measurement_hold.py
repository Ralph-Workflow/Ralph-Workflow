import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agents.marketing import distribution_lane_selector
from agents.marketing.distribution_lane_selector import LaneDecision
from agents.marketing import distribution_lane_executor
from agents.marketing import run_posting


class DistributionLaneExecutorMeasurementHoldTests(unittest.TestCase):
    def test_execution_board_lists_manual_review_asset_when_only_manual_channels_remain(self):
        now = datetime(2026, 5, 27, 16, 20, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            distribution_lane_latest = log_dir / 'distribution_lane_latest.json'
            distribution_lane_latest.write_text(json.dumps({}), encoding='utf-8')
            (drafts_dir / 'primary_repo_flat_manual_review_asset_latest.md').write_text('# manual asset\n', encoding='utf-8')

            with ExitStack() as stack:
                stack.enter_context(patch.object(distribution_lane_executor, 'LOG_DIR', log_dir))
                stack.enter_context(patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir))
                stack.enter_context(patch.object(distribution_lane_executor, '_load_curator_queue_rows', return_value=[]))
                stack.enter_context(patch.object(distribution_lane_executor, '_comparison_queue_rows', return_value=[]))
                stack.enter_context(patch.object(distribution_lane_executor, '_current_manual_demand_capture_hint', return_value={}))
                stack.enter_context(patch.object(distribution_lane_executor, '_current_stackoverflow_scheduled_run', return_value=''))
                stack.enter_context(patch.object(distribution_lane_executor.distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=False))
                stack.enter_context(patch.object(distribution_lane_executor, '_current_primary_repo_flat_actionable_findings', return_value=[]))
                stack.enter_context(patch.object(distribution_lane_executor.distribution_lane_selector, '_primary_repo_flat_manual_review_targets_waiting_for_execution', return_value=['ctxt.dev / Signum']))
                stack.enter_context(patch.object(distribution_lane_executor, '_primary_repo_flat_packet_delivery_still_active', return_value=False))
                stack.enter_context(patch.object(distribution_lane_executor, '_primary_repo_flat_prepared_only_family_repeat_count', return_value=0))
                stack.enter_context(patch.object(distribution_lane_executor, '_load_json', side_effect=lambda path: {}))
                stack.enter_context(patch.object(distribution_lane_executor, '_recent_local_executed_action_type', return_value=False))
                stack.enter_context(patch.object(distribution_lane_executor, '_backlink_status_latest_path', return_value=log_dir / 'backlink_status_latest.json'))
                stack.enter_context(patch.object(distribution_lane_executor, '_secondary_surface_repair_rows', return_value=[]))
                stack.enter_context(patch.object(distribution_lane_executor, '_directory_confirmation_packet_is_current', return_value=False))
                stack.enter_context(patch.object(distribution_lane_executor, '_directory_secondary_surface_repair_still_active', return_value=False))
                stack.enter_context(patch.object(distribution_lane_executor, '_manual_contact_queue_rows', return_value=[]))
                stack.enter_context(patch.object(distribution_lane_executor, '_current_curator_handoff_targets', return_value=[]))
                stack.enter_context(patch.object(distribution_lane_executor, '_current_comparison_handoff_targets', return_value=[]))
                stack.enter_context(patch.object(distribution_lane_executor, '_comparison_packet_delivery_still_active', return_value=False))
                stack.enter_context(patch.object(distribution_lane_executor, '_current_measurement_hold_release_run', return_value=''))
                stack.enter_context(patch.object(distribution_lane_executor, '_adoption_summary', return_value='Codeberg is still flat.'))
                stack.enter_context(patch.object(distribution_lane_executor, '_reddit_discussion_asset_waiting_for_execution', return_value=None))
                stack.enter_context(patch.object(distribution_lane_executor, '_reddit_manual_discussion_blocked', return_value=False))

                board_path, targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')
            self.assertIn('Manual publisher outreach asset', board_text)
            self.assertIn('manual follow-through asset already exists', board_text)
            self.assertIn('ctxt.dev / Signum', board_text)
            self.assertEqual(targets, ['ctxt.dev / Signum'])

    def test_execution_board_empty_marker_wins_over_informational_review_window_bullets(self):
        now = datetime(2026, 5, 27, 11, 21, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            board_path = tmp / 'marketing_execution_board_latest.md'
            board_path.write_text(
                '\n'.join([
                    '# Ralph Workflow Marketing Execution Board',
                    'Generated: 2026-05-27T11:20:34',
                    '',
                    '## Active review windows',
                    '- Short review-window congestion clears at: 2026-05-27T14:26:29',
                    '- Comparison backlink packet was already manually delivered in the current review window; do not surface it again until that window expires or the prepared target set changes.',
                    '',
                    '## Best executable assets still waiting',
                    '- No do-now handoff packet is currently truthful in this review window.',
                    '- Curator handoff packet exists, but curator reply/backlink review windows are already saturated in the current short window.',
                ]),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', board_path), \
                 patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[]), \
                 patch.object(distribution_lane_selector, '_pending_confirmation_actions', return_value=[]):
                empty = distribution_lane_selector._execution_board_has_no_truthful_do_now_packet(now)

        self.assertTrue(empty)

    def test_execution_board_empty_marker_still_yields_to_real_waiting_asset(self):
        now = datetime(2026, 5, 27, 11, 21, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            board_path = tmp / 'marketing_execution_board_latest.md'
            board_path.write_text(
                '\n'.join([
                    '# Ralph Workflow Marketing Execution Board',
                    '',
                    '## Best executable assets still waiting',
                    '- No do-now handoff packet is currently truthful in this review window.',
                ]),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', board_path), \
                 patch.object(distribution_lane_selector, '_manual_outreach_assets_waiting_for_execution', return_value=[{'path': '/tmp/packet.md'}]), \
                 patch.object(distribution_lane_selector, '_pending_confirmation_actions', return_value=[]):
                empty = distribution_lane_selector._execution_board_has_no_truthful_do_now_packet(now)

        self.assertFalse(empty)

    def test_manual_review_asset_is_detected_without_prior_marketing_log(self):
        now = datetime(2026, 5, 27, 15, 37, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            discovery_path = log_dir / 'primary_repo_flat_contact_discovery_latest.json'
            discovery_path.write_text(json.dumps({
                'targets': [
                    {
                        'target': 'ctxt.dev / Signum',
                        'channels': [
                            {'type': 'telegram', 'value': 'https://t.me/ctxtdev', 'label': 'Telegram'},
                            {'type': 'website', 'value': 'https://ctxt.dev/work-with-me', 'label': 'work with me page'},
                        ],
                    }
                ]
            }), encoding='utf-8')

            manual_asset = drafts_dir / 'primary_repo_flat_manual_review_asset_latest.md'
            manual_asset.write_text('# manual asset\n', encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path), \
                 patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=set()), \
                 patch.object(distribution_lane_selector, '_recent_curator_queue_contact_targets', return_value=set()), \
                 patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value=set()), \
                 patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=[]), \
                 patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False), \
                 patch.object(distribution_lane_selector, '_primary_repo_flat_recent_prep_count', return_value=0), \
                 patch.object(distribution_lane_selector, '_execution_board_short_review_release_at', return_value=None):
                assets = distribution_lane_selector._manual_outreach_assets_waiting_for_execution(now)

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]['artifact_path'], str(manual_asset))
        self.assertEqual(assets[0]['targets'], ['ctxt.dev / Signum'])

    def test_execution_board_empty_marker_yields_to_manual_review_asset(self):
        now = datetime(2026, 5, 27, 15, 37, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            board_path = drafts_dir / 'marketing_execution_board_latest.md'
            board_path.write_text(
                '\n'.join([
                    '# Ralph Workflow Marketing Execution Board',
                    '',
                    '## Best executable assets still waiting',
                    '- No do-now handoff packet is currently truthful in this review window.',
                ]),
                encoding='utf-8',
            )
            discovery_path = log_dir / 'primary_repo_flat_contact_discovery_latest.json'
            discovery_path.write_text(json.dumps({
                'targets': [
                    {
                        'target': 'ctxt.dev / Signum',
                        'channels': [
                            {'type': 'telegram', 'value': 'https://t.me/ctxtdev', 'label': 'Telegram'},
                        ],
                    }
                ]
            }), encoding='utf-8')
            (drafts_dir / 'primary_repo_flat_manual_review_asset_latest.md').write_text('# manual asset\n', encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'EXECUTION_BOARD_LATEST_PATH', board_path), \
                 patch.object(distribution_lane_selector, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path), \
                 patch.object(distribution_lane_selector, '_recent_contact_targets', return_value=set()), \
                 patch.object(distribution_lane_selector, '_recent_curator_queue_contact_targets', return_value=set()), \
                 patch.object(distribution_lane_selector, '_active_manual_outreach_delivery_targets', return_value=set()), \
                 patch.object(distribution_lane_selector, '_primary_repo_flat_contact_targets_waiting_for_execution', return_value=[]), \
                 patch.object(distribution_lane_selector, '_primary_repo_flat_packet_delivery_still_active', return_value=False), \
                 patch.object(distribution_lane_selector, '_primary_repo_flat_recent_prep_count', return_value=0), \
                 patch.object(distribution_lane_selector, '_execution_board_short_review_release_at', return_value=None), \
                 patch.object(distribution_lane_selector, '_pending_confirmation_actions', return_value=[]):
                empty = distribution_lane_selector._execution_board_has_no_truthful_do_now_packet(now)

        self.assertFalse(empty)

    def test_owned_content_lane_publishes_unposted_repo_guide(self):
        now = datetime(2026, 5, 26, 2, 53, 0)
        decision = LaneDecision(
            lane='owned_content',
            reason='No stronger autonomous lane detected.',
            reasons=['board empty, owned content is the truthful lane'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json: Codeberg movement is the primary success gate'],
            artifact_path='',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            source = tmp / 'review_ai_coding_output_before_merge.md'
            source.write_text(
                '\n'.join([
                    '# Review AI Coding Output Before Merge',
                    '',
                    'Ralph Workflow is the operating system for autonomous coding: a free and open-source composable loop framework and AI orchestrator.',
                    '',
                    'Use the default workflow as-is first, then build your own workflow on top once you know what kind of review bundle you actually need.',
                    '',
                    'Before merge, ask whether the diff matches the task, whether the checks really ran, and whether the finish is boring enough to trust.',
                    '',
                    'That is the real conversion question for unattended coding: not whether the tool sounded confident, but whether the result is ready to review honestly and worth following on Codeberg first.',
                ]),
                encoding='utf-8',
            )

            posted_file = log_dir / 'posted_urls.json'

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'OWNED_CONTENT_SOURCE_CANDIDATES', [source]), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value={}), \
                 patch.object(distribution_lane_executor.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='')), \
                 patch.object(distribution_lane_executor, 'post_telegraph', return_value=(True, 'https://telegra.ph/example-review-guide')), \
                 patch.object(run_posting, 'POSTED_FILE', posted_file):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.action_type, 'owned_content_publication')
            self.assertEqual(execution.status, 'executed')
            self.assertTrue(execution.live_external_action)
            self.assertTrue(Path(execution.artifact_path).exists())
            self.assertIn('https://telegra.ph/example-review-guide', execution.targets_prepared)

            posted = json.loads(posted_file.read_text(encoding='utf-8'))
            self.assertEqual(posted['posts'][0]['platform'], 'telegraph')
            self.assertEqual(posted['posts'][0]['url'], 'https://telegra.ph/example-review-guide')

            action_log = json.loads((log_dir / 'marketing_2026-05-26_owned_content_execution.json').read_text(encoding='utf-8'))
            self.assertEqual(action_log['chosen_action']['type'], 'owned_content_publication')

    def test_owned_content_lane_skips_when_same_guide_was_already_posted(self):
        now = datetime(2026, 5, 26, 2, 54, 0)
        decision = LaneDecision(
            lane='owned_content',
            reason='No stronger autonomous lane detected.',
            reasons=['board empty, owned content is the truthful lane'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json: Codeberg movement is the primary success gate'],
            artifact_path='',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            source = tmp / 'review_ai_coding_output_before_merge.md'
            body = '\n'.join([
                '# Review AI Coding Output Before Merge',
                '',
                'Ralph Workflow is the operating system for autonomous coding: a free and open-source composable loop framework and AI orchestrator.',
                '',
                'Use the default workflow as-is first, then build your own workflow on top once you know what kind of review bundle you actually need.',
                '',
                'Before merge, ask whether the diff matches the task, whether the checks really ran, and whether the finish is boring enough to trust.',
            ])
            source.write_text(body, encoding='utf-8')

            draft_hash = run_posting.digest_text(body.strip())
            posted_file = log_dir / 'posted_urls.json'
            posted_file.write_text(json.dumps({'posts': [{
                'platform': 'telegraph',
                'ok': True,
                'draft_hash': draft_hash,
                'draft': 'already-posted.md',
            }]}), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'OWNED_CONTENT_SOURCE_CANDIDATES', [source]), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value={}), \
                 patch.object(distribution_lane_executor.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='')), \
                 patch.object(distribution_lane_executor, 'post_telegraph') as mock_post, \
                 patch.object(run_posting, 'POSTED_FILE', posted_file):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.action_type, 'owned_content_lane_noop')
            self.assertEqual(execution.status, 'skipped')
            mock_post.assert_not_called()

    def test_distribution_architecture_repair_creates_manual_reddit_discussion_asset_when_monitor_has_live_opportunities(self):
        now = datetime(2026, 5, 25, 14, 8, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            seo_dir = tmp / 'seo-reports'
            log_dir.mkdir()
            drafts_dir.mkdir()
            seo_dir.mkdir()

            (log_dir / 'adoption_metrics_latest.json').write_text(
                json.dumps({
                    'recent_window': {
                        'Codeberg': {
                            'samples': 9,
                            'stars_delta_window': 0,
                            'watchers_delta_window': 0,
                            'forks_delta_window': 0,
                        }
                    }
                }),
                encoding='utf-8',
            )
            (seo_dir / 'reddit_monitor_latest.md').write_text(
                '\n'.join([
                    '# Reddit monitor',
                    '',
                    '## Best current discussion opportunities (reply-worthiness first, product-fit second)',
                    '',
                    '### 1) genuine question for people who have built multi-agent systems in production. how do you handle context continuity across enterprise tools?',
                    '- URL: <https://www.reddit.com/r/AI_Agents/comments/example1>',
                    '- Community: `r/AI_Agents`',
                    '- Freshness: during this pass',
                    '- Direct reply fit: **high**',
                    '- Mention fit: **medium-low**',
                    '- Best RalphWorkflow angle: **content-family match: production_failure**',
                    '- Why it fits: content-first match from `production_failure` query family; query=`workflow continuity ai agents reddit`',
                    '',
                    '### 2) seedance 2.0 is impressive. it\'s still not a production workflow.',
                    '- URL: <https://www.reddit.com/r/AI_Agents/comments/example2>',
                    '- Community: `r/AI_Agents`',
                    '- Freshness: during this pass',
                    '- Direct reply fit: **high**',
                    '- Mention fit: **medium-low**',
                    '- Best RalphWorkflow angle: **content-family match: production_failure**',
                    '- Why it fits: content-first match from `production_failure` query family; query=`workflow continuity ai agents reddit`',
                    '',
                    '## Strong current rejects',
                ]),
                encoding='utf-8',
            )

            reddit_execution_status = log_dir / 'reddit_execution_status_latest.json'
            reddit_execution_status.write_text(json.dumps({
                'generated_at': '2026-05-25T14:07:59+02:00',
                'status': 'browser_session_ready',
            }), encoding='utf-8')

            decision = LaneDecision(
                lane='distribution_architecture_repair',
                reason='repair the empty board',
                reasons=['empty board'],
                owned_content_posts_last_36h=0,
                unsubmitted_directory_channels=[],
                shared_findings_used=['adoption_metrics_latest.json: Codeberg movement is the primary success gate'],
                artifact_path=str(drafts_dir / 'distribution_action_brief.md'),
            )

            def fake_run(*args, **kwargs):
                return SimpleNamespace(returncode=1, stdout='', stderr='')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'SEO_REPORTS_DIR', seo_dir), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', log_dir / 'adoption_metrics_latest.json'), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', tmp / 'outreach-log.md'), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value={}), \
                 patch.object(distribution_lane_selector, 'REDDIT_EXECUTION_STATUS_PATH', reddit_execution_status), \
                 patch.object(distribution_lane_selector, 'REDDIT_MONITOR_LATEST', seo_dir / 'reddit_monitor_latest.md'), \
                 patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None), \
                 patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={
                     'third_strike': True,
                     'execution_board_fingerprint': 'abc123',
                     'repeat_count': 6,
                     'guard_follow_through_count': 7,
                     'guard_pause_count': 5,
                 }), \
                 patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)), \
                 patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=5), \
                 patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True), \
                 patch.object(distribution_lane_executor.subprocess, 'run', side_effect=fake_run):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now=now)

            self.assertEqual(execution.action_type, 'reddit_discussion_channel_ready_outreach_asset')
            self.assertTrue(Path(execution.artifact_path).exists())
            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('Reddit Discussion Handoff Packet', artifact_text)
            self.assertIn('Suggested first reply', artifact_text)
            self.assertIn('Codeberg repo is the primary place to inspect it', artifact_text)

            board_text = (drafts_dir / 'marketing_execution_board_latest.md').read_text(encoding='utf-8')
            self.assertIn('Manual community discussion asset', board_text)

    def test_distribution_architecture_repair_skips_manual_reddit_discussion_asset_when_reddit_execution_is_blocked(self):
        now = datetime(2026, 5, 27, 5, 12, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            seo_dir = tmp / 'seo-reports'
            log_dir.mkdir()
            drafts_dir.mkdir()
            seo_dir.mkdir()

            (log_dir / 'adoption_metrics_latest.json').write_text(
                json.dumps({
                    'recent_window': {
                        'Codeberg': {
                            'samples': 9,
                            'stars_delta_window': 0,
                            'watchers_delta_window': 0,
                            'forks_delta_window': 0,
                        }
                    }
                }),
                encoding='utf-8',
            )
            (seo_dir / 'reddit_monitor_latest.md').write_text(
                '\n'.join([
                    '# Reddit monitor',
                    '',
                    '## Best current discussion opportunities (reply-worthiness first, product-fit second)',
                    '',
                    '### 1) live thread that would normally be usable',
                    '- URL: <https://www.reddit.com/r/AI_Agents/comments/example1>',
                    '- Community: `r/AI_Agents`',
                    '- Freshness: during this pass',
                    '- Direct reply fit: **high**',
                    '- Mention fit: **medium-low**',
                    '- Best RalphWorkflow angle: **content-family match: production_failure**',
                    '- Why it fits: current pain thread',
                ]),
                encoding='utf-8',
            )
            reddit_execution_status = log_dir / 'reddit_execution_status_latest.json'
            reddit_execution_status.write_text(json.dumps({
                'generated_at': '2026-05-27T05:05:29+02:00',
                'status': 'execution_blocked',
            }), encoding='utf-8')

            decision = LaneDecision(
                lane='distribution_architecture_repair',
                reason='repair the empty board',
                reasons=['empty board'],
                owned_content_posts_last_36h=0,
                unsubmitted_directory_channels=[],
                shared_findings_used=['adoption_metrics_latest.json: Codeberg movement is the primary success gate'],
                artifact_path=str(drafts_dir / 'distribution_action_brief.md'),
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'SEO_REPORTS_DIR', seo_dir), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', log_dir / 'adoption_metrics_latest.json'), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', tmp / 'outreach-log.md'), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value={}), \
                 patch.object(distribution_lane_selector, 'REDDIT_EXECUTION_STATUS_PATH', reddit_execution_status), \
                 patch.object(distribution_lane_selector, 'REDDIT_MONITOR_LATEST', seo_dir / 'reddit_monitor_latest.md'), \
                 patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None), \
                 patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={
                     'third_strike': True,
                     'execution_board_fingerprint': 'abc123',
                     'repeat_count': 6,
                     'guard_follow_through_count': 7,
                     'guard_pause_count': 5,
                 }), \
                 patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)), \
                 patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=5), \
                 patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now=now)

            self.assertIn(execution.action_type, {'distribution_architecture_repair', 'distribution_architecture_churn_guard_repair'})
            self.assertNotEqual(execution.action_type, 'reddit_discussion_channel_ready_outreach_asset')
            self.assertFalse((drafts_dir / 'reddit_discussion_handoff_packet_latest.md').exists())
            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertNotIn('Reddit Discussion Handoff Packet', artifact_text)

    def test_distribution_architecture_repair_does_not_create_manual_publisher_asset_when_board_has_no_truthful_targets(self):
        now = datetime(2026, 5, 27, 5, 30, 0)
        decision = LaneDecision(
            lane='distribution_architecture_repair',
            reason='repair the empty board',
            reasons=['empty board'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json: Codeberg movement is the primary success gate'],
            artifact_path='/tmp/distribution_action_brief.md',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value={}), \
                 patch.object(distribution_lane_executor, '_write_marketing_execution_board', return_value=(drafts_dir / 'marketing_execution_board_latest.md', [])), \
                 patch.object(distribution_lane_executor, '_load_primary_repo_flat_contact_discovery', return_value=[{
                     'target': 'ComputingForGeeks',
                     'channels': [{'type': 'website', 'value': 'https://computingforgeeks.com/contact'}],
                 }]), \
                 patch.object(distribution_lane_executor, '_write_reddit_discussion_handoff_asset', return_value=None), \
                 patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={
                     'third_strike': False,
                     'execution_board_fingerprint': 'empty-board',
                     'repeat_count': 1,
                     'guard_follow_through_count': 0,
                     'guard_pause_count': 0,
                 }), \
                 patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None), \
                 patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now=now)

        self.assertIn(execution.action_type, {'distribution_architecture_repair', 'distribution_architecture_churn_guard_repair'})
        self.assertNotEqual(execution.action_type, 'publisher_manual_review_channel_ready_outreach_asset')

    def test_distribution_architecture_repair_refreshes_stale_manual_packets(self):
        now = datetime(2026, 5, 26, 18, 42, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            board_path = drafts_dir / 'marketing_execution_board_latest.md'

            def fake_write_board(_now):
                board_path.write_text('# board\n', encoding='utf-8')
                return board_path, ['TLDL']

            decision = LaneDecision(
                lane='distribution_architecture_repair',
                reason='repair the empty board',
                reasons=['empty board'],
                owned_content_posts_last_36h=0,
                unsubmitted_directory_channels=[],
                shared_findings_used=['adoption_metrics_latest.json: Codeberg movement is the primary success gate'],
                artifact_path=str(drafts_dir / 'distribution_action_brief.md'),
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value={}), \
                 patch.object(distribution_lane_executor, '_write_marketing_execution_board', side_effect=fake_write_board), \
                 patch.object(distribution_lane_executor, '_write_reddit_discussion_handoff_asset', return_value=None), \
                 patch.object(distribution_lane_executor, '_refresh_manual_execution_assets', return_value=(
                     ['primary-repo-flat publisher contact packet → /tmp/primary_repo_flat_contact_handoff_packet_latest.md'],
                     ['TLDL'],
                 )), \
                 patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None), \
                 patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={
                     'third_strike': False,
                     'execution_board_fingerprint': 'abc123',
                     'repeat_count': 2,
                     'guard_follow_through_count': 0,
                     'guard_pause_count': 0,
                 }), \
                 patch.object(distribution_lane_executor.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='')):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now=now)

            self.assertEqual(execution.action_type, 'distribution_architecture_repair')
            self.assertIn('Refreshed stale manual execution packets', execution.summary)
            self.assertIn('TLDL', execution.targets_prepared)
            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('## Same-run packet repairs applied', artifact_text)
            self.assertIn('primary-repo-flat publisher contact packet', artifact_text)

    def test_execution_board_hides_already_delivered_manual_reddit_asset(self):
        now = datetime(2026, 5, 25, 15, 11, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            artifact = drafts_dir / 'reddit_discussion_handoff_packet_latest.md'
            artifact.write_text('# Reddit packet\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-25_reddit_discussion_channel_ready_outreach_asset.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T14:08:00+02:00',
                    'type': 'reddit_discussion_channel_ready_outreach_asset',
                    'chosen_action': {
                        'channel': 'manual_contact_asset',
                        'artifact': str(artifact),
                        'title': 'Prepare Reddit discussion handoff packet',
                    },
                    'measurement_window': {
                        'review_at': '2026-06-01T14:08:00+02:00',
                    },
                    'result': {
                        'status': 'executed',
                        'artifact': str(artifact),
                    },
                }),
                encoding='utf-8',
            )
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

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertNotIn('Manual community discussion asset', board_text)
        self.assertIn('No do-now handoff packet is currently truthful in this review window.', board_text)

    def test_execution_board_hides_live_published_reddit_comment_asset(self):
        now = datetime(2026, 5, 26, 15, 12, 43)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            artifact = log_dir / 'marketing_2026-05-26_145518_reddit_comment_published.md'
            artifact.write_text('# Reddit comment published\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-26_145518_reddit_comment_published.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-26T14:55:18.485562',
                    'chosen_action': {
                        'type': 'reddit_comment_published',
                        'channel': 'reddit',
                        'title': 'Reddit comment published: Seedance thread',
                        'url': 'https://old.reddit.com/r/AI_Agents/comments/1rawxiw/seedance_20_is_impressive_its_still_not_a/onyqq6t/',
                        'draft': str(artifact),
                    },
                    'result': {
                        'ok': True,
                        'status': 'published',
                        'live_external_action': True,
                    },
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 26, 20, 55, 18)), \
                 patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True), \
                 patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)), \
                 patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=0):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertNotIn('Manual community discussion asset', board_text)
        self.assertNotIn('marketing_2026-05-26_145518_reddit_comment_published.md', board_text)

    def test_execution_board_falls_back_to_live_short_window_release_when_latest_lane_json_omits_it(self):
        now = datetime(2026, 5, 25, 19, 23, 49)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            (log_dir / 'distribution_lane_latest.json').write_text(
                json.dumps({'lane': 'distribution_architecture_repair', 'short_review_window_release_at': None}),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 25, 23, 7, 41)), \
                 patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=False), \
                 patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)), \
                 patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=0):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertIn('Short review-window congestion clears at: 2026-05-25T23:07:41', board_text)

    def test_execution_board_drops_expired_short_window_release_marker(self):
        now = datetime(2026, 5, 26, 15, 2, 45)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            (log_dir / 'distribution_lane_latest.json').write_text(
                json.dumps({'lane': 'distribution_architecture_repair', 'short_review_window_release_at': '2026-05-25T23:07:41'}),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None), \
                 patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=False), \
                 patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)), \
                 patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=0):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertNotIn('Short review-window congestion clears at: 2026-05-25T23:07:41', board_text)

    def test_non_live_lane_rewrites_execution_board_after_post_hold_rerun_schedule_updates(self):
        now = datetime(2026, 5, 26, 9, 46, 0)
        decision = LaneDecision(
            lane='owned_content',
            reason='fallback lane',
            reasons=['test'],
            owned_content_posts_last_36h=1,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='',
            short_review_window_release_at='2026-05-26T09:50:16',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            board_path = tmp / 'marketing_execution_board_latest.md'
            board_path.write_text('# board\n', encoding='utf-8')
            contract_path = tmp / 'post_hold_distribution_reentry_latest.md'
            contract_path.write_text('# contract\n', encoding='utf-8')
            artifact_path = tmp / 'owned_content_noop.md'
            artifact_path.write_text('# noop\n', encoding='utf-8')

            with patch.object(distribution_lane_executor, '_execute_owned_content', return_value=distribution_lane_executor.LaneExecution(
                lane='owned_content',
                action_type='owned_content_lane_noop',
                status='skipped',
                artifact_path=str(artifact_path),
                summary='Owned-content lane remains active; no non-content distribution execution packet needed.',
                targets_prepared=[],
                shared_findings_used=['adoption_metrics_latest.json'],
                live_external_action=False,
                blocking_factors=[],
            )), \
                 patch.object(distribution_lane_executor, '_write_marketing_execution_board', return_value=(board_path, [])) as mock_write_board, \
                 patch.object(distribution_lane_executor, '_write_post_hold_reentry_contract', return_value=contract_path), \
                 patch.object(distribution_lane_executor, '_schedule_measurement_hold_release_run', return_value={'status': 'scheduled', 'scheduled_run_at': '2026-05-26T09:50:16'}), \
                 patch.object(distribution_lane_executor, '_append_post_hold_schedule_note'), \
                 patch.object(distribution_lane_executor, '_write_action_log'):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now=now)

        self.assertEqual(mock_write_board.call_count, 2)
        self.assertIn('Scheduled an automatic post-hold marketer rerun at the updated short-window release time.', execution.summary)

    def test_post_hold_reentry_contract_falls_back_to_live_short_window_release_when_latest_lane_json_omits_it(self):
        now = datetime(2026, 5, 26, 8, 12, 8)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            (log_dir / 'distribution_lane_latest.json').write_text(
                json.dumps({'lane': 'distribution_architecture_repair', 'short_review_window_release_at': None}),
                encoding='utf-8',
            )
            board_path = drafts_dir / 'marketing_execution_board_latest.md'
            board_path.write_text('# board\n', encoding='utf-8')
            discovery_path = log_dir / 'primary_repo_flat_contact_discovery_latest.json'
            discovery_path.write_text(json.dumps({'targets': []}), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path), \
                 patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=datetime(2026, 5, 26, 8, 57, 0)), \
                 patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=False):
                contract_path = distribution_lane_executor._write_post_hold_reentry_contract(
                    now,
                    release_at=None,
                    execution_board_path=board_path,
                    shared_findings_used=['adoption_metrics_latest.json'],
                )

            contract_text = contract_path.read_text(encoding='utf-8')

        self.assertIn('- Hold release at: 2026-05-26T08:57:00', contract_text)

    def test_post_hold_reentry_contract_uses_later_live_short_window_release_than_stale_requested_release(self):
        now = datetime(2026, 5, 26, 13, 19, 30)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            (log_dir / 'distribution_lane_latest.json').write_text(
                json.dumps({'lane': 'distribution_architecture_repair', 'short_review_window_release_at': '2026-05-26T13:22:23'}),
                encoding='utf-8',
            )
            board_path = drafts_dir / 'marketing_execution_board_latest.md'
            board_path.write_text('# board\n', encoding='utf-8')
            discovery_path = log_dir / 'primary_repo_flat_contact_discovery_latest.json'
            discovery_path.write_text(json.dumps({'targets': []}), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', discovery_path), \
                 patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=False):
                contract_path = distribution_lane_executor._write_post_hold_reentry_contract(
                    now,
                    release_at='2026-05-26T13:14:38',
                    execution_board_path=board_path,
                    shared_findings_used=['adoption_metrics_latest.json'],
                )

            contract_text = contract_path.read_text(encoding='utf-8')

        self.assertIn('- Hold release at: 2026-05-26T13:22:23', contract_text)

    def test_reddit_discussion_handoff_asset_regenerates_when_current_opportunities_changed(self):
        now = datetime(2026, 5, 27, 4, 21, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            seo_dir = tmp / 'seo-reports'
            log_dir.mkdir()
            drafts_dir.mkdir()
            seo_dir.mkdir()

            latest_packet = drafts_dir / 'reddit_discussion_handoff_packet_latest.md'
            latest_packet.write_text(
                '# Ralph Workflow Reddit Discussion Handoff Packet\n'
                '## Opportunity 1: stale thread title\n'
                '- URL: <https://www.reddit.com/r/AI_Agents/comments/stale1>\n',
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-25_reddit_discussion_manual_delivery.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T14:24:00+02:00',
                    'type': 'reddit_discussion_manual_delivery',
                    'chosen_action': {
                        'channel': 'current_chat',
                        'packet': str(latest_packet),
                    },
                    'measurement_window': {
                        'review_at': '2026-06-01T14:24:00+02:00',
                    },
                    'result': {
                        'status': 'executed',
                        'artifact_reused': str(latest_packet),
                    },
                }),
                encoding='utf-8',
            )
            (seo_dir / 'reddit_monitor_latest.md').write_text(
                '\n'.join([
                    '# Reddit monitor',
                    '',
                    '## Best current discussion opportunities (reply-worthiness first, product-fit second)',
                    '',
                    '### 1) fresh thread title',
                    '- URL: <https://www.reddit.com/r/AI_Agents/comments/fresh1>',
                    '- Community: `r/AI_Agents`',
                    '- Freshness: during this pass',
                    '- Direct reply fit: **high**',
                    '- Mention fit: **medium-low**',
                    '- Best RalphWorkflow angle: **content-family match: production_failure**',
                    '- Why it fits: thread is still live',
                    '',
                    '## Strong current rejects',
                ]),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'SEO_REPORTS_DIR', seo_dir):
                artifact = distribution_lane_executor._write_reddit_discussion_handoff_asset(now, ['adoption_metrics_latest.json'])

            self.assertIsNotNone(artifact)
            latest_text = latest_packet.read_text(encoding='utf-8')
            self.assertIn('fresh thread title', latest_text)
            self.assertIn('https://www.reddit.com/r/AI_Agents/comments/fresh1', latest_text)
            self.assertNotIn('stale thread title', latest_text)

    def test_distribution_architecture_repair_does_not_regenerate_delivered_reddit_packet(self):
        now = datetime(2026, 5, 25, 15, 28, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            seo_dir = tmp / 'seo-reports'
            log_dir.mkdir()
            drafts_dir.mkdir()
            seo_dir.mkdir()

            latest_packet = drafts_dir / 'reddit_discussion_handoff_packet_latest.md'
            latest_packet.write_text(
                '# Ralph Workflow Reddit Discussion Handoff Packet\n'
                '## Opportunity 1: how do you keep multi-agent runs reviewable?\n'
                '- URL: <https://www.reddit.com/r/AI_Agents/comments/example1>\n',
                encoding='utf-8',
            )
            delivered_packet_ts = datetime(2026, 5, 25, 12, 20, 0).timestamp()
            os.utime(latest_packet, (delivered_packet_ts, delivered_packet_ts))
            (log_dir / 'marketing_2026-05-25_reddit_discussion_manual_delivery.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T14:24:00+02:00',
                    'type': 'reddit_discussion_manual_delivery',
                    'chosen_action': {
                        'channel': 'current_chat',
                        'packet': str(latest_packet),
                    },
                    'measurement_window': {
                        'review_at': '2026-06-01T14:24:00+02:00',
                    },
                    'result': {
                        'status': 'executed',
                        'artifact_reused': str(latest_packet),
                    },
                }),
                encoding='utf-8',
            )
            (seo_dir / 'reddit_monitor_latest.md').write_text(
                '\n'.join([
                    '# Reddit monitor',
                    '',
                    '## Best current discussion opportunities (reply-worthiness first, product-fit second)',
                    '',
                    '### 1) how do you keep multi-agent runs reviewable?',
                    '- URL: <https://www.reddit.com/r/AI_Agents/comments/example1>',
                    '- Community: `r/AI_Agents`',
                    '- Freshness: during this pass',
                    '- Direct reply fit: **high**',
                    '- Mention fit: **medium-low**',
                    '- Best RalphWorkflow angle: **content-family match: production_failure**',
                    '- Why it fits: thread is still live',
                    '',
                    '## Strong current rejects',
                ]),
                encoding='utf-8',
            )

            decision = LaneDecision(
                lane='distribution_architecture_repair',
                reason='repair the empty board',
                reasons=['empty board'],
                owned_content_posts_last_36h=0,
                unsubmitted_directory_channels=[],
                shared_findings_used=['adoption_metrics_latest.json: Codeberg movement is the primary success gate'],
                artifact_path=str(drafts_dir / 'distribution_action_brief.md'),
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'SEO_REPORTS_DIR', seo_dir), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', tmp / 'outreach-log.md'), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value={}), \
                 patch.object(distribution_lane_executor.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='')), \
                 patch.object(distribution_lane_selector, '_recent_live_external_window_release_at', return_value=None), \
                 patch.object(distribution_lane_selector, '_distribution_architecture_repair_state', return_value={
                     'third_strike': False,
                     'execution_board_fingerprint': 'abc123',
                     'repeat_count': 1,
                     'guard_follow_through_count': 0,
                     'guard_pause_count': 0,
                 }), \
                 patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(False, False)), \
                 patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=5), \
                 patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now=now)

            self.assertEqual(execution.action_type, 'distribution_architecture_repair')
            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('Suppressed regeneration of the Reddit discussion handoff packet', artifact_text)
            self.assertFalse((drafts_dir / '2026-05-25_reddit_discussion_handoff_packet.md').exists())

    def test_reddit_discussion_waiting_asset_reappears_after_packet_refresh_changes_targets(self):
        now = datetime(2026, 5, 27, 4, 21, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            seo_dir = tmp / 'seo-reports'
            log_dir.mkdir()
            drafts_dir.mkdir()
            seo_dir.mkdir()

            artifact = drafts_dir / 'reddit_discussion_handoff_packet_latest.md'
            artifact.write_text(
                '# Ralph Workflow Reddit Discussion Handoff Packet\n'
                '## Opportunity 1: fresh thread title\n'
                '- URL: <https://www.reddit.com/r/AI_Agents/comments/fresh1>\n',
                encoding='utf-8',
            )
            os.utime(artifact, None)
            (log_dir / 'marketing_2026-05-25_reddit_discussion_channel_ready_outreach_asset.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-27T04:20:00+02:00',
                    'type': 'reddit_discussion_channel_ready_outreach_asset',
                    'chosen_action': {
                        'channel': 'manual_contact_asset',
                        'artifact': str(artifact),
                        'title': 'Prepare Reddit discussion handoff packet',
                    },
                    'measurement_window': {'review_at': '2026-06-01T14:08:00+02:00'},
                    'result': {'status': 'prepared', 'artifact': str(artifact)},
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-25_reddit_discussion_manual_delivery.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T14:24:00+02:00',
                    'type': 'reddit_discussion_manual_delivery',
                    'chosen_action': {
                        'channel': 'current_chat',
                        'packet': str(artifact),
                    },
                    'measurement_window': {'review_at': '2026-06-01T14:24:00+02:00'},
                    'result': {'status': 'executed', 'artifact_reused': str(artifact)},
                }),
                encoding='utf-8',
            )
            (seo_dir / 'reddit_monitor_latest.md').write_text(
                '\n'.join([
                    '# Reddit monitor',
                    '',
                    '## Best current discussion opportunities (reply-worthiness first, product-fit second)',
                    '',
                    '### 1) fresh thread title',
                    '- URL: <https://www.reddit.com/r/AI_Agents/comments/fresh1>',
                    '- Community: `r/AI_Agents`',
                    '- Freshness: during this pass',
                    '- Direct reply fit: **high**',
                    '- Mention fit: **medium-low**',
                    '- Best RalphWorkflow angle: **content-family match: production_failure**',
                    '- Why it fits: thread is still live',
                    '',
                    '## Strong current rejects',
                ]),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'SEO_REPORTS_DIR', seo_dir):
                asset = distribution_lane_executor._reddit_discussion_asset_waiting_for_execution(now)

        self.assertIsNotNone(asset)
        self.assertEqual(asset['path'], str(artifact))

    def test_manual_follow_through_prefers_current_primary_repo_flat_packet_over_stale_legacy_asset(self):
        now = datetime(2026, 5, 25, 17, 8, 0)
        decision = LaneDecision(
            lane='manual_outreach_asset_follow_through',
            reason='Reuse the truthful packet.',
            reasons=['primary packet exists'],
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
                json.dumps({
                    'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}
                }),
                encoding='utf-8',
            )
            (log_dir / 'primary_repo_flat_contact_discovery_latest.json').write_text(
                json.dumps({
                    'targets': [
                        {'target': 'TIMEWELL', 'channels': [{'type': 'email', 'value': 'timewell@timewell.jp'}]},
                        {'target': 'Toolradar', 'channels': [{'type': 'email', 'value': 'editorial@toolradar.com'}]},
                        {'target': 'Morph', 'channels': [{'type': 'email', 'value': 'info@morphllm.com'}]},
                    ],
                }),
                encoding='utf-8',
            )
            primary_packet = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            primary_packet.write_text(
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
            legacy_asset.write_text('# Reddit packet\n', encoding='utf-8')
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

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', log_dir / 'adoption_metrics_latest.json'), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value={}):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now=now)

            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')

        self.assertEqual(execution.action_type, 'manual_outreach_asset_follow_through')
        self.assertIn('Primary-repo-flat publisher contact packet', artifact_text)
        self.assertIn(str(primary_packet), artifact_text)
        self.assertIn('TIMEWELL', artifact_text)
        self.assertIn('Morph', artifact_text)
        self.assertNotIn(str(legacy_asset), artifact_text)

    def test_execution_board_surfaces_primary_repo_flat_packet_for_verified_manual_contact_target(self):
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
                                {'type': 'website', 'value': 'https://ctxt.dev/contact', 'label': 'contact form'},
                                {'type': 'telegram', 'value': 'https://t.me/ctxtdev', 'label': 'Telegram'},
                            ],
                        }
                    ]
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md').write_text(
                '# publisher packet\n\n- ToolWise — https://toolwise.ai/tools/ralph-workflow\n\n### 1. ctxt.dev / Signum\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertIn('### 1. Primary-repo-flat publisher contact packet', board_text)
        self.assertIn('Targets: ctxt.dev / Signum', board_text)
        self.assertIn('human-executable via verified public contact paths', board_text)

    def test_execution_board_does_not_surface_primary_repo_flat_packet_for_github_issue_only_target(self):
        now = datetime(2026, 5, 26, 13, 58, 0)

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
                            'target': 'TLDL',
                            'recommended_next_step': 'GitHub issue/PR path is now identified',
                            'channels': [
                                {'type': 'github_issue', 'value': 'https://github.com/shenli/tldl/issues/new', 'label': 'GitHub issue'},
                            ],
                        },
                    ],
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md').write_text(
                '# publisher packet\n\n- ToolWise — https://toolwise.ai/tools/ralph-workflow\n\n### 1. TLDL\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertNotIn('### 1. Primary-repo-flat publisher contact packet', board_text)
        self.assertIn('Remaining publisher-contact discovery is not runtime-sendable here: TLDL.', board_text)

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
        self.assertIn('canonical handoff packet no longer covers the current waiting target set', board_text)

    def test_execution_board_prefers_delivery_active_blocker_over_false_stale_packet_flag(self):
        now = datetime(2026, 5, 25, 7, 36, 0)

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
                            'channels': [{'type': 'website', 'value': 'https://ctxt.dev/work-with-me', 'label': 'work with me page'}],
                        },
                        {
                            'target': 'ToolChase',
                            'channels': [{'type': 'email', 'value': 'hello@toolchase.com', 'label': 'email'}],
                        },
                        {
                            'target': 'NxCode',
                            'channels': [{'type': 'email', 'value': 'support@nxcode.io', 'label': 'email'}],
                        },
                        {
                            'target': 'TIMEWELL',
                            'channels': [{'type': 'email', 'value': 'timewell@timewell.jp', 'label': 'email'}],
                        },
                    ]
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md').write_text(
                '\n'.join([
                    '# packet',
                    '',
                    '- ToolWise — https://toolwise.ai/tools/ralph-workflow',
                    '',
                    '### 1. ctxt.dev / Signum',
                    '### 2. ToolChase',
                    '### 3. NxCode',
                    '### 4. TIMEWELL',
                    '',
                ]),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-25_toolchase_manual_delivery.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T05:16:29+02:00',
                    'type': 'primary_repo_flat_contact_manual_delivery',
                    'chosen_action': {
                        'draft': str(drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'),
                    },
                    'measurement_window': {
                        'review_at': '2026-05-31T05:16:29+02:00',
                    },
                    'result': {
                        'status': 'executed',
                        'ok': True,
                        'artifact': str(drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'),
                    },
                    'ok': True,
                    'target': 'ToolChase',
                }),
                encoding='utf-8',
            )
            (log_dir / 'distribution_lane_latest.json').write_text(
                json.dumps({
                    'short_review_window_release_at': '2026-05-25T08:24:28',
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertIn('Primary-repo-flat publisher contact packet was already manually delivered in the current review window', board_text)
        self.assertIn('Primary-repo-flat publisher contact packet already exists but was already delivered in the current review window', board_text)
        self.assertNotIn('canonical handoff packet is stale', board_text)

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

    def test_execution_board_hides_primary_repo_flat_packet_when_recent_outreach_target_uses_long_title(self):
        now = datetime(2026, 5, 27, 8, 39, 0)

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
                            'target': 'SitePoint',
                            'channels': [
                                {'type': 'email', 'value': 'support@sitepoint.com', 'label': 'email'},
                            ],
                        },
                    ],
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md').write_text(
                '# publisher packet\n\n### 1. SitePoint\n',
                encoding='utf-8',
            )
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
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'):
                board_path, targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertEqual(targets, [])
        self.assertNotIn('### 1. Primary-repo-flat publisher contact packet', board_text)
        self.assertNotIn('Targets: SitePoint', board_text)

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
                 patch.object(distribution_lane_executor, '_resolve_measurement_hold_release_delivery', return_value={'channel': 'matrix', 'to': '@mistlight_oriroris:matrix.org'}), \
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
        self.assertEqual(cron_log['verification']['delivery_channel'], 'matrix')
        self.assertEqual(cron_log['verification']['delivery_target'], '@mistlight_oriroris:matrix.org')
        self.assertEqual(
            cron_log['verification']['reentry_contract_path'],
            str(drafts_dir / 'post_hold_distribution_reentry_latest.md'),
        )
        add_command = next(call.args[0] for call in mock_run.call_args_list if call.args and call.args[0][:3] == ['openclaw', 'cron', 'add'])
        self.assertIn('--channel', add_command)
        self.assertIn('matrix', add_command)
        self.assertIn('--to', add_command)
        self.assertIn('@mistlight_oriroris:matrix.org', add_command)

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
                def fake_run(command, *args, **kwargs):
                    if command[:3] == ['openclaw', 'cron', 'list']:
                        return SimpleNamespace(returncode=0, stdout=json.dumps({'jobs': [{
                            'id': 'cron-keep',
                            'name': 'marketing-measurement-hold-release',
                            'enabled': True,
                            'schedule': {'kind': 'at', 'at': '2026-05-25T02:05:05'},
                        }]}), stderr='')
                    return SimpleNamespace(returncode=1, stdout='{}', stderr='')

                mock_run.side_effect = fake_run
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
                def fake_run(command, *args, **kwargs):
                    if command[:3] == ['openclaw', 'cron', 'list']:
                        return SimpleNamespace(returncode=0, stdout=json.dumps({'jobs': [{
                            'id': 'cron-keep',
                            'name': 'marketing-measurement-hold-release',
                            'enabled': True,
                            'schedule': {'kind': 'at', 'at': '2026-05-25T07:20:16'},
                        }]}), stderr='')
                    return SimpleNamespace(returncode=1, stdout='{}', stderr='')

                mock_run.side_effect = fake_run
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

    def test_measurement_hold_scheduler_ignores_stale_release_log_without_live_job(self):
        now = datetime(2026, 5, 25, 20, 30, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            (log_dir / 'marketing_2026-05-25_142310_measurement_hold_release_cron.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T14:23:10.404832',
                    'chosen_action': {'type': 'measurement_hold_release_cron'},
                    'review_window': {'scheduled_run_at': '2026-05-25T15:07:03'},
                    'result': {'status': 'scheduled', 'ok': True, 'live_external_action': False},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor.subprocess, 'run') as mock_run:
                mock_run.side_effect = [
                    SimpleNamespace(returncode=0, stdout=json.dumps({'jobs': []}), stderr=''),
                    SimpleNamespace(returncode=0, stdout=json.dumps({'job': {'id': 'fresh-cron', 'name': 'marketing-measurement-hold-release'}}), stderr=''),
                ]

                schedule = distribution_lane_executor._schedule_measurement_hold_release_run(
                    now=now,
                    release_at='2026-05-25T23:07:41',
                    shared_findings_used=['adoption_metrics_latest.json'],
                    reentry_contract_path=str(drafts_dir / 'post_hold_distribution_reentry_latest.md'),
                )

            cron_log = json.loads((log_dir / 'marketing_2026-05-25_203000_measurement_hold_release_cron.json').read_text(encoding='utf-8'))

        self.assertEqual(schedule['status'], 'scheduled')
        self.assertEqual(schedule['job_id'], 'fresh-cron')
        self.assertEqual(schedule['scheduled_run_at'], '2026-05-25T23:07:41')
        self.assertEqual(cron_log['review_window']['scheduled_run_at'], '2026-05-25T23:07:41')

    def test_measurement_hold_scheduler_passes_timezone_aware_cron_at_argument(self):
        now = datetime(2026, 5, 26, 7, 11, 0)

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
                    SimpleNamespace(returncode=0, stdout=json.dumps({'jobs': []}), stderr=''),
                    SimpleNamespace(returncode=0, stdout=json.dumps({'job': {'id': 'fresh-cron', 'name': 'marketing-measurement-hold-release'}}), stderr=''),
                ]

                schedule = distribution_lane_executor._schedule_measurement_hold_release_run(
                    now=now,
                    release_at='2026-05-26T09:50:16',
                    shared_findings_used=['adoption_metrics_latest.json'],
                    reentry_contract_path=str(drafts_dir / 'post_hold_distribution_reentry_latest.md'),
                )

                add_call = mock_run.call_args_list[1].args[0]

            cron_log = json.loads((log_dir / 'marketing_2026-05-26_071100_measurement_hold_release_cron.json').read_text(encoding='utf-8'))

        self.assertEqual(schedule['status'], 'scheduled')
        self.assertEqual(schedule['scheduled_run_at'], '2026-05-26T09:50:16')
        self.assertEqual(add_call[add_call.index('--at') + 1], '2026-05-26T09:50:16+02:00')
        self.assertEqual(cron_log['verification']['cron_at_argument'], '2026-05-26T09:50:16+02:00')

    def test_current_measurement_hold_release_run_normalizes_utc_cron_time_to_local_display(self):
        now = datetime(2026, 5, 26, 9, 11, 0)

        with patch.object(distribution_lane_executor.subprocess, 'run', return_value=SimpleNamespace(
            returncode=0,
            stdout=json.dumps({'jobs': [{
                'id': 'cron-live',
                'name': 'marketing-measurement-hold-release',
                'enabled': True,
                'schedule': {'kind': 'at', 'at': '2026-05-26T07:50:16.000Z'},
            }]}),
            stderr='',
        )):
            scheduled = distribution_lane_executor._current_measurement_hold_release_run(now)

        self.assertEqual(scheduled, '2026-05-26T09:50:16')

    def test_measurement_hold_scheduler_removes_stale_running_release_job_before_reschedule(self):
        now = datetime(2026, 5, 26, 2, 13, 0)

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
                            'id': 'stale-running-cron',
                            'name': 'marketing-measurement-hold-release',
                            'enabled': True,
                            'status': 'running',
                            'schedule': {'kind': 'at', 'at': '2026-05-25T23:07:41'},
                            'state': {'runningAtMs': 1779752163746},
                        }
                    ]}), stderr=''),
                    SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True}), stderr=''),
                    SimpleNamespace(returncode=0, stdout=json.dumps({'job': {'id': 'fresh-cron', 'name': 'marketing-measurement-hold-release'}}), stderr=''),
                ]

                schedule = distribution_lane_executor._schedule_measurement_hold_release_run(
                    now=now,
                    release_at='2026-05-26T03:05:18',
                    shared_findings_used=['adoption_metrics_latest.json'],
                    reentry_contract_path=str(drafts_dir / 'post_hold_distribution_reentry_latest.md'),
                )

                add_call = mock_run.call_args_list[2].args[0]

            cron_log = json.loads((log_dir / 'marketing_2026-05-26_021300_measurement_hold_release_cron.json').read_text(encoding='utf-8'))

        self.assertEqual(schedule['status'], 'scheduled')
        self.assertEqual(schedule['job_id'], 'fresh-cron')
        self.assertEqual(schedule['removed_stale_jobs'][0]['job_id'], 'stale-running-cron')
        self.assertEqual(cron_log['cleanup']['removed_stale_jobs'][0]['job_id'], 'stale-running-cron')
        message = add_call[add_call.index('--message') + 1]
        self.assertIn('verify from the latest distribution-lane and execution-board artifacts that the short review window has actually cleared', message)
        self.assertIn('treat the wake as an early-release scheduling failure', message)

    def test_measurement_hold_scheduler_removes_overdue_idle_release_job_before_reschedule(self):
        now = datetime(2026, 5, 26, 12, 41, 36)

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
                            'id': 'overdue-idle-cron',
                            'name': 'marketing-measurement-hold-release',
                            'enabled': True,
                            'status': 'idle',
                            'schedule': {'kind': 'at', 'at': '2026-05-26T12:30:22'},
                        }
                    ]}), stderr=''),
                    SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True}), stderr=''),
                    SimpleNamespace(returncode=0, stdout=json.dumps({'job': {'id': 'fresh-cron', 'name': 'marketing-measurement-hold-release'}}), stderr=''),
                ]

                schedule = distribution_lane_executor._schedule_measurement_hold_release_run(
                    now=now,
                    release_at='2026-05-26T13:14:38',
                    shared_findings_used=['adoption_metrics_latest.json'],
                    reentry_contract_path=str(drafts_dir / 'post_hold_distribution_reentry_latest.md'),
                )

                rm_call = mock_run.call_args_list[1].args[0]

            cron_log = json.loads((log_dir / 'marketing_2026-05-26_124136_measurement_hold_release_cron.json').read_text(encoding='utf-8'))

        self.assertEqual(schedule['status'], 'scheduled')
        self.assertEqual(schedule['job_id'], 'fresh-cron')
        self.assertEqual(schedule['removed_stale_jobs'][0]['job_id'], 'overdue-idle-cron')
        self.assertEqual(cron_log['cleanup']['removed_stale_jobs'][0]['job_id'], 'overdue-idle-cron')
        self.assertEqual(rm_call[:4], ['openclaw', 'cron', 'rm', 'overdue-idle-cron'])

    def test_measurement_hold_scheduler_uses_later_live_short_window_release_than_stale_requested_release(self):
        now = datetime(2026, 5, 26, 13, 19, 30)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            (log_dir / 'distribution_lane_latest.json').write_text(
                json.dumps({'lane': 'distribution_architecture_repair', 'short_review_window_release_at': '2026-05-26T13:22:23'}),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor.subprocess, 'run') as mock_run:
                mock_run.side_effect = [
                    SimpleNamespace(returncode=0, stdout=json.dumps({'jobs': [
                        {
                            'id': 'stale-future-cron',
                            'name': 'marketing-measurement-hold-release',
                            'enabled': True,
                            'status': 'idle',
                            'schedule': {'kind': 'at', 'at': '2026-05-26T13:14:38'},
                        }
                    ]}), stderr=''),
                    SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True}), stderr=''),
                    SimpleNamespace(returncode=0, stdout=json.dumps({'job': {'id': 'fresh-cron', 'name': 'marketing-measurement-hold-release'}}), stderr=''),
                ]

                schedule = distribution_lane_executor._schedule_measurement_hold_release_run(
                    now=now,
                    release_at='2026-05-26T13:14:38',
                    shared_findings_used=['adoption_metrics_latest.json'],
                    reentry_contract_path=str(drafts_dir / 'post_hold_distribution_reentry_latest.md'),
                )

                add_call = mock_run.call_args_list[2].args[0]

            cron_log = json.loads((log_dir / 'marketing_2026-05-26_131930_measurement_hold_release_cron.json').read_text(encoding='utf-8'))

        self.assertEqual(schedule['status'], 'scheduled')
        self.assertEqual(schedule['scheduled_run_at'], '2026-05-26T13:22:23')
        self.assertEqual(schedule['removed_stale_jobs'][0]['job_id'], 'stale-future-cron')
        self.assertEqual(add_call[add_call.index('--at') + 1], '2026-05-26T13:22:23+02:00')
        self.assertEqual(cron_log['review_window']['scheduled_run_at'], '2026-05-26T13:22:23')

    def test_current_measurement_hold_release_run_prefers_future_job_over_overdue_idle_one(self):
        now = datetime(2026, 5, 26, 12, 56, 0)

        with patch.object(distribution_lane_executor.subprocess, 'run', return_value=SimpleNamespace(
            returncode=0,
            stdout=json.dumps({'jobs': [
                {
                    'id': 'overdue-idle-cron',
                    'name': 'marketing-measurement-hold-release',
                    'enabled': True,
                    'status': 'idle',
                    'schedule': {'kind': 'at', 'at': '2026-05-26T12:30:22'},
                },
                {
                    'id': 'future-cron',
                    'name': 'marketing-measurement-hold-release',
                    'enabled': True,
                    'status': 'idle',
                    'schedule': {'kind': 'at', 'at': '2026-05-26T13:14:38'},
                },
            ]}),
            stderr='',
        )):
            scheduled = distribution_lane_executor._current_measurement_hold_release_run(now)

        self.assertEqual(scheduled, '2026-05-26T13:14:38')

    def test_current_measurement_hold_release_run_hides_stale_job_before_current_short_window_release(self):
        now = datetime(2026, 5, 26, 13, 19, 30)

        with patch.object(distribution_lane_executor.subprocess, 'run', return_value=SimpleNamespace(
            returncode=0,
            stdout=json.dumps({'jobs': [
                {
                    'id': 'stale-future-cron',
                    'name': 'marketing-measurement-hold-release',
                    'enabled': True,
                    'status': 'idle',
                    'schedule': {'kind': 'at', 'at': '2026-05-26T13:14:38'},
                }
            ]}),
            stderr='',
        )):
            scheduled = distribution_lane_executor._current_measurement_hold_release_run(
                now,
                not_before='2026-05-26T13:22:23',
            )

        self.assertEqual(scheduled, '')

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
                            {'type': 'website', 'value': 'https://ctxt.dev/about', 'label': 'about page'},
                            {'type': 'x', 'value': 'https://x.com/ctxtdev', 'label': 'X/Twitter'},
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
                 patch.object(distribution_lane_executor, '_write_marketing_execution_board', return_value=(drafts_dir / 'marketing_execution_board_latest.md', [])) as mock_board_write, \
                 patch.object(distribution_lane_executor.subprocess, 'run') as mock_run:
                mock_run.return_value.returncode = 1
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')

        self.assertEqual(execution.action_type, 'primary_repo_flat_contact_handoff_follow_through')
        self.assertIn('non-runtime-executable channels', execution.summary.lower())
        self.assertNotIn('## Execute these first', artifact_text)
        self.assertNotIn('Ready-to-send email draft', artifact_text)
        self.assertNotIn('Short contact-form version', artifact_text)
        mock_board_write.assert_called_once_with(now)

    def test_primary_repo_flat_packet_reschedules_post_hold_rerun_when_short_window_moves_later(self):
        now = datetime(2026, 5, 26, 4, 1, 0)
        decision = LaneDecision(
            lane='primary_repo_flat_contact_handoff_packet',
            reason='Fresh primary-repo-flat publisher targets now have verified public contact paths.',
            reasons=['publisher contacts discovered'],
            owned_content_posts_last_36h=1,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json', 'marketing_execution_board_latest.md'],
            artifact_path='',
            short_review_window_release_at='2026-05-26T08:57:00',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            discovery = {
                'targets': [
                    {'target': 'AI Saying', 'channels': [{'type': 'email', 'value': 'hello@aisaying.com'}]},
                ]
            }
            (log_dir / 'primary_repo_flat_contact_discovery_latest.json').write_text(json.dumps(discovery), encoding='utf-8')
            board_path = drafts_dir / 'marketing_execution_board_latest.md'
            reentry_path = drafts_dir / 'post_hold_distribution_reentry_latest.md'
            board_path.write_text('# board\n', encoding='utf-8')
            reentry_path.write_text('# reentry\n', encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=None), \
                 patch.object(distribution_lane_executor, '_write_marketing_execution_board', return_value=(board_path, [])), \
                 patch.object(distribution_lane_executor, '_write_post_hold_reentry_contract', return_value=reentry_path), \
                 patch.object(distribution_lane_executor.subprocess, 'run') as mock_run:
                mock_run.side_effect = [
                    SimpleNamespace(returncode=0, stdout=json.dumps({'jobs': []}), stderr=''),
                    SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True}), stderr=''),
                    SimpleNamespace(returncode=0, stdout=json.dumps({'job': {'id': 'fresh-cron', 'name': 'marketing-measurement-hold-release'}}), stderr=''),
                ]
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            artifact_path = Path(execution.artifact_path)
            artifact_text = artifact_path.read_text(encoding='utf-8')
            cron_log = json.loads((log_dir / 'marketing_2026-05-26_040100_measurement_hold_release_cron.json').read_text(encoding='utf-8'))

        self.assertEqual(execution.action_type, 'primary_repo_flat_contact_handoff_packet_execution')
        self.assertIn('Scheduled an automatic post-hold marketer rerun at the updated short-window release time.', execution.summary)
        self.assertIn('## Post-hold marketer rerun scheduled', artifact_text)
        self.assertIn('2026-05-26T08:57:00', artifact_text)
        self.assertEqual(cron_log['review_window']['scheduled_run_at'], '2026-05-26T08:57:00')
        self.assertNotIn('cleanup', cron_log)

    def test_primary_repo_flat_packet_refresh_also_refreshes_execution_board(self):
        now = datetime(2026, 5, 25, 11, 14, 27)
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
                        'target': 'Toolradar',
                        'article_url': 'https://toolradar.com/guides/best-ai-coding-tools',
                        'root_url': 'https://toolradar.com/',
                        'hook': 'Best AI Coding Tools in 2026',
                        'reason': 'B2B buyer guide audience already comparing coding-agent tradeoffs and adjacent workflow layers.',
                        'outreach_subject': 'Ralph Workflow as a workflow-system addition to your AI coding tools guide',
                        'recommended_next_step': 'email/contact send path is now identified',
                        'channels': [
                            {'type': 'email', 'value': 'editorial@toolradar.com', 'label': 'email'},
                        ],
                    },
                ]
            }
            (log_dir / 'primary_repo_flat_contact_discovery_latest.json').write_text(json.dumps(discovery), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=None), \
                 patch.object(distribution_lane_executor, '_write_marketing_execution_board', return_value=(drafts_dir / 'marketing_execution_board_latest.md', ['Toolradar'])) as mock_board_write, \
                 patch.object(distribution_lane_executor.subprocess, 'run') as mock_run:
                mock_run.return_value.returncode = 1
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

        self.assertEqual(execution.action_type, 'primary_repo_flat_contact_handoff_packet_execution')
        mock_board_write.assert_called_once_with(now)

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

    def test_primary_repo_flat_packet_refresh_excludes_targets_already_covered_by_recent_outreach_or_manual_delivery(self):
        now = datetime(2026, 5, 27, 0, 53, 4)
        findings = [
            {
                'target': 'ctxt.dev / Signum',
                'article_url': 'https://ctxt.dev/posts/en/tasks-are-not-goals',
                'hook': 'Tasks Are Not Goals',
                'reason': 'Contract-first workflow audience overlap.',
                'outreach_subject': 'RalphWorkflow for your next contract-first roundup',
                'recommended_next_step': 'Telegram consulting contact path is explicitly confirmed',
                'channels': [
                    {'type': 'telegram', 'value': 'https://t.me/ctxtdev', 'label': 'Telegram'},
                ],
            },
            {
                'target': 'ToolChase',
                'article_url': 'https://toolchase.com/blog/best-ai-coding-tools-2026/',
                'hook': 'AI coding tools comparison page',
                'reason': 'Comparison audience overlap.',
                'outreach_subject': 'Ralph Workflow for your next AI coding tools comparison refresh',
                'recommended_next_step': 'email/contact send path is now identified',
                'channels': [
                    {'type': 'email', 'value': 'hello@toolchase.com', 'label': 'email'},
                ],
            },
            {
                'target': 'TLDL',
                'article_url': 'https://www.tldl.io/resources/ai-coding-tools-2026',
                'hook': 'AI Coding Tools Compared (2026)',
                'reason': 'Comparison audience overlap strongly with workflow evaluators.',
                'outreach_subject': 'Ralph Workflow for your next AI coding tools comparison refresh',
                'recommended_next_step': 'GitHub issue/PR path is now identified',
                'channels': [
                    {'type': 'github_issue', 'value': 'https://github.com/shenli/tldl/issues/new', 'label': 'GitHub issue'},
                ],
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            drafts_dir.mkdir()
            log_dir.mkdir()
            (log_dir / 'marketing_2026-05-25_toolchase_publisher_outreach.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T12:00:00',
                    'chosen_action': {'type': 'publisher_email_outreach'},
                    'target': 'ToolChase',
                    'status': 'executed',
                    'ok': True,
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-25_primary_repo_flat_contact_manual_delivery_refresh.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T10:23:54+02:00',
                    'chosen_action': {
                        'type': 'primary_repo_flat_contact_manual_delivery_refresh',
                        'packet': str(drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'),
                    },
                    'why_this_action': {
                        'targets_prepared': ['ctxt.dev / Signum'],
                    },
                    'measurement_window': {
                        'review_at': '2026-06-01T10:23:54+02:00',
                    },
                    'result': {'status': 'delivered_to_current_chat', 'ok': True},
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, '_latest_research_signals', return_value=[]), \
                 patch.object(distribution_lane_executor, '_append_live_listing_proof', return_value=None):
                artifact, prepared = distribution_lane_executor._write_primary_repo_flat_contact_handoff_packet(now, findings)

            text = artifact.read_text(encoding='utf-8')

        self.assertEqual(prepared, ['ToolChase'])
        self.assertIn('### 1. ToolChase', text)
        self.assertNotIn('### 1. ctxt.dev / Signum', text)
        self.assertNotIn('### 2. TLDL', text)

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

        self.assertIn('A refreshed primary-repo-flat publisher packet now exists for the current waiting target set, but the short review window is still active', board_text)
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

    def test_execution_board_does_not_misclassify_manual_asset_as_reddit_from_summary_only(self):
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

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertIn('Manual publisher outreach asset', board_text)
        self.assertNotIn('Manual community discussion asset', board_text)

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
            packet_path = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            packet_path.write_text('# publisher packet\n\n### 1. AXME Code\n', encoding='utf-8')
            delivered_source_mtime = datetime(2026, 5, 24, 7, 30, 0)
            os.utime(packet_path, (delivered_source_mtime.timestamp(), delivered_source_mtime.timestamp()))
            (log_dir / 'marketing_2026-05-24_primary_repo_flat_contact_manual_delivery.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-24T07:33:00+02:00',
                    'chosen_action': {
                        'type': 'primary_repo_flat_contact_manual_delivery',
                        'packet': str(packet_path),
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

    def test_execution_board_hides_primary_repo_flat_packet_when_prepared_only_repeat_threshold_is_hit(self):
        now = datetime(2026, 5, 26, 14, 11, 0)

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
                            'target': 'TLDL',
                            'channels': [{'type': 'website', 'value': 'https://www.tldl.io/resources/ai-coding-tools-2026'}],
                        }
                    ]
                }),
                encoding='utf-8',
            )
            packet_path = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            packet_path.write_text('# publisher packet\n\n### 1. TLDL\n', encoding='utf-8')
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

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertNotIn('### 1. Primary-repo-flat publisher contact packet', board_text)
        self.assertIn('No do-now handoff packet is currently truthful in this review window.', board_text)

    def test_execution_board_hides_primary_repo_flat_packet_after_manual_delivery_refresh_in_active_review_window(self):
        now = datetime(2026, 5, 25, 6, 53, 23)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            packet_path = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            (log_dir / 'primary_repo_flat_contact_discovery_latest.json').write_text(
                json.dumps({
                    'targets': [
                        {
                            'target': 'ctxt.dev / Signum',
                            'channels': [{'type': 'website', 'value': 'https://ctxt.dev/work-with-me', 'label': 'contact form'}],
                        },
                        {
                            'target': 'NxCode',
                            'channels': [{'type': 'website', 'value': 'https://www.nxcode.io/ar/contact', 'label': 'contact form'}],
                        },
                    ]
                }),
                encoding='utf-8',
            )
            packet_path.write_text(
                '# publisher packet\n\n- ToolWise — https://toolwise.ai/tools/ralph-workflow\n\n### 1. ctxt.dev / Signum\n\n### 2. NxCode\n',
                encoding='utf-8',
            )
            delivered_source_mtime = datetime(2026, 5, 25, 6, 45, 0)
            os.utime(packet_path, (delivered_source_mtime.timestamp(), delivered_source_mtime.timestamp()))
            (log_dir / 'marketing_2026-05-25_primary_repo_flat_contact_manual_delivery_refresh.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T06:50:32+02:00',
                    'chosen_action': {
                        'type': 'primary_repo_flat_contact_manual_delivery_refresh',
                        'packet': str(packet_path),
                    },
                    'measurement_window': {'review_at': '2026-06-01T06:50:32+02:00'},
                    'result': {
                        'status': 'executed',
                        'ok': True,
                        'artifact': str(packet_path),
                    },
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

    def test_execution_board_surfaces_refreshed_primary_repo_flat_packet_after_same_path_delivery_log(self):
        now = datetime(2026, 5, 25, 17, 4, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            packet_path = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            (log_dir / 'primary_repo_flat_contact_discovery_latest.json').write_text(
                json.dumps({
                    'targets': [
                        {
                            'target': 'Toolradar',
                            'channels': [{'type': 'email', 'value': 'editorial@toolradar.com', 'label': 'email'}],
                        }
                    ]
                }),
                encoding='utf-8',
            )
            packet_path.write_text(
                '# publisher packet\n\n- ToolWise — https://toolwise.ai/tools/ralph-workflow\n\n### 1. Toolradar\n',
                encoding='utf-8',
            )
            refreshed_at = datetime(2026, 5, 25, 15, 4, 46)
            os.utime(packet_path, (refreshed_at.timestamp(), refreshed_at.timestamp()))
            (log_dir / 'marketing_2026-05-25_primary_repo_flat_contact_manual_delivery_refresh.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-25T06:50:32+02:00',
                    'chosen_action': {
                        'type': 'primary_repo_flat_contact_manual_delivery_refresh',
                        'packet': str(packet_path),
                    },
                    'measurement_window': {'review_at': '2026-06-01T06:50:32+02:00'},
                    'result': {
                        'status': 'executed',
                        'ok': True,
                        'artifact': str(packet_path),
                    },
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertIn('### 1. Primary-repo-flat publisher contact packet', board_text)
        self.assertIn('Targets: Toolradar', board_text)
        self.assertNotIn('already manually delivered in the current review window', board_text)

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

    def test_execution_board_filters_non_runtime_primary_targets_already_covered_by_recent_delivery_or_outreach(self):
        now = datetime(2026, 5, 26, 21, 37, 0)

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
                            'target': 'AI Saying',
                            'channels': [{'type': 'website', 'value': 'https://aisaying.ai/contact'}],
                        },
                        {
                            'target': 'TLDL',
                            'channels': [{'type': 'website', 'value': 'https://tldl.ai/about'}],
                        },
                    ]
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-26_aisaying_publisher_outreach.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-26T03:47:33+02:00',
                    'action_type': 'publisher_feedback_form_submission',
                    'target': 'AI Saying',
                    'status': 'executed',
                    'ok': True,
                }),
                encoding='utf-8',
            )
            (log_dir / 'marketing_2026-05-26_signum_manual_delivery.json').write_text(
                json.dumps({
                    'timestamp': '2026-05-26T18:00:00+02:00',
                    'chosen_action': {'type': 'manual_outreach_asset_follow_through', 'channel': 'current_chat_manual_handoff'},
                    'why_this_action': {'targets_prepared': ['ctxt.dev / Signum']},
                    'result': {
                        'status': 'delivered_to_current_chat',
                        'ok': True,
                        'next_review_at': '2026-05-31T18:00:00+02:00',
                    },
                }),
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH', log_dir / 'primary_repo_flat_contact_discovery_latest.json'):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertIn('Remaining publisher-contact discovery is not runtime-sendable here: TLDL.', board_text)
        self.assertNotIn('Remaining publisher-contact discovery is not runtime-sendable here: ctxt.dev / Signum', board_text)
        self.assertIn('Fresh publisher outreach already shipped in the current review window for: AI Saying.', board_text)

    def test_execution_board_hides_current_chat_final_reply_manual_asset(self):
        now = datetime(2026, 5, 27, 4, 6, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

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

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir):
                assets = distribution_lane_executor._manual_outreach_assets_waiting_for_execution(now)

        self.assertEqual(assets, [])

    def test_manual_outreach_assets_skip_post_hold_only_primary_repo_flat_packet_after_repeat_threshold(self):
        now = datetime(2026, 5, 25, 5, 54, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            packet_path = drafts_dir / 'primary_repo_flat_contact_handoff_packet_latest.md'
            packet_path.write_text('# packet\n', encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, '_current_primary_repo_flat_actionable_findings', return_value=[{
                     'target': 'TLDL',
                     'channels': [{'type': 'email', 'value': 'tips@tldl.example'}],
                 }]), \
                 patch.object(distribution_lane_executor, '_handoff_packet_is_current', return_value=True), \
                 patch.object(distribution_lane_executor, '_primary_repo_flat_packet_delivery_still_active', return_value=False), \
                 patch.object(distribution_lane_executor, '_primary_repo_flat_recent_prep_count', return_value=2), \
                 patch.object(distribution_lane_executor, '_short_review_window_release_at', return_value=datetime(2026, 5, 25, 7, 20, 16)):
                assets = distribution_lane_executor._manual_outreach_assets_waiting_for_execution(now)

        self.assertEqual(assets, [])

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

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir):
                targets = distribution_lane_executor._active_manual_outreach_delivery_targets(now)

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

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir):
                targets = distribution_lane_executor._active_manual_outreach_delivery_targets(now)

        self.assertEqual(targets, {'TLDL', 'ComputingForGeeks'})

    def test_execution_board_surfaces_repo_proof_asset_after_exhausted_stackoverflow_slot(self):
        now = datetime(2026, 5, 26, 6, 30, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', log_dir / 'comparison_backlink_queue_latest.json'), \
                 patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(True, True)), \
                 patch.object(distribution_lane_selector, '_stack_overflow_post_cooldown_surface_exhausted', return_value=True):
                board_path, targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertIn('### 1. Repo conversion proof asset', board_text)
        self.assertIn('workflow composition example + START_HERE routing', board_text)
        self.assertNotIn('No do-now handoff packet is currently truthful in this review window.', board_text)
        self.assertIn(str(distribution_lane_executor.WORKFLOW_COMPOSITION_EXAMPLE_PATH), targets)
        self.assertIn(str(distribution_lane_executor.START_HERE_PATH), targets)

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

    def test_execution_board_surfaces_due_apollo_followup_review(self):
        now = datetime(2026, 5, 26, 2, 18, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            (log_dir / 'apollo_sequence_status_latest.json').write_text(
                json.dumps({
                    'status': 'not_outcome_ready',
                    'measurement_pending': False,
                    'record_count': 5,
                    'sequence_name': 'Ralph Workflow curator follow-up — Codeberg CTA',
                    'needs_live_verification': False,
                    'next_review_at': '2026-05-26T01:11:13+02:00',
                    'launch_review_at': '2026-06-01T23:11:13+02:00',
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'apollo_launch_handoff_packet_latest.md').write_text('# Apollo handoff packet\n', encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', log_dir / 'comparison_backlink_queue_latest.json'), \
                 patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(True, True)):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertIn('Apollo outcome-readiness review packet', board_text)
        self.assertIn('Ralph Workflow curator follow-up — Codeberg CTA', board_text)
        self.assertNotIn('No do-now handoff packet is currently truthful in this review window.', board_text)

    def test_execution_board_surfaces_apollo_runtime_blocker_review_when_followup_is_due(self):
        now = datetime(2026, 5, 26, 2, 36, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            (log_dir / 'apollo_sequence_status_latest.json').write_text(
                json.dumps({
                    'status': 'runtime_auth_blocked',
                    'measurement_pending': False,
                    'record_count': 5,
                    'sequence_name': 'Ralph Workflow curator follow-up — Codeberg CTA',
                    'needs_live_verification': True,
                    'runtime_blocker_status': 'cloudflare_auth_blocked',
                    'next_review_at': '2026-05-26T01:11:13+02:00',
                }),
                encoding='utf-8',
            )
            (log_dir / 'apollo_status.json').write_text(
                json.dumps({
                    'status': 'cloudflare_auth_blocked',
                    'cloudflare_blocked': True,
                    'notes': 'Cloudflare interstitial detected on authenticated surface.',
                }),
                encoding='utf-8',
            )
            (drafts_dir / 'apollo_launch_handoff_packet_latest.md').write_text('# Apollo handoff packet\n', encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'APOLLO_STATUS_PATH', log_dir / 'apollo_status.json'), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', log_dir / 'comparison_backlink_queue_latest.json'), \
                 patch.object(distribution_lane_selector, '_active_repair_pause_flags', return_value=(True, True)):
                board_path, _targets = distribution_lane_executor._write_marketing_execution_board(now)

            board_text = board_path.read_text(encoding='utf-8')

        self.assertIn('Apollo runtime-blocker review packet', board_text)
        self.assertIn('Ralph Workflow curator follow-up — Codeberg CTA', board_text)
        self.assertIn('apollo_runtime_blocker_review_packet', board_text)
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

    def test_delivered_manual_outreach_asset_is_not_reused_on_execution_side(self):
        now = datetime(2026, 5, 25, 5, 49, 0)

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

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir):
                assets = distribution_lane_executor._manual_outreach_assets_waiting_for_execution(now)

        self.assertEqual(assets, [])

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
            fingerprint = distribution_lane_selector.hashlib.sha1(
                distribution_lane_selector._normalized_execution_board_text(
                    board_path.read_text(encoding='utf-8')
                ).encode('utf-8')
            ).hexdigest()
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

    def test_distribution_architecture_guard_pause_skips_duplicate_follow_through_churn(self):
        now = datetime(2026, 5, 25, 8, 0, 0)
        decision = LaneDecision(
            lane='distribution_architecture_guard_pause',
            reason='The same empty-board distribution-architecture failure is still guarded and was already acknowledged once in this review window; pause further duplicate guard follow-through churn until the board fingerprint or blocker set materially changes.',
            reasons=['A third-strike distribution-architecture churn guard is already active for this same execution-board fingerprint.'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json', 'primary_repo_flat_contact_discovery_latest.json'],
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
            fingerprint = distribution_lane_selector.hashlib.sha1(
                distribution_lane_selector._normalized_execution_board_text(
                    board_path.read_text(encoding='utf-8')
                ).encode('utf-8')
            ).hexdigest()
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

        self.assertEqual(execution.action_type, 'distribution_architecture_guard_pause')
        self.assertEqual(execution.status, 'skipped_repair')
        self.assertIn('pause further duplicate guard notes', artifact_text.lower())
        self.assertIn('prior guard follow-through runs in this window: 1', artifact_text.lower())

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
                 patch.object(distribution_lane_executor, '_write_reddit_discussion_handoff_asset', return_value=None), \
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
                 patch.object(distribution_lane_executor, '_write_reddit_discussion_handoff_asset', return_value=None), \
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
