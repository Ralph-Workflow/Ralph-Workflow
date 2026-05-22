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

    def test_classifies_captcha_submit_surface_as_non_autonomous(self):
        status, note = channel_discovery.classify_submission_surface_probe(
            {
                "probe_status": "ok",
                "form_count": 1,
                "input_count": 5,
                "textarea_count": 1,
                "select_count": 1,
                "body_excerpt": "Submit your AI tool\nTool Name\nDescription\nContact Email",
                "forms": [
                    {
                        "action": None,
                        "method": None,
                        "control_count": 7,
                        "named_control_count": 4,
                    }
                ],
            },
            {
                "probe_status": "ok",
                "has_captcha_markers": True,
                "has_network_submission_markers": True,
            },
            {
                "probe_status": "ok",
                "public_submit_detected": False,
            },
        )
        self.assertEqual(status, "captcha_blocked")
        self.assertIn("captcha", note.lower())

    def test_classifies_server_error_submit_endpoint_as_broken_surface(self):
        status, note = channel_discovery.classify_submission_surface_probe(
            {
                "probe_status": "ok",
                "form_count": 1,
                "input_count": 4,
                "textarea_count": 1,
                "select_count": 0,
                "body_excerpt": "Submit your tool\nYour Email\nTool Name\nWebsite URL\nDescription",
                "forms": [
                    {
                        "action": None,
                        "method": None,
                        "control_count": 5,
                        "named_control_count": 5,
                    }
                ],
            },
            {
                "probe_status": "ok",
                "has_network_submission_markers": True,
            },
            {
                "probe_status": "ok",
                "submit_url": "https://www.iatool.online/api/submit-tool",
                "post_code": 500,
                "server_error_detected": True,
                "public_submit_detected": False,
            },
        )
        self.assertEqual(status, "broken_submit_surface")
        self.assertIn("server error 500", note.lower())

    def test_known_security_blocked_submit_host_overrides_false_positive_accessibility(self):
        status, note = channel_discovery.classify_submission_surface_probe(
            {
                "probe_status": "ok",
                "form_count": 1,
                "input_count": 6,
                "textarea_count": 2,
                "select_count": 2,
                "body_excerpt": "Submit Your Tool\nTool Name\nWebsite URL\nCategory\nPricing Type",
                "forms": [
                    {
                        "action": None,
                        "method": None,
                        "control_count": 10,
                        "named_control_count": 10,
                    }
                ],
            },
            {
                "probe_status": "ok",
                "has_captcha_markers": True,
                "has_network_submission_markers": True,
            },
            {
                "probe_status": "ok",
                "public_submit_detected": False,
            },
            page_url="https://www.codaone.ai/submit/",
        )
        self.assertEqual(status, "broken_submit_surface")
        self.assertIn("security verification", note.lower())

    def test_known_broken_submit_host_overrides_even_when_surface_probe_errors(self):
        status, note = channel_discovery.classify_submission_surface_probe(
            {
                "probe_status": "error",
                "note": "Page.goto timeout",
            },
            {
                "probe_status": "ok",
                "has_network_submission_markers": True,
            },
            {
                "probe_status": "ok",
                "post_code": 403,
                "public_submit_detected": False,
            },
            page_url="https://www.codaone.ai/submit/",
        )
        self.assertEqual(status, "broken_submit_surface")
        self.assertIn("security verification", note.lower())

    def test_known_broken_submit_host_overrides_false_positive_api_accessibility(self):
        status, note = channel_discovery.classify_submission_surface_probe(
            {
                "probe_status": "ok",
                "form_count": 1,
                "input_count": 5,
                "textarea_count": 1,
                "select_count": 1,
                "body_excerpt": "Submit Your AI Tool\nTool Name\nWebsite URL\nCategory\nDescription",
                "forms": [
                    {
                        "action": None,
                        "method": None,
                        "control_count": 7,
                        "named_control_count": 7,
                    }
                ],
            },
            {
                "probe_status": "ok",
                "has_network_submission_markers": True,
            },
            {
                "probe_status": "ok",
                "submit_url": "https://aisotools.com/api/submit",
                "public_submit_detected": True,
                "post_code": 400,
            },
            page_url="https://aisotools.com/submit",
        )
        self.assertEqual(status, "broken_submit_surface")
        self.assertIn("valid payload returns", note.lower())

    def test_prioritizes_easy_submit_channels_before_harder_untried_lanes(self):
        ordered = channel_discovery.prioritize_new_channels([
            ("medium-post", "https://example.com/post", "post", "medium"),
            ("easy-submit", "https://example.com/submit", "submit", "easy"),
            ("hard-submit", "https://example.com/hard", "submit", "hard"),
            ("easy-answer", "https://example.com/answer", "answer", "easy"),
        ])
        self.assertEqual([item[0] for item in ordered], ["easy-submit", "easy-answer", "medium-post", "hard-submit"])

    def test_validated_autonomous_submit_host_short_circuits_to_accessible(self):
        status, note = channel_discovery.classify_submission_surface_probe(
            {"probe_status": "ok"},
            page_url="https://www.thenextai.com/submit-ai-tool/",
        )
        self.assertEqual(status, "accessible")
        self.assertIn("confirmed autonomous submit lane", note)

    def test_toolshelf_validated_submit_host_short_circuits_to_accessible(self):
        status, note = channel_discovery.classify_submission_surface_probe(
            {"probe_status": "ok"},
            page_url="https://toolshelf.dev/submit",
        )
        self.assertEqual(status, "accessible")
        self.assertIn("/api/submit", note)

    def test_aigearbase_validated_submit_host_short_circuits_to_accessible(self):
        status, note = channel_discovery.classify_submission_surface_probe(
            {"probe_status": "ok"},
            page_url="https://aigearbase.com/submit",
        )
        self.assertEqual(status, "accessible")
        self.assertIn("math captcha", note.lower())

    def test_login_required_submit_api_overrides_public_form_copy(self):
        status, note = channel_discovery.classify_submission_surface_probe(
            {
                "probe_status": "ok",
                "form_count": 1,
                "input_count": 6,
                "textarea_count": 2,
                "select_count": 2,
                "body_excerpt": "Submit Your AI Tool\nTool Name\nWebsite URL\nCategory\nDescription\nPricing",
                "forms": [
                    {
                        "action": None,
                        "method": None,
                        "control_count": 10,
                        "named_control_count": 10,
                    }
                ],
            },
            {
                "probe_status": "ok",
                "has_network_submission_markers": True,
            },
            {
                "probe_status": "ok",
                "submit_url": "https://www.aipowerstacks.com/api/submit",
                "post_code": 401,
                "auth_required_detected": True,
                "public_submit_detected": False,
            },
            page_url="https://www.aipowerstacks.com/submit",
        )
        self.assertEqual(status, "login_required")
        self.assertIn("requires authentication", note.lower())

    def test_login_interstitial_copy_is_marked_login_required(self):
        status, note = channel_discovery.classify_submission_surface_probe(
            {
                "probe_status": "ok",
                "form_count": 1,
                "input_count": 1,
                "textarea_count": 0,
                "select_count": 0,
                "body_excerpt": "Sign In to List Your AI Tool\nLog In\nCreate Account\nAfter login, you'll return here automatically.",
                "forms": [
                    {
                        "action": None,
                        "method": None,
                        "control_count": 1,
                        "named_control_count": 0,
                    }
                ],
            },
            {
                "probe_status": "ok",
                "has_auth_markers": True,
                "has_network_submission_markers": True,
            },
            {
                "probe_status": "ok",
                "submit_url": "https://www.aipowerstacks.com/api/rss",
                "post_code": 405,
                "public_submit_detected": False,
                "auth_required_detected": False,
            },
            page_url="https://www.aipowerstacks.com/submit",
        )
        self.assertEqual(status, "login_required")
        self.assertIn("authentication", note.lower())

    def test_known_broken_toolsland_host_is_marked_broken(self):
        status, note = channel_discovery.classify_submission_surface_probe(
            {
                "probe_status": "ok",
                "form_count": 1,
                "input_count": 10,
                "textarea_count": 1,
                "select_count": 8,
                "body_excerpt": "Submit your AI tool for free\nTool Name\nWebsite URL\nSelect Product Type\nSelect AI Type",
                "forms": [
                    {
                        "action": None,
                        "method": None,
                        "control_count": 19,
                        "named_control_count": 19,
                    }
                ],
            },
            {
                "probe_status": "ok",
                "has_network_submission_markers": True,
            },
            {
                "probe_status": "partial",
                "submit_url": "https://api.toolsland.ai/graphql",
                "note": "hostname mismatch",
            },
            page_url="https://www.toolsland.ai/submit-ai-tool-free",
        )
        self.assertEqual(status, "broken_submit_surface")
        self.assertIn("tls", note.lower())

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
        self.assertIn("codaone", active)
        self.assertIn("aisotools", active)
        self.assertIn("comeai", active)
        self.assertIn("toolsland", active)
        self.assertIn("aipowerstacks", active)
        self.assertIn("aigearbase", active)


if __name__ == "__main__":
    unittest.main()
