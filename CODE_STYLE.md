# Code Style Guide

## Important Rules for this Project

- Pre-existing issues are fix-now work: if you find a broken test, warning, lint failure, stale docs, dead code, or other existing repo problem, stop and fix it instead of carrying it forward.
- Production files should target 300 lines max (guideline), 500 lines recommended limit, 1000 lines hard limit (dylint enforces). Files 500-700 lines should be reviewed for cohesion (see guidelines below). Test files should stay under 1000 lines.
- A file should do one conceptual job. If you need a paragraph to explain what the file does, it's doing too much.
- If you need comments to explain what the code does, rewrite it.
- If nesting goes past 3–4 levels, refactor.
- Prefer early returns over deep if trees.
- Function should be at most 100 lines long but if it's longer than 50 lines you should start considering refactoring, if it's like barely passing lint at 97 your PR may not be accepted and you will be asked to refactor.
- Avoid clever code. Boring is good.

### File Size Guidelines

**Target ranges:**
- **Under 300 lines:** Ideal for new code, no action needed
- **300-500 lines:** Good, acceptable for cohesive code
- **500-700 lines:** Review structure (see criteria below)
- **700-1000 lines:** Strong smell, likely needs splitting
- **Over 1000 lines:** MUST split (dylint enforces)

**Files over 500 lines are acceptable IF they are cohesive:**
- **Large match statements:** Single reducer matching on 20+ event variants (e.g., `state_reduction/review.rs`)
- **Comprehensive enums:** Type definitions with 20+ variants and extensive documentation (e.g., `effect/types.rs`)
- **Core state structures:** Central state types with 30+ fields organized by domain (e.g., `state/pipeline.rs`)
- **Single-algorithm implementations:** Event loops or state machines that are one cohesive function (e.g., `event_loop/driver.rs`)

**Files over 500 lines SHOULD be split IF they have:**
- **Multiple responsibilities:** 5+ handler functions, mixed concerns (input prep + validation + execution)
- **Obvious boundaries:** Clear separation between concerns that could be modules
- **Example:** Handler implementations should group by phase (input/prompt/execution/validation/output)

See `docs/contributing/refactoring-history.md` for detailed examples of good and bad splits.

---

## Functional Rust

This codebase enforces a functional programming discipline through custom dylint lints. The goal is code that is easy to reason about, test, and compose. Mutation is a side effect — push it to the boundaries of the system.

### The Core Rule

**Outside boundary modules, code must be purely functional:**
- No `let mut` bindings
- No `while`, `loop`, or `for` constructs
- No `&mut self` method calls on non-boundary types
- No interior-mutability types (`Cell`, `RefCell`, `Mutex`, `RwLock`, etc.)

### Boundary Modules

Low-level I/O, FFI, and runtime code sometimes genuinely requires mutation — byte-level parsing, process management, retries, OS interaction. That code belongs in a **boundary module**: any module whose path contains one of the following components:

| Marker | Purpose |
|--------|---------|
| `io/` | Filesystem, network, stream I/O |
| `runtime/` | Process execution, OS interaction |
| `ffi/` | Foreign function interface |
| `boundary/` | Any other explicit mutation boundary |

Code inside these paths is excluded from all four functional-Rust lints. Code outside them is held to strict functional standards.

**When to use a boundary module:** If you are writing code that inherently requires mutation (a process executor, a file writer, a byte-level parser), place it inside one of these paths. Do not fight the lints by workarounds in domain code — move the mutation to its proper home.

### What to Write Instead

Replace imperative patterns with combinators:

```rust
// Bad — imperative accumulation
let mut result = Vec::new();
for item in items {
    if item.is_valid() {
        result.push(item.transform());
    }
}

// Good — declarative pipeline
let result: Vec<_> = items
    .into_iter()
    .filter(|item| item.is_valid())
    .map(|item| item.transform())
    .collect();
```

```rust
// Bad — mutable binding for reduction
let mut total = 0u64;
for item in items {
    total += item.price;
}

// Good — combinator
let total: u64 = items.iter().map(|item| item.price).sum();
```

```rust
// Bad — in-place mutation via &mut self
state.push_event(event);

// Good — return a new value
let state = state.with_event(event);
```

