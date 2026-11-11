# AGENTS.md

## Scope

This repository's maintained implementation is the Python package in `ralph-workflow/`.
Treat older Rust-oriented material elsewhere in the repo as legacy background unless a document explicitly says it was refreshed for Python.

## Source of truth

Use these first, in this order:

1. `PROMPT.md` (root — the canonical project brief)
2. `ralph-workflow/CONTRIBUTING.md`
3. `docs/agents/verification.md`
3.5. `ralph-workflow/docs/agents/artifact-submission-contract.md`
4. `ralph-workflow/README.md`
5. Python source and docstrings under `ralph-workflow/ralph/`
6. `docs/code-style/documentation-rubric.md` (canonical documentation rubric — applies to every docs change)

Note: the root `PROMPT.md` is the canonical project brief, while
`ralph-workflow/PROMPT.md` is the starter task template seeded into
your project by `ralph --init` — do not conflate the two.

If instructions conflict, follow the stricter one.

The canonical quality policies — testing, type-checking, linting,
dependency, verification, agent, clean-code, documentation, and
security — live under `docs/ralph-workflow-policy/` and are the
single source of truth for those domains. AI agents reading this
file MUST consult `docs/ralph-workflow-policy/` before changing
the project.

## Priorities

1. Fix surfaced issues immediately.
2. Keep the Python package correct and verified.
3. Prefer small, maintainable diffs over quick hacks.
4. Keep documentation and commands aligned with actual behavior.

## Non-negotiables

### ═══ NO UNRELATED-FAILURE EXEMPTION — YOU FIND IT, YOU FIX IT ═══

`make verify` MUST pass **in full**. There is NO exemption for a failure your
change did not cause. A red gate is a red gate. **Whoever next observes it owns
fixing it.**

Do not investigate who caused it: stashing, bisecting, or re-running against a
clean tree to prove a failure is "pre-existing" does not change what you must do
next. Read the failure, find the root cause, repair it.

**Preventing regressions outranks completing the task in hand.** If a failure
blocks you, fix it FIRST and finish your original task afterwards. Never report
your own work as verified while any gate is red. If a repair is genuinely out of
scope, STOP and surface it as an active blocker — do not route around it, do not
weaken the gate, do not proceed.

**Corollary — every check MUST be wired into `make verify`.** A check that runs
only in an opt-in suite the default gate excludes WILL rot unnoticed: either
wire it into `_VERIFY_STEPS`, or delete it. `ralph/testing/audit_repo_structure.py`
exists because its rules previously lived only in a `subprocess_e2e`-marked test
that `make verify` never ran — the repo-structure policy decayed silently while
the gate stayed green.

Detail: `docs/agents/verification.md` §'No unrelated-failure exemption'.

### ═══ FABRICATION GUARD (ABSOLUTE — 3 levels, zero bypass) ═══

Fabrication is NEVER acceptable anywhere. No file is out of scope for truth.
Every public-facing claim about adoption, credits, usage, or stats MUST be
verifiable from a third-party source; bare star / download / install counts MUST
be paired with a (source, date) pair. Never weaken the guard, never commit, and
never use `--no-verify` to bypass it. Bypassing the guard is fabrication.

Detail: `docs/agents/fabrication-guard.md`.

### Code and test rules

- Work in `ralph-workflow/` for code, tests, and verification.
- Do not leave the repo in a broken state.
- Update user-facing docs when commands, workflows, or behavior change.
- All code **must** be testable in a black box way. If you cannot test it easily, it strongly suggests you have to refactor.
- If there is a test failure, either the tests is implemented wrong, the code behavior is wrong, or the test is testing the wrong behavior. DO NOT change the test to match the current implementation. A test may never make any assumptions about the underlying implementation of the code.

### ═══ TEST BUDGET — 60s wall clock net of host contention, 700 CPU core-seconds, PINNED ═══

