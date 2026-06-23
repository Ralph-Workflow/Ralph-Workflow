---
name: submit-plan-artifact
description: Use when submitting a structured execution plan via ralph_submit_plan_section, ralph_submit_plan_sections, ralph_finalize_plan, or the atomic ralph_submit_artifact path for a short plan, or when the cross-section validator rejected the staged draft and you need to recover the next retry envelope
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
`ralph_submit_plan_sections` / `ralph_finalize_plan` (or `ralph_submit_artifact`
with `artifact_type="plan"` for the atomic path).

## When to Use

Use this skill when you are about to call any of:

- `ralph_submit_plan_section` to stage a single plan section.
- `ralph_submit_plan_sections` to batch every section in one round-trip.
- `ralph_insert_plan_step` / `ralph_replace_plan_step` / `ralph_patch_step` /
  `ralph_remove_plan_step` / `ralph_move_plan_step` to edit a staged draft.
- `ralph_get_plan_draft` to recover the current staged draft (returns
  `{staged_sections, draft, source: 'draft'|'finalized_plan'}`). Use this
  to inspect or resume work after an interruption, after a step-mutation
  echo payload rewrote the step numbers, or when you need to confirm the
  surviving step numbers before issuing another mutation.
- `ralph_validate_draft` for a read-only dry-run of the cross-section
  validator before finalizing. Returns `{valid: true}` on success or
  `{valid: false, errors: [...]}` on failure with the same error shape
  the finalize path returns, so you can fix the offending sections and
  re-run the dry-run before staging again.
- `ralph_finalize_plan` once every required section is staged and valid.
- `ralph_discard_plan_draft` only when the staged draft is unsalvageable.
- `ralph_submit_artifact` with `artifact_type="plan"` for the atomic short-plan
  path.

The per-tool retry envelopes and reindex semantics for
`ralph_insert_plan_step`, `ralph_replace_plan_step`, `ralph_patch_step`,
`ralph_remove_plan_step`, and `ralph_move_plan_step` are documented in the
companion `submit-plan-step-edits` skill; consult it whenever the error came
from one of those five tools.

If you are not submitting a plan, this skill is the wrong skill — see the
companion `submit-artifact` skill for generic artifact submission.

## Core Flow (one-shot)

1. Read `.agent/artifact-formats/plan.md` once. It defines every required
   field, the per-list caps, the three string-length tiers, and the step
   contract. Treat it as a contract you must match exactly.
2. Build the six required sections (`summary`, `skills_mcp`, `steps`,
   `critical_files`, `risks_mitigations`, `verification_strategy`) and
   optionally `design`, `parallel_plan`, `work_units`, `constraints`.
3. Stage each section via `ralph_submit_plan_section(section='<name>',
   mode='replace', content=<section-payload-as-dict>)` OR batch every section
   in one call via `ralph_submit_plan_sections(entries=[...])`.
4. Run `ralph_validate_draft` for a dry-run check before finalizing. If it
   returns `valid=false`, fix the offending sections (the message names
   them) and re-run the dry-run before finalizing.
5. Call `ralph_finalize_plan` to write `.agent/artifacts/plan.json`.

**Minimal one-shot happy-path envelope** for the very first section
(`section='summary'`):

```json
{
  "section": "summary",
  "mode": "replace",
  "content": {
    "context": "What is being changed and why",
    "intent": "One-line user-facing outcome",
    "intent_verb": "improve",
    "scope_items": [
      {"text": "Concrete outcome 1"},
      {"text": "Concrete outcome 2"},
      {"text": "Concrete outcome 3"}
    ]
  }
}
```