```rust
// Bad — interior mutability hiding mutation
struct Cache {
    entries: RefCell<HashMap<String, String>>,
}

// Good — functional update returns new value
fn with_entry(cache: &Cache, key: String, value: String) -> Cache {
    let mut entries = cache.entries.clone();
    entries.insert(key, value);
    Cache { entries }
}
// Or use a boundary module if caching is genuinely a runtime concern
```

### The Four Functional-Rust Lints

These are enforced by custom dylint lints in the `lints/` directory:

| Lint | What it rejects | Boundary exception |
|------|----------------|--------------------|
| `forbid_mut_binding` | `let mut` bindings | `io/`, `runtime/`, `ffi/`, `boundary/` |
| `forbid_imperative_loops` | `while`, `loop`, `for` | `io/`, `runtime/`, `ffi/`, `boundary/` |
| `forbid_mutating_receiver_methods` | `&mut self` calls on non-allowlisted types | `io/`, `runtime/`, `ffi/`, `boundary/` |
| `forbid_interior_mutability` | `Cell`, `RefCell`, `Mutex`, `RwLock`, `OnceLock`, `LazyLock`, `OnceCell` | `io/`, `runtime/`, `ffi/`, `boundary/` |

**When a lint fires on domain code:** The lint is right. Refactor to combinators and immutable transforms. Do not argue with the lint, suppress it, or move code into a fake boundary module. If the code genuinely belongs at the system boundary, move it there.

---

## Architecture

Ralph uses an **event-sourced reducer architecture**. See [effect-system.md](docs/architecture/effect-system.md).

If you change **pipeline behavior** (phases, retries/fallback, effect sequencing, checkpoint/resume, or any reducer/event/effect shape), treat the reducer/effect architecture as **mandatory reading**:

- `docs/architecture/event-loop-and-reducers.md`
- `docs/architecture/effect-system.md`

```
State → Orchestrator → Effect → Handler → Event → Reducer → State
```

| Component | Pure? | Role |
|-----------|-------|------|
| `PipelineState` | Yes | Immutable progress snapshot |
| `reduce()` | Yes | `(State, Event) → State` |
| `determine_next_effect()` | Yes | `State → Effect` |
| `EffectHandler` | No | Executes effects, produces events |

**Business logic → reducers/orchestration (pure). I/O → handlers (impure).**

### Reducers, Effects, and Events (Non-Negotiable)

- **Events are facts:** effect handlers emit descriptive, past-tense outcome events ("what happened"), not control/decision events ("what to do next").
- **Reducers decide policy:** retry/fallback, phase transitions, counters/limits, and pipeline sequencing live in reducers/orchestration (pure) and must be state-driven.
- **Handlers execute, not decide:** handlers perform I/O and translate outcomes into events; they must not contain hidden retries/fallback loops or mutate pipeline state directly.
- **UI events are not correctness:** `UIEvent` is display-only; pipeline correctness must not depend on UI output.

### Two Effect Layers

| Layer | When | Filesystem |
|-------|------|------------|
| `AppEffect` | Before repo root known | `std::fs` directly |
| `Effect` | After repo root known | `ctx.workspace` |

Never mix. AppEffect cannot use Workspace; Effect cannot use `std::fs`.

### Reducer-Driven Control-Flow and Metrics

All pipeline control-flow decisions (iteration advancement, retry/continuation/fallback logic) are derived solely from reducer state. Handlers execute at most one attempt per effect and must not contain hidden loops or decision logic.

**Metrics are a view, not a driver:** The `RunMetrics` struct in `PipelineState.metrics` provides observability into pipeline execution, but metrics do not drive control-flow. Control-flow is driven by the reducer's state machine (phase, iteration, continuation state, agent chain state, etc.), and metrics simply track the transitions.

**Agent execution state is two-dimensional:** When agent work is active, reducer state should track both the active runtime consumer (`drain`, such as planning or fix) and the drain-local mode (`normal`, continuation, same-agent retry, XSD retry). Capability labels like `AgentRole` can still exist, but they must not replace explicit runtime drain identity.