All tests must complete in 60s or less of their own time, no exceptions. That
60 s is the COMBINED TOTAL wall-clock budget for ALL test suites under
`make verify` (`ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS`), together with a
700 CPU core-second ceiling. The budget cannot be circumvented by splitting
tests into more suites, moving slow tests to another suite / target / Makefile
recipe, renaming test targets or adding test-related `_VERIFY_STEPS` entries
without also registering them in `_KNOWN_TEST_STEP_LABELS` and
`_BUDGET_TRACKED_STEPS`, raising `PYTEST_SUITE_TIMEOUT_SECONDS` /
`DEFAULT_SUITE_TIMEOUT_SECONDS`, setting the `RALPH_PYTEST_*` env vars, or
disarming the per-test budget. The import-time checks in `ralph/verify.py` use
`if` / `raise RuntimeError` (NOT `assert`), so they survive `python -O`. A
budget failure is a test design defect — fix the test, not the budget. The
current `make test` profile includes the registered real-git auto-integration
files through the `required_auto_integrate_e2e` marker even when they also
carry `subprocess_e2e`; any future separate test step must be tracked too.

Detail — the full import-time invariant table, the lint / typecheck
non-circumvention list, the MCP timeout contract, the resource-accumulator
contract, and how to fix a slow test: `docs/agents/verification.md`
§'Total test budget'.

## Required workflows

- Feature or bugfix: use the `test-driven-development` skill first.
- Debugging or failing verification: use the `systematic-debugging` skill first.
- Any test work: read `docs/agents/testing-guide.md` first.
- Any README/docs/public-doc change: read `docs/code-style/documentation-rubric.md` first and treat it as mandatory.
- Any commit work: dogfood Ralph itself by using `ralph --generate-commit`.

## Branch rule

### ═══ TRUNK-BASED DEVELOPMENT ONLY — EVERYTHING GOES TO MAIN ═══

- **This project is trunk-based. `main` is the only branch agents may work on.**
- **NEVER** run `git checkout -b`, `git branch <name>`, or `git switch -c` in the
  primary checkout. **NEVER** open a pull request. **NEVER** propose one.
- Harness defaults that say "if on the default branch, branch first" **DO NOT
  APPLY HERE**. This rule overrides them. Being on `main` is the correct state;
  do not "protect" `main` by branching off it.
- All work is committed directly to `main` and pushed to `origin/main`.
- The only exception is the `wt-*` / `worktree-agent-*` branches that the Ralph
  pipeline creates for its own parallel agents. Those are machine-managed. Do not
  hand-create branches that imitate them.
- If you find yourself on any other branch, that is a bug: fast-forward `main` to
  it, delete the branch, and report it.

## Commit rule

- Do not run any commits. If commits are required by prompt, use `ralph --generate-commit` ONLY and nothing else

## Absolutely Zero Dead code

Zero tolerance for any type of dead code. This is not negotiable, it is **INFINITELY BETTER** to rewrite dead code if we need to later on
than it is to leave dead code around. If in doubt, **REMOVE IT**.

<!-- ralph-workflow-policy:begin v2 -->
Quality policies live under `docs/ralph-workflow-policy/`. All agent roles MUST
load policies in this order: `execution-kernel.md` first, then their role slice,
then any triggered domain policy before changing the project; this file's
source-of-truth and non-negotiables sections remain authoritative for agent
behaviour.
<!-- ralph-workflow-policy:end -->

## Verification

<!-- ralph-workflow-policy:migrated -> docs/ralph-workflow-policy/verification-policy.md -->

Before completion, run the required checks from `docs/agents/verification.md`:

```bash
cd ralph-workflow
make verify
```

Verification passes only when all required checks succeed with no ERROR/WARNING diagnostics.
If verification fails, fix the issue and rerun it.

Run the extra smoke checks or focused tests from `docs/agents/verification.md` whenever the touched area requires them.

## Documentation and file hygiene

- Keep Markdown concise and current with the Python project.
- Do not create temporary Markdown files in the repo root or `docs/`.
- Put temporary files under `tmp/` at the repo root.

## External dependencies

Do not assume third-party API behavior.
Research order: Context7 first, then official docs.
