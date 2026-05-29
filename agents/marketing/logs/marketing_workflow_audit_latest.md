# Marketing Workflow Audit

- Generated: 2026-05-29T01:41:35.292438
- Current bottleneck: **conversion_to_free_use**
- Owned articles logged: **6**
- Reddit posts analyzed: **3**

## Why this is the bottleneck
- Owned content and outreach exist, but repo/public adoption signals are still low.
- Codeberg is the primary repo, so primary-repo movement matters more than mirror vanity metrics.
- GitHub mirror adoption is also flat, which reinforces that activity is not converting anywhere meaningful yet.
- Historical Reddit repetition is still on record, but Reddit is blocked from this environment, so do not spend this run rewriting a suspended channel.
- The most recent runtime activity was distribution_lane_switch, but the latest meaningful external/replacement execution remains distribution_architecture_repair_plus_blog_deploy; do not confuse follow-through with fresh outcome-bearing distribution.
- The active loop did execute a live marketing action recently (distribution_architecture_repair_plus_blog_deploy), so the system is still shipping output even though outcome movement is not visible yet.
- A fresh outcome-capability runtime is now logged (manual_outreach_asset_follow_through), so the system-design repair should be treated as shipped even though repo adoption has not moved yet.
- Apollo managed outbound is already inside an active measurement window until 2026-06-01T23:11:13.732870+02:00, so the loop should not repackage the same lane before that checkpoint.

## What actually worked
- Execution path produced a live external action with non-flat outcome context: distribution_architecture_repair_plus_blog_deploy. Stars delta: +1

## What did not work
- Mirror adoption did not move either: GitHub stayed flat, so activity is not converting on either repo surface.

## What is repetitive
- Repeated outreach opening: Which of the five made the most difference for your team? Worth thinking about explicitly — most teams do not name it clearly until after it becomes a bottleneck.

## What is low-signal
- Reddit remains blocked/partial from this environment, so that channel cannot produce a trustworthy execution read right now.
- Reddit opening repair already exists in the shared artifact /home/mistlight/.openclaw/workspace/agents/marketing/reddit_fresh_openings.md; reuse that bank when channel access returns instead of drafting another siloed template.

## What should change now
- Ensure all public-facing content links Codeberg as primary and GitHub as mirror. If GitHub mirror remains flat, it is secondary evidence — do not allocate dedicated effort unless Codeberg is moving.

## Observed risks
- Repetition risk in outreach opening: "Which of the five made the most difference for your team? Worth thinking about explicitly — most teams do not name it clearly until after it becomes a bottleneck."
- Failing tactic detected: mirror_repo_flat_window
- Dormant risk parked for now: reddit_style_repetition_suspended_while_channel_blocked

## Outcome evaluation
- Codeberg: samples=9, stars +1, watchers +0, forks +0
- PyPI: samples=9, stars +0, watchers +0, forks +0
- GitHub: samples=9, stars +0, watchers +0, forks +0
- GitHub mirror adoption is also flat across the recent measurement window.
- Codeberg remains the stronger adoption surface and should stay the primary evaluation target.
- PyPI has 1498 downloads/month (10/day) — real usage signal that repo metrics don't capture.

## Repair actions (execute in this run)
- **mirror_repo_flat** (tactic) → Ensure all public-facing content links Codeberg as primary and GitHub as mirror. If GitHub mirror remains flat, it is secondary evidence — do not allocate dedicated effort unless Codeberg is moving.
  - Repair state: pending_measurement
  - Kill condition: N/A (mirror, not primary)
  - Success metric: GitHub mirror shows any adoption delta
- No additional same-run repair actions remain. Existing repairs are live and the loop is now waiting on measurement.
- Measurement-pending reasons: mirror_repo_flat

## Latest executed marketing action
- Type: distribution_architecture_repair_plus_blog_deploy
- Title: distribution architecture repair plus blog deploy
- Status: executed (ok=True)
- Outcome-ready: True
- Source log: /home/mistlight/.openclaw/workspace/agents/marketing/logs/marketing_2026-05-29_0030_blog_sandbox_security_deployed.json

