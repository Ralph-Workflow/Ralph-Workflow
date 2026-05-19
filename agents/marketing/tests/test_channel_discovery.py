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

    def test_classifies_cross_host_homepage_redirect_as_non_actionable(self):
        status, note = channel_discovery.classify_platform_response(
            "https://blogsearch.google.com",
            "https://www.google.com/",
            200,
            "Google",
        )
        self.assertEqual(status, "redirects")
        self.assertIn("redirected away from original host", note)

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

    def test_classifies_noop_submit_surface_when_form_only_swaps_to_success_copy(self):
        status, note = channel_discovery.classify_submission_surface_probe(
            {
                "probe_status": "ok",
                "form_count": 1,
                "input_count": 5,
                "textarea_count": 1,
                "select_count": 1,
                "body_excerpt": "Submit a Tool\nTool Name\nWebsite URL\nDescription\nSubmit Tool",
                "forms": [
                    {
                        "action": None,
                        "method": None,
                        "control_count": 7,
                        "named_control_count": 0,
                    }
                ],
            },
            {
                "probe_status": "ok",
                "has_prevent_default": True,
                "has_success_markers": True,
                "has_network_submission_markers": False,
            },
        )
        self.assertEqual(status, "noop_submit_surface")
        self.assertIn("client-side only", note)

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
            {"name": "devpages", "status": "noop_submit_surface", "difficulty": "easy", "url": "u", "method": "submit"},
            {"name": "saashub", "status": "accessible", "difficulty": "easy", "url": "u", "method": "submit"},
        ])
        self.assertEqual([item["name"] for item in working], ["saashub"])

    def test_retired_channels_are_removed_from_active_queue(self):
        active = {name for name, *_ in channel_discovery.CHANNELS_TO_TRY}
        self.assertNotIn("toolhunt", active)
        self.assertNotIn("toolhunter", active)
        self.assertNotIn("devpages", active)
        self.assertIn("toolwise", active)
        self.assertIn("aitoolsindex", active)


if __name__ == "__main__":
    unittest.main()
