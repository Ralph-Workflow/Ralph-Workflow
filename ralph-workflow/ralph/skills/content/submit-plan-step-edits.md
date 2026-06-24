---
name: submit-plan-step-edits
description: Use when a cross-section validator failure or validation_warnings involves a plan step mutation, step numbering off-by-one, dangling depends_on, orphan AC satisfied_by_steps, or step schema repair
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
`ralph_finalize_plan`, `ralph_discard_plan_draft`).

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
  `depends_on` and `AC.satisfied_by_steps`; references to the removed
  step are preserved as staged JSON and reported in
  `validation_warnings` so validation can fail without losing data.
- `ralph_move_plan_step` — move a step to a new 1-based index in a single
  round-trip. The move auto-reindexes once, so do NOT combine it with
  insert/remove in the same draft-edit batch.

If you are not editing a staged plan draft, this skill is the wrong skill —
see the companion `submit-plan-artifact` skill for the rest of the
planning MCP surface.

## Core Flow (step mutation)

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
    "title": "Add the foo() regression test",
    "content": "Add tests/test_foo.py::test_clamp_handles_out_of_range_index before changing production code.",
    "step_type": "file_change",
    "priority": "high",
    "targets": [{"path": "tests/test_foo.py", "action": "modify"}],
    "satisfies": ["AC-01"],
    "expected_evidence": [
      {"kind": "file", "ref": "tests/test_foo.py"},
      {"kind": "test_name", "ref": "tests/test_foo.py::test_clamp_handles_out_of_range_index"}
    ],
    "depends_on": []
  }
}
```

**Worked retry envelope** for `ralph_replace_plan_step`:

```json
{
  "step_number": 2,
  "step": {
    "title": "Clamp the foo() index",
    "content": "Update src/foo.py so the lookup index is clamped to the valid range while preserving the public foo() signature.",
    "step_type": "file_change",
    "priority": "high",
    "targets": [{"path": "src/foo.py", "action": "modify"}],
    "satisfies": ["AC-02"],
    "expected_evidence": [
      {"kind": "file", "ref": "src/foo.py"},
      {"kind": "test_name", "ref": "tests/test_foo.py::test_clamp_handles_out_of_range_index"}
    ],
    "depends_on": [1]
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
    "content": "Update src/foo.py so the lookup index is clamped to the valid range while preserving the public foo() signature.",
    "targets": [{"path": "src/foo.py", "action": "modify"}],
    "expected_evidence": [
      {"kind": "file", "ref": "src/foo.py"},
      {"kind": "test_name", "ref": "tests/test_foo.py::test_clamp_handles_out_of_range_index"}
    ],
    "depends_on": [1]
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

### Add one step at a time

When you need to insert a single new step into an existing staged
draft, follow this exact worked example. Each call is independent
and round-trips through the MCP broker — there is no batching for
step mutations.

**Call 1 — `ralph_submit_plan_sections`** with complete section entries to
stage the initial draft (2 starter steps, the other required sections, and
the design AC links):

```json
{
  "entries": [
    {
      "section": "summary",
      "mode": "replace",
      "content": {
        "context": "Fix the foo() off-by-one regression and prove it with a focused unit test.",
        "intent": "Clamp foo() index so the regression cannot recur.",
        "intent_verb": "improve",
        "scope_items": [
          {"text": "Add a regression test for the out-of-range foo() index", "category": "test"},
          {"text": "Modify src/foo.py to clamp the index before lookup", "category": "file_change"},
          {"text": "Run pytest tests/test_foo.py -q to prove the regression is fixed", "category": "test"}
        ]
      }
    },
    {
      "section": "skills_mcp",
      "mode": "replace",
      "content": {"skills": ["test-driven-development", "systematic-debugging"], "mcps": []}
    },
    {
      "section": "steps",
      "mode": "replace",
      "content": [
        {
          "number": 1,
          "title": "Add the foo() regression test",
          "content": "Add tests/test_foo.py::test_clamp_handles_out_of_range_index before changing production code.",
          "step_type": "file_change",
          "targets": [{"path": "tests/test_foo.py", "action": "modify"}],
          "satisfies": ["AC-01"],
          "expected_evidence": [
            {"kind": "file", "ref": "tests/test_foo.py"},
            {"kind": "test_name", "ref": "tests/test_foo.py::test_clamp_handles_out_of_range_index"}
          ],
          "depends_on": []
        },
        {
          "number": 2,
          "title": "Clamp the foo() index",
          "content": "Update src/foo.py so the lookup index is clamped to the valid range while preserving the public foo() signature.",
          "step_type": "file_change",
          "targets": [{"path": "src/foo.py", "action": "modify"}],
          "satisfies": ["AC-02"],
          "expected_evidence": [
            {"kind": "file", "ref": "src/foo.py"},
            {"kind": "test_name", "ref": "tests/test_foo.py::test_clamp_handles_out_of_range_index"}
          ],
          "depends_on": [1]
        }
      ]
    },
    {
      "section": "critical_files",
      "mode": "replace",
      "content": {
        "primary_files": [
          {"path": "src/foo.py", "action": "modify"},
          {"path": "tests/test_foo.py", "action": "modify"}
        ]
      }
    },
    {
      "section": "risks_mitigations",
      "mode": "replace",
      "content": [
        {
          "risk": "Clamping could hide a caller bug that should remain visible in behavior expectations.",
          "mitigation": "Preserve the public signature and add focused assertions documenting the intended clamping behavior.",
          "severity": "medium"
        }
      ]
    },
    {
      "section": "verification_strategy",
      "mode": "replace",
      "content": [
        {
          "method": "pytest tests/test_foo.py -q",
          "expected_outcome": "The focused regression test passes.",
          "timeout_seconds": 60,
          "cwd": "."
        }
      ]
    },
    {
      "section": "design",
      "mode": "replace",
      "content": {
        "planning_profile": "strict",
        "outcome": "foo() handles out-of-range indexes without crashing and the regression test passes.",
        "acceptance_criteria": {
          "criteria": [
            {
              "id": "AC-01",
              "description": "A focused regression test covers the out-of-range index.",
              "satisfied_by_steps": [1]
            },
            {
              "id": "AC-02",
              "description": "src/foo.py clamps the index while preserving the public signature.",
              "satisfied_by_steps": [2]
            }
          ]
        }
      }
    }
  ]
}
```

**Call 2 — `ralph_get_plan_draft`** (no parameters) to read the
staged draft and learn the current step numbers. Do NOT guess — the
reindex map from any prior mutation may have rewritten them.

```json
{}
```

The echoed payload has the shape `{"staged_sections": [...], "draft":
{...sections...}, "source": "draft"}`. The current step numbers
live at `draft.steps[*].number`; the example draft above
contains `[1, 2]`.

**Call 3 — `ralph_insert_plan_step`** at `index=3` to insert a new
step after the existing two. The runtime assigns a synthetic
`step.number` and then reindexes the full steps list so existing
numbers stay `1..N`.

```json
{
  "index": 3,
  "step": {
    "title": "Document the foo() clamp behavior",
    "content": "Update docs/foo.md with the accepted out-of-range index behavior after the code and focused regression test are in place.",
    "step_type": "file_change",
    "targets": [{"path": "docs/foo.md", "action": "modify"}],
    "satisfies": ["AC-02"],
    "expected_evidence": [
      {"kind": "file", "ref": "docs/foo.md"},
      {"kind": "command_output", "ref": "pytest tests/test_foo.py -q", "note": "Regression test still passes after documentation update"}
    ],
    "depends_on": [2]
  }
}
```

The echo payload includes `action`, `new_step_number`, `reindex_map`,
`rewritten_depends_on`, `rewritten_ac_satisfied_by_steps`,
`dropped_ac_satisfied_by_steps`, and `total_steps`.

**Call 4 — `ralph_validate_draft`** (no parameters) for a dry-run of
the cross-section validator BEFORE finalizing. The dry-run is
read-only; it does NOT delete the staged draft on success or failure.

```json
{}
```

A successful dry-run returns `{"valid": true, ...}`; a failed one
returns `{"valid": false, "errors": [...]}` with the literal
validator message and the offending field path.

**Call 5 — `ralph_finalize_plan`** (no parameters) to write
`plan.json` and delete the staged draft. Only call this once every
required section is staged AND `ralph_validate_draft` returned
`{"valid": true}`.

```json
{}
```

The 5-call sequence is the entire happy-path for "add one step at
a time". The same pattern (read draft → mutate → validate →
finalize) applies for `ralph_replace_plan_step`, `ralph_patch_step`,
`ralph_remove_plan_step`, and `ralph_move_plan_step`.

## Correcting Rejected Step Edits

When any of the five step-mutation tools rejects a payload, the helper
`_format_plan_step_edit_error` produces a structured message that names
the failing tool, the plan format doc reference, a canonical retry
envelope, and a pointer to the bundled `submit-plan-step-edits` skill.
Read it carefully, then:

1. Confirm the tool you used (`ralph_insert_plan_step`,
   `ralph_replace_plan_step`, `ralph_patch_step`,
   `ralph_remove_plan_step`, `ralph_move_plan_step`) and that the
   envelope shape matches the one for that tool in ## Core Flow.
2. For `ralph_insert_plan_step`: `index` may be an integer or numeric
   string. Values `<= 0` insert at the beginning; values beyond
   `len(steps) + 1` append. Confirm `step.number` is either omitted or
   ignored — the runtime assigns a synthetic number and reindexes before
   validation. A conflicting user-supplied `step.number` does not survive
   the mutation.
3. For `ralph_replace_plan_step` / `ralph_patch_step`: confirm
   `step_number` is an integer that exists in the CURRENT staged draft.
   After a previous insert/remove/move, the surviving step numbers are
   renumbered 1..N; re-read the draft via `ralph_get_plan_draft` to learn
   the current numbers.
4. For `ralph_remove_plan_step`: confirm `step_number` is an integer or
   numeric string that exists in the CURRENT staged draft. If surviving
   steps or AC entries still reference the removed step, the edit stages
   unresolved JSON and returns `validation_warnings`; fix those warnings
   before `ralph_finalize_plan`.
5. For `ralph_move_plan_step`: confirm `from_step_number` is an integer
   that exists in the current staged draft. `to_index` may be an integer
   or numeric string; values `<= 0` move to the beginning and oversized
   values append. `from_step_number` is the current 1-based
   position, and `to_index` is the destination 1-based position. Do NOT
   combine `ralph_move_plan_step` with `ralph_insert_plan_step` or
   `ralph_remove_plan_step` in the same draft-edit batch — the move
   auto-reindexes once, and a follow-up insert/remove will rewrite the
   numbers a second time. Sequence the calls: move, then insert/remove,
   or insert/remove, then move.
6. If the echo payload reports non-empty `validation_warnings`, re-fetch
   or inspect the staged draft, repair the named step/AC fields, and run
   `ralph_validate_draft` before finalizing. Future numeric references
   are allowed to remain staged while you add the future step; unresolved
   removed-step references must be repaired before finalize.

**Worked retry envelope** for a `_format_plan_step_edit_error` style
failure on `ralph_insert_plan_step`:

```json
{
  "index": 2,
  "step": {
    "title": "Add the foo() regression test",
    "content": "Add tests/test_foo.py::test_clamp_handles_out_of_range_index before changing production code.",
    "step_type": "file_change",
    "targets": [{"path": "tests/test_foo.py", "action": "modify"}],
    "satisfies": ["AC-01"],
    "expected_evidence": [
      {"kind": "file", "ref": "tests/test_foo.py"},
      {"kind": "test_name", "ref": "tests/test_foo.py::test_clamp_handles_out_of_range_index"}
    ],
    "depends_on": []
  }
}
```

After every successful step-mutation call, call `ralph_get_plan_draft` to
recover the new step numbers from the reindexed draft. Do not guess the
new numbers.

## Analysis Feedback Corrections

Planning analysis commonly rejects drafts where a step is too vague, lacks
evidence, omits concrete file targets, or leaves an acceptance criterion
unproven. Fix those findings with `ralph_replace_plan_step` or
`ralph_patch_step` by adding the missing engineering detail directly to the
step payload.

### Replace a vague implementation step

Use `ralph_replace_plan_step` when the whole step is underspecified. This
example corrects analysis feedback: "Step 2 says fix foo() but does not name
the file, dependency, AC, or evidence."

```json
{
  "step_number": 2,
  "step": {
    "title": "Clamp the foo() index",
    "content": "Update src/foo.py so the lookup index is clamped to the valid range while preserving the public foo() signature. Keep the regression test from step 1 red before this change and green after it.",
    "step_type": "file_change",
    "priority": "high",
    "targets": [{"path": "src/foo.py", "action": "modify"}],
    "satisfies": ["AC-02"],
    "expected_evidence": [
      {"kind": "file", "ref": "src/foo.py"},
      {"kind": "test_name", "ref": "tests/test_foo.py::test_clamp_handles_out_of_range_index"},
      {"kind": "command_output", "ref": "pytest tests/test_foo.py -q"}
    ],
    "depends_on": [1]
  }
}
```

### Patch missing proof onto an otherwise good step

Use `ralph_patch_step` when the title and main content are already correct
but analysis feedback identifies missing proof fields. This example preserves
the rest of step 2 and adds the list-shaped fields that prove the work.

```json
{
  "step_number": 2,
  "step": {
    "targets": [{"path": "src/foo.py", "action": "modify"}],
    "satisfies": ["AC-02"],
    "expected_evidence": [
      {"kind": "file", "ref": "src/foo.py"},
      {"kind": "test_name", "ref": "tests/test_foo.py::test_clamp_handles_out_of_range_index"}
    ],
    "depends_on": [1]
  }
}
```

### Insert a verification step after implementation

Use `ralph_insert_plan_step` when analysis feedback says the plan has no
observable verification step after code changes. A verify step must include
`verify_command` or `location`; it does not satisfy ACs directly, but it does
provide evidence that the implementation steps completed.

```json
{
  "index": 3,
  "step": {
    "title": "Run the focused regression test",
    "content": "Run pytest tests/test_foo.py -q from the repository root and confirm it passes after the foo() clamp change.",
    "step_type": "verify",
    "verify_command": "pytest tests/test_foo.py -q",
    "expected_evidence": [
      {"kind": "command_output", "ref": "pytest tests/test_foo.py -q"}
    ],
    "depends_on": [2]
  }
}
```

### Per-tool retry envelopes

These fenced-JSON blocks are the **exact canonical retry shapes**
the no-skill helper `_format_plan_step_edit_error` inlines in its
step-edit guidance. Use
the matching block when a step-mutation tool returns an error.

**`ralph_insert_plan_step`** — stage a new step at a normalized
1-based position; the runtime assigns the synthetic `step.number`.

```json
{
  "index": 2,
  "step": {
    "title": "Add the foo() regression test",
    "content": "Add tests/test_foo.py::test_clamp_handles_out_of_range_index before changing production code.",
    "step_type": "file_change",
    "targets": [{"path": "tests/test_foo.py", "action": "modify"}],
    "satisfies": ["AC-01"],
    "expected_evidence": [
      {"kind": "file", "ref": "tests/test_foo.py"},
      {"kind": "test_name", "ref": "tests/test_foo.py::test_clamp_handles_out_of_range_index"}
    ],
    "depends_on": []
  }
}
```

**`ralph_replace_plan_step`** — overwrite a single existing step's
full payload; missing fields are NOT preserved (full payload required).

```json
{
  "step_number": 2,
  "step": {
    "title": "Clamp the foo() index",
    "content": "Update src/foo.py so the lookup index is clamped to the valid range while preserving the public foo() signature.",
    "step_type": "file_change",
    "targets": [{"path": "src/foo.py", "action": "modify"}],
    "satisfies": ["AC-02"],
    "expected_evidence": [
      {"kind": "file", "ref": "src/foo.py"},
      {"kind": "test_name", "ref": "tests/test_foo.py::test_clamp_handles_out_of_range_index"}
    ],
    "depends_on": [1]
  }
}
```

**`ralph_patch_step`** — shallow-merge a single existing step;
fields you omit are preserved, NOT cleared.

```json
{
  "step_number": 2,
  "step": {
    "content": "Update src/foo.py so the lookup index is clamped to the valid range while preserving the public foo() signature.",
    "targets": [{"path": "src/foo.py", "action": "modify"}],
    "expected_evidence": [
      {"kind": "file", "ref": "src/foo.py"},
      {"kind": "test_name", "ref": "tests/test_foo.py::test_clamp_handles_out_of_range_index"}
    ],
    "depends_on": [1]
  }
}
```

**`ralph_remove_plan_step`** — delete a single existing step by
its current 1-based number.

```json
{"step_number": 2}
```

**`ralph_move_plan_step`** — move a step to a new 1-based index.

```json
{"from_step_number": 2, "to_index": 1}
```

**`ralph_get_plan_draft`** — read the staged draft to learn the
current step numbers (no parameters required).

```json
{}
```

**`ralph_validate_draft`** — read-only dry-run of the cross-section
validator (no parameters required).

```json
{}
```

**`ralph_discard_plan_draft`** — delete the on-disk staged draft
so the agent can start over (no parameters required).

```json
{}
```

### Cross-section validator error to fix mapping

The 5 step-edit-relevant error strings emitted by
`ralph/mcp/artifacts/plan/_validation.py` and what to do about each:

- `plan step depends_on cycle detected at step N` — the new step
  you inserted (or a step you updated via `ralph_patch_step` /
  `ralph_replace_plan_step`) closes a cycle in `depends_on`. Edit
  one `depends_on` entry to break the loop, then re-issue the
  mutation.
- `acceptance criterion 'ID' references unknown step number N` —
  the cited step number in `design.acceptance_criteria.criteria[*]
  .satisfied_by_steps` no longer exists because the step was
  removed (orphan reference). Either add a new step that satisfies
  the dropped AC, or edit the AC to remove the broken reference.
- `satisfied_by_steps cannot reference a research or verify step;
  step N is 'TYPE' for criterion 'ID'` — the step you inserted has
  `step_type="research"` or `step_type="verify"` but is referenced
  by an AC's `satisfied_by_steps`. Only `file_change` and `action`
  steps can satisfy an AC. Change the step's `step_type` or remove
  the AC reference.
- `plan draft is missing a 'sections' object` — you called a step
  mutation without first staging the 6 required sections via
  `ralph_submit_plan_section` or `ralph_submit_plan_sections`.
  Stage `summary`, `skills_mcp`, `steps`, `critical_files`,
  `risks_mitigations`, and `verification_strategy` first.
- `plan envelope has no valid 'content' object` / `plan payload must
  decode to a JSON object` — the `step` argument to a mutation tool
  was not a JSON object (it was a string, list, or scalar). The
  handler expects `params["step"]` to be a dict shaped like
  `{"title":"Clamp the foo() index","content":"Update src/foo.py",
  "step_type":"file_change","targets":[...],"depends_on":[...]}`.

If the error you received is not in this list, read the
`## Recovery from a Bad Payload` section of the companion
`submit-plan-artifact` skill for the broader section-shape
mismatches.

## Source of Truth Reference

- `.agent/artifact-formats/plan.md` — the canonical schema for the plan
  artifact, including sections 'Tightened step contract', 'Step-mutation
  read-after-write echo', 'Cross-section invariants', and 'Plan size limits'.
  Bundled by Ralph Workflow and materialized into the
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
- Relying on a user-supplied `step.number` on `ralph_insert_plan_step`.
  The runtime assigns a synthetic `number` from
  `max(existing_numbers, default=0) + 1` and then reindexes the full list,
  so a user-supplied `number` is ignored. Supplying a `number` that you
  expect to survive reindex is wrong, whether it is unique or conflicts
  with an existing step, because the runtime rewrites every step's
  `number` after the call.
  Omit `step.number` from the payload; position is governed by `index`.
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
- Submitting `ralph_insert_plan_step` (or any of the other four
  step-mutation tools) without first calling `ralph_get_plan_draft` to
  confirm the current step numbers. The off-by-one error pattern is the
  single most common step-edit mistake: the agent submits `index=2`
  assuming step 1 is still in position 1, but a prior move or insert
  has shifted the surviving steps. Always re-read the draft first.
- Omitting the reindex echo acknowledgment after a mutation. The
  handler auto-reindexes every `depends_on` array and every
  `AC.satisfied_by_steps` reference in the same call. The echo payload
  reports the new `step.number`, the `reindex_map`, the rewritten
  `depends_on`, the rewritten AC ids, and any dropped AC ids. If the
  echo payload is suppressed or ignored, the next mutation is built on
  stale numbers and the cross-section validator will reject it at
  finalize.
- Calling `ralph_finalize_plan` immediately after a step-mutation
  without re-running `ralph_validate_draft` first. The dry-run
  validator exposes the same cross-section failures before the write
  path. A failed finalize keeps the staged draft available for repair;
  a successful finalize writes the final artifact and deletes the
  staged draft.
- Ignoring `validation_warnings` in the mutation echo. Empty warnings mean
  the edit staged cleanly. Non-empty warnings mean valid JSON was
  preserved but does not yet pass the plan schema; repair the named
  `depends_on`, `satisfied_by_steps`, or step fields and rerun
  `ralph_validate_draft`.

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
