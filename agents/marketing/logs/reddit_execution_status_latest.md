# Reddit Execution Status

- Generated at: `2026-05-27T05:05:29.102014+02:00`
- Status: `execution_blocked`
- Browser username: `unknown`
- Expected username: `Informal-Salt827`
- Raw request statuses: `https://www.reddit.com/login/ -> 403, https://old.reddit.com/login -> 403, https://www.reddit.com/api/me.json -> 403`
- Notes: Reddit is IP-blocked even for the authenticated Chromium session. This is an IP-level block, not a cookie/session issue.
- Blocking reason: Reddit is serving a "blocked by network security" page even through the Chromium session. IP-level block is in effect.
