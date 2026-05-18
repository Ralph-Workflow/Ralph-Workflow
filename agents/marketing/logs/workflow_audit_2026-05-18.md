# Workflow Audit — 2026-05-18 12:23 UTC

## Bottleneck verdict
**Still conversion_to_free_use.** GitHub stars: 0. Codeberg: 9 stars, 2 forks, 3 open issues.

This has not changed. The conversion surfaces are now genuinely strong. The problem is that nobody is arriving at them.

---

## What worked since the last audit

| Action | Effect |
|--------|--------|
| DevTool Center + MadeWithStack submissions | New distribution channels opened; both pending editorial review |
| GitHub mirror CTA slug fix | GitHub-native evaluators now hit the right repo path |
| Reddit infrastructure patches (freshness, watchdog retry, pacing visibility) | Distribution loop is tighter; safe-post windows are now actionable |
| Fresh Reddit bodies (Informal-Salt827, May 17–18) | Two posts without the banned opener; concept cadence still needs work |
| write.as article set (May 11–16) | Content assets exist; distribution is the problem, not the content |

---

## What did not work / what's still broken

**1. Repeated opening line leaked again.**
"Ban" was recorded in `REDDIT_LEARNINGS.md` and acknowledged in the prior audit, but it appeared in two Informal-Salt827 posts (May 16 `critique-my-workflow` and one earlier post). The autoposter's `body_similarity` check only appends a trailing sentence instead of regenerating the body. The fix is incomplete.

**2. Six write.as articles have zero distribution.**
Published May 11–16. Nobody read them. They exist at their own URLs with no incoming links, no HN/Lobsters submission, no track from any live surface. Weeks of content work producing zero funnel effect.

**3. Reddit pacing gate is active.**
`volume_guard_active: 3_posts_in_6h` — this is correct account hygiene, not a failure, but it means Reddit is not a high-frequency lever right now.

**4. GitHub stars at zero.**
All the conversion surfaces point at the GitHub mirror, but the mirror has 0 stars. Something in the awareness→GitHub path is broken. Either the Reddit traffic isn't linking to GitHub with enough consistency, or the evaluators who arrive don't convert to stars.

---

## What to stop doing

- **Adding conversion assets.** The surfaces are strong enough: proof bundle, first-task templates, START_HERE, quickstart, task-fit guide, Aider comparison, worktrees comparison, unattended-coding-agent page, homepage CTAs. Further additions have diminishing returns. The bottleneck is not surface quality.
- **Forcing a Reddit post quota.** The monitor keeps finding 6 shortlist-worthy threads, but only 2–3 pass the stricter "helpful reply + natural product mention" gate. Stop treating the 6 as a target.
- **Undifferentiated awareness content.** write.as articles that don't link to GitHub or point at a live surface are dead weight until they have a distribution path.

---

## What to start doing (next higher-leverage moves)

### Priority 1: Get an HN account and submit the strongest write.as article
The strongest existing asset with the clearest distribution path is the May 16 write.as piece on reviewable output or the "when unattended coding works" article. Submitting to HN with a GitHub mirror link would create real referral traffic to the conversion surfaces. This is the single highest-leverage move available right now.

**Action:** Acquire an HN account (or find a delegate path). Submit with title + GitHub mirror + one-line framing anchored to the "wake up to reviewable output" promise.

### Priority 2: Fix the autoposter body freshness gate end-to-end
The `opening_is_repetitive` / `body_similarity` check appends a trailing sentence. It should regenerate the body. This is a one-file fix in `reddit_autopost.py`.

**Action:** Update `build_comment()` so that when `body_similarity > 0.7`, the body is regenerated from scratch using a different template family, not patched with a trailing sentence.

### Priority 3: Reddit posts that link to GitHub
Two fresh high-fit threads from the latest monitor:
- `r/ClaudeCode` — "Claude Code Agent Teams W/ Gemini and Codex" (May 17)
- `r/ClaudeCode` — "Autonomous Claude Code runs in the new reality" (May 17)

Both are strong mention fits. The autoposter's GitHub-link CTA is now automated. Post when the pacing window clears.

**Action:** When `reddit_watchdog.py` reports `volume_guard_active: false`, post to those two threads.

### Priority 4: Submit a second write.as article to Lobsters
Lobsters accepts anonymous submissions if the content is good enough. The "how to tell if an AI coding task is actually done" piece fits the Lobsters aesthetic well.

**Action:** Submit from the same environment using a direct POST to the Lobsters submissions endpoint, or flag for manual submission if blocked.

---

## Four marketing questions — still answered

| Question | Status |
|----------|--------|
| What is it? | ✅ Free/OSS; orchestrates existing agents on your machine |
| Who is it for? | ✅ Developers with work too big to babysit / too risky to trust blindly |
| Why different? | ✅ Repo-native unattended; leaves substantial reviewable output |
| Why now? | ✅ Free to use; overnight project-scale work; wake up to reviewable output |

---

## Repetition risk — current state

The banned opener `"I've had the best results when I stop optimizing for more agents and start optimizing for reviewable work units."` is still in the logged post bodies and the analysis. The concept cadence (thesis opener → scope/diff/receipt advice → product mention) is still the dominant post shape. 

Future posts should vary:
- Opening move (thesis vs. question vs. concrete example vs. disagreement)
- Concept cadence (don't always go thesis → advice → product)
- Product mention placement (not always final paragraph)

---

## Summary

The conversion surfaces are built. The distribution infrastructure is solid. The remaining bottleneck is **awareness delivery**: the content exists but isn't reaching the people who would convert. The highest-leverage move is submitting existing write.as content to HN and Lobsters, then posting to the two fresh Reddit threads when the pacing window clears. Secondarily, fix the body freshness gate so future Reddit posts don't replay the same concept cadence even without the banned opener.
