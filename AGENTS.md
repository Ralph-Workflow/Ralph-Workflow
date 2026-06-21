# AGENTS.md

## Scope

This repository’s maintained implementation is the Python package in `ralph-workflow/`.
Treat older Rust-oriented material elsewhere in the repo as legacy background unless a document explicitly says it was refreshed for Python.

## Source of truth

Use these first, in this order:
1. `PROMPT.md`
2. `ralph-workflow/CONTRIBUTING.md`
3. `docs/agents/verification.md`
3.5. `ralph-workflow/docs/agents/artifact-submission-contract.md`
4. `ralph-workflow/README.md`
5. Python source and docstrings under `ralph-workflow/ralph/`

If instructions conflict, follow the stricter one.

## Priorities

1. Fix surfaced issues immediately.
2. Keep the Python package correct and verified.
3. Prefer small, maintainable diffs over quick hacks.
4. Keep documentation and commands aligned with actual behavior.

## Non-negotiables

### ═══ FABRICATION GUARD (ABSOLUTE — 3 levels, zero bypass) ═══

The D91 incident (commit 58a1d25e9, 2026-06-21) fabricated an entire entry
(`john-ezra/open-ralph`) in USERS.md — a nonexistent repo, npm package, and user.
This was the second fabrication in the project (after the 2026-06-11 SHOWCASE.md
failure). Fabrication is the single gravest threat to this project's credibility.

**The guard system:**

- **`scripts/fabrication_guard.py`** — multi-level fabrication defense:
  - Level 1 (regex patterns, no network, <100ms): catches known bad patterns.
    Runs as a **pre-commit hook** (`.git/hooks/pre-commit`) — blocks bad commits.
  - Level 2 (existence checks, network, cached): verifies every GitHub repo,
    npm package, and external URL actually exists. Run with `--level 2`.
  - Level 3 (quantitative verification, network, needs GITHUB_TOKEN):
    cross-references star counts, fork counts, line counts against live API.
    Run with `--level 3`.
- **`scripts/verify_social_proof.py`** — thin backward-compat wrapper.

**Mandatory protocol (DO NOT SKIP — no exceptions):**

1. Before editing ANY public-facing markdown (README.md, ralph-workflow/README.md,
   SHOWCASE.md, USERS.md, docs/), run:
   ```bash
   ./scripts/fabrication_guard.py --level 1 <file>
   ```
2. If adding NEW external references (GitHub repos, npm packages, URLs), run:
   ```bash
   ./scripts/fabrication_guard.py --level 2 <file>
   ```
3. After editing, run the same check again.
4. If ANY level fails: FIX THE CLAIMS, do NOT weaken the guard, do NOT commit
   until clean.
5. The pre-commit hook will block you if you forget. Do NOT use `--no-verify`
   to bypass it. Bypassing the guard is fabrication.

**Non-negotiable rules:**

- Every public-facing claim about adoption, credits, usage, or stats MUST be
  verifiable from a third-party source.
- GitHub repo links in claim files (USERS.md, SHOWCASE.md, README.md) MUST have
  a `verify: repo-exists` annotation.
- npm package claims MUST have a `verify: npm-@org/pkg-exists` annotation
  verified against the npm registry.
- Bare star/download/install counts MUST be paired with (source, date).
- Banned forever: "Nightcrawler credits Ralph Workflow" (it credits
  ghuntley.com/ralph); "~1,300 installs/month" (stale + fabricated).
- Fabrication is NEVER acceptable anywhere. No file is out of scope for truth.

### Code and test rules

- Work in `ralph-workflow/` for code, tests, and verification.
- Fix any bug, lint failure, type failure, test failure, or warning you surface before moving on.
- Do not leave the repo in a broken state.
- Do not weaken checks to get green results.
- Update user-facing docs when commands, workflows, or behavior change.
- All code **must** be testable in a black box way. If you cannot test it easily, it strongly suggests you have to refactor.
- If there is a test failure, either the tests is implemented wrong, the code behavior is wrong, or the test is testing the wrong behavior. DO NOT change the test to match the current implementation. A test may never make any assumptions about the underlying implementation of the code.

