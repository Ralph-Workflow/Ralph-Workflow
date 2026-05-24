# Example First Task

## Good first-task example

**Task:** Add a billing history page with date filters and CSV export, without changing invoice creation or billing calculations.

**Why this is a good first task:**
- substantial enough that orchestration is justified
- concrete enough to review honestly the next morning
- easy to verify with real UI behavior and real checks
- meaningful enough that a good result feels like actual progress, not a toy demo

## Copy-paste task spec

```md
Change:
Add a billing history page with date-range filters and CSV export.

Keep unchanged:
Do not change invoice creation, billing calculations, or existing payment flows.

Done means:
Users can open billing history, filter by date range, and export the currently filtered rows to CSV.

Checks:
Relevant billing tests pass, any new billing-history tests pass, and the app build succeeds.
```

## Why this is better than a tiny demo

This gives you a real yes/no evaluation:
- did the workflow land a coherent product slice instead of scattered edits?
- did the result stay inside the stated boundaries?
- did the checks really run?
- would you actually keep or merge the outcome?

A tiny validation fix can be useful for smoke-testing the toolchain, but it is a weak first proof of what Ralph Workflow is for. The better first evaluation is one meaningful backlog task that is too big to babysit and still concrete enough to judge.

## After your first run

If Ralph Workflow handles a task like this well, do the public next step on **Codeberg** first:
- inspect the primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow
- star or watch it there if the result feels real
- open an issue for any first-run friction you hit

GitHub is only the mirror: https://github.com/Ralph-Workflow/Ralph-Workflow
