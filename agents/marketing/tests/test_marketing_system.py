import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agents.marketing import distribution_lane_executor, distribution_lane_selector, generate_content, run, run_posting


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
            adoption_path.write_text(json.dumps(adoption), encoding='utf-8')
            channel_path.write_text(json.dumps(channel_log), encoding='utf-8')
            outreach_path.write_text(outreach_text, encoding='utf-8')
            for payload, name in recent_logs:
                (log_dir / name).write_text(json.dumps(payload), encoding='utf-8')

            with patch.object(distribution_lane_selector, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_selector, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_selector, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_selector, 'CHANNEL_DISCOVERY_PATH', channel_path), \
                 patch.object(distribution_lane_selector, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
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
                 patch.object(distribution_lane_selector, 'LATEST_JSON', latest_json), \
                 patch.object(distribution_lane_selector, 'LATEST_MD', latest_md), \
                 patch.object(distribution_lane_selector, 'MARKET_INTELLIGENCE_PATH', tmp / 'missing.json'):
                decision = distribution_lane_selector.choose_distribution_lane(now)

            self.assertEqual(decision.lane, 'curator_outreach')


class DistributionLaneExecutorTests(unittest.TestCase):
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

            with patch.object(distribution_lane_executor, 'DRAFTS_DIR', drafts_dir), \
                 patch.object(distribution_lane_executor, 'LOG_DIR', log_dir), \
                 patch.object(distribution_lane_executor, 'TARGETS_PATH', targets_path), \
                 patch.object(distribution_lane_executor, 'OUTREACH_LOG_PATH', outreach_path), \
                 patch.object(distribution_lane_executor, 'ADOPTION_PATH', adoption_path), \
                 patch.object(distribution_lane_executor, 'load_market_intelligence', return_value=market_intelligence):
                execution = distribution_lane_executor.execute_distribution_lane(decision, now)

            self.assertEqual(execution.status, 'executed')
            self.assertTrue(execution.artifact_path)
            text = Path(execution.artifact_path).read_text(encoding='utf-8')
            self.assertIn('Shared findings reused', text)
            self.assertIn('Claude Code', text)
            self.assertIn('awesome-ai-coding-tools', text)
            action_log = log_dir / 'marketing_2026-05-22_curator_outreach_execution.json'
            self.assertTrue(action_log.exists())


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
