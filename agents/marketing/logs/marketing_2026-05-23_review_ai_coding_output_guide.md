# Marketing action — Review AI coding output before merge guide

- **When:** 2026-05-23 13:53 Europe/Berlin
- **Action:** Added a repo-native `Review AI coding output before merge` guide and routed the top-level first-run path toward it.
- **Why now:** Codeberg adoption is flat, Reddit is fail-closed, Apollo and curator outreach are already inside active measurement windows, and the strongest reusable pain frame is still the gap between an agent claiming done and a result a developer would actually merge.

## Shared findings reused
- `agents/marketing/logs/market_intelligence_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.md`
- `agents/marketing/logs/adoption_metrics_latest.md`
- `agents/marketing/ADOPTION_FUNNEL_NEXT.md`
- `agents/marketing/NON_REDDIT_CHANNELS_NEXT.md`
- `agents/marketing/logs/distribution_lane_latest.md`

## What changed
- Added `content/guides/review_ai_coding_output_before_merge.md`
- Updated `README.md` quick links to promote the new guide without increasing the top-level quick-link count
- Updated `START_HERE.md` to route evaluators with trust/review concerns toward the new guide

## Docs review note
- **What changed:** added one deep guide and rerouted existing top-level paths toward it
- **Why this surface:** this is a high-intent trust question that belongs below README, but it needs to be one click from the repo landing path
- **What was pruned:** the README quick-link count stayed flat at 4 instead of growing another branch
- **Duplication reduced:** yes, by replacing an example-only top-level route with a stronger decision guide that still links to the example asset
- **Why the top-level experience is better:** evaluators now have a clearer path from "can I trust this finish?" to a concrete review standard without making README noisier

## Verification
- Local markdown link check passed for `README.md`, `START_HERE.md`, and `content/guides/review_ai_coding_output_before_merge.md`
- README quick-link count remains **4**

## Measurement contract
- **Review by:** 2026-06-06
- **Success metric:** clearer qualified inspection and any Codeberg adoption movement after evaluators hit the repo-first review/trust path
- **Replacement condition:** if Codeberg remains flat after the current external measurement windows close and this guide still shows no visible reuse in distribution or conversion surfaces, replace it with a stronger proof asset instead of another light docs pass
