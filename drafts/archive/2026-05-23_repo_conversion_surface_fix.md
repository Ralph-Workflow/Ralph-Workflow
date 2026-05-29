# Ralph Workflow Repo Conversion Surface Fix

Generated: 2026-05-23T07:16:00+02:00

## Why this action ran
- Primary Codeberg adoption is flat.
- Current external distribution lanes are either already in measurement windows or blocked on external auth.
- The strongest immediate local move was to tighten the repo-first evaluation path instead of producing another handoff packet.

## Shared findings reused
- ADOPTION_FUNNEL_NEXT.md → the first-task / start-here asset is the closest move to adoption
- marketing_workflow_audit_latest.json → current bottleneck is distribution_and_message_to_primary_repo_conversion
- adoption_metrics_latest.json → Codeberg is the primary success gate
- market_intelligence_latest.json → keep Codeberg-first messaging and the four core truths intact

## What changed
- Created `START_HERE.md` as the canonical first-run guide
- Updated `README.md` to route the main quick link and first-click CTA to `START_HERE.md`
- Replaced the old full `START_HERE_RALPHWORKFLOW.md` body with a thin redirect to reduce duplication

## Docs review note
- What changed: canonicalized the first-run path around `README.md -> START_HERE.md -> deep guides`
- Why this surface: this is the shortest path from evaluator attention to a real first run
- What was pruned: duplicated top-level start-here copy in `START_HERE_RALPHWORKFLOW.md`
- Duplication reduced: yes
- Why top-level is better now: one canonical first click, cleaner naming, clearer Codeberg-first path

## Verification
- Local link check passed for `README.md`, `START_HERE.md`, and `START_HERE_RALPHWORKFLOW.md`
- Broken links found: 0

## Measurement contract
- Expected outcome: lower evaluator confusion on the first run and better conversion from repo visit to serious trial
- Review window: through 2026-06-06
- Replacement condition: if distribution windows resolve and Codeberg still stays flat, move to the next stronger proof asset rather than another light wording pass
