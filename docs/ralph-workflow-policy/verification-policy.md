<!-- ralph-policy-schema: v3 -->
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

A run is judged on **time to a correct, verified change**; speed never permits partial proof.

Prevent an extra pass first; within a pass, the cost of proving the change is the largest term; where they conflict, avoid the extra pass.

The four commitments are: **fast path first**, **a slow gate is a defect**, **do not leave the loop slower**, and **checks answer consistently**. Non-compliance is a defect: fix it within the requested run when that fits its budget, or raise it to an owner who can act. A change is never done on a partial check.

* A single authoritative verification entry point MUST exist (`make
  verify` invoked from `ralph-workflow/`). Its declared gate list MUST
  match the `_VERIFY_STEPS` tuple in `ralph/verify.py` exactly; a
  verify command that does not exercise every tracked gate is
  non-compliant.
* Gates MUST include, as applicable to the project: Sphinx docs build,
  ruff lint, mypy strict, the project test suite, and every policy /
  lifecycle / drift audit declared in `ralph/verify.py:_VERIFY_STEPS`
  (see Verification below).
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
* Every check MUST sit in a declared lane (see "Gate lanes" below). A
  check that exists only in a suite the default gate excludes, with no
  declared owner and no declared trigger (an undeclared opt-in marker, a
  manual script), WILL rot unnoticed and is non-compliant: wire it into
  `make verify`, declare it as a named profile, or delete it.
  `audit_repo_structure` exists because its rules previously lived only in
  a `subprocess_e2e` test that `make verify` never ran, and they silently
  decayed.
* Verification MUST complete within a bounded, gate-enforced time budget
  (see `verification_time_budget` below). The generic sizing guide is
  ~1 second per 1k LOC with a **HARD CAP of 2 minutes** regardless of
  size; past ~120k LOC the cap, not the per-LOC rate, is the binding
  constraint. The budget may shrink freely but may only GROW as a
  deliberate, reviewed change — a suite already well under budget MUST
  NOT relax up toward the guide.
* Fast-path and full-gate costs MUST be measured from invocation to answer, including startup. The fast path MUST complete within 10 seconds; the full gate has the 60-second combined-test budget below. Reducing recurring gate, fast-path, or setup cost is in scope when it fits the run budget without displacing the request; otherwise record it where an owner can act. Added verification cost and any budget breach require an explicit decision and actionable escalation.
* A check that answers inconsistently is a defect: fix or raise it, never normalize reruns. Failure output MUST identify what broke so diagnosis is bounded.
* Orientation that every run would otherwise rebuild MUST be durable, cheap to read, and updated by the run that learns it.
* Phase order, wait permissions, and phase-granted capabilities belong to workflow machinery outside this policy; the runtime owns their settings and reports them alongside phase time where available.
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

## Gate lanes

Every check this project owns sits in exactly one lane. The lane system
keeps `make verify` fast and trusted without pushing real checks into an
unowned opt-in suite where they decay silently — the failure mode that
produced `audit_repo_structure`.

1. DEFAULT GATE — `make -C ralph-workflow verify`, the authoritative
   pre-merge entry point: the `docs` and `verify-drift` prerequisites
   followed by the full `ralph/verify.py:_VERIFY_STEPS` chain, under the
   60 s combined test budget. This is the lane for everything that can
   run deterministically from a clean clone with in-process fakes, and a
   check that can meet those constraints MUST NOT be demoted to a profile
   because it is inconvenient.
2. NAMED PROFILE — a check that is genuinely valuable and repeatable but
   cannot meet the default gate's constraints: real subprocesses and
   sockets, a network-backed agent lifecycle, or a developer-only sweep.
   A profile is legitimate ONLY when its command, its owner, and its
   trigger or schedule are declared in `required_verification_profiles`
   below, and only when it fails hard on the run. The declared set is
   `default`, `pre-commit`, `subprocess-e2e`, and `live-agy`; a profile
   selected by its Make target and recorded here is wired in, whereas an
   opt-in marker nobody declared and nobody owns is not.
3. DELETED — the correct destination for a check with no owner, no
   trigger, and no lane. Deleting a decayed check is honest; leaving it
   in an unobserved corner is not, because a permanently unread check
   reports a safety it never verified.

