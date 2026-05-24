# Example Review Bundle

A useful unattended result should not just say “done.”

It should look more like this:

## Task
Add a billing history page with date-range filters and CSV export without changing invoice creation or billing calculations.

## Changed files
- `app/billing/history/page.tsx`
- `app/billing/history/export.ts`
- `components/billing/history-table.tsx`
- `tests/billing-history.spec.ts`

## What changed
- added a billing history page that lists invoice rows with a date-range filter
- added CSV export for the currently filtered rows
- kept invoice creation and billing calculation logic untouched
- surfaced the export action in the billing history UI instead of spreading it across unrelated screens

## Checks run
- billing feature tests
- new billing-history tests
- app build
- lint / formatting checks if applicable

## Open questions
- should CSV export include all columns by default or only the visible subset?
- do very large export jobs need a background-job path later?

## Final review question
Would you merge this?

If the answer is not obvious, the handoff is not clean enough yet.

## After a good morning-after handoff

If this is the kind of finish you want, use **Codeberg** as the main public home:
- inspect the primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow
- star or watch it there if the workflow earned it
- file an issue with the task shape or review gap if something still felt weak

GitHub is the mirror only: https://github.com/Ralph-Workflow/Ralph-Workflow
