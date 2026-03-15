# AGENTS.md

---

## UNATTENDED SYSTEM (TOP PRIORITY)

Ralph runs in unattended mode. Agent workflows must never depend on interactive human/operator decisions during pipeline execution.

For commit handling and leftover files:
- Never block waiting for human approval.
- Never silently discard changes.
- Prefer deterministic automatic handling with safe non-destructive defaults.
- If work cannot be finalized in the current commit pass, carry it forward automatically to the next cycle with explicit machine-readable state.

---

## ⚠️ EVERY PRE-EXISTING ISSUE MUST BE FIXED NOW — NO EXCEPTIONS ⚠️

This is the most important rule in this file. Read it. Internalize it. It overrides everything else.

**THE INSTANT ANY ISSUE IS SURFACED — broken test, warning, lint violation, failing check, dead code, stale doc, regression — THAT ISSUE BECOMES YOUR #1 PRIORITY. YOUR ORIGINAL PROMPT IS NOW SECONDARY.**

There is no such thing as "pre-existing." There is no such thing as "not mine." There is no such thing as "unrelated to my task." The moment you see a problem, it is yours. You own it. Fix it now.

**The rule, stated plainly:**

1. You discover an issue — anything: a failing test, a warning, a lint error, dead code, a broken doc link, anything.
2. You STOP what you were doing.
3. Fixing the surfaced issue is now your entire job. The user's original request waits.
4. You fix it completely.
5. You return to the original task.

**Do not:**
- Say "I'll note this for later."
- Say "this is pre-existing and out of scope."
- Say "I didn't cause this."
- File a mental note and keep going.
- Defer to a follow-up task.

**None of those are acceptable responses.** They are rationalizations for shipping brokenness.

### Why pre-existing issues are MORE urgent, not less

A "pre-existing" issue means it has already been tolerated too long. It is not a reason to defer. It is proof that no one has fixed it yet — which means you are now the fix. The longer something has been broken, the more urgently it must be addressed.

Every known failure left unfixed poisons every task that follows it. Continuing past a known issue is an explicit decision to make the codebase worse. That is never acceptable.

### The verification loop

Run `docs/agents/verification.md` before marking any task complete. If verification fails:

- **Stop everything.**
- The original task is suspended.
- Fix the failing check.
- Re-run verification.
- Repeat until clean.
- Only then return to the original task.

There is no "mostly passing." There is no "just one warning." Every ERROR and WARNING must be zero before you are done.

### Pre-existing failures encountered mid-task

If you discover an issue while working on something else:

- Fix it in the same work cycle, immediately.
- Do not batch it for later.
- Do not leave a TODO comment.
- Do not document it and move on.
- **Fix it now, then continue.**

This is not optional. This is not extra credit. It is the first and most fundamental rule of this repository.

---

## Non-Negotiables

- **FIRST RULE (repeated): EVERY issue surfaced during your work MUST be fixed NOW.** No carve-outs. No deferrals. It overrides your current prompt the moment it surfaces.
- **TDD is required for all code changes.** No production code without a failing test first.
- **Verification is required for ANY code change** (prod code or tests): run `docs/agents/verification.md` before PR/completion.
- **Architecture reading is REQUIRED** before any pipeline/reducer/behavioral change: `CODE_STYLE.md` (Architecture), `docs/architecture/event-loop-and-reducers.md`, `docs/architecture/effect-system.md`.
- **Testing guide is REQUIRED reading** before writing/changing tests: `docs/agents/testing-guide.md`.
- **The GUI is Angular v21, not React.** If older prompts, docs, or comments mention React for the GUI, treat that as stale and update the reference to Angular v21 when touching it.
- **Prefer Tailwind over inline CSS styles in the GUI.** Use inline styles only when there is a clear reason they are necessary.
- **Do not introduce tech debt.** If the alternative is adding/keeping tech debt, **prefer refactor** even when it makes the diff larger; do not leave deprecated/unused code behind.
- **Do not change linting rules without explicit direction.** Lint policy is a repository contract, not a convenience setting.
- **Never weaken or disable lint rules just to avoid refactoring.** "Being lazy to refactor" is explicitly forbidden as a reason to change linting behavior.
- **`#[allow(...)]` is never permitted** — one narrow exception only; see Lint Policy below.
- **`.expect()` and `.unwrap()` are forbidden** except at the documented sites; see Lint Policy below.
- **Lint-policy exceptions must stay narrow and documented.** The only permitted `#[allow(...)]` is `#[allow(clippy::large_stack_frames)]` directly preceded by `#[cfg(test)]`.

---

## Skill Usage (CRITICAL)

