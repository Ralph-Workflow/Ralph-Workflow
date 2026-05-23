import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agents.marketing import stackoverflow_answer_lane


class StackOverflowAnswerLaneTests(unittest.TestCase):
    def test_billing_question_is_not_draft_worthy(self):
        question = {
            "title": "Are VS Code Copilot Agent Debug Log Token Counts the Exact Billing Metrics?",
            "body_snippet": "I want to know whether debug log token counts exactly match billing and quota.",
            "answers": 0,
            "accepted_answer": "",
            "tags": ["github-copilot"],
        }
        question["pain_family"] = stackoverflow_answer_lane.classify_pain_family(question, question)
        question["score"] = stackoverflow_answer_lane.score_question(question, question)

        self.assertEqual(question["pain_family"], "general")
        self.assertFalse(stackoverflow_answer_lane.is_draft_worthy(question))
        self.assertLess(question["score"], 2.4)

    def test_reliability_workflow_question_is_draft_worthy(self):
        question = {
            "title": "How should I structure autonomous AI agent workflows for production reliability in a TypeScript/Next.js fintech platform?",
            "body_snippet": "I need review, verification, checkpoints, and a way to know when an unattended agent run is actually done.",
            "answers": 0,
            "accepted_answer": "",
            "tags": ["openai-api"],
        }
        question["pain_family"] = stackoverflow_answer_lane.classify_pain_family(question, question)
        question["score"] = stackoverflow_answer_lane.score_question(question, question)

        self.assertIn(question["pain_family"], {"verification-review", "workflow-orchestration", "unattended-runs"})
        self.assertTrue(stackoverflow_answer_lane.is_draft_worthy(question))
        self.assertGreaterEqual(question["score"], 2.4)

    def test_answer_blurb_prefers_codeberg_primary(self):
        question = {
            "title": "How do I verify an autonomous coding agent output before review?",
            "body_snippet": "Need verification and review before merging.",
        }
        answer = stackoverflow_answer_lane.draft_answer(question, question)

        self.assertNotIn("Primary repo", answer)
        self.assertNotIn("GitHub mirror", answer)

    def test_answer_stays_stackoverflow_native(self):
        question = {
            "title": "How should I structure autonomous AI agent workflows for production reliability?",
            "body_snippet": "Need checkpoints, verification, and reviewable unattended runs.",
        }
        answer = stackoverflow_answer_lane.draft_answer(question, question)

        self.assertNotIn("What type of task", answer)
        self.assertFalse(answer.strip().endswith("?"))

    def test_load_recent_drafted_question_urls_reads_recent_drafts(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft_dir = Path(tmp)
            draft = draft_dir / "so_answer_2026-05-23_example.md"
            draft.write_text(
                "# StackOverflow Answer Draft\n\n**Question:** Example\n**URL:** https://stackoverflow.com/questions/123/example\n",
                encoding="utf-8",
            )
            now = datetime.fromtimestamp(draft.stat().st_mtime)
            with patch.object(stackoverflow_answer_lane, "DRAFT_DIR", draft_dir):
                urls = stackoverflow_answer_lane.load_recent_drafted_question_urls(now=now)

        self.assertEqual(urls, {"https://stackoverflow.com/questions/123/example"})

    def test_main_skips_recent_duplicate_candidate(self):
        question = {
            "title": "How should I structure autonomous AI agent workflows for production reliability in a TypeScript/Next.js fintech platform?",
            "url": "https://stackoverflow.com/questions/79942291/how-should-i-structure-autonomous-ai-agent-workflows-for-production-reliability",
            "tags": ["openai-api"],
            "answers": 0,
            "votes": 0,
            "body_snippet": "Need checkpoints, verification, and reviewable unattended runs.",
        }
        with patch.object(stackoverflow_answer_lane, "SO_SEARCH_SPECS", [{"label": "workflow"}]), \
             patch.object(stackoverflow_answer_lane, "so_search_site", return_value=[question]), \
             patch.object(stackoverflow_answer_lane, "fetch_question_detail", return_value=question), \
             patch.object(stackoverflow_answer_lane, "load_recent_drafted_question_urls", return_value={question["url"]}), \
             patch.object(stackoverflow_answer_lane, "append_outreach_log"):
            rc = stackoverflow_answer_lane.main()

        self.assertEqual(rc, 0)
        payload = stackoverflow_answer_lane.json.loads(stackoverflow_answer_lane.SO_LOG.read_text(encoding="utf-8"))
        self.assertEqual(payload["drafts_created"], 0)
        self.assertEqual(payload["skipped_existing_drafts"], 1)


if __name__ == "__main__":
    unittest.main()
