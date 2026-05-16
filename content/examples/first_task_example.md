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