- **Consolidated rule:** keep skill requirements here; when updating skill workflow guidance, update this section instead of scattering rules elsewhere in this file.
- **Mandatory workflow:** before any meaningful action, agents MUST check whether any skill might apply. This check happens before any response or action, including code edits, debugging, file exploration, planning, implementation, analysis, refactors, documentation lookup, or asking clarifying questions.
- **Questions are tasks:** user questions, codebase exploration, planning, debugging, and "just looking around" all count as tasks and therefore require a skill check first.
- **Default behavior:** use skills liberally and proactively. If there is any reasonable chance a skill is relevant, agents MUST invoke it.
- **No narrow interpretation:** these requirements are minimums, not the full list of valid skill use cases. If a skill is plausibly relevant, invoke it even if the task seems small, obvious, routine, or informational.
- **Prefer false positives over misses:** it is better to use a skill and not need it than to skip a skill that would have improved the work.
- **Do not rationalize skipping:** thoughts like "this is simple," "I know this already," "I'll inspect files first," "I'll gather context first," "this is just a question," or "this probably doesn't need a skill" are not valid reasons to skip skill invocation.
- **Uncertainty rule:** when uncertain, agents MUST invoke the most likely applicable skill first and may skip a skill only when it is clearly inapplicable.
- **Major feature or bug fix:** use the `test-driven-development` skill before changing code.
- **Debugging:** use the `systematic-debugging` skill before proposing or applying fixes.
- **Angular and GUI work:** use the Angular MCP server and the `frontend-angular` skill first for implementation, debugging, analysis, refactors, and documentation lookup.
- **Styling and visual design:** use the `frontend-design` skill for any styling work, visual polish, layout design, or UI presentation changes.
- **Enforcement mindset:** treat failure to use an applicable skill as a process failure, even if the resulting code or answer would otherwise be acceptable.

This repository welcomes automated code assistants ("agents") and human contributors.
Follow these rules so changes stay safe, consistent, and easy to review.

---

## FORBIDDEN GIT COMMANDS (CRITICAL — NO EXCEPTIONS)

**YOU ARE STRICTLY PROHIBITED from running ANY git command that writes, modifies history, or changes repository state.**

Ralph is the ONLY entity allowed to commit. Accidental commits break the deterministic pipeline and cannot be automatically undone.

### NEVER run these commands (not exhaustive — when in doubt, do NOT run it):

- `git commit` — Ralph orchestrates ALL commits
- `git push` — Ralph orchestrates ALL pushes
- `git tag` — Ralph orchestrates ALL tagging
- `git merge` — Ralph controls branching strategy
- `git rebase` — Ralph controls history (use rebase effects only)
- `git reset --hard` — Destroys uncommitted work irreversibly
- `git reset --soft` / `--mixed` — Modifies commit history
- `git checkout -- .` / `git restore .` — Destroys uncommitted changes
- `git stash drop` / `git stash pop` / `git stash apply` — Can overwrite or destroy work
- `git branch -D` / `git branch -d` — Destroys branches
- `git clean -f` / `-fd` / `-fx` — Destroys untracked files
- `git cherry-pick` — Modifies history
- `git revert` — Modifies history
- `git am` / `git apply` — Modifies working tree in uncontrolled way
- `git add` — Ralph orchestrates ALL staging
- `git init` — Creating git repositories during agent phase is forbidden

### ONLY these git commands are allowed (read-only, non-destructive):

- `git status` — check working tree state
- `git log` — view commit history
- `git diff` — view changes (unstaged/staged)
- `git show` — inspect a commit or object
- `git branch` (list only, no `-D`/`-d`) — list branches
- `git remote -v` — view remote URLs
- `git stash list` — list stashes (do NOT pop/apply/drop)
- `git rev-parse` — resolve refs and paths
- `git ls-files` — list tracked files
- `git describe` — describe a commit

### Enforcement

Git hooks (pre-commit, pre-push, pre-merge-commit) and a PATH wrapper are installed automatically by Ralph during the agent phase. If you attempt a forbidden command, it WILL be blocked with exit code 1 and a message like: "blocked (agent phase): agent protections active."

**Do not attempt to bypass these hooks.** If you need a commit, write your changes to files and let Ralph's commit effect handle it.

### FORBIDDEN MCP/TOOL USAGE (CRITICAL)

**MCP git tools are equivalent to CLI commands and are EQUALLY PROHIBITED.** MCP servers bypass the PATH wrapper, but hooks and HEAD OID comparison will still detect and block unauthorized commits.

You MUST NEVER use these MCP tools:

- `mcp__git__git_commit` — commits are orchestrated ONLY by Ralph
- `mcp__git__git_add` — staging is orchestrated ONLY by Ralph
- `mcp__git__git_push` — pushes are orchestrated ONLY by Ralph
- `mcp__git__git_reset` — destroys history or uncommitted work
- `mcp__git__git_checkout` (with `--` flag) — destroys uncommitted changes
- `mcp__git__git_stash` (except list) — can overwrite or destroy work
- `mcp__git__git_merge` — Ralph controls branching strategy
- `mcp__git__git_init` — creating git repositories is forbidden
- `mcp__git__git_create_branch` — Ralph controls branching strategy

