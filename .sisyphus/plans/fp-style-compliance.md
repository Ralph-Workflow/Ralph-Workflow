# FP Style Compliance

## Overview

This plan brings `ralph-workflow` into genuine compliance with the code style guide and its
functional programming principles. The goal is **real architectural quality** — pure domain code,
thin flat boundaries, explicit effects, typed failures, testable design. Dylint is a diagnostic
tool, not the definition of success.

## Latest Checkpoint (2026-03-22)

- `cargo check -p ralph-workflow --lib` ✅ clean
- `cargo test -p ralph-workflow --lib` ✅ 3901 passing, 0 failures
- R3 target violations resolved: `app/boundary/conflict_resolution.rs` and `app/boundary/rebase_conflict_resolution.rs`
  — `boundary_function_too_complex` eliminated by extracting helpers and splitting complex functions.
- R4 target violations resolved: `files/agent_files.rs` (`forbid_io_effects`), `files/integrity/mod.rs` (`forbid_read_clock`),
  `files/llm_output_extraction/file_based_extraction.rs` (`forbid_io_effects`) — boundary `io.rs` modules introduced.
- Bonus fix: `xsd_validation_plan/validation/main_validator.rs` — self-closing `<skills-mcp/>` bug fixed (missing recursive
  call after `Event::Empty` handler was short-circuiting plan parse).
- R6 gate complete: `cargo xtask verify` passes all 10 checks clean.
- Fixed formatting issues in `executor/real.rs` and `executor/executor_trait.rs` (`SpawnedProcess` struct init multi-line form).
- Added `try_wait` and `kill` delegation methods to `SpawnedProcess` in `executor_trait.rs`.
- Fixed `KillNotifyingExecutor::spawn` in `tests/integration_tests/timeout_file_activity.rs` to return `SpawnedProcess` instead of `std::process::Child`.
- Recovery Reset checklist now fully complete (R1–R6 all `[x]`). Ready for Final Verification Wave.

### Recovery Reset (mandatory before Final Verification Wave)

The plan is reopened. Do **not** enter Final Verification Wave until the recovery checklist below is complete.

- [x] **R1-rebaseline-dylint**: Capture a fresh full dylint log to file and summarize by lint category + top files.
  Use this as the source-of-truth backlog for recovery slices.

- [x] **R2-reader-boundary-imports**: Eliminate remaining `forbid_domain_boundary_dependencies` errors
  (starting with `ralph-workflow/src/agents/opencode_api/mod.rs` and any other current-tree regressions).

- [x] **R3-app-mut-loop-cluster**: Burn down `app/` cluster (`effectful.rs`, `pipeline_setup.rs`, `plumbing.rs`,
  `core.rs`, `runner/pipeline_execution/**`) by converting pure-domain mutation/loops to value transformations
  or relocating true effect code to boundary modules.

- [x] **R4-files-mut-loop-cluster**: Burn down `files/` cluster (`monitoring.rs`, `protection/validation/helpers.rs`,
  `llm_output_extraction/xml_*`) with the same rule: pure logic stays domain and becomes transformation-based;
  true I/O loops move to boundary seams.

- [x] **R5-checkpoint-compression-cluster**: Resolve `checkpoint/` mut/loop violations in
  `execution_history/compression.rs`, `state/serialization.rs`, and `validation.rs` using IMPURE→PURE→IMPURE split.

- [x] **R6-reverify-gate**: Re-run all required verification (`cargo check -p ralph-workflow --lib`,
  `cargo test -p ralph-workflow --lib`, `cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet`,
  and `cargo xtask verify`) and only then resume Final Verification Wave items.

**Critical constraints:**
- Do NOT modify `lints/ralph_lints/`. Dylint lints are being developed in parallel.
- Do NOT move pure-but-imperative code into `boundary/` to silence lints. That is explicitly
  called out as an anti-pattern in `docs/code-style/boundaries.md`.
- After any task, re-run `cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet`
  and use its output as a compass: investigate every remaining signal.

**Required reading before ANY task:**
- `docs/code-style/boundaries.md` — normative boundary guide
- `docs/code-style/functional-transformations.md` — FP cookbook
- `docs/code-style/module-organization.md` — module layout rules
- `docs/code-style/architecture.md` — State→Orchestrator→Effect→Handler→Event→Reducer→State
- `docs/code-style/errors-and-diagnostics.md` — errors and diagnostics as values
- `docs/tooling/dylint.md` — what each lint checks and its known heuristic limitations

---

## How To Use Dylint As A Compass (Not A Ruler)

The lints have two severity levels. Understand the difference before touching any code.

### DENY lints — hard architectural rules, always block the build

| Lint | What it enforces |
|------|-----------------|
| `forbid_nested_boundary_modules` | Boundary directories must be flat — no subdirectory trees inside `io/`, `runtime/`, `ffi/`, `boundary/`, `executor/`, and recognised adapter dirs |
| `forbid_domain_boundary_dependencies` | Non-boundary code must never `use`-import from boundary modules |
| `forbid_boundary_policy_calls` | Boundary code must never call reducer/orchestrator policy helpers directly |
| `forbid_boundary_retry_loops` | Boundary code must not own retry policy in a loop |
| `forbid_result_swallowing` | `let _ = result`, `.ok()` on Result, silent `if let Err` are forbidden |
| `file_too_long` | Source files must stay under 1000 lines |

If a DENY lint fires, the code has a genuine architectural violation. Find and fix the root cause.
Do not suppress. Do not rename a module to avoid path matching.

### WARN lints — style heuristics, need judgment

| Lint | What it approximates | Known limitation |
|------|---------------------|-----------------|
| `forbid_mut_binding` | Detects `let mut` bindings | Pattern-based; cannot prove the binding escapes or mutates shared state |
| `forbid_imperative_loops` | Detects `for`/`while`/`loop` | Pattern-based; cannot prove the loop has side effects |
| `forbid_interior_mutability` | Detects interior-mutability types | Type-based; cannot prove mutation actually occurs |
| `forbid_mutating_receiver_methods` | Detects `&mut self` calls | Type-based; cannot prove the method mutates semantically important state |
| `forbid_terminal_output` | Detects `println!`/`eprintln!` | Pattern-based; cannot prove output reaches the user |
| `forbid_io_effects` | Detects `std::fs`, `std::env`, etc. | Path-based; cannot trace re-exports |
| `forbid_raw_effect_types_in_public_apis` | Detects raw capability types in pub APIs | v1 heuristic |
| `boundary_function_too_complex` | Detects complex boundary functions | Threshold-based; complexity is a proxy for mixing concerns |

When a WARN lint fires, investigate: is this a genuine style violation or a false positive?
- Genuine violation → apply the style principle, fix the code
- False positive on correct code → add a code comment explaining why the code is correct;
  do NOT suppress with `#[expect(...)]` without a clear justification

Currently, `#![deny(warnings)]` in `ralph-workflow` promotes all warnings to build errors.
This does NOT change what the lint is measuring — it only changes when you find out about it.

### The one rule that overrides everything

> Never move code into `boundary/` or any boundary-named module to silence a lint.
> 
> Boundary modules are for **real effect seams**: functions that perform actual I/O, spawn
> real processes, read environment variables, write to files, make network calls. Moving
> pure-but-imperative code there to avoid `forbid_mut_binding` or `forbid_imperative_loops`
> is explicitly forbidden by `docs/code-style/boundaries.md`.

---

## Current Compliance Gaps — Where The Project Falls Short

These are architectural problems. The lint counts are an approximate location guide.

### Gap 1 — Boundary architecture has collapsed into a workflow engine

`boundary/` contains nested workflow sub-trees. Handlers must be **flat** per
`docs/code-style/module-organization.md` — one file per effect, not a nested tree.

```
boundary/commit/          (7 files) — commit workflow logic inside effect seam
boundary/development/     (6 files) — dev workflow logic inside effect seam
boundary/planning/        (6 files) — planning workflow logic inside effect seam
boundary/review/          (9 files) — review/fix logic inside effect seam

claude/delta_handling/    (5 files) — pure parsing logic inside agent adapter boundary
streaming_state/session/  (11 files) — session state inside streaming boundary
codex/event_handlers/     (6 files) — event dispatch inside agent adapter boundary
opencode/formatting/      (3 files) — formatting logic inside agent adapter boundary
printer/virtual_terminal/ (1 file)
runtime/streaming/        (1 file)
```

**55 nested boundary module violations (DENY).** These are hard architectural failures.

### Gap 2 — Domain code reaches into effect seams instead of receiving capabilities

Non-boundary modules directly import from `io/` (90), `runtime/` (24), `executor/` (17),
`boundary/` itself (12), and agent adapters (6). This means those domain functions cannot
be tested without real I/O. The Reader pattern is not applied.

### Gap 3 — Style violations throughout domain code

`let mut` bindings, imperative loops, interior mutability throughout domain modules. These
are WARN-level heuristics — investigate each one to determine whether the code is genuinely
imperative in a problematic way, or whether the lint is firing on legitimate code.

### Gap 4 — Compiler errors block everything

E0255, E0599, E0282, E0658, E0308, E0061 in the library. These must be resolved first.

### Gap 5 — Errors, diagnostics, and panic patterns in domain code

`git_helpers/config_state.rs` alone has ~85 `.unwrap()` calls. Domain functions print
diagnostics directly instead of returning them as data.

### Gap 6 — Test infrastructure gaps

No property-based testing. No coverage instrumentation. Some modules have no tests.

---

## Architectural Principles Being Applied

### Reader monad → capability injection

**Principle:** A pure function receives all its dependencies as parameters. It never reaches
into the module tree to find them.

**How it looks when correct:**
```rust
// Domain function — only plain values, returns plain values
pub fn parse_agent_config(raw: &str) -> Result<AgentConfig, ConfigParseError> {
    // no imports from io/, runtime/, std::fs, std::env
    // pure: same input always produces same output
}

// Boundary function — gathers input, calls pure helper, performs effect
pub fn load_agent_config(
    workspace: &dyn Workspace,
    path: &str,
) -> Result<AgentConfig, LoadConfigError> {
    let raw = workspace.read(path).map_err(LoadConfigError::Io)?;
    parse_agent_config(&raw).map_err(LoadConfigError::Parse)
}
```

**How to test that Reader is correctly applied:** Can you call the domain function in a
unit test with `assert_eq!(f(input), expected)` and zero setup (no MemoryWorkspace, no
MockProcessExecutor, no environment variables)? If yes, Reader is applied. If no, it is not.

### Writer monad → diagnostics as data

**Principle:** Pure functions return diagnostics as values. Only the boundary emits them.