## Recovery from a Bad Payload

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
    "context": "What is being changed and why",
    "scope_items": [
      {"text": "Concrete outcome 1"},
      {"text": "Concrete outcome 2"},
      {"text": "Concrete outcome 3"}
    ]
  }
}
```

For batch failures, `_format_plan_batch_envelope_error` references
`ralph_submit_plan_sections(entries=[{'section': '...', 'mode': '...',
'content': {...}}, ...])`. For finalize failures, `_format_plan_finalize_error`
shows the canonical shape of every required section and names
`ralph_submit_plan_section` / `ralph_submit_plan_sections` as the repair
tools. For step-edit failures, `_format_plan_step_edit_error` shows the
minimal retry envelopes for `ralph_insert_plan_step` /
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
  `ralph_finalize_plan` (and `ralph_submit_artifact` with
  `artifact_type="plan"` for atomic short plans only).
- Submitting `scope_items` with fewer than 3 items or wrapping list sections
  under a top-level key like `{"steps": [...]}`. List sections with
  `mode='replace'` MUST be a bare JSON array, not a wrapped object.
- Using `step_type: "test"`, `step_type: "check"`, or `step_type: "run"`. The
  closed set is `file_change`, `action`, `research`, `verify`. Aliases coerce
  to `verify` but the canonical value should be set explicitly.
- Forgetting to stage the `design` section when relying on
  `design.planning_profile="minimal"` — under minimal, an empty
  `skills_mcp.skills` is auto-filled, but the preset only takes effect once
  the `design` section is staged.
- Wrapping the atomic payload in `{"type": "plan", "content": ...}`. The
  `ralph_submit_artifact` envelope for plans is `{"artifact_type": "plan",
  "content": "<JSON string of the RAW plan payload>"}` with no outer wrapper.

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
| `skills_mcp.skills must contain at least one skill name unless design.planning_profile == 'minimal'` | `_validation.py` skills gate (around line 251) | Add at least one skill to `skills_mcp.skills`, or stage `design.planning_profile = "minimal"` to permit an empty list. |
| `acceptance criterion 'ID' references unknown step number N` | `_validation.py` `_check_satisfied_by_steps_links` (around line 681) | The cited step number must match an existing `step.number` in the staged `steps`. Re-read the draft with `ralph_get_plan_draft` to confirm the current step numbers after a mutation. |
| `satisfied_by_steps cannot reference a research or verify step; step N is 'TYPE' for criterion 'ID'` | `_validation.py` `_check_research_verify_step_references` (around line 732) | Only `file_change` and `action` steps can satisfy an AC. Remove the cited step from the `satisfied_by_steps` list, or change the step's `step_type` to `file_change` or `action`. |
| `plan envelope has no valid 'content' object` | `_validation.py` `_decode_plan_payload` (around line 758) | The atomic `ralph_submit_artifact` payload must be `{"artifact_type": "plan", "content": "<JSON string of the RAW plan payload>"}`. Do NOT wrap the plan in an extra `{"type": "plan", "content": ...}` envelope. |
| `plan payload must decode to a JSON object` | `_validation.py` `_decode_plan_payload` (around line 769) | The `content` argument must be a JSON string whose decoded object is the raw plan payload (an object with `summary`, `skills_mcp`, `steps`, etc.) — not a bare list, string, or scalar. |
| `plan draft is missing a 'sections' object` | `_validation.py` `finalize_plan_draft` (around line 796) | Stage every required section via `ralph_submit_plan_section` (or batch via `ralph_submit_plan_sections`) before calling `ralph_finalize_plan`. The 6 required sections are: `summary`, `skills_mcp`, `steps`, `critical_files`, `risks_mitigations`, `verification_strategy`. |

If the error message you received is not in this table, it is a
field-level Pydantic error from `PlanArtifact.model_validate`; in
that case read the `## Required fields (inside content)` section of
`.agent/artifact-formats/plan.md` and re-shape the failing field
against the schema.

## Per-section minimal payload templates

The six fenced JSON blocks below are the **minimum valid payloads**
that pass `ralph_submit_plan_section(section='<name>', mode='replace',
content=<payload>)` for each required section. Use them as the
starting point; enrich the values for your specific task. Each
block has the bare-minimum content to pass validation — no
optional fields, no extras.

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
- Does `skills_mcp.skills` contain at least one skill name, OR did you stage `design.planning_profile = "minimal"` to permit an empty list?
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