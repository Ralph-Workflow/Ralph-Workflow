# plan artifact format

Write one executor-ready markdown plan. Use the structure that best fits the
work, prose for explanations, and stable IDs for machine-consumed references.
Validate with `ralph_verify_md_artifact`, then submit with
`ralph_submit_md_artifact` (`artifact_type: plan`). Large plans may use the
stage/get/finalize tools.

For a step inside a conventional `## Steps` section, call
`ralph_edit_md_plan_step` with its stable `S-n` ID. Insert, move, replace, and
remove never renumber IDs. A replacement is one complete
`### [S-n] Title` block whose ID matches `step_id`. Resubmit the whole document
to edit steps placed under custom or nested headings.

Parallel work is delegated to agent-managed sub-agents. Ralph-managed fan-out
is dormant in this build, but work-unit markers are still validated when used.

See the complete opinionated sample:
`.agent/artifact-formats/examples/plan.md`.

## Strongly recommended best-practice outline

For an ordinary task, the strongly recommended outline is Summary, Scope,
Skills MCP, Steps, Critical Files, optional Design or Constraints, Acceptance
Criteria, Risks, and Verification. It gives an executor the clearest handoff,
but it is guidance rather than a required skeleton.

Choose detail in proportion to risk and coordination cost. The three complete
examples below are all valid; they are size-based recommendations, not three
additional schemas.

### Tiny task: compact checklist

Use a compact shape when one bounded change and one focused check communicate
the whole job.

```markdown artifact=plan example-size=tiny
---
type: plan
---
## Checklist

### [S-1] Update and prove the timeout default
Change the default and add one focused regression test.

Type: file_change
Files:
- modify src/settings.py
- modify tests/test_settings.py
Verify: pytest tests/test_settings.py -q

## Acceptance Criteria
- [AC-01] The configured default is returned and covered by the regression test.
  Satisfied by: S-1
  Verify: pytest tests/test_settings.py -q
```

### Medium task: conventional linear plan

Use the conventional outline when a small sequence, explicit risks, and one
verification strategy make execution clearer.

```markdown artifact=plan example-size=medium
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

### Large task: five-work-unit fan-out

Use this shape when four independent sub-agents can work concurrently and a
fifth unit owns explicit fan-in integration and verification. Each work unit
has a separate subplan; step IDs remain globally unique across the document.

```markdown artifact=plan example-size=large
---
type: plan
---
## Work Units
- [api] Implement the API change
  Directories: src/api/, tests/api/
- [web] Implement the web client change
  Directories: src/web/, tests/web/
- [docs] Document the operator workflow
  Directories: docs/
- [contract-tests] Add cross-surface contract coverage
  Directories: tests/contracts/
- [integration] Integrate all four outputs and run fan-in verification
  Directories: integration/, tests/integration/
  Depends on: api, web, docs, contract-tests

## API Subplan

### [S-10] Add the API capability
Implement the endpoint and its focused unit coverage.

Type: file_change
Files:
- modify src/api/routes.py
- modify tests/api/test_routes.py

## Web Subplan

### [S-20] Add the web workflow
Connect the client to the new endpoint and cover the visible behavior.

Type: file_change
Files:
- modify src/web/client.ts
- modify tests/web/client.test.ts

## Documentation Subplan

### [S-30] Document the operator path
Describe setup, expected behavior, and the concrete success check.

Type: file_change
Files:
- modify docs/operator-workflow.md

## Contract-Test Subplan

### [S-40] Add the shared contract test
Pin the request and response contract independently of either implementation.

Type: file_change
Files:
- modify tests/contracts/test_api_web_contract.py

## Integration Subplan

### [S-50] Fan in the four work-unit outputs
Integrate the API, web, documentation, and contract-test changes and resolve
cross-surface mismatches.

Depends on: S-10, S-20, S-30, S-40

### [S-51] Run integrated verification
Prove the combined workflow after fan-in.

Type: verify
Depends on: S-50
Verify: pytest tests/integration/test_operator_workflow.py -q

