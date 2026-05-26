import unittest
from unittest.mock import Mock, patch

from agents.unblocker import run


class UnblockerTests(unittest.TestCase):
    def test_choose_next_actions_prefers_useful_history(self):
        channel = {
            "allowed_actions": ["check_auth_status", "verify_browser_readiness", "prepare_manual_api_key_request"],
            "preferred_next_actions": ["check_auth_status", "verify_browser_readiness", "prepare_manual_api_key_request"],
            "attempt_history": [
                {"action": "check_auth_status", "status": "blocked"},
                {"action": "verify_browser_readiness", "status": "useful"},
                {"action": "verify_browser_readiness", "status": "useful"},
            ],
        }
        actions = run.choose_next_actions(channel)
        self.assertEqual(actions[0], "verify_browser_readiness")

    def test_verify_browser_readiness_reports_blocked_when_none_found(self):
        channel = {"browser_requirements": {"extension_notes": []}}
        with patch("agents.unblocker.run.shutil.which", return_value=None):
            result = run.verify_browser_readiness(channel)
        self.assertEqual(result.status, "blocked")

    def test_prepare_manual_account_setup_returns_steps(self):
        channel = {"manual_prerequisites": ["Create account", "Confirm email"]}
        result = run.prepare_manual_account_setup(channel)
        self.assertEqual(result.status, "useful")
        self.assertEqual(result.details["steps"], ["Create account", "Confirm email"])

    def test_run_channel_appends_attempt_history(self):
        channel = {
            "id": "devto",
            "name": "dev.to",
            "status": "blocked",
            "allowed_actions": ["prepare_manual_account_setup"],
            "preferred_next_actions": ["prepare_manual_account_setup"],
            "manual_prerequisites": ["Create account"],
            "attempt_history": [],
        }
        result = run.run_channel(channel)
        self.assertEqual(result["recommendation"], "continue_legitimate_unblock")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["exact_blocker"], "Manual prerequisite pending: Create account")
        self.assertEqual(len(channel["attempt_history"]), 1)
        self.assertIn("last_review", channel)

    def test_github_auth_status_not_logged_in_is_blocked(self):
        channel = {"id": "github-write"}
        completed = Mock(stdout="", stderr="You are not logged into any GitHub hosts. To log in, run: gh auth login")
        with patch("agents.unblocker.run.shutil.which", return_value="/usr/bin/gh"):
            with patch("agents.unblocker.run.command_output", return_value=completed):
                result = run.check_auth_status(channel)
        self.assertEqual(result.status, "blocked")
        self.assertIn("not logged into any host", result.summary)


if __name__ == "__main__":
    unittest.main()
