# Issues and Gotchas

## 2026-03-19 — Plan Creation

### Dylint work is ongoing in parallel — do not touch lints/ralph_lints/
Other developers are adding new lints concurrently. Any new lint may reveal new violations.
Treat new violation categories as additional diagnostic signals — investigate and fix the
underlying architectural problem. Do not mechanically chase down error counts.

### Compiler errors block dylint from running cleanly
E0255 in files/mod.rs (duplicate module definitions), E0599 on EventTraceBuffer, E0282 type
inference failures. These must be fixed first (Phase 1) before the rest can be audited.

### Phase 2 boundary restructuring is the riskiest phase
Moving 55+ files out of boundary directories into domain modules touches a lot of code.
Important: do NOT just move files — audit what is pure vs effectful in each, then:
- Pure logic → new/existing domain module
- Effect wiring only → stays in boundary, flattened to a single file
If unsure what is pure, write a test for it with plain values. If the test works without
fakes/mocks, it is pure.

### Some violations may be in legitimately boundary-located code
The forbid_domain_boundary_dependencies lint fires when non-boundary code imports from
boundary modules. After Phase 2, some of these will disappear naturally. Before fixing
any individual violation, confirm the file is genuinely domain code (not itself in a
boundary path) before applying the Reader pattern.

### git_helpers/ mixes git2 FFI with pure domain logic
git_helpers/config_state.rs has ~85 .unwrap() calls and likely mixes git command execution
with pure state interpretation. Phase 8 must be done carefully: read the module fully before
splitting. Pure = parsing git output strings. Effectful = executing git commands, reading .git/.

