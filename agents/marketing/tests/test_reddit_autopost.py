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


if __name__ == "__main__":
    unittest.main()
