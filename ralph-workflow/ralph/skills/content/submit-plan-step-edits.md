---
name: submit-plan-step-edits
description: Use when the cross-section validator failure rejected a step mutation, when step numbering off-by-one after an insert/remove/move echo, when a dangling depends_on fails the build, or when an orphan AC satisfied_by_steps reference was silently dropped from the staged draft
---

# submit-plan-step-edits

## Overview

This is an **OPTIONAL** skill that lives alongside the canonical plan format
doc at `.agent/artifact-formats/plan.md`. Use it as a quick lookup before
calling any of the five step-mutation MCP tools, not as a substitute for the
format doc. The format doc is the source of truth for every field, every
per-list cap, every string-length tier, and the full step contract.

**Skill name vs MCP tool name.** This skill is named
`submit-plan-step-edits`. It covers the family of five step-mutation MCP
tools that share a single read-after-write echo contract. The umbrella
`submit-plan-artifact` skill covers the rest of the planning MCP surface
(`ralph_submit_plan_section`, `ralph_submit_plan_sections`,
`ralph_finalize_plan`, `ralph_discard_plan_draft`). The atomic
`ralph_submit_artifact` envelope for the canonical short-plan escape hatch
is documented in the format doc itself.

## When to Use

Use this skill when you are about to call any of the five step-mutation
tools. Each tool has a single mandatory payload shape and a single canonical
echo contract; both are documented below.

- `ralph_insert_plan_step` — stage a new step at a specific numbered
  position in an existing staged draft. Auto-reindexes `depends_on` and
  `AC.satisfied_by_steps` in lockstep.
- `ralph_replace_plan_step` — overwrite a single existing step's full
  payload. Auto-reindexes `depends_on` and `AC.satisfied_by_steps` in
  lockstep.
- `ralph_patch_step` — partial-update a single existing step (shallow-merge
  per `replace_plan_step_with_echo` in
  `ralph/mcp/artifacts/plan/_step_edit.py`; missing fields are preserved).
  Prefer over `ralph_replace_plan_step` when only one or two fields change.
- `ralph_remove_plan_step` — delete a single existing step. Auto-reindexes
  `depends_on` and `AC.satisfied_by_steps`; orphan AC
  `satisfied_by_steps` entries that referenced the removed step are
  silently dropped (this is the principle-of-least-surprise behavior,
  not a bug).
- `ralph_move_plan_step` — move a step to a new 1-based index in a single
  round-trip. The move auto-reindexes once, so do NOT combine it with
  insert/remove in the same draft-edit batch.

If you are not editing a staged plan draft, this skill is the wrong skill —
see the companion `submit-plan-artifact` skill for the rest of the
planning MCP surface.

## Core Flow (one-shot)

1. Read `.agent/artifact-formats/plan.md` once. It defines every step
   field, the closed `step_type` set (`file_change`, `action`, `research`,
   `verify`), the per-list caps, and the step↔AC contract. Treat it as a
   contract you must match exactly.
2. Read the current staged draft via `ralph_get_plan_draft` (it returns
   `{staged_sections, draft, source: 'draft' | 'finalized_plan'}`). The
   `step_number` arguments on the four mutation tools target the numbers
   in the current draft, NOT the numbers you used the last time you
   edited. After any insert/remove/move, all surviving step numbers are
   renumbered 1..N, and every `depends_on` and `AC.satisfied_by_steps`
   entry is rewritten through the reindex map.
3. Pick the right tool for the change:
   - Insert a new step at a specific position → `ralph_insert_plan_step`.
   - Replace a step's whole payload → `ralph_replace_plan_step`.
   - Update one or two fields of a step → `ralph_patch_step`.
   - Remove a step → `ralph_remove_plan_step`.
   - Reorder an existing step → `ralph_move_plan_step`.
4. Build the tool-specific envelope (see Worked retry envelopes below)
   and call the tool. Every successful call returns an echo payload with
   `{action, reindex_map, rewritten_depends_on,
   rewritten_ac_satisfied_by_steps, dropped_ac_satisfied_by_steps,
   total_steps, ...}` plus the action-specific field (`new_step_number`,
   `step_number`, `removed_step_number`, `from_step_number`, `to_index`).
5. Re-read the staged draft with `ralph_get_plan_draft` after the call so
   the next edit targets the new numbers — do not assume the numbers you
   passed in are still the numbers in the draft.
