# Reddit search infrastructure fix

Date: 2026-05-22

- Added jittered delay between provider retries to reduce bursty challenge/rate-limit behavior.
- Hardened Bing challenge detection with Bing-specific human-verification markers.
- Updated Browserless Bing fetch to use a fresh page/context pattern with a slightly longer network-idle wait and randomized settle time.
- Added `reddit_json` as a minimal fallback provider ahead of Browserless so the monitor can still discover Reddit threads when external search engines challenge.

Why: the provider chain was degrading heavily and frequently returning `provider_challenge`, which made zero-opportunity runs indistinguishable from broken telemetry and stalled Reddit momentum.
