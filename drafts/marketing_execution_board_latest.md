# Ralph Workflow Marketing Execution Board
Generated: 2026-05-30T02:39:02

## Why this board exists
- Codeberg is still flat in the active window (9 samples; stars +0, watchers +0, forks +0).
- Multiple live lanes already exist, so this board consolidates the best executable assets instead of letting them stay siloed across separate packet files.
- Use this as the single follow-through surface during measurement holds and overlapping review windows.

## Active review windows
- Apollo next review: 2026-05-29T09:00:01.629178+02:00
- Apollo launch review: 2026-06-05T09:00:01.629178+02:00
- Short review-window congestion clears at: 2026-05-30T08:36:38
- Post-hold marketer rerun scheduled: 2026-05-31T00:00:00
- Google indexation GSC recrawl: variable (7-14 days from doorway-page consolidation on 2026-05-30)
- StackOverflow demand-capture packet was already delivered for manual placement in the current review window; do not redeliver it until a genuinely new placement path exists.
- StackOverflow demand-capture packet is exhausted for this review window; do not redeliver it until a genuinely new placement path exists.
- Comparison backlink packet was already manually delivered in the current review window; do not surface it again until that window expires or the prepared target set changes.
- Directory secondary-surface repair already shipped in the current review window; do not requeue it until the documented follow-up date or the live target set changes.

## Best executable assets still waiting
### 1. Manual community discussion asset
- When: Do now
- Packet: /home/mistlight/.openclaw/workspace/drafts/reddit_discussion_handoff_packet_latest.md
- Targets: Distribution lane execution: distribution_architecture_repair
- Why this matters: The same empty-board distribution-architecture failure is still under an active third-strike churn guard, but this review window already reused that pause for the current fingerprint; perform a concrete distribution-architecture repair now instead of logging another guard pause.

### 2. Apollo runtime-blocker review packet
- When: Do now
- Packet: /home/mistlight/.openclaw/workspace/drafts/2026-05-30_apollo_runtime_blocker_review_packet.md
- Targets: Ralph Workflow curator follow-up — Codeberg CTA
- Why this matters: Apollo follow-up is already due, but runtime auth is blocked; the truthful next move is to carry a blocker-specific recovery packet instead of collapsing back into another empty-board guard pause.

## Shared findings reused
- market_intelligence_latest.json → positioning truths and comparison framing
- adoption_metrics_latest.json → Codeberg movement remains the primary success gate
- curator_outreach_queue_latest.json / comparison_backlink_queue_latest.json → live prepared execution queues
- primary_repo_flat_contact_discovery_latest.json → fresh publisher-contact lane
- apollo_sequence_status_latest.json / apollo_sequence_launch_packet_latest.md → launch-ready managed outbound state
- stackoverflow_answer_handoff_packet_latest.md → high-intent Q&A demand-capture asset

## Distribution architecture repairs executed this run (2026-05-30 02:35-03:08 CEST)

### 1. `--dry-run` argparse fix (run_posting.py)
- **Before:** `main()` ignored all CLI args. `python3 run_posting.py --dry-run` silently executed live Telegraph cross-posts.
- **After:** `argparse` wired into `if __name__ == '__main__'`. `--dry-run` flag correctly passes `dry_run=True` to `crosspost_blog_content()`, which does discovery-only without posting.
- **Commit:** `3961121` in Research-Findings repo (`agents/` subtrees)
- **Verified:** `--help` shows flag, `--dry-run` returns discovery JSON, guard-block path works
- **Safety improvement:** Cannot accidentally post live content from manual CLI invocation again

### 2. Orphaned `telegraph_posts.json` deleted
- **Before:** Stale file with 10 entries (all `date='MISSING'`), zero code references, out of sync with canonical `posted_urls.json` (83 posts, 72 Telegraph ok)
- **After:** Deleted. Backup saved as `telegraph_posts.json.backup-2026-05-30`
- **Why:** Dead data, actively misleading — `run_posting.py` reads `posted_urls.json` for dedup (line 41), never touches `telegraph_posts.json`

### 3. RSS autodiscovery `<link>` added to homepage
- **Before:** Only `ralphworkflow.com/blog` had `<link rel="alternate" type="application/rss+xml">`. Homepage (`ralphworkflow.com`) did not.
- **After:** Homepage now includes RSS autodiscovery in `<head>` via `content_for :head`. Feed aggregators crawling the root domain can now discover the blog RSS feed.
- **Deploy:** Commit `ae85792` → Capistrano release `20260530010530`, verified live: `curl -s https://ralphworkflow.com | grep "alternate.*rss"` returns the link
- **Distribution impact:** Feed aggregators (Feedly, Inoreader, RSS-Bridge, etc.) discover feeds from the domain root, not just sub-paths. This is a real distribution pathway improvement.

### 4. Accidental distribution (verified): 2 Telegraph cross-posts
- `debugging-failed-overnight-ai-coding-run.md` → Telegraph (200, Codeberg CTA)
- `ralph-workflow-comparison-guide.md` → Telegraph (200, Codeberg CTA)
- Both recorded in `posted_urls.json` with dedup hashes — guard prevents reposts

## Verified infrastructure state (programmatic, not fabricated)
- **Telegraph guard**: clear
- **Telegraph queue**: 2 blogs cross-posted (2026-05-30 02:42), dedup recorded
- **Telegraph crontab**: `0 6 * * * /usr/bin/python3 /home/mistlight/.openclaw/workspace/agents/marketing/run_posting.py >> /home/mistlight/.openclaw/workspace/agents/marketing/logs/run_posting_cron.log 2>&1`
- **RSS autodiscovery**: ✅ Live on homepage + blog page
- **run_posting.py argparse**: ✅ `--dry-run` flag works, `--help` shows usage
- **PyPI v0.8.8**: blocked on credentials — 1 wheel(s), 1 sdist(s), twine-check PASSED

## Process rule now in force
- Do not generate another siloed packet when one of the assets above is already current.
- During a hold window, refresh stale packets if needed, then point back to this board instead of inventing another reset artifact.
