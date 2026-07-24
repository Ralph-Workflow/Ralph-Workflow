---
name: submit-plan-artifact
description: Use when authoring, validating, staging, or submitting a native-markdown plan, or recovering from plan section, stable-ID, dependency, acceptance-criteria, or step-contract diagnostics
version: 2.0.0
---

# submit-plan-artifact

## Overview

A plan is one native-markdown document. Explanations are prose; structured
values use labeled fields. Steps are `### [S-n] Title` blocks with stable IDs
that are never renumbered.

Validate with `ralph_verify_md_artifact`, submit with
`ralph_submit_md_artifact` (`artifact_type: plan`), and use
`ralph_edit_md_plan_step` for targeted edits.

## Document shape

Required:

- `## Summary`: context prose, optional `Intent:` and `Coverage:`.
- `## Scope`: at least three `- [SC-n] text` items; optional indented
  `Category:` / `Count:`.
- `## Skills MCP`: `Skills:` must name at least one skill; `MCPs:` is optional.
- `## Steps`: one or more complete `### [S-n] Title` blocks.
- `## Critical Files`: `- [CF-n] path` items with `Action:` or `Purpose:`.
- `## Risks`: `- [R-n] risk` items with `Mitigation:`.
- `## Verification`: `- [V-n] command` items with `Expect:`.

Optional: `## Constraints`, `## Design`, `## Acceptance Criteria`,
`## Parallel Plan`, or `## Work Units`. Parallel Plan and Work Units are
mutually exclusive.

Each step needs description prose. Useful fields:

- `Type: file_change|action|research|verify`
- `Priority: critical|high|medium|low`
- `Files:` followed by `- modify path`, `- create path`, and similar bullets
- `Depends on: S-1, S-2`
- `Satisfies: AC-01`
- `Verify: pytest ...` or `Location: path`
- `Rationale: ...`
- `Evidence:` followed by `- file: path`, `- test_name: node`, or
  `- command_output: command`

`file_change` requires `Files:`. `verify` requires `Verify:` or `Location:`.
Dependencies must name existing IDs and form a DAG. A criterion is
`- [AC-01] description`, with `Satisfied by: S-1` and optional `Verify:` /
`Evidence:` fields.

## Core flow

1. Write the full document.
2. Call `ralph_verify_md_artifact` with `artifact_type: plan`; repair every
   error at its reported line and section.
3. Call `ralph_submit_md_artifact` with the same artifact type and content.

Worked example:

```markdown
---
type: plan
---
## Summary
foo() crashes on out-of-range indexes.

Intent: Clamp indexes and prove the behavior.
Coverage: bugfix, test

## Scope
- [SC-1] Add a failing regression test
  Category: test
- [SC-2] Clamp indexes in src/foo.py
  Category: bugfix
- [SC-3] Run focused verification
  Category: test

## Skills MCP
Skills: test-driven-development

## Steps

### [S-1] Add the regression test
Add test_clamp_out_of_range before changing production code.

Type: file_change
Files:
- modify tests/test_foo.py

### [S-2] Clamp the index
Clamp negative and oversized indexes without changing foo()'s signature.

Type: file_change
Files:
- modify src/foo.py
Depends on: S-1

### [S-3] Run the focused suite
Prove the regression is fixed.

Type: verify
Depends on: S-2
Verify: pytest tests/test_foo.py -q

## Critical Files
- [CF-1] src/foo.py
  Action: modify
- [CF-2] tests/test_foo.py
  Action: modify

## Risks
- [R-1] Clamping could mask a caller bug
  Mitigation: Assert the exact boundary result.

## Verification
- [V-1] pytest tests/test_foo.py -q
  Expect: test_clamp_out_of_range passes
  Timeout: 60
```

## Staged authoring

For a long plan:

1. Append chunks with `ralph_stage_md_artifact` (`mode: append`), or replace
   the draft with `mode: replace_all`.
2. Inspect the full draft with `ralph_get_md_draft`.
3. Edit a step by stable ID with `ralph_edit_md_plan_step`; the tool saves the
   updated draft.
4. Submit the assembled draft with `ralph_finalize_md_artifact`. Failed
   validation preserves the draft.
5. Use `ralph_discard_md_draft` only when intentionally starting over.

## Error recovery

- Missing `Scope` or `Risks`: use the exact required section names.
- `file_change` without `Files:` or `verify` without `Verify:` / `Location:`:
  complete the step contract.
- Unknown dependency or acceptance-criterion reference: use an existing stable
  ID; IDs are never inferred or renumbered.
- Unknown labeled field: use only fields documented above and in
  `.agent/artifact-formats/plan.md`.
- Shell guard: verification methods must not start with `bash -c`, `sh -c`,
  or `eval`.
