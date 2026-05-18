import unittest
from pathlib import Path

from agents.marketing import reddit_autopost, reddit_next_window_packet


class RedditNextWindowPacketTests(unittest.TestCase):
    def test_build_packet_prefers_unused_medium_plus_fit_threads(self):
        report = Path("/tmp/reddit_monitor_2026-05-18_2115.md")
        report.write_text(
            """### 1) Autonomous Claude Code runs in the new reality.
- URL: https://www.reddit.com/r/ClaudeCode/comments/one/
- Community: `r/ClaudeCode`
- Freshness: today
- Best RalphWorkflow angle:
  - **autonomy only matters if the run stays bounded and reviewable**
- Mention fit: **medium**

### 2) Worktrees in Claude Code Desktop App
- URL: https://www.reddit.com/r/ClaudeCode/comments/two/
- Community: `r/ClaudeCode`
- Freshness: today
- Best RalphWorkflow angle:
  - **worktrees solve collision, not the finish surface**
- Mention fit: **very low**
""",
            encoding="utf-8",
        )
        original_already_used = reddit_autopost.already_used
        try:
            reddit_autopost.already_used = lambda url: False
            packet, entries = reddit_next_window_packet.build_packet(report)
        finally:
            reddit_autopost.already_used = original_already_used
        self.assertEqual(len(entries), 1)
        self.assertIn("Autonomous Claude Code runs in the new reality.", packet)
        self.assertNotIn("Worktrees in Claude Code Desktop App", packet)
        self.assertIn("when-unattended-coding-fits.md", packet)


if __name__ == "__main__":
    unittest.main()
