# Marketing Workflow Audit

- Generated: 2026-05-17T14:20:06.856844
- Updated: 2026-05-17T14:20 UTC
- **Bottleneck: distribution_to_GitHub_conversion** (shifted from conversion_to_free_use)
- Owned articles logged: **6**
- Reddit posts analyzed: **9**

## Adoption Metrics

| Platform | Stars | Watchers | Forks | Open Issues |
|----------|-------|----------|-------|-------------|
| GitHub   | 0     | 2        | 0     | 0           |
| Codeberg | 9     | 2        | 2     | 3           |

## Why this is the bottleneck

- Conversion surfaces are now strong: proof bundle, first-task templates, task-fit guide, Aider comparison, homepage CTAs, GitHub mirror surfaced — all shipped and synced to both Codeberg and GitHub.
- Despite this, GitHub stars remain at 0. The pipeline is awareness → consideration but not → action/GitHub click.
- Reddit posts published today kept Ralph secondary and did not link to the GitHub mirror — traffic doesn't convert to stars without the link path.
- write.as articles exist and are published but have not been submitted to Hacker News, Lobsters, or any platform that would drive real distribution.
- No distribution to broader dev communities (r/programming, r/Python, r/devops) — still concentrated in r/ClaudeCode and r/codex.
- r/programming is network-blocked from this host, but HN and Lobsters are accessible.

## Observed risks (and status)

- Repetition risk in Reddit openings: **FIXED** — freshness/variation gate added to reddit_autopost.py; today's posts used fresh body shapes.
- Conversion asset proliferation: **STOP** — surfaces are strong; further work there has diminishing returns until distribution delivers visitors.
- Stale thread reuse: **FIXED** — stale-thread guardrail and freshness scoring now in place.

## What worked today

- Conversion surfaces fully strengthened and shipped ✅
- Reddit body freshness improved (3 fresh posts, non-repetitive bodies) ✅
- Infrastructure hardened (freshness gate, stale-thread guardrail, pacing guards) ✅

## What is not working

- GitHub stars still 0 — Reddit posts without GitHub links generate awareness but not GitHub adoption.
- Owned write.as content sitting unused at their own URLs — zero distribution beyond write.as.
- Community reach narrow — r/ClaudeCode and r/codex only; broader dev channels untouched.

## Next highest-leverage moves (priority order)

1. **Link to GitHub mirror in Reddit posts** — highest leverage. Where contextually natural, include the GitHub mirror link so awareness converts to GitHub stars. Test linked vs. unlinked posts and track GitHub star movement.
2. **Submit write.as articles to Hacker News and Lobsters** — owned content (e.g. "Spec-Driven Development: How to Run Claude Code Unattended", "When Unattended AI Coding Actually Works") has not reached platforms with real distribution. These are accessible from this host.
3. **Broader community distribution** — r/programming is blocked, but HN and Lobsters are available. Consider r/Python, r/devops, r/opensource as secondary targets.
4. **Stop adding conversion assets** — surfaces are strong. Further conversion work has diminishing returns until distribution actually delivers visitors.

## Four marketing questions that messaging must answer
- what_is_it: free and open-source tool that orchestrates existing agents on your machine
- who_is_it_for: developers/teams with engineering work too big to babysit and too risky to trust blindly
- why_different: repo-native unattended orchestration that aims to leave substantial, reviewable output instead of just a transcript
- why_now: free to use now and useful for overnight project-scale work while you sleep

## Principle reference
- See `/home/mistlight/.openclaw/workspace/agents/marketing/MARKETING_WORKFLOW_PRINCIPLES.md`
- See `/home/mistlight/.openclaw/workspace/agents/marketing/FOUR_MARKETING_QUESTIONS.md`
