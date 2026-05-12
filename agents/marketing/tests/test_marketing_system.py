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
        site_health = {
            "homepage": {"ok": True, "status": 200},
            "robots": {"ok": True, "status": 200},
            "sitemap": {"ok": True, "status": 200},
        }
        decisions = run.build_weekly_decisions(summary, site_health)
        actions = "\n".join(item["action"] for item in decisions)
        self.assertIn("Keep publishing technical content.", actions)
        self.assertIn("Shift one future slot away from philosophy toward technical.", actions)

    def test_recent_successful_posts_filters_old_or_failed_posts(self):
        now = datetime(2026, 5, 12, 9, 0, 0)
        posts = [
            {"ok": True, "date": "2026-05-11", "title": "recent"},
            {"ok": True, "date": "2026-03-01", "title": "old"},
            {"ok": False, "date": "2026-05-10", "title": "failed"},
        ]
        filtered = run.recent_successful_posts(posts, now, days=30)
        self.assertEqual([p["title"] for p in filtered], ["recent"])


class SummaryTests(unittest.TestCase):
    def test_build_summary_aggregates_post_views(self):
        now = datetime(2026, 5, 12, 9, 0, 0)
        fake_posts = [
            {"ok": True, "date": "2026-05-11", "url": "https://write.as/a", "content_type": "technical"},
            {"ok": True, "date": "2026-05-10", "url": "https://write.as/b", "content_type": "technical"},
        ]
        with patch("agents.marketing.run.load_posted_records", return_value=fake_posts), patch(
            "agents.marketing.run.fetch_writeas_views", side_effect=[30, 50]
        ), patch("agents.marketing.run.http_status", return_value={"ok": True, "status": 200}):
            summary = run.build_summary(now)
        self.assertEqual(summary["totals"]["posts_last_30d"], 2)
        self.assertEqual(summary["totals"]["views_last_30d"], 80)
        self.assertEqual(summary["content_summary"]["technical"]["avg_views"], 40.0)


if __name__ == "__main__":
    unittest.main()
