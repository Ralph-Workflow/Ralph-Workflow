<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: verification-policy.md -->

# Verification Policy

## Purpose and scope

This policy defines the authoritative verification entry point for the
project. It enumerates every gate that must pass before code can be
merged or released, the exact commands, the prerequisites, and the
bypass-detection rules.

The maintained runtime is the Python package under `ralph-workflow/`.
The authoritative `make verify` entry point lives in
`ralph-workflow/Makefile` and delegates to `uv run python -m
ralph.verify`, which owns the 60-second combined test budget and the
per-step timeouts (see `ralph/verify.py:_VERIFY_STEPS` /
`_TOTAL_TEST_BUDGET_SECONDS`).

## Default requirements

* A single authoritative verification entry point MUST exist (`make
  verify` invoked from `ralph-workflow/`). Its declared gate list MUST
  match the `_VERIFY_STEPS` tuple in `ralph/verify.py` exactly; a
  verify command that does not exercise every tracked gate is
  non-compliant.
* Gates MUST include, as applicable to the project: Sphinx docs build,
  ruff lint, mypy strict, the project test suite, and 18 policy /
  lifecycle / drift audits (see Verification below).
* A gate documented here but not actually runnable is non-compliant.
  Documented impossibility MUST be reported as an active blocker.
* Verification MUST pass in full, with NO exemption for a failure the
  current change did not cause. "It was already broken on `main`",
  "that failure is unrelated to my change", and "that gate is not run
  by `make test`" are NOT acceptable outcomes: a red gate is a red
  gate, and whoever next observes it owns fixing it. Preventing
  regressions outranks completing the task in hand — if the two
  conflict, fix the regression first and finish the task after.
* Do NOT spend effort establishing WHO caused a failure. Stashing your
  changes, bisecting, or re-running against a clean tree to prove a
  failure is "pre-existing" is almost NEVER useful work: the answer
  does not change what you must do next, which is fix it. Provenance is
  worth investigating ONLY when it is genuinely diagnostic — when the
  triggering change tells you what the bug IS — never to decide whether
  the failure is yours to own. It is always yours to own.
* Every gate MUST be wired into the authoritative entry point. A check
  that exists only in a suite the default gate excludes (an opt-in
  marker, a manual script) WILL rot unnoticed and is non-compliant:
  either wire it into `make verify` or delete it. `audit_repo_structure`
  exists because its rules previously lived only in a `subprocess_e2e`
  test that `make verify` never ran, and they silently decayed.
* Verification MUST complete within a bounded, gate-enforced time budget
  (see `verification_time_budget` below). The generic sizing guide is
  ~1 second per 1k LOC with a **HARD CAP of 2 minutes** regardless of
  size; past ~120k LOC the cap, not the per-LOC rate, is the binding
  constraint. The budget may shrink freely but may only GROW as a
  deliberate, reviewed change — a suite already well under budget MUST
  NOT relax up toward the guide.
* A slow gate is a DEFECT, not a cost of doing business. Verification
  time that grows superlinearly, or a step that hangs, is a HARD
  indicator of a real problem — most often architectural: production
  code that cannot be exercised without real I/O, real subprocesses,
  real sleeps, or real network, which is the signature of tests coupled
  to internals instead of driving the system as a black box through its
  seams. Diagnose the coupling and fix the design. NEVER raise a budget
  to make a slow gate fit.
* Bypass detection (lint/typecheck/audit bypasses) MUST be enforced
  when the selected tools permit such checks. See "Bypass detection"
  below.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents
rely on them when enforcing this policy and MUST keep them current as
the project evolves.

