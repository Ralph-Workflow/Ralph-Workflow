import unittest

from agents.marketing import channel_discovery


class ChannelDiscoveryTests(unittest.TestCase):
    def test_classifies_login_required_submission_page(self):
        status, note = channel_discovery.classify_platform_response(
            "https://slashdot.org/submission",
            "https://slashdot.org/submission",
            200,
            "You must be logged in to submit stories. You can login here.",
        )
        self.assertEqual(status, "login_required")
        self.assertIn("login", note)

    def test_classifies_parked_domain(self):
        status, note = channel_discovery.classify_platform_response(
            "https://toolhunt.ai",
            "https://toolhunt.ai/",
            200,
            "toolhunt.ai is for sale on Spaceship. Secure checkout and quick transfer.",
        )
        self.assertEqual(status, "parked")
        self.assertIn("parked", note)

    def test_classifies_normal_accessible_page(self):
        status, note = channel_discovery.classify_platform_response(
            "https://example.com/submit",
            "https://example.com/submit",
            200,
            "Submit your product here.",
        )
        self.assertEqual(status, "accessible")
        self.assertIsNone(note)

    def test_classifies_broken_submit_surface_when_copy_loads_without_controls(self):
        status, note = channel_discovery.classify_submission_surface_probe({
            "probe_status": "ok",
            "form_count": 0,
            "input_count": 0,
            "textarea_count": 0,
            "select_count": 0,
            "body_excerpt": "Submit a Tool\nFill out the form below and we'll review it within 48 hours.",
        })
        self.assertEqual(status, "broken_submit_surface")
        self.assertIn("no usable form controls", note)

    def test_submission_probe_gate_only_triggers_for_real_submit_pages(self):
        self.assertTrue(channel_discovery.submission_surface_needs_form_probe(
            "https://www.toolhunter.cc/submit",
            "submit",
            "Submit a Tool. Fill out the form below.",
        ))
        self.assertFalse(channel_discovery.submission_surface_needs_form_probe(
            "https://example.com/blog/post",
            "article",
            "Submit your article idea.",
        ))

    def test_build_working_channels_uses_latest_accessible_results_only(self):
        working = channel_discovery.build_working_channels([
            {"name": "slashdot", "status": "login_required", "difficulty": "medium", "url": "u", "method": "submit"},
            {"name": "toolhunt", "status": "parked", "difficulty": "easy", "url": "u", "method": "submit"},
            {"name": "saashub", "status": "accessible", "difficulty": "easy", "url": "u", "method": "submit"},
        ])
        self.assertEqual([item["name"] for item in working], ["saashub"])


if __name__ == "__main__":
    unittest.main()
