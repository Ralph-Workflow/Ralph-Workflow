<!-- ralph-policy-schema: v1 -->
<!-- ralph-policy-id: testing-policy.md -->

# Testing Policy

## Purpose and scope

This policy governs how every AI agent working in this project plans,
writes, runs, and maintains automated tests. It applies to every change
that adds, modifies, or removes behaviour that could regress without a
test. It does NOT govern manual exploratory testing, end-to-end smoke
checks performed by humans, or third-party hosted service reliability.

## Default requirements

* The test suite MUST be black-box by default: tests assert observable
  behaviour through the project's public surface (CLI entry points, MCP
  tool handlers, library exports, HTTP endpoints, CLI outputs). White-box
  tests that reach into private internals are permitted only when no
  observable surface can express the regression.
* When a behaviour cannot be expressed through the public surface
  cleanly, the agent MUST refactor the production boundary (extract an
  interface, add a seam, return a typed value) so a black-box test is
  possible. Defaulting to white-box coupling is a design defect.
* Narrower unit tests are appropriate for pure functions, parsers,
  validators, and decision tables where every branch is reachable from
  the function's signature alone.
* Tests MUST be deterministic: no real time, real filesystem, real
  network, real subprocess, or global singleton mutation. Inject
  dependencies through constructors or fixtures; use fakes and doubles
  for clocks, filesystems, and processes.
* Every bug fix MUST add a regression test that fails on the bug and
  passes on the fix. The test name SHOULD encode the regression so
  future readers understand the contract.
* Every new behaviour MUST add at least one positive test (the behaviour
  works as documented) and one negative test (the behaviour rejects
  invalid input).

## Project facts to resolve

The agent MUST resolve every `RALPH-FACT:` line below with a value
verified against repository evidence (commands actually run during this
preflight, manifest files actually present in the project tree, etc.).
Placeholder tokens in the `RALPH-FACT:` values are forbidden — the
validator will reject any policy that still contains them.

* RALPH-FACT: test_command: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: test_command_prerequisites: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: primary_test_framework: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: secondary_test_frameworks: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: test_isolation_strategy: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: flake_policy: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: regression_test_convention: PROJECT-FACT-UNRESOLVED

## AI execution instructions

The agent MUST:

* INSPECT the project to identify the test framework, test command, and
  test isolation strategy before editing. List the evidence used.
* PRESERVE stricter existing testing requirements. Reconcile any
  contradiction by adapting the stricter rule, not weakening the policy.
* REPLACE every starter placeholder above with a verified project fact.
* REMOVE inapplicable conditional sections rather than marking them
  complete.
* PREFER existing test helpers, fixtures, and utilities. Do not add a
  new testing dependency when the existing stack can express the case.
* AVOID adding a dependency, abstraction, or numeric target without
  demonstrated need from a failing test or observed behaviour.
* RUN every declared `RALPH-COMMAND:` gate and report the outcome. Do
  not report commands that were not actually run.
* UPDATE this policy and the related docs in the same workflow that
  changes the test command or test isolation strategy.
* REFUSE to add the completion marker comment
  while any placeholder, contradiction, or unverified command remains.

The agent MUST NOT:

* Default to white-box tests that couple to private internals.
* Weaken the testing gate to obtain a passing result (no skipping tests,
  no lowering coverage thresholds, no `--continue-on-collection-errors`).
* Introduce real `time.sleep()`, real filesystem I/O, or real network
  I/O in tests. Use fakes and dependency injection.

## Verification

The agent MUST declare at least one `RALPH-COMMAND:` line below. A line
that is empty, contains a placeholder token, or names a non-runnable
command is rejected by the validator.

* RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is a deterministic test suite that
finishes within the project's documented budget. On failure, the agent
MUST report the failing test names and the failure category (assertion
failure, collection error, timeout, environmental). The agent MUST NOT
ignore failures or skip them to obtain green.

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

Citations supporting the requirements above.

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

* publisher: xUnit Test Patterns (Meszaros)
  title: "Goals of Test Automation"
  http: https://xunitpatterns.com/Goals%20of%20Test%20Automation.html
  review date: 2026-07-11

## Ralph markers

* Policy id: `<!-- ralph-policy-id: testing-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
* Completion marker: the `ralph-policy-complete` completion comment (added ONLY when
  every requirement above is satisfied and every placeholder is
  resolved).
