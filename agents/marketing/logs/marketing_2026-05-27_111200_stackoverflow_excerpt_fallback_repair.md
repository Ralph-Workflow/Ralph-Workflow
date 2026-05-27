# StackOverflow excerpt fallback repair

- **Timestamp:** 2026-05-27T11:12:00+02:00
- **Type:** marketing-runtime-repair
- **Action:** Broaden StackOverflow demand-capture search with official excerpt-search fallback

## Why this action
The execution board still had no truthful do-now packet. Reddit/manual lanes were already in review-window constraints, comparison and curator packets were already delivered or still blocked by window truth, and the StackOverflow lane was still too narrow to surface fresh unanswered demand. The best executable move was a runtime repair that improves the next real demand-capture attempt instead of regenerating stale handoff assets.

## What changed
- Patched `agents/marketing/stackoverflow_answer_lane.py`
- Added `/search/excerpts` fallback when `/search/advanced` returns no results
- Added intent-rich `q` and `body` terms to the StackOverflow search specs
- Filtered excerpt-search results to question rows only
- Preserved existing no-churn protections for exhausted/recently drafted candidates
- Added regression tests in `agents/marketing/tests/test_stackoverflow_answer_lane.py`

## Verification
- `python3 -m unittest agents.marketing.tests.test_stackoverflow_answer_lane -v` ✅
- `python3 agents/marketing/stackoverflow_answer_lane.py` ✅

## Live result
The live probe stayed truthful: no new StackOverflow handoff packet was created. The broadest current candidate was still an already-answered low-fit question (`score -0.6`, `answers 4`), so the lane remained empty instead of fabricating progress.

## Expected effect
At the next post-hold rerun, the StackOverflow lane can search a wider official API surface for high-intent unanswered questions and has a better chance of producing a real Codeberg-first placement path.