**Chain config is separate from runtime drain identity:** Config may define reusable named chains and bind built-in drains to those chains, but runtime effects/events/state should operate on resolved concrete drain bindings rather than re-deriving role-shaped defaults during execution.

**Registry/runtime boundaries stay drain-first:** `AgentRegistry` and effect handlers should treat resolved drain bindings as the authoritative runtime chain source. Any legacy role-shaped compatibility view must be derived from those bindings on demand, not stored as parallel mutable runtime state.

**Drain defaults come from resolved drain bindings first:** When commit or analysis are not bound explicitly, normalization should inherit from already-bound review/fix and planning/development drains before falling back to legacy compatibility names like `reviewer` or `developer`.

**Legacy compatibility is config-only:** Legacy `[agent_chain]` input may still be accepted, but it must be normalized into the same built-in resolved drain bindings before runtime code, handlers, or tests consume it.

**Invariants:**

- **Single source of truth:** Any advance/retry/continue decision is derived from reducer state plus the latest event
- **Determinism:** Given same checkpoint + same events, the reducer produces identical state and control-flow
- **No hidden loops:** Handlers perform at most one attempt per effect; repeated attempts must be explicit reducer events
- **No shadow state:** No runtime-only counters may influence control-flow

See `ralph-workflow/src/reducer/state/metrics.rs` for complete event-to-metric mapping.

---

## Glossary

| Term | Definition |
|------|------------|
| **Effect** | A side-effect operation (git, filesystem, agent execution) that handlers execute. See "Two Effect Layers" section. |
| **AppEffect** | CLI-layer effect type for operations before repository root is known. Uses `std::fs` directly. |
| **Reducer** | Pure function: `(State, Event) → State` with no side effects |
| **PipelineState** | Immutable state snapshot representing current pipeline progress. Doubles as checkpoint data. |
| **Workspace** | Filesystem abstraction trait - use `WorkspaceFs` in production, `MemoryWorkspace` in tests |
| **Phase** | Pipeline stage: Planning, Development, Review, Commit |
| **Agent Chain** | Ordered fallback list of agents - Ralph tries next agent on failure |
| **CCS** | Claude Code Switch - tool for switching between Claude Code profiles |
| **NDJSON** | Newline-delimited JSON - streaming format used by agent CLIs |
| **XSD** | XML Schema Definition - used to validate agent XML output |
| **ProcessExecutor** | Process execution abstraction trait - use `RealProcessExecutor` in production, `MockProcessExecutor` in tests |
| **EffectHandler** | Trait for executing effects (impure operations). Produces events from effects. |
| **UIEvent** | Events for user-facing display (status, progress, XML output). See `reducer::ui_event`. |
| **Work Guide** | PROMPT.md template for describing tasks to AI agents (e.g., bug-fix, feature-spec, refactor) |
| **PLAN.md** | Implementation plan file written by orchestrator to `.agent/PLAN.md` after planning phase. Contains AI-generated plan based on PROMPT.md. |
| **ISSUES.md** | Review issues file written by orchestrator to `.agent/ISSUES.md` after review phase. Contains problems found by reviewer agent. |

---

## Design Principles

- **High cohesion**: Code that changes together lives together
- **Single responsibility**: One job per module/type
- **Explicit boundaries**: Separate domain, orchestration, I/O, CLI
- **Safe APIs**: Types encode invariants, hard to misuse
- **Minimal surface**: Private by default

---

## Code Guidelines

| Aspect | Rule |
|--------|------|
| Function size | < 30 lines |
| Module size | < 300 lines |
| Test file size | < 1000 lines |
| Nesting depth | Max 3 levels |
| Magic numbers | Extract to named constants |
| Abbreviations | Only universal (`ctx`, `cfg`) |

- Early returns over nested conditionals
- `Result` + `?` with context; no `unwrap()` or `.expect()` outside the documented exceptions (see Linting section)
- DRY, but duplication beats wrong abstraction

---

## Comments

**Comments explain *why*, not *what*.**

| Required | Forbidden |
|----------|-----------|
| Module-level `//!`: purpose, when to use | Restating code |
| Public items `///`: what, params, errors | Commented-out code |
| Non-obvious logic: why this approach | TODO without issue number |
| Workarounds: link to issue | |