### ═══ ABSOLUTE TEST BUDGET — 60s, IMMUTABLE ═══

- All tests must complete in 60s or less, no exceptions.
- This 60-second limit is the COMBINED TOTAL wall-clock budget for ALL test suites running sequentially under `make verify`. It is ABSOLUTE and IMMUTABLE — enforced by `ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS`. Individual suite timeouts (PYTEST_SUITE_TIMEOUT_SECONDS, DEFAULT_SUITE_TIMEOUT_SECONDS) are per-suite caps only; the combined budget cannot be circumvented by splitting tests, adding suites, or changing per-suite limits. A timeout failure is a test design defect — fix the test, not the budget.
- Per-suite timeouts (PYTEST_SUITE_TIMEOUT_SECONDS, DEFAULT_SUITE_TIMEOUT_SECONDS) are SECONDARY caps only. The AUTHORITATIVE and only budget that matters is `_TOTAL_TEST_BUDGET_SECONDS = 60.0` tracked cumulatively by `ralph/verify.py` via `time.monotonic()` across ALL `_BUDGET_TRACKED_STEPS`. Raising per-suite limits does NOT increase the combined budget.
- The budget enforcement in `ralph/verify.py` uses `if`/`raise RuntimeError` (NOT `assert`) — this prevents `python -O` from stripping the invariant checks. All import-time invariants survive `-O`.

**Every import-time invariant in `ralph/verify.py` that guards the budget:**

| Invariant | Error message | Purpose |
|---|---|---|
| `_TOTAL_TEST_BUDGET_SECONDS > 0` | `must be positive` | Budget can't be zero or negative |
| `abs(_TOTAL_TEST_BUDGET_SECONDS - 60.0) < 1e-9` | `must be 60.0` | Budget constant can't be silently altered |
| `_BUDGET_TRACKED_STEPS` indices valid | `indices must be valid` | Tracked steps must exist in `_VERIFY_STEPS` |
| Tracked steps have positive timeout | `must have a positive timeout` | Budget-tracked steps need timeout |
| `_KNOWN_TEST_STEP_LABELS` non-empty | `must not be empty` | Can't empty label set to hide test steps |
| `_BUDGET_TRACKED_STEPS` non-empty | `must not be empty` | Can't empty tracked set to disable budget |
| `'make test'` in `_KNOWN_TEST_STEP_LABELS` | `must contain 'make test'` | Primary test step label always present |
| Labels/steps sync enforced | `must be`/`must NOT be` | Every label in `_KNOWN_TEST_STEP_LABELS` is tracked; no untracked test steps |
| `_VERIFY_STEP_TIMEOUT_SECONDS > 0` | `must be positive` | Per-step timeout can't be zero or negative |
| `_VERIFY_STEP_TIMEOUT_SECONDS >= 5.0` | `must be at least 5.0` | Per-step timeout must be non-trivial (>= 5s) |

All invariants are tested in `tests/test_verify_invariants.py` under `python -O` to confirm immunity.

