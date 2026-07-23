---
name: submit-plan-artifact
description: Use when authoring or submitting a plan artifact as markdown via ralph_submit_md_artifact, staging a large plan with ralph_stage_md_artifact / ralph_get_md_draft / ralph_finalize_md_artifact, or when a plan was rejected over step IDs, depends_on, acceptance-criteria links, or a missing required section
version: 2.0.0
---

# submit-plan-artifact

## Overview

A plan is one markdown document (`artifact_type: "plan"`). Sections hold
stable-ID list items; each item's text is a compact JSON object carrying
that entry's fields. Steps get stable IDs `S-1`, `S-2`, … that the rest of
the pipeline (edits, proofs) refers back to.

Submit with `ralph_submit_md_artifact`, pre-check with
`ralph_verify_md_artifact`, edit one step by ID with
`ralph_edit_md_plan_step` (see the `submit-plan-step-edits` skill).

## Document Shape

Frontmatter: `type: plan` (required); `schema_version: <int>` and
`intent_verb: <verb>` optional (an unknown `intent_verb` is coerced to
`add` with a warning).

| Section | Required | Items |
|---|---|---|
| `## Summary` | yes | exactly 1 |
| `## Skills MCP` | yes | exactly 1 |
| `## Steps` | yes | 1+ with `S-<n>` IDs |
| `## Critical Files` | yes | exactly 1 |
| `## Risks Mitigations` | yes | 1+ |
| `## Verification` | yes | 1+ |
| `## Constraints`, `## Design` | no | exactly 1 each |
| `## Parallel Plan`, `## Work Units` | no | 1+ each; mutually exclusive |

Every item is `- [ID] {json}` on one line. Key field rules (enforced by
the canonical plan validator):

- Summary: `{"context", "intent", "intent_verb", "scope_items": [{"text",
  "category"}, ...]}` — at least 3 scope items. The `intent_verb` must be
  compatible with each scope item's `category` (e.g. `fix` allows only
  `bugfix`, `file_change`, `other`, `unknown`).
- Skills MCP: `{"skills": [...]}` — at least one skill name.
- Steps: ID must be `S-<positive number>`; JSON needs `"title"` and
  `"content"`. `"step_type"` is one of `file_change` (requires
  `"targets": [{"path", "action"}, ...]`), `action`, `research`, `verify`
  (requires `"verify_command"` or `"location"`). `"depends_on"` is a list
  of other steps' `S-<n>` IDs — unknown IDs and cycles are rejected.
- Critical Files: `{"primary_files": [{"path", "action"}, ...]}` — at
  least one entry; `action` is `create`, `modify`, or `delete`.
- Risks Mitigations: `{"risk", "mitigation"}` per item.
- Verification: `{"method", "expected_outcome"}` per item; `method` must
  not start with `bash -c `, `sh -c `, or `eval `.

Unknown closed-vocabulary values (`step_type`, `priority`, scope
`category`, target `action`, evidence `kind`, risk `severity`) are coerced
to a documented default with a warning, not rejected.

## Acceptance-Criteria ↔ Step Links

If `## Design` declares `acceptance_criteria`, the link is two-way and
checked in both directions:

- A step's `"satisfies": ["AC-01"]` must name an existing criterion ID
  (pattern `^[A-Z]+-\d{2,}$`).
- A criterion's `"satisfied_by_steps": [1]` uses step **numbers** — the
  numeric part of the `S-<n>` step IDs — and must name existing steps.
- Only `file_change` and `action` steps may satisfy a criterion.

## Core Flow (one-shot)

1. Write the full plan document.
2. `ralph_verify_md_artifact({"artifact_type": "plan", "content": ...})`
   and fix every error diagnostic at its reported line.
3. `ralph_submit_md_artifact({"artifact_type": "plan", "content": ...})`.

Worked example:

```markdown
---
type: plan
---

## Summary

- [SUM-1] {"context":"foo() crashes on out-of-range indexes","intent":"Clamp foo() indexes and prove the fix with a regression test","intent_verb":"fix","scope_items":[{"text":"Add a regression test for out-of-range indexes","category":"bugfix"},{"text":"Clamp the index in src/foo.py","category":"bugfix"},{"text":"Keep the public foo() signature unchanged","category":"other"}]}

## Skills MCP

- [SKL-1] {"skills":["test-driven-development"]}

## Steps

- [S-1] {"title":"Add regression test","content":"Add tests/test_foo.py::test_clamp_handles_out_of_range_index reproducing the crash.","step_type":"file_change","targets":[{"path":"tests/test_foo.py","action":"modify"}]}
- [S-2] {"title":"Clamp the index","content":"Clamp negative and oversized indexes in src/foo.py before lookup.","step_type":"file_change","targets":[{"path":"src/foo.py","action":"modify"}],"depends_on":["S-1"]}
- [S-3] {"title":"Run the focused suite","content":"Prove the regression is fixed.","step_type":"verify","verify_command":"pytest tests/test_foo.py -q","depends_on":["S-2"]}

## Critical Files

- [CF-1] {"primary_files":[{"path":"src/foo.py","action":"modify"}]}

## Risks Mitigations

- [R-1] {"risk":"Clamping could mask a caller bug","mitigation":"The regression test asserts the exact clamped result"}

## Verification

- [V-1] {"method":"pytest tests/test_foo.py -q","expected_outcome":"all tests pass"}
```

## Staged Authoring (large plans)

For a plan too large to author in one call, build a persisted draft
chunk-by-chunk instead of one-shot submission:

1. `ralph_stage_md_artifact({"artifact_type": "plan", "content": "<chunk>"})`
   — appends the chunk to the persisted draft (`"mode": "append"`, the
   default; pass `"mode": "replace_all"` to overwrite). Staging never
   gates on validity: each call reports the draft's length, section
   outline, and check-only diagnostics — a partial draft is expected to
   report missing sections.
2. `ralph_get_md_draft({"artifact_type": "plan"})` — returns the full
   draft (`content`, `exists`) plus the same diagnostics, for resuming or
   inspecting before repair.
3. Repair a specific step with `ralph_edit_md_plan_step` (see
   `submit-plan-step-edits`), then re-stage the edited document with
   `"mode": "replace_all"`.
4. `ralph_finalize_md_artifact({"artifact_type": "plan"})` — runs the
   full submission gate on the assembled draft. On success it persists
   the canonical plan and deletes the draft; on validation failure the
   draft is kept intact for repair and the diagnostics are returned.
5. `ralph_discard_md_draft({"artifact_type": "plan"})` — deletes the
   draft. Use only when truly starting over; never after a failed
   finalize, which already preserved the draft for repair.

## Error Recovery

Diagnostics name the `line`, `section`, and `code`. Fix errors in place
and re-verify; warnings (coerced vocabulary values) never block. Common
plan rejections:

- `step ID 'X' must use the S-<positive-number> form` — rename the item ID.
- `Steps references unknown step ID` — a `depends_on` entry names a step
  that does not exist; use the exact `S-<n>` IDs from `## Steps`.
- `<Section> must contain exactly one item` — Summary, Skills MCP,
  Critical Files, Constraints, and Design are single-item sections.
- `... must contain a JSON object` — the item text after `[ID]` failed to
  parse as JSON; keep it a one-line JSON object.
- Cross-link failures name the offending step number or criterion ID —
  repair the `satisfies` / `satisfied_by_steps` pair on both sides.
