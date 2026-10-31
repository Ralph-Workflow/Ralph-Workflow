<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: verification-policy.md -->

# Verification Policy

## Purpose and scope

This policy defines the authoritative verification entry point for the
project. It enumerates every gate that must pass before code can be
merged or released, the exact commands, the prerequisites, and the
bypass-detection rules.

## Default requirements

* A single authoritative verification entry point MUST exist (Makefile
  target, CI workflow, or `make verify` equivalent) that runs every
  declared gate in the documented order.
* Gates MUST include, as applicable to the project: tests, type
  checking, linting, formatting checks, policy enforcement scripts, and
  any other mandatory project gate.
* A gate documented here but not actually runnable is non-compliant.
  Documented impossibility MUST be reported as an active blocker.
* Bypass detection (lint/typecheck bypasses) MUST be enforced when the
  selected tools permit such checks. See "Bypass detection" below.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

RALPH-FACT: authoritative_verify_command: `make verify`, run from `ralph-workflow/`. The Makefile target is `verify: formula-check verify-drift docs` followed by `uv run python -m ralph.verify`, which executes the ordered step list in `ralph/verify.py:_VERIFY_STEPS` and returns the first non-zero exit code. The entry point is also the completion gate in CI: `.github/workflows/verify.yml` runs `cd ralph-workflow && make verify` on every push and pull_request event.

RALPH-FACT: gate_prerequisites: `make verify` runs three Make prerequisites before the Python verify chain: `make formula-check` (Homebrew formula syntax validation via `scripts/packaging_operations.sh`), `make verify-drift` (the bounded, fail-closed policy-drift script `scripts/verify_drift.sh`), and `make docs` (the Sphinx HTML build `uv run --extra docs sphinx-build -b html docs/sphinx docs/sphinx/_build/html -W --keep-going`, so any Sphinx warning fails the gate). `make docs` is a Make prerequisite and NOT a `_VERIFY_STEPS` entry, so it stays outside the combined test budget. The `docs` step requires the `docs` extra; the Python verify chain resolves its toolchain through `uv run --locked --project .` against `uv.lock`, and the CI runner installs `uv` with `pip install --upgrade uv` and provisions Ruby 3.3.6 for the formula gate.

RALPH-FACT: gate_order: the Make prerequisites run first — `make formula-check`, `make verify-drift`, `make docs` — and then `uv run python -m ralph.verify` runs `ralph/verify.py:_VERIFY_STEPS` in tuple order: `ruff check ralph/ tests/` → `python -m mypy ralph/` → the budget-tracked `make test` → `ruff format --check ralph/ tests/` → the lint-bypass audit → the typecheck-bypass audit → the test-policy audit → the MCP-timeout audit → the DI-seam audit → the activity-aware-watchdog audit → the watchdog-drift audit → the parallelization-dormant audit → the artifact-submission-canonical-path audit → the agent-registry-sync audit → the agent-module-state audit → the agent-internal-paths audit → the social-proof gate (`python3 ../scripts/verify_social_proof.py`) → the resource-lifecycle audit → the skill-auto-commit audit → the skill-mirror-freshness audit → the public-docstring audit → the terminal-escape-containment audit → the repo-structure audit → the fsevents-watch-consolidation audit → the log-sink-buffering audit → the filesystem write-consolidation audit → the filesystem read-consolidation audit → the filesystem polling/invocation-ownership audit → the regression-test-elimination audit → the template render-integrity audit → the fenced-artifact-example audit → the prompt single-sourcing audit → the cast-policy audit → the workspace-resource-inventory audit → the canonical-session-text audit → the kwargs-forwarding audit → the opts-key-drift audit → the budget-tracked aggregate `make test-verification-smoke` → `audit_mcp_agent_boundary`. The exact audit inventory and order live in `_VERIFY_STEPS`, so adding an audit there updates the authoritative topology without a duplicated list elsewhere. Each non-test step carries the `_VERIFY_STEP_TIMEOUT_SECONDS = 30.0` per-step cap; the two test-running steps (`make test` and `make test-verification-smoke`) are charged against the cumulative `_TOTAL_TEST_BUDGET_SECONDS = 60.0` tracker, which sums wall-clock across every budget-tracked step and judges it net of host contention, and against `_TOTAL_TEST_CPU_BUDGET_SECONDS = 700.0` CPU core-seconds of their own process trees; they are killed only at the `_TOTAL_TEST_HANG_CAP_SECONDS = 180.0` hang cap.

