# plan artifact format

A plan is the executor's instruction set. Parallel plans are the default; sequential execution is a fallback for real prerequisites or conflicting writers. Every active plan uses stable `### [S-n] Title` steps. The only step-less form is exactly `type: plan` with `noop: true`.

Each step has a `Type` from `file_change`, `file_create`, `file_delete`, `refactor`, `config_change`, `discovery`, or `verify`.

- Work steps should provide `Files`, a concrete `Verify`, and an observable `Expect`.
- `verify` should provide `Verify` plus `Expect` or `Location`.
- `discovery` should provide `Verify`, `Location`, or `Evidence`.
- Use `Depends on: S-n` only where ordering exists. `Satisfies`, `Rationale`, and `Evidence` add useful execution context.

Missing step types, targets, or verification details produce advisory diagnostics and preserve the submitted plan for analysis and refinement. An imperfect parallel plan is still a plan. Stable IDs and resolvable, acyclic dependencies remain required for dispatch. Reserved ownership and unreadable execution policy block unsafe dispatch. `schema_version` and `## Validation Overrides` are unsupported.

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

Plans default to parallel work to reduce elapsed time. Use as many parallel workers as distinct ready responsibilities need within active capacity. A wholly sequential schedule requires real prerequisites or conflicting edits; a step list can still have independent branches that run concurrently. Explain dependencies, order shared contracts only before their consumers, and give each unit request criteria, outputs, and focused proof. Include integration verification after fan-in.

Units may share directories or even files when their responsibilities are distinct. `Directories:` and `Paths:` are optional responsibility context, not acceptance requirements. Define coordination for shared edits before dispatch; isolated workers still need safe scopes, and conflicting edits must be integrated without lost changes. Step plans also schedule ready work concurrently; list order is not a dependency.

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
- An optional `Directories:` field is a comma-separated inline list of relative responsibility areas. Areas may overlap across units with distinct work; an omitted field is refined before isolated worker dispatch.
- An optional `Depends on:` field is a comma-separated inline list of unit IDs that must complete first; the validator rejects cycles and unknown IDs.
- Place nested `### [S-n]` steps after their owning unit item, as above. Alternatively, list steps under `## Work`; the executor infers ownership from `Files:` paths inside the unit's `Directories:` set. Cross-unit step dependencies add an edge from the consuming unit to the producing unit. The dependent pair above demonstrates ordering; units without a dependency path and with disjoint ownership run concurrently.
- A `## Work Units` plan and a `## Parallel Plan` plan are mutually exclusive; declare exactly one.

### Constraints

- Responsibility paths are optional. Declared paths may not claim the workspace root, an empty path, `.agent`, `.git`, or `.worktrees` (reserved paths).
- Shared directories and files are accepted at the planning boundary. Before isolated dispatch, refine edit scopes or coordinate shared-file changes in the main session.
- The total unit count must fit within `max_work_units`, rendered into the planning prompt. `max_parallel_workers` limits simultaneous workers; additional ready units queue.
- Avoid step-level `Depends on:` chains that would force the cross-unit inference to add an edge in both directions between two units; that pattern produces a cycle the validator rejects.

The compact two-unit example in `.agent/artifact-formats/examples/plan.md` exercises this syntax end-to-end and round-trips through the validator.

## Parallel Plan format

`## Parallel Plan` is equally supported by validation, execution, and unit proof tracking. Use the same unit items and nested steps as Work Units, with `Directories:` and/or `Paths:` as comma-separated relative edit areas. `Paths:` preserves file ownership without expanding it to the parent directory. Dependencies and owned step IDs survive normalization and dispatch. Declare exactly one of the two unit formats.

Prefer either explicit parallel format when ownership is known. A step plan remains valid and encourages concurrent ready steps; use sequential scheduling only where a concrete prerequisite or writer conflict requires it. Missing ownership produces advice for refinement before worker dispatch, rather than rejecting the planning substance.

## Submission

Submit the complete document with `ralph_submit_md_artifact` using `artifact_type: plan`. Use `ralph_edit_md_artifact` for a similar revision; it submits when the repaired draft validates. Staging is not submission. Do not write `.agent/artifacts/plan.md` directly. After a valid submission, call `declare_complete` as the final action.
