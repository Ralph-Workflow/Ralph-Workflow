import unittest

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


if __name__ == "__main__":
    unittest.main()
