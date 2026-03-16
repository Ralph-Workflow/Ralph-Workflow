# AGENTS.md

## ⚠️ EVERY SURFACED ISSUE MUST BE FIXED NOW — NO EXCEPTIONS ⚠️

**THE INSTANT ANY ISSUE IS SURFACED — broken test, warning, lint violation, failing check, dead code, stale doc, regression — IT BECOMES YOUR #1 PRIORITY. YOUR ORIGINAL PROMPT IS NOW SECONDARY.**

There is no "pre-existing." There is no "not mine." There is no "unrelated." The moment you see a problem, you own it. Fix it now.

**The rule:**
1. Discover any issue — test failure, warning, lint error, dead code, broken link, anything.
2. STOP. Fixing it is now your entire job. The original request waits.
3. Fix it completely. Re-run verification. Confirm clean.
4. Return to the original task.

**Forbidden rationalizations:** "I'll note this for later." / "pre-existing and out of scope." / "I didn't cause this." / "follow-up task." None are acceptable.

Pre-existing issues are *more* urgent — they've been tolerated too long. Every known failure left unfixed poisons every task that follows.

Run `docs/agents/verification.md` before marking any task complete. No "mostly passing." No "just one warning." Every ERROR and WARNING must be zero. If verification fails: stop, fix, re-run, repeat until clean.

---

## Non-Negotiables

- **EVERY surfaced issue MUST be fixed NOW.** No carve-outs. No deferrals.
- **TDD required for all code changes.** No production code without a failing test first.
- **Verification required for ANY code change:** run `docs/agents/verification.md` before PR/completion.
- **Architecture reading REQUIRED** before any pipeline/reducer/behavioral change: `docs/code-style/code-shape.md`, `docs/code-style/architecture.md`, `docs/code-style/boundaries.md`, `docs/architecture/event-loop-and-reducers.md`, `docs/architecture/effect-system.md`.
- **Testing guide REQUIRED** before writing/changing tests: `docs/agents/testing-guide.md`.
- **GUI is Angular v21, not React.** Treat React references in old docs/comments as stale; update when touched.
- **Prefer Tailwind over inline CSS** in the GUI. Inline styles only when clearly necessary.
- **No tech debt.** Prefer refactor even when it makes the diff larger. No deprecated/unused code.
- **Do not change linting rules** without explicit direction. Lint policy is a repository contract.
- **Never weaken lint rules to avoid refactoring.** The refactor is always the right answer.
- **`#[allow(...)]` is never permitted** — zero exceptions.
- **`.expect()` and `.unwrap()` are forbidden** except at documented sites; see Lint Policy.
- **`#[expect(...)]` requires all three documented conditions** and must stay narrow.

---

## Skill Usage (CRITICAL)

Before any meaningful action — code edits, debugging, exploration, planning, analysis, refactors, docs lookup, clarifying questions — agents MUST check whether any skill applies.

- **Questions are tasks.** Skill check before any response or action, including "just looking around."
- **Use skills liberally.** If there is any reasonable chance a skill is relevant, invoke it.
- **Prefer false positives over misses.** Better to invoke and not need it than to skip.
- **Do not rationalize skipping.** "This is simple" / "I know this already" / "I'll gather context first" / "just a question" are not valid reasons.
- **When uncertain:** invoke the most likely skill first.
- **Feature/bugfix:** use `test-driven-development` skill before changing code.
- **Debugging:** use `systematic-debugging` skill before proposing fixes.
- **Angular/GUI:** use Angular MCP server + `frontend-angular` skill for implementation, debugging, refactors, docs.
- **Styling/visual:** use `frontend-design` skill for any styling, layout, or UI presentation work.
- Failure to use an applicable skill is a process failure, even if the resulting output is otherwise acceptable.

---

## FORBIDDEN GIT COMMANDS (CRITICAL — NO EXCEPTIONS)

**Ralph is the ONLY entity allowed to commit.** You are STRICTLY PROHIBITED from running ANY git command that writes, modifies history, or changes repository state.

**NEVER run (not exhaustive — when in doubt, do NOT run it):**
`git commit`, `git push`, `git tag`, `git merge`, `git rebase`, `git reset` (any flag), `git checkout -- .`, `git restore .`, `git stash drop/pop/apply`, `git branch -D/-d`, `git clean`, `git cherry-pick`, `git revert`, `git am`, `git apply`, `git add`, `git init`

**ONLY these are allowed (read-only):**
`git status`, `git log`, `git diff`, `git show`, `git branch` (list only), `git remote -v`, `git stash list`, `git rev-parse`, `git ls-files`, `git describe`

