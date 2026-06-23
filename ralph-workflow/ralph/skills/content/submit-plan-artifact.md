---
name: submit-plan-artifact
description: Use when submitting a structured execution plan via ralph_submit_plan_section, ralph_submit_plan_sections, ralph_finalize_plan, or the atomic ralph_submit_artifact path for a short plan
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
- `ralph_validate_plan_draft` for a dry-run before finalizing.
- `ralph_finalize_plan` once every required section is staged and valid.
- `ralph_discard_plan_draft` only when the staged draft is unsalvageable.
- `ralph_submit_artifact` with `artifact_type="plan"` for the atomic short-plan
  path.

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
4. Run `ralph_validate_plan_draft` for a dry-run check before finalizing.
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
`ralph_move_plan_step` / `ralph_patch_step`.

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