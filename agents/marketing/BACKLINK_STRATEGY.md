# Backlink Strategy — 2026-05-20

## Current State
- Backlinks: 0 indexed in the current measurement window, despite several pending or live directory placements
- SEO score: 70/100
- Ranked keywords: 0
- Primary goal: earn qualified discovery that routes to **Codeberg first** and GitHub second

## Root Cause
The problem is no longer "we have no places to submit." The real problem is:
1. **indexing velocity is slow** on the directory submissions already made
2. **some internal strategy/docs still reference dead lanes** (especially Toolhunt AI)
3. **competitor-citation outreach is blocked** by missing write-capable GitHub access or manual browser-auth paths

## Available Paths

### 1. Live / already-used directory lanes
| Directory | Current state | Action now |
|-----------|---------------|------------|
| SaaSHub | ✅ Live listing / backlink | Monitor only |
| ToolWise | ✅ Submitted and live review surface | Monitor / reuse as proof link |
| ToolShelf | ✅ Submitted via public API, awaiting review/indexing | Monitor |
| AIToolsIndex | ✅ Submitted via public API, awaiting review/indexing | Monitor |
| MadeWithStack | ✅ Submitted, editorial review pending | Monitor |
| DevTool Center | ✅ Submitted, editorial review pending | Monitor |

### 2. Manual-only or blocked lanes
| Directory / channel | Current state | Action required |
|---------------------|---------------|-----------------|
| AlternativeTo | 🔒 Login required | Manual account creation + submit |
| Product Hunt | 🔒 Real launch flow required | Manual maker account + launch prep |
| There’s An AI For That | 🟡 Manual, not automated here | Manual submission if bandwidth exists |
| Futurepedia / FutureTools / Ben's Bites / similar | 🟡 Manual editorial lanes | Manual outreach only if stronger lanes stall |

### 3. Explicitly dead or stale lanes — do not prioritize
| Directory | Why not |
|-----------|---------|
| Toolhunt AI | stale guidance; current discovery/repair notes no longer treat this as a trustworthy executable lane |
| ToolHunter / parked variants | broken or non-usable submit surfaces |

### Priority actions now
1. **Monitor live submissions for approval/indexing**
   - ToolWise review page
   - ToolShelf
   - AIToolsIndex
   - MadeWithStack
   - DevTool Center
2. **Use live proof surfaces in public CTAs** so trust-seeking evaluators see third-party validation before install
3. **Only spend manual effort on AlternativeTo / Product Hunt** if there is a deliberate human-execution window

### 2. Competitor Citations (write-access or manual-auth needed)
Repos and tools can mention Ralph Workflow without linking to it. This is still high leverage, but the writable execution path is blocked.

**When read-write GitHub access or manual browser-auth is available:**
1. File issues / PRs on repos that mention Ralph Workflow and add a direct **Codeberg** link
2. Request inclusion on comparison pages that already discuss Aider, Claude Code, Codex CLI, Hermes, or Conductor
3. Prefer citations that can link directly to `https://codeberg.org/RalphWorkflow/Ralph-Workflow`

### 3. Guest Posts / Blog Citations
- Find dev blogs or newsletters that cover unattended coding, AI coding workflows, or reviewable AI output
- Pitch concrete proof-led topics, not generic "AI orchestration"
- Use only if directory/indexing momentum stays flat

## Next Actions
1. Monitor ToolShelf / AIToolsIndex / MadeWithStack / DevTool Center for approval or indexing over the next 7-14 days
2. Keep public proof blocks pointing to ToolWise + SaaSHub while Codeberg remains the primary repo CTA
3. Do **not** spend another cycle on Toolhunt AI or other stale submit surfaces
4. When manual bandwidth exists: AlternativeTo first, Product Hunt second
5. When GitHub PAT becomes read-write: activate competitor-citation outreach aimed at Codeberg links

## When to Re-evaluate
- If the current live submissions still produce **no indexed backlink signal** and **no Codeberg star/watch/fork delta** after 14 days, replace directory work with warmer citation/outreach lanes
- If GitHub PAT becomes read-write, immediately activate repo-citation outreach