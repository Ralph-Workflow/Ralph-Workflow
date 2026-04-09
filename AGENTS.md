# AGENTS.md

## > [!IMPORTANT]
> 
THERE ARE OTHER DEVELOPERS WORKING ON THIS, DO NOT REVERT THEIR CHANGES WORK WITH THEM

## Context — Re-read When Needed
- **Current plan:** `.agent/PLAN.md`
- **Overall objective:** `PROMPT.md`

THERE ARE OTHER DEVELOPERS WORKING ON THIS, DO NOT REVERT THEIR CHANGES WORK WITH THEM
---

## 🚨 TOP PRIORITY: Fix ALL Pre-existing Issues Immediately UNLESS YOU WERE TOLD NOT TO 
It does not matter if something is Pre-existing **Every surfaced issue becomes your #1 priority immediately.** 
No exceptions, no "pre-existing" carve-outs UNLESS YOUR PROMPT HAS ONE THAT SPECIFICALLY SAYS IT, ASSUME NO CARVE-OUTS IF PROMPT DOES NOT SPECIFY

1. Discover any issue → STOP → Fix it completely → Verify clean → Return to original task.

Run `docs/agents/verification.md` before completion. Zero ERROR/WARNING required.

---

## Git: User-Directed Only
**By default, keep git usage read-only. If the current user prompt directly and explicitly requests a git operation, you may perform that specific operation.**

| Allowed by default | Allowed only when directly requested in the user prompt | Still forbidden unless the user explicitly asks for them |
|---------|-----------|-----------|
| `git status`, `git log`, `git diff`, `git show`, `git branch` (list), `git remote -v` | `git add`, `git commit`, `git push`, `git merge`, `git rebase`, `git stash`, `git cherry-pick`, `git revert` | destructive or high-risk git commands such as `git reset --hard`, `git clean`, `git branch -D`, or equivalent force operations |

**MCP git tools follow the same rule.** Only perform the exact git operation the user directly asked for, and do not broaden that permission to other git actions. Hook/marker tampering remains a security violation.

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
| Remote build | `docs/tooling/remote-build.md` |

---

## FORBIDDEN GIT COMMANDS (CRITICAL — NO EXCEPTIONS)

**YOU ARE STRICTLY PROHIBITED from running ANY git command that writes, modifies history, or changes repository state.**

Ralph Workflow is the ONLY entity allowed to commit. Accidental commits break the deterministic pipeline and cannot be automatically undone.

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

```bash
# Metrics unit tests
cargo xtask test -p ralph-workflow --lib reducer::state_reduction::tests::metrics

# Metrics integration tests
cargo xtask test -p ralph-workflow-tests --test integration_tests iteration_counter
cargo xtask test -p ralph-workflow-tests --test integration_tests continuation_budget
cargo xtask test -p ralph-workflow-tests --test integration_tests summary_consistency
```

### Additional verification for logging changes

When changing per-run logging infrastructure, event loop logging, or log file paths:

```bash
# Per-run logging infrastructure tests
cargo xtask test -p ralph-workflow-tests --test integration_tests logging_per_run

# Event loop trace dump tests
cargo xtask test -p ralph-workflow-tests --test integration_tests event_loop_trace_dump
```

All tests must pass with no ERROR/WARNING diagnostics (informational output is acceptable).

---

## Custom Lints (dylint)

See `docs/tooling/dylint.md`.

Autogenerated Rust files may opt out of the `file_too_long` dylint only when the file itself
declares `reason = "autogenerated"` near the top of the file. `cargo xtask verify` treats that
marker as informational output and prints `[file] has been marked as autogenerated` instead of
failing on the generated-file exemption itself.
