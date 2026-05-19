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

    def test_build_packet_uses_fresh_generator_and_avoids_repeated_openings_within_packet(self):
        report = Path("/tmp/reddit_monitor_2026-05-19_packet.md")
        report.write_text(
            """### 1) Claude Code stuck in approval loop
- URL: https://www.reddit.com/r/ClaudeCode/comments/approval/
- Community: `r/ClaudeCode`
- Freshness: today
- Best RalphWorkflow angle:
  - **approval drag is really a weak stop-condition problem**
- Mention fit: **high**

### 2) Claude Code just shipped a \"run until done\" mode. Upgrade to v2.1.139 for /goal.
- URL: https://www.reddit.com/r/ClaudeCode/comments/goal/
- Community: `r/ClaudeCode`
- Freshness: today
- Best RalphWorkflow angle:
  - **run-until-done only helps if done is bounded, fail-closed, and easy to review**
- Mention fit: **high**
""",
            encoding="utf-8",
        )
        original_already_used = reddit_autopost.already_used
        original_load_recent = reddit_next_window_packet.load_recent_bodies
        try:
            reddit_autopost.already_used = lambda url: False
            reddit_next_window_packet.load_recent_bodies = lambda limit=12: [
                "Honestly the part I'd optimize first is the handoff, not the model stack.\n\n"
                "If the run ends with one readable diff, real checks, and a short note about what still looks sketchy, you can move fast without lying to yourself about the result."
            ]
            packet, entries = reddit_next_window_packet.build_packet(report, max_entries=2)
        finally:
            reddit_autopost.already_used = original_already_used
            reddit_next_window_packet.load_recent_bodies = original_load_recent
        self.assertEqual(len(entries), 2)
        self.assertIn(reddit_autopost.CODEBERG_PRIMARY_URL, entries[0].body)
        self.assertIn(reddit_autopost.CODEBERG_PRIMARY_URL, entries[1].body)
        self.assertNotIn("Honestly the part I'd optimize first is the handoff", entries[0].body)
        self.assertNotEqual(entries[0].body.split("\n\n", 1)[0], entries[1].body.split("\n\n", 1)[0])
        self.assertIn("Posting discipline before using any of these", packet)


if __name__ == "__main__":
    unittest.main()
