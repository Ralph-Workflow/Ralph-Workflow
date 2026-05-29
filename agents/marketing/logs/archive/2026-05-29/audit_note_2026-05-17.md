# Audit note — 2026-05-17 12:20 UTC

## Key finding
Distribution is now the bottleneck, not conversion surfaces.

## Evidence
- GitHub: 0 stars despite a full day of conversion improvements (proof bundle, quickstart, START_HERE, task-fit guide, Aider comparison, homepage)
- Codeberg: 9 stars / 2 forks — product resonates, but GitHub audience isn't converting
- Reddit: only 1 post published today; watchdog flagged no_recent_reddit_post 7 times and reddit_monitor_stale 5 times
- Post bodies are now structurally repetitive: same opener, same middle pattern, same last-paragraph product mention

## What's done (conversion surfaces — strong enough now)
- Proof bundle: published and surfaced everywhere
- First-task templates: published
- START_HERE: sharpened
- Quickstart: tightened conversion path
- Task-fit guide: surfaced on homepage
- Aider comparison: published and linked
- GitHub mirror: surfaced on all entry points

## Actions taken (2026-05-17 12:25 UTC)
1. ✅ Posted "Run both Claude code and codex" (r/ClaudeCode) — fresh body, no thesis opener, no soft Ralph closer
2. ✅ Posted "Do you actually read and review the code..." (r/ClaudeCode) — review-first angle, no repetitive structure

## Root cause of repetition
`reddit_autopost.py`'s `build_comment()` uses hardcoded body templates keyed to thread title patterns. The "merge/safety/workflow/critique" template produces the repeated "I've had the best results when I stop optimizing for more agents..." opener. The `opening_is_repetitive` and `body_similarity` checks only add a trailing sentence instead of regenerating the body.

## What needs to happen next
1. Fix the body generator: add template variants for each thread type so posts don't repeat the same structure
2. Ship 1–2 more Reddit posts this week from the remaining shortlist
3. Get write.as articles distributed — they exist but aren't reaching Reddit/HN readers
4. Consider Reddit posts that link directly to the GitHub mirror to move GitHub stars
5. Stop: adding more conversion assets while distribution is thin and post bodies are structurally repetitive

## Decision
Distribution via Reddit is the next higher-leverage move. Conversion surfaces are strong enough.
