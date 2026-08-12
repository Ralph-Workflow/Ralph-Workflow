<!-- ralph-policy-schema: v3 -->
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
  the default suite (except the registered real-git auto-integration
  registry — see `test_isolation_strategy` below), and run via
  `make test-subprocess-e2e`.
* Every bug fix MUST add a regression test that fails on the bug and
  passes on the fix. The test name MUST encode the regression so
  future readers understand the contract.
* Every new behaviour MUST add at least one positive test (the behaviour
  works as documented) and one negative test (the behaviour rejects
  invalid input).
* Smoke tests (`@pytest.mark.smoke`) are one-off manual debug harnesses
  for a SPECIFIC agent issue. They MUST NOT run in any suite. Excluded
  by default in `pytest.ini` (`addopts = -m "not smoke"`).

## Suite admission

Not every acceptance criterion belongs in the default suite. Forcing one
in is how a suite grows large and slow while proving less, and the 60 s
combined budget leaves no room for tests that carry no distinct failure.
Route each criterion to exactly one lane before writing a test.

1. DEFAULT SUITE — the default lane, run by `make test` under the
   selection `(not subprocess_e2e and not smoke) or
   required_auto_integrate_e2e`. Use it when the criterion is objective,
   decidable by the machine, and reproducible from a clean clone with the
   in-process fakes named in `io_mocking_approach`. A criterion that could
   meet those constraints MUST NOT be routed elsewhere to dodge the work
   of building a seam.
2. HUMAN REVIEW — use it when the criterion is perceptual, ergonomic, or
   editorial: whether terminal output reads clearly, whether a status line
   is legible at width, whether documentation prose is accurate and
   unpadded. These are judgments, not assertions. Encoding one as a
   machine constant produces the worst kind of test — it fails on every
   legitimate redesign and never fails on a genuinely bad result. Record a
   dated review on the change instead. Automation MAY supply supporting
   evidence (rendered snapshots, width measurements); it MUST NOT supply
   the verdict.
3. NAMED PROFILE — use it when the check is genuinely repeatable and worth
   keeping but cannot meet the default suite's constraints: real
   subprocesses and sockets (`subprocess_e2e`, run by
   `make test-subprocess-e2e`) or a network-backed agent lifecycle
   (`live_agy`, run by `make test-live-agy`). Every profile is declared in
   `required_verification_profiles` in the verification policy with its
   Make target, and it fails hard when it runs. A profile is not a place
   to park a test that went red.
4. ONE-OFF EVIDENCE — use it when the verification is not repeatable and
   is not meant to be: a debug harness for a specific agent issue
   (`@pytest.mark.smoke`, excluded from every suite), a credential that
   expires, a sandbox that will be torn down, a one-time migration probe.
   Perform the check, record the dated command and its actual output on
   the change, and do not commit it as suite coverage. A check that
   cannot pass six months from now on a clean clone is not a test; it is
   a receipt, and filing it in the suite converts it into a scheduled
   failure a future agent will "fix" by deleting the assertion.

Two consequences bind:

* Liveness of a third party is a monitoring question, not a pre-merge
  question. "Does the vendor's API respond right now" belongs to
  operational alerting; "does our client handle the vendor's documented
  responses and failures" belongs to lane 1 against a fake. A default-suite
  test that fails during someone else's outage is testing their uptime
  with our gate.
* Routing to lane 2, 3, or 4 NEVER means the criterion goes unverified.
  Each lane carries its own evidence. A criterion with no lane, no owner,
  and no record is unverified, and that is a blocker.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents
rely on them when enforcing this policy and MUST keep them current as
the project evolves.

