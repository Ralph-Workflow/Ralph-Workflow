# Apollo.io Channel Status

**Checked:** 2026-05-31 20:39 CEST (18:39 UTC)
**Status:** 🔴 Cloudflare Auth Blocked

## Blocker State: **cloudflare_auth_blocked**

Apollo is not currently actionable. Cloudflare's challenge interstitial is blocking the login page entirely — automation cannot get past it.

## Details

- **Final URL:** https://app.apollo.io/#/login
- **Cloudflare blocked:** Yes — interstitial detected in response body from `https://app.apollo.io/`
- **Login attempted:** No — could not reach the login form
- **Browserless probe status:** cloudflare_auth_blocked — same result via Browserless
- **Auth endpoints checked:** None (login page itself was blocked)

## Notes

Cloudflare is serving a challenge on the root Apollo domain. This prevents both the headless browser (xvfb) and the Browserless remote probe from even seeing the login form. No credential attempt was made because the form was unreachable.

## Downstream Status

❌ **Blocked.** Apollo is a managed outbound channel but cannot be used until the Cloudflare challenge can be passed. Manual login via a real browser with human-interactive Cloudflare resolution is required to re-establish a session.
