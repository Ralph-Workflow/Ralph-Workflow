<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: testing-policy.md -->

# Testing Policy

## Purpose and scope

This policy governs how every AI agent working in this project plans,
writes, runs, and maintains automated tests. It applies to every change
that adds, modifies, or removes behaviour that could regress without a
test. It does NOT govern manual exploratory testing, end-to-end smoke
checks performed by humans, or third-party hosted service reliability.

The Ralph Workflow Python package (`ralph-workflow/ralph/`) ships
pure-Python automated tests under `ralph-workflow/tests/`; the
`ralph-workflow/skills-package/` Node.js bundle ships no automated
tests of its own and is excluded from the testing gate (the bundle is
treated as a distribution artefact, not a behaviour surface).

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
  for clocks, filesystems, and processes. Tests that require real I/O
  MUST be marked `subprocess_e2e` (or `live_agy`), are excluded from
  the default suite, and run via `make test-subprocess-e2e`.
* Every bug fix MUST add a regression test that fails on the bug and
  passes on the fix. The test name MUST encode the regression so
  future readers understand the contract.
* Every new behaviour MUST add at least one positive test (the behaviour
  works as documented) and one negative test (the behaviour rejects
  invalid input).
* Smoke tests (`@pytest.mark.smoke`) are one-off manual debug harnesses
  for a SPECIFIC agent issue. They MUST NOT run in any suite. Excluded
  by default in `pytest.ini` (`addopts = -m "not smoke"`).

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents
rely on them when enforcing this policy and MUST keep them current as
the project evolves.

RALPH-FACT: test_command: cd ralph-workflow && make test
RALPH-FACT: test_command_prerequisites: cd ralph-workflow && make dev (syncs the uv environment — Python 3.12+, dev extras, editable install)
RALPH-FACT: primary_test_framework: pytest (>= 8.0; configured in ralph-workflow/pytest.ini)
RALPH-FACT: secondary_test_frameworks: pytest-xdist (parallel workers via -n auto, capped at 8 in ralph/test_suites.py), pytest-asyncio (asyncio_mode=auto), pytest-cov (only on test-cov target), hypothesis (property-based tests for cross-process contracts; >= 6.100 in dev extras), vulture (dead-code audit; one-shot via `make dead-code`, not part of `make test`)
RALPH-FACT: test_isolation_strategy: real-time I/O is forbidden in the default suite — injected clocks (Clock / FakeClock), MemoryWorkspace / FsWorkspace, MockProcessExecutor, tmp_path for filesystem, RecordedMcpServerFactory for MCP. subprocess_e2e tests are the only path that exercises real subprocesses / sockets and they are excluded from the default suite via `make test`. The combined wall-clock budget is 60 seconds (ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS, IMMUTABLE) across every budget-tracked step.
RALPH-FACT: io_mocking_approach: in-process fakes only — no real network, real subprocess (unless tagged `subprocess_e2e`), real filesystem outside tmp_path, real clock, or real MCP server. The default suite uses Clock / FakeClock for time, MemoryWorkspace / FsWorkspace / tmp_path for filesystem, MockProcessExecutor for subprocess, and RecordedMcpServerFactory for MCP. Every helper lives in ralph-workflow/tests/_fakes/ or ralph-workflow/tests/conftest.py.
RALPH-FACT: suite_time_budget: 60 seconds combined (enforced by `make test` -> ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS) across every budget-tracked step in ralph/verify.py:_VERIFY_STEPS. The combined total is the IMMUTABLE contract; per-step timeouts sit at 30 s in ralph/verify.py:_VERIFY_STEP_TIMEOUT_SECONDS. The default test step is labeled "make test" in ralph/verify.py:_KNOWN_TEST_STEP_LABELS.
RALPH-FACT: per_test_timeout: 60 s per test (declared in ralph-workflow/Makefile as PYTEST_SUITE_TIMEOUT_SECONDS / PYTEST_SUITE_TIMEOUT and enforced by pytest via -o timeout=). subprocess_e2e tests are excluded from the default suite. The 60 s cap is a fail-closed ceiling; a test that hits it is a defect to repair, not a budget to raise.
RALPH-FACT: timeout_enforcement_mechanism: pytest's per-test --timeout option (raised by ralph-workflow/Makefile as the -o timeout flag) plus the combined budget in ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS tracked by the budget-tracked-steps set in ralph/verify.py:_BUDGET_TRACKED_STEPS. The combined budget tracker is enforced with `if` / `raise RuntimeError` (NOT `assert`) so it survives `python -O`; import-time invariants live in ralph/verify.py and are tested in ralph-workflow/tests/test_verify_invariants.py.
RALPH-FACT: flake_policy: any flaky test is a design defect, not a CI tax. Flake sources must be eliminated (inject clocks, remove real sleep, mock subprocess, refactor I/O behind an interface); freezing a test with @pytest.mark.skip or @pytest.mark.xfail without a tracked issue is forbidden. Quarantine is permitted only via the documented `subprocess_e2e` / `smoke` / `live_agy` / `verify_budget_real_time` markers, all of which deselect from `make test`.
RALPH-FACT: regression_test_convention: regression tests MUST follow `<area>_regression_<bug_description>` (snake_case test names) — e.g. `test_agy_classifier_regression_stale_session_resets_chain`, `test_recovery_classifier_regression_artifact_missing`. The test name MUST be parseable by a future reader without opening the diff. Each fix MUST also link the originating plan-step or how_to_fix item in the test docstring.