RALPH-FACT: test_command: cd ralph-workflow && make test
RALPH-FACT: test_command_prerequisites: from the checkout, `cd ralph-workflow && make test` resolves its locked environment through `uv run`. `make dev` is optional dev-install setup: it copies the checkout, runs `uv sync --extra dev` in the copied snapshot, and writes the `rdev` launcher; it does not bootstrap the current checkout in place.
RALPH-FACT: primary_test_framework: pytest (>= 8.0; configured in ralph-workflow/pytest.ini)
RALPH-FACT: secondary_test_frameworks: pytest-xdist (available to raw Make targets; `PYTEST_WORKERS=auto` is the default), pytest-asyncio (asyncio_mode=auto), pytest-cov (only on test-cov target), hypothesis (property-based tests for cross-process contracts; >= 6.100 in dev extras), and vulture (dead-code audit; one-shot via `make dead-code`, not part of `make test`). `make test` uses `ralph.test_suites`, whose auto mode caps concurrent plain-pytest file shards at 32; optional `PYTEST_XDIST_WORKERS_PER_SHARD` adds bounded in-shard workers.
RALPH-FACT: test_isolation_strategy: real-time I/O is forbidden in the default suite — injected clocks (Clock / FakeClock), MemoryWorkspace / FsWorkspace, MockProcessExecutor, tmp_path for filesystem, RecordedMcpServerFactory for MCP. subprocess_e2e tests are the only path that exercises real subprocesses / sockets and they are excluded from the default suite via `make test` EXCEPT for the registered real-git auto-integration files (`ralph/test_suites.py:REQUIRED_AUTO_INTEGRATE_E2E_FILES`), which are re-included by the `required_auto_integrate_e2e` marker that `ralph-workflow/tests/conftest.py` stamps at collection time. The combined wall-clock budget is 60 seconds (ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS, IMMUTABLE) across every budget-tracked step.
RALPH-FACT: io_mocking_approach: in-process fakes only — no real network, real subprocess (unless tagged `subprocess_e2e`), real filesystem outside tmp_path, real clock, or real MCP server. The default suite uses Clock / FakeClock for time, MemoryWorkspace / FsWorkspace / tmp_path for filesystem, MockProcessExecutor for subprocess, and RecordedMcpServerFactory for MCP. Reusable fixtures live in `ralph-workflow/tests/fixtures/`, `ralph-workflow/tests/_fixtures/`, and `ralph-workflow/tests/_support/`; narrowly scoped helpers may live in `tests/conftest.py`, root-level or feature-local `tests/**/*.py` helper modules, or the consuming test file. There is no `tests/_fakes/` directory.
RALPH-FACT: suite_time_budget: 60 seconds combined (enforced by ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS via cumulative time.monotonic() tracking across every step in ralph/verify.py:_BUDGET_TRACKED_STEPS) across `make test`, `make test-multimodal-smoke`, and `make test-visual-smoke`. The combined total is the IMMUTABLE contract; per-step timeouts sit at 30 s in ralph/verify.py:_VERIFY_STEP_TIMEOUT_SECONDS. The test labels are declared in ralph/verify.py:_KNOWN_TEST_STEP_LABELS.
RALPH-FACT: per_test_timeout: 1.0 s per test (declared as DEFAULT_TEST_TIMEOUT_SECONDS in ralph-workflow/ralph/verify_timeout.py:20, propagated to the pytest subprocess via the RALPH_PYTEST_TEST_TIMEOUT_SECONDS env var, and enforced in-process by the SIGALRM / ITIMER_REAL hookwrapper `pytest_runtest_call` in ralph-workflow/tests/conftest.py:52-83, which raises TestExecutionTimeoutError and charges WALL CLOCK during the test CALL phase only; the per-test override is @pytest.mark.timeout_seconds(value) registered at ralph-workflow/pytest.ini:13). The default-suite mark expression is `(not subprocess_e2e and not smoke) or required_auto_integrate_e2e` (ralph/test_suites.py:_VERIFICATION_MARK_EXPRESSION): every `subprocess_e2e` test is deselected EXCEPT the registered real-git auto-integration files in `ralph/test_suites.py:REQUIRED_AUTO_INTEGRATE_E2E_FILES`, which ralph-workflow/tests/conftest.py marks `required_auto_integrate_e2e` at collection time so the expression selects them even though they also carry `subprocess_e2e`. The 1.0 s cap is a fail-closed ceiling; a test that hits it is a defect to repair, not a budget to raise.
RALPH-FACT: timeout_enforcement_mechanism: three layers -- (1) per-test 1.0 s via the SIGALRM/ITIMER_REAL hookwrapper `pytest_runtest_call` in ralph-workflow/tests/conftest.py, raising TestExecutionTimeoutError; (2) per-suite wall-clock via ralph/verify_timeout.py:run_command_with_timeout, invoked as `python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS)` by the Makefile suite targets (Makefile:122, :125, :134, :157) and via ralph/test_suites.py for `make test`, converting a breach to exit code 124; (3) the combined 60 s budget in ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS tracked over ralph/verify.py:_BUDGET_TRACKED_STEPS. The combined budget tracker is enforced with `if` / `raise RuntimeError` (NOT `assert`) so it survives `python -O`; import-time invariants live in ralph/verify.py and are tested in ralph-workflow/tests/test_verify_invariants.py.
RALPH-FACT: flake_policy: any flaky test is a design defect, not a CI tax. Flake sources must be eliminated (inject clocks, remove real sleep, mock subprocess, refactor I/O behind an interface); freezing a test with @pytest.mark.skip or @pytest.mark.xfail without a tracked issue is forbidden. Quarantine is permitted only via the documented `subprocess_e2e` / `smoke` / `live_agy` / `verify_budget_real_time` markers; each deselects from `make test` via the `not` clauses of the default mark expression, with the single sanctioned exception that a file registered in `ralph/test_suites.py:REQUIRED_AUTO_INTEGRATE_E2E_FILES` may carry `subprocess_e2e` and still be selected through the `required_auto_integrate_e2e` marker.
RALPH-FACT: regression_test_convention: regression tests MUST follow `<area>_regression_<bug_description>` (snake_case test names) — e.g. `test_agy_classifier_regression_stale_session_resets_chain`, `test_recovery_classifier_regression_artifact_missing`. The test name MUST be parseable by a future reader without opening the diff. Each fix MUST also link the originating plan-step or how_to_fix item in the test docstring.
RALPH-FACT: assertion_quality_check: RALPH-PENDING (assumed 2026-08-09); review trigger: once a wired assertion-quality audit can prove every collected test has a falsifiable, distinct observable assertion without rejecting valid black-box tests
RALPH-FACT: clean_clone_setup_command: cd ralph-workflow && make dev (the documented fresh-clone dev-install bootstrap in CONTRIBUTING.md; it creates a copied `rdev` snapshot). Checkout-local Make targets resolve their locked environment through `uv run`.
RALPH-FACT: external_dependency_test_approach: test this project's handling of documented third-party responses and failures against in-process fakes; vendor liveness is operational monitoring, while real subprocesses/sockets use the declared `subprocess_e2e` profile and live AGY network calls use `live_agy`
RALPH-FACT: parallel_execution_mechanism: `make test` runs `uv run python -m ralph.test_suites`; `PYTEST_WORKERS=auto` selects up to 32 concurrent plain-pytest file shards, while optional `PYTEST_XDIST_WORKERS_PER_SHARD` adds bounded xdist workers; each maintained shard disables pytest cache with `-p no:cacheprovider` (ralph/test_suites.py)
RALPH-FACT: quarantine_mechanism_expiry_and_max_size: RALPH-PENDING (assumed 2026-08-09); review trigger: once the project declares and enforces a bounded quarantine registry with a maximum entry count and expiry; current skip policy requires an issue URL and resolution within one sprint but defines no registry or maximum
RALPH-FACT: slow_test_report_command: cd ralph-workflow && uv run python -m pytest -q --durations=20 (pytest's built-in slowest-test report; run on demand, not a separate gate)
RALPH-FACT: suite_review_cadence_and_owner: RALPH-PENDING (assumed 2026-08-09); review trigger: once a named person or team and recurring cadence are recorded for the RALPH-REVIEW suite-quality procedure; current maintenance triggers are change-driven only
RALPH-FACT: suite_test_count_command: cd ralph-workflow && uv run python -m pytest --collect-only -q (prints the collected-test count for the current checkout)
RALPH-FACT: supported_platform_matrix: Linux only is CI-verified for tests (`ubuntu-latest`, Python 3.12, `.github/workflows/verify.yml`); no macOS or Windows test CI matrix is declared, regardless of packaging classifiers
RALPH-FACT: design_capture_command: RALPH-PENDING (assumed 2026-08-11); review trigger: when the managed repository declares its executable web-UI renderer command in docs/ralph-workflow-policy/design-system-policy.md, the workspace-relative path ralph-workflow/ralph/visual/policy_facts.py:DESIGN_SYSTEM_POLICY_RELPATH reads (the seeded starter ralph-workflow/ralph/project_policy/starters/design-system-policy.md declares that location and today carries the field as an unresolved placeholder)
RALPH-FACT: visual_capture_handle: ralph://media/{artifact_id}
RALPH-FACT: visual_verdict_artifact: design_verdict

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

## Test optimization strategy

When a suite is slow or a test is added, consult the target repository's
optimization strategy when it has one; otherwise apply these moves before
splitting or quarantining tests:

* **Collapse down the pyramid.** Move a behavior from E2E to integration, or
  from integration to unit, when the lower layer can assert the same observable
  contract. Delete an E2E test once a deterministic integration test covers
  the behavior it existed to prove.
* **Delete redundant coverage.** TDD scaffolding that duplicates a later,
  clearer regression test is debt: keep the test that best expresses the
  contract and delete the duplicate. Never delete the sole coverage for a
  path.
* **Refactor for testability, not speed-hacking.** Extract the I/O seam so
  deterministic lower-level tests can replace slow real-world setup. Preserve
  the smallest higher-layer test that covers the boundary the lower layer
  cannot.

## Verification

Run every gate below before claiming a change complies with this policy.

RALPH-COMMAND: make -C ralph-workflow test

The expected successful result is a deterministic default test profile.
Its wall-clock time contributes to the project's 60-second combined budget
with the two smoke test steps under `make verify`; `Makefile`'s 60-second
per-suite limit is secondary to that immutable combined budget. On failure,
report the failing test names and the failure category (assertion
failure, collection error, timeout, environmental). Never ignore or
skip a failure to obtain green.

For subprocess E2E coverage (separate suite, excluded from the 60 s
budget):

RALPH-COMMAND: make -C ralph-workflow test-subprocess-e2e

These tests require real subprocesses or network sockets and are
deselected from the default `make test` mark expression
(`(not subprocess_e2e and not smoke) or required_auto_integrate_e2e` in
`ralph/test_suites.py`), with ONE sanctioned exception: the registered
real-git auto-integration files in
`ralph/test_suites.py:REQUIRED_AUTO_INTEGRATE_E2E_FILES` carry
`subprocess_e2e` AND the `required_auto_integrate_e2e` marker (stamped by
`ralph-workflow/tests/conftest.py`), which re-includes them in `make test`
so the authoritative profile keeps one external Git boundary proof. Run
the full subprocess suite on demand before release; the per-suite timeout
is the same 60 s cap.
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

* publisher: Martin Fowler
  title: "TestCoverage"
  http: https://martinfowler.com/bliki/TestCoverage.html
  review date: 2026-08-02

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
* Schema version: `<!-- ralph-policy-schema: v3 -->`

## Visual design evidence

An appearance assertion (CSS/class/style/DOM) is NOT evidence of design
quality. Design proof requires captures graded visually via the
`DesignVerdict` artifact and reachable through the project's canonical
capture handle scheme. Tests asserting rendered output MUST cite a
captured cell; tests asserting only CSS/class/style/DOM MUST be routed
to the design-system policy's `DesignVerdict` review lane rather than
the default suite.

The artifact and handle values below are verified against the maintained
Python package — `ralph/visual/design_verdict.py` mints the
`design_verdict` signed-off artifact, and
`ralph/mcp/tools/workspace/_media_capture.py` plus
`ralph/mcp/multimodal/resources` mint every `ralph://media/{artifact_id}`
handle that the agent-visible wire ledger carries. The capture command
defers because this project ships no managed UI; `ralph.visual.policy_facts`
and the bounded argv executor are wired for downstream projects that do,
but Ralph-Workflow itself has nothing to capture today.

This project's `Project facts to resolve` section carries the single
authoritative `RALPH-FACT` lines for the visual lane (the verdict artifact,
the capture handle, and the dated `design_capture_command` deferral); this
section explains what they mean and does not redeclare them.
