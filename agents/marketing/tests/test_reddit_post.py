import json
import tempfile
import unittest
from pathlib import Path

from agents.marketing import reddit_post


class RedditPostValidationTests(unittest.TestCase):
    def setUp(self):
        self._original_log = reddit_post.REDDIT_LOG_JSONL
        self._tmpdir = tempfile.TemporaryDirectory()
        reddit_post.REDDIT_LOG_JSONL = Path(self._tmpdir.name) / "reddit_posts.jsonl"

    def tearDown(self):
        reddit_post.REDDIT_LOG_JSONL = self._original_log
        self._tmpdir.cleanup()

    def _write_posts(self, bodies):
        rows = [
            {"timestamp": f"2026-05-20T00:0{i}:00", "body": body}
            for i, body in enumerate(bodies)
        ]
        reddit_post.REDDIT_LOG_JSONL.write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n",
            encoding="utf-8",
        )

    def test_validate_body_rejects_opening_reused_from_last_ten_posts(self):
        repeated_opening = "The overnight run question is usually not 'which agent should run longer' — it is 'what will I actually be able to review in the morning.'"
        self._write_posts([
            repeated_opening + "\n\nOlder body one.",
            "Different opener\n\nOlder body two.",
        ])
        ok, reason = reddit_post.validate_body(repeated_opening + "\n\nFresh second paragraph.")
        self.assertFalse(ok)
        self.assertIn("opening reused", reason)

    def test_validate_body_rejects_recent_cadence_match(self):
        previous = (
            "What breaks first for me is confidence in the merged state, not the individual agent runs.\n\n"
            "The painful part is shared boundaries: config/schema/migrations and who owns them.\n\n"
            "So every run ends with a tiny finish receipt: touched areas, checks run, assumptions made, and unresolved risks.\n\n"
            "That is why I built RalphWorkflow.\n\n"
            "https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        )
        candidate = (
            "The biggest failure mode is trust in the merged state, not raw execution speed.\n\n"
            "Shared boundary drift is what hurts: config/schema/migrations and global checks.\n\n"
            "I want a short finish receipt with checks, assumptions, and open questions before I review anything.\n\n"
            "That is basically the Ralph Workflow problem space.\n\n"
            "https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        )
        self._write_posts([previous])
        ok, reason = reddit_post.validate_body(candidate)
        self.assertFalse(ok)
        self.assertIn("body cadence matches", reason)

    def test_validate_body_allows_fresh_body(self):
        self._write_posts([
            "Approval mode works until 2am when you're still clicking approve.\n\nNeed a finish contract.",
        ])
        candidate = (
            "The worst overnight miss for me was not a crash. It was a quiet run that looked fine until I opened the repo.\n\n"
            "Now I care more about the morning proof bundle than the chat transcript.\n\n"
            "If a tool cannot leave changed files, checks, and a short list of unresolved calls, I do not trust it unattended."
        )
        ok, reason = reddit_post.validate_body(candidate)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")


if __name__ == "__main__":
    unittest.main()