## Next highest-leverage moves
- Kill or rewrite any tactic that stays flat across the recent adoption window instead of rewarding it for mere activity.
- Treat Codeberg movement as the primary outcome metric; GitHub is secondary mirror evidence only.
- Reduce repetitive outreach patterns and keep messaging tied to real workflow pain in a native-sounding voice.
- Require each new marketing action to name its expected outcome, measurement window, and replacement condition if it fails.
- If the current agent/process design is too weak to improve outcomes, create or repair agents, prompts, cron jobs, scripts, tests, and workflow rules in the same run instead of merely recommending them.

## Self-improvement mandate
- The marketing system owns outcomes, not just activity.
- Default internal decision rule: it is up to the system to decide and proceed.
- Allowed same-run self-repairs include new agents, prompt rewrites, cron changes, script patches, stronger tests, and process redesign when those improve marketing outcomes.

## Four marketing questions that messaging must answer
- what_is_it: Ralph Workflow is the operating system for autonomous coding: a free and open-source composable loop framework and AI orchestrator.
- who_is_it_for: Developers and technical teams doing ambitious software work that benefits from a structured workflow instead of a chat session.
- why_different: It keeps a simple Ralph-loop core, then composes that core into planning, development, verification, and broader workflow loops with strong defaults.
- why_now: You can use the default workflow as-is today, or build your own workflow on top without giving up control of your tools or process.

## Principle reference
- See `/home/mistlight/.openclaw/workspace/agents/marketing/MARKETING_WORKFLOW_PRINCIPLES.md`
- See `/home/mistlight/.openclaw/workspace/agents/marketing/FOUR_MARKETING_QUESTIONS.md`
- See `/home/mistlight/.openclaw/workspace/agents/marketing/MARKETING_SELF_IMPROVEMENT.md`

## Shared findings artifact sync
- Status: synced
- Refreshed distribution lane: distribution_architecture_guard_pause
- Execution board: /home/mistlight/.openclaw/workspace/drafts/2026-05-29_marketing_execution_board.md
- Outcome status: /home/mistlight/.openclaw/workspace/agents/marketing/logs/outcome_execution_board_latest.json

---

## Distribution architecture repair — 2026-05-29T02:21 UTC

**Problem:** OWNED_CONTENT_SOURCE_CANDIDATES was hardcoded to 4 guide paths, making 25 live Ralph-Site blog posts completely invisible to the marketing loop. The `_owned_content_publication_available()` gate could never see blog content. This was a structural blind spot contributing to 47+ consecutive empty-board measurement holds.

**Fix applied:**
1. `distribution_lane_selector.py`: Replaced hardcoded `OWNED_CONTENT_SOURCE_CANDIDATES` list with dynamic `_owned_content_source_candidates()` that discovers blog posts from `Ralph-Site/content/blog/` plus the existing 4 guide paths.
2. `run_posting.py`: Added `crosspost_blog_content()` to discover and cross-post uncrossposted blog content to Telegraph, with rate limiting (3 per batch, 1s sleep between batches).
3. `tests/test_blog_discovery.py`: 9 new tests covering candidate discovery, uniqueness, sorting, and cross-post staging behavior.

**Validation results:**
- Dynamic candidates: 29 (4 guides + 25 blogs) — was 4 in old hardcoded list
- No duplicate candidates
- Blog candidates sorted for deterministic behavior
- 25 blog posts discoverable for cross-posting (0 previously cross-posted via source_path)
- Old `OWNED_CONTENT_SOURCE_CANDIDATES` constant no longer exists (prevents stale references)

**Hold-window compliance:** This is a concrete runtime/process repair with code and test changes — not a prompt/rerun tweak. The hold window already contains `active_loop_prompt_repair` and `post_hold_reentry_contract_repair`; this repair is a different concrete change that modifies the execution board fingerprint.

**Next step after congestion clears (05:56 UTC):** The next marketing loop run can now see blog content as owned-content assets. The `crosspost_blog_content()` pipeline is ready for first cross-posting run.