## Acceptance Criteria
- [AC-10] The API behavior is covered at its public boundary.
  Satisfied by: S-10
  Verify: pytest tests/api/test_routes.py -q
- [AC-20] The web workflow exercises the new API capability.
  Satisfied by: S-20
  Verify: npm test -- tests/web/client.test.ts
- [AC-30] The operator workflow is documented with a success check.
  Satisfied by: S-30
  Evidence: docs/operator-workflow.md
- [AC-40] One contract test covers the API-to-web agreement.
  Satisfied by: S-40
  Verify: pytest tests/contracts/test_api_web_contract.py -q
- [AC-50] All work-unit outputs operate together after fan-in.
  Satisfied by: S-50
  Verify: pytest tests/integration/test_operator_workflow.py -q

## Verification
- [V-10] pytest tests/integration/test_operator_workflow.py -q
  Expect: the integrated operator-workflow test passes with exit code 0
- [V-20] npm test -- tests/web/client.test.ts
  Expect: the focused web-client test passes with exit code 0
```

## Structural freedom

Every conventional section is optional, repeatable, and may appear in any order.
Custom `##` headings are valid, and an `### [S-n] Title` step may live under any
section. The validator therefore accepts radically different shapes, including:

- one linear `## Steps` list;
- two or more separate subplans, each with its own scope, steps, and criteria;
- one section per sub-agent, each with independently scoped steps; and
- `## Work Units` or `## Parallel Plan` followed by full nested mini-plans for
  each unit.

Repeated conventional sections are merged for validation. Section order never
defines identity, and omitting Summary, Scope, Skills MCP, Critical Files,
Design, Constraints, Risks, or Verification does not by itself reject a plan.

## Hard contract

Only machine-consumed structure is hard:

- Frontmatter must contain `type: plan`.
- A non-noop plan must contain at least one `### [S-n] Title` block somewhere
  in the document. Each ID uses a positive number and is globally unique
  across all linear steps, separate subplans, sub-agent sections, and nested
  mini-plans.
- Every step or criterion reference must be resolvable by stable ID.
  `Depends on:` and `Satisfied by:` values name existing `S-n` steps;
  dangling references and dependency cycles are errors.
- When `## Work Units` or `## Parallel Plan` is used for fan-out, each marker
  is a parseable `- [unit-id] description` item. Its optional `Directories:`,
  `Paths:`, and `Depends on:` fields must remain parseable, and unit
  dependencies must resolve.
- Acceptance criteria and verification must be genuinely evaluatable when
  used. Each `## Acceptance Criteria` item declares either a concrete
  `Verify:` command or a specific `Evidence:` file/artifact. Each
  `## Verification` item declares `Expect:`. A step declared
  `Type: verify` supplies `Verify:` or `Location:`.

Use direct verification commands; commands beginning with `bash -c`, `sh -c`,
or `eval` are rejected. A step declared `Type: file_change` supplies at least
one `Files:` target.

## Conventional syntax

These conventions make the recommended outline easier to execute, but they do
not make the surrounding sections mandatory:

- Steps use `### [S-n] Title`, description prose, and optional fields such as
  `Type:`, `Priority:`, `Files:`, `Depends on:`, `Satisfies:`, `Verify:`,
  `Location:`, `Rationale:`, and `Evidence:`.
- Acceptance criteria use `- [AC-n] outcome` with `Verify:` or `Evidence:`;
  `Satisfied by:` may link them to steps.
- Verification uses `- [V-n] method` plus a concrete `Expect:` result.
- Work units use `- [unit-id] description`; add `Directories:` and
  `Depends on:` only when fan-out consumes them.
- Summary, Scope, Skills MCP, Critical Files, Design, Constraints, and Risks
  may use natural prose and the labels shown in the example.

Descriptive labels and vocabulary are advisory. Unfamiliar wording may produce
a warning or be treated as prose; never depend on fallback behavior. Structural
hard errors name the malformed ID, unresolved reference, fan-out marker, or
unevaluatable check that must be repaired.
