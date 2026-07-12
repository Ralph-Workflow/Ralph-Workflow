<!-- ralph-policy-schema: v1 -->
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
* Bypass detection (lint/typecheck/audit bypasses) MUST be enforced
  when the selected tools permit such checks. See "Bypass detection"
  below.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents
rely on them when enforcing this policy and MUST keep them current as
the project evolves.

RALPH-FACT: authoritative_verify_command: `make -C ralph-workflow verify` (delegates to `uv run python -m ralph.verify`, which owns `ralph/verify.py:_VERIFY_STEPS`). The target prerequisite is `make docs`, wired in the Makefile so `make verify` always runs the Sphinx HTML build first under `-W --keep-going` (any Sphinx warning fails the gate).
RALPH-FACT: gate_prerequisites: `make -C ralph-workflow dev` (uv sync of the editable install + dev extras). The prerequisite gates the test budget: tests run only against a synced environment; CI runs `make dev` before `make verify`.
RALPH-FACT: gate_order: [1] `make docs` (Sphinx HTML build with `-W --keep-going` via `uv run --extra docs sphinx-build`); [2] ruff (`uv run ruff check ralph/ tests/`); [3] mypy (`uv run python -m mypy ralph/`); [4] `make test` (the budget-tracked pytest step, capped at 60 s combined via `_TOTAL_TEST_BUDGET_SECONDS`); [5-21] the 17 non-test audits: `ralph.testing.audit_lint_bypass`, `audit_typecheck_bypass`, `audit_test_policy`, `audit_mcp_timeout`, `audit_di_seam`, `audit_activity_aware_watchdog`, `audit_watchdog_drift`, `audit_parallelization_dormant`, `audit_artifact_submission_canonical_path`, `audit_agent_registry_sync`, `audit_agent_module_state`, `audit_agent_internal_paths`, `audit_resource_lifecycle`, `audit_skill_auto_commit`, `audit_public_docstrings`, plus the social-proof gate (`python3 ../scripts/verify_social_proof.py`). Each step has a 30 s per-step timeout (`_VERIFY_STEP_TIMEOUT_SECONDS`); only `make test` counts against the combined budget.
RALPH-FACT: bypass_detection_lint_audit: `ralph.testing.audit_lint_bypass` walks the project tree to detect `per-file-ignores`, `extend-per-file-ignores`, blanket `# noqa` without a specific ruff error code, and weakens to the documented allowlist. Wired into `make verify` as the audit_lint_bypass step.
RALPH-FACT: bypass_detection_typecheck_audit: `ralph.testing.audit_typecheck_bypass` walks the project tree to detect `ignore_missing_imports`, `follow_imports = silent`, `ignore_errors`, `disable_error_code`, blanket `# type: ignore` without a specific mypy error code, `# type: ignore` inside test files, and `disallow_untyped_defs = false`. The acceptance bar is documented in `ralph-workflow/docs/agents/type-ignore-policy.md`; violations fail verify.
RALPH-FACT: ci_integration_command: both Codeberg (Woodpecker) and GitHub Actions run `make verify` on every PR; the social-proof gate under `python3 ../scripts/verify_social_proof.py` is part of the same suite. The opt-in subprocess E2E suite (`make test-subprocess-e2e`) and live AGY suite (`make test-live-agy`) are NOT in `make verify` and have their own budgets.

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

An agent MUST NOT:

* Add a "verification" command that does not exercise every gate.
* Weaken a gate to obtain a passing result.
* Hide bypasses via file-level disables or blanket silencers.

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
steps and is wired into `make verify` directly.

RALPH-COMMAND: make -C ralph-workflow verify-drift

The expected successful result is exit 0 from `verify-drift` and from
the wired lint/typecheck/MCP/resource-lifecycle audits. On failure,
report the affected file, line, and bypass category. Approved
documented exceptions MUST be listed under "Exceptions" below.

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
