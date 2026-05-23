# Example Workflow Composition

This is what a **real Ralph Workflow run** should feel like when you try it on one bounded backlog task.

If you only want the repo first, start on **Codeberg**: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

## Example task

**Task:** Add a CSV export to a billing-history page without changing invoice creation or billing calculations.

## 1. Sharpen the task before code starts

Use one paragraph, not a vague prompt dump.

```md
Change:
Add CSV export to the billing history page.

Keep unchanged:
Do not change invoice creation, billing calculations, or existing filters.

Done means:
Users can export the currently filtered billing-history rows to CSV from the page.

Checks:
Relevant billing tests pass and any new billing-history tests pass.
```

Why this phase matters:
- it locks the finish line before implementation starts
- it keeps the run bounded enough to review later
- it gives verification something concrete to test

## 2. Build inside the workflow, not as disconnected chat hops

The implementation phase should stay scoped to the task above.

Useful expectations:
- the diff stays narrow
- unrelated cleanup is avoided
- changed files are easy to inspect
- the workflow records what was attempted and what was fixed

## 3. Verify before anyone calls it done

The workflow should run the checks named in the spec and hand back the result clearly.

A clean verification section usually includes:
- which tests ran
- whether build/lint passed
- what failed first, if anything
- what was repaired before the final handoff

## 4. Hand back a morning-after review bundle

The morning artifact should answer the merge question fast.

That bundle should include:
- the scoped task
- changed files
- what changed in plain language
- checks that really ran
- open questions or remaining risk

See also: [Review bundle example](./review_bundle_example.md)

## What this composition proves

The point is not that an agent can write code.

The point is that the workflow composes:
1. task sharpening
2. implementation
3. verification
4. morning-after review

That is the difference between a transcript and a result you can actually judge.

## Best next steps

- [Start here on one real task](../../START_HERE.md)
- [Example first task](./first_task_example.md)
- [Good unattended task vs bad one](../guides/good_unattended_task.md)
- [Review AI coding output before merge](../guides/review_ai_coding_output_before_merge.md)

## Public next step

If this is the kind of workflow you want, inspect the primary repo on **Codeberg** first and use the mirror only if you need it:

- **Codeberg (primary):** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **GitHub mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>
