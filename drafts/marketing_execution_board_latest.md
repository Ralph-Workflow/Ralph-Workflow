# Ralph Workflow Marketing Execution Board
Generated: 2026-05-30T10:18:06

## Why this board exists
- Codeberg is still flat in the active window (9 samples; stars +0, watchers +0, forks +0).
- Multiple live lanes already exist, so this board consolidates the best executable assets instead of letting them stay siloed across separate packet files.
- Use this as the single follow-through surface during measurement holds and overlapping review windows.

## Active review windows
- Apollo next review: 2026-05-29T09:00:01.629178+02:00
- Apollo launch review: 2026-06-05T09:00:01.629178+02:00
- Short review-window congestion clears at: 2026-05-30T11:02:59
- Post-hold marketer rerun scheduled: 2026-05-30T11:02:59
- StackOverflow demand-capture packet was already delivered for manual placement in the current review window; do not redeliver it until a genuinely new placement path exists.
- StackOverflow demand-capture packet is exhausted for this review window; do not redeliver it until a genuinely new placement path exists.
- Comparison backlink packet was already manually delivered in the current review window; do not surface it again until that window expires or the prepared target set changes.
- Directory secondary-surface repair already shipped in the current review window; do not requeue it until the documented follow-up date or the live target set changes.

## Best executable assets still waiting
### 1. Apollo runtime-blocker review packet
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

## Verified infrastructure state (programmatic, not fabricated)
- **Telegraph guard**: clear
- **Telegraph queue**: 1 blog pending cross-post (dry-run discovery verified), 0 already posted
- **Telegraph crontab**: `0 6 * * * /usr/bin/python3 /home/mistlight/.openclaw/workspace/agents/marketing/run_posting.py >> /home/mistlight/.openclaw/workspace/agents/marketing/logs/run_posting_cron.log 2>&1`
- **PyPI v0.8.8**: blocked on credentials — 1 wheel(s), 1 sdist(s), twine-check PASSED

## Process rule now in force
- Do not generate another siloed packet when one of the assets above is already current.
- During a hold window, refresh stale packets if needed, then point back to this board instead of inventing another reset artifact.

## Distribution architecture repair completed (2026-05-30 ~10:40)
- **Commit**: 5feeb81 — BlogPosting JSON-LD enrichment + inline Codeberg CTA
- **Deployed**: release 20260530084034, verified live at 2026-05-30T08:40:57Z
- **What changed**:
  - `json_ld_article` now includes author, publisher (Organization w/ logo), image, dateModified, mainEntityOfPage — all 4 required fields for Google Article rich result eligibility
  - `_meta_tags.html.erb` passes og_image to json_ld_article
  - `blog/show.html.erb` now has an inline Codeberg-first CTA between header and content on every post
- **Verified**: BlogPosting JSON-LD confirmed live, inline CTA confirmed on all blog posts
- **IndexNow**: 92 pages submitted post-deploy
- **Current hold**: still active until 11:02:59 CEST — post-hold reentry scheduled
