# AGENTS.md

## Scope

This repository’s maintained implementation is the Python package in `ralph-workflow/`.
Treat older Rust-oriented material elsewhere in the repo as legacy background unless a document explicitly says it was refreshed for Python.

## Source of truth

Use these first, in this order:
1. `PROMPT.md`
2. `ralph-workflow/CONTRIBUTING.md`
3. `docs/agents/verification.md`
4. `ralph-workflow/README.md`
5. Python source and docstrings under `ralph-workflow/ralph/`

If instructions conflict, follow the stricter one.

## Priorities

1. Fix surfaced issues immediately.
2. Keep the Python package correct and verified.
3. Prefer small, maintainable diffs over quick hacks.
4. Keep documentation and commands aligned with actual behavior.

## Non-negotiables

- Work in `ralph-workflow/` for code, tests, and verification.
- Fix any bug, lint failure, type failure, test failure, or warning you surface before moving on.
- Do not leave the repo in a broken state.
- Do not weaken checks to get green results.
- Update user-facing docs when commands, workflows, or behavior change.
- All code **must** be testable in a black box way. If you cannot test it easily, it strongly suggests you have to refactor.
- If there is a test failure, either the tests is implemented wrong, the code behavior is wrong, or the test is testing the wrong behavior. DO NOT change the test to match the current implementation. A test may never make any assumptions about the underlying implementation of the code.
- All tests must complete in 30s or less, no exceptions.
- This 30-second limit is the COMBINED TOTAL wall-clock budget for ALL test suites running sequentially under `make verify`. It is ABSOLUTE and IMMUTABLE — enforced by `ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS`. Individual suite timeouts (PYTEST_SUITE_TIMEOUT_SECONDS, DEFAULT_SUITE_TIMEOUT_SECONDS) are per-suite caps only; the combined budget cannot be circumvented by splitting tests, adding suites, or changing per-suite limits. A timeout failure is a test design defect — fix the test, not the budget.

## Required workflows

- Feature or bugfix: use the `test-driven-development` skill first.
- Debugging or failing verification: use the `systematic-debugging` skill first.
- Any test work: read `docs/agents/testing-guide.md` first.
- Any README/docs/public-doc change: read `docs/code-style/documentation-rubric.md` first and treat it as mandatory.
- Any commit work: dogfood Ralph itself by using `ralph --generate-commit`.

## Commit rule

- Do not run any commits. If commits are required by prompt, use `ralph --generate-commit` ONLY and nothing else

## Absolutely Zero Dead code

Zero tolerance for any type of dead code. This is not negotiable, it is **INFINITELY BETTER** to rewrite dead code if we need to later on 
than it is to leave dead code around. If in doubt, **REMOVE IT**.

## Verification

Before completion, run the required checks from `docs/agents/verification.md`:

```bash
cd ralph-workflow
make verify
```

Verification passes only when all required checks succeed with no ERROR/WARNING diagnostics.
If verification fails, fix the issue and rerun it.

Run the extra smoke checks or focused tests from `docs/agents/verification.md` whenever the touched area requires them.

The 30s combined test budget is absolute; see `docs/agents/verification.md` for the full policy.

## Documentation and file hygiene

- Keep Markdown concise and current with the Python project.
- Do not create temporary Markdown files in the repo root or `docs/`.
- Put temporary files under `tmp/` at the repo root.

## External dependencies

Do not assume third-party API behavior.
Research order: Context7 first, then official docs.
