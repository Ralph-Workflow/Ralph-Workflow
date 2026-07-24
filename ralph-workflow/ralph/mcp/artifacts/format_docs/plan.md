# plan artifact format

Write one executor-ready markdown plan. Use the structure that best fits the
work, prose for explanations, and stable IDs for machine-consumed references.
Validate with `ralph_verify_md_artifact`, then submit with
`ralph_submit_md_artifact` (`artifact_type: plan`). Large plans may use the
stage/get/finalize tools.

Call `ralph_edit_md_plan_step` with a step's stable `S-n` ID wherever that step
appears in the document, including under custom, repeated, or nested headings.
Insert, move, replace, and remove never renumber IDs. A replacement is one
complete `### [S-n] Title` block whose ID matches `step_id`.

Parallel work is delegated to agent-managed sub-agents. Ralph-managed fan-out is dormant
in this build, but work-unit markers are still validated when used.

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
Expect: the focused settings tests pass with exit code 0

## Acceptance Criteria
- [AC-01] The configured default is returned and covered by the regression test.
  Satisfied by: S-1
  Verify: pytest tests/test_settings.py -q
  Expect: the focused settings tests pass with exit code 0
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
Expect: test_clamp_out_of_range passes with exit code 0

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
  Expect: test_clamp_out_of_range passes with exit code 0

## Risks
- [R-1] Clamping could mask a caller bug
  Severity: medium
  Mitigation: Assert the exact boundary behavior in the regression test.

## Verification
- [V-1] pytest tests/test_foo.py -q
  Expect: test_clamp_out_of_range passes with exit code 0
  Timeout: 60
```

### Large task: four-subplan fan-out with main-session fan-in

Use this shape when four or five dedicated execution sub-agents can run
substantial independent mini-plans concurrently. The main session owns
explicit fan-in integration and verification after those subplans finish.
Step IDs remain globally unique across the document.

`## Work Units` is a different, lighter shape for small bounded tasks or
verification gates. A substantial execution sub-agent gets a repeatable
`## <Name> Subplan` or `## Subplan: <Name>` section with its own scoped steps.
Matching is case-insensitive and also accepts `Sub-plan` plus colon or dash
prefix/suffix variants; the standard forms above remain clearest for agents.
Targeted Subplans normalize to internal work-unit IDs such as
`subplan-s-10`; cross-subplan `Depends on: S-n` edges remap to those IDs.
A targetless integration section stays in the main session, so do not label the
fan-in section as a Subplan.

```markdown artifact=plan example-size=large
---
type: plan
---
## API Subplan

### [S-10] Add the API capability
Implement the endpoint and its focused unit coverage.

Type: file_change
Files:
- modify src/api/routes.py
- modify tests/api/test_routes.py

### [S-11] Prove the API capability
Run the API boundary tests inside this execution subplan.

Type: verify
Depends on: S-10
Verify: pytest tests/api/test_routes.py -q
Expect: the focused API route tests pass with exit code 0

## Web Subplan

### [S-20] Add the web workflow
Connect the client to the new endpoint and cover the visible behavior.

Type: file_change
Files:
- modify src/web/client.ts
- modify tests/web/client.test.ts

### [S-21] Prove the web workflow
Run the web-client test inside this execution subplan.

Type: verify
Depends on: S-20
Verify: npm test -- tests/web/client.test.ts
Expect: the focused web-client test passes with exit code 0

## Documentation Subplan

### [S-30] Document the operator path
Describe setup, expected behavior, and the concrete success check.

Type: file_change
Files:
- modify docs/operator-workflow.md

### [S-31] Prove the documentation contract
Run the repository's documentation check inside this execution subplan.

Type: verify
Depends on: S-30
Verify: python scripts/check_docs.py docs/operator-workflow.md
Expect: the documentation checker reports docs/operator-workflow.md valid

## Contract-Test Subplan

### [S-40] Add the shared contract test
Pin the request and response contract independently of either implementation.

Type: file_change
Files:
- modify tests/contracts/test_api_web_contract.py

### [S-41] Prove the shared contract
Run the contract test inside this execution subplan.

Type: verify
Depends on: S-40
Verify: pytest tests/contracts/test_api_web_contract.py -q
Expect: the API-to-web contract test passes with exit code 0

## Integration and Verification

### [S-50] Fan in the four execution-subplan outputs
Integrate the API, web, documentation, and contract-test changes and resolve
cross-surface mismatches.

Depends on: S-11, S-21, S-31, S-41

### [S-51] Run integrated verification
Prove the combined workflow after fan-in.

Type: verify
Depends on: S-50
Verify: pytest tests/integration/test_operator_workflow.py -q
Expect: the integrated operator-workflow test passes with exit code 0

## Acceptance Criteria
- [AC-10] The API behavior is covered at its public boundary.
  Satisfied by: S-10, S-11
  Verify: pytest tests/api/test_routes.py -q
  Expect: the focused API route tests pass with exit code 0
- [AC-20] The web workflow exercises the new API capability.
  Satisfied by: S-20, S-21
  Verify: npm test -- tests/web/client.test.ts
  Expect: the focused web-client test passes with exit code 0
- [AC-30] The operator workflow is documented with a success check.
  Satisfied by: S-30, S-31
  Evidence: docs/operator-workflow.md
- [AC-40] One contract test covers the API-to-web agreement.
  Satisfied by: S-40, S-41
  Verify: pytest tests/contracts/test_api_web_contract.py -q
  Expect: the API-to-web contract test passes with exit code 0
- [AC-50] All execution-subplan outputs operate together after fan-in.
  Satisfied by: S-50
  Verify: pytest tests/integration/test_operator_workflow.py -q
  Expect: the integrated operator-workflow test passes with exit code 0

## Verification
- [V-10] pytest tests/integration/test_operator_workflow.py -q
  Expect: the integrated operator-workflow test passes with exit code 0
- [V-20] npm test -- tests/web/client.test.ts
  Expect: the focused web-client test passes with exit code 0
```

