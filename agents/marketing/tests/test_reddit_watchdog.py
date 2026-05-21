import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.marketing import reddit_watchdog


class RedditWatchdogTests(unittest.TestCase):
    def test_watchdog_generates_next_window_packet_after_cooldown_skip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report_dir = root / "seo-reports"
            report_dir.mkdir(parents=True)
            report = report_dir / "reddit_monitor_2026-05-18_2115.md"
            report.write_text("stub", encoding="utf-8")

            state_path = root / "agents/marketing/logs/reddit_autopost_state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text("{}", encoding="utf-8")

            runs = []

            def fake_run(cmd, capture_output=True, text=True):
                runs.append(cmd)
                if str(cmd[-1]).endswith("reddit_autopost.py"):
                    return type("Proc", (), {
                        "returncode": 0,
                        "stdout": json.dumps({"status": "cooldown_skip"}),
                        "stderr": "",
                    })()
                return type("Proc", (), {
                    "returncode": 0,
                    "stdout": json.dumps({"status": "packet_generated", "entries": 2}),
                    "stderr": "",
                })()

            with patch.object(reddit_watchdog, "ROOT", root), \
                 patch.object(reddit_watchdog, "REPORT_DIR", report_dir), \
                 patch.object(reddit_watchdog, "STATE_PATH", state_path), \
                 patch.object(reddit_watchdog, "AUTOpOST", root / "agents/marketing/reddit_autopost.py"), \
                 patch.object(reddit_watchdog, "NEXT_WINDOW_PACKET", root / "agents/marketing/reddit_next_window_packet.py"), \
                 patch("agents.marketing.reddit_watchdog.report_is_fresh", return_value=True), \
                 patch("agents.marketing.reddit_watchdog.subprocess.run", side_effect=fake_run), \
                 patch("builtins.print") as mock_print:
                rc = reddit_watchdog.main()

            self.assertEqual(rc, 0)
            self.assertEqual(len(runs), 2)
            printed = json.loads(mock_print.call_args[0][0])
            self.assertEqual(printed["autopost"]["status"], "cooldown_skip")
            self.assertEqual(printed["next_window_packet"]["status"], "packet_generated")

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
