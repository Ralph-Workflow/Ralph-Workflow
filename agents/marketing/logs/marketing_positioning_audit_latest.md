# Marketing Positioning Audit

- Generated: 2026-05-20T09:13:27.560696
- Canonical source: `agents/marketing/RALPH_WORKFLOW_POSITIONING.md`
- Shared module: `agents/marketing/positioning.py`

## Public copy loops
- `generate_content.py` — draft generation — shared positioning: yes
- `run_posting.py` — scheduled publishing — shared positioning: yes
- `post_to_web.py` — manual cross-post publishing — shared positioning: yes
- `seo_outreach.py` — directory submission copy — shared positioning: yes
- `outreach_pipeline.py` — GitHub outreach copy — shared positioning: yes
- `reddit_autopost.py` — Reddit reply generation — shared positioning: yes
- `reddit_next_window_packet.py` — Reddit draft packet generation — shared positioning: yes
- `marketing_workflow_audit.py` — messaging audit artifact — shared positioning: yes
- `competitor_analysis.py` — public comparison page generation — shared positioning: yes

## Internal / non-copy loops
- `run.py` — strategy/metrics orchestration — shared positioning: no — internal/non-copy loop
- `seo_daily.py` — SEO measurement — shared positioning: no — internal/non-copy loop
- `adoption_tracker.py` — repo adoption measurement — shared positioning: no — internal/non-copy loop
- `marketing_momentum_watchdog.py` — status/watchdog output — shared positioning: no — internal/non-copy loop
- `reflection_engine.py` — internal reflection — shared positioning: no — internal/non-copy loop
- `weekly_review.py` — internal review — shared positioning: no — internal/non-copy loop
- `channel_discovery.py` — channel research — shared positioning: no — internal/non-copy loop
- `apollo_monitor.py` — account monitoring — shared positioning: no — internal/non-copy loop
- `github_outreach.py` — legacy/outreach support — shared positioning: no — internal/non-copy loop
- `reddit_watchdog.py` — opportunity timing gate — shared positioning: no — internal/non-copy loop
- `reddit_retrospective.py` — postmortem analysis — shared positioning: no — internal/non-copy loop
- `reddit_post.py` — browser posting transport — shared positioning: no — transport only; content comes from reddit_autopost.py
- `post_helpers.py` — posting transport helpers — shared positioning: no — internal/non-copy loop
- `sync_research.py` — research sync — shared positioning: no — internal/non-copy loop
- `gsc_auth.py` — auth utility — shared positioning: no — internal/non-copy loop
