---
name: submit-plan-artifact
description: Use when submitting a detailed structured execution plan via ralph_submit_plan_section, ralph_submit_plan_sections, ralph_finalize_plan, or when a staged plan needs to satisfy planning-analysis and software-engineering quality criteria
---

# submit-plan-artifact

## Overview

This is an **OPTIONAL** skill that lives alongside the canonical plan format
doc at `.agent/artifact-formats/plan.md`. Use it as a quick lookup before
submitting a plan, not as a substitute for the format doc. The format doc is
the source of truth for every field, section shape, and size cap.

**Skill name vs MCP tool name.** This skill is named `submit-plan-artifact`.
It is a separate name from the MCP tool `ralph_submit_artifact`, which is
the generic artifact submission entry point. Do not conflate the two: the
MCP tool for plan submission is `ralph_submit_plan_section` /
`ralph_submit_plan_sections` / `ralph_finalize_plan`.

## When to Use

Use this skill when you are about to call any of:

- `ralph_submit_plan_section` to stage a single plan section.
- `ralph_submit_plan_sections` to stage multiple complete sections together
  after each entry is analysis-ready.
- `ralph_insert_plan_step` / `ralph_replace_plan_step` / `ralph_patch_step` /
  `ralph_remove_plan_step` / `ralph_move_plan_step` to edit a staged draft.
- `ralph_get_plan_draft` to recover the current staged draft (returns
  `{"staged_sections":[...],"draft":{...},"source":"draft"|"finalized_plan"}`). Use this
  to inspect or resume work after an interruption, after a step-mutation
  echo payload rewrote the step numbers, or when you need to confirm the
  surviving step numbers before issuing another mutation.
- `ralph_validate_draft` for a read-only dry-run of the cross-section
  validator before finalizing. Returns `{"valid":true}` on success or
  `{"valid":false,"errors":[...]}` on failure with the same error shape
  the finalize path returns, so you can fix the offending sections and
  re-run the dry-run before staging again.
- `ralph_finalize_plan` once every required section is staged and valid.
- `ralph_discard_plan_draft` only when the staged draft is unsalvageable.

The per-tool retry envelopes and reindex semantics for
`ralph_insert_plan_step`, `ralph_replace_plan_step`, `ralph_patch_step`,
`ralph_remove_plan_step`, and `ralph_move_plan_step` are documented in the
companion `submit-plan-step-edits` skill; consult it whenever the error came
from one of those five tools.

If you are not submitting a plan, this skill is the wrong skill — see the
companion `submit-artifact` skill for generic artifact submission.

## Core Flow

1. Read `.agent/artifact-formats/plan.md` once. It defines every required
   field, the per-list caps, the three string-length tiers, and the step
   contract. Treat it as a contract you must match exactly.
2. Build an analysis-ready plan with the six required sections (`summary`, `skills_mcp`, `steps`,
   `critical_files`, `risks_mitigations`, `verification_strategy`) and
   optionally `design`, `parallel_plan`, `work_units`, `constraints`.
3. Stage each section via `ralph_submit_plan_section(section='<name>',
   mode='replace', content=<section-payload-as-dict>)` OR stage multiple
   complete sections via `ralph_submit_plan_sections(entries=[...])`.
   Inspect the returned `validation_warnings`; valid JSON that is not yet
   schema-valid is staged, not abandoned.
4. Run `ralph_validate_draft` for a dry-run check before finalizing. If it
   returns `valid=false` or any staging call returned non-empty
   `validation_warnings`, fix the offending sections (the message names
   them) and re-run the dry-run before finalizing.
5. Call `ralph_finalize_plan` to write `.agent/artifacts/plan.json`.

**Canonical envelope** for the first section
(`section='summary'`):

```json
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
}
```

## Planning Quality Criteria

Before submitting, make the plan executor-ready and planning-analysis-ready:

- Map every explicit and implied user requirement to `summary.scope_items`, concrete implementation steps, and verification entries.
- Use at least one task-relevant skill in `skills_mcp.skills`; include specialized skills for TDD, debugging, security, accessibility, frontend work, or documentation when those domains are in scope.
- Give every `file_change` step concrete `targets`, every `verify` step a concrete `verify_command` or `location`, and every dependency in `depends_on` as an integer step number.
- Populate `design.acceptance_criteria.criteria` for non-trivial work, and link each criterion to `file_change` or `action` steps with `satisfied_by_steps`.
- Include `expected_evidence` on implementation steps when it helps the executor prove completion: files, command outputs, or test names.
- Use exact verification commands and expected outcomes. Do not write vague instructions such as "run tests".
- Name real risks and mitigations, not generic placeholders.

## Correcting a Rejected Payload

When `ralph_submit_plan_section` returns an error, the helper
`_format_plan_section_submission_error` produces a structured message that
names the failing section, the mode you passed, the format-doc reference, and
a canonical retry envelope. Read it carefully, then:

1. Confirm the failing section name (e.g. `summary`, `skills_mcp`, `steps`,
   `critical_files`, `risks_mitigations`, `verification_strategy`,
   `design`).
2. Confirm the mode you used (`replace` for full replacement, `append` for
   list extension). `mode='replace'` requires the section payload as a JSON
   array for list sections, not a wrapped object.
3. Re-read the relevant section of `.agent/artifact-formats/plan.md`.
4. Re-issue the same call: `ralph_submit_plan_section(section='<section>',
   mode='<mode>', content=<corrected-payload-as-dict>)`.

**Worked retry envelope** for a `_format_plan_section_submission_error` style
failure on the `summary` section with `mode='replace'`:

```json
{
  "section": "summary",
  "mode": "replace",
  "content": {
    "context": "Fix the foo() off-by-one regression and prove it with a focused unit test.",
    "scope_items": [
      {"text": "Add a regression test for the out-of-range foo() index", "category": "test"},
      {"text": "Modify src/foo.py to clamp the index before lookup", "category": "file_change"},
      {"text": "Run pytest tests/test_foo.py -q to prove the regression is fixed", "category": "test"}
    ]
  }
}
```

For batch failures, `_format_plan_batch_envelope_error` references
`ralph_submit_plan_sections` with `{"entries":[{"section":"summary","mode":"replace",
"content":{...}}, ...]}`. For finalize failures, `_format_plan_finalize_error`
shows the canonical shape of every required section and names
`ralph_submit_plan_section` / `ralph_submit_plan_sections` as the tools to
update the draft. For step-edit failures, `_format_plan_step_edit_error`
shows the canonical envelopes for `ralph_insert_plan_step` /
`ralph_replace_plan_step` / `ralph_remove_plan_step` /
`ralph_move_plan_step` / `ralph_patch_step`. After every successful
step-mutation call, the canonical way to recover the new step numbers is
`ralph_get_plan_draft` (returns `{staged_sections, draft, source: 'draft'
| 'finalized_plan'}`); do not guess the new numbers — the
`reindex_map` field in the echo payload is the only authoritative source.

## Source of Truth Reference

- `.agent/artifact-formats/plan.md` — the canonical schema for the plan
  artifact. Bundled by Ralph Workflow and materialized into the workspace on
  demand. Every field, every per-list cap, every string-length tier, and the
  full step contract are defined here.
- `.agent/artifact-formats/artifact_formats_index.md` — the index that lists
  every supported `artifact_type` (including `plan`) and points to each
  format doc.

If this skill and the format doc ever disagree, the format doc wins.

## Common Mistakes

- Treating this skill as authoritative. The format doc at
  `.agent/artifact-formats/plan.md` is the source of truth; this skill is a
  quick pointer, not a substitute.
- Conflating `submit-plan-artifact` (this skill) with the MCP tool
  `ralph_submit_artifact`. The MCP tool for plans is
  `ralph_submit_plan_section` / `ralph_submit_plan_sections` /
  `ralph_finalize_plan`.