**How it looks when correct:**
```rust
// Domain function — returns diagnostics as data, never prints
pub fn normalise_config(raw: RawConfig) -> WithDiagnostics<Config> {
    let timeout = raw.timeout.unwrap_or(30);
    let diagnostics = [
        raw.timeout.is_none().then_some(ConfigDiag::UsedDefaultTimeout),
    ].into_iter().flatten().collect();
    WithDiagnostics { value: Config { timeout }, diagnostics }
}

// Boundary — emits the diagnostics
pub fn load_and_report_config(ws: &dyn Workspace, logger: &dyn Logger, path: &str)
    -> Result<Config, LoadError>
{
    let raw = ws.read(path).map_err(LoadError::Io)?;
    let result = parse_raw_config(&raw).map_err(LoadError::Parse)?;
    let normalised = normalise_config(result);
    normalised.diagnostics.iter().for_each(|d| logger.emit(d));
    Ok(normalised.value)
}
```

### Except monad → typed Result propagation

**Principle:** Recoverable failures are values in `Result<T, E>` with typed error enums.
`.unwrap()`, `.expect()`, `panic!` never appear in domain code for recoverable failures.

**How it looks when correct:**
```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConfigParseError { MissingField(&'static str), InvalidTimeout(u32) }

pub fn parse_config(raw: &str) -> Result<Config, ConfigParseError> {
    let timeout = raw.parse::<u32>()
        .map_err(|_| ConfigParseError::InvalidTimeout(0))?;
    Ok(Config { timeout })
}
```

### Standard boundary shape (IMPURE → PURE → IMPURE)

Every boundary function follows exactly this rhythm:
1. **IMPURE** — call capability to gather raw input (read file, run process, read env)
2. **PURE** — call domain parsers/validators/planners on plain values
3. **IMPURE** — perform the requested edge interaction; return typed result or event

**How to test a boundary follows this shape:** Describe the function aloud as one sentence:
"Read X from capability, call pure helper with X, write/return the result."
If you cannot describe it in one sentence, the function is doing too much.

### Retry belongs in the state machine, not boundary loops

**Wrong (retry in boundary):**
```rust
// boundary/run_agent.rs — WRONG
pub fn run_with_retries(executor: &dyn Executor, req: &Request, max: u32)
    -> Result<Response, Error>
{
    let mut attempts = 0;
    loop {
        match executor.run(req) {
            Ok(r) => return Ok(r),
            Err(e) if attempts < max => attempts += 1,
            Err(e) => return Err(e),
        }
    }
}
```

**Correct (retry in state machine):**
```rust
// domain/reducer.rs — CORRECT: reducer decides if retry is needed
fn reduce(state: State, event: AgentEvent) -> State {
    match event {
        AgentEvent::Failed { .. } if state.retries_remaining > 0 =>
            State { retries_remaining: state.retries_remaining - 1,
                    next_effect: Some(Effect::RunAgent { .. }),
                    ..state },
        AgentEvent::Failed { error } =>
            State { phase: Phase::Failed(error), ..state },
        AgentEvent::Succeeded(output) =>
            State { phase: Phase::Done(output), ..state },
    }
}
// boundary/run_agent.rs — CORRECT: executes exactly one attempt
pub fn run_agent_once(executor: &dyn Executor, req: &Request)
    -> Result<AgentEvent, ExecutionError>
{
    executor.run(req)
        .map(AgentEvent::Succeeded)
        .map_err(|e| Ok(AgentEvent::Failed { error: e.to_string() }))
        .unwrap_or_else(|e| e)
}
```

---

## TODOs

### Phase 1 — Fix Compiler Errors (Immediate Blockers)

These prevent `cargo check` from passing and block all other work.

**How to find all instances:**
```bash
cargo check -p ralph-workflow --lib 2>&1 | grep "^error\[E"
```

- [x] **P1-E0255-files-mod**: `ralph-workflow/src/files/mod.rs` — both `llm_output_extraction`
  and `result_extraction` have `pub mod X;` AND `pub use self::X;` in the same file, creating
  a duplicate definition. Remove the `pub use self::llm_output_extraction;` and
  `pub use self::result_extraction;` lines. Keep only the `pub mod` declarations.
  
  **Acceptance criteria:** `cargo check -p ralph-workflow --lib 2>&1 | grep E0255` returns
  nothing.

- [x] **P1-E0599-EventTraceBuffer**: `EventTraceBuffer` in `ralph-workflow/src/app/trace/`
  does not have a `.push()` method (5 call sites) or `.flush()` method (1 call site). This
  means `EventTraceBuffer` was recently changed to an immutable type but call sites were not
  updated.
  
  **Find all call sites:**
  ```bash
  cargo check -p ralph-workflow --lib 2>&1 | grep "no method named" | grep -E "push|flush"
  ```
  
  **What to do:** For each `buf.push(event)` call site, replace with whatever the new
  `EventTraceBuffer` API provides — check `ralph-workflow/src/app/trace/` for the current
  type definition. If `EventTraceBuffer` has a `with_event(self, e) -> Self` builder, use:
  `let buf = buf.with_event(event)`. If it accepts events in a different way, match the
  actual API. Do not add a `.push()` method back — the change away from mutation was
  intentional.
  
  **Acceptance criteria:** `cargo check -p ralph-workflow --lib 2>&1 | grep EventTraceBuffer`
  returns nothing.

- [x] **P1-E0599-filter_map**: Two call sites call `.filter_map(...)` directly on a `Vec`
  value (which is not an iterator). Fix: change `vec.filter_map(f)` to
  `vec.into_iter().filter_map(f).collect::<Vec<_>>()` or
  `vec.iter().filter_map(f).collect::<Vec<_>>()` depending on whether ownership is needed.
  
  **Find call sites:**
  ```bash
  cargo check -p ralph-workflow --lib 2>&1 | grep "no method named.*filter_map"
  ```
  
  **Acceptance criteria:** `cargo check -p ralph-workflow --lib 2>&1 | grep filter_map`
  returns nothing.

- [x] **P1-E0658-str_as_str**: Use of unstable `str_as_str` feature. Find the call site and
  replace `s.as_str()` on a `str` with `s` directly (a `&str` is already a `&str`).
  
  **Find:** `cargo check -p ralph-workflow --lib 2>&1 | grep E0658`

- [x] **P1-E0308-mismatched-types**: One type mismatch error. Read the error output, find
  the call site, and fix the type to match what the function actually returns.
  
  **Find:** `cargo check -p ralph-workflow --lib 2>&1 | grep E0308`

- [x] **P1-E0061-argument-count**: One call site passes 4 arguments to a function that now
  takes 6. Read the function signature, add the missing arguments.
  
  **Find:** `cargo check -p ralph-workflow --lib 2>&1 | grep E0061`

- [x] **P1-E0282-type-annotations**: Three sites where type inference fails. Add explicit
  type annotations: `let x: ConcreteType = ...` or use the turbofish `::<Type>` syntax.
  
  **Find:** `cargo check -p ralph-workflow --lib 2>&1 | grep E0282`

- [x] **P1-E0596-opencode-parser**: `ralph-workflow/src/json_parser/opencode/parser_core.rs`
  has `&self` receivers on `parse_stream` and a related method that actually need `&mut self`
  (or the internal state needs to be restructured to not require mutation). This likely surfaced
  because the `opencode/formatting/` boundary flattening (Phase 2I) is partially complete and
  broke the call site.
  
  **Find:** `cargo check -p ralph-workflow --lib 2>&1 | grep E0596`
  
  Read `parser_core.rs` fully before deciding: if `parse_stream` takes a `&mut self` because
  it genuinely maintains streaming state, it belongs in a boundary module where `&mut self`
  is acceptable. If it can be made into a pure function that returns a new parser state, do
  that. Do NOT blindly add `&mut self` without understanding why mutation is needed.

- [x] **P1-unused-imports**: Three `error: unused import` diagnostics:
  - `self::llm_output_extraction` — in `files/mod.rs` (will be fixed by P1-E0255)
  - `self::result_extraction` — in `files/mod.rs` (will be fixed by P1-E0255)
  - `std::fmt::Write` — find and remove the unused import
  
  **Find:** `cargo check -p ralph-workflow --lib 2>&1 | grep "unused import"`

**Phase 1 done when:**
```bash
cargo check -p ralph-workflow --lib 2>&1 | grep "^error" | grep -v "^error: could not compile"
```
Returns nothing. The library compiles cleanly. Integration tests can now run.

---

### Phase 2 — Flatten Nested Boundary Modules (DENY: forbid_nested_boundary_modules)

This is the largest architectural repair in the plan. Boundary module directories are effect
seam markers — they must be flat. A boundary directory may contain `.rs` files but must not
contain subdirectories with further `.rs` files.

**How to find all current violations:**
```bash
cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet 2>&1 \
  | grep "nested module"
```

**The rule from `docs/code-style/module-organization.md`:**
Handlers are thin effect-execution shims. One file per effect seam. No nested trees inside
boundary directories. Where you have `boundary/review/mod.rs + inputs.rs + execution.rs`,
it becomes `boundary/run_review.rs`.

**The transformation pattern for every nested boundary sub-tree:**
1. Read every file in the sub-directory.
2. For each function: ask "can this run on plain values with no capability access?" 
   - YES → it is pure domain logic; move it to an appropriate non-boundary module
   - NO (it touches real I/O, processes, env, network) → it is boundary wiring; keep it
3. Collapse the boundary directory into a single flat `.rs` file that contains only the
   wiring. The wiring calls the extracted domain functions.
4. Delete the subdirectory.

**NOT ACCEPTABLE:**
- Moving pure-domain logic into the flat boundary file (mixing concerns)
- Renaming a directory from `boundary/review/` to `phases/review/boundary/` to avoid the
  lint path-matching (the lint checks for boundary directory names, but more importantly
  this is still architecturally wrong)
- Leaving policy/retry/validation logic inside the boundary file

**Acceptance criteria for EVERY restructured boundary module:**
1. The flat boundary `.rs` file describes itself in one sentence per function: "Read X,
   call pure_helper(X), write/return Y."
2. Every extracted domain function has a unit test that uses only plain value inputs
   (no `MemoryWorkspace`, no `MockProcessExecutor`, no environment setup).
3. `cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet 2>&1 | grep "nested module.*<module_name>"` returns nothing for that module.

#### 2A — Flatten `boundary/commit/` (7 nested files)

