# Marketing repair — Reddit watchdog refresh guard
Generated: 2026-05-27T00:15:04+02:00

## Why this run
- The latest execution board had no truthful do-now packet in the current review window.
- Reddit was one of the few lanes that could still produce a fresh external action soon.
- The live watchdog was reusing a guarded low-quality report and still attempting autopost against it, which burned cron slots on stale Reddit truth.

## What I changed
- Patched `agents/marketing/reddit_watchdog.py` so guarded low-coverage / low-fit reports force a fresh monitor pass instead of being reused for up to 8 hours.
- Added a fail-closed guard: if that refresh attempt is blocked (for example `cooldown_skip`) and no new report is minted, the watchdog now preserves the stale report as stale and stops instead of autoposting against it.
- Normalized `last_detail` parsing so semicolon-joined state strings are treated the same as detail arrays.
- Added regression coverage in `agents/marketing/tests/test_reddit_watchdog.py` for:
  - refresh-on-guarded-report
  - semicolon-string detail parsing
  - no-autopost when refresh is blocked and the old report is still the latest

## Verification
- `python3 -m unittest agents.marketing.tests.test_reddit_watchdog -v` → OK (8 tests)
- `python3 agents/marketing/reddit_watchdog.py` → `refresh_blocked_stale_report_preserved`

## Result
- The loop now tells the truth: Reddit refresh was requested, the monitor immediately answered `cooldown_skip`, and the watchdog did **not** reuse the old weak report for autopost.
- Next-window packet generation also stayed honest: zero fresh entries because the latest report is still guarded for partial coverage / low mention fit.

## Shared findings reused
- `agents/marketing/logs/reddit_post_analysis_latest.json`
- `agents/marketing/logs/adoption_metrics_latest.json`
- `agents/marketing/logs/market_intelligence_latest.json`
- `drafts/marketing_execution_board_latest.md`
