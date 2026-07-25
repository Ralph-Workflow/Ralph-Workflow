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

## Philosophy — floor, not form

This document grammar is a **floor**, not a form. Required means *only what
a downstream consumer reads* — every other section is available structure
that you may omit when the work is small enough to skip without losing
clarity. Omitting a section with a stated reason is normal, not a violation.
You are encouraged to keep the plan short enough to re-read mid-run under
context pressure; state each commitment once, next to the thing it describes.

The validator splits its findings into three severities so you can tell the
difference between a blocker and an opinion:

- **error** — a pipeline-consumed anchor is broken. Each error names the
  consumer that cannot proceed (development_result proof cross-references,
  fan-out dispatch, bounded-exec safety, noop routing, the spec registry,
  the pydantic schema, or the override ledger itself). Errors block
  submission.
- **warning** — a shape or meaning check the validator predicts will cost
  the run something (a missing `Files:`, a vague `Verify:`, an unpaired
  `Expect:`). Warnings do not block submission; the analysis phase owns the
  substance check.
- **info** — an observation worth a second look. Stale overrides surface
  here so suppressions that no longer suppress anything are flagged.

You can override any non-error finding by recording a reason under
`## Validation Overrides` (one `- [RULE-ID] reason` line per override; an
optional `Where: <section>` label narrows the match to one section). The
overrides and the recorded reasons are returned in tool results and persist
with the plan. Overriding an error is a `PLAN026` warning; the error still
blocks.

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

Only machine-consumed structure is hard. Every error message names the
consumer it blocks; the bullets below name the same consumer in plain words.

- **Frontmatter `type: plan`** — blocking because the artifact spec registry
  (`ralph/mcp/artifacts/markdown/registry.py`) routes the parsed `'type'`
  value to the plan validator; an unknown type means no validator is invoked
  and the artifact is not canonical.
- **At least one `### [S-n] Title` step block** (unless `noop: true`) —
  blocking because the development_result `Plan Items Proven` proof in
  `ralph/phases/execution.py` cross-references step numbers; a plan with no
  steps produces no proof IDs.
- **Globally unique stable step IDs** (`S-n` with a positive number, no
  alphabetic or mixed malformed variants like `S-X`, `STEP-X`, or `S-1a`) —
  blocking because the development_result proof cross-references the same
  step numbers; duplicated or malformed IDs lose the link.
- **Resolvable `Depends on:` / `Satisfied by:` references and acyclic
  dependency graphs** — blocking because the development_result proof
  cross-references step numbers and a cycle is unrepresentable downstream.
- **Fail-closed `## Work Units` / `## Parallel Plan` marker grammar** —
  blocking because the worker fan-out in `ralph/pipeline/work_units.py`
  (and `ralph/pipeline/fan_out.py`) parses unit IDs, edit areas, and
  dependencies to scope edits and dispatch units. Loose prose under those
  headings, malformed bullets, missing declared units, or unresolvable
  unit dependencies all block submission. The fan-out parser matches an
  exact, case-sensitive `## Work Units` or `## Parallel Plan` heading and
  the section fails closed on any line that is not a `- [unit-id]`
  item. Acceptance-criterion items are criteria, never phantom work units.
- **`noop: true` literal value with exactly `type: plan` frontmatter and no
  body** — blocking because `ralph/phases/analysis.py` reads `noop: true`
  to short-circuit the planning pipeline; a malformed no-op is not
  routable.
- **No shell-interpreter prefix on verification commands** (`bash -c`,
  `sh -c`, `eval`, …) — blocking because the bounded-exec safety policy
  forbids shell interpreter invocations (`subprocess.run` is invoked without
  `shell=True`); such a command bypasses the policy at every subprocess call
  site.
- **Plan size within the MCP payload bound** — blocking because the plan is
  carried through MCP tool result payloads and unbounded documents exceed
  the bounded payload contract.
- **Pydantic schema constraints** (`schema_version` integer, etc.) —
  blocking because `ralph/mcp/artifacts/plan/_validation.py` enforces
  pydantic field schemas on the canonical plan content dict; an invalid
  value prevents `normalize_plan_artifact_content` from returning.

Acceptance criteria, verification commands, file targets, and similar shape
or meaning checks are advisory (warnings). They do not block submission and
may be recorded under `## Validation Overrides` when the planner has a
defensible reason.

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
- Verification commands must not start with `bash -c`, `sh -c`, or `eval`.
- Work units use `- [unit-id] description`; add `Directories:` and
  `Depends on:` only when fan-out consumes them.
- Summary, Scope, Skills MCP, Critical Files, Design, Constraints, and Risks
  may use natural prose and the labels shown in the example.

Descriptive labels and vocabulary are advisory. `Intent`, `Coverage`, scope
`Category`, step `Type`, step `Priority`, target and critical-file `Action`,
and risk `Severity` are free-form descriptive hints, so project-specific
`Type:` values and target actions are preserved verbatim. Recommended
built-in `Type:` values follow the built-in `file_change` and `verify`
contracts; project-specific values are accepted and preserved verbatim. An
unrecognized field label may still produce a warning or remain prose; never
depend on a typo being consumed. Arbitrary headings remain descriptive;
machine-consumed anchors stay strict, so only the sections the validator or
fan-out reads are enforced. Structural hard errors name the malformed ID,
unresolved reference, fan-out marker, or unevaluatable check that must be
repaired.

Descriptive labels and vocabulary are advisory. `Intent`, `Coverage`, scope
`Category`, step `Type`, step `Priority`, target and critical-file `Action`,
and risk `Severity` are free-form descriptive hints. Project-specific
`Type:` values and target actions are preserved verbatim, and arbitrary
headings remain descriptive — only the fan-out parser, validators, and the
schema rules above enforce structure.

## Validation Overrides

When a non-error advisory finding would bounce the plan but you have a
defensible reason to leave the warning in place, record the override inside
the plan so the validator partitions the finding into the `overridden` list
returned with the submit receipt:

```markdown
---
type: plan
---
## Steps

### [S-1] Override example step
Illustrative step that pairs with the validation overrides below.

## Validation Overrides
- [PLAN010] The step writes only metadata; downstream consumers read it from
  the registry, so the missing `Files:` is intentional.
- [PLAN020] Where: Steps The `Verify:` command is documented in the
  repository README and runnable from any working directory; the analysis
  phase knows where to find it.
```

The grammar is fail-closed:

- Every override is `- [RULE-ID] reason` (an optional `Where: <section>`
  label narrows the match to one section).
- Errors are not overridable; an override targeting an error rule surfaces as
  `PLAN026` (warning) and the error still blocks submission.
- An override whose rule (and section, when narrowed) matches no diagnostic
  in this document surfaces as `PLAN025` (info) so stale suppressions are
  visible instead of silently lost.

The overrides persist with the plan (`.agent/artifacts/plan.md` and the
history archive) and are returned in tool results with the recorded reason,
so downstream consumers see the same ledger the planner wrote.
