# Public Conversion Surface Repair

- Timestamp: 2026-05-27T14:40:23+02:00
- Action: **Retrofit Codeberg-first first-run CTAs onto high-intent Ralph Workflow blog posts**
- Channel: `owned_content_conversion`

## Why this action
- Current bottleneck: **conversion_to_free_use**
- Live external lanes are already in overlapping measurement windows, so another outbound push right now would blur measurement.
- Three high-intent owned posts still lacked a direct Codeberg-first evaluator path:
  - `Ralph-Site/content/blog/hello-ralph-workflow.md`
  - `Ralph-Site/content/blog/how-to-run-claude-code-unattended.md`
  - `Ralph-Site/content/blog/spec-driven-ai-agents-why-workflow-is-the-unit-of-work.md`

## What changed
- Added Codeberg-primary evaluator CTAs to all three posts
- Added `first-task-guide` and `START_HERE` links
- Added the morning-after merge question: **would you merge this?**
- Expanded conversion-surface regression coverage so these posts fail closed if the CTA disappears or GitHub moves ahead of Codeberg

## Shared findings reused
- `agents/marketing/logs/marketing_workflow_audit_latest.json`
- `agents/marketing/logs/distribution_lane_latest.json`
- `drafts/marketing_execution_board_latest.md`
- `agents/marketing/logs/adoption_metrics_latest.json`
- `agents/marketing/logs/market_intelligence_latest.json`
- `agents/marketing/logs/reddit_post_analysis_latest.md`
- `agents/marketing/logs/reddit_execution_status_latest.md`

## Verification
- `python3 -m unittest agents.marketing.tests.test_public_conversion_surfaces agents.marketing.tests.test_positioning_footer -v` ✅

## Expected outcome
Existing owned-content traffic now has a cleaner path into Codeberg-first evaluation instead of ending at article consumption only.

## Review window
- Review at: **2026-06-01T23:11:13.732870+02:00**
- Success metric: repaired posts keep their Codeberg-first CTA and any resulting repo movement lands on Codeberg first.
- Kill condition: if Codeberg remains flat through the next checkpoint, stop spending slots on older owned-content CTA polish and move to a different executable demand-capture lane or stronger proof-surface move.
