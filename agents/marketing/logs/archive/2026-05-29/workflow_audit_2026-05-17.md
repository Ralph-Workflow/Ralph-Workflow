# Marketing Workflow Audit — 2026-05-17 18:20 UTC

## Diagnosis: Distribution-to-GitHub Conversion Is Still the Bottleneck

**GitHub: 0 stars / 2 watchers / 0 forks**
**Codeberg: 9 stars / 2 watchers / 2 forks**

The conversion surface work since the last audit has been genuinely strong. Proof bundle, first-task templates, task-fit guide, Aider comparison, multi-agent trust-break guide, review-merge guide, and the Claude Code + Codex workflow path are all shipped and surfaced. That work is done.

The bottleneck has not moved. GitHub mirrors the entire repo but converts zero visitors to stars. This is a distribution problem now — not conversion asset quality.

---

## What Actually Worked

1. **Proof assets shipped and surfaced** — the example review bundle, first-task templates, and merge-review guide give high-intent visitors a real artifact to judge, not just claims.

2. **Reddit distribution stayed fresh** — the last batch of posts used varied body shapes, avoided the hardcoded opening, and kept Ralph secondary. Posts on "Run both Claude code and codex" and "Do you actually read and review the code" were genuinely useful without being promotional.

3. **Freshness gate in autoposter** — the May 17 afternoon fix to `reddit_autopost.py` was the right call. It now refuses stale threads and scores freshness first. That protects account quality.

4. **Pacing guards** — the volume limit (4 posts / 6h) is the right safeguard, not the problem.

---

## What Is Repetitive or Low-Leverage

1. **One opening line is still flagged**: "I've had the best results when I stop optimizing for more agents and start optimizing for reviewable work units." — appears in the analysis as a known repetition risk. Any future autopost should explicitly compare against this before drafting.

2. **Full cadence repetition is the bigger risk now**: The structural cycle — small scope → checks → diff → receipt → human decides — is now a recognizable pattern even when wording is fresh. A post that mechanically follows this sequence reads as templated even if individual sentences are original. This is the main quality risk in the next cycle.

3. **write.as articles not distributed**: Six articles are published on write.as but none appear to have been submitted to Hacker News, Lobsters, or linked from any of the Reddit posts. They exist but have zero distribution beyond their own URLs. This is the single highest-leverage move left on owned content.

4. **Reddit comments don't link to GitHub**: The contextual GitHub mirror CTA was added to the autoposter logic, but the logged posts don't show GitHub links in the actual comment bodies. If GitHub stars are the metric, Reddit comments need to include the mirror link more reliably when the thread fit is high.

---

## The Four Marketing Questions — Status

| Question | Status |
|---|---|
| What is it? | ✅ Strong on all entry surfaces |
| Who is it for? | ✅ Surfaced via task-fit guide, START_HERE, homepage |
| Why different? | ✅ Aider comparison, worktrees comparison, merge-review guide |
| Why now? | ⚠️ Present but not urgent-sounding enough on Reddit |

The "why now" is still the weakest of the four in Reddit body copy. "Free and runs tonight" should be more explicit, especially in comments where readers are deciding whether to click anything at all.

---

## What the Current Bottleneck Actually Is

GitHub adoption signals are the operational Bottleneck — specifically stars and forks.

**Why GitHub visitors aren't starring:**
- The GitHub mirror URL (`github.com/Ralph-Workflow/Ralph-Workflow`) is not being driven by Reddit comment distribution reliably enough
- write.as articles, if submitted to HN/Lobsters, could send direct GitHub-intent traffic
- The Reddit comments that DID get published don't consistently include the GitHub mirror link

**The two things that would move GitHub stars:**
1. A platform post that links directly to the GitHub mirror (not just a contextual mention)
2. HN or Lobsters submission of a write.as article with the GitHub link in it

---

## Next Higher-Leverage Move

**Submit at least one write.as article to Hacker News or Lobsters.** The articles exist, they're good, and they're not being read. An HN/Lobsters submission with the GitHub mirror link would send high-intent traffic directly to a repo with strong conversion surfaces but zero stars.

Secondary: Ensure the Reddit autoposter includes the GitHub mirror URL in comment bodies on threads where the fit is high, not just when the thread is about trust specifically.

---

## Decision Log (carry forward)

- 2026-05-17 12:25 UTC: Identified distribution as bottleneck, not conversion surfaces. Correct.
- 2026-05-17 18:20 UTC: Confirmed. Conversion surfaces are strong. Distribution-to-GitHub conversion is still the blocking issue. write.as distribution is the highest-leverage move remaining.
- 2026-05-17 21:20 UTC: GitHub link injection confirmed working in last 5 posts. Bottleneck is write.as articles with zero platform distribution. HN submission is the next move; Lobsters requires invitation (longer path). Cooldown-limited on Reddit. Stop adding conversion assets.
