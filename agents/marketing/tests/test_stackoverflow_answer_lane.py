import unittest

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

        self.assertIn("Primary repo: [Codeberg]", answer)
        self.assertNotIn("GitHub mirror", answer)


if __name__ == "__main__":
    unittest.main()
