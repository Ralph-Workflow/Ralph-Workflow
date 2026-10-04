---
name: submit-plan-artifact
description: Use when authoring or revising a markdown plan artifact
version: 2.1.0
---

# submit-plan-artifact

Read `.agent/artifact-formats/plan.md`. Submit one mandatory executor-ready plan: stable `### [S-n] Title` steps, allowed `Type`, concrete targets or discovery location, real dependencies, and per-step proof.

## Author and submit

1. Ground the outcome, current behavior, target files, risks, and proof in repository evidence.
2. Cover Orient, Characterize, Change, Verify. Use a discovery step for an unknown rather than inventing a path or command.
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

`schema_version` and `## Validation Overrides` are unsupported. Repair every diagnostic directly. The only step-less document is exactly `type: plan` plus `noop: true`.

## Parallel plans (## Work Units)

For fan-out work the plan may add a `## Work Units` block alongside the normal `### [S-n]` step list. The unit count must fit within the development phase's `max_parallel_workers` cap (rendered into this prompt as the cap); a unit is a stable-ID list item:

- `- [U-N] description` (one per unit, N a positive integer)
- `Directories: <path>[, <path>...]` — one inline list field naming subdirectories the unit may edit; every path must be disjoint from every other unit's set, and the reserved paths `.agent`, `.git`, and `.worktrees` (plus the empty / root path) are never allowed.
- `Depends on: U-X[, U-Y...]` — optional inline list of unit IDs the unit must wait for; the validator rejects cycles and unknown IDs.
- Steps are listed under `## Work` as usual; the executor infers the unit that owns a step by the step's `Files:` paths falling inside the unit's `Directories:` set and adds a cross-unit `Depends on:` edge from the consuming unit to the producing unit. Avoid step-level `Depends on:` chains that would add edges in both directions between two units — that pattern produces a cycle the validator rejects.
- A `## Work Units` plan and a `## Parallel Plan` plan are mutually exclusive; declare exactly one.

The compact two-unit example in `.agent/artifact-formats/examples/plan.md` exercises this syntax end-to-end and round-trips through the validator.
