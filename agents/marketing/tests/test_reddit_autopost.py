import unittest

from agents.marketing import reddit_autopost


class RedditAutopostTests(unittest.TestCase):
    def test_parse_opportunities_captures_mention_fit(self):
        report = """### 1) Trust Codex?
- URL: https://www.reddit.com/r/codex/comments/example/
- Community: `r/codex`
- Freshness: published today
- Recommended angle:
  - trust the process, not the model
- Mention fit: **medium-high**
"""
        opps = reddit_autopost.parse_opportunities(report)
        self.assertEqual(len(opps), 1)
        self.assertEqual(opps[0].mention_fit, "**medium-high**")

    def test_build_comment_adds_github_link_for_high_fit_codex_thread(self):
        opp = reddit_autopost.Opportunity(
            rank=1,
            title="How many of you Trust Codex?",
            url="https://www.reddit.com/r/codex/comments/example/",
            community="r/codex",
            angle="process > trust",
            freshness="today",
            mention_fit="**high**",
        )
        body = reddit_autopost.build_comment(opp, recent=[])
        self.assertIn(reddit_autopost.GITHUB_MIRROR_URL, body)
        self.assertIn("free/open-source", body)
        self.assertIn("your own machine", body)

    def test_build_comment_stays_unlinked_for_generic_thread(self):
        opp = reddit_autopost.Opportunity(
            rank=1,
            title="Best AI workflow?",
            url="https://www.reddit.com/r/AI_Agents/comments/example/",
            community="r/AI_Agents",
            angle="general workflow advice",
            freshness="today",
            mention_fit="**medium**",
        )
        body = reddit_autopost.build_comment(opp, recent=[])
        self.assertNotIn(reddit_autopost.GITHUB_MIRROR_URL, body)

    def test_concept_cadence_repeats_when_structure_is_semantically_the_same(self):
        previous = (
            "What breaks first for me is confidence in the merged state, not the individual agent runs.\n\n"
            "The painful part is shared boundaries: config/schema/migrations and who owns them.\n\n"
            "So every run ends with a tiny finish receipt: touched areas, checks run, assumptions made, and unresolved risks.\n\n"
            "That is why I built RalphWorkflow.\n\n"
            f"{reddit_autopost.GITHUB_MIRROR_URL}"
        )
        candidate = (
            "The biggest failure mode is trust in the merged state, not raw execution speed.\n\n"
            "Shared boundary drift is what hurts: config/schema/migrations and global checks.\n\n"
            "I want a short finish receipt with checks, assumptions, and open questions before I review anything.\n\n"
            "That is basically the Ralph Workflow problem space.\n\n"
            f"{reddit_autopost.GITHUB_MIRROR_URL}"
        )
        self.assertTrue(reddit_autopost.concept_cadence_repeats(candidate, [previous]))

    def test_build_comment_keeps_github_link_while_avoiding_recent_cadence(self):
        opp = reddit_autopost.Opportunity(
            rank=1,
            title="People running 2–5 coding agents: what actually breaks first for you?",
            url="https://www.reddit.com/r/ClaudeCode/comments/example/",
            community="r/ClaudeCode",
            angle="the hard part is coming back to a result you can reconstruct and trust",
            freshness="today",
            mention_fit="**high**",
        )
        recent = [
            "What breaks first for me is confidence in the merged state, not the individual agent runs.\n\n"
            "The painful part the next morning is shared boundaries and merged-state checks.\n\n"
            "So every run ends with a tiny finish receipt instead of a heroic transcript.\n\n"
            "I built RalphWorkflow around that morning-after problem.\n\n"
            f"{reddit_autopost.GITHUB_MIRROR_URL}",
        ]
        body = reddit_autopost.build_comment(opp, recent=recent)
        self.assertIn(reddit_autopost.GITHUB_MIRROR_URL, body)
        self.assertFalse(reddit_autopost.concept_cadence_repeats(body, recent))

    def test_build_comment_avoids_reusing_identical_github_cta(self):
        opp = reddit_autopost.Opportunity(
            rank=1,
            title="Claude -> Codex -> Claude",
            url="https://www.reddit.com/r/ClaudeCode/comments/example/",
            community="r/ClaudeCode",
            angle="use separate build and review phases",
            freshness="today",
            mention_fit="**high**",
        )
        repeated_cta = (
            "If the useful part here is \"one tool builds, one checks, then judge the result like a PR,\" RalphWorkflow is my free/open-source take on that loop. "
            "It keeps the agents on your own machine and pushes toward reviewable output rather than another long transcript.\n\n"
            f"{reddit_autopost.GITHUB_MIRROR_URL}"
        )
        recent = [
            "Previous body.\n\n" + repeated_cta,
            "Another previous body.\n\n" + repeated_cta,
        ]
        body = reddit_autopost.build_comment(opp, recent=recent)
        self.assertIn(reddit_autopost.GITHUB_MIRROR_URL, body)
        self.assertNotIn("If the useful part here is \"one tool builds, one checks, then judge the result like a PR,\"", body)
        self.assertFalse(reddit_autopost.github_cta_repeats(body, recent))

    def test_detect_category_separates_handoff_and_mixed_team_threads(self):
        self.assertEqual(reddit_autopost.detect_category("Claude -> Codex -> Claude"), "handoff")
        self.assertEqual(
            reddit_autopost.detect_category("Claude Code Agent Teams W/ Gemini and Codex"),
            "mixed_team",
        )
        self.assertEqual(
            reddit_autopost.detect_category("People running 2–5 coding agents: what actually breaks first for you?"),
            "breaks_first",
        )

    def test_live_high_fit_threads_generate_distinct_bodies(self):
        handoff = reddit_autopost.Opportunity(
            rank=2,
            title="Claude -> Codex -> Claude",
            url="https://www.reddit.com/r/ClaudeCode/comments/handoff/",
            community="r/ClaudeCode",
            angle="cap review loops and keep the handoff small",
            freshness="today",
            mention_fit="**high**",
        )
        mixed_team = reddit_autopost.Opportunity(
            rank=3,
            title="Claude Code Agent Teams W/ Gemini and Codex",
            url="https://www.reddit.com/r/ClaudeCode/comments/team/",
            community="r/ClaudeCode",
            angle="stable handoff contract > clever choreography",
            freshness="today",
            mention_fit="**high**",
        )
        handoff_body = reddit_autopost.build_comment(handoff, recent=[])
        mixed_team_body = reddit_autopost.build_comment(mixed_team, recent=[])
        self.assertNotEqual(handoff_body, mixed_team_body)
        self.assertIn(reddit_autopost.GITHUB_MIRROR_URL, handoff_body)
        self.assertIn(reddit_autopost.GITHUB_MIRROR_URL, mixed_team_body)
        self.assertIn("free/open-source", handoff_body)
        self.assertIn("free/open-source", mixed_team_body)


if __name__ == "__main__":
    unittest.main()