RALPH-FACT: bypass_detection_lint_audit: `ralph.testing.audit_lint_bypass` — wired into `make verify` as the `lint bypass audit (audit_lint_bypass)` step of `ralph/verify.py:_VERIFY_STEPS`. It walks the project tree and reports `per-file-ignores`, `extend-per-file-ignores`, blanket `# noqa` without a specific ruff error code, and any suppression comment outside the documented allowlist. The acceptance bar is documented in `ralph-workflow/docs/ralph-workflow-policy/linting-policy.md`; violations fail verify.

RALPH-FACT: bypass_detection_typecheck_audit: `ralph.testing.audit_typecheck_bypass` — wired into `make verify` as the `typecheck bypass audit (audit_typecheck_bypass)` step of `ralph/verify.py:_VERIFY_STEPS`. It walks the project tree and reports `ignore_missing_imports`, `follow_imports = silent`, `ignore_errors`, `disable_error_code`, blanket `# type: ignore` without a specific mypy error code, `# type: ignore` inside test files, and `disallow_untyped_defs = false`. The acceptance bar is documented in `ralph-workflow/docs/agents/type-ignore-policy.md`; violations fail verify.

RALPH-FACT: repo_structure_gate: `ralph.testing.audit_repo_structure` — wired into `make verify` as the `repo structure audit (audit_repo_structure)` step of `ralph/verify.py:_VERIFY_STEPS`. The audit enforces four repository-structure rules over `ralph/` and `tests/`: a file may not exceed 1000 lines, a module may define at most one public top-level class, a test may not import a private (`_`-prefixed) name from `ralph`, and no lint- or typecheck-suppression comment may appear outside the documented allowlist. The audit is a HARD gate: a violation fails `make verify`, so the tree must satisfy it on every run — the gate is not advisory and a violation is not an "unrelated failure". The rules previously lived ONLY in a `subprocess_e2e`-marked test that `make test` excludes, which let the policy decay silently while the gate stayed green; keeping the audit in `_VERIFY_STEPS` is the regression guard, so it must not be unwired or demoted to an opt-in suite.

RALPH-FACT: ci_integration_command: `.github/workflows/verify.yml` runs `cd ralph-workflow && make verify` on every `push` and `pull_request` event on `ubuntu-latest` with Python 3.12, after the CLA check (`python -m ralph.contrib.cla`) and `pip install --upgrade uv`. `.woodpecker.yml` runs the same `cd ralph-workflow && make verify` gate on the Codeberg mirror, adding `apt-get install ruby` for the formula prerequisite. `.github/workflows/cla.yml` is a separate pull_request-only CLA check, and `.github/workflows/publish-python-package.yml` is a release-only workflow triggered by `ralph-workflow-v*` tags. The opt-in `make test-subprocess-e2e` and `make test-live-agy` suites are NOT part of `make verify` and carry their own per-suite budgets.

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

RALPH-COMMAND: make verify

The expected successful result is exit 0 from the authoritative entry
point. On failure, report the failing gate and the failure category.

`make verify` must pass in full. A failure in any step is a blocker that
the next agent to observe it owns fixing; "it was already failing" and
"that gate is unrelated to my change" are not acceptable dispositions.

## Bypass detection

Lint and typecheck bypass detection MUST be enforced as part of the
authoritative verification gate. The bypass-detection rules:

* Newly weakened global configuration (per-file-ignores,
  ignore_missing_imports, follow_imports = silent, ignore_errors,
  disable_error_code, etc.) is detected and reported.
* Blanket or unexplained inline suppressions are detected and reported.
* Commands that claim to verify the project while omitting required
  paths are detected and reported.

The bypass-detection command is declared as a `RALPH-COMMAND:` under
this heading. The audit tooling is project-specific; the project's
existing audit scripts MUST be wired into the verification gate.

RALPH-COMMAND: uv run python -m ralph.testing.audit_lint_bypass

RALPH-COMMAND: uv run python -m ralph.testing.audit_typecheck_bypass

RALPH-COMMAND: uv run python -m ralph.testing.audit_repo_structure

The first two commands run inside `make verify` as dedicated `_VERIFY_STEPS`
entries, so a bypass fails the authoritative entry point. The third is the
repository-structure gate: it enforces the file-size, public-class,
private-import and suppression-comment rules over `ralph/` and `tests/`, and
a violation fails `make verify` exactly like a lint or typecheck bypass.

The expected successful result is exit 0 (no bypass detected). On
failure, report the affected file, line, and bypass category. Approved
documented exceptions MUST be listed under "Exceptions" below.

## Exceptions

