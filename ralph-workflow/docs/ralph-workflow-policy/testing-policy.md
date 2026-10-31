<!-- ralph-policy-schema: v2 -->
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

## Test optimization

Optimize a slow or over-budget suite before changing its limits. Apply these
moves while preserving the clearest test for every contract:

1. Delete TDD-scaffolding coverage once a clearer test pins the same behavior;
   never delete the sole coverage for a path.
2. Collapse an integration case to a unit test when an injectable seam can
   express the same observable contract more cheaply.
3. Retire an end-to-end test once a deterministic integration test covers the
   same contract; retain the smallest higher-layer test for boundaries the
   lower layer cannot observe.

Use this lifecycle whenever a suite exceeds its budget or a test is slow.
Refactor seams for testability rather than splitting, skipping, or weakening
the gate.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

RALPH-FACT: test_command: `make test`, run from `ralph-workflow/` (the Makefile `test` target executes `uv run --locked --project . python -m ralph.test_suites`, which selects the default lane with the mark expression `(not subprocess_e2e and not smoke) or required_auto_integrate_e2e` declared in `ralph/test_suites.py`). The suite-selection helpers `make test-unit`, `make test-integration`, and `make test-fast` are focused developer profiles over the same runner; `make test` is the default lane.

RALPH-FACT: test_command_prerequisites: `uv` >= 0.7.0 (the Makefile `uv-preflight` target is the project's declared minimum and fails closed with the install URL below it) and the project-local `.venv` (the Makefile exports `VIRTUAL_ENV := $(abspath .venv)` and defines `RUN_PYTHON = uv run --locked --project . python`, so the locked dependency set in `uv.lock` is resolved from the checkout without a separate install step). No package manager bootstrap is required beyond a `uv` binary on `PATH`.

RALPH-FACT: primary_test_framework: pytest — `pytest>=8.0` in the `ralph-workflow/pyproject.toml` dev dependencies and `minversion = 8.0` in `ralph-workflow/pytest.ini` — with pytest-xdist (`pytest-xdist>=3.6`) supplying the parallel workers; `ralph/test_suites.py` shards the default lane across up to `_MAX_PYTEST_WORKERS = 8` concurrent workers.

RALPH-FACT: secondary_test_frameworks: none beyond pytest plugins. `pytest-asyncio` (>= 0.23, `pytest.ini` `asyncio_mode = auto`), `pytest-cov` (>= 4.0, used only by the `test-cov` coverage target), and `hypothesis` (>= 6.100) are pytest plugins rather than separate test frameworks. `vulture` (>= 2.16) is a standalone dead-code audit run by `make dead-code`; it is not a test framework and is not part of `make test`.

RALPH-FACT: test_isolation_strategy: injected fakes — `Clock` (`ralph/agents/clock.py`) and `FakeClock` (`ralph/agents/timeout_clock.py`) for time, `MemoryWorkspace` (`ralph/workspace/memory.py`), `FsWorkspace` (`ralph/workspace/fs.py`), and pytest's `tmp_path` for the filesystem, and `MockProcessExecutor` for subprocesses. `ralph/testing/audit_test_policy.py` fails the gate on real `time.sleep()` with non-zero arguments, real file `open()`, real subprocess calls, and real socket/`urllib`/`requests`/`httpx` calls in any test that is not marked `subprocess_e2e`. Real subprocesses and sockets are therefore confined to the `subprocess_e2e` marker and run via `make test-subprocess-e2e`, with the sanctioned exception of the registered real-git auto-integration files: the selection registry `ralph/test_suites.py:REQUIRED_AUTO_INTEGRATE_E2E_FILES`, which `ralph-workflow/tests/conftest.py` re-marks `required_auto_integrate_e2e` at collection time so the default lane keeps one external Git proof, plus the audit-exception registry `ralph/test_suites.py:SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES` for default-lane files pytest cannot select (starting with `tests/conftest.py`); `ralph/testing/audit_test_policy.py` honours both so the sanction and the selection requirement cannot drift. `ralph-workflow/tests/test_install_make_smoke.py` is the deliberate `make verify` exception: it drives the public `make install` transaction with an isolated HOME/PATH, a fail-closed fake `uv`, and no network.

RALPH-FACT: flake_policy: every observed failure is owned and fixed — there is no rerun-and-hope, no `@pytest.mark.skip` / `@pytest.mark.xfail` masking, and no flake quarantine. Nondeterminism is repaired at the source (inject the clock, replace real I/O with fakes, refactor the seam behind an interface) rather than absorbed. The combined test budget is pinned: `ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS = 60.0` (wall clock, judged net of host contention) and `_TOTAL_TEST_CPU_BUDGET_SECONDS = 700.0` (the suite's own CPU core-seconds, a hard limit) are held by import-time epsilon checks that raise `RuntimeError` under `python -O`, so a slow or flaky test is a design defect to fix, never a budget to raise. A test that asserts on wall clock it does not own (a child process's startup time, a fixed sleep used as synchronization, a raw latency sample a scheduler stall can inflate) is such a defect.

RALPH-FACT: regression_test_convention: the regression test name encodes the regression, following the project's `<area>_regression_<bug_description>` snake_case convention — e.g. `test_development_result_regression_every_terminal_result_consumes_one_analysis_cycle` (`tests/test_partial_execution_result_routing.py`) and `test_opencode_transport_regression_stale_ralph_entry_is_dropped_not_rejected` (`tests/mcp/test_opencode_transport.py`). The name must be parseable by a future reader without opening the diff.

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
  I/O in tests. Use fakes and dependency injection.

## Verification

Run every gate below before claiming a change complies with this policy.

RALPH-COMMAND: make verify

The expected successful result is a deterministic test suite that
finishes within the project's documented budget. On failure, report the
failing test names and the failure category (assertion failure,
collection error, timeout, environmental). Never ignore or skip a
failure to obtain green.

`make verify` is the authoritative entry point and runs `make test`
(the default lane) plus the aggregate `make test-verification-smoke`
step; both are charged against the pinned combined budget enforced by
`ralph/verify.py` (60 s wall clock net of host contention or capacity, 700 CPU
core-seconds). An agent that
needs only the default lane may run `make test` from `ralph-workflow/`
for fast feedback, but only `make verify` proves that a change complies
with this policy.

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
  disabling, or deleting a requirement so that a failing change passes
  is forbidden; evolution clarifies and extends, it does not water down.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: testing-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v2 -->`