- Submitting `scope_items` with fewer than 3 items or wrapping list sections
  under a top-level key like `{"steps": [...]}`. List sections with
  `mode='replace'` MUST be a bare JSON array, not a wrapped object.
- Using `step_type: "test"`, `step_type: "check"`, or `step_type: "run"`. The
  closed set is `file_change`, `action`, `research`, `verify`. Use the
  canonical value explicitly.
- Submitting an empty `skills_mcp.skills` array. A plan must list at least
  one task-relevant skill.
- Falling back to `ralph_submit_artifact` for plans. Planning must go through
  `ralph_submit_plan_section` / `ralph_submit_plan_sections` /
  `ralph_finalize_plan`.

## Canonical validator errors to fix

When `ralph_submit_plan_section`, `ralph_submit_plan_sections`,
`ralph_validate_draft`, or `ralph_finalize_plan` returns an error, the
message comes from one of the cross-section validators in
`ralph/mcp/artifacts/plan/_validation.py` or from the payload decoders
in the same module. The table below enumerates every literal error
string the agent will see and the canonical fix. The error strings
are copied verbatim from the f-strings that raise
`PlanArtifactValidationError`; do NOT paraphrase them when retrying.

Staging tools can also return `is_error=false` with non-empty
`validation_warnings`. Treat those warnings as repair work: the JSON was
preserved in the draft, but `ralph_validate_draft` / `ralph_finalize_plan`
will reject it until the named fields are fixed.

| Error string (verbatim from the validator) | Source location | Fix |
| --- | --- | --- |
| `plan step depends_on cycle detected at step N` | `_validation.py` cycle guard (around line 173) | Remove the cycle: edit one `depends_on` entry on the cited step so the graph becomes a DAG. |
| `plan cannot declare both parallel_plan and work_units; pick one` | `_validation.py` `_validate_step_ac_cross_references` (around line 229) | Pick exactly one parallelization mode. Delete the `work_units` field if you want `parallel_plan`, or vice versa. |
| `verification method must not invoke a shell interpreter directly; use the executable path` | `_validation.py` shell-invocation guard (around line 239) | Replace `bash -c "..."` / `sh -c "..."` / `eval "..."` with the executable path and pass args as a list. |
| `skills_mcp.skills must contain at least one skill name` | `_validation.py` skills gate | Add at least one task-relevant skill to `skills_mcp.skills`. |
| `acceptance criterion 'ID' references unknown step number N` | `_validation.py` `_check_satisfied_by_steps_links` (around line 681) | The cited step number must match an existing `step.number` in the staged `steps`. Re-read the draft with `ralph_get_plan_draft` to confirm the current step numbers after a mutation. |
| `satisfied_by_steps cannot reference a research or verify step; step N is 'TYPE' for criterion 'ID'` | `_validation.py` `_check_research_verify_step_references` (around line 732) | Only `file_change` and `action` steps can satisfy an AC. Remove the cited step from the `satisfied_by_steps` list, or change the step's `step_type` to `file_change` or `action`. |
| `plan envelope has no valid 'content' object` | `_validation.py` `_decode_plan_payload` | Submit the plan through `ralph_submit_plan_section` / `ralph_submit_plan_sections` and finalize the staged draft. |
| `plan payload must decode to a JSON object` | `_validation.py` `_decode_plan_payload` | Submit each section with the documented native object or array shape for that section. |
| `plan draft is missing a 'sections' object` | `_validation.py` `finalize_plan_draft` (around line 796) | Stage every required section via `ralph_submit_plan_section` (or batch via `ralph_submit_plan_sections`) before calling `ralph_finalize_plan`. The 6 required sections are: `summary`, `skills_mcp`, `steps`, `critical_files`, `risks_mitigations`, `verification_strategy`. |

If the error message you received is not in this table, it is a
field-level Pydantic error from `PlanArtifact.model_validate`; in
that case read the `## Required fields (inside content)` section of
`.agent/artifact-formats/plan.md` and re-shape the failing field
against the schema.

## Per-section canonical payload templates

