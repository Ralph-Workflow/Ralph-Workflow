---
name: submit-plan-step-edits
description: Use when editing one native-markdown plan step by stable S-<n> ID with ralph_edit_md_plan_step, or recovering from an invalid replacement block, unknown ID, index, dependency, or acceptance-criteria reference
version: 2.1.0
---

# submit-plan-step-edits

## Overview

`ralph_edit_md_plan_step` applies one edit to `## Steps` in the persisted plan
draft, validates the full plan, and saves the edited markdown.

Step IDs are stable identifiers and are never renumbered. Moving a step changes
only its position. Insert with a new unused ID. Replace with one complete
`### [S-n] Title` block whose ID exactly matches `step_id`.

## Call shape

- `action`: `insert`, `replace`, `remove`, or `move`.
- `step_id`: stable `S-<positive-number>` ID.
- `replacement`: required for insert/replace; one complete markdown step block.
- `index`: 1-based destination; required for move, optional for insert.

| action | `step_id` | `replacement` | `index` |
|---|---|---|---|
| insert | new unused ID | required | optional, 1..n+1 |
| replace | existing ID | required | ignored |
| remove | existing ID | omitted | ignored |
| move | existing ID | omitted | required, 1..n |

## Core flow

Replace `S-2` with a full native-markdown block:

```text
action: replace
step_id: S-2
replacement: |
  ### [S-2] Clamp indexes in foo()
  Clamp negative and oversized indexes without changing the public signature.

  Type: file_change
  Files:
  - modify src/foo.py
  Depends on: S-1
  Satisfies: AC-01
```

The replacement heading, prose, and labeled fields travel together. For a
verify step, use `Type: verify` plus `Verify:` or `Location:`. For a file
change, use `Type: file_change` plus `Files:` bullets.

Perform any next edit against the same persisted draft, then submit it with
`ralph_finalize_md_artifact`. References are not rewritten: if remove makes a
`Depends on:`, `Satisfies:`, or `Satisfied by:` reference dangle, update the
dependent plan content before retrying.

## Error recovery

- `unknown step ID 'S-9'`: replace/remove/move must target an existing ID.
- `insert requires a new step ID and a replacement block`: choose an unused ID
  and include the complete matching block.
- `replacement must be a single '### [S-n] Title' step block`: remove wrappers
  or extra blocks.
- `replacement block ID ... must match step_id`: make both stable IDs equal.
- `index must be between ...`: use a 1-based position in the reported range.
- Plan diagnostics after an edit: repair dangling references or another
  cross-section invariant, then retry.