**Files to process:**
- `ralph-workflow/src/reducer/boundary/commit/agent.rs`
- `ralph-workflow/src/reducer/boundary/commit/execution.rs`
- `ralph-workflow/src/reducer/boundary/commit/inputs.rs`
- `ralph-workflow/src/reducer/boundary/commit/mod.rs`
- `ralph-workflow/src/reducer/boundary/commit/prompts.rs`
- `ralph-workflow/src/reducer/boundary/commit/validation.rs`
- `ralph-workflow/src/reducer/boundary/commit/xml.rs`

*(Verify exact paths with: `find ralph-workflow/src -path "*/boundary/commit*" -name "*.rs"`)*

- [x] Audit each file: separate pure (prompt construction, XML parsing, commit message
  formatting, validation of already-parsed values) from effectful (agent invocation,
  file writes, process spawning).

- [x] Move pure logic into `ralph-workflow/src/phases/commit/` or an appropriate existing
  domain module. Each moved function must have a unit test using plain values.

- [x] Collapse remaining wiring into `ralph-workflow/src/reducer/boundary/commit.rs`
  (single flat file). The file should contain one to three functions that each follow the
  IMPURE→PURE→IMPURE shape. If it grows beyond ~80 lines of real logic, revisit whether
  more logic should be extracted to domain.

- [x] Delete the `commit/` subdirectory.

**Verify:** `cargo dylint ... 2>&1 | grep "nested module.*commit"` → empty.

#### 2B — Flatten `boundary/development/` (6 nested files)

**Files:**
- `ralph-workflow/src/reducer/boundary/development/core.rs`
- `ralph-workflow/src/reducer/boundary/development/materialization.rs`
- `ralph-workflow/src/reducer/boundary/development/mod.rs`
- `ralph-workflow/src/reducer/boundary/development/preparation.rs`
- `ralph-workflow/src/reducer/boundary/development/preparation/modes.rs`
- `ralph-workflow/src/reducer/boundary/development/validation.rs`

*(Verify: `find ralph-workflow/src -path "*/boundary/development*" -name "*.rs"`)*

- [x] Same audit pattern as 2A. Materialization, prompt preparation, mode selection,
  input validation on already-parsed values are domain concerns. Move them out.

- [x] Collapse to `ralph-workflow/src/reducer/boundary/development.rs` (flat file).

- [x] Delete the `development/` subdirectory.

**Verify:** `cargo dylint ... 2>&1 | grep "nested module.*development"` → empty.

#### 2C — Flatten `boundary/planning/` (6 nested files)

**Files:**
- `ralph-workflow/src/reducer/boundary/planning/agent_execution.rs`
- `ralph-workflow/src/reducer/boundary/planning/input_materialization.rs`
- `ralph-workflow/src/reducer/boundary/planning/mod.rs`
- `ralph-workflow/src/reducer/boundary/planning/output_processing.rs`
- `ralph-workflow/src/reducer/boundary/planning/prompt_preparation.rs`
- `ralph-workflow/src/reducer/boundary/planning/xml_validation.rs`

*(Verify: `find ralph-workflow/src -path "*/boundary/planning*" -name "*.rs"`)*

- [x] XML validation on already-read strings is pure. Prompt preparation that builds a
  string from domain types is pure. Output processing that parses an agent response string
  is pure. Move all of these to `ralph-workflow/src/phases/planning/` domain module.

- [x] Collapse to `ralph-workflow/src/reducer/boundary/planning.rs` (flat file).

- [x] Delete the `planning/` subdirectory.

**Verify:** `cargo dylint ... 2>&1 | grep "nested module.*planning"` → empty.

#### 2D — Flatten `boundary/review/` (9 nested files)

**Files:**
- `ralph-workflow/src/reducer/boundary/review/fix_flow.rs`
- `ralph-workflow/src/reducer/boundary/review/review_flow/agent_invocation.rs`
- `ralph-workflow/src/reducer/boundary/review/review_flow/input_materialization.rs`
- `ralph-workflow/src/reducer/boundary/review/review_flow/io/mod.rs`
- `ralph-workflow/src/reducer/boundary/review/review_flow/mod.rs`
- `ralph-workflow/src/reducer/boundary/review/review_flow/output_rendering.rs`
- `ralph-workflow/src/reducer/boundary/review/review_flow/prompt_generation.rs`
- `ralph-workflow/src/reducer/boundary/review/review_flow/regex_cache.rs`
- `ralph-workflow/src/reducer/boundary/review/review_flow/validation.rs`
- `ralph-workflow/src/reducer/boundary/review/review_flow/xsd_retry_materialization.rs`

*(Verify: `find ralph-workflow/src -path "*/boundary/review*" -name "*.rs"`)*

- [x] Move pure logic (prompt generation, output rendering/parsing, regex compilation and
  matching, validation of parsed data, XSD retry decision logic) to
  `ralph-workflow/src/phases/review/` domain module.

- [x] Note: "regex_cache" is a WARN for interior mutability (`LazyLock`). If the regex is
  a compile-time constant, use a `const` or `OnceLock` — but this goes in domain code if
  the regex is domain knowledge, or in the boundary if it's an I/O adapter concern.

- [x] Collapse to `ralph-workflow/src/reducer/boundary/run_review.rs` and
  `ralph-workflow/src/reducer/boundary/run_fix.rs` (flat files, one per effect).

- [x] Delete the `review/` subdirectory.

**Verify:** `cargo dylint ... 2>&1 | grep "nested module.*review"` → empty.

#### 2E — Flatten `boundary/io/` (2 nested files)

- [x] Read `ralph-workflow/src/reducer/boundary/io/mod.rs` and `io/cloud.rs`.
  If the files contain only re-exports or thin wiring, inline them into the parent
  boundary module or rename to flat files (`boundary/io_cloud.rs`).
  If they contain domain logic, extract it first.

**Verify:** `cargo dylint ... 2>&1 | grep "nested module.*io"` for boundary context → empty.

#### 2F — Flatten `claude/delta_handling/` (5 nested files)

**Files:**
- `ralph-workflow/src/json_parser/claude/delta_handling/mod.rs`
- `ralph-workflow/src/json_parser/claude/delta_handling/content_blocks.rs`
- `ralph-workflow/src/json_parser/claude/delta_handling/errors.rs`
- `ralph-workflow/src/json_parser/claude/delta_handling/finalization.rs`
- `ralph-workflow/src/json_parser/claude/delta_handling/messages.rs`

*(Verify: `find ralph-workflow/src -path "*/claude/delta_handling*" -name "*.rs"`)*

- [x] Delta parsing (content block interpretation, message finalisation, error classification)
  is pure parsing logic. Move it to `ralph-workflow/src/json_parser/delta_parsing/` domain
  module. These functions accept structured data (e.g., `DeltaEvent`) and return typed
  domain values — no streaming I/O involved.

- [x] Keep only the streaming glue code (reading bytes/events from the Claude SSE stream
  and dispatching to the pure delta parsers) in a flat `claude/delta_handling.rs` single file.

- [x] Delete the `delta_handling/` subdirectory inside `claude/`.

**Verify:** `cargo dylint ... 2>&1 | grep "nested module.*claude"` → empty.

#### 2G — Flatten `streaming_state/session/` (11 nested files)

*(Find all files: `find ralph-workflow/src -path "*/streaming_state/session*" -name "*.rs"`)*

- [x] Read each file. Session state struct definitions and state transitions are pure domain
  types. Delta-handling logic (text, thinking, tool deltas) is pure parsing once the raw
  bytes are already decoded. Move pure types and logic to `ralph-workflow/src/streaming/`
  domain module.

- [x] Keep only the stateful streaming session management (maintaining a live connection,
  receiving bytes, dispatching to pure handlers) in a flat `streaming_state/session.rs` file.

- [x] Delete the `session/` subdirectory inside `streaming_state/`.

**Verify:** `cargo dylint ... 2>&1 | grep "nested module.*streaming_state"` → empty.

#### 2H — Flatten `codex/event_handlers/` (6 nested files)

*(Find: `find ralph-workflow/src -path "*/codex/event_handlers*" -name "*.rs"`)*

- [x] Event interpretation logic (what does a `turn_started` event mean in domain terms?)
  is pure. Move to `ralph-workflow/src/agents/codex/event_interpretation.rs` domain module.

- [x] Keep only the HTTP/stream event dispatch (receiving raw Codex API events and calling
  pure interpreters) in a flat `codex/event_handling.rs` file.

- [x] Delete the `event_handlers/` subdirectory inside `codex/`.

**Verify:** `cargo dylint ... 2>&1 | grep "nested module.*codex"` → empty.

#### 2I — Flatten `opencode/formatting/` (3 nested files)

*(Find: `find ralph-workflow/src -path "*/opencode/formatting*" -name "*.rs"`)*

- [x] Formatting logic (converting domain types to strings for display) is pure domain
  rendering. Move to `ralph-workflow/src/agents/opencode/formatting.rs` domain module.

- [x] Delete the `formatting/` subdirectory inside `opencode/`.

**Verify:** `cargo dylint ... 2>&1 | grep "nested module.*opencode"` → empty.

#### 2J — Flatten `printer/virtual_terminal/` and `runtime/streaming/`

- [x] `printer/virtual_terminal/mod.rs`: move pure terminal state/rendering logic to
  domain; keep only actual terminal write calls in flat `printer/virtual_terminal.rs`.

- [x] `runtime/streaming/streaming_line_reader.rs`: move pure line-parsing logic to
  domain; keep only the streaming I/O reads in flat `runtime/streaming.rs`.

**Verify:** `cargo dylint ... 2>&1 | grep "nested module"` → empty for all.

**Phase 2 done when:**
```bash
cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet 2>&1 \
  | grep "nested module" | wc -l
```
Returns `0`. Every nested boundary module violation is gone. AND every extracted domain
function has at least one passing unit test using plain value inputs.

---

### Phase 2 Completion Gate (MANDATORY — blocks ALL subsequent phases)

Phase 2 flattening MUST be complete before ANY Phase 3 work begins. Domain logic
extraction from boundary subtrees changes file locations and module paths — starting
Phase 3 before Phase 2 is done means fixing imports that point at code about to move.

```bash
# GATE CHECK — must return 0:
cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet 2>&1 \
  | grep "nested module" | wc -l
```

If this returns any value > 0, STOP. Complete the remaining Phase 2 tasks first.

---

### Cumulative Verification Protocol (applies to ALL phases below)

After completing EACH phase, run ALL previously-cleared checks — not just the current
phase's lint. A Phase 5 refactor can accidentally re-introduce a Phase 3 violation.

```bash
# Always run after every phase:
cargo check -p ralph-workflow --lib 2>&1 | grep "^error" | grep -v "could not compile"
  # → 0 errors (compilation clean)

cargo test -p ralph-workflow --lib --all-features
  # → all tests pass

# Add each cleared DENY lint to the cumulative check:
# After Phase 2: + grep "nested module" → 0
# After Phase 3: + grep "import from boundary module" → 0
# After Phase 4: + grep -E "policy_call|retry_loop" → 0
# After Phase 8: + grep "swallow" → 0
```