A documented bypass (e.g. a generated file with a `// @ts-nocheck`
header) requires a documented rationale, scope, owner, and removal or
review date. Undocumented bypasses are non-compliant.

## Integration lifecycle verification: object-reachability pre-pass and typed projection

The integration lifecycle is governed by the typed authorities established in
[ADR-0007](../architecture/adr-0007-integration-source-of-truth.md) and
[ADR-0008](../architecture/adr-0008-integration-lifecycle-consolidation.md).

The live decision authority is the integration owner seam,
`ralph.pipeline.integration_owner`: `decide_integration` and
`decide_integration_verdict` are the only places an integration decision
is produced, and the durable attempt ledger is written through the owner's
`charge_integration_attempt` seam. `ralph.pipeline._integration_action.next_integration_action`
is NOT that authority — it is the typed projection helper that maps a
durable state (`RebaseState` / `IntegrationRecord`) onto the closed
`IntegrationAction` vocabulary, and it fails closed to
`REQUIRE_INTERVENTION` when the durable facts cannot prove a next step.

### Object-reachability pre-pass

During diagnostic recovery (`handoff_integration_diagnostic` in `ralph.pipeline._integration_diagnostic`),
Ralph executes an operation-scoped pre-pass before calling any diagnostic agent:

1. **Object reachability**: `SubprocessOperationEvidenceCollector` queries `git cat-file -e <sha>`
   for `MERGE_HEAD` and `REBASE_HEAD` within the operation worktree. It emits
   `_KIND_MERGE_OBJECT_RESOLVABLE` and `_KIND_REBASE_OBJECT_RESOLVABLE` facts.
   If a marker SHA is unresolvable (`False`), the pre-pass halts immediately and transitions the
   durable record to `diagnostic_intervention_required=True` via `RAISE_DIAGNOSTIC_INTERVENTION`
   without consuming an attempt, preserving all worktree metadata.

2. **Positive completion proof**: If marker objects are resolvable and the typed projection
   `next_integration_action(record)` is `IntegrationAction.CONTINUE_GIT_OPERATION`,
   Ralph evaluates the four-condition completion proof:
   - *Commit ancestry*: HEAD matches `integrated_feature_sha` or `pre_target_sha`, and parent of HEAD matches expected parent(s).
   - *Operation ownership*: `owning_worktree` matches the inspected root, and `diagnostic_ownership` matches `ownership_fingerprint(record)`.
   - *Clean index and tree*: `MERGE_HEAD` and `REBASE_HEAD` are absent, index contains no unmerged paths, and `git diff --quiet HEAD` is clean.
   - *Durable atomic transition*: `record.phase == "integrated"` (or integrating with `integrated_feature_sha`) and `diagnostic_evidence` is empty.

If all four conditions pass, Ralph releases diagnostic evidence via `RELEASE_DIAGNOSTIC_EVIDENCE`
and advances the pipeline without dispatching an agent. If any condition fails, the pre-pass transitions
to `RAISE_DIAGNOSTIC_INTERVENTION` without consuming an attempt or deleting marker files.

### Worked example: typed projection

```python
from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
    next_integration_action,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.display.auto_integrate_message import OperationDisplayState

# 1. Authoritative producer records typed outcome
state = RebaseState(last_outcome=RebaseOutcomeKind.CONFLICT)

# 2. Backward-compatible last_action property projects string value
assert state.last_action == "conflict"

# 3. The typed projection over the durable state names the closed action
#    vocabulary. A CONFLICT state with no durable retry deadline cannot
#    prove a bounded wait, so the projection fails closed.
action = next_integration_action(state)
assert action == IntegrationAction.REQUIRE_INTERVENTION

# 4. Operator display projects directly from RebaseState without prose parsing
assert state.operation_display_state == OperationDisplayState.CONFLICT_RESOLUTION
```

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new gate is added or an existing gate is removed.
* The authoritative entry point changes.
* The bypass-detection audit changes.

## Research basis

* publisher: Google Engineering Practices
  title: "Code Review: Speed of Code Reviews"
  http: https://google.github.io/eng-practices/review/reviewer/speed.html
  review date: 2026-07-11

* publisher: Google SRE Book
  title: "Monitoring Distributed Systems"
  http: https://sre.google/sre-book/monitoring-distributed-systems/
  review date: 2026-07-11

* publisher: Martin Fowler
  title: "Continuous Integration"
  http: https://martinfowler.com/articles/continuousIntegration.html
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

* Policy id: `<!-- ralph-policy-id: verification-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v2 -->`