**Non-circumvention rule — test budget** — the following do **NOT** circumvent the 60-second combined budget (see also `docs/agents/verification.md` §'Total test budget — 60 seconds, ABSOLUTE and IMMUTABLE' for the full cross-reference table):
  - Splitting tests into more suites (N suites does NOT give N × 60 s; cumulative tracker sums time across ALL budget-tracked steps)
  - Moving slow tests to a different suite, target, or Makefile recipe
  - Renaming test targets or adding new test-related `_VERIFY_STEPS` entries without also adding to `_KNOWN_TEST_STEP_LABELS` and `_BUDGET_TRACKED_STEPS`
  - Raising `DEFAULT_SUITE_TIMEOUT_SECONDS` or `PYTEST_SUITE_TIMEOUT_SECONDS` in the Makefile
  - Setting environment variables (`RALPH_PYTEST_SUITE_TIMEOUT_SECONDS`, `RALPH_PYTEST_TEST_TIMEOUT_SECONDS`)
  - Raising `_TOTAL_TEST_BUDGET_SECONDS` or modifying `_BUDGET_TRACKED_STEPS` in `ralph/verify.py` (blocked by import-time RuntimeError checks — immune to `python -O`)
  - Emptying `_KNOWN_TEST_STEP_LABELS` to hide test steps from budget tracking (blocked by import-time non-empty RuntimeError check)
  - Emptying `_BUDGET_TRACKED_STEPS` to disable budget enforcement (blocked by import-time non-empty RuntimeError check)
  - Removing `'make test'` from `_KNOWN_TEST_STEP_LABELS` to silently exclude the primary test step (blocked by import-time containment RuntimeError check)

- **Non-circumvention rule — lint and typecheck** — the following do **NOT** circumvent lint/typecheck enforcement:
  - Adding `per-file-ignores`, `extend-per-file-ignores`, or any ruff config to weaken lint enforcement — detected by `ralph/testing/audit_lint_bypass.py`
  - Adding `ignore_missing_imports`, `follow_imports = silent`, `exclude` patterns, `ignore_errors`, `disable_error_code`, `warn_unused_ignores = false`, or `disallow_untyped_defs = false` to mypy config — detected by `ralph/testing/audit_typecheck_bypass.py`
  - Using bare `# noqa` without a specific error code, or `# noqa: CODE` where CODE is not in the allowlist — detected by `ralph/testing/audit_lint_bypass.py`
  - Using blanket `# type: ignore` without a specific mypy error code, or `# type: ignore[CODE]` without a policy-compliant reason marker — detected by `ralph/testing/audit_typecheck_bypass.py` and enforced by `../docs/agents/type-ignore-policy.md` (mandatory reading)
  - Using `# type: ignore` in test files — tests must be fully typed (no exceptions)
  - Using `time.sleep()`, real subprocess, or real file I/O in non-`subprocess_e2e` tests — detected by `ralph/testing/audit_test_policy.py`
  - Any weakening of any check requires a documented justification and an entry in the audit allowlist — there is NO other path to bypass
- **Non-circumvention rule — MCP timeout contract** — every operation under `ralph/mcp/` must perform blocking I/O with a bounded, fail-closed timeout. An unbounded blocking call hangs the MCP server thread and starves the agent of output (a real agent-hang vector). Detected by `ralph/testing/audit_mcp_timeout.py` (part of `make verify`): `subprocess.run`/`.communicate`/`.wait` without `timeout=`, and `httpx.*`/`requests.*`/`urlopen`/`socket.create_connection` without `timeout=`. The ONLY bypass is an inline `# mcp-timeout-ok: <reason>` marker for a genuinely unbounded-by-design call. See `docs/agents/verification.md` §'MCP timeout contract'.
- **How to fix a slow test** (do NOT work around the budget):
  - Replace real I/O with fakes (MemoryWorkspace, tmp_path, MockProcessExecutor)
  - Eliminate sleep() and real wall-clock waits — inject a clock abstraction instead
  - Refactor production code for testability — extract I/O behind an interface
  - Assert on observable behavior, not implementation internals
  - For cumulative volume bottlenecks (many fast tests, not individual slow tests): consolidate parameterized tests with overlapping coverage, optimize shared fixtures, or reduce redundant test coverage — see `docs/agents/testing-guide.md`

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

The 60s combined test budget is absolute; see `docs/agents/verification.md` for the full policy.

## Documentation and file hygiene

- Keep Markdown concise and current with the Python project.
- Do not create temporary Markdown files in the repo root or `docs/`.
- Put temporary files under `tmp/` at the repo root.

## External dependencies

Do not assume third-party API behavior.
Research order: Context7 first, then official docs.
