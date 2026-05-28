# Primary-Repo Flat Contact Discovery Repair
Generated: 2026-05-28T02:52:20+02:00

## Why this ran
The board was still in truthful measurement hold, so another Reddit, Apollo, StackOverflow, curator, comparison, or manual packet refresh would have been fake progress. The live bottleneck is still distribution and message to primary repo conversion, which made shared publisher-contact discovery the highest-leverage same-run repair lane.

## What I repaired
- Added **Dupple** to the shared publisher discovery set using its live article `https://dupple.com/learn/claude-code-vs-cursor`.
- Seeded explicit contact routes for the next truthful follow-through window:
  - `louis@dupple.com`
  - `techpresso@dupple.com`
- Refreshed the latest shared discovery artifact so the post-hold selector can reuse it immediately.
- Fixed a crawl-quality bug uncovered during the rerun: placeholder junk like `email@domain.com` is now filtered instead of leaking into outreach targets.
- Added regression tests for both the Dupple explicit-email path and the placeholder-email filter.

## Validation
- `python3 -m unittest agents.marketing.tests.test_primary_repo_flat_contact_discovery -q` ✅
- `python3 agents/marketing/primary_repo_flat_contact_discovery.py` ✅

## Outcome
New shared discovery target is now present and clean:
- **Target:** Dupple
- **Hook:** Claude Code vs Cursor in 2026: Which AI Coding Tool Should You Use?
- **Next step:** email/contact send path is now identified
- **Artifact:** `/home/mistlight/.openclaw/workspace/agents/marketing/logs/primary_repo_flat_contact_discovery_latest.json`
- **Draft:** `/home/mistlight/.openclaw/workspace/drafts/primary_repo_flat_contact_discovery_latest.md`

Primary repo CTA remains Codeberg first:
- https://codeberg.org/RalphWorkflow/Ralph-Workflow

GitHub mirror stays secondary:
- https://github.com/Ralph-Workflow/Ralph-Workflow

## Why this matters
This turns a dead hold-window slot into a reusable contact-discovery asset for a 500K+ newsletter-and-tools distribution surface, while also cleaning a real data-quality bug that would have polluted future outreach packets.