The six fenced JSON blocks below are the **canonical starting payloads**
that pass `ralph_submit_plan_section(section='<name>', mode='replace',
content=<payload>)` for each required section. Use them as the
starting point, then enrich the values until the plan is executor-ready.

### summary

```json
{
  "context": "Fix the foo() off-by-one regression and prove it with a focused unit test.",
  "scope_items": [
    {"text": "Add a regression test for the out-of-range foo() index", "category": "test"},
    {"text": "Modify src/foo.py to clamp the index before lookup", "category": "file_change"},
    {"text": "Run pytest tests/test_foo.py -q to prove the regression is fixed", "category": "test"}
  ]
}
```

### skills_mcp

```json
{
  "skills": ["writing-plans"],
  "mcps": []
}
```

### steps

```json
[
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
```

### critical_files

```json
{
  "primary_files": [
    {"path": "src/foo.py", "action": "modify"},
    {"path": "tests/test_foo.py", "action": "modify"}
  ],
  "reference_files": []
}
```

### risks_mitigations

```json
[
  {
    "risk": "Clamping could hide a caller bug that should remain visible in behavior expectations.",
    "mitigation": "Preserve the public foo() signature and add a focused regression test documenting the intended clamping behavior.",
    "severity": "medium"
  }
]
```

### verification_strategy

```json
[
  {
    "method": "pytest tests/test_foo.py -q",
    "expected_outcome": "The focused foo() regression test passes.",
    "timeout_seconds": 60,
    "cwd": "."
  }
]
```

### design

Use this optional section whenever planning analysis expects explicit
acceptance criteria or when any step includes `satisfies`.

```json
{
  "planning_profile": "strict",
  "outcome": "foo() handles out-of-range indexes without crashing and the focused regression test passes.",
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
```

These are the same shapes the no-skill error helper
`_format_plan_finalize_error` inlines in its canonical guidance. After
fixing the payload, re-run `ralph_validate_draft` (read-only dry-run)
before calling `ralph_finalize_plan`.

## Dumb-proof checklist (plan-artifact)

Before calling `ralph_finalize_plan`, walk this list. Every bullet
maps to one cross-section validator rule; missing one bullet is the
single most common cause of a finalize failure.

- Did you stage all 6 required sections (`summary`, `skills_mcp`, `steps`, `critical_files`, `risks_mitigations`, `verification_strategy`) via `ralph_submit_plan_section` or batch them in `ralph_submit_plan_sections`?
- Does `summary.scope_items` contain at least 3 entries (the validator enforces `min_length=3`)?
- Does `skills_mcp.skills` contain at least one task-relevant skill name?
- Is every step's `step_type` one of the closed set `file_change`, `action`, `research`, `verify` (NOT `test`, `check`, `run`, or any ad-hoc label)?
- Does every `file_change` step declare at least one `targets` entry, and does every `targets[*].action` use one of `create`, `modify`, `delete`, `read`, `reference`?
- Does every `verify` step set `verify_command` (or `location` for a test file path)?
- Does `risks_mitigations` contain at least 1 entry, and does `verification_strategy` contain at least 1 entry with a non-empty `method` and `expected_outcome`?
- Does `critical_files.primary_files` contain at least 1 entry with a valid `path` and `action`?
- Does your `verification_strategy[*].method` NOT start with `bash -c `, `sh -c `, or `eval ` (the shell-invocation guard rejects those prefixes)?
- Does your `steps[*].depends_on` graph form a DAG (no cycles)? The cycle guard raises `plan step depends_on cycle detected at step N` on the first cycle it finds.
- Does your plan declare AT MOST one of `parallel_plan` or `work_units` (the cross-section validator rejects both)?
- If you included `design.acceptance_criteria.criteria[*].satisfied_by_steps`, does every entry reference an existing step number, and is that step's `step_type` NOT `research` or `verify` (only `file_change` and `action` can satisfy an AC)?

## Canonical validator errors to fix

