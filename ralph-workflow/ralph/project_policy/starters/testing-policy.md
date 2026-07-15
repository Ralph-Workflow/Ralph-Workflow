<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: testing-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, and deletes this banner. Readiness stays
blocked while this banner or any placeholder token remains. -->

# Testing Policy

## Purpose and scope

This policy governs how every AI agent working in this project plans,
writes, runs, and maintains automated tests. It applies to every change
that adds, modifies, or removes behaviour that could regress without a
test. It does NOT govern manual exploratory testing, end-to-end smoke
checks performed by humans, or third-party hosted service reliability.

## Default requirements

The numbered rules below are the concrete, enforceable obligations of this
policy. Each is MANDATORY unless it says SHOULD. They are co-equal: a change
that satisfies one rule by breaking another — fast tests with no coverage,
thorough tests with no time limit — does NOT comply.

1. Assert stable observable contracts at the narrowest useful boundary.
   Public-surface, contract, package, and internal unit tests are all valid
   when they express behavior cheaply without coupling to incidental
   implementation details.
2. Prefer narrow unit tests for pure functions, parsers, validators, and
   decision tables where every branch is reachable from the function's
   signature alone.
3. Refactor the production boundary only when a test reveals a real
   cohesion, dependency, or I/O-seam problem. Do NOT invent a public API
   solely to accommodate a test.
4. Automated testing is mandatory for first-party software behavior. A
   missing framework or code that is difficult to test requires a real test
   seam and gate; it does not make testing inapplicable.
5. Tests MUST be deterministic. A test that can pass or fail without a
   change in behavior is a defect: fix or remove it, never retry it until
   it goes green.
6. Unit tests MUST isolate real time, filesystem, network, subprocess, and
   global singleton mutation behind in-memory fakes. Integration, contract,
   system, and end-to-end tests MAY use controlled real resources when that
   interaction is the behavior under test; those resources MUST be isolated,
   reproducible, bounded, and cleaned up.
7. Mock real I/O by default. Real I/O is the dominant source of slow, flaky
   tests, so mocking, faking, or stubbing filesystem, network/HTTP,
   database, subprocess, and clock access is STRONGLY PREFERRED. Touching a
   real external resource is the EXCEPTION, permitted only at the
   integration, contract, system, or end-to-end layers where that specific
   interaction is the behavior under test.
8. Every test suite MUST enforce a bounded execution time limit, and the
   suite in the main verification pipeline MUST enforce one. This is NOT
   OPTIONAL: a suite with no enforced limit is itself a policy violation,
   not a slow-but-tolerable suite. The limit MUST be enforced by the test
   runner — a per-test and/or whole-suite timeout that FAILS the gate when
   exceeded — never by convention or reviewer vigilance. AI agents block on
   this pipeline, so one unbounded test can hang an entire run indefinitely.
9. A timeout is a HARD failure, never a shortcut to green — but the two
   limits evolve differently:
   - The per-test timeout catches one slow or hanging test. Raising it is
     almost always the wrong fix; repair the test instead (usually by
     mocking its I/O per rule 7).
   - The whole-suite budget scales with the number of tests, within a HARD
     CAP. Sizing guide: roughly 1 second per 1k LOC, capped at 1-2 minutes
     no matter how large the project gets. Past roughly 120k LOC the cap —
     not the per-LOC rate — is the binding constraint, and it does not
     move. Growing the budget below the cap is legitimate ONLY as a
     deliberate, reviewed maintenance change (see Maintenance triggers)
     that tracks more genuinely-fast tests, NEVER a quiet bump to hide a
     slow one. A suite already well under budget MUST NOT relax up toward
     the guide: it is a ceiling, never an entitlement.
   When it can, the gate SHOULD surface these time-limit and performance
   rules on an over-budget failure, so the developer's reflex is to fix the
   tests, not to raise the budget.
10. A PERFORMANCE failure is a HARD failure, and it is treated as at least
    as serious as a functional one — often more so, because a functional
    failure is one broken behavior while a slow or hanging suite is usually
    a broken ARCHITECTURE that will keep producing bugs.
    - A suite that is slow, that hangs, or whose runtime grows
      superlinearly is a DEFECT to diagnose, never a cost to absorb and
      never a number to raise.
    - The usual root cause is a missing seam: production code that cannot
      be exercised without real I/O, real subprocesses, real sleeps, a real
      network, or a real agent. That is the signature of tests bound to
      internals instead of driving the system as a BLACK BOX through
      injectable seams. A test that must reach through to the real world to
      run is telling you the design has no seam there — add the seam.
    - Fix the coupling in the production design. Do NOT raise the timeout,
      do NOT split the suite to dodge the budget, and do NOT mark the test
      skip/xfail to make the gate green.
