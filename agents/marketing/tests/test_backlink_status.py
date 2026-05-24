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

    def test_search_queries_include_claudetory_after_submission(self):
        self.assertIn("ralph workflow claudetory", backlink_status.SEARCH_QUERIES)


if __name__ == "__main__":
    unittest.main()
