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

    def test_fintech_reliability_answer_is_question_specific(self):
        question = {
            "title": "How should I structure autonomous AI agent workflows for production reliability in a TypeScript/Next.js fintech platform?",
            "body_snippet": "Need webhook retries, observability, safe deploys, and a way to prevent cascading failures between agents.",
        }
        answer = stackoverflow_answer_lane.draft_answer(question, question)

        self.assertIn("queue-backed workers", answer)
        self.assertIn("idempotency", answer)
        self.assertIn("correlation ID", answer)
        self.assertIn("canary", answer)

    def test_useful_results_question_is_draft_worthy(self):
        question = {
            "title": "How can I get more useful results from ai coding agents?",
            "body_snippet": "Claude Code gets mixed results and I want a workflow with analysis, design, implementation, review, and verification instead of babysitting every step.",
            "answers": 0,
            "accepted_answer": "",
            "tags": ["claude-code", "artificial-intelligence"],
        }
        question["pain_family"] = stackoverflow_answer_lane.classify_pain_family(question, question)
        question["score"] = stackoverflow_answer_lane.score_question(question, question)

        self.assertIn(question["pain_family"], {"verification-review", "workflow-orchestration"})
        self.assertTrue(stackoverflow_answer_lane.is_draft_worthy(question))
        self.assertGreaterEqual(question["score"], 2.4)

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

    def test_find_recent_draft_for_url_returns_matching_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft_dir = Path(tmp)
            draft = draft_dir / "so_answer_2026-05-23_example.md"
            draft.write_text(
                "# StackOverflow Answer Draft\n\n**Question:** Example\n**URL:** https://stackoverflow.com/questions/123/example\n\n---\n\nBody",
                encoding="utf-8",
            )
            now = datetime.fromtimestamp(draft.stat().st_mtime)
            with patch.object(stackoverflow_answer_lane, "DRAFT_DIR", draft_dir):
                found = stackoverflow_answer_lane.find_recent_draft_for_url("https://stackoverflow.com/questions/123/example", now=now)

        self.assertEqual(found, draft)

    def test_main_skips_recent_duplicate_candidate(self):
        question = {
            "title": "How should I structure autonomous AI agent workflows for production reliability in a TypeScript/Next.js fintech platform?",
            "url": "https://stackoverflow.com/questions/79942291/how-should-i-structure-autonomous-ai-agent-workflows-for-production-reliability",
            "tags": ["openai-api"],
            "answers": 0,
            "votes": 0,
            "body_snippet": "Need checkpoints, verification, and reviewable unattended runs.",
        }
        with tempfile.TemporaryDirectory() as tmp:
            draft_dir = Path(tmp) / "drafts"
            draft_dir.mkdir(parents=True, exist_ok=True)
            draft = draft_dir / "so_answer_2026-05-23_example.md"
            draft.write_text(
                "# StackOverflow Answer Draft\n\n**Question:** Example\n**URL:** https://stackoverflow.com/questions/79942291/how-should-i-structure-autonomous-ai-agent-workflows-for-production-reliability\n\n---\n\nReusable body",
                encoding="utf-8",
            )
            packet_path = Path(tmp) / "stackoverflow_answer_handoff_packet_latest.md"
            log_path = Path(tmp) / "stackoverflow_answer_lane_latest.json"
            with patch.object(stackoverflow_answer_lane, "SO_SEARCH_SPECS", [{"label": "workflow"}]), \
                 patch.object(stackoverflow_answer_lane, "SO_LOG", log_path), \
                 patch.object(stackoverflow_answer_lane, "so_search_site", return_value=[question]), \
                 patch.object(stackoverflow_answer_lane, "fetch_question_detail", return_value=question), \
                 patch.object(stackoverflow_answer_lane, "load_recent_drafted_question_urls", return_value={question["url"]}), \
                 patch.object(stackoverflow_answer_lane, "find_recent_draft_for_url", return_value=draft), \
                 patch.object(stackoverflow_answer_lane, "HANDOFF_PACKET_LATEST", packet_path), \
                 patch.object(stackoverflow_answer_lane, "append_outreach_log"):
                rc = stackoverflow_answer_lane.main()
            payload = stackoverflow_answer_lane.json.loads(log_path.read_text(encoding="utf-8"))
            packet_exists = packet_path.exists()

        self.assertEqual(rc, 0)
        self.assertEqual(payload["drafts_created"], 0)
        self.assertEqual(payload["skipped_existing_drafts"], 1)
        self.assertEqual(payload["reused_existing_draft"]["draft_file"], str(draft))
        self.assertTrue(packet_exists)

    def test_main_preserves_previous_state_when_rate_limited(self):
        previous = {
            "generated_at": "2026-05-23T16:34:38.381978",
            "top_questions": [{"title": "Earlier question", "url": "https://stackoverflow.com/questions/1/example"}],
            "drafts_created": 0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "stackoverflow_answer_lane_latest.json"
            log_path.write_text(stackoverflow_answer_lane.json.dumps(previous), encoding="utf-8")
            def rate_limited_search(_spec):
                stackoverflow_answer_lane.SEARCH_RUNTIME["rate_limited"] = True
                stackoverflow_answer_lane.SEARCH_RUNTIME["errors"] = [{"label": "workflow", "code": 429, "error": "HTTP Error 429: Too Many Requests"}]
                return []

            with patch.object(stackoverflow_answer_lane, "SO_LOG", log_path), \
                 patch.object(stackoverflow_answer_lane, "SO_SEARCH_SPECS", [{"label": "workflow"}]), \
                 patch.object(stackoverflow_answer_lane, "so_search_site", side_effect=rate_limited_search), \
                 patch.object(stackoverflow_answer_lane, "append_outreach_log"):
                rc = stackoverflow_answer_lane.main()
                payload = stackoverflow_answer_lane.json.loads(log_path.read_text(encoding="utf-8"))

        self.assertEqual(rc, 0)
        self.assertEqual(payload["status"], "rate_limited_reused_previous")
        self.assertTrue(payload["rate_limited"])
        self.assertTrue(payload["cooldown_active"])
        self.assertIsNotNone(payload["next_retry_at"])
        self.assertEqual(payload["top_questions"], previous["top_questions"])

    def test_main_respects_active_rate_limit_cooldown(self):
        previous = {
            "generated_at": "2026-05-24T04:49:56.677445",
            "status": "rate_limited_reused_previous",
            "rate_limited": True,
            "cooldown_active": True,
            "next_retry_at": "2026-05-24T10:49:56.677445",
            "top_questions": [{"title": "Earlier question", "url": "https://stackoverflow.com/questions/1/example"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "stackoverflow_answer_lane_latest.json"
            log_path.write_text(stackoverflow_answer_lane.json.dumps(previous), encoding="utf-8")
            with patch.object(stackoverflow_answer_lane, "SO_LOG", log_path), \
                 patch.object(stackoverflow_answer_lane, "SO_SEARCH_SPECS", [{"label": "workflow"}]), \
                 patch.object(stackoverflow_answer_lane, "so_search_site") as mocked_search:
                rc = stackoverflow_answer_lane.main()
                payload = stackoverflow_answer_lane.json.loads(log_path.read_text(encoding="utf-8"))

        self.assertEqual(rc, 0)
        mocked_search.assert_not_called()
        self.assertEqual(payload["status"], "rate_limit_cooldown_reused_previous")
        self.assertTrue(payload["cooldown_active"])
        self.assertEqual(payload["top_questions"], previous["top_questions"])

    def test_main_stops_search_after_strong_early_candidate(self):
        question = {
            "title": "How should I structure autonomous AI agent workflows for production reliability in a TypeScript/Next.js fintech platform?",
            "url": "https://stackoverflow.com/questions/79942291/how-should-i-structure-autonomous-ai-agent-workflows-for-production-reliability",
            "tags": ["openai-api"],
            "answers": 0,
            "votes": 0,
            "body_snippet": "Need checkpoints, verification, and reviewable unattended runs.",
        }
        with tempfile.TemporaryDirectory() as tmp:
            draft_dir = Path(tmp) / "drafts"
            draft_dir.mkdir(parents=True, exist_ok=True)
            log_path = Path(tmp) / "stackoverflow_answer_lane_latest.json"
            packet_path = Path(tmp) / "stackoverflow_answer_handoff_packet_latest.md"
            search_calls = []

            def fake_search(spec):
                search_calls.append(spec["label"])
                return [dict(question)]

            with patch.object(stackoverflow_answer_lane, "SO_LOG", log_path), \
                 patch.object(stackoverflow_answer_lane, "SO_SEARCH_SPECS", [{"label": "production-reliability"}, {"label": "claude-autonomous"}]), \
                 patch.object(stackoverflow_answer_lane, "DRAFT_DIR", draft_dir), \
                 patch.object(stackoverflow_answer_lane, "HANDOFF_PACKET_LATEST", packet_path), \
                 patch.object(stackoverflow_answer_lane, "so_search_site", side_effect=fake_search), \
                 patch.object(stackoverflow_answer_lane, "fetch_question_detail", return_value=dict(question)), \
                 patch.object(stackoverflow_answer_lane, "append_outreach_log"):
                rc = stackoverflow_answer_lane.main()
                payload = stackoverflow_answer_lane.json.loads(log_path.read_text(encoding="utf-8"))

        self.assertEqual(rc, 0)
        self.assertEqual(search_calls, ["production-reliability"])
        self.assertEqual(payload["total_questions_found"], 1)


if __name__ == "__main__":
    unittest.main()
