---
name: submit-plan-artifact
description: Use when authoring or revising a markdown plan artifact
version: 2.1.0
---

# submit-plan-artifact

Read `.agent/artifact-formats/plan.md`. The plan is the executor's instruction set. Submit a parallel plan by default, with stable `### [S-n] Title` steps, concrete ownership, real dependencies, and focused proof. These are authoring recommendations: submission and phase loading accept plan text without schema or content validation. Only binary control characters are rejected. Frontmatter and step blocks are optional.

## Author and submit

1. Ground the outcome, current behavior, target files, risks, and proof in repository evidence.
2. Cover Orient, Characterize, Partition, Change, Verify. Use a discovery step for an unknown rather than inventing a path or command. Partition distinct responsibilities into work units, with shared contracts completed before their consumers.
3. Optionally check with `ralph_verify_md_artifact`, then submit with `ralph_submit_md_artifact` using `artifact_type: plan` and the full text.
4. For a similar revision, use `ralph_edit_md_artifact` on the staged draft; it submits when valid. Use `ralph_stage_md_artifact` with `replace_all` only for a wholesale rewrite. `ralph_get_md_draft` inspects the draft and `ralph_finalize_md_artifact` submits an assembled staged draft.
5. `ralph_discard_md_draft` is only for a genuine wholesale restart.

Worked example:

```markdown
---
type: plan
---

## Work

### [S-1] Characterize the retry default
Inspect the current retry behavior and add a focused regression before changing it.

Type: file_change
Files:
- modify ralph/retry.py
- modify tests/test_retry.py
Verify: pytest tests/test_retry.py -q
Expect: the regression fails before the change

### [S-2] Change and prove the timeout behavior
Implement the smallest timeout change, then run the focused regression.

Type: file_change
Files:
- modify ralph/retry.py
- modify tests/test_retry.py
Depends on: S-1
Verify: pytest tests/test_retry.py -q
Expect: the focused retry tests pass with exit code 0

## Verification
- [V-1] pytest tests/test_retry.py -q
  Expect: the focused retry tests pass with exit code 0
```

Use `ralph_verify_md_artifact` before submission when a fast diagnostic preview helps. Use `ralph_stage_md_artifact`, `ralph_get_md_draft`, and `ralph_finalize_md_artifact` for an assembled draft; use `ralph_discard_md_draft` only for a genuine wholesale restart.

Frontmatter, step blocks, and metadata are optional. Plan content and formatting do not produce validation diagnostics. Use `noop: true` only to explicitly skip development.

## Parallel plans (## Work Units)

Parallel plans are the default. Use `## Work Units` or the equally supported `## Parallel Plan` for distinct responsibilities with optional directories and/or explicit `Paths:` files. Step plans also run independent ready steps concurrently; sequential scheduling is a fallback for concrete prerequisites or conflicting edits. Total units must fit `max_work_units`; `max_parallel_workers` limits concurrent workers, with additional ready units queued. Refine incomplete step details during analysis and execution. A unit is a stable-ID list item:

- `- [U-N] description` (one per unit, N a positive integer)
- Optional `Directories: <path>[, <path>...]` or `Paths:` names responsibility areas. Units may share directories or files with distinct responsibilities and a shared-edit coordination strategy. Reserved `.agent`, `.git`, `.worktrees`, empty, and root paths are never allowed. Use as many workers as ready independent work needs within active capacity.
- `Depends on: U-X[, U-Y...]` — optional inline list of unit IDs the unit must wait for; keep prerequisites resolvable and acyclic when preparing execution.
- Nest `### [S-n]` steps after the owning unit item, or list steps under `## Work` and let `Files:` paths determine ownership. Step dependencies add cross-unit edges from consumer to producer. Avoid edges in both directions between two units; they prevent the scheduler from finding ready units.
- A `## Work Units` plan and a `## Parallel Plan` plan are mutually exclusive; declare exactly one.

```markdown
## Work Units

- [U-1] Shared retry contract
  Directories: src/contracts

### [S-1] Define the retry contract
Document the accepted retry limit in the shared type.

Type: file_change
Files:
- modify src/contracts/retry.py
Verify: pytest tests/contracts/test_retry.py -q
Expect: retry contract tests pass with exit code 0

- [U-2] Retry consumer
  Directories: src/client
  Depends on: U-1

### [S-2] Adopt the retry contract
Update the client to consume the completed contract.

Type: file_change
Files:
- modify src/client/retry.py
Depends on: S-1
Verify: pytest tests/client/test_retry.py -q
Expect: client retry tests pass with exit code 0
```

The dependent pair illustrates ordering; additional units with disjoint ownership and no dependency path run concurrently. The full example at `.agent/artifact-formats/examples/plan.md` includes the artifact frontmatter. Replace illustrative paths and commands with repository evidence before submission.