## Structural freedom

Every conventional section is optional, repeatable, and may appear in any order.
Custom `##` headings are valid, and an `### [S-n] Title` step may live under any
section. In other words, arbitrary headings remain descriptive. The validator
therefore accepts radically different shapes, including:

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
  mini-plans. Alphabetic or mixed malformed step-like IDs such as `S-X`,
  `STEP-X`, or `S-1a` fail instead of disappearing into prose.
- Every step or criterion reference must be resolvable by stable ID.
  `Depends on:` and `Satisfied by:` values name existing `S-n` steps;
  dangling references and dependency cycles are errors.
- An exact, case-sensitive `## Work Units` or `## Parallel Plan` heading opts
  into machine-consumed fan-out grammar and fails closed. Loose top-level body
  prose and malformed bullets are errors. Stable-ID list items are parseable
  `- [unit-id] description` markers or evaluatable `- [AC-n]` criteria;
  nested `### [S-n]` step blocks may follow unit markers. A consumed section
  must declare at least one real unit. Acceptance-criterion items are criteria,
  never phantom work units. Optional `Directories:`, `Paths:`, and
  `Depends on:` fields must remain parseable, and unit dependencies must
  resolve. Lowercase lookalikes and other arbitrary headings remain
  descriptive.
- Acceptance criteria and verification must be genuinely evaluatable when
  used. Every `Verify:` command is paired with a specific `Expect:` output;
  alternatively, a criterion may name a specific `Evidence:` file/artifact and
  a verify step may name a specific `Location:`. Each `## Verification` item
  also declares a specific `Expect:` result. For compatibility, a step or
  criterion may reuse the outcome from a global Verification item whose command
  text matches exactly, but co-locating `Expect:` is clearer.
- Explicit Work Unit and Parallel Plan dependencies must resolve to declared
  unit IDs and form a DAG. Nested mini-plan step ownership is one-to-one.

Use direct verification commands; they must not start with `bash -c`, `sh -c`, or `eval`.
Project-specific `Type:` values and target actions are preserved verbatim,
without coercion. Recommended built-ins are `file_change`, `action`,
`research`, and `verify`; only the built-in `file_change` and `verify`
contracts add type-specific requirements. A step declared
`Type: file_change` supplies at least one `Files:` target.

## Conventional syntax

These conventions make the recommended outline easier to execute, but they do
not make the surrounding sections mandatory:

- Steps use `### [S-n] Title`, description prose, and optional fields such as
  `Type:`, `Priority:`, `Files:`, `Depends on:`, `Satisfies:`, `Verify:`,
  `Expect:`, `Location:`, `Rationale:`, and `Evidence:`. When `Verify:` is
  present, `Expect:` is required.
- Acceptance criteria use `- [AC-n] outcome` with `Verify:` plus `Expect:`, or
  with a specific `Evidence:` artifact; `Satisfied by:` may link them to steps.
- Verification uses `- [V-n] method` plus a concrete `Expect:` result.
- Work units use `- [unit-id] description`; add `Directories:` and
  `Depends on:` only when fan-out consumes them.
- Summary, Scope, Skills MCP, Critical Files, Design, Constraints, and Risks
  may use natural prose and the labels shown in the example.

Descriptive labels and vocabulary are advisory. `Intent`, `Coverage`, scope
`Category`, step `Type`, step `Priority`, target and critical-file `Action`,
and risk `Severity` are free-form descriptive hints, so project-specific
values are preserved. An unrecognized field label may still produce a warning
or remain prose; never depend on a typo being consumed.
Structural hard errors name the malformed ID, unresolved reference, fan-out
marker, or unevaluatable check that must be repaired.
