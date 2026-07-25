---
name: submit-plan-artifact
description: Use when authoring, validating, staging, or submitting a native-markdown plan, or recovering from plan section, stable-ID, dependency, acceptance-criteria, or step-contract diagnostics
version: 2.1.0
---

# submit-plan-artifact

## Overview

A plan is one native-markdown document. Explanations are prose; structured
values use labeled fields. Steps are `### [S-n] Title` blocks with stable IDs
that are never renumbered.

Validate with `ralph_verify_md_artifact`, submit with
`ralph_submit_md_artifact` (`artifact_type: plan`). To revise a submitted plan,
stage the complete revised document with `ralph_stage_md_artifact`
(`mode="replace_all"`), edit the staged text directly, inspect with
`ralph_get_md_draft`, then submit with `ralph_finalize_md_artifact`.

## Document shape

Universal anchors:

- Frontmatter contains `type: plan`.
- At least one complete `### [S-n] Title` step block exists unless the plan
  explicitly declares `noop: true`.
- Step IDs are unique across the whole document.
- Dependencies and acceptance-criterion references resolve to existing IDs.
- Every verification or acceptance criterion is evaluatable with a concrete
  command and expected result, or a specific file/artifact to inspect.

Everything else is a recommended authoring pattern, not required grammar.
`## Summary`, `## Scope`, `## Skills MCP`, `## Steps`, `## Critical Files`,
`## Risks`, and `## Verification` are useful conventional sections, but plans
with radically different headings, ordering, prose, or nested subplans remain
valid when the universal anchors and consumed references parse.

Use `## Work Units` for small bounded tasks or verification gates. Use
repeatable `## <Name> Subplan` or `## Subplan: <Name>` sections when each
execution subagent needs a substantial scoped mini-plan. Large plans normally
fan out four or five independent execution Subplans, then fan in through an
ordinary main-session integration and verification section that is not itself
labeled as a Subplan. Prefix/suffix recognition is case-insensitive and also
accepts `Sub-plan` plus colon or dash variants.

An exact, case-sensitive `## Work Units` or `## Parallel Plan` heading opts
into fail-closed unit parsing. Loose top-level body prose and malformed bullets
are errors. Stable-ID list items must be valid `- [unit-id] description`
markers or evaluatable `- [AC-n]` criteria; nested `### [S-n]` step blocks may
follow unit markers. The section must declare at least one real unit.
Acceptance-criterion items are criteria, never phantom work units. Lowercase
lookalikes and other arbitrary headings remain descriptive.

Keep every executable step under an exact Work Unit, including a small final
integration gate. With execution Subplans, keep cross-subplan fan-in as global
`S-n` steps in the main session so the final result proves those steps too.

Each step may use description prose and these useful fields:

- `Type: <free-form value>`; recommended built-ins are `file_change`,
  `action`, `research`, and `verify`
- `Priority: critical|high|medium|low`
- `Files:` followed by free-form actions such as `- modify path`,
  `- create path`, or `- inspect path`
- `Depends on: S-1, S-2`
- `Satisfies: AC-01`
- `Verify: pytest ...` plus `Expect: the named tests pass with exit code 0`, or
  `Location: path`
- `Rationale: ...`
- `Evidence:` followed by `- file: path`, `- test_name: node`, or
  `- command_output: command`

Project-specific `Type:` values and target actions are preserved verbatim
without coercion; use repository vocabulary that helps the executor. `Intent`,
`Coverage`, scope `Category`, step `Priority`, and risk `Severity` are also
free-form descriptive hints. The listed values are conventions, not validation
gates.

The built-in `file_change` and `verify` contracts activate their specific
requirements: `file_change` must name `Files:`.
Every `Verify:` command must have a specific `Expect:` result; a verify step
may instead name a specific
`Location:` artifact. Dependencies must name existing IDs and form a DAG. A
conventional criterion is `- [AC-01] description`, with `Satisfied by: S-1`
and either `Verify:` plus `Expect:`, or a specific `Evidence:` artifact.
Legacy plans may reuse a global Verification item when its command text
matches exactly.

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
Expect: test_clamp_out_of_range passes with exit code 0

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
3. Edit the staged draft in place with `ralph_edit_md_artifact` (`edits`: a
   list of `oldText`/`newText` pairs). Every submission stages its document
   as the draft, so this also repairs a rejected submission — and the edit
   itself submits the plan canonically as soon as the edited draft
   validates, reporting `submitted` in its response.
4. `ralph_finalize_md_artifact` submits an assembled draft that was built by
   staging alone. It is not needed after an edit that already reported
   `submitted: true`. Failed validation preserves the draft either way.
5. Use `ralph_discard_md_draft` only when intentionally starting over — never
   to recover from validation errors, and never for a revision whose content
   is substantially similar to the draft. Edit in place instead; discarding
   throws the document away and forces a full retype.

## Error recovery

- Missing or malformed universal anchor: add `type: plan`, a unique step ID, or
  the concrete evaluatable verification detail named by the diagnostic.
- `file_change` without `Files:`, a `Verify:` command without a specific
  `Expect:`, or a verify step without a specific `Location:` alternative:
  add the consumed field named by the diagnostic.
- Unknown dependency or acceptance-criterion reference: use an existing stable
  ID; IDs are never inferred or renumbered.
- Unknown descriptive label: keep it when it helps the plan; descriptive prose
  and labels that the pipeline does not consume are tolerated.
- Shell guard: verification methods must not start with `bash -c`, `sh -c`,
  or `eval`.