A check MUST NOT be moved from lane 1 to lane 2 to escape a red result or
a budget breach. Demotion is a design decision with an owner, recorded
with its reason; it is never a way to make the gate green today.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents
rely on them when enforcing this policy and MUST keep them current as
the project evolves.

RALPH-FACT: authoritative_verify_command: `make -C ralph-workflow verify` (runs the fail-closed `formula-check`, `verify-drift`, and `docs` prerequisites, then delegates to `uv run python -m ralph.verify`, which owns `ralph/verify.py:_VERIFY_STEPS`). The docs prerequisite runs the Sphinx HTML build under `-W --keep-going` (any Sphinx warning fails the gate).
RALPH-FACT: gate_prerequisites: Ruby is a required system prerequisite for the Homebrew-formula gate; `make formula-check` fails non-zero with a policy citation rather than skipping when it is absent. GitHub CI provisions Ruby 3.3.6 through `ruby/setup-ruby@v1`; the maintained Codeberg runner installs Ruby before invoking the gate. Developer-side `make -C ralph-workflow dev` delegates to `python -m ralph.install`, which copies the checkout to `~/.local/share/ralph-workflow-dev/current`, runs `uv sync --extra dev` in that snapshot, and writes the `rdev` launcher. The CI prerequisite is the smaller `pip install --upgrade uv` declared in `.github/workflows/verify.yml` and `.woodpecker.yml`; neither workflow calls `make dev` — each installs `uv` and then runs `cd ralph-workflow && make verify` directly. The `make verify` target resolves the current checkout's Python toolchain via `uv run` against `uv.lock`, so the locked test environment is reproducible without an explicit `make dev` step on the CI runner.
RALPH-FACT: gate_order: `make formula-check`, `make docs`, and `make verify-drift` are Make prerequisites. Formula validation runs `ruby -c Formula/ralph-workflow.rb` and must resolve successfully before the Python verify chain starts. Ralph then executes `ralph/verify.py:_VERIFY_STEPS` in tuple order: ruff (`uv run ruff check ralph/ tests/`), mypy (`uv run python -m mypy ralph/`), the budget-tracked `make test` profile, the remaining audits and social-proof step, then the budget-tracked `make test-multimodal-smoke` and `make test-visual-smoke` steps. The exact audit inventory and order live in `_VERIFY_STEPS`, so adding an audit there updates the authoritative topology without a duplicated numeric count. `make test` uses `(not subprocess_e2e and not smoke) or required_auto_integrate_e2e`: files in `ralph/test_suites.py:REQUIRED_AUTO_INTEGRATE_E2E_FILES` receive the marker in `tests/conftest.py` and remain in the default profile even when they also carry `subprocess_e2e`. Each non-test step has the `_VERIFY_STEP_TIMEOUT_SECONDS` cap; every test-running step is charged to `_TOTAL_TEST_BUDGET_SECONDS`.
RALPH-FACT: repo_structure_audit: `ralph.testing.audit_repo_structure` (allowlists in `ralph/testing/_repo_structure_allowlists.py`) enforces four structural rules: max 1000 lines per file; at most one public top-level class per module; no private (`_`-prefixed) `ralph` imports in tests; no unallowlisted lint/typecheck suppression comments. `tests/integration/test_policy_file_rules.py` asserts on the same `collect_violations()` result so there is one source of truth. It is wired into `_VERIFY_STEPS` precisely because it previously lived only in a `subprocess_e2e` test that `make verify` never ran, and the policy rotted undetected.
RALPH-FACT: bypass_detection_lint_audit: `ralph.testing.audit_lint_bypass` walks the project tree to detect `per-file-ignores`, `extend-per-file-ignores`, blanket `# noqa` without a specific ruff error code, and weakens to the documented allowlist. Wired into `make verify` as the audit_lint_bypass step.
RALPH-FACT: bypass_detection_typecheck_audit: `ralph.testing.audit_typecheck_bypass` walks the project tree to detect `ignore_missing_imports`, `follow_imports = silent`, `ignore_errors`, `disable_error_code`, blanket `# type: ignore` without a specific mypy error code, `# type: ignore` inside test files, and `disallow_untyped_defs = false`. The acceptance bar is documented in `ralph-workflow/docs/agents/type-ignore-policy.md`; violations fail verify.
RALPH-FACT: ci_integration_command: `.github/workflows/verify.yml` runs `cd ralph-workflow && make verify` on every push and pull_request event on the GitHub primary repository. `.woodpecker.yml` continues the same gate on the Codeberg mirror while it is maintained. `.github/workflows/cla.yml` separately runs the CLA checkbox check on pull_request events, while `.github/workflows/publish-python-package.yml` is a release-only workflow triggered on `ralph-workflow-v*` tags. The social-proof gate under `python3 ../scripts/verify_social_proof.py` is part of the same verify suite. The opt-in subprocess E2E suite (`make test-subprocess-e2e`) and live AGY suite (`make test-live-agy`) are NOT in `make verify` and have their own budgets.
RALPH-FACT: required_verification_profiles: four named profiles are declared so a caller picks the right surface for the work at hand. (1) `default` profile = `make -C ralph-workflow verify` (formula-check, docs, and drift prerequisites followed by the complete `_VERIFY_STEPS` chain: ruff, mypy, the combined `make test` selection, all declared audits, social proof, and the deterministic multimodal and visual smoke steps under the 60 s combined test budget). The combined selection includes the registered real-git auto-integration files through `required_auto_integrate_e2e`; there is no separate auto-integration step in `_VERIFY_STEPS`. (2) `pre-commit` profile = `make -C ralph-workflow pre-commit` (runs the package `.pre-commit-config.yaml` hooks — whitespace/EOF/YAML/large-file/TOML checks, ruff, ruff-format, and mypy — across all files; not a CI gate and not the dead-code sweep). (3) `subprocess-e2e` profile = `make -C ralph-workflow test-subprocess-e2e` (the broader subprocess-reality suite; subprocess tests outside the required auto-integration registry are excluded from the default profile, and the opt-in suite timeout lives in ralph-workflow/Makefile as PYTEST_SUITE_TIMEOUT_SECONDS). (4) `live-agy` profile = `make -C ralph-workflow test-live-agy` (a network-backed AGY lifecycle test, excluded from the default budget via the `live_agy` marker and sized via LIVE_AGY_SUITE_TIMEOUT_SECONDS). A profile is selected by its Make target; a missing profile is a build-time blocker, not a runtime fallback.
RALPH-FACT: verification_time_budget: fast path invocation-to-answer cap 10 seconds; full gate is 60 seconds combined wall-clock for ALL test suites running sequentially under `make verify`, pinned to 60.0 in `ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS` by an import-time epsilon check `abs(_TOTAL_TEST_BUDGET_SECONDS - 60.0) < 1e-9` and a positive-runtime guard `_TOTAL_TEST_BUDGET_SECONDS > 0`. Per-step caps are secondary and independent: `_VERIFY_STEP_TIMEOUT_SECONDS = 30.0` for each non-test verification step, with `_MIN_VERIFY_STEP_TIMEOUT_SECONDS = 5.0` as a non-trivial floor. Integration tests under `tests/integration/` have a hard 1.0-second per-test SIGALRM cap (`_INTEGRATION_PER_TEST_TIMEOUT_SECONDS = 1.0`). The budget may only GROW as a deliberate, reviewed change; suites well under 60 s MUST NOT relax up toward the generic sizing guide (the 60-second cap is the binding constraint for any project past ~120k LOC).
RALPH-FACT: verification_time_enforcement_mechanism: cumulative `time.monotonic()` tracker in `ralph/verify.py:run_verify()` that sums elapsed wall-clock across every step selected by `_BUDGET_TRACKED_STEPS`: `make test`, `make test-multimodal-smoke`, and `make test-visual-smoke`. Before each tracked step, the remaining budget is computed and passed as `min(step_timeout, remaining_budget)`; once cumulative time exceeds 60 s the runner returns `TIMEOUT_EXIT_CODE` and emits the high-visibility failure banner from `format_verify_failure_banner()`. Import-time `if`/`raise RuntimeError` invariants (immune to `python -O`) pin: `_TOTAL_TEST_BUDGET_SECONDS > 0`, `_BUDGET_TRACKED_STEPS` indices valid into `_VERIFY_STEPS`, every budget-tracked step has a positive timeout, `_KNOWN_TEST_STEP_LABELS` and `_BUDGET_TRACKED_STEPS` are non-empty, `'make test'` is in `_KNOWN_TEST_STEP_LABELS`, every label in `_KNOWN_TEST_STEP_LABELS` is tracked and every tracked step is labelled, `_VERIFY_STEP_TIMEOUT_SECONDS > 0` and `>= 5.0`, and the audit_mcp_timeout + audit_resource_lifecycle steps are present. A budget-tracked test step MUST be reachable from `make verify`; splitting tests, renaming targets, raising per-suite caps, or setting `RALPH_PYTEST_*` env vars does NOT increase the combined budget. A timeout is a test-design defect — diagnose the production coupling and fix it; never raise the budget to make a slow gate fit.
RALPH-FACT: fast_path_command: RALPH-PENDING (assumed 2026-08-09); review trigger: once a <=10-second composite fast-path Make target is implemented, measured, and declared in the verification profile inventory
RALPH-FACT: fast_path_selection_mechanism: RALPH-PENDING (assumed 2026-08-09); review trigger: once a documented rule maps change scope to the measured fast-path command; current Make targets select named profiles but not a fast path
RALPH-FACT: gate_cache_mechanism_and_key: none — maintained pytest shards disable the pytest cache provider (`-p no:cacheprovider` in `ralph/test_suites.py`), and `.github/workflows/verify.yml` declares no CI cache action or cache key
RALPH-FACT: gate_duration_report_location: successful `make -C ralph-workflow verify` writes `Cumulative test elapsed: <seconds>s / budget: 60.0s` to standard output from `ralph.verify.run_verify()`; CI retains that console log but declares no durable timing artifact, summary, or per-step report
RALPH-FACT: gate_lane_review_cadence_and_owner: RALPH-PENDING (assumed 2026-08-09); review trigger: once named lane owners and a recurring review cadence are declared; existing policy review is event-driven when gate topology changes
RALPH-FACT: gate_parallelism_mechanism: `ralph.verify.run_verify()` executes `_VERIFY_STEPS` serially; its `make test` step delegates to `ralph.test_suites`, which concurrently runs CPU-capped file shards selected by `PYTEST_WORKERS` and optionally bounded xdist workers per shard

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
  `ralph/executor/`, `ralph/agents/`, `ralph/pro_support/`, `ralph/api/`,
  `ralph/update_check/`, and `ralph/contrib/`. The ONLY bypass is an inline
  `# mcp-timeout-ok: <reason>` marker for a genuinely unbounded-by-design
  call.
