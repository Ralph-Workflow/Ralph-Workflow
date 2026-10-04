# plan artifact format

A plan is the executor's instruction set. Every active plan uses stable `### [S-n] Title` steps. The only step-less form is exactly `type: plan` with `noop: true`.

Each step has a `Type` from `file_change`, `file_create`, `file_delete`, `refactor`, `config_change`, `discovery`, or `verify`.

- Work steps require `Files`, a concrete `Verify`, and an observable `Expect`.
- `verify` requires `Verify` plus `Expect` or `Location`.
- `discovery` requires `Verify`, `Location`, or `Evidence`.
- Use `Depends on: S-n` only where ordering exists. `Satisfies`, `Rationale`, and `Evidence` add useful execution context.

Missing or inconsistent required structure blocks submission with a line- and step-anchored repair diagnostic. `schema_version` and `## Validation Overrides` are unsupported; repair the plan instead of bypassing validation.

## Example

See the complete validator-backed example at `.agent/artifact-formats/examples/plan.md`.

```markdown
---
type: plan
---

## Work

### [S-1] Characterize current retry behavior
Inspect the current default and preserve it in a focused regression.

Type: discovery
Location: tests/test_retry.py

### [S-2] Change and prove the default
Update retry handling and run the focused regression.

Type: file_change
Files:
- modify ralph/retry.py
- modify tests/test_retry.py
Depends on: S-1
Verify: pytest tests/test_retry.py -q
Expect: the focused retry tests pass with exit code 0
```

Orient, Characterize, Change, and Verify are useful ordering guidance, not required document sections. For parallel work, use `## Work Units` or `## Parallel Plan` only when the executor will consume them.

## Work Units plan format

A plan that fan-outs work across N workers uses `## Work Units` alongside the normal `### [S-n]` step list. Each unit is a stable-ID list item with a `Directories:` field that scopes which subdirectory the unit may edit; the executor dispatches units in parallel up to the policy-derived `max_parallel_workers` cap that the planning prompt renders as the unit-count budget.

### Syntax

```markdown
## Work Units

- [U-1] Per-token-key lock body
  Replace the global refresh lock with a per-token-key lock.

  Directories: src/auth

- [U-2] Race regression test
  Add a focused regression proving the per-token-key lock holds.

  Directories: tests/auth
  Depends on: U-1
```

- One `- [U-N] description` item per unit, where `<N>` is a stable positive integer and the bracket is the unit's proof ID in the development result.
- A `Directories:` field is a comma-separated inline list (one value is fine) of relative paths; every line is a subdirectory the unit is permitted to edit. Disjoint directories across units are required (no shared subdirectory between any two units).
- An optional `Depends on:` field is a comma-separated inline list of unit IDs that must complete first; the validator rejects cycles and unknown IDs.
- Steps are listed under `## Work` (or any `### [S-n]`-bearing section) as usual; the executor infers the unit that owns a step by the step's `Files:` paths falling inside the unit's `Directories:` set, and adds a cross-unit `Depends on:` edge from the consuming unit to the producing unit.
- A `## Work Units` plan and a `## Parallel Plan` plan are mutually exclusive; declare exactly one.

### Constraints

- Every unit must declare at least one `Directories:` entry; the unit may not edit the workspace root, an empty path, `.agent`, `.git`, or `.worktrees` (reserved paths).
- No two units may share a directory prefix; the validator checks pairwise path-segment overlap, not just exact matches.
- The unit count must fit within the development phase's `max_parallel_workers` cap; the cap is rendered into the planning prompt so the planner plans around it.
- Avoid step-level `Depends on:` chains that would force the cross-unit inference to add an edge in both directions between two units; that pattern produces a cycle the validator rejects.

The compact two-unit example in `.agent/artifact-formats/examples/plan.md` exercises this syntax end-to-end and round-trips through the validator.

## Submission

Submit the complete document with `ralph_submit_md_artifact` using `artifact_type: plan`. Use `ralph_edit_md_artifact` for a similar revision; it submits when the repaired draft validates. Staging is not submission. Do not write `.agent/artifacts/plan.md` directly. After a valid submission, call `declare_complete` as the final action.