## AI execution instructions

To follow this policy, an agent making any change MUST:

* WRITE the test before the production change when fixing a bug or
  adding behaviour; watch it fail for the expected reason first.
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
* Introduce real `time.sleep()`, real filesystem I/O, or real network
  I/O in tests that are not marked `subprocess_e2e` (or `live_agy`).
* Mark a test `smoke` to dodge the budget.
* Add `@pytest.mark.skip` / `xfail` without an issue link and a
  documented rationale.
* Treat a slow or hanging suite as a cost to absorb, or "fix" it by
  raising a timeout, splitting the suite, or skipping the offender.

## Performance is a HARD failure — and usually an architecture defect

The 60-second combined budget is a **HARD CAP**, not a target to grow into.
A performance failure is treated as at least as serious as a functional
one, and often MORE serious: a functional failure is one broken behavior,
whereas a slow or hanging suite is normally a broken ARCHITECTURE that will
keep producing bugs.

* A suite that is slow, that hangs, or whose runtime grows superlinearly
  is a DEFECT to diagnose — never a number to raise.
* The usual root cause is a MISSING SEAM: production code that cannot be
  exercised without real I/O, a real subprocess, a real sleep, a real
  network, or a real agent. That is the signature of a test bound to
  internals rather than driving the system as a BLACK BOX through its
  injectable seams. A test that must reach through to the real world is
  telling you the design has no seam there — add the seam.
* Worked example (2026-07-13):
  `tests/test_interrupt.py::test_run_pipeline_saves_interrupted_resume_checkpoint`
  hung the entire `subprocess_e2e` suite indefinitely. It stubbed
  `run_func`, `ckpt`, `load_config`, and `resolve_workspace_scope`, but not
  the Phase 2c project-policy-readiness preflight — which therefore ran for
  real against an empty `tmp_path`, found the project unready, and invoked a
  REAL remediation agent (a `claude` subprocess plus a live MCP server) that
  never returned. The fix was to stub the documented
  `_run_project_policy_readiness` seam, NOT to raise the budget. The hang was
  the design telling us an un-injected side effect sat in the startup path.
* Fix the coupling in the production design. Raising the timeout, splitting
  the suite to dodge the budget, or skipping the test is FORBIDDEN.

## Verification

Run every gate below before claiming a change complies with this policy.

RALPH-COMMAND: make -C ralph-workflow test

The expected successful result is a deterministic test suite that
finishes within the project's documented 60-second combined budget (the
per-suite timeout is 60 s in `Makefile`; the combined budget is
pinned in `ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS`). On failure,
report the failing test names and the failure category (assertion
failure, collection error, timeout, environmental). Never ignore or
skip a failure to obtain green.

For subprocess E2E coverage (separate suite, excluded from the 60 s
budget):

RALPH-COMMAND: make -C ralph-workflow test-subprocess-e2e

These tests require real subprocesses or network sockets and are
deselected from `make test` via `-m "not subprocess_e2e"`. Run them
on demand before release; the per-suite timeout is the same 60 s cap.
Live AGY tests have their own `make test-live-agy` target with a sized
`LIVE_AGY_SUITE_TIMEOUT_SECONDS` (default 600 s) and remain excluded
from the combined budget.

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
  review date: 2026-07-12

* publisher: Google Testing Blog
  title: "Flaky Tests At Google and How We Mitigate Them"
  http: https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html
  review date: 2026-07-12

* publisher: Martin Fowler
  title: "Test Pyramid"
  http: https://martinfowler.com/bliki/TestPyramid.html
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
* Schema version: `<!-- ralph-policy-schema: v1 -->`