* The resource-lifecycle audit (`audit_resource_lifecycle`) detects
  unbounded mutable accumulators (list / dict / set / deque WITHOUT
  `maxlen=` assigned module-level or to `self.X` in `__init__`) across its
  default production roots: `mcp`, `agents`, `executor`, `process`,
  `pipeline`, `runtime`, `pro_support`, `recovery`, `display`, `prompts`,
  `diagnostics`, `api`, `update_check`, `contrib`, `git`, `cli`, `telemetry`,
  `policy`, `language_detector`, `workspace`, `phases`, `guidelines`,
  `checkpoint`, `config`, `exit_pause`, `files`, `platform`, `project_policy`,
  `skills`, and `interrupt`. The ONLY bypass is an inline
  `# bounded-accumulator-ok: <reason>` marker naming the cap or drain.
* The drift audit (`verify-drift` Makefile target, followed by
  `scripts/wt028-drift-check.sh`) catches new uses of the canonical
  Pro-contract surface (hardcoded PROMPT.md literals outside the
  resolver, new uses of `.ralph/run.json` outside the marker reader,
  rogue uses of RALPH_* env vars beyond the canonical three).

Bypass detection is implemented by non-budget-tracked entries in
`_VERIFY_STEPS` and is wired into `make verify` directly. The
lint/typecheck audits (`audit_lint_bypass`, `audit_typecheck_bypass`,
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
* Schema version: `<!-- ralph-policy-schema: v3 -->`
