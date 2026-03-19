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
