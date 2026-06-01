# Marketing action — Claude Code + Codex repo guide

- **When:** 2026-05-23 13:35 Europe/Berlin
- **Action:** Added a repo-native `Claude Code + Codex workflow` guide and routed the top-level first-run path toward it.
- **Why now:** Codeberg adoption is flat, external lanes are already in measurement windows or blocked, and shared findings keep pointing to the handoff/review gap as the live evaluator pain.

## Shared findings reused
- `agents/marketing/logs/market_intelligence_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.md`
- `agents/marketing/logs/adoption_metrics_latest.md`
- `agents/marketing/ADOPTION_FUNNEL_NEXT.md`
- `agents/marketing/NON_REDDIT_CHANNELS_NEXT.md`
- `agents/marketing/logs/distribution_lane_latest.md`

## What changed
- Added `content/guides/claude_code_codex_workflow.md`
- Updated `README.md` quick links to promote the new guide without increasing the top-level link count
- Updated `START_HERE.md` to route evaluators already stitching tools together toward the new guide

## Docs review note
- **What changed:** added one deep guide and rerouted existing top-level paths toward it
- **Why this surface:** it answers one concrete high-intent question while keeping README focused
- **What was pruned:** no new top-level link sprawl; the README quick-link count stayed flat at 4
- **Duplication reduced:** yes, by replacing a broader README route instead of adding another branch
- **Why the top-level experience is better:** the first-click path now matches the strongest live pain frame more directly

## Verification
- Local markdown link check passed for `README.md`, `START_HERE.md`, and `content/guides/claude_code_codex_workflow.md`
- README quick-link count remains **4**

## Measurement contract
- **Review by:** 2026-06-06
- **Success metric:** clearer qualified inspection and any Codeberg adoption movement after evaluators hit the repo-first path
- **Replacement condition:** if this guide does not get reused in conversion/distribution and Codeberg stays flat after current windows close, replace it with a stronger proof asset