11. Every bug fix MUST add a regression test that fails on the bug and
    passes on the fix. The test name SHOULD encode the regression so future
    readers understand the contract.
12. Every new behaviour MUST add positive coverage. Negative coverage is
    mandatory when rejection, failure, permission, boundary, or recovery
    behavior exists.
13. Shape the suite so the cheapest, most isolated layers carry the most
    cases. The count of tests SHOULD grow as their scope narrows: many fast
    tests at the lowest layer a unit of behavior supports, fewer integration
    tests that exercise real collaboration across a seam, and only a thin cap
    of end-to-end tests. The exact ratio is stack-dependent, NOT a fixed
    quota: backend and library code that decomposes into pure units leans
    heavily toward unit tests (the classic test pyramid), while UI-heavy or
    full-stack code legitimately shifts weight toward component and
    integration tests, where a rendered component or a wired-up module — not
    an isolated function — is the smallest honest unit of behavior (the
    "testing trophy"). What is invariant across every stack is the shape:
    broad and cheap at the base, narrow and expensive at the top.
14. Match each layer to the job it does cheapest, and keep case enumeration
    OUT of the slow layers. Exhaustive branch, edge, boundary, and negative
    coverage (rule 12) belongs at the unit or component layer, where every
    case is reachable directly and runs fast. Integration tests SHOULD cover
    the contracts and failure modes across a seam that unit tests cannot see.
    End-to-end tests are the SCARCEST resource: reserve them for a small
    number of critical user journeys — the primary happy path of each, plus
    the few failure paths whose breakage would be catastrophic — never as the
    place to enumerate variations. Pushing case enumeration up into slow,
    brittle end-to-end tests is the specific anti-pattern the pyramid exists
    to prevent, and it is also the usual cause of an over-budget suite
    (rule 10).

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

<!-- REPLACE-ME: record one verified, machine-checkable value per fact
below (commands, paths, names, versions — not adjectives or aspirations).
If a fact cannot be resolved yet (project too young, tool not chosen, value
not knowable), defer it with the RALPH-PENDING form "RALPH-PENDING (assumed
<date>); review trigger: <trigger>" — it reaches readiness and a dev-cycle
agent resolves it when its trigger fires. Then
delete this comment. -->

RALPH-FACT: test_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: test_command_prerequisites: PROJECT-FACT-UNRESOLVED
RALPH-FACT: primary_test_framework: PROJECT-FACT-UNRESOLVED
RALPH-FACT: secondary_test_frameworks: PROJECT-FACT-UNRESOLVED
RALPH-FACT: test_isolation_strategy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: io_mocking_approach: PROJECT-FACT-UNRESOLVED
RALPH-FACT: suite_time_budget: PROJECT-FACT-UNRESOLVED
RALPH-FACT: per_test_timeout: PROJECT-FACT-UNRESOLVED
RALPH-FACT: timeout_enforcement_mechanism: PROJECT-FACT-UNRESOLVED
RALPH-FACT: flake_policy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: regression_test_convention: PROJECT-FACT-UNRESOLVED

## AI execution instructions

To follow this policy, an agent making any change MUST:

* WRITE the test before the production change and report the observed red
  result. If it unexpectedly passes, confirm existing behavior and refine the
  missing contract. If characterization or generated/declarative work has no
  meaningful red phase, record why and never manufacture a failure.
* MOCK real I/O by default. Reach for an in-memory fake for filesystem,
  network, database, subprocess, and clock before writing any test that
  touches the real resource; use a real resource only at the integration
  layers named above, and justify it.
* PREFER existing test helpers, fixtures, and utilities. Do not add a
  new testing dependency when the existing stack can express the case.
* AVOID adding a dependency, abstraction, or numeric target without
  demonstrated need from a failing test or observed behaviour.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy (facts, commands, requirements) in the same workflow
  that changes the test command, framework, isolation strategy, mocking
  approach, or time budget.

An agent MUST NOT:

* Default to white-box tests that couple to private internals.
* Weaken the testing gate to obtain a passing result: no skipping tests,
  no lowering coverage thresholds, no raising/disabling/deleting the suite
  or per-test time limit, no `--continue-on-collection-errors`.
* Introduce wall-clock sleeps or uncontrolled external I/O. Use fakes for
  unit tests and isolated bounded resources only at test layers intended to
  exercise those integrations.