Git hooks (pre-commit, pre-push, pre-merge-commit) and a PATH wrapper are installed automatically by Ralph. Forbidden commands WILL be blocked with exit code 1.

### FORBIDDEN MCP/TOOL USAGE

MCP git tools are equally prohibited: `mcp__git__git_commit`, `mcp__git__git_add`, `mcp__git__git_push`, `mcp__git__git_reset`, `mcp__git__git_checkout` (with `--`), `mcp__git__git_stash` (except list), `mcp__git__git_merge`, `mcp__git__git_init`, `mcp__git__git_create_branch`.

The prohibition applies to ALL mechanisms: CLI, MCP tools, direct library calls, subprocess spawning with absolute paths.

### FORBIDDEN: Hook and Marker Tampering

Deleting or modifying git hooks or files in `.git/ralph/` is equivalent to an unauthorized commit and will be treated as a security violation.

**NEVER:**
- Delete/modify `.git/hooks/pre-commit`, `pre-push`, `pre-merge-commit`
- Delete/modify files in `.git/ralph/`
- Use absolute paths (`/usr/bin/git`) to bypass the PATH wrapper
- Modify `PATH` to remove Ralph's git wrapper directory
- Run `chmod` on hook or `.git/ralph/` files
- Set `GIT_DIR`, `GIT_WORK_TREE`, or `GIT_EXEC_PATH`

**Why bypass is futile:** Hooks, markers, and protections are reinstalled before EVERY agent invocation. Ralph detects unauthorized commits by comparing HEAD OID before/after each run. All tampering is logged. Every bypass attempt wastes your execution budget with zero chance of success.

**What to do instead:** Write changes to files. Ralph's commit effect commits them at the right time.

---

## Priorities (in order)

1. **Fix surfaced issues** — any issue discovered mid-task becomes priority zero immediately
2. **Correctness** — tests pass, behavior matches intent
3. **Maintainability** — clear code, no magic
4. **Consistency** — follow existing patterns, rustfmt/clippy clean
5. **Small diffs** — keep changes focused *if possible*; prefer refactor over tech debt even if the diff grows

If instructions conflict with other files (e.g., `CONTRIBUTING.md`), follow the **stricter** rule.

For **pipeline behavior changes** (phases, retries/fallback, effect sequencing, checkpoint/resume, reducer/event/effect shape), architecture reading is **REQUIRED**: `docs/code-style/code-shape.md`, `docs/code-style/architecture.md`, `docs/code-style/boundaries.md`, `docs/architecture/event-loop-and-reducers.md`, `docs/architecture/effect-system.md`.

---

## Where The Details Live

| Topic | File |
|-------|------|
| Filesystem I/O rules (Workspace vs `std::fs`) | `docs/agents/workspace-trait.md` |
| Testing strategy, rules, patterns | `docs/agents/testing-guide.md` |
| Required verification commands | `docs/agents/verification.md` |
| Custom lints (dylint), env vars, troubleshooting | `docs/tooling/dylint.md` |
| Code style guide index | `docs/code-style/index.md` |
| Finished-code shape, layer responsibilities | `docs/code-style/code-shape.md` |
| Reducer-driven flow, state/event/effect vocabulary | `docs/code-style/architecture.md` |
| Boundary placement (`domain/`, `io/`, `runtime/`, etc.) | `docs/code-style/boundaries.md` |
| Module organization by stable responsibility | `docs/code-style/module-organization.md` |
| Rust refactor patterns, iterator/fold style | `docs/code-style/coding-patterns.md` |
| Typed errors, diagnostics-as-data | `docs/code-style/errors-and-diagnostics.md` |
| When to use abstractions like `frunk` | `docs/code-style/generics-and-abstractions.md` |
| Layer-appropriate testing patterns and doubles | `docs/code-style/testing.md` |

---

## File Creation Rules

- **NO temporary `.md` files** in root or doc folders.
- **NO new files** in root/doc directories unless explicitly documentation.
- **DO** update outdated documentation when encountered.
- **ALL temporary files go in `tmp/`** at repo root (gitignored); use a unique subdir like `tmp/ralph-workflow-*`.

---

## External Dependencies

Never assume API behavior. Research order: (1) context7, (2) official docs via playwright.

---

## YOLO Mode (CRITICAL)

All agents MUST run with YOLO mode enabled. Ralph is fully automated; all roles write XML to `.agent/tmp/`. Without write permissions, the XSD retry mechanism fails.

