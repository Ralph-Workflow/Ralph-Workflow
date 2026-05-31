# Ralph Workflow Marketing Execution Board
Generated: 2026-05-31T02:57:00+00:00 (doorway consolidation deploy time)
Last updated: 2026-05-31T05:00:00+02:00 (llms.txt/llms-full.txt update)

## Why this board exists
- Codeberg is flat in the active window (12 stars, 2 forks, 0 movement).
- 0/80 pages indexed in Google (GSC).
- The 02:57 UTC doorway consolidation is the single biggest architectural improvement since launch — 7 alternative pages now 301-redirect to a single canonical `/compare` hub.
- This board tracks the post-consolidation follow-through so the architectural fix translates into discovery and adoption.

## Architecture state (post-doorway consolidation)
- **7 → 1:** aider-alternative, claude-code-alternative, conductor-alternative, continue-alternative, copilot-alternative, cursor-alternative, hermes-alternative → all 301 → `/compare`
- **/compare page:** Live, rendering correctly. Canonical comparison hub covering Aider, Claude Code, Conductor, Continue, Copilot, Cursor, Hermes.
- **sitemap.xml:** 100 URLs, all lastmod 2026-05-31.
- **llms.txt:** Updated — 42 blog articles (was 38), `/compare` in Quick Links.
- **llms-full.txt:** Updated — 2236 lines, new compare section + 4 missing articles (overnight-coding-agent-pattern, ralph-workflow-in-5-minutes, ai-coding-agent-testing-strategy, vendor-neutral-ai-coding-platform-independent-workflow).
- **robots.txt:** GPTBot, PerplexityBot, Claude, all allowed.
- **IndexNow:** Pings sent for all 7 redirected URLs + new /compare URL at 02:57 UTC.

## Active review windows
- Apollo next review: 2026-05-29T09:00:01+02:00 (expired — needs re-engagement)
- Apollo launch review: 2026-06-05T09:00:01+02:00
- StackOverflow: Cooldown through Wednesday June 3. First cron run: 2026-06-03T03:15 CEST.
  - Packet `/home/mistlight/.openclaw/workspace/drafts/stackoverflow_answer_handoff_packet_latest.md` is current.
  - Do NOT regenerate or manually re-deliver — the scheduled cron handles it.
- Comparison backlink: Already delivered in current window. Do not re-surface.
- Directory secondary-surface: Already shipped in current window. Do not requeue.

## Best executable assets available now
### 1. AI crawler discovery surface (just updated)
- Status: **Live.** llms.txt and llms-full.txt now surface /compare and all 42 articles.
- Why this matters: The /compare page is the single most important new surface for indexation. AI crawlers (GPTBot, PerplexityBot, Claude) are explicitly allowed in robots.txt and will discover /compare via the updated llms.txt.
- Next check: Monitor GSC for first indexation of /compare (likely 72h+ after AI crawler discovery).

### 2. Manual publisher outreach
- Packet: `/home/mistlight/.openclaw/workspace/drafts/primary_repo_flat_manual_review_asset_latest.md`
- Targets: ComputingForGeeks
- Human-gated: Requires manual email delivery.

## Blockers requiring human action
| Blocker | What's needed | Impact |
|---------|--------------|--------|
| Google Indexing API | Enable in GCP project 292739303076 via Cloud Console | Could request crawl of `/compare` immediately |
| GSC Indexing | Read-only scope (no manual request-crawl) | Wait for natural/AI-crawler discovery |
| PyPI token | Credentials blocked | Can't push v0.8.8 release |
| Apollo Cloudflare | Token blocked | Can't send email sequences |
| GitHub auth login | Interactive login required | Can't create issues/PRs on target repos |
| Reddit/HN/Lobsters/dev.to | Human-gated | Can't post/reply |
| SMTP user | Blocked | Can't send email |

## Hold-exhaustion circuit breaker
- Measurement-holds: 0 this 24h window (doorway consolidation + llms update are both concrete external actions).
- Next hold resets at: 2026-05-31T05:00:00+02:00.

## Shared findings reused
- market_intelligence_latest.json → positioning truths and comparison framing
- distribution_architecture_repair log (02:57 UTC) → 7→1 doorway consolidation
- stackoverflow_answer_handoff_packet_latest.md → high-intent Q&A demand-capture asset
- apollo_sequence_status_latest.json → launch-ready managed outbound state

## Process rule
- Do not generate another siloed packet when one of the assets above is already current.
- The next scheduled lane is StackOverflow (Wednesday June 3, 03:15 CEST). Do not burn slots re-analyzing it.
- If no executable lane exists when this board is read, perform a concrete distribution_architecture_repair or measurement infrastructure improvement.
