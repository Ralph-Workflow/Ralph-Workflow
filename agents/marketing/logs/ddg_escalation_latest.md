# DDG Search Provider — Escalation Notification
Generated: 2026-06-04T09:30:02.375330+00:00

## Status
- **DDG status**: degraded_http_202
- **HTTP**: 202
- **Results**: 0
- **Bot-blocked**: False
- **Reddit query test**: FAIL
- **Brave fallback**: degraded (0 results)
- **Days since last usable retrieval**: 7 (since 2026-05-28)

## ⚠️ ESCALATION DEADLINE: TODAY (June 4)

DDG has been completely unresponsive for 7 days. The suspension marker says:
> "If suspension exceeds 7 days (2026-06-04), escalate via user notification and consider provider migration (Brave Search API, SerpAPI, etc.)"

### Impact
- **Reddit monitor**: dead (no signal since May 28)
- **Publisher discovery**: dead (publisher_discovery_lane.py depends on DDG HTML scrape)
- **SEO indexation diagnostic**: dead (seo_indexation_diagnostic.py depends on DDG)
- **All web_search-driven discovery**: dead
- **Distribution lane selector**: operating blind — cannot find new distribution surfaces

### What's still working
- Owned conversion surfaces (blog, README, compare page, docs, PyPI, Docker)
- Direct URL access (stackoverflow.com, curated targets in queues)
- Content quality maintenance (conversion_surface_watchdog, social_proof_bootstrap CTA audits)
- Star conversion runner (ralph contribute CLI, runner.py periodic CTA)
- Internal optimization (cross-links, SEO integrity, comparison pages)

### Recommended actions (human)
1. Configure a Brave Search API key (free tier: 2,000 queries/month) or SerpAPI key
2. Set `BRAVE_API_KEY` or `SERPAPI_KEY` environment variable
3. Or: configure a different search backend in OpenClaw (if supported)
4. Or: run the marketing loop from a non-Hetzner IP (AWS, home connection) to unblock DDG
5. Delete `/agents/marketing/logs/reddit_monitor_suspension.json` to re-enable web_search attempts

### What happens if unattended
- The system will continue optimizing owned conversion surfaces (blog, README, compare page)
- External distribution will remain limited to: StackOverflow, curated handoff packets, ralph contribute CLI
- No new Reddit/social discovery will surface
- The measurement hold cycle will keep the system from spiraling into fake-progress churn

## Live state
```json
{
  "ddg": {
    "provider": "duckduckgo",
    "timestamp": "2026-06-04T09:30:01.805076+00:00",
    "ok": false,
    "http_status": 202,
    "result_count": 0,
    "bot_blocked": false,
    "reddit_test": {
      "ok": false,
      "http_status": 202
    }
  },
  "brave": {
    "provider": "brave",
    "timestamp": "2026-06-04T09:30:02.176143+00:00",
    "ok": false,
    "http_status": 429,
    "result_count": 0
  }
}
```

## Discovered URLs (from working fallback)
