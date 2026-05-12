import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agents.marketing import generate_content, run, run_posting


class GenerateContentTests(unittest.TestCase):
    def test_build_draft_content_includes_experiment_metadata(self):
        topic = generate_content.TOPICS[0]
        content = generate_content.build_draft_content(topic, datetime(2026, 5, 11, 7, 0, 0))
        self.assertIn('experiment_id: "2026-05-11-philosophy"', content)
        self.assertIn('product: "RalphWorkflow"', content)
        self.assertIn("# Why AI Agents Need Structure, Not Just Prompts", content)


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
        self.assertIn("cost arbitrage", md)  # Ralph advantage


if __name__ == "__main__":
    unittest.main()