6. Run `ralph_validate_draft` for a dry-run check before `ralph_finalize_plan`.

**Worked retry envelope** for `ralph_insert_plan_step`:

```json
{
  "index": 2,
  "step": {
    "title": "Concrete step title",
    "content": "Detailed executor instructions",
    "step_type": "file_change",
    "priority": "high",
    "targets": [{"path": "path/to/file.py", "action": "modify"}],
    "depends_on": []
  }
}
```

**Worked retry envelope** for `ralph_replace_plan_step`:

```json
{
  "step_number": 2,
  "step": {
    "title": "Concrete step title",
    "content": "Detailed executor instructions",
    "step_type": "file_change",
    "priority": "high",
    "targets": [{"path": "path/to/file.py", "action": "modify"}],
    "depends_on": []
  }
}
```

**Worked retry envelope** for `ralph_patch_step` (shallow-merge per
`replace_plan_step_with_echo` in `ralph/mcp/artifacts/plan/_step_edit.py`;
fields you omit are preserved, NOT cleared):

```json
{
  "step_number": 2,
  "step": {
    "content": "Revised executor instructions for this step only"
  }
}
```

**Worked retry envelope** for `ralph_remove_plan_step`:

```json
{"step_number": 2}
```

**Worked retry envelope** for `ralph_move_plan_step`:

```json
{"from_step_number": 2, "to_index": 1}
```

## Recovery from a Bad Payload

When any of the five step-mutation tools rejects a payload, the helper
`_format_plan_step_edit_error` produces a structured message that names
the failing tool, the plan format doc reference, a one-shot retry
envelope, and a pointer to the bundled `submit-plan-step-edits` skill.
Read it carefully, then:

1. Confirm the tool you used (`ralph_insert_plan_step`,
   `ralph_replace_plan_step`, `ralph_patch_step`,
   `ralph_remove_plan_step`, `ralph_move_plan_step`) and that the
   envelope shape matches the one for that tool in ## Core Flow.
2. For `ralph_insert_plan_step`: confirm `index` is an integer between
   `1` and `len(steps) + 1`. Confirm `step.number` is either omitted or
   ignored — the runtime assigns a synthetic number and reindexes; passing
   a `number` that conflicts with an existing step raises a
   `PlanArtifactValidationError`.
3. For `ralph_replace_plan_step` / `ralph_patch_step`: confirm
   `step_number` is an integer that exists in the CURRENT staged draft.
   After a previous insert/remove/move, the surviving step numbers are
   renumbered 1..N; re-read the draft via `ralph_get_plan_draft` to learn
   the current numbers.
4. For `ralph_remove_plan_step`: confirm `step_number` is an integer that
   exists in the CURRENT staged draft AND that no surviving step's
   `depends_on` references it. Removing a step that another step depends
   on fails fast with `cannot remove step N; another step depends on step N`.
   Remove the dependent steps first, or edit the dependent step's
   `depends_on` to drop the doomed reference.
5. For `ralph_move_plan_step`: confirm both `from_step_number` and
   `to_index` are integers in range. `from_step_number` is the current
   1-based position, `to_index` is the destination 1-based position. Do
   NOT combine `ralph_move_plan_step` with `ralph_insert_plan_step` or
   `ralph_remove_plan_step` in the same draft-edit batch — the move
   auto-reindexes once, and a follow-up insert/remove will rewrite the
   numbers a second time. Sequence the calls: move, then insert/remove,
   or insert/remove, then move.
6. If the echo payload reports a non-empty
   `dropped_ac_satisfied_by_steps` list, an AC's `satisfied_by_steps`
   entries were silently dropped because the referenced step is gone.
   Re-fetch the plan, re-check the surviving AC ids, and either add a
   new step that satisfies the dropped AC or edit the AC to remove the
   broken reference.

**Worked retry envelope** for a `_format_plan_step_edit_error` style
failure on `ralph_insert_plan_step`:

```json
{
  "index": 2,
  "step": {
    "title": "Concrete step title",
    "content": "Detailed executor instructions",
    "step_type": "file_change",
    "targets": [{"path": "path/to/file.py", "action": "modify"}],
    "depends_on": []
  }
}
```

