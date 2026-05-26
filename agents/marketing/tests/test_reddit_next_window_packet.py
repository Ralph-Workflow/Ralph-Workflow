import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agents.marketing import reddit_autopost, reddit_next_window_packet


class RedditNextWindowPacketTests(unittest.TestCase):
    def test_main_skips_packet_generation_when_reddit_execution_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / "drafts"
            log_dir = tmp / "logs"
            drafts_dir.mkdir()
            log_dir.mkdir()
            status_path = log_dir / "reddit_execution_status_latest.json"
            status_path.write_text(json.dumps({
                "status": "network_security_blocked",
                "blocking_reason": "Reddit login/posting is blocked.",
            }), encoding="utf-8")

            with patch.object(reddit_next_window_packet, "DRAFTS_DIR", drafts_dir), \
                 patch.object(reddit_next_window_packet, "LATEST_PATH", drafts_dir / "reddit_next_window_packets_latest.md"), \
                 patch.object(reddit_next_window_packet, "REDDIT_EXECUTION_STATUS_PATH", status_path):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    rc = reddit_next_window_packet.main()

            payload = json.loads(stdout.getvalue())

        self.assertEqual(rc, 0)
        self.assertEqual(payload["status"], "channel_blocked_skip")
        self.assertEqual(payload["entries"], 0)
        self.assertEqual(payload["paths"], [])
        self.assertFalse((drafts_dir / "reddit_next_window_packets_latest.md").exists())

    def test_main_reports_no_actionable_entries_truthfully(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / "drafts"
            log_dir = tmp / "logs"
            drafts_dir.mkdir()
            log_dir.mkdir()
            report = tmp / "reddit_monitor_latest.md"
            report.write_text("No opportunities here.\n", encoding="utf-8")

            with patch.object(reddit_next_window_packet, "DRAFTS_DIR", drafts_dir), \
                 patch.object(reddit_next_window_packet, "LATEST_PATH", drafts_dir / "reddit_next_window_packets_latest.md"), \
                 patch.object(reddit_next_window_packet, "REDDIT_EXECUTION_STATUS_PATH", log_dir / "reddit_execution_status_latest.json"), \
                 patch.object(reddit_next_window_packet.reddit_autopost, "latest_report", return_value=report), \
                 patch.object(reddit_next_window_packet, "build_packet", return_value=("# empty\n", [])):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    rc = reddit_next_window_packet.main()

            payload = json.loads(stdout.getvalue())
            latest_exists = (drafts_dir / "reddit_next_window_packets_latest.md").exists()

        self.assertEqual(rc, 0)
        self.assertEqual(payload["status"], "no_actionable_entries")
        self.assertEqual(payload["entries"], 0)
        self.assertTrue(latest_exists)

    def test_browser_session_ready_does_not_force_channel_block_skip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            drafts_dir = tmp / "drafts"
            log_dir = tmp / "logs"
            drafts_dir.mkdir()
            log_dir.mkdir()
            status_path = log_dir / "reddit_execution_status_latest.json"
            status_path.write_text(json.dumps({
                "status": "browser_session_ready",
            }), encoding="utf-8")
            report = tmp / "reddit_monitor_latest.md"
            report.write_text("usable report\n", encoding="utf-8")

            with patch.object(reddit_next_window_packet, "DRAFTS_DIR", drafts_dir), \
                 patch.object(reddit_next_window_packet, "LATEST_PATH", drafts_dir / "reddit_next_window_packets_latest.md"), \
                 patch.object(reddit_next_window_packet, "REDDIT_EXECUTION_STATUS_PATH", status_path), \
                 patch.object(reddit_next_window_packet.reddit_autopost, "latest_report", return_value=report), \
                 patch.object(reddit_next_window_packet, "build_packet", return_value=("# packet\n", [object()])):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    rc = reddit_next_window_packet.main()

            payload = json.loads(stdout.getvalue())

        self.assertEqual(rc, 0)
        self.assertEqual(payload["status"], "packet_generated")

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

    def test_build_packet_prefers_clean_structural_body_when_available(self):
        report = Path("/tmp/reddit_monitor_2026-05-22_structural.md")
        report.write_text(
            """### 1) Claude Code stuck in approval loop
- URL: https://www.reddit.com/r/ClaudeCode/comments/approval/
- Community: `r/ClaudeCode`
- Freshness: today
- Best RalphWorkflow angle:
  - **approval drag is really a weak stop-condition problem**
- Mention fit: **high**
""",
            encoding="utf-8",
        )
        original_already_used = reddit_autopost.already_used
        original_load_recent = reddit_next_window_packet.load_recent_bodies
        original_load_structural = reddit_next_window_packet.load_structural_bodies
        structural_body = (
            "Ralph Workflow is the operating system for autonomous coding: a free and open-source "
            "composable loop framework and AI orchestrator. It ships with a strong default workflow, "
            "aims to leave finished code and tested code ready to review, and points people here: "
            f"{reddit_autopost.CODEBERG_PRIMARY_URL}"
        )
        try:
            reddit_autopost.already_used = lambda url: False
            reddit_next_window_packet.load_recent_bodies = lambda limit=12: []
            reddit_next_window_packet.load_structural_bodies = lambda: {
                "question_opening": structural_body
            }
            packet, entries = reddit_next_window_packet.build_packet(report, max_entries=1)
        finally:
            reddit_autopost.already_used = original_already_used
            reddit_next_window_packet.load_recent_bodies = original_load_recent
            reddit_next_window_packet.load_structural_bodies = original_load_structural
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].body, structural_body)
        self.assertIn("operating system for autonomous coding", entries[0].body)
        self.assertIn("Claude Code stuck in approval loop", packet)


if __name__ == "__main__":
    unittest.main()
