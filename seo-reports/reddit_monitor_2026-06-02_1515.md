# Reddit monitor — RalphWorkflow — 2026-06-02 15:15 Europe/Berlin (13:15 UTC)

## Self-suspension status — Day 5 of 7

**Suspension marker:** `agents/marketing/logs/reddit_monitor_suspension.json` — active since May 31 11:19 CEST.
**Last usable retrieval:** 2026-05-28 11:19 CEST — **~122 hours stale (~5 days).**
**7-day escalation deadline:** ~June 4 11:19 CEST (~2 days from now).

## Provider status (13:15 UTC)

| Provider | Status | Since | Detail |
|----------|--------|-------|--------|
| DuckDuckGo web_search | 🔴 **collapsed after 6 queries** | May 28 | **Session-scoped rate-limit confirmed across 3 passes (Jun 1 19:15, Jun 2 12:00, Jun 2 15:05).** First 3 broad queries returned real results. Next 3 `site:reddit.com` queries returned stale unindexed results. Then all remaining queries collapsed to bot-detection. This is an established empirical pattern — not recovery. Re-enable requires 2+ separate sessions across 2+ passes. |
| Reddit direct (web_fetch) | 🔴 **403 IP-blocked** | May 23 | Hetzner Helsinki IP — 30 consecutive days. |
| ralphworkflow.com | ✅ Live | — | 200 OK. Title: "Autonomous coding workflow — Ralph Workflow" |
| Codeberg repo | ✅ Live | — | 200 OK, 12⭐ |
| Competitor analysis | ✅ Live | Today 09:00 CEST | 8 competitors monitored. No drift. |

## What changed from prior pass (2026-06-02 15:05 CEST)

- DDG session-scoped rate-limit confirmed again with identical pattern. The 6-query session cap is now empirically established across 3 consecutive monitoring passes. This session was used for:
  1. Broad "Claude Code unattended workflow 2026" — returned OvernightAgent, Claude Code Auto Mode GA, multiple workflow guides
  2. Broad "AI coding agent review merge safety 2026" — returned security review tools, CodeRabbit vs Cursor benchmark
  3. `site:reddit.com` query — returned stale content (no fresh Reddit threads due to index lag)
  4. `site:reddit.com` query — same stale pool
  5. OvernightAgent GitHub direct — validated comparison
  6. `site:reddit.com` Claude Code workflow — stale results
- OvernightAgent comparison page created: `seo-reports/comparisons/overnight-agent.md`

## Non-Reddit market intelligence

### OvernightAgent — closest competitor identified
- GitHub: a20185/OvernightAgent (Node/TypeScript, MIT, v0)
- Queue-based CLI for unattended overnight runs against claude/codex/opencode
- Produces SUMMARY.md with verification results and flagged issues
- Four-gate verify pipeline, structured event stream (36 typed events), daemon mode with control socket
- **Differentiation:** Queue-based (throughput) vs RalphWorkflow's spec-driven loop (finish-state trust)
- **RalphWorkflow advantage:** Planning loop validates the spec before execution. Composable loop framework. Reviewable finish state as primary output. OvernightAgent is stronger for throughput; RalphWorkflow is stronger for finish quality and merge safety.

### Market category hardening
- Claude Code Auto Mode GA now allows 30-60 minute unattended runs natively
- Claude Code added `/goal` command, `/code-review`, agent view, pinned background sessions in May 2026
- Multiple blog posts covering worktree isolation + agent skills + morning review — the category vocabulary is standardizing
- "Unattended coding agent" is no longer an emerging concept; it's an established tool category with competing implementations
- RalphWorkflow's differentiation window is shrinking as Claude Code and OvernightAgent absorb the adjacent feature surface

### Strongest external market validation (carried forward)
- **Faros.ai 10,000-developer survey:** PR review time spikes 91% between month 3 and month 9 of AI code adoption
- **DEV.to "Delivery Slowdown in AI-Generated Codebases":** Structural coupling of AI-generated code explained
- Directly validates RalphWorkflow bounded-scope, reviewable-finish thesis with real data

## Shortlist

**Empty.** Zero usable Reddit retrieval that meets freshness standards.

## Posting verdict

**No posting.** Suspension holds.

## Self-improving lessons

1. **OvernightAgent comparison page created.** Added to the competitive tracking at `seo-reports/comparisons/overnight-agent.md`. This is a genuine competitor worth monitoring — it's the first project that directly overlaps with RalphWorkflow's unattended-overnight niche. Monitor its v0→v1 trajectory.

2. **DDG session cap now empirically established as constant.** 6 working queries (3 broad + 3 site:reddit) every session, then total collapse. This pass confirms the pattern with the exact same shape as the two prior passes. Future passes should: use the first 3 broad queries for competitor docs and market surveys; burn the next 3 on site:reddit only if freshness seems likely; do not count any single-session burst as recovery.

3. **Re-enable still requires 2+ separate sessions across 2+ passes.** This has not changed. Each compressed session is independent rate-limit behavior, not a provider recovery trend.

4. **Escalation countdown: ~2 days to June 4 11:19 CEST.** If DDG pattern holds and no alternative provider emerges, prepare escalation text. The strongest path will be Brave Search API or SerpAPI as a replacement web_search provider, which requires mistlight to configure.