After every successful step-mutation call, call `ralph_get_plan_draft` to
recover the new step numbers from the reindexed draft. Do not guess the
new numbers.

## Source of Truth Reference

- `.agent/artifact-formats/plan.md` — the canonical schema for the plan
  artifact, including sections 'Step contract', 'Step-mutation
  read-after-write echo', 'Cross-section invariants', 'Plan size limits',
  and 'Cycle guard'. Bundled by Ralph Workflow and materialized into the
  workspace on demand. Every field, every per-list cap, and the step↔AC
  reindex contract are defined here.
- `.agent/artifact-formats/artifact_formats_index.md` — the index that
  lists every supported `artifact_type` (including `plan`) and points to
  each format doc.

If this skill and the format doc ever disagree, the format doc wins.

## Common Mistakes

- Treating this skill as authoritative. The format doc at
  `.agent/artifact-formats/plan.md` is the source of truth; this skill
  is a quick pointer, not a substitute.
- Renumbering steps manually after an insert/remove/move echo. The
  runtime reindexes the full steps list AND rewrites every `depends_on`
  AND every `AC.satisfied_by_steps` entry in lockstep; manually
  renumbering creates a stale draft that the cross-section validator
  will reject.
- Combining `ralph_move_plan_step` with `ralph_insert_plan_step` or
  `ralph_remove_plan_step` in the same draft-edit batch. The move
  auto-reindexes once; a follow-up insert/remove rewrites the numbers a
  second time and can leave the draft in a state the validator rejects.
  Sequence the calls.
- Treating `ralph_patch_step` as a deep-merge. The runtime applies a
  shallow-merge per `replace_plan_step_with_echo` in
  `ralph/mcp/artifacts/plan/_step_edit.py`: top-level fields you supply
  replace the existing field; fields you omit are preserved. There is no
  deep-merge of nested objects like `targets` or `depends_on` — those
  are replaced wholesale.
- Omitting `step.number` on `ralph_insert_plan_step`. The runtime assigns
  a synthetic `number` from `max(existing_numbers, default=0) + 1` and
  then reindexes the full list, so a user-supplied `number` is ignored.
  Supplying a `number` that conflicts with an existing step raises a
  `PlanArtifactValidationError`.
- Supplying a `depends_on` entry after a move without re-reading the
  draft. The move rewrites every step's `depends_on` through the reindex
  map; the new numbers you want to depend on are the numbers from the
  reindexed draft, not the numbers from before the move. Always re-read
  the draft with `ralph_get_plan_draft` before passing `depends_on` into
  a subsequent mutation.
- Suppressing the echo payload. The echo payload is the only signal that
  `rewritten_depends_on`, `rewritten_ac_satisfied_by_steps`, and
  `dropped_ac_satisfied_by_steps` were rewritten. If the echo payload is
  ignored, downstream AC references and `depends_on` arrays can drift
  silently until the cross-section validator fails them at finalize.

## Red Flags - STOP and Start Over

- "The step number did not change so I do not need to re-read the draft."
  STOP. The runtime reindexes every successful mutation; the numbers
  you see in the prompt template are not the numbers in the live draft.
  Re-read with `ralph_get_plan_draft`.
- "I will renumber manually to save a round-trip." STOP. Manual
  renumbering creates a stale draft that the cross-section validator
  rejects. The round-trip is the reindex map in the echo payload.
- "I already read `plan.md` so the skill is redundant." STOP. The skill
  is a per-tool retry envelope; `plan.md` is the schema. They cover
  different failure modes.
- "The skill is OPTIONAL therefore ignorable." STOP. The OPTIONAL marker
  means the agent may consult the skill, not that the agent may skip the
  source-of-truth format doc. The skill names the format doc explicitly.
- "I will guess the new step number instead of calling
  `ralph_get_plan_draft`." STOP. The reindex map is the only
  authoritative source of new numbers. Guessing produces an
  off-by-one draft.
- "I will combine `ralph_move_plan_step` with `ralph_insert_plan_step`
  to save round-trips." STOP. Move auto-reindexes once. A combined
  insert/remove rewrites the numbers a second time. Sequence the calls.
- "I will use `ralph_replace_plan_step` to update one field." STOP.
  `ralph_patch_step` is the shallow-merge per-tool for partial updates;
  `ralph_replace_plan_step` requires the full payload.