---

### Inline TDD Requirement (applies to ALL phases below)

Every phase that extracts, moves, or refactors a function MUST follow red-first TDD
per `docs/agents/testing-guide.md` and `AGENTS.md`. This is NOT deferred to Phase 12.

```
For every extracted/refactored function:
□ Red test written first (fails before refactor)
□ Green after refactor (test passes)
□ Pure functions: test with plain values, zero setup
□ Boundary functions: test with MemoryWorkspace + MockProcessExecutor
□ No #[serial] — use env-injection and MemoryWorkspace
```

Phase 12 serves as a TDD AUDIT — verifying each phase's work was tested, not as the
testing phase itself.

---

### Phase 3 — Apply Reader Pattern: Capability Injection (DENY: forbid_domain_boundary_dependencies)

**What this fixes:** Domain modules that directly `use`-import from boundary modules. This
makes domain functions untestable without real I/O.

**How to find all instances:**
```bash
cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet 2>&1 \
  | grep "import from boundary module"
```

**The transformation every time:**

```rust
// BEFORE: domain function reaches for its own dependency
use crate::io::workspace::WorkspaceImpl;  // WRONG: importing from boundary

fn build_prompt(path: &str) -> String {
    let content = WorkspaceImpl::new().read(path).unwrap();
    format!("Context:\n{content}")
}

// AFTER: dependency injected as plain trait parameter
// domain function:
pub fn build_prompt(context: &str) -> String {
    format!("Context:\n{context}")
}

// boundary function:
pub fn build_prompt_for_path(
    workspace: &dyn Workspace,
    path: &str,
) -> Result<String, BuildPromptError> {
    let content = workspace.read(path).map_err(BuildPromptError::Read)?;
    Ok(build_prompt(&content))
}
```

**NOT ACCEPTABLE:**
- Moving `build_prompt` into a boundary module to make the import "legal" — this mixes
  prompt construction (pure domain) with file reading (boundary effect)
- Adding a new parameter `workspace: Option<&dyn Workspace>` and doing the read inside the
  domain function — still impure
- Creating a new boundary-named module just to house the function and avoid the lint

**Acceptance criteria for EVERY fixed function:**
1. The domain function accepts only plain values (primitives, structs, `&str`, `&[T]`, etc.)
   and the `Workspace`/`ProcessExecutor`/capability traits are on a DIFFERENT function (the
   boundary caller).
2. A unit test exists that calls the domain function with literal values and no fakes:
   ```rust
   #[test]
   fn test_build_prompt() {
       let result = build_prompt("some context");
       assert!(result.contains("Context:"));
   }
   ```
3. A boundary-level integration test exercises the wiring using `MemoryWorkspace`.

#### Execution Order: Topological, Not Lint-Bucket

**DO NOT fix all io imports, then all runtime imports, then all executor imports.**
A single function may import from io AND runtime AND executor. Fixing them one lint
category at a time means touching the same function 3 times.

Instead, follow this sequence:

#### Step 1 — Capability Contracts (shared traits + translated types)

Before fixing any individual import, verify the abstraction layer is complete:

- [x] **P3-contracts-workspace**: Verify `Workspace` trait (at `workspace.rs:100`) covers
  all file access needs across all domain callers. Confirm `MemoryWorkspace` is ready for
  tests. If any domain function needs a file operation the trait doesn't support, extend
  the trait first.

- [x] **P3-contracts-executor**: Verify `ProcessExecutor` trait (at `executor/executor_trait.rs:28`)
  covers all process execution needs. Confirm `MockProcessExecutor` is ready for tests.
  Create `CommandOutput` domain type if not already available (exit code, stdout, stderr as
  plain values — NOT `std::process::Output`).

- [x] **P3-contracts-env**: Check if `Environment`/`ConfigEnvironment` traits exist for env
  access. If not, create one or adopt the env-injection pattern from `docs/agents/testing-guide.md`:
  `impl Fn(&str) -> Option<String>`. Domain code should never import `std::env::var` directly.

- [x] **P3-contracts-agents**: Define or verify an abstract agent trait (e.g., `AgentInvoker`
  or `ModelExecutor`). Boundary adapters for claude, codex, gemini, opencode implement the
  trait. Domain code depends on the trait only; concrete adapter types never appear in domain
  module imports.

#### Step 2 — Fix Per-Workflow (not per-lint-bucket)

For each workflow, fix ALL boundary imports (io + runtime + executor + boundary) in one pass:

- [x] **P3-workflow-context**: Fix `phases/context.rs` and config loading:
  Currently imports from executor (ProcessExecutor), runtime (GitEnvironment).
  Refactor to receive both via constructor/parameter injection. Single pass.

- [x] **P3-workflow-pipeline**: Fix pipeline modules (`idle_timeout/`, `prompt/`, `clipboard.rs`,
  `types.rs`): these import executor types (ProcessExecutor, AgentChild, ChildProcessInfo).
  Domain code should receive `CommandOutput` plain values or accept the trait via injection.

- [x] **P3-workflow-platform**: Fix `platform/detection.rs`: imports RealProcessExecutor.
  Platform detection logic (interpreting OS info) is pure; running the detection command
  is boundary. Split into `detect_platform(os_info: &str) -> Platform` (pure) and
  `gather_platform_info(executor: &dyn ProcessExecutor) -> CommandOutput` (boundary).

- [x] **P3-workflow-git**: Fix `git_helpers/identity.rs` and other git_helpers boundary
  imports. Note: comprehensive git_helpers refactor happens in Phase 9 — here only fix
  the specific boundary import violations. Do not restructure the entire git_helpers module.

- [x] **P3-workflow-app**: Fix `app/effect_handler.rs`, `app/plumbing.rs`,
  `app/rebase/orchestration.rs`, `app/rebase/conflicts.rs`, `app/env_access/mod.rs`.
  These are higher-level wiring modules — some may legitimately be boundary code. For
  each: if the function IS boundary wiring (gathering inputs, calling pure logic, performing
  effects), verify it's in a boundary-named module. If it's domain policy that happens to
  import a boundary type, extract the pure logic out.

- [x] **P3-workflow-agents**: Fix `agents/mod.rs`, `agents/cache_environment.rs`,
  `agents/ccs_env.rs`, `agents/config/file.rs`. These import `std::io` and `runtime`.
  Agent config parsing is pure (accept &str, return typed config). File reading is boundary.

#### Step 3 — Fix Remaining Standalone Imports

- [x] **P3-remaining-boundary**: After Phase 2 flattening, the ~12 `boundary/` import
  violations should mostly be gone. For any that remain: the importing file is using
  boundary logic directly (e.g., calling a handler from domain code). Move the policy
  decision to the domain caller and have it emit an `Effect` enum variant instead of
  calling the boundary directly.

- [x] **P3-remaining-agents**: For any remaining `claude`, `codex`, `gemini`, `opencode`,
  `streaming_state`, `printer` boundary imports from domain code that weren't fixed in
  Step 2 workflows — apply the abstract trait pattern from P3-contracts-agents.

#### Per-Category Refactoring Guidance

**io/ imports (most are for TerminalInput/TerminalOutput/BannerOutput):**
- Domain function should return a display-ready value; boundary function writes it
- Pattern: `fn format_banner(config: &Config) -> BannerContent` (pure) +
  `fn write_banner(output: &dyn TerminalOutput, content: &BannerContent)` (boundary)

**runtime/ imports (most are for Environment/GitEnvironment):**
- Refactor to accept env values as parameters, not the Environment trait
- Pattern: `fn resolve_config_path(home: &str, project_dir: &str) -> PathBuf` (pure) +
  `fn load_config_path(env: &dyn Environment) -> Result<PathBuf, ...>` (boundary gathers
  home/project_dir, calls pure resolver)

**executor/ imports (most are for ProcessExecutor trait):**
- Domain code should receive `CommandOutput` (plain values), not the executor trait itself
- Pattern: `fn interpret_format_result(output: &CommandOutput) -> FormatEvent` (pure) +
  `fn run_formatter(executor: &dyn ProcessExecutor) -> Result<FormatEvent, ...>` (boundary
  runs command, calls pure interpreter)

**Phase 3 done when:**
```bash
cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet 2>&1 \
  | grep "import from boundary module" | wc -l
```
Returns `0`. AND every refactored domain function has a unit test with plain value inputs.

---

### Phase 4 — Remove Policy from Boundary Functions (DENY: forbid_boundary_policy_calls, forbid_boundary_retry_loops)

**What this fixes:** Boundary functions that make policy decisions (retry, fallback, workflow
progression, business branching) instead of delegating to domain code or the state machine.

**How to find violations:**
```bash
cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet 2>&1 \
  | grep -E "forbid_boundary_policy|forbid_boundary_retry|retry.*boundary|policy.*boundary"
```

#### Existing Effect Infrastructure (MUST reference — do NOT reinvent)

The Effect dispatch mechanism already exists. Phase 4 work extends it, not replaces it:

```
Effect enum:      reducer/effect/types/effect_enum.rs
Orchestration:    reducer/orchestration/ (per-workflow determine_next_effect)
Handler dispatch: reducer/boundary/mod.rs:187 (MainEffectHandler.execute())
Mock dispatch:    app/mock_effect_handler/core.rs (MockEffectHandler for tests)
```

When moving policy OUT of a boundary function, the policy goes INTO the orchestrator +
reducer, and the boundary gets a new Effect variant (or reuses an existing one).

#### Per-Effect Checklist (MANDATORY for every new Effect variant)

When Phase 4 work creates a new Effect (e.g., splitting a retry loop into single-attempt
effects), verify ALL of these:

```
□ Effect variant added to reducer/effect/types/effect_enum.rs
□ Orchestrator: determine_next_effect() handles the new state (selects this effect)
□ Handler: MainEffectHandler.execute() dispatches to the boundary function
□ MockEffectHandler: match arm added for testing
□ Event type(s) defined for the handler's outcome (past-tense, fact-shaped)
□ Reducer: reduce() handles the new event and produces next state
□ Unit test: orchestrator selects the correct effect from state
□ Unit test: reducer produces correct state from event
□ Integration test: effect → handler → event → reducer round-trip
```

#### Effect Design Rules (from docs/code-style/architecture.md)

