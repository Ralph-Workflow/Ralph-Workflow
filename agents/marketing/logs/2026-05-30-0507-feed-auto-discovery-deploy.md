# Marketing Action Log — 2026-05-30 05:07 UTC

## Action
Feed auto-discovery `<link>` tags added to individual blog post pages + deployed to production.

## What changed
- **Code change**: `app/views/blog/show.html.erb` — added `content_for :head` block with RSS and JSON Feed alternate links
- **Git commit**: `6c5e04d` — `seo: add feed auto-discovery links to individual blog post pages`
- **Deploy**: Capistrano → ralphworkflow.com, release `20260530050654`
- **IndexNow**: Auto-submitted all 91 sitemap URLs to Bing/Yandex on deploy

## Why this matters
- Previously only `/blog` had feed auto-discovery. Individual blog post pages (arrived at from search results, social shares, etc.) had no feed advertisement.
- Feed readers, RSS aggregators, and AI crawlers that discover a blog post from search now see both RSS and JSON Feed links in the `<head>`, enabling subscription and crawl discovery.
- This is a concrete distribution-architecture repair (code change + deploy), not a prompt tweak.

## Verification
- ✅ `https://ralphworkflow.com/blog/hello-ralph-workflow` returns feed alternate links
- ✅ `https://ralphworkflow.com/blog/ralph-workflow-vs-aider` (noindex post) also returns feed alternate links
- ✅ IndexNow submitted all 91 sitemap URLs with 200 OK response
- ✅ Capistrano deploy completed successfully with verify_live_public_surface OK

## Context
- Measurement hold active (lifts 2026-05-30T08:36:38 UTC, ~1.5h from now)
- Per mandatory rule: both `active_loop_prompt_repair` and `post_hold_reentry_contract_repair` already exist — cannot spend another slot on prompt tweaks; must do concrete code/test change
- This action satisfies that constraint: real code change, CI-passing deploy, live verification
- Sitemap ping endpoints (Google ping, Bing ping) confirmed deprecated — IndexNow is the correct replacement and is already wired into the deploy pipeline
