import unittest
from pathlib import Path

from agents.marketing import reddit_watchdog


class RedditWatchdogTests(unittest.TestCase):
    def test_should_skip_report_for_posted_report(self):
        report = Path("/tmp/reddit_monitor_2026-05-17_2115.md")
        state = {
            "last_report": str(report),
            "last_attempt_status": "posted",
        }
        self.assertTrue(reddit_watchdog.should_skip_report(report, state))

    def test_should_retry_same_report_after_cooldown_skip(self):
        report = Path("/tmp/reddit_monitor_2026-05-17_2115.md")
        state = {
            "last_report": str(report),
            "last_attempt_status": "cooldown_skip",
        }
        self.assertFalse(reddit_watchdog.should_skip_report(report, state))

    def test_should_retry_same_report_after_fresh_rate_limit(self):
        report = Path("/tmp/reddit_monitor_2026-05-17_2115.md")
        state = {
            "last_report": str(report),
            "last_attempt_status": "fresh_opportunity_rate_limited",
        }
        self.assertFalse(reddit_watchdog.should_skip_report(report, state))


if __name__ == "__main__":
    unittest.main()