- Effects are **concrete edge actions** ("RunReview", "LoadConfig"), NOT control flow ("Retry", "Continue")
- Effect payloads are **domain-shaped** (typed structs), not raw types
- Retries **re-derive the same concrete effect** from updated state — the reducer decrements
  retry count and the orchestrator re-selects the same effect. Do NOT create generic "RetryEffect"
  variants that encode control policy in the effect layer.
- Events are **past-tense facts** ("ReviewCompleted", "ValidationFailed"), NOT commands
  ("RetryReview", "AdvancePhase"). Reducers decide consequences from state + facts.

**Retry loop transformation (from `docs/code-style/functional-transformations.md`):**

The boundary executes ONE attempt and returns a typed event. The reducer decides if another
attempt is needed and emits an `Effect` to schedule it. This is not a debate — see
`docs/code-style/boundaries.md` Example 5.

**For every boundary function containing a `loop`, `while`, or conditional retry:**

1. Extract ONE attempt into a pure handler function: `fn execute_once(...) -> Result<SuccessEvent, FailureEvent>`
2. Remove the retry counting from the boundary.
3. In the reducer, add a retry counter to the relevant state struct.
4. In the `reduce()` function, match on `FailureEvent` and if retries remain, return the
   state with a new `Effect::RetryAttempt` scheduled.

**For every boundary function containing business branching (`if domain_condition { do_this } else { do_that }`):**

The condition belongs in the orchestrator or reducer. The boundary should receive a typed
effect that already encodes the decision:
```rust
// WRONG: boundary decides which path
pub fn handle_review(state: &State, workspace: &dyn Workspace) -> Result<Event, Error> {
    if state.needs_xsd_retry { run_xsd_retry(workspace) }
    else { run_standard_review(workspace) }
}

// CORRECT: orchestrator decides, boundary just executes
pub enum Effect { RunXsdRetry { .. }, RunStandardReview { .. } }
pub fn handle_effect(effect: Effect, workspace: &dyn Workspace) -> Result<Event, Error> {
    match effect {
        Effect::RunXsdRetry { .. } => run_xsd_retry(workspace),
        Effect::RunStandardReview { .. } => run_standard_review(workspace),
    }
}
```

#### Phase 4 Task Checklist (manual + lint-guided, style-guide-first)

- [x] **P4-lint-policy-shape** — Strengthen `forbid_boundary_policy_calls` heuristic so branch-driven
  effect selection (`if`/`match`) is detected using effect-category awareness, not only narrow call-name
  matching.

- [x] **P4-lint-retry-shape** — Strengthen `forbid_boundary_retry_loops` heuristic to catch helper-mediated
  retry wrappers (loop calls helper, helper performs effect), not just inline retry loops.

- [x] **P4-lint-verify-incrementally** — Keep lint crate compiling/testing after each heuristic update
  (`RUSTUP_TOOLCHAIN=nightly cargo check` + targeted lint tests).

- [x] **P4-manual-policy-inventory** — Manually enumerate boundary policy violations across
  `reducer/boundary/`, `runtime/`, `io/`, `executor/` style boundaries. Record file:line + why it is policy
  (decision/fallback/branching) and proposed extraction target.

- [x] **P4-manual-retry-inventory** — Manually enumerate boundary retry-policy ownership patterns
  (direct loops, helper-based retries, fallback chains, attempt counters/backoff) and classify legitimate
  polling vs policy retry.

- [x] **P4-crosscheck-manual-vs-lint** — Build three lists: (a) manual-only (lint false negatives),
  (b) lint-only (possible false positives), (c) overlap true positives. Use this as the source of truth
  for the Phase 4 burn-down queue.

- [x] **P4-fix-policy-violations** — Remove all confirmed boundary policy decisions by pushing decisions
  into reducer/orchestrator/effect selection and keeping boundaries thin (IMPURE -> PURE -> IMPURE).
  - [x] **P4-policy-commit-promptmode-guard** — Removed commit-boundary PromptMode admissibility policy
    from `reducer/boundary/commit.rs`; orchestrator invariant now owns mode filtering and boundary enforces
    only a precondition assertion in debug.
  - [x] **P4-policy-development-promptmode-branches** — Split development PromptMode branching into
    pre-decided orchestration/effect routing.
  - [x] **P4-policy-cloud-exitcode-triway** — Move cloud exit-code interpretation decisions out of boundary.

- [x] **P4-fix-retry-violations** — Remove boundary-owned retry policy by converting retries into state-machine
  transitions (single-attempt boundary effect + reducer/orchestrator retry scheduling).
  - [x] **P4-R10-run-review-xsd-fallback** — Moved XSD retry input fallback selection in
    `reducer/boundary/run_review.rs` into pure domain strategy helper
    (`phases/review/xsd_retry_input_strategy.rs`) and added focused tests.
  - [x] **P4-retry-remaining-manual-candidates** — Re-check ambiguous/manual retry candidates and
    either fix them or explicitly classify as legitimate boundary polling.

- [x] **P4-close-lint-gaps-from-inventory** — For every confirmed manual false negative, either:
  (1) improve the lint heuristic with tests, or (2) document why structural detection is out-of-scope and
  add a compensating review pattern. Goal: lint as useful tool aligned to style-guide intent.

- [x] **P4-regression-proofing** — Add/expand tests around newly fixed policy/retry boundaries and lint
  fixtures for new detection patterns so regressions are caught quickly.

**Phase 4 done when:**
```bash
cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet 2>&1 \
  | grep -E "policy_call|retry_loop" | wc -l
```
Returns `0`. AND: describe aloud every boundary function in one sentence. If you cannot, it still contains policy.

---

### Phase 5 — Value Transformations in Domain Code (MERGED: forbid_mut_binding + forbid_imperative_loops)

**This phase merges the old separate "mutable bindings" and "imperative loops" phases.**
Most `let mut` violations ARE the imperative loop pattern — `let mut v = Vec::new(); for x
in xs { v.push(f(x)); }` triggers BOTH lints on the same function. Fixing them separately
would mean visiting the same function twice.

**Execution model:** Work per-MODULE, not per-pattern. For each module, fix ALL let-mut +
ALL loops + ALL flags in one pass. Keep separate acceptance criteria for lint visibility.

**Important:** These are WARN-level heuristics. The lint fires on `let mut` bindings but
cannot prove that any problematic mutation actually happens. For every firing, investigate:
is this genuine domain-layer mutation that would be cleaner as a value transformation?

**How to find all current instances (use as investigation starting points only):**
```bash
cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet 2>&1 \
  | grep "let mut.*is forbidden"
```

**The judgment call for each `let mut`:**

Ask: Does removing `let mut` and using a value-returning transformation make the code
GENUINELY CLEARER? If yes, apply it. If the `let mut` version is actually clearer
(rare), document why and do not change it.

**Common patterns and their FP replacements** (all from `docs/code-style/functional-transformations.md`):

| Imperative pattern | Preferred replacement |
|---|---|
| `let mut v = Vec::new(); for x in xs { v.push(f(x)); }` | `xs.into_iter().map(f).collect()` |
| `let mut v = Vec::new(); for x in xs { if p(&x) { v.push(x); } }` | `xs.into_iter().filter(p).collect()` |
| `let mut acc = String::new(); for s in parts { acc.push_str(s); }` | `parts.join("")` or `parts.into_iter().collect()` |
| `let mut result = None; for x in xs { if cond(x) { result = Some(x); break; } }` | `xs.into_iter().find(cond)` |
| `let mut n = 0; for x in xs { if p(&x) { n += 1; } }` | `xs.iter().filter(p).count()` |
| `let mut config = Config::default(); config.field = x; config.other = y;` | `Config { field: x, other: y, ..Config::default() }` or `Config::default().with_field(x).with_other(y)` |
| `let mut s = base; if cond { s = transform(s); }` | `let s = if cond { transform(base) } else { base }` (shadowing) |

**NOT ACCEPTABLE:**
- Moving a function with `let mut` into a boundary module to silence the lint
- Renaming the binding from `let mut result` to `let mut r` 
- Wrapping `let mut` in a closure that gets immediately called

**Acceptance criteria for each refactored function:**
- The function body reads as a sequence of value transformations, not state mutations
- A reader can understand what the function produces without tracking variable state
- All existing tests still pass; add a new test if the function previously had none

**Investigation groups** (check each group, fix genuine violations, document false positives):

- [x] **P5-parse-state** — vars like `buf`, `reader`, `text`, `content`, `inner_buf`,
  `bare_content_xml`, `raw_text_parts`: If reading bytes from a `Read` trait — that is
  boundary code and `let mut buf` is legitimate there. If operating on an already-read
  `String`/`&str` — replace with `.lines()`, `.chars()`, `.split()`, `scan()`, `fold()`.
  - [x] **P5-parse-state-runtime-render-loop-item** — Refactored
    `ralph-workflow/src/prompts/runtime.rs::render_loop_item` from mutable parse-state
    string assembly to `fold`-based value composition with focused unit tests.
  - [x] **P5-parse-state-opencode-accumulate-text** — Refactored
    `ralph-workflow/src/files/llm_output_extraction/xml_extraction_plan.rs::OpenCodeStrategy::accumulate_text`
    from mutable accumulation to iterator composition with focused parsing tests.
  - [x] **P5-parse-state-json-result-extract** — Refactored
    `ralph-workflow/src/files/llm_output_extraction/xml_extraction_plan.rs::JsonResultStrategy::extract`
    from nested imperative line/field scanning to nested `find_map` value-transformation style,
    with focused multi-field search test coverage.
  - [x] **P5-parse-state-development-result-json-scan** — Refactored
    `ralph-workflow/src/files/llm_output_extraction/xml_extraction_development_result.rs::try_extract_from_json_string`
    from imperative NDJSON line/field scanning to iterator/find_map composition with focused
    alternate-field extraction test coverage.
  - [x] **P5-parse-state-fix-result-json-scan** — Refactored
    `ralph-workflow/src/files/llm_output_extraction/xml_extraction_fix_result.rs::try_extract_from_json_string`
    from imperative NDJSON line/field scanning to iterator/find_map composition with focused
    content-field extraction test coverage.
  - [x] **P5-parse-state-issues-result-json-scan** — Verified
    `ralph-workflow/src/files/llm_output_extraction/xml_extraction_issues.rs::try_extract_from_json_string`
    is in iterator/find_map value-transformation style with helper-closure fallback extraction
    and passing focused module tests.

