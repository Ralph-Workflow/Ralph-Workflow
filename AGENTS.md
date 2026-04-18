# AGENTS.md

> **Repository status:** Ralph Workflow is now maintained as a Python project under `ralph-workflow/`.
> Unless a section explicitly says it has been refreshed for Python, Rust-specific guidance below should be treated as legacy implementation history rather than the current source of truth.
> Current verification lives in `docs/agents/verification.md` and `ralph-workflow/CONTRIBUTING.md`.

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

## Lint Policy (Strict)
- **`#[allow(...)]` macro — Forbidden.** Zero exceptions. Use `#[expect(..., reason = "...")]` only for external proc-macro output.
- **`.expect()`/`.unwrap()` — Forbidden** except at: `test-helpers/src/lib.rs`, `xtask/src/main.rs`, boundary modules (`io/`, `runtime/`, `ffi/`, `boundary/`).
- **Functional lints:** Never suppress. Don't fake a boundary module just to silence a lint.
- Check compliance: `cargo xtask lsp-forbidden-allow-expect`
- See `docs/agents/verification.md` for `#[allow]`/`#[expect]` enforcement; `docs/tooling/dylint.md` for boundary module definitions.

---

## Required Workflows
| Trigger | Action |
|---------|--------|
| Feature/bugfix | Use `test-driven-development` skill first |
| Debugging | Use `systematic-debugging` skill first |
| Any pipeline/reducer change | Read architecture docs first |
| Any test work | Read `docs/agents/testing-guide.md` |
| Filesystem I/O | Read `docs/agents/workspace-trait.md` |
| Build/verify | Runs on `rw-build-server` by default; see `docs/tooling/remote-build.md` |

---

## Non-Negotiables
- TDD required (failing test first)
- Verification required before PR
- No tech debt (prefer refactor)
- No dead code (`#[allow(dead_code)]` forbidden)
- Never weaken lint rules

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

### `#[allow(...)]` — Never. Not Once.

**`#[allow(...)]` and `#![allow(...)]` are prohibited everywhere in this codebase**, with zero exceptions.

There are no exceptions. If a lint fires on code generated by an external source (test harness, proc-macro, external trait impl), use `#[expect(..., reason = "...")]` at item scope per the conditions below.

### `#[expect(...)]` — Conditional, With Reason

**`#[expect(...)]` is permitted ONLY when ALL three conditions are met:**

1. The lint fires on code you cannot modify (proc-macro output, external trait impls, build-script artifacts).
2. It includes `reason = "..."` naming the specific external source.
3. It is the narrowest possible scope (item attribute, not module or crate).

Example of correct usage:
```rust
#[expect(clippy::some_lint, reason = "proc-macro output from derive_more")]
```

**`#![expect(...)]` (inner attribute) is ALWAYS prohibited**, regardless of reason.

If a lint fires on your code:
- **Refactor the code.** The lint is telling you something is wrong with the structure.
- Do not suppress. Do not argue inline. If you believe the lint rule itself is wrong, raise it as a policy discussion — never suppress ad hoc.

If you encounter an existing `#[allow(...)]`: **fix it now.** It is a pre-existing violation. See the top of this file.

### `.expect()` and `.unwrap()` — Forbidden Except at Documented Sites

These are forbidden in production workflow code and integration tests. Permitted only at:

| Location | Justification |
|----------|--------------|
| `test-helpers/src/lib.rs` | Wraps git2/libgit2 C API; cannot propagate `Result` without redesigning the harness |
| `xtask/src/main.rs` | Top-level binary entry point; no caller to return `Result` to |
| Boundary modules (`io/`, `runtime/`) | OS-level calls where failure is unrecoverable and `Result` propagation is architecturally impossible |

Everywhere else: use `?`, `map_err`, `and_then`, or proper `Result` propagation. Finding `.expect()` outside these sites is a bug. Fix it now — it is a pre-existing issue.

### Functional Rust Lints — Never Suppress, Never Fake a Boundary

The four functional-Rust dylint lints (`forbid_mut_binding`, `forbid_imperative_loops`, `forbid_mutating_receiver_methods`, `forbid_interior_mutability`) enforce that domain code is written as pure functional transformations.

**Boundary modules (`io/`, `runtime/`, `ffi/`, `boundary/`) are where mutation genuinely belongs.** Do not move code into a boundary module just to silence a lint. Ask: does this code interact directly with I/O, the OS, FFI, or a process boundary? If yes, a boundary module is appropriate. If no, refactor to combinators.

See **[CODE_STYLE.md](CODE_STYLE.md)** — Functional Rust section — for the full explanation, lint table, and examples.

### Other lint rules

- If a requested lint rule exposes existing violations, fix the violating code; do not disable or dilute the rule.
- Do not add new lint suppressions of any kind without explicit direction from the user.
- Do not change `clippy.toml` or crate-level lint attributes to work around a failure.

---

## No Dead Code — Ever

Dead code is not a future asset. It is a liability that increases cognitive overhead, blocks refactoring, and misleads future contributors.

**`#[allow(dead_code)]` is forbidden** — it falls under the absolute prohibition on `#[allow(...)]` and also hides a structural problem.

When you find dead code, handle it by:
- Deleting it — preferred
- Making it used if there is a concrete immediate need
- Gating behind an active feature flag
- Moving to `examples/` or `benches/`

Dead code removal is **more important than the task you were working on.** If you find dead code while doing something else, stop and delete it first.

---

## Required Verification (before PR/completion)

Run the commands in `docs/agents/verification.md`. Verification passes when required checks complete successfully with **no ERROR/WARNING diagnostics** (informational output is acceptable).

### ⚠️ YOU MUST FIX ALL FAILURES — NOT JUST YOUR OWN ⚠️

**There is no such thing as a "pre-existing" failure that you are allowed to ignore.**

If verification or investigation surfaces an issue — any issue — **that issue is now yours and you must fix it before completion.** It does not matter who wrote the code. It does not matter when the issue was introduced. The moment you see it, you own it.

**The instant an issue is surfaced, it becomes YOUR TOP PRIORITY.** Everything else stops. The user's original prompt becomes secondary. The surfaced issue overrides the current task until it is resolved.

Forbidden rationalizations (none of these are acceptable):
- "This is pre-existing."
- "I didn't write this code."
- "This is unrelated to my task."
- "I'll clean this up in a follow-up."
- "Someone else should fix this."

**The rule:**
1. Run verification.
2. Any failure or ERROR/WARNING → **STOP EVERYTHING.**
3. Fixing the surfaced issue is now your entire job.
4. Fix it completely. Re-run verification. Confirm clean.
5. Return to the original task.

### Additional verification for metrics changes

When changing iteration/retry/continuation/fallback logic, run the metrics tests:

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
