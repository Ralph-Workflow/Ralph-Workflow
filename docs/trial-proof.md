# Trial Proof: What Good Ralph Workflow Output Looks Like

The fastest honest way to evaluate Ralph Workflow is not to ask whether it generated code.

Ask whether it gave you something you would actually review and merge.

## Example first task

**Task:** Add validation so the CLI rejects empty project names before creating files.

Why this is a good first task:
- narrow scope
- obvious expected behavior
- easy to verify
- low rollback risk

### One-paragraph spec

When a user runs the project creation flow, reject empty or whitespace-only project names before any files are created. Keep the rest of the flow unchanged. Add or update tests to cover the validation. Done means the CLI shows a clear error, no project is created for invalid input, and tests pass.

## Example review bundle

A useful unattended result should not just say “done.”

It should look more like this:

### Task
Add empty-project-name validation to the CLI create flow.

### Changed files
- `cli/create.py`
- `tests/test_create.py`

### What changed
- added validation for empty / whitespace-only names before file creation
- returned a clear user-facing error message
- kept the rest of the flow unchanged

### Checks run
- unit tests for create flow
- lint / formatting checks if applicable

### Open questions
- should the same validation also reject reserved names?
- should the UI prompt trim whitespace before validation?

## Final review question

**Would you merge this?**

If the answer is not obvious, the handoff is not clean enough yet.
