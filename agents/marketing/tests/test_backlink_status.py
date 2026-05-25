import unittest
from unittest.mock import patch

from agents.marketing import backlink_status


class BacklinkStatusTests(unittest.TestCase):
    def test_runtime_truth_domains_use_current_live_hosts(self):
        self.assertEqual(
            backlink_status.SUBMISSIONS["AIToolsIndex"]["submit_url"],
            "https://aitoolsindex.org/submit",
        )
        self.assertEqual(
            backlink_status.SUBMISSIONS["ToolWise"]["listing_url"],
            "https://toolwise.ai/tools/ralph-workflow",
        )
        self.assertEqual(
            backlink_status.SUBMISSIONS["Claudetory"]["listing_url"],
            "https://claudetory.com/tools/ralph-workflow",
        )
        self.assertEqual(
            backlink_status.SUBMISSIONS["AiAgentsDirectory"]["submit_url"],
            "https://aiagents.directory/submit/",
        )

    def test_check_listing_status_preserves_status_note(self):
        result = backlink_status.check_listing_status(
            "ToolWise",
            {
                "submit_url": "https://toolwise.ai/submit-tool",
                "listing_url": "https://toolwise.ai/tools/ralph-workflow",
                "known_check_urls": [],
                "status_note": "Existing listing already live.",
            },
        )
        self.assertEqual(result["status_note"], "Existing listing already live.")
        self.assertFalse(result["listing_live"])

    def test_check_listing_status_rejects_false_positive_not_found_page(self):
        with patch.object(
            backlink_status,
            "check_url_status",
            return_value={
                "url": "https://aitoolboard.com/tools/ralph-workflow",
                "status": 200,
                "ok": True,
                "has_product_marker": True,
                "negative_markers": ["tool not found"],
                "transient_markers": [],
            },
        ):
            result = backlink_status.check_listing_status(
                "AIToolboard",
                {
                    "submit_url": "https://aitoolboard.com/submit",
                    "listing_url": "https://aitoolboard.com/tools/ralph-workflow",
                    "known_check_urls": ["https://aitoolboard.com/tools/ralph-workflow"],
                },
            )
        self.assertFalse(result["listing_live"])

    def test_check_listing_status_rejects_transient_loading_page(self):
        with patch.object(
            backlink_status,
            "check_url_status",
            return_value={
                "url": "https://nav-ai.net/tools/ralph-workflow",
                "status": 200,
                "ok": True,
                "has_product_marker": False,
                "negative_markers": [],
                "transient_markers": ["loading..."],
            },
        ):
            result = backlink_status.check_listing_status(
                "NavAI",
                {
                    "submit_url": "https://nav-ai.net/submit",
                    "listing_url": "https://nav-ai.net/tools/ralph-workflow",
                    "known_check_urls": ["https://nav-ai.net/tools/ralph-workflow"],
                },
            )
        self.assertFalse(result["listing_live"])

    def test_body_quality_flags_detects_real_listing_signal(self):
        quality = backlink_status._body_quality_flags(
            "Ralph Workflow is the operating system for autonomous coding"
        )
        self.assertTrue(quality["has_product_marker"])
        self.assertEqual(quality["negative_markers"], [])
        self.assertEqual(quality["transient_markers"], [])

    def test_listing_targets_detects_codeberg_primary(self):
        targets = backlink_status._listing_targets([
            "https://codeberg.org/RalphWorkflow/Ralph-Workflow",
            "https://ralphworkflow.com",
        ])
        self.assertTrue(targets["has_codeberg_repo_link"])
        self.assertFalse(targets["has_github_repo_link"])
        self.assertTrue(targets["has_site_link"])
        self.assertEqual(targets["preferred_repo_target"], "codeberg_primary")

    def test_check_listing_status_aggregates_repo_target(self):
        with patch.object(
            backlink_status,
            "check_url_status",
            return_value={
                "url": "https://toolwise.ai/tools/ralph-workflow",
                "status": 200,
                "ok": True,
                "has_product_marker": True,
                "negative_markers": [],
                "transient_markers": [],
                "has_codeberg_repo_link": True,
                "has_github_repo_link": False,
                "has_site_link": True,
                "preferred_repo_target": "codeberg_primary",
            },
        ):
            result = backlink_status.check_listing_status(
                "ToolWise",
                {
                    "submit_url": "https://toolwise.ai/submit-tool",
                    "listing_url": "https://toolwise.ai/tools/ralph-workflow",
                    "known_check_urls": ["https://toolwise.ai/tools/ralph-workflow"],
                },
            )
        self.assertTrue(result["listing_live"])
        self.assertEqual(result["preferred_repo_target"], "codeberg_primary")
        self.assertTrue(result["has_codeberg_repo_link"])

    def test_search_queries_include_claudetory_after_submission(self):
        self.assertIn("ralph workflow claudetory", backlink_status.SEARCH_QUERIES)

    def test_search_queries_include_aiagents_directory_after_submission(self):
        self.assertIn("ralph workflow aiagents.directory", backlink_status.SEARCH_QUERIES)

    def test_secondary_check_urls_capture_saashub_alternatives_surface(self):
        self.assertIn(
            "https://www.saashub.com/ralph-workflow-alternatives",
            backlink_status.SUBMISSIONS["SaaSHub"]["secondary_check_urls"],
        )

    def test_check_listing_status_tracks_live_secondary_surface_targets(self):
        with patch.object(
            backlink_status,
            "check_url_status",
            side_effect=[
                {
                    "url": "https://saashub.com/ralph-workflow",
                    "status": 200,
                    "ok": True,
                    "has_product_marker": True,
                    "negative_markers": [],
                    "transient_markers": [],
                    "has_codeberg_repo_link": True,
                    "has_github_repo_link": True,
                    "has_site_link": True,
                    "preferred_repo_target": "both",
                },
                {
                    "url": "https://www.saashub.com/ralph-workflow-alternatives",
                    "status": 200,
                    "ok": True,
                    "has_product_marker": True,
                    "negative_markers": [],
                    "transient_markers": [],
                    "has_codeberg_repo_link": False,
                    "has_github_repo_link": True,
                    "has_site_link": True,
                    "preferred_repo_target": "github_only",
                },
            ],
        ):
            result = backlink_status.check_listing_status(
                "SaaSHub",
                {
                    "submit_url": "https://saashub.com/add_url",
                    "listing_url": "https://saashub.com/ralph-workflow",
                    "known_check_urls": ["https://saashub.com/ralph-workflow"],
                    "secondary_check_urls": ["https://www.saashub.com/ralph-workflow-alternatives"],
                },
            )

        self.assertTrue(result["listing_live"])
        self.assertEqual(result["secondary_live_surfaces"], 1)
        self.assertEqual(
            result["secondary_surface_targets"][0]["preferred_repo_target"],
            "github_only",
        )

    def test_google_rate_limited_detects_429_error(self):
        self.assertTrue(
            backlink_status._google_rate_limited(
                {"error": "HTTP Error 429: Too Many Requests"}
            )
        )
        self.assertFalse(backlink_status._google_rate_limited({"error": "timeout"}))

    def test_skipped_google_index_marks_result_unavailable_without_requery(self):
        result = backlink_status.skipped_google_index(
            "ralph workflow toolwise",
            "Skipped after earlier Google 429 to avoid hammering the rate-limited endpoint.",
        )
        self.assertIsNone(result["indexed"])
        self.assertTrue(result["skipped"])
        self.assertIn("429", result["error"])


if __name__ == "__main__":
    unittest.main()