- [x] **P5-accumulators** — vars like `result`, `output`, `summary`, `elements`, `parts`,
  `body`, `cells`, `collected`, `accumulated`, `content_fragments`: Almost always replaceable
  with `map`/`filter_map`/`flat_map`/`fold`/`collect` pipelines or `[a, b, c].into_iter()
  .filter(|s| !s.is_empty()).collect::<Vec<_>>().join("\n")` for multi-section assembly.
  - [x] **P5-accumulators-step-parser-attrs-string** — Refactored
    `ralph-workflow/src/files/llm_output_extraction/xsd_validation_plan/validation/step_parsers.rs::attrs_to_string`
    from mutable string accumulation to iterator/value-transformation composition while preserving
    exact spacing and quoting output semantics.
  - [x] **P5-accumulators-section-inline-elements** — Refactored
    `ralph-workflow/src/files/llm_output_extraction/xsd_validation_plan/validation/section_parsers.rs::parse_inline_elements`
    from mutable inline-element accumulation to iterator-driven cursor iteration with
    equivalent inline text/emphasis/code/link parsing behavior.
  - [x] **P5-accumulators-language-detector-extension-scan** — Refactored
    `ralph-workflow/src/language_detector/io.rs::count_extensions_with_workspace`
    to thread queue/count/file-scan state through recursive value returns rather than
    in-loop mutable reassignment, preserving extension-count semantics.
  - [x] **P5-accumulators-language-detector-test-scan** — Refactored
    `ralph-workflow/src/language_detector/io.rs::detect_tests_with_workspace`
    to thread queue/scan state through recursive value returns and composed queue-merge
    helper logic instead of in-loop mutable reassignment.

- [x] **P5-flags** — vars like `has_entries`, `found_root`, `in_tag`, `in_content`,
  `no_issues_found`, `files_changed_present`: Replace scanning flags with `any()`, `all()`,
  `find()`, `find_map()`, or `position()`. A flag that records "did we see X in the loop"
  is always replaceable with `items.iter().any(|x| is_X(x))`.
  - [x] **P5-flags-main-validator-root-detection** — Refactored
    `ralph-workflow/src/files/llm_output_extraction/xsd_validation_plan/validation/main_validator.rs::validate_plan_xml`
    to remove mutable `found_root` scanning state in favor of pre-scan root detection and
    root-scoped parsing while preserving missing-root error behavior.
  - [x] **P5-flags-xml-formatter-mode-state** — Refactored
    `ralph-workflow/src/files/llm_output_extraction/xml_formatter.rs::pretty_print_xml`
    from mutable boolean flag tracking (`in_tag`, `in_content`) to explicit mode-state
    transitions while preserving formatting output semantics.
  - [x] **P5-flags-diff-truncation-file-state** — Refactored
    `ralph-workflow/src/phases/commit/diff_truncation.rs::truncate_diff_to_model_budget`
    to remove boolean `in_file` tracking in favor of value-encoded current-file state,
    with regression coverage for header-only trailing diff blocks.

- [x] **P5-builders** — vars like `config`, `handler`, `phase_ctx`, `opts`, `diff_opts`:
  Use `with_*` consuming builder pattern or struct-update syntax. Check if the type already
  has `with_*` methods; if not, add them per the pattern in
  `docs/code-style/functional-transformations.md` ("The with_* method pattern").
  - [x] **P5-builders-git-snapshot-status-options** — Refactored
    `ralph-workflow/src/git_helpers/repo/snapshot.rs::git_snapshot_impl`
    to use extracted configured status-options builder construction, preserving
    `include_untracked`/`recurse_untracked_dirs`/`include_ignored` semantics.
  - [x] **P5-builders-git-diff-options-helper** — Refactored
    `ralph-workflow/src/git_helpers/repo/diff.rs` call sites to use shared
    `configured_diff_options()` builder construction instead of repeated mutable
    option setup blocks.
  - [x] **P5-builders-git-commit-status-options** — Refactored
    `ralph-workflow/src/git_helpers/repo/commit.rs::git_add_all_impl`
    to use shared `configured_status_options()` builder construction instead of
    in-function repeated mutable status-options setup.

- [x] **P5-git** — vars like `git_helpers`, `index`, `perms`, `files`, `diff_opts`:
  Many of these are in `git_helpers/` which is being comprehensively refactored in Phase 9.
  Coordinate with that phase — fix the architecture first, then the style follows.
  - [x] **P5-git-snapshot-options-slice** — Completed one atomic options cleanup in
    `ralph-workflow/src/git_helpers/repo/snapshot.rs` as a low-risk pre-Phase-9
    value-style builder alignment slice.
  - [x] **P5-git-diff-options-slice** — Completed one atomic options cleanup in
    `ralph-workflow/src/git_helpers/repo/diff.rs` by centralizing repeated
    diff-options construction while preserving git diff semantics.
  - [x] **P5-git-commit-options-slice** — Completed one atomic options cleanup in
    `ralph-workflow/src/git_helpers/repo/commit.rs` by centralizing status-options
    construction while preserving git add staging semantics.

- [x] **P5-misc** — remaining `let mut` vars: investigate each individually. Document any
  that are false positives with a comment explaining why.
  - [x] **P5-misc-git-cleanup-track-issues-accumulator** — Refactored
    `ralph-workflow/src/git_helpers/cleanup.rs::check_track_file_issues`
    from mutable vec accumulation + conditional push to iterator/value composition
    while preserving issue ordering and emitted message strings.

---

### ~~Phase 6~~ (MERGED into Phase 5 — Imperative Loops)

**This phase has been merged into Phase 5 above.** The loop replacement table and
investigation tasks are now part of the Phase 5 "Value Transformations" pass.

The following guidance applies during Phase 5 work for loop replacements:

**How to find imperative loop instances (run alongside the let-mut search):**
```bash
cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet 2>&1 \
  | grep "loop is forbidden"
```

**Loop purpose → correct fix mapping:**

| Loop purpose | Correct fix |
|---|---|
| Building a collection | `map`/`filter`/`filter_map`/`flat_map` + `collect()` |
| Accumulating a value | `fold(init, f)` — with a PURE `f` that does NOT use `mut acc` |
| Finding first match | `find()` or `find_map()` |
| Checking a predicate over all elements | `any()` or `all()` |
| Counting | `count()` or `filter(...).count()` |
| Validating all elements (fail fast) | `try_for_each(validate)?` |
| Retry policy | Move to reducer state machine — see Phase 4 |
| Streaming I/O read loop | This belongs in a boundary module — move there if not already |
| State machine step (advance until done) | `std::iter::successors(init, step)` or recursive pure helper |

**NOT ACCEPTABLE:**
- Moving a function with a `for` loop into a boundary module to silence the lint when the
  function does no actual I/O (e.g., transforming an in-memory collection)
- Converting `for item in items { result.push(f(item)) }` to `for item in items { result = result.with_f(item) }` (same loop, different mutation)

**Investigation tasks (execute during Phase 5 module-by-module pass):**

- [x] **P5-loops-for**: For each `for` loop encountered during Phase 5 module work — identify
  its purpose from the table above and apply the correct replacement.
  - [x] **P5-loops-for-ps-children-grouping** — Refactored
    `ralph-workflow/src/executor/ps.rs::build_children_lookup` from explicit
    `for`-loop grouping into iterator `fold` composition while preserving
    parent-PID grouping semantics.
  - [x] **P5-loops-for-runtime-loop-rendering** — Refactored
    `ralph-workflow/src/prompts/runtime.rs::process_loops_with_log` item accumulation
    from explicit `for` loop into iterator `map` + `unzip` + `flatten` composition
    while preserving rendered-output and unsubstituted-variable ordering semantics.

- [x] **P5-loops-bare**: Each bare `loop` in domain code is almost certainly retry policy
  (→ Phase 4 state machine) or a streaming I/O loop (→ boundary module). Classify and fix.

- [x] **P5-loops-while**: Each `while` is either:
  - `while condition { mutate }` → recursive step or `successors()`
  - `while let Some(x) = iter.next()` → `for x in iter` or combinator
  - `while bytes_read > 0` → streaming I/O (boundary)
  - [x] **P5-loops-while-config-entries-iteration** — Refactored
    `ralph-workflow/src/git_helpers/config_state.rs::collect_config_entries`
    from `while let Some(entry) = entries.next()` accumulation to iterator
    `from_fn(...).collect()` value composition preserving error mapping semantics.

**Combined Phase 5 acceptance criteria (both let-mut AND loops):**
```bash
# Mutable bindings (track count decrease):
cargo dylint ... 2>&1 | grep "let mut.*is forbidden" | wc -l

# Imperative loops (track count decrease):
cargo dylint ... 2>&1 | grep "loop is forbidden" | wc -l
```
Both counts should decrease after each module pass. WARN-level: 0 is aspirational, not
mandatory. Genuine false positives should be documented with code comments.

---

### Phase 7 — Interior Mutability in Domain Code (WARN: forbid_interior_mutability)

**Important:** WARN-level heuristic. Investigate before changing.

**How to find:**
```bash
cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet 2>&1 \
  | grep "interior-mutability"
```

**For each instance, ask:** Why is shared mutable state needed here?

#### Legitimate Interior Mutability (document, do NOT "fix")

Not all interior mutability is problematic. The following uses are architecturally correct
and should be documented with a comment, not refactored away:

- `LazyLock<Regex>` / `OnceLock<Regex>` for compile-once domain regex patterns — this is
  a performance optimisation for immutable data, not shared mutable state
- `OnceLock<T>` for expensive one-time initialization of truly constant data
- `Mutex`/`RwLock` in boundary modules protecting real I/O resources (connection pools,
  file handles, terminal state) — this is intrinsic to the effect seam

#### Problematic Interior Mutability (MUST fix)

- [x] **P7-mutex** (~6 `Mutex` instances in domain code): If protecting shared data across
  threads in domain code — the real question is why domain code involves threading. Threading
  is a runtime/boundary concern. Re-model as explicit state flowing through the reducer cycle.
  If `Mutex` wraps a resource needed for I/O (e.g., a connection pool), move it to the
  boundary module that owns that resource.

- [x] **P7-lazylock** (~4 `LazyLock` instances): 
  - Static data that is truly constant → `const` or `static` (no `LazyLock` needed)
  - Compiled regex that is domain knowledge → `OnceLock<Regex>` in the domain module is
    acceptable IF the regex is actually domain knowledge; WARN lint may fire but document
    with a comment explaining why this is correct
  - Runtime-derived singleton → inject as a parameter (Reader pattern)

- [x] **P7-cell** (~2 `Cell` instances): `Cell<T>` in domain code usually means a counter
  or flag that is being threaded through callbacks. Replace by threading the value explicitly
  through function return values.

**Acceptance criteria:** For each fixed instance, the data that was behind interior mutability
is now either (a) a parameter passed explicitly, (b) a `const`/`static` compile-time value,
or (c) legitimately in a boundary module because it IS an I/O resource.

---

### Phase 8 — Fix Result Swallowing (DENY: forbid_result_swallowing)