```rust
/// Executes the next pipeline effect based on current state.
///
/// # Errors
/// Returns error if effect execution fails (agent crash, I/O error).
pub fn execute_next(state: &PipelineState, handler: &mut impl EffectHandler) -> Result<PipelineEvent>
```

Comments must stand alone without external docs.

---

## Linting

All code (production and tests) must pass clippy with strict lint levels configured at the crate level.

### Required Lint Configuration

All crate roots must carry the following lint attributes:

```rust
#![deny(warnings)]
#![deny(clippy::all)]
#![forbid(unsafe_code)]
#![deny(
    // No explicit iterator loops when a more idiomatic form exists
    clippy::explicit_iter_loop,
    clippy::explicit_into_iter_loop,
    // No implicit crashes / partial operations
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::panic_in_result_fn,
    clippy::indexing_slicing,
    // No casual side effects / debugging leftovers
    clippy::print_stdout,
    clippy::print_stderr,
    clippy::dbg_macro,
    // Treat unchecked arithmetic as suspicious
    clippy::arithmetic_side_effects,
    // Push toward combinators instead of hand-written control flow
    clippy::manual_map,
    clippy::manual_filter,
    clippy::manual_find,
    clippy::manual_filter_map,
    clippy::manual_flatten,
    clippy::needless_collect,
)]
```

