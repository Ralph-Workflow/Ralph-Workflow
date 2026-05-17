# Marketing Workflow Audit

- Generated: 2026-05-17T04:20:07.839677
- Current bottleneck: **conversion_to_free_use**
- Owned articles logged: **6**
- Reddit posts analyzed: **5**

## What actually worked (last 24h)
- `docs/why-worktrees-are-not-enough.md` — strong differentiation asset against worktrees; directly answers "why different" for high-intent evaluators
- `docs/when-unattended-coding-fits.md` — first-task fit guide; helps visitors self-select before trying, reducing bad-first-impression conversions

## What did not work
- Reddit autopost failed (Playwright textarea timeout on r/ClaudeCode "Critique my Workflow")
- Multiple monitor passes → shortlist → no-post cycles (low-leverage pipeline overhead)
- Dev.to auth blocked, Reddit network-blocked from host; platform access constraints limit distribution
- Repetitive opening line in Reddit posts now flagged in two separate analyses

## What is repetitive
- write.as articles: 6 articles covering overlapping themes (reviewable output, done criteria, when unattended works, is it done) — no new angles in recent articles
- Reddit posts: similar threads (overnight drift, workflow critique, Claude+Codex handoff) from same account
- Monitor → analyze → no-post cycles repeated without fresh angles to show for it

## What is low leverage right now
- More awareness/content work without a clearer conversion path from repo landing to first trial
- More Reddit posting without a distinct opening-line library and thread-specific angles
- Monitoring passes that don't produce posts

## Bottleneck in detail
GitHub: 0 stars. Codeberg: 9 stars, 3 open issues.
The 3 open Codeberg issues likely represent people who found the repo, tried it, and hit friction — direct conversion funnel feedback.
Docs conversion assets (worktrees comparison, first-task guide) may not be prominently linked from README root.

## Four marketing questions — status
All four anchoring questions remain answered in current messaging:
- what_is_it: free and open-source tool that orchestrates existing agents on your machine
- who_is_it_for: developers/teams with engineering work too big to babysit and too risky to trust blindly
- why_different: repo-native unattended orchestration that aims to leave substantial, reviewable output instead of just a transcript
- why_now: free to use now and useful for overnight project-scale work while you sleep

## Next highest-leverage moves (in priority order)
1. **Check and respond to the 3 open Codeberg issues** — these are people in the conversion funnel with friction; resolving blockers is higher leverage than any new content
2. **Surface conversion assets from README root** — ensure `why-worktrees-are-not-enough.md` and `when-unattended-coding-fits.md` are linked directly from the top-level README, not buried in a docs index — a visitor should see the best conversion assets before technical reference
3. **Pause generic write.as awareness articles** — 6 articles is sufficient for SEO/discovery; further articles should be tied to a new angle or proof asset, not re-cover existing themes
4. **Build Reddit opening-line library** — 3-4 distinct first sentences for the next autopost cycle so the repeated opening-line risk is addressed
5. **Reduce monitor-only passes** — if a shortlist doesn't produce a post, log the reason and move on rather than repeating the same scan without action

## Principle reference
- See `/home/mistlight/.openclaw/workspace/agents/marketing/MARKETING_WORKFLOW_PRINCIPLES.md`
- See `/home/mistlight/.openclaw/workspace/agents/marketing/FOUR_MARKETING_QUESTIONS.md`
