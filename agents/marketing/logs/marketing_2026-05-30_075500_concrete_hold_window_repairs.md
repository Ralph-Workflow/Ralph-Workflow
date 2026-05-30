# Marketing Active Loop — Hold-Window Concrete Repairs
**Run**: 2026-05-30 07:55 CEST (05:55 UTC)  
**Slot**: marketing-active-loop cron (every 4h)  
**Hold status**: Active until 08:36 UTC (post-hold reentry cron `3afb2dc0` scheduled)  
**Rule applied**: Both `active_loop_prompt_repair` and `post_hold_reentry_contract_repair` already exist → must make concrete runtime/process repair with code/test changes

## Context at entry
- Measurement hold active until 08:36 UTC
- All metrics flat: Codeberg 12★/2 forks, GitHub 1★, PyPI 1299/mo
- All distribution lanes blocked (Reddit, HN, dev.to, Apollo, SMTP, GitHub, PyPI)
- Publisher discovery returns empty (DDG HTML endpoint permanently blocked)
- RSS feed working at /blog/feed.rss but standard paths (/feed.xml, /blog/feed.xml) returned 404
- Reentry contract and prompt repair already in place — another idle hold would be process failure

## Repair 1: Publisher Discovery Lane — `--from-json` Injection Bridge

### Problem
`publisher_discovery_lane.py` exclusively uses `https://html.duckduckgo.com/html/` which is permanently blocked by bot detection. The cron at 02:30 UTC consistently returns `{"status":"empty","discovered":0}`. The `web_search` tool works intermittently but there's no bridge to inject its results into the discovery pipeline.

### Fix
Added `--from-json FILE` flag to `publisher_discovery_lane.py`:
- New `inject_results()` function normalizes, deduplicates, and ranks pre-discovered results
- Agent-injected items receive `query="agent-injected"` for pipeline traceability
- Validates required fields (title, url), derives source domain from URL when missing
- Writes to both `publisher_discovery_latest.json` and `publisher_discovery_queue_latest.json`

### Verification
- 9 unit tests (all passing)
- Test injection verified: `echo '[{"title":"Top 5 Open-Source Agent Orchestration Platforms in 2026","url":"https://orloj.dev/blog/...","source":"orloj.dev"}]' | --from-json /tmp/test.json`
- Queue correctly populated and ranked

### Impact
When the marketing agent has working web_search, it can now inject discoveries directly:
```bash
python3 publisher_discovery_lane.py --from-json /tmp/fresh_discoveries.json
```

## Repair 2: RSS Feed Standard-Path Aliases

### Problem
RSS 2.0 feed was healthy at `/blog/feed.rss` (200, 36 posts with auto-discovery link tags on blog listing). But two standard paths returned errors:
- `/feed.xml` → 404 (many feed readers try domain root)
- `/blog/feed.xml` → 404 (some readers append .xml)

This is a concrete discoverability gap affecting content syndication, aggregator crawling, and AI crawler feed discovery.

### Fix
Added two route aliases in `routes.rb`:
```ruby
get "blog/feed.xml", to: "blog#feed", defaults: { format: :rss }
get "feed.xml", to: "blog#feed", defaults: { format: :rss }
```

### Deployment
- Committed to `Ralph-Site/main`, pushed to origin
- Full `cap production deploy` with verification checks:
  - Runtime fidelity check: PASS
  - Live public surface check: PASS
  - IndexNow notify: 91 URLs submitted (200 OK)

### Verification
```
/feed.xml → 200 (RSS XML with 36 items)
/blog/feed.xml → 200 (same content)
/blog/feed.rss → 200 (unchanged, already working)
```

## Post-hold readiness assessment

The 08:36 UTC reentry cron inherits:
- ✅ Working IndexNow pipeline (91 URLs submitted, 200 OK)
- ✅ Fixable publisher discovery (agents can inject via `--from-json`)
- ✅ Standard RSS paths (feed.xml aliases live)
- ✅ Can setup crons that patch remaining infrastructure gaps
- ⚠️ DDG web_search still intermittent (but injection bridge exists now)
- ⚠️ All distribution lanes still blocked (unchanged by these repairs)

## Outcome
Two concrete runtime/process repairs executed, tested, deployed, and verified live. The hold window was not wasted on another idle measurement hold or prompt/contract tweak. The post-hold reentry cron (08:36 UTC) inherits an improved infrastructure surface.
