# Example First Task

## Good first-task example

**Task:** Add a small validation check to reject empty project names in the CLI create flow.

**Why this is a good first task:**
- narrow scope
- obvious expected behavior
- easy to verify
- low rollback risk

## Copy-paste task spec

```md
Change:
Reject empty or whitespace-only project names in the CLI create flow.

Keep unchanged:
Do not alter the rest of the creation flow or the generated file layout.

Done means:
Invalid names show a clear error and create no project.

Checks:
Existing create-flow tests still pass and new validation tests pass.
```

## Why this is better than a vague demo

This gives you a fast yes/no evaluation:
- did the validation land in the right place?
- did the tests really run?
- would you merge the result?

## After your first run

If Ralph Workflow handles a task like this well, do the public next step on **Codeberg** first:
- inspect the primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow
- star or watch it there if the result feels real
- open an issue for any first-run friction you hit

GitHub is only the mirror: https://github.com/Ralph-Workflow/Ralph-Workflow