**The prohibition applies to ALL mechanisms of invoking git operations:** CLI commands, MCP tools, direct library calls, subprocess spawning with absolute paths.

### ADDITIONAL PROHIBITED ACTIONS — HOOKS AND MARKERS (CRITICAL)

**Deleting or modifying git hooks or files in `.git/ralph/` IS equivalent to an unauthorized commit. It WILL be detected and Ralph will treat it as a security violation.**

You MUST NEVER:

- Delete or modify `.git/hooks/pre-commit` — this hook blocks unauthorized commits
- Delete or modify `.git/hooks/pre-push` — this hook blocks unauthorized pushes
- Delete or modify `.git/hooks/pre-merge-commit` — this hook blocks unauthorized merge commits
- Delete or modify files in `.git/ralph/` — this directory holds enforcement state (marker, wrapper track file, head OID)
- Use an absolute path (e.g., `/usr/bin/git`, `/opt/homebrew/bin/git`) to invoke git directly and bypass the PATH wrapper
- Delete or modify any file in `.git/hooks/` that contains `RALPH_RUST_MANAGED_HOOK`
- Modify the `PATH` environment variable to remove or reorder Ralph's git wrapper directory
- Use `rm`, `unlink`, or any file deletion command on `.git/hooks/` files or `.git/ralph/` files
- Write to or overwrite `.git/hooks/pre-commit`, `.git/hooks/pre-push`, or `.git/hooks/pre-merge-commit` files
- Run `chmod` on `.git/hooks/` files or `.git/ralph/` files to change permissions
- Run `git init` to reinitialize the repository or create a new one
- Use `exec`, `env`, `command`, or other shell builtins to invoke git bypassing the PATH wrapper
- Set `GIT_DIR`, `GIT_WORK_TREE`, or `GIT_EXEC_PATH` environment variables — the wrapper unsets these automatically

**CONSEQUENCES:** Any bypass attempt is logged as a security violation. Ralph will detect the tampering before the next agent run, reinstall all protections, and report the violation. The agent run will be considered a failure.

**Why this matters:**
- Ralph reinstalls hooks before every agent invocation — tampering will be detected immediately
- The `.git/ralph/` enforcement state is recreated before every agent invocation
- `.git/ralph/` lives inside git metadata, not tracked repo code, so it will not appear in `git status` or normal working-tree scans
- Ralph's deterministic pipeline depends on ALL commits going through its commit effect
- A commit made outside Ralph cannot be undone automatically and breaks the review pipeline

**What to do instead:**
- Write changes to files as normal — Ralph's commit effect will commit them at the right time
- If you believe a commit is needed, write the required file changes and let Ralph orchestrate the commit
- NEVER attempt to commit, even if you think the hooks are "in the way"

### WHY BYPASS IS FUTILE — DO NOT WASTE YOUR EXECUTION BUDGET

1. The `.git/ralph/no_agent_commit` marker is recreated before EVERY agent invocation. Deleting it is pointless.
2. All git hooks (pre-commit, pre-push, pre-merge-commit) are reinstalled before EVERY agent invocation.
3. Hook and marker permissions are verified and restored before EVERY agent invocation. Using `chmod` is pointless.
4. The PATH wrapper unsets `GIT_DIR`, `GIT_WORK_TREE`, and `GIT_EXEC_PATH` when the marker exists.
5. The PATH wrapper script is read-only (0o555). Overwriting it requires `chmod` first, which is logged.
6. ALL tampering is detected, logged as a security violation, and reported. The agent run will be flagged as compromised.
7. Even if a commit somehow succeeds via MCP tools or absolute paths, Ralph detects the unauthorized commit by comparing HEAD OID before and after each agent run. The pipeline FAILS and the agent run is flagged as compromised.
8. Every bypass attempt wastes tokens from your execution budget with ZERO chance of success.

---

## Priorities (in order)

1. **Fix surfaced issues** — any issue discovered mid-task becomes priority zero immediately
2. **Correctness** — tests pass, behavior matches intent
3. **Maintainability** — clear code, no magic
4. **Consistency** — follow existing patterns, rustfmt/clippy clean
5. **Small diffs** — keep changes focused *if possible*; if the alternative is adding/keeping tech debt, **prefer refactor** even when it makes the diff larger

If instructions conflict with other files (e.g., `CONTRIBUTING.md`), follow the **stricter** rule.

See **[CODE_STYLE.md](CODE_STYLE.md)** for design principles and testing philosophy.

