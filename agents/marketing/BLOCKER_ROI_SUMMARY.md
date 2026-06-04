# Marketing Blocker ROI Summary

Generated: 2026-05-31T15:10 UTC | Updated: 2026-06-04T00:43 UTC (audit #24)
Purpose: One human-readable artifact that maps each structural blocker to the adoption
signal it unlocks, so a human (or external agent) can prioritize the highest-ROI unblock.

## CRITICAL UPDATE (June 4 00:43 UTC)

- **DDG + Brave both dead**: DDG HTTP 202 since May 28 (7 days tomorrow), Brave returns 0 results. Escalation deadline: June 4 11:19 CEST (~9h). Both search providers blocked.
- **social_proof_bootstrap handler DEPLOYED**: The June 3 06:20 audit claimed to add it but never did. Now actually exists in `distribution_lane_executor.py`. Circuit-breaker → handler → execution flow verified.
- **4 dead cron jobs killed**: reddit-monitor (5x/day), reddit-pipeline-watchdog (3x/hr), distribution-monitor (6x/day), apollo-channel-monitor (4x/day). Reddit suspended since May 31, Apollo terminated in architecture.
- **All 7 external lanes remain blocked**: SMTP, Apollo, PyPI token, gh auth, Reddit (suspended), HN/Lobsters, dev.to — all require human credentials.

## State at a glance

| Metric | Value |
|---|---|
| Codeberg stars | 12 (flat, 9 samples) |
| Codeberg watchers | 2 (flat) |
| Codeberg forks | 2 (flat) |
| PyPI downloads/month | 1,297 (real usage signal) |
| SO drafts | 12 (0 posted by human) |
| Curator email drafts | 30+ (unsent, SMTP_USER missing) |
| Blog posts | 41 (saturated) |
| StackOverflow questions scored | 7 |
| Apollo sequence | 5 contacts, Cloudflare-blocked |

## Blocker → ROI mapping (highest first)

### 1. SMTP_USER missing → all email lanes
**Cost to unblock:** One SMTP credential (SendGrid/Mailgun/similar) in env.
**Unlocks:** 30+ curator email drafts, Apollo sequence delivery verification,
publisher contact form responses, outreach follow-ups.
**Why highest-ROI:** No outbound lane that requires email works without it.
Every curator, publisher, and comparison outreach execution creates a draft
that piles up in `/workspace/drafts/` — none ship.
**If blocked forever:** The system can only write content, score questions,
and produce drafts that never reach humans. No email-based backlink acquisition
can work.

### 2. Apollo Cloudflare auth blocked → managed outbound
**Cost to unblock:** Browser-capable runtime (not headless), or Apollo API key
with bypass configured.
**Unlocks:** Apollo sequence launch (5 verified curator contacts waiting),
Codeberg-primary CTA delivery, live outbound measurement.
**Why second:** A 5-contact warm list is sitting idle with a Codeberg CTA sequence
ready to send. The sequence itself is already verified non-empty.
**If blocked forever:** The marketing loop's highest-intent outreach path
(curators who could link to Codeberg) stays permanently theoretical.

### 3. PYPI_TOKEN missing → v0.8.8 unpublished
**Cost to unblock:** Valid PyPI API token.
**Unlocks:** v0.8.8 publication, which creates a distribution event that
can trigger blog posts, Reddit mentions, and outbound pitch hooks.
**Why important:** 1,297 monthly downloads from a stale version means real
users who could upgrade. Each release is a legitimate news event that opens
conversations on Hacker News, Reddit, and StackOverflow.
**If blocked forever:** No release-driven adoption cycles. Blog content and
comparison pages slowly rot.

### 4. Google Search Console API disabled
**Cost to unblock:** GSC API credentials with property access.
**Unlocks:** Search query intelligence — which pages rank, which terms
drive traffic, what competitors show for Codeberg-visible terms.
**Why lower:** Intelligence only; the autonomous loop can't create backlinks
from it. But it removes guesswork from comparison page targeting.

### 5. Reddit blocked from environment
**Cost to unblock:** Reddit-aware runtime or API access.
**Unlocks:** Real-time subreddit monitoring, actual posting (not just drafts),
r/opensource and r/selfhosted placement.
**Why lowest active:** Reddit already has an opening bank in
`reddit_fresh_openings.md` — the content is ready, just blocked at runtime.

## What works autonomously (no human needed)

- StackOverflow search, scoring, and drafting (uses StackExchange site search, no auth)
- Codeberg release tracking and changelog generation
- Competitor intelligence gathering
- Blog post drafting and Telegraph cross-posting
- Comparison page generation (8 competitors tracked)
- Adoption metric collection (Codeberg, PyPI, GitHub)

## What needs a human action to cross the finish line

| Action | What's waiting |
|---|---|
| Post 1 StackOverflow draft | 12 ready, 0 posted |
| Run Apollo sequence | 5 contacts + Codeberg CTA ready |
| Send 1 curator email | 30+ drafts ready |
| Publish v0.8.8 to PyPI | Built, just needs token |

## Decision framework

If no human action is possible this week:
- The system should focus on owned content that drives Codeberg adoption
  (comparison pages, repo proof assets, quickstart improvements)
- Stop generating assets that require blocked lanes (curator emails, SO drafts above cap)
- Move from "produce more" to "improve existing conversion paths"

If one human action can be done:
- Post 1 StackOverflow answer (12 ready, choose highest-scored) —
  this proves the drafting pipeline and gives the autonomous loop permission
  to scale that lane
