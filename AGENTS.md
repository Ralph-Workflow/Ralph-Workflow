# AGENTS.md

## UNATTENDED SYSTEM (TOP PRIORITY)

Ralph runs in unattended mode. Agent workflows must never depend on interactive human/operator decisions during pipeline execution.

For commit handling and leftover files:
- Never block waiting for human approval.
- Never silently discard changes.
- Prefer deterministic automatic handling with safe non-destructive defaults.
- If work cannot be finalized in the current commit pass, carry it forward automatically to the next cycle with explicit machine-readable state.

ALWAYS USE test-driven-development!

## Non-Negotiables

- **TDD is required for all code changes.** No production code without a failing test first.
- **Verification is required for ANY code change** (prod code or tests): run `docs/agents/verification.md` before PR/completion.
- **Architecture reading is REQUIRED** before any pipeline/reducer/behavioral change: `CODE_STYLE.md` (Architecture), `docs/architecture/event-loop-and-reducers.md`, `docs/architecture/effect-system.md`.
- **Testing guide is REQUIRED reading** before writing/changing tests: `docs/agents/testing-guide.md`.
- **Do not introduce tech debt.** If the alternative is adding/keeping tech debt, **prefer refactor** even when it makes the diff larger; do not leave deprecated/unused code behind.

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

1. **Correctness** - tests pass, behavior matches intent
2. **Maintainability** - clear code, no magic
3. **Consistency** - follow existing patterns, rustfmt/clippy clean
4. **Small diffs** - keep changes focused *if possible*; if the alternative is adding/keeping tech debt, **prefer refactor** even when it makes the diff larger

If instructions conflict with other files (e.g., `CONTRIBUTING.md`), follow the **stricter** rule.

See **[CODE_STYLE.md](CODE_STYLE.md)** for design principles and testing philosophy.

If you change **pipeline behavior** (phases, retries/fallback, effect sequencing, checkpoint/resume, or any reducer/event/effect shape), the reducer/effect architecture reading is **REQUIRED**: `CODE_STYLE.md` (Architecture section), `docs/architecture/event-loop-and-reducers.md`, `docs/architecture/effect-system.md`.

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
- OpenCode: No CLI flag (uses `OPENCODE_PERMISSION` env var instead)
- Claude Code: Usually no flag needed

---

## Testing (CRITICAL)

Read `docs/agents/testing-guide.md` before writing or touching any test.

---

## Workspace Trait (CRITICAL)

Read `docs/agents/workspace-trait.md` before doing any filesystem I/O.

---

## No `#[allow(dead_code)]`

**Never** introduce or keep `#[allow(dead_code)]` (or deprecated code). Consider Dead Code removal to be EVEN MORE IMPORTANT! than the actual current task. It is very important no dead code exists.

Handle dead code by:
- Making it used
- Implementing the feature now (no time constraints)
- Gating behind a feature flag
- Moving to `examples/` or `benches/`
- Deleting it

---

## Required Verification (before PR/completion)

Run the commands in `docs/agents/verification.md`. Verification passes when required checks complete successfully with **no ERROR/WARNING diagnostics** (informational output is acceptable).

### YOU MUST FIX ALL FAILURES - NOT JUST YOUR OWN

**CRITICAL:** When verification fails, you MUST fix ALL failures before committing - even if they are pre-existing issues you did not introduce.

**Pre-existing failures become YOUR TOP PRIORITY.** Drop whatever you were doing. The user's original prompt is now secondary. Fix the verification failures FIRST.

**Why pre-existing failures are MORE urgent:**
- They've been rotting longer - every day they exist makes them harder to fix
- Someone else skipped verification - you must not perpetuate the failure
- If you don't fix it now, the next contributor inherits the mess
- **The longer a failure exists, the more urgent it becomes**

**The rule is simple:**
1. Run verification
2. If any command fails or emits ERROR/WARNING diagnostics → STOP EVERYTHING
3. Fixing failures is now your #1 priority - above the user's original request
4. Do not rationalize ("not my change", "pre-existing", "unrelated to my task")
5. Do not defer ("I'll file an issue", "someone else should fix it")
6. FIX IT NOW, then return to the original task

**If you encounter pre-existing failures:**
- Fix them as part of your commit (note in commit message: "Also fixes pre-existing...")
- This is not optional. This is not extra credit. This is the job.
- **Investigate thoroughly** - if you didn't make the change, look even harder because it's been broken longer

**Additional verification for metrics changes:**

When changing iteration/retry/continuation/fallback logic, run the metrics tests to ensure metrics remain accurate and no drift occurs:

```bash
# Metrics unit tests
cargo test --lib reducer::state_reduction::tests::metrics

# Metrics integration tests
cargo test --test '*' iteration_counter
cargo test --test '*' continuation_budget
cargo test --test '*' summary_consistency
```

All tests must pass with no ERROR/WARNING diagnostics (informational output is acceptable).

**Additional verification for logging changes:**

When changing per-run logging infrastructure, event loop logging, or log file paths, run the logging tests to ensure the logging system remains correct:

```bash
# Per-run logging infrastructure tests
cargo test --test '*' logging_per_run

# Event loop trace dump tests
cargo test --test '*' event_loop_trace_dump
```

All tests must pass with no ERROR/WARNING diagnostics (informational output is acceptable).

---

## Custom Lints (dylint)

See `docs/tooling/dylint.md`.