RALPH-FACT: authoritative_verify_command: `make -C ralph-workflow verify` (delegates to `uv run python -m ralph.verify`, which owns `ralph/verify.py:_VERIFY_STEPS`). The target prerequisite is `make docs`, wired in the Makefile so `make verify` always runs the Sphinx HTML build first under `-W --keep-going` (any Sphinx warning fails the gate).
RALPH-FACT: gate_prerequisites: developer-side `make -C ralph-workflow dev` (`uv sync --extra dev`, syncs the editable install + dev extras). The CI prerequisite is the smaller `pip install --upgrade uv` declared in `.woodpecker.yml`, which provides `uv` for the `uv run` / `uv sync` invocations made from inside `make verify`; `.woodpecker.yml` does NOT call `make dev` and never has — it installs `uv` and then runs `cd ralph-workflow && make verify` directly. The `make verify` target itself resolves the Python toolchain via `uv run` against `uv.lock`, so the locked test environment is reproducible without an explicit `make dev` step on the CI runner.
RALPH-FACT: gate_order: [1] `make docs` (Sphinx HTML build with `-W --keep-going` via `uv run --extra docs sphinx-build`); then the 21 `_VERIFY_STEPS`: [2] ruff (`uv run ruff check ralph/ tests/`); [3] mypy (`uv run python -m mypy ralph/`); [4] `make test` (the budget-tracked pytest step, capped at 60 s combined via `_TOTAL_TEST_BUDGET_SECONDS`); [5-22] the 18 non-test audits: `ralph.testing.audit_lint_bypass`, `audit_typecheck_bypass`, `audit_test_policy`, `audit_mcp_timeout`, `audit_di_seam`, `audit_activity_aware_watchdog`, `audit_watchdog_drift`, `audit_parallelization_dormant`, `audit_artifact_submission_canonical_path`, `audit_agent_registry_sync`, `audit_agent_module_state`, `audit_agent_internal_paths`, `audit_resource_lifecycle`, `audit_skill_auto_commit`, `audit_public_docstrings`, `audit_terminal_escape_containment`, `audit_repo_structure`, plus the social-proof gate (`python3 ../scripts/verify_social_proof.py`). Each step has a 30 s per-step timeout (`_VERIFY_STEP_TIMEOUT_SECONDS`); only `make test` counts against the combined budget.
RALPH-FACT: repo_structure_audit: `ralph.testing.audit_repo_structure` (step 21, allowlists in `ralph/testing/_repo_structure_allowlists.py`) enforces four structural rules: max 1000 lines per file; at most one public top-level class per module; no private (`_`-prefixed) `ralph` imports in tests; no unallowlisted lint/typecheck suppression comments. `tests/integration/test_policy_file_rules.py` asserts on the same `collect_violations()` result so there is one source of truth. It is wired into `_VERIFY_STEPS` precisely because it previously lived only in a `subprocess_e2e` test that `make verify` never ran, and the policy rotted undetected.
RALPH-FACT: bypass_detection_lint_audit: `ralph.testing.audit_lint_bypass` walks the project tree to detect `per-file-ignores`, `extend-per-file-ignores`, blanket `# noqa` without a specific ruff error code, and weakens to the documented allowlist. Wired into `make verify` as the audit_lint_bypass step.
RALPH-FACT: bypass_detection_typecheck_audit: `ralph.testing.audit_typecheck_bypass` walks the project tree to detect `ignore_missing_imports`, `follow_imports = silent`, `ignore_errors`, `disable_error_code`, blanket `# type: ignore` without a specific mypy error code, `# type: ignore` inside test files, and `disallow_untyped_defs = false`. The acceptance bar is documented in `ralph-workflow/docs/agents/type-ignore-policy.md`; violations fail verify.
RALPH-FACT: ci_integration_command: `.woodpecker.yml` runs `cd ralph-workflow && make verify` on every push and pull_request event on Codeberg and is the only CI surface that runs the authoritative verification gate. `.github/workflows/cla.yml` runs only `cd ralph-workflow && python -m ralph.contrib.cla` (the CLA checkbox check) on pull_request events; it does NOT run `make verify`. `.github/workflows/publish-python-package.yml` is a release-only workflow triggered on `ralph-workflow-v*` tags and does NOT run `make verify`. The social-proof gate under `python3 ../scripts/verify_social_proof.py` is part of the same verify suite run by Woodpecker. The opt-in subprocess E2E suite (`make test-subprocess-e2e`) and live AGY suite (`make test-live-agy`) are NOT in `make verify` and have their own budgets.
RALPH-FACT: required_verification_profiles: four named profiles are declared so a caller picks the right surface for the work at hand. (1) `default` profile = `make -C ralph-workflow verify` (the 21-step `_VERIFY_STEPS` chain in ralph/verify.py, including ruff + mypy + make test + 17 audits + social-proof, the 60 s combined budget tracker, the docs prerequisite). (2) `pre-commit` profile = `make -C ralph-workflow pre-commit` (a one-shot ruff + format-check + dead-code sweep a developer runs before push; not a CI gate). (3) `subprocess-e2e` profile = `make -C ralph-workflow test-subprocess-e2e` (the subprocess-reality suite, excluded from the default 60 s budget via the `subprocess_e2e` marker, run on demand before release; per-suite timeout lives in ralph-workflow/Makefile as PYTEST_SUITE_TIMEOUT_SECONDS). (4) `live-agy` profile = `make -C ralph-workflow test-live-agy` (a network-backed AGY lifecycle test, excluded from the default budget via the `live_agy` marker and sized via LIVE_AGY_SUITE_TIMEOUT_SECONDS). A profile is selected by its Make target; a missing profile is a build-time blocker, not a runtime fallback.
RALPH-FACT: verification_time_budget: 60 seconds combined wall-clock for ALL test suites running sequentially under `make verify`; pinned to 60.0 in `ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS` by an import-time epsilon check `abs(_TOTAL_TEST_BUDGET_SECONDS - 60.0) < 1e-9` and a positive-runtime guard `_TOTAL_TEST_BUDGET_SECONDS > 0`. Per-step caps are secondary and independent: `_VERIFY_STEP_TIMEOUT_SECONDS = 30.0` for each non-test verification step, with `_MIN_VERIFY_STEP_TIMEOUT_SECONDS = 5.0` as a non-trivial floor. Integration tests under `tests/integration/` have a hard 1.0-second per-test SIGALRM cap (`_INTEGRATION_PER_TEST_TIMEOUT_SECONDS = 1.0`). The budget may only GROW as a deliberate, reviewed change; suites well under 60 s MUST NOT relax up toward the generic sizing guide (the 60-second cap is the binding constraint for any project past ~120k LOC).
RALPH-FACT: verification_time_enforcement_mechanism: cumulative `time.monotonic()` tracker in `ralph/verify.py:run_verify()` that sums elapsed wall-clock across every step whose index is in `_BUDGET_TRACKED_STEPS` (currently `frozenset({2})` = the `make test` step). Before each tracked step, the remaining budget is computed and passed as `min(step_timeout, remaining_budget)`; once cumulative time exceeds 60 s the runner returns `TIMEOUT_EXIT_CODE` and emits the high-visibility failure banner from `format_verify_failure_banner()`. Import-time `if`/`raise RuntimeError` invariants (immune to `python -O`) pin: `_TOTAL_TEST_BUDGET_SECONDS > 0`, `_BUDGET_TRACKED_STEPS` indices valid into `_VERIFY_STEPS`, every budget-tracked step has a positive timeout, `_KNOWN_TEST_STEP_LABELS` and `_BUDGET_TRACKED_STEPS` are non-empty, `'make test'` is in `_KNOWN_TEST_STEP_LABELS`, every label in `_KNOWN_TEST_STEP_LABELS` is tracked and every tracked step is labelled, `_VERIFY_STEP_TIMEOUT_SECONDS > 0` and `>= 5.0`, and the audit_mcp_timeout + audit_resource_lifecycle steps are present. A budget-tracked test step MUST be reachable from `make verify`; splitting tests, renaming targets, raising per-suite caps, or setting `RALPH_PYTEST_*` env vars does NOT increase the combined budget. A timeout is a test-design defect — diagnose the production coupling and fix it; never raise the budget to make a slow gate fit.

