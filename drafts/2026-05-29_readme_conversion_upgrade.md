# Distribution Action Brief — 2026-05-29T06:15 CEST

## Action Taken: Codeberg README Conversion Upgrade + Push

**What:** Merged a stronger conversion-copy README opening into the Ralph-Workflow `ralph-workflow/README.md` and pushed to Codeberg `main`.

**Why this action:**
- The Ralph-Workflow vendor submodule had uncommitted but significantly improved README copy (comparison table, "This is not a chat window" framing, external blog CTAs) that had been created in a prior marketing pass but never committed
- The upstream repo had also evolved (stronger install/docs structure, depth presets, good-first-tasks list) — the merge preserves both
- All distribution lanes remain in measurement holds or cooldown, but repo conversion surface is permanently active and zero-cost to improve
- Every visitor to Codeberg.org/RalphWorkflow/Ralph-Workflow now sees a stronger first screen

**What changed in the README:**
| Section | Before | After |
|---|---|---|
| Opening hook | "Python CLI for AI agent orchestration" | "The operating system for autonomous coding" with comparison table |
| Differentiation | Missing | 5-row comparison table (multi-agent vs single, handoff vs copy-paste, etc.) |
| "What it does" | Implicit | Explicit phase-based pipeline explanation |
| Blog CTAs | None | Two external blog links (first overnight task, real-task walkthrough) |
| Codeberg framing | "GitHub is the mirror" buried | Prominent Codeberg-primary badge + link block at top |

**Commit:** `8b614e1be` on Ralph-Workflow main
**Ralph-Site submodule:** bumped to track latest

**Lane:** `repo_conversion_surface` (autonomous, non-blocking, permanently active)

**Expected impact:** Small but durable improvement to conversion_to_free_use for every visitor who lands on the Codeberg primary repo — no cooldown, no gate, no measurement hold.

**Shared findings reused:**
- adoption_metrics_latest.json: Codeberg movement is primary success gate → every Codeberg visitor should see our strongest conversion surface
- market_intelligence_latest.json: "not a chat window" framing used consistently
