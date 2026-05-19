# Example First Task

## Good first-task example

**Task:** Add a small validation check to reject empty project names in the CLI create flow.

**Why this is a good first task:**
- narrow scope
- obvious expected behavior
- easy to verify
- low rollback risk

## One-paragraph spec

When a user runs the project creation flow, reject empty or whitespace-only project names before any files are created. Keep the rest of the flow unchanged. Add or update tests to cover the validation. Done means the CLI shows a clear error, no project is created for invalid input, and tests pass.

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
