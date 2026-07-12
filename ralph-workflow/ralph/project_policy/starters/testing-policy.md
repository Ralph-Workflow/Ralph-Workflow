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

* Tests SHOULD assert stable observable contracts at the narrowest useful
  boundary. Public-surface, contract, package, and internal unit tests are
  all valid when they express behavior cheaply without coupling to incidental
  implementation details.
* Test friction SHOULD trigger a production-boundary refactor only when it
  reveals a real cohesion, dependency, or I/O-seam problem. Do not create an
  artificial public API solely to accommodate a test.
* Narrower unit tests are appropriate for pure functions, parsers,
  validators, and decision tables where every branch is reachable from
  the function's signature alone.
* Automated testing is mandatory for first-party software behavior. A
  missing framework or code that is difficult to test requires a real test
  seam and gate; it does not make testing inapplicable.
* Tests MUST be deterministic and bounded. Unit tests isolate real time,
  filesystem, network, subprocess, and global singleton mutation. Integration,
  contract, system, and end-to-end tests MAY use controlled real resources
  when that interaction is the behavior under test; those resources MUST be
  isolated, reproducible, time-bounded, and cleaned up.
* Every bug fix MUST add a regression test that fails on the bug and
  passes on the fix. The test name SHOULD encode the regression so
  future readers understand the contract.
* Every new behaviour MUST add positive coverage. Negative coverage is
  mandatory when rejection, failure, permission, boundary, or recovery
  behavior exists.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

<!-- REPLACE-ME: record one verified, machine-checkable value per fact
below (commands, paths, names, versions — not adjectives or aspirations).
If the project is too young for a fact to be settled, record the best
current answer plus the condition that will settle it, e.g.
"none yet (assumed <date>; revisit when <trigger>)" — a future agent must
be able to tell a settled fact from a provisional one at a glance. Then
delete this comment. -->

RALPH-FACT: test_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: test_command_prerequisites: PROJECT-FACT-UNRESOLVED
RALPH-FACT: primary_test_framework: PROJECT-FACT-UNRESOLVED
RALPH-FACT: secondary_test_frameworks: PROJECT-FACT-UNRESOLVED
RALPH-FACT: test_isolation_strategy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: flake_policy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: regression_test_convention: PROJECT-FACT-UNRESOLVED

## AI execution instructions

To follow this policy, an agent making any change MUST:

* WRITE the test before the production change and report the observed red
  result. If it unexpectedly passes, confirm existing behavior and refine the
  missing contract. If characterization or generated/declarative work has no
  meaningful red phase, record why and never manufacture a failure.
* PREFER existing test helpers, fixtures, and utilities. Do not add a
  new testing dependency when the existing stack can express the case.
* AVOID adding a dependency, abstraction, or numeric target without
  demonstrated need from a failing test or observed behaviour.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy (facts, commands, requirements) in the same
  workflow that changes the test command, framework, or isolation
  strategy.

An agent MUST NOT:

* Default to white-box tests that couple to private internals.
* Weaken the testing gate to obtain a passing result (no skipping tests,
  no lowering coverage thresholds, no `--continue-on-collection-errors`).
* Introduce wall-clock sleeps or uncontrolled external I/O. Use fakes for
  unit tests and isolated bounded resources only at test layers intended to
  exercise those integrations.

## Verification

Run every gate below before claiming a change complies with this policy.

<!-- REPLACE-ME: set the project's real gate command. The first token must
be an approved gate tool (wrap anything else in `make`, `uv run`, or
`npx`). If the project has no such gate yet, create the smallest real one
(a make target running the actual check) rather than declaring a hollow
command; only a gate that truly cannot exist may be recorded as
inapplicable with a reason and the condition that would create it. Then
delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is a deterministic test suite that
finishes within the project's documented budget. On failure, report the
failing test names and the failure category (assertion failure,
collection error, timeout, environmental). Never ignore or skip a
failure to obtain green.

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
* The test isolation strategy or fake-injection pattern changes.
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
