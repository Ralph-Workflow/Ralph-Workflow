# AGENTS.md

## Context
- **Current plan:** `.agent/PLAN.md`
- **Overall objective:** `PROMPT.md`

---

## 🚨 FIX SURFACED ISSUES NOW — THIS IS THE ONLY MOST IMPORTANT SECTION IN AGENTS.md

> **Pre-existing issue being fixed NOW. This is non-negotiable.**

**When you encounter ANY issue — bug, lint error, test failure, warning, design flaw, missing validation, pre-existing issue, anything — you fix it immediately. No exceptions. No deferring. No "you can ignore pre-existing issues" unless your PROMPT explicitly says so.**

### What "surfaced" means
- Anything you encounter while working: bugs, lint errors, test failures, compiler warnings, missing error handling, race conditions, edge cases
- Issues found in verification output
- Issues found in code review
- Issues in CI/CD pipelines
- **ANY** pre-existing issue you notice

### What "fix now" means
1. Stop current task
2. Fix the issue completely (not a workaround, not a TODO)
3. Verify the fix passes all checks
4. Continue original task

### The only exception
Your PROMPT explicitly states **"you can ignore pre-existing issues"** — only then may you skip fixing pre-existing issues. This must be **exact wording** — never assumed.

### Verification requirement
Run `docs/agents/verification.md` before completing ANY task. Zero ERROR/WARNING allowed.

---

## Priorities (in order)

> **Pre-existing issue being fixed NOW. This is non-negotiable. Only "you can ignore pre-existing issues" in your PROMPT overrides this.**

1. **Fix surfaced issues NOW** (see 🚨 section above — this is the only most important section)
2. Correctness — tests pass, behavior matches intent
3. Maintainability — clear code, no magic
4. Consistency — follow patterns, rustfmt/clippy clean
5. Small diffs — prefer refactor over tech debt

If instructions conflict with other files (e.g., `CONTRIBUTING.md`), follow the **stricter** rule.

---

## Lint Policy — STRICT

> **Pre-existing issue being fixed NOW. This is non-negotiable. Only "you can ignore pre-existing issues" in your PROMPT overrides this.**

| Rule | Policy |
|------|--------|
| `#[allow(...)]` | **FORBIDDEN** — zero exceptions |
| `#[expect(...)]` | Only with `reason = "..."` for external proc-macro output at item scope |
| `.expect()` / `.unwrap()` | **Forbidden** except at: `test-helpers/src/lib.rs`, `xtask/src/main.rs`, `ralph-gui/src/main.rs`, boundary modules (`io/`, `runtime/`) |
| Functional lints | Never suppress: `forbid_mut_binding`, `forbid_imperative_loops`, `forbid_mutating_receiver_methods`, `forbid_interior_mutability` |

### Boundary modules (where mutation is allowed)
- `io/`, `runtime/`, `ffi/`, `boundary/`

### If a lint fires
**Refactor the code. Never suppress it.** If you find existing `#[allow(...)]`: fix it now (pre-existing issues included — fix them all).

See `docs/tooling/dylint.md` for boundary module definitions.

---

## No Dead Code

> **Pre-existing issue being fixed NOW. This is non-negotiable. Only "you can ignore pre-existing issues" in your PROMPT overrides this.**

- `#[allow(dead_code)]` is **FORBIDDEN** (same category as `#[allow(...)]`)
- Test-only code in production modules = dead code. Move to `test-helpers/`, inline `#[cfg(test)]`, or `tests/`
- Rule: if removing it causes zero non-test compilation failures, it's dead code. **Delete it.**
- Any pre-existing dead code you encounter = surfaced issue = fix it now.

---

## Required Workflows

| Situation | Do This First |
|-----------|---------------|
| Feature/bugfix | `test-driven-development` skill |
| Debugging | `systematic-debugging` skill |
| Angular/GUI | Angular MCP + `frontend-angular` skill |
| Styling/visual | `frontend-design` skill |
| Pipeline/reducer change | Read architecture docs first |
| Any test work | `docs/agents/testing-guide.md` |
| Filesystem I/O | `docs/agents/workspace-trait.md` |
| Build/verify | Runs on `rw-build-server` by default; see `docs/tooling/remote-build.md` |

---

## Key References

| Topic | File |
|-------|------|
| Verification commands | `docs/agents/verification.md` |
| Testing guide | `docs/agents/testing-guide.md` |
| Architecture | `docs/code-style/architecture.md`, `docs/architecture/event-loop-and-reducers.md` |
| Dylint lints | `docs/tooling/dylint.md` |
| Workspace trait | `docs/agents/workspace-trait.md` |
| Remote build | `docs/tooling/remote-build.md` |

---

## File Creation Rules

- **NO** temporary .md files in root or doc folders
- **NO** new files in root/doc unless documentation
- Temp files → `tmp/` at repo root (gitignored)

---

## External Dependencies

Never assume API behavior. Research order: context7 → official docs via playwright.

---

## YOLO Mode

All agents need `yolo_flag` in `agents.toml`:
- Claude CLI: `--dangerously-skip-permissions`
- Aider: `--yes`
- Claude Code: via environment (no flag needed)

---

## Testing

Read `docs/agents/testing-guide.md` before writing or touching any test.

---

## Pipeline/Reducer Changes

If you change pipeline behavior (phases, retries/fallback, effect sequencing, checkpoint/resume, or any reducer/event/effect shape), architecture reading is **REQUIRED**:
- `CODE_STYLE.md` (Architecture section)
- `docs/architecture/event-loop-and-reducers.md`
- `docs/architecture/effect-system.md`

---

## Verification Before PR

> **Pre-existing issue being fixed NOW. This is non-negotiable. Only "you can ignore pre-existing issues" in your PROMPT overrides this.**

Run `docs/agents/verification.md`. **Fix ALL failures** — not just your own. No "pre-existing" exceptions. This includes verifying that pre-existing issues discovered during your work are also fixed.

### Metrics changes (iteration/retry/continuation/fallback logic)
```bash
# Metrics unit tests
cargo xtask test -p ralph-workflow --lib reducer::state_reduction::tests::metrics

# Metrics integration tests
cargo xtask test -p ralph-workflow-tests --test integration_tests iteration_counter
cargo xtask test -p ralph-workflow-tests --test integration_tests continuation_budget
cargo xtask test -p ralph-workflow-tests --test integration_tests summary_consistency
```

### Logging changes (per-run logging, event loop logging, log file paths)
```bash
# Per-run logging infrastructure tests
cargo xtask test -p ralph-workflow-tests --test integration_tests logging_per_run

# Event loop trace dump tests
cargo xtask test -p ralph-workflow-tests --test integration_tests event_loop_trace_dump
```