**This is DENY-level.** Silent result discarding is forbidden regardless of context.

**How to find:**
```bash
cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet 2>&1 \
  | grep "result_swallowing\|let _\|\.ok()"
```

**Patterns and their fixes:**

```rust
// FORBIDDEN: let _ = fallible_operation();
// FIX: propagate with ? or handle explicitly
fallible_operation()?;
// or:
if let Err(e) = fallible_operation() {
    // explicitly handle or log the error
}

// FORBIDDEN: fallible_operation().ok();
// FIX: use the value or propagate
let _result = fallible_operation()?;  // if result unneeded, still propagate failure

// FORBIDDEN: if let Err(_) = result { }  (unit body, swallows error)
// FIX: at minimum log the error as a diagnostic
if let Err(e) = result {
    return Err(MyError::from(e));  // or return it, or add to diagnostics
}
```

- [x] **P8-swallow**: For every silent result discard — decide: should this failure propagate
  to the caller? Almost always yes. Add `?` propagation and a typed error variant.

**Note: Phase 8 / Phase 9 overlap.** 190 of the result swallowing matches are in
`git_helpers/` (85 in `config_state.rs` alone). These are addressed comprehensively in
Phase 9's git_helpers refactor. During Phase 8, **skip git_helpers files** — fix result
swallowing everywhere else first. Phase 9 will handle the git_helpers unwraps as part of
the larger architectural refactor of that module.

**Phase 8 done when:**
```bash
# Excluding git_helpers (handled in Phase 9):
cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet 2>&1 \
  | grep "swallow" | grep -v "git_helpers" | wc -l
```
Returns `0`.

---

### Phase 9 — git_helpers Architecture Refactor (Major Module)

`git_helpers/config_state.rs` has ~85 `.unwrap()` calls. This module mixes pure git state
interpretation with effectful git operations, using panic-driven control flow throughout.

- [x] **P9-audit**: Map every function in `ralph-workflow/src/git_helpers/` as either:
  - Pure: parsing git status strings, building `CommandSpec` structs, interpreting diff
    output, classifying commit messages — these accept `&str` or structured data
  - Effectful: running `git` processes, reading `.git/` directory, writing refs

  Record findings in `.sisyphus/notepads/fp-style-compliance/learnings.md`.

- [x] **P9-split**: Reorganise the module using **facade preservation strategy**:

  **CRITICAL:** `git_helpers` is imported in **48 files with 225 matches** across the crate.
  A hard API rename (e.g., `git_helpers::foo` → `git_helpers::domain::foo`) would force
  broad call-site rework and create noisy diffs conflicting with other branches.

  **Strategy: Keep `mod.rs` public API stable, refactor internals:**
  ```
  git_helpers/
    mod.rs              ← PUBLIC API UNCHANGED — re-exports from internal modules
    domain/             ← NEW: pure parsing, validation, command spec building
      parse.rs          ← parse_status_output(), parse_diff_output(), etc.
      identity.rs       ← validate_git_identity_fields(), choose_username()
      types.rs          ← GitError enum, GitStatus, DiffSummary, etc.
    boundary.rs         ← NEW: effectful git2 calls, process spawning
    config_state.rs     ← gradually migrate .unwrap() to Result<T, GitError>
  ```

  The `mod.rs` file re-exports everything through the existing public paths. Callers see
  no API change. Internally, pure logic moves to `domain/` and effectful code stays in
  `boundary.rs`.

  **Only change public import paths AFTER** the internal split is stable and all phases
  are complete. Change one workflow at a time during a follow-up task, not a global rename.

- [x] **P9-errors**: Create a `GitError` enum:
  ```rust
  #[derive(Debug, Clone, PartialEq, Eq)]
  pub enum GitError {
      NotARepository,
      NoCommits,
      ParseFailed { context: String },
      ExecutionFailed(String),
  }
  ```
  Replace every `.unwrap()` in the pure domain functions with `?` returning `GitError`.
  Replace every `.unwrap()` in boundary functions with `map_err(GitError::from)` or
  explicit match arms.

- [x] **P9-tests**: Write unit tests for EVERY pure git_helpers function BEFORE refactoring
  (red-first TDD). Pure git parsers accept `&str` input — no subprocess, no git repository
  needed:
  ```rust
  #[test]
  fn test_parse_git_status_modified() {
      let output = " M ralph-workflow/src/main.rs\n";
      let entries = parse_status_output(output).unwrap();
      assert_eq!(entries.len(), 1);
      assert_eq!(entries[0].state, FileState::Modified);
  }
  ```

**Phase 9 done when:** Every function in `ralph-workflow/src/git_helpers/` that was
previously pure-but-panicky now returns `Result<T, GitError>` and has a passing unit test.

---

### Phase 10A — Error Cleanup (Except Monad — no newtypes needed yet)

**Why 10A before Phase 11:** This phase replaces panics, unwraps, and string errors with
typed error enums using primitive payloads. It does NOT need the newtypes from Phase 11 —
those come next. After Phase 11 introduces strong types, Phase 10B enriches error payloads.

- [x] **P10-unwrap-domain**: Audit all `.unwrap()` in non-test, non-boundary domain code.
  
  **Find:** 
  ```bash
  rg '\.unwrap\(\)' ralph-workflow/src/ --glob '*.rs' \
    --glob '!*test*' --glob '!*/io/*' --glob '!*/runtime/*' \
    --glob '!*/boundary/*' --glob '!*/executor/*'
  ```
  
  For each: replace with `?`, `ok_or_else(|| MyError::Specific)`, or `unwrap_or_else`.
  
  **Acceptance criteria:** Every `.unwrap()` in domain code either:
  - Is replaced by `?` with a typed error type, OR
  - Has a code comment explaining the invariant that guarantees it cannot fail, e.g.:
    `// SAFETY: split_once is called only when line.contains(':'), so this always succeeds`

- [x] **P10-panic-domain**: Audit `panic!` in non-boundary, non-test, non-xtask domain code.
  ```bash
  rg 'panic!' ralph-workflow/src/ --glob '*.rs' \
    --glob '!*test*' --glob '!*/io/*' --glob '!*/runtime/*' --glob '!*/boundary/*'
  ```
  Replace with `Result` propagation. `panic!` belongs only in test assertions, documented
  compile-time invariants, and entry-point crash-on-unrecoverable-error.

- [x] **P10-string-errors**: Audit domain functions returning `String` or `Box<dyn Error>` as
  the error type. Replace with named error enums:
  ```rust
  // WRONG: fn parse(s: &str) -> Result<Config, String>
  // RIGHT:
  #[derive(Debug, Clone, PartialEq, Eq)]
  pub enum ConfigParseError { MissingField(&'static str), InvalidValue { field: &'static str, got: String } }
  pub fn parse(s: &str) -> Result<Config, ConfigParseError>
  ```

- [x] **P10A-diagnostics-as-data**: Identify domain functions that call `println!`, `eprintln!`,
  `log::warn!`, `tracing::warn!`, or similar with domain-meaningful content (normalisation
  decisions, defaults applied, values clamped). Refactor to return `WithDiagnostics<T>`:
  ```rust
  #[derive(Debug, Clone, PartialEq, Eq)]
  pub enum ConfigDiagnostic { UsedDefaultTimeout, ClampedRetries(u32) }
  
  pub struct WithDiagnostics<T> { pub value: T, pub diagnostics: Vec<ConfigDiagnostic> }
  
  // domain function — returns diagnostics as data
  pub fn normalise_config(raw: RawConfig) -> WithDiagnostics<Config> { ... }
  
  // boundary — emits them
  pub fn load_config(ws: &dyn Workspace, logger: &dyn Logger) -> Result<Config, LoadError> {
      let raw = ws.read("config").map_err(LoadError::Io)?;
      let result = normalise_config(parse(&raw).map_err(LoadError::Parse)?);
      result.diagnostics.iter().for_each(|d| logger.emit(d));
      Ok(result.value)
  }
  ```
  
  **Acceptance criteria:** The `normalise_config` function has a unit test that checks both
  the `.value` and the `.diagnostics` list — no output capture, no logger mock needed.

---

### Phase 11 — Type-Driven Design at Edges (Parse, Don't Validate)

- [x] **P11-newtypes**: Audit boundary intake functions for raw types that carry implicit
  invariants (non-empty string, bounded integer, non-empty collection). Create newtypes:
  ```rust
  #[derive(Debug, Clone, PartialEq, Eq)]
  pub struct NonEmptyString(String);
  impl NonEmptyString {
      pub fn new(s: impl Into<String>) -> Result<Self, ParseError> {
          let s = s.into();
          (!s.trim().is_empty()).then_some(Self(s)).ok_or(ParseError::Empty)
      }
      pub fn as_str(&self) -> &str { &self.0 }
  }
  ```
  Once `NonEmptyString` is constructed, downstream code never re-checks emptiness.

- [x] **P11-parse-at-edge**: For every boundary function with inline presence checks or
  validation of individual fields, extract a `parse_*` domain function:
  ```rust
  // WRONG (validation scattered in boundary):
  pub fn load(ws: &dyn Workspace) -> Result<Config, Error> {
      let raw = ws.read("cfg")?;
      if raw.is_empty() { return Err(Error::Empty); }  // ← validation in boundary
      let cfg = Config { content: raw };
      if cfg.content.len() > 1000 { return Err(Error::TooLong); }  // ← more validation
      Ok(cfg)
  }
  
  // CORRECT (parsing at the edge, boundary just wires):
  pub fn parse_config(raw: &str) -> Result<Config, ConfigParseError> {
      // all validation here, with typed error variants
  }
  pub fn load(ws: &dyn Workspace) -> Result<Config, LoadError> {
      let raw = ws.read("cfg").map_err(LoadError::Io)?;
      parse_config(&raw).map_err(LoadError::Parse)
  }
  ```

- [x] **P11-raw-types**: For boundary functions that return or pass inward raw capability
  types (`std::process::Output`, `git2::Oid`, raw byte buffers, `http::Response`), translate
  to domain types at the boundary before they cross inward. The WARN lint
  `forbid_raw_effect_types_in_public_apis` flags these — investigate each.

---

### Phase 10B — Enrich Error Payloads With Strong Types

**Why 10B after Phase 11:** Now that newtypes and parse functions exist (Phase 11), error
enums can carry domain-typed context instead of raw strings.

