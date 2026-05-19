# Example Review Bundle

A useful unattended result should not just say “done.”

It should look more like this:

## Task
Add empty-project-name validation to the CLI create flow.

## Changed files
- `cli/create.py`
- `tests/test_create.py`

## What changed
- added validation for empty / whitespace-only names before file creation
- returned a clear user-facing error message
- kept the rest of the creation flow unchanged

## Checks run
- unit tests for create flow
- lint / formatting checks if applicable

## Open questions
- should the same validation also reject reserved names?
- should the UI prompt trim whitespace before validation?

## Final review question
Would you merge this?

If the answer is not obvious, the handoff is not clean enough yet.

## After a good morning-after handoff

If this is the kind of finish you want, use **Codeberg** as the main public home:
- inspect the primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow
- star or watch it there if the workflow earned it
- file an issue with the task shape or review gap if something still felt weak

GitHub is the mirror only: https://github.com/Ralph-Workflow/Ralph-Workflow
