import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.marketing import apollo_sequence_launcher, apollo_sequence_status, channel_discovery, distribution_lane_executor, distribution_lane_selector, generate_content, marketing_loop_checker, marketing_loop_independent_verify, marketing_loop_runner, marketing_momentum_watchdog, marketing_workflow_audit, run, run_posting, sync_outreach_log


class GenerateContentTests(unittest.TestCase):
    def test_build_draft_content_includes_experiment_metadata(self):
        topic = generate_content.TOPIC_ROTATION[0]
        content = generate_content.build_draft_content(topic, datetime(2026, 5, 11, 7, 0, 0))
        self.assertIn('experiment_id: "2026-05-11-spec_driven"', content)
        self.assertIn('product: "RalphWorkflow"', content)
        self.assertIn("# Spec-Driven AI Agent: Why Explicit Contracts Change What Your Agent Produces", content)


class PostingTests(unittest.TestCase):
    def test_parse_front_matter_extracts_metadata_and_body(self):
        raw = """---
experiment_id: \"exp-1\"
content_type: \"technical\"
---

# Hello World

Body here.
"""
        metadata, body = run_posting.parse_front_matter(raw)
        self.assertEqual(metadata["experiment_id"], "exp-1")
        self.assertEqual(metadata["content_type"], "technical")
        self.assertIn("# Hello World", body)

    def test_already_posted_successfully_matches_hash(self):
        posted = {
            "posts": [
                {"platform": "write.as", "ok": True, "draft_hash": "abc"},
                {"platform": "write.as", "ok": False, "draft_hash": "xyz"},
            ]
        }
        self.assertTrue(run_posting.already_posted_successfully(posted, "abc"))
        self.assertFalse(run_posting.already_posted_successfully(posted, "xyz"))


class MarketingPathTests(unittest.TestCase):
    def test_default_outreach_logs_live_under_agents_marketing(self):
        expected = Path('/home/mistlight/.openclaw/workspace/agents/marketing/outreach-log.md')
        self.assertEqual(distribution_lane_selector.OUTREACH_LOG_PATH, expected)
        self.assertEqual(distribution_lane_executor.OUTREACH_LOG_PATH, expected)
        self.assertEqual(apollo_sequence_launcher.OUTREACH_LOG, expected)
        self.assertEqual(marketing_workflow_audit.OUTREACH, expected)

    def test_sync_outreach_log_backfills_missing_submission_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            outreach = tmp / 'outreach-log.md'
            logs = tmp / 'logs'
            logs.mkdir()
            outreach.write_text('# Outreach Log\n\n## 2026-05-23\n\n## Notes\n', encoding='utf-8')
            artifact = logs / 'marketing_2026-05-23_aitoolboard_submission.json'
            artifact.write_text(json.dumps({
                'timestamp': '2026-05-23T06:43:10Z',
                'channel': {
                    'name': 'AIToolboard',
                    'submit_url': 'https://aitoolboard.com/submit',
                },
                'submitted_payload': {
                    'website_url': 'https://codeberg.org/RalphWorkflow/Ralph-Workflow',
                },
                'result': {
                    'http_code': 200,
                    'response': {'success': True},
                },
            }), encoding='utf-8')
            added = sync_outreach_log.sync_submission_artifacts(outreach_path=outreach, logs_dir=logs)
            text = outreach.read_text(encoding='utf-8')
            self.assertEqual(added, ['marketing_2026-05-23_aitoolboard_submission.json'])
            self.assertIn('**AIToolboard** — directory submission sent', text)
            self.assertIn('https://aitoolboard.com/submit', text)
            self.assertIn('https://codeberg.org/RalphWorkflow/Ralph-Workflow', text)
            self.assertEqual(sync_outreach_log.sync_submission_artifacts(outreach_path=outreach, logs_dir=logs), [])


class MarketingDiscoveryTests(unittest.TestCase):
    def test_channel_discovery_seeds_vbwebtools_from_execution_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            artifact = tmp / 'marketing_2026-05-23_vbwebtools_submission.json'
            artifact.write_text(json.dumps({
                'timestamp': '2026-05-23T11:08:00+02:00',
                'status': 'executed',
                'ok': True,
                'live_external_action': True,
                'submit_url': 'https://www.vbwebtools.com/submit-tool/',
            }), encoding='utf-8')
            seeded = channel_discovery.seed_results_from_execution_logs(tmp)
            self.assertEqual(len(seeded), 1)
            self.assertEqual(seeded[0]['name'], 'vbwebtools')
            self.assertEqual(seeded[0]['status'], 'accessible')
            self.assertEqual(seeded[0]['url'], 'https://www.vbwebtools.com/submit-tool/')


class MarketingDecisionTests(unittest.TestCase):
    def test_build_weekly_decisions_prefers_best_bucket(self):
        summary = {
            "technical": {"posts": 2, "views": 120, "avg_views": 60.0},
            "philosophy": {"posts": 2, "views": 40, "avg_views": 20.0},
        }
        seo_trends = {}
        seo_current = {"onpage_score": "75/100", "backlinks_approx": 0}
        actions = []
        decisions = run.build_weekly_decisions(summary, seo_trends, seo_current, actions)
        actions_text = "\n".join(item["action"] for item in decisions)
        self.assertIn("Keep publishing technical content.", actions_text)
        self.assertIn("Shift one future slot away from philosophy toward technical.", actions_text)

    def test_build_weekly_decisions_adds_competitor_decision_on_monday(self):
        summary = {}
        seo_trends = {}
        seo_current = {"onpage_score": "75/100"}
        actions = []
        competitor_data = {
            "competitor_count": 8,
            "competitors": {
                "aider": {"stars": 44680},
                "hermes-agent": {"stars": 145388},
            }
        }
        decisions = run.build_weekly_decisions(
            summary, seo_trends, seo_current, actions,
            competitor_data=competitor_data
        )
        actions_text = "\n".join(item["action"] for item in decisions)
        self.assertTrue(any("comparison pages" in a for a in actions_text.split("\n")))
        self.assertTrue(any("Hermes" in a or "145388" in a for a in actions_text.split("\n")))

    def test_build_weekly_decisions_refuses_zero_signal_content_winner_and_holds_telegraph_when_flat(self):
        summary = {
            "technical": {"posts": 2, "views": 0, "avg_views": 0.0},
            "philosophy": {"posts": 1, "views": 0, "avg_views": 0.0},
        }
        seo_trends = {}
        seo_current = {"onpage_score": "75/100"}
        actions = []
        adoption = {
            "evaluation": {"failing_signals": ["primary_repo_flat"]},
            "recent_window": {"Codeberg": {"samples": 9, "stars_delta_window": 0, "watchers_delta_window": 0, "forks_delta_window": 0}},
        }
        decisions = run.build_weekly_decisions(summary, seo_trends, seo_current, actions, adoption_data=adoption)
        actions_text = "\n".join(item["action"] for item in decisions)
        repair_text = "\n".join(item.get("repair", "") for item in decisions)
        self.assertIn("Do not infer a winning owned-content format yet.", actions_text)
        self.assertIn("Hold Telegraph at maintenance only; do not treat more owned-content volume as the next best move.", actions_text)
        self.assertIn("current runtime proof", repair_text)
        self.assertNotIn("write.as is dead", repair_text)
        self.assertNotIn("Keep publishing technical content.", actions_text)

    def test_build_weekly_decisions_uses_runtime_proof_language_when_not_flat(self):
        summary = {"technical": {"posts": 2, "views": 120, "avg_views": 60.0}}
        seo_trends = {}
        seo_current = {"onpage_score": "80/100"}
        actions = []
        adoption = {
            "evaluation": {"failing_signals": []},
            "recent_window": {"Codeberg": {"samples": 9, "stars_delta_window": 1, "watchers_delta_window": 0, "forks_delta_window": 0}},
        }

        decisions = run.build_weekly_decisions(summary, seo_trends, seo_current, actions, adoption_data=adoption)
        actions_text = "\n".join(item["action"] for item in decisions)

        self.assertIn("Continue only the owned/distribution channels that have current runtime proof, and keep Codeberg as the primary CTA.", actions_text)
        self.assertNotIn("write.as is permanently blocked", actions_text)

    def test_recent_successful_posts_filters_old_or_failed_posts(self):
        now = datetime(2026, 5, 12, 9, 0, 0)
        posts = [
            {"ok": True, "date": "2026-05-11", "title": "recent"},
            {"ok": True, "date": "2026-03-01", "title": "old"},
            {"ok": False, "date": "2026-05-10", "title": "failed"},
        ]
        filtered = run.recent_successful_posts(posts, now, days=30)
        self.assertEqual([p["title"] for p in filtered], ["recent"])