When `ralph_submit_plan_section`, `ralph_submit_plan_sections`,
`ralph_validate_draft`, or `ralph_finalize_plan` returns an error, the
message comes from one of the cross-section validators in
`ralph/mcp/artifacts/plan/_validation.py` or from the payload decoders
in the same module. The table below enumerates every literal error
string the agent will see and the canonical fix. The error strings
are copied verbatim from the f-strings that raise
`PlanArtifactValidationError`; do NOT paraphrase them when retrying.

| Error string (verbatim from the validator) | Source location | Fix |
| --- | --- | --- |
| `plan step depends_on cycle detected at step N` | `_validation.py` cycle guard (around line 173) | Remove the cycle: edit one `depends_on` entry on the cited step so the graph becomes a DAG. |
| `plan cannot declare both parallel_plan and work_units; pick one` | `_validation.py` `_validate_step_ac_cross_references` (around line 229) | Pick exactly one parallelization mode. Delete the `work_units` field if you want `parallel_plan`, or vice versa. |
| `verification method must not invoke a shell interpreter directly; use the executable path` | `_validation.py` shell-invocation guard (around line 239) | Replace `bash -c "..."` / `sh -c "..."` / `eval "..."` with the executable path and pass args as a list. |
| `skills_mcp.skills must contain at least one skill name` | `_skills_mcp.py` / `_validation.py` skills gate | Add at least one task-relevant skill to `skills_mcp.skills`; empty skill lists are invalid. |
| `acceptance criterion 'ID' references unknown step number N` | `_validation.py` `_check_satisfied_by_steps_links` (around line 681) | The cited step number must match an existing `step.number` in the staged `steps`. Re-read the draft with `ralph_get_plan_draft` to confirm the current step numbers after a mutation. |
| `satisfied_by_steps cannot reference a research or verify step; step N is 'TYPE' for criterion 'ID'` | `_validation.py` `_check_research_verify_step_references` (around line 732) | Only `file_change` and `action` steps can satisfy an AC. Remove the cited step from the `satisfied_by_steps` list, or change the step's `step_type` to `file_change` or `action`. |
| `plan envelope has no valid 'content' object` | Legacy atomic-plan decoder | Do not retry plan submission through generic `ralph_submit_artifact`. Use staged planning tools instead: submit or repair the relevant section with `ralph_submit_plan_section` / `ralph_submit_plan_sections`, then run `ralph_validate_draft` before `ralph_finalize_plan`. |
| `plan payload must decode to a JSON object` | Legacy atomic-plan decoder | Do not wrap the entire plan as a generic artifact payload. Stage each raw section through the planning tools using the documented section content shapes. |
| `plan draft is missing a 'sections' object` | `_validation.py` `finalize_plan_draft` (around line 796) | Stage every required section via `ralph_submit_plan_section` (or batch via `ralph_submit_plan_sections`) before calling `ralph_finalize_plan`. The 6 required sections are: `summary`, `skills_mcp`, `steps`, `critical_files`, `risks_mitigations`, `verification_strategy`. |

If the error message you received is not in this table, it is a
field-level Pydantic error from `PlanArtifact.model_validate`; in
that case read the `## Required fields (inside content)` section of
`.agent/artifact-formats/plan.md` and re-shape the failing field
against the schema.

## Per-section compact payload templates

The six fenced JSON blocks below are **compact starting payloads**
that pass `ralph_submit_plan_section(section='<name>', mode='replace',
content=<payload>)` for each required section. Use them as the
starting point; enrich the values for your specific task. Each
block has deliberately small content to show the shape. Do not stop
there for a real task; add task-specific detail, evidence, and
software-engineering rationale.

### summary

```json
{
  "context": "What is being changed and why",
  "scope_items": [
    {"text": "Concrete outcome 1"},
    {"text": "Concrete outcome 2"},
    {"text": "Concrete outcome 3"}
  ]
}
```

### skills_mcp

```json
{
  "skills": ["writing-plans"],
  "mcps": []
}
```

### steps

