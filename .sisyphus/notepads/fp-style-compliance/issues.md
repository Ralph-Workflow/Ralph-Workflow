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
