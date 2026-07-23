---
name: submit-plan-step-edits
description: Use when editing one plan step by its stable S-<n> ID via ralph_edit_md_plan_step (insert, replace, remove, or move), or when a step edit was rejected over an unknown step ID, a missing replacement, an out-of-range index, or a depends_on / satisfied_by_steps link broken by renumbering
version: 2.0.0
---

# submit-plan-step-edits

## Overview

`ralph_edit_md_plan_step` applies exactly one step edit to a plan markdown
document and returns the edited document. It changes only the `## Steps`
section, re-validates the whole plan, and rejects the edit if the result
would be invalid — so an accepted edit is always a valid plan.

The tool does not persist anything. Take the returned `content` and submit
it with `ralph_submit_md_artifact` (or continue staged authoring per the
`submit-plan-artifact` skill).

## Call Shape

Parameters:

- `content` (required) — the full current plan markdown.
- `action` (required) — `insert`, `replace`, `remove`, or `move`.
- `step_id` (required) — a stable step ID in `S-<positive-number>` form.
- `replacement` — the step's JSON content as an object (no `[ID]` prefix,
  no markdown). Required for `insert` and `replace`.
- `index` — 1-based position in `## Steps`. Required for `move`; optional
  for `insert` (default: append at the end).

Per-action rules, exactly as enforced:

| action | step_id | replacement | index |
|---|---|---|---|
| `insert` | must be NEW (not an existing step) | required | optional, 1..n+1 |
| `replace` | must exist | required | ignored |
| `remove` | must exist | — | ignored |
| `move` | must exist | — | required, 1..n |

## Renumbering Semantics

After every edit the steps are renumbered top-to-bottom to `S-1` … `S-n`:

- Every step's `depends_on` list is rewritten automatically to the new
  IDs, so dependencies keep pointing at the same steps.
- `satisfied_by_steps` integers inside `## Design` acceptance criteria are
  NOT rewritten. After an `insert`, `remove`, or `move` that shifts step
  numbers, re-check each criterion's `satisfied_by_steps` and update the
  Design item yourself before submitting.

## Core Flow

1. Get the current plan markdown (from your draft or `ralph_get_md_draft`
   during staged authoring).
2. Call the tool, e.g. append a verify step:

   ```json
   {
     "content": "<full plan markdown>",
     "action": "insert",
     "step_id": "S-4",
     "replacement": {
       "title": "Run the focused suite",
       "content": "Prove the regression is fixed.",
       "step_type": "verify",
       "verify_command": "pytest tests/test_foo.py -q",
       "depends_on": ["S-3"]
     }
   }
   ```

3. The tool returns `{"content": "<edited markdown>"}`. Use that document
   for the next edit or submit it.

`replacement` is the same step JSON documented in `submit-plan-artifact`:
`title` and `content` required; `file_change` steps need `targets`;
`verify` steps need `verify_command` or `location`; `depends_on` uses
`S-<n>` IDs of steps that exist after the edit.

## Error Recovery

- `unknown step ID 'S-9'` — for `replace`/`remove`/`move` the ID must
  match an existing `## Steps` item exactly.
- `insert requires a new step ID and replacement` — you reused an
  existing ID, or omitted `replacement`. Pick an unused `S-<n>` (it is
  renumbered afterwards anyway) and pass the step object.
- `action must be replace, insert, remove, or move; move requires index`
  — check the action spelling and pass `index` for `move`.
- `index must be between 1 and <n>` — the position is 1-based and bounded
  by the resulting step count.
- Validation errors after the edit (the plan-level diagnostics from
  `submit-plan-artifact`) mean the edit would produce an invalid plan —
  commonly a criterion's `satisfied_by_steps` pointing at a step number
  that no longer exists. Fix the `## Design` item first, then retry.
