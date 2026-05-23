import unittest
from datetime import datetime

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

    def test_parse_opportunities_supports_inline_angle_and_missing_freshness(self):
        report = """### 1) Claude Code Agent Teams W/ Gemini and Codex
- URL: https://www.reddit.com/r/ClaudeCode/comments/example/
- Community: `r/ClaudeCode`
- Best RalphWorkflow angle:
  - **the weak point is not the model mix; it is the handoff contract and who owns the finish state**
- Mention fit: **medium-high**
"""
        opps = reddit_autopost.parse_opportunities(report)
        self.assertEqual(len(opps), 1)
        self.assertEqual(opps[0].freshness, "during this pass")
        self.assertEqual(
            opps[0].angle,
            "the weak point is not the model mix; it is the handoff contract and who owns the finish state",
        )

    def test_build_comment_adds_codeberg_link_for_high_fit_codex_thread(self):
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
        self.assertIn(reddit_autopost.CODEBERG_PRIMARY_URL, body)
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
        self.assertNotIn(reddit_autopost.CODEBERG_PRIMARY_URL, body)

    def test_concept_cadence_repeats_when_structure_is_semantically_the_same(self):
        previous = (
            "What breaks first for me is confidence in the merged state, not the individual agent runs.\n\n"
            "The painful part is shared boundaries: config/schema/migrations and who owns them.\n\n"
            "So every run ends with a tiny finish receipt: touched areas, checks run, assumptions made, and unresolved risks.\n\n"
            "That is why I built RalphWorkflow.\n\n"
            f"{reddit_autopost.CODEBERG_PRIMARY_URL}"
        )
        candidate = (
            "The biggest failure mode is trust in the merged state, not raw execution speed.\n\n"
            "Shared boundary drift is what hurts: config/schema/migrations and global checks.\n\n"
            "I want a short finish receipt with checks, assumptions, and open questions before I review anything.\n\n"
            "That is basically the Ralph Workflow problem space.\n\n"
            f"{reddit_autopost.CODEBERG_PRIMARY_URL}"
        )
        self.assertTrue(reddit_autopost.concept_cadence_repeats(candidate, [previous]))

    def test_build_comment_keeps_codeberg_link_while_avoiding_recent_cadence(self):
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
            f"{reddit_autopost.CODEBERG_PRIMARY_URL}",
        ]
        body = reddit_autopost.build_comment(opp, recent=recent)
        self.assertIn(reddit_autopost.CODEBERG_PRIMARY_URL, body)
        self.assertFalse(reddit_autopost.concept_cadence_repeats(body, recent))

    def test_build_comment_avoids_reusing_identical_product_cta(self):
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
            f"{reddit_autopost.CODEBERG_PRIMARY_URL}"
        )
        recent = [
            "Previous body.\n\n" + repeated_cta,
            "Another previous body.\n\n" + repeated_cta,
        ]
        body = reddit_autopost.build_comment(opp, recent=recent)
        self.assertIn(reddit_autopost.CODEBERG_PRIMARY_URL, body)
        self.assertNotIn("If the useful part here is \"one tool builds, one checks, then judge the result like a PR,\"", body)
        self.assertFalse(reddit_autopost.github_cta_repeats(body, recent))

    def test_emergency_rewrite_uses_recent_context_and_avoids_banned_opening(self):
        opp = reddit_autopost.Opportunity(
            rank=1,
            title='Claude Code just shipped a "run until done" mode. Upgrade to v2.1.139 for /goal.',
            url="https://www.reddit.com/r/ClaudeCode/comments/example/",
            community="r/ClaudeCode",
            angle="run-until-done only helps if done is bounded, fail-closed, and easy to review",
            freshness="today",
            mention_fit="**medium-low**",
        )
        recent = [
            "Honestly the part I'd optimize first is the handoff, not the model stack.\n\n"
            "If the run ends with one readable diff, real checks, and a short note about what still looks sketchy, you can move fast without lying to yourself about the result.\n\n"
            "Most of the pain is not raw generation. It's stale assumptions, fuzzy ownership, and nobody making the finish easy to review."
        ]
        body = reddit_autopost.build_comment(opp, recent=recent)
        self.assertFalse(reddit_autopost.opening_is_repetitive(body, recent))
        self.assertNotIn("Honestly the part I'd optimize first is the handoff", body)

    def test_opening_family_detects_and_blocks_same_pain_shape(self):
        recent = [
            "Approval drag usually means the workflow has no trustworthy stop point, so the human gets turned into a live safety system.\n\n"
            "A bounded task plus checks and explicit unresolved calls helps more than another toggle."
        ]
        candidate = (
            "Approval mode only feels better when the finish state is easy to judge.\n\n"
            "If the run still ends in fuzzy output, the human is still acting like a safety system."
        )
        self.assertEqual(reddit_autopost.opening_family(recent[0]), "approval_drag")
        self.assertEqual(reddit_autopost.opening_family(candidate), "approval_drag")
        self.assertTrue(reddit_autopost.opening_family_repeats(candidate, recent))

    def test_build_comment_prefers_fresh_opening_family_for_approval_threads(self):
        opp = reddit_autopost.Opportunity(
            rank=1,
            title="Claude Code stuck in approval loop",
            url="https://www.reddit.com/r/ClaudeCode/comments/example/",
            community="r/ClaudeCode",
            angle="approval drag is really a weak stop-condition problem",
            freshness="today",
            mention_fit="**high**",
        )
        recent = [
            "Approval drag usually means the workflow has no trustworthy stop point, so the human gets turned into a live safety system.\n\n"
            "What helps more than another toggle is a bounded task, checks attached at the end, and explicit unresolved calls when the run stops."
        ]
        body = reddit_autopost.build_comment(opp, recent=recent)
        self.assertIn(reddit_autopost.CODEBERG_PRIMARY_URL, body)
        self.assertNotEqual(reddit_autopost.opening_family(body), "approval_drag")
        self.assertTrue(
            "what changed, what passed" in body.lower()
            or "finished code, tested code" in body.lower()
        )

    def test_build_comment_rewrites_run_until_done_threads_away_from_stale_cadence(self):
        opp = reddit_autopost.Opportunity(
            rank=1,
            title='Claude Code just shipped a "run until done" mode. Upgrade to v2.1.139 for /goal.',
            url="https://www.reddit.com/r/ClaudeCode/comments/example/",
            community="r/ClaudeCode",
            angle="run-until-done only helps if done is bounded, fail-closed, and easy to review",
            freshness="today",
            mention_fit="**high**",
        )
        recent = [
            "Honestly the part I'd optimize first is the handoff, not the model stack.\n\n"
            "If the run ends with one readable diff, real checks, and a short note about what still looks sketchy, you can move fast without lying to yourself about the result.\n\n"
            "Most of the pain is not raw generation. It's stale assumptions, fuzzy ownership, and nobody making the finish easy to review."
        ]
        body = reddit_autopost.build_comment(opp, recent=recent)
        self.assertIn(reddit_autopost.CODEBERG_PRIMARY_URL, body)
        self.assertTrue(
            "finished code, tested code" in body.lower()
            or "whether you'd actually merge it" in body.lower()
        )
        self.assertNotIn("If the run ends with one readable diff, real checks", body)
        self.assertFalse(reddit_autopost.body_needs_regeneration(body, recent))

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

    def test_freshness_score_uses_current_reference_date_for_absolute_dates(self):
        reference = datetime(2026, 5, 18, 1, 55)
        self.assertEqual(reddit_autopost.freshness_score("May 17, 2026", reference=reference), 5)
        self.assertEqual(reddit_autopost.freshness_score("May 11, 2026", reference=reference), 4)
        self.assertEqual(reddit_autopost.freshness_score("April 10, 2026", reference=reference), 1)

    def test_freshness_score_treats_same_day_monitor_language_as_fresh(self):
        reference = datetime(2026, 5, 18, 1, 55)
        self.assertEqual(
            reddit_autopost.freshness_score(
                "active same-day page visibility during this pass",
                reference=reference,
            ),
            5,
        )

    def test_choose_opportunity_prefers_fresh_rate_limited_over_stale_fallback(self):
        fresh = reddit_autopost.Opportunity(
            rank=1,
            title="Claude -> Codex -> Claude",
            url="https://www.reddit.com/r/ClaudeCode/comments/fresh/",
            community="`r/ClaudeCode`",
            angle="keep the handoff small",
            freshness="active same-day page visibility during this pass",
            mention_fit="**high**",
        )
        stale = reddit_autopost.Opportunity(
            rank=7,
            title="Older codex thread",
            url="https://www.reddit.com/r/codex/comments/stale/",
            community="`r/codex`",
            angle="reviewable finish matters",
            freshness="Saturday, May 9, 2026",
            mention_fit="**medium**",
        )
        original_load_recent = reddit_autopost.load_recent_post_records
        original_already_used = reddit_autopost.already_used
        try:
            reddit_autopost.load_recent_post_records = lambda hours=24: [
                {
                    "__parsed_timestamp": datetime.now(),
                    "metadata": {"community": "r/ClaudeCode"},
                }
            ]
            reddit_autopost.already_used = lambda url: False
            chosen, state = reddit_autopost.choose_opportunity([fresh, stale])
        finally:
            reddit_autopost.load_recent_post_records = original_load_recent
            reddit_autopost.already_used = original_already_used
        self.assertIsNone(chosen)
        self.assertEqual(state, "fresh_rate_limited")

    def test_posting_gate_reports_next_safe_time_for_global_cooldown(self):
        now = datetime(2026, 5, 18, 0, 30)
        recent_posts = [{"__parsed_timestamp": datetime(2026, 5, 18, 0, 10)}]
        allowed, reason, retry_after, next_safe = reddit_autopost.posting_gate(now, recent_posts)
        self.assertFalse(allowed)
        self.assertEqual(reason, "global_cooldown_active:20m_since_last_post")
        self.assertEqual(retry_after, 25)
        self.assertEqual(next_safe, "2026-05-18T00:55:00")

    def test_posting_gate_reports_next_safe_time_for_volume_guard(self):
        now = datetime(2026, 5, 18, 0, 30)
        recent_posts = [
            {"__parsed_timestamp": datetime(2026, 5, 17, 19, 0)},
            {"__parsed_timestamp": datetime(2026, 5, 17, 21, 0)},
            {"__parsed_timestamp": datetime(2026, 5, 17, 23, 0)},
        ]
        allowed, reason, retry_after, next_safe = reddit_autopost.posting_gate(now, recent_posts)
        self.assertFalse(allowed)
        self.assertEqual(reason, "volume_guard_active:3_posts_in_6h")
        self.assertEqual(retry_after, 31)
        self.assertEqual(next_safe, "2026-05-18T01:01:00")

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
        self.assertIn(reddit_autopost.CODEBERG_PRIMARY_URL, handoff_body)
        self.assertIn(reddit_autopost.CODEBERG_PRIMARY_URL, mixed_team_body)
        self.assertIn("free/open-source", handoff_body)
        self.assertIn("free/open-source", mixed_team_body)

    def test_emergency_rewrite_falls_back_to_safe_codeberg_primary_body(self):
        opp = reddit_autopost.Opportunity(
            rank=1,
            title="Claude Code stuck in approval loop",
            url="https://www.reddit.com/r/ClaudeCode/comments/example/",
            community="r/ClaudeCode",
            angle="approval drag is really a weak stop-condition problem",
            freshness="today",
            mention_fit="**high**",
        )
        recent = [
            "Approval mode stops feeling useful when every pause means another rescue loop instead of a real judgment call.",
            "The session transcript is not the output — the diff plus the test results are the output.",
            "What I set as the approval gate: finished code, a diff I can read in two minutes, and no unresolved decisions longer than a sentence.",
        ]
        body = reddit_autopost.emergency_rewrite(opp, recent=recent)
        self.assertIn(reddit_autopost.CODEBERG_PRIMARY_URL, body)
        self.assertIn("finished code", body.lower())
        self.assertFalse(reddit_autopost.contains_banned_phrase(body))
        self.assertFalse(reddit_autopost.candidate_policy_issues(body, opp))
        self.assertFalse(reddit_autopost.opening_is_repetitive(body, recent))

    def test_choose_opportunity_prefers_medium_plus_fit_over_low_fit_same_day_thread(self):
        medium = reddit_autopost.Opportunity(
            rank=1,
            title="Autonomous Claude Code runs in the new reality.",
            url="https://www.reddit.com/r/ClaudeCode/comments/medium/",
            community="`r/ClaudeCode`",
            angle="bounded autonomy should end in a reviewable handoff",
            freshness="today",
            mention_fit="**medium**",
        )
        low = reddit_autopost.Opportunity(
            rank=5,
            title="Is multi-agent supervision becoming the real job?",
            url="https://www.reddit.com/r/AI_Agents/comments/low/",
            community="`r/AI_Agents`",
            angle="less supervision drag without blind trust",
            freshness="today",
            mention_fit="**low**",
        )
        original_load_recent = reddit_autopost.load_recent_post_records
        original_already_used = reddit_autopost.already_used
        try:
            reddit_autopost.load_recent_post_records = lambda hours=24: []
            reddit_autopost.already_used = lambda url: False
            chosen, state = reddit_autopost.choose_opportunity([low, medium])
        finally:
            reddit_autopost.load_recent_post_records = original_load_recent
            reddit_autopost.already_used = original_already_used
        self.assertEqual(state, "fresh")
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.title, medium.title)

    def test_choose_opportunity_skips_when_only_weak_fit_threads_remain(self):
        low = reddit_autopost.Opportunity(
            rank=5,
            title="Is multi-agent supervision becoming the real job?",
            url="https://www.reddit.com/r/AI_Agents/comments/low/",
            community="`r/AI_Agents`",
            angle="less supervision drag without blind trust",
            freshness="today",
            mention_fit="**low**",
        )
        original_load_recent = reddit_autopost.load_recent_post_records
        original_already_used = reddit_autopost.already_used
        try:
            reddit_autopost.load_recent_post_records = lambda hours=24: []
            reddit_autopost.already_used = lambda url: False
            chosen, state = reddit_autopost.choose_opportunity([low])
        finally:
            reddit_autopost.load_recent_post_records = original_load_recent
            reddit_autopost.already_used = original_already_used
        self.assertIsNone(chosen)
        self.assertEqual(state, "weak_fit_only")

    def test_parse_current_report_shape_finds_live_opportunities(self):
        report = """### 1) Claude Code Agent Teams W/ Gemini and Codex
- URL: https://www.reddit.com/r/ClaudeCode/comments/1tep6dl/claude_code_agent_teams_w_gemini_and_codex/
- Community: `r/ClaudeCode`
- Sentiment: enthusiastic but friction-aware
- Why it fits:
  - real handoff-state pain instead of generic “more agents” hype
- Best RalphWorkflow angle:
  - **the weak point is not the model mix; it is the handoff contract and who owns the finish state**
- Mention fit: **medium-high**

### 2) Autonomous Claude Code runs in the new reality.
- URL: https://www.reddit.com/r/ClaudeCode/comments/1tcngab/autonomous_claude_code_runs_in_the_new_reality/
- Community: `r/ClaudeCode`
- Sentiment: practical, cost-aware, slightly frustrated
- Why it fits:
  - explicit unattended-run thread with real operational constraints
- Best RalphWorkflow angle:
  - **the goal is not autonomy by itself; it is a bounded run that comes back reviewable**
- Mention fit: **medium**
"""
        opps = reddit_autopost.parse_opportunities(report)
        self.assertEqual(len(opps), 2)
        self.assertEqual(opps[0].title, "Claude Code Agent Teams W/ Gemini and Codex")
        self.assertEqual(opps[1].mention_fit, "**medium**")


if __name__ == "__main__":
    unittest.main()
