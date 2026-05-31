# Apollo.io Channel Status — 2026-05-31 13:55 CEST

**Status:** ❌ BLOCKED

## Blockers

| Blocker | Detail |
|---|---|
| Cloudflare anti-bot interstitial | ✅ Confirmed — browser automation hit Cloudflare JS challenge before reaching login page |
| IP reputation | Likely — session IP flagged by Cloudflare |
| Login attempted | No — blocked before login form loaded |

## Session Details

- **Final URL reached:** `https://app.apollo.io/#/login` (via fragment redirect after Cloudflare block)
- **Browserless probe:** Same outcome — Cloudflare interstitial confirmed
- **Auth endpoints reached:** 0 of 0

## Next Steps

- Apollo is **not actionable** in this state
- Next automatic check will retry via cron (~30m cycle)
- Manual intervention required if this persists: rotate IP (VPN/proxy), or solve Cloudflare challenge in a real browser session
- No action needed from mistlight unless this persists for >2h

## Raw Data

See `apollo_status.json` for full structured output.