## Verification

Run every gate below before claiming a change complies with this policy.

<!-- REPLACE-ME: set the project's real gate command. The first token must
be an approved gate tool (wrap anything else in `make`, `uv run`, or
`npx`). If the project has no such gate yet, create the smallest real one
(a make target running the actual check) rather than declaring a hollow
command; a gate that applies but is not wired yet (for example the tool is
not installed on a new project) is recorded as a RALPH-PENDING deferral —
`RALPH-PENDING: <approved-tool> (assumed <date>); review trigger: <trigger>`
— which reaches readiness and is resolved by a later dev cycle when its
trigger fires; only a gate that truly cannot EVER exist is recorded as
inapplicable with a reason and the condition that would create it. The gate
MUST enforce the suite time limit recorded in the RALPH-FACT lines above
(via the runner's timeout, not a manual stopwatch).
You are FILLING OUT THIS FORM, not fixing the project: record the real gate
command and confirm it EXISTS and enforces a timeout (you MAY run it once as
a bounded probe, capped at ~10s, to check that it resolves). Do NOT fix
failing tests or run the suite to green — a failing or slow suite is the
project's problem to address later, not a form-filling blocker. Run only the
commands you declare here, and if you write a helper script to wire a gate,
cover it with a unit test. Then delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is a deterministic test suite that finishes
within its enforced time budget (see the `suite_time_budget` and
`per_test_timeout` facts above). On failure, report the failing test names
and the failure category (assertion failure, collection error, timeout,
environmental).

A `timeout` category is a HARD failure meaning the suite exceeded its
enforced limit. Fix the slow test (usually by mocking its I/O per rule 7);
do not raise the per-test timeout or quietly bump the suite budget to pass.
The suite budget grows only as a deliberate, reviewed change that tracks a
larger test count (rule 9), never to hide a slow test. When it can, the gate
SHOULD print or link this policy's time-limit and performance rules (8, 9,
7) on an over-budget failure, so the developer is reminded to fix the tests
rather than the budget. Never ignore or skip a failure to obtain green.

## Exceptions

A narrower scope (e.g. no negative tests for purely declarative YAML
schemas) requires a documented rationale in this section, the scope of
the exception, and the owner of the exception. Exceptions expire at the
next policy review; an expired exception without an updated rationale
is treated as a violation.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* The test framework, test runner, or test command changes.
* A new test layer (unit / integration / end-to-end / contract) is
  introduced.
* The test isolation strategy, mocking approach, or fake-injection pattern
  changes.
* The suite time budget, per-test timeout, or timeout enforcement
  mechanism changes.
* Coverage thresholds, mutation testing, or other quality bars are
  changed.
* A new test dependency is added or an existing one is replaced.

## Research basis

* publisher: Google Testing Blog / Google Engineering Practices
  title: "Just Say No to More End-to-End Tests"
  http: https://testing.googleblog.com/2015/04/just-say-no-to-more-end-to-end-tests.html
  review date: 2026-07-11

* publisher: Google Testing Blog
  title: "Flaky Tests At Google and How We Mitigate Them"
  http: https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html
  review date: 2026-07-11

* publisher: Martin Fowler
  title: "Test Pyramid"
  http: https://martinfowler.com/bliki/TestPyramid.html
  review date: 2026-07-11

* publisher: Kent C. Dodds
  title: "The Testing Trophy and Testing Classifications"
  http: https://kentcdodds.com/blog/the-testing-trophy-and-testing-classifications
  review date: 2026-07-14

* publisher: Pearson / Kent Beck
  title: "Test-Driven Development: By Example"
  http: https://www.pearson.com/en-us/subject-catalog/p/test-driven-development-by-example/P200000009421/9780321146533
  review date: 2026-07-12

## Living document contract

This policy is a living document. It MUST evolve as the project grows:
update the resolved facts, commands, and requirements whenever verified
project reality changes (new frameworks, new commands, new structure).
Two guardrails bound every amendment:

* Conflicts between this policy's generic defaults and the project's
  established practice are resolved in
  favor of the existing project policy — adapt this file to verified
  project reality, never the reverse. A looser project practice is
  NOT such a conflict: keep the stronger requirement unless a
  documented exception narrows it.
* An amendment MUST NOT subvert the INTENT of this policy. Weakening,
  disabling, or deleting a requirement so that a failing change passes is
  forbidden; evolution clarifies and extends, it does not water down.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: testing-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v2 -->`
