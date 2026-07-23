# plan artifact format

Write one executor-ready markdown plan. Use prose for explanations, labeled
fields for structured values, and stable IDs for references. Validate with
`ralph_verify_md_artifact`, then submit with `ralph_submit_md_artifact`
(`artifact_type: plan`). Large plans may use the stage/get/finalize tools.

To edit one step, call `ralph_edit_md_plan_step` with its stable `S-n` ID.
Insert, move, replace, and remove never renumber IDs. A replacement is one
complete `### [S-n] Title` block whose ID matches `step_id`.

See the complete opinionated sample:
`.agent/artifact-formats/examples/plan.md`.

## Complete minimal example

```markdown
---
type: plan
---
## Summary
foo() crashes on out-of-range indexes.

Intent: Clamp indexes without changing the public signature.
Coverage: bugfix, test

## Scope
- [SC-1] Add a regression test for invalid indexes
  Category: test
- [SC-2] Clamp indexes in src/foo.py
  Category: bugfix
- [SC-3] Run focused verification
  Category: test

## Skills MCP
Skills: test-driven-development

## Steps

### [S-1] Add the regression test
Add tests/test_foo.py::test_clamp_out_of_range before production changes.

Type: file_change
Files:
- modify tests/test_foo.py
Satisfies: AC-01

### [S-2] Clamp indexes in foo()
Clamp negative and oversized indexes while preserving foo()'s signature.

Type: file_change
Files:
- modify src/foo.py
Depends on: S-1
Satisfies: AC-01

### [S-3] Run the focused test
Prove the regression is fixed.

Type: verify
Depends on: S-2
Verify: pytest tests/test_foo.py -q

## Critical Files
- [CF-1] src/foo.py
  Action: modify
  Changes: clamp the lookup index
- [CF-2] tests/test_foo.py
  Action: modify
  Changes: add one regression test

## Acceptance Criteria
- [AC-01] Invalid indexes no longer crash foo()
  Satisfied by: S-1, S-2
  Verify: pytest tests/test_foo.py -q

## Risks
- [R-1] Clamping could mask a caller bug
  Severity: medium
  Mitigation: Assert the exact boundary behavior in the regression test.

## Verification
- [V-1] pytest tests/test_foo.py -q
  Expect: test_clamp_out_of_range passes with exit code 0
  Timeout: 60
```

## Grammar

Frontmatter requires `type: plan`. Optional fields are `schema_version` and
`intent_verb` (add, fix, refactor, migrate, document, investigate, improve,
configure, or remove).

Required sections:

- `## Summary`: prose plus optional `Intent:` and comma-separated `Coverage:`.
- `## Scope`: at least three `- [SC-n] text` items; indented `Category:` and
  `Count:` fields are optional.
- `## Skills MCP`: `Skills:` must name at least one skill; `MCPs:` is optional.
- `## Steps`: one or more `### [S-n] Title` blocks. Each block needs
  description prose. Fields may include `Type:`, `Priority:`, `Files:` bullets,
  `Depends on:`, `Satisfies:`, `Verify:`, `Location:`, `Rationale:`, and
  `Evidence:` bullets.
- `## Critical Files`: `- [CF-n] path` items. Primary files use `Action:` and
  optional `Changes:`; reference files use `Purpose:` instead.
- `## Risks`: `- [R-n] risk` items with `Mitigation:` and optional `Severity:`.
- `## Verification`: `- [V-n] command` items with `Expect:` and optional
  `Timeout:` / `Cwd:`.

Optional sections use these closed shapes:

- `## Constraints` contains only `Must not break:` and `Must keep working:`
  bullet lists, plus scalar `Performance budget:` and `Security posture:`.
- `## Design` allows prose and these labeled fields: `Profile:`, `Outcome:`,
  `Constraints:`, `Invariants:` bullets, `Architecture:`, `Non-goals:` bullets,
  `Black box: yes|no`, `Forbidden in tests:`, `Test layers:`,
  `Clock injection: yes|no`, `Max unit test seconds:`, `DI required: yes|no`,
  `DI preferred:`, `DI forbidden:`, `DI notes:`, `Guard commands:` bullets,
  `Expected outputs:` bullets, `Drift sources:`, `On drift:`,
  `Refactor approach:`, `Dead code:`, `Preserve API: yes|no`, and
  `Temporary hacks: yes|no`. `Invariants:` / `Architecture:` require
  `Constraints:`; testability fields require `Black box:`; DI fields require
  `DI required:`; refactor fields require `Refactor approach:`.
- `## Acceptance Criteria` uses `- [AC-nn] text` items with optional
  `Satisfied by: S-1, S-2`, `Verify:`, and `Evidence:` scalar fields.
- `## Parallel Plan` uses `- [ID] description` items with optional
  `Paths:`, `Directories:`, and `Depends on:` comma-separated fields.
- `## Work Units` uses `- [unit-ID] description` items with optional
  `Directories:` and `Depends on:` comma-separated fields.

Use either `## Parallel Plan` or `## Work Units`, not both. For example:

    ## Work Units
    - [backend] Implement and test the API changes
      Directories: src/api/, tests/api/

## Step and reference rules

- Step IDs match `S-<positive-number>`, are unique, and are stable identifiers;
  order does not determine identity.
- `Type: file_change` requires one or more `Files:` bullets such as
  `- modify src/foo.py`.
- `Type: verify` requires `Verify:` or `Location:`.
- `Depends on:` is a comma-separated list of existing step IDs. Dangling
  references and cycles are errors.
- `Satisfies:` names criteria declared as `- [AC-01] ...`; each criterion's
  `Satisfied by:` field names existing step IDs.
- Verification commands must not start with `bash -c`, `sh -c`, or `eval`.

Unknown closed-vocabulary values produce warnings and safe coercions. Missing
sections, malformed IDs, broken references, invalid step contracts, and size
limit violations are hard errors.