class DistributionLaneSelectorTests(unittest.TestCase):
    def test_recent_live_action_family_count_counts_sent_curator_email(self):
        now = datetime(2026, 5, 23, 18, 0, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            log_dir.mkdir()
            (log_dir / 'marketing_2026-05-23_155607_andyrewlee_curator_email.json').write_text(json.dumps({
                'timestamp_utc': '2026-05-23T15:56:07+00:00',
                'action': 'curator_email_outreach',
                'status': 'sent',
                'channel': {'recipient': 'andyrewlee@gmail.com'},
            }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir):
                count = distribution_lane_selector._recent_live_action_family_count(now, hours=24, family='curator_outreach')

            self.assertEqual(count, 1)

    def test_recent_executed_action_type_counts_repo_conversion_quickstart_patch_as_proof_asset(self):
        now = datetime(2026, 5, 24, 1, 47, 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            log_dir.mkdir()
            (log_dir / 'marketing_2026-05-24_repo_conversion_quickstart_patch.json').write_text(json.dumps({
                'timestamp': '2026-05-24T01:32:00+02:00',
                'chosen_action': {
                    'type': 'repo_conversion_quickstart_patch',
                },
                'result': {
                    'status': 'executed',
                    'ok': True,
                },
            }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir):
                seen = distribution_lane_selector._recent_executed_action_type(
                    now,
                    action_types=distribution_lane_selector.RECENT_PROOF_ASSET_ACTION_TYPES,
                )

            self.assertTrue(seen)

    def test_prefers_directory_submission_when_content_is_saturated_and_new_easy_channel_exists(self):
        now = datetime(2026, 5, 22, 6, 0, 0)
        adoption = {
            "evaluation": {"failing_signals": ["primary_repo_flat"]}
        }
        channel_log = {
            "working": [
                {"name": "aitoolsindex"},
                {"name": "thenextai"},
                {"name": "freshlane"},
            ]
        }
        outreach_text = "AIToolsIndex submitted earlier. The Next AI submitted earlier."
        recent_logs = [
            ({"timestamp": "2026-05-21T22:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post A", "channel": "telegraph"}, "result": {"ok": True}}, "marketing_a.json"),
            ({"timestamp": "2026-05-22T01:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post B", "channel": "telegraph"}, "result": {"ok": True}}, "marketing_b.json"),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            reset_queue_path = log_dir / 'distribution_reset_targets_latest.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text(outreach_text, encoding='utf-8')
            reset_queue_path.write_text(json.dumps({'targets': []}), encoding='utf-8')
            for payload, name in recent_logs:
                (log_dir / name).write_text(json.dumps(payload), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', tmp / 'missing.json'):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'directory_submission')
            self.assertEqual(decision.unsubmitted_directory_channels, ['freshlane'])
            self.assertTrue((drafts_dir / '2026-05-22_distribution_action_brief.md').exists())

    def test_prefers_curator_outreach_when_content_is_saturated_but_no_new_directory_lane_exists(self):
        now = datetime(2026, 5, 22, 6, 0, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": [{"name": "aitoolsindex"}, {"name": "thenextai"}]}
        outreach_text = "AIToolsIndex submitted earlier. The Next AI submitted earlier."

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text(outreach_text, encoding='utf-8')
            (log_dir / 'marketing_a.json').write_text(json.dumps({"timestamp": "2026-05-21T22:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post A", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')
            (log_dir / 'marketing_b.json').write_text(json.dumps({"timestamp": "2026-05-22T01:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post B", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, '_apollo_ready', return_value=False), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', tmp / 'missing.json'):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'curator_outreach')

    def test_prefers_directory_confirmation_when_directory_burst_is_active_and_snapshot_is_stale(self):
        now = datetime(2026, 5, 23, 23, 33, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()

            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            backlink_status_path = log_dir / 'backlink_status_latest.json'
            reset_queue_path = log_dir / 'distribution_reset_targets_latest.json'

            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps({"working": []}), encoding='utf-8')
            outreach_path.write_text('', encoding='utf-8')
            reset_queue_path.write_text(json.dumps({'targets': []}), encoding='utf-8')
            backlink_status_path.write_text(json.dumps({
                'generated_at': '2026-05-23T16:51:24+00:00',
                'summary': {
                    'directories_with_live_listings': 4,
                    'queries_indexed': 1,
                    'total_queries': 14,
                },
            }), encoding='utf-8')

            for idx in range(4):
                (log_dir / f'marketing_2026-05-23_example{idx}_submission.json').write_text(json.dumps({
                    'timestamp': f'2026-05-23T1{idx}:00:00',
                    'status': 'executed',
                    'ok': True,
                    'live_external_action': True,
                    'submit_url': f'https://example{idx}.com/submit',
                }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'BACKLINK_STATUS_LATEST_PATH', backlink_status_path), \
                 patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', tmp / 'missing.json'), \
                 patch.object(distribution_lane_selector, '_apollo_ready', return_value=False), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'directory_confirmation')
            self.assertIn('directory submissions already burst', decision.reason.lower())

    def test_prefers_directory_confirmation_over_reset_when_burst_and_snapshot_are_stale(self):
        now = datetime(2026, 5, 24, 0, 58, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        curator_queue = {
            "targets": [
                {"target": f"target-{idx}", "status": "sent_via_email_fallback", "review_due_date": "2026-06-05", "last_contact_at": "2026-05-23T05:00:00"}
                for idx in range(5)
            ]
        }
        comparison_queue = {
            "targets": [
                {"slug": f"comp-{idx}", "name": f"Comp {idx}", "status": "prepared", "review_due_date": "2026-06-05"}
                for idx in range(1, 9)
            ]
        }
        stackoverflow_latest = {
            "drafts_created": 1,
            "drafts": [{"question_title": "Workflow reliability question", "question_url": "https://stackoverflow.com/q/1"}],
        }
        apollo_sequence = {
            "measurement_pending": True,
            "next_review_at": "2026-05-30T00:14:49+02:00",
        }
        market_intelligence = {
            "comparison_pages": [
                {"slug": f"comp-{idx}", "name": f"Comp {idx}", "path": f"/comparisons/comp-{idx}.md"}
                for idx in range(1, 9)
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            (drafts_dir / 'stackoverflow').mkdir()

            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            backlink_status_path = log_dir / 'backlink_status_latest.json'
            apollo_sequence_path = log_dir / 'apollo_sequence_status_latest.json'
            stackoverflow_path = log_dir / 'stackoverflow_answer_lane_latest.json'
            market_path = log_dir / 'market_intelligence.json'
            reset_queue_path = log_dir / 'distribution_reset_targets_latest.json'
            handoff_path = drafts_dir / 'stackoverflow_answer_handoff_packet_latest.md'

            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps({"working": []}), encoding='utf-8')
            outreach_path.write_text('HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.', encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps(comparison_queue), encoding='utf-8')
            backlink_status_path.write_text(json.dumps({
                'generated_at': '2026-05-23T16:51:24+00:00',
                'summary': {
                    'directories_with_live_listings': 4,
                    'queries_indexed': 1,
                    'total_queries': 14,
                },
            }), encoding='utf-8')
            apollo_sequence_path.write_text(json.dumps(apollo_sequence), encoding='utf-8')
            stackoverflow_path.write_text(json.dumps(stackoverflow_latest), encoding='utf-8')
            market_path.write_text(json.dumps(market_intelligence), encoding='utf-8')
            reset_queue_path.write_text(json.dumps({'targets': []}), encoding='utf-8')
            handoff_path.write_text('# StackOverflow handoff\n', encoding='utf-8')
            (drafts_dir / 'stackoverflow' / 'so_answer_2026-05-23_example.md').write_text('# draft\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_stackoverflow_manual_delivery.json').write_text(json.dumps({
                'timestamp': '2026-05-23T20:39:00+02:00',
                'chosen_action': {'type': 'stackoverflow_manual_delivery'},
                'result': {'status': 'executed', 'ok': True, 'live_external_action': False},
            }), encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_repo_conversion_docs_push.json').write_text(json.dumps({
                'timestamp': '2026-05-23T21:00:00+02:00',
                'chosen_action': {'type': 'repo_conversion_docs_push'},
                'result': {'status': 'executed', 'ok': True, 'live_external_action': True},
            }), encoding='utf-8')
            for idx in range(4):
                (log_dir / f'marketing_2026-05-23_example{idx}_submission.json').write_text(json.dumps({
                    'timestamp': f'2026-05-23T1{idx}:00:00',
                    'status': 'executed',
                    'ok': True,
                    'live_external_action': True,
                    'submit_url': f'https://example{idx}.com/submit',
                }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'BACKLINK_STATUS_LATEST_PATH', backlink_status_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_SEQUENCE_STATUS_PATH', apollo_sequence_path), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', stackoverflow_path), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_HANDOFF_LATEST_PATH', handoff_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path), \
                 patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue_path), \
                 patch.object(distribution_lane_selector, '_apollo_ready', return_value=False), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'directory_confirmation')
            self.assertIn('refresh live listing and backlink evidence', decision.reason.lower())

    def test_prefers_repo_conversion_proof_asset_when_external_lanes_are_saturated_and_stackoverflow_packet_is_current(self):
        now = datetime(2026, 5, 23, 16, 0, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        curator_queue = {
            "targets": [
                {"target": f"target-{idx}", "status": "sent_via_email_fallback", "review_due_date": "2026-06-05", "last_contact_at": "2026-05-23T05:00:00"}
                for idx in range(5)
            ]
        }
        comparison_queue = {
            "targets": [
                {"slug": "aider", "name": "Aider", "status": "prepared", "review_due_date": "2026-06-05"}
            ]
        }
        market_intelligence = {"comparison_pages": [{"slug": "aider", "name": "Aider", "path": "/tmp/aider.md"}]}
        stackoverflow_latest = {
            "drafts_created": 1,
            "drafts": [{"question_title": "workflow reliability", "draft_file": "/tmp/so_answer.md"}],
        }
        apollo_sequence_status = {
            "measurement_pending": True,
            "next_review_at": "2026-05-30T00:14:49.075391+02:00",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            (drafts_dir / 'stackoverflow').mkdir()

            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_queue.json'
            comparison_queue_path = log_dir / 'comparison_queue.json'
            market_path = log_dir / 'market.json'
            reset_queue_path = log_dir / 'distribution_reset_targets_latest.json'
            apollo_sequence_status_path = log_dir / 'apollo_sequence_status_latest.json'
            stackoverflow_latest_path = log_dir / 'stackoverflow_answer_lane_latest.json'
            stackoverflow_handoff_path = drafts_dir / 'stackoverflow_answer_handoff_packet_latest.md'
            reddit_monitor_path = tmp / 'reddit_monitor_latest.md'

            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text('', encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps(comparison_queue), encoding='utf-8')
            market_path.write_text(json.dumps(market_intelligence), encoding='utf-8')
            reset_queue_path.write_text(json.dumps({'targets': []}), encoding='utf-8')
            apollo_sequence_status_path.write_text(json.dumps(apollo_sequence_status), encoding='utf-8')
            stackoverflow_latest_path.write_text(json.dumps(stackoverflow_latest), encoding='utf-8')
            stackoverflow_handoff_path.write_text('# current packet\n', encoding='utf-8')
            reddit_monitor_path.write_text('Partial visibility only. Fail closed. reddit_direct_access_degraded=1\n', encoding='utf-8')

            for idx in range(4):
                (log_dir / f'marketing_2026-05-23_dir_{idx}_submission.json').write_text(json.dumps({
                    'timestamp': f'2026-05-23T0{idx}:00:00',
                    'status': 'executed',
                    'result': {'ok': True, 'live_external_action': True},
                }), encoding='utf-8')

            for idx in range(5):
                (log_dir / f'marketing_2026-05-23_curator_email_{idx}.json').write_text(json.dumps({
                    'timestamp': f'2026-05-23T0{idx}:30:00',
                    'status': 'executed',
                    'result': {'ok': True, 'live_external_action': True},
                }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path), \
                 patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_SEQUENCE_STATUS_PATH', apollo_sequence_status_path), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', stackoverflow_latest_path), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_HANDOFF_LATEST_PATH', stackoverflow_handoff_path), \
                 patch.object(distribution_lane_selector, 'REDDIT_MONITOR_LATEST', reddit_monitor_path), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False), \
                 patch.object(distribution_lane_selector, '_apollo_ready', return_value=False), \
                 patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=False):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'repo_conversion_proof_asset')

    def test_stops_repeating_repo_conversion_proof_asset_after_recent_docs_push(self):
        now = datetime(2026, 5, 23, 17, 37, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        curator_queue = {
            "targets": [
                {"target": f"target-{idx}", "status": "sent_via_email_fallback", "review_due_date": "2026-06-05", "last_contact_at": "2026-05-23T05:00:00"}
                for idx in range(5)
            ]
        }
        comparison_queue = {
            "targets": [
                {"slug": "aider", "name": "Aider", "status": "prepared", "review_due_date": "2026-06-05"}
            ]
        }
        market_intelligence = {"comparison_pages": [{"slug": "aider", "name": "Aider", "path": "/tmp/aider.md"}]}
        stackoverflow_latest = {
            "drafts_created": 1,
            "drafts": [{"question_title": "workflow reliability", "draft_file": "/tmp/so_answer.md"}],
        }
        apollo_sequence_status = {
            "measurement_pending": True,
            "next_review_at": "2026-05-30T00:14:49.075391+02:00",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            (drafts_dir / 'stackoverflow').mkdir()

            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_queue.json'
            comparison_queue_path = log_dir / 'comparison_queue.json'
            market_path = log_dir / 'market.json'
            reset_queue_path = log_dir / 'distribution_reset_targets_latest.json'
            apollo_sequence_status_path = log_dir / 'apollo_sequence_status_latest.json'
            stackoverflow_latest_path = log_dir / 'stackoverflow_answer_lane_latest.json'
            stackoverflow_handoff_path = drafts_dir / 'stackoverflow_answer_handoff_packet_latest.md'
            reddit_monitor_path = tmp / 'reddit_monitor_latest.md'

            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text('', encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps(comparison_queue), encoding='utf-8')
            market_path.write_text(json.dumps(market_intelligence), encoding='utf-8')
            reset_queue_path.write_text(json.dumps({'targets': []}), encoding='utf-8')
            apollo_sequence_status_path.write_text(json.dumps(apollo_sequence_status), encoding='utf-8')
            stackoverflow_latest_path.write_text(json.dumps(stackoverflow_latest), encoding='utf-8')
            stackoverflow_handoff_path.write_text('# current packet\n', encoding='utf-8')
            reddit_monitor_path.write_text('Partial visibility only. Fail closed. reddit_direct_access_degraded=1\n', encoding='utf-8')

            for idx in range(4):
                (log_dir / f'marketing_2026-05-23_dir_{idx}_submission.json').write_text(json.dumps({
                    'timestamp': f'2026-05-23T0{idx}:00:00',
                    'status': 'executed',
                    'result': {'ok': True, 'live_external_action': True},
                }), encoding='utf-8')

            for idx in range(5):
                (log_dir / f'marketing_2026-05-23_curator_email_{idx}.json').write_text(json.dumps({
                    'timestamp': f'2026-05-23T0{idx}:30:00',
                    'status': 'executed',
                    'result': {'ok': True, 'live_external_action': True},
                }), encoding='utf-8')

            (log_dir / 'marketing_2026-05-23_repo_conversion_docs_push.json').write_text(json.dumps({
                'timestamp': '2026-05-23T16:35:00+02:00',
                'chosen_action': {'type': 'repo_conversion_docs_push'},
                'result': {'ok': True, 'live_external_action': True},
            }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path), \
                 patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_SEQUENCE_STATUS_PATH', apollo_sequence_status_path), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', stackoverflow_latest_path), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_HANDOFF_LATEST_PATH', stackoverflow_handoff_path), \
                 patch.object(distribution_lane_selector, 'REDDIT_MONITOR_LATEST', reddit_monitor_path), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False), \
                 patch.object(distribution_lane_selector, '_apollo_ready', return_value=False), \
                 patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=False):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'distribution_reset')

    def test_submitted_channel_log_with_tld_name_counts_as_attempted(self):
        now = datetime(2026, 5, 23, 6, 0, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": [{"name": "openagents"}]}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text('', encoding='utf-8')
            (log_dir / 'marketing_a.json').write_text(json.dumps({"timestamp": "2026-05-22T22:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post A", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')
            (log_dir / 'marketing_b.json').write_text(json.dumps({"timestamp": "2026-05-23T01:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post B", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_openagents_submission.json').write_text(json.dumps({"channel": {"name": "OpenAgents.pro"}}), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, '_apollo_ready', return_value=False), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', tmp / 'missing.json'):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.unsubmitted_directory_channels, [])
            self.assertNotEqual(decision.lane, 'directory_submission')

    def test_submission_target_name_counts_as_attempted_even_when_channel_field_is_generic(self):
        now = datetime(2026, 5, 23, 11, 20, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": [{"name": "madewithstack"}]}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            reset_queue_path = log_dir / 'distribution_reset_targets_latest.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text('', encoding='utf-8')
            reset_queue_path.write_text(json.dumps({'targets': []}), encoding='utf-8')
            (log_dir / 'marketing_a.json').write_text(json.dumps({"timestamp": "2026-05-22T22:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post A", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')
            (log_dir / 'marketing_b.json').write_text(json.dumps({"timestamp": "2026-05-23T01:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post B", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_madewithstack_submission.json').write_text(json.dumps({
                "channel": "directory_submission",
                "target": "MadeWithStack",
                "submit_url": "https://www.madewithstack.com/submit"
            }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, '_apollo_ready', return_value=False), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', tmp / 'missing.json'):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.unsubmitted_directory_channels, [])
            self.assertNotEqual(decision.lane, 'directory_submission')

    def test_outreach_log_url_marks_aitools_inc_as_attempted(self):
        now = datetime(2026, 5, 23, 11, 20, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": [{"name": "aitools-inc"}]}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            reset_queue_path = log_dir / 'distribution_reset_targets_latest.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text('- **AI Tools (aitools.inc)** — directory submission sent\n  - Submit URL: https://aitools.inc/submit\n', encoding='utf-8')
            reset_queue_path.write_text(json.dumps({'targets': []}), encoding='utf-8')
            (log_dir / 'marketing_a.json').write_text(json.dumps({"timestamp": "2026-05-22T22:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post A", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')
            (log_dir / 'marketing_b.json').write_text(json.dumps({"timestamp": "2026-05-23T01:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post B", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, '_apollo_ready', return_value=False), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', tmp / 'missing.json'):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.unsubmitted_directory_channels, [])
            self.assertFalse(any('Validated easy-submit channels still unused' in reason for reason in decision.reasons))
            self.assertNotEqual(decision.lane, 'directory_submission')


class DistributionLaneSelectorFallbackTests(unittest.TestCase):
    def test_prefers_distribution_reset_over_curator_handoff_when_same_family_windows_are_saturated(self):
        now = datetime(2026, 5, 23, 19, 40, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}

        def recent_action(now_arg, *, action_types, hours=48):
            if action_types == distribution_lane_selector.RECENT_RESET_ACTION_TYPES:
                return True
            if action_types == distribution_lane_selector.RECENT_PROOF_ASSET_ACTION_TYPES:
                return True
            return False

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text('No duplicate directory submissions logged.', encoding='utf-8')

            with ExitStack() as stack:
                for patcher in [
                    patch.object(distribution_lane_selector, 'LOG_DIR', log_dir),
                    patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir),
                    patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path),
                    patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path),
                    patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path),
                    patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json),
                    patch.object(distribution_lane_selector, 'LATEST_MD', latest_md),
                    patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True}),
                    patch.object(distribution_lane_selector, '_github_auth_available', return_value=False),
                    patch.object(distribution_lane_selector, '_apollo_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True),
                    patch.object(distribution_lane_selector, '_apollo_sequence_measurement_status', return_value={'measurement_pending': True}),
                    patch.object(distribution_lane_selector, '_live_curator_queue_count', return_value=8),
                    patch.object(distribution_lane_selector, '_prepared_curator_targets_waiting_for_handoff', return_value=4),
                    patch.object(distribution_lane_selector, '_prepared_curator_target_names', return_value=['AI for Code', 'VibeFactory directory']),
                    patch.object(distribution_lane_selector, '_curator_measurement_window_count', return_value=15),
                    patch.object(distribution_lane_selector, '_contact_discovery_current_for_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_contact_discovery_has_targets', return_value=False),
                    patch.object(distribution_lane_selector, '_manual_contact_targets_waiting_for_execution', return_value=[]),
                    patch.object(distribution_lane_selector, '_comparison_queue_capacity', return_value=(8, 8)),
                    patch.object(distribution_lane_selector, '_distribution_reset_targets_ready', return_value=0),
                    patch.object(distribution_lane_selector, '_recent_live_action_family_count', side_effect=[14, 48]),
                    patch.object(distribution_lane_selector, '_stack_overflow_measurement_pending', return_value=True),
                    patch.object(distribution_lane_selector, '_stack_overflow_handoff_packet_current', return_value=True),
                    patch.object(distribution_lane_selector, '_recent_executed_action_type', side_effect=recent_action),
                ]:
                    stack.enter_context(patcher)
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'distribution_reset')
            self.assertIn('same-family curator windows are already saturated', decision.reason)

    def test_prefers_manual_follow_through_over_rerunning_stackoverflow_during_measurement_window(self):
        now = datetime(2026, 5, 23, 14, 49, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        outreach_text = 'HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.'
        curator_queue = {
            'targets': [
                {
                    'target': f'Curator Target {i}',
                    'status': 'awaiting_reply',
                    'review_due_date': '2026-06-06',
                }
                for i in range(6)
            ]
        }
        comparison_queue = {
            'targets': [
                {'slug': 'claude-code', 'name': 'Claude Code', 'status': 'prepared', 'review_due_date': '2026-06-06'}
            ]
        }
        apollo_status = {'status': 'login_succeeded', 'cloudflare_blocked': False}
        apollo_sequence = {'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}
        market_intelligence = {
            'comparison_pages': [
                {'slug': 'claude-code', 'name': 'Claude Code', 'path': '/comparisons/claude-code.md'}
            ]
        }
        curator_contact_discovery = {
            'targets': [
                {
                    'target': 'OpenAIToolsHub',
                    'status': 'ready',
                    'contact_channels': [{'type': 'email', 'value': 'contact@example.com'}],
                }
            ]
        }
        stackoverflow_latest = {
            'drafts_created': 0,
            'drafts': [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            seo_dir = tmp / 'seo-reports'
            log_dir.mkdir()
            drafts_dir.mkdir()
            seo_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            apollo_path = log_dir / 'apollo_status.json'
            apollo_sequence_path = log_dir / 'apollo_sequence_status_latest.json'
            market_path = log_dir / 'market_intelligence.json'
            so_path = log_dir / 'stackoverflow_answer_lane_latest.json'
            contact_discovery_path = log_dir / 'curator_contact_discovery_latest.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text(outreach_text, encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps(comparison_queue), encoding='utf-8')
            apollo_path.write_text(json.dumps(apollo_status), encoding='utf-8')
            apollo_sequence_path.write_text(json.dumps(apollo_sequence), encoding='utf-8')
            market_path.write_text(json.dumps(market_intelligence), encoding='utf-8')
            so_path.write_text(json.dumps(stackoverflow_latest), encoding='utf-8')
            contact_discovery_path.write_text(json.dumps(curator_contact_discovery), encoding='utf-8')
            so_drafts_dir = drafts_dir / 'stackoverflow'
            so_drafts_dir.mkdir()
            (so_drafts_dir / 'so_answer_2026-05-23_example.md').write_text('# StackOverflow Answer Draft\n', encoding='utf-8')
            (seo_dir / 'reddit_monitor_latest.md').write_text('Reddit is IP-blocked from this environment.\n**Search diagnostics:** ok=0, reddit_ip_blocked=5\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_a_submission.json').write_text(json.dumps({"timestamp": "2026-05-23T08:00:00", "status": "executed", "ok": True, "live_external_action": True}), encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_b_submission.json').write_text(json.dumps({"timestamp": "2026-05-23T09:00:00", "status": "executed", "ok": True, "live_external_action": True}), encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_c_submission.json').write_text(json.dumps({"timestamp": "2026-05-23T10:00:00", "status": "executed", "ok": True, "live_external_action": True}), encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_d_submission.json').write_text(json.dumps({"timestamp": "2026-05-23T11:00:00", "status": "executed", "ok": True, "live_external_action": True}), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_STATUS_PATH', apollo_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_SEQUENCE_STATUS_PATH', apollo_sequence_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', so_path), \
                 patch.object(distribution_lane_selector, 'CURATOR_CONTACT_DISCOVERY_LATEST_PATH', contact_discovery_path), \
                 patch.object(distribution_lane_selector, 'REDDIT_MONITOR_LATEST', seo_dir / 'reddit_monitor_latest.md'), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'stackoverflow_answer_handoff_packet')
            self.assertIn('A fresh StackOverflow answer draft already exists', '\n'.join(decision.reasons))

    def test_avoids_repeating_stackoverflow_handoff_after_manual_delivery(self):
        now = datetime(2026, 5, 23, 21, 0, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        outreach_text = 'HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.'
        curator_queue = {
            'targets': [
                {
                    'target': f'Curator Target {i}',
                    'status': 'awaiting_reply',
                    'review_due_date': '2026-06-06',
                }
                for i in range(6)
            ]
        }
        comparison_queue = {
            'targets': [
                {'slug': 'claude-code', 'name': 'Claude Code', 'status': 'prepared', 'review_due_date': '2026-06-06'}
            ]
        }
        apollo_status = {'status': 'login_succeeded', 'cloudflare_blocked': False}
        apollo_sequence = {'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}
        market_intelligence = {
            'comparison_pages': [
                {'slug': 'claude-code', 'name': 'Claude Code', 'path': '/comparisons/claude-code.md'}
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            seo_dir = tmp / 'seo-reports'
            log_dir.mkdir()
            drafts_dir.mkdir()
            seo_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            apollo_path = log_dir / 'apollo_status.json'
            apollo_sequence_path = log_dir / 'apollo_sequence_status_latest.json'
            market_path = log_dir / 'market_intelligence.json'
            so_path = log_dir / 'stackoverflow_answer_lane_latest.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text(outreach_text, encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps(comparison_queue), encoding='utf-8')
            apollo_path.write_text(json.dumps(apollo_status), encoding='utf-8')
            apollo_sequence_path.write_text(json.dumps(apollo_sequence), encoding='utf-8')
            market_path.write_text(json.dumps(market_intelligence), encoding='utf-8')
            so_path.write_text(json.dumps({'drafts_created': 1, 'drafts': ['draft']}), encoding='utf-8')
            (drafts_dir / 'stackoverflow').mkdir()
            (drafts_dir / 'stackoverflow' / 'so_answer_2026-05-23_example.md').write_text('# StackOverflow Answer Draft\n', encoding='utf-8')
            (drafts_dir / 'stackoverflow_answer_handoff_packet_latest.md').write_text('# current handoff\n', encoding='utf-8')
            (seo_dir / 'reddit_monitor_latest.md').write_text('Reddit is IP-blocked from this environment.\n**Search diagnostics:** ok=0, reddit_ip_blocked=5\n', encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_stackoverflow_manual_delivery.json').write_text(json.dumps({
                'timestamp': '2026-05-23T20:39:00+02:00',
                'chosen_action': {'type': 'stackoverflow_manual_delivery'},
                'result': {'status': 'executed', 'ok': True, 'live_external_action': False},
            }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_STATUS_PATH', apollo_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_SEQUENCE_STATUS_PATH', apollo_sequence_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', so_path), \
                 patch.object(distribution_lane_selector, 'REDDIT_MONITOR_LATEST', seo_dir / 'reddit_monitor_latest.md'), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False), \
                 patch.object(distribution_lane_selector, '_recent_executed_action_type', return_value=False):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'repo_conversion_proof_asset')
            self.assertIn('already delivered for manual placement', '\n'.join(decision.reasons))

    def test_avoids_another_directory_submission_during_same_day_burst(self):
        now = datetime(2026, 5, 23, 12, 0, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": [{"name": "freshlane"}]}
        reddit_report = (
            '# Reddit monitor\n\n'
            '- **Threads/posts scanned:** 0\n'
            '- **Shortlisted:** 0\n'
            '- **Query attempts:** 18\n'
            '- **Search diagnostics:** ok=0, reddit_ip_blocked=9\n\n'
            'Reddit is IP-blocked from this environment.\n'
        )
        apollo_status = {'status': 'login_succeeded', 'cloudflare_blocked': False}
        apollo_sequence = {
            'status': 'measurement_pending_launch_window',
            'measurement_pending': True,
            'next_review_at': '2026-05-30T00:12:00+02:00',
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            seo_dir = tmp / 'seo-reports'
            log_dir.mkdir()
            drafts_dir.mkdir()
            seo_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            apollo_path = log_dir / 'apollo_status.json'
            apollo_sequence_path = log_dir / 'apollo_sequence_status_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            curator_queue_path = log_dir / 'curator_outreach_queue_latest.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text('', encoding='utf-8')
            apollo_path.write_text(json.dumps(apollo_status), encoding='utf-8')
            apollo_sequence_path.write_text(json.dumps(apollo_sequence), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps({'targets': [{'slug': 'claude-code', 'status': 'prepared'}]}), encoding='utf-8')
            curator_queue_path.write_text(json.dumps({'targets': []}), encoding='utf-8')
            (seo_dir / 'reddit_monitor_latest.md').write_text(reddit_report, encoding='utf-8')
            for idx in range(4):
                (log_dir / f'marketing_2026-05-23_lane_{idx}_submission.json').write_text(json.dumps({
                    'timestamp': f'2026-05-23T0{idx}:00:00+00:00',
                    'status': 'executed',
                    'ok': True,
                    'live_external_action': True,
                    'submit_url': f'https://example{idx}.com/submit',
                }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'APOLLO_STATUS_PATH', apollo_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_SEQUENCE_STATUS_PATH', apollo_sequence_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'REDDIT_MONITOR_LATEST', seo_dir / 'reddit_monitor_latest.md'), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'stackoverflow_answer')
            self.assertIn('same-family burst', '\n'.join(decision.reasons))

    def test_prefers_stackoverflow_when_reddit_is_fail_closed_and_curator_windows_are_already_saturated(self):
        now = datetime(2026, 5, 23, 11, 46, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        reddit_report = (
            '# Reddit monitor — RalphWorkflow\n\n'
            '- **Shortlisted:** 4\n'
            '- **Search diagnostics:** indexed_web_ok=3, local_monitor_timeout=1, reddit_direct_access_degraded=1\n\n'
            'Coverage integrity: direct access is still degraded and this pass must be treated as partial visibility only.\n'
            'Current verdict: fail closed on any posting decision.\n'
        )
        curator_queue = {
            'targets': [
                {'target': f'{idx}. Curator {idx}', 'status': 'sent_via_email_fallback', 'review_due_date': '2026-06-05', 'last_contact_at': '2026-05-23T05:00:00Z'}
                for idx in range(1, 7)
            ]
        }
        comparison_queue = {
            'targets': [
                {'slug': 'claude-code', 'name': 'Claude Code', 'status': 'prepared', 'review_due_date': '2026-06-05'},
                {'slug': 'cursor', 'name': 'Cursor', 'status': 'prepared', 'review_due_date': '2026-06-05'},
            ]
        }
        apollo_status = {
            'status': 'login_succeeded',
            'cloudflare_blocked': False,
        }
        apollo_sequence_status = {
            'measurement_pending': True,
            'next_review_at': '2026-05-30T00:14:49.075391+02:00',
        }
        market_intelligence = {
            'comparison_pages': [
                {'slug': 'claude-code', 'name': 'Claude Code', 'path': '/comparisons/claude-code.md'},
                {'slug': 'cursor', 'name': 'Cursor', 'path': '/comparisons/cursor.md'},
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            seo_dir = tmp / 'seo-reports'
            log_dir.mkdir()
            drafts_dir.mkdir()
            seo_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            apollo_path = log_dir / 'apollo_status.json'
            apollo_sequence_path = log_dir / 'apollo_sequence_status.json'
            market_path = log_dir / 'market_intelligence.json'
            reddit_path = seo_dir / 'reddit_monitor_latest.md'
            stackoverflow_path = log_dir / 'stackoverflow_answer_lane_latest.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text('', encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps(comparison_queue), encoding='utf-8')
            apollo_path.write_text(json.dumps(apollo_status), encoding='utf-8')
            apollo_sequence_path.write_text(json.dumps(apollo_sequence_status), encoding='utf-8')
            market_path.write_text(json.dumps(market_intelligence), encoding='utf-8')
            reddit_path.write_text(reddit_report, encoding='utf-8')
            stackoverflow_path.write_text(json.dumps({'drafts_created': 0}), encoding='utf-8')
            (log_dir / 'marketing_a.json').write_text(json.dumps({"timestamp": "2026-05-22T22:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post A", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')
            (log_dir / 'marketing_b.json').write_text(json.dumps({"timestamp": "2026-05-23T01:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post B", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_STATUS_PATH', apollo_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_SEQUENCE_STATUS_PATH', apollo_sequence_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path), \
                 patch.object(distribution_lane_selector, 'REDDIT_MONITOR_LATEST', reddit_path), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', stackoverflow_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False), \
                 patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'stackoverflow_answer')
            self.assertIn('curator/comparison outreach is already saturated or exhausted', decision.reason)

    def test_prefers_handoff_packet_when_apollo_is_only_authenticated_but_not_proven_live(self):
        now = datetime(2026, 5, 22, 21, 0, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        reddit_report = (
            '# Reddit monitor\n\n'
            '- **Threads/posts scanned:** 0\n'
            '- **Shortlisted:** 0\n'
            '- **Query attempts:** 18\n'
            '- **Search diagnostics:** ok=0, reddit_ip_blocked=9\n\n'
            'Reddit is IP-blocked from this environment.\n'
        )
        curator_queue = {
            'targets': [
                {'target': '1. Example Curator', 'status': 'prepared', 'review_due_date': '2026-06-05'}
            ]
        }
        comparison_queue = {
            'targets': [
                {'slug': 'claude-code', 'name': 'Claude Code', 'status': 'prepared', 'review_due_date': '2026-06-05'}
            ]
        }
        apollo_status = {
            'status': 'login_succeeded',
            'cloudflare_blocked': False,
        }
        market_intelligence = {
            'comparison_pages': [
                {'slug': 'claude-code', 'name': 'Claude Code', 'path': '/comparisons/claude-code.md'}
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            seo_dir = tmp / 'seo-reports'
            log_dir.mkdir()
            drafts_dir.mkdir()
            seo_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            apollo_path = log_dir / 'apollo_status.json'
            market_path = log_dir / 'market_intelligence.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text('HN/Lobsters blocker noted.', encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps(comparison_queue), encoding='utf-8')
            apollo_path.write_text(json.dumps(apollo_status), encoding='utf-8')
            market_path.write_text(json.dumps(market_intelligence), encoding='utf-8')
            (seo_dir / 'reddit_monitor_latest.md').write_text(reddit_report, encoding='utf-8')
            (log_dir / 'marketing_a.json').write_text(json.dumps({"timestamp": "2026-05-21T22:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post A", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')
            (log_dir / 'marketing_b.json').write_text(json.dumps({"timestamp": "2026-05-22T01:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post B", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_STATUS_PATH', apollo_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path), \
                 patch.object(distribution_lane_selector, 'REDDIT_MONITOR_LATEST', seo_dir / 'reddit_monitor_latest.md'), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'curator_handoff_packet')
            self.assertIn('Prepared curator targets exist', decision.reason)

    def test_does_not_reselect_apollo_while_measurement_window_is_active(self):
        now = datetime(2026, 5, 23, 9, 0, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        reddit_report = (
            '# Reddit monitor\n\n'
            '- **Threads/posts scanned:** 0\n'
            '- **Shortlisted:** 0\n'
            '- **Query attempts:** 18\n'
            '- **Search diagnostics:** ok=0, reddit_ip_blocked=9\n\n'
            'Reddit is IP-blocked from this environment.\n'
        )
        curator_queue = {
            'targets': [
                {'target': '1. Example Curator', 'status': 'prepared', 'review_due_date': '2026-06-05'}
            ]
        }
        comparison_queue = {'targets': []}
        apollo_status = {
            'status': 'login_succeeded',
            'cloudflare_blocked': False,
        }
        apollo_sequence = {
            'status': 'measurement_pending_launch_window',
            'measurement_pending': True,
            'next_review_at': '2026-05-30T00:12:00+02:00',
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            seo_dir = tmp / 'seo-reports'
            log_dir.mkdir()
            drafts_dir.mkdir()
            seo_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            apollo_path = log_dir / 'apollo_status.json'
            apollo_sequence_path = log_dir / 'apollo_sequence_status_latest.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text('HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.', encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps(comparison_queue), encoding='utf-8')
            apollo_path.write_text(json.dumps(apollo_status), encoding='utf-8')
            apollo_sequence_path.write_text(json.dumps(apollo_sequence), encoding='utf-8')
            (seo_dir / 'reddit_monitor_latest.md').write_text(reddit_report, encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_apollo_sequence_launch.json').write_text(json.dumps({
                'timestamp': '2026-05-23T00:12:00+02:00',
                'chosen_action': {'type': 'apollo_sequence_launch', 'channel': 'apollo_outreach'},
                'result': {'ok': True, 'live_external_action': True, 'outcome_ready': True, 'record_count': 5},
            }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_STATUS_PATH', apollo_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_SEQUENCE_STATUS_PATH', apollo_sequence_path), \
                 patch.object(distribution_lane_selector, 'REDDIT_MONITOR_LATEST', seo_dir / 'reddit_monitor_latest.md'), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'curator_handoff_packet')
            self.assertIn('do not spend this run repackaging the same outbound lane', '\n'.join(decision.reasons))

    def test_prefers_curator_contact_handoff_when_contact_discovery_is_already_current(self):
        now = datetime(2026, 5, 23, 9, 0, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        reddit_report = (
            '# Reddit monitor\n\n'
            '- **Threads/posts scanned:** 0\n'
            '- **Shortlisted:** 0\n'
            '- **Query attempts:** 18\n'
            '- **Search diagnostics:** ok=0, reddit_ip_blocked=9\n\n'
            'Reddit is IP-blocked from this environment.\n'
        )
        curator_queue = {
            'targets': [
                {'target': '1. Example Curator', 'status': 'prepared', 'review_due_date': '2026-06-05'}
            ]
        }
        contact_discovery = {
            'targets': [
                {
                    'target': '1. Example Curator',
                    'channels': [{'type': 'website', 'value': 'https://example.com/contact'}],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            seo_dir = tmp / 'seo-reports'
            log_dir.mkdir()
            drafts_dir.mkdir()
            seo_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            contact_discovery_path = log_dir / 'curator_contact_discovery_latest.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text('HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.', encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps({'targets': []}), encoding='utf-8')
            contact_discovery_path.write_text(json.dumps(contact_discovery), encoding='utf-8')
            (seo_dir / 'reddit_monitor_latest.md').write_text(reddit_report, encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'CURATOR_CONTACT_DISCOVERY_LATEST_PATH', contact_discovery_path), \
                 patch.object(distribution_lane_selector, 'REDDIT_MONITOR_LATEST', seo_dir / 'reddit_monitor_latest.md'), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'curator_contact_handoff_packet')
            self.assertIn('manual-contact execution packet', decision.reason)


class DistributionLaneSelectorManualContactFreshnessTests(unittest.TestCase):
    def test_falls_back_to_distribution_reset_when_manual_handoff_lacks_current_contact_discovery(self):
        now = datetime(2026, 5, 23, 18, 0, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        outreach_text = 'HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.'
        curator_queue = {
            'targets': [
                {
                    'target': 'vivy-yi/awesome-agent-orchestration',
                    'url': 'https://github.com/vivy-yi/awesome-agent-orchestration',
                    'action': 'Submit PR or request inclusion with a Codeberg-primary entry',
                    'priority': 'HIGH',
                    'status': 'email_invalid_manual_handoff_remaining',
                    'review_due_date': '2026-06-06',
                    'last_contact_at': '2026-05-23T05:32:06Z',
                }
            ]
        }
        comparison_queue = {
            'targets': [
                {'slug': f'comp-{idx}', 'name': f'Comp {idx}', 'status': 'prepared', 'review_due_date': '2026-06-05'}
                for idx in range(1, 9)
            ]
        }
        market_intelligence = {
            'comparison_pages': [
                {'slug': f'comp-{idx}', 'name': f'Comp {idx}', 'path': f'/comparisons/comp-{idx}.md'}
                for idx in range(1, 9)
            ]
        }
        apollo_sequence = {
            'measurement_pending': True,
            'next_review_at': '2026-05-30T00:14:49.075391+02:00',
        }
        stackoverflow_latest = {
            'generated_at': '2026-05-23T16:34:38.381978',
            'drafts_created': 0,
            'drafts': [],
            'skipped_existing_drafts': 1,
            'top_questions': [
                {
                    'title': 'How should I structure autonomous AI agent workflows for production reliability?',
                    'url': 'https://stackoverflow.com/questions/79942291/example',
                    'score': 5.7,
                    'votes': 0,
                    'answers': 0,
                }
            ],
        }
        stale_contact_discovery = {'generated_at': '2026-05-23T18:19:57.821130', 'targets': []}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            stackoverflow_drafts_dir = drafts_dir / 'stackoverflow'
            stackoverflow_drafts_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            market_path = log_dir / 'market_intelligence.json'
            apollo_sequence_path = log_dir / 'apollo_sequence_status_latest.json'
            stackoverflow_path = log_dir / 'stackoverflow_answer_lane_latest.json'
            contact_discovery_path = log_dir / 'curator_contact_discovery_latest.json'
            reset_queue_path = log_dir / 'distribution_reset_targets_latest.json'
            reset_log_path = log_dir / 'distribution_reset_execution_log.md'
            stackoverflow_handoff_path = drafts_dir / 'stackoverflow_answer_handoff_packet_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text(outreach_text, encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps(comparison_queue), encoding='utf-8')
            market_path.write_text(json.dumps(market_intelligence), encoding='utf-8')
            apollo_sequence_path.write_text(json.dumps(apollo_sequence), encoding='utf-8')
            stackoverflow_path.write_text(json.dumps(stackoverflow_latest), encoding='utf-8')
            contact_discovery_path.write_text(json.dumps(stale_contact_discovery), encoding='utf-8')
            reset_queue_path.write_text(json.dumps({'targets': []}), encoding='utf-8')
            reset_log_path.write_text('# Distribution Reset Execution Log\n', encoding='utf-8')
            stackoverflow_handoff_path.write_text('# StackOverflow handoff\n', encoding='utf-8')
            (stackoverflow_drafts_dir / 'so_answer_79942291.md').write_text('draft', encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_repo_conversion_docs_push.json').write_text(json.dumps({
                'timestamp': '2026-05-23T15:00:00',
                'action_type': 'repo_conversion_docs_push',
                'result': {'ok': True},
            }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_SEQUENCE_STATUS_PATH', apollo_sequence_path), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', stackoverflow_path), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_HANDOFF_LATEST_PATH', stackoverflow_handoff_path), \
                 patch.object(distribution_lane_selector, 'CURATOR_CONTACT_DISCOVERY_LATEST_PATH', contact_discovery_path), \
                 patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue_path), \
                 patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_LOG_PATH', reset_log_path), \
                 patch.object(distribution_lane_selector, '_apollo_ready', return_value=True), \
                 patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False), \
                 patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'curator_contact_handoff_packet')
            self.assertIn('manual-contact-only curator target', decision.reason)


class ApolloSequenceStatusTests(unittest.TestCase):
    def test_marks_recent_launch_as_measurement_pending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            log_dir.mkdir()
            launch = log_dir / 'marketing_2026-05-23_apollo_sequence_launch.json'
            launch.write_text(json.dumps({
                'timestamp': '2026-05-23T00:12:00+02:00',
                'chosen_action': {'type': 'apollo_sequence_launch'},
                'result': {'outcome_ready': True, 'record_count': 5, 'sequence_name': 'test sequence'},
            }), encoding='utf-8')

            with patch.object(apollo_sequence_status, 'LOG_DIR', log_dir), \
                 patch.object(apollo_sequence_status, 'STATUS_JSON', log_dir / 'apollo_sequence_status_latest.json'), \
                 patch.object(apollo_sequence_status, 'STATUS_MD', log_dir / 'apollo_sequence_status_latest.md'):
                payload = apollo_sequence_status.build_status(datetime.fromisoformat('2026-05-24T00:12:00+02:00'))

            self.assertEqual(payload['status'], 'measurement_pending_launch_window')
            self.assertTrue(payload['measurement_pending'])
            self.assertEqual(payload['record_count'], 5)


class MarketingWorkflowAuditBurstTests(unittest.TestCase):
    def test_flags_same_day_directory_submission_burst_as_failing_tactic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            logs = tmp / 'logs'
            logs.mkdir()
            adoption_path = logs / 'adoption.json'
            retro_path = logs / 'retro.json'
            outreach_path = tmp / 'outreach-log.md'
            audit_json = logs / 'audit.json'
            audit_md = logs / 'audit.md'
            principles = tmp / 'principles.md'
            four = tmp / 'four.md'
            self_improvement = tmp / 'self_improvement.md'
            reddit_latest = tmp / 'reddit_monitor_latest.md'

            adoption_path.write_text(json.dumps({
                'metrics': [],
                'recent_window': {
                    'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                    'GitHub': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                },
                'evaluation': {'failing_signals': ['primary_repo_flat'], 'findings': []},
            }), encoding='utf-8')
            retro_path.write_text(json.dumps({'recent_posts': [], 'repeated_openings': []}), encoding='utf-8')
            outreach_path.write_text('', encoding='utf-8')
            principles.write_text('principles', encoding='utf-8')
            four.write_text('four', encoding='utf-8')
            self_improvement.write_text('self improvement', encoding='utf-8')
            reddit_latest.write_text('ok', encoding='utf-8')
            for idx in range(4):
                (logs / f'marketing_2026-05-23_dir_{idx}_submission.json').write_text(json.dumps({
                    'status': 'executed',
                    'ok': True,
                    'submit_url': f'https://example{idx}.com/submit',
                }), encoding='utf-8')

            with patch.object(marketing_workflow_audit, 'OUT_DIR', logs), \
                 patch.object(marketing_workflow_audit, 'ADOPTION', adoption_path), \
                 patch.object(marketing_workflow_audit, 'RETRO', retro_path), \
                 patch.object(marketing_workflow_audit, 'OUTREACH', outreach_path), \
                 patch.object(marketing_workflow_audit, 'AUDIT_JSON', audit_json), \
                 patch.object(marketing_workflow_audit, 'AUDIT_MD', audit_md), \
                 patch.object(marketing_workflow_audit, 'PRINCIPLES', principles), \
                 patch.object(marketing_workflow_audit, 'FOUR_QUESTIONS_DOC', four), \
                 patch.object(marketing_workflow_audit, 'SELF_IMPROVEMENT_DOC', self_improvement), \
                 patch.object(marketing_workflow_audit, 'REDDIT_MONITOR_LATEST', reddit_latest), \
                 patch.object(marketing_workflow_audit, 'APOLLO_SEQUENCE_STATUS', logs / 'missing.json'):
                self.assertEqual(marketing_workflow_audit.main(), 0)

            payload = json.loads(audit_json.read_text(encoding='utf-8'))
            self.assertIn('same_family_distribution_overlap', payload['failing_tactics'])

    def test_prefers_curator_outreach_when_reddit_coverage_is_degraded_and_hn_ceiling_repeats(self):
        now = datetime(2026, 5, 22, 6, 0, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        reddit_report = (
            '# Reddit monitor\n\n'
            '- **Threads/posts scanned:** 0\n'
            '- **Shortlisted:** 0\n'
            '- **Query attempts:** 18\n'
            '- **Search diagnostics:** ok=1, provider_challenge=9\n\n'
            'No reliable coverage yet.\n'
        )
        audit = {"failing_tactics": ["execution_ceiling_repetition"]}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            seo_dir = tmp / 'seo-reports'
            log_dir.mkdir()
            drafts_dir.mkdir()
            seo_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text('HN/Lobsters remained blocked again.', encoding='utf-8')
            (seo_dir / 'reddit_monitor_latest.md').write_text(reddit_report, encoding='utf-8')
            (log_dir / 'marketing_workflow_audit_latest.json').write_text(json.dumps(audit), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', tmp / 'missing.json'), \
                 patch.object(distribution_lane_selector, 'REDDIT_MONITOR_LATEST', seo_dir / 'reddit_monitor_latest.md'), \
                 patch.object(distribution_lane_selector, 'AUDIT_LATEST_JSON', log_dir / 'marketing_workflow_audit_latest.json'):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'curator_outreach')
            self.assertIn('Monitoring is not the move right now', decision.reason)

    def test_prefers_comparison_backlink_outreach_when_curator_queue_is_saturated(self):
        now = datetime(2026, 5, 22, 6, 0, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        outreach_text = 'HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.'
        queue_payload = {
            'targets': [
                {'target': f'{idx}. target', 'status': 'prepared', 'review_due_date': '2026-06-05'}
                for idx in range(1, 7)
            ]
        }
        market_intelligence = {
            'comparison_pages': [
                {'slug': f'comp-{idx}', 'name': f'Comp {idx}', 'path': f'/comparisons/comp-{idx}.md'}
                for idx in range(1, 9)
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            queue_path = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            market_path = log_dir / 'market_intelligence.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text(outreach_text, encoding='utf-8')
            queue_path.write_text(json.dumps(queue_payload), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps({'targets': []}), encoding='utf-8')
            market_path.write_text(json.dumps(market_intelligence), encoding='utf-8')
            (log_dir / 'marketing_a.json').write_text(json.dumps({"timestamp": "2026-05-21T22:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post A", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')
            (log_dir / 'marketing_b.json').write_text(json.dumps({"timestamp": "2026-05-22T01:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post B", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, '_apollo_ready', return_value=False), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'comparison_backlink_outreach')

    def test_prefers_distribution_reset_when_curator_and_comparison_queues_are_both_saturated(self):
        now = datetime(2026, 5, 22, 6, 0, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        outreach_text = 'HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.'
        curator_queue = {
            'targets': [
                {'target': f'{idx}. target', 'status': 'prepared', 'review_due_date': '2026-06-05'}
                for idx in range(1, 7)
            ]
        }
        comparison_queue = {
            'targets': [
                {'slug': f'comp-{idx}', 'name': f'Comp {idx}', 'status': 'prepared', 'review_due_date': '2026-06-05'}
                for idx in range(1, 9)
            ]
        }
        market_intelligence = {
            'comparison_pages': [
                {'slug': f'comp-{idx}', 'name': f'Comp {idx}', 'path': f'/comparisons/comp-{idx}.md'}
                for idx in range(1, 9)
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            market_path = log_dir / 'market_intelligence.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text(outreach_text, encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps(comparison_queue), encoding='utf-8')
            market_path.write_text(json.dumps(market_intelligence), encoding='utf-8')
            (log_dir / 'marketing_a.json').write_text(json.dumps({"timestamp": "2026-05-21T22:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post A", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')
            (log_dir / 'marketing_b.json').write_text(json.dumps({"timestamp": "2026-05-22T01:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post B", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=True), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'distribution_reset')

    def test_prefers_curator_outreach_when_fresh_reset_targets_exist_only_in_reset_log(self):
        now = datetime(2026, 5, 23, 17, 45, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        outreach_text = 'HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.'
        curator_queue = {'targets': []}
        comparison_queue = {
            'targets': [
                {'slug': 'awesome-claude-code', 'name': 'hesreallyhim/awesome-claude-code', 'url': 'https://github.com/hesreallyhim/awesome-claude-code', 'status': 'prepared', 'review_due_date': '2026-06-05'}
            ]
        }
        market_intelligence = {
            'comparison_pages': [
                {'slug': f'comp-{idx}', 'name': f'Comp {idx}', 'path': f'/comparisons/comp-{idx}.md'}
                for idx in range(1, 9)
            ]
        }
        apollo_sequence = {
            'measurement_pending': True,
            'next_review_at': '2026-05-30T00:14:49.075391+02:00',
        }
        stackoverflow_latest = {
            'drafts_created': 0,
            'drafts': [],
            'top_questions': [],
        }
        reset_log = """# Distribution Reset Execution Log

1. **Agent-Analytics/awesome-multi-agent-orchestrators**
   URL: https://github.com/Agent-Analytics/awesome-multi-agent-orchestrators
   Why it fits: explicitly curates multi-agent orchestrators.

2. **hesreallyhim/awesome-claude-code**
   URL: https://github.com/hesreallyhim/awesome-claude-code
   Why it fits: Claude Code ecosystem roundup.
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            market_path = log_dir / 'market_intelligence.json'
            apollo_sequence_path = log_dir / 'apollo_sequence_status_latest.json'
            stackoverflow_path = log_dir / 'stackoverflow_answer_lane_latest.json'
            reset_queue_path = log_dir / 'distribution_reset_targets_latest.json'
            reset_log_path = log_dir / 'distribution_reset_execution_log.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text(outreach_text, encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps(comparison_queue), encoding='utf-8')
            market_path.write_text(json.dumps(market_intelligence), encoding='utf-8')
            apollo_sequence_path.write_text(json.dumps(apollo_sequence), encoding='utf-8')
            stackoverflow_path.write_text(json.dumps(stackoverflow_latest), encoding='utf-8')
            reset_queue_path.write_text(json.dumps({'targets': []}), encoding='utf-8')
            reset_log_path.write_text(reset_log, encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_repo_conversion_docs_push.json').write_text(json.dumps({
                'timestamp': '2026-05-23T15:00:00',
                'action_type': 'repo_conversion_docs_push',
                'result': {'ok': True},
            }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_SEQUENCE_STATUS_PATH', apollo_sequence_path), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', stackoverflow_path), \
                 patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue_path), \
                 patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_LOG_PATH', reset_log_path), \
                 patch.object(distribution_lane_selector, '_apollo_ready', return_value=True), \
                 patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False), \
                 patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'curator_outreach')
            self.assertIn('Fresh reset targets exist', decision.reason)

    def test_prefers_manual_contact_execution_before_distribution_reset_when_only_manual_handoff_remains(self):
        now = datetime(2026, 5, 23, 18, 0, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        outreach_text = 'HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.'
        curator_queue = {
            'targets': [
                {
                    'target': 'vivy-yi/awesome-agent-orchestration',
                    'url': 'https://github.com/vivy-yi/awesome-agent-orchestration',
                    'action': 'Submit PR or request inclusion with a Codeberg-primary entry',
                    'priority': 'HIGH',
                    'status': 'email_invalid_manual_handoff_remaining',
                    'review_due_date': '2026-06-06',
                    'last_contact_at': '2026-05-23T05:32:06Z',
                }
            ]
        }
        comparison_queue = {
            'targets': [
                {'slug': f'comp-{idx}', 'name': f'Comp {idx}', 'status': 'prepared', 'review_due_date': '2026-06-05'}
                for idx in range(1, 9)
            ]
        }
        market_intelligence = {
            'comparison_pages': [
                {'slug': f'comp-{idx}', 'name': f'Comp {idx}', 'path': f'/comparisons/comp-{idx}.md'}
                for idx in range(1, 9)
            ]
        }
        apollo_sequence = {
            'measurement_pending': True,
            'next_review_at': '2026-05-30T00:14:49.075391+02:00',
        }
        stackoverflow_latest = {
            'generated_at': '2026-05-23T16:34:38.381978',
            'drafts_created': 0,
            'drafts': [],
            'skipped_existing_drafts': 1,
            'top_questions': [
                {
                    'title': 'How should I structure autonomous AI agent workflows for production reliability?',
                    'url': 'https://stackoverflow.com/questions/79942291/example',
                    'score': 5.7,
                    'votes': 0,
                    'answers': 0,
                }
            ],
        }
        contact_discovery = {
            'targets': [
                {
                    'target': 'vivy-yi/awesome-agent-orchestration',
                    'status': 'email_invalid_manual_handoff_remaining',
                    'channels': [
                        {'type': 'github_issue', 'value': 'https://github.com/vivy-yi/awesome-agent-orchestration/issues/new'}
                    ],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            stackoverflow_drafts_dir = drafts_dir / 'stackoverflow'
            stackoverflow_drafts_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            market_path = log_dir / 'market_intelligence.json'
            apollo_sequence_path = log_dir / 'apollo_sequence_status_latest.json'
            stackoverflow_path = log_dir / 'stackoverflow_answer_lane_latest.json'
            contact_discovery_path = log_dir / 'curator_contact_discovery_latest.json'
            reset_queue_path = log_dir / 'distribution_reset_targets_latest.json'
            reset_log_path = log_dir / 'distribution_reset_execution_log.md'
            stackoverflow_handoff_path = drafts_dir / 'stackoverflow_answer_handoff_packet_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text(outreach_text, encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps(comparison_queue), encoding='utf-8')
            market_path.write_text(json.dumps(market_intelligence), encoding='utf-8')
            apollo_sequence_path.write_text(json.dumps(apollo_sequence), encoding='utf-8')
            stackoverflow_path.write_text(json.dumps(stackoverflow_latest), encoding='utf-8')
            contact_discovery_path.write_text(json.dumps(contact_discovery), encoding='utf-8')
            reset_queue_path.write_text(json.dumps({'targets': []}), encoding='utf-8')
            reset_log_path.write_text('# Distribution Reset Execution Log\n', encoding='utf-8')
            stackoverflow_handoff_path.write_text('# StackOverflow handoff\n', encoding='utf-8')
            (stackoverflow_drafts_dir / 'so_answer_79942291.md').write_text('draft', encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_repo_conversion_docs_push.json').write_text(json.dumps({
                'timestamp': '2026-05-23T15:00:00',
                'action_type': 'repo_conversion_docs_push',
                'result': {'ok': True},
            }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_SEQUENCE_STATUS_PATH', apollo_sequence_path), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', stackoverflow_path), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_HANDOFF_LATEST_PATH', stackoverflow_handoff_path), \
                 patch.object(distribution_lane_selector, 'CURATOR_CONTACT_DISCOVERY_LATEST_PATH', contact_discovery_path), \
                 patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue_path), \
                 patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_LOG_PATH', reset_log_path), \
                 patch.object(distribution_lane_selector, '_apollo_ready', return_value=True), \
                 patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False), \
                 patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'curator_contact_handoff_packet')
            self.assertIn('execution packet', decision.reason)

    def test_prefers_manual_contact_lane_even_when_contact_discovery_is_stale(self):
        now = datetime(2026, 5, 23, 18, 0, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        outreach_text = 'HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.'
        curator_queue = {
            'targets': [
                {
                    'target': 'vivy-yi/awesome-agent-orchestration',
                    'url': 'https://github.com/vivy-yi/awesome-agent-orchestration',
                    'action': 'Submit PR or request inclusion with a Codeberg-primary entry',
                    'priority': 'HIGH',
                    'status': 'email_invalid_manual_handoff_remaining',
                    'review_due_date': '2026-06-06',
                    'last_contact_at': '2026-05-23T05:32:06Z',
                }
            ]
        }
        comparison_queue = {
            'targets': [
                {'slug': f'comp-{idx}', 'name': f'Comp {idx}', 'status': 'prepared', 'review_due_date': '2026-06-05'}
                for idx in range(1, 9)
            ]
        }
        market_intelligence = {
            'comparison_pages': [
                {'slug': f'comp-{idx}', 'name': f'Comp {idx}', 'path': f'/comparisons/comp-{idx}.md'}
                for idx in range(1, 9)
            ]
        }
        apollo_sequence = {
            'measurement_pending': True,
            'next_review_at': '2026-05-30T00:14:49.075391+02:00',
        }
        stackoverflow_latest = {
            'generated_at': '2026-05-23T16:34:38.381978',
            'drafts_created': 0,
            'drafts': [],
            'skipped_existing_drafts': 1,
            'top_questions': [
                {
                    'title': 'How should I structure autonomous AI agent workflows for production reliability?',
                    'url': 'https://stackoverflow.com/questions/79942291/example',
                    'score': 5.7,
                    'votes': 0,
                    'answers': 0,
                }
            ],
        }
        stale_contact_discovery = {'generated_at': '2026-05-23T18:19:57.821130', 'targets': []}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            stackoverflow_drafts_dir = drafts_dir / 'stackoverflow'
            stackoverflow_drafts_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            market_path = log_dir / 'market_intelligence.json'
            apollo_sequence_path = log_dir / 'apollo_sequence_status_latest.json'
            stackoverflow_path = log_dir / 'stackoverflow_answer_lane_latest.json'
            contact_discovery_path = log_dir / 'curator_contact_discovery_latest.json'
            reset_queue_path = log_dir / 'distribution_reset_targets_latest.json'
            reset_log_path = log_dir / 'distribution_reset_execution_log.md'
            stackoverflow_handoff_path = drafts_dir / 'stackoverflow_answer_handoff_packet_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text(outreach_text, encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps(comparison_queue), encoding='utf-8')
            market_path.write_text(json.dumps(market_intelligence), encoding='utf-8')
            apollo_sequence_path.write_text(json.dumps(apollo_sequence), encoding='utf-8')
            stackoverflow_path.write_text(json.dumps(stackoverflow_latest), encoding='utf-8')
            contact_discovery_path.write_text(json.dumps(stale_contact_discovery), encoding='utf-8')
            reset_queue_path.write_text(json.dumps({'targets': []}), encoding='utf-8')
            reset_log_path.write_text('# Distribution Reset Execution Log\n', encoding='utf-8')
            stackoverflow_handoff_path.write_text('# StackOverflow handoff\n', encoding='utf-8')
            (stackoverflow_drafts_dir / 'so_answer_79942291.md').write_text('draft', encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_repo_conversion_docs_push.json').write_text(json.dumps({
                'timestamp': '2026-05-23T15:00:00',
                'action_type': 'repo_conversion_docs_push',
                'result': {'ok': True},
            }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_SEQUENCE_STATUS_PATH', apollo_sequence_path), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', stackoverflow_path), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_HANDOFF_LATEST_PATH', stackoverflow_handoff_path), \
                 patch.object(distribution_lane_selector, 'CURATOR_CONTACT_DISCOVERY_LATEST_PATH', contact_discovery_path), \
                 patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue_path), \
                 patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_LOG_PATH', reset_log_path), \
                 patch.object(distribution_lane_selector, '_apollo_ready', return_value=True), \
                 patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False), \
                 patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'curator_contact_handoff_packet')
            self.assertIn('manual-contact-only curator target', decision.reason)

    def test_skips_repeated_curator_contact_handoff_when_packet_is_already_current(self):
        now = datetime(2026, 5, 23, 21, 50, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        outreach_text = 'HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.'
        curator_queue = {
            'targets': [
                {
                    'target': 'vivy-yi/awesome-agent-orchestration',
                    'url': 'https://github.com/vivy-yi/awesome-agent-orchestration',
                    'action': 'Submit PR or request inclusion with a Codeberg-primary entry',
                    'priority': 'HIGH',
                    'status': 'email_invalid_manual_handoff_remaining',
                    'review_due_date': '2026-06-06',
                    'last_contact_at': '2026-05-23T05:32:06Z',
                }
            ]
        }
        comparison_queue = {
            'targets': [
                {'slug': f'comp-{idx}', 'name': f'Comp {idx}', 'status': 'prepared', 'review_due_date': '2026-06-05'}
                for idx in range(1, 9)
            ]
        }
        market_intelligence = {
            'comparison_pages': [
                {'slug': f'comp-{idx}', 'name': f'Comp {idx}', 'path': f'/comparisons/comp-{idx}.md'}
                for idx in range(1, 9)
            ]
        }
        apollo_sequence = {
            'measurement_pending': True,
            'next_review_at': '2026-05-30T00:14:49.075391+02:00',
        }
        stackoverflow_latest = {
            'generated_at': '2026-05-23T16:34:38.381978',
            'drafts_created': 0,
            'drafts': [],
            'skipped_existing_drafts': 1,
            'top_questions': [
                {
                    'title': 'How should I structure autonomous AI agent workflows for production reliability?',
                    'url': 'https://stackoverflow.com/questions/79942291/example',
                    'score': 5.7,
                    'votes': 0,
                    'answers': 0,
                }
            ],
        }
        contact_discovery = {
            'targets': [
                {
                    'target': 'vivy-yi/awesome-agent-orchestration',
                    'status': 'email_invalid_manual_handoff_remaining',
                    'channels': [
                        {'type': 'email', 'value': 'developer@claude-ai-hub.com'}
                    ],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            stackoverflow_drafts_dir = drafts_dir / 'stackoverflow'
            stackoverflow_drafts_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            market_path = log_dir / 'market_intelligence.json'
            apollo_sequence_path = log_dir / 'apollo_sequence_status_latest.json'
            stackoverflow_path = log_dir / 'stackoverflow_answer_lane_latest.json'
            contact_discovery_path = log_dir / 'curator_contact_discovery_latest.json'
            curator_contact_handoff_path = drafts_dir / 'curator_contact_handoff_packet_latest.md'
            reset_queue_path = log_dir / 'distribution_reset_targets_latest.json'
            reset_log_path = log_dir / 'distribution_reset_execution_log.md'
            stackoverflow_handoff_path = drafts_dir / 'stackoverflow_answer_handoff_packet_latest.md'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text(outreach_text, encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps(comparison_queue), encoding='utf-8')
            market_path.write_text(json.dumps(market_intelligence), encoding='utf-8')
            apollo_sequence_path.write_text(json.dumps(apollo_sequence), encoding='utf-8')
            stackoverflow_path.write_text(json.dumps(stackoverflow_latest), encoding='utf-8')
            contact_discovery_path.write_text(json.dumps(contact_discovery), encoding='utf-8')
            curator_contact_handoff_path.write_text(
                '# Ralph Workflow Curator Contact Execution Packet\n\n## Execute these first\n### 1. vivy-yi/awesome-agent-orchestration\n',
                encoding='utf-8',
            )
            reset_queue_path.write_text(json.dumps({'targets': []}), encoding='utf-8')
            reset_log_path.write_text('# Distribution Reset Execution Log\n', encoding='utf-8')
            stackoverflow_handoff_path.write_text('# StackOverflow handoff\n', encoding='utf-8')
            (stackoverflow_drafts_dir / 'so_answer_79942291.md').write_text('draft', encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_repo_conversion_docs_push.json').write_text(json.dumps({
                'timestamp': '2026-05-23T15:00:00',
                'action_type': 'repo_conversion_docs_push',
                'result': {'ok': True},
            }), encoding='utf-8')
            (log_dir / 'marketing_2026-05-23_curator_contact_handoff_packet_execution.json').write_text(json.dumps({
                'timestamp': '2026-05-23T19:46:39',
                'chosen_action': {'type': 'curator_contact_handoff_packet_execution'},
                'result': {'ok': True},
            }), encoding='utf-8')

            with ExitStack() as stack:
                stack.enter_context(patch.object(distribution_lane_selector, 'LOG_DIR', log_dir))
                stack.enter_context(patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir))
                stack.enter_context(patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path))
                stack.enter_context(patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path))
                stack.enter_context(patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path))
                stack.enter_context(patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json))
                stack.enter_context(patch.object(distribution_lane_selector, 'LATEST_MD', latest_md))
                stack.enter_context(patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path))
                stack.enter_context(patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path))
                stack.enter_context(patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path))
                stack.enter_context(patch.object(distribution_lane_selector, 'APOLLO_SEQUENCE_STATUS_PATH', apollo_sequence_path))
                stack.enter_context(patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', stackoverflow_path))
                stack.enter_context(patch.object(distribution_lane_selector, 'STACKOVERFLOW_HANDOFF_LATEST_PATH', stackoverflow_handoff_path))
                stack.enter_context(patch.object(distribution_lane_selector, 'CURATOR_CONTACT_DISCOVERY_LATEST_PATH', contact_discovery_path))
                stack.enter_context(patch.object(distribution_lane_selector, 'CURATOR_CONTACT_HANDOFF_LATEST_PATH', curator_contact_handoff_path))
                stack.enter_context(patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue_path))
                stack.enter_context(patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_LOG_PATH', reset_log_path))
                stack.enter_context(patch.object(distribution_lane_selector, '_apollo_ready', return_value=True))
                stack.enter_context(patch.object(distribution_lane_selector, '_apollo_execution_ready', return_value=True))
                stack.enter_context(patch.object(distribution_lane_selector, '_github_auth_available', return_value=False))
                stack.enter_context(patch.object(distribution_lane_selector, '_load_recent_monitor_summary', return_value={'provider_degraded': True, 'reddit_blocked': True, 'partial_visibility_only': True}))
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'distribution_reset')
            self.assertIn('already current', '\n'.join(decision.reasons))

    def test_prefers_handoff_packet_when_prepared_queues_exist_but_github_auth_is_blocked(self):
        now = datetime(2026, 5, 22, 6, 0, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        outreach_text = 'HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.'
        curator_queue = {
            'targets': [
                {'target': f'{idx}. target', 'status': 'prepared', 'review_due_date': '2026-06-05'}
                for idx in range(1, 7)
            ]
        }
        comparison_queue = {
            'targets': [
                {'slug': f'comp-{idx}', 'name': f'Comp {idx}', 'status': 'prepared', 'review_due_date': '2026-06-05'}
                for idx in range(1, 9)
            ]
        }
        market_intelligence = {
            'comparison_pages': [
                {'slug': f'comp-{idx}', 'name': f'Comp {idx}', 'path': f'/comparisons/comp-{idx}.md'}
                for idx in range(1, 9)
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            market_path = log_dir / 'market_intelligence.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text(outreach_text, encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps(comparison_queue), encoding='utf-8')
            market_path.write_text(json.dumps(market_intelligence), encoding='utf-8')
            (log_dir / 'marketing_a.json').write_text(json.dumps({"timestamp": "2026-05-21T22:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post A", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')
            (log_dir / 'marketing_b.json').write_text(json.dumps({"timestamp": "2026-05-22T01:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post B", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, '_apollo_ready', return_value=False), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'curator_handoff_packet')

    def test_prefers_curator_activation_when_distribution_reset_targets_are_waiting(self):
        now = datetime(2026, 5, 22, 6, 0, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        outreach_text = 'HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.'
        curator_queue = {
            'targets': [
                {'target': f'{idx}. target', 'status': 'prepared', 'review_due_date': '2026-06-05'}
                for idx in range(1, 7)
            ]
        }
        comparison_queue = {
            'targets': [
                {'slug': f'comp-{idx}', 'name': f'Comp {idx}', 'status': 'prepared', 'review_due_date': '2026-06-05'}
                for idx in range(1, 9)
            ]
        }
        reset_queue = {
            'targets': [
                {'target': 'Agent-Analytics/awesome-multi-agent-orchestrators', 'url': 'https://github.com/Agent-Analytics/awesome-multi-agent-orchestrators', 'status': 'discovered'}
            ]
        }
        market_intelligence = {
            'comparison_pages': [
                {'slug': f'comp-{idx}', 'name': f'Comp {idx}', 'path': f'/comparisons/comp-{idx}.md'}
                for idx in range(1, 9)
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            reset_queue_path = log_dir / 'distribution_reset_targets_latest.json'
            market_path = log_dir / 'market_intelligence.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text(outreach_text, encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps(comparison_queue), encoding='utf-8')
            reset_queue_path.write_text(json.dumps(reset_queue), encoding='utf-8')
            market_path.write_text(json.dumps(market_intelligence), encoding='utf-8')
            (log_dir / 'marketing_a.json').write_text(json.dumps({"timestamp": "2026-05-21T22:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post A", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')
            (log_dir / 'marketing_b.json').write_text(json.dumps({"timestamp": "2026-05-22T01:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post B", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'curator_outreach')
            self.assertIn('Fresh reset targets exist', decision.reason)

    def test_distribution_reset_ready_count_ignores_targets_already_executed_elsewhere(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            log_dir.mkdir()
            reset_queue_path = log_dir / 'distribution_reset_targets_latest.json'
            reset_queue_path.write_text(json.dumps({
                'targets': [
                    {
                        'target': 'AI IDE',
                        'url': 'https://aiide.dev/',
                        'status': 'discovered',
                    }
                ]
            }), encoding='utf-8')
            (log_dir / 'marketing_2026-05-24_aiide_distribution_action.json').write_text(json.dumps({
                'timestamp': '2026-05-24T02:06:00+02:00',
                'chosen_action': {
                    'type': 'fresh_curator_outreach',
                    'title': 'Fresh distribution execution: AI IDE curator email',
                },
                'result': {
                    'status': 'sent',
                    'ok': True,
                    'live_external_action': True,
                    'recipient': 'support@aiide.dev',
                },
            }), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue_path):
                ready = distribution_lane_selector._distribution_reset_targets_ready()

            self.assertEqual(ready, 0)
            reconciled = json.loads(reset_queue_path.read_text(encoding='utf-8'))
            self.assertEqual(reconciled['targets'][0]['status'], 'executed_elsewhere')

    def test_prefers_stackoverflow_when_only_internal_curator_work_remains_and_apollo_is_measuring(self):
        now = datetime(2026, 5, 23, 7, 56, 0)
        adoption = {"evaluation": {"failing_signals": ["primary_repo_flat"]}}
        channel_log = {"working": []}
        outreach_text = 'HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.'
        curator_queue = {
            'targets': [
                {
                    'target': '6. GitHub Topics: AI agents',
                    'url': 'https://github.com/topics/ai-agents',
                    'action': 'Check if Ralph Workflow is already tagged; if not, could add topic tag to the repo description',
                    'status': 'prepared',
                    'review_due_date': '2026-06-05',
                }
            ]
        }
        comparison_queue = {
            'targets': [
                {'slug': 'claude-code', 'name': 'Claude Code', 'status': 'prepared', 'review_due_date': '2026-06-05'}
            ]
        }
        apollo_status = {'status': 'login_succeeded', 'cloudflare_blocked': False}
        apollo_sequence = {'measurement_pending': True, 'next_review_at': '2026-05-30T00:14:49+02:00'}
        market_intelligence = {
            'comparison_pages': [
                {'slug': 'claude-code', 'name': 'Claude Code', 'path': '/comparisons/claude-code.md'}
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            seo_dir = tmp / 'seo-reports'
            log_dir.mkdir()
            drafts_dir.mkdir()
            seo_dir.mkdir()
            adoption_path = log_dir / 'adoption.json'
            channel_path = log_dir / 'channels.json'
            outreach_path = tmp / 'outreach-log.md'
            latest_json = log_dir / 'distribution_lane_latest.json'
            latest_md = log_dir / 'distribution_lane_latest.md'
            curator_queue_path = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue_path = log_dir / 'comparison_backlink_queue_latest.json'
            apollo_path = log_dir / 'apollo_status.json'
            apollo_sequence_path = log_dir / 'apollo_sequence_status_latest.json'
            market_path = log_dir / 'market_intelligence.json'
            so_path = log_dir / 'stackoverflow_answer_lane_latest.json'
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text(outreach_text, encoding='utf-8')
            curator_queue_path.write_text(json.dumps(curator_queue), encoding='utf-8')
            comparison_queue_path.write_text(json.dumps(comparison_queue), encoding='utf-8')
            apollo_path.write_text(json.dumps(apollo_status), encoding='utf-8')
            apollo_sequence_path.write_text(json.dumps(apollo_sequence), encoding='utf-8')
            market_path.write_text(json.dumps(market_intelligence), encoding='utf-8')
            so_path.write_text(json.dumps({'drafts_created': 0}), encoding='utf-8')
            (seo_dir / 'reddit_monitor_latest.md').write_text('Reddit is IP-blocked from this environment.\n**Search diagnostics:** ok=0, reddit_ip_blocked=5\n', encoding='utf-8')
            (log_dir / 'marketing_a.json').write_text(json.dumps({"timestamp": "2026-05-22T22:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post A", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')
            (log_dir / 'marketing_b.json').write_text(json.dumps({"timestamp": "2026-05-23T01:00:00", "chosen_action": {"type": "owned_content_publication", "title": "Post B", "channel": "telegraph"}, "result": {"ok": True}}), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'CURATOR_QUEUE_LATEST_PATH', curator_queue_path), \
                 patch.object(distribution_lane_selector, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_STATUS_PATH', apollo_path), \
                 patch.object(distribution_lane_selector, 'APOLLO_SEQUENCE_STATUS_PATH', apollo_sequence_path), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', market_path), \
                 patch.object(distribution_lane_selector, 'STACKOVERFLOW_LATEST_PATH', so_path), \
                 patch.object(distribution_lane_selector, 'REDDIT_MONITOR_LATEST', seo_dir / 'reddit_monitor_latest.md'), \
                 patch.object(distribution_lane_selector, '_github_auth_available', return_value=False):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'stackoverflow_answer')
            self.assertIn('high-intent StackOverflow answers', decision.reason)


class DistributionLaneExecutorTests(unittest.TestCase):
    def test_directory_confirmation_execution_refreshes_backlink_snapshot(self):
        now = datetime(2026, 5, 23, 23, 40, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='directory_confirmation',
            reason='test',
            reasons=['test'],
            owned_content_posts_last_36h=0,
            unsubmitted_directory_channels=[],
            shared_findings_used=['agents/marketing/logs/backlink_status_latest.json'],
            artifact_path='/tmp/test.md',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            backlink_status_path = log_dir / 'backlink_status_latest.json'
            backlink_status_path.write_text(json.dumps({
                'generated_at': '2026-05-23T21:30:00+00:00',
                'directories': {
                    'ToolWise': {
                        'listing_url': 'https://toolwise.ai/tools/ralph-workflow',
                        'listing_live': True,
                        'status_note': 'Existing listing already live.'
                    },
                    'OpenAgents': {
                        'listing_url': 'https://www.openagents.pro/tools/ralph-workflow',
                        'listing_live': False,
                        'status_note': 'Pending review.',
                        'check_results': [{'status': 404, 'ok': False}],
                    },
                },
                'summary': {
                    'directories_with_live_listings': 1,
                    'queries_indexed': 0,
                    'total_queries': 14,
                },
            }), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'BACKLINK_STATUS_LATEST_PATH', backlink_status_path), \
                 patch.object(distribution_lane_executor.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='', stderr='')):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.action_type, 'directory_confirmation_execution')
            self.assertEqual(execution.status, 'executed')
            self.assertIn('live listing', execution.summary.lower())
            self.assertTrue((drafts_dir / '2026-05-23_directory_confirmation_execution.md').exists())

    def test_extract_contact_links_filters_github_asset_noise(self):
        html = '''
        <a href="https://www.linkedin.com/in/example-founder/">LinkedIn</a>
        <a href="https://example.com/contact">Contact</a>
        <a href="https://github.githubassets.com/assets/profile.css">noise</a>
        <a href="https://avatars.githubusercontent.com/u/123?v=4">avatar</a>
        <a href="mailto:founder@example.com">mail</a>
        '''

        channels = distribution_lane_executor._extract_contact_links(html)

        self.assertEqual(
            channels,
            [
                {'type': 'email', 'value': 'founder@example.com', 'label': 'email'},
                {'type': 'website', 'value': 'https://example.com/contact', 'label': 'contact page'},
                {'type': 'linkedin', 'value': 'https://www.linkedin.com/in/example-founder', 'label': 'LinkedIn'},
            ],
        )

    def test_apollo_execution_writes_managed_outbound_packet(self):
        now = datetime(2026, 5, 22, 21, 0, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='apollo_outreach',
            reason='Apollo is live while Reddit is blocked.',
            reasons=['Apollo is authenticated right now.'],
            owned_content_posts_last_36h=2,
            unsubmitted_directory_channels=[],
            shared_findings_used=['market_intelligence_latest.json'],
            artifact_path='drafts/brief.md',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            apollo_path = log_dir / 'apollo_status.json'
            curator_queue = log_dir / 'curator_outreach_queue_latest.json'
            comparison_queue = log_dir / 'comparison_backlink_queue_latest.json'
            adoption_path = log_dir / 'adoption_metrics_latest.json'
            apollo_path.write_text(json.dumps({'status': 'login_succeeded', 'final_url': 'https://app.apollo.io/#/home', 'notes': 'ready'}), encoding='utf-8')
            curator_queue.write_text(json.dumps({'targets': [{'target': 'Target A', 'artifact_path': '/tmp/a.md', 'status': 'prepared'}]}), encoding='utf-8')
            comparison_queue.write_text(json.dumps({'targets': [{'slug': 'claude-code', 'name': 'Claude Code', 'artifact_path': '/tmp/c.md', 'status': 'prepared'}]}), encoding='utf-8')
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'APOLLO_STATUS_PATH', apollo_path), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', curator_queue), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'SEO_REPORTS_DIR', tmp), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value={'comparison_pages': [{'slug': 'claude-code', 'name': 'Claude Code', 'path': '/comparisons/claude-code.md'}], 'competitors': {'claude-code': {'name': 'Claude Code', 'positioning': 'CLI for agentic coding', 'github_stars': 1}}}):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.action_type, 'apollo_outreach_execution')
            self.assertTrue((drafts_dir / 'apollo_outreach_packet_latest.md').exists())
            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('Apollo Outbound Execution Packet', artifact_text)
            self.assertIn('Codeberg repo', artifact_text)

    def test_stackoverflow_lane_does_not_claim_progress_when_no_drafts_created(self):
        now = datetime(2026, 5, 23, 14, 8, 31)
        decision = distribution_lane_selector.LaneDecision(
            lane='stackoverflow_answer',
            reason='Use higher-intent demand capture.',
            reasons=['Directory submissions are saturated.'],
            owned_content_posts_last_36h=1,
            unsubmitted_directory_channels=['aitools-inc'],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='drafts/brief.md',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            stackoverflow_latest = log_dir / 'stackoverflow_answer_lane_latest.json'
            adoption_path = log_dir / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')
            stackoverflow_latest.write_text(json.dumps({
                'drafts_created': 0,
                'top_questions': [{'title': 'Workflow reliability question', 'url': 'https://stackoverflow.com/q/1'}],
                'drafts': [],
                'total_questions_found': 1,
            }), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'STACKOVERFLOW_LATEST_PATH', stackoverflow_latest), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor.stackoverflow_answer_lane, 'main', return_value=0):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.status, 'skipped')
            self.assertEqual(execution.targets_prepared, [])
            self.assertIn('did not surface draft-worthy questions', execution.summary)
            self.assertEqual(execution.blocking_factors, ['No draft-worthy StackOverflow questions surfaced in this pass.'])

    def test_stackoverflow_handoff_packet_packages_existing_draft(self):
        now = datetime(2026, 5, 23, 14, 20, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='stackoverflow_answer_handoff_packet',
            reason='Advance the existing draft instead of rerunning the lane.',
            reasons=['A fresh StackOverflow answer draft already exists.'],
            owned_content_posts_last_36h=1,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='drafts/brief.md',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            logs_dir = tmp / 'logs'
            drafts_dir.mkdir()
            logs_dir.mkdir()
            so_dir = drafts_dir / 'stackoverflow'
            so_dir.mkdir()
            (so_dir / 'so_answer_2026-05-23_example.md').write_text('# StackOverflow Answer Draft\n', encoding='utf-8')

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', logs_dir), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value={}):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.action_type, 'stackoverflow_answer_handoff_packet')
            self.assertEqual(execution.status, 'prepared')
            self.assertTrue((drafts_dir / 'stackoverflow_answer_handoff_packet_latest.md').exists())
            self.assertIn('so_answer_2026-05-23_example.md', execution.targets_prepared)

    def test_latest_apollo_warning_ignores_older_zero_record_log_when_newer_verification_is_ready(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            log_dir.mkdir()

            older = log_dir / 'marketing_2026-05-22_apollo_curator_followup_list.json'
            older.write_text(json.dumps({
                'chosen_action': {'channel': 'apollo_outreach'},
                'result': {
                    'ok': True,
                    'live_external_action': True,
                    'outcome_ready': False,
                    'notes': ['The visible record count was 0 right after creation, so the import path likely needs a second-pass check before using this list for a sequence.'],
                    'evidence': [],
                },
            }), encoding='utf-8')

            newer = log_dir / 'marketing_2026-05-23_apollo_list_verification.json'
            newer.write_text(json.dumps({
                'chosen_action': {'channel': 'apollo_outreach'},
                'result': {
                    'ok': True,
                    'live_external_action': True,
                    'outcome_ready': True,
                    'notes': [],
                    'evidence': ["Apollo UI shows list 'Ralph Workflow — curator follow-up 2026-05-22' with 5 visible records."],
                },
            }), encoding='utf-8')

            older_ts = datetime(2026, 5, 22, 22, 17, 0).timestamp()
            newer_ts = datetime(2026, 5, 23, 0, 7, 0).timestamp()
            os.utime(older, (older_ts, older_ts))
            os.utime(newer, (newer_ts, newer_ts))

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir):
                self.assertIsNone(distribution_lane_executor._latest_apollo_execution_warning())

    def test_curator_parser_skips_measurement_only_rows(self):
        text = """### 1. Real target
- **URL:** https://example.com/1
- **Action:** Submit PR

### 2. Measurement only
- **URL:** https://example.com/2
- **Action:** Check indexing status — any live backlinks from these?
"""
        targets = distribution_lane_executor._parse_curator_targets(text)
        actionable = [t for t in targets if distribution_lane_executor._is_actionable_curator_target(t)]
        self.assertEqual([t['heading'] for t in actionable], ['1. Real target'])

    def test_latest_research_signals_falls_back_to_reddit_monitor_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            seo_dir = tmp / 'seo-reports'
            seo_dir.mkdir()
            (seo_dir / 'reddit_monitor_latest.md').write_text(
                'visible review packets\n'
                'staged autonomy\n'
                'seeing what the agent actually did\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'SEO_REPORTS_DIR', seo_dir):
                signals = distribution_lane_executor._latest_research_signals()

        self.assertIn('visible review packets', signals)
        self.assertIn('staged autonomy', signals)
        self.assertIn('seeing what the agent actually did', signals)

    def test_distribution_reset_parser_stops_at_next_heading(self):
        text = """1. **first-target**
   URL: https://example.com/1
   Why it fits: first reason.

### Discovery notes
- extra note
"""

        targets = distribution_lane_executor._parse_distribution_reset_targets(text)

        self.assertEqual(targets, [{
            'target': 'first-target',
            'url': 'https://example.com/1',
            'why_it_fits': 'first reason.',
        }])

    def test_curator_execution_builds_target_specific_artifact_and_log(self):
        now = datetime(2026, 5, 22, 7, 15, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='curator_outreach',
            reason='Owned content is saturated for now; switch to comparison-page and curator distribution prep.',
            reasons=['Primary Codeberg adoption is flat.'],
            owned_content_posts_last_36h=3,
            unsubmitted_directory_channels=[],
            shared_findings_used=['market_intelligence_latest.json: reusable competitor comparisons and positioning truths'],
            artifact_path='/tmp/brief.md',
        )
        market_intelligence = {
            'comparison_pages': [
                {'slug': 'claude-code', 'name': 'Claude Code', 'path': '/tmp/claude-code.md'},
                {'slug': 'cursor', 'name': 'Cursor', 'path': '/tmp/cursor.md'},
            ]
        }
        targets_md = """# Targets\n\n### 1. ai-for-developers/awesome-ai-coding-tools\n- **URL:** https://github.com/ai-for-developers/awesome-ai-coding-tools\n- **What it is:** Curated list\n- **Why it fits:** Ralph Workflow is an AI coding orchestration tool\n- **Action:** Submit PR adding Ralph Workflow entry\n- **Priority:** HIGH\n- **Entry format:** - [Ralph Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — composable loop framework and AI orchestrator for unattended coding runs\n"""

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            drafts_dir.mkdir()
            log_dir.mkdir()
            targets_path = tmp / 'curator_outreach_targets.md'
            targets_path.write_text(targets_md, encoding='utf-8')
            outreach_path = tmp / 'outreach-log.md'
            outreach_path.write_text('HN/Lobsters mentioned several times', encoding='utf-8')
            adoption_path = tmp / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')
            reset_queue_path = log_dir / 'distribution_reset_targets_latest.json'
            reset_queue_path.write_text(json.dumps({'targets': []}), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'SEO_REPORTS_DIR', tmp / 'seo-reports'), \
                 patch.object(distribution_lane_executor, 'TARGETS_PATH', targets_path), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue_path), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=market_intelligence):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.status, 'prepared')
            self.assertFalse(execution.live_external_action)
            self.assertIn('github_auth_missing_for_live_pr_submission', execution.blocking_factors)
            self.assertTrue(execution.artifact_path)
            text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('Shared findings reused', text)
            self.assertIn('Claude Code', text)
            self.assertIn('awesome-ai-coding-tools', text)
            self.assertIn('Ready target files', text)
            self.assertIn('Canonical manual execution packet', text)
            self.assertNotIn('Current demand phrases reused', text)
            self.assertIn('canonical curator handoff packet', execution.summary)
            action_log = log_dir / 'marketing_2026-05-22_curator_outreach_execution.json'
            self.assertTrue(action_log.exists())
            queue_log = log_dir / 'curator_outreach_queue_latest.json'
            self.assertTrue(queue_log.exists())
            queue_payload = json.loads(queue_log.read_text(encoding='utf-8'))
            self.assertEqual(queue_payload['targets'][0]['status'], 'prepared')
            self.assertTrue(Path(queue_payload['targets'][0]['artifact_path']).exists())
            self.assertTrue((drafts_dir / 'curator_handoff_packet_latest.md').exists())

    def test_curator_execution_reuses_reddit_monitor_signals_when_research_file_missing(self):
        now = datetime(2026, 5, 22, 7, 15, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='curator_outreach',
            reason='Owned content is saturated for now; switch to comparison-page and curator distribution prep.',
            reasons=['Primary Codeberg adoption is flat.'],
            owned_content_posts_last_36h=3,
            unsubmitted_directory_channels=[],
            shared_findings_used=['market_intelligence_latest.json: reusable competitor comparisons and positioning truths'],
            artifact_path='/tmp/brief.md',
        )
        market_intelligence = {'comparison_pages': []}
        targets_md = """# Targets

### 1. ai-for-developers/awesome-ai-coding-tools
- **URL:** https://github.com/ai-for-developers/awesome-ai-coding-tools
- **What it is:** Curated list
- **Why it fits:** Ralph Workflow is an AI coding orchestration tool
- **Action:** Submit PR adding Ralph Workflow entry
- **Priority:** HIGH
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            seo_dir = tmp / 'seo-reports'
            drafts_dir.mkdir()
            log_dir.mkdir()
            seo_dir.mkdir()
            (seo_dir / 'reddit_monitor_latest.md').write_text(
                'visible review packets\nseeing what the agent actually did\n',
                encoding='utf-8',
            )
            targets_path = tmp / 'curator_outreach_targets.md'
            targets_path.write_text(targets_md, encoding='utf-8')
            outreach_path = tmp / 'outreach-log.md'
            outreach_path.write_text('Curator outreach active.', encoding='utf-8')
            adoption_path = tmp / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'SEO_REPORTS_DIR', seo_dir), \
                 patch.object(distribution_lane_executor, 'TARGETS_PATH', targets_path), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', log_dir / 'curator_outreach_queue_latest.json'), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=market_intelligence):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('Current demand phrases reused', text)
            self.assertIn('visible review packets', text)
            self.assertIn('seeing what the agent actually did', text)
            self.assertIn('reddit_monitor_latest.md', text)

    def test_curator_execution_advances_to_unprepared_targets_instead_of_regenerating_same_queue(self):
        now = datetime(2026, 5, 23, 7, 15, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='curator_outreach',
            reason='Monitoring is not the move right now; switch to a Codeberg-primary curator/comparison distribution lane.',
            reasons=['Primary Codeberg adoption is flat.'],
            owned_content_posts_last_36h=3,
            unsubmitted_directory_channels=[],
            shared_findings_used=['market_intelligence_latest.json: reusable competitor comparisons and positioning truths'],
            artifact_path='/tmp/brief.md',
        )
        market_intelligence = {'comparison_pages': []}
        targets_md = """# Targets

### 1. first-target
- **URL:** https://example.com/1
- **Action:** Submit PR
- **Priority:** HIGH

### 2. second-target
- **URL:** https://example.com/2
- **Action:** Submit PR
- **Priority:** HIGH

### 3. third-target
- **URL:** https://example.com/3
- **Action:** Submit PR
- **Priority:** HIGH

### 4. fourth-target
- **URL:** https://example.com/4
- **Action:** Submit PR
- **Priority:** MEDIUM

### 5. fifth-target
- **URL:** https://example.com/5
- **Action:** Submit PR
- **Priority:** MEDIUM
"""
        existing_queue = {
            'generated_at': '2026-05-22T09:25:12',
            'targets': [
                {'target': '1. first-target', 'url': 'https://example.com/1', 'status': 'prepared', 'review_due_date': '2026-06-05', 'artifact_path': '/tmp/1.md'},
                {'target': '2. second-target', 'url': 'https://example.com/2', 'status': 'prepared', 'review_due_date': '2026-06-05', 'artifact_path': '/tmp/2.md'},
                {'target': '3. third-target', 'url': 'https://example.com/3', 'status': 'prepared', 'review_due_date': '2026-06-05', 'artifact_path': '/tmp/3.md'},
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            drafts_dir.mkdir()
            log_dir.mkdir()
            targets_path = tmp / 'curator_outreach_targets.md'
            targets_path.write_text(targets_md, encoding='utf-8')
            outreach_path = tmp / 'outreach-log.md'
            outreach_path.write_text('Curator outreach active.', encoding='utf-8')
            adoption_path = tmp / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')
            queue_path = log_dir / 'curator_outreach_queue_latest.json'
            queue_path.write_text(json.dumps(existing_queue), encoding='utf-8')
            reset_queue_path = log_dir / 'distribution_reset_targets_latest.json'
            reset_queue_path.write_text(json.dumps({'targets': []}), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'SEO_REPORTS_DIR', tmp / 'seo-reports'), \
                 patch.object(distribution_lane_executor, 'TARGETS_PATH', targets_path), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', queue_path), \
                 patch.object(distribution_lane_executor, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue_path), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=market_intelligence):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.status, 'prepared')
            queue_payload = json.loads(queue_path.read_text(encoding='utf-8'))
            queue_targets = [row['target'] for row in queue_payload['targets']]
            self.assertEqual(queue_targets[-2:], ['4. fourth-target', '5. fifth-target'])
            self.assertNotIn('1. first-target', execution.targets_prepared[-2:])
            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('01_4-fourth-target.md', artifact_text)
            self.assertIn('02_5-fifth-target.md', artifact_text)

    def test_curator_follow_through_reuses_research_signals_and_status_rules(self):
        now = datetime(2026, 5, 23, 7, 15, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='curator_outreach',
            reason='Monitoring is not the move right now; switch to a Codeberg-primary curator/comparison distribution lane.',
            reasons=['Primary Codeberg adoption is flat.'],
            owned_content_posts_last_36h=3,
            unsubmitted_directory_channels=[],
            shared_findings_used=['market_intelligence_latest.json: reusable competitor comparisons and positioning truths'],
            artifact_path='/tmp/brief.md',
        )
        targets_md = """# Targets

### 1. first-target
- **URL:** https://example.com/1
- **Action:** Submit PR
- **Priority:** HIGH
"""
        existing_queue = {
            'generated_at': '2026-05-22T09:25:12',
            'targets': [
                {'target': '1. first-target', 'url': 'https://example.com/1', 'status': 'sent_via_email_fallback', 'review_due_date': '2026-06-05', 'artifact_path': '/tmp/1.md'},
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            seo_dir = tmp / 'seo-reports'
            drafts_dir.mkdir()
            log_dir.mkdir()
            seo_dir.mkdir()

            (seo_dir / 'research_2026-05-22.md').write_text('Teams want to stop babysitting your agents and want finished code that is ready to review.', encoding='utf-8')
            targets_path = tmp / 'curator_outreach_targets.md'
            targets_path.write_text(targets_md, encoding='utf-8')
            outreach_path = tmp / 'outreach-log.md'
            outreach_path.write_text('Curator outreach active.', encoding='utf-8')
            adoption_path = tmp / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')
            queue_path = log_dir / 'curator_outreach_queue_latest.json'
            queue_path.write_text(json.dumps(existing_queue), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'SEO_REPORTS_DIR', seo_dir), \
                 patch.object(distribution_lane_executor, 'TARGETS_PATH', targets_path), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', queue_path), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value={'comparison_pages': []}):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('Demand signals to preserve in any outreach', artifact_text)
            self.assertIn('stop babysitting your agents', artifact_text)
            self.assertIn('sent_via_email_fallback: do not resend now', artifact_text)

    def test_comparison_backlink_execution_creates_fresh_asset_from_next_unprepared_market_intelligence_targets(self):
        now = datetime(2026, 5, 23, 7, 15, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='comparison_backlink_outreach',
            reason='Curator queue prep is already full; ship a fresh comparison/backlink outreach asset instead of another follow-through note.',
            reasons=['Primary Codeberg adoption is flat.'],
            owned_content_posts_last_36h=3,
            unsubmitted_directory_channels=[],
            shared_findings_used=['market_intelligence_latest.json: reusable competitor comparisons and positioning truths'],
            artifact_path='/tmp/brief.md',
        )
        market_intelligence = {
            'comparison_pages': [
                {'slug': 'hermes-agent', 'name': 'Hermes Agent', 'path': '/tmp/hermes-agent.md'},
                {'slug': 'aider', 'name': 'Aider', 'path': '/tmp/aider.md'},
                {'slug': 'continue', 'name': 'Continue', 'path': '/tmp/continue.md'},
                {'slug': 'conductor-oss', 'name': 'Conductor OSS', 'path': '/tmp/conductor-oss.md'},
            ],
            'competitors': {
                'hermes-agent': {'name': 'Hermes Agent', 'positioning': 'Self-improving agent', 'github_stars': 100},
                'aider': {'name': 'Aider', 'positioning': 'CLI pair programmer', 'github_stars': 95},
                'continue': {'name': 'Continue', 'positioning': 'IDE assistant', 'github_stars': 90},
                'conductor-oss': {'name': 'Conductor OSS', 'positioning': 'Enterprise workflow orchestration', 'github_stars': 85},
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            seo_dir = tmp / 'seo-reports'
            drafts_dir.mkdir()
            log_dir.mkdir()
            seo_dir.mkdir()
            (seo_dir / 'research_2026-05-22.md').write_text('Teams want run until done and finished code that is ready to review.', encoding='utf-8')
            outreach_path = tmp / 'outreach-log.md'
            outreach_path.write_text('Curator queue saturated.', encoding='utf-8')
            adoption_path = tmp / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')
            comparison_queue = log_dir / 'comparison_backlink_queue_latest.json'
            comparison_queue.write_text(json.dumps({'targets': [
                {'slug': 'hermes-agent', 'name': 'Hermes Agent', 'status': 'prepared'},
                {'slug': 'aider', 'name': 'Aider', 'status': 'prepared'},
                {'slug': 'continue', 'name': 'Continue', 'status': 'prepared'},
            ]}), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'SEO_REPORTS_DIR', seo_dir), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch('subprocess.run', return_value=SimpleNamespace(returncode=1)), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=market_intelligence):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.status, 'prepared')
            self.assertEqual(execution.action_type, 'comparison_backlink_outreach_execution')
            self.assertEqual(execution.targets_prepared, ['Conductor OSS'])
            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('Comparison Backlink Outreach Pack', artifact_text)
            self.assertIn('Conductor OSS', artifact_text)
            self.assertIn('Canonical manual execution packet', artifact_text)
            self.assertIn('canonical comparison handoff packet', execution.summary)
            self.assertTrue(comparison_queue.exists())
            self.assertTrue((drafts_dir / 'comparison_backlink_handoff_packet_latest.md').exists())

    def test_comparison_backlink_execution_marks_follow_through_when_queue_is_exhausted(self):
        now = datetime(2026, 5, 23, 7, 15, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='comparison_backlink_outreach',
            reason='Curator queue prep is already full; ship a fresh comparison/backlink outreach asset instead of another follow-through note.',
            reasons=['Primary Codeberg adoption is flat.'],
            owned_content_posts_last_36h=3,
            unsubmitted_directory_channels=[],
            shared_findings_used=['market_intelligence_latest.json: reusable competitor comparisons and positioning truths'],
            artifact_path='/tmp/brief.md',
        )
        market_intelligence = {
            'comparison_pages': [
                {'slug': 'hermes-agent', 'name': 'Hermes Agent', 'path': '/tmp/hermes-agent.md'},
            ],
            'competitors': {
                'hermes-agent': {'name': 'Hermes Agent', 'positioning': 'Self-improving agent', 'github_stars': 100},
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            seo_dir = tmp / 'seo-reports'
            drafts_dir.mkdir()
            log_dir.mkdir()
            seo_dir.mkdir()
            outreach_path = tmp / 'outreach-log.md'
            outreach_path.write_text('Curator queue saturated.', encoding='utf-8')
            adoption_path = tmp / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')
            comparison_queue = log_dir / 'comparison_backlink_queue_latest.json'
            comparison_queue.write_text(json.dumps({'targets': [
                {'slug': 'hermes-agent', 'name': 'Hermes Agent', 'status': 'prepared', 'artifact_path': '/tmp/hermes-agent.md', 'review_due_date': '2026-06-05'},
            ]}), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'SEO_REPORTS_DIR', seo_dir), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=market_intelligence):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.action_type, 'comparison_backlink_follow_through')
            self.assertEqual(execution.targets_prepared, [])
            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('Comparison Backlink Follow-Through', artifact_text)
            self.assertIn('already covers every ranked competitor', artifact_text)

    def test_curator_handoff_packet_also_points_to_comparison_handoff_when_comparison_queue_exists(self):
        now = datetime(2026, 5, 23, 7, 15, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='curator_handoff_packet',
            reason='Prepared outreach targets already exist but GitHub auth is blocked here; refresh the canonical manual execution packet instead of discovering more targets.',
            reasons=['Primary Codeberg adoption is flat.'],
            owned_content_posts_last_36h=3,
            unsubmitted_directory_channels=[],
            shared_findings_used=['market_intelligence_latest.json: reusable competitor comparisons and positioning truths'],
            artifact_path='/tmp/brief.md',
        )
        market_intelligence = {
            'comparison_pages': [
                {'slug': 'hermes-agent', 'name': 'Hermes Agent', 'path': '/tmp/hermes-agent.md'},
            ],
            'competitors': {
                'hermes-agent': {'name': 'Hermes Agent', 'positioning': 'Self-improving agent', 'github_stars': 100},
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            drafts_dir.mkdir()
            log_dir.mkdir()
            outreach_path = tmp / 'outreach-log.md'
            outreach_path.write_text('Curator queue saturated.', encoding='utf-8')
            adoption_path = tmp / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')
            curator_queue = log_dir / 'curator_outreach_queue_latest.json'
            curator_queue.write_text(json.dumps({'targets': [
                {'target': '1. Example Curator', 'url': 'https://github.com/example/awesome', 'status': 'prepared', 'artifact_path': '/tmp/curator.md', 'review_due_date': '2026-06-05'},
            ]}), encoding='utf-8')
            comparison_queue = log_dir / 'comparison_backlink_queue_latest.json'
            comparison_queue.write_text(json.dumps({'targets': [
                {'slug': 'hermes-agent', 'name': 'Hermes Agent', 'status': 'prepared', 'artifact_path': '/tmp/hermes-agent.md', 'comparison_path': '/tmp/hermes-agent.md', 'review_due_date': '2026-06-05'},
            ]}), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', curator_queue), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue), \
                 patch('subprocess.run', return_value=SimpleNamespace(returncode=1)), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=market_intelligence):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('Comparison backlink execution packet', artifact_text)
            self.assertIn('comparison backlink handoff packet', execution.summary)
            self.assertTrue((drafts_dir / 'comparison_backlink_handoff_packet_latest.md').exists())

    def test_curator_handoff_packet_falls_back_to_follow_through_when_latest_packet_is_already_current(self):
        now = datetime(2026, 5, 23, 8, 0, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='curator_handoff_packet',
            reason='Prepared outreach targets already exist but GitHub auth is blocked here; refresh the canonical manual execution packet instead of discovering more targets.',
            reasons=['Primary Codeberg adoption is flat.'],
            owned_content_posts_last_36h=3,
            unsubmitted_directory_channels=[],
            shared_findings_used=['market_intelligence_latest.json: reusable competitor comparisons and positioning truths'],
            artifact_path='/tmp/brief.md',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            drafts_dir.mkdir()
            log_dir.mkdir()
            outreach_path = tmp / 'outreach-log.md'
            outreach_path.write_text('Curator queue saturated.', encoding='utf-8')
            adoption_path = tmp / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')
            curator_queue = log_dir / 'curator_outreach_queue_latest.json'
            curator_queue.write_text(json.dumps({'targets': [
                {'target': '1. Example Curator', 'url': 'https://github.com/example/awesome', 'status': 'prepared', 'artifact_path': '/tmp/curator.md', 'review_due_date': '2026-06-05'},
            ]}), encoding='utf-8')
            comparison_queue = log_dir / 'comparison_backlink_queue_latest.json'
            comparison_queue.write_text(json.dumps({'targets': []}), encoding='utf-8')
            (drafts_dir / 'curator_handoff_packet_latest.md').write_text(
                '# Ralph Workflow Curator Execution Handoff Packet\n\n### 1. Example Curator\n- Ready file: /tmp/curator.md\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', curator_queue), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue), \
                 patch('subprocess.run', return_value=SimpleNamespace(returncode=0)), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value={'comparison_pages': [], 'competitors': {}}):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.action_type, 'curator_handoff_follow_through')
            self.assertEqual(execution.targets_prepared, [])
            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('existing manual packet is still current', artifact_text)
            self.assertIn('curator_handoff_packet_latest.md', artifact_text)

    def test_curator_handoff_packet_uses_contact_discovery_when_packet_is_current_and_github_auth_is_missing(self):
        now = datetime(2026, 5, 23, 8, 0, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='curator_handoff_packet',
            reason='Prepared outreach targets already exist but GitHub auth is blocked here; refresh the canonical manual execution packet instead of discovering more targets.',
            reasons=['Primary Codeberg adoption is flat.'],
            owned_content_posts_last_36h=3,
            unsubmitted_directory_channels=[],
            shared_findings_used=['market_intelligence_latest.json: reusable competitor comparisons and positioning truths'],
            artifact_path='/tmp/brief.md',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            drafts_dir.mkdir()
            log_dir.mkdir()
            outreach_path = tmp / 'outreach-log.md'
            outreach_path.write_text('Curator queue saturated.', encoding='utf-8')
            adoption_path = tmp / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')
            curator_queue = log_dir / 'curator_outreach_queue_latest.json'
            curator_queue.write_text(json.dumps({'targets': [
                {
                    'target': '1. Example Curator',
                    'url': 'https://github.com/example/awesome',
                    'status': 'prepared',
                    'priority': 'HIGH — example',
                    'artifact_path': '/tmp/curator.md',
                    'review_due_date': '2026-06-05',
                },
            ]}), encoding='utf-8')
            comparison_queue = log_dir / 'comparison_backlink_queue_latest.json'
            comparison_queue.write_text(json.dumps({'targets': []}), encoding='utf-8')
            curator_contact_discovery = log_dir / 'curator_contact_discovery_latest.json'
            (drafts_dir / 'curator_handoff_packet_latest.md').write_text(
                '# Ralph Workflow Curator Execution Handoff Packet\n\n### 1. Example Curator\n- Ready file: /tmp/curator.md\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', curator_queue), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue), \
                 patch.object(distribution_lane_executor, 'CURATOR_CONTACT_DISCOVERY_LATEST_PATH', curator_contact_discovery), \
                 patch('subprocess.run', return_value=SimpleNamespace(returncode=1)), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value={'comparison_pages': [], 'competitors': {}}), \
                 patch.object(distribution_lane_executor, '_discover_curator_channels', return_value=[{
                     'target': '1. Example Curator',
                     'url': 'https://github.com/example/awesome',
                     'channels': [{'type': 'website', 'value': 'https://example.com/contact', 'label': 'profile contact page'}],
                     'recommended_next_step': 'manual contact channel is now identified',
                     'artifact_path': '/tmp/curator.md',
                 }]):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.action_type, 'curator_contact_discovery_execution')
            self.assertEqual(execution.targets_prepared, ['1. Example Curator'])
            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('contact-channel discovery', artifact_text)
            self.assertIn('https://example.com/contact', artifact_text)

    def test_curator_handoff_packet_escalates_current_contact_discovery_into_contact_packet(self):
        now = datetime(2026, 5, 23, 8, 30, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='curator_handoff_packet',
            reason='Prepared outreach targets already exist but GitHub auth is blocked here; refresh the canonical manual execution packet instead of discovering more targets.',
            reasons=['Primary Codeberg adoption is flat.'],
            owned_content_posts_last_36h=3,
            unsubmitted_directory_channels=[],
            shared_findings_used=['market_intelligence_latest.json: reusable competitor comparisons and positioning truths'],
            artifact_path='/tmp/brief.md',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            drafts_dir.mkdir()
            log_dir.mkdir()
            outreach_path = tmp / 'outreach-log.md'
            outreach_path.write_text('Curator queue saturated.', encoding='utf-8')
            adoption_path = tmp / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')
            curator_queue = log_dir / 'curator_outreach_queue_latest.json'
            curator_queue.write_text(json.dumps({'targets': [
                {
                    'target': '1. Example Curator',
                    'url': 'https://github.com/example/awesome',
                    'status': 'prepared',
                    'priority': 'HIGH — example',
                    'artifact_path': '/tmp/curator.md',
                    'review_due_date': '2026-06-05',
                },
            ]}), encoding='utf-8')
            comparison_queue = log_dir / 'comparison_backlink_queue_latest.json'
            comparison_queue.write_text(json.dumps({'targets': []}), encoding='utf-8')
            curator_contact_discovery = log_dir / 'curator_contact_discovery_latest.json'
            curator_contact_discovery.write_text(json.dumps({
                'generated_at': now.isoformat(),
                'targets': [{
                    'target': '1. Example Curator',
                    'url': 'https://github.com/example/awesome',
                    'channels': [{'type': 'website', 'value': 'https://example.com/contact', 'label': 'profile contact page'}],
                    'recommended_next_step': 'manual contact channel is now identified',
                    'artifact_path': '/tmp/curator.md',
                }],
            }), encoding='utf-8')
            (drafts_dir / 'curator_handoff_packet_latest.md').write_text(
                '# Ralph Workflow Curator Execution Handoff Packet\n\n### 1. Example Curator\n- Ready file: /tmp/curator.md\n',
                encoding='utf-8',
            )

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', curator_queue), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue), \
                 patch.object(distribution_lane_executor, 'CURATOR_CONTACT_DISCOVERY_LATEST_PATH', curator_contact_discovery), \
                 patch('subprocess.run', return_value=SimpleNamespace(returncode=1)), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value={'comparison_pages': [], 'competitors': {}}):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.action_type, 'curator_contact_handoff_packet_execution')
            self.assertEqual(execution.targets_prepared, ['1. Example Curator'])
            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('canonical human-executable contact list', artifact_text)
            self.assertIn('https://example.com/contact', artifact_text)

    def test_curator_contact_handoff_lane_uses_contact_packet_directly(self):
        now = datetime(2026, 5, 23, 8, 30, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='curator_contact_handoff_packet',
            reason='Prepared curator targets already have non-GitHub contact channels; advance the manual-contact execution packet instead of another generic handoff refresh.',
            reasons=['Primary Codeberg adoption is flat.'],
            owned_content_posts_last_36h=3,
            unsubmitted_directory_channels=[],
            shared_findings_used=['market_intelligence_latest.json: reusable competitor comparisons and positioning truths'],
            artifact_path='/tmp/brief.md',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            drafts_dir.mkdir()
            log_dir.mkdir()
            outreach_path = tmp / 'outreach-log.md'
            outreach_path.write_text('Curator queue saturated.', encoding='utf-8')
            adoption_path = tmp / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')
            curator_queue = log_dir / 'curator_outreach_queue_latest.json'
            curator_queue.write_text(json.dumps({'targets': [
                {
                    'target': '1. Example Curator',
                    'url': 'https://github.com/example/awesome',
                    'status': 'prepared',
                    'priority': 'HIGH — example',
                    'artifact_path': '/tmp/curator.md',
                    'review_due_date': '2026-06-05',
                },
            ]}), encoding='utf-8')
            comparison_queue = log_dir / 'comparison_backlink_queue_latest.json'
            comparison_queue.write_text(json.dumps({'targets': []}), encoding='utf-8')
            curator_contact_discovery = log_dir / 'curator_contact_discovery_latest.json'
            curator_contact_discovery.write_text(json.dumps({
                'generated_at': now.isoformat(),
                'targets': [{
                    'target': '1. Example Curator',
                    'url': 'https://github.com/example/awesome',
                    'channels': [{'type': 'website', 'value': 'https://example.com/contact', 'label': 'profile contact page'}],
                    'recommended_next_step': 'manual contact channel is now identified',
                    'artifact_path': '/tmp/curator.md',
                }],
            }), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', curator_queue), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue), \
                 patch.object(distribution_lane_executor, 'CURATOR_CONTACT_DISCOVERY_LATEST_PATH', curator_contact_discovery), \
                 patch('subprocess.run', return_value=SimpleNamespace(returncode=1)), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value={'comparison_pages': [], 'competitors': {}}):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.action_type, 'curator_contact_handoff_packet_execution')
            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('canonical human-executable contact list', artifact_text)

    def test_display_target_name_preserves_repo_names_starting_with_digits(self):
        self.assertEqual(
            distribution_lane_executor._display_target_name('0xWelt/Awesome-Vibe-Coding'),
            '0xWelt/Awesome-Vibe-Coding',
        )
        self.assertEqual(
            distribution_lane_executor._display_target_name('7. Example Curator'),
            'Example Curator',
        )

    def test_contact_discovery_filters_noisy_links_and_prioritizes_actionable_channels(self):
        channels = distribution_lane_executor._extract_contact_links(' '.join([
            'https://github-readme-stats.vercel.app/api?username=test',
            'https://orcid.org/0000-0000-0000-0000',
            'https://example.com/contact',
            'https://example.com/blog/post',
            'https://www.linkedin.com/in/example',
            'https://x.com/example',
        ]))
        self.assertEqual(
            channels,
            [
                {'type': 'website', 'value': 'https://example.com/contact', 'label': 'contact page'},
                {'type': 'linkedin', 'value': 'https://www.linkedin.com/in/example', 'label': 'LinkedIn'},
                {'type': 'x', 'value': 'https://x.com/example', 'label': 'X/Twitter'},
            ],
        )

        prioritized = distribution_lane_executor._prioritize_contact_channels([
            {'type': 'website', 'value': 'https://example.com', 'label': 'profile website'},
            {'type': 'website', 'value': 'https://example.com/contact', 'label': 'possible contact page'},
            {'type': 'website', 'value': 'https://example.com/about', 'label': 'about page'},
            {'type': 'x', 'value': 'https://x.com/example', 'label': 'X/Twitter'},
            {'type': 'linkedin', 'value': 'https://linkedin.com/in/example', 'label': 'LinkedIn'},
        ])
        self.assertEqual(
            prioritized,
            [
                {'type': 'x', 'value': 'https://x.com/example', 'label': 'X/Twitter'},
                {'type': 'linkedin', 'value': 'https://linkedin.com/in/example', 'label': 'LinkedIn'},
                {'type': 'website', 'value': 'https://example.com/about', 'label': 'about page'},
                {'type': 'website', 'value': 'https://example.com/contact', 'label': 'possible contact page'},
            ],
        )

    def test_distribution_reset_execution_uses_fresh_discovered_targets_instead_of_relogging_comparison_assets(self):
        now = datetime(2026, 5, 23, 7, 15, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='distribution_reset',
            reason='Curator and comparison queues are both saturated; ship a new queue-reset/discovery packet instead of pretending a fresh outreach asset exists.',
            reasons=['Primary Codeberg adoption is flat.'],
            owned_content_posts_last_36h=3,
            unsubmitted_directory_channels=[],
            shared_findings_used=['market_intelligence_latest.json: reusable competitor comparisons and positioning truths'],
            artifact_path='/tmp/brief.md',
        )
        market_intelligence = {
            'comparison_pages': [
                {'slug': 'hermes-agent', 'name': 'Hermes Agent', 'path': '/tmp/hermes-agent.md'},
                {'slug': 'claude-code', 'name': 'Claude Code', 'path': '/tmp/claude-code.md'},
            ]
        }
        reset_log = """# Distribution Reset Execution Log

1. **Agent-Analytics/awesome-multi-agent-orchestrators**
   URL: https://github.com/Agent-Analytics/awesome-multi-agent-orchestrators
   Why it fits: explicitly curates multi-agent orchestrators.

2. **hesreallyhim/awesome-claude-code**
   URL: https://github.com/hesreallyhim/awesome-claude-code
   Why it fits: Claude Code ecosystem roundup.
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            drafts_dir.mkdir()
            log_dir.mkdir()
            outreach_path = tmp / 'outreach-log.md'
            outreach_path.write_text('HN/Lobsters blocker noted.', encoding='utf-8')
            adoption_path = tmp / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')
            curator_queue = log_dir / 'curator_outreach_queue_latest.json'
            curator_queue.write_text(json.dumps({'targets': []}), encoding='utf-8')
            comparison_queue = log_dir / 'comparison_backlink_queue_latest.json'
            comparison_queue.write_text(json.dumps({'targets': [
                {'slug': 'awesome-claude-code', 'name': 'hesreallyhim/awesome-claude-code', 'url': 'https://github.com/hesreallyhim/awesome-claude-code', 'status': 'prepared'},
            ]}), encoding='utf-8')
            reset_log_path = log_dir / 'distribution_reset_execution_log.md'
            reset_log_path.write_text(reset_log, encoding='utf-8')
            reset_queue_path = log_dir / 'distribution_reset_targets_latest.json'

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'SEO_REPORTS_DIR', tmp / 'seo-reports'), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', curator_queue), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue), \
                 patch.object(distribution_lane_executor, 'DISTRIBUTION_RESET_LOG_PATH', reset_log_path), \
                 patch.object(distribution_lane_executor, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue_path), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=market_intelligence):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.action_type, 'distribution_reset_execution')
            self.assertEqual(execution.targets_prepared, ['Agent-Analytics/awesome-multi-agent-orchestrators'])
            queue_payload = json.loads(reset_queue_path.read_text(encoding='utf-8'))
            self.assertEqual([row['target'] for row in queue_payload['targets']], ['Agent-Analytics/awesome-multi-agent-orchestrators'])
            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('Fresh targets discovered in this reset', artifact_text)
            self.assertIn('Agent-Analytics/awesome-multi-agent-orchestrators', artifact_text)
            self.assertNotIn('hesreallyhim/awesome-claude-code', execution.targets_prepared)

    def test_curator_execution_promotes_distribution_reset_log_targets_when_queue_file_is_empty(self):
        now = datetime(2026, 5, 23, 7, 15, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='curator_outreach',
            reason='Fresh reset targets exist; promote them into real outreach assets before logging another reset or queue-housekeeping cycle.',
            reasons=['Primary Codeberg adoption is flat.'],
            owned_content_posts_last_36h=3,
            unsubmitted_directory_channels=[],
            shared_findings_used=['market_intelligence_latest.json: reusable competitor comparisons and positioning truths'],
            artifact_path='/tmp/brief.md',
        )
        market_intelligence = {
            'comparison_pages': [
                {'slug': 'claude-code', 'name': 'Claude Code', 'path': '/tmp/claude-code.md'},
            ]
        }
        curator_targets = """### 1. Static target
- **URL:** https://example.com/static
- **Action:** Submit PR
- **Priority:** HIGH
"""
        reset_log = """# Distribution Reset Execution Log

1. **Agent-Analytics/awesome-multi-agent-orchestrators**
   URL: https://github.com/Agent-Analytics/awesome-multi-agent-orchestrators
   Why it fits: explicitly curates multi-agent orchestrators.
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            seo_dir = tmp / 'seo-reports'
            drafts_dir.mkdir()
            log_dir.mkdir()
            seo_dir.mkdir()
            outreach_path = tmp / 'outreach-log.md'
            outreach_path.write_text('HN/Lobsters blocker noted.', encoding='utf-8')
            adoption_path = tmp / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')
            curator_queue = log_dir / 'curator_outreach_queue_latest.json'
            curator_queue.write_text(json.dumps({'targets': []}), encoding='utf-8')
            comparison_queue = log_dir / 'comparison_backlink_queue_latest.json'
            comparison_queue.write_text(json.dumps({'targets': []}), encoding='utf-8')
            target_path = tmp / 'curator_targets.md'
            target_path.write_text(curator_targets, encoding='utf-8')
            reset_queue = log_dir / 'distribution_reset_targets_latest.json'
            reset_queue.write_text(json.dumps({'targets': []}), encoding='utf-8')
            reset_log_path = log_dir / 'distribution_reset_execution_log.md'
            reset_log_path.write_text(reset_log, encoding='utf-8')

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'SEO_REPORTS_DIR', seo_dir), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', curator_queue), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue), \
                 patch.object(distribution_lane_executor, 'TARGETS_PATH', target_path), \
                 patch.object(distribution_lane_executor, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue), \
                 patch.object(distribution_lane_executor, 'DISTRIBUTION_RESET_LOG_PATH', reset_log_path), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=market_intelligence), \
                 patch('subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='', stderr='')):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.action_type, 'curator_outreach_execution')
            self.assertIn('Agent-Analytics/awesome-multi-agent-orchestrators', execution.targets_prepared)
            queue_payload = json.loads(curator_queue.read_text(encoding='utf-8'))
            self.assertEqual(queue_payload['targets'][0]['target'], 'Agent-Analytics/awesome-multi-agent-orchestrators')
            reset_payload = json.loads(reset_queue.read_text(encoding='utf-8'))
            self.assertEqual(reset_payload['targets'], [])

    def test_curator_execution_promotes_distribution_reset_targets_before_static_queue(self):
        now = datetime(2026, 5, 23, 7, 15, 0)
        decision = distribution_lane_selector.LaneDecision(
            lane='curator_outreach',
            reason='Fresh reset targets exist; promote them into real outreach assets before logging another reset or queue-housekeeping cycle.',
            reasons=['Primary Codeberg adoption is flat.'],
            owned_content_posts_last_36h=3,
            unsubmitted_directory_channels=[],
            shared_findings_used=['market_intelligence_latest.json: reusable competitor comparisons and positioning truths'],
            artifact_path='/tmp/brief.md',
        )
        market_intelligence = {
            'comparison_pages': [
                {'slug': 'claude-code', 'name': 'Claude Code', 'path': '/tmp/claude-code.md'},
            ]
        }
        curator_targets = """### 1. Static target
- **URL:** https://example.com/static
- **Action:** Submit PR
- **Priority:** HIGH
"""

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            seo_dir = tmp / 'seo-reports'
            drafts_dir.mkdir()
            log_dir.mkdir()
            seo_dir.mkdir()
            outreach_path = tmp / 'outreach-log.md'
            outreach_path.write_text('HN/Lobsters blocker noted.', encoding='utf-8')
            adoption_path = tmp / 'adoption_metrics_latest.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')
            curator_queue = log_dir / 'curator_outreach_queue_latest.json'
            curator_queue.write_text(json.dumps({'targets': []}), encoding='utf-8')
            comparison_queue = log_dir / 'comparison_backlink_queue_latest.json'
            comparison_queue.write_text(json.dumps({'targets': []}), encoding='utf-8')
            reset_queue = log_dir / 'distribution_reset_targets_latest.json'
            reset_queue.write_text(json.dumps({'targets': [
                {
                    'target': 'Agent-Analytics/awesome-multi-agent-orchestrators',
                    'url': 'https://github.com/Agent-Analytics/awesome-multi-agent-orchestrators',
                    'why_it_fits': 'explicitly curates multi-agent orchestrators.',
                    'status': 'discovered',
                }
            ]}), encoding='utf-8')
            target_path = log_dir / 'curator_outreach_targets.md'
            target_path.write_text(curator_targets, encoding='utf-8')
            (seo_dir / 'reddit_monitor_latest.md').write_text('visible review packets\n', encoding='utf-8')

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'SEO_REPORTS_DIR', seo_dir), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', curator_queue), \
                 patch.object(distribution_lane_executor, 'COMPARISON_QUEUE_LATEST_PATH', comparison_queue), \
                 patch.object(distribution_lane_executor, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue), \
                 patch.object(distribution_lane_executor, 'TARGETS_PATH', target_path), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=market_intelligence):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.action_type, 'curator_outreach_execution')
            self.assertEqual(execution.targets_prepared, ['Agent-Analytics/awesome-multi-agent-orchestrators'])
            queue_payload = json.loads(curator_queue.read_text(encoding='utf-8'))
            self.assertEqual(queue_payload['targets'][0]['target'], 'Agent-Analytics/awesome-multi-agent-orchestrators')
            reset_payload = json.loads(reset_queue.read_text(encoding='utf-8'))
            self.assertEqual(reset_payload['targets'][0]['status'], 'promoted_to_curator_queue')
            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('Reset targets activated in this run', artifact_text)

    def test_executor_load_distribution_reset_queue_rows_retires_live_executed_targets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            log_dir.mkdir()
            reset_queue = log_dir / 'distribution_reset_targets_latest.json'
            reset_queue.write_text(json.dumps({
                'targets': [
                    {
                        'target': 'AI IDE',
                        'url': 'https://aiide.dev/',
                        'status': 'discovered',
                    }
                ]
            }), encoding='utf-8')
            (log_dir / 'marketing_2026-05-24_aiide_distribution_action.json').write_text(json.dumps({
                'timestamp': '2026-05-24T02:06:00+02:00',
                'chosen_action': {'type': 'fresh_curator_outreach'},
                'result': {
                    'status': 'sent',
                    'ok': True,
                    'live_external_action': True,
                    'recipient': 'support@aiide.dev',
                },
            }), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'DISTRIBUTION_RESET_QUEUE_LATEST_PATH', reset_queue):
                rows = distribution_lane_executor._load_distribution_reset_queue_rows()

            self.assertEqual(rows[0]['status'], 'executed_elsewhere')
            reconciled = json.loads(reset_queue.read_text(encoding='utf-8'))
            self.assertEqual(reconciled['targets'][0]['status'], 'executed_elsewhere')

    def test_curator_queue_dedupes_same_url_even_if_heading_changes(self):
        rows = [
            {'target': '6. GitHub Topics: AI agents', 'url': 'https://github.com/topics/ai-agents', 'status': 'prepared'},
            {'target': '9. GitHub Topics: AI agents', 'url': 'https://github.com/topics/ai-agents', 'status': 'prepared'},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_path = Path(tmpdir) / 'queue.json'
            queue_path.write_text(json.dumps({'targets': rows}), encoding='utf-8')
            with patch.object(distribution_lane_executor, 'CURATOR_QUEUE_LATEST_PATH', queue_path):
                loaded = distribution_lane_executor._load_curator_queue_rows()

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]['target'], '6. GitHub Topics: AI agents')

    def test_stackoverflow_execution_uses_latest_draft_log(self):
        decision = distribution_lane_selector.LaneDecision(
            lane='stackoverflow_answer',
            reason='Need a fresh high-intent demand lane.',
            reasons=['Codeberg adoption is flat.'],
            owned_content_posts_last_36h=2,
            unsubmitted_directory_channels=[],
            shared_findings_used=['adoption_metrics_latest.json'],
            artifact_path='/tmp/brief.md',
        )
        now = datetime(2026, 5, 23, 8, 15, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / 'drafts'
            log_dir = tmp / 'logs'
            drafts_dir.mkdir()
            log_dir.mkdir()
            so_log = log_dir / 'stackoverflow_answer_lane_latest.json'
            so_log.write_text(json.dumps({
                'total_questions_found': 4,
                'drafts_created': 2,
                'drafts': [
                    {'question_title': 'How should I structure autonomous AI agent workflows for production reliability?', 'draft_file': '/tmp/so1.md'},
                    {'question_title': 'VS Code Copilot Agent/Chat extension cannot see terminal command output', 'draft_file': '/tmp/so2.md'},
                ],
            }), encoding='utf-8')
            adoption_path = log_dir / 'adoption.json'
            adoption_path.write_text(json.dumps({'recent_window': {'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0}}}), encoding='utf-8')

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'STACKOVERFLOW_LATEST_PATH', so_log), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value={}), \
                 patch.object(distribution_lane_executor.stackoverflow_answer_lane, 'main', return_value=0):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.action_type, 'stackoverflow_answer_execution')
            self.assertEqual(len(execution.targets_prepared), 2)
            artifact_text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('Ready answer drafts', artifact_text)
            self.assertIn('production reliability', artifact_text)


class MarketingLoopRunnerTests(unittest.TestCase):
    def test_runner_executes_outcome_engine_before_reporting_scripts(self):
        names = [path.name for path in marketing_loop_runner.SCRIPTS]
        self.assertEqual(names[0], 'run.py')
        self.assertIn('marketing_workflow_audit.py', names)
        self.assertIn('marketing_momentum_watchdog.py', names)
        self.assertIn('marketing_loop_independent_verify.py', names)
        self.assertLess(names.index('marketing_momentum_watchdog.py'), names.index('marketing_loop_independent_verify.py'))
        self.assertEqual(names[-1], 'marketing_loop_verifier.py')

    def test_runner_keeps_operational_ok_true_when_only_certification_scripts_fail(self):
        script_results = {
            script.name: SimpleNamespace(returncode=0, stdout='{}', stderr='')
            for script in marketing_loop_runner.SCRIPTS
        }
        script_results['marketing_loop_independent_verify.py'] = SimpleNamespace(returncode=1, stdout='{"verdict":"fail"}', stderr='')
        script_results['marketing_loop_verifier.py'] = SimpleNamespace(returncode=1, stdout='{"ok":false}', stderr='')

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / 'runner.json'

            def fake_run(cmd, capture_output=True, text=True):
                return script_results[Path(cmd[-1]).name]

            with patch.object(marketing_loop_runner, 'OUT', out), \
                 patch.object(marketing_loop_runner.subprocess, 'run', side_effect=fake_run):
                rc = marketing_loop_runner.main()

            self.assertEqual(rc, 1)
            payload = json.loads(out.read_text(encoding='utf-8'))
            self.assertTrue(payload['ok'])
            self.assertTrue(payload['operational_ok'])
            self.assertFalse(payload['certification_ok'])


class MarketingMomentumWatchdogTests(unittest.TestCase):
    def _run_watchdog_with_action(self, action_type: str, live_external_action: bool = False, runner_payload: dict | None = None) -> tuple[int, dict]:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            status_dir = tmp / 'logs'
            seo_dir = tmp / 'seo-reports'
            status_dir.mkdir()
            seo_dir.mkdir()
            adoption_path = status_dir / 'adoption_metrics_latest.json'
            audit_path = status_dir / 'marketing_workflow_audit_latest.json'
            status_path = status_dir / 'marketing_momentum_watchdog.json'
            apollo_status = status_dir / 'apollo_status.json'
            runner_path = status_dir / 'marketing_loop_runner_latest.json'
            reddit_jsonl = status_dir / 'reddit_posts.jsonl'
            retro_path = tmp / 'retro.py'

            adoption_path.write_text(json.dumps({'evaluation': {'failing_signals': ['primary_repo_flat']}}), encoding='utf-8')
            audit_path.write_text(json.dumps({
                'repair_window_status': 'measurement_pending',
                'measurement_pending_reasons': ['primary_repo_flat'],
                'repair_actions': [],
                'failing_tactics': ['primary_repo_flat_window'],
                'latest_executed_action': {'type': action_type, 'ok': True, 'live_external_action': live_external_action},
                'has_failing_tactics': True,
            }), encoding='utf-8')
            (seo_dir / 'reddit_monitor_latest.md').write_text('# report\n\n- **Shortlisted:** 0\n', encoding='utf-8')
            (seo_dir / 'reddit_monitor_latest_healthy.md').write_text('# report\n\n- **Shortlisted:** 0\n', encoding='utf-8')
            apollo_status.write_text(json.dumps({'status': 'login_succeeded', 'cloudflare_blocked': False}), encoding='utf-8')
            reddit_jsonl.write_text('', encoding='utf-8')
            retro_path.write_text('print("{}")\n', encoding='utf-8')
            if runner_payload is not None:
                runner_path.write_text(json.dumps(runner_payload), encoding='utf-8')

            with patch.object(marketing_momentum_watchdog, 'ROOT', tmp), \
                 patch.object(marketing_momentum_watchdog, 'SEO', seo_dir), \
                 patch.object(marketing_momentum_watchdog, 'STATUS_DIR', status_dir), \
                 patch.object(marketing_momentum_watchdog, 'STATUS_PATH', status_path), \
                 patch.object(marketing_momentum_watchdog, 'ADOPTION_PATH', adoption_path), \
                 patch.object(marketing_momentum_watchdog, 'AUDIT_PATH', audit_path), \
                 patch.object(marketing_momentum_watchdog, 'APOLLO_STATUS_PATH', apollo_status), \
                 patch.object(marketing_momentum_watchdog, 'RUNNER_PATH', runner_path), \
                 patch.object(marketing_momentum_watchdog, 'LOG_JSONL', reddit_jsonl), \
                 patch.object(marketing_momentum_watchdog, 'RETRO', retro_path):
                rc = marketing_momentum_watchdog.main()

            return rc, json.loads(status_path.read_text(encoding='utf-8'))

    def test_watchdog_accepts_live_directory_submission_as_live_outcome_repair(self):
        rc, payload = self._run_watchdog_with_action('aigearbase_free_listing_submission', live_external_action=True)
        self.assertEqual(rc, 0)
        self.assertNotIn('outcome_system_repair_missing', payload['actions'])
        self.assertIn('primary_repo_adoption_flat', payload['watch_actions'])

    def test_watchdog_accepts_apollo_outreach_execution_as_structural_repair(self):
        rc, payload = self._run_watchdog_with_action('apollo_outreach_execution', live_external_action=False)
        self.assertEqual(rc, 0)
        self.assertNotIn('outcome_system_repair_missing', payload['actions'])
        self.assertIn('primary_repo_adoption_flat', payload['watch_actions'])

    def test_watchdog_rejects_prepared_curator_packet_as_live_outcome_repair(self):
        rc, payload = self._run_watchdog_with_action('curator_handoff_packet_execution', live_external_action=False)
        self.assertEqual(rc, 1)
        self.assertIn('outcome_system_repair_missing', payload['actions'])

    def test_watchdog_rejects_distribution_reset_as_insufficient_live_outcome_repair(self):
        rc, payload = self._run_watchdog_with_action('distribution_reset_execution')
        self.assertEqual(rc, 1)
        self.assertIn('outcome_system_repair_missing', payload['actions'])

    def test_watchdog_rejects_curator_follow_through_as_live_outcome_repair(self):
        rc, payload = self._run_watchdog_with_action('curator_queue_follow_through')
        self.assertEqual(rc, 1)
        self.assertIn('outcome_system_repair_missing', payload['actions'])
        self.assertIn('measurement_pending_without_repairs', payload['actions'])
        self.assertNotIn('no_recent_reddit_post', payload['actions'])
        self.assertIn('primary_repo_adoption_flat', payload['watch_actions'])

    def test_watchdog_does_not_fail_stale_reddit_report_when_recent_monitor_hit_cooldown(self):
        runner_payload = {
            'generated_at': datetime.now().isoformat(),
            'results': [
                {
                    'script': '/tmp/reddit_monitor.py',
                    'stdout': json.dumps({'status': 'cooldown_skip'}),
                }
            ],
        }
        rc, payload = self._run_watchdog_with_action(
            'aigearbase_free_listing_submission',
            live_external_action=True,
            runner_payload=runner_payload,
        )
        self.assertEqual(rc, 0)
        self.assertNotIn('reddit_monitor_stale', payload['actions'])
        self.assertEqual(payload['reddit_monitor_runtime']['status'], 'cooldown_skip')
        self.assertIn('primary_repo_adoption_flat', payload['watch_actions'])


class MarketingWorkflowAuditTests(unittest.TestCase):
    def test_load_latest_marketing_action_prefers_execution_log_over_lane_switch_stub(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            decision = out_dir / 'marketing_2026-05-22_curator_outreach.json'
            execution = out_dir / 'marketing_2026-05-22_curator_outreach_execution.json'
            decision.write_text(json.dumps({'chosen_action': {'type': 'distribution_lane_switch'}}), encoding='utf-8')
            execution.write_text(json.dumps({'chosen_action': {'type': 'curator_queue_follow_through'}}), encoding='utf-8')

            with patch.object(marketing_workflow_audit, 'OUT_DIR', out_dir):
                payload = marketing_workflow_audit.load_latest_marketing_action()

            self.assertEqual(payload['chosen_action']['type'], 'curator_queue_follow_through')

    def test_load_latest_marketing_action_normalizes_live_submission_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            live = out_dir / 'marketing_2026-05-22_aigearbase_submission.json'
            live.write_text(json.dumps({
                'action': 'aigearbase_free_listing_submission',
                'type': 'EXECUTED / DISTRIBUTION',
                'channel': {'name': 'AI Gear Base', 'submit_page': 'https://aigearbase.com/submit', 'response': {'http_status': 200}},
                'submitted_payload': {'website_url': 'https://codeberg.org/RalphWorkflow/Ralph-Workflow'},
            }), encoding='utf-8')

            with patch.object(marketing_workflow_audit, 'OUT_DIR', out_dir):
                payload = marketing_workflow_audit.load_latest_marketing_action()

            self.assertEqual(payload['chosen_action']['type'], 'aigearbase_free_listing_submission')
            self.assertTrue(payload['result']['live_external_action'])

    def test_load_latest_marketing_action_prefers_live_action_over_newer_apollo_packet_stub(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            older_live = out_dir / 'marketing_2026-05-22_apollo_curator_followup_list.json'
            newer_packet = out_dir / 'marketing_2026-05-22_apollo_outreach_execution.json'
            older_live.write_text(json.dumps({
                'chosen_action': {'type': 'apollo_people_list_creation', 'channel': 'apollo_outreach'},
                'result': {'ok': True, 'status': 'executed', 'live_external_action': True},
            }), encoding='utf-8')
            newer_packet.write_text(json.dumps({
                'chosen_action': {'type': 'apollo_outreach_execution', 'channel': 'apollo_outreach'},
                'result': {'ok': True, 'status': 'prepared', 'live_external_action': False},
            }), encoding='utf-8')
            newer_packet.touch()

            with patch.object(marketing_workflow_audit, 'OUT_DIR', out_dir):
                payload = marketing_workflow_audit.load_latest_marketing_action()

            self.assertEqual(payload['chosen_action']['type'], 'apollo_people_list_creation')

    def test_load_latest_marketing_action_can_read_daily_bundle_distribution_execution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            bundle = out_dir / 'marketing_2026-05-24.json'
            bundle.write_text(json.dumps({
                'distribution_lane': {'lane': 'measurement_hold'},
                'distribution_execution': {
                    'action_type': 'measurement_hold_follow_through',
                    'status': 'executed',
                    'artifact_path': '/tmp/hold.md',
                    'summary': 'Active hold respected.',
                    'live_external_action': False,
                    'blocking_factors': [],
                },
            }), encoding='utf-8')

            with patch.object(marketing_workflow_audit, 'OUT_DIR', out_dir):
                payload = marketing_workflow_audit.load_latest_marketing_action(prefer_meaningful=False)

            self.assertEqual(payload['chosen_action']['type'], 'measurement_hold_follow_through')
            self.assertEqual(payload['chosen_action']['channel'], 'measurement_hold')
            self.assertEqual(payload['result']['status'], 'executed')

    def test_load_latest_marketing_action_keeps_latest_activity_separate_from_latest_meaningful_execution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            live = out_dir / 'marketing_2026-05-24_050000_aiagents_directory_submission.json'
            bundle = out_dir / 'marketing_2026-05-24.json'
            live.write_text(json.dumps({
                'chosen_action': {'type': 'aiagents_directory_submission', 'channel': 'directory_submission'},
                'result': {'ok': True, 'status': 'executed', 'live_external_action': True},
            }), encoding='utf-8')
            bundle.write_text(json.dumps({
                'distribution_lane': {'lane': 'measurement_hold'},
                'distribution_execution': {
                    'action_type': 'measurement_hold_follow_through',
                    'status': 'executed',
                    'artifact_path': '/tmp/hold.md',
                    'summary': 'Active hold respected.',
                    'live_external_action': False,
                    'blocking_factors': [],
                },
            }), encoding='utf-8')
            bundle.touch()

            with patch.object(marketing_workflow_audit, 'OUT_DIR', out_dir):
                latest_meaningful = marketing_workflow_audit.load_latest_marketing_action()
                latest_activity = marketing_workflow_audit.load_latest_marketing_action(prefer_meaningful=False)

            self.assertEqual(latest_meaningful['chosen_action']['type'], 'aiagents_directory_submission')
            self.assertEqual(latest_activity['chosen_action']['type'], 'measurement_hold_follow_through')

    def test_audit_treats_curator_redesign_as_shipped_and_drops_duplicate_architecture_repair(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out_dir = tmp / 'logs'
            out_dir.mkdir()
            adoption_path = out_dir / 'adoption_metrics_latest.json'
            retro_path = out_dir / 'reddit_post_analysis.json'
            outreach_path = tmp / 'outreach-log.md'
            principles_path = tmp / 'principles.md'
            four_questions_path = tmp / 'four_questions.md'
            self_improvement_path = tmp / 'self_improvement.md'
            audit_json = out_dir / 'marketing_workflow_audit_latest.json'
            audit_md = out_dir / 'marketing_workflow_audit_latest.md'

            adoption_path.write_text(json.dumps({
                'metrics': [],
                'recent_window': {
                    'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                    'GitHub': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                },
                'evaluation': {'failing_signals': ['primary_repo_flat']},
            }), encoding='utf-8')
            retro_path.write_text(json.dumps({'recent_posts': [], 'repeated_openings': []}), encoding='utf-8')
            outreach_path.write_text('HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.', encoding='utf-8')
            principles_path.write_text('principles', encoding='utf-8')
            four_questions_path.write_text('questions', encoding='utf-8')
            self_improvement_path.write_text('self-improvement', encoding='utf-8')
            (out_dir / 'marketing_2026-05-22_aigearbase_submission.json').write_text(json.dumps({
                'action': 'aigearbase_free_listing_submission',
                'type': 'EXECUTED / DISTRIBUTION',
                'channel': {'name': 'AI Gear Base', 'submit_page': 'https://aigearbase.com/submit', 'response': {'http_status': 200}},
                'submitted_payload': {'website_url': 'https://codeberg.org/RalphWorkflow/Ralph-Workflow'},
            }), encoding='utf-8')

            with patch.object(marketing_workflow_audit, 'OUT_DIR', out_dir), \
                 patch.object(marketing_workflow_audit, 'AUDIT_JSON', audit_json), \
                 patch.object(marketing_workflow_audit, 'AUDIT_MD', audit_md), \
                 patch.object(marketing_workflow_audit, 'OUTREACH', outreach_path), \
                 patch.object(marketing_workflow_audit, 'ADOPTION', adoption_path), \
                 patch.object(marketing_workflow_audit, 'RETRO', retro_path), \
                 patch.object(marketing_workflow_audit, 'PRINCIPLES', principles_path), \
                 patch.object(marketing_workflow_audit, 'FOUR_QUESTIONS_DOC', four_questions_path), \
                 patch.object(marketing_workflow_audit, 'SELF_IMPROVEMENT_DOC', self_improvement_path):
                rc = marketing_workflow_audit.main()

            self.assertEqual(rc, 0)
            payload = json.loads(audit_json.read_text(encoding='utf-8'))
            targets = [item['target_tactic'] for item in payload['repair_actions']]
            self.assertNotIn('marketing_system_architecture', targets)
            content_action = next(item for item in payload['repair_actions'] if item['target_tactic'] == 'content_distribution')
            self.assertIn('Owned content is saturated for now', content_action['action'])

    def test_audit_treats_sent_curator_email_as_live_replacement_action(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out_dir = tmp / 'logs'
            out_dir.mkdir()

            payload = {
                'timestamp_utc': '2026-05-23T15:56:07.757532+00:00',
                'action': 'curator_email_outreach',
                'status': 'sent',
                'channel': {
                    'recipient': 'andyrewlee@gmail.com',
                    'subject': 'Ralph Workflow for awesome-agent-orchestrators',
                },
                'body_file': 'drafts/curator_outreach/2026-05-23/andyrewlee-awesome-agent-orchestrators-email.txt',
            }
            path = out_dir / 'marketing_2026-05-23_155607_andyrewlee_curator_email.json'
            path.write_text(json.dumps(payload), encoding='utf-8')

            normalized = marketing_workflow_audit.normalize_marketing_action(payload, path)
            self.assertEqual(normalized['chosen_action']['type'], 'curator_email_outreach')
            self.assertTrue(normalized['result']['live_external_action'])
            self.assertEqual(normalized['result']['status'], 'sent')

        
    def test_audit_does_not_treat_curator_follow_through_as_shipped_system_repair(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out_dir = tmp / 'logs'
            out_dir.mkdir()
            adoption_path = out_dir / 'adoption_metrics_latest.json'
            retro_path = out_dir / 'reddit_post_analysis.json'
            outreach_path = tmp / 'outreach-log.md'
            principles_path = tmp / 'principles.md'
            four_questions_path = tmp / 'four_questions.md'
            self_improvement_path = tmp / 'self_improvement.md'
            audit_json = out_dir / 'marketing_workflow_audit_latest.json'
            audit_md = out_dir / 'marketing_workflow_audit_latest.md'

            adoption_path.write_text(json.dumps({
                'metrics': [],
                'recent_window': {
                    'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                    'GitHub': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                },
                'evaluation': {'failing_signals': ['primary_repo_flat']},
            }), encoding='utf-8')
            retro_path.write_text(json.dumps({'recent_posts': [], 'repeated_openings': []}), encoding='utf-8')
            outreach_path.write_text('HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.', encoding='utf-8')
            principles_path.write_text('principles', encoding='utf-8')
            four_questions_path.write_text('questions', encoding='utf-8')
            self_improvement_path.write_text('self-improvement', encoding='utf-8')
            (out_dir / 'marketing_2026-05-22_curator_outreach_execution.json').write_text(json.dumps({
                'chosen_action': {'type': 'curator_queue_follow_through', 'title': 'Distribution lane execution: curator_outreach'},
                'result': {'ok': True, 'status': 'executed'},
            }), encoding='utf-8')

            with patch.object(marketing_workflow_audit, 'OUT_DIR', out_dir), \
                 patch.object(marketing_workflow_audit, 'AUDIT_JSON', audit_json), \
                 patch.object(marketing_workflow_audit, 'AUDIT_MD', audit_md), \
                 patch.object(marketing_workflow_audit, 'OUTREACH', outreach_path), \
                 patch.object(marketing_workflow_audit, 'ADOPTION', adoption_path), \
                 patch.object(marketing_workflow_audit, 'RETRO', retro_path), \
                 patch.object(marketing_workflow_audit, 'PRINCIPLES', principles_path), \
                 patch.object(marketing_workflow_audit, 'FOUR_QUESTIONS_DOC', four_questions_path), \
                 patch.object(marketing_workflow_audit, 'SELF_IMPROVEMENT_DOC', self_improvement_path):
                rc = marketing_workflow_audit.main()

            self.assertEqual(rc, 0)
            payload = json.loads(audit_json.read_text(encoding='utf-8'))
            targets = [item['target_tactic'] for item in payload['repair_actions']]
            self.assertIn('marketing_system_architecture', targets)

    def test_audit_does_not_treat_apollo_outreach_packet_as_shipped_system_repair(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out_dir = tmp / 'logs'
            out_dir.mkdir()
            adoption_path = out_dir / 'adoption_metrics_latest.json'
            retro_path = out_dir / 'reddit_post_analysis.json'
            outreach_path = tmp / 'outreach-log.md'
            principles_path = tmp / 'principles.md'
            four_questions_path = tmp / 'four_questions.md'
            self_improvement_path = tmp / 'self_improvement.md'
            audit_json = out_dir / 'marketing_workflow_audit_latest.json'
            audit_md = out_dir / 'marketing_workflow_audit_latest.md'

            adoption_path.write_text(json.dumps({
                'metrics': [],
                'recent_window': {
                    'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                    'GitHub': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                },
                'evaluation': {'failing_signals': ['primary_repo_flat']},
            }), encoding='utf-8')
            retro_path.write_text(json.dumps({'recent_posts': [], 'repeated_openings': []}), encoding='utf-8')
            outreach_path.write_text('Reddit blocked; Apollo packet prepared.', encoding='utf-8')
            principles_path.write_text('principles', encoding='utf-8')
            four_questions_path.write_text('questions', encoding='utf-8')
            self_improvement_path.write_text('self-improvement', encoding='utf-8')
            (out_dir / 'marketing_2026-05-22_apollo_outreach_execution.json').write_text(json.dumps({
                'chosen_action': {'type': 'apollo_outreach_execution', 'title': 'Distribution lane execution: apollo_outreach'},
                'result': {'ok': True, 'status': 'prepared', 'live_external_action': False},
            }), encoding='utf-8')

            with patch.object(marketing_workflow_audit, 'OUT_DIR', out_dir), \
                 patch.object(marketing_workflow_audit, 'AUDIT_JSON', audit_json), \
                 patch.object(marketing_workflow_audit, 'AUDIT_MD', audit_md), \
                 patch.object(marketing_workflow_audit, 'OUTREACH', outreach_path), \
                 patch.object(marketing_workflow_audit, 'ADOPTION', adoption_path), \
                 patch.object(marketing_workflow_audit, 'RETRO', retro_path), \
                 patch.object(marketing_workflow_audit, 'PRINCIPLES', principles_path), \
                 patch.object(marketing_workflow_audit, 'FOUR_QUESTIONS_DOC', four_questions_path), \
                 patch.object(marketing_workflow_audit, 'SELF_IMPROVEMENT_DOC', self_improvement_path):
                rc = marketing_workflow_audit.main()

            self.assertEqual(rc, 0)
            payload = json.loads(audit_json.read_text(encoding='utf-8'))
            targets = [item['target_tactic'] for item in payload['repair_actions']]
            self.assertIn('marketing_system_architecture', targets)

    def test_audit_treats_contact_and_comparison_follow_through_as_housekeeping_not_shipped_repair(self):
        for action_type in ('curator_contact_handoff_follow_through', 'comparison_backlink_follow_through'):
            with self.subTest(action_type=action_type), tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                out_dir = tmp / 'logs'
                out_dir.mkdir()
                adoption_path = out_dir / 'adoption_metrics_latest.json'
                retro_path = out_dir / 'reddit_post_analysis.json'
                outreach_path = tmp / 'outreach-log.md'
                principles_path = tmp / 'principles.md'
                four_questions_path = tmp / 'four_questions.md'
                self_improvement_path = tmp / 'self_improvement.md'
                audit_json = out_dir / 'marketing_workflow_audit_latest.json'
                audit_md = out_dir / 'marketing_workflow_audit_latest.md'

                adoption_path.write_text(json.dumps({
                    'metrics': [],
                    'recent_window': {
                        'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                        'GitHub': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                    },
                    'evaluation': {'failing_signals': ['primary_repo_flat']},
                }), encoding='utf-8')
                retro_path.write_text(json.dumps({'recent_posts': [], 'repeated_openings': []}), encoding='utf-8')
                outreach_path.write_text('HN/Lobsters blocker noted. HN/Lobsters blocker noted. HN/Lobsters blocker noted.', encoding='utf-8')
                principles_path.write_text('principles', encoding='utf-8')
                four_questions_path.write_text('questions', encoding='utf-8')
                self_improvement_path.write_text('self-improvement', encoding='utf-8')
                (out_dir / f'marketing_2026-05-22_{action_type}.json').write_text(json.dumps({
                    'chosen_action': {'type': action_type, 'title': f'Distribution lane execution: {action_type}'},
                    'result': {'ok': True, 'status': 'executed'},
                }), encoding='utf-8')

                with patch.object(marketing_workflow_audit, 'OUT_DIR', out_dir), \
                     patch.object(marketing_workflow_audit, 'AUDIT_JSON', audit_json), \
                     patch.object(marketing_workflow_audit, 'AUDIT_MD', audit_md), \
                     patch.object(marketing_workflow_audit, 'OUTREACH', outreach_path), \
                     patch.object(marketing_workflow_audit, 'ADOPTION', adoption_path), \
                     patch.object(marketing_workflow_audit, 'RETRO', retro_path), \
                     patch.object(marketing_workflow_audit, 'PRINCIPLES', principles_path), \
                     patch.object(marketing_workflow_audit, 'FOUR_QUESTIONS_DOC', four_questions_path), \
                     patch.object(marketing_workflow_audit, 'SELF_IMPROVEMENT_DOC', self_improvement_path):
                    rc = marketing_workflow_audit.main()

                self.assertEqual(rc, 0)
                payload = json.loads(audit_json.read_text(encoding='utf-8'))
                targets = [item['target_tactic'] for item in payload['repair_actions']]
                self.assertIn('marketing_system_architecture', targets)
                self.assertEqual(payload['latest_executed_action']['type'], action_type)

    def test_audit_preserves_pending_measurement_repairs_across_reruns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out_dir = tmp / 'logs'
            out_dir.mkdir()
            adoption_path = out_dir / 'adoption_metrics_latest.json'
            retro_path = out_dir / 'reddit_post_analysis.json'
            outreach_path = tmp / 'outreach-log.md'
            principles_path = tmp / 'principles.md'
            four_questions_path = tmp / 'four_questions.md'
            self_improvement_path = tmp / 'self_improvement.md'
            audit_json = out_dir / 'marketing_workflow_audit_latest.json'
            audit_md = out_dir / 'marketing_workflow_audit_latest.md'

            adoption_path.write_text(json.dumps({
                'metrics': [],
                'recent_window': {
                    'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                    'GitHub': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                },
                'evaluation': {'failing_signals': ['primary_repo_flat']},
            }), encoding='utf-8')
            retro_path.write_text(json.dumps({'recent_posts': [], 'repeated_openings': []}), encoding='utf-8')
            outreach_path.write_text('Reddit blocked; Apollo packet prepared.', encoding='utf-8')
            principles_path.write_text('principles', encoding='utf-8')
            four_questions_path.write_text('questions', encoding='utf-8')
            self_improvement_path.write_text('self-improvement', encoding='utf-8')
            audit_json.write_text(json.dumps({
                'repair_window_status': 'needs_repair',
                'measurement_pending_reasons': ['same_family_distribution_overlap'],
                'repair_actions': [
                    {
                        'target_tactic': 'directory_submission_burst',
                        'failure_type': 'same_family_distribution_overlap',
                        'repair_kind': 'tactic',
                        'action': 'pause directory submissions',
                        'kill_condition': 'n/a',
                        'success_metric': 'n/a',
                        'priority': 1,
                        'repair_state': 'pending_measurement',
                        'repair_acknowledged_at': '2026-05-24T00:51:00+02:00',
                    }
                ],
            }), encoding='utf-8')
            (out_dir / 'marketing_2026-05-22_apollo_outreach_execution.json').write_text(json.dumps({
                'chosen_action': {'type': 'apollo_outreach_execution', 'title': 'Distribution lane execution: apollo_outreach'},
                'result': {'ok': True, 'status': 'prepared', 'live_external_action': False},
            }), encoding='utf-8')

            with patch.object(marketing_workflow_audit, 'OUT_DIR', out_dir), \
                 patch.object(marketing_workflow_audit, 'AUDIT_JSON', audit_json), \
                 patch.object(marketing_workflow_audit, 'AUDIT_MD', audit_md), \
                 patch.object(marketing_workflow_audit, 'OUTREACH', outreach_path), \
                 patch.object(marketing_workflow_audit, 'ADOPTION', adoption_path), \
                 patch.object(marketing_workflow_audit, 'RETRO', retro_path), \
                 patch.object(marketing_workflow_audit, 'PRINCIPLES', principles_path), \
                 patch.object(marketing_workflow_audit, 'FOUR_QUESTIONS_DOC', four_questions_path), \
                 patch.object(marketing_workflow_audit, 'SELF_IMPROVEMENT_DOC', self_improvement_path), \
                 patch.object(marketing_workflow_audit, 'recent_live_action_family_count', side_effect=lambda _now, family: marketing_workflow_audit.DIRECTORY_SUBMISSION_BURST_THRESHOLD if family == 'directory_submission' else 0):
                rc = marketing_workflow_audit.main()

            self.assertEqual(rc, 0)
            payload = json.loads(audit_json.read_text(encoding='utf-8'))
            repair = next(item for item in payload['repair_actions'] if item['failure_type'] == 'same_family_distribution_overlap')
            self.assertEqual(repair['repair_state'], 'pending_measurement')
            self.assertEqual(repair['repair_acknowledged_at'], '2026-05-24T00:51:00+02:00')
            self.assertIn('same_family_distribution_overlap', payload['measurement_pending_reasons'])

    def test_audit_rejects_zero_record_apollo_live_execution_as_shipped_system_repair(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out_dir = tmp / 'logs'
            out_dir.mkdir()
            adoption_path = out_dir / 'adoption_metrics_latest.json'
            retro_path = out_dir / 'reddit_post_analysis.json'
            outreach_path = tmp / 'outreach-log.md'
            principles_path = tmp / 'principles.md'
            four_questions_path = tmp / 'four_questions.md'
            self_improvement_path = tmp / 'self_improvement.md'
            audit_json = out_dir / 'marketing_workflow_audit_latest.json'
            audit_md = out_dir / 'marketing_workflow_audit_latest.md'

            adoption_path.write_text(json.dumps({
                'metrics': [],
                'recent_window': {
                    'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                    'GitHub': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                },
                'evaluation': {'failing_signals': ['primary_repo_flat']},
            }), encoding='utf-8')
            retro_path.write_text(json.dumps({'recent_posts': [], 'repeated_openings': []}), encoding='utf-8')
            outreach_path.write_text('Reddit blocked; Apollo list was attempted.', encoding='utf-8')
            principles_path.write_text('principles', encoding='utf-8')
            four_questions_path.write_text('questions', encoding='utf-8')
            self_improvement_path.write_text('self-improvement', encoding='utf-8')
            (out_dir / 'marketing_2026-05-22_apollo_curator_followup_list.json').write_text(json.dumps({
                'chosen_action': {'type': 'apollo_people_list_creation', 'channel': 'apollo_outreach', 'title': 'Apollo curator follow-up list creation'},
                'result': {
                    'ok': True,
                    'status': 'executed',
                    'live_external_action': True,
                    'notes': ['The visible record count was 0 right after creation, so the import path likely needs a second-pass check before using this list for a sequence.'],
                },
            }), encoding='utf-8')

            with patch.object(marketing_workflow_audit, 'OUT_DIR', out_dir), \
                 patch.object(marketing_workflow_audit, 'AUDIT_JSON', audit_json), \
                 patch.object(marketing_workflow_audit, 'AUDIT_MD', audit_md), \
                 patch.object(marketing_workflow_audit, 'OUTREACH', outreach_path), \
                 patch.object(marketing_workflow_audit, 'ADOPTION', adoption_path), \
                 patch.object(marketing_workflow_audit, 'RETRO', retro_path), \
                 patch.object(marketing_workflow_audit, 'PRINCIPLES', principles_path), \
                 patch.object(marketing_workflow_audit, 'FOUR_QUESTIONS_DOC', four_questions_path), \
                 patch.object(marketing_workflow_audit, 'SELF_IMPROVEMENT_DOC', self_improvement_path):
                rc = marketing_workflow_audit.main()

            self.assertEqual(rc, 0)
            payload = json.loads(audit_json.read_text(encoding='utf-8'))
            targets = [item['target_tactic'] for item in payload['repair_actions']]
            self.assertIn('marketing_system_architecture', targets)
            self.assertIn('managed_outbound_execution', targets)
            self.assertFalse(payload['latest_executed_action']['outcome_ready'])

    def test_audit_parks_reddit_repetition_when_reddit_is_infrastructure_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out_dir = tmp / 'logs'
            out_dir.mkdir()
            adoption_path = out_dir / 'adoption_metrics_latest.json'
            retro_path = out_dir / 'reddit_post_analysis.json'
            outreach_path = tmp / 'outreach-log.md'
            principles_path = tmp / 'principles.md'
            four_questions_path = tmp / 'four_questions.md'
            self_improvement_path = tmp / 'self_improvement.md'
            reddit_monitor_path = tmp / 'reddit_monitor_latest.md'
            audit_json = out_dir / 'marketing_workflow_audit_latest.json'
            audit_md = out_dir / 'marketing_workflow_audit_latest.md'

            adoption_path.write_text(json.dumps({
                'metrics': [],
                'recent_window': {
                    'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                    'GitHub': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                },
                'evaluation': {'failing_signals': ['primary_repo_flat']},
            }), encoding='utf-8')
            retro_path.write_text(json.dumps({'recent_posts': [], 'repeated_openings': ['same reddit hook']}), encoding='utf-8')
            reddit_monitor_path.write_text('Today\'s bottom line\n- Reddit is IP-blocked from this server: all Reddit API calls return HTTP 403.', encoding='utf-8')
            outreach_path.write_text('Measurement window active.', encoding='utf-8')
            principles_path.write_text('principles', encoding='utf-8')
            four_questions_path.write_text('questions', encoding='utf-8')
            self_improvement_path.write_text('self-improvement', encoding='utf-8')

            with patch.object(marketing_workflow_audit, 'OUT_DIR', out_dir), \
                 patch.object(marketing_workflow_audit, 'AUDIT_JSON', audit_json), \
                 patch.object(marketing_workflow_audit, 'AUDIT_MD', audit_md), \
                 patch.object(marketing_workflow_audit, 'OUTREACH', outreach_path), \
                 patch.object(marketing_workflow_audit, 'ADOPTION', adoption_path), \
                 patch.object(marketing_workflow_audit, 'RETRO', retro_path), \
                 patch.object(marketing_workflow_audit, 'PRINCIPLES', principles_path), \
                 patch.object(marketing_workflow_audit, 'FOUR_QUESTIONS_DOC', four_questions_path), \
                 patch.object(marketing_workflow_audit, 'SELF_IMPROVEMENT_DOC', self_improvement_path), \
                 patch.object(marketing_workflow_audit, 'REDDIT_MONITOR_LATEST', reddit_monitor_path):
                rc = marketing_workflow_audit.main()

            self.assertEqual(rc, 0)
            payload = json.loads(audit_json.read_text(encoding='utf-8'))
            self.assertNotIn('reddit_style_repetition', payload['failing_tactics'])
            self.assertIn('reddit_style_repetition_suspended_while_channel_blocked', payload['dormant_risks'])
            self.assertFalse(any(item['target_tactic'] == 'reddit_post_style' for item in payload['repair_actions']))

    def test_audit_parks_reddit_repetition_when_latest_report_is_partial_coverage_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out_dir = tmp / 'logs'
            out_dir.mkdir()
            adoption_path = out_dir / 'adoption_metrics_latest.json'
            retro_path = out_dir / 'reddit_post_analysis.json'
            outreach_path = tmp / 'outreach-log.md'
            principles_path = tmp / 'principles.md'
            four_questions_path = tmp / 'four_questions.md'
            self_improvement_path = tmp / 'self_improvement.md'
            reddit_monitor_path = tmp / 'reddit_monitor_latest.md'
            audit_json = out_dir / 'marketing_workflow_audit_latest.json'
            audit_md = out_dir / 'marketing_workflow_audit_latest.md'

            adoption_path.write_text(json.dumps({
                'metrics': [],
                'recent_window': {
                    'Codeberg': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                    'GitHub': {'samples': 9, 'stars_delta_window': 0, 'watchers_delta_window': 0, 'forks_delta_window': 0},
                },
                'evaluation': {'failing_signals': ['primary_repo_flat']},
            }), encoding='utf-8')
            retro_path.write_text(json.dumps({'recent_posts': [], 'repeated_openings': ['same reddit hook']}), encoding='utf-8')
            reddit_monitor_path.write_text(
                '# Reddit monitor\n\n'
                '- **Important telemetry note**: some Reddit queries were blocked (**reddit_ip_blocked=3**), but other queries still returned usable results (**ok=4**). Treat this as partial coverage, not a total Reddit outage.\n'
                '- Provider still challenge-heavy and fails closed on posting.\n',
                encoding='utf-8',
            )
            outreach_path.write_text('Measurement window active.', encoding='utf-8')
            principles_path.write_text('principles', encoding='utf-8')
            four_questions_path.write_text('questions', encoding='utf-8')
            self_improvement_path.write_text('self-improvement', encoding='utf-8')

            with patch.object(marketing_workflow_audit, 'OUT_DIR', out_dir), \
                 patch.object(marketing_workflow_audit, 'AUDIT_JSON', audit_json), \
                 patch.object(marketing_workflow_audit, 'AUDIT_MD', audit_md), \
                 patch.object(marketing_workflow_audit, 'OUTREACH', outreach_path), \
                 patch.object(marketing_workflow_audit, 'ADOPTION', adoption_path), \
                 patch.object(marketing_workflow_audit, 'RETRO', retro_path), \
                 patch.object(marketing_workflow_audit, 'PRINCIPLES', principles_path), \
                 patch.object(marketing_workflow_audit, 'FOUR_QUESTIONS_DOC', four_questions_path), \
                 patch.object(marketing_workflow_audit, 'SELF_IMPROVEMENT_DOC', self_improvement_path), \
                 patch.object(marketing_workflow_audit, 'REDDIT_MONITOR_LATEST', reddit_monitor_path):
                rc = marketing_workflow_audit.main()

            self.assertEqual(rc, 0)
            payload = json.loads(audit_json.read_text(encoding='utf-8'))
            self.assertNotIn('reddit_style_repetition', payload['failing_tactics'])
            self.assertIn('reddit_style_repetition_suspended_while_channel_blocked', payload['dormant_risks'])
            self.assertFalse(any(item['target_tactic'] == 'reddit_post_style' for item in payload['repair_actions']))


class ApolloSequenceLauncherTests(unittest.TestCase):
    def test_launcher_skips_duplicate_outreach_log_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            log_dir = tmp / 'logs'
            drafts_dir = tmp / 'drafts'
            log_dir.mkdir()
            drafts_dir.mkdir()
            verification_path = log_dir / 'marketing_2026-05-23_apollo_list_verification.json'
            verification_path.write_text(json.dumps({
                'result': {
                    'record_count': 5,
                    'outcome_ready': True,
                    'final_url': 'https://app.apollo.io/#/lists/example',
                }
            }), encoding='utf-8')
            outreach_path = tmp / 'outreach-log.md'
            outreach_path.write_text('# Outreach Log\n\n' + apollo_sequence_launcher.PRIMARY_HEADING + '\n- existing entry\n', encoding='utf-8')

            with patch.object(apollo_sequence_launcher, 'LOG_DIR', log_dir), \
                 patch.object(apollo_sequence_launcher, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(apollo_sequence_launcher, 'OUTREACH_LOG', outreach_path):
                rc = apollo_sequence_launcher.main()

            self.assertEqual(rc, 0)
            text = outreach_path.read_text(encoding='utf-8')
            self.assertEqual(text.count(apollo_sequence_launcher.PRIMARY_HEADING), 1)


class MarketingLoopCertificationTests(unittest.TestCase):
    def test_checker_rejects_curator_follow_through_as_system_redesign_shipment(self):
        audit = {
            'latest_executed_action': {'type': 'curator_queue_follow_through', 'ok': True},
            'repair_actions': [],
        }
        self.assertFalse(marketing_loop_checker.shipped_system_redesign(audit))

    def test_checker_accepts_live_external_submission_as_system_redesign_shipment(self):
        audit = {
            'latest_executed_action': {'type': 'aigearbase_free_listing_submission', 'ok': True, 'live_external_action': True},
            'repair_actions': [],
        }
        self.assertTrue(marketing_loop_checker.shipped_system_redesign(audit))

    def test_checker_accepts_apollo_sequence_launch_as_system_redesign_shipment(self):
        audit = {
            'latest_executed_action': {'type': 'apollo_sequence_launch', 'ok': True, 'live_external_action': True, 'outcome_ready': True},
            'repair_actions': [],
        }
        self.assertTrue(marketing_loop_checker.shipped_system_redesign(audit))

    def test_checker_rejects_apollo_outreach_execution_packet_as_system_redesign_shipment(self):
        audit = {
            'latest_executed_action': {'type': 'apollo_outreach_execution', 'ok': True, 'live_external_action': False},
            'repair_actions': [],
        }
        self.assertFalse(marketing_loop_checker.shipped_system_redesign(audit))

    def test_checker_rejects_low_signal_live_external_action_as_system_redesign_shipment(self):
        audit = {
            'latest_executed_action': {'type': 'apollo_people_list_creation', 'ok': True, 'live_external_action': True, 'outcome_ready': False},
            'repair_actions': [],
        }
        self.assertFalse(marketing_loop_checker.shipped_system_redesign(audit))

    def test_checker_rejects_curator_handoff_packet_as_system_redesign_shipment(self):
        audit = {
            'latest_executed_action': {'type': 'curator_handoff_packet_execution', 'ok': True, 'live_external_action': False},
            'repair_actions': [],
        }
        self.assertFalse(marketing_loop_checker.shipped_system_redesign(audit))

    def test_checker_rejects_distribution_reset_execution_as_system_redesign_shipment(self):
        audit = {
            'latest_executed_action': {'type': 'distribution_reset_execution', 'ok': True},
            'repair_actions': [],
        }
        self.assertFalse(marketing_loop_checker.shipped_system_redesign(audit))

    def test_checker_refuses_to_certify_flat_primary_repo_even_when_measurement_is_pending(self):
        ok, reason = marketing_loop_checker.watch_state_is_certifiable(
            {'watch_actions': ['primary_repo_adoption_flat'], 'actions': []},
            {'repair_window_status': 'measurement_pending', 'measurement_pending_reasons': ['primary_repo_flat']},
        )
        self.assertFalse(ok)
        self.assertIn('do not certify', reason)

    def test_independent_verifier_rejects_curator_follow_through_as_system_redesign_shipment(self):
        audit = {
            'latest_executed_action': {'type': 'curator_queue_follow_through', 'ok': True},
            'repair_actions': [],
        }
        self.assertFalse(marketing_loop_independent_verify.shipped_system_redesign(audit))

    def test_independent_verifier_accepts_live_external_submission_as_system_redesign_shipment(self):
        audit = {
            'latest_executed_action': {'type': 'toolshelf_free_listing_submission', 'ok': True, 'live_external_action': True},
            'repair_actions': [],
        }
        self.assertTrue(marketing_loop_independent_verify.shipped_system_redesign(audit))

    def test_independent_verifier_accepts_apollo_sequence_launch_as_system_redesign_shipment(self):
        audit = {
            'latest_executed_action': {'type': 'apollo_sequence_launch', 'ok': True, 'live_external_action': True, 'outcome_ready': True},
            'repair_actions': [],
        }
        self.assertTrue(marketing_loop_independent_verify.shipped_system_redesign(audit))

    def test_independent_verifier_rejects_apollo_outreach_execution_packet_as_system_redesign_shipment(self):
        audit = {
            'latest_executed_action': {'type': 'apollo_outreach_execution', 'ok': True, 'live_external_action': False},
            'repair_actions': [],
        }
        self.assertFalse(marketing_loop_independent_verify.shipped_system_redesign(audit))

    def test_independent_verifier_rejects_low_signal_live_external_action_as_system_redesign_shipment(self):
        audit = {
            'latest_executed_action': {'type': 'apollo_people_list_creation', 'ok': True, 'live_external_action': True, 'outcome_ready': False},
            'repair_actions': [],
        }
        self.assertFalse(marketing_loop_independent_verify.shipped_system_redesign(audit))

    def test_independent_verifier_rejects_distribution_reset_as_system_redesign_shipment(self):
        audit = {
            'latest_executed_action': {'type': 'distribution_reset_execution', 'ok': True},
            'repair_actions': [],
        }
        self.assertFalse(marketing_loop_independent_verify.shipped_system_redesign(audit))

    def test_independent_verifier_treats_flat_primary_repo_as_certification_blocker(self):
        ok, blockers, watchpoints = marketing_loop_independent_verify.watch_state_is_certifiable(
            {'watch_actions': ['primary_repo_adoption_flat'], 'actions': []},
            {'repair_window_status': 'measurement_pending', 'measurement_pending_reasons': ['primary_repo_flat']},
        )
        self.assertFalse(ok)
        self.assertTrue(any('do not issue a healthy certification artifact yet' in blocker for blocker in blockers))
        self.assertIn('primary repo adoption remains measurement-pending after shipped repairs', watchpoints)


class CompetitorAnalysisTests(unittest.TestCase):
    def test_competitor_registry_has_hermes_and_conductor(self):
        from agents.marketing import competitor_analysis
        self.assertIn("hermes-agent", competitor_analysis.COMPETITORS)
        self.assertIn("conductor-oss", competitor_analysis.COMPETITORS)
        self.assertIn("conductor-teams", competitor_analysis.COMPETITORS)

    def test_ralph_advantages_defined(self):
        from agents.marketing import competitor_analysis
        self.assertTrue(len(competitor_analysis.RALPH_ADVANTAGES) >= 5)
        self.assertTrue(any("multi-agent" in a.lower() for a in competitor_analysis.RALPH_ADVANTAGES))

    def test_competitor_monitoring_returns_dict(self):
        from agents.marketing import competitor_analysis
        info = competitor_analysis.COMPETITORS["hermes-agent"]
        # Don't call the actual HTTP fetch in tests, just verify structure
        self.assertEqual(info["name"], "Hermes Agent")
        self.assertIn("key_strengths", info)
        self.assertIn("github", info)

    def test_comparison_page_has_required_sections(self):
        from agents.marketing import competitor_analysis
        info = competitor_analysis.COMPETITORS["hermes-agent"]
        monitoring_data = {
            "hermes-agent": {
                "site_status": 200,
                "github_stars": 145388,
                "key_features_found": ["Persistent memory", "Self-improving"],
            }
        }
        md = competitor_analysis.generate_comparison_page("hermes-agent", info, monitoring_data)
        self.assertIn("# Ralph Workflow vs Hermes Agent", md)
        self.assertIn("## Feature Comparison", md)
        self.assertIn("## Why Choose Ralph Workflow", md)
        self.assertIn("pip install ralph-workflow", md)
        self.assertIn("Claude Code", md)  # Ralph advantage
        self.assertIn("Cost arbitrage", md)  # Ralph advantage


if __name__ == "__main__":
    unittest.main()
