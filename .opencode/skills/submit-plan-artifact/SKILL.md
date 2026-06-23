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