## AI execution instructions

To follow this policy, an agent making any change MUST:

* ENSURE every gate listed here is actually runnable in the
  environment. Document any gate that cannot run and the reason.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy (facts, commands, requirements) in the same
  workflow that changes the authoritative entry point, gate order, or
  bypass-detection audit.
* FIX every failure the gate reports, including failures the agent did
  not introduce and failures in code the agent never touched. On
  encountering a pre-existing red gate, the agent MUST repair it (or,
  when the repair is genuinely out of scope, stop and report it as an
  active blocker) — it MUST NOT proceed, and MUST NOT report its own
  work as verified while any gate is red.

An agent MUST NOT:

* Add a "verification" command that does not exercise every gate.
* Weaken a gate to obtain a passing result.
* Hide bypasses via file-level disables or blanket silencers.
* Dismiss, defer, or excuse a failing gate on the grounds that the
  failure is pre-existing, unrelated to the current change, or
  someone else's regression.
* Stash, bisect, or re-run against a clean tree merely to establish that
  a failure is pre-existing. That is wasted work: the verdict is the
  same either way — fix it.
* Claim verification passed on the strength of a subset of gates.

## Verification

Run every gate below before claiming a change complies with this policy.

RALPH-COMMAND: make -C ralph-workflow verify

The expected successful result is exit 0 from the authoritative entry
point. On failure, report the failing gate and the failure category
(docs warning, lint error, mypy error, test failure, audit finding).
A failing verify step emits a high-visibility banner that cites this
policy and `AGENTS.md`.

## Bypass detection

