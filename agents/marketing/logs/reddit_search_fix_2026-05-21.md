# Reddit search fix — 2026-05-21

## Summary
- Root cause: the monitor relied on DuckDuckGo HTML/Lite and Brave HTML search. On 2026-05-21 those providers were effectively unusable from this host:
  - DuckDuckGo HTML/Lite returned HTTP 202 challenge pages containing `anomaly-modal` instead of results.
  - Brave Search returned HTTP 429 rate limits.
- Result: most queries ended as `fetch_error`/challenge and the monitor produced degraded coverage.

## What I checked
- Read `agents/marketing/reddit_monitor.py` and confirmed search flow: DuckDuckGo HTML -> DuckDuckGo Lite -> Brave.
- Verified Browserless token from `TOOLS.md`: `2UWbL11RUlO4quE8238557491eab7d21b44da3db127e3d5e4`.
- Confirmed Browserless is reachable via `https://production-sfo.browserless.io/json/version?token=...`.
- Reproduced provider failures directly:
  - DuckDuckGo HTML/Lite: challenge page (`anomaly-modal` present).
  - Brave: HTTP 429 Too Many Requests.
  - Direct Reddit search JSON also blocked with HTTP 403, so that was not a viable fallback.

## Fix implemented
- Updated `agents/marketing/reddit_monitor.py` to add a Browserless-backed Bing fallback.
- Added:
  - `BROWSERLESS_TOKEN` config (env override supported, existing token defaulted for this workspace)
  - `fetch_bing_results(query)` using Playwright over Browserless CDP
  - `parse_bing_results(...)` for Bing result pages
  - fallback provider entry `bing_browserless` in `search_query(...)`
- This preserves the existing fast providers first, but restores coverage when DDG/Brave are blocked.

## Verification
- Unit tests: `python3 -m unittest agents.marketing.tests.test_reddit_monitor` ✅
- Direct smoke query after patch:
  - `search_query('approval_drag', 'Claude Code approval reddit')` returned `status=ok` with 12 parsed Reddit results.
- Sample returned results included relevant Reddit threads such as:
  - `r/ClaudeAI` — Claude Code approval mode thread
  - `r/ClaudeCode` — asking for explicit approval

## Notes
- `reddit_monitor.py` may still reuse a recent cached healthy report for ~30 minutes; that reuse path is separate from the provider failure and was left intact.
- Full 24-query end-to-end runs through Browserless are noticeably slower than the old HTML fetch path, but they now have a viable recovery path instead of failing outright.
