---
type: plan
---

# Exact-file ownership for parallel development

Extend work-unit ownership to exact files, preserving directory ownership and
safe execution. Paths and commands below are relative to `ralph-workflow/`.
This example illustrates decomposition, not evidence that the work is complete.

## Work Units

- [U-1] Shared ownership contract
  Paths: ralph/pipeline/work_unit.py, ralph/pipeline/work_units.py, tests/test_work_units.py

### [S-1] Define and extract exact-file ownership
Add exact file paths alongside directory scopes in the work-unit model and
extraction. Infer scope from step Files only when both unit fields are absent.
Keep unknown scope in the main session and remove protected paths from briefs.
Verify: `uv run python -m pytest tests/test_work_units.py -q`.
Expect: exact-file, directory, fallback and protected-path scenarios pass.

- [U-2] Conflict-safe scheduler consumer
  Paths: ralph/pipeline/parallel/scheduler.py, tests/test_scheduler.py
  Depends on: U-1

### [S-2] Schedule disjoint ownership concurrently
Consume the shared ownership contract. Serialize file equality, directory
ancestry and file/directory containment; run excess ready units in later waves.
Verify: `uv run python -m pytest tests/test_scheduler.py -q`.
Expect: disjoint files can run together; conflicting scopes never do.

- [U-3] Operator documentation consumer
  Paths: docs/sphinx/artifacts.md, docs/sphinx/advanced-pipeline-configuration.md
  Depends on: U-1

### [S-3] Explain ownership and queued waves
Document combined Paths and Directories ownership, step Files fallback,
protected-path filtering and the concurrency limit. U-2 and U-3 can proceed
independently once U-1 defines the contract; neither edits the other's files.
Verify: `make docs`.
Expect: the Sphinx manual builds without errors or warnings.

## Integration after fan-in

### [S-4] Verify the combined behavior
Depends on: S-2, S-3
After both consumers finish, the main session checks that the manual matches
the shared contract and scheduler behavior, then runs `make verify` once.
Expect: the complete gate exits zero without errors or warnings.