### No existing coverage tool — cannot measure current baseline
There is no tarpaulin/llvm-cov configuration. Phase 13 adds this. Until then, coverage
assessment is qualitative (read the tests, count modules with zero #[cfg(test)] blocks).

## 2026-03-19 — EventTraceBuffer push/flush fixes

### Problem
`EventTraceBuffer` was changed from mutable (`.push(&mut self, ...)`) to immutable builder
(`append(self, entry) -> Self`). Call sites still used `.push()`.

Additionally `flush_stdout()` in `effect_io.rs` was missing `use std::io::Write` inside
the function body, causing E0599 on `Stdout::flush`. And `terminal.rs` called `.as_str()`
on a `&'static str` (return type of `Colors::reset()`), triggering unstable `str_as_str`.

### Fix applied
- `effect_io.rs`: Added `use std::io::Write;` inside `flush_stdout()` body
- `terminal.rs`: Removed `.as_str()` on `colors.reset()` — it already returns `&str`
- `recovery.rs` + `runtime/mod.rs`: All `trace.push(entry)` replaced with
  `*trace = std::mem::replace(trace, EventTraceBuffer::new(1)).append(entry);`
  (and the `runtime.trace` variant with `&mut runtime.trace`)

### Pattern for future append-consuming calls on &mut T
When an API uses consuming builder (`fn foo(self, ...) -> Self`) and you only have
`&mut T`, use: `*t = std::mem::replace(t, T::placeholder()).foo(...)` where `placeholder`
is a cheap-to-create sentinel value.

## 2026-03-19 — orchestration.rs dead code cleanup

**File:** `ralph-workflow/src/app/rebase/orchestration.rs`

**Problem:** 15 private functions (record_rebase_start, save_pre_rebase_checkpoint,
handle_rebase_success, handle_rebase_noop, handle_rebase_conflicts, record_conflict_detected,
save_conflict_checkpoint, handle_conflicts_resolved, handle_resolution_failed,
handle_resolution_error, handle_rebase_failed, handle_rebase_error,
save_post_rebase_checkpoint, create_checkpoint_builder, read_repo_head_or_unknown)
were defined but never called from anywhere in the codebase. Two of them
(handle_conflicts_resolved lines 323/344) also had #[must_use] violations because
add_step_bounded follows the consuming builder pattern (returns Self) but the
return was discarded — all within dead code.

**Fix:** Deleted all 15 dead private functions. Cleaned up all now-unused imports
(ConflictResolutionContext, ExecutionStep, StepOutcome, save_checkpoint_with_workspace,
CheckpointBuilder, PipelinePhase, RebaseState, abort_rebase, continue_rebase,
get_conflicted_files, RebaseErrorKind, Workspace, std::collections::HashMap).

**Resolution:** `cargo check -p ralph-workflow --lib 2>&1 | grep "^error" | grep -v "could not compile"` returns nothing.

**Note:** The consuming builder pattern (add_step_bounded takes self, returns Self) requires
callers to use the return value. Other live call sites in the file correctly use `let _ = ...`
to explicitly discard it when mutation is the intent. This pattern mismatch (mutable field
vs consuming builder) is worth tracking — if these functions are ever revived, they'll need
`phase_ctx.execution_history = phase_ctx.execution_history.add_step_bounded(...)`.

## 2026-03-19 — 18:27 — Phase 3 forbid_domain_boundary_dependencies Audit

### Summary
Exactly ONE `forbid_domain_boundary_dependencies` violation found in current repo state.

### Violation Details

**File:** `/Users/mistlight/Projects/RalphWithReviewer/wt-68-build-system/ralph-workflow/src/lib.rs:178:5`

**Location within file:**
```rust
// Lines 177-185
mod executor_reexports_boundary {
    pub use crate::executor::{
        AgentChild, AgentChildHandle, AgentCommandResult, AgentSpawnConfig, ChildProcessInfo,
        ProcessExecutor, ProcessOutput, RealAgentChild, RealProcessExecutor,
    };

    #[cfg(any(test, feature = "test-utils"))]
    pub use crate::executor::{MockAgentChild, MockProcessExecutor};
}
```

**Offending import:** `pub use crate::executor::{...}` — importing from boundary module `executor`

**Why it triggers the lint:**
- The `lib.rs` file is the crate root (not a boundary module)
- The inline module `executor_reexports_boundary` is defined in `lib.rs`, NOT in a boundary directory
- Even though the module name contains "boundary", it's not actually located in a recognized boundary path (`io/`, `runtime/`, `ffi/`, `boundary/`, `executor/`)
- The lint correctly detects that non-boundary code is importing from the `executor` boundary module

**Matching plan checkbox:** P3-remaining-boundary (or could be considered P3-workflow-* since it's in lib.rs)

### Root Cause Analysis
The re-export pattern was likely intended to make executor types available at the crate root for dependency injection. However, the module containing these re-exports is not physically located in a boundary directory, so the lint treats it as non-boundary code importing from a boundary module.

### Verification Command
```bash
cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet 2>&1 | grep "import from boundary module"
```
Returns exactly 1 line with this violation.

### Note
All other Phase 3 boundary import violations have already been resolved in prior work. The repo is very close to Phase 3 completion with only this single remaining issue.

## 2026-03-19T12:30Z — Interrupt runtime re-export fix

- **Problem:** `interrupt/io_tests.rs` imported `crate::interrupt::runtime::INTERRUPT_CONTEXT`, but the interrupt module now keeps its implementation inside a `handling` module (`#[path = "runtime.rs"] mod handling`). There was no `runtime` re-export, so the test failed with `E0432: unresolved import`.
- **Fix:** Added a `pub(crate) mod runtime { pub use super::handling::INTERRUPT_CONTEXT; }` wrapper so the runtime namespace exposes only the constant that tests still need, avoiding the previous wildcard import while keeping the implementation inside the boundary module.

## 2026-03-19T12:45Z — Interrupt runtime shim unused import

- **Problem:** Making the runtime shim always available introduced a wildcard re-export in `interrupt/mod.rs` that the non-test build flagged as `unused import` under `#![deny(warnings)]`. The warning was triggered because only the `io_tests` target uses the runtime namespace.
- **Fix:** Guarded the runtime re-export with `#[cfg(test)]` so the constant is exposed only under test builds. This keeps `crate::interrupt::runtime::INTERRUPT_CONTEXT` available where needed while avoiding unused import warnings during the normal build.