- [x] **P10B-error-payloads**: Revisit error enums created in Phase 10A. Where a variant
  carries a raw `String` or `usize` that now has a corresponding newtype from Phase 11,
  upgrade the payload:
  ```rust
  // Phase 10A created this with primitive payload:
  enum ConfigError { InvalidField(String) }
  
  // Phase 10B upgrades to use newtype from Phase 11:
  enum ConfigError { InvalidField(FieldName) }
  ```

  **Note:** Parse errors should keep raw input types, not validated newtypes — the parse
  function is what creates the newtype, so parse errors occur BEFORE the newtype exists:
  ```rust
  // CORRECT: parse error carries raw input
  enum GitOidParseError { WrongLength { expected: usize, actual: usize } }
  
  // WRONG: parse error carries the type it's trying to create
  enum GitOidParseError { Invalid(GitOid) }  // GitOid doesn't exist yet!
  ```

- [x] **P10B-diagnostic-payloads**: Similarly, upgrade `WithDiagnostics<T>` diagnostic
  enum variants to carry newtype context where it adds clarity. Diagnostic variants that
  describe "what was defaulted" benefit from strong types; variants that describe "what
  raw input was rejected" keep raw types.

---

### Phase 12 — TDD Audit and Coverage Gap Fill (repurposed)

**This phase is now an AUDIT, not the testing phase.** The "Inline TDD Requirement" at the
top of Phases 3+ mandates red-first testing during each phase. Phase 12 verifies that
mandate was followed and fills any gaps.

**Audit rule:** For every function touched in Phases 2–11, at least one test must exist.
If a function was missed during its owning phase, write the test now.

- [x] **P12-tdd-pure**: For every new or extracted pure domain function — write a red test
  first (calling the function with plain values and asserting on the result), then implement.
  Pure function tests need no setup:
  ```rust
  #[test]
  fn test_parse_config_rejects_empty_field() {
      let result = parse_config("timeout=\n");
      assert_eq!(result, Err(ConfigParseError::MissingField("timeout")));
  }
  ```

- [x] **P12-boundary-seams**: For every new or restructured boundary function — write an
  integration test using `MemoryWorkspace` + `MockProcessExecutor` that verifies:
  1. The right capability method is called
  2. Capability errors produce the correct typed boundary error
  3. The correct typed result/event is returned on success

- [x] **P12-error-variants**: For every new error enum variant — write a test that asserts
  the correct variant is produced from the corresponding invalid input.

- [x] **P12-diagnostics**: For every `WithDiagnostics<T>` function — write:
  - Test that correct `.value` is produced from nominal input
  - Test that correct `.diagnostics` list is produced when defaults/clamping occur
  - Test that `.diagnostics` is empty for fully-valid input

- [x] **P12-no-serial**: All tests in `ralph-workflow/src/` and `tests/integration_tests/`
  must pass with no `#[serial]`. Use env-injection and `MemoryWorkspace`. If you find
  yourself wanting `#[serial]`, that is a signal to refactor the production code.

---

### Phase 13 — Add Property-Based Testing

- [x] **P13-proptest-dep**: Add `proptest = "1"` to `[dev-dependencies]` in
  `ralph-workflow/Cargo.toml`. Verify `cargo test -p ralph-workflow --lib` still passes.

- [x] **P13-parsers**: Add property tests for parser functions — the ones extracted in
  Phases 2–9. A parser should never panic on arbitrary input:
  ```rust
  use proptest::prelude::*;
  proptest! {
      #[test]
      fn parse_git_status_never_panics(s in ".*") {
          // Must not panic on any string input
          let _ = parse_git_status_output(&s);
      }
      #[test]
      fn parse_config_rejects_empty(s in "\\s*") {
          assert!(parse_config(s.trim()).is_err());
      }
  }
  ```

- [x] **P13-reducers**: Add property tests for key reducers — verify state invariants hold
  after any event:
  ```rust
  proptest! {
      #[test]
      fn reducer_retries_never_go_negative(
          initial_retries in 0u32..=10u32,
          event_sequence in prop::collection::vec(arb_event(), 0..20),
      ) {
          let state = PipelineState { retries_remaining: initial_retries, ..Default::default() };
          let final_state = event_sequence.into_iter().fold(state, reduce);
          assert!(final_state.retries_remaining <= initial_retries);
      }
  }
  ```

---

### Phase 14 — Add Code Coverage Instrumentation

- [x] **P14-llvm-cov**: Document `cargo install cargo-llvm-cov --locked` in
  `docs/agents/verification.md` as a dev-tool setup step. Do not add it as a required
  CI dependency — it is an investigation tool.

- [x] **P14-xtask-coverage**: Add `cargo xtask coverage` subcommand:
  ```bash
  cargo llvm-cov --all-features --lib -p ralph-workflow \
    --html --output-dir target/coverage/html
  cargo llvm-cov report --lib -p ralph-workflow
  ```
  Coverage is a diagnostic signal — not a build gate. The command exits 0 always.

- [x] **P14-docs**: Add to `docs/agents/verification.md`: "Run `cargo xtask coverage` after
  touching any module refactored in the fp-style-compliance plan. Low coverage on a module
  is a signal to ask 'do we understand the failure modes here?' — not a gate to block PR."

---

## Final Verification Wave

These are APPROVAL GATES. Each is answered by investigation and judgment, not by a metric.

### F1 — Architecture is Genuinely Correct (Primary Gate, oracle agent)

The oracle reads the restructured boundary modules and every extracted domain module.
For EACH boundary module in `boundary/`, `claude/`, `streaming_state/`, `codex/`,
`opencode/`, `printer/`, `runtime/`:

**Questions the oracle must answer YES to:**
- [ ] Is this boundary file flat? (No subdirectories, one to a few thin functions)
- [ ] Can every boundary function be described in one sentence as "gather X, call pure(X), do effect"?
- [ ] Does no boundary function contain a retry loop? (retry → reducer)
- [ ] Does no boundary function make domain-policy decisions inline? (policy → orchestrator)

For EACH domain module extracted in Phases 2–9:
- [ ] Does the module contain zero imports from `io/`, `runtime/`, `executor/`, `boundary/`,
  or any agent adapter directory?
- [ ] Can every public function in the module be unit-tested with `f(plain_value)` and no setup?
- [ ] Are all recoverable failures `Result<T, E>` with a typed error enum?
- [ ] Are diagnostics returned as `WithDiagnostics<T>` (not printed directly)?

**VERDICT:** APPROVE if all questions answered YES.
REJECT with specific file:function citations if any answer is NO.

### F2 — Build and tests pass

```bash
cargo xtask verify  # must pass all 7 lanes, no ERROR/WARNING
cargo test -p ralph-workflow --lib --all-features
cargo test -p ralph-workflow-tests --test integration_tests
```

No `#[serial]` in unit or integration tests. All tests deterministic.

### F3 — Tests verify behaviour (oracle agent, sample of 10 new tests)

- [ ] Each test asserts on an observable outcome (return value, event emitted, typed error variant),
  NOT on internal implementation details (call counts, internal variable values)
- [ ] Pure-function tests use zero mocking, zero I/O setup, zero fakes
- [ ] Boundary tests verify the contract (capability called correctly, errors mapped, typed result
  returned) — not internal structure
- [ ] Test names are `test_<behaviour_under_test>` not `test_<implementation_detail>`

**VERDICT:** APPROVE if all 10 sampled tests meet these criteria.
REJECT with specific test names and explanations if not.

### F4 — Dylint as diagnostic scan (informational, not a gate)

```bash
cargo dylint --lib ralph_lints -p ralph-workflow -- --lib --quiet 2>&1 \
  | tee /tmp/dylint-final.txt
wc -l /tmp/dylint-final.txt
```

For every remaining line:
- Investigate: is this a genuine architectural problem or a lint false-positive?
- Genuine problem → create a follow-up task, fix it, come back here
- Confirmed false-positive → add a code comment at the offending site explaining
  why the code is correct (do NOT use `#[expect(...)]` without explanation)

This gate has no required error count. Its purpose is to prevent missing genuine
violations that the architecture review did not catch.

### F5 — Integration Test Behavioral Equivalence Against Baseline `ceb66980`

**Done only once, at the very end, after all other gates pass.**

The commit `ceb66980` is the last commit before this refactoring project began. The
integration tests are the behavioral specification. Every scenario that passed on the
baseline must still pass — unless we discovered it was testing a bug rather than correct
behavior.

**Step 1 — Run integration tests on HEAD and capture results:**
```bash
cargo test -p ralph-workflow-tests --test integration_tests 2>&1 \
  | tee /tmp/integration-head.txt
```

**Step 2 — Check out baseline in a separate worktree and run the same suite:**
```bash
git worktree add /tmp/baseline-check ceb66980
cd /tmp/baseline-check
cargo test -p ralph-workflow-tests --test integration_tests 2>&1 \
  | tee /tmp/integration-baseline.txt
cd -
git worktree remove /tmp/baseline-check
```

**Step 3 — Compare which tests passed on baseline but fail on HEAD:**
```bash
# Extract passing test names from each run
grep "^test .* ok$" /tmp/integration-baseline.txt | sort > /tmp/pass-baseline.txt
grep "^test .* ok$" /tmp/integration-head.txt    | sort > /tmp/pass-head.txt

# Tests that passed on baseline but are missing or failing on HEAD
comm -23 /tmp/pass-baseline.txt /tmp/pass-head.txt
```

**Step 4 — Triage every discrepancy:**

For each test that passed on `ceb66980` but does not pass on HEAD:

- **Is this a behavioral regression introduced by the refactor?**
  The refactored code produces a different result from the old code on the same input.
  → Fix the regression. The test should pass on HEAD.

- **Was the baseline test itself testing a bug in the old imperative code?**
  The old code produced an incorrect result (wrong output, swallowed error, wrong state
  transition) and the test asserted on that incorrect result. The refactored code now
  produces the correct result, which breaks the old assertion.
  → Update the test to assert on the correct behavior. Document the bug that was fixed
  with a comment in the test: `// Previously the imperative version incorrectly <did X>.
  // Refactored version correctly <does Y> — see ceb66980 for the old behavior.`

- **Is the test now redundant because it was testing an implementation detail that no
  longer exists after restructuring?**
  → Replace it with a test against the new observable seam. The behavior being tested
  must still be covered — just through the new public API.

**NOT ACCEPTABLE:**
- Deleting tests from the baseline that fail on HEAD without replacing them with
  equivalent coverage
- Marking tests as `#[ignore]` to make the comparison pass
- Changing tests to assert on the refactored code's actual (wrong) output instead of
  investigating the discrepancy

**VERDICT:** APPROVE when every test that passed on `ceb66980` either:
(a) passes on HEAD with the same behavior, OR
(b) is replaced by a test that asserts the correct behavior (with the baseline bug documented)

REJECT if any baseline-passing test is simply gone or silently broken on HEAD.
