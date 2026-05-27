# Apollo.io Channel Status

**Checked:** 2026-05-27 17:26 UTC (5:22 PM Europe/Berlin)
**Status:** ✅ Login Succeeded

## Blocker State: **none**

Apollo is authenticated and the UI is fully usable. Real-browser automation path succeeded.

## Details

- **Final URL:** https://app.apollo.io/#/home
- **Cloudflare blocked:** No (background challenges seen on ancillary requests but did not interrupt the authenticated UI)
- **Login attempted:** No (session was already authenticated)
- **Browserless probe:** Not used — real browser path succeeded

## Notes

Cloudflare interstitial was detected in response bodies from:
- `https://app.apollo.io/`
- `https://app.apollo.io/api/v1/contacts/search?...`
- `https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/...`

These were background challenges on ancillary requests. The authenticated Apollo surface was already active and remained usable throughout.

## Downstream Status

Apollo is a **managed outbound channel** — outbound email/sequence sends are **actionable** via this authenticated session.