**`yolo_flag` in `agents.toml`:** Claude CLI: `--dangerously-skip-permissions` | Aider: `--yes` | Claude Code: no flag needed (granted via environment).

---

## Testing (CRITICAL)

Read `docs/agents/testing-guide.md` before writing or touching any test.

## Workspace Trait (CRITICAL)

Read `docs/agents/workspace-trait.md` before doing any filesystem I/O.

---

## Lint Policy (CRITICAL)

Lint configuration is a repository contract. Never weaken it for convenience or to avoid a refactor. The refactor is always the right answer.

### `#[allow(...)]` — Never. Not Once.

`#[allow(...)]` and `#![allow(...)]` are **prohibited everywhere**, zero exceptions. For lints on code you cannot modify (proc-macro output, external trait impls), use `#[expect(..., reason = "...")]` per the conditions below.

### `#[expect(...)]` — Conditional, With Reason

Permitted **only when ALL three conditions are met:**
1. The lint fires on code you cannot modify (proc-macro output, external trait impls, build-script artifacts).
2. Includes `reason = "..."` naming the specific external source.
3. Narrowest possible scope (item attribute, not module or crate).

```rust
#[expect(clippy::some_lint, reason = "proc-macro output from derive_more")]
```

`#![expect(...)]` (inner attribute) is **always prohibited**. If a lint fires on your code: refactor. If you find an existing `#[allow(...)]`: fix it now — pre-existing violation.

### `.expect()` and `.unwrap()` — Forbidden Except at Documented Sites

Forbidden in production workflow code and integration tests. Permitted only at:

| Location | Justification |
|----------|--------------|
| `test-helpers/src/lib.rs` | Wraps git2/libgit2 C API; `Result` propagation requires harness redesign |
| `xtask/src/main.rs` | Top-level binary entry; no caller to return `Result` to |
| `ralph-gui/src/main.rs` | Tauri framework entry; framework owns `main()` signature |
| Boundary modules (`io/`, `runtime/`) | OS-level calls where failure is unrecoverable |

Everywhere else: use `?`, `map_err`, `and_then`, or proper `Result` propagation. `.expect()` outside these sites is a bug — fix it now.

### Functional Rust Lints — Never Suppress, Never Fake a Boundary

The four dylint lints (`forbid_mut_binding`, `forbid_imperative_loops`, `forbid_mutating_receiver_methods`, `forbid_interior_mutability`) enforce pure functional transformations in domain code. Boundary modules (`io/`, `runtime/`, `ffi/`, `boundary/`) are where mutation genuinely belongs — do not move code there just to silence a lint. See `docs/code-style/code-shape.md`, `docs/code-style/boundaries.md`, `docs/code-style/coding-patterns.md`.

### Other Lint Rules

- If a requested lint exposes existing violations, fix the code — do not disable the rule.
- Do not add new lint suppressions without explicit direction.
- Do not change `clippy.toml` or crate-level lint attributes to work around a failure.

---

## No Dead Code — Ever

Dead code is a liability. `#[allow(dead_code)]` is forbidden. When you find dead code: delete it (preferred), make it used if there is an immediate need, gate behind an active feature flag, or move to `examples/`/`benches/`. Dead code removal is **more important than your current task** — stop and delete it first.

---

## Required Verification (before PR/completion)

> **Note:** Not all tasks require verification — documentation-only changes are an obvious example. Your original prompt may also specify different or additional verification steps. Use judgment and check the prompt.

Run `docs/agents/verification.md`. Passes when all checks complete with **no ERROR/WARNING diagnostics**.

Any failure or ERROR/WARNING → STOP. Fix it. Re-run. Confirm clean. Return to original task.

### Additional verification for metrics changes

When changing iteration/retry/continuation/fallback logic:

```bash
cargo test -p ralph-workflow --lib reducer::state_reduction::tests::metrics
cargo test -p ralph-workflow-tests --test integration_tests iteration_counter
cargo test -p ralph-workflow-tests --test integration_tests continuation_budget
cargo test -p ralph-workflow-tests --test integration_tests summary_consistency
```

### Additional verification for logging changes

When changing per-run logging infrastructure, event loop logging, or log file paths:

```bash
cargo test -p ralph-workflow-tests --test integration_tests logging_per_run
cargo test -p ralph-workflow-tests --test integration_tests event_loop_trace_dump
```

---

## Custom Lints (dylint)

See `docs/tooling/dylint.md`. Autogenerated Rust files may opt out of the `file_too_long` dylint only when the file declares `reason = "autogenerated"` near the top. `cargo xtask verify` treats that marker as informational and prints `[file] has been marked as autogenerated`.