**`ralph-gui`** uses `[lints]` in `Cargo.toml` instead of source attributes to allow crate-specific exemptions (e.g., `needless_pass_by_value` for Tauri's `State<'_, T>`).

**Note on `clippy::cargo`**: The `clippy::cargo` lint group is not enabled because it flags transitive dependency version conflicts (e.g., `bitflags 1.3.2` from `inotify` vs `2.10.0` from other crates) which are ecosystem-level issues outside our control and don't reflect code quality problems.

### Why Explicit Rules (not `pedantic`/`nursery`)

The lint policy uses hand-picked, individually named rules instead of the `clippy::pedantic` and `clippy::nursery` groups. This gives precise control over which checks are enforced and avoids surprise breakage when clippy adds new lints to those groups.

### Clippy Configuration (`clippy.toml`)

In addition to the source-level attributes, each `clippy.toml` configures:

- **Test strictness**: `allow-unwrap-in-tests = false`, `allow-expect-in-tests = false`, `allow-panic-in-tests = false`, `allow-print-in-tests = false`, `allow-indexing-slicing-in-tests = false`
- **Iterator loops**: `enforce-iter-loop-reborrow = true`
- **Disallowed types**: Interior-mutability types (`Cell`, `RefCell`, `Mutex`, `RwLock`, `OnceLock`, `LazyLock`)
- **Disallowed methods**: `unwrap`, `expect`, and mutating `Vec` methods (`push`, `append`, `insert`, `remove`, `retain`, `sort`, `sort_unstable`)
- **Disallowed macros**: `println!`, `eprintln!`, `dbg!`

### `#[allow(...)]` — Absolute Prohibition

**`#[allow(...)]` and `#![allow(...)]` are never permitted** in any form, with one narrow, machine-verified exception:

**The only allowed exception:**

```rust
#[cfg(test)]
#[allow(clippy::large_stack_frames)]
mod tests;
```

This pattern — `#[allow(clippy::large_stack_frames)]` immediately preceded by `#[cfg(test)]` — is permitted solely because the Rust test harness generates deeply-nested stack frames that trigger the lint. It is verified and enforced by `xtask verify`'s `forbidden-allow-expect-scan` check.

**No other `#[allow(...)]` is ever acceptable**, regardless of context. If a lint fires:
1. Refactor the code to not trigger it.
2. If the lint is incorrect for the situation, open a discussion about changing the lint policy — do not suppress inline.
3. "It's just temporary" and "it's annoying" are not reasons.

### `#[expect(...)]` — Permitted With Documented Reason

**`#[expect(...)]` is permitted ONLY when ALL three conditions are met:**

1. The lint fires on code you cannot modify (proc-macro output, external trait impls, build-script artifacts).
2. It includes `reason = "..."` naming the specific external source.
3. It is the narrowest possible scope (item attribute, not module or crate).

Example of correct usage:
```rust
#[expect(clippy::some_lint, reason = "proc-macro output from derive_more")]
```

**`#![expect(...)]` (inner attribute) is ALWAYS prohibited**, regardless of reason.

### `.expect()` — Restricted to Documented Sites

`.expect()` is forbidden in production workflow code and integration tests. It is permitted only in the following specific cases:

| Context | Why permitted | Examples |
|---------|--------------|---------|
| `test-helpers/src/lib.rs` | Library code wrapping git2/libgit2; these calls cannot return `Result` to callers without redesigning the entire harness API | `repo.index().expect("open index")`, `Signature::now(...).expect("signature")` |
| `xtask/src/main.rs` entry point | Top-level binary entry that cannot propagate `Result` further | `.expect("xtask manifest dir has a parent")` |
| `ralph-gui/src/main.rs` entry point | Tauri application entry; framework requires `main()` not return `Result` | `.expect("error while running tauri application")` |
| `ralph-workflow/src/executor/real.rs` boundary code | OS-level process spawning in a boundary module; failures here are unrecoverable | `.expect("spawn sleep")` |

**Everywhere else: use `?`, `map_err`, or proper `Result` propagation.** If you find `.expect()` outside the above contexts, treat it as a bug and fix it.

`.unwrap()` carries the same prohibition as `.expect()` — no exceptions anywhere outside `test-helpers` and entry points.

### Custom Dylint Lints

Beyond clippy, the repository enforces additional rules via [dylint](docs/tooling/dylint.md):

| Lint | Description |
|------|-------------|
| `file_too_long` | Warns at 500+ lines, errors at 1000+ lines |
| `forbid_mut_binding` | Rejects `let mut` outside boundary modules (`io/`, `runtime/`, `ffi/`, `boundary/`) |
| `forbid_imperative_loops` | Rejects `while`, `loop`, `for` outside boundary modules |
| `forbid_mutating_receiver_methods` | Rejects `&mut self` method calls unless receiver is an allowlisted boundary type |
| `forbid_interior_mutability` | Rejects interior-mutability types (`Cell`, `RefCell`, `Mutex`, etc.) outside boundary modules |

See the **Functional Rust** section above for the full explanation and examples.

### Unsafe Code Policy

- **All crates**: `unsafe_code` is `forbid`-level — no unsafe code permitted anywhere
- **All unsafe blocks**: Must have safety documentation explaining why they are safe

### Common Lint Fixes

**Documentation:**
- Add `# Errors` sections to functions returning `Result`
- Add `# Panics` sections to functions that may panic
- Add backticks around code items in docs (`` `PipelineState` ``, not `PipelineState`)

**Attributes:**
- Add `#[must_use]` to functions/methods with important return values
- Prefer `const fn` when functions can be evaluated at compile time

**Code Style:**
- Use format string interpolation: `format!("{var}")` not `format!("{}", var)`
- Use `write!()` instead of `format!()` when appending to existing `String`
- Use field init shorthand: `State { phase }` not `State { phase: phase }`
- Remove unnecessary `mut` from parameters that aren't mutated

**Absolutely Forbidden:**
- `#[allow(...)]` or `#![allow(...)]` — see above; one narrow exception only
- `#![expect(...)]` — module/crate scope always prohibited; `#[expect(...)]` without `reason = "..."` — see conditional rule above
- `.unwrap()` or `.expect()` — outside documented sites only
- `let mut` — use combinators; boundary modules only for genuine mutation
- Imperative loops (`for`, `while`, `loop`) — use iterators; boundary modules only

### Verification

Run clippy on all targets:

```bash
# Library + unit tests + benchmarks + examples
cargo clippy -p ralph-workflow --all-targets --all-features -- -D warnings

# Integration tests
cargo clippy -p ralph-workflow-tests --all-targets -- -D warnings

# Test helpers
cargo clippy -p test-helpers --all-targets -- -D warnings
```

Verification passes when required checks complete successfully with **no ERROR/WARNING diagnostics** (informational output is acceptable).

---

## Dead Code

Dead code = not referenced by production, only by tests, "for future use", unused feature flags.

Handle by: delete it, implement the feature now, gate behind active feature flag, move to `examples/`.

**Never `#[allow(dead_code)]`** — this falls under the absolute prohibition on `#[allow(...)]`.

---

## Testing

Three tiers with strict boundaries:

| Tier | Command | What | Mocks? |
|------|---------|------|--------|
| Unit | `cargo test -p ralph-workflow --lib` | Pure logic | None needed |
| Integration | `cargo test -p ralph-workflow-tests` | Component interactions | `MemoryWorkspace`, `MockProcessExecutor` |
| System | `cargo test -p ralph-workflow-tests --test git2-system-tests` | Real filesystem/git | None (real I/O) |

See [INTEGRATION_TESTS.md](tests/INTEGRATION_TESTS.md), [SYSTEM_TESTS.md](tests/system_tests/SYSTEM_TESTS.md).

### Rules

- **Black-box**: Test through public APIs, assert observable outcomes
- **Behavior over implementation**: Tests survive internal refactors
- **Mock at boundaries only**: Filesystem, network, processes - never domain logic
- **Fix implementation, not tests**: Unless expected behavior intentionally changed

### Parallelism (Mandatory)

Integration tests **must run in parallel** (standard Rust test harness default). System tests
serialize via `#[serial]` only due to libgit2's global reference counter — not a design choice.

| Test tier | Threading | Why |
|-----------|-----------|-----|
| Unit | Parallel (default) | Pure functions, no shared state |
| Integration | Parallel (default) | `MemoryWorkspace` and `MockProcessExecutor` are isolated per test |
| System | Serial (`#[serial]`) | libgit2 C library has thread-unsafe global shutdown |

**`#[serial]` in integration tests is a design smell.** It means production code calls
`std::env::var`, touches real filesystem, or uses singletons instead of accepting injectable
dependencies. The fix is always dependency injection, never test serialization.

See [INTEGRATION_TESTS.md](tests/INTEGRATION_TESTS.md) for the env-injection pattern.

### Workspace Abstraction

| Forbidden | Required |
|-----------|----------|
| `std::fs::read_to_string()` | `workspace.read()` |
| `std::fs::write()` | `workspace.write()` |
| `path.exists()` | `workspace.exists()` |

Exceptions: `WorkspaceFs` impl, `RealAppEffectHandler`, bootstrap code.

---

## Performance Optimization

### Memory Optimization Guidelines

Ralph uses memory-efficient data structures to minimize heap allocations and support long-running pipelines with bounded memory growth:

**String Interning (Arc<str>)**
- Use `Arc<str>` for repeated strings (phase names, agent names)
- Share allocations via `StringPool` to reduce memory footprint
- Example: `ExecutionStep.phase` and `ExecutionStep.agent` use `Arc<str>`

**Exact Allocation (Box<str>)**
- Use `Box<str>` for unique strings that don't need sharing
- Avoids Vec<u8> over-allocation compared to String
- Example: `ExecutionStep.step_type` uses `Box<str>`

**Optional Collections (Option<Box<[T]>>)**
- Use `Option<Box<[T]>>` for collections that are often empty
- Saves 24 bytes (Vec overhead) when None
- Example: `StepOutcome::Success.files_modified` uses `Option<Box<[String]>>`

**When to Optimize:**
- Hot paths (executed thousands of times per pipeline run)
- Data structures stored in bounded collections (execution history)
- Repeated strings across many instances

**When NOT to Optimize:**
- One-off allocations (config loading, CLI parsing)
- Small structs (< 100 bytes total)
- Code clarity would suffer significantly

### Benchmarking

Run benchmarks to measure performance:
```bash
cargo test --lib benchmarks -- --nocapture
```

Expected performance targets (as of v0.7.3):
- Execution history: ~40-45 bytes per entry (core fields)
- Checkpoint serialization: < 10ms for 1000 entries
- Memory growth: Linear and bounded by `execution_history_limit`

See `ralph-workflow/src/benchmarks/baselines.rs` for regression tests.

---

## Principles

- Tests don't legitimize production code - if code exists only for tests, delete both
- Good tests protect behavior, not implementation
- Dead code is liability, not asset
- Prefer deletion over suppression
- Pure logic is testable logic - push I/O to boundaries
