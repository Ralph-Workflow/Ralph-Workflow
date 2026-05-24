# Marketing runtime truthfulness repair
Generated: 2026-05-24T12:34:44+02:00

## What I fixed
- Isolated `agents/marketing/tests/test_marketing_momentum_watchdog.py` from live workspace state.
- Updated the measurement-hold fixture to use a canonical `marketing_*.json` filename so the shared hold-runtime parser sees the intended test fixture.
- Patched `ROOT` in the affected test alongside `STATUS_DIR`, `SEO`, and related paths so live `reddit_execution_status_latest.json` and other workspace artifacts cannot leak into the suite.

## Why this was the highest-leverage move now
- Reddit is currently fail-closed for live execution from this environment.
- Directory, curator, and Apollo lanes are already inside overlap or measurement windows.
- The active marketing slot was better spent repairing a real gate regression than generating another fake-progress packet.

## Verification
- `python3 -m unittest agents.marketing.tests.test_marketing_momentum_watchdog` ✅
- `python3 -m unittest agents.marketing.tests.test_marketing_momentum_watchdog agents.marketing.tests.test_marketing_system` ✅

## Outcome
- Future watchdog/system gate runs now evaluate the intended fixtures instead of ambient workspace state.
- This keeps measurement-hold and blocked-lane repairs honest while the loop waits for a genuinely executable distribution window.
