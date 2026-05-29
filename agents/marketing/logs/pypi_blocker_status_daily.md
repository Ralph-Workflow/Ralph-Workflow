# PyPI Publish Blocker — Daily Status
## 2026-05-29 09:54 CEST

### The Truth
PyPI v0.8.8 is **built, README-verified, and ready to publish** — but cannot be published without `PYPI_TOKEN`.

### What's at Stake
| Metric | Value |
|--------|-------|
| Monthly downloads | 1,428 (down from 1,498) |
| README served | v0.8.7 (stale — no Codeberg CTA) |
| v0.8.8 status | Built, README verified (has Codeberg + GitHub CTA) |
| Blocker | `PYPI_TOKEN` missing from environment |
| Downloads seeing conversion path | 0 (v0.8.7 README has no CTA to Codeberg) |

### Why This Is the Highest-ROI Blocked Action
- 1,428 installs/month = ~48 installs/day
- Each install is a developer who has Ralph Workflow running on their machine
- v0.8.8 README adds a direct link to Codeberg (primary) + GitHub (mirror)
- Every install becomes a potential star/watch/fork conversion
- This is the **only** conversion surface that reaches users who have already installed the tool
- No other autonomous lane can reach this audience

### What's Needed
1. Set `PYPI_TOKEN` environment variable to a valid PyPI API token for the `ralph-workflow` package
2. Run: `cd Ralph-Site/vendor/Ralph-Workflow && python3 -m build && python3 -m twine upload dist/*`
3. Verify: `pip install ralph-workflow==0.8.8 && pip show ralph-workflow`

### Handoff Urgency
**High.** Every day this stays unpublished wastes ~48 developer installs that could see the Codeberg CTA. Over one month, that's ~1,400 missed conversion opportunities.

### Related
- Blog: 31 posts + 1 new evaluator decision guide (deployed 2026-05-29)
- All blog posts link to Codeberg primary + GitHub mirror
- Telegraph cross-posts resume next 06:00 UTC cron (data format fix applied)
- Outreach log: `outreach-log.md` at workspace root