```json
[
  {
    "number": 1,
    "title": "Concrete step title",
    "content": "Detailed executor instructions",
    "step_type": "file_change",
    "targets": [{"path": "path/to/file.py", "action": "modify"}],
    "depends_on": []
  }
]
```

### critical_files

```json
{
  "primary_files": [{"path": "path/to/file.py", "action": "modify"}],
  "reference_files": []
}
```

### risks_mitigations

```json
[
  {
    "risk": "Specific failure mode",
    "mitigation": "How to avoid it",
    "severity": "medium"
  }
]
```

### verification_strategy

```json
[
  {
    "method": "pytest tests/test_x.py -q",
    "expected_outcome": "All tests pass"
  }
]
```

These are the same shapes the no-skill error helper
`_format_plan_finalize_error` inlines in its repair guidance. After
fixing the payload, re-run `ralph_validate_draft` (read-only dry-run)
before calling `ralph_finalize_plan`.

## Dumb-proof checklist (plan-artifact)

Before calling `ralph_finalize_plan`, walk this list. Every bullet
maps to one cross-section validator rule; missing one bullet is the
single most common cause of a finalize failure.

- Did you stage all 6 required sections (`summary`, `skills_mcp`, `steps`, `critical_files`, `risks_mitigations`, `verification_strategy`) via `ralph_submit_plan_section` or batch them in `ralph_submit_plan_sections`?
- Does `summary.scope_items` contain at least 3 entries (the validator enforces `min_length=3`)?
- Does `skills_mcp.skills` contain at least one task-relevant skill name? Empty skill lists are invalid for every planning profile.
- Is every step's `step_type` one of the closed set `file_change`, `action`, `research`, `verify` (NOT `test`, `check`, `run`, or any ad-hoc label)?
- Does every `file_change` step declare at least one `targets` entry, and does every `targets[*].action` use one of `create`, `modify`, `delete`, `read`, `reference`?
- Does every `verify` step set `verify_command` (or `location` for a test file path)?
- Does `risks_mitigations` contain at least 1 entry, and does `verification_strategy` contain at least 1 entry with a non-empty `method` and `expected_outcome`?
- Does `critical_files.primary_files` contain at least 1 entry with a valid `path` and `action`?
- Does your `verification_strategy[*].method` NOT start with `bash -c `, `sh -c `, or `eval ` (the shell-invocation guard rejects those prefixes)?
- Does your `steps[*].depends_on` graph form a DAG (no cycles)? The cycle guard raises `plan step depends_on cycle detected at step N` on the first cycle it finds.
- Does your plan declare AT MOST one of `parallel_plan` or `work_units` (the cross-section validator rejects both)?
- If you included `design.acceptance_criteria.criteria[*].satisfied_by_steps`, does every entry reference an existing step number, and is that step's `step_type` NOT `research` or `verify` (only `file_change` and `action` can satisfy an AC)?

## Red Flags - STOP and Start Over

- "I have read `plan.md` so I do not need the skill." STOP. The skill is
  a per-tool retry envelope; `plan.md` is the schema. They cover
  different failure modes.
- "The skill is OPTIONAL therefore ignorable." STOP. The OPTIONAL marker
  means the agent may consult the skill, not that the agent may skip the
  source-of-truth format doc. The skill names the format doc explicitly.
- "I will copy a previous payload without checking schema." STOP. The
  cross-section validator and the per-tool envelopes both evolve; copying
  a payload from a prior plan re-runs every old failure mode. Read the
  format doc first.
- "I will guess the new step number after an insert/remove/move." STOP.
  The reindex map in the echo payload is the only authoritative source of
  new numbers; guessing produces an off-by-one draft that the
  cross-section validator rejects. Re-read the draft with
  `ralph_get_plan_draft`.
- "I will skip `ralph_validate_draft` because finalize will validate
  anyway." STOP. The dry-run validator is the only signal you get before
  the staged draft is deleted on a successful `ralph_finalize_plan`. If
  the dry-run fails, you can fix it cheaply; if finalize fails, you have
  lost the staged draft.
