# plan artifact format

A plan is the executor's instruction set. Parallel plans are the default; sequential execution is a fallback for real prerequisites or conflicting writers. Every active plan uses stable `### [S-n] Title` steps. The only step-less form is exactly `type: plan` with `noop: true`.

Each step has a `Type` from `file_change`, `file_create`, `file_delete`, `refactor`, `config_change`, `discovery`, or `verify`.

- Work steps should provide `Files`, a concrete `Verify`, and an observable `Expect`.
- `verify` should provide `Verify` plus `Expect` or `Location`.
- `discovery` should provide `Verify`, `Location`, or `Evidence`.
- Use `Depends on: S-n` only where ordering exists. `Satisfies`, `Rationale`, and `Evidence` add useful execution context.

Missing step types, targets, or verification details produce advisory diagnostics and preserve the submitted plan for analysis and refinement. An imperfect parallel plan is still a plan. Stable IDs and resolvable, acyclic dependencies remain required for dispatch. Reserved or overlapping ownership and unreadable execution policy block unsafe dispatch. `schema_version` and `## Validation Overrides` are unsupported.

## Example

See the complete validator-backed example at `.agent/artifact-formats/examples/plan.md`.

```markdown
---
type: plan
---

## Work Units

- [U-1] Retry implementation
  Directories: ralph

### [S-1] Characterize current retry behavior
Inspect the current default and preserve it in a focused regression.

Type: discovery
Location: tests/test_retry.py

- [U-2] Independent command documentation
  Directories: docs

### [S-2] Document existing retry commands
Document the current retry command while implementation is characterized.

Type: file_change
Files:
- modify docs/retry.md
Verify: make docs
Expect: the documentation builds with exit code 0
```

Orient, Characterize, Change, and Verify are useful ordering guidance, not required document sections. Add a Partition decision before Change: use an explicit parallel format for disjoint ownership, keeping shared-contract changes ahead of their consumers. Both `## Work Units` and `## Parallel Plan` are first-class parallel formats. Any step plan can also parallelize independent ready work.

Plans default to parallel work to reduce elapsed time. A wholly linear plan requires every step to depend on preceding work; a dependency chain may still have independent branches that run concurrently. Explain the output consumed by each dependency, order shared contracts only before their consumers, and give each unit disjoint ownership, request criteria, outputs, and focused proof. Include integration verification after fan-in to check that the combined result satisfies the request.

Disjoint files alone do not permit separate directory-owned Work Units. Use `## Parallel Plan` with explicit `Paths:` for independent files in the same directory or at repository root, or retain independent steps in a step plan. Step plans also schedule disjoint ready writers concurrently; list order is not a dependency.

## Work Units plan format

A plan that fans out work uses `## Work Units` alongside the normal `### [S-n]` step list. Each unit is a stable-ID list item with a `Directories:` field that scopes its edits. The planning prompt renders the `max_work_units` total-unit budget; the executor dispatches ready units up to `max_parallel_workers` concurrently.

### Syntax

    ## Work Units

    - [U-1] Per-token-key lock body
      Replace the global refresh lock with a per-token-key lock.

      Directories: src/auth

    ### [S-1] Serialize refreshes per token key
    Guard the refresh critical section with a bounded per-key lock lifecycle.

    Type: file_change
    Files:
    - modify src/auth/refresh.py
    Verify: pytest tests/auth/test_refresh.py -q
    Expect: existing refresh tests pass with exit code 0

    - [U-2] Race regression test
      Add a focused regression proving the per-token-key lock holds.

      Directories: tests/auth
      Depends on: U-1

    ### [S-2] Prove same-key refresh serialization
    Add a deterministic regression against the completed lock implementation.

    Type: file_create
    Files:
    - create tests/auth/test_refresh_race.py
    Depends on: S-1
    Verify: pytest tests/auth/test_refresh_race.py -q
    Expect: same-key refresh regression passes with exit code 0

- One `- [U-N] description` item per unit, where `<N>` is a stable positive integer and the bracket is the unit's proof ID in the development result.
- A `Directories:` field is a comma-separated inline list (one value is fine) of relative paths; every line is a subdirectory the unit is permitted to edit. Disjoint directories across units are required (no shared subdirectory between any two units).
- An optional `Depends on:` field is a comma-separated inline list of unit IDs that must complete first; the validator rejects cycles and unknown IDs.
- Place nested `### [S-n]` steps after their owning unit item, as above. Alternatively, list steps under `## Work`; the executor infers ownership from `Files:` paths inside the unit's `Directories:` set. Cross-unit step dependencies add an edge from the consuming unit to the producing unit. The dependent pair above demonstrates ordering; units without a dependency path and with disjoint ownership run concurrently.
- A `## Work Units` plan and a `## Parallel Plan` plan are mutually exclusive; declare exactly one.

### Constraints

- Every unit must declare at least one `Directories:` entry; the unit may not edit the workspace root, an empty path, `.agent`, `.git`, or `.worktrees` (reserved paths).
- No two units may share a directory prefix; the validator checks pairwise path-segment overlap, not just exact matches.
- The total unit count must fit within `max_work_units`, rendered into the planning prompt. `max_parallel_workers` limits simultaneous workers; additional ready units queue.
- Avoid step-level `Depends on:` chains that would force the cross-unit inference to add an edge in both directions between two units; that pattern produces a cycle the validator rejects.

The compact two-unit example in `.agent/artifact-formats/examples/plan.md` exercises this syntax end-to-end and round-trips through the validator.

## Parallel Plan format

`## Parallel Plan` is equally supported by validation, execution, and unit proof tracking. Use the same unit items and nested steps as Work Units, with `Directories:` and/or `Paths:` as comma-separated relative edit areas. `Paths:` preserves file ownership without expanding it to the parent directory. Dependencies and owned step IDs survive normalization and dispatch. Declare exactly one of the two unit formats.

Prefer either explicit parallel format when ownership is known. A step plan remains valid and encourages concurrent ready steps; use sequential scheduling only where a concrete prerequisite or writer conflict requires it. Missing ownership produces advice for refinement before worker dispatch, rather than rejecting the planning substance.

## Submission

Submit the complete document with `ralph_submit_md_artifact` using `artifact_type: plan`. Use `ralph_edit_md_artifact` for a similar revision; it submits when the repaired draft validates. Staging is not submission. Do not write `.agent/artifacts/plan.md` directly. After a valid submission, call `declare_complete` as the final action.
