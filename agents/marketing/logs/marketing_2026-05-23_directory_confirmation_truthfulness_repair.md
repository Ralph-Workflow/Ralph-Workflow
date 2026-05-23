# Directory confirmation truthfulness repair — 2026-05-23

## What ran
Repaired `agents/marketing/backlink_status.py`, added regression tests, and reran the backlink confirmation snapshot.

## Why this was the highest-leverage move
The active audit already said **directory confirmation** was the best lane to run next. But a live check exposed a false-green measurement bug: at least one supposed live listing (`AIToolboard`) returned HTTP 200 while still showing **"Tool Not Found"**. Reusing that as proof would have made the marketing loop flatteringly wrong.

So the right move was to fix the measurement surface first, then refresh it immediately.

## What changed
- Added visible-text heuristics in `backlink_status.py` so raw HTML/script noise no longer causes bogus truth judgments.
- Fail-closed on 200-status placeholder pages and transient loading shells.
- Added regression tests in `agents/marketing/tests/test_backlink_status.py`.
- Updated the Google indexing summary to separate **unavailable (429)** from **not indexed**.

## Verification
- `python3 -m unittest agents.marketing.tests.test_backlink_status -v` ✅
- `python3 agents/marketing/backlink_status.py` ✅

## Corrected snapshot
- **Confirmed live listings:** 2
  - SaaSHub — https://saashub.com/ralph-workflow
  - ToolWise — https://toolwise.ai/tools/ralph-workflow
- **Removed false positive:**
  - AIToolboard — 200 response but still a "Tool Not Found" placeholder
- **Still pending / not yet confirmed live:** NavAI, AIToolsIndex, ToolShelf, and the rest of the fresh submission burst
- **Google indexing checks:** 14 unavailable this run due to HTTP 429 rate limiting

## Why this helps outcomes
This keeps the next marketing action honest:
- only reuse the two real live listings as trust assets
- stop treating placeholder pages as distribution wins
- avoid stacking more directory work on top of a dirty measurement surface

The loop now has a cleaner truth base for Codeberg-first distribution decisions.