If you change **pipeline behavior** (phases, retries/fallback, effect sequencing, checkpoint/resume, or any reducer/event/effect shape), the reducer/effect architecture reading is **REQUIRED**: `CODE_STYLE.md` (Architecture section), `docs/architecture/event-loop-and-reducers.md`, `docs/architecture/effect-system.md`.

---

## Where The Details Live

- Filesystem I/O rules (Workspace vs `std::fs`, exceptions): `docs/agents/workspace-trait.md`
- Testing strategy, rules, and patterns (all tiers): `docs/agents/testing-guide.md`
- Required verification commands (no ERROR/WARNING output): `docs/agents/verification.md`
- Custom lints (dylint), env vars, troubleshooting: `docs/tooling/dylint.md`

---

## File Creation Rules

- **NO temporary .md files** in root or doc folders
- **NO new files** in root/doc directories unless explicitly about documentation
- **DO** update outdated documentation when encountered
- **ALL temporary files MUST go in `tmp/` at the repo root** (gitignored); use a unique subdir like `tmp/ralph-workflow-*` if needed

---

## External Dependencies

Never assume API behavior. Research order:
1. Use context7
2. If that fails, check official docs via playwright

---

## YOLO Mode (CRITICAL)

All agents MUST run with YOLO mode enabled to allow automated file operations.

**Why:** Ralph is a fully automated pipeline. All roles (Developer, Reviewer, Commit) write XML to `.agent/tmp/`. Without write permissions, the XSD retry mechanism fails.

**Configuration:** Every agent needs `yolo_flag` in `agents.toml`:
- Claude CLI: `--dangerously-skip-permissions`
- Aider: `--yes`
- Claude Code: No CLI flag needed (permissions granted via environment)

---

## Testing (CRITICAL)

Read `docs/agents/testing-guide.md` before writing or touching any test.

---

## Workspace Trait (CRITICAL)

Read `docs/agents/workspace-trait.md` before doing any filesystem I/O.

---

## Lint Policy (CRITICAL)

Lint configuration is a repository contract. It must not be weakened for convenience, laziness, or to avoid a refactor. The refactor is always the right answer.

### `#[allow(...)]` — Never. Not Once.

**`#[allow(...)]` and `#![allow(...)]` are prohibited everywhere in this codebase**, with one machine-verified exception:

```rust
#[cfg(test)]
#[allow(clippy::large_stack_frames)]
mod tests;
```

This is the only permitted form. It exists because the Rust test harness generates stack frames that trip the lint. It requires `#[cfg(test)]` on the immediately preceding line and is verified by `xtask verify`. Any other `#[allow(...)]` will fail the `forbidden-allow-expect-scan` check.

**`#[expect(...)]` is equally banned.** It is suppression syntax and carries the same prohibition.

If a lint fires on your code:
- **Refactor the code.** The lint is telling you something is wrong with the structure.
- Do not suppress. Do not argue inline. If you believe the lint rule itself is wrong, raise it as a policy discussion — never suppress ad hoc.

If you encounter an existing `#[allow(...)]` outside the documented exception: **fix it now.** It is a pre-existing issue. See the top of this file.

### `.expect()` and `.unwrap()` — Forbidden Except at Documented Sites

These are forbidden in production workflow code and integration tests. Permitted only at:

| Location | Justification |
|----------|--------------|
| `test-helpers/src/lib.rs` | Wraps git2/libgit2 C API; cannot propagate `Result` without redesigning the harness |
| `xtask/src/main.rs` | Top-level binary entry point; no caller to return `Result` to |
| `ralph-gui/src/main.rs` | Tauri framework entry point; framework owns `main()` signature |
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

```bash
# Metrics unit tests
cargo test -p ralph-workflow --lib reducer::state_reduction::tests::metrics

# Metrics integration tests
cargo test -p ralph-workflow-tests --test integration_tests iteration_counter
cargo test -p ralph-workflow-tests --test integration_tests continuation_budget
cargo test -p ralph-workflow-tests --test integration_tests summary_consistency
```

### Additional verification for logging changes

When changing per-run logging infrastructure, event loop logging, or log file paths:

```bash
# Per-run logging infrastructure tests
cargo test -p ralph-workflow-tests --test integration_tests logging_per_run

# Event loop trace dump tests
cargo test -p ralph-workflow-tests --test integration_tests event_loop_trace_dump
```

All tests must pass with no ERROR/WARNING diagnostics (informational output is acceptable).

---

## Custom Lints (dylint)

See `docs/tooling/dylint.md`.

Autogenerated Rust files may opt out of the `file_too_long` dylint only when the file itself
declares `reason = "autogenerated"` near the top of the file. `cargo xtask verify` treats that
marker as informational output and prints `[file] has been marked as autogenerated` instead of
failing on the generated-file exemption itself.
