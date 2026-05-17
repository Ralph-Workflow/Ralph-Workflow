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


if __name__ == "__main__":
    unittest.main()
