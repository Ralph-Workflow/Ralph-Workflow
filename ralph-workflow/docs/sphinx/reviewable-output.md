# What Good Ralph Workflow Output Looks Like

Ralph Workflow is a **free and open-source** orchestration CLI that runs the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is not that it can generate code. It is that it is built to hand back a **reviewable result** in your repo: changed files, checks, logs, and enough context to decide whether the work actually holds up.

Why use it now? Because you can test it on one real backlog task tonight and answer the only question that matters tomorrow morning: **would you merge this?**

## The wrong evaluation question

Do not ask whether the agent sounded convincing.

Do not ask whether the transcript was impressive.

Do not ask whether the model said it was done.

Those are weak trust signals.

## The right evaluation question

Ask whether Ralph Workflow handed back something a human reviewer can judge quickly and honestly.

A useful unattended run should leave you with:

- a real diff
- changed files you can inspect normally
- checks that actually ran
- notes about what changed
- open questions where uncertainty remains

## Example first task

**Task:** Add validation so the CLI rejects empty project names before creating files.

Why this is a good first task:

- narrow scope
- obvious expected behavior
- easy to verify
- cheap rollback if the run misses

### Example one-paragraph spec

```markdown
# Goal

Reject empty or whitespace-only project names before any files are created.

## Acceptance criteria

- the CLI shows a clear error for empty or whitespace-only names
- no project is created for invalid input
- the rest of the flow stays unchanged
- tests cover the validation
```

## Example review bundle

A good handoff looks more like this than a generic "done" message:

### Task

Add empty-project-name validation to the CLI create flow.

### Changed files

- `cli/create.py`
- `tests/test_create.py`

### What changed

- added validation for empty and whitespace-only names before file creation
- returned a clear user-facing error message
- kept the rest of the flow unchanged

### Checks run

- unit tests for the create flow
- lint or formatting checks if applicable

### Open questions

- should reserved names be rejected too?
- should the prompt trim whitespace before validation?

## The merge test

The real handoff question is still simple:

> **Would you merge this?**

If the answer is not obvious, tighten the task, the checks, or the workflow before trusting it with bigger work.

## Next step

- Read [Choose Your First Ralph Workflow Task](first-task-guide.md)
- Continue to [Getting Started](getting-started.md) for the install and first-run path
