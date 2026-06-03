# Apollo.io Channel Status

**Last checked:** 2026-06-03 08:41 CEST  
**Status:** `cloudflare_auth_blocked` (blocked)

| Field | Value |
|-------|-------|
| Login attempted | No (blocked before reaching login) |
| Final URL | `https://app.apollo.io/#/login` |
| Cloudflare detected | ✅ Yes — interstitial content present |
| Browserless | Also blocked — same Cloudflare result |

## Notes

- Apollo.io served Cloudflare challenge content for both direct and Browserless-proxied requests.
- Login form was never reached; no credentials were sent.
- Apollo channel is **not actionable** until Cloudflare auth is resolved from a compatible IP/browser fingerprint.
- Next monitor cycle will retry and re-evaluate.

## Raw status (apollo_status.json)

```json
{
  "timestamp": "2026-06-03T08:41:25.454913+02:00",
  "status": "cloudflare_auth_blocked",
  "final_url": "https://app.apollo.io/#/login",
  "login_attempted": false,
  "cloudflare_blocked": true,
  "notes": "Cloudflare interstitial detected in response body from https://app.apollo.io/.",
  "browserless_probe_status": "cloudflare_auth_blocked",
  "browserless_probe_final_url": "https://app.apollo.io/#/login",
  "browserless_probe_notes": "Browserless saw Cloudflare interstitial content from https://app.apollo.io/."
}
```
