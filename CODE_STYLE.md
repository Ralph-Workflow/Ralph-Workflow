# Code Style Guide

## Important Rules for this Project
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
- `Result` + `?` with context; no `unwrap()`/`expect()` in production
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

**Library and test code** (`ralph-workflow/src/lib.rs`, `tests/*/main.rs`):

```rust
#![deny(
    warnings,
    clippy::all,
    clippy::pedantic,
    clippy::nursery
)]
```

**Binary target** (`ralph-workflow/src/main.rs`) adds `unsafe_code` denial:

```rust
#![deny(
    warnings,
    unsafe_code,      // Binary should contain no unsafe operations
    clippy::all,
    clippy::pedantic,
    clippy::nursery
)]
```

**Custom lint crates** (`lints/file_too_long/src/lib.rs`):

```rust
#![deny(
    warnings,
    clippy::all,
    clippy::pedantic,
    clippy::nursery
)]
```

**Note on `clippy::cargo`**: The `clippy::cargo` lint group is not enabled because it flags transitive dependency version conflicts (e.g., `bitflags 1.3.2` from `inotify` vs `2.10.0` from other crates) which are ecosystem-level issues outside our control and don't reflect code quality problems.

### Why Pedantic and Nursery

- **`clippy::pedantic`**: Enforces idiomatic Rust patterns, API design, and documentation standards
- **`clippy::nursery`**: Catches additional issues that may become pedantic in future releases
- Both are configured as `deny` to maintain high code quality and catch issues early

### Unsafe Code Policy

- **Library and tests**: Unsafe code is permitted for legitimate low-level operations (POSIX syscalls, FFI)
- **Binary target**: No unsafe code allowed
- **All unsafe blocks**: Must have safety documentation explaining why they are safe
- **Examples**: `fcntl`, `kill`, `setpgid` for process group management; `tzset` for timezone testing

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

**Forbidden:**
- **Never** use `#[allow(..)]` or `#[expect(..)]` attributes (see [AGENTS.md](AGENTS.md))
- If a lint fires incorrectly, refactor to avoid triggering it rather than suppressing it

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

**Never `#[allow(dead_code)]`** - see [AGENTS.md](AGENTS.md).

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