Lint and typecheck bypass detection MUST be enforced as part of the
authoritative verification gate. The bypass-detection rules enforced by
the wired audits:

* Newly weakened global configuration (`per-file-ignores`,
  `extend-per-file-ignores`, `ignore_missing_imports`, `follow_imports
  = silent`, `ignore_errors`, `disable_error_code`,
  `disallow_untyped_defs = false`, blank `warn_unused_ignores`, etc.)
  is detected and reported by `audit_lint_bypass` /
  `audit_typecheck_bypass`.
* Blanket or unexplained inline suppressions (`# noqa` without a
  specific ruff code outside the allowlist; `# type: ignore` without
  a mypy error code; `# type: ignore` inside test files) are detected
  and reported.
* The MCP-bypass audit (`audit_mcp_timeout`) detects unbounded
  blocking calls (`subprocess.run` / `.communicate` / `.wait`,
  `httpx.*`, `requests.*`, `urlopen`, `socket.create_connection`)
  without `timeout=` under `ralph/mcp/`, `ralph/git/`, `ralph/process/`,
  `ralph/executor/`, `ralph/agents/`, and `ralph/pro_support/`. The
  ONLY bypass is an inline `# mcp-timeout-ok: <reason>` marker for a
  genuinely unbounded-by-design call.
* The resource-lifecycle audit (`audit_resource_lifecycle`) detects
  unbounded mutable accumulators (list / dict / set / deque WITHOUT
  `maxlen=` assigned module-level or to `self.X` in `__init__`). The
  ONLY bypass is an inline `# bounded-accumulator-ok: <reason>`
  marker naming the cap or drain.
* The drift audit (`verify-drift` Makefile target, followed by
  `scripts/wt028-drift-check.sh`) catches new uses of the canonical
  Pro-contract surface (hardcoded PROMPT.md literals outside the
  resolver, new uses of `.ralph/run.json` outside the marker reader,
  rogue uses of RALPH_* env vars beyond the canonical three).

The bypass-detection gate is a single command run before the final
test budget check; it is one of the 17 non-budget-tracked verify
steps and is wired into `make verify` directly. The lint/typecheck
audits (`audit_lint_bypass`, `audit_typecheck_bypass`,
`audit_mcp_timeout`, `audit_resource_lifecycle`) are separate
`_VERIFY_STEPS` entries, NOT children of the drift target; each is
invoked by `uv run python -m ralph.verify` directly from inside the
Python verify chain, and the drift target runs alongside `make docs`
as a Make prerequisite of `make verify`.

RALPH-COMMAND: make -C ralph-workflow verify-drift

The expected successful result is exit 0 from `verify-drift`. The
target runs the four canonical Pro-contract drift greps (no
hardcoded `PROMPT.md` references outside the resolver; no
`.ralph/run.json` references outside the marker reader; no
`time.sleep` in `ralph/pro_support/`; no rogue `RALPH_*` env vars
beyond the canonical three) and then invokes
`scripts/wt028-drift-check.sh` to catch the consolidated single-mode
invariant drift. It does NOT run the lint/typecheck/MCP/resource-lifecycle
audits; those audits are separate `_VERIFY_STEPS` entries invoked
by `ralph.verify`. On failure, report the affected file, line, and
drift category. Approved documented exceptions MUST be listed under
"Exceptions" below.

## Exceptions

A documented bypass (e.g. a generated file with a `// @ts-nocheck`
header; a vendored dependency stub under `ralph-workflow/stubs/`) MUST
have a rationale, scope, owner, and removal or review date. The
allowlists in `ralph/testing/audit_lint_bypass.py` /
`audit_typecheck_bypass.py` / `audit_mcp_timeout.py` /
`audit_resource_lifecycle.py` are the canonical home for those
allowlists, and they MUST be updated in the same commit that adds the
exception. Undocumented bypasses are non-compliant and fail the
corresponding audit.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new gate is added or an existing gate is removed.
* The authoritative entry point changes.
* The bypass-detection audit changes.

## Research basis

* publisher: Google Engineering Practices
  title: "Code Review: Speed of Code Reviews"
  http: https://google.github.io/eng-practices/review/reviewer/speed.html
  review date: 2026-07-12

* publisher: Google SRE Book
  title: "Monitoring Distributed Systems"
  http: https://sre.google/sre-book/monitoring-distributed-systems/
  review date: 2026-07-12

* publisher: Martin Fowler
  title: "Continuous Integration"
  http: https://martinfowler.com/articles/continuousIntegration.html
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

* Policy id: `<!-- ralph-policy-id: verification-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
