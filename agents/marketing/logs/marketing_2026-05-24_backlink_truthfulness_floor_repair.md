# Backlink truthfulness floor repair — 2026-05-24

## What I changed
- Patched `agents/marketing/seo_daily.py` so backlink reporting reuses `agents/marketing/logs/backlink_status_latest.json` as a truthful floor.
- Preserved the Google approximation, but stopped letting a flaky/empty Google check erase already-verified live listings.
- Added regression tests in `agents/marketing/tests/test_seo_daily.py` for both the normal-floor path and the Google-error fallback path.

## Shared findings reused
- `agents/marketing/logs/backlink_status_latest.json` already confirms 2 live listings: SaaSHub and ToolWise.
- The loop was still reporting `backlinks_approx: 0`, which was pushing misleading zero-backlink decisions during measurement-hold runs.

## Verification
- `python3 -m unittest agents.marketing.tests.test_seo_daily.BacklinkTruthfulnessTests agents.marketing.tests.test_seo_daily.TrendComputationTests -v` ✅
- `python3 agents/marketing/seo_daily.py` now reports `backlinks_approx: 2` ✅
- `python3 agents/marketing/run.py` now carries `backlinks_approx: 2` and `backlinks_delta: 1.8` into the loop output ✅

## Outcome
The marketing loop now tells the truth about known third-party proof. That makes the hold window and next-lane decisions less noisy and stops the system from repeatedly acting like Ralph has zero backlinks when two live directory listings are already verified.